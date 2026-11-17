"""
TELEGRAM: пиши Мартышке с телефона, пока комп включён.

Бот слушается только хозяина. Привязка: хозяин один раз отправляет боту код из окна настроек.
Сообщения кладутся в общую очередь главной программы — Мартышка обрабатывает их по очереди с голосом.
"""
import json
import queue
import random
import threading
import time

from . import config, style

API = "https://api.telegram.org/bot{token}/{method}"
CODE_FILE = config.DATA_DIR / "telegram_код.txt"

# Меню бота (кнопка «Меню» в Telegram): команда латиницей (так требует Telegram) → (подпись, что это значит)
MENU = {
    "svodka": ("Сводка: погода, напоминания, заряд", "расскажи сводку"),
    "pogoda": ("Погода дома", "какая погода?"),
    "zaryad": ("Заряд мышки и наушников", "сколько заряда у мышки и наушников?"),
    "sistema": ("Как там комп: процессор, видеокарта, диск", "как там комп? загрузка и температура"),
    "zametki": ("Мои заметки", "прочитай мои заметки"),
    "skrinshot": ("Сделать скриншот экрана", "сделай скриншот"),
    "otmena": ("Отменить выключение компа", "отмени выключение компа"),
}


def pair_code() -> str:
    """Код привязки (6 цифр). Создаётся один раз."""
    try:
        code = CODE_FILE.read_text(encoding="utf-8").strip()
        if code:
            return code
    except Exception:
        pass
    code = f"{random.SystemRandom().randint(0, 999999):06d}"
    CODE_FILE.parent.mkdir(parents=True, exist_ok=True)
    CODE_FILE.write_text(code, encoding="utf-8")
    return code


def _save_owner(owner_id: int) -> None:
    try:
        data = json.loads(config.SETTINGS_FILE.read_text(encoding="utf-8"))
    except Exception:
        data = {}
    data["TELEGRAM_OWNER_ID"] = owner_id
    config.SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    config.SETTINGS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


class TelegramBot:
    def __init__(self, events: queue.Queue, log=print):
        import httpx
        self.token = config.TELEGRAM_TOKEN.strip()
        self.owner = int(config.TELEGRAM_OWNER_ID or 0)
        self.events = events            # сюда кладём ("telegram", {"text"| "voice": ...})
        self.log = log
        self.http = httpx.Client(timeout=40)
        self.answers: queue.Queue = queue.Queue()   # ответы на «точно?» (подтверждение опасных дел)
        self.waiting_answer = threading.Event()
        self.offset = 0

    # --- запросы ---
    def _call(self, method: str, **params):
        r = self.http.post(API.format(token=self.token, method=method), json=params)
        return r.json()

    def send(self, text: str) -> None:
        if not self.owner or not text.strip():
            return
        for i in range(0, len(text), 4000):        # у Telegram предел ~4096 знаков
            try:
                self._call("sendMessage", chat_id=self.owner, text=text[i:i + 4000])
            except Exception as e:
                self.log(f"   (Telegram: не отправилось: {e})")

    def send_photo(self, path) -> None:
        """Прислать картинку (скриншот экрана) на телефон."""
        try:
            with open(path, "rb") as f:
                self.http.post(API.format(token=self.token, method="sendPhoto"),
                               data={"chat_id": self.owner}, files={"photo": f})
        except Exception as e:
            self.log(f"   (Telegram: картинка не отправилась: {e})")

    def send_document(self, path) -> None:
        """Прислать файл (готовый документ Word) на телефон."""
        try:
            with open(path, "rb") as f:
                self.http.post(API.format(token=self.token, method="sendDocument"),
                               data={"chat_id": self.owner}, files={"document": (path.name, f)})
        except Exception as e:
            self.log(f"   (Telegram: файл не отправился: {e})")

    def typing(self) -> None:
        try:
            self._call("sendChatAction", chat_id=self.owner, action="typing")
        except Exception:
            pass

    def download_voice(self, file_id: str) -> bytes | None:
        try:
            path = self._call("getFile", file_id=file_id)["result"]["file_path"]
            return self.http.get(f"https://api.telegram.org/file/bot{self.token}/{path}").content
        except Exception:
            return None

    def confirm(self, question: str, timeout: float = 90) -> bool:
        """Опасное дело (выключить комп…): спросить в Telegram и дождаться «да»."""
        from .hands import is_yes
        while not self.answers.empty():
            self.answers.get_nowait()
        self.waiting_answer.set()
        self.send(question + " (да / нет)")
        try:
            return is_yes(self.answers.get(timeout=timeout))
        except queue.Empty:
            self.send("Не дождался ответа — отбой.")
            return False
        finally:
            self.waiting_answer.clear()

    # --- приём сообщений ---
    def start(self) -> bool:
        if not self.token:
            return False
        me = {}
        try:
            me = self._call("getMe")
            if not me.get("ok"):
                self.log("   (Telegram: ключ бота не подходит — проверь его в настройках)")
                return False
            self.log(f"📱 Telegram: бот @{me['result'].get('username')} на связи"
                     + ("" if self.owner else f" — отправь ему код {pair_code()}, чтобы привязать телефон"))
        except Exception as e:
            self.log(f"   (Telegram: нет связи: {e})")
        threading.Thread(target=self._dress_up, args=(me.get("result", {}),), daemon=True).start()
        threading.Thread(target=self._loop, daemon=True).start()
        if self.owner:
            self.send(style.phrase("tg_online"))
        return True

    def _dress_up(self, me: dict) -> None:
        """Русское имя, описание и меню кнопок бота (один раз для каждого бота)."""
        done = config.DATA_DIR / "telegram_оформлен.txt"
        bot_id = str(me.get("id", ""))
        try:
            if done.read_text(encoding="utf-8").strip() == bot_id:
                return
        except Exception:
            pass
        try:
            self._call("setMyName", name="Мартышка")
            self._call("setMyShortDescription",
                       short_description="Мартышка — твой домашний помощник на компе. Пиши, что надо.")
            self._call("setMyDescription", description=(
                "Это Мартышка — голосовой помощник с твоего компьютера.\n\n"
                "Пиши как другу: «сколько заряда у мышки», «какая погода», «напомни в 18:00 купить хлеб», "
                "«выключи комп через час». Можно голосовыми.\n\n"
                "Работает, пока компьютер включён. Слушается только хозяина."))
            self._call("setMyCommands", commands=[{"command": c, "description": d} for c, (d, _t) in MENU.items()])
            done.parent.mkdir(parents=True, exist_ok=True)
            done.write_text(bot_id, encoding="utf-8")
        except Exception:
            pass

    def _loop(self) -> None:
        while True:
            try:
                r = self._call("getUpdates", offset=self.offset, timeout=30, allowed_updates=["message"])
                for upd in r.get("result", []):
                    self.offset = upd["update_id"] + 1
                    self._handle(upd.get("message") or {})
            except Exception:
                time.sleep(10)   # нет интернета — ждём и пробуем снова

    def _handle(self, msg: dict) -> None:
        user = (msg.get("from") or {}).get("id")
        chat = (msg.get("chat") or {}).get("id")
        text = (msg.get("text") or "").strip()
        if not user or chat != user:          # только личная переписка
            return
        if not self.owner:
            if text.replace("/start", "").strip() == pair_code():
                self.owner = user
                _save_owner(user)
                self.log(f"📱 Telegram привязан (id {user})")
                self.send(style.phrase("tg_paired"))
            elif text.startswith("/start"):
                self._call("sendMessage", chat_id=chat, text="Отправь код привязки из настроек Мартышки.")
            return
        if user != self.owner:                # чужим не отвечаем вообще
            return
        if text == "/start":
            self.send(style.phrase("tg_start"))
            return
        cmd = text.split("@")[0].lstrip("/") if text.startswith("/") else ""
        if cmd in MENU:                       # кнопка из меню → обычная русская просьба
            text = MENU[cmd][1]
        if self.waiting_answer.is_set() and text:
            self.answers.put(text)
            return
        if text:
            self.events.put(("telegram", {"text": text}))
        elif msg.get("voice"):
            self.events.put(("telegram", {"voice": msg["voice"]["file_id"]}))
        else:
            self.send("Я понимаю текст и голосовые. Картинки и файлы пока не умею.")
