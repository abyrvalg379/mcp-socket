# -*- coding:utf-8 -*-
"""Structured read-only scene queries + multi-instance registry for MCP_Socket.

These handlers exist so MCP clients can inspect the scene without falling back
to ``execute_code`` for every question: cheaper round trips, no accidental
mutations, JSON-safe output. Everything here is read-only with the single
exception of the instance-registry files in the temp dir.

Multi-instance: every running bridge writes ``%TEMP%/mcp_socket_instances/
pid_<pid>.json`` on start and refreshes it from the UI refresh timer
(heartbeat). A second Blender instance can't bind the primary port, so the
server walks +1..+10 (see server.py) — ``list_instances`` is how a client
discovers who is on which port. Files whose mtime is older than
``stale_seconds`` are treated as dead instances (Blender killed without
unregister).
"""

from __future__ import annotations

import json
import os
import time
import tomllib
from typing import Any, Dict, List, Optional

import bpy

_TAG = "[MCP_Socket]"

_VERSION_CACHE: str = ""

# ── bridge / instance info ────────────────────────────────────────────────


def bridge_version() -> str:
    """Read the version from blender_manifest.toml — single source of truth."""
    global _VERSION_CACHE
    if _VERSION_CACHE:
        return _VERSION_CACHE
    try:
        path = os.path.join(os.path.dirname(__file__), "blender_manifest.toml")
        with open(path, "rb") as fh:
            _VERSION_CACHE = str(tomllib.load(fh).get("version", "?"))
    except Exception:  # noqa: BLE001 — diagnostics must never raise
        _VERSION_CACHE = "?"
    return _VERSION_CACHE


def get_bridge_info() -> Dict[str, Any]:
    """One-call identity card of this Blender instance."""
    scene = getattr(bpy.context, "scene", None)
    srv = getattr(bpy.types, "mcp_socket_server", None)
    snap: Dict[str, Any] = srv.status_snapshot() if srv is not None else {}
    return {
        "bridge_version": bridge_version(),
        "blender_version": bpy.app.version_string,
        "pid": os.getpid(),
        "background": bool(bpy.app.background),
        "scene": scene.name if scene else None,
        "filepath": bpy.data.filepath or None,
        "object_count": len(scene.objects) if scene else 0,
        "materials_count": len(bpy.data.materials),
        "fps": scene.render.fps if scene else None,
        "server_running": bool(snap.get("running")),
        "port": snap.get("port"),
        "total_commands": snap.get("total_commands", 0),
    }


# ── instance registry (multi-instance discovery) ──────────────────────────


def _registry_dir() -> str:
    import tempfile
    return os.path.join(tempfile.gettempdir(), "mcp_socket_instances")


def write_instance_file(server) -> Optional[str]:
    """Write/refresh this instance's registry file. Returns the path or None."""
    try:
        reg = _registry_dir()
        os.makedirs(reg, exist_ok=True)
        # During register bpy.context is _RestrictContext — scene may be absent.
        scene = getattr(bpy.context, "scene", None)
        data = {
            "pid": os.getpid(),
            "host": server.host,
            "port": server.port,
            "bridge_version": bridge_version(),
            "blender_version": bpy.app.version_string,
            "scene": scene.name if scene else "",
            "filepath": (getattr(bpy.data, "filepath", "") or ""),
            "ts": round(time.time(), 3),
        }
        path = os.path.join(reg, f"pid_{os.getpid()}.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        return path
    except Exception as exc:  # noqa: BLE001
        print(f"{_TAG} instance registry write failed: {exc}")
        return None


def remove_instance_file() -> None:
    try:
        path = os.path.join(_registry_dir(), f"pid_{os.getpid()}.json")
        if os.path.isfile(path):
            os.remove(path)
    except OSError:
        pass


def heartbeat() -> None:
    """Refresh the registry file if this instance's server is running.

    Called from the UI refresh timer — keep it cheap and silent.
    """
    srv = getattr(bpy.types, "mcp_socket_server", None)
    if srv is not None and getattr(srv, "running", False):
        write_instance_file(srv)


def list_instances(stale_seconds: float = 15.0) -> Dict[str, Any]:
    """Live bridge instances on this machine (registry dir, mtime-fresh)."""
    reg = _registry_dir()
    instances: List[Dict[str, Any]] = []
    if os.path.isdir(reg):
        now = time.time()
        for fn in os.listdir(reg):
            if not (fn.startswith("pid_") and fn.endswith(".json")):
                continue
            path = os.path.join(reg, fn)
            try:
                age = now - os.path.getmtime(path)
                if age > stale_seconds:
                    continue
                with open(path, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                data["age_seconds"] = round(age, 1)
                instances.append(data)
            except (OSError, ValueError):
                continue
    instances.sort(key=lambda i: i.get("port", 0))
    return {"instances": instances, "registry_dir": reg}


# ── scene queries ─────────────────────────────────────────────────────────


def _r3(vec) -> List[float]:
    return [round(float(v), 4) for v in vec]


def get_hierarchy() -> Dict[str, Any]:
    """Collection tree of the scene with the objects in each collection.

    An object linked into several collections appears in each of them.
    """
    scene = getattr(bpy.context, "scene", None)
    if scene is None:
        return {"scene": None, "world": None, "tree": {}}

    def node(coll, visited):
        entry: Dict[str, Any] = {"name": coll.name, "objects": [], "children": []}
        if coll.name in visited:
            entry["circular"] = True
            return entry
        visited.add(coll.name)
        for ob in coll.objects:
            entry["objects"].append({
                "name": ob.name,
                "type": ob.type,
                "hide_viewport": bool(ob.hide_viewport),
                "hide_render": bool(ob.hide_render),
            })
        for child in coll.children:
            entry["children"].append(node(child, visited))
        return entry

    return {
        "scene": scene.name,
        "world": scene.world.name if scene.world else None,
        "tree": node(scene.collection, set()),
    }


def get_object_data(name: str) -> Dict[str, Any]:
    """Full read-only dossier for one object: transforms, modifiers, slots..."""
    obj = bpy.data.objects.get(name)
    if obj is None:
        raise ValueError(f"Object not found: {name}")

    info: Dict[str, Any] = {
        "name": obj.name,
        "type": obj.type,
        "parent": obj.parent.name if obj.parent else None,
        "location": _r3(obj.location),
        "rotation_euler": _r3(obj.rotation_euler),
        "scale": _r3(obj.scale),
        "dimensions": _r3(obj.dimensions),
        "hide_viewport": bool(obj.hide_viewport),
        "hide_render": bool(obj.hide_render),
        "visible": obj.visible_get(),
        "collections": [c.name for c in obj.users_collection],
        "modifiers": [
            {"name": m.name, "type": m.type,
             "show_viewport": bool(m.show_viewport),
             "show_render": bool(m.show_render)}
            for m in obj.modifiers
        ],
        "constraints": [
            {"name": c.name, "type": c.type} for c in obj.constraints
        ] if hasattr(obj, "constraints") else [],
        "material_slots": [
            {"slot": i, "material": s.material.name if s.material else None,
             "link": s.link}
            for i, s in enumerate(obj.material_slots)
        ],
        "custom_props": sorted(k for k in obj.keys() if not k.startswith("_")),
        "action": (obj.animation_data.action.name
                   if obj.animation_data and obj.animation_data.action else None),
    }

    if obj.type == "MESH":
        me = obj.data
        info["mesh"] = {
            "vertices": len(me.vertices),
            "edges": len(me.edges),
            "polygons": len(me.polygons),
            "uv_layers": [u.name for u in me.uv_layers],
            "shape_keys": (len(me.shape_keys.key_blocks)
                           if me.shape_keys else 0),
        }
    return info


def get_material_info(name: str) -> Dict[str, Any]:
    """Material dossier: nodes, image textures with colorspace + file state."""
    mat = bpy.data.materials.get(name)
    if mat is None:
        raise ValueError(f"Material not found: {name}")

    info: Dict[str, Any] = {
        "name": mat.name,
        "users": mat.users,
        "use_nodes": bool(mat.use_nodes),
        "nodes": [],
        "image_textures": [],
        "output": None,
    }
    if mat.use_nodes and mat.node_tree:
        for n in mat.node_tree.nodes:
            info["nodes"].append({"name": n.name, "type": n.type,
                                  "label": n.label})
            if n.type == "TEX_IMAGE" and n.image is not None:
                img = n.image
                fp = bpy.path.abspath(img.filepath) if img.filepath else ""
                info["image_textures"].append({
                    "node": n.name,
                    "image": img.name,
                    "filepath": fp,
                    "colorspace": img.colorspace_settings.name,
                    "packed": bool(img.packed_file),
                    "exists": bool(fp) and os.path.isfile(fp),
                })
        for n in mat.node_tree.nodes:
            if n.type == "OUTPUT_MATERIAL" and getattr(n, "is_active_output", True):
                info["output"] = {"node": n.name,
                                  "target": getattr(n, "target", "")}
                break
    return info


def get_images_report() -> Dict[str, Any]:
    """All images in the blend: paths, colorspace, packed, missing files.

    Built for the FLOMASTER/OCIO workflow — a one-call answer to "which
    textures are raw/acescg and which files have vanished from disk".
    """
    images: List[Dict[str, Any]] = []
    for img in bpy.data.images:
        fp = bpy.path.abspath(img.filepath) if img.filepath else ""
        images.append({
            "name": img.name,
            "source": img.source,
            "filepath": fp,
            "colorspace": img.colorspace_settings.name,
            "packed": bool(img.packed_file),
            "exists": bool(fp) and os.path.isfile(fp),
            "size": list(img.size),
            "users": img.users,
        })
    missing = [i["name"] for i in images
               if i["filepath"] and not i["packed"] and not i["exists"]]
    return {"count": len(images), "missing": missing, "images": images}
