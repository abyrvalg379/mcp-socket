# -*- coding:utf-8 -*-
"""Custom colored status icons for ZCode_MCP.

Blender ships no plain green/red dot icon (COLOR_GREEN/COLOR_RED are RGB-palette
swatches that render with a white "G"/"R" letter; COLLECTION_COLOR_* are
rounded squares). For a clean round status light we generate small
filled-circle PNGs in memory and register them via ``bpy.utils.previews`` so
the panel can use ``icon_value=``.

⚠ ``bpy.utils.previews`` is a LAZY SUBMODULE in every Blender version including
5.2 — attribute access fails with "module 'bpy.utils' has no attribute
'previews'" until something executes ``import bpy.utils.previews``. Other
addons used to prime it before us, masking the bug; on a clean environment
nothing does. The explicit import below is the whole fix.

No external files ship with the addon — PNGs are generated into a temp dir on
register and discarded on unregister.
"""

from __future__ import annotations

import os
import struct
import tempfile
import zlib

import bpy

_TAG = "[ZCode_MCP]"

# RGB of the two status lights. Tuned to read clearly on a dark panel.
_GREEN_RGB = (70, 200, 95)
_RED_RGB = (225, 72, 72)
_DOT_SIZE = 32  # px square; plenty for a sidebar icon

# Module-level icon ids, read by ui.draw at paint time. 0 means "not loaded".
GREEN: int = 0
RED: int = 0

_previews = None
_cache_dir: str = ""


# ── minimal PNG encoder (no Pillow dependency) ───────────────────────────

def _png_from_raw(raw: bytes, w: int, h: int) -> bytes:
    def _chunk(tag: bytes, data: bytes) -> bytes:
        blob = tag + data
        crc = zlib.crc32(blob) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + blob + struct.pack(">I", crc)

    sig = b"\x89PNG\r\n\x1a\n"
    # 8-bit depth, colour type 6 = RGBA.
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)
    idat = zlib.compress(raw, 9)
    return sig + _chunk(b"IHDR", ihdr) + _chunk(b"IDAT", idat) + _chunk(b"IEND", b"")


def _make_dot_png(rgb: tuple, size: int = _DOT_SIZE) -> bytes:
    """A filled circle of ``rgb`` on a transparent background, anti-aliased."""
    cx = cy = (size - 1) / 2.0
    radius = size / 2.0
    raw = bytearray()
    for y in range(size):
        raw.append(0)  # per-scanline filter: None
        for x in range(size):
            dx = x + 0.5 - cx
            dy = y + 0.5 - cy
            dist = (dx * dx + dy * dy) ** 0.5
            coverage = radius - dist  # >=1 fully in, <=0 fully out
            if coverage >= 1:
                alpha = 255
            elif coverage <= 0:
                alpha = 0
            else:
                alpha = int(coverage * 255)
            if alpha == 0:
                raw += b"\x00\x00\x00\x00"
            else:
                raw += bytes(rgb) + bytes((alpha,))
    return _png_from_raw(bytes(raw), size, size)


# ── registration ─────────────────────────────────────────────────────────

def register() -> None:
    global _previews, _cache_dir, GREEN, RED
    try:
        # ⚠ the import IS the fix: bpy.utils.previews is a lazy submodule, the
        # attribute does not exist (and never will) until this line runs.
        import bpy.utils.previews  # noqa: F401

        _cache_dir = os.path.join(tempfile.gettempdir(), "zcode_mcp_icons")
        os.makedirs(_cache_dir, exist_ok=True)

        paths = {
            "GREEN": (os.path.join(_cache_dir, "green.png"), _GREEN_RGB),
            "RED": (os.path.join(_cache_dir, "red.png"), _RED_RGB),
        }
        for name, (path, rgb) in paths.items():
            if not os.path.isfile(path):
                with open(path, "wb") as fh:
                    fh.write(_make_dot_png(rgb))

        _previews = bpy.utils.previews.new()
        _previews.load("GREEN", paths["GREEN"][0], "IMAGE")
        _previews.load("RED", paths["RED"][0], "IMAGE")
        GREEN = _previews["GREEN"].icon_id
        RED = _previews["RED"].icon_id
    except Exception as exc:  # noqa: BLE001 — never let icon load kill the addon
        print(f"{_TAG} icon register failed (falling back): {exc}")
        GREEN = 0
        RED = 0


def unregister() -> None:
    global _previews, GREEN, RED
    GREEN = 0
    RED = 0
    if _previews is not None:
        try:
            bpy.utils.previews.remove(_previews)
        except Exception:  # noqa: BLE001
            pass
        _previews = None
