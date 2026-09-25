"""Enable the blender-mcp add-on in a GUI Blender session.

Usage (virtual display in CI/sandbox):
    xvfb-run -a -s "-screen 0 1600x1000x24" blender --python tools/mcp/start_blender_mcp.py

The add-on (https://github.com/ahujasid/blender-mcp, addon.py) opens its socket bridge on
port 9876; tools/mcp/blender_mcp_run.py then drives Blender through the MCP server.
"""
import os
import shutil
import sys

import addon_utils
import bpy

ADDON_SRC = os.environ.get("BLENDER_MCP_ADDON", "/tmp/work/blender-mcp/addon.py")
MODULE = "blender_mcp_addon"

addons_dir = bpy.utils.user_resource("SCRIPTS", path="addons", create=True)
shutil.copyfile(ADDON_SRC, os.path.join(addons_dir, MODULE + ".py"))
# A freshly created user add-on dir is not on sys.path until Blender restarts.
if addons_dir not in sys.path:
    sys.path.append(addons_dir)
addon_utils.modules_refresh()
bpy.ops.preferences.addon_enable(module=MODULE)


def _ensure_server():
    scene = bpy.context.scene
    if scene is None:
        return 0.5
    scene.blendermcp_auto_start_server = True
    server = getattr(bpy.types, "blendermcp_server", None)
    if server is None or not server.running:
        try:
            bpy.ops.blendermcp.start_server()
        except Exception as exc:  # auto-start timer may already own the port
            print("blender-mcp start_server:", exc)
    server = getattr(bpy.types, "blendermcp_server", None)
    print("blender-mcp server running:", bool(server and server.running), "port", scene.blendermcp_port)
    return None


bpy.app.timers.register(_ensure_server, first_interval=2.0)
