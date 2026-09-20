# Run this in Blender's Python console (Scripting workspace) or via MCP to
# hot-reload MCP_Socket after editing the source. Mirrors the STUKACH reload.
import bpy, sys

bpy.ops.preferences.addon_disable(module="mcp_socket")

to_del = [k for k in sys.modules if k == 'mcp_socket' or k.startswith('mcp_socket.')]
for k in to_del:
    del sys.modules[k]

bpy.ops.preferences.addon_enable(module="mcp_socket")
print("[MCP_Socket] Reloaded OK")
