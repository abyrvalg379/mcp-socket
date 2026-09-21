#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""MCP Socket — thin MCP server (stdio) proxying to the Blender addon.

Chain: MCP client → this process → TCP 127.0.0.1:9876 → MCP Socket addon.
The addon owns all logic; this process only translates MCP tools into
{"type": ..., "params": ...} bridge commands (blender-mcp wire protocol).

Hand-rolled MCP JSON-RPC (newline-delimited over stdio) — same proven
pattern as maya_mcp.py on this machine; zero dependencies beyond stdlib.

Run (registered in ZCode config → mcp.servers):
    python.exe mcp_server/server.py [--port 9876]

Multi-instance: launch a second entry with --port 9877 (the addon
auto-offsets ports; list_instances discovers who is where).
"""

from __future__ import annotations

import json
import os
import socket
import sys

_DEFAULT_PORT = 9876
_CALL_TIMEOUT = 180.0  # seconds; generous for heavy exports


def _bridge_port() -> int:
    for part in sys.argv[1:]:
        if part == "--port" and sys.argv.index(part) + 1 < len(sys.argv):
            return int(sys.argv[sys.argv.index(part) + 1])
    env = os.environ.get("MCP_SOCKET_PORT")
    if env:
        return int(env)
    return _DEFAULT_PORT


def _bridge_call(cmd_type: str, params: dict, port: int) -> dict:
    """One bridge command: fresh socket, JSON in, JSON out (no framing —
    accumulate bytes until they parse whole, matching blender-mcp clients)."""
    with socket.create_connection(("127.0.0.1", port), timeout=_CALL_TIMEOUT) as sock:
        sock.settimeout(_CALL_TIMEOUT)
        sock.sendall(json.dumps({"type": cmd_type, "params": params}).encode("utf-8"))
        buf = b""
        while True:
            chunk = sock.recv(8192)
            if not chunk:
                break
            buf += chunk
            try:
                return json.loads(buf.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
    raise RuntimeError("Bridge closed the connection before replying")


# ── tool table: name → (bridge type, parameter passthrough) ────────────────
# Everything the addon's HANDLERS expose, minus internal handshake/stubs.

TOOLS = [
    {"name": "ping", "bridge": "get_bridge_info", "params": [],
     "description": ("Check the MCP Socket bridge in the open Blender: versions, "
                     "pid, scene, filepath, object/material counts, port.")},
    {"name": "get_scene_info", "bridge": "get_scene_info", "params": [],
     "description": "Minimal scene overview: name, counts, first objects."},
    {"name": "get_object_info", "bridge": "get_object_info", "params": ["name"],
     "description": "Details for one object by name (location, bounds, mesh stats)."},
    {"name": "get_hierarchy", "bridge": "get_hierarchy", "params": [],
     "description": "Collection tree of the scene with objects and visibility flags."},
    {"name": "get_object_data", "bridge": "get_object_data", "params": ["name"],
     "description": ("Full read-only object dossier: transforms, modifiers, "
                     "constraints, material slots, mesh stats, custom props, action.")},
    {"name": "get_material_info", "bridge": "get_material_info", "params": ["name"],
     "description": ("Material dossier: nodes summary, image textures with "
                     "colorspace and on-disk state.")},
    {"name": "get_images_report", "bridge": "get_images_report", "params": [],
     "description": ("All images in the blend: paths, colorspace, packed, "
                     "missing files.")},
    {"name": "get_console_log", "bridge": "get_console_log",
     "params": ["last_n", "filter", "stream"],
     "description": ("Ring buffer of Python-level console output (addon prints, "
                     "tracebacks, logging). C-level console is not visible to Python.")},
    {"name": "clear_console_log", "bridge": "clear_console_log", "params": [],
     "description": "Clear the console ring buffer."},
    {"name": "list_instances", "bridge": "list_instances", "params": [],
     "description": "Live MCP Socket bridge instances on this machine (ports, scenes, pids)."},
    {"name": "list_presets", "bridge": "list_presets", "params": [],
     "description": "Export presets with resolved settings and receiver notes (PROKLADKA)."},
    {"name": "export_fbx", "bridge": "export_fbx",
     "params": ["path", "preset", "scope", "collection_name", "overrides"],
     "description": ("Export to FBX with a pipeline preset (neutral: meters, Y-up, "
                     "binary, modifiers baked). Children of selected Empties included. "
                     "Overrides merge over the preset and are echoed in the report.")},
    {"name": "import_fbx", "bridge": "import_fbx",
     "params": ["path", "container", "global_scale"],
     "description": ("Import FBX per the receiver rule: container EMPTY t=0 r=0 s=1. "
                     "Oversized meshes (>50 m) are reported, never rescaled.")},
    {"name": "get_viewport_screenshot", "bridge": "get_viewport_screenshot",
     "params": ["filepath", "max_size", "format"],
     "description": ("Capture the active 3D viewport to a PNG on disk; returns the "
                     "filepath (read the file to view it).")},
    {"name": "render_offscreen", "bridge": "render_offscreen",
     "params": ["filepath", "mode", "percent", "width", "height"],
     "description": ("Render to a file without opening the render window: mode "
                     "'viewport' = fast OpenGL render of the current 3D view (no "
                     "overlays/gizmos); mode 'camera' = full scene-engine render "
                     "from the scene camera (Cycles can be slow; timeout 180 s). "
                     "Returns the filepath, engine, resolution, elapsed time.")},
    {"name": "get_bpy_api_info", "bridge": "get_bpy_api_info",
     "params": ["query"],
     "description": ("Look up exact API facts from the LIVE Blender instead of "
                     "guessing: 'bpy.ops.<cat>.<op>' returns the operator's real "
                     "parameter names/types/defaults; 'bpy.types.<Type>' a member "
                     "overview; 'bpy.types.<Type>.<member>' a property card with "
                     "enum items. ALWAYS check here before writing execute_code "
                     "with an unfamiliar operator or enum.")},
    {"name": "get_pipeline_conventions", "bridge": "get_pipeline_conventions",
     "params": [],
     "description": ("The user's pipeline rules from a JSON file (no guessing): "
                     "receiver rule for imported assets (container t=0 r=0 s=1, "
                     "dimensions baked, oversize reported), per-DCC units/naming/UV "
                     "table (Blender/Maya/Houdini/Unreal), naming idempotency, "
                     "export preset guidance. Read once before asset-moving work.")},
    {"name": "execute_code", "bridge": "execute_code", "params": ["code"],
     "description": ("Escape hatch: run arbitrary bpy Python code in Blender. "
                     "Prefer the typed tools above.")},
]


def _tool_schema(tool: dict) -> dict:
    props, required = {}, []
    types = {"path": "string", "filepath": "string", "preset": "string",
             "scope": "string", "collection_name": "string", "name": "string",
             "filter": "string", "stream": "string", "code": "string",
             "format": "string"}
    numbers = {"last_n": "integer", "max_size": "integer", "percent": "integer",
               "width": "integer", "height": "integer"}
    booleans = {"container": "boolean"}
    numbers.update({"global_scale": "number"})
    for param in tool["params"]:
        if param in numbers:
            props[param] = {"type": numbers[param]}
        elif param in booleans:
            props[param] = {"type": "boolean"}
        elif param == "overrides":
            props[param] = {"type": "object",
                            "description": "export_scene.fbx property overrides"}
        elif param == "stream":
            props[param] = {"type": "string",
                            "description": '"stdout" | "stderr" | "" for both'}
        elif param == "mode":
            props[param] = {"type": "string",
                            "description": "'viewport' (OpenGL, fast) or "
                                           "'camera' (scene engine)",
                            "enum": ["viewport", "camera"]}
        elif param == "scope":
            props[param] = {"type": "string",
                            "description": '"selected" | "collection" | "scene"',
                            "enum": ["selected", "collection", "scene"]}
        elif param == "preset":
            props[param] = {"type": "string",
                            "description": "maya | houdini | ue | neutral (list_presets)"}
        else:
            props[param] = {"type": types.get(param, "string")}
        if param in ("path", "name", "code", "filepath", "query"):
            required.append(param)
    return {"type": "object", "properties": props, "required": required}


def _tool_definitions() -> list:
    defs = []
    for tool in TOOLS:
        defs.append({
            "name": tool["name"],
            "description": tool["description"],
            "inputSchema": _tool_schema(tool),
        })
    return defs


def _call_tool(name: str, arguments: dict, port: int) -> tuple:
    tool = next((t for t in TOOLS if t["name"] == name), None)
    if tool is None:
        raise ValueError(f"Unknown tool: {name}")
    params = {k: v for k, v in (arguments or {}).items() if v is not None}
    reply = _bridge_call(tool["bridge"], params, port)
    if reply.get("status") == "success":
        result = reply.get("result", "")
        text = result if isinstance(result, str) else json.dumps(
            result, indent=2, ensure_ascii=False)
        return {"content": [{"type": "text", "text": text or "(no output)"}]}, False
    return {"content": [{"type": "text",
                         "text": f"Bridge error: {reply.get('message', 'unknown')}"}],
            "isError": True}, True


# ── MCP JSON-RPC loop (newline-delimited over stdio) ───────────────────────

def _send(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _handle(msg: dict, port: int) -> None:
    method = msg.get("method", "")
    req_id = msg.get("id")

    if method == "initialize":
        _result(req_id, {
            "protocolVersion": msg.get("params", {}).get("protocolVersion",
                                                         "2025-11-25"),
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "mcp-socket", "version": "2.6.0"},
        })
    elif method == "notifications/initialized":
        pass
    elif method == "tools/list":
        _result(req_id, {"tools": _tool_definitions()})
    elif method == "tools/call":
        try:
            name = msg["params"].get("name", "")
            content, is_error = _call_tool(name,
                                           msg["params"].get("arguments", {}),
                                           port)
            result = dict(content)
            if is_error:
                result["isError"] = True
            _result(req_id, result)
        except Exception as exc:  # noqa: BLE001 — report as tool error
            _result(req_id, {"content": [{"type": "text",
                                          "text": f"MCP Socket server error: {exc}"}],
                             "isError": True})
    elif req_id is not None:
        _error(req_id, -32601, f"Method not found: {method}")


def _result(req_id, data: dict) -> None:
    _send({"jsonrpc": "2.0", "id": req_id, "result": data})


def _error(req_id, code: int, message: str) -> None:
    _send({"jsonrpc": "2.0", "id": req_id,
           "error": {"code": code, "message": message}})


def main() -> None:
    port = _bridge_port()
    print(f"[MCP_Socket_server] started, bridge port {port}", file=sys.stderr,
          flush=True)
    for raw_line in sys.stdin:
        raw_line = raw_line.strip()
        if not raw_line:
            continue
        try:
            msg = json.loads(raw_line)
        except json.JSONDecodeError as exc:
            print(f"[MCP_Socket_server] JSON parse error: {exc}", file=sys.stderr,
                  flush=True)
            continue
        try:
            _handle(msg, port)
        except Exception:  # noqa: BLE001 — keep the server alive no matter what
            import traceback
            print(f"[MCP_Socket_server] handler error:\n{traceback.format_exc()}",
                  file=sys.stderr, flush=True)
            if msg.get("id") is not None:
                _error(msg["id"], -32603, "internal error")


if __name__ == "__main__":
    main()
