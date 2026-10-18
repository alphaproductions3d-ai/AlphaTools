# Runs when Unreal starts with the Alpha Tools plugin enabled.
import traceback

import unreal

try:
    import alpha_tools
    alpha_tools.register_menu()
except Exception:
    unreal.log_error("[Alpha Tools] could not create the menu:\n" + traceback.format_exc())
