"""
ИНСТРУМЕНТЫ АГЕНТА СТУДИИ («коннекторы»).

Каждый инструмент: описание для мозга (TOOLS) + исполнитель (run_tool). Инструменты сгруппированы в коннекторы
(Интернет, Файлы, Команды, Управление ПК, Картинки, Дела Мартышки, Память) — их можно выключать в окне.

Разрешения (окно «Мартышка спрашивает»):
  - файлы внутри папки проектов — свободно; вне её — спросить (можно «всё в этой папке»);
  - команды PowerShell — спросить (можно «всё в этой папке» или «всё до конца задачи»);
  - клики, клавиши и печать на экране — спросить (можно «всё в этом приложении»);
  - удалять агент не умеет вообще; опасные команды запрещены всегда.
"""
from __future__ import annotations

import base64
import io
import json
import os
import re
import subprocess
import time
from pathlib import Path

from .. import config

MAX_OUT = 12000
MEMORY_FILE = config.DATA_DIR / "studio" / "память.json"


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

# коннектор → инструменты
GROUPS = {
    "web": ("Интернет", "Поиск и чтение страниц — чтобы не выдумывать факты"),
    "files": ("Файлы", "Читать, создавать и править файлы, искать по ПК"),
    "shell": ("Команды", "PowerShell: запуск программ и скриптов, pip, git"),
    "pc": ("Управление ПК", "Смотреть на экран, кликать, печатать, открывать программы и настройки"),
    "images": ("Картинки", "Генератор картинок на этом ПК"),
    "hands": ("Дела Мартышки", "Громкость, напоминания, погода, окна и другие привычные дела"),
    "memory": ("Память", "Запоминать, где что лежит и что брат любит"),
}

TOOLS = [
    {"name": "plan", "group": "",
     "description": "Показать брату план задачи списком шагов и отмечать прогресс. Вызывай в начале задачи из 3+ "
                    "шагов и обновляй по ходу. status: pending | in_progress | done.",
     "input_schema": _schema({"items": {"type": "array", "items": _schema(
         {"step": S, "status": {"type": "string", "enum": ["pending", "in_progress", "done"]}},
         ["step", "status"])}}, ["items"])},
    {"name": "web_search", "group": "web",
     "description": "Поиск в интернете. Возвращает заголовки, ссылки и выдержки. Используй для фактов, новостей, "
                    "цен, документации библиотек и ошибок программ — НЕ выдумывай.",
     "input_schema": _schema({"query": S}, ["query"])},
    {"name": "web_fetch", "group": "web",
     "description": "Прочитать главный текст страницы по ссылке.",
     "input_schema": _schema({"url": S}, ["url"])},
    {"name": "list_files", "group": "files",
     "description": "Список файлов и папок. Путь без диска — внутри папки проектов.",
     "input_schema": _schema({"path": S}, ["path"])},
    {"name": "find_files", "group": "files",
     "description": "Найти файлы по части имени (Рабочий стол, Документы, Загрузки, Картинки, Видео, Музыка и "
                    "папка проектов). Возвращает полные пути.",
     "input_schema": _schema({"name": S}, ["name"])},
    {"name": "read_file", "group": "files",
     "description": "Прочитать текстовый файл (с номерами строк). Для больших — offset и limit (строки).",
     "input_schema": _schema({"path": S, "offset": I, "limit": I}, ["path"])},
    {"name": "write_file", "group": "files",
     "description": "Создать или перезаписать текстовый файл целиком. Путь без диска — внутри папки проектов.",
     "input_schema": _schema({"path": S, "content": S}, ["path", "content"])},
    {"name": "edit_file", "group": "files",
     "description": "Заменить в файле кусок old на new. old должен встречаться ровно один раз.",
     "input_schema": _schema({"path": S, "old": S, "new": S}, ["path", "old", "new"])},
    {"name": "run_command", "group": "shell",
     "description": "Выполнить команду PowerShell. cwd — папка (по умолчанию папка проектов). Возвращает вывод.",
     "input_schema": _schema({"command": S, "cwd": S, "timeout_sec": I}, ["command"])},
    {"name": "open_app", "group": "pc",
     "description": "Открыть программу, сайт, игру или папку по названию («стим», «блокнот», «youtube.com»).",
     "input_schema": _schema({"name": S}, ["name"])},
    {"name": "open_settings", "group": "pc",
     "description": "Сразу открыть нужное место: настройки Windows (звук, экран, блютуз, Wi-Fi, мышь…), диспетчер "
                    "устройств, службы, настройки игры в Steam и т.п. Пиши своими словами.",
     "input_schema": _schema({"where": S}, ["where"])},
    {"name": "windows", "group": "pc",
     "description": "Какие окна сейчас открыты и какое активное.",
     "input_schema": _schema({}, [])},
    {"name": "screen_look", "group": "pc",
     "description": "Посмотреть на экран и ответить на вопрос (что открыто, что за ошибка, какой текст). "
                    "Используй, чтобы проверить результат действий на экране.",
     "input_schema": _schema({"question": S}, ["question"])},
    {"name": "click", "group": "pc",
     "description": "Кликнуть по кнопке/пункту на экране по названию («Сохранить», «правой кнопкой по Корзина»).",
     "input_schema": _schema({"target": S}, ["target"])},
    {"name": "press_keys", "group": "pc",
     "description": "Нажать сочетание клавиш: ctrl+s, alt+tab, enter, win+d…",
     "input_schema": _schema({"keys": S}, ["keys"])},
    {"name": "type_text", "group": "pc",
     "description": "Напечатать текст туда, где сейчас курсор (любой язык).",
     "input_schema": _schema({"text": S}, ["text"])},
    {"name": "draw_image", "group": "images",
     "description": "Нарисовать картинку генератором на этом ПК. Опиши подробно: сюжет, стиль, свет, ракурс, "
                    "формат. Возвращает путь к PNG.",
     "input_schema": _schema({"description": S}, ["description"])},
    {"name": "open_path", "group": "files",
     "description": "Открыть брату файл, папку или ссылку программой по умолчанию.",
     "input_schema": _schema({"path": S}, ["path"])},
    {"name": "martyshka_action", "group": "hands",
     "description": "Обычное дело Мартышки. name — одно из: громкость, музыка, система, напомнить, заметка, "
                    "заметки, окно, погода, заряд, буфер, скриншот, фото, видео, таблица, документ. "
                    "args — параметры по порядку (например name=громкость, args=[\"50\"]).",
     "input_schema": _schema({"name": S, "args": {"type": "array", "items": S}}, ["name"])},
    {"name": "remember", "group": "memory",
     "description": "Запомнить надолго полезный факт: где лежит проект, как брат любит, какие программы у него "
                    "есть. Коротко, одной фразой.",
     "input_schema": _schema({"fact": S}, ["fact"])},
]
TOOL_GROUP = {t["name"]: t["group"] for t in TOOLS}
CHAT_TOOL_NAMES = ("web_search", "web_fetch", "open_app", "open_settings", "draw_image", "open_path",
                   "martyshka_action", "remember", "windows")


def active_tools(names: tuple[str, ...] | None = None) -> list[dict]:
    """Инструменты для мозга: без выключенных коннекторов и без служебного поля group."""
    off = set(getattr(config, "STUDIO_DISABLED_GROUPS", []) or [])
    if not config.WEB_ENABLED:
        off.add("web")
    out = []
    for t in TOOLS:
        if t["group"] in off or (names and t["name"] not in names):
            continue
        out.append({k: v for k, v in t.items() if k != "group"})
    return out


# Что агент делает — для живой строки состояния в окне
ACTIVITY = {
    "plan": ("plan", "Составляю план"), "web_search": ("search", "Ищу в интернете"),
    "web_fetch": ("read", "Читаю страницу"), "list_files": ("read", "Смотрю папку"),
    "find_files": ("search", "Ищу файлы"), "read_file": ("read", "Читаю файл"),
    "write_file": ("write", "Пишу файл"), "edit_file": ("write", "Правлю файл"),
    "run_command": ("exec", "Исполняю команду"), "open_app": ("exec", "Открываю"),
    "open_settings": ("exec", "Открываю настройки"), "windows": ("look", "Смотрю окна"),
    "screen_look": ("look", "Смотрю на экран"), "click": ("exec", "Кликаю"), "press_keys": ("exec", "Нажимаю"),
    "type_text": ("write", "Печатаю"), "draw_image": ("draw", "Рисую"), "open_path": ("exec", "Открываю"),
    "martyshka_action": ("exec", "Делаю"), "remember": ("memory", "Запоминаю"),
}


def label(name: str, args: dict) -> str:
    detail = (args.get("query") or args.get("url") or args.get("path") or args.get("command") or args.get("name")
              or args.get("where") or args.get("question") or args.get("target") or args.get("keys")
              or args.get("text") or args.get("description") or args.get("fact") or "")
    detail = str(detail).replace("\n", " ")
    if len(detail) > 90:
        detail = detail[:87] + "…"
    verb = ACTIVITY.get(name, ("exec", name))[1]
    return f"{verb}: {detail}" if detail else verb


def validate(name: str, args: dict) -> str | None:
    tool = next((t for t in TOOLS if t["name"] == name), None)
    if tool is None:
        return f"Нет такого инструмента: {name}. Доступны: {', '.join(t['name'] for t in TOOLS)}."
    for key in tool["input_schema"]["required"]:
        if key not in args:
            return f"Не хватает параметра «{key}»."
    return None


# ---------- разрешения ----------
def _foreground_app() -> str:
    try:
        from .. import hands
        w = next((w for w in hands.list_windows() if w["active"]), None)
        return (w["exe"] or w["title"] or "окно") if w else "рабочий стол"
    except Exception:
        return "окно"


def permission(name: str, args: dict) -> dict | None:
    """Нужно ли спросить брата. Возвращает {question, scope, scope_label} или None."""
    if name in ("write_file", "edit_file"):
        p = _path(args.get("path", ""))
        if not _inside_ws(p):
            return {"question": f"Изменить файл вне папки проектов:\n{p}", "scope": f"dir:{p.parent}",
                    "scope_label": f"всё в папке «{p.parent.name or p.parent}»"}
    if name == "run_command":
        cwd = _path(args.get("cwd") or str(workspace()))
        safe = config.STUDIO_AUTO_COMMANDS and _inside_ws(cwd) and not re.search(
            r"remove-item|\brm\b|\bdel\b|rmdir|\brd\b|uninstall|stop-process|taskkill|format", args["command"], re.I)
        if not safe:
            return {"question": f"Выполнить команду:\n{args['command']}\n\nв папке: {cwd}", "scope": f"cmd:{cwd}",
                    "scope_label": f"все команды в папке «{cwd.name}»"}
    if name in ("click", "press_keys", "type_text"):
        app = _foreground_app()
        what = {"click": f"Кликнуть: {args.get('target')}", "press_keys": f"Нажать клавиши: {args.get('keys')}",
                "type_text": f"Напечатать: {str(args.get('text'))[:300]}"}[name]
        return {"question": f"{what}\n\nв приложении: {app}", "scope": f"app:{app.lower()}",
                "scope_label": f"всё в приложении «{app}»"}
    return None


# ---------- память ----------
def memory_facts() -> list[str]:
    try:
        return json.loads(MEMORY_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []


def _remember(fact: str) -> str:
    facts = [f for f in memory_facts() if f.strip().lower() != fact.strip().lower()]
    facts.append(fact.strip())
    MEMORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    MEMORY_FILE.write_text(json.dumps(facts[-80:], ensure_ascii=False, indent=1), encoding="utf-8")
    return "Запомнила."


# Мозг иногда пишет место по-английски — переводим для быстрых путей
EN_PLACES = {"settings": "настройки", "sound": "звука", "audio": "звука", "display": "экрана", "screen": "экрана",
             "bluetooth": "блютуз", "wi-?fi": "wifi", "network": "сети", "mouse": "мыши", "keyboard": "клавиатуры",
             "startup": "автозагрузки", "graphics": "графики", "device manager": "диспетчер устройств",
             "services": "службы", "control panel": "панель управления", "downloads": "загрузки",
             "windows": "", "update": "обновления", "power": "питания", "apps": "приложения"}


def _thumb(path: Path) -> str:
    try:
        from PIL import Image
        im = Image.open(path).convert("RGB")
        im.thumbnail((900, 900))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=88)
        return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
    except Exception:
        return ""


# ---------- выполнение ----------
def run_tool(name: str, args: dict, ctx: dict) -> dict:
    """Выполнить инструмент. ctx: {"vision_model", "vision_ctx", "ask_action"}.
    Возвращает {"text": для мозга, "ok": bool, "image": data-uri, "file": путь}."""
    try:
        return _run(name, args, ctx)
    except Exception as e:
        return {"text": f"Ошибка: {type(e).__name__}: {e}", "ok": False}


def _res(r) -> dict:
    return {"text": r.text, "ok": r.ok}


def _run(name: str, args: dict, ctx: dict) -> dict:
    from .. import agent, hands, web

    if name == "plan":
        return {"text": "План показан брату.", "ok": True}

    if name == "web_search":
        found = web.search(args["query"], 8)
        if not found:
            return {"text": "Ничего не нашлось (или поиск недоступен). Попробуй другой запрос.", "ok": False}
        return {"text": "\n\n".join(f"{r.get('title', '')}\n{r.get('href', '')}\n{r.get('body', '')}"
                                    for r in found), "ok": True}

    if name == "web_fetch":
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

    if name == "find_files":
        needle = args["name"].lower().strip()
        home = Path.home()
        roots = [workspace()] + [home / d for d in ("Desktop", "Documents", "Downloads", "Pictures", "Videos", "Music",
                                                     "OneDrive/Desktop", "OneDrive/Documents")]
        found, t0 = [], time.time()
        for root in roots:
            if not root.exists():
                continue
            for dirpath, dirs, files in os.walk(root):
                dirs[:] = [d for d in dirs if not d.startswith((".", "node_modules", "__pycache__", "venv", ".venv"))]
                for f in files + dirs:
                    if needle in f.lower():
                        found.append(str(Path(dirpath) / f))
                if len(found) >= 40 or time.time() - t0 > 8:
                    break
        return {"text": "\n".join(dict.fromkeys(found)) or "Ничего не нашла.", "ok": bool(found)}

    if name == "read_file":
        p = _path(args["path"])
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        off = max(int(args.get("offset") or 1), 1)
        lim = int(args.get("limit") or 400)
        part = lines[off - 1: off - 1 + lim]
        body = "\n".join(f"{i:>5}  {line}" for i, line in enumerate(part, off))
        more = f"\n… всего строк: {len(lines)}" if off - 1 + lim < len(lines) else ""
        return {"text": (body[:MAX_OUT * 2] or "(пустой файл)") + more, "ok": True}

    if name == "write_file":
        p = _path(args["path"])
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(args["content"], encoding="utf-8")
        return {"text": f"Записано: {p} ({len(args['content'])} символов).", "ok": True, "file": str(p)}

    if name == "edit_file":
        p = _path(args["path"])
        text = p.read_text(encoding="utf-8")
        n = text.count(args["old"])
        if n != 1:
            return {"text": f"Кусок old найден {n} раз(а), а нужно ровно 1. Прочитай файл и возьми больше "
                            "соседнего текста.", "ok": False}
        p.write_text(text.replace(args["old"], args["new"]), encoding="utf-8")
        return {"text": f"Исправлено: {p}", "ok": True, "file": str(p)}

    if name == "run_command":
        cmd = args["command"]
        bad = agent.check_command(cmd)
        if bad:
            return {"text": bad, "ok": False}
        cwd = _path(args.get("cwd") or str(workspace()))
        timeout = min(max(int(args.get("timeout_sec") or 120), 5), 900)
        try:
            r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command",
                                "[Console]::OutputEncoding=[Text.Encoding]::UTF8; $ProgressPreference='SilentlyContinue'; "
                                + cmd], cwd=str(cwd), capture_output=True, timeout=timeout, creationflags=0x08000000)
        except subprocess.TimeoutExpired:
            return {"text": f"Команда не закончилась за {timeout} с. Если это программа с окном — запусти её "
                            "через Start-Process.", "ok": False}
        out = (r.stdout or b"").decode("utf-8", "replace")
        err = (r.stderr or b"").decode("utf-8", "replace")
        text = (out[-MAX_OUT:] + ("\nПОТОК ОШИБОК:\n" + err[-4000:] if err.strip() else "")).strip()
        return {"text": f"Код выхода {r.returncode}\n{text or '(без вывода)'}", "ok": r.returncode == 0}

    if name == "open_app":
        return _res(hands.open_thing(args["name"]))

    if name == "open_settings":
        from .. import goto
        w = args["where"].strip()
        for en, ru in EN_PLACES.items():
            w = re.sub(rf"\b{en}\b", ru, w, flags=re.I)
        said = None
        for q in (w, "зайди в " + w, "настройки " + w, "зайди в настройки " + w):
            try:
                said = goto.goto(q)
            except Exception:
                said = None
            if said:
                break
        if said:
            return {"text": said, "ok": True}
        return {"text": "Быстрого пути туда нет. Открой программу через open_app и дойди кликами, проверяя "
                        "экран через screen_look.", "ok": False}

    if name == "windows":
        return _res(hands.windows_info())

    if name in ("screen_look", "click"):
        from .. import vision
        vision.MODEL_OVERRIDE, vision.CTX_OVERRIDE = ctx.get("vision_model", ""), ctx.get("vision_ctx", 0)
        try:
            if name == "screen_look":
                return {"text": "На экране: " + vision.look(args["question"]), "ok": True}
            text = vision.click(args["target"])
            return {"text": text, "ok": not re.search(r"не (наш|наход|вид|смог|получ)", text, re.I)}
        finally:
            vision.MODEL_OVERRIDE, vision.CTX_OVERRIDE = "", 0

    if name == "press_keys":
        return _res(hands.press_keys(args["keys"]))

    if name == "type_text":
        return _res(hands.type_text(args["text"]))

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
            if not ctx["ask_action"](res.confirm):
                return {"text": "Брат не разрешил.", "ok": False}
            res = hands.run(args["name"], list(args.get("args") or []), confirmed=True)
        return _res(res)

    if name == "remember":
        return {"text": _remember(args["fact"]), "ok": True}

    return {"text": f"Нет такого инструмента: {name}", "ok": False}
