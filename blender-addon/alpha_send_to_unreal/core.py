# SPDX-License-Identifier: GPL-3.0-or-later
#
# Alpha Send to Unreal - exports a Mixamo-rigged character to Unreal Engine.
# Copyright (C) 2026 Alpha Productions
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

"""Core logic: validation, temporary root bone, FBX export, texture copy, manifest.

Nothing in this module draws UI. Every function that changes the scene restores it
in a ``finally`` block.
"""

import json
import os
import re
import shutil
from pathlib import Path

import bpy
from mathutils import Vector

try:  # bundled with Blender; only needed to find the solid faces of a Glass material
    import numpy as np
except ImportError:  # pragma: no cover
    np = None

MANIFEST_VERSION = 1
HIPS_SUFFIX = "Hips"
ROOT_NAME = "root"

# How a material's alpha is used in Unreal (stored on each Blender material: Material.astu_alpha_mode)
ALPHA_DEFAULT = 'DEFAULT'   # alpha ignored: normal opaque material
ALPHA_GLASS = 'GLASS'       # semi-transparent surface (glass, liquid): translucent material
ALPHA_CUTOUT = 'CUTOUT'     # hard-edged transparency (leaves, hair cards): masked material
SOLID_OPACITY = 0.95        # texture alpha at or above this counts as solid (cork, leather...)

_FORMAT_EXT = {
    "PNG": ".png", "JPEG": ".jpg", "JPEG2000": ".jp2", "TARGA": ".tga",
    "TARGA_RAW": ".tga", "BMP": ".bmp", "TIFF": ".tif", "OPEN_EXR": ".exr",
    "OPEN_EXR_MULTILAYER": ".exr", "HDR": ".hdr", "WEBP": ".webp",
}
_CHANNEL = {"Red": "R", "Green": "G", "Blue": "B", "Alpha": "A", "R": "R", "G": "G", "B": "B"}


class SendToUnrealError(Exception):
    """A problem the user can fix. The message is shown as-is in Blender."""


# --------------------------------------------------------------------------- helpers
def sanitize(name):
    """File-system and Unreal friendly name."""
    clean = re.sub(r"[^A-Za-z0-9_\-]", "_", name.strip())
    return clean.strip("_") or "Character"


def _uniform(scale, tol=1e-4):
    return max(scale) - min(scale) <= tol * max(1.0, abs(max(scale)))


def find_armature(context):
    obj = context.active_object
    if obj is None:
        raise SendToUnrealError("Nothing is selected. Select the character's armature or one of its meshes.")
    if obj.type == 'ARMATURE':
        return obj
    if obj.type == 'MESH':
        for mod in obj.modifiers:
            if mod.type == 'ARMATURE' and mod.object is not None:
                return mod.object
        if obj.parent is not None and obj.parent.type == 'ARMATURE':
            return obj.parent
        raise SendToUnrealError(f"Mesh '{obj.name}' is not bound to an armature (no Armature modifier or parent).")
    raise SendToUnrealError(f"The active object '{obj.name}' is not an armature or a mesh (type: {obj.type.title()}). "
                            "Select the armature or one of its meshes.")


def find_meshes(context, armature):
    """Meshes bound to the armature. Returns (visible meshes, hidden meshes)."""
    visible, hidden = [], []
    for ob in context.view_layer.objects:
        if ob.type != 'MESH':
            continue
        bound = ob.parent == armature or any(
            m.type == 'ARMATURE' and m.object == armature for m in ob.modifiers)
        if not bound:
            continue
        (visible if ob.visible_get() else hidden).append(ob)
    return visible, hidden


def find_hips(armature):
    for bone in armature.data.bones:
        if bone.name.endswith(HIPS_SUFFIX):
            return bone
    return None


def has_root_bone(armature):
    hips = find_hips(armature)
    return hips is not None and hips.parent is not None and hips.parent.name.lower() == ROOT_NAME


def validate(context, armature):
    """Return (meshes, warnings). Raises SendToUnrealError with one specific cause."""
    warnings = []
    if context.mode != 'OBJECT':
        raise SendToUnrealError("Switch to Object Mode before sending the character.")
    if not armature.visible_get():
        raise SendToUnrealError(f"Armature '{armature.name}' is hidden. Show it and try again.")
    hips = find_hips(armature)
    if hips is None:
        raise SendToUnrealError(
            f"Armature '{armature.name}' has no bone ending in 'Hips'. This add-on is made for Mixamo-rigged characters.")
    if not _uniform(tuple(armature.scale)):
        raise SendToUnrealError(
            f"Armature scale is not uniform ({armature.scale[0]:.3f}, {armature.scale[1]:.3f}, "
            f"{armature.scale[2]:.3f}). Apply the scale or make it uniform first.")
    if hips.parent is not None and hips.parent.name.lower() != ROOT_NAME:
        raise SendToUnrealError(
            f"Bone '{hips.name}' is not at the top of the hierarchy (its parent is '{hips.parent.name}').")
    tops = [b.name for b in armature.data.bones if b.parent is None]
    if len(tops) > 1 and not has_root_bone(armature):
        warnings.append(f"The armature has several top-level bones ({', '.join(tops[:4])}). Unreal may import them oddly.")

    meshes, hidden = find_meshes(context, armature)
    if hidden:
        warnings.append("Hidden meshes were skipped: " + ", ".join(o.name for o in hidden))
    if not meshes:
        raise SendToUnrealError(f"No visible mesh is bound to armature '{armature.name}'.")
    for ob in meshes:
        if not _uniform(tuple(ob.scale)):
            warnings.append(f"Mesh '{ob.name}' has a non-uniform scale; apply it (Ctrl+A) if it looks stretched in Unreal.")
        if not any(m.type == 'ARMATURE' and m.object == armature for m in ob.modifiers):
            warnings.append(f"Mesh '{ob.name}' is only parented to the armature (no Armature modifier); it may not deform.")
    return meshes, warnings


# --------------------------------------------------------------------------- root bone
def _floor_z(meshes):
    z = None
    for ob in meshes:
        for corner in ob.bound_box:
            wz = (ob.matrix_world @ Vector(corner)).z
            z = wz if z is None else min(z, wz)
    return 0.0 if z is None else z


def _enter_edit(context, armature):
    context.view_layer.objects.active = armature
    bpy.ops.object.mode_set(mode='EDIT')


def add_temp_root(context, armature, meshes):
    """Create a 'root' bone under the floor and make Hips its child. Returns True if added."""
    if has_root_bone(armature):
        return False
    hips_name = find_hips(armature).name
    mw = armature.matrix_world
    inv = mw.inverted()
    hips_w = mw @ armature.data.bones[hips_name].head_local
    floor = _floor_z(meshes)
    head_w = Vector((hips_w.x, hips_w.y, floor))
    length = max(0.1 * abs(hips_w.z - floor), 0.01)
    tail_w = head_w + Vector((0.0, 0.0, length))
    try:
        _enter_edit(context, armature)
        eb = armature.data.edit_bones
        root = eb.new(ROOT_NAME)
        root.head = inv @ head_w
        root.tail = inv @ tail_w
        root.use_deform = False
        eb[hips_name].parent = root
        eb[hips_name].use_connect = False
    finally:
        if armature.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
    return True


def remove_temp_root(context, armature):
    """Undo add_temp_root: Hips goes back to the top, 'root' is deleted."""
    if ROOT_NAME not in armature.data.bones:
        return
    hips_name = find_hips(armature).name
    try:
        _enter_edit(context, armature)
        eb = armature.data.edit_bones
        if hips_name in eb:
            eb[hips_name].parent = None
        if ROOT_NAME in eb:
            eb.remove(eb[ROOT_NAME])
    finally:
        if armature.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')


# --------------------------------------------------------------------------- materials
def _principled(mat):
    if not mat or not mat.use_nodes or mat.node_tree is None:
        return None
    nodes = mat.node_tree.nodes
    outputs = [n for n in nodes if n.type == 'OUTPUT_MATERIAL']
    outputs.sort(key=lambda n: not n.is_active_output)
    for out in outputs:
        sock = out.inputs.get('Surface')
        if sock and sock.links and sock.links[0].from_node.type == 'BSDF_PRINCIPLED':
            return sock.links[0].from_node
    return next((n for n in nodes if n.type == 'BSDF_PRINCIPLED'), None)


def _skip_reroutes(link):
    while link is not None and link.from_node.type == 'REROUTE':
        inputs = link.from_node.inputs[0].links
        link = inputs[0] if inputs else None
    return link


def _linked_node(socket):
    if socket is None or not socket.links:
        return None, None
    link = _skip_reroutes(socket.links[0])
    return (link.from_node, link.from_socket) if link else (None, None)


def _image_of(node):
    if node is not None and node.type == 'TEX_IMAGE' and node.image is not None:
        return node.image
    return None


def _channel_source(socket):
    """Follow a Roughness/Metallic input. Returns (image, channel) or (None, None)."""
    node, from_sock = _linked_node(socket)
    if node is None:
        return None, None
    if node.type in {'SEPARATE_COLOR', 'SEPARATE_RGB'}:
        src, _ = _linked_node(node.inputs[0])
        return _image_of(src), _CHANNEL.get(from_sock.name, "R")
    if node.type == 'TEX_IMAGE':
        return _image_of(node), "R"
    return None, None


def _default(socket):
    try:
        v = socket.default_value
        return list(v) if hasattr(v, "__len__") else float(v)
    except Exception:
        return None


def alpha_setup(mat):
    """How the material's Alpha input is wired.
    Returns None (no alpha), ("ok", image) when the Base Color image's Alpha drives the BSDF Alpha,
    or ("unsupported", text) for any other alpha setup."""
    bsdf = _principled(mat)
    if bsdf is None or 'Alpha' not in bsdf.inputs or not bsdf.inputs['Alpha'].links:
        return None
    src, sock = _linked_node(bsdf.inputs['Alpha'])
    base, _ = _linked_node(bsdf.inputs['Base Color'])
    if (src is not None and base is not None and src == base and sock is not None
            and sock.identifier == 'Alpha' and _image_of(src) is not None):
        return ("ok", _image_of(src))
    label = src.bl_label if src is not None else "an unknown node"
    return ("unsupported", f"Alpha comes from '{label}'")


def emission_setup(mat):
    """Emission of the Principled BSDF. Returns None when the material does not emit, otherwise a dict:
    {"image": image or None, "color": [r, g, b] or None, "strength": float, "warnings": [text]}.
    Only an Image Texture connected straight to Emission Color is exported as a texture."""
    bsdf = _principled(mat)
    if bsdf is None or 'Emission Color' not in bsdf.inputs or 'Emission Strength' not in bsdf.inputs:
        return None
    warns = []
    strength_sock = bsdf.inputs['Emission Strength']
    if strength_sock.links:
        strength = 1.0
        warns.append(f"'{mat.name}': Emission Strength is connected to a node; exported as 1.0 "
                     "(change EmissiveStrength in the Unreal Material Instance).")
    else:
        strength = float(strength_sock.default_value)
    if strength <= 0.0:
        return None
    color_sock = bsdf.inputs['Emission Color']
    image, color = None, None
    if color_sock.links:
        node, _ = _linked_node(color_sock)
        image = _image_of(node)
        if image is None:
            warns.append(f"'{mat.name}': Emission Color goes through a '{node.bl_label if node else 'unknown'}' node. "
                         "Connect an Image Texture directly to export it.")
            return {"image": None, "color": None, "strength": strength, "warnings": warns, "skip": True}
    else:
        value = list(color_sock.default_value)[:3]
        if max(value) <= 0.0:
            return None
        color = value
    return {"image": image, "color": color, "strength": strength, "warnings": warns, "skip": False}


def alpha_mode_of(mat):
    return getattr(mat, "astu_alpha_mode", ALPHA_DEFAULT) if mat is not None else ALPHA_DEFAULT


def character_materials(context):
    """Unique materials of the visible meshes bound to the active character. [] when nothing valid is selected."""
    try:
        armature = find_armature(context)
        meshes, _hidden = find_meshes(context, armature)
    except SendToUnrealError:
        return []
    mats = []
    for ob in meshes:
        for slot in ob.material_slots:
            if slot.material is not None and slot.material not in mats:
                mats.append(slot.material)
    return mats


def analyze_material(mat):
    """Read how a material uses textures. Returns (info dict, warnings, images dict)."""
    warnings, images = [], {}
    info = {"base_color": None, "normal": None, "orm": None,
            "base_color_value": None, "roughness_value": None, "metallic_value": None,
            "emissive": None, "emissive_strength": None, "emissive_color_value": None}
    bsdf = _principled(mat)
    if bsdf is None:
        warnings.append(f"Material '{mat.name if mat else '(empty slot)'}' has no Principled BSDF; no textures exported.")
        return info, warnings, images

    # Base Color
    node, _ = _linked_node(bsdf.inputs['Base Color'])
    img = _image_of(node)
    if img is not None:
        images["base_color"] = img
        info["base_color"] = {"colorspace": img.colorspace_settings.name}
        mode = alpha_mode_of(mat)
        setup = alpha_setup(mat)
        if mode != ALPHA_DEFAULT:
            if setup is not None and setup[0] == "ok":
                info["base_color"]["alpha_mode"] = "glass" if mode == ALPHA_GLASS else "cutout"
                if _extension(img).lower() in (".jpg", ".jpeg"):
                    warnings.append(f"'{mat.name}': the Base Color is a JPEG, which cannot store transparency. "
                                    "Save it as PNG in Blender so the alpha reaches Unreal.")
            else:
                warnings.append(f"'{mat.name}': Alpha mode is set but the Base Color alpha is not connected to the "
                                "BSDF Alpha, so it is exported as Default (opaque).")
        elif setup is not None and setup[0] == "unsupported":
            warnings.append(f"'{mat.name}': {setup[1]}, which is not supported. The material is exported opaque.")
    else:
        info["base_color_value"] = _default(bsdf.inputs['Base Color'])
        if node is not None:
            warnings.append(f"'{mat.name}': Base Color goes through a '{node.bl_label}' node. Connect an Image Texture directly.")

    # Normal
    node, _ = _linked_node(bsdf.inputs['Normal'])
    if node is not None and node.type == 'NORMAL_MAP':
        src, _ = _linked_node(node.inputs['Color'])
        img = _image_of(src)
        if img is not None:
            images["normal"] = img
            info["normal"] = {"convention": getattr(node, "convention", "OPENGL")}
        else:
            warnings.append(f"'{mat.name}': Normal Map node has no Image Texture connected.")
    elif node is not None:
        warnings.append(f"'{mat.name}': Normal input uses a '{node.bl_label}' node, which is not supported (use Normal Map).")

    # Roughness / Metallic (packed map)
    r_img, r_ch = _channel_source(bsdf.inputs['Roughness'])
    m_img, m_ch = _channel_source(bsdf.inputs['Metallic'])
    info["roughness_value"] = _default(bsdf.inputs['Roughness'])
    info["metallic_value"] = _default(bsdf.inputs['Metallic'])
    if r_img is not None and m_img is not None and r_img != m_img:
        warnings.append(f"'{mat.name}': Roughness and Metallic use different images. Only the Roughness image is exported; "
                        "pack both in one map to export both.")
        m_img, m_ch = None, None
    packed = r_img or m_img
    if packed is not None:
        images["orm"] = packed
        info["orm"] = {"roughness_channel": r_ch if r_img is not None else None,
                       "metallic_channel": m_ch if m_img is not None else None,
                       "colorspace": packed.colorspace_settings.name}

    # Emission
    emi = emission_setup(mat)
    if emi is not None:
        warnings.extend(emi["warnings"])
        if not emi["skip"]:
            info["emissive_strength"] = emi["strength"]
            if emi["image"] is not None:
                images["emissive"] = emi["image"]
                info["emissive"] = {"colorspace": emi["image"].colorspace_settings.name}
            else:
                info["emissive_color_value"] = emi["color"]
    return info, warnings, images


# --------------------------------------------------------------------------- glass split
def _solid_face_mask(me, image):
    """Boolean array, one per polygon: True when the whole face samples solid alpha (>= SOLID_OPACITY)."""
    if np is None:
        raise SendToUnrealError("NumPy is not available in this Blender, cannot split glass from solid faces.")
    if not me.uv_layers.active:
        raise SendToUnrealError(f"Mesh '{me.name}' has no UV map, cannot split glass from solid faces.")
    width, height = image.size
    buf = np.empty(width * height * image.channels, dtype=np.float32)
    image.pixels.foreach_get(buf)
    alpha = buf[image.channels - 1::image.channels].reshape(height, width) if image.channels == 4 else None
    del buf
    if alpha is None:
        return np.ones(len(me.polygons), dtype=bool)  # no alpha channel at all: everything is solid

    n_loops, n_polys = len(me.loops), len(me.polygons)
    uv = np.empty(n_loops * 2, dtype=np.float32)
    me.uv_layers.active.data.foreach_get('uv', uv)
    uv = uv.reshape(-1, 2)
    starts = np.empty(n_polys, dtype=np.int32)
    totals = np.empty(n_polys, dtype=np.int32)
    me.polygons.foreach_get('loop_start', starts)
    me.polygons.foreach_get('loop_total', totals)

    def sample(points):
        x = np.clip((points[:, 0] % 1.0) * width, 0, width - 1).astype(np.int32)
        y = np.clip((points[:, 1] % 1.0) * height, 0, height - 1).astype(np.int32)
        return alpha[y, x]

    per_loop = sample(uv)
    face_min = np.minimum.reduceat(per_loop, starts)
    centers = np.add.reduceat(uv, starts, axis=0) / totals[:, None]
    return np.minimum(face_min, sample(centers)) >= SOLID_OPACITY


def apply_glass_splits(meshes):
    """For every Glass material, move its solid faces (cork, leather...) to a temporary copy without alpha.
    Returns (records to undo later, notes for the user, warnings). The scene is restored by restore_glass_splits."""
    records, notes, warnings = [], [], []
    done_meshes = set()
    solid_copies = {}
    for ob in meshes:
        me = ob.data
        for index, slot in enumerate(ob.material_slots):
            mat = slot.material
            if mat is None or alpha_mode_of(mat) != ALPHA_GLASS:
                continue
            setup = alpha_setup(mat)
            if setup is None or setup[0] != "ok" or slot.link != 'DATA':
                continue
            key = (me.name_full, index)
            if key in done_meshes:
                continue
            done_meshes.add(key)
            try:
                mask = _solid_face_mask(me, setup[1])
            except SendToUnrealError as exc:
                warnings.append(f"'{mat.name}': {exc} Exported as fully translucent.")
                continue
            idx = np.empty(len(me.polygons), dtype=np.int32)
            me.polygons.foreach_get('material_index', idx)
            solid = (idx == index) & mask
            n_solid, n_glass = int(solid.sum()), int(((idx == index) & ~mask).sum())
            if n_solid == 0:
                notes.append(f"{mat.name}: Glass, no solid faces found (fully translucent).")
                continue
            if n_glass == 0:
                warnings.append(f"'{mat.name}': every face of '{ob.name}' is solid (alpha is about 1). "
                                "Use Default for this material.")
                continue
            if mat.name not in solid_copies:
                copy = mat.copy()
                n = 1
                wanted = f"{mat.name}_Solid"
                while wanted in bpy.data.materials:
                    n += 1
                    wanted = f"{mat.name}_Solid{n}"
                copy.name = wanted
                try:
                    copy.astu_alpha_mode = ALPHA_DEFAULT
                except Exception:
                    pass
                bsdf = _principled(copy)
                for link in list(bsdf.inputs['Alpha'].links):
                    copy.node_tree.links.remove(link)
                bsdf.inputs['Alpha'].default_value = 1.0
                solid_copies[mat.name] = copy
            copy = solid_copies[mat.name]
            me.materials.append(copy)
            new_index = len(me.materials) - 1
            original = idx.copy()
            idx[solid] = new_index
            me.polygons.foreach_set('material_index', idx)
            me.update()
            records.append({"mesh": me, "slot": new_index, "original": original, "material": copy})
            notes.append(f"{mat.name}: Glass, {n_solid} solid faces separated as {copy.name}.")
    return records, notes, warnings


def restore_glass_splits(records):
    """Undo apply_glass_splits: original material indices, temporary slots and materials removed."""
    temp_materials = []
    for rec in reversed(records):
        me = rec["mesh"]
        try:
            me.polygons.foreach_set('material_index', rec["original"])
            if len(me.materials) > rec["slot"]:
                me.materials.pop(index=rec["slot"])
            me.update()
        except Exception:
            pass
        if rec["material"] not in temp_materials:
            temp_materials.append(rec["material"])
    for mat in temp_materials:
        try:
            bpy.data.materials.remove(mat)
        except Exception:
            pass


# --------------------------------------------------------------------------- textures
def _extension(image, source_path=None):
    if source_path:
        ext = Path(source_path).suffix.lower()
        if ext:
            return ext
    return _FORMAT_EXT.get(image.file_format, ".png")


def copy_image(image, dest_noext):
    """Copy an image to ``dest_noext + ext`` without changing the Blender image. Returns the path."""
    if image.source == 'TILED':
        raise SendToUnrealError(f"Image '{image.name}' uses UDIM tiles, which are not supported.")
    if image.packed_file is not None:
        dest = Path(str(dest_noext) + _extension(image))
        dest.write_bytes(image.packed_file.data)
        return dest
    raw = image.filepath_from_user()
    path = Path(bpy.path.abspath(raw)) if raw else None
    if path is None or not path.is_file():
        shown = raw or "(no file)"
        raise SendToUnrealError(
            f"Image '{image.name}' is missing on disk ({shown}). Fix the path or pack it, then try again.")
    dest = Path(str(dest_noext) + _extension(image, path))
    shutil.copy2(path, dest)
    return dest


def export_textures(materials, tex_dir):
    """Copy textures with standard names and return (manifest materials, warnings)."""
    warnings, out = [], {}
    tex_dir.mkdir(parents=True, exist_ok=True)
    copied = {}  # image name -> copied file: materials that share an image share one copy
    for mat in materials:
        info, warns, images = analyze_material(mat)
        warnings.extend(warns)
        base = sanitize(mat.name)
        for role, label in (("base_color", "BaseColor"), ("normal", "Normal"), ("orm", "ORM"),
                            ("emissive", "Emissive")):
            image = images.get(role)
            if image is None:
                continue
            dest = copied.get(image.name)
            if dest is None:
                dest = copy_image(image, tex_dir / f"{base}_{label}")
                copied[image.name] = dest
            info[role]["file"] = f"{tex_dir.name}/{dest.name}"
            info[role]["size"] = list(image.size)
        out[mat.name] = info
    return out, warnings


# --------------------------------------------------------------------------- export
def _set_selection(context, objects):
    for ob in context.view_layer.objects:
        ob.select_set(False)
    for ob in objects:
        ob.select_set(True)
    context.view_layer.objects.active = objects[0]


def export_fbx(context, armature, meshes, fbx_path):
    selected = list(context.selected_objects)
    active = context.view_layer.objects.active
    try:
        _set_selection(context, [armature] + list(meshes))
        result = bpy.ops.export_scene.fbx(
            filepath=str(fbx_path),
            check_existing=False,
            use_selection=True,
            object_types={'ARMATURE', 'MESH'},
            use_mesh_modifiers=True,
            mesh_smooth_type='FACE',
            add_leaf_bones=False,
            bake_anim=False,
            path_mode='STRIP',
            embed_textures=False,
        )
        if 'FINISHED' not in result:
            raise SendToUnrealError("Blender's FBX exporter did not finish. Check the system console for details.")
    finally:
        for ob in context.view_layer.objects:
            ob.select_set(False)
        for ob in selected:
            if ob.name in context.view_layer.objects:
                ob.select_set(True)
        if active is not None and active.name in context.view_layer.objects:
            context.view_layer.objects.active = active


def resolve_export_dir(export_dir):
    """Turn the panel path into an absolute folder. '//' needs a saved .blend."""
    if export_dir.startswith("//") and not bpy.data.filepath:
        raise SendToUnrealError("Save the .blend file first, or choose an export folder.")
    path = Path(bpy.path.abspath(export_dir))
    if not str(path).strip():
        raise SendToUnrealError("Choose an export folder.")
    return path


def default_character_name(armature, meshes):
    base = meshes[0].name if meshes else armature.name
    return sanitize(base)


def send_to_unreal(context, export_dir, character_name, add_root=True):
    """Run the whole export. Returns a summary dict (also written to manifest.json)."""
    armature = find_armature(context)
    meshes, warnings = validate(context, armature)

    name = sanitize(character_name) if character_name.strip() else default_character_name(armature, meshes)
    file_stem = name if name.lower().startswith("sk_") else f"SK_{name}"
    out_dir = resolve_export_dir(export_dir) / file_stem
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise SendToUnrealError(f"Cannot create the folder '{out_dir}': {exc}") from exc

    # 1) Glass materials: solid faces (cork, leather...) move to a temporary material while exporting
    split_records, alpha_notes, alpha_warnings = [], [], []
    root_added = False
    try:
        split_records, alpha_notes, alpha_warnings = apply_glass_splits(meshes)
        warnings.extend(alpha_warnings)

        materials = []
        for ob in meshes:
            for slot in ob.material_slots:
                if slot.material is not None and slot.material not in materials:
                    materials.append(slot.material)

        # 2) textures: a missing file stops everything (the finally below restores the scene)
        tex_dir = out_dir / "textures"
        mat_info, tex_warnings = export_textures(materials, tex_dir)
        warnings.extend(tex_warnings)
        for mat in materials:
            mode = (mat_info.get(mat.name, {}).get("base_color") or {}).get("alpha_mode")
            if mode == "cutout":
                alpha_notes.append(f"{mat.name}: Cutout (transparent areas are cut away).")
            minfo = mat_info.get(mat.name, {})
            if minfo.get("emissive") is not None:
                alpha_notes.append(f"{mat.name}: Emission texture exported (strength {minfo['emissive_strength']:g}).")
            elif minfo.get("emissive_color_value") is not None:
                alpha_notes.append(f"{mat.name}: Emission color exported (strength {minfo['emissive_strength']:g}).")

        # 3) FBX, with the temporary root bone
        try:
            if add_root:
                root_added = add_temp_root(context, armature, meshes)
            fbx_path = out_dir / f"{file_stem}.fbx"
            export_fbx(context, armature, meshes, fbx_path)
        finally:
            if root_added:
                remove_temp_root(context, armature)
    finally:
        restore_glass_splits(split_records)

    manifest = {
        "manifest_version": MANIFEST_VERSION,
        "generator": "Alpha Send to Unreal",
        "blender_version": bpy.app.version_string,
        "character": file_stem,
        "fbx": fbx_path.name,
        "armature": armature.name,
        "root_bone": ROOT_NAME if (root_added or has_root_bone(armature)) else None,
        "meshes": [ob.name for ob in meshes],
        "materials": mat_info,
        "warnings": warnings,
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return {"folder": str(out_dir), "fbx": str(fbx_path), "materials": len(mat_info),
            "root_added": root_added, "warnings": warnings, "notes": alpha_notes}
