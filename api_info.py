# -*- coding:utf-8 -*-
"""Runtime API lookup for MCP Socket: exact signatures from the live bpy.

The agent's main error source when writing execute_code is hallucinated
API: wrong parameter names, missing enum items, invented defaults. This
handler answers from the *running* Blender — bpy.ops RNA properties,
bpy.types members, enum item lists — so the agent checks instead of
guessing. Inspired by bpy-dev/blender-mcp's "runtime API lookup".

Queries:
    "bpy.ops.export_scene.fbx"                  → operator properties
    "bpy.types.ImageFormatSettings"             → type overview
    "bpy.types.ImageFormatSettings.file_format" → property + enum items
    "ImageFormatSettings.file_format"           → same (bpy. prefix optional)
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import bpy

_MAX_PROPS = 40
_MAX_ENUM = 60
_MAX_DESC = 220


def _clip(text: Any, limit: int = _MAX_DESC) -> str:
    """Description with a hard cap — long RNA docs burn the agent's context."""
    s = str(text) if text is not None else ""
    return s if len(s) <= limit else s[: limit - 3] + "..."


def _prop_card(prop) -> Dict[str, Any]:
    """One RNA property → compact JSON card."""
    card: Dict[str, Any] = {
        "name": prop.identifier,
        "type": prop.type,
        "readonly": bool(prop.is_readonly),
        "description": _clip(prop.description),
    }
    if prop.type in ("INT", "FLOAT", "BOOLEAN", "STRING", "ENUM"):
        try:
            card["default"] = prop.default
        except Exception:  # noqa: BLE001 — some defaults raise on exotic arrays
            pass
    if prop.type == "ENUM":
        try:
            card["enum_items"] = [item.identifier for item in prop.enum_items][: _MAX_ENUM]
        except Exception:  # noqa: BLE001 — dynamic enums may need a context
            card["enum_items"] = []
    if prop.type == "POINTER" and prop.fixed_type is not None:
        card["points_to"] = prop.fixed_type.identifier
    return card


def _operator_card(op_obj, identifier: str) -> Dict[str, Any]:
    rna = op_obj.get_rna_type()
    props = [_prop_card(p) for p in rna.properties.values()
             if p.identifier != "rna_type"][: _MAX_PROPS]
    # rna.identifier = "EXPORT_SCENE_OT_fbx" → category + operator name
    cat, _, op_name = rna.identifier.partition("_OT_")
    return {
        "query": identifier,
        "kind": "operator",
        "identifier": "bpy.ops.%s.%s" % (cat.lower(), op_name.lower()),
        "rna_identifier": rna.identifier,
        "description": _clip(rna.description),
        "properties": props,
        "hint": ("Pass these names as kwargs to the operator; "
                 "bl_idname is what bpy.ops resolves to."),
    }


def _type_card(cls, identifier: str) -> Dict[str, Any]:
    rna = cls.bl_rna
    props = [_prop_card(p) for p in rna.properties.values()
             if p.identifier != "rna_type"][: _MAX_PROPS]
    functions = sorted(f.identifier for f in rna.functions)[: _MAX_PROPS]
    return {
        "query": identifier,
        "kind": "type",
        "identifier": "bpy.types.%s" % rna.identifier,
        "description": _clip(rna.description),
        "properties": props,
        "functions": functions,
        "truncated": {"properties": len(rna.properties) - 1 > _MAX_PROPS},
        "hint": "Query a member: bpy.types.%s.<name> for its full card." % rna.identifier,
    }


def _member_card(cls, member: str, identifier: str) -> Dict[str, Any]:
    rna = cls.bl_rna
    prop = rna.properties.get(member)
    if prop is not None:
        card = _prop_card(prop)
        card.update({"query": identifier, "kind": "property",
                     "owner": "bpy.types.%s" % rna.identifier})
        return card
    func = rna.functions.get(member)
    if func is not None:
        params = [_prop_card(p) for p in func.properties.values()] [: _MAX_PROPS]
        return {
            "query": identifier,
            "kind": "function",
            "identifier": "bpy.types.%s.%s" % (rna.identifier, func.identifier),
            "description": _clip(func.description),
            "parameters": params,
        }
    raise ValueError(
        "bpy.types.%s has no member %r — see 'properties' via querying "
        "bpy.types.%s" % (rna.identifier, member, rna.identifier))


def _suggest(prefix: str, tail: str) -> List[str]:
    """Close matches from the given namespace for the error message."""
    import difflib
    names = [n for n in dir(prefix) if not n.startswith("_")]
    return difflib.get_close_matches(tail, names, n=4, cutoff=0.4)


def get_bpy_api_info(query: str) -> Dict[str, Any]:
    """Resolve a bpy query against the running Blender and return exact data."""
    if not query or not str(query).strip():
        raise ValueError("No query provided. Examples: bpy.ops.export_scene.fbx, "
                         "bpy.types.ImageFormatSettings.file_format")
    q = str(query).strip()
    if not q.startswith("bpy."):
        q = "bpy.types." + q

    parts = q.split(".")

    if parts[1] == "ops":
        if len(parts) < 4:
            raise ValueError("Operator query needs a category and an id: "
                             "bpy.ops.<category>.<operator> (got %r)" % query)
        obj = bpy.ops
        for part in parts[2:]:
            obj = getattr(obj, part)  # AttributeError → unknown op
        try:
            return _operator_card(obj, q)
        except Exception as exc:  # noqa: BLE001 — bpy raises many exotic types here
            raise ValueError("Unknown operator %r (%s). Operators live in "
                             "categories: bpy.ops.<category>.<name>"
                             % (query, exc)) from exc

    if parts[1] == "types":
        tail = parts[2:]
        if not tail:
            raise ValueError("Type query needs a class name, e.g. "
                             "bpy.types.ImageFormatSettings")
        try:
            cls = getattr(bpy.types, tail[0])
        except AttributeError as exc:
            raise ValueError("Unknown type %r. Close matches in bpy.types: %s"
                             % (tail[0], _suggest(bpy.types, tail[0]))) from exc
        if len(tail) == 1:
            return _type_card(cls, q)
        return _member_card(cls, ".".join(tail[1:]), q)

    raise ValueError("Unsupported root %r — use bpy.ops.<category>.<operator> "
                     "or bpy.types.<Type>[.<member>]" % query)
