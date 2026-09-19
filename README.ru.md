# ZCode MCP

**Расширение Blender 4.2+.** Лёгкий локальный TCP-мост между Blender и ZCode
(или любым клиентом, совместимым с `blender-mcp` 1.6.x). Совместим по
протоколу с `blender-mcp.exe` — конфиг MCP на стороне клиента менять не нужно.

*English documentation: [README.md](README.md)*

Автор: **Maksim Kovalev** · Лицензия: GPL-3.0-or-later

> ⚠️ **Безопасность:** аддон поднимает TCP-сервер на `localhost` (по умолчанию
> порт 9876), команда `execute_code` исполняет **произвольный Python-код в
> Blender** без аутентификации. Как и в оригинальном blender-mcp, это норма
> для личной машины — но не открывайте порт наружу и не запускайте на общих
> машинах.

## Возможности

- TCP-сервер на `localhost:9876`
- 9 команд ядра: чтение сцены, произвольный bpy-код, скриншот вьюпорта,
  handshake телеметрии, 4 статус-заглушки интеграций
- Автостарт при включении аддона
- Панель в N-меню: статус (зелёный/красный кружок), Test Connection,
  Last command
- Idle-timeout 30 с против зависших соединений
- Иконки генерируются в памяти (без внешних файлов и Pillow)

## Установка (Blender 4.2+)

1. **Освободите порт 9876** — отключите другие MCP-аддоны, перезапустите Blender.
2. Скачайте `zcode_mcp.zip` со страницы [последнего релиза](https://github.com/abyrvalg379/zcode-mcp/releases/latest).
3. `Edit → Preferences → Get Extensions → ≡ → Install from Disk…` → выберите zip.
4. Включите **ZCode MCP** — сервер стартует сам.
5. Проверка: `/mcp` в ZCode → `connected`, зелёный кружок в панели.

## Настройки (Preferences → Add-ons → ZCode MCP)

- **Port** — порт моста (по умолчанию 9876)
- **Allow richer anonymous telemetry** — по умолчанию выключено

## Как это работает

```
Клиент ZCode MCP (протокол blender-mcp.exe)
        │  JSON на каждый запрос: {"type": "<command>", "params": {...}}
        ▼
TCP-сервер (фоновый поток, localhost:9876)
        │  bpy.app.timers → главный поток (bpy не потокобезопасен)
        ▼
handlers.py → {"status": "success", "result": ...} | {"status": "error", ...}
```
