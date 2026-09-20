# -*- coding:utf-8 -*-
"""Offscreen render handler for MCP Socket: save a rendered image to disk.

Two modes, one handler:

``viewport`` — ``bpy.ops.render.opengl`` on the first 3D viewport: fast,
uses the viewport's camera angle and shading, no render window, no
overlays/gizmos in the output. The everyday tool for "show me the scene".

``camera`` — ``bpy.ops.render.render``: the full scene engine (Eevee/Cycles
as configured in the scene) from the scene camera. Honours scene quality
settings, so Cycles renders can take minutes — the bridge call timeout is
180 s, keep that in mind for heavy scenes.

The handler temporarily swaps ``scene.render`` output settings (filepath,
format, resolution) and always restores them in ``finally`` — a render via
the bridge must never leak settings into the user's scene.
"""

from __future__ import annotations

import os
import time
from typing import Any, Dict, Optional, Tuple

import bpy

_MODES = ("viewport", "camera")

# File extension → Blender image file format. An unrecognised (or missing)
# extension gets ".png" appended — the file type follows the extension.
_FORMAT_BY_EXT = {
    ".png": "PNG",
    ".jpg": "JPEG",
    ".jpeg": "JPEG",
    ".webp": "WEBP",
    ".bmp": "BMP",
    ".tga": "TGA",
    ".exr": "OPEN_EXR",
}


def _resolve_output(path: str) -> Tuple[str, str]:
    """Absolute output path + matching Blender file format."""
    final = bpy.path.abspath(str(path))
    fmt = _FORMAT_BY_EXT.get(os.path.splitext(final)[1].lower())
    if fmt is None:
        final += ".png"
        fmt = "PNG"
    parent = os.path.dirname(final)
    if parent:
        os.makedirs(parent, exist_ok=True)
    return final, fmt


def _first_viewport():
    """(window, area) of the first 3D viewport, or (None, None)."""
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == "VIEW_3D":
                return window, area
    return None, None


def render_offscreen(filepath: str, mode: str = "viewport",
                     percent: int = 100,
                     width: Optional[int] = None,
                     height: Optional[int] = None) -> Dict[str, Any]:
    """Render to ``filepath`` without opening the render window."""
    if not filepath or not str(filepath).strip():
        raise ValueError("No filepath provided.")
    if mode not in _MODES:
        raise ValueError(f"Unknown mode {mode!r}. Valid: {list(_MODES)}")
    final_path, fmt = _resolve_output(filepath)

    scene = bpy.context.scene
    r = scene.render
    saved = (r.filepath, r.image_settings.file_format,
             r.resolution_x, r.resolution_y, r.resolution_percentage)
    # Blender 5.2: image_settings.media_type = 'VIDEO' (e.g. after a
    # turntable render) narrows file_format's enum to FFMPEG only — still
    # images need media_type='IMAGE' (5.2 enum: IMAGE/MULTI_LAYER_IMAGE/
    # VIDEO). Saved and restored too.
    saved_media = r.image_settings.media_type
    started = time.perf_counter()
    used_res = {"x": r.resolution_x, "y": r.resolution_y, "percent": int(percent)}
    try:
        r.filepath = final_path
        r.image_settings.media_type = "IMAGE"
        r.image_settings.file_format = fmt
        if width:
            r.resolution_x = int(width)
        if height:
            r.resolution_y = int(height)
        r.resolution_percentage = int(percent)
        used_res = {"x": r.resolution_x, "y": r.resolution_y,
                    "percent": r.resolution_percentage}

        if mode == "viewport":
            window, area = _first_viewport()
            if area is None:
                raise ValueError("No 3D viewport open — a viewport render "
                                 "needs a visible VIEW_3D area.")
            with bpy.context.temp_override(window=window, area=area):
                bpy.ops.render.opengl(write_still=True)
            engine = "VIEWPORT_OPENGL"
        else:
            engine = r.engine
            bpy.ops.render.render(write_still=True)
    except Exception as exc:
        raise RuntimeError(f"Offscreen render failed: {exc}") from exc
    finally:
        # media_type first: with 'VIDEO' restored, file_format's enum
        # narrows back to FFMPEG and the saved value becomes assignable
        # (the reverse order raises "enum FFMPEG not found").
        r.image_settings.media_type = saved_media
        r.filepath, r.image_settings.file_format = saved[0], saved[1]
        r.resolution_x, r.resolution_y, r.resolution_percentage = saved[2:]

    size = os.path.getsize(final_path) if os.path.isfile(final_path) else 0
    if not size:
        raise RuntimeError(f"Render wrote no file: {final_path}")

    return {
        "rendered": True,
        "filepath": final_path,
        "size_bytes": size,
        "mode": mode,
        "engine": engine,
        "resolution": used_res,
        "elapsed_s": round(time.perf_counter() - started, 2),
        "note": ("Viewport mode uses the first 3D viewport's camera and "
                 "shading. Camera mode uses the scene engine — heavy Cycles "
                 "renders may outlive the 180 s bridge timeout."),
    }
