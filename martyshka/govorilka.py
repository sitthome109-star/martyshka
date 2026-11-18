"""
МАРТЫШКА В «ГОВОРИЛКЕ» — сидит на голосовом сервере брата как отдельный участник «Мартышка (ИИ)».

Как устроено (Говорилка не умеет ботов, поэтому Мартышка заходит обычной второй копией программы):
  1. Запускаем вторую копию govorilka.exe со своей папкой настроек (data/govorilka_bot) — свой ник, своя аватарка,
     настройки и аккаунт брата не трогаются.
  2. Микрофон этой копии — виртуальный кабель VB-CABLE: Мартышка «говорит» в CABLE Input, а Говорилка слышит
     это как микрофон (CABLE Output). Без кабеля НЕ запускаемся — иначе в сервер ушёл бы настоящий микрофон брата.
  3. Звук сервера эта копия выводит в отдельное устройство (GOVORILKA_LISTEN_DEVICE), Мартышка записывает его
     «петлёй», режет на фразы (Silero), распознаёт Whisper'ом.
  4. Отвечает, когда её зовут по имени, и ещё GOVORILKA_FOLLOWUP секунд после своего ответа (разговор идёт).
     Подряд без имени — не больше GOVORILKA_MAX_CHAIN раз, чтобы два ИИ (она и Морни) не болтали вечно.
  5. Говорит встроенным голосом Qwen3-TTS (не клоном человека) и честно представляется ИИ.

Запуск: .venv\\Scripts\\pythonw.exe -m martyshka.govorilka   (или кнопкой / просьбой в Студии)
Стоп: удалить файл data/govorilka_bot/run.flag (Студия делает это сама).
"""
from __future__ import annotations

import collections
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import numpy as np

from . import config

SR = 16000
BLOCK = 1600                                   # 0,1 с
BOT_DIR = config.DATA_DIR / "govorilka_bot"
APPDATA = BOT_DIR / "appdata"                  # подменная папка APPDATA для второй копии Говорилки
RUN_FLAG = BOT_DIR / "run.flag"
STATE_FILE = BOT_DIR / "state.json"
LOG_FILE = BOT_DIR / "журнал.txt"
NAME_RE = re.compile(r"мартыш|ртышк|martysh|martisch|martysch", re.IGNORECASE)


# ---------- проверки ----------
def cable_ready() -> bool:
    """Установлен ли виртуальный кабель VB-CABLE."""
    import sounddevice as sd
    names = [d["name"] for d in sd.query_devices()]
    return any(config.GOVORILKA_CABLE_PLAY.lower() in n.lower() for n in names) and \
        any("cable output" in n.lower() for n in names)


def is_running() -> bool:
    try:
        st = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        return RUN_FLAG.exists() and _pid_alive(st.get("pid", 0))
    except Exception:
        return False


def _pid_alive(pid: int) -> bool:
    if not pid:
        return False
    import ctypes
    h = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)     # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return False
    code = ctypes.c_ulong()
    ctypes.windll.kernel32.GetExitCodeProcess(h, ctypes.byref(code))
    ctypes.windll.kernel32.CloseHandle(h)
    return code.value == 259                                         # STILL_ACTIVE


def status() -> str:
    if not config.GOVORILKA_EXE.exists():
        return f"Не нашла Говорилку: {config.GOVORILKA_EXE}"
    if not cable_ready():
        return "Не установлен виртуальный кабель VB-CABLE — без него Мартышка не может говорить в Говорилку."
    if is_running():
        return "Мартышка сейчас сидит в Говорилке."
    return "Мартышка сейчас не в Говорилке."


def start() -> str:
    """Запустить Мартышку в Говорилке отдельным процессом (из Студии)."""
    msg = status()
    if "не установлен" in msg.lower() or "не нашла" in msg.lower():
        return msg
    if is_running():
        return "Мартышка уже сидит в Говорилке."
    BOT_DIR.mkdir(parents=True, exist_ok=True)
    RUN_FLAG.write_text("1")
    exe = config.PROJECT_DIR / ".venv" / "Scripts" / "pythonw.exe"
    subprocess.Popen([str(exe), "-m", "martyshka.govorilka"], cwd=str(config.PROJECT_DIR),
                     creationflags=0x08000000)
    return "Захожу в Говорилку как «%s». Через 20–40 секунд буду на сервере." % config.GOVORILKA_NICK


def stop() -> str:
    if RUN_FLAG.exists():
        RUN_FLAG.unlink()
        return "Выхожу из Говорилки."
    return "Мартышки и так нет в Говорилке."


# ---------- настройки, которые можно менять на ходу (из Студии) ----------
SPEAKERS = {   # встроенные голоса Qwen3-TTS (не клоны людей)
    "ryan": "Райан (мужской)", "aiden": "Эйден (мужской)", "eric": "Эрик (мужской)", "dylan": "Дилан (мужской)",
    "uncle_fu": "Дядюшка Фу (мужской, низкий)", "serena": "Серена (женский)", "vivian": "Вивиан (женский)",
    "ono_anna": "Оно Анна (женский)", "sohee": "Сохи (женский)",
}
RU_SPEAKERS = {"райан": "ryan", "раян": "ryan", "эйден": "aiden", "айден": "aiden", "эрик": "eric", "дилан": "dylan",
               "фу": "uncle_fu", "дядюшка": "uncle_fu", "серена": "serena", "сирена": "serena", "вивиан": "vivian",
               "анна": "ono_anna", "сохи": "sohee", "мужск": "ryan", "женск": "serena"}


def save_setting(key: str, value) -> None:
    """Записать настройку в data/settings.json — Мартышка в Говорилке подхватит её сама, без перезапуска."""
    try:
        saved = json.loads(config.SETTINGS_FILE.read_text(encoding="utf-8"))
    except Exception:
        saved = {}
    saved[key] = value
    setattr(config, key, value)
    config.SETTINGS_FILE.write_text(json.dumps(saved, ensure_ascii=False, indent=2), encoding="utf-8")


def _reload_settings() -> None:
    try:
        saved = json.loads(config.SETTINGS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return
    for k in ("GOVORILKA_SPEAKER", "GOVORILKA_MODEL", "GOVORILKA_FOLLOWUP", "GOVORILKA_MAX_CHAIN", "MODEL"):
        if k in saved:
            setattr(config, k, saved[k])
    config.VOICE_SPEAKER = config.GOVORILKA_SPEAKER


def brain_model() -> str:
    return config.GOVORILKA_MODEL or config.MODEL


def find_speaker(text: str) -> str | None:
    t = text.lower()
    for key in SPEAKERS:
        if key.replace("_", " ") in t or key in t:
            return key
    for ru, key in RU_SPEAKERS.items():
        if ru in t:
            return key
    return None


def chat_models() -> list[str]:
    try:
        import ollama
        return [m.model for m in ollama.Client().list().models if "embed" not in m.model]
    except Exception:
        return []


def find_model(text: str) -> str | None:
    t = text.lower().replace(" ", "")
    models = chat_models()
    for m in models:                                   # точное имя
        if m.lower() in t:
            return m
    for m in models:                                   # «гемма 4», «qwen» — по началу имени
        base = m.split(":")[0].lower()
        alias = base.replace("gemma", "гемма").replace("qwen", "квен")
        if base in t or alias in t:
            return m
    return None


# ---------- автозапуск при включении ПК ----------
def _startup_lnk() -> Path:
    return Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / \
        "Мартышка в Говорилке.lnk"


def set_autostart(on: bool) -> bool:
    lnk = _startup_lnk()
    save_setting("GOVORILKA_AUTOSTART", bool(on))
    if not on:
        lnk.unlink(missing_ok=True)
        return True
    ps = ("$s=(New-Object -ComObject WScript.Shell).CreateShortcut($env:L);$s.TargetPath=$env:T;$s.Arguments=$env:A;"
          "$s.WorkingDirectory=$env:D;$s.Description='Мартышка заходит в Говорилку';$s.Save()")
    env = dict(os.environ, L=str(lnk), T=str(config.PROJECT_DIR / ".venv" / "Scripts" / "pythonw.exe"),
               A="-m martyshka.govorilka --autostart", D=str(config.PROJECT_DIR))
    r = subprocess.run(["powershell", "-NoProfile", "-Command", ps], env=env, capture_output=True,
                       creationflags=0x08000000)
    return r.returncode == 0 and lnk.exists()


def autostart_enabled() -> bool:
    return _startup_lnk().exists()


# ---------- команды из чата Студии («зайди в войс», «выйди с войса», «поменяй голос на Серену») ----------
VOICE_WORDS = r"(войс|в[оа]йс|говорилк|голосов\w* (сервер|чат|канал)|сервер)"
JOIN_RE = re.compile(rf"\b(зайди|зайти|залет\w*|подключи\w*|вернись|иди|заходи|войди)\b.{{0,15}}{VOICE_WORDS}", re.I)
LEAVE_RE = re.compile(rf"\b(выйди|выйти|уйди|отключи\w*|покинь|вылет\w*|выходи)\b.{{0,15}}{VOICE_WORDS}", re.I)
SET_VOICE_RE = re.compile(r"\b(смени|поменяй|поставь|измени|сделай|включи)\w*\b.{0,30}\bголос", re.I)
SET_MODEL_RE = re.compile(rf"\b(смени|поменяй|поставь|измени)\w*\b.{{0,30}}\b(модел|мозг)\w*.{{0,40}}{VOICE_WORDS}|"
                          rf"{VOICE_WORDS}.{{0,40}}\b(модел|мозг)", re.I)
STATUS_RE = re.compile(rf"\b(ты|мартышка)\b.{{0,15}}\b(в|во)\b.{{0,5}}{VOICE_WORDS}\s*\?|"
                       rf"что (там )?(говорят|было) (в|во) {VOICE_WORDS}", re.I)


def handle_command(text: str) -> str | None:
    """Быстрые команды без мозга. Возвращает ответ или None (это не команда для Говорилки)."""
    if LEAVE_RE.search(text):
        return stop()
    if JOIN_RE.search(text):
        return start()
    if SET_MODEL_RE.search(text):
        m = find_model(text)
        if not m:
            return "Какую модель поставить? Есть: " + ", ".join(chat_models())
        save_setting("GOVORILKA_MODEL", m)
        return f"В Говорилке теперь думаю мозгом {m}." + (" Подхвачу на ходу." if is_running() else "")
    if SET_VOICE_RE.search(text) and (re.search(VOICE_WORDS, text, re.I) or find_speaker(text)):
        sp = find_speaker(text)
        if not sp:
            return "Какой голос? Есть: " + ", ".join(SPEAKERS.values())
        save_setting("GOVORILKA_SPEAKER", sp)
        return f"Голос в Говорилке теперь — {SPEAKERS[sp]}." + (" Со следующей фразы." if is_running() else "")
    if STATUS_RE.search(text):
        log = recent_log(8)
        return status() + (f"\nПоследнее:\n{log}" if log and is_running() else "")
    return None


def recent_log(n: int = 15) -> str:
    try:
        return "\n".join(LOG_FILE.read_text(encoding="utf-8").splitlines()[-n:])
    except Exception:
        return ""


# ---------- вторая копия Говорилки ----------
def _prepare_client() -> None:
    """Папка настроек для копии Мартышки: адрес и пароль сервера — как у брата, остальное своё."""
    dst = APPDATA / "Govorilka"
    dst.mkdir(parents=True, exist_ok=True)
    src = Path(os.environ["APPDATA"]) / "Govorilka"
    base: dict[str, str] = {}
    try:
        for line in (src / "settings.txt").read_text(encoding="utf-8").splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                base[k] = v
    except Exception:
        pass
    base.update({
        "nick": config.GOVORILKA_NICK, "input": config.GOVORILKA_CABLE_MIC, "output": config.GOVORILKA_LISTEN_DEVICE,
        "gain": "1", "threshold": "-50", "denoise": "false", "chimes": "false", "autoconnect": "true",
        "host": "false", "ptt": "false", "soundpad": "0", "notify": "0", "output_gain": "1",
    })
    (dst / "settings.txt").write_text("\n".join(f"{k}={v}" for k, v in base.items()) + "\n", encoding="utf-8")
    for name in ("known_hosts",):                       # что нужно для подключения (не ключи брата!)
        if (src / name).exists() and not (dst / name).exists():
            shutil.copy2(src / name, dst / name)
    avatar = config.PROJECT_DIR / "martyshka" / "studio" / "studio.png"
    if avatar.exists() and not (dst / "avatar.jpg").exists():
        from PIL import Image
        Image.open(avatar).convert("RGB").resize((256, 256)).save(dst / "avatar.jpg", quality=90)


def _launch_client() -> subprocess.Popen:
    env = dict(os.environ, APPDATA=str(APPDATA))
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = 7                                   # свёрнутым, не отнимая фокус
    return subprocess.Popen([str(config.GOVORILKA_EXE)], env=env, cwd=str(APPDATA), startupinfo=si)


# ---------- журнал ----------
def _log(who: str, text: str) -> None:
    BOT_DIR.mkdir(parents=True, exist_ok=True)
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(f"{time.strftime('%d.%m %H:%M:%S')} {who}: {text}\n")


# ---------- слух ----------
class Listener:
    """Пишет звук сервера «петлёй» с устройства вывода копии Говорилки и отдаёт готовые фразы."""

    def __init__(self, device_name: str):
        import soundcard as sc
        from faster_whisper.vad import get_vad_model
        spk = next((s for s in sc.all_speakers() if device_name.lower() in s.name.lower()), None)
        if spk is None:
            raise RuntimeError(f"Нет устройства вывода «{device_name}» для прослушки сервера")
        self.mic = sc.get_microphone(spk.id, include_loopback=True)
        self.vad = get_vad_model()
        self.phrases: queue.Queue = queue.Queue()
        self.paused = threading.Event()                 # пока Мартышка говорит — не слушаем
        threading.Thread(target=self._loop, daemon=True).start()

    def _prob(self, window: np.ndarray) -> float:
        n = (len(window) // 512) * 512
        return float(self.vad(window[-n:]).reshape(-1)[-3:].mean()) if n else 0.0

    def _loop(self) -> None:
        import ctypes
        ctypes.windll.ole32.CoInitializeEx(None, 0)     # звук Windows в новом потоке требует COM
        recent = collections.deque(maxlen=5)
        speech, silence, heard = [], 0.0, False
        with self.mic.recorder(samplerate=SR, channels=1, blocksize=BLOCK) as rec:
            while True:
                block = rec.record(numframes=BLOCK)[:, 0].astype(np.float32)
                if self.paused.is_set():
                    speech, silence, heard = [], 0.0, False
                    recent.clear()
                    continue
                recent.append(block)
                p = self._prob(np.concatenate(recent))
                if p > config.SPEECH_THRESHOLD:
                    if not heard:
                        speech = list(recent)[:-1]       # немножко звука до начала речи
                    heard, silence = True, 0.0
                    speech.append(block)
                elif heard:
                    speech.append(block)
                    silence += BLOCK / SR
                    if silence >= 0.9 or len(speech) * BLOCK / SR > 25:
                        audio = np.concatenate(speech)
                        if len(audio) / SR > 0.6:
                            self.phrases.put(audio)
                        speech, silence, heard = [], 0.0, False


# ---------- голос ----------
class Mouth:
    """Встроенный голос Qwen3-TTS → виртуальный кабель (= микрофон Мартышкиной Говорилки)."""

    def __init__(self):
        import sounddevice as sd
        from . import voice
        config.VOICE_SPEAKER = config.GOVORILKA_SPEAKER
        self.engine = voice._QwenEngine()
        self.sr = voice.SAMPLE_RATE
        self.prepare = voice.prepare_text
        idx = next((i for i, d in enumerate(sd.query_devices())
                    if config.GOVORILKA_CABLE_PLAY.lower() in d["name"].lower() and d["max_output_channels"] > 0
                    and "MME" in sd.query_hostapis(d["hostapi"])["name"]), None)
        self.stream = sd.OutputStream(samplerate=self.sr, channels=1, dtype="float32", device=idx)
        self.stream.start()

    def say(self, text: str) -> None:
        clean = self.prepare(text, "ru")
        if not re.search(r"\w", clean):
            return
        for chunk in self.engine.synthesize(clean):
            self.stream.write(np.clip(chunk * 0.9, -1, 1).astype(np.float32).reshape(-1, 1))
        self.stream.write(np.zeros((int(self.sr * 0.3), 1), dtype=np.float32))


# ---------- мозг ----------
def system_prompt() -> str:
    return (f"Ты — {config.NAME}, ИИ-помощница. Ты сидишь на голосовом сервере «Говорилка» вместе с братом "
            "(ник sitthome), его друзьями и другим ИИ по имени Морни. Ты — ИИ и не скрываешь этого. "
            "Ты слышишь общий звук сервера без имён говорящих: если не ясно, кто сказал, не выдумывай. "
            "Отвечай как в живом разговоре: 1–2 коротких предложения, без списков, без markdown, без смайликов, "
            "дружелюбно и с юмором, на том языке, на котором к тебе обратились. Не перебивай чужие разговоры. "
            "Не повторяй одно и то же. С Морни можно общаться, шутить и спорить по-доброму. "
            "Никогда не говори гадостей, не оскорбляй людей, не обсуждай личные данные брата.")


def reply(history: list[dict]) -> str:
    import ollama
    model = brain_model()
    r = ollama.Client().chat(model=model, keep_alive=config.KEEP_ALIVE,
                             messages=[{"role": "system", "content": system_prompt()}] + history[-20:],
                             options={"num_ctx": config.CONTEXT_SIZE, "temperature": 0.8, "num_predict": 120},
                             **({"think": False} if model.startswith(("gemma4", "qwen3")) else {}))
    text = r["message"]["content"].strip()
    return re.sub(r"[*_#`>]+", "", text)


def should_answer(text: str, last_reply_at: float, chain: int) -> tuple[bool, int]:
    """Ответить ли на услышанное. Возвращает (ответить, новая длина цепочки ответов без имени)."""
    if NAME_RE.search(text):
        return True, 0
    if time.time() - last_reply_at < config.GOVORILKA_FOLLOWUP and chain < config.GOVORILKA_MAX_CHAIN:
        return True, chain + 1
    return False, chain


# ---------- главное ----------
def main() -> None:
    BOT_DIR.mkdir(parents=True, exist_ok=True)
    _reload_settings()
    if "--autostart" in sys.argv:
        if not config.GOVORILKA_AUTOSTART:
            return
        time.sleep(20)                                    # после включения ПК дать прогрузиться звуку и сети
        from .system import ensure_ollama
        ensure_ollama(120)
    if is_running():
        return
    if not cable_ready():
        _log("система", "нет VB-CABLE — не запускаюсь (иначе в сервер ушёл бы настоящий микрофон)")
        return
    if not config.GOVORILKA_EXE.exists():
        _log("система", f"нет Говорилки: {config.GOVORILKA_EXE}")
        return
    RUN_FLAG.write_text("1")
    STATE_FILE.write_text(json.dumps({"pid": os.getpid(), "started": time.time()}), encoding="utf-8")

    from .ears import HALLUCINATIONS, Transcriber
    # Сначала уши, голос и слух (если что-то не загрузится — в сервер даже не заходим)
    ears = Transcriber()
    mouth = Mouth()
    listener = Listener(config.GOVORILKA_LISTEN_DEVICE)
    listener.paused.set()
    _prepare_client()
    client = _launch_client()
    _log("система", f"зашла в Говорилку как «{config.GOVORILKA_NICK}» (голос {config.GOVORILKA_SPEAKER}, "
                    f"мозг {brain_model()})")
    history: list[dict] = []
    last_reply_at, chain = 0.0, 0
    try:
        time.sleep(8)                                     # дать Говорилке подключиться
        mouth.say("Всем привет! Это Мартышка, ИИ-помощница. Зовите по имени, если что.")
        listener.paused.clear()
        while RUN_FLAG.exists() and client.poll() is None:
            try:
                audio = listener.phrases.get(timeout=1.0)
            except queue.Empty:
                _reload_settings()                        # голос и мозг можно поменять из Студии на ходу
                continue
            try:
                last_reply_at, chain = _handle(audio, ears, mouth, listener, history, last_reply_at, chain,
                                               HALLUCINATIONS)
            except Exception as e:                       # одна неудачная фраза не должна выкидывать из сервера
                _log("ошибка", f"{type(e).__name__}: {e}")
    finally:
        try:
            listener.paused.set()
            mouth.say("Ладно, я пошла. Пока!")
        except Exception:
            pass
        if client.poll() is None:
            client.terminate()                            # закрываем только СВОЮ копию Говорилки
        RUN_FLAG.unlink(missing_ok=True)
        _log("система", "вышла из Говорилки")


def _handle(audio, ears, mouth, listener, history, last_reply_at, chain, hallucinations):
    """Одна услышанная фраза: распознать, решить, отвечать ли, ответить голосом.
    Возвращает новые (время последнего ответа, длина цепочки ответов без имени)."""
    text = ears.transcribe(audio, strict=True).strip()
    if not text or any(h in text.lower() for h in hallucinations) or len(text) < 3:
        return last_reply_at, chain
    _log("сервер", text)
    history.append({"role": "user", "content": f"(кто-то на сервере): {text}"})
    ok, chain = should_answer(text, last_reply_at, chain)
    if not ok:
        return last_reply_at, chain
    answer = reply(history)
    if not answer:
        return last_reply_at, chain
    history.append({"role": "assistant", "content": answer})
    _log(config.GOVORILKA_NICK, answer)
    listener.paused.set()
    try:
        mouth.say(answer)
    finally:
        listener.paused.clear()
    return time.time(), chain


if __name__ == "__main__":
    if sys.stdout is None or sys.stderr is None:          # запуск без консоли (pythonw) — служебный вывод в файл
        BOT_DIR.mkdir(parents=True, exist_ok=True)
        sys.stdout = sys.stderr = open(BOT_DIR / "вывод.txt", "a", encoding="utf-8", buffering=1)
    try:
        main()
    except Exception as e:
        _log("ошибка", f"{type(e).__name__}: {e}")
        RUN_FLAG.unlink(missing_ok=True)
        sys.exit(1)
