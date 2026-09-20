# -*- coding:utf-8 -*-
"""MCP Socket — local Blender bridge for MCP clients, wire-compatible with blender-mcp 1.6.x.

Metadata lives in ``blender_manifest.toml`` (Blender 4.2+ extension standard).

On register, the TCP bridge auto-starts on the configured port (default 9876)
so MCP clients can connect without any UI interaction — same behaviour
as the upstream blender-mcp addon.
"""

from __future__ import annotations

import bpy

if "bpy" in locals():
    # Hot-reload: reload submodules in dependency order so code edits apply
    # without restarting Blender. Mirrors the STUKACH reload pattern.
    import importlib
    import sys as _sys
    from . import handlers, icons, logcap, pipeline, presets, queries, render, server, ui
    for _key, _mod in (
        ("mcp_socket.presets", presets),
        ("mcp_socket.pipeline", pipeline),
        ("mcp_socket.render", render),
        ("mcp_socket.icons", icons),
        ("mcp_socket.logcap", logcap),
        ("mcp_socket.queries", queries),
        ("mcp_socket.handlers", handlers),
        ("mcp_socket.server", server),
        ("mcp_socket.ui", ui),
    ):
        _sys.modules[_key] = _mod
        importlib.reload(_mod)

from . import logcap, queries, ui  # noqa: E402 — re-import after reload guard

# Extension-only add-on: all metadata lives in blender_manifest.toml
# (Blender 4.2+ extension standard). No legacy bl_info fallback.

_TAG = "[MCP_Socket]"
_ADDON_ID = "mcp_socket"


def _scene_port(scene) -> int:
    """Port to bind, reading scene prop or falling back to 9876."""
    try:
        port = scene.mcp_socket_port
        if isinstance(port, int) and 1024 <= port <= 65535:
            return port
    except AttributeError:
        pass
    return 9876


def _autostart() -> None:
    """Start the bridge on register if no server is running yet."""
    from . import server as _server
    scene = getattr(bpy.context, "scene", None)
    port = _scene_port(scene) if scene is not None else 9876
    try:
        addon = bpy.context.preferences.addons.get(__package__)
        auto_offset = bool(addon.preferences.auto_port_offset) if addon else True
    except (AttributeError, KeyError):
        auto_offset = True
    current = getattr(bpy.types, "mcp_socket_server", None)
    if current is None:
        bpy.types.mcp_socket_server = _server.MCPSocketServer(
            port=port, auto_offset=auto_offset)
    if not bpy.types.mcp_socket_server.running:
        ok = bpy.types.mcp_socket_server.start()
        if scene is not None:
            try:
                scene.mcp_socket_running = bpy.types.mcp_socket_server.running
            except AttributeError:
                pass
        if not ok:
            print(f"{_TAG} auto-start failed — see messages above")


def register() -> None:
    try:
        icons.register()
    except Exception as exc:  # noqa: BLE001
        print(f"{_TAG} icons.register: {exc}")

    try:
        logcap.install()
    except Exception as exc:  # noqa: BLE001
        print(f"{_TAG} logcap.install: {exc}")

    try:
        ui.register()
    except Exception as exc:  # noqa: BLE001
        print(f"{_TAG} ui.register: {exc}")

    _autostart()
    print(f"{_TAG} addon registered")


def unregister() -> None:
    current = getattr(bpy.types, "mcp_socket_server", None)
    if current is not None:
        try:
            current.stop()
        except Exception as exc:  # noqa: BLE001
            print(f"{_TAG} stop on unregister: {exc}")
        try:
            del bpy.types.mcp_socket_server
        except AttributeError:
            pass

    try:
        ui.unregister()
    except Exception as exc:  # noqa: BLE001
        print(f"{_TAG} ui.unregister: {exc}")

    try:
        icons.unregister()
    except Exception as exc:  # noqa: BLE001
        print(f"{_TAG} icons.unregister: {exc}")

    try:
        logcap.uninstall()
    except Exception as exc:  # noqa: BLE001
        print(f"{_TAG} logcap.uninstall: {exc}")
    print(f"{_TAG} addon unregistered")


if __name__ == "__main__":
    register()
