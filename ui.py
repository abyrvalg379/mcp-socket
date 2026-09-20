# -*- coding:utf-8 -*-
"""UI for ZCode_MCP: a sidebar panel in the 3D viewport and addon preferences.

Panel shows a live connection-status badge, an active-client/last-command
counter, a Start/Stop operator, a Test-connection operator, and the port.
A background timer refreshes the panel every ~1.5s so the badge tracks real
client connections without the user clicking anything.
"""

from __future__ import annotations

import bpy
from bpy.props import BoolProperty, IntProperty, StringProperty
from bpy.types import AddonPreferences, Operator, Panel

from .queries import bridge_version

_TAG = "[ZCode_MCP]"
_DEFAULT_PORT = 9876

# Panel header shows the version (product rule: every addon's N-panel header
# carries its version). Read from blender_manifest.toml — single source.
_VERSION_STR = bridge_version()

# Refresh cadence for the status badge / counters (seconds). Light enough to
# feel live, not so fast it spams tag_redraw.
_REFRESH_INTERVAL = 1.5


# ── addon preferences ────────────────────────────────────────────────────

class ZCodeMCP_AddonPreferences(AddonPreferences):
    bl_idname = "zcode_mcp"

    port: IntProperty(
        name="Port",
        description="TCP port for the ZCode MCP bridge (matches blender-mcp default)",
        default=_DEFAULT_PORT,
        min=1024,
        max=65535,
    )
    telemetry_consent: BoolProperty(
        name="Allow richer anonymous telemetry",
        description=(
            "If on, the bridge reports 'consent=True' to blender-mcp's "
            "telemetry check, enabling prompts/snippets in its anonymous events. "
            "Off by default — only minimal usage data is collected by blender-mcp."
        ),
        default=False,
    )
    auto_port_offset: BoolProperty(
        name="Auto port offset for second instance",
        description=(
            "If the preferred port is busy (e.g. another Blender instance "
            "already runs a bridge), try the next 10 ports instead of failing. "
            "Clients discover the actual port via the instance registry "
            "(list_instances / get_bridge_info)."
        ),
        default=True,
    )


# ── helpers ──────────────────────────────────────────────────────────────

def _prefs(context) -> ZCodeMCP_AddonPreferences:
    addon = context.preferences.addons.get("zcode_mcp")
    return addon.preferences if addon else None


def _server() -> object:
    """The live ZCodeMCPServer instance, or None."""
    return getattr(bpy.types, "zcode_mcp_server", None)


def _classify_status(snap: dict) -> tuple[str, int, bool]:
    """Return (label, icon_value, alert) for the status badge.

    Two states only — the bridge is either running (green dot) or stopped
    (red dot). Per-client connection state is intentionally not surfaced:
    blender-mcp.exe connects only transiently per command, so a live/
    no-client split would read as noise the other 99% of the time.

    icon_value comes from the previews module (generated green/red circle
    PNGs — see icons.py for the lazy-submodule import gotcha). 0 means icons
    failed to load — draw() falls back to text-only.
    """
    from . import icons
    if not snap or not snap.get("running"):
        return "STOPPED", icons.RED, True
    return "RUNNING", icons.GREEN, False


def _fmt_age(age) -> str:
    if age is None:
        return "—"
    if age < 1.0:
        return "just now"
    if age < 60.0:
        return f"{int(age)}s ago"
    if age < 3600.0:
        return f"{int(age // 60)}m ago"
    return "—"


# ── operators ────────────────────────────────────────────────────────────

class ZCODEMCP_OT_start(Operator):
    bl_idname = "zcode_mcp.start"
    bl_label = "Start Bridge"
    bl_description = "Start the ZCode MCP TCP bridge on the configured port"

    def execute(self, context):
        from . import server as _server_mod
        prefs = _prefs(context)
        port = prefs.port if prefs else _DEFAULT_PORT
        auto_offset = bool(prefs.auto_port_offset) if prefs else True

        current = _server()
        if current is None:
            bpy.types.zcode_mcp_server = _server_mod.ZCodeMCPServer(
                port=port, auto_offset=auto_offset)
        ok = bpy.types.zcode_mcp_server.start()
        context.scene.zcode_mcp_running = bpy.types.zcode_mcp_server.running
        if ok:
            self.report({"INFO"}, f"ZCode MCP bridge on :{port}")
        else:
            self.report({"ERROR"}, f"Could not start ZCode MCP bridge on :{port} "
                                   "(port busy? remove the old 'Blender MCP' addon)")
        return {"FINISHED"}


class ZCODEMCP_OT_stop(Operator):
    bl_idname = "zcode_mcp.stop"
    bl_label = "Stop Bridge"
    bl_description = "Stop the ZCode MCP TCP bridge"

    def execute(self, context):
        current = _server()
        if current is not None:
            current.stop()
            del bpy.types.zcode_mcp_server
        context.scene.zcode_mcp_running = False
        self.report({"INFO"}, "ZCode MCP bridge stopped")
        return {"FINISHED"}


class ZCODEMCP_OT_test(Operator):
    bl_idname = "zcode_mcp.test"
    bl_label = "Test Connection"
    bl_description = "Run get_scene_info locally and report the result"

    def execute(self, context):
        # Self-test runs the handler directly on the main thread (we are on it
        # already inside an operator). This validates the bridge's *handler*
        # layer without needing a live socket client.
        from . import handlers
        try:
            result = handlers.get_scene_info()
            obj_count = result.get("object_count", "?")
            mat_count = result.get("materials_count", "?")
            self.report({"INFO"},
                        f"OK — {obj_count} objects, {mat_count} materials")
            context.scene.zcode_mcp_test_ok = True
            context.scene.zcode_mcp_test_msg = f"{obj_count} objects · {mat_count} materials"
        except Exception as exc:  # noqa: BLE001
            self.report({"ERROR"}, f"Test failed: {exc}")
            context.scene.zcode_mcp_test_ok = False
            context.scene.zcode_mcp_test_msg = f"ERROR: {exc}"
        return {"FINISHED"}


class ZCODEMCP_OT_log_save(Operator):
    bl_idname = "zcode_mcp.log_save"
    bl_label = "Save Log"
    bl_description = ("Dump the console ring buffer to a timestamped file in "
                      "%TEMP% (path lands in the clipboard)")

    def execute(self, context):
        import os
        import tempfile
        import time as _time
        from . import logcap
        data = logcap.get_console_log(last_n=logcap.MAX_LINES)
        out_dir = os.path.join(tempfile.gettempdir(), "zcode_mcp")
        os.makedirs(out_dir, exist_ok=True)
        path = os.path.join(
            out_dir, f"console_{_time.strftime('%Y%m%d_%H%M%S')}.log")
        with open(path, "w", encoding="utf-8") as fh:
            for entry in data["lines"]:
                ts = entry.get("ts")
                stamp = (_time.strftime("%H:%M:%S", _time.localtime(ts))
                         if ts else "--:--:--")
                fh.write(f"[{stamp}][{entry['stream']}] {entry['text']}\n")
        context.window_manager.clipboard = path
        self.report({"INFO"}, f"Saved: {path}")
        return {"FINISHED"}


class ZCODEMCP_OT_log_clear(Operator):
    bl_idname = "zcode_mcp.log_clear"
    bl_label = "Clear"
    bl_description = "Clear the console ring buffer"

    def execute(self, context):
        from . import logcap
        logcap.clear_console_log()
        self.report({"INFO"}, "Console log cleared")
        return {"FINISHED"}


# ── sidebar panel ────────────────────────────────────────────────────────

class ZCODEMCP_PT_panel(Panel):
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "ZCode MCP"
    bl_label = f"ZCode MCP Bridge {_VERSION_STR}"

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        srv = _server()
        snap = srv.status_snapshot() if srv else {"running": False}

        # ── Status badge: green dot (RUNNING) / red dot (STOPPED) ───────────
        label, icon_value, alert = _classify_status(snap)
        box = layout.box()
        row = box.row()
        row.alert = alert
        if icon_value:
            row.label(text=label, icon_value=icon_value)
        else:
            # Icons failed to load — fall back to a text-only badge so the
            # panel still renders (alert flag still tints the row red/green).
            row.label(text=label)

        # ── Last command (proof the bridge actually serves requests) ───────
        if snap.get("running"):
            last = snap.get("last_command")
            last_st = snap.get("last_status")
            age = snap.get("last_command_age")
            row = layout.row(align=True)
            if last:
                ok = (last_st == "success")
                row.label(text=f"Last: {last}",
                          icon="CHECKMARK" if ok else "ERROR")
                row.label(text=_fmt_age(age))
            else:
                row.active = False
                row.label(text="No commands yet")

        # ── Test connection result (shown briefly after the test) ─────────
        test_msg = getattr(scene, "zcode_mcp_test_msg", "")
        if test_msg:
            row = layout.row(align=True)
            ok = getattr(scene, "zcode_mcp_test_ok", False)
            row.alert = not ok
            row.label(text=test_msg,
                      icon="CHECKMARK" if ok else "ERROR")

        # ── Actions ───────────────────────────────────────────────────────
        col = layout.column(align=True)
        col.scale_y = 1.3
        if snap.get("running"):
            col.operator("zcode_mcp.stop", icon="PAUSE")
            col.operator("zcode_mcp.test", icon="CONSOLE")
        else:
            col.operator("zcode_mcp.start", icon="PLAY")

        # ── Port (live value when running — may be offset in a second
        # instance; editable in Preferences) ────────────────────────────────
        prefs = _prefs(context)
        if snap.get("running"):
            port_val = snap.get("port")
        else:
            port_val = prefs.port if prefs else _DEFAULT_PORT
        row = layout.row(align=True)
        row.active = False
        row.label(text=f"Port: {port_val}", icon="SCRIPT")


# ── console log sub-panel ────────────────────────────────────────────────

class ZCODEMCP_PT_log(Panel):
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "ZCode MCP"
    bl_parent_id = "ZCODEMCP_PT_panel"
    bl_label = "Console Log"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout
        from . import logcap
        lines = [e["text"] for e in logcap.get_console_log(last_n=8)["lines"]]
        if not lines:
            row = layout.row()
            row.active = False
            row.label(text="(empty)")
        else:
            col = layout.column(align=True)
            col.scale_y = 0.85
            for text in lines:
                col.label(text=text[:64])
        row = layout.row(align=True)
        row.operator("zcode_mcp.log_save", text="Save to File", icon="EXPORT")
        row.operator("zcode_mcp.log_clear", text="", icon="X")


# ── refresh timer ────────────────────────────────────────────────────────

_refresh_timer = None
_hb_tick = 0


def _refresh_panel():
    """Periodically nudge the viewport UI to redraw so the badge stays live.

    Connection state changes happen on background threads; without this the
    panel only repaints on user interaction. Every ~7th tick (~10s) it also
    refreshes the multi-instance registry heartbeat. Returns None so the timer
    keeps firing at _REFRESH_INTERVAL.
    """
    global _hb_tick
    _hb_tick += 1
    if _hb_tick % 7 == 0:
        try:
            from . import queries
            queries.heartbeat()
        except Exception:  # noqa: BLE001 — heartbeat must never kill the timer
            pass
    # Force a redraw of all 3D viewports so the badge/counters update.
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == "VIEW_3D":
                area.tag_redraw()
    return _REFRESH_INTERVAL


# ── registration ─────────────────────────────────────────────────────────

classes = (
    ZCodeMCP_AddonPreferences,
    ZCODEMCP_OT_start,
    ZCODEMCP_OT_stop,
    ZCODEMCP_OT_test,
    ZCODEMCP_OT_log_save,
    ZCODEMCP_OT_log_clear,
    ZCODEMCP_PT_panel,
    ZCODEMCP_PT_log,
)


def register() -> None:
    global _refresh_timer
    for cls in classes:
        try:
            bpy.utils.register_class(cls)
        except Exception as exc:  # noqa: BLE001
            print(f"{_TAG} register {cls.__name__}: {exc}")

    bpy.types.Scene.zcode_mcp_port = IntProperty(
        name="Port",
        default=_DEFAULT_PORT,
        min=1024,
        max=65535,
    )
    bpy.types.Scene.zcode_mcp_running = BoolProperty(name="Running", default=False)
    # Transient state for the Test-connection operator's result display.
    bpy.types.Scene.zcode_mcp_test_ok = BoolProperty(default=False)
    bpy.types.Scene.zcode_mcp_test_msg = StringProperty(default="")

    # Start the refresh timer so the badge tracks live connections.
    if _refresh_timer is None:
        try:
            _refresh_timer = bpy.app.timers.register(_refresh_panel, first_interval=_REFRESH_INTERVAL)
        except Exception as exc:  # noqa: BLE001
            print(f"{_TAG} could not register refresh timer: {exc}")


def unregister() -> None:
    global _refresh_timer
    if _refresh_timer is not None:
        try:
            bpy.app.timers.unregister(_refresh_timer)
        except Exception:  # noqa: BLE001
            pass
        _refresh_timer = None

    for cls in reversed(classes):
        try:
            bpy.utils.unregister_class(cls)
        except Exception:  # noqa: BLE001
            pass

    for attr in ("zcode_mcp_port", "zcode_mcp_running",
                 "zcode_mcp_test_ok", "zcode_mcp_test_msg"):
        try:
            delattr(bpy.types.Scene, attr)
        except AttributeError:
            pass
