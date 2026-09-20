# -*- coding:utf-8 -*-
"""Export presets for MCP Socket pipeline handlers.

Philosophy (PROKLADKA): the SENDING side exports in a NEUTRAL format —
meters, Y-up, binary FBX — and the RECEIVING DCC adapts the asset to its own
conventions. Therefore all presets share the same FBX settings; the per-DCC
entries exist to document the receiver contract and to carry future deltas
if a receiver ever needs them.

Settings are pipeline contracts: they change with a release, stay identical
across machines, and every deviation is passed as an explicit override
argument (and echoed in the export report).
"""

from __future__ import annotations

from typing import Any, Dict

# Neutral base — pinned io_scene.fbx exporter settings (verified against the
# live RNA; the exporter in 5.2 is ``export_scene.fbx``).
_BASE: Dict[str, Any] = {
    "global_scale": 1.0,          # meters stay meters — no magic multipliers
    "use_selection": True,        # scope resolved by the handler
    "use_mesh_modifiers": True,   # modifiers baked in
    "axis_forward": "-Z",         # Blender defaults → Y-up FBX file
    "axis_up": "Y",
    "use_space_transform": True,
    "bake_anim": False,           # asset transport: no animation unless asked
    "add_leaf_bones": False,      # Maya-friendly: no extra leaf bones
    "path_mode": "AUTO",
}

# Receiver contracts (PROKLADKA philosophy table). ``settings`` may carry
# documented deltas from _BASE — today there are none: receivers adapt.
PRESETS: Dict[str, Dict[str, Any]] = {
    "neutral": {
        "note": "Pure neutral FBX: meters, Y-up, binary. No receiver adaptation.",
        "receiver": "any",
        "settings": dict(_BASE),
    },
    "maya": {
        "note": ("Maya 2025 receiver: meters (import ×100 fix pending in "
                 "bridge_qt), UV map1, naming lowercase + _geo/_grp."),
        "receiver": "Maya",
        "settings": dict(_BASE),
    },
    "houdini": {
        "note": ("Houdini 20.5 receiver: meters, node suffixes "
                 "_geo/_vdb/_abc, attrs over UVs."),
        "receiver": "Houdini",
        "settings": dict(_BASE),
    },
    "ue": {
        "note": ("Unreal receiver: cm — import scale ×100 (m→cm) done UE-side "
                 "by PROKLADKA, naming PascalCase + SM_/SK_/T_/MI_."),
        "receiver": "Unreal",
        "settings": dict(_BASE),
    },
}


def get_preset(name: str) -> Dict[str, Any]:
    """Resolve a preset by name. Raises ValueError with valid names."""
    try:
        return PRESETS[name]
    except KeyError:
        raise ValueError(
            f"Unknown preset {name!r}. Valid: {sorted(PRESETS)}") from None


def preset_names() -> list:
    return sorted(PRESETS)
