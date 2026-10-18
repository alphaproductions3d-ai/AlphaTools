# -----------------------------------------------------------------------------
# mixamo_setup_materials.py   (Alpha Productions)   v0.7.4
#
# HOW TO USE (nothing to edit):
#   1. In Blender, press "Send to Unreal" (add-on "Alpha Send to Unreal").
#   2. In Unreal, import the exported .fbx (drag it into the Content Browser, no animations).
#   3. Select the imported Skeletal Mesh in the Content Browser.
#   4. Run:   py "C:/Users/Horus/Downloads/mixamo_setup_materials.py"
#
# What it does:
#   - Finds manifest.json next to the .fbx that the selected mesh was imported from.
#   - Imports the textures with the right settings:
#       BaseColor -> sRGB on | Normal -> Normal compression, Flip Green if it came from Blender (OpenGL)
#       ORM -> sRGB off (Green = Roughness, Blue = Metallic)
#   - Creates (once) two master materials in /Game/AlphaTools/Materials and one Material Instance
#     per material of your character (in <mesh folder>/Materials), and assigns them to the mesh slots.
# It only CREATES assets (and re-imports its own textures when you run it again).
# Every log line starts with "[PM]".
# -----------------------------------------------------------------------------
import json
import os
import traceback

import struct
import tempfile
import zlib

import unreal

TAG = "[PM]"
MASTER_FOLDER = "/Game/AlphaTools/Materials"
PBR_NAME = "M_Alpha_PBR"          # BaseColor + Normal + ORM
SIMPLE_NAME = "M_Alpha_Simple"    # BaseColor + constant Roughness / Metallic
PBR_GLASS_NAME = "M_Alpha_PBR_Glass"        # PBR + glass look (opacity from the BaseColor alpha)
SIMPLE_GLASS_NAME = "M_Alpha_Simple_Glass"  # Simple + glass look
PBR_CUTOUT_NAME = "M_Alpha_PBR_Cutout"      # PBR + hard-edged transparency (leaves, hair cards)
SIMPLE_CUTOUT_NAME = "M_Alpha_Simple_Cutout"

mel = unreal.MaterialEditingLibrary


class Abort(Exception):
    pass


def log(m):
    unreal.log(f"{TAG} {m}")


def warn(m):
    unreal.log_warning(f"{TAG} {m}")


def section(t):
    log("")
    log(f"===== {t} =====")


# ------------------------------------------------------------------ inputs ---
def get_target_mesh():
    sel = unreal.EditorUtilityLibrary.get_selected_assets()
    meshes = [a for a in sel if isinstance(a, unreal.SkeletalMesh)]
    if not meshes:
        raise Abort("Select your character's Skeletal Mesh in the Content Browser first.")
    return meshes[0]


def mesh_folder(mesh):
    return mesh.get_path_name().split(".")[0].rsplit("/", 1)[0]


def find_manifest(mesh):
    """manifest.json lives in the same folder as the .fbx the mesh was imported from."""
    files = []
    try:
        data = mesh.get_editor_property("asset_import_data")
        files = [str(f) for f in data.extract_filenames()]
    except Exception as e:
        log(f"could not read the import source: {e!r}")
    for f in files:
        for folder in (os.path.dirname(f), os.path.dirname(os.path.dirname(f))):
            candidate = os.path.join(folder, "manifest.json")
            if os.path.isfile(candidate):
                return candidate
    shown = files[0] if files else "(unknown)"
    raise Abort("manifest.json not found next to the .fbx this mesh was imported from "
                f"({shown}). Export again with 'Send to Unreal' and re-import that .fbx.")


# ---------------------------------------------------------------- textures ---
def import_texture(src_file, dest_folder, asset_name):
    task = unreal.AssetImportTask()
    task.set_editor_property("filename", src_file)
    task.set_editor_property("destination_path", dest_folder)
    task.set_editor_property("destination_name", asset_name)
    task.set_editor_property("automated", True)
    task.set_editor_property("replace_existing", True)
    task.set_editor_property("save", False)
    unreal.AssetToolsHelpers.get_asset_tools().import_asset_tasks([task])
    tex = unreal.load_asset(f"{dest_folder}/{asset_name}")
    if tex is None:
        raise Abort(f"Texture import failed: {src_file}")
    return tex


def set_prop(obj, name, value):
    try:
        obj.set_editor_property(name, value)
        return True
    except Exception as e:
        warn(f"could not set {name} on {obj.get_name()}: {e!r}")
        return False


def configure_texture(tex, role, convention):
    if role in ("base_color", "emissive"):
        set_prop(tex, "srgb", True)
        set_prop(tex, "compression_settings", unreal.TextureCompressionSettings.TC_DEFAULT)
    elif role == "normal":
        set_prop(tex, "compression_settings", unreal.TextureCompressionSettings.TC_NORMALMAP)
        set_prop(tex, "srgb", False)
        # Blender normals are OpenGL (Y+); Unreal wants DirectX (Y-) -> flip green
        set_prop(tex, "flip_green_channel", str(convention).upper() == "OPENGL")
    elif role == "orm":
        # Unreal's importer sometimes mistakes a packed map for a normal map: force plain data settings
        set_prop(tex, "srgb", False)
        set_prop(tex, "compression_settings", unreal.TextureCompressionSettings.TC_DEFAULT)
        set_prop(tex, "flip_green_channel", False)
        set_prop(tex, "lod_group", unreal.TextureGroup.TEXTUREGROUP_WORLD)
    if role == "normal":
        set_prop(tex, "lod_group", unreal.TextureGroup.TEXTUREGROUP_WORLD_NORMAL_MAP)
    unreal.EditorAssetLibrary.save_loaded_asset(tex, only_if_is_dirty=False)


# ---------------------------------------------------------------- masters ----
def create_or_load(folder, name, cls, factory):
    path = f"{folder}/{name}"
    if unreal.EditorAssetLibrary.does_asset_exist(path):
        return unreal.load_asset(path), False
    obj = unreal.AssetToolsHelpers.get_asset_tools().create_asset(name, folder, cls, factory)
    if obj is None:
        raise Abort(f"create_asset returned None for {path}")
    log(f"created {path}")
    return obj, True


def tex_param(mat, name, sampler, default_path, x, y):
    node = mel.create_material_expression(mat, unreal.MaterialExpressionTextureSampleParameter2D, x, y)
    set_prop(node, "parameter_name", name)
    set_prop(node, "sampler_type", sampler)
    default = unreal.load_asset(default_path) or unreal.load_asset(f"{default_path}.{default_path.rsplit('/', 1)[1]}")
    if default is not None:
        set_prop(node, "texture", default)
    else:
        warn(f"default texture not found: {default_path} (the master will not compile until instances set {name})")
    return node


def scalar_param(mat, name, value, x, y):
    node = mel.create_material_expression(mat, unreal.MaterialExpressionScalarParameter, x, y)
    set_prop(node, "parameter_name", name)
    set_prop(node, "default_value", value)
    return node


WHITE = "/Engine/EngineResources/WhiteSquareTexture"
FLAT_NORMAL = "/Engine/EngineMaterials/DefaultNormal"
DEFAULT_ORM = "/Game/AlphaTools/Textures/T_Alpha_Default_ORM"   # linear: AO 1, Roughness 0.5, Metallic 0
MASTER_REV = "AlphaMasterRev3"      # marker: masters without it were made by an older version and are rebuilt


def _write_png(path, rgb, size=4):
    def chunk(tag, data):
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)
    row = b"\x00" + bytes(rgb) * size
    png = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(row * size)) + chunk(b"IEND", b""))
    with open(path, "wb") as f:
        f.write(png)


def ensure_default_orm():
    """Default texture for the ORM node. Unreal's WhiteSquareTexture is sRGB and cannot be the default of a
    Linear Color sampler (the master would not compile), so a tiny linear texture is created once."""
    if unreal.EditorAssetLibrary.does_asset_exist(DEFAULT_ORM):
        return DEFAULT_ORM
    folder, name = DEFAULT_ORM.rsplit("/", 1)
    unreal.EditorAssetLibrary.make_directory(folder)
    tmp = os.path.join(tempfile.gettempdir(), f"{name}.png")
    _write_png(tmp, (255, 128, 0))
    tex = import_texture(tmp, folder, name)
    configure_texture(tex, "orm", "OPENGL")
    log(f"created default linear texture {DEFAULT_ORM}")
    return DEFAULT_ORM


def ensure_skeletal_usage(mat):
    """Characters use Skeletal Meshes: enable that usage up front (also on masters from older runs)."""
    try:
        if not mat.get_editor_property("used_with_skeletal_mesh"):
            mat.set_editor_property("used_with_skeletal_mesh", True)
            mel.recompile_material(mat)
            unreal.EditorAssetLibrary.save_loaded_asset(mat, only_if_is_dirty=False)
            log(f"enabled 'Used with Skeletal Mesh' on {mat.get_name()}")
    except Exception as e:
        warn(f"could not enable skeletal mesh usage on {mat.get_name()}: {e!r}")


def add_emissive(mat):
    """Emission, off by default: Emissive = EmissiveMap x EmissiveColor x EmissiveStrength, and
    EmissiveStrength is 0 until a Material Instance raises it (plain nodes only, no static switch)."""
    tex = tex_param(mat, "EmissiveMap", unreal.MaterialSamplerType.SAMPLERTYPE_COLOR, WHITE, -700, 900)
    color = mel.create_material_expression(mat, unreal.MaterialExpressionVectorParameter, -700, 1100)
    set_prop(color, "parameter_name", "EmissiveColor")
    set_prop(color, "default_value", unreal.LinearColor(1.0, 1.0, 1.0, 1.0))
    strength = scalar_param(mat, "EmissiveStrength", 0.0, -700, 1250)
    scalar_param(mat, MASTER_REV, 0.0, -700, 1400)      # marker (see MASTER_REV)
    mul1 = mel.create_material_expression(mat, unreal.MaterialExpressionMultiply, -450, 950)
    mul2 = mel.create_material_expression(mat, unreal.MaterialExpressionMultiply, -300, 1000)
    for src, out, dst, name in ((tex, "RGB", mul1, "A"), (color, "", mul1, "B"),
                                (mul1, "", mul2, "A"), (strength, "", mul2, "B")):
        if not mel.connect_material_expressions(src, out, dst, name):
            warn(f"emissive network: could not connect {out or 'out'} -> {name}")
    mel.connect_material_property(mul2, "", unreal.MaterialProperty.MP_EMISSIVE_COLOR)


def make_translucent(base, mat):
    """Glass look for materials whose Base Color alpha drives the opacity in Blender.
    Translucent blend + 'Surface ForwardShading' lighting (gives real specular highlights and
    reflections, the default 'Volumetric' mode looks flat) + opacity from the texture alpha that gets
    more opaque at grazing angles (Fresnel) + a light Fresnel refraction.
    Tweakable in every Material Instance: OpacityScale, EdgeOpacity, IOR."""
    set_prop(mat, "blend_mode", unreal.BlendMode.BLEND_TRANSLUCENT)
    set_prop(mat, "translucency_lighting_mode", unreal.TranslucencyLightingMode.TLM_SURFACE_PER_PIXEL_LIGHTING)

    def link(src, src_out, dst, dst_in):
        if not mel.connect_material_expressions(src, src_out, dst, dst_in):
            warn(f"glass network: could not connect {src_out or 'out'} -> {dst_in or 'in'}")

    scale = scalar_param(mat, "OpacityScale", 1.0, -1100, 560)
    edge = scalar_param(mat, "EdgeOpacity", 0.35, -1100, 760)
    ior = scalar_param(mat, "IOR", 1.1, -700, 900)
    fres = mel.create_material_expression(mat, unreal.MaterialExpressionFresnel, -1100, 660)
    one = mel.create_material_expression(mat, unreal.MaterialExpressionConstant, -700, 820)
    set_prop(one, "r", 1.0)
    mul_a = mel.create_material_expression(mat, unreal.MaterialExpressionMultiply, -800, 520)
    mul_f = mel.create_material_expression(mat, unreal.MaterialExpressionMultiply, -800, 700)
    add = mel.create_material_expression(mat, unreal.MaterialExpressionAdd, -600, 600)
    sat = mel.create_material_expression(mat, unreal.MaterialExpressionSaturate, -450, 600)
    lerp = mel.create_material_expression(mat, unreal.MaterialExpressionLinearInterpolate, -450, 820)
    link(base, "A", mul_a, "A")
    link(scale, "", mul_a, "B")
    link(fres, "", mul_f, "A")
    link(edge, "", mul_f, "B")
    link(mul_a, "", add, "A")
    link(mul_f, "", add, "B")
    link(add, "", sat, "")
    link(one, "", lerp, "A")
    link(ior, "", lerp, "B")
    link(fres, "", lerp, "Alpha")
    mel.connect_material_property(sat, "", unreal.MaterialProperty.MP_OPACITY)
    mel.connect_material_property(lerp, "", unreal.MaterialProperty.MP_REFRACTION)


def make_cutout(base, mat):
    """Hard-edged transparency (leaves, hair cards, fences): Masked blend, two-sided.
    The alpha of the BaseColor texture cuts the surface away; ClipThreshold (0..1, default 0.5)
    can be changed in each Material Instance to cut more or less."""
    set_prop(mat, "blend_mode", unreal.BlendMode.BLEND_MASKED)
    set_prop(mat, "two_sided", True)
    set_prop(mat, "opacity_mask_clip_value", 0.5)
    thr = scalar_param(mat, "ClipThreshold", 0.5, -1100, 560)
    half = mel.create_material_expression(mat, unreal.MaterialExpressionConstant, -1100, 700)
    set_prop(half, "r", 0.5)
    shift = mel.create_material_expression(mat, unreal.MaterialExpressionSubtract, -900, 640)
    add = mel.create_material_expression(mat, unreal.MaterialExpressionAdd, -700, 560)
    for src, out, dst, name in ((half, "", shift, "A"), (thr, "", shift, "B"),
                                (base, "A", add, "A"), (shift, "", add, "B")):
        if not mel.connect_material_expressions(src, out, dst, name):
            warn(f"cutout network: could not connect {out or 'out'} -> {name}")
    mel.connect_material_property(add, "", unreal.MaterialProperty.MP_OPACITY_MASK)


def apply_alpha_mode(base, mat, mode):
    """mode: None (opaque), 'glass' or 'cutout'."""
    if mode == "glass":
        safe_translucent(base, mat)
    elif mode == "cutout":
        make_cutout(base, mat)


def safe_translucent(base, mat):
    try:
        make_translucent(base, mat)
    except Exception as e:
        warn(f"glass network failed ({e!r}); using plain translucent opacity instead.")
        set_prop(mat, "blend_mode", unreal.BlendMode.BLEND_TRANSLUCENT)
        mel.connect_material_property(base, "A", unreal.MaterialProperty.MP_OPACITY)


def reset_if_broken(mat):
    """Masters made by older runs may have texture nodes with no default texture ('Found NULL, requires
    Texture2D'): the master then fails to compile and every instance shows the grey checker.
    The masters are 100% generated by this script, so a broken one is simply rebuilt (instances keep
    their texture overrides because the parameter names do not change)."""
    try:
        has_textures = len(mel.get_used_textures(mat)) > 0
        has_emissive = MASTER_REV in [str(n) for n in mel.get_scalar_parameter_names(mat)]
        if has_textures and has_emissive:
            return False
    except Exception as e:
        warn(f"could not check master {mat.get_name()}: {e!r}")
        return False
    log(f"master {mat.get_name()} is from an older version (empty texture nodes or no emission): rebuilding it")
    try:
        mel.delete_all_material_expressions(mat)
    except Exception as e:
        warn(f"could not clear master {mat.get_name()}: {e!r}")
        return False
    return True


def build_pbr_master(mode=None):
    name = {"glass": PBR_GLASS_NAME, "cutout": PBR_CUTOUT_NAME}.get(mode, PBR_NAME)
    mat, new = create_or_load(MASTER_FOLDER, name,
                              unreal.Material, unreal.MaterialFactoryNew())
    if not new:
        new = reset_if_broken(mat)
    if new:
        base = tex_param(mat, "BaseColor", unreal.MaterialSamplerType.SAMPLERTYPE_COLOR, WHITE, -700, -300)
        nrm = tex_param(mat, "Normal", unreal.MaterialSamplerType.SAMPLERTYPE_NORMAL, FLAT_NORMAL, -700, 0)
        orm = tex_param(mat, "ORM", unreal.MaterialSamplerType.SAMPLERTYPE_LINEAR_COLOR, ensure_default_orm(), -700, 300)
        mel.connect_material_property(base, "RGB", unreal.MaterialProperty.MP_BASE_COLOR)
        mel.connect_material_property(nrm, "RGB", unreal.MaterialProperty.MP_NORMAL)
        mel.connect_material_property(orm, "G", unreal.MaterialProperty.MP_ROUGHNESS)
        mel.connect_material_property(orm, "B", unreal.MaterialProperty.MP_METALLIC)
        apply_alpha_mode(base, mat, mode)
        add_emissive(mat)
        set_prop(mat, "used_with_skeletal_mesh", True)
        mel.recompile_material(mat)
        unreal.EditorAssetLibrary.save_loaded_asset(mat, only_if_is_dirty=False)
    ensure_skeletal_usage(mat)
    return mat


def build_simple_master(mode=None):
    name = {"glass": SIMPLE_GLASS_NAME, "cutout": SIMPLE_CUTOUT_NAME}.get(mode, SIMPLE_NAME)
    mat, new = create_or_load(MASTER_FOLDER, name,
                              unreal.Material, unreal.MaterialFactoryNew())
    if not new:
        new = reset_if_broken(mat)
    if new:
        base = tex_param(mat, "BaseColor", unreal.MaterialSamplerType.SAMPLERTYPE_COLOR, WHITE, -700, -200)
        rough = scalar_param(mat, "Roughness", 0.5, -700, 100)
        metal = scalar_param(mat, "Metallic", 0.0, -700, 250)
        mel.connect_material_property(base, "RGB", unreal.MaterialProperty.MP_BASE_COLOR)
        mel.connect_material_property(rough, "", unreal.MaterialProperty.MP_ROUGHNESS)
        mel.connect_material_property(metal, "", unreal.MaterialProperty.MP_METALLIC)
        apply_alpha_mode(base, mat, mode)
        add_emissive(mat)
        set_prop(mat, "used_with_skeletal_mesh", True)
        mel.recompile_material(mat)
        unreal.EditorAssetLibrary.save_loaded_asset(mat, only_if_is_dirty=False)
    ensure_skeletal_usage(mat)
    return mat


# --------------------------------------------------------------- instances ---
def make_instance(folder, name, parent):
    mi, _ = create_or_load(folder, name, unreal.MaterialInstanceConstant,
                           unreal.MaterialInstanceConstantFactoryNew())
    set_prop(mi, "parent", parent)
    return mi


class Masters(dict):
    """Master materials are created only when a character material needs them: key = (uses_pbr, alpha mode)."""
    def __missing__(self, key):
        use_pbr, mode = key
        self[key] = build_pbr_master(mode) if use_pbr else build_simple_master(mode)
        return self[key]


def process_material(mat_name, info, manifest_dir, tex_folder, mat_folder, masters, imported=None):
    imported = {} if imported is None else imported
    safe = "".join(c if c.isalnum() or c in "_-" else "_" for c in mat_name)
    textures = {}
    for role in ("base_color", "normal", "orm", "emissive"):
        entry = info.get(role)
        if not entry or not entry.get("file"):
            continue
        src = os.path.normpath(os.path.join(manifest_dir, entry["file"]))
        if not os.path.isfile(src):
            warn(f"{mat_name}: texture file not found: {src}")
            continue
        suffix = {"base_color": "BaseColor", "normal": "Normal", "orm": "ORM", "emissive": "Emissive"}[role]
        key = (src, role)
        if key in imported:  # several materials use the same file: import it only once
            textures[role] = imported[key]
            log(f"{mat_name}: {role} -> {imported[key].get_path_name()} (shared)")
            continue
        tex = import_texture(src, tex_folder, f"T_{safe}_{suffix}")
        configure_texture(tex, role, (info.get("normal") or {}).get("convention", "OPENGL"))
        textures[role] = imported[key] = tex
        log(f"{mat_name}: {role} -> {tex.get_path_name()}")

    orm_info = info.get("orm") or {}
    if orm_info and (orm_info.get("roughness_channel") not in (None, "G") or
                     orm_info.get("metallic_channel") not in (None, "B")):
        warn(f"{mat_name}: ORM channels are {orm_info.get('roughness_channel')}/{orm_info.get('metallic_channel')} "
             "but this version only supports Green = Roughness and Blue = Metallic.")

    use_pbr = all(k in textures for k in ("base_color", "normal", "orm"))
    mode = (info.get("base_color") or {}).get("alpha_mode") if "base_color" in textures else None
    if mode not in ("glass", "cutout"):
        mode = None
    if mode == "glass":
        log(f"{mat_name}: Glass (parameters in the Material Instance: OpacityScale, EdgeOpacity, IOR)")
    elif mode == "cutout":
        log(f"{mat_name}: Cutout (two-sided; parameter in the Material Instance: ClipThreshold)")
    mi = make_instance(mat_folder, f"MI_{safe}", masters[(use_pbr, mode)])
    if "base_color" in textures:
        mel.set_material_instance_texture_parameter_value(mi, "BaseColor", textures["base_color"])
    if use_pbr:
        mel.set_material_instance_texture_parameter_value(mi, "Normal", textures["normal"])
        mel.set_material_instance_texture_parameter_value(mi, "ORM", textures["orm"])
    else:
        rough, metal = info.get("roughness_value"), info.get("metallic_value")
        if isinstance(rough, (int, float)):
            mel.set_material_instance_scalar_parameter_value(mi, "Roughness", float(rough))
        if isinstance(metal, (int, float)):
            mel.set_material_instance_scalar_parameter_value(mi, "Metallic", float(metal))
        missing = [k for k in ("normal", "orm") if k not in textures]
        log(f"{mat_name}: simple material (no {', '.join(missing)} map) using Roughness/Metallic values")
    strength, color = info.get("emissive_strength"), info.get("emissive_color_value")
    if strength and ("emissive" in textures or color):
        mel.set_material_instance_scalar_parameter_value(mi, "EmissiveStrength", float(strength))
        if "emissive" in textures:
            mel.set_material_instance_texture_parameter_value(mi, "EmissiveMap", textures["emissive"])
            log(f"{mat_name}: Emission texture, strength {strength:g} (parameters: EmissiveMap, EmissiveColor, EmissiveStrength)")
        else:
            mel.set_material_instance_vector_parameter_value(
                mi, "EmissiveColor", unreal.LinearColor(color[0], color[1], color[2], 1.0))
            log(f"{mat_name}: Emission color, strength {strength:g} (parameters: EmissiveColor, EmissiveStrength)")
    mel.update_material_instance(mi)
    for pname, role in (("BaseColor", "base_color"), ("Normal", "normal"), ("ORM", "orm"), ("EmissiveMap", "emissive")):
        if role in textures and not mel.get_material_instance_texture_parameter_value(mi, pname):
            warn(f"{mat_name}: parameter {pname} did not keep its texture. Open {mi.get_name()} and check it.")
    unreal.EditorAssetLibrary.save_loaded_asset(mi, only_if_is_dirty=False)
    return mi


def assign_slots(mesh, instances):
    mats = list(mesh.get_editor_property("materials"))
    names = [str(m.get_editor_property("material_slot_name")) for m in mats]
    log(f"mesh slots: {names}")
    lowered = {k.lower(): v for k, v in instances.items()}
    changed = 0
    for slot in mats:
        slot_name = str(slot.get_editor_property("material_slot_name"))
        mi = lowered.get(slot_name.lower())
        if mi is None:
            warn(f"slot '{slot_name}' has no matching material in the manifest; left unchanged.")
            continue
        slot.set_editor_property("material_interface", mi)
        changed += 1
    mesh.set_editor_property("materials", mats)
    unreal.EditorAssetLibrary.save_loaded_asset(mesh, only_if_is_dirty=False)
    return changed



# ------------------------------------------------------------------ notice ---
def notice_fbx_leftovers(originals, instances, folder):
    """Nothing is deleted automatically: just tell the user which FBX-import materials are now unused."""
    ours = {m.get_path_name() for m in instances.values()}
    names = []
    for m in originals:
        if m is not None and m.get_path_name() not in ours and m.get_path_name().startswith(folder + "/"):
            names.append(m.get_name())
    if names:
        section("TIDY UP (optional)")
        log("These materials were created by Unreal's FBX import and are no longer used by the character:")
        log("    " + ", ".join(sorted(set(names))))
        log("You can delete them (and the empty Textures folder, if any) from the Content Browser.")


# -------------------------------------------------------------------- main ---
def main():
    log("Material setup started.")
    try:
        mesh = get_target_mesh()
        log(f"character mesh: {mesh.get_path_name()}")
        manifest_path = find_manifest(mesh)
        manifest_dir = os.path.dirname(manifest_path)
        with open(manifest_path, "r", encoding="utf-8") as fh:
            manifest = json.load(fh)
        log(f"manifest: {manifest_path} ({len(manifest.get('materials', {}))} material(s))")
        for w in manifest.get("warnings", []):
            warn(f"from Blender: {w}")

        folder = mesh_folder(mesh)
        tex_folder, mat_folder = f"{folder}/Textures", f"{folder}/Materials"
        for p in (MASTER_FOLDER, tex_folder, mat_folder):
            unreal.EditorAssetLibrary.make_directory(p)

        section("MASTER MATERIALS")
        masters = Masters()

        section("TEXTURES + MATERIAL INSTANCES")
        instances, imported = {}, {}
        for mat_name, info in manifest.get("materials", {}).items():
            instances[mat_name] = process_material(mat_name, info, manifest_dir, tex_folder, mat_folder, masters, imported)

        section("ASSIGN TO MESH")
        originals = [m.get_editor_property("material_interface") for m in mesh.get_editor_property("materials")]
        n = assign_slots(mesh, instances)
        notice_fbx_leftovers(originals, instances, folder)
        log("")
        log(f"DONE. {n} slot(s) now use the new Material Instances.")
        log("If a material looks wrong, open the Material Instance and check its texture parameters.")
        log(f"To see the node graph: open a Material Instance, and double-click its 'Parent' (or open {MASTER_FOLDER}).")
    except Abort as e:
        warn(f"STOPPED: {e}")
    except Exception:
        warn("UNEXPECTED ERROR:\n" + traceback.format_exc())

