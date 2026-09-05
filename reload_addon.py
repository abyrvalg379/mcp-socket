# Run this in Blender's Python console (Scripting workspace) or via MCP to
# hot-reload ZCode_MCP after editing the source. Mirrors the STUKACH reload.
import bpy, sys

bpy.ops.preferences.addon_disable(module="zcode_mcp")

to_del = [k for k in sys.modules if k == 'zcode_mcp' or k.startswith('zcode_mcp.')]
for k in to_del:
    del sys.modules[k]

bpy.ops.preferences.addon_enable(module="zcode_mcp")
print("[ZCode_MCP] Reloaded OK")
