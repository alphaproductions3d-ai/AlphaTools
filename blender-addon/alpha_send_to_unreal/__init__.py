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

import textwrap
import webbrowser
from pathlib import Path

import bpy
from bpy.props import BoolProperty, EnumProperty, PointerProperty, StringProperty
from bpy.types import Operator, Panel, PropertyGroup

from . import core

SUPPORT_EMAIL = "alphaproductions3d@gmail.com"


ALPHA_ITEMS = (
    (core.ALPHA_DEFAULT, "Default (ignore alpha)",
     "Exports a normal opaque material. The transparency is ignored"),
    (core.ALPHA_GLASS, "Glass (translucent)",
     "For glass, liquids and clear plastic: semi-transparent surfaces. Solid parts in the same material "
     "(cork, metal, leather...) are separated automatically while exporting; your scene is not changed"),
    (core.ALPHA_CUTOUT, "Cutout (alpha clip)",
     "For leaves, hair cards, fences, flags: hard-edged transparency. Transparent areas are cut away and "
     "the material is rendered two-sided"),
)

ALPHA_NOTES = {
    core.ALPHA_DEFAULT: "Alpha is ignored: exported as a normal opaque material.",
    core.ALPHA_GLASS: "Glass, liquid, clear plastic. Solid parts of this material (cork, metal, leather...) "
                      "are separated automatically while exporting. Your scene is not changed.",
    core.ALPHA_CUTOUT: "Leaves, hair cards, fences, flags. Transparent areas are cut away and the material "
                       "is rendered two-sided.",
}


def _wrapped(layout, text, context, icon='NONE'):
    """Label that wraps to the width of the panel."""
    width = max(24, int(context.region.width / (7.0 * max(context.preferences.system.ui_scale, 0.5))) - 4)
    col = layout.column(align=True)
    col.scale_y = 0.8
    for i, line in enumerate(textwrap.wrap(text, width) or [""]):
        col.label(text=line, icon=icon if i == 0 else 'NONE')


class ASTU_Props(PropertyGroup):
    export_dir: StringProperty(
        name="Export Folder",
        description="Folder where the character folder (FBX, textures and manifest.json) is created. "
                    "'//' means next to the saved .blend file",
        subtype='DIR_PATH',
        default="//",
    )
    character_name: StringProperty(
        name="Name",
        description="Name of the exported character. Leave empty to use the first mesh name. "
                    "'SK_' is added in front when it is missing",
        default="",
    )
    add_root: BoolProperty(
        name="Add Root Bone",
        description="Temporarily add a 'root' bone above Hips for the export, then remove it from your scene. "
                    "Unreal's retargeting expects this bone; Mixamo skeletons do not have it",
        default=True,
    )
    open_folder: BoolProperty(
        name="Open Folder After Export",
        description="Show the export folder in your file browser when the export finishes",
        default=False,
    )


class ASTU_OT_send(Operator):
    bl_idname = "alpha_send_to_unreal.send"
    bl_label = "Send to Unreal"
    bl_description = ("Export the selected Mixamo character as FBX with a root bone, copy its textures "
                      "with standard names and write a manifest.json for the Unreal import script")
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.mode == 'OBJECT'

    def execute(self, context):
        props = context.scene.alpha_send_to_unreal
        try:
            summary = core.send_to_unreal(context, props.export_dir, props.character_name, props.add_root)
        except core.SendToUnrealError as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        except OSError as exc:
            self.report({'ERROR'}, f"File error: {exc}")
            return {'CANCELLED'}
        for text in summary["warnings"]:
            self.report({'WARNING'}, text)
        for text in summary.get("notes", []):
            self.report({'INFO'}, text)
        self.report({'INFO'}, f"Exported {summary['materials']} material(s) to {summary['folder']}")
        if props.open_folder:
            bpy.ops.wm.path_open(filepath=summary["folder"])
        return {'FINISHED'}


class ASTU_OT_manual(Operator):
    bl_idname = "alpha_send_to_unreal.open_manual"
    bl_label = "Documentation"
    bl_description = "Open the add-on manual in your web browser"

    def execute(self, context):
        manual = Path(__file__).resolve().parent / "manual" / "manual.html"
        if not manual.is_file():
            self.report({'ERROR'}, "The manual file is missing from the add-on folder (manual/manual.html).")
            return {'CANCELLED'}
        webbrowser.open(manual.as_uri())
        return {'FINISHED'}


class ASTU_PT_main(Panel):
    bl_label = "Send to Unreal"
    bl_idname = "ASTU_PT_main"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Send to Unreal"

    def draw(self, context):
        layout = self.layout
        props = context.scene.alpha_send_to_unreal
        layout.use_property_split = True
        layout.use_property_decorate = False
        layout.prop(props, "export_dir")
        layout.prop(props, "character_name")
        layout.prop(props, "add_root")
        layout.prop(props, "open_folder")

        mats = core.character_materials(context)
        rows = [(m, core.alpha_setup(m)) for m in mats]
        rows = [(m, st) for m, st in rows if st is not None]
        box = layout.box()
        box.label(text="Transparency", icon='MATERIAL')
        if not mats:
            _wrapped(box, "Select the character's armature or a mesh to see its materials.", context, 'INFO')
        elif not rows:
            _wrapped(box, "No transparency found in this character's materials. Nothing to set.", context, 'INFO')
        else:
            _wrapped(box, "Pick how each material's alpha is used in Unreal. Materials without alpha are not listed.",
                     context, 'INFO')
            for mat, setup in rows:
                col = box.column(align=True)
                if setup[0] == "ok":
                    col.prop(mat, "astu_alpha_mode", text=mat.name)
                    _wrapped(col, ALPHA_NOTES.get(mat.astu_alpha_mode, ""), context)
                else:
                    col.label(text=mat.name, icon='ERROR')
                    _wrapped(col, f"{setup[1]}: not supported, exported opaque. Connect the Base Color "
                                  "image's Alpha to the BSDF Alpha to use it.", context)

        col = layout.column()
        col.scale_y = 1.4
        col.operator(ASTU_OT_send.bl_idname, icon='EXPORT')
        layout.label(text="Select the armature or a mesh first.", icon='INFO')


class ASTU_PT_support(Panel):
    bl_label = "Support"
    bl_idname = "ASTU_PT_support"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Send to Unreal"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        layout.operator(ASTU_OT_manual.bl_idname, icon='HELP')
        layout.label(text="Alpha Productions")
        layout.label(text=SUPPORT_EMAIL)


classes = (ASTU_Props, ASTU_OT_send, ASTU_OT_manual, ASTU_PT_main, ASTU_PT_support)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.alpha_send_to_unreal = PointerProperty(type=ASTU_Props)
    bpy.types.Material.astu_alpha_mode = EnumProperty(
        name="Alpha Mode",
        description="How this material's alpha (transparency) is used in Unreal",
        items=ALPHA_ITEMS,
        default=core.ALPHA_DEFAULT,
    )


def unregister():
    del bpy.types.Material.astu_alpha_mode
    del bpy.types.Scene.alpha_send_to_unreal
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
