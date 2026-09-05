# -*- coding:utf-8 -*-
"""TCP bridge between Blender and the ZCode / blender-mcp client.

Wire protocol (matches blender-mcp 1.6.x):

* Server (this module) listens on ``localhost:PORT`` (default 9876).
* Client connects, sends one JSON document per request::

      {"type": "<command>", "params": {...}}

* Server replies with one JSON document::

      {"status": "success", "result": <handler return value>}
      {"status": "error",   "message": "<str>"}

There is **no framing byte** — the client accumulates received bytes and tries
``json.loads`` until it parses whole. We do the same when reading requests.

Thread-safety: the socket runs on a background thread, but ``bpy`` is only
safe on the main thread. Each request is therefore handed to
``bpy.app.timers.register`` with ``first_interval=0.0``; the result is sent
back from that timer callback.
"""

from __future__ import annotations

import json
import socket
import threading
import time
import traceback
from typing import Any, Dict, Optional

import bpy

from . import handlers

_TAG = "[ZCode_MCP]"

# Default loopback bind. 9876 matches blender-mcp's DEFAULT_PORT so the existing
# ZCode MCP config (no path/port changes) keeps working.
_DEFAULT_HOST = "localhost"
_DEFAULT_PORT = 9876

# SO_REUSEADDR + accept() timeout so the server loop can notice self.running
# flipping to False within this many seconds.
_ACCEPT_TIMEOUT = 1.0

# Per-request recv buffer. JSON is accumulated until it parses whole.
_RECV_CHUNK = 8192

# How long a single main-thread handler may block before we give up on it.
# (bpy.app.timers have no hard deadline; this is a soft watchdog for logging.)
_HANDLER_WARN_SECONDS = 30.0

# Idle read timeout per client connection. blender-mcp.exe opens a fresh
# socket per command and closes it after the reply, so a socket that has been
# silent for this long is a half-closed zombie — drop it so the active-client
# counter returns to zero. Generous enough to never fire mid-request.
_CLIENT_IDLE_TIMEOUT = 30.0


class ZCodeMCPServer:
    """Single-connection TCP server bridging to Blender's main thread."""

    def __init__(self, host: str = _DEFAULT_HOST, port: int = _DEFAULT_PORT) -> None:
        self.host = host
        self.port = port
        self.running = False
        self.socket: Optional[socket.socket] = None
        self._thread: Optional[threading.Thread] = None

        # Thread-safe observability state for the UI panel.
        # Written from background client threads, read from Blender's main
        # thread by status_snapshot(). _state_lock guards every access.
        self._state_lock = threading.Lock()
        self._active_clients = 0
        self._last_command: Optional[str] = None        # e.g. "get_scene_info"
        self._last_status: Optional[str] = None         # "success" | "error"
        self._last_command_ts: float = 0.0              # monotonic seconds
        self._total_commands: int = 0

    # ── lifecycle ─────────────────────────────────────────────────────────

    def start(self) -> bool:
        if bpy.app.background:
            # In headless mode timers never run, so commands would hang forever.
            print(f"{_TAG} cannot start server in background mode (run Blender with a GUI)")
            return False
        if self.running:
            print(f"{_TAG} server already running on {self.host}:{self.port}")
            return True

        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind((self.host, self.port))
            sock.listen(1)
            sock.settimeout(_ACCEPT_TIMEOUT)
        except OSError as exc:
            # Most common: EADDRINUSE because the old blender-mcp addon still
            # holds the port. The user must remove/disable it first.
            print(f"{_TAG} failed to bind {self.host}:{self.port}: {exc}")
            print(f"{_TAG} another addon may already own this port "
                  "(remove the old 'Blender MCP' addon and restart Blender)")
            return False

        self.socket = sock
        self.running = True
        self._thread = threading.Thread(target=self._accept_loop, daemon=True)
        self._thread.start()
        print(f"{_TAG} server started on {self.host}:{self.port}")
        return True

    def stop(self) -> None:
        self.running = False
        if self.socket is not None:
            try:
                self.socket.close()
            except OSError:
                pass
            self.socket = None
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        self._thread = None
        print(f"{_TAG} server stopped")

    # ── accept loop (background thread) ───────────────────────────────────

    def _accept_loop(self) -> None:
        assert self.socket is not None
        while self.running:
            try:
                client, address = self.socket.accept()
            except socket.timeout:
                continue
            except OSError:
                if not self.running:
                    break
                time.sleep(0.25)
                continue
            print(f"{_TAG} client connected: {address}")
            client_thread = threading.Thread(
                target=self._handle_client, args=(client,), daemon=True
            )
            client_thread.start()

    # ── per-client loop (background thread) ───────────────────────────────

    def _handle_client(self, client: socket.socket) -> None:
        # Idle timeout: drop connections that go silent so the active-client
        # counter can't get stuck on a half-closed socket. A live MCP client
        # sends a command within a few seconds of connecting.
        client.settimeout(_CLIENT_IDLE_TIMEOUT)
        buffer = b""
        try:
            with self._state_lock:
                self._active_clients += 1
            while self.running:
                try:
                    data = client.recv(_RECV_CHUNK)
                except socket.timeout:
                    # No data for the whole idle window → assume the client
                    # is gone. Decrement happens in finally.
                    print(f"{_TAG} client idle, closing connection")
                    break
                if not data:
                    break  # client disconnected cleanly
                buffer += data
                # The wire has no delimiter: keep trying to parse as complete JSON.
                command, consumed = _try_parse(buffer)
                if command is None:
                    continue
                buffer = buffer[consumed:]
                self._record_command_seen(command.get("type"))
                self._dispatch(client, command)
        except (ConnectionError, OSError) as exc:
            print(f"{_TAG} connection error: {exc}")
        except Exception as exc:  # noqa: BLE001 — never let a client thread die silently
            print(f"{_TAG} handler thread crashed: {exc}")
            traceback.print_exc()
        finally:
            with self._state_lock:
                self._active_clients = max(0, self._active_clients - 1)
            try:
                client.close()
            except OSError:
                pass
            print(f"{_TAG} client handler stopped")

    # ── command execution (main thread) ───────────────────────────────────

    def _dispatch(self, client: socket.socket, command: Dict[str, Any]) -> None:
        """Schedule ``command`` on the main thread and stream the reply back."""
        cmd_type = command.get("type")

        def run_on_main_thread():
            try:
                response = self._execute(command)
            except Exception as exc:  # noqa: BLE001 — surface as protocol error
                print(f"{_TAG} command failed: {exc}")
                traceback.print_exc()
                response = {"status": "error", "message": str(exc)}
            # Record final outcome (success/error) for the UI.
            self._record_command_done(cmd_type, response.get("status", "error"))
            try:
                client.sendall((json.dumps(response)).encode("utf-8"))
            except OSError:
                print(f"{_TAG} failed to send reply — client gone")
            return None  # timers must return None to not repeat

        # Execute on Blender's main thread; bpy is not thread-safe.
        try:
            bpy.app.timers.register(run_on_main_thread, first_interval=0.0)
        except ValueError:
            # Timer already registered (shouldn't happen with a fresh function);
            # fall back to sending an error so the client doesn't hang.
            client.sendall((json.dumps({
                "status": "error",
                "message": "Could not schedule command on main thread",
            })).encode("utf-8"))

    def _execute(self, command: Dict[str, Any]) -> Dict[str, Any]:
        cmd_type = command.get("type")
        params = command.get("params") or {}
        if not isinstance(cmd_type, str):
            return {"status": "error", "message": "Missing 'type' in command"}

        handler = handlers.HANDLERS.get(cmd_type)
        if handler is None:
            return {
                "status": "error",
                "message": (
                    f"Unknown command type: {cmd_type!r}. "
                    f"Known: {sorted(handlers.HANDLERS)}"
                ),
            }

        started = time.monotonic()
        try:
            result = handler(**params) if isinstance(params, dict) else handler()
        except TypeError as exc:
            # Param mismatch — surface clearly so the client can correct itself.
            return {"status": "error", "message": f"Bad params for {cmd_type!r}: {exc}"}
        elapsed = time.monotonic() - started
        if elapsed > _HANDLER_WARN_SECONDS:
            print(f"{_TAG} slow command {cmd_type!r}: {elapsed:.1f}s")
        return {"status": "success", "result": result}

    # ── observability (thread-safe) ───────────────────────────────────────

    def _record_command_seen(self, cmd_type: Optional[str]) -> None:
        """Note that a command arrived. Called from the background thread."""
        if not cmd_type:
            return
        with self._state_lock:
            self._total_commands += 1

    def _record_command_done(self, cmd_type: Optional[str], status: str) -> None:
        """Record a completed command's outcome. Called from the main thread."""
        if not cmd_type:
            return
        with self._state_lock:
            self._last_command = cmd_type
            self._last_status = status
            self._last_command_ts = time.monotonic()

    def status_snapshot(self) -> Dict[str, Any]:
        """Thread-safe snapshot of server state for the UI panel.

        Returns a plain dict the main thread can render without touching the
        background threads. Safe to call from draw().
        """
        with self._state_lock:
            last_ts = self._last_command_ts
            return {
                "running": self.running,
                "host": self.host,
                "port": self.port,
                "active_clients": self._active_clients,
                "last_command": self._last_command,
                "last_status": self._last_status,
                "last_command_age": (time.monotonic() - last_ts) if last_ts else None,
                "total_commands": self._total_commands,
            }


# ── module helpers ────────────────────────────────────────────────────────

def _try_parse(buffer: bytes) -> tuple[Optional[Dict[str, Any]], int]:
    """Return (decoded_command, bytes_consumed) or (None, 0) if incomplete.

    Tries progressively larger prefixes: the longest prefix that parses as
    valid JSON is consumed, leaving the tail for the next request.
    """
    # Fast path: the whole buffer is one JSON document.
    try:
        return json.loads(buffer.decode("utf-8")), len(buffer)
    except (json.JSONDecodeError, UnicodeDecodeError):
        pass

    # Slow path: multiple documents concatenated without a delimiter.
    # Scan for the shortest valid prefix.
    for i in range(1, len(buffer)):
        try:
            obj = json.loads(buffer[:i].decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        return obj, i
    return None, 0
