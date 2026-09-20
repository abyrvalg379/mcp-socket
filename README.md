# ZCode MCP

![ZCode MCP](cover.png)

**Blender 4.2+ extension.** A lightweight local TCP bridge between Blender and
ZCode (or any `blender-mcp` 1.6.x-compatible client). Wire-compatible with the
`blender-mcp.exe` protocol — no MCP config changes needed on the client side.

*Документация на русском: [README.ru.md](README.ru.md)*

Author: **Maksim Kovalev** · License: GPL-3.0-or-later

> ⚠️ **Security note:** this add-on hosts a TCP server on `localhost` (port
> 9876 by default) whose `execute_code` command runs **arbitrary Python code
> in Blender** with no authentication. This matches the upstream blender-mcp
> design and is fine on a personal machine, but do not expose the port beyond
> `localhost` and don't run it on shared/untrusted hosts.

## Features

- TCP server on `localhost:9876` (blender-mcp default port)
- 9 core commands:
  - `get_telemetry_consent` — handshake (blender-mcp blocks without it)
  - `get_scene_info` / `get_object_info` — read the scene
  - `execute_code` — run arbitrary `bpy` code
  - `get_viewport_screenshot` — capture the 3D viewport
  - `get_polyhaven_status` / `get_hyper3d_status` / `get_sketchfab_status` /
    `get_hunyuan3d_status` — stubs answering `enabled: false`
    (blender-mcp drops the connection without them)
- Auto-starts when the add-on is enabled
- N-panel in the 3D viewport: status badge (green/red dot), Test Connection,
  Last command
- 30 s idle-timeout against zombie connections
- Status icons are generated as PNGs in memory — no external files, no Pillow

## Installation (Blender 4.2+)

1. **Free the port.** Disable any other add-on that owns port 9876
   (e.g. the Blender Lab "MCP" extension or an old "Blender MCP" add-on),
   then restart Blender.
2. Download `zcode_mcp.zip` from the [latest release](https://github.com/abyrvalg379/zcode-mcp/releases/latest).
3. `Edit → Preferences → Get Extensions → ≡ (top right) → Install from Disk…`
   → pick the zip.
4. Enable **ZCode MCP**. The bridge starts automatically.
5. Verify: `/mcp` in ZCode shows Blender as `connected` (green dot in panel).

## Preferences

- **Port** — TCP port of the bridge (default 9876)
- **Allow richer anonymous telemetry** — off by default; if on, the bridge
  answers `consent=true` to blender-mcp's telemetry check

## How it works

```
ZCode MCP client (blender-mcp.exe protocol)
        │  JSON per request: {"type": "<command>", "params": {...}}
        ▼
TCP server (background thread, localhost:9876)
        │  bpy.app.timers → main thread (bpy is not thread-safe)
        ▼
handlers.py → {"status": "success", "result": ...} | {"status": "error", ...}
```

## Structure

```
zcode_mcp/
├── blender_manifest.toml   extension metadata (Blender 4.2+)
├── __init__.py             register/unregister + auto-start + hot-reload guard
├── server.py               TCP 9876, threads, main-thread dispatch
├── handlers.py             9 core commands
├── ui.py                   N-panel + preferences + operators + refresh timer
├── icons.py                in-memory PNG status icons (green/red dot)
└── reload_addon.py         hot reload snippet
```

## Changelog

- **1.2.0** — extension-only build (manifest is the single source of metadata,
  legacy `bl_info` removed)
- **1.1.0** — status icons, integration status stubs (4 integrations),
  idle-timeout 30 s, Test Connection, Last command, refresh timer
- **1.0.0** — initial release: 5 core commands, TCP server, Start/Stop panel
