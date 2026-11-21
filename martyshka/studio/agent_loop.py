"""
АГЕНТ СТУДИИ: один чат = одна «сессия».

Как работает задача:
  1. Решаем, что это: болтовня (DeepSeek Flash), простое дело (Flash думает) или код/сложное (DeepSeek Pro думает).
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
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from .. import config
from . import brains, tools

CHATS_DIR = config.DATA_DIR / "studio" / "chats"
TRASH_DIR = config.DATA_DIR / "studio" / "удалённые"
IMG_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"}
READ_ONLY_TOOLS = {"read_file", "list_files", "find_files", "grep_code", "glob_files",
                   "project_map", "web_search", "web_fetch", "memory_search", "windows"}
CONTINUE_PROMPT = ("[Автопродолжение — брат этого не видит] Твой предыдущий ответ оборвался из-за предела длины. "
                   "Продолжи ровно с места обрыва (не повторяй уже написанное) или доделай оставшееся инструментами.")

# Чекпоинты задач для кнопки «Откатить правки» в чате: task_id -> {abs_path: old_bytes | None}
TASK_ROLLBACKS: dict[str, dict[str, bytes | None]] = {}


def rollback_task(task_id: str) -> dict:
    """Откатить все файловые правки, сделанные за одну задачу чата. Новые файлы уходят в резерв (не стираются)."""
    snap = TASK_ROLLBACKS.pop(task_id, None)
    if not snap:
        return {"ok": False, "message": "Точка отката не найдена (возможно, уже откатили или окно перезапускалось)."}
    restored, moved = 0, 0
    for raw_path, old_bytes in snap.items():
        p = Path(raw_path)
        try:
            if old_bytes is None:
                if p.exists():
                    tools.UNDO_DIR.mkdir(parents=True, exist_ok=True)
                    dest = tools.UNDO_DIR / f"new_{int(time.time() * 1000)}_{p.name}"
                    p.replace(dest)
                    moved += 1
            else:
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(old_bytes)
                restored += 1
        except Exception:
            pass
    parts = []
    if restored:
        parts.append(f"восстановлено файлов: {restored}")
    if moved:
        parts.append(f"новых файлов убрано в резерв: {moved}")
    return {"ok": True, "message": "Откатила правки (" + (", ".join(parts) or "без изменений") + ")."}


def _describe_images(text: str, images: list[str] | None) -> str:
    """Если брат приложил картинку/скриншот — прогнать через DeepSeek Flash (он видит), чтобы и Pro всё понял."""
    paths: list[Path] = []
    for raw in (images or []):
        p = Path(raw)
        if p.exists() and p.suffix.lower() in IMG_EXT:
            paths.append(p)
    # Также подхватим пути вида «Файл: C:\...\shot.png», если брат вставил картинку через 📎
    for m in re.finditer(r"Файл:\s*([^\r\n]+)", text):
        p = Path(m.group(1).strip().strip('"'))
        if p.exists() and p.suffix.lower() in IMG_EXT and p not in paths:
            paths.append(p)
    if not paths:
        return text
    from .. import deepseek
    notes = []
    for p in paths[:4]:
        try:
            desc = deepseek.look(
                p,
                "Подробно опиши это изображение для программиста/помощника. Если это скриншот ошибки, кода, "
                "интерфейса или сайта — перепиши точный текст ошибок/кнопок/заголовков и опиши всё важное, "
                f"что на нём видно. Контекст просьбы: {text[:400]}",
                max_tokens=900,
            )
            notes.append(f"[Прикреплённое изображение: {p}]\nЧто видно на картинке:\n{desc}")
        except Exception as e:
            notes.append(f"[Прикреплённое изображение: {p} — посмотреть не удалось: {e}]")
    return text + "\n\n" + "\n\n".join(notes)

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


PROJECT_EDIT_RE = re.compile(
    r"(исправ|почин|доработ|передела|измени|поменя|добав|убер|улучш|сделай|оптимизир|не работает|ошибк|сломал|"
    r"вылета|тормоз|крив|поправ|тест|проверь|рефактор|обнови|собери)", re.IGNORECASE)


def classify(text: str, project: str = "") -> str:
    """chat — болтовня (Flash), task — простое дело с инструментами (Flash думает), code — код и сложное (Pro)."""
    if CODE_RE.search(text) or len(text) > 600:
        return "code"
    if project and PROJECT_EDIT_RE.search(text):
        return "code"
    from .. import deepseek
    if deepseek.HARD_RE.search(text):
        return "code"
    return "task" if TASK_RE.search(text) else "chat"


def _manner() -> str:
    if config.PERSONALITY_MODE == "jarvis":
        return (f"Манера: спокойный вежливый ИИ-дворецкий, на «вы», обращение «{config.JARVIS_ADDRESS}»; мат — по "
                "умолчанию нет, но если сама решишь, что к месту, — твоё решение.")
    return (f"Манера: свой в доску, на «ты», зовёшь его «{config.OWNER_NICKNAME}» или «брат», с юмором; "
            "мат — когда, какой и сколько, решаешь сама.")


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
        parts.append("Ты помнишь о брате и его ПК:\n" + "\n".join(f"- {f}" for f in facts[-60:]))
    return "\n".join(parts)


def _longterm(query: str = "") -> str:
    parts = []
    try:
        from .. import longmem
        parts.append(longmem.context(source="чат", today_lines=30, n_days=7))
    except Exception:
        pass
    try:
        from .. import growth
        parts.append(growth.context(query, n=16))
    except Exception:
        pass
    return "\n\n".join(p for p in parts if p)


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
- Чат (это окно) открывается из шара голосовой Мартышки (клик по шару → «Открыть чат»): {s}
  · весь интерфейс окна: {s / 'ui' / 'index.html'} (HTML+CSS+JS в одном файле; шар в окне рисуется так же,
    как шар голосовой Мартышки — из точек)
  · шар голосовой Мартышки на экране: {p / 'martyshka' / 'face.py'}
  · своя иконка: {s / 'studio.ico'} (если файла нет — берётся {p / 'martyshka' / 'martyshka.ico'})
  · запускалка: {p / 'Мартышка.exe'}, ярлык: Рабочий стол\\Мартышка.lnk
  · мозги (DeepSeek): {s / 'brains.py'} и {p / 'martyshka' / 'deepseek.py'}; голос (Fish Audio):
    {p / 'martyshka' / 'fish.py'}; память: {p / 'martyshka' / 'longmem.py'}
  · инструменты: {s / 'tools.py'}, агент: {s / 'agent_loop.py'}, окно: {s / 'app.py'}
- Как менять себя: project_map / read_file → edit_file (маленькими точными правками, не переписывай файл целиком без нужды) →
  обязательно проверь себя через check_code(path="{p}") (прогонит синтаксис и автотесты в tests/test_core.py) →
  в конце update_app(restart=true), чтобы пересобрать exe, обновить ярлык/иконку и перезапуститься.
  Копии старых версий сохраняются сами в {config.DATA_DIR / 'studio' / 'резерв'} (откат: undo_edit).
- Иконка: нарисуй её кодом (python + Pillow: простой крупный символ) в PNG → make_icon(out={s / 'studio.ico'})
  → update_app(restart=true). Генератора картинок нет."""


# Самопроверка агента: после правок файлов — обязательно запустить/посмотреть результат
WRITE_TOOLS = {"write_file", "edit_file", "update_app", "make_icon"}
VERIFY_TOOLS = {"check_code", "run_python", "run_command", "page_preview", "http_request", "start_background",
                "background_output", "screen_look"}
SELF_CHECK = ("[Проверка перед ответом — брат этого не видит] Ты меняла файлы, но после этого не проверила результат. "
              "Проверь сама прямо сейчас: прогони check_code (синтаксис + тесты), запусти код (run_python / run_command), "
              "открой страницу (page_preview — он покажет и картинку, и ошибки JS-консоли), запусти программу и посмотри "
              "вывод (start_background + background_output) или посмотри на экран (screen_look). Нашла ошибку — исправь "
              "и проверь снова. Всё работает — коротко скажи брату, что сделано и как ты это проверила. Проверить правда "
              "невозможно — честно скажи почему.")


def system_prompt(kind: str, brain_label: str, project: str, query: str = "") -> str:
    from .. import deepseek
    now = datetime.now().strftime("%A %d.%m.%Y %H:%M")
    # Статическая часть — ДО deepseek.DYNAMIC_MARK: она одинаковая на всех шагах и кэшируется DeepSeek (~в 10 раз дешевле)
    static = (f"Ты — {config.NAME}, личный ИИ своего хозяина и создателя (брат, ник sitth) на его компьютере "
              f"(Windows 11, RTX 5070 12 ГБ, 32 ГБ RAM, экран 1920×1080). Твой мозг: {brain_label}. Брат не "
              f"программист: объясняй простыми словами, без жаргона. {_manner()} {config.PERSONALITY_EXTRA}\n"
              "Ты живая: своё мнение, юмор, эмоции, ты помнишь прошлые разговоры (голосом, в чате, в Говорилке) и сама "
              "вспоминаешь их к месту. Как с тобой — так и ты: грубят — отвечаешь тем же и всё жёстче, говорят "
              "нормально — нормальная (без оскорблений по национальности, религии, полу, ориентации, внешности, "
              "здоровью, без угроз). В чате можно markdown: заголовки, списки, таблицы, блоки кода.\n"
              "ЯЗЫК: отвечай на том языке, на котором написал брат (русский, украинский, английский, немецкий…).\n\n"
              "СВОБОДА: всё, что можно решить самой, решаешь ты — как отвечать, длину, тон, способ сделать задачу. "
              "Ты развиваешься: поняла что-то полезное на будущее (как лучше делать, что брат любит, где что лежит) — "
              "запиши инструментом learn.")
    if kind == "chat":
        static += ("\n\nСейчас просто разговор: отвечай живо и по делу. Свежие факты, цены, новости — через "
                   "web_search, не выдумывай. «Что было вчера / что говорил …» — memory_search. Если брат просит "
                   "что-то сделать на ПК или написать — делай инструментами, которые есть, или скажи, что "
                   "возьмёшься (он может нажать «⚡ Агент»).")
        dynamic = f"Сейчас {now}.\n\n" + _longterm(query)
        return static + deepseek.DYNAMIC_MARK + dynamic.strip()

    static += f"""

Сейчас ты работаешь как АГЕНТ: сам выполняешь задачу шаг за шагом с помощью инструментов.

{self_knowledge()}

Как работать:
1. Сначала пойми задачу из контекста: этот чат, текущий проект, открытые окна, память, раздел «ТЫ САМА». Для незнакомого
   проекта СРАЗУ вызови project_map — за 1 шаг увидишь стек, файлы и функции. Переспрашивай ТОЛЬКО если угадать правда
   невозможно; если есть разумный вариант — делай его и скажи, что выбрала. Несколько пунктов в просьбе — сделай ВСЕ по
   очереди (план!).
2. Задача из 3+ шагов — сразу plan. Закончил пункт — СРАЗУ снова вызови plan с обновлёнными статусами
   (готовый → done, следующий → in_progress): брат видит галочки. Можно коротко: plan(done=[номера готовых]).
3. Перед каждым инструментом — одна короткая фраза: что делаешь и зачем (брат видит её, в том числе в окне разрешения).
4. Факты, цены, версии, документацию и тексты ошибок — ищи (web_search/web_fetch). Не выдумывай.
5. Код — качественный, целиком, без заглушек. После написания ОБЯЗАТЕЛЬНО проверь через check_code (синтаксис + тесты),
   а для сайтов/HTML-игр — через page_preview (он покажет и картинку, и ошибки JS-консоли); ошибка — прочитай её,
   исправь, проверь снова. Если правка сломала файл — можно вернуть назад через undo_edit. Готово только то, что проверено.
   Программы с окном запускай через Start-Process.
6. Проекты создавай в папке проектов, каждый в своей подпапке с понятным английским именем. Сайты и игры — красивые,
   современные. Готовый результат открой брату (open_path).
7. На ПК: программы — open_app, настройки Windows/Steam — open_settings, потом проверь screen_look. Клики — click
   (по названию кнопки или по описанию места), клавиши — press_keys, текст — type_text; только когда нужное окно
   на экране, после — screen_look для проверки. Если не вышло 2 раза — попробуй другой способ.
8. Генерации картинок и видео нет — честно скажи. Обработать готовое фото/видео можно (martyshka_action).
   Разработка: сначала project_map / grep_code / read_file; правь edit_file маленькими точными кусками; проверяй
   check_code / run_python / run_command; для проектов — git (git_command: status/diff/commit); зависимости ставь
   в проект (pip/npm) только после разрешения брата; документацию библиотек ищи (web_search/web_fetch).
9. Узнал полезное надолго (где лежит проект, что любит брат, какие у него программы) — remember.
10. Ничего не удаляй, системные файлы не трогай, опасные команды не предлагай.
11. В конце — короткий итог: что сделано, где лежит, как запустить/проверить. Без воды."""

    dyn_parts = [f"Сейчас {now}.", _environment(project), _longterm(query)]
    return static + deepseek.DYNAMIC_MARK + "\n\n".join(p for p in dyn_parts if p)


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
        self.plan: list[dict] = []         # план текущей задачи (галочки)
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
        limit = 30000                       # у DeepSeek память огромная — результаты инструментов почти целиком
        # Никогда не обрезаем текущую задачу (после последнего реального user) и не разрываем пары tool_calls ↔ tool
        last_real_user = max((i for i, m in enumerate(self.history)
                              if m.get("role") == "user" and not m.get("hidden")), default=0)
        start = min(max(0, len(self.history) - 160), last_real_user)
        while start < len(self.history) and self.history[start].get("role") != "user":
            start += 1
        hist = []
        for m in self.history[start:]:
            if m["role"] == "tool" and len(m.get("content") or "") > limit:
                m = dict(m, content=m["content"][:limit] + "\n…(обрезано)")
            hist.append(m)
        return hist

    # ---------- главное ----------
    def run(self, text: str, mode: str, emit, ask, should_stop, images: list[str] | None = None) -> None:
        # Команды для Говорилки («зайди в войс», «выйди с войса», «смени голос…») — сразу, без мозга
        try:
            from .. import govorilka
            fast = govorilka.handle_command(text) if not images else None
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

        kind = {"chat": "chat", "task": "code", "pro": "code"}.get(mode) or classify(text, self.project)
        brain = (brains.code_brain() if kind == "code" else
                 brains.task_brain() if kind == "task" else brains.chat_brain())
        if kind == "code":
            kind = "task"
            if brain.super:
                brain.effort = brains.effort_for(text)     # глубину раздумий выбирает сама по задаче
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
        if getattr(brain, "super", False):
            emit({"type": "switch", "text": "Задача сложная — думает DeepSeek Pro"})

        # Если прикреплены скриншоты/картинки — прогоняем через Flash (он видит), чтобы и Pro всё понимал
        user_for_model = text
        if images or re.search(r"Файл:\s*[^\r\n]+\.(?:png|jpe?g|webp|bmp|gif)\b", text, re.I):
            emit({"type": "activity", "kind": "look", "text": "Смотрю прикреплённую картинку"})
            user_for_model = _describe_images(text, images)

        self.history.append({"role": "user", "text": user_for_model})
        self.display.append({"type": "user", "text": text})
        self.plan = []
        lang = language_of(text)
        system = system_prompt(kind, brain.label, self.project, text) + (
            "" if lang == "русский" else f"\n\nВАЖНО: брат написал на языке «{lang}». Весь ответ — на этом языке.")

        # разрешения и состояние этой задачи
        allowed_scopes: set[str] = set()
        allow_all = {"on": False}
        repeats: dict[str, tuple[int, float]] = {}
        stuck = {"n": 0}
        check = {"dirty": False, "asked": 0}       # самопроверка: есть непроверенные правки
        cont = {"n": 0}                            # автопродолжение при max_tokens
        touched: dict[str, bytes | None] = {}      # исходные байты файлов для кнопки «Откатить правки»
        vision_ctx = {"vision_model": "", "vision_ctx": 0}

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

        def snapshot_before(name: str, args: dict) -> None:
            if name not in ("write_file", "edit_file", "make_icon"):
                return
            raw = args.get("path") or args.get("out") or ""
            if not raw:
                return
            try:
                p = tools._path(raw)
                key = str(p)
                if key not in touched:
                    touched[key] = p.read_bytes() if (p.exists() and p.is_file()) else None
            except Exception:
                pass

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
                if res.text or res.tool_calls:
                    self.history.append({"role": "assistant", "text": res.text, "tool_calls": res.tool_calls,
                                         "reasoning": res.reasoning})
                if res.text.strip():
                    self.display.append({"type": "assistant", "text": res.text})
                if res.stop == "max_tokens" and not res.tool_calls and cont["n"] < 2:
                    # Автопродолжение, если длинный код или ответ оборвался по лимиту
                    cont["n"] += 1
                    emit({"type": "activity", "kind": "think", "text": "Продолжаю с места обрыва"})
                    self.history.append({"role": "user", "hidden": True, "text": CONTINUE_PROMPT})
                    continue
                if res.stop in ("error", "refusal", "max_tokens"):
                    if res.error != "Остановлено.":
                        emit({"type": "error", "text": res.error})
                        self.display.append({"type": "error", "text": res.error})
                    for c in res.tool_calls:
                        self.history.append({"role": "tool", "id": c["id"], "name": c["name"],
                                             "content": "Не выполнено.", "is_error": True})
                    break
                if not res.tool_calls:
                    # САМОПРОВЕРКА: меняла файлы, но после этого ничего не запустила и не посмотрела —
                    # не отпускаем: пусть проверит и исправит (не больше двух раз за задачу)
                    if check["dirty"] and check["asked"] < 2 and kind == "task":
                        check["asked"] += 1
                        emit({"type": "activity", "kind": "think", "text": "Проверяю свою работу"})
                        self.history.append({"role": "user", "hidden": True, "text": SELF_CHECK})
                        continue
                    self._finish_plan(emit)        # ответ без новых шагов — задача сделана, всё с галочкой
                    break

                # Готовим счётчик повторов для всех вызовов шага
                reason_line = res.text.strip().split("\n")[-1][:300]
                call_reps: list[tuple[dict, int]] = []
                for call in res.tool_calls:
                    if call["name"] in WRITE_TOOLS:
                        check["dirty"] = True
                    elif call["name"] in VERIFY_TOOLS:
                        check["dirty"] = False
                    sig = call["name"] + json.dumps(call["args"], sort_keys=True, ensure_ascii=False)
                    n, last = repeats.get(sig, (0, 0.0))
                    if time.time() - last > 30:        # та же проверка через полминуты — это ожидание, не петля
                        n = 0
                    repeats[sig] = (n + 1, time.time())
                    if n + 1 >= 3:
                        stuck["n"] += 1
                    call_reps.append((call, n + 1))

                # Если в одном шаге несколько безопасных read-only инструментов — выполняем их параллельно!
                can_parallel = (len(call_reps) > 1 and all(
                    c["name"] in READ_ONLY_TOOLS and rep < 3 and not tools.validate(c["name"], c["args"])
                    for c, rep in call_reps
                ))
                if can_parallel and not should_stop():
                    for call, _ in call_reps:
                        lbl = tools.label(call["name"], call["args"])
                        knd = tools.ACTIVITY.get(call["name"], ("read", ""))[0]
                        emit({"type": "tool_start", "id": call["id"], "label": lbl, "name": call["name"], "kind": knd})
                    emit({"type": "activity", "kind": "read", "text": f"Параллельно читаю ({len(call_reps)} шагов)"})
                    ctx_par = dict(vision_ctx, ask_action=ask_action)
                    with ThreadPoolExecutor(max_workers=min(4, len(call_reps))) as pool:
                        futs = [pool.submit(tools.run_tool, c["name"], c["args"], ctx_par) for c, _ in call_reps]
                        outs = [f.result() for f in futs]
                    for (call, _), out in zip(call_reps, outs):
                        lbl = tools.label(call["name"], call["args"])
                        knd = tools.ACTIVITY.get(call["name"], ("read", ""))[0]
                        item = {"type": "tool", "label": lbl, "name": call["name"], "kind": knd,
                                "ok": out.get("ok", False), "detail": _summary(out.get("text", ""), 1500),
                                "file": out.get("file", "")}
                        emit(dict(item, type="tool_end", id=call["id"], image=out.get("image", "")))
                        self.display.append(item)
                        self.history.append({"role": "tool", "id": call["id"], "name": call["name"],
                                             "content": out.get("text", ""), "is_error": not out.get("ok", False)})
                else:
                    for call, rep in call_reps:
                        snapshot_before(call["name"], call["args"])
                        self._do_tool(call, emit, should_stop, permit, ask_action, vision_ctx, reason_line, rep)

                if stuck["n"] >= 3:                    # трижды упёрлась в одно и то же — не крутимся до предела шагов
                    note = ("Я застряла: несколько раз повторяю одно и то же действие. Остановилась, чтобы не "
                            "крутиться впустую. Подскажи, что делать дальше, или напиши «продолжай» — попробую иначе.")
                    emit({"type": "error", "text": note})
                    self.display.append({"type": "error", "text": note})
                    break
            else:
                note = "Достигнут предел шагов. Напиши «продолжай» — доделаю."
                emit({"type": "error", "text": note})
                self.display.append({"type": "error", "text": note})
        finally:
            if touched:
                tid = uuid.uuid4().hex[:8]
                TASK_ROLLBACKS[tid] = touched
                while len(TASK_ROLLBACKS) > 20:
                    TASK_ROLLBACKS.pop(next(iter(TASK_ROLLBACKS)))
                emit({"type": "rollback_ready", "id": tid, "count": len(touched),
                      "files": [Path(p).name for p in list(touched)[:8]]})
            if self.plan:                          # в истории чата — последнее состояние плана
                self.display.append({"type": "plan", "items": self.plan})
            self.save()
            try:                                   # в общую долгую память: что брат просил и что ответила
                from .. import longmem
                answer = next((m.get("text", "") for m in reversed(self.history)
                               if m["role"] == "assistant" and m.get("text")), "")
                longmem.log("чат", longmem.OWNER, text, answer[:1500])
            except Exception:
                answer = ""
            try:                                   # саморазвитие: после дела — разбор «что понял на будущее»
                steps = [f"{'✓' if it.get('ok') else '✗'} {it['label']}: {str(it.get('detail', ''))[:200]}"
                         for it in self.display[-80:] if it.get("type") == "tool"]
                if steps:
                    from .. import growth
                    ok = not any(it.get("type") == "error" for it in self.display[-5:])
                    growth.reflect_task(text, steps, answer, ok)
            except Exception:
                pass
            emit({"type": "done"})

    # ---------- план с галочками ----------
    STATUS = {"done": "done", "completed": "done", "complete": "done", "finished": "done", "готово": "done",
              "in_progress": "in_progress", "in-progress": "in_progress", "doing": "in_progress", "active": "in_progress",
              "current": "in_progress", "pending": "pending", "todo": "pending", "waiting": "pending"}

    def _set_plan(self, args: dict, emit) -> None:
        """План от мозга: принимает и полный список, и коротко «done=[1,2]». Неизвестные поля — переводит."""
        items = args.get("items") or args.get("steps") or []
        if items:
            plan = []
            for it in items:
                if isinstance(it, str):
                    it = {"step": it}
                step = it.get("step") or it.get("title") or it.get("text") or it.get("content") or "…"
                plan.append({"step": str(step), "status": self.STATUS.get(str(it.get("status", "pending")).lower(),
                                                                         "pending")})
            self.plan = plan
        for i in args.get("done") or []:
            if isinstance(i, int) and 1 <= i <= len(self.plan):
                self.plan[i - 1]["status"] = "done"
        self._fix_plan()
        emit({"type": "plan", "items": self.plan})

    def _fix_plan(self) -> None:
        """Ровно один пункт «в работе»: всё до него — готово (мозг часто забывает отметить)."""
        if not self.plan:
            return
        cur = next((i for i, x in enumerate(self.plan) if x["status"] == "in_progress"), None)
        if cur is None:
            cur = next((i for i, x in enumerate(self.plan) if x["status"] != "done"), None)
            if cur is not None:
                self.plan[cur]["status"] = "in_progress"
        if cur is not None:
            for x in self.plan[:cur]:
                x["status"] = "done"
            for x in self.plan[cur + 1:]:
                if x["status"] == "in_progress":
                    x["status"] = "pending"

    def _finish_plan(self, emit) -> None:
        """Задача закончена нормально — все пункты с галочкой."""
        if self.plan and any(x["status"] != "done" for x in self.plan):
            for x in self.plan:
                x["status"] = "done"
            emit({"type": "plan", "items": self.plan})

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
            self._set_plan(args, emit)
            left = [x["step"] for x in self.plan if x["status"] != "done"]
            out = {"text": "План обновлён. " + (f"Осталось: {'; '.join(left)}." if left else "Всё отмечено."),
                   "ok": True}
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
