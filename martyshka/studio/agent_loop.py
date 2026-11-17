"""
АГЕНТ СТУДИИ: один чат = одна «сессия».

Как работает задача:
  1. Решаем, что это: болтовня (мозг на ПК) или задача (умный мозг + инструменты).
  2. Мозг думает и вызывает инструменты (план, поиск, файлы, команды, картинки…).
  3. Результаты инструментов возвращаются мозгу, он смотрит и делает следующий шаг.
  4. Так, пока задача не готова (или не кончились шаги, или брат нажал «стоп»).
Окну по ходу отправляются события: текст, начало/конец шага, план, вопросы «да/нет».
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
    r"(напиш|сдела|созда|напрограмм|запрограмм|программ|код|скрипт|сайт|прилож|игр[ауы]|бот[аы]?\b|"
    r"найди|поищ|загугл|узна[йть]|погод|курс|новост|цен[аы]|сколько стоит|последн|свеж|сегодня|202\d|"
    r"нарису|картинк|фото|изображ|видео|открой|запусти|установи|исправ|почин|провер|посчита|переведи|"
    r"файл|папк|документ|таблиц|excel|python|html|javascript|c\+\+|c#|java\b)", re.IGNORECASE)


CODE_RE = re.compile(
    r"(напрограмм|запрограмм|программ|код|скрипт|сайт|прилож|игр[ауыо]|\bбот|парсер|функци|алгоритм|"
    r"python|питон|html|css|javascript|\bjs\b|c\+\+|c#|java\b|exe\b|баг|отлад|рефактор|"
    r"(напиш|сдела|созда|исправ|почин|доработ|улучш)\w*\s+(\S+\s+){0,3}(програм|код|скрипт|сайт|игр|бот|прилож|утилит))",
    re.IGNORECASE)


def classify(text: str) -> str:
    """chat — болтовня (лёгкий мозг), task — простое дело с инструментами, code — программирование (супер-мозг)."""
    if CODE_RE.search(text):
        return "code"
    return "task" if TASK_RE.search(text) else "chat"


def _manner() -> str:
    if config.PERSONALITY_MODE == "jarvis":
        return (f"Манера: спокойный вежливый ИИ-дворецкий, на «вы», обращение «{config.JARVIS_ADDRESS}», без мата.")
    return (f"Манера: свой в доску, на «ты», зовёшь его «{config.OWNER_NICKNAME}» или «брат», с юмором, "
            "мат только к месту и редко.")


def system_prompt(kind: str, brain_label: str) -> str:
    now = datetime.now().strftime("%d.%m.%Y %H:%M")
    base = (f"Ты — {config.NAME}, личный ИИ-помощник брата на его компьютере (Windows 11, RTX 5070 12 ГБ, "
            f"32 ГБ RAM). Сейчас {now}. Твой мозг сейчас: {brain_label}. Брат не программист и пишет по-русски: "
            f"отвечай по-русски, простыми словами, без жаргона. {_manner()} {config.PERSONALITY_EXTRA}".strip())
    if kind == "chat":
        return base + ("\nСейчас просто разговор. Отвечай коротко и живо. Если для ответа нужны свежие факты, а "
                       "инструмента поиска нет — честно скажи, что не уверен, и предложи поискать. Не выдумывай.")
    ws = tools.workspace()
    return base + f"""
Сейчас ты работаешь как АГЕНТ: выполняешь задачу брата сам, шаг за шагом, с помощью инструментов.

Правила работы:
- Задача из 3+ шагов — сначала вызови plan со списком шагов, по ходу отмечай статусы (in_progress → done).
- Перед каждым вызовом инструмента пиши одну короткую фразу, что сейчас делаешь и зачем — брат видит это в окне.
- Факты, цены, новости, версии библиотек, как что-то устроено — ищи через web_search/web_fetch. НЕ выдумывай.
  Если не нашёл — так и скажи.
- Проекты и файлы создавай в папке проектов: {ws} (каждый проект — своя подпапка с понятным названием).
- Код пиши качественно и целиком, без заглушек «TODO». После написания ОБЯЗАТЕЛЬНО проверь: запусти
  (run_command), посмотри на ошибки, исправь, запусти снова — пока не заработает. Для Python на ПК есть `python`.
- Для сайтов и игр делай современный красивый дизайн; готовый результат открой брату (open_path).
- Картинки рисуй через draw_image, описание делай подробным (сюжет, стиль, свет, ракурс, качество).
- Видео пока делать не умеешь (генератор видео не установлен) — так и скажи и предложи, что можно сделать.
- Ничего не удаляй без явной просьбы. Не трогай системные файлы. Опасные команды не предлагай.
- В конце — короткий итог для брата: что сделано, где лежит, как запустить. Без воды.
"""


def _summary(text: str, n: int = 300) -> str:
    text = (text or "").strip()
    return text if len(text) <= n else text[:n] + "…"


class Chat:
    def __init__(self, chat_id: str | None = None):
        self.id = chat_id or datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:4]
        self.title = "Новый чат"
        self.created = time.time()
        self.history: list[dict] = []      # для мозга
        self.display: list[dict] = []      # для окна (что показать при открытии чата)
        if chat_id:
            self.load()

    # ---------- хранение ----------
    @property
    def path(self) -> Path:
        return CHATS_DIR / f"{self.id}.json"

    def load(self) -> None:
        d = json.loads(self.path.read_text(encoding="utf-8"))
        self.title, self.created = d.get("title", self.title), d.get("created", self.created)
        self.history, self.display = d.get("history", []), d.get("display", [])

    def save(self) -> None:
        CHATS_DIR.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"title": self.title, "created": self.created, "history": self.history,
                                         "display": self.display}, ensure_ascii=False), encoding="utf-8")

    @staticmethod
    def list_all() -> list[dict]:
        out = []
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
        # Локальной модели мало памяти: длинные результаты урезаем, старое отбрасываем
        hist = []
        for m in self.history[-40:]:
            if m["role"] == "tool" and len(m["content"]) > 3000:
                m = dict(m, content=m["content"][:3000] + "\n…(обрезано)")
            hist.append(m)
        while hist and hist[0]["role"] != "user":       # история должна начинаться с вопроса брата
            hist.pop(0)
        return hist

    # ---------- главное ----------
    def run(self, text: str, mode: str, emit, ask, should_stop) -> None:
        # «Агент» в окне = максимальное качество (супер-мозг); «Авто» решает сам
        kind = {"chat": "chat", "task": "code"}.get(mode) or classify(text)
        brain = (brains.code_brain() if kind == "code" else
                 brains.task_brain() if kind == "task" else brains.chat_brain())
        if kind == "code":
            kind = "task"                      # дальше работает как обычный агент, только мозг сильнее
        tool_list = tools.TOOLS if kind == "task" else (tools.CHAT_TOOLS if brain.use_tools else [])
        if kind == "task" and not brain.use_tools:
            emit({"type": "error", "text": f"Модель {brain.model} не умеет пользоваться инструментами. "
                                           "Выбери другую в настройках студии."})
            return
        if self.title == "Новый чат":
            self.title = _summary(text.replace("\n", " "), 48)
        emit({"type": "status", "kind": kind, "brain": brain.label, "title": self.title})
        if isinstance(brain, brains.LocalBrain) and not brain.cloud:
            if getattr(brain, "super", False):
                emit({"type": "switch", "text": f"⚡ Включаю супер-мозг {brain.model} — загрузка до минуты…"})
            brain.prepare()                    # освобождаем видеокарту от других моделей

        self.history.append({"role": "user", "text": text})
        self.display.append({"type": "user", "text": text})
        system = system_prompt(kind, brain.label)
        always_yes: set[str] = set()

        def ask_once(question: str, tool_name: str) -> bool:
            if tool_name in always_yes:
                return True
            ans = ask(question)            # "yes" | "always" | "no"
            if ans == "always":
                always_yes.add(tool_name)
            return ans in ("yes", "always")

        try:
            for _ in range(int(config.STUDIO_MAX_STEPS)):
                if should_stop():
                    break
                emit({"type": "assistant_start"})
                res = brain.step(system, self._history_for(brain), tool_list,
                                 on_text=lambda t: emit({"type": "text", "text": t}),
                                 should_stop=should_stop,
                                 on_progress=lambda t: emit({"type": "progress", "text": t}))
                if res.text or res.tool_calls or res.claude_raw:
                    self.history.append({"role": "assistant", "text": res.text, "tool_calls": res.tool_calls,
                                         "claude_raw": res.claude_raw})
                if res.text.strip():
                    self.display.append({"type": "assistant", "text": res.text})
                if res.stop in ("error", "refusal", "max_tokens"):
                    if res.error != "Остановлено.":
                        emit({"type": "error", "text": res.error})
                        self.display.append({"type": "error", "text": res.error})
                    # мозгу нужен ответ на каждый вызов инструмента, иначе история «сломается»
                    for c in res.tool_calls:
                        self.history.append({"role": "tool", "id": c["id"], "name": c["name"],
                                             "content": "Не выполнено.", "is_error": True})
                    break
                if not res.tool_calls:
                    break
                for call in res.tool_calls:
                    self._do_tool(call, emit, ask_once, should_stop)
            else:
                note = "Достиг предела шагов. Напиши «продолжай», и я доделаю."
                emit({"type": "error", "text": note})
                self.display.append({"type": "error", "text": note})
        finally:
            self.save()
            if getattr(brain, "super", False):
                # задача готова — супер-мозг выгружаем, лёгкий загружаем обратно (в фоне)
                emit({"type": "switch", "text": "Готово — возвращаюсь на лёгкий мозг."})
                emit({"type": "status", "kind": "chat", "brain": brains.chat_brain().label, "title": self.title})

                def back():
                    brains.unload(brain.model)
                    brains.chat_brain().warm()
                threading.Thread(target=back, daemon=True).start()
            emit({"type": "done"})

    def _do_tool(self, call: dict, emit, ask_once, should_stop) -> None:
        name, args = call["name"], call["args"]
        if should_stop():
            out = {"text": "Остановлено братом.", "ok": False}
        elif (bad := tools.validate(name, args)):
            out = {"text": bad, "ok": False}
        else:
            if name == "plan":
                emit({"type": "plan", "items": args.get("items", [])})
                self.display.append({"type": "plan", "items": args.get("items", [])})
            lbl = tools.label(name, args)
            if name != "plan":
                emit({"type": "tool_start", "id": call["id"], "label": lbl, "name": name})
            out = tools.run_tool(name, args, False, lambda q: ask_once(q, name))
            if name != "plan":
                item = {"type": "tool", "label": lbl, "name": name, "ok": out.get("ok", False),
                        "detail": _summary(out.get("text", ""), 1500), "file": out.get("file", "")}
                emit(dict(item, type="tool_end", id=call["id"], image=out.get("image", "")))
                self.display.append(item)
        self.history.append({"role": "tool", "id": call["id"], "name": name, "content": out.get("text", ""),
                             "is_error": not out.get("ok", False)})
