"""
TELEGRAM-СЕРВИС: работает независимо от голосовой Мартышки.

Раньше Telegram-бот жил внутри голосовой (martyshka_main.py) и отвечал только
пока включён голос. Теперь бота держит ОДИН процесс — тот, что запустился первым:
голосовая Мартышка ИЛИ окно чата (Студия). Пока работает хоть что-то одно — бот на связи.

Держатель — файл data/telegram_держатель.json с pid процесса. Если процесс умер,
другой (сторожем) подхватывает бота сам.
"""
import ctypes
import io
import json
import os
import queue
import re
import threading
import time
from pathlib import Path

import numpy as np

from . import config, errlog, style

HOLDER = config.DATA_DIR / "telegram_держатель.json"
WATCHDOG_EVERY = 15.0    # с: как часто проверяем, не умер ли держатель

NO_RECOVER = {"узнать", "погода", "заряд", "сводка", "система", "заметки", "буфер", "питание",
              "отмена выключения", "экран", "нажать", "клавиши", "команда", "задача", "удалить",
              "окна", "игра", "доработать игру", "exe", "вспомнить"}

DONT_KNOW_RE = re.compile(r"(не знаю|не в курсе|без понятия|не уверен|нет (у меня )?(информации|данных)|"
                          r"не могу (знать|сказать|проверить)|не имею доступа|не слежу)", re.IGNORECASE)
AGENT_CMD_RE = re.compile(r"^\s*(?:агент|программируй|кодь|agent)\s*[:\-]\s*(.+)$", re.IGNORECASE | re.S)
TG_FILES_DIR = config.DATA_DIR / "telegram_files"


# ---------- держатель (только один процесс поллит Telegram) ----------
def _pid_alive(pid) -> bool:
    if not pid:
        return False
    k32 = ctypes.windll.kernel32
    h = k32.OpenProcess(0x1000, False, int(pid))   # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return False
    code = ctypes.c_ulong()
    k32.GetExitCodeProcess(h, ctypes.byref(code))   # процесс закрылся, но ссылка на него осталась — не жив
    k32.CloseHandle(h)
    return code.value == 259                         # STILL_ACTIVE


def _read_holder() -> dict:
    try:
        return json.loads(HOLDER.read_text(encoding="utf-8"))
    except Exception:
        return {}


def held_by_other() -> bool:
    d = _read_holder()
    return _pid_alive(d.get("pid")) and int(d.get("pid") or 0) != os.getpid()


def acquire() -> bool:
    """Стать держателем бота. Возвращает False, если уже держит живой чужой процесс."""
    for _ in range(2):
        if held_by_other():
            return False
        try:                                # держатель мёртв — убрать старый
            HOLDER.unlink(missing_ok=True)
        except Exception:
            pass
        try:                                # атомарно занять место
            fd = os.open(HOLDER, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, json.dumps({"pid": os.getpid(), "started": time.time()}).encode("utf-8"))
            os.close(fd)
            return True
        except FileExistsError:
            continue
    return False


def release() -> None:
    try:
        if int(_read_holder().get("pid") or 0) == os.getpid():
            HOLDER.unlink(missing_ok=True)
    except Exception:
        pass


def run_actions(brain, hands, actions, confirm_fn, request: str):
    """Выполнить дела; если что-то не вышло — один раз попросить мозг найти другой путь."""
    summary, need_report, lines = hands.execute_all(actions, confirm_fn)
    for n, a, t in hands.last_failed:
        if "передумал" not in t:
            errlog.write("дело не вышло", просьба=request, дело=f"{n} | {' | '.join(a)}", результат=t[:300])
    failed = [f for f in hands.last_failed if f[0] not in NO_RECOVER]
    if failed:
        what = "; ".join(f"{n} {' | '.join(a)} → {t}" for n, a, t in failed)
        try:
            plan_b = brain.recover(what, request)
        except Exception:
            plan_b = []
        plan_b = [p for p in plan_b if (p[0], p[1]) not in [(n, a) for n, a, _ in failed]]
        if plan_b:
            lines.append("(не вышло — пробую по-другому)")
            s2, n2, l2 = hands.execute_all(plan_b, confirm_fn)
            summary += "; ОБХОДНОЙ ПУТЬ: " + s2
            lines += l2
            need_report = True
    return summary, need_report, lines


class TelegramService:
    """events — чужая очередь (голосовая Мартышка): сообщения обрабатывает ЕЁ главный цикл через handle(),
    по очереди с голосом — так один мозг не работает из двух потоков сразу. Без неё — свой поток."""
    def __init__(self, log=print, brain=None, transcriber=None, events: queue.Queue | None = None):
        self.log = log
        self.inline = events is not None
        self.events: queue.Queue = events if events is not None else queue.Queue()
        self.tg = None
        self.brain = brain
        self.transcriber = transcriber
        self._owns_brain = brain is None
        self.turn_log = errlog.Turn()
        self._stop = threading.Event()

    # ---------- запуск ----------
    def start(self) -> bool:
        if not config.TELEGRAM_TOKEN.strip() or self.tg is not None:
            return False
        # СНАЧАЛА занять место держателя, потом слушать Telegram: иначе два процесса читали бы сообщения
        # одновременно (Telegram отдаёт каждое только одному — и половина терялась бы)
        if not acquire():
            return False
        try:
            self._prepare()
            from .telegram_bot import TelegramBot
            tg = TelegramBot(self.events, log=self.log)
            if not tg.start():
                release()
                return False
            self.tg = tg
        except Exception as e:
            self.log(f"   (Telegram-сервис не запустился: {e})")
            release()
            return False
        import atexit
        atexit.register(release)
        if not self.inline:
            threading.Thread(target=self._run, daemon=True, name="telegram-run").start()
        return True

    def handle(self, msg: dict) -> None:
        """Обработать сообщение из чужой очереди (главный цикл голосовой Мартышки)."""
        try:
            self._handle(msg)
        except Exception as e:
            self.log(f"   (Telegram: ошибка {e})")
            if self.tg:
                self.tg.send(f"Что-то сломалось: {e}")

    def _prepare(self) -> None:
        from . import hands, media, games
        if self.brain is None:
            from .brain import Brain
            self.brain = Brain()
            self.brain.warm_up()
            # не перебиваем уже настроенные «руки» (в Студии свой служебный мозг) — ставим только пустое
            if hands._llm is None:
                hands.set_llm(self.brain.complete)
            if media._llm is None:
                media.set_hooks(gpu_before=None, gpu_after=None, llm=self.brain.complete)
            if games._llm_raw is None:
                games.set_llm(self.brain.complete_raw)
        hands.load_apps()
        if self.transcriber is None:
            from .ears import Transcriber
            self.transcriber = Transcriber()

    def stop(self) -> None:
        self._stop.set()
        release()

    # ---------- сторож: если держатель умер — подхватываем ----------
    def watchdog_loop(self) -> None:
        while not self._stop.is_set():
            try:
                if self.tg is None and not held_by_other() and config.TELEGRAM_TOKEN.strip():
                    self.log("   (Telegram-сервис: держатель освободился — захожу)")
                    self.start()
            except Exception:
                pass
            time.sleep(WATCHDOG_EVERY)

    # ---------- приём и обработка ----------
    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                _kind, msg = self.events.get(timeout=5)
            except queue.Empty:
                continue
            try:
                self._handle(msg)
            except Exception as e:
                self.log(f"   (Telegram: ошибка {e})")
                if self.tg:
                    self.tg.send(f"Что-то сломалось: {e}")

    def _run_agent(self, task_text: str, images: list[str] | None = None) -> None:
        """Запустить полноценного агента Студии по команде из Telegram."""
        tg = self.tg
        if tg is None:
            return
        from .studio import agent_loop
        tg.send("⚡ Приняла задачу в режим агента, работаю…")
        tg.typing()
        chat = agent_loop.Chat()
        last_answer: list[str] = []
        created_files: list[Path] = []
        last_typing = [time.time()]

        def emit(ev: dict) -> None:
            tp = ev.get("type")
            if tp == "text":
                last_answer.append(ev.get("text", ""))
            elif tp == "tool_start":
                if time.time() - last_typing[0] > 4:
                    tg.typing()
                    last_typing[0] = time.time()
            elif tp == "tool_end" and ev.get("ok") and ev.get("file"):
                p = Path(ev["file"])
                if p.exists() and p not in created_files:
                    created_files.append(p)
            elif tp == "error":
                last_answer.append("\n⚠ " + str(ev.get("text", "")))

        def ask(info: dict) -> str:
            q = f"⚡ Агент спрашивает ({info.get('tool', 'действие')}):\n{info.get('question', '')}"
            return "once" if tg.confirm(q) else "no"

        chat.run(task_text, "pro", emit, ask, lambda: self._stop.is_set(), images=images)
        reply = "".join(last_answer).strip() or "Готово."
        tg.send(reply)
        for p in created_files[:4]:
            try:
                if p.stat().st_size < 14_000_000:
                    if p.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp"):
                        tg.send_photo(p)
                    else:
                        tg.send_document(p)
            except Exception:
                pass

    def _handle(self, msg: dict) -> None:
        tg = self.tg
        if tg is None:
            return
        text = msg.get("text", "")
        if msg.get("voice"):
            data = tg.download_voice(msg["voice"])
            try:
                import soundfile as sf
                from scipy.signal import resample_poly
                a, sr = sf.read(io.BytesIO(data), dtype="float32")
                if a.ndim > 1:
                    a = a.mean(1)
                g = np.gcd(16000, sr)
                text = self.transcriber.transcribe(resample_poly(a, 16000 // g, sr // g).astype(np.float32))
            except Exception:
                text = ""
            if not text:
                tg.send("Не разобрал голосовое — напиши текстом.")
                return
            tg.send(f"🎙 Услышал: {text}")

        # Фото из Telegram → сохраняем и смотрим через DeepSeek Flash (или отдаём агенту, если есть «агент:»)
        if msg.get("photo"):
            tg.typing()
            data, rpath = tg.download_file(msg["photo"])
            if not data:
                tg.send("Не получилось скачать фото.")
                return
            TG_FILES_DIR.mkdir(parents=True, exist_ok=True)
            ext = Path(rpath).suffix or ".jpg"
            img_path = TG_FILES_DIR / f"photo_{time.strftime('%Y%m%d_%H%M%S')}{ext}"
            img_path.write_bytes(data)
            self.log(f"📱 Ты (Telegram фото): {img_path.name} {text}")
            m_ag = AGENT_CMD_RE.match(text or "")
            if m_ag:
                self._run_agent(m_ag.group(1).strip(), images=[str(img_path)])
                return
            from . import deepseek
            q = text or "Опиши, что на этом фото. Если это скриншот ошибки, кода или документа — разбери и помоги решить."
            ans = deepseek.look(img_path, q, max_tokens=1000)
            tg.send(ans or "Не смогла разобрать картинку.")
            return

        # Документ из Telegram → сохраняем в data/telegram_files и передаём агенту
        if msg.get("document"):
            tg.typing()
            data, _ = tg.download_file(msg["document"])
            if not data:
                tg.send("Не получилось скачать файл.")
                return
            TG_FILES_DIR.mkdir(parents=True, exist_ok=True)
            safe_name = re.sub(r'[<>:"/\\|?*]', "_", msg.get("file_name") or "file.bin")[:120]
            doc_path = TG_FILES_DIR / f"{time.strftime('%Y%m%d_%H%M%S')}_{safe_name}"
            doc_path.write_bytes(data)
            self.log(f"📱 Ты (Telegram файл): {doc_path.name} {text}")
            prompt = (f"{text}\n\n" if text else "Посмотри этот файл и коротко расскажи, что в нём.\n\n") + f"Файл: {doc_path}"
            self._run_agent(prompt)
            return

        from .ears import strip_wake_word
        from .brain import reply_language
        from . import commands, hands, media

        text = strip_wake_word(text) or text      # «Мартышка, …» — имя не нужно
        self.log(f"📱 Ты (Telegram): {text}")
        self.turn_log.complaint(text, "telegram")
        tg.typing()

        # Режим агента-программиста из Telegram: «агент: …» / «программируй: …» / «кодь: …»
        m_ag = AGENT_CMD_RE.match(text)
        if m_ag:
            self._run_agent(m_ag.group(1).strip())
            return

        own = commands.match(text)
        if own:
            _s, _n, lines = hands.execute_all(commands.actions_for(own, self.brain.actions_only), tg.confirm)
            for line in lines:
                self.log(f"  {line}")
            tg.send(own.get("say", "").strip() or "Сделано.")
            return

        lang = reply_language(text)
        self.brain.source = "telegram"
        try:
            answer = "".join(self.brain.reply(text, lang)).strip()
        finally:
            self.brain.source = "голос"
        if not self.brain.last_actions and config.WEB_ENABLED and DONT_KNOW_RE.search(answer):
            self.brain.last_actions = [("узнать", [text])]
            answer = ""
        if answer:
            tg.send(answer)
        if self.brain.last_actions:
            tg.typing()
            hands.set_progress(tg.send)
            summary, need_report, lines = run_actions(self.brain, hands, self.brain.last_actions, tg.confirm, text)
            hands.set_progress(self.log)
            hands.to_read.clear()                 # «прочитать вслух» с телефона не нужно
            for line in lines:
                self.log(f"  {line}")
            if any(a[0] in ("фото", "видео", "нарисуй") for a in self.brain.last_actions):
                f = media.last_file
                if f and f.exists() and time.time() - f.stat().st_mtime < 600:
                    if f.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp") and f.stat().st_size < 9_000_000:
                        tg.send_photo(f)
                    else:
                        tg.send_document(f)
            if any(a[0] in ("документ", "таблица", "игра", "доработать игру", "exe") for a in self.brain.last_actions):
                files = sorted((Path.home() / "Documents").glob("*.*"), key=lambda p: p.stat().st_mtime)
                newest = hands.last_document or (files[-1] if files else None)
                if newest and newest.exists() and time.time() - newest.stat().st_mtime < 600:
                    tg.send_document(newest)
            if any(a[0] == "скриншот" for a in self.brain.last_actions):
                shots = sorted((Path.home() / "Pictures" / "Скриншоты Мартышки").glob("*.png"),
                               key=lambda p: p.stat().st_mtime)
                if shots:
                    tg.send_photo(shots[-1])
            if need_report:
                tg.send("".join(self.brain.report(summary)).strip())
            elif self.brain.pending_say:
                tg.send("".join(self.brain.say_text(self.brain.pending_say)))
        self.turn_log.done(text, self.brain.last_actions, [], answer, None, "telegram")
        self.log(f"{config.NAME} → Telegram")


def start(log=print, brain=None, transcriber=None, events=None) -> TelegramService | None:
    """Поднять Telegram-бота. Если сейчас его держит другой процесс — сторож подхватит, когда освободится.
    events — очередь главного цикла голосовой Мартышки (тогда сообщения обрабатываются там, через handle)."""
    if not config.TELEGRAM_TOKEN.strip():
        return None
    svc = TelegramService(log=log, brain=brain, transcriber=transcriber, events=events)
    svc.start()   # пробуем сразу
    threading.Thread(target=svc.watchdog_loop, daemon=True, name="telegram-watchdog").start()
    return svc
