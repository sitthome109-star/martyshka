"""
ИНСТРУМЕНТЫ АГЕНТА СТУДИИ.

Каждый инструмент: описание для мозга (TOOLS) + функция-исполнитель (run_tool).
Безопасность:
  - файлы внутри папки проектов агент пишет свободно; вне её — только после «да» брата;
  - команды PowerShell — после «да» (или сами, если брат включил это в настройках и команда в папке проектов);
  - удаление — только в Корзину и только с подтверждением;
  - опасные команды (форматирование, отключение защиты…) запрещены всегда.
"""
from __future__ import annotations

import base64
import io
import os
import re
import subprocess
from pathlib import Path

from .. import config

MAX_OUT = 12000


def workspace() -> Path:
    ws = Path(config.STUDIO_WORKSPACE)
    ws.mkdir(parents=True, exist_ok=True)
    return ws


def _path(p: str) -> Path:
    p = os.path.expandvars(os.path.expanduser((p or "").strip().strip('"')))
    path = Path(p)
    if not path.is_absolute():
        path = workspace() / path
    return path.resolve()


def _inside_ws(path: Path) -> bool:
    try:
        path.resolve().relative_to(workspace().resolve())
        return True
    except ValueError:
        return False


def _schema(props: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": props, "required": required, "additionalProperties": False}


S = {"type": "string"}
I = {"type": "integer"}

TOOLS = [
    {"name": "plan",
     "description": "Показать брату план задачи в виде списка шагов и отмечать прогресс. Вызывай в начале задачи "
                    "из 3+ шагов и обновляй, когда шаг сделан. status: pending | in_progress | done.",
     "input_schema": _schema({"items": {"type": "array", "items": _schema(
         {"step": S, "status": {"type": "string", "enum": ["pending", "in_progress", "done"]}},
         ["step", "status"])}}, ["items"])},
    {"name": "web_search",
     "description": "Поиск в интернете (DuckDuckGo). Возвращает заголовки, ссылки и выдержки. Используй для любых "
                    "фактов, свежих новостей, документации библиотек — НЕ выдумывай.",
     "input_schema": _schema({"query": S}, ["query"])},
    {"name": "web_fetch",
     "description": "Прочитать текст страницы по ссылке (главный текст, без меню и рекламы).",
     "input_schema": _schema({"url": S}, ["url"])},
    {"name": "list_files",
     "description": "Список файлов и папок. Путь без диска — внутри папки проектов.",
     "input_schema": _schema({"path": S}, ["path"])},
    {"name": "read_file",
     "description": "Прочитать текстовый файл (с номерами строк). Для больших файлов укажи offset и limit (строки).",
     "input_schema": _schema({"path": S, "offset": I, "limit": I}, ["path"])},
    {"name": "write_file",
     "description": "Создать или полностью перезаписать текстовый файл. Путь без диска — внутри папки проектов. "
                    "Папки создаются сами.",
     "input_schema": _schema({"path": S, "content": S}, ["path", "content"])},
    {"name": "edit_file",
     "description": "Заменить в файле кусок текста old на new. old должен встречаться в файле ровно один раз "
                    "(бери с запасом соседних строк).",
     "input_schema": _schema({"path": S, "old": S, "new": S}, ["path", "old", "new"])},
    {"name": "run_command",
     "description": "Выполнить команду PowerShell (запуск python-скриптов, pip, git, проверка программ). "
                    "cwd — папка, где выполнять (по умолчанию папка проектов). Возвращает вывод.",
     "input_schema": _schema({"command": S, "cwd": S, "timeout_sec": I}, ["command"])},
    {"name": "draw_image",
     "description": "Нарисовать картинку генератором на этом ПК (RealVisXL). Опиши подробно: сюжет, стиль, свет, "
                    "ракурс, формат (вертикальная/горизонтальная). Возвращает путь к PNG.",
     "input_schema": _schema({"description": S}, ["description"])},
    {"name": "open_path",
     "description": "Открыть файл, папку или ссылку у брата на экране (программой по умолчанию).",
     "input_schema": _schema({"path": S}, ["path"])},
    {"name": "martyshka_action",
     "description": "Сделать обычное дело Мартышки: открыть программу, громкость, окна, напоминание, погода и т.п. "
                    "name — название дела, args — его параметры.",
     "input_schema": _schema({"name": S, "args": {"type": "array", "items": S}}, ["name"])},
]
TOOL_NAMES = {t["name"] for t in TOOLS}
CHAT_TOOLS = [t for t in TOOLS if t["name"] in ("web_search", "web_fetch", "draw_image", "open_path",
                                                "martyshka_action")]

# Короткие подписи для окна: что агент делает прямо сейчас
LABELS = {
    "plan": "План", "web_search": "Ищу в интернете", "web_fetch": "Читаю страницу", "list_files": "Смотрю папку",
    "read_file": "Читаю файл", "write_file": "Пишу файл", "edit_file": "Правлю файл",
    "run_command": "Выполняю команду", "draw_image": "Рисую", "open_path": "Открываю",
    "martyshka_action": "Делаю",
}


def label(name: str, args: dict) -> str:
    detail = (args.get("query") or args.get("url") or args.get("path") or args.get("command")
              or args.get("description") or args.get("name") or "")
    detail = str(detail).replace("\n", " ")
    if len(detail) > 90:
        detail = detail[:87] + "…"
    return f"{LABELS.get(name, name)}: {detail}" if detail else LABELS.get(name, name)


def validate(name: str, args: dict) -> str | None:
    """Проверить, что мозг прислал все нужные поля (поток мог оборваться)."""
    tool = next((t for t in TOOLS if t["name"] == name), None)
    if tool is None:
        return f"Нет такого инструмента: {name}"
    for key in tool["input_schema"]["required"]:
        if key not in args:
            return f"Не хватает параметра «{key}»."
    return None


def needs_confirm(name: str, args: dict) -> str | None:
    """Вопрос брату перед действием или None, если можно сразу."""
    if name == "write_file" or name == "edit_file":
        p = _path(args.get("path", ""))
        if not _inside_ws(p):
            return f"Изменить файл вне папки проектов?\n{p}"
    if name == "run_command":
        cwd = _path(args.get("cwd") or str(workspace()))
        if config.STUDIO_AUTO_COMMANDS and _inside_ws(cwd) and not re.search(
                r"remove-item|\brm\b|\bdel\b|rmdir|\brd\b|uninstall|stop-process|taskkill", args["command"], re.I):
            return None
        return f"Выполнить команду?\n{args['command']}\n(папка: {cwd})"
    return None


def _thumb(path: Path) -> str:
    """Уменьшенная картинка для окна (data URI)."""
    try:
        from PIL import Image
        im = Image.open(path).convert("RGB")
        im.thumbnail((900, 900))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=88)
        return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
    except Exception:
        return ""


def run_tool(name: str, args: dict, confirmed: bool, ask) -> dict:
    """Выполнить инструмент. Возвращает {"text": для мозга, "ok": bool, "image": data-uri, "file": путь}."""
    try:
        return _run(name, args, confirmed, ask)
    except Exception as e:
        return {"text": f"Ошибка: {type(e).__name__}: {e}", "ok": False}


def _run(name: str, args: dict, confirmed: bool, ask) -> dict:
    from .. import agent, hands, web

    if name == "plan":
        return {"text": "План показан брату.", "ok": True}

    if name == "web_search":
        if not config.WEB_ENABLED:
            return {"text": "Интернет выключен в настройках.", "ok": False}
        found = web.search(args["query"], 8)
        if not found:
            return {"text": "Ничего не нашлось (или поиск недоступен). Попробуй другой запрос.", "ok": False}
        return {"text": "\n\n".join(f"{r.get('title', '')}\n{r.get('href', '')}\n{r.get('body', '')}"
                                    for r in found), "ok": True}

    if name == "web_fetch":
        if not config.WEB_ENABLED:
            return {"text": "Интернет выключен в настройках.", "ok": False}
        text = web.fetch_text(args["url"], MAX_OUT)
        return {"text": text or "Страница пустая или не открылась.", "ok": bool(text)}

    if name == "list_files":
        p = _path(args["path"] or ".")
        if not p.exists():
            return {"text": f"Нет такой папки: {p}", "ok": False}
        items = sorted(p.iterdir(), key=lambda x: (x.is_file(), x.name.lower()))[:300]
        lines = [f"{'📄' if x.is_file() else '📁'} {x.name}" + (f"  ({x.stat().st_size:,} байт)" if x.is_file() else "")
                 for x in items]
        return {"text": f"{p}\n" + ("\n".join(lines) or "(пусто)"), "ok": True}

    if name == "read_file":
        p = _path(args["path"])
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        off = max(int(args.get("offset") or 1), 1)
        lim = int(args.get("limit") or 400)
        part = lines[off - 1: off - 1 + lim]
        body = "\n".join(f"{i:>5}  {line}" for i, line in enumerate(part, off))
        more = f"\n… всего строк: {len(lines)}" if off - 1 + lim < len(lines) else ""
        return {"text": (body[:MAX_OUT * 2] or "(пустой файл)") + more, "ok": True}

    if name in ("write_file", "edit_file"):
        p = _path(args["path"])
        q = needs_confirm(name, args)
        if q and not confirmed and not ask(q):
            return {"text": "Брат не разрешил.", "ok": False}
        if name == "write_file":
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(args["content"], encoding="utf-8")
            return {"text": f"Записано: {p} ({len(args['content'])} символов).", "ok": True, "file": str(p)}
        text = p.read_text(encoding="utf-8")
        n = text.count(args["old"])
        if n != 1:
            return {"text": f"Кусок old найден {n} раз(а), а нужно ровно 1. Возьми больше соседнего текста.",
                    "ok": False}
        p.write_text(text.replace(args["old"], args["new"]), encoding="utf-8")
        return {"text": f"Исправлено: {p}", "ok": True, "file": str(p)}

    if name == "run_command":
        cmd = args["command"]
        bad = agent.check_command(cmd)
        if bad:
            return {"text": bad, "ok": False}
        cwd = _path(args.get("cwd") or str(workspace()))
        q = needs_confirm(name, args)
        if q and not confirmed and not ask(q):
            return {"text": "Брат не разрешил выполнять команду.", "ok": False}
        timeout = min(max(int(args.get("timeout_sec") or 120), 5), 900)
        try:
            r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command",
                                "[Console]::OutputEncoding=[Text.Encoding]::UTF8; " + cmd],
                               cwd=str(cwd), capture_output=True, timeout=timeout, creationflags=0x08000000)
        except subprocess.TimeoutExpired:
            return {"text": f"Команда не закончилась за {timeout} с.", "ok": False}
        out = (r.stdout or b"").decode("utf-8", "replace")
        err = (r.stderr or b"").decode("utf-8", "replace")
        text = (out[-MAX_OUT:] + ("\nОШИБКИ:\n" + err[-4000:] if err.strip() else "")).strip()
        return {"text": f"Код выхода {r.returncode}\n{text or '(без вывода)'}", "ok": r.returncode == 0}

    if name == "draw_image":
        from .. import media
        res = media.draw(args["description"])
        path = media.last_file if res.ok else None
        if not res.ok or path is None:
            return {"text": res.text, "ok": False}
        return {"text": f"Готово: {path}", "ok": True, "image": _thumb(path), "file": str(path)}

    if name == "open_path":
        target = args["path"].strip()
        if re.match(r"https?://", target):
            os.startfile(target)
        else:
            p = _path(target)
            if not p.exists():
                return {"text": f"Нет такого файла: {p}", "ok": False}
            os.startfile(str(p))
        return {"text": "Открыл.", "ok": True}

    if name == "martyshka_action":
        res = hands.run(args["name"], list(args.get("args") or []))
        if res.confirm:
            if not ask(res.confirm):
                return {"text": "Брат не разрешил.", "ok": False}
            res = hands.run(args["name"], list(args.get("args") or []), confirmed=True)
        return {"text": res.text, "ok": res.ok}

    return {"text": f"Нет такого инструмента: {name}", "ok": False}
