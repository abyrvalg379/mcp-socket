# -*- coding:utf-8 -*-
"""Command handlers for MCP_Socket.

Every handler runs on Blender's main thread (scheduled by server.py via
``bpy.app.timers``) and returns a JSON-serialisable result. The server wraps
it into ``{"status": "success", "result": ...}``. Raising here becomes
``{"status": "error", "message": ...}``.

Wire protocol is the same as blender-mcp 1.6.x: ``send_command(type, params)``
→ ``handler(**params)``.
"""

from __future__ import annotations

import io
import mathutils
from contextlib import redirect_stdout
from typing import Any, Dict, List

import bpy

from . import logcap, queries

# Cap the number of objects reported by get_scene_info to keep responses small.
# The full list is intentionally truncated — MCP clients call get_object_info
# per-name when they need detail.
_SCENE_INFO_MAX_OBJECTS = 10


def get_telemetry_consent() -> Dict[str, bool]:
    """Telemetry consent handshake.

    blender-mcp 1.6.x calls this at startup and blocks until an answer arrives.
    Returning ``{"consent": False}`` tells it to send only minimal anonymous
    events — that is the safe default for a local bridge. Users wanting richer
    reporting can flip the preference in the MCP Socket panel.
    """
    consent = False
    try:
        prefs = bpy.context.preferences.addons.get(__package__)
        if prefs is not None:
            consent = bool(prefs.preferences.telemetry_consent)
    except (AttributeError, KeyError):
        consent = False
    return {"consent": consent}


def get_scene_info() -> Dict[str, Any]:
    """Minimal scene overview: name, object/material counts, first objects."""
    scene = bpy.context.scene
    objects: List[Dict[str, Any]] = []
    for i, obj in enumerate(scene.objects):
        if i >= _SCENE_INFO_MAX_OBJECTS:
            break
        objects.append({
            "name": obj.name,
            "type": obj.type,
            "location": [round(float(obj.location.x), 2),
                         round(float(obj.location.y), 2),
                         round(float(obj.location.z), 2)],
        })
    return {
        "name": scene.name,
        "object_count": len(scene.objects),
        "objects": objects,
        "materials_count": len(bpy.data.materials),
    }


def get_object_info(name: str) -> Dict[str, Any]:
    """Detailed info for one object by name."""
    obj = bpy.data.objects.get(name)
    if obj is None:
        raise ValueError(f"Object not found: {name}")

    info: Dict[str, Any] = {
        "name": obj.name,
        "type": obj.type,
        "location": [obj.location.x, obj.location.y, obj.location.z],
        "rotation": [obj.rotation_euler.x, obj.rotation_euler.y, obj.rotation_euler.z],
        "scale": [obj.scale.x, obj.scale.y, obj.scale.z],
        "visible": obj.visible_get(),
        "materials": [s.material.name for s in obj.material_slots if s.material],
    }

    if obj.type == "MESH":
        info["world_bounding_box"] = _world_aabb(obj)
        me = obj.data
        info["mesh"] = {
            "vertices": len(me.vertices),
            "edges": len(me.edges),
            "polygons": len(me.polygons),
        }
    return info


def execute_code(code: str) -> Dict[str, Any]:
    """Run arbitrary bpy Python code, return captured stdout.

    Powerful and dangerous by design — matches blender-mcp's execute_code so
    MCP clients can drive Blender fully.
    """
    namespace: Dict[str, Any] = {"bpy": bpy}
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        exec(code, namespace)  # noqa: S102 — intentional, MCP bridge contract
    return {"executed": True, "result": buffer.getvalue()}


def get_viewport_screenshot(
    filepath: str,
    max_size: int = 800,
    format: str = "png",
) -> Dict[str, Any]:
    """Capture the active 3D viewport to ``filepath`` and downscale."""
    if not filepath:
        return {"error": "No filepath provided"}

    area = None
    for a in bpy.context.screen.areas:
        if a.type == "VIEW_3D":
            area = a
            break
    if area is None:
        return {"error": "No 3D viewport found"}

    with bpy.context.temp_override(area=area):
        bpy.ops.screen.screenshot_area(filepath=filepath)

    img = bpy.data.images.load(filepath)
    width, height = img.size
    if max(width, height) > max_size:
        scale = max_size / max(width, height)
        width, height = int(width * scale), int(height * scale)
        img.scale(width, height)
        img.file_format = format.upper()
        img.save()
    bpy.data.images.remove(img)

    return {"success": True, "width": width, "height": height, "filepath": filepath}


# ── integration status stubs ─────────────────────────────────────────────
# blender-mcp 1.6.x calls these on EVERY tool invocation (get_blender_connection
# in server.py:228 pings the connection via get_polyhaven_status). Returning
# {"status":"error"} makes the server treat the connection as dead and reconnect
# — which breaks every MCP call. So we must answer with {"enabled": False}, not
# error. The actual integrations are intentionally not implemented; this just
# keeps the connection alive and tells the server "no Polyhaven/Hyper3D/etc".

def _disabled_status(message: str) -> Dict[str, Any]:
    return {"enabled": False, "message": message}


def get_polyhaven_status() -> Dict[str, Any]:
    return _disabled_status("Poly Haven integration is not available in MCP_Socket.")


def get_hyper3d_status() -> Dict[str, Any]:
    return _disabled_status("Hyper3D Rodin integration is not available in MCP_Socket.")


def get_sketchfab_status() -> Dict[str, Any]:
    return _disabled_status("Sketchfab integration is not available in MCP_Socket.")


def get_hunyuan3d_status() -> Dict[str, Any]:
    return _disabled_status("Hunyuan3D integration is not available in MCP_Socket.")


# ── helpers ───────────────────────────────────────────────────────────────

def _world_aabb(obj) -> List[List[float]]:
    """World-space axis-aligned bounding box of a mesh object: [[min],[max]]."""
    corners = [obj.matrix_world @ mathutils.Vector(c) for c in obj.bound_box]
    mn = mathutils.Vector(map(min, zip(*corners)))
    mx = mathutils.Vector(map(max, zip(*corners)))
    return [[*mn], [*mx]]


# Command registry: type-string → callable.
# Core scene commands are fully implemented. The four *_status integration
# probes MUST be present because blender-mcp 1.6.x uses them as a connection
# health-check on every tool call — answering them with {"enabled": False}
# keeps the connection alive without pulling in those integrations' code.
# Bridge-extension commands (console log, structured queries, instance
# discovery) are reachable from any client via its execute_code escape hatch:
#   from bl_ext.user_default.mcp_socket import handlers
#   handlers.HANDLERS["get_console_log"](...)
HANDLERS: Dict[str, Any] = {
    "get_telemetry_consent": get_telemetry_consent,
    "get_scene_info": get_scene_info,
    "get_object_info": get_object_info,
    "execute_code": execute_code,
    "get_viewport_screenshot": get_viewport_screenshot,
    # Integration status stubs (connection health-check pings):
    "get_polyhaven_status": get_polyhaven_status,
    "get_hyper3d_status": get_hyper3d_status,
    "get_sketchfab_status": get_sketchfab_status,
    "get_hunyuan3d_status": get_hunyuan3d_status,
    # Bridge extensions (v1.3.0): diagnostics + structured queries.
    "get_console_log": logcap.get_console_log,
    "clear_console_log": logcap.clear_console_log,
    "get_bridge_info": queries.get_bridge_info,
    "get_hierarchy": queries.get_hierarchy,
    "get_object_data": queries.get_object_data,
    "get_material_info": queries.get_material_info,
    "get_images_report": queries.get_images_report,
    "list_instances": queries.list_instances,
}
