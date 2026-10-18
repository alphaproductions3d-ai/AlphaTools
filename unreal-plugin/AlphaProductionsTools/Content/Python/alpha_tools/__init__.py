# Alpha Tools (Alpha Productions) - menu and launcher.
import importlib
import os
import traceback

import unreal

__version__ = "0.1.8"
MENU_ID = "AlphaProductions.AlphaTools"

STEPS = {
    "materials": "Materials from Blender",
    "retarget": "Retarget animations + Blueprint",
}


def _run_step(step):
    mod = importlib.import_module(f"alpha_tools.{step}")
    importlib.reload(mod)          # always run the code that is on disk
    mod.main()


def run(*steps):
    """Run one or more steps in order (called from the menu)."""
    # Remember the selected assets: a step can change the Content Browser selection, and the next step needs it.
    selected = [a.get_path_name() for a in unreal.EditorUtilityLibrary.get_selected_assets()]
    for i, step in enumerate(steps):
        if i > 0 and selected:
            unreal.EditorAssetLibrary.sync_browser_to_objects(selected)
        unreal.log(f"[Alpha Tools] ===== {STEPS.get(step, step)} =====")
        try:
            _run_step(step)
        except Exception:
            unreal.log_error("[Alpha Tools] step failed:\n" + traceback.format_exc())
            return


def manual():
    """Open the HTML manual that ships inside the plugin."""
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    path = os.path.join(root, "Docs", "manual.html")
    if not os.path.isfile(path):
        unreal.log_warning(f"[Alpha Tools] manual not found: {path}")
        return
    uri = "file:///" + path.replace(os.sep, "/")
    try:
        unreal.SystemLibrary.launch_url(uri)
    except Exception:
        unreal.log_warning(f"[Alpha Tools] could not open the browser. Open this file by hand: {path}")


def about():
    text = (
        f"ALPHA TOOLS v{__version__}  -  Alpha Productions\n"
        "Turns a Mixamo character from Blender into a playable third-person character in Unreal.\n\n"
        "NEEDS\n"
        "- Blender 5.2+ with the 'Alpha Send to Unreal' add-on (it exports the FBX, textures and manifest.json).\n"
        "- Unreal 5.8 with the Third Person template content (Manny).\n"
        "- A character with the Mixamo skeleton.\n\n"
        "STEPS\n"
        "1. Export from Blender and import the FBX here (leave the export folder where it is).\n"
        "2. Select the Skeletal Mesh, then run '1. Materials from Blender'.\n"
        "3. Select the Skeletal Mesh again, then run '2. Retarget animations + Blueprint'.\n\n"
        "Full details, troubleshooting and known issues: menu Alpha Tools > Manual.\n"
        "Results and warnings appear in the Output Log ([PM] and [PL]).\n\n"
        "Contact: alphaproductions3d@gmail.com"
    )
    try:
        unreal.EditorDialog.show_message("Alpha Tools", text, unreal.AppMsgType.OK)
    except Exception:
        unreal.log(text)


def _entry(menu, section, name, label, tip, command):
    entry = unreal.ToolMenuEntry(name=name, type=unreal.MultiBlockType.MENU_ENTRY)
    entry.set_label(label)
    entry.set_tool_tip(tip)
    entry.set_string_command(unreal.ToolMenuStringCommandType.PYTHON, "", command)
    menu.add_menu_entry(section, entry)


def register_menu():
    menus = unreal.ToolMenus.get()
    main = menus.find_menu("LevelEditor.MainMenu")
    if main is None:
        unreal.log_warning("[Alpha Tools] main menu not found; menu not created.")
        return
    sub = main.add_sub_menu(main.get_name(), "", "AlphaTools", "Alpha Tools",
                            "Alpha Productions tools (select your character's Skeletal Mesh first)")
    sub.add_section("Mixamo", "Mixamo character")
    _entry(sub, "Mixamo", "AlphaTools.Materials", "1. Materials from Blender",
           "Step 1: import the textures and create the Material Instances from the Send to Unreal export",
           "import alpha_tools; alpha_tools.run('materials')")
    _entry(sub, "Mixamo", "AlphaTools.Retarget", "2. Retarget animations + Blueprint",
           "Step 2: retarget the Mannequin animations and make the playable Character Blueprint and GameMode",
           "import alpha_tools; alpha_tools.run('retarget')")
    sub.add_section("Info", "Info")
    _entry(sub, "Info", "AlphaTools.Manual", "Manual", "Open the Alpha Tools manual (HTML, EN/ES) in your browser",
           "import alpha_tools; alpha_tools.manual()")
    _entry(sub, "Info", "AlphaTools.About", "About Alpha Tools", "Version and contact",
           "import alpha_tools; alpha_tools.about()")
    menus.refresh_all_widgets()
    unreal.log(f"[Alpha Tools] v{__version__} menu ready (top menu bar: Alpha Tools).")
