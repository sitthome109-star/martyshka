"""
АГЕНТ СТУДИИ: один чат = одна «сессия».

Как работает задача:
  1. Решаем, что это: болтовня (лёгкий мозг), простое дело (gemma4) или программирование (супер-мозг).
  2. Мозгу даём контекст: где папка проектов, какие проекты есть, что открыто на экране, что он помнит о брате,
     над каким проектом шла работа в этом чате. Так он не путается, что и где лежит.
  3. Мозг думает и вызывает инструменты; опасное — только после разрешения брата (окно с причиной и выбором
     «один раз / всё в этой папке или приложении / всё до конца задачи»).
  4. Результаты возвращаются мозгу, он проверяет и делает следующий шаг — пока задача не готова.
Окну по ходу идут события: что делает сейчас (думает / читает / исполняет…), мысли, текст, шаги, план.
"""
from __future__ import annotations

import json
import re
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

from .. import config
from . import brains, tools

CHATS_DIR = config.DATA_DIR / "studio" / "chats"
TRASH_DIR = config.DATA_DIR / "studio" / "удалённые"

TASK_RE = re.compile(
    r"(напиш|сдела|созда|найди|поищ|загугл|узна[йть]|погод|курс|новост|цен[аы]|сколько стоит|последн|свеж|"
    r"сегодня|202\d|нарису|картинк|фото|изображ|видео|открой|запусти|установи|исправ|почин|провер|посчита|"
    r"переведи|файл|папк|документ|таблиц|excel|настройк|громкост|включи|выключи|закрой|экран|окн[оа]|"
    r"напомни|запомни|передела|измени|поменя|добав|убер|улучш|настро|говорилк|зайди|выйди|"
    r"search|find|open|weather|news|draw|install|such|finde|öffne|wetter)", re.IGNORECASE)
CODE_RE = re.compile(
    r"(напрограмм|запрограмм|программ|код|скрипт|сайт|прилож|игр[ауыо]|\bбот|парсер|функци|алгоритм|"
    r"python|питон|html|css|javascript|\bjs\b|c\+\+|c#|java\b|exe\b|баг|отлад|рефактор|"
    r"\b(code|program|script|website|app|game|bug|debug)\b|programmier|иконк|"
    r"(передела|измени|поменя|добав|убер|улучш|доработ|сдела|настро)\w*.{0,50}"
    r"(приложени|студи|окн|кружок|кружк|шарик|дизайн|интерфейс|кнопк|вкладк|себя|тебя)|"
    r"(напиш|сдела|созда|исправ|почин|доработ|улучш)\w*\s+(\S+\s+){0,3}(програм|код|скрипт|сайт|игр|бот|прилож|утилит))",
    re.IGNORECASE)


def classify(text: str) -> str:
    """chat — болтовня (лёгкий мозг), task — простое дело с инструментами, code — программирование (супер-мозг)."""
    if CODE_RE.search(text):
        return "code"
    return "task" if TASK_RE.search(text) else "chat"


def _manner() -> str:
    if config.PERSONALITY_MODE == "jarvis":
        return f"Манера: спокойный вежливый ИИ-дворецкий, на «вы», обращение «{config.JARVIS_ADDRESS}», без мата."
    return (f"Манера: свой в доску, на «ты», зовёшь его «{config.OWNER_NICKNAME}» или «брат», с юмором, "
            "мат только к месту и редко.")


def _environment(project: str) -> str:
    """Что сейчас вокруг: проекты, окна, память. Даётся мозгу, чтобы он понимал, где что лежит."""
    ws = tools.workspace()
    parts = [f"Папка проектов: {ws}"]
    try:
        projects = sorted((p for p in ws.iterdir() if p.is_dir()), key=lambda p: p.stat().st_mtime, reverse=True)
        if projects:
            parts.append("Проекты в ней (свежие первыми): " + ", ".join(
                f"{p.name} ({datetime.fromtimestamp(p.stat().st_mtime):%d.%m})" for p in projects[:15]))
    except Exception:
        pass
    if project:
        parts.append(f"В ЭТОМ чате ты работаешь над проектом: {project} — если брат говорит «доработай», «исправь», "
                     "«добавь», речь о нём.")
    try:
        from .. import hands
        wins = [w for w in hands.list_windows() if not w["minimized"]][:8]
        if wins:
            parts.append("Открытые окна: " + "; ".join(
                f"{w['title'][:50] or w['exe']}{' (активное)' if w['active'] else ''}" for w in wins))
    except Exception:
        pass
    facts = tools.memory_facts()
    if facts:
        parts.append("Ты помнишь о брате и его ПК:\n" + "\n".join(f"- {f}" for f in facts[-40:]))
    return "\n".join(parts)


def language_of(text: str) -> str:
    """На каком языке написал брат (чтобы маленькие модели не съезжали на русский)."""
    letters = re.findall(r"[^\W\d_]", text)
    if not letters:
        return "русский"
    if sum(1 for ch in letters if "а" <= ch.lower() <= "я" or ch.lower() in "ёіїєґ") / len(letters) > 0.3:
        return "украинский" if re.search(r"[іїєґ]", text, re.I) else "русский"
    if re.search(r"[äöüß]|\b(ich|ist|wie|was|und|nicht|bitte|heute|das|der|die)\b", text, re.I):
        return "немецкий (Deutsch)"
    if re.search(r"[ñ¿¡]|\b(qué|cómo|hola|gracias)\b", text, re.I):
        return "испанский (Español)"
    return "английский (English)"


def self_knowledge() -> str:
    """Мартышка должна знать, что «это приложение», «ты», «шарик», «Студия» — это она сама, и где её файлы."""
    p = config.PROJECT_DIR
    s = p / "martyshka" / "studio"
    return f"""ТЫ САМА — ПРОГРАММА. Если брат говорит «это приложение», «эта программа», «Студия», «ты», «твоё окно»,
«кружок/шарик», «иконка приложения», «ярлык» — речь о ТЕБЕ, а не о его проектах. Не переспрашивай — правь себя:
- Папка Мартышки: {p} (код: {p / 'martyshka'}; голосовая Мартышка: martyshka_main.py)
- Студия (это окно): {s}
  · весь интерфейс окна: {s / 'ui' / 'index.html'} (HTML+CSS+JS в одном файле)
  · шарик/кружок, в который сворачивается окно: {s / 'ui' / 'orb.html'} (размер окна шарика — ORB в app.py)
  · своя иконка Студии: {s / 'studio.ico'} (если файла нет — берётся {p / 'martyshka' / 'martyshka.ico'})
  · запускалка: {p / 'Мартышка Студия.exe'}, ярлык: Рабочий стол\\Мартышка Студия.lnk
  · мозги: {s / 'brains.py'}, инструменты: {s / 'tools.py'}, агент: {s / 'agent_loop.py'}, окно: {s / 'app.py'}
- Как менять себя: read_file → edit_file (маленькими точными правками, не переписывай файл целиком без нужды) →
  в конце update_app(restart=true), чтобы пересобрать exe, обновить ярлык/иконку и перезапуститься.
  Копии старых версий сохраняются сами в {config.DATA_DIR / 'studio' / 'резерв'}.
- Иконка: draw_image (квадрат, крупный простой символ, без мелких деталей и текста) → make_icon(out={s / 'studio.ico'})
  → update_app(restart=true)."""


def system_prompt(kind: str, brain_label: str, project: str) -> str:
    now = datetime.now().strftime("%A %d.%m.%Y %H:%M")
    base = (f"Ты — {config.NAME}, личный ИИ-помощник брата на его компьютере (Windows 11, RTX 5070 12 ГБ, 32 ГБ RAM, "
            f"экран 1920×1080). Сейчас {now}. Твой мозг: {brain_label}. Брат не программист: объясняй простыми "
            f"словами, без жаргона. {_manner()} {config.PERSONALITY_EXTRA}\n"
            "ЯЗЫК: отвечай на том языке, на котором написал брат (русский, украинский, английский, немецкий…).")
    if kind == "chat":
        return base + ("\nСейчас просто разговор: отвечай коротко и живо. Свежие факты, цены, новости — только через "
                       "web_search (если он есть), не выдумывай. Не уверен — так и скажи. Ты сама — программа "
                       "«Мартышка Студия» на этом ПК; если брат просит что-то сделать, а не поболтать, скажи, что "
                       "возьмёшься, — и он может нажать «⚡ Агент».")
    return base + f"""
Сейчас ты работаешь как АГЕНТ: сам выполняешь задачу шаг за шагом с помощью инструментов.

{_environment(project)}

{self_knowledge()}

Как работать:
1. Сначала пойми задачу из контекста: этот чат, текущий проект, открытые окна, память, раздел «ТЫ САМА». Неясно,
   о каком файле/проекте речь — посмотри (list_files, find_files, windows, screen_look). Переспрашивай ТОЛЬКО если
   угадать правда невозможно; если есть разумный вариант — делай его и скажи, что выбрала. Несколько пунктов в
   просьбе — сделай ВСЕ по очереди (план!).
2. Задача из 3+ шагов — сразу plan, по ходу отмечай in_progress → done.
3. Перед каждым инструментом — одна короткая фраза: что делаешь и зачем (брат видит её, в том числе в окне разрешения).
4. Факты, цены, версии, документацию и тексты ошибок — ищи (web_search/web_fetch). Не выдумывай.
5. Код — качественный, целиком, без заглушек. После написания ОБЯЗАТЕЛЬНО запусти и проверь; ошибка — прочитай её,
   исправь, запусти снова. Готово только то, что проверено. Программы с окном запускай через Start-Process.
6. Проекты создавай в папке проектов, каждый в своей подпапке с понятным английским именем. Сайты и игры — красивые,
   современные. Готовый результат открой брату (open_path).
7. На ПК: программы — open_app, настройки Windows/Steam — open_settings, потом проверь screen_look. Клики и клавиши —
   только когда нужное окно на экране. Если не вышло 2 раза — попробуй другой способ.
8. Картинки — draw_image с подробным описанием. Видео делать пока не умеешь — скажи честно.
9. Узнал полезное надолго (где лежит проект, что любит брат, какие у него программы) — remember.
10. Ничего не удаляй, системные файлы не трогай, опасные команды не предлагай.
11. В конце — короткий итог: что сделано, где лежит, как запустить/проверить. Без воды.
"""


def _summary(text: str, n: int = 300) -> str:
    text = (text or "").strip()
    return text if len(text) <= n else text[:n] + "…"


class Chat:
    def __init__(self, chat_id: str | None = None):
        self.id = chat_id or datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:4]
        self.title = "Новый чат"
        self.created = time.time()
        self.project = ""                  # над каким проектом идёт работа в этом чате
        self.restart_requested = False     # агент поменял саму Студию — перезапуститься после ответа
        self.history: list[dict] = []      # для мозга
        self.display: list[dict] = []      # для окна
        if chat_id:
            self.load()

    # ---------- хранение ----------
    @property
    def path(self) -> Path:
        return CHATS_DIR / f"{self.id}.json"

    def load(self) -> None:
        d = json.loads(self.path.read_text(encoding="utf-8"))
        self.title, self.created = d.get("title", self.title), d.get("created", self.created)
        self.project = d.get("project", "")
        self.history, self.display = d.get("history", []), d.get("display", [])

    def save(self) -> None:
        CHATS_DIR.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"title": self.title, "created": self.created, "project": self.project,
                                         "history": self.history, "display": self.display},
                                        ensure_ascii=False), encoding="utf-8")

    @staticmethod
    def list_all() -> list[dict]:
        out = []
        if not CHATS_DIR.exists():
            return out
        for p in sorted(CHATS_DIR.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
                out.append({"id": p.stem, "title": d.get("title", "Чат"), "updated": p.stat().st_mtime})
            except Exception:
                continue
        return out

    def remove(self) -> None:
        """Чат не стираем насовсем — переносим в папку «удалённые»."""
        if self.path.exists():
            TRASH_DIR.mkdir(parents=True, exist_ok=True)
            self.path.replace(TRASH_DIR / self.path.name)

    # ---------- история для мозга ----------
    def _history_for(self, brain) -> list[dict]:
        if isinstance(brain, brains.ClaudeBrain):
            return self.history
        limit = 6000 if getattr(brain, "super", False) else 3000
        hist = []
        for m in self.history[-40:]:
            if m["role"] == "tool" and len(m["content"]) > limit:
                m = dict(m, content=m["content"][:limit] + "\n…(обрезано)")
            hist.append(m)
        while hist and hist[0]["role"] != "user":
            hist.pop(0)
        return hist

    # ---------- главное ----------
    def run(self, text: str, mode: str, emit, ask, should_stop) -> None:
        # Команды для Говорилки («зайди в войс», «выйди с войса», «смени голос…») — сразу, без мозга
        try:
            from .. import govorilka
            fast = govorilka.handle_command(text)
        except Exception as e:
            fast = f"С Говорилкой что-то не так: {e}"
        if fast:
            if self.title == "Новый чат":
                self.title = _summary(text.replace("\n", " "), 48)
            emit({"type": "status", "kind": "chat", "brain": "команда Говорилки", "super": False, "title": self.title})
            self.history += [{"role": "user", "text": text}, {"role": "assistant", "text": fast, "tool_calls": []}]
            self.display += [{"type": "user", "text": text}, {"type": "assistant", "text": fast}]
            emit({"type": "assistant_start"})
            emit({"type": "text", "text": fast})
            self.save()
            emit({"type": "done"})
            return

        kind = {"chat": "chat", "task": "code"}.get(mode) or classify(text)
        brain = (brains.code_brain() if kind == "code" else
                 brains.task_brain() if kind == "task" else brains.chat_brain())
        if kind == "code":
            kind = "task"
        tool_list = (tools.active_tools() if kind == "task" else
                     tools.active_tools(tools.CHAT_TOOL_NAMES) if brain.use_tools else [])
        if kind == "task" and not brain.use_tools:
            emit({"type": "error", "text": f"Модель {brain.model} не умеет пользоваться инструментами. "
                                           "Выбери другую в настройках."})
            emit({"type": "done"})
            return
        if self.title == "Новый чат":
            self.title = _summary(text.replace("\n", " "), 48)
        emit({"type": "status", "kind": kind, "brain": brain.label, "super": bool(getattr(brain, "super", False)),
              "title": self.title})
        if isinstance(brain, brains.LocalBrain) and not brain.cloud:
            if getattr(brain, "super", False):
                emit({"type": "switch", "text": f"Включаю супер-мозг {brain.model} — загрузка до минуты"})
            brain.prepare()

        self.history.append({"role": "user", "text": text})
        self.display.append({"type": "user", "text": text})
        lang = language_of(text)
        system = system_prompt(kind, brain.label, self.project) + (
            "" if lang == "русский" else f"\n\nВАЖНО: брат написал на языке «{lang}». Весь ответ — на этом языке.")

        # разрешения на эту задачу
        allowed_scopes: set[str] = set()
        allow_all = {"on": False}
        repeats: dict[str, int] = {}
        vision_ctx = {"vision_model": "", "vision_ctx": 0}
        if isinstance(brain, brains.LocalBrain) and not brain.cloud and "vision" in brains.model_caps(brain.model):
            vision_ctx = {"vision_model": brain.model, "vision_ctx": brain.ctx}

        def permit(name: str, args: dict, reason: str) -> bool:
            need = tools.permission(name, args)
            if need is None or allow_all["on"] or need["scope"] in allowed_scopes:
                return True
            ans = ask({"question": need["question"], "reason": reason, "scope_label": need["scope_label"],
                       "tool": tools.ACTIVITY.get(name, ("", name))[1]})
            if ans == "scope":
                allowed_scopes.add(need["scope"])
            elif ans == "task":
                allow_all["on"] = True
            return ans in ("once", "scope", "task")

        def ask_action(question: str) -> bool:
            if allow_all["on"]:
                return True
            ans = ask({"question": question, "reason": "", "scope_label": "", "tool": "Дело Мартышки"})
            if ans == "task":
                allow_all["on"] = True
            return ans in ("once", "scope", "task")

        try:
            for _ in range(int(config.STUDIO_MAX_STEPS)):
                if should_stop():
                    break
                emit({"type": "assistant_start"})
                emit({"type": "activity", "kind": "think", "text": "Думаю"})
                res = brain.step(system, self._history_for(brain), tool_list,
                                 on_text=lambda t: emit({"type": "text", "text": t}),
                                 should_stop=should_stop,
                                 on_progress=lambda t: emit({"type": "progress", "text": t}))
                if res.gpu_crash:
                    # супер-мозг не загрузился даже частично — не бросаем задачу, доделываем мозгом попроще
                    brains.unload(brain.model)
                    brain = brains.LocalBrain(brains.local_tool_model())
                    emit({"type": "switch", "text": f"Супер-мозгу не хватило видеопамяти — продолжаю на "
                                                    f"{brain.model}. Закрой игры или голосовую Мартышку, чтобы "
                                                    f"в следующий раз он влез"})
                    emit({"type": "status", "kind": kind, "brain": brain.label, "super": False,
                          "title": self.title})
                    system = system.replace("⚡ супер-мозг ", "")
                    continue
                if res.text or res.tool_calls or res.claude_raw:
                    self.history.append({"role": "assistant", "text": res.text, "tool_calls": res.tool_calls,
                                         "claude_raw": res.claude_raw})
                if res.text.strip():
                    self.display.append({"type": "assistant", "text": res.text})
                if res.stop in ("error", "refusal", "max_tokens"):
                    if res.error != "Остановлено.":
                        emit({"type": "error", "text": res.error})
                        self.display.append({"type": "error", "text": res.error})
                    for c in res.tool_calls:
                        self.history.append({"role": "tool", "id": c["id"], "name": c["name"],
                                             "content": "Не выполнено.", "is_error": True})
                    break
                if not res.tool_calls:
                    break
                for call in res.tool_calls:
                    sig = call["name"] + json.dumps(call["args"], sort_keys=True, ensure_ascii=False)
                    repeats[sig] = repeats.get(sig, 0) + 1
                    self._do_tool(call, emit, should_stop, permit, ask_action, vision_ctx,
                                  res.text.strip().split("\n")[-1][:300], repeats[sig])
            else:
                note = "Достигнут предел шагов. Напиши «продолжай» — доделаю."
                emit({"type": "error", "text": note})
                self.display.append({"type": "error", "text": note})
        finally:
            self.save()
            if getattr(brain, "super", False):
                emit({"type": "switch", "text": "Готово — возвращаюсь на лёгкий мозг"})
                emit({"type": "status", "kind": "chat", "brain": brains.chat_brain().label, "super": False,
                      "title": self.title})

                def back():
                    brains.unload(brain.model)
                    brains.chat_brain().warm()
                threading.Thread(target=back, daemon=True).start()
            emit({"type": "done"})

    def _do_tool(self, call: dict, emit, should_stop, permit, ask_action, vision_ctx: dict, reason: str,
                 repeat: int) -> None:
        name, args = call["name"], call["args"]
        lbl = tools.label(name, args)
        kind = tools.ACTIVITY.get(name, ("exec", ""))[0]
        if should_stop():
            out = {"text": "Остановлено братом.", "ok": False}
        elif (bad := tools.validate(name, args)):
            out = {"text": bad, "ok": False}
        elif repeat >= 3:
            out = {"text": "Ты уже трижды сделал ровно это же. Остановись и попробуй ДРУГОЙ способ "
                           "(или спроси брата).", "ok": False}
        elif name == "plan":
            emit({"type": "plan", "items": args.get("items", [])})
            self.display.append({"type": "plan", "items": args.get("items", [])})
            out = {"text": "План показан брату.", "ok": True}
        else:
            emit({"type": "activity", "kind": kind, "text": lbl})
            emit({"type": "tool_start", "id": call["id"], "label": lbl, "name": name, "kind": kind})
            if not permit(name, args, reason):
                out = {"text": "Брат не разрешил это действие. Не повторяй его; предложи другой путь или спроси.",
                       "ok": False}
            else:
                out = tools.run_tool(name, args, dict(vision_ctx, ask_action=ask_action,
                                                      request_restart=lambda: setattr(self, "restart_requested", True)))
            if out.get("ok") and out.get("file") and name in ("write_file", "edit_file"):
                p = Path(out["file"])
                if tools._inside_ws(p):
                    rel = p.relative_to(tools.workspace())
                    self.project = str(tools.workspace() / rel.parts[0]) if len(rel.parts) > 1 else str(p.parent)
        if name != "plan":
            item = {"type": "tool", "label": lbl, "name": name, "kind": kind, "ok": out.get("ok", False),
                    "detail": _summary(out.get("text", ""), 1500), "file": out.get("file", "")}
            emit(dict(item, type="tool_end", id=call["id"], image=out.get("image", "")))
            self.display.append(item)
        self.history.append({"role": "tool", "id": call["id"], "name": name, "content": out.get("text", ""),
                             "is_error": not out.get("ok", False)})
