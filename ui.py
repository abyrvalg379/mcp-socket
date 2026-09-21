# -*- coding:utf-8 -*-
"""UI for MCP_Socket: a sidebar panel in the 3D viewport and addon preferences.

Panel shows a live connection-status badge, an active-client/last-command
counter, a Start/Stop operator, a Test-connection operator, and the port.
A background timer refreshes the panel every ~1.5s so the badge tracks real
client connections without the user clicking anything.
"""

from __future__ import annotations

import bpy
from bpy.props import BoolProperty, EnumProperty, IntProperty, StringProperty
from bpy.types import AddonPreferences, Operator, Panel

from .queries import bridge_version

_TAG = "[MCP_Socket]"
_DEFAULT_PORT = 9876

# Panel header shows the version (product rule: every addon's N-panel header
# carries its version). Read from blender_manifest.toml — single source.
_VERSION_STR = bridge_version()

# Refresh cadence for the status badge / counters (seconds). Light enough to
# feel live, not so fast it spams tag_redraw.
_REFRESH_INTERVAL = 1.5


# ── addon preferences ────────────────────────────────────────────────────

class MCPSocket_AddonPreferences(AddonPreferences):
    bl_idname = "mcp_socket"

    port: IntProperty(
        name="Port",
        description="TCP port for the MCP Socket bridge (matches blender-mcp default)",
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
    undo_checkpoint: BoolProperty(
        name="Undo checkpoint per agent session",
        description=(
            "Before the first code-execution command after a 10 s idle gap, "
            "push an undo checkpoint. The panel's Undo Agent Work button (or "
            "one Ctrl+Z) then rolls back everything the agent changed in that "
            "session. Disable on huge scenes if the checkpoint push is slow."
        ),
        default=True,
    )


# ── helpers ──────────────────────────────────────────────────────────────

def _prefs(context) -> MCPSocket_AddonPreferences:
    addon = context.preferences.addons.get("mcp_socket")
    return addon.preferences if addon else None


def _server() -> object:
    """The live MCPSocketServer instance, or None."""
    return getattr(bpy.types, "mcp_socket_server", None)


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

class MCPSOCKET_OT_start(Operator):
    bl_idname = "mcp_socket.start"
    bl_label = "Start Bridge"
    bl_description = "Start the MCP Socket TCP bridge on the configured port"

    def execute(self, context):
        from . import server as _server_mod
        prefs = _prefs(context)
        port = prefs.port if prefs else _DEFAULT_PORT
        auto_offset = bool(prefs.auto_port_offset) if prefs else True

        current = _server()
        if current is None:
            bpy.types.mcp_socket_server = _server_mod.MCPSocketServer(
                port=port, auto_offset=auto_offset)
        ok = bpy.types.mcp_socket_server.start()
        context.scene.mcp_socket_running = bpy.types.mcp_socket_server.running
        if ok:
            self.report({"INFO"}, f"MCP Socket bridge on :{port}")
        else:
            self.report({"ERROR"}, f"Could not start MCP Socket bridge on :{port} "
                                   "(port busy? remove the old 'Blender MCP' addon)")
        return {"FINISHED"}


class MCPSOCKET_OT_stop(Operator):
    bl_idname = "mcp_socket.stop"
    bl_label = "Stop Bridge"
    bl_description = "Stop the MCP Socket TCP bridge"

    def execute(self, context):
        current = _server()
        if current is not None:
            current.stop()
            del bpy.types.mcp_socket_server
        context.scene.mcp_socket_running = False
        self.report({"INFO"}, "MCP Socket bridge stopped")
        return {"FINISHED"}


class MCPSOCKET_OT_test(Operator):
    bl_idname = "mcp_socket.test"
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
            context.scene.mcp_socket_test_ok = True
            context.scene.mcp_socket_test_msg = f"{obj_count} objects · {mat_count} materials"
        except Exception as exc:  # noqa: BLE001
            self.report({"ERROR"}, f"Test failed: {exc}")
            context.scene.mcp_socket_test_ok = False
            context.scene.mcp_socket_test_msg = f"ERROR: {exc}"
        return {"FINISHED"}


class MCPSOCKET_OT_log_save(Operator):
    bl_idname = "mcp_socket.log_save"
    bl_label = "Save Log"
    bl_description = ("Dump the console ring buffer to a timestamped file in "
                      "%TEMP% (path lands in the clipboard)")

    def execute(self, context):
        import os
        import tempfile
        import time as _time
        from . import logcap
        data = logcap.get_console_log(last_n=logcap.MAX_LINES)
        out_dir = os.path.join(tempfile.gettempdir(), "mcp_socket")
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


class MCPSOCKET_OT_log_clear(Operator):
    bl_idname = "mcp_socket.log_clear"
    bl_label = "Clear"
    bl_description = "Clear the console ring buffer"

    def execute(self, context):
        from . import logcap
        logcap.clear_console_log()
        self.report({"INFO"}, "Console log cleared")
        return {"FINISHED"}


def _preset_items(self, context):
    from . import presets as _presets
    return [(name, name, _presets.PRESETS[name]["note"])
            for name in sorted(_presets.PRESETS)]


class MCPSOCKET_OT_export_fbx(Operator):
    bl_idname = "mcp_socket.export_fbx"
    bl_label = "Export FBX"
    bl_description = ("Export selected objects to FBX with a pipeline preset "
                      "(children included, modifiers baked)")

    filepath: StringProperty(name="File", subtype="FILE_PATH",
                             default="mcp_export.fbx")
    preset: EnumProperty(name="Preset", items=_preset_items, default="maya")

    def draw(self, context):
        self.layout.prop(self, "preset")

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        from . import pipeline
        try:
            rep = pipeline.export_fbx(self.filepath, preset=self.preset,
                                      scope="selected")
        except (ValueError, RuntimeError) as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        context.window_manager.clipboard = rep["filepath"]
        self.report({"INFO"}, f"Exported {len(rep['objects'])} objects "
                              f"(preset {rep['preset']})")
        return {"FINISHED"}


class MCPSOCKET_OT_import_fbx(Operator):
    bl_idname = "mcp_socket.import_fbx"
    bl_label = "Import FBX"
    bl_description = ("Import an FBX per the receiver rule: container EMPTY "
                      "t=0 r=0 s=1, oversized meshes reported")

    filepath: StringProperty(name="File", subtype="FILE_PATH")
    filter_glob: StringProperty(default="*.fbx", options={"HIDDEN"})

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        from . import pipeline
        try:
            rep = pipeline.import_fbx(self.filepath, container=True)
        except (ValueError, RuntimeError) as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        self.report({"INFO"}, f"Imported {rep['objects_count']} objects "
                              f"into '{rep['container']}'")
        return {"FINISHED"}


class MCPSOCKET_OT_render(Operator):
    bl_idname = "mcp_socket.render"
    bl_label = "Render Offscreen"
    bl_description = ("Render to a file without opening the render window. "
                      "Viewport: fast OpenGL render of the current 3D view. "
                      "Camera: scene engine from the scene camera (Cycles "
                      "can be slow)")

    filepath: StringProperty(name="File", subtype="FILE_PATH",
                             default="mcp_render.png")
    mode: EnumProperty(
        name="Mode",
        items=(
            ("viewport", "Viewport", "OpenGL render of the current 3D view (fast)"),
            ("camera", "Camera", "Scene engine render from the scene camera"),
        ),
        default="viewport",
    )

    def draw(self, context):
        self.layout.prop(self, "mode", text="")

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        from . import render
        try:
            rep = render.render_offscreen(self.filepath, mode=self.mode)
        except (ValueError, RuntimeError) as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        context.window_manager.clipboard = rep["filepath"]
        self.report({"INFO"},
                    f"Rendered {rep['resolution']['x']}x{rep['resolution']['y']} "
                    f"({rep['engine']}) in {rep['elapsed_s']} s")
        return {"FINISHED"}


class MCPSOCKET_OT_replay(Operator):
    bl_idname = "mcp_socket.replay"
    bl_label = "Replay Last Session"
    bl_description = ("Re-run the last agent session's mutating commands in "
                      "order (read-only queries are skipped). Replay against "
                      "the CURRENT scene — each step reports its own result")

    def invoke(self, context, event):
        from . import sessions
        path = sessions.last_session_file()
        if not path:
            self.report({"ERROR"}, "No session log recorded yet")
            return {"CANCELLED"}
        return context.window_manager.invoke_confirm(self, event)

    def draw(self, context):
        from . import sessions
        info = sessions.session_info()
        col = self.layout.column()
        col.label(text="Replay %d commands from:" % info.get("replayable", 0))
        col.label(text=info.get("filename", "?"), icon="FILE_TICK")

    def execute(self, context):
        from . import sessions
        path = sessions.last_session_file()
        if not path:
            self.report({"ERROR"}, "No session log recorded yet")
            return {"CANCELLED"}
        try:
            rep = sessions.run_replay(path)
        except (ValueError, RuntimeError) as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        msg = f"Replayed {rep['steps_ok']}/{rep['steps_total']} ok"
        if rep["steps_failed"]:
            self.report({"WARNING"}, msg)
        else:
            self.report({"INFO"}, msg)
        return {"FINISHED"}


class MCPSOCKET_OT_session_path(Operator):
    bl_idname = "mcp_socket.session_path"
    bl_label = "Copy Session Path"
    bl_description = ("Copy the newest session log's path to the clipboard "
                      "(JSONL: every bridge command with params and status)")

    def execute(self, context):
        from . import sessions
        path = sessions.last_session_file()
        if not path:
            self.report({"ERROR"}, "No session log recorded yet")
            return {"CANCELLED"}
        context.window_manager.clipboard = path
        self.report({"INFO"}, f"Copied: {path}")
        return {"FINISHED"}


class MCPSOCKET_OT_undo(Operator):
    bl_idname = "mcp_socket.undo"
    bl_label = "Undo Agent Work"
    bl_description = ("Undo one step. The bridge drops an undo checkpoint "
                      "before each agent session (a code command after a 10 s "
                      "gap), so one click rolls back everything the agent "
                      "changed in that session")

    def execute(self, context):
        try:
            bpy.ops.ed.undo()
            self.report({"INFO"}, "Undone")
        except Exception as exc:  # noqa: BLE001
            self.report({"WARNING"}, f"Nothing to undo: {exc}")
        return {"FINISHED"}


# ── sidebar panel ────────────────────────────────────────────────────────

class MCPSOCKET_PT_panel(Panel):
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "MCP Socket"
    bl_label = f"MCP Socket {_VERSION_STR}"

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
        test_msg = getattr(scene, "mcp_socket_test_msg", "")
        if test_msg:
            row = layout.row(align=True)
            ok = getattr(scene, "mcp_socket_test_ok", False)
            row.alert = not ok
            row.label(text=test_msg,
                      icon="CHECKMARK" if ok else "ERROR")

        # ── Actions ───────────────────────────────────────────────────────
        col = layout.column(align=True)
        col.scale_y = 1.3
        if snap.get("running"):
            col.operator("mcp_socket.stop", icon="PAUSE")
            col.operator("mcp_socket.test", icon="CONSOLE")
        else:
            col.operator("mcp_socket.start", icon="PLAY")
        col.operator("mcp_socket.undo", icon="LOOP_BACK")

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


# ── pipeline sub-panel ───────────────────────────────────────────────────

class MCPSOCKET_PT_pipeline(Panel):
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "MCP Socket"
    bl_parent_id = "MCPSOCKET_PT_panel"
    bl_label = "Pipeline"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout
        col = layout.column(align=True)
        col.scale_y = 1.2
        col.operator("mcp_socket.export_fbx", icon="EXPORT")
        col.operator("mcp_socket.import_fbx", icon="IMPORT")
        row = layout.row()
        row.active = False
        row.label(text="Neutral FBX: meters, Y-up", icon="INFO")


# ── render sub-panel ─────────────────────────────────────────────────────

class MCPSOCKET_PT_render(Panel):
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "MCP Socket"
    bl_parent_id = "MCPSOCKET_PT_panel"
    bl_label = "Render"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout
        col = layout.column(align=True)
        col.scale_y = 1.2
        col.operator("mcp_socket.render", icon="RENDER_STILL")
        row = layout.row()
        row.active = False
        row.label(text="Viewport: OpenGL, no window", icon="INFO")


# ── agent sessions sub-panel ─────────────────────────────────────────────

class MCPSOCKET_PT_sessions(Panel):
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "MCP Socket"
    bl_parent_id = "MCPSOCKET_PT_panel"
    bl_label = "Agent Sessions"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        from . import sessions
        layout = self.layout
        info = sessions.session_info()
        if not info.get("available"):
            row = layout.row()
            row.active = False
            row.label(text="(no sessions recorded)", icon="TIME")
            return
        row = layout.row(align=True)
        row.scale_y = 0.75
        row.active = False
        row.label(text="  Last: %s (%d cmds)" % (info.get("filename", "?")[:28],
                                                 info.get("commands", 0)),
                  icon="TIME")
        col = layout.column(align=True)
        col.scale_y = 1.1
        col.operator("mcp_socket.replay", icon="LOOP_FORWARDS")
        row = layout.row(align=True)
        row.operator("mcp_socket.session_path", text="Copy Log Path", icon="COPYDOWN")


# ── console log sub-panel ────────────────────────────────────────────────

class MCPSOCKET_PT_log(Panel):
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "MCP Socket"
    bl_parent_id = "MCPSOCKET_PT_panel"
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
        row.operator("mcp_socket.log_save", text="Save to File", icon="EXPORT")
        row.operator("mcp_socket.log_clear", text="", icon="X")


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
    MCPSocket_AddonPreferences,
    MCPSOCKET_OT_start,
    MCPSOCKET_OT_stop,
    MCPSOCKET_OT_test,
    MCPSOCKET_OT_log_save,
    MCPSOCKET_OT_log_clear,
    MCPSOCKET_OT_undo,
    MCPSOCKET_OT_export_fbx,
    MCPSOCKET_OT_import_fbx,
    MCPSOCKET_OT_render,
    MCPSOCKET_OT_replay,
    MCPSOCKET_OT_session_path,
    MCPSOCKET_PT_panel,
    MCPSOCKET_PT_pipeline,
    MCPSOCKET_PT_render,
    MCPSOCKET_PT_sessions,
    MCPSOCKET_PT_log,
)


def register() -> None:
    global _refresh_timer
    for cls in classes:
        try:
            bpy.utils.register_class(cls)
        except Exception as exc:  # noqa: BLE001
            print(f"{_TAG} register {cls.__name__}: {exc}")

    bpy.types.Scene.mcp_socket_port = IntProperty(
        name="Port",
        default=_DEFAULT_PORT,
        min=1024,
        max=65535,
    )
    bpy.types.Scene.mcp_socket_running = BoolProperty(name="Running", default=False)
    # Transient state for the Test-connection operator's result display.
    bpy.types.Scene.mcp_socket_test_ok = BoolProperty(default=False)
    bpy.types.Scene.mcp_socket_test_msg = StringProperty(default="")

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

    for attr in ("mcp_socket_port", "mcp_socket_running",
                 "mcp_socket_test_ok", "mcp_socket_test_msg"):
        try:
            delattr(bpy.types.Scene, attr)
        except AttributeError:
            pass
