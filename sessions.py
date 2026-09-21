# -*- coding:utf-8 -*-
"""Agent session log for MCP Socket: JSONL record + replay.

Every bridge command is appended to a per-session JSONL file in
``%TEMP%/mcp_socket/sessions/``. A "session" is the same concept the undo
checkpoint uses: commands separated by less than 10 s of idle belong to one
agent session; a longer gap starts a new file. The log answers three
questions: *what did the agent do*, *can I redo that ritual on the next
asset*, and *which step broke*.

Replay re-runs the last session's mutating commands (skipping read-only
queries — re-reading the scene tells us nothing new). Replay is a draft for
modifying commands: Blender state may have changed since the recording, so
each step reports its own success/failure and one failure never stops the
rest.

Files are capped (oldest deleted) so %TEMP% stays clean.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from typing import Any, Dict, List, Optional, Tuple

_TAG = "[MCP_Socket]"

# Same idle gap the undo checkpoint uses (server.py) — one concept, two uses.
_SESSION_GAP_SECONDS = 10.0

# Chatter that carries no audit value: connection-health pings the blender-mcp
# protocol makes before every real call. Everything else is recorded.
_SKIP_RECORD_TYPES = {
    "ping",
    "get_telemetry_consent",
    "get_polyhaven_status",
    "get_hyper3d_status",
    "get_sketchfab_status",
    "get_hunyuan3d_status",
}

# Read-only queries: replaying them re-reads an already-changed scene, so
# replay skips them. Everything else (execute_code, export_fbx, import_fbx,
# render_offscreen, …) is replayable.
_REPLAY_SKIP_TYPES = {
    "get_telemetry_consent",
    "get_polyhaven_status",
    "get_hyper3d_status",
    "get_sketchfab_status",
    "get_hunyuan3d_status",
    "ping",
    "get_bridge_info",
    "get_scene_info",
    "get_object_info",
    "get_object_data",
    "get_hierarchy",
    "get_material_info",
    "get_images_report",
    "get_console_log",
    "clear_console_log",
    "list_instances",
    "list_presets",
    "get_bpy_api_info",
    "get_pipeline_conventions",
    "get_viewport_screenshot",
}

_SESSIONS_KEEP = 30

_state: Dict[str, Any] = {"file": None, "last_ts": 0.0}


def sessions_dir() -> str:
    return os.path.join(tempfile.gettempdir(), "mcp_socket", "sessions")


def current_session_file() -> Optional[str]:
    return _state["file"]


def record_command(cmd_type: str, params: Dict[str, Any], status: str,
                   elapsed_s: float, replay: bool = False) -> None:
    """Append one command to the current session file (main thread only).

    A gap longer than _SESSION_GAP_SECONDS since the previous recorded
    command starts a new file — the same session boundary the undo
    checkpoint uses. Never raises: logging must not break the command.
    """
    if cmd_type in _SKIP_RECORD_TYPES:
        return
    try:
        now = time.monotonic()
        if _state["last_ts"] and (now - _state["last_ts"]) <= _SESSION_GAP_SECONDS \
                and _state["file"] and os.path.isfile(_state["file"]):
            path = _state["file"]
        else:
            path = _new_session_file()
        _state["file"] = path
        _state["last_ts"] = now

        entry = {
            "ts": round(time.time(), 3),
            "type": cmd_type,
            "params": params,
            "status": status,
            "elapsed_s": round(elapsed_s, 3),
        }
        if replay:
            entry["replay"] = True
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as exc:  # noqa: BLE001 — logging must never break commands
        print(f"{_TAG} session log write failed: {exc}", file=__import__("sys").stderr)


def _new_session_file() -> str:
    d = sessions_dir()
    os.makedirs(d, exist_ok=True)
    base = time.strftime("session_%Y%m%d_%H%M%S")
    path = os.path.join(d, base + ".jsonl")
    n = 2
    while os.path.exists(path):  # two sessions within one second
        path = os.path.join(d, "%s_%d.jsonl" % (base, n))
        n += 1
    _prune_old(d)
    return path


def _prune_old(d: str) -> None:
    try:
        files = sorted(f for f in os.listdir(d) if f.endswith(".jsonl"))
        for old in files[:-_SESSIONS_KEEP]:
            os.remove(os.path.join(d, old))
    except OSError:
        pass


def last_session_file() -> Optional[str]:
    """Newest .jsonl in the sessions dir (falls back to the live session)."""
    d = sessions_dir()
    try:
        files = sorted(f for f in os.listdir(d) if f.endswith(".jsonl"))
    except OSError:
        return None
    return os.path.join(d, files[-1]) if files else _state["file"]


def session_info() -> Dict[str, Any]:
    """Panel-friendly summary of the newest session file."""
    path = last_session_file()
    if not path:
        return {"available": False}
    total = replayable = 0
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                total += 1
                if _is_replayable(entry):
                    replayable += 1
    except OSError:
        return {"available": False}
    return {
        "available": True,
        "filepath": path,
        "filename": os.path.basename(path),
        "commands": total,
        "replayable": replayable,
    }


def _is_replayable(entry: Dict[str, Any]) -> bool:
    return (entry.get("status") == "success"
            and entry.get("type") not in _REPLAY_SKIP_TYPES)


def read_replayable(path: str) -> List[Tuple[str, Dict[str, Any]]]:
    """(type, params) pairs of the last session's mutating, successful commands."""
    steps: List[Tuple[str, Dict[str, Any]]] = []
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if _is_replayable(entry):
                steps.append((entry["type"], entry.get("params") or {}))
    return steps


def run_replay(path: str) -> Dict[str, Any]:
    """Re-execute the last session's replayable commands, in order.

    Each step runs the same handler the bridge would call, on the main
    thread (replay runs inside an operator). One failure is reported and
    the rest still run. Replay steps are recorded back into the log with
    ``replay: true`` so an audit shows the re-run too.
    """
    from . import handlers
    steps = read_replayable(path)
    if not steps:
        raise ValueError("No replayable commands in %s (nothing mutating "
                         "and successful was recorded)." % os.path.basename(path))
    results: List[Dict[str, Any]] = []
    ok = 0
    for cmd_type, params in steps:
        started = time.monotonic()
        try:
            handler = handlers.HANDLERS.get(cmd_type)
            if handler is None:
                raise RuntimeError("no handler")
            handler(**params)
            status = "success"
            ok += 1
        except Exception as exc:  # noqa: BLE001 — one failure must not stop replay
            status = "error: %s" % str(exc)[:160]
        record_command(cmd_type, params, "success" if status == "success" else "error",
                       time.monotonic() - started, replay=True)
        results.append({"type": cmd_type, "status": status})
    return {
        "replayed": True,
        "filepath": path,
        "steps_total": len(steps),
        "steps_ok": ok,
        "steps_failed": len(steps) - ok,
        "results": results,
        "note": ("Replay re-runs recorded commands against the CURRENT scene — "
                 "modifying steps act as a draft, check each result."),
    }
