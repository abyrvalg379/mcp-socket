# -*- coding:utf-8 -*-
"""Pipeline handlers for MCP Socket: FBX export/import per PROKLADKA rules.

Export — the SENDING side stays neutral: meters, Y-up, binary FBX, modifiers
baked. Per-call ``overrides`` are merged over the preset and echoed in the
report, so every deviation from the contract is explicit and auditable.

Import — the RECEIVER rule (PROKLADKA 2026-08-30): the imported asset lands
in a container EMPTY with t=0 r=0 s=1 at the world origin; correct dimensions
bake into the geometry, no compensating transforms on the container. No
automatic rescaling and no automatic renaming — oversized meshes (>50 m) are
reported for a human decision instead (never add magic multipliers).
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

import bpy

from . import presets as _presets

_TAG = "[MCP_Socket]"

# PROKLADKA import rule: meshes larger than this are flagged, not rescaled.
_OVERSIZE_METERS = 50.0

_SCOPES = ("selected", "collection", "scene")


# ── presets ───────────────────────────────────────────────────────────────

def list_presets() -> Dict[str, Any]:
    """All export presets with their resolved settings and receiver notes."""
    return {
        "presets": [
            {"name": name,
             "note": data["note"],
             "receiver": data["receiver"],
             "settings": dict(data["settings"])}
            for name, data in sorted(_presets.PRESETS.items())
        ]
    }


# ── export ────────────────────────────────────────────────────────────────

def _resolve_export_objects(scope: str, collection_name: str) -> List[bpy.types.Object]:
    """Objects to export. Raises ValueError with one clear message on misses."""
    if scope not in _SCOPES:
        raise ValueError(f"Unknown scope {scope!r}. Valid: {list(_SCOPES)}")

    if scope == "selected":
        objs = list(bpy.context.selected_objects)
        if not objs:
            raise ValueError("Nothing selected — select objects to export "
                             "or pass scope='collection'/'scene'.")
    elif scope == "collection":
        if not collection_name:
            raise ValueError("scope='collection' requires collection_name.")
        coll = bpy.data.collections.get(collection_name)
        if coll is None:
            raise ValueError(f"Collection not found: {collection_name!r}")
        objs = list(coll.all_objects)
        if not objs:
            raise ValueError(f"Collection {collection_name!r} is empty.")
    else:  # scene
        objs = list(bpy.context.scene.objects)
        if not objs:
            raise ValueError("The scene is empty — nothing to export.")

    # PROKLADKA lesson: use_selection does NOT pull children automatically —
    # expand the selection with children_recursive so an Empty exports its
    # whole branch.
    expanded = set(objs)
    for obj in objs:
        expanded.update(obj.children_recursive)
    return sorted(expanded, key=lambda o: o.name)


def export_fbx(path: str, preset: str = "maya", scope: str = "selected",
               collection_name: str = "",
               overrides: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Export to FBX with a pipeline preset. Returns a self-documenting report."""
    if not path or not str(path).strip():
        raise ValueError("No filepath provided.")
    data = _presets.get_preset(preset)

    final_path = str(path)
    if not final_path.lower().endswith(".fbx"):
        final_path += ".fbx"
    final_path = bpy.path.abspath(final_path)
    parent = os.path.dirname(final_path)
    if parent:
        os.makedirs(parent, exist_ok=True)

    objects = _resolve_export_objects(scope, collection_name)

    settings = dict(data["settings"])
    settings["use_selection"] = True  # scope is applied through the selection
    overrides = overrides or {}
    op_props = bpy.ops.export_scene.fbx.get_rna_type().properties
    valid = {p.identifier for p in op_props.values()}
    unknown = [k for k in overrides if k not in valid]
    if unknown:
        raise ValueError(f"Unknown override keys: {sorted(unknown)}. "
                         f"Valid keys are the export_scene.fbx properties.")
    settings.update(overrides)

    saved = set(o.name for o in bpy.context.selected_objects)
    try:
        with bpy.context.temp_override(selected_objects=objects,
                                       selected_editable_objects=objects):
            bpy.ops.export_scene.fbx(filepath=final_path, **settings)
    except Exception as exc:
        raise RuntimeError(f"FBX export failed: {exc}") from exc
    finally:
        _restore_selection(saved)

    return {
        "exported": True,
        "filepath": final_path,
        "size_bytes": os.path.getsize(final_path) if os.path.isfile(final_path) else 0,
        "preset": preset,
        "receiver": data["receiver"],
        "note": data["note"],
        "scope": scope,
        "objects": [o.name for o in objects],
        "overrides_applied": dict(overrides),
        "settings": {k: str(v) for k, v in settings.items()},
    }


def _restore_selection(saved_names: set) -> None:
    """Best-effort restore of the user's selection after a scoped export."""
    try:
        bpy.ops.object.select_all(action="DESELECT")
        for name in saved_names:
            obj = bpy.data.objects.get(name)
            if obj is not None:
                obj.select_set(True)
    except Exception:  # noqa: BLE001 — never break the export over selection
        pass


# ── import ────────────────────────────────────────────────────────────────

def _sanitize_container_name(stem: str) -> str:
    """Filename stem → safe Blender name (spaces kept, C-identifiers fixed)."""
    name = stem.strip() or "imported"
    if not name[0].isalpha() and name[0] != "_":
        name = "_" + name
    return name


def import_fbx(path: str, container: bool = True,
               global_scale: Optional[float] = None) -> Dict[str, Any]:
    """Import FBX per the receiver rule: container EMPTY t=0 r=0 s=1.

    No automatic rescaling or renaming — oversized meshes (>50 m) are
    reported instead so a human decides (never magic multipliers).
    """
    if not path or not str(path).strip():
        raise ValueError("No filepath provided.")
    final_path = bpy.path.abspath(str(path))
    if not os.path.isfile(final_path):
        raise ValueError(f"File not found: {final_path}")

    before = set(o.name for o in bpy.data.objects)
    kwargs: Dict[str, Any] = {"filepath": final_path}
    if global_scale is not None:
        kwargs["global_scale"] = float(global_scale)
    try:
        bpy.ops.import_scene.fbx(**kwargs)
    except Exception as exc:
        raise RuntimeError(f"FBX import failed: {exc}") from exc

    imported = [o for o in bpy.data.objects if o.name not in before]
    if not imported:
        raise RuntimeError(f"FBX import produced no objects: {final_path}")

    imported_set = set(imported)
    top_level = [o for o in imported if o.parent not in imported_set]

    container_name = None
    if container:
        stem = os.path.splitext(os.path.basename(final_path))[0]
        cont = bpy.data.objects.new(_sanitize_container_name(stem), None)
        cont.empty_display_type = "PLAIN_AXES"
        bpy.context.scene.collection.objects.link(cont)
        for obj in top_level:
            obj.parent = cont  # container is identity → world transforms kept
        container_name = cont.name

    oversized = []
    for obj in imported:
        if obj.type == "MESH":
            dim = obj.dimensions
            if dim and max(dim) > _OVERSIZE_METERS:
                oversized.append({"name": obj.name,
                                  "max_dimension_m": round(float(max(dim)), 3)})

    return {
        "imported": True,
        "filepath": final_path,
        "container": container_name,
        "objects_count": len(imported),
        "top_level": [o.name for o in top_level],
        "objects": [o.name for o in imported],
        "oversized": oversized,
        "note": ("Receiver rule: container t=0 r=0 s=1, geometry carries the "
                 "dimensions. Oversized meshes are reported, never rescaled."),
    }
