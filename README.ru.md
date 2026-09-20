# ZCode MCP

![ZCode MCP](cover.png)

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
- **Расширенные команды моста** (v1.3.0) — доступны любому клиенту через его
  code-execution лаз:
  ```python
  from bl_ext.user_default.zcode_mcp import handlers
  handlers.HANDLERS["get_console_log"](last_n=100)
  ```
  - `get_console_log` / `clear_console_log` — кольцевой буфер (500 строк)
    Python-вывода консоли: принты аддонов, traceback'и, logging.
    C-уровень (register-варнинги, отчёты операторов) Python'у не виден.
  - `get_bridge_info` — визитка инстанса одним вызовом: версии моста/Blender,
    pid, сцена, файл, счётчики, фактический порт
  - `get_hierarchy` — дерево коллекций с объектами и флагами видимости
  - `get_object_data` — полная read-only карточка объекта (модификаторы,
    констрейнты, слоты материалов, статистика меша, кастом-пропы, экшен)
  - `get_material_info` — сводка нод, image-текстуры с колорспейсом и
    состоянием файла на диске
  - `get_images_report` — все изображения: пути, колорспейс, packed,
    пропавшие файлы
  - `list_instances` — живые инстансы моста на этой машине (мульти-инстанс)
- **Мульти-инстанс:** если основной порт занят, мост занимает следующий
  (9877, 9878, …) и регистрируется в
  `%TEMP%/zcode_mcp_instances/pid_<pid>.json` (хартбит ~10 с) — второй Blender
  живёт со своим мостом параллельно с первым
- Автостарт при включении аддона
- Панель в N-меню: статус (зелёный/красный кружок), Test Connection,
  Last command, фактический порт
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
- **Auto port offset for second instance** — если порт занят, пробовать
  следующие 10 портов вместо отказа (включено по умолчанию)
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
