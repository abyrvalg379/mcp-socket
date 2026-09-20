# -*- coding:utf-8 -*-
"""Console capture for MCP_Socket: a ring buffer of Python-level output.

Scope (important): only output that flows through Python's ``sys.stdout`` /
``sys.stderr`` — addon prints, tracebacks, the ``logging`` module. C-level
console output (``register_class`` convention warnings, operator reports,
Blender's own printf) bypasses Python entirely and is NOT visible here; that
part still requires the System Console window. The bridge reports this
caveat in every ``get_console_log`` reply.

The tee is installed at addon register and removed at unregister. Output
written through the tee is still forwarded to the original stream, so the
System Console behaves exactly as before.
"""

from __future__ import annotations

import sys
import threading
import time
from collections import deque
from typing import Any, Dict, List, Optional

_TAG = "[MCP_Socket]"

# Ring size in complete lines. 500 covers a long debugging session without
# growing unbounded.
MAX_LINES = 500

_lock = threading.Lock()
_buffer: deque = deque(maxlen=MAX_LINES)  # entries: {"ts", "stream", "text"}
_partial: Dict[str, str] = {"stdout": "", "stderr": ""}
_orig_stdout = None
_orig_stderr = None
_installed = False


class _Tee:
    """File-like wrapper: forwards to the original stream + feeds the ring."""

    def __init__(self, original, stream: str) -> None:
        self._original = original
        self._stream = stream

    def write(self, s):
        if s:
            _feed(self._stream, s)
        try:
            return self._original.write(s)
        except Exception:  # noqa: BLE001 — the ring must never break printing
            return len(s)

    def flush(self):
        try:
            self._original.flush()
        except Exception:  # noqa: BLE001
            pass

    def __getattr__(self, item):
        # isatty, fileno, encoding, ... — delegate everything else.
        return getattr(self._original, item)


def _feed(stream: str, s: str) -> None:
    with _lock:
        _partial[stream] += s
        parts = _partial[stream].split("\n")
        _partial[stream] = parts.pop()  # tail without a newline stays pending
        for line in parts:
            _buffer.append({
                "ts": round(time.time(), 3),
                "stream": stream,
                "text": line,
            })


def get_console_log(last_n: int = 200, filter: str = "",
                    stream: str = "") -> Dict[str, Any]:
    """Return the last ``last_n`` captured lines (oldest last).

    ``filter`` — case-insensitive substring match on the text.
    ``stream`` — "stdout" | "stderr" | "" for both.
    """
    try:
        last_n = max(1, min(int(last_n or 200), MAX_LINES))
    except (TypeError, ValueError):
        last_n = 200

    with _lock:
        items: List[Dict[str, Any]] = list(_buffer)
        for name, frag in _partial.items():
            if frag:
                items.append({"ts": None, "stream": name,
                              "text": frag + "  ← (incomplete line)"})

    if stream:
        items = [i for i in items if i["stream"] == stream]
    if filter:
        needle = filter.lower()
        items = [i for i in items if needle in i["text"].lower()]

    return {
        "count": len(items),
        "lines": items[-last_n:],
        "caveat": ("Python-level output only (prints/tracebacks/logging). "
                   "C-level console (register_class warnings, operator reports) "
                   "is not visible to Python."),
    }


def clear_console_log() -> Dict[str, Any]:
    with _lock:
        _buffer.clear()
    return {"cleared": True}


def install() -> None:
    """Wrap sys.stdout/sys.stderr with tees. Idempotent."""
    global _orig_stdout, _orig_stderr, _installed
    if _installed:
        return
    _orig_stdout = sys.stdout
    _orig_stderr = sys.stderr
    sys.stdout = _Tee(_orig_stdout, "stdout")
    sys.stderr = _Tee(_orig_stderr, "stderr")
    _installed = True
    print(f"{_TAG} console capture installed (ring {MAX_LINES} lines)")


def uninstall() -> None:
    global _installed, _orig_stdout, _orig_stderr
    if not _installed:
        return
    # Restore only if nothing was swapped on top of our tee in the meantime.
    if isinstance(sys.stdout, _Tee):
        sys.stdout = _orig_stdout
    if isinstance(sys.stderr, _Tee):
        sys.stderr = _orig_stderr
    _installed = False
    _orig_stdout = _orig_stderr = None


def ensure_installed() -> None:
    """Re-assert the tees if something replaced them.

    ⚠ Needed because an addon reinstall performed THROUGH the bridge breaks
    the stdout tee: uninstall of the old code restores the real console, the
    new register installs a fresh tee, and then ``redirect_stdout`` inside the
    still-running execute_code restores the OLD tee captured at exec start —
    dropping the new one. The heartbeat calls this every ~10 s, so the ring
    self-heals within one tick (same watchdog pattern as the TOCHKA keymap
    sentinel).
    """
    global _installed, _orig_stdout, _orig_stderr
    if not _installed:
        return
    if isinstance(sys.stdout, _Tee) and isinstance(sys.stderr, _Tee):
        return
    # Drop whatever stale state we hold and re-wrap the current streams.
    _installed = False
    _orig_stdout = _orig_stderr = None
    install()
