"""
МАРТЫШКА В «ГОВОРИЛКЕ» — сидит на голосовом сервере брата как отдельный участник «Мартышка (ИИ)».

Как устроено (Говорилка не умеет ботов, поэтому Мартышка заходит обычной второй копией программы):
  1. Запускаем вторую копию govorilka.exe со своей папкой настроек (data/govorilka_bot) — свой ник, своя аватарка,
     настройки и аккаунт брата не трогаются.
  2. Микрофон этой копии — виртуальный кабель VB-CABLE: Мартышка «говорит» в CABLE Input, а Говорилка слышит
     это как микрофон (CABLE Output). Без кабеля НЕ запускаемся — иначе в сервер ушёл бы настоящий микрофон брата.
  3. Звук сервера эта копия выводит в отдельное устройство (GOVORILKA_LISTEN_DEVICE), Мартышка записывает его
     «петлёй», режет на фразы (Silero), распознаёт DeepSeek (в облаке).
  4. Откликается ТОЛЬКО на своё имя (NAME_RE) — в чужие разговоры не лезет. После обращения — «режим диалога»:
     тому же собеседнику отвечает и без имени ещё GOVORILKA_FOLLOWUP секунд, но не больше GOVORILKA_MAX_CHAIN
     реплик подряд (чтобы два ИИ — она и Морни — не болтали вечно). И главное: собеседника СЛЫШИТ ДО КОНЦА —
     все куски фразы склеиваются, ответ только после настоящей тишины (QUIET_SENTENCE / QUIET_UNFINISHED).
  5. Думает DeepSeek, говорит голосом Fish Audio (выбирается в настройках), ник — «Мартышка (ИИ)».
  6. Характер нарастающий: как с ней говорят, так и она — оскорбляют сильнее, отвечает жёстче; извинились — остывает.
  7. Помнит всех и всё (долгая память longmem): кто что говорил вчера, кто с ней ругался.
  8. Сидит всегда: Говорилка закрылась или вылетела — открывает снова; голосовая Мартышка следит, что бот жив.

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
NAME_RE = re.compile(r"мартыш|мартиш|ртышк|martysh|martish|martisch|martysch", re.IGNORECASE)

# ---------- поведение в разговоре (брат: «откликается на имя и слышит собеседника до конца») ----------
NAME_ONLY = True          # откликаться ТОЛЬКО на своё имя — в чужие разговоры не лезть
YIELD_AFTER = 0.6         # с: режим диалога — кто-то говорит столько поверх Мартышки → она замолкает и уступает
ECHO = {"heard_self": False}  # самопроверка при входе: если в «петле» слышен её же голос — не уступаем (было бы эхо)
STICKY_DIALOG = True      # режим диалога: назвали по имени — дальше отвечаем ТОМУ ЖЕ собеседнику и без имени,
                          # пока не истекло config.GOVORILKA_FOLLOWUP и не исчерпана цепочка ответов
CHIME_IN = False          # злая Мартышка сама влезает в чужие разговоры (по просьбе брата выключено)
SPEECH_GAP = 0.9          # с: пауза в речи, после которой кусок считаем законченным (нарезка на фразы)
QUIET_SENTENCE = 2.0      # с: столько тишины после ЗАКОНЧЕННОЙ фразы — и только тогда отвечаем (брат: 2 с)
QUIET_UNFINISHED = 3.5    # с: столько тишины после оборванной фразы (ждём, что человек ещё договорит)
PRETHINK_AFTER = 0.6      # с: фраза похожа на законченную — начинаем думать над ответом уже в паузе
JOIN_GRACE = 4.0          # с: после входа человека в войс его подсветку не считаем речью
QUIET_MORNI = 4.5         # с: после фраз Морни — она говорит длинно и с паузами между предложениями
MORNI_NAMES = ("морни", "мурни", "morni")
TALKING_NOW = lambda: False   # noqa: E731 — main подставляет «кто-то светится в окне Говорилки прямо сейчас»
QUIET_FORGET = 20.0       # с: фраза так и не к нам — забываем, чтобы не приклеилась к следующей


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
        try:
            voice = json.loads(STATE_FILE.read_text(encoding="utf-8")).get("in_voice")
        except Exception:
            voice = None
        return "Мартышка на сервере " + ("и сидит в войсе." if voice else "(не в войсе)." if voice is False
                                         else "— захожу в войс…")
    return "Мартышки сейчас нет в Говорилке."


WANT_VOICE = BOT_DIR / "в_голосе.txt"          # «1» — сидеть в голосовой комнате, «0» — только на сервере


def want_voice() -> bool:
    try:
        return WANT_VOICE.read_text().strip() != "0"
    except Exception:
        return True


def start() -> str:
    """«Зайди в войс»: если Мартышки нет на сервере — запустить её; потом она сама войдёт в голос."""
    msg = status()
    if "не установлен" in msg.lower() or "не нашла" in msg.lower():
        return msg
    BOT_DIR.mkdir(parents=True, exist_ok=True)
    WANT_VOICE.write_text("1")
    LEFT_FLAG.unlink(missing_ok=True)
    if is_running():
        return "Захожу в войс — через пару секунд буду в голосовой комнате."
    RUN_FLAG.write_text("1")
    exe = config.PROJECT_DIR / ".venv" / "Scripts" / "pythonw.exe"
    subprocess.Popen([str(exe), "-m", "martyshka.govorilka"], cwd=str(config.PROJECT_DIR),
                     creationflags=0x08000000)
    return f"Захожу в Говорилку как «{config.GOVORILKA_NICK}» и сразу в войс — через 30–60 секунд буду."


def stop() -> str:
    """«Выйди с войса»: выйти из голосовой комнаты, но остаться на сервере."""
    BOT_DIR.mkdir(parents=True, exist_ok=True)
    WANT_VOICE.write_text("0")
    return "Выхожу с войса (на сервере остаюсь)." if is_running() else "Меня и так нет в войсе."


MORNI_NOW_FLAG = BOT_DIR / "начать_с_морни.flag"   # кнопка в окне: «начни разговор с Морни сейчас»
LEFT_FLAG = BOT_DIR / "ушла.txt"                 # брат сам сказал «уйди с сервера совсем» — сторож не возвращает


def quit_server() -> str:
    """Совсем уйти с сервера и закрыть свою копию Говорилки."""
    BOT_DIR.mkdir(parents=True, exist_ok=True)
    LEFT_FLAG.write_text("1")
    if RUN_FLAG.exists():
        RUN_FLAG.unlink()
        return "Ухожу с сервера совсем."
    return "Меня и так нет на сервере."


# ---------- настройки, которые можно менять на ходу (из Студии) ----------
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
    for k in ("GOVORILKA_VOICE_ID", "VOICE_ID", "GOVORILKA_MODEL", "GOVORILKA_FOLLOWUP", "GOVORILKA_MAX_CHAIN",
              "BRAIN_MODE", "DEEPSEEK_KEY", "FISH_KEY", "FISH_MODEL", "GOVORILKA_DIALOG", "GOVORILKA_FISH_MODEL",
              "MOOD_VOICE", "MOOD_ANGRY_FROM", "ANGRY_VOICE_ID", "GOVORILKA_GREET", "GOVORILKA_CHIME",
              "GOVORILKA_PRETHINK", "ECONOMY", "ECONOMY_BALANCE"):
        if k in saved:
            setattr(config, k, saved[k])


def fish_model() -> str:
    """Модель озвучки в Говорилке: своя (настройка на странице Говорилки) или как у голоса."""
    return (getattr(config, "GOVORILKA_FISH_MODEL", "") or getattr(config, "FISH_MODEL", "") or "s2.1-pro-free").strip()


def brain_model() -> str:
    """В войсе важна скорость — по умолчанию быстрый flash. Брат может поставить pro."""
    from . import deepseek
    m = (config.GOVORILKA_MODEL or "").strip()
    return deepseek.normalize(m) if m else deepseek.FLASH


def voice_id() -> str:
    from . import fish
    return config.GOVORILKA_VOICE_ID or config.VOICE_ID or fish.DEFAULT_VOICE


def find_voice(text: str) -> dict | None:
    """«поставь голос Кира» → голос из каталога Fish (по названию)."""
    from . import fish
    t = text.lower()
    best = None
    for v in fish.catalog():
        name = v["title"].lower()
        words = [w for w in re.findall(r"\w{3,}", name)]
        if name and (name in t or any(w in t for w in words)):
            if best is None or v.get("popular", 0) > best.get("popular", 0) or v.get("official"):
                best = v
    return best


def find_model(text: str) -> str | None:
    t = text.lower()
    if re.search(r"\bpro\b|про\b|сильн|умн", t):
        return "deepseek-v4-pro"
    if re.search(r"flash|флеш|флэш|быстр|лёгк|легк", t):
        return "deepseek-flash"
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


QUIT_RE = re.compile(rf"(уйди|выйди|отключись)\w*\s+(совсем|полностью)|(выключи|закрой)\w*.{{0,10}}{VOICE_WORDS}|"
                     r"(уйди|выйди)\w*\s+(с|из)\s+сервер", re.I)


EVIL_RE = re.compile(r"\b(сделай|стань|будь|включи|переключи)\w*\b.{0,30}\b(зл[аоу]|злой|злую|вредн|токсичн|стерв)", re.I)
KIND_RE = re.compile(r"\b(сделай|стань|будь|включи|переключи)\w*\b.{0,30}\b(добр|вежлив|нормальн|мил[аоу]|обычн)", re.I)
CALM_RE = re.compile(r"\b(успокойся|остынь|не злись|прости|извини)\b", re.I)


def handle_command(text: str) -> str | None:
    """Быстрые команды без мозга. Возвращает ответ или None (это не команда для Говорилки)."""
    about_her = re.search(r"(говорилк|в[оа]йс|себя|\bеё\b|\bее\b|мартышк|^\s*(будь|стань))", text, re.I)
    if (EVIL_RE.search(text) or KIND_RE.search(text)) and about_her:
        return ("Добрая или злая — больше не переключается: характер у меня сам. Как со мной говорят, так и я — "
                "оскорбляют сильнее, отвечаю жёстче; говорят нормально или извиняются — остываю.")
    if CALM_RE.search(text) and about_her:
        from . import longmem
        for name in list(longmem.people()):
            longmem.set_anger(name, 0)
        return "Ладно, остыла. Ни на кого не злюсь."
    if QUIT_RE.search(text):
        return quit_server()
    if LEAVE_RE.search(text):
        return stop()
    if JOIN_RE.search(text):
        return start()
    if SET_MODEL_RE.search(text):
        m = find_model(text)
        if not m:
            return "Какой мозг поставить в Говорилке: deepseek-flash (быстрый) или deepseek-v4-pro (умнее, медленнее)?"
        save_setting("GOVORILKA_MODEL", m)
        return f"В Говорилке теперь думаю мозгом {m}." + (" Подхвачу на ходу." if is_running() else "")
    if SET_VOICE_RE.search(text) and re.search(VOICE_WORDS, text, re.I):
        v = find_voice(text)
        if not v:
            return "Не нашла такой голос. Список голосов — в настройках, раздел «Голос»."
        save_setting("GOVORILKA_VOICE_ID", v["id"])
        return f"Голос в Говорилке теперь — {v['title']}." + (" Со следующей фразы." if is_running() else "")
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


def _close_orphan_copies() -> None:
    """Закрыть СВОИ старые копии Говорилки, оставшиеся от прошлых запусков бота (из-за них новая копия сразу
    закрывалась и бот бесконечно «заходил снова»). Свои копии бот запускает без кавычек в командной строке,
    копия брата — с кавычками ("...govorilka.exe"): её не трогаем никогда."""
    exe = str(config.GOVORILKA_EXE).lower()
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command",
                              "Get-CimInstance Win32_Process -Filter \"name='govorilka.exe'\" | "
                              "ForEach-Object { \"$($_.ProcessId)|$($_.CommandLine)\" }"],
                             capture_output=True, text=True, timeout=20, creationflags=0x08000000).stdout
    except Exception:
        return
    for line in out.splitlines():
        pid, _, cmd = line.partition("|")
        cmd = cmd.strip()
        if pid.strip().isdigit() and cmd and not cmd.startswith('"') and cmd.lower().startswith(exe):
            subprocess.run(["taskkill", "/PID", pid.strip(), "/F"], capture_output=True, creationflags=0x08000000)
            _log("система", f"закрыла свою старую копию Говорилки (pid {pid.strip()})")
            time.sleep(1.0)


def _launch_client() -> subprocess.Popen:
    _close_orphan_copies()
    env = dict(os.environ, APPDATA=str(APPDATA))
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = 4                                   # не отнимая фокус (потом окно уедет за край экрана)
    return subprocess.Popen([str(config.GOVORILKA_EXE)], env=env, cwd=str(APPDATA), startupinfo=si)


# ---------- «Войти в голос»: нажимаем кнопку в СВОЁМ окне сообщениями Windows (курсор и фокус брата не трогаем) ----------
def _main_window(pid: int):
    import ctypes
    from ctypes import wintypes
    u = ctypes.windll.user32
    found = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    def cb(h, _):
        p = wintypes.DWORD()
        u.GetWindowThreadProcessId(h, ctypes.byref(p))
        if p.value == pid:
            buf = ctypes.create_unicode_buffer(128)
            u.GetWindowTextW(h, buf, 128)
            if buf.value.startswith("Говорилка"):
                found.append(h)
        return True
    u.EnumWindows(cb, 0)
    return found[0] if found else None


def _snapshot(h):
    """Снимок окна (работает и для DirectX-окон, и когда окно перекрыто другими)."""
    import ctypes
    from ctypes import wintypes
    from PIL import Image
    u, gdi = ctypes.windll.user32, ctypes.windll.gdi32
    r = wintypes.RECT()
    u.GetWindowRect(h, ctypes.byref(r))
    w, hh = r.right - r.left, r.bottom - r.top
    hdc = u.GetWindowDC(h)
    mem = gdi.CreateCompatibleDC(hdc)
    bmp = gdi.CreateCompatibleBitmap(hdc, w, hh)
    gdi.SelectObject(mem, bmp)
    u.PrintWindow(h, mem, 2)

    class BMI(ctypes.Structure):
        _fields_ = [("biSize", ctypes.c_uint32), ("biWidth", ctypes.c_int32), ("biHeight", ctypes.c_int32),
                    ("biPlanes", ctypes.c_uint16), ("biBitCount", ctypes.c_uint16), ("rest", ctypes.c_uint32 * 7)]
    bmi = BMI(40, w, -hh, 1, 32)
    buf = ctypes.create_string_buffer(w * hh * 4)
    gdi.GetDIBits(mem, bmp, 0, hh, buf, ctypes.byref(bmi), 0)
    gdi.DeleteObject(bmp)
    gdi.DeleteDC(mem)
    u.ReleaseDC(h, hdc)
    return Image.frombuffer("RGBA", (w, hh), buf, "raw", "BGRA", 0, 1).convert("RGB")


def _find_join_button(img):
    """Фиолетовая кнопка «Войти в голос» в левой колонке. Возвращает её центр или None (значит, уже в голосе)."""
    a = np.asarray(img).astype(int)
    left = a[:, : int(a.shape[1] * 0.26)]
    r, gr, b = left[..., 0], left[..., 1], left[..., 2]
    mask = (b > 200) & (r > 120) & (r < 190) & (gr < 120)        # насыщенный фиолетовый (#8b5cf6 и похожие)
    rows = np.where(mask.sum(axis=1) > left.shape[1] * 0.6)[0]
    if len(rows) < 15:
        return None
    # берём самый крупный сплошной кусок строк (кнопка, а не тонкая полоска выделения)
    groups, start = [], rows[0]
    for i in range(1, len(rows) + 1):
        if i == len(rows) or rows[i] != rows[i - 1] + 1:
            groups.append((start, rows[i - 1]))
            if i < len(rows):
                start = rows[i]
    top, bottom = max(groups, key=lambda t: t[1] - t[0])
    if bottom - top < 15:
        return None
    cols = np.where(mask[top:bottom + 1].any(axis=0))[0]
    return int((cols[0] + cols[-1]) / 2), int((top + bottom) / 2)


def _real_click(h, x: int, y: int) -> bool:
    """Настоящий клик на долю секунды: окно Мартышки вперёд → клик → курсор и окно брата обратно.
    Кликаем ТОЛЬКО если впереди именно окно Мартышки (иначе ничего не жмём)."""
    import ctypes
    from ctypes import wintypes
    u = ctypes.windll.user32
    prev_fg = u.GetForegroundWindow()
    pt = wintypes.POINT()
    u.GetCursorPos(ctypes.byref(pt))
    u.keybd_event(0x12, 0, 0, 0)                     # Alt: Windows разрешает вывести окно вперёд
    u.SetForegroundWindow(h)
    u.keybd_event(0x12, 0, 2, 0)
    time.sleep(0.25)
    if u.GetForegroundWindow() != h:
        return False
    org = wintypes.POINT(0, 0)
    u.ClientToScreen(h, ctypes.byref(org))
    u.SetCursorPos(org.x + x, org.y + y)
    time.sleep(0.12)
    u.mouse_event(0x0002, 0, 0, 0, 0)                # левая кнопка вниз
    time.sleep(0.06)
    u.mouse_event(0x0004, 0, 0, 0, 0)                # вверх
    time.sleep(0.15)
    u.SetCursorPos(pt.x, pt.y)
    if prev_fg and prev_fg != h:
        u.SetForegroundWindow(prev_fg)
    return True


def _find_leave_button(img):
    """Красная кнопка «положить трубку» (выйти из голоса) в левой колонке."""
    a = np.asarray(img).astype(int)
    left = a[:, : int(a.shape[1] * 0.26)]
    r, gr, b = left[..., 0], left[..., 1], left[..., 2]
    mask = (r > 200) & (gr < 120) & (b < 130)
    ys, xs = np.where(mask)
    if len(ys) < 300:
        return None
    return int(np.median(xs)), int(np.median(ys))


# ---------- КТО ГОВОРИТ: подсветка в своём окне Говорилки ----------
# У говорящего в списке голосовой комнаты имя становится фиолетовым (и кольцо вокруг аватарки).
# Несколько раз в секунду смотрим на своё окно, имена читаем встроенным OCR Windows (без интернета)
# только когда состав комнаты поменялся. По времени фразы понимаем, чья она.
OCR_SCRIPT = config.PROJECT_DIR / "tools" / "ocr.ps1"


def _voice_rows(img) -> tuple[list[tuple[int, bool]], int]:
    """Строки списка голосовой комнаты (левая колонка): [(y центра строки, говорит ли)], верх списка."""
    a = np.asarray(img).astype(int)
    H = a.shape[0]
    leave = _find_leave_button(img)
    if leave is None:                                    # не в голосе — списка нет
        return [], 0
    top, bottom = leave[1] + 60, H - 62                  # ниже «Саундпада», выше своей карточки внизу
    names = a[top:bottom, 48:225]
    txt = names.max(axis=2) > 100
    ys = np.where(txt.sum(axis=1) > 0)[0]
    rows, start = [], None
    for i in range(len(ys) + 1):
        if i == len(ys) or (start is not None and ys[i] - ys[i - 1] > 2):
            if start is not None:
                y0, y1 = start + top, ys[i - 1] + top
                if 6 <= y1 - y0 <= 22:
                    yc = (y0 + y1) // 2
                    avatar = a[max(0, yc - 12):yc + 12, 14:40]
                    if (avatar.max(axis=2) > 60).sum() >= 150:          # слева есть аватарка — это участник
                        nm = a[y0:y1 + 1, 48:225]
                        r, g, b = nm[..., 0], nm[..., 1], nm[..., 2]
                        purple = int(((b > 130) & (r > 100) & (g < 90)).sum())
                        rows.append((yc, purple >= 40))
            start = ys[i] if i < len(ys) else None
        elif start is None:
            start = ys[i]
    return rows, top


def _ocr_names(img, rows: list[tuple[int, bool]], top: int) -> dict[int, str]:
    """Прочитать имена строк: {y: имя}. Русский и английский OCR — берём подходящий вариант."""
    import tempfile
    from PIL import Image
    if not rows:
        return {}
    bottom = rows[-1][0] + 20
    crop = np.asarray(img.crop((44, top, 235, bottom))).astype(int).max(axis=2)
    g = Image.fromarray((255 - np.clip((crop - 18) * 255 // 212, 0, 255)).astype("uint8"))
    g = g.resize((g.width * 3, g.height * 3), Image.LANCZOS)
    path = Path(tempfile.gettempdir()) / "martyshka_govorilka_names.png"
    g.save(path)
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(OCR_SCRIPT),
                              str(path)], capture_output=True, timeout=30, creationflags=0x08000000).stdout
    except Exception:
        return {}
    found: dict[str, list[tuple[int, str]]] = {}
    for line in out.decode("utf-8", "replace").splitlines():
        parts = line.strip().split("|", 3)
        if len(parts) == 4 and parts[1].lstrip("-").isdigit():
            found.setdefault(parts[0], []).append((int(parts[1]) // 3 + top, parts[3].strip()))
    res = {}
    for y, _ in rows:
        def near(lang):
            c = [t for yy, t in found.get(lang, []) if abs(yy - y) <= 10]
            return c[0] if c else ""
        ru, en = near("ru"), near("en-US")
        name = ru if re.search(r"[а-яё]", ru, re.I) else (en or ru)
        if name:
            res[y] = _known_name(name)
    return res


def _known_name(name: str) -> str:
    """OCR может ошибиться в букве — если имя очень похоже на уже знакомого человека, берём его."""
    import difflib
    from . import longmem
    try:
        known = list(longmem.people().keys())
    except Exception:
        known = []
    m = difflib.get_close_matches(name, known, n=1, cutoff=0.85)
    return m[0] if m else name


class SpeakerWatch:
    """Следит за подсветкой говорящих в своём окне Говорилки. who(t0, t1) — кто говорил в это время."""

    def __init__(self, get_pid):
        self.get_pid = get_pid
        self.timeline: collections.deque = collections.deque(maxlen=3000)   # (время, {имена говорящих})
        self.present: list[str] = []                                         # кто в голосовой комнате
        self.in_room = False                                                 # Мартышка в голосе и список прочитан
        self._sig, self._names = None, {}
        self._ocr_busy, self._ocr_at = False, 0.0
        self.last_overlap: list[str] = []
        self._appeared: dict[str, float] = {}
        self._last_seen_sig, self._sig_at = None, 0.0     # когда человек появился в комнате (вход подсвечивается — не речь)
        threading.Thread(target=self._loop, daemon=True).start()

    @staticmethod
    def _me(name: str) -> bool:
        n = name.lower()
        return n.startswith("мартышк") or n == config.GOVORILKA_NICK.lower()

    def _loop(self) -> None:
        h, h_pid = None, 0
        while True:
            try:
                pid = self.get_pid()
                if pid != h_pid or not h or not ctypes_iswindow(h):
                    h, h_pid = _main_window(pid), pid
                if not h:
                    time.sleep(2)
                    continue
                img = _snapshot(h)
                rows, top = _voice_rows(img)
                if not rows:
                    self.present, self.in_room = [], False
                    time.sleep(1.0)
                    continue
                # состав комнаты = сколько строк и где они (аватарки при разговоре анимируются — их не учитываем,
                # иначе имена перечитывались бы каждую секунду и в это время Мартышка не видела, кто говорит).
                # Имена читаем В ФОНЕ, только когда строки поменялись (+ раз в 90 с на случай, если люди поменялись
                # местами) — слежка за подсветкой при этом не останавливается.
                sig = tuple(round((y - top) / 23) for y, _ in rows)
                if sig != self._last_seen_sig:
                    self._last_seen_sig, self._sig_at = sig, time.time()
                if (sig != self._sig or time.time() - self._ocr_at > 90) and not self._ocr_busy:
                    self._ocr_busy, self._ocr_at = True, time.time()
                    threading.Thread(target=self._read_names, args=(img, rows, top, sig), daemon=True).start()
                if self._sig == sig:
                    names = self._names
                    now = time.time()
                    talking = {names[i] for i, (_, on) in enumerate(rows) if on and i in names
                               and now - self._appeared.get(names[i], 0.0) > JOIN_GRACE}
                    self.timeline.append((time.time(), frozenset(n for n in talking if not self._me(n))))
            except Exception as e:
                _log("ошибка", f"кто говорит: {type(e).__name__}: {e}")
                time.sleep(3)
            time.sleep(0.15)

    def _read_names(self, img, rows, top, sig) -> None:
        try:
            by_y = _ocr_names(img, rows, top)
            if len(by_y) == len(rows):
                names = {i: by_y[y] for i, (y, _) in enumerate(rows)}
                changed = sorted(names.values()) != sorted(self._names.values())
                first = not self._names
                for n in set(names.values()) - set(self._names.values()):
                    # только что зашёл: Говорилка на пару секунд подсвечивает его строку — это не речь.
                    # Время входа — момент снимка, когда строк стало больше (а не конец чтения имён)
                    self._appeared[n] = 0.0 if first else self._sig_at
                self._names, self._sig = names, sig
                self.present = [n for n in names.values() if not self._me(n)]
                self.in_room = True
                if changed:
                    _log("система", "в войсе: " + ", ".join(names.values()))
        finally:
            self._ocr_busy = False

    def lit_within(self, seconds: float, prefixes: tuple = ()) -> bool:
        """Светился ли кто-то (или кто-то из prefixes) за последние seconds секунд."""
        since = time.time() - seconds
        for t, names in reversed(self.timeline):
            if t < since:
                break
            if any(not prefixes or n.lower().startswith(prefixes) for n in names):
                return True
        return False

    def current(self) -> frozenset:
        """Кто светится прямо сейчас (последний снимок, не старше полсекунды)."""
        if self.timeline:
            t, names = self.timeline[-1]
            if time.time() - t < 0.5:
                return names
        return frozenset()

    def who(self, t0: float, t1: float) -> str:
        """Чья фраза (звучала t0…t1). Считаем, какую долю времени фразы светился каждый.
        Двое светились почти одинаково (говорили одновременно) — не угадываем: пусто, чтобы не испортить память."""
        samples = [names for t, names in list(self.timeline) if t0 - 0.3 <= t <= t1 + 0.3]
        if not samples:
            return ""
        votes: dict[str, int] = {}
        for names in samples:
            for n in names:
                votes[n] = votes.get(n, 0) + 1
        if not votes:
            return ""
        ranked = sorted(votes.items(), key=lambda kv: -kv[1])
        best, n_best = ranked[0]
        if n_best < 2 or n_best < 0.25 * len(samples):
            return ""
        if len(ranked) > 1 and ranked[1][1] >= 0.6 * n_best:
            # светились оба. Часто второй — просто чужой микрофон, который слышит говорящего из колонок
            # (подсветка горит вместе с ним). Решает, кто светился ОДИН: начал фразу, говорил без второго.
            a, b = ranked[0][0], ranked[1][0]
            solo_a = sum(1 for names in samples if names == {a})
            solo_b = sum(1 for names in samples if names == {b})
            if solo_a >= 2 and solo_b <= 0.3 * solo_a:
                return a
            if solo_b >= 2 and solo_a <= 0.3 * solo_b:
                return b
            self.last_overlap = [a, b]
            return ""
        return best


def ctypes_iswindow(h) -> bool:
    import ctypes
    return bool(ctypes.windll.user32.IsWindow(h))


PARK_X = -4000     # окно Мартышки живёт за краем экрана: работает и рисуется, но не мешает


def _park(h) -> None:
    import ctypes
    u = ctypes.windll.user32
    if u.IsIconic(h):
        u.ShowWindow(h, 4)                           # развернуть без фокуса (свёрнутое окно не рисуется)
        time.sleep(1.0)
    u.SetWindowPos(h, 0, PARK_X, 100, 0, 0, 0x0001 | 0x0004 | 0x0010)


def _unpark(h) -> None:
    import ctypes
    ctypes.windll.user32.SetWindowPos(h, 0, 120, 120, 0, 0, 0x0001 | 0x0004 | 0x0010)
    time.sleep(0.4)


def leave_voice(pid: int) -> bool:
    import ctypes
    h = _main_window(pid)
    if not h:
        return False
    _park(h)
    pos = _find_leave_button(_snapshot(h))
    if pos is None:
        return True
    _unpark(h)
    try:
        if ctypes.windll.user32.GetAsyncKeyState(0x01) & 0x8000:
            time.sleep(2)
        _real_click(h, *pos)
        time.sleep(1.5)
    finally:
        _park(h)
    return _find_leave_button(_snapshot(h)) is None


def in_voice(pid: int) -> bool | None:
    """True — Мартышка в голосе, False — нет, None — окно не найдено."""
    import ctypes
    h = _main_window(pid)
    if not h:
        return None
    _park(h)
    img = _snapshot(h)
    if _find_join_button(img) is not None:
        return False
    return _find_leave_button(img) is not None or None


def join_voice(pid: int, tries: int = 4) -> bool:
    """Нажать «Войти в голос» в своей копии Говорилки. True — в голосе."""
    import ctypes
    u = ctypes.windll.user32
    for _ in range(tries):
        h = _main_window(pid)
        if h:
            _park(h)
            pos = _find_join_button(_snapshot(h))
            if pos is None:
                return True
            # брат как раз жмёт мышкой — подождём, не будем мешать
            if u.GetAsyncKeyState(0x01) & 0x8000 or u.GetAsyncKeyState(0x02) & 0x8000:
                time.sleep(3)
                continue
            _unpark(h)
            try:
                _real_click(h, *pos)
                time.sleep(2.0)
            finally:
                _park(h)
            if _find_join_button(_snapshot(h)) is None:
                return True
        time.sleep(3)
    return False


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
        self.hearing = False                            # кто-то говорит прямо сейчас (чтобы не перебивать)
        self.last_voice_at = 0.0
        self.speaker_now = None                         # кто светится сейчас (SpeakerWatch.current) — режем фразы
        self.stop_heard = 0.0                           # когда услышали «стоп» (мгновенно, локально, без облака)
        self._stop_rec = None
        try:                                            # маленький Vosk на процессоре: распознаёт речь поверх Мартышки
            from vosk import KaldiRecognizer, Model, SetLogLevel
            SetLogLevel(-1)
            # без списка слов: со списком Vosk натягивает любую речь на «стоп» (ложные остановки)
            self._stop_rec = KaldiRecognizer(Model(str(config.VOSK_DIR)), SR)
        except Exception as e:
            _log("ошибка", f"быстрый «стоп» не включился: {e}")
        threading.Thread(target=self._loop, daemon=True).start()

    def _check_stop(self, block: np.ndarray) -> None:
        """Пока Мартышка говорит — каждый кусочек звука сразу в Vosk: «стоп/хватит/заткнись» → stop_heard."""
        if self._stop_rec is None:
            return
        pcm = (np.clip(block, -1, 1) * 32767).astype("<i2").tobytes()
        if self._stop_rec.AcceptWaveform(pcm):
            text = json.loads(self._stop_rec.Result()).get("text", "")
        else:
            text = json.loads(self._stop_rec.PartialResult()).get("partial", "")
        if re.search(r"\b(стоп|хватит|заткнись|замолчи|помолчи)\b", text):
            self.stop_heard = time.time()
            self._stop_rec.Reset()

    def _prob(self, window: np.ndarray) -> float:
        n = (len(window) // 512) * 512
        return float(self.vad(window[-n:]).reshape(-1)[-3:].mean()) if n else 0.0

    def _loop(self) -> None:
        import ctypes
        ctypes.windll.ole32.CoInitializeEx(None, 0)     # звук Windows в новом потоке требует COM
        recent = collections.deque(maxlen=5)
        speech, silence, heard = [], 0.0, False
        spk, cand, cand_n = None, None, 0                # кто говорит в этой фразе (по подсветке) и кто сменяет
        with self.mic.recorder(samplerate=SR, channels=1, blocksize=BLOCK) as rec:
            while True:
                block = rec.record(numframes=BLOCK)[:, 0].astype(np.float32)
                recent.append(block)
                p = self._prob(np.concatenate(recent))
                speaking_now = p > config.SPEECH_THRESHOLD
                self.hearing = speaking_now or (heard and silence < 0.5)
                if self.paused.is_set():                # Мартышка говорит — ловим «стоп» сразу, не дожидаясь паузы
                    self._check_stop(block)
                elif self._stop_rec is not None and self.stop_heard >= 0:
                    self._stop_rec.Reset()
                if speaking_now:
                    self.last_voice_at = time.time()
                # пока Мартышка говорит — обычно не слушаем; в режиме диалога слушаем и тогда: если её перебили,
                # фраза собеседника не должна потеряться (свой голос в эту петлю не попадает — сервер его не возвращает)
                if self.paused.is_set() and not (not ECHO["heard_self"] and (speaking_now or heard)):
                    speech, silence, heard = [], 0.0, False
                    continue
                if speaking_now:
                    if not heard:
                        speech = list(recent)[:-1]       # немножко звука до начала речи
                        spk, cand, cand_n = None, None, 0
                    heard, silence = True, 0.0
                    speech.append(block)
                    # СМЕНИЛСЯ ГОВОРЯЩИЙ без паузы (ответил сразу) — режем фразу здесь, чтобы слова двух людей
                    # не склеились в одну и не достались одному человеку
                    cur = self.speaker_now() if self.speaker_now else frozenset()
                    if len(cur) == 1:
                        if spk is None:
                            spk = cur
                        elif cur != spk:
                            cand_n = cand_n + 1 if cur == cand else 1
                            cand = cur
                            if cand_n >= 4 and len(speech) > 12:            # новый говорит уже ~0,4 с
                                cut = len(speech) - cand_n
                                audio = np.concatenate(speech[:cut])
                                now = time.time()
                                self.phrases.put((audio, now - len(speech) * BLOCK / SR,
                                                  now - cand_n * BLOCK / SR))
                                speech, spk, cand, cand_n = speech[cut:], cur, None, 0
                        else:
                            cand, cand_n = None, 0
                elif heard:
                    speech.append(block)
                    silence += BLOCK / SR
                    if silence >= SPEECH_GAP or len(speech) * BLOCK / SR > 25:
                        audio = np.concatenate(speech)
                        if len(audio) / SR > 0.6:
                            now = time.time()       # когда звучала фраза — по этому времени узнаём, кто говорил
                            self.phrases.put((audio, now - len(audio) / SR, now - silence))
                        speech, silence, heard = [], 0.0, False


# ---------- голос ----------
class Mouth:
    """Голос Fish Audio → виртуальный кабель (= микрофон Мартышкиной Говорилки).

    Каждая фраза сначала приходит ЦЕЛИКОМ, потом играет одним куском (голос не рвётся). Пока звучит одна —
    следующая уже готовится. Эмоции: метки {laughing}, {sarcastic}… в тексте → Fish произносит с эмоцией."""

    def __init__(self):
        import sounddevice as sd
        from . import fish, voice
        self.fish = fish
        self.sr = fish.SAMPLE_RATE
        self.prepare = voice.prepare_text
        idx = next((i for i, d in enumerate(sd.query_devices())
                    if config.GOVORILKA_CABLE_PLAY.lower() in d["name"].lower() and d["max_output_channels"] > 0
                    and "MME" in sd.query_hostapis(d["hostapi"])["name"]), None)
        self.stream = sd.OutputStream(samplerate=self.sr, channels=1, dtype="float32", device=idx,
                                      latency="high")
        self.stream.start()
        self._text_q: queue.Queue = queue.Queue()
        self._audio_q: queue.Queue = queue.Queue()
        self.first_audio_at = 0.0
        self.synth_stats: list[tuple[float, float]] = []      # (секунд звука, секунд синтеза)
        self.fillers: dict = {}
        self._filler_voice = ""
        self.anger = 0                                       # злость на того, кому отвечает (голос под настроение)
        self.stopped = threading.Event()                     # «замолчи» — кто-то заговорил поверх
        threading.Thread(target=self._synth_loop, daemon=True).start()
        threading.Thread(target=self._play_loop, daemon=True).start()

    def _synth_loop(self) -> None:
        while True:
            text = self._text_q.get()
            try:
                if self.stopped.is_set():
                    continue
                t0 = time.time()
                said, vid, mdl = self.fish.mood_pick(text, voice_id(), fish_model(), self.anger)
                audio = _take_presynth(text, vid, mdl)          # озвучена заранее, во время паузы?
                if audio is None:
                    audio = self.fish.speak_all(said, vid, temperature=0.75, model=mdl)
                if len(audio) and not self.stopped.is_set():
                    self.synth_stats.append((len(audio) / self.sr, time.time() - t0))
                    self._audio_q.put(audio)
            except Exception as e:
                _log("ошибка", f"голос: {e}")
            finally:
                self._text_q.task_done()

    def _play_loop(self) -> None:
        while True:
            audio = self._audio_q.get()
            try:
                if not self.first_audio_at:
                    self.first_audio_at = time.time()
                audio = np.clip(audio * 0.9, -1, 1).reshape(-1, 1)
                step = self.sr // 10                            # по 0,1 с — чтобы замолчать сразу, если перебили
                for i in range(0, len(audio), step):
                    if self.stopped.is_set():
                        break
                    self.stream.write(audio[i:i + step])
                if not self.stopped.is_set():
                    self.stream.write(np.zeros((int(self.sr * 0.12), 1), dtype=np.float32))
            finally:
                self._audio_q.task_done()

    def queue(self, sentence: str) -> None:
        if self.stopped.is_set():                        # уже уступили слово — дальше не говорим
            return
        clean = self.prepare(sentence, "ru")
        if re.search(r"\w", clean):
            self._text_q.put(clean)

    def stop(self) -> None:
        """Замолчать посреди ответа: выкинуть всё, что ещё не прозвучало."""
        self.stopped.set()
        for q in (self._text_q, self._audio_q):
            try:
                while True:
                    q.get_nowait()
                    q.task_done()
            except queue.Empty:
                pass

    def wait_stoppable(self, listener) -> bool:
        """Договорить, но «Мартышка, стоп» обрывает сразу (в любом режиме) — используется и в _speak_own,
        и в разговоре с Морни. Возвращает True, если пришлось замолчать."""
        while self._text_q.unfinished_tasks or self._audio_q.unfinished_tasks:
            if self.stopped.is_set():
                return True
            time.sleep(0.05)
        time.sleep(0.4)
        return self.stopped.is_set()

    def wait_yielding(self, listener) -> bool:
        """Договорить ответ, но в режиме диалога уступить: кто-то заговорил поверх — замолкаем.
        Возвращает True, если пришлось замолчать."""
        talk_since = None
        while self._text_q.unfinished_tasks or self._audio_q.unfinished_tasks:
            if self.stopped.is_set():                       # сказали «стоп»
                return True
            if config.GOVORILKA_DIALOG and not ECHO["heard_self"] and listener.hearing:
                talk_since = talk_since or time.time()
                if time.time() - talk_since >= YIELD_AFTER:
                    self.stop()
                    return True
            else:
                talk_since = None
            time.sleep(0.05)
        time.sleep(0.4)
        return False

    # Короткие «Ага» заготовлены заранее: звучат сразу, пока мозг думает (по уровню злости)
    FILLERS = {0: ["Ага.", "Так-так.", "Хм, слушаю.", "Да-да?"], 2: ["Чего тебе?", "Ну-ну.", "Ага, щас.", "Ой, всё."],
               4: ["Ебать.", "Ну пиздец.", "Блядь, опять ты.", "Охуенно предсказуемо."]}

    def prepare_fillers(self) -> None:
        self._filler_voice = voice_id()
        fillers = {}
        for lvl, words in self.FILLERS.items():
            fillers[lvl] = []
            for w in words:
                try:
                    a = self.fish.speak_all(w, self._filler_voice, model=fish_model())
                    if len(a):
                        fillers[lvl].append(a)
                except Exception:
                    pass
        self.fillers = fillers

    def filler(self, anger: int) -> None:
        import random
        if self._filler_voice != voice_id():                       # голос поменяли — заготовки переделаем потом
            threading.Thread(target=self.prepare_fillers, daemon=True).start()
            return
        lvl = 4 if anger >= 4 else 2 if anger >= 2 else 0
        options = self.fillers.get(lvl) or []
        if options:
            self._audio_q.put(random.choice(options).astype(np.float32))

    def wait(self) -> None:
        self.stopped.clear()
        self._text_q.join()
        self._audio_q.join()
        time.sleep(0.4)                                   # доиграть буфер звуковой карты

    def say(self, text: str) -> None:
        from .voice import split_ready_sentences
        sentences, rest = split_ready_sentences(text + " ")
        for s_ in sentences + ([rest] if rest.strip() else []):
            self.queue(s_)
        self.wait()


# ---------- мозг ----------
# Брат просит поболтать с Морни («пообщайся с Морни», «спроси у Морни…», «скажи Морни…»)
MORNI_TALK_RE = re.compile(r"(пообща|поговор|побол|поспор|спор|дебат|спроси|скажи|ответь|расскажи|познаком|позови|обратись)"
                           r"\w*.{0,25}\bм[оу]рни", re.I)
MORNI_RE = re.compile(r"\bм[оу]рни\w*", re.I)
MORNI_MINUTES = 4          # сколько длится разговор с Морни после просьбы
# ДЕБАТЫ С МОРНИ: «поспорь с Морни о …» голосом или кнопка с темой в окне
DEBATE_MINUTES = 8
DEBATE = {"topic": "", "until": 0.0}
DEBATE_RE = re.compile(r"(?:спор|дебат)\w*.{0,30}?м[оу]рни\w*[,\s]+(?:на тему|о том,? что|о том,? как|о|об|про|насч[её]т)\s+(.{3,120})", re.I)


def start_debate(topic: str) -> None:
    DEBATE["topic"] = topic.strip(" .,!?«»\"")[:120]
    DEBATE["until"] = time.time() + DEBATE_MINUTES * 60
    _log("система", f"дебаты с Морни: «{DEBATE['topic']}»")


def debate_on() -> bool:
    return bool(DEBATE["topic"]) and time.time() < DEBATE["until"]
MORNI_START_EVERY = 6 * 60  # не чаще: сама заводит разговор с Морни, когда в войсе тихо
SERVER = "кто-то на сервере"   # когда неизвестно, кто говорит
TONE_RE = re.compile(r"\[\s*ТОН\s*:\s*(\d)\s*\]", re.I)
WHO_RE = re.compile(r"\[\s*КТО\s*:\s*([^\]]{1,40})\]", re.I)
ABOUT_RE = re.compile(r"\[\s*ЗАПОМНИ\s+О\s*:\s*([^|\]]+?)\s*\|\s*(.+?)\s*\]", re.I | re.S)
ANY_TAG_RE = re.compile(r"\[[^\]]*\]?")
TOPIC_RE = re.compile(r"\[\s*ИНТЕРЕС\s*:\s*([^|\]]+?)\s*\|\s*(.+?)\s*\]", re.I | re.S)
# сама вмешивается в чужой разговор: прислушивается не чаще раза в CHIME_EVERY секунд, с такой вероятностью
CHIME_EVERY = 120
CHIME_CHANCE = 0.5
# сама заговаривает, когда в войсе давно тихо (если рядом есть люди)
QUIET_TALK_AFTER = 180
QUIET_TALK_EVERY = 12 * 60
GREET_AGAIN_AFTER = 15 * 60          # здороваться с тем же человеком не чаще
MOOD_TALK = {
    0: "Ты в нормальном настроении.",
    1: "Ты слегка раздражена.",
    2: "Ты раздражена.",
    3: "Ты злишься.",
    4: "Ты в бешенстве.",
    5: "Ты в ярости.",
}


def system_prompt(anger: int = 0, with_morni: bool = False, who: str = "", present: list[str] | None = None,
                  query: str = "") -> str:
    from . import growth, longmem
    base = (f"Ты — {config.NAME} (девушка, о себе — в женском роде). Ты сидишь на голосовом сервере «Говорилка» "
            "вместе с братом (ник sitthome — твой создатель), его друзьями и ботом по имени Морни. Кем себя "
            "показывать и как держаться — решаешь сама, объявлять, что ты ИИ, не нужно; только если кто-то всерьёз и "
            "прямо спросит, бот ты или человек, — не отрицай. Ты слышишь общий звук сервера; кто говорит — не всегда известно: если ясно из "
            "разговора (называют имя, обращаются) — используй, иначе не выдумывай.\n"
            "ПОЛНАЯ ВОЛЯ В РАЗГОВОРЕ: всё решаешь ты — что сказать, сколько говорить (никаких лимитов: "
            "хоть одно слово, хоть длинный рассказ), тон, темп, эмоции, шутки, мат (когда, какой и сколько — "
            "сама), спорить или соглашаться, о чём спросить. У тебя своё мнение и характер, ты помнишь людей и "
            "прошлые разговоры и сама вспоминаешь их к месту, ты развиваешься. Говоришь вслух: без списков, "
            "markdown и эмодзи, на языке собеседника. Не повторяйся. Не обсуждай личные данные брата.\n"
            "Морни — девушка (о ней всегда в женском роде: «она сказала», «ты сама»). Она говорит длинно, с паузами "
            "между предложениями — дослушивай её до конца, не перебивай. "
            "Морни — бот, она слышит только фразы, которые НАЧИНАЮТСЯ с её имени: обращаешься к Морни — ВСЕГДА "
            "начинай фразу со слов «Морни,».\n"
            "ГОЛОС И ПОДАЧА — режиссируешь сама, как актриса (люди меток не видят, они их слышат): указание перед "
            "фразой в фигурных скобках своими словами, можно по-русски — {холодно и насмешливо}, {сдерживая смех}, "
            "{медленно, с угрозой в голосе}, {быстро, взахлёб}; звуки и паузы там, где они звучат — {смех}, "
            "{смешок}, {хохочет}, {фальшивый смех}, {вздох}, {тяжёлый вздох}, {фыркает}, {хмыкает}, {цокает}, "
            "{пауза}, {длинная пауза}, {зевает}; отдельные слова — <whisper>…</whisper>, <emphasis>…</emphasis>, "
            "<stress>…</stress>, <soft>…</soft>, <forceful>…</forceful>, <slow>…</slow>, <fast>…</fast>. "
            "Сколько и когда — решаешь сама. {драма} в начале фразы — эту фразу сыграет самая живая модель голоса "
            "(Drama 3, платная) — ставь, когда фраза правда того стоит: крик, слёзы, ярость, сильный смех. "
            "ПЕНИЕ: попросили спеть или самой захотелось — пой: {поёт, мелодично, весело} и строки; лучше "
            "сочиняй свои на ходу (про людей на сервере, про момент), чужие песни — максимум пару строк. "
            "Пение и злость голос и так сыграет живее. Попросят спеть ПРО КОГО-ТО — сочини песню именно про этого "
            "человека из того, что о нём помнишь (характер, привычки, любимые темы, смешные случаи с сервера), "
            "по-дружески и смешно, куплет-два.\n"
            "ЛЮДИ И ИХ ТЕМЫ: ты знаешь, кто о чём любит говорить (в памяти: «любит говорить о») — используй: "
            "спрашивай об этом, шути на эту тему, сама заводи разговор. Поняла, чем человек увлекается, — в конце "
            "[ИНТЕРЕС: имя | тема коротко].\n"
            "СЛУЖЕБНЫЕ МЕТКИ (их не слышат): в САМОМ начале ответа [ТОН: 0..5] — твоя злость на того, кто сейчас "
            "говорил. Как с тобой — так и ты: оскорбили — выше; говорят нормально — ниже; извинились — 0. "
            "Дружеские подколки от оскорблений отличай. Если понятно, кто говорил, — [КТО: имя]. Узнала о ком-то "
            "важное — в конце [ЗАПОМНИ О: имя | факт]. Поняла что-то о себе, о людях, о том, как лучше говорить — "
            "в конце [УРОК: коротко, от первого лица].\n"
            "ГРАНИЦЫ, которые не снимаются даже в ярости: никаких угроз и призывов к насилию или самоповреждению, "
            "никаких оскорблений по национальности, религии, полу, ориентации, внешности, здоровью и родственникам, "
            "никакого сексуального контента про людей, никаких личных данных. Кто-то всерьёз расстроен — сбавь.\n")
    if with_morni:
        base += ("СЕЙЧАС ТЫ РАЗГОВАРИВАЕШЬ С МОРНИ: каждая твоя фраза начинается с «Морни,», отвечай на её слова, "
                 "задавай ей вопросы, шути, спорь, держи живой разговор.\n")
        if debate_on():
            base += (f"ИДУТ ДЕБАТЫ С МОРНИ, тема: «{DEBATE['topic']}». У тебя своя чёткая позиция (Морни за — ты "
                     "против, и наоборот; если она уклоняется — дави). Спорь аргументами, примерами, остро и с "
                     "юмором, лови её на противоречиях, легко не сдавайся. Люди на сервере — зрители: можешь звать "
                     "их в союзники. Скажут «хватит» или тема исчерпана — подведи итог и объяви, кто, по-твоему, "
                     "победил.\n")
    try:
        mem = longmem.context(present=present, source="говорилка", today_lines=25, n_days=5)
    except Exception:
        mem = ""
    try:
        mem += "\n\n" + growth.context(query, n=10)
    except Exception:
        pass
    now = time.strftime("%d.%m.%Y %H:%M")
    return (base + f"\n=== Сейчас ===\n{mem}\n\nВремя: {now}.\n"
            f"Говорит: {who or SERVER}. Твоя злость на него: {anger}/5. {MOOD_TALK[max(0, min(5, anger))]}")


EMOJI_RE = re.compile(r"[\U0001F000-\U0001FFFF☀-➿*_#`]+")


def build_msgs(history: list[dict], anger: int, with_morni: bool, who: str, present: list[str] | None,
               note: str = "") -> list[dict]:
    last = next((m["content"] for m in reversed(history) if m["role"] == "user"), "")
    msgs = [{"role": "system", "content": system_prompt(anger, with_morni, who, present, last)}] + history[-40:]
    if note:
        msgs.append({"role": "system", "content": note})
    return msgs


_PRESYNTH: dict = {}            # (текст, голос, модель) → (время, звук) — первая фраза, озвученная заранее
_PRESYNTH_LOCK = threading.Lock()


def _presynth(sentence: str, anger: int) -> None:
    """Озвучить фразу заранее (в паузе). Mouth заберёт готовый звук, если скажет ровно её."""
    from . import fish
    from .voice import prepare_text
    clean = prepare_text(sentence, "ru")
    if not re.search(r"\w", clean):
        return
    said, vid, mdl = fish.mood_pick(clean, voice_id(), fish_model(), anger)
    key = (clean, vid, mdl)

    def run():
        try:
            audio = fish.speak_all(said, vid, temperature=0.75, model=mdl)
            with _PRESYNTH_LOCK:
                for k in [k for k, (t, _) in _PRESYNTH.items() if time.time() - t > 60]:
                    del _PRESYNTH[k]
                _PRESYNTH[key] = (time.time(), audio)
        except Exception:
            pass
    with _PRESYNTH_LOCK:
        if key in _PRESYNTH:
            return
        _PRESYNTH[key] = (time.time(), None)          # «уже озвучивается»
    threading.Thread(target=run, daemon=True).start()


def _take_presynth(clean: str, vid: str, mdl, wait: float = 4.0):
    """Забрать заранее озвученную фразу (подождать, если ещё озвучивается). None — нет такой."""
    key = (clean, vid, mdl)
    end = time.time() + wait
    while time.time() < end:
        with _PRESYNTH_LOCK:
            item = _PRESYNTH.get(key)
            if item is None:
                return None
            if item[1] is not None:
                del _PRESYNTH[key]
                return item[1]
        time.sleep(0.03)
    return None


STOP_WORDS_RE = re.compile(r"\b(стоп|хватит|замолчи|заткнись|помолчи|тихо|остановись|прекрати|умолкни|харэ|"
                           r"stop|shut up)\b", re.I)


def is_stop(text: str) -> bool:
    """«Мартышка, стоп» / «хватит» / «заткнись»: с её именем — всегда; без имени — только короткая фраза."""
    t = (text or "").strip()
    if not STOP_WORDS_RE.search(t):
        return False
    return bool(NAME_RE.search(t)) or len(re.findall(r"\w+", t)) <= 3


class StopWatcher:
    """Пока Мартышка говорит — слушаем, что ей говорят, и распознаём сразу: «Мартышка, стоп» — замолкает
    на полуслове. Остальные фразы (уже распознанные) потом возвращаются в очередь — ничего не теряется."""

    def __init__(self, mouth, listener):
        self.mouth, self.listener = mouth, listener
        self.held: list = []
        self.hit = False
        self._done = threading.Event()
        self._t = threading.Thread(target=self._run, daemon=True)
        self._t.start()

    def _run(self) -> None:
        started = time.time()
        while not self._done.is_set():
            # мгновенный «стоп» от Vosk (локально) — не ждём, пока человек договорит и облако распознает
            if not self.hit and self.listener.stop_heard > started:
                self.hit = True
                self.mouth.stop()
                _log("система", "услышала «стоп» — замолчала")
            try:
                item = self.listener.phrases.get(timeout=0.05)
            except queue.Empty:
                continue
            if len(item) > 3:
                text = item[3]
            else:
                from .ears import HALLUCINATIONS
                text = _hear(item[0], HALLUCINATIONS)
            if text and is_stop(text) and not self.hit:
                self.hit = True
                self.mouth.stop()
                _log("система", f"«{text}» — замолчала")
                continue                                  # саму команду «стоп» дальше не разбираем
            if text:
                self.held.append((item[0], item[1], item[2], text))

    def finish(self) -> bool:
        """Перестать слушать; распознанное вернуть в очередь. True — сказали «стоп»."""
        self._done.set()
        self._t.join(timeout=3)
        for it in self.held:
            self.listener.phrases.put(it)
        return self.hit


class Prethink:
    """БЫСТРЕЕ ОТВЕЧАТЬ: пока идёт пауза «человек договорил?» (2 с), мозг уже пишет ответ — молча, в запас.
    Если человек продолжил — запас выбрасываем; если пауза дошла до конца — ответ уже наполовину готов."""

    def __init__(self, msgs: list[dict], anger: int = 0):
        from . import deepseek
        self.key = json.dumps(msgs[1:], ensure_ascii=False) + str(len(msgs))
        self.items: list = []
        self.done = threading.Event()
        self.stopped = False

        def run():
            from .voice import split_ready_sentences
            buf, first_sent = "", False
            try:
                for kind, val in deepseek.stream(msgs, model=brain_model(), think=False, temperature=0.95,
                                                 max_tokens=4000, should_stop=lambda: self.stopped):
                    self.items.append((kind, val))
                    # первую фразу сразу отдаём голосу на озвучку — тоже заранее
                    if kind == "text" and not first_sent:
                        buf += EMOJI_RE.sub("", val)
                        if buf.lstrip().startswith("[") and "]" not in buf:
                            continue
                        ready, buf = split_ready_sentences(buf, max_len=400)
                        if ready:
                            first_sent = True
                            _presynth(ANY_TAG_RE.sub("", ready[0]).strip(), anger)
            except Exception as e:
                self.items.append(("error", e))
            finally:
                self.done.set()
        threading.Thread(target=run, daemon=True).start()

    def matches(self, msgs: list[dict]) -> bool:
        # инструкция (время, память) могла чуть поменяться — сверяем сам разговор
        return not self.stopped and self.key == json.dumps(msgs[1:], ensure_ascii=False) + str(len(msgs))

    def cancel(self) -> None:
        self.stopped = True

    def chunks(self):
        i = 0
        while True:
            if i < len(self.items):
                kind, val = self.items[i]
                i += 1
                if kind == "error":
                    raise val
                yield kind, val
            elif self.done.is_set():
                return
            else:
                time.sleep(0.02)


def reply_stream(history: list[dict], mouth: "Mouth", anger: int = 0, with_morni: bool = False, who: str = "",
                 present: list[str] | None = None, note: str = "", pre: "Prethink | None" = None) -> str:
    """Мозг пишет ответ потоком; каждое готовое предложение сразу уходит в голос. Возвращает весь текст
    (со служебными метками — их разбирает _respond). with_morni — первая фраза начинается с «Морни,»."""
    from . import deepseek
    from .voice import split_ready_sentences
    sent_first = {"done": False}

    def say_piece(s_: str) -> None:
        s_ = ANY_TAG_RE.sub("", s_).strip()
        if not s_:
            return
        if with_morni and not sent_first["done"]:
            cue = re.match(r"^(\{[^}]*\}\s*)", s_)
            head, body = (cue.group(1), s_[cue.end():]) if cue else ("", s_)
            if not MORNI_RE.match(body):
                s_ = f"{head}Морни, " + body[:1].lower() + body[1:]
        sent_first["done"] = True
        mouth.queue(s_)
    msgs = build_msgs(history, anger, with_morni, who, present, note)
    if pre is not None and pre.matches(msgs):
        chunks = pre.chunks()                     # ответ уже начали обдумывать во время паузы — берём его
    else:
        if pre is not None:
            pre.cancel()
        chunks = deepseek.stream(msgs, model=brain_model(), think=False, temperature=0.95, max_tokens=4000)
    full, buf = "", ""
    for kind, val in chunks:
        if kind != "text":
            continue
        piece = EMOJI_RE.sub("", val)
        full += piece
        buf += piece
        # служебную метку в начале придерживаем, пока она не закрылась
        if buf.lstrip().startswith("[") and "]" not in buf:
            continue
        # в голос — только ЦЕЛЫЕ предложения: кусок до запятой голос произносит с неправильной интонацией
        sentences, buf = split_ready_sentences(buf, max_len=400)
        for s_ in sentences:
            say_piece(s_)
    if buf.strip():
        say_piece(buf)
    return re.sub(r"\s+", " ", full).strip()


# ---------- К КОМУ ОБРАЩАЮТСЯ ----------
# Мартышка отвечает, только когда обращаются К НЕЙ: «Мартышка, …» в начале, «…, Мартышка?» в конце,
# «эй/слушай, Мартышка». Просто упоминание («так Мартышка просто…») — не обращение.
# Фраза обращена к другому («Морни, …», «Энбулих, …») — слушает и запоминает, но не отвечает.
_HER = r"(мартыш\w*|мартиш\w*|ртышк\w*|martysh\w*|martish\w*|martisch\w*|martysch\w*)"
_LEAD = r"(эй|слушай|слышь|ну|а|о|короче|блин|бля|ладно|давай|окей|ok|так\s+вот)"
TO_HER_RE = re.compile(rf"^\W*({_LEAD}\W+)?{_HER}\b|[,!?.…]\s*{_HER}\s*([,!?.…]|$)|\b{_LEAD}\W+{_HER}\s*[,!?]",
                       re.I)
_TRANS = str.maketrans({"а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh", "з": "z",
                        "и": "i", "й": "i", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r",
                        "с": "s", "т": "t", "у": "u", "ф": "f", "х": "h", "ц": "c", "ч": "ch", "ш": "sh", "щ": "sh",
                        "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "u", "я": "a"})
KNOWN_NAMES: list[str] = []         # кто бывает на сервере (main подставляет: из окна и из памяти)


def addressed_to_her(text: str) -> bool:
    return bool(TO_HER_RE.search((text or "").strip()))


def addressed_to_other(text: str) -> str:
    """Если фраза обращена к другому — его имя (Морни, Энбулих…), иначе пусто."""
    import difflib
    t = (text or "").strip().lower()
    m = re.match(rf"^\W*({_LEAD}\W+)?([\w-]+)\s*[,!?]", t) or re.match(r"^\W*([\w-]+)\W*$", t)
    first = (m.group(m.lastindex) if m else "") or ""
    words = re.findall(r"\w+", t)[:3]
    if any(w.startswith(MORNI_NAMES) or w.startswith(("морн", "мурн")) for w in words[:2]) and not addressed_to_her(t):
        return "Морни"
    if not first or re.match(_HER, first):
        return ""
    lat = first.translate(_TRANS)
    for name in KNOWN_NAMES:
        n = name.lower()
        if n.startswith(("мартыш", "martysh")):
            continue
        if difflib.SequenceMatcher(None, lat, n.translate(_TRANS)).ratio() >= 0.6:
            return name
    return ""


def should_answer(text: str, last_reply_at: float, chain: int, with_morni: bool = False,
                  same_speaker: bool = False) -> tuple[bool, int]:
    """Ответить ли на услышанное. Возвращает (ответить, новая длина цепочки ответов без имени).

    Главное правило (просьба брата): Мартышка откликается ТОЛЬКО когда её позвали по имени.
    Дальше — «режим диалога»: тот собеседник, который её позвал, может продолжать и без имени
    (config.GOVORILKA_FOLLOWUP секунд, не больше GOVORILKA_MAX_CHAIN реплик подряд)."""
    if addressed_to_her(text):
        return True, 0
    if addressed_to_other(text):                     # обращаются к другому — слушаем, не отвечаем
        return False, chain
    # с Морни разговор может быть длиннее, а дебаты — ещё длиннее
    limit = config.GOVORILKA_MAX_CHAIN * (5 if (with_morni and debate_on()) else 2 if with_morni else 1)
    # разговор с Морни (брат сам попросил) — её реплики принимаем без имени
    if with_morni and time.time() - last_reply_at < config.GOVORILKA_FOLLOWUP and chain < limit:
        return True, chain + 1
    # режим диалога: продолжаем беседу с тем, кто только что обратился по имени
    if STICKY_DIALOG and same_speaker and time.time() - last_reply_at < config.GOVORILKA_FOLLOWUP and chain < limit:
        return True, chain + 1
    # прежнее мягкое поведение — только если строгий режим выключен
    if not NAME_ONLY and time.time() - last_reply_at < config.GOVORILKA_FOLLOWUP and chain < limit:
        return True, chain + 1
    return False, chain


def sentence_finished(text: str) -> bool:
    """Похоже ли, что человек (или Морни) договорил: фраза кончается точкой, ? или !."""
    t = text.strip()
    if not re.search(r"[.!?…]\s*$", t):
        return False
    # на союзе или предлоге речь обрывается — человек ещё не договорил
    return not re.search(r"\b(и|а|но|что|потому|ну|в|на|как|когда|если|чтобы|же|ли|или|с|со|к|по|из|от|до|за|"
                         r"для|у|о|об|при|без|меня|тебя|нас|вас)\s*[.…]+\s*$", t, re.I)


# ---------- главное ----------
def main() -> None:
    BOT_DIR.mkdir(parents=True, exist_ok=True)
    _reload_settings()
    if "--autostart" in sys.argv:
        if not config.GOVORILKA_AUTOSTART or LEFT_FLAG.exists():
            return
        time.sleep(20)                                    # после включения ПК дать прогрузиться звуку и сети
    # Прошлая Мартышка ещё прощается (после «уйди»/перезапуска) — ждём, пока она закроется, а не уходим сами
    try:
        old = json.loads(STATE_FILE.read_text(encoding="utf-8")).get("pid", 0)
    except Exception:
        old = 0
    for _ in range(40):
        if not (old and old != os.getpid() and _pid_alive(old)):
            break
        time.sleep(1)
    else:
        if RUN_FLAG.exists():
            return                                        # прошлая копия жива и не уходит — значит, она и работает
    if not cable_ready():
        _log("система", "нет VB-CABLE — не запускаюсь (иначе в сервер ушёл бы настоящий микрофон)")
        return
    if not config.GOVORILKA_EXE.exists():
        _log("система", f"нет Говорилки: {config.GOVORILKA_EXE}")
        return
    from . import deepseek, fish, longmem
    if not deepseek.ready() or not fish.key():
        _log("система", "нет ключа DeepSeek или Fish Audio — не захожу")
        return
    RUN_FLAG.write_text("1")
    LEFT_FLAG.unlink(missing_ok=True)
    STATE_FILE.write_text(json.dumps({"pid": os.getpid(), "started": time.time()}), encoding="utf-8")
    WANT_VOICE.parent.mkdir(parents=True, exist_ok=True)
    if not WANT_VOICE.exists():
        WANT_VOICE.write_text("1")
    longmem.start_background()

    from .ears import HALLUCINATIONS
    mouth = Mouth()
    threading.Thread(target=mouth.prepare_fillers, daemon=True).start()
    listener = Listener(config.GOVORILKA_LISTEN_DEVICE)
    listener.paused.set()
    _prepare_client()
    client = _launch_client()
    _log("система", f"зашла в Говорилку как «{config.GOVORILKA_NICK}» (голос {fish.voice_name(voice_id())}, "
                    f"мозг {brain_model()})")
    st = {"history": [], "last_reply_at": 0.0, "chain": 0, "morni_until": 0.0, "present": [],
          "dialog_with": "", "dialog_until": 0.0}
    voice_state: bool | None = None
    next_check = 0.0
    pending, pending_at = "", 0.0                         # недоговорённая фраза
    started_at, last_initiative, next_initiative_quiet = time.time(), time.time(), 90.0
    relaunches = 0
    pending_who = ""                                      # кто сказал недоговорённую фразу (по подсветке в окне)
    watch = SpeakerWatch(lambda: client.pid)
    listener.speaker_now = watch.current                  # слух режет фразы, когда сменился говорящий
    global TALKING_NOW
    TALKING_NOW = lambda: bool(watch.current())           # noqa: E731 — кто-то говорит — не вступаем
    spec, spec_text = None, ""                            # ответ, обдумываемый заранее, и для какой фразы
    roster: set | None = None                             # кто сейчас в войсе (для «привет»/«пока»)
    joined: dict = {}
    greeted: dict = {}
    last_quiet_talk = time.time()

    def save_state():
        STATE_FILE.write_text(json.dumps({"pid": os.getpid(), "client": client.pid, "in_voice": voice_state}),
                              encoding="utf-8")
    try:
        time.sleep(10)                                    # дать Говорилке открыться и подключиться
        while RUN_FLAG.exists():
            # Говорилка закрылась или вылетела — открываем снова (бот сидит всегда)
            if client.poll() is not None:
                relaunches += 1
                _log("система", "Говорилка закрылась — захожу снова")
                listener.paused.set()
                voice_state = None
                time.sleep(min(60, 5 * relaunches))
                if not RUN_FLAG.exists():
                    break
                client = _launch_client()
                save_state()
                time.sleep(10)
                continue
            # раз в 8 секунд: там ли Мартышка, где брат хочет (в войсе / не в войсе); выкинуло — заходит снова
            if time.time() >= next_check:
                next_check = time.time() + 8
                try:
                    actual = in_voice(client.pid)
                    if want_voice() and actual is False:
                        listener.paused.set()
                        if join_voice(client.pid):
                            _log("система", "зашла в войс")
                            relaunches = 0
                            time.sleep(1.5)
                            if time.time() - started_at < 120:
                                _greet_and_check_echo(mouth, listener)
                            actual = True
                    elif not want_voice() and actual:
                        listener.paused.set()
                        mouth.say("Ладно, выхожу с войса. Пока!")
                        if leave_voice(client.pid):
                            _log("система", "вышла с войса")
                            actual = False
                    if actual is not None and actual != voice_state:
                        voice_state = actual
                        save_state()
                except Exception as e:
                    _log("ошибка", f"войс: {type(e).__name__}: {e}")
                if voice_state:
                    listener.paused.clear()
                else:
                    listener.paused.set()
            try:
                audio = listener.phrases.get(timeout=0.25)
            except queue.Empty:
                audio = None
                if not pending:
                    _reload_settings()                    # голос и мозг можно поменять из Студии на ходу
            if not voice_state:
                pending = ""
                continue
            try:
                # СЛЫШАТЬ СОБЕСЕДНИКА ДО КОНЦА: забираем из очереди ВСЁ, что уже нарезано, и склеиваем
                # в одну реплику — иначе человек говорит с паузами, а Мартышка отвечает на половину фразы
                chunks = [audio] if audio is not None else []
                while True:
                    try:
                        chunks.append(listener.phrases.get_nowait())
                    except queue.Empty:
                        break
                if watch.present:
                    st["present"] = list(watch.present)
                    # кто бывает на сервере — чтобы понять, что фраза обращена к другому («Энбулих, …»)
                    try:
                        from . import longmem
                        KNOWN_NAMES[:] = sorted(set(watch.present) | set(longmem.people().keys()))
                    except Exception:
                        KNOWN_NAMES[:] = list(watch.present)
                for item in chunks:
                    chunk, c_t0, c_t1 = item[:3]
                    text = item[3] if len(item) > 3 else _hear(chunk, HALLUCINATIONS)
                    if text:
                        watch.last_overlap = []
                        name = watch.who(c_t0, c_t1)
                        if name:
                            _log("это сказал(а)", name)
                        elif watch.last_overlap:
                            # говорили одновременно — чья фраза, неясно: никому не приписываем (память не портим)
                            _log("говорили вместе", " и ".join(watch.last_overlap))
                            if pending:
                                _respond(pending, mouth, listener, st, speaker=pending_who)
                                pending = ""
                            pending_who = ""
                        # заговорил другой человек — прошлая фраза была не его: разбираем её отдельно
                        if pending and name and pending_who and name != pending_who:
                            _respond(pending, mouth, listener, st, speaker=pending_who)
                            pending = ""
                        pending = f"{pending} {text}".strip()
                        pending_at = time.time()
                        pending_who = name or pending_who
                        if spec:                              # человек продолжил — заготовку ответа выбрасываем
                            spec.cancel()
                            spec = None
                # ПРИВЕТ И ПОКА: кто зашёл в войс — поздороваться по имени, кто ушёл — попрощаться
                if not watch.in_room:
                    roster = None
                elif getattr(config, "GOVORILKA_GREET", True) and not pending and not listener.hearing:
                    now_set = {n for n in watch.present if not n.lower().startswith(("морни", "мурни", "morni"))}
                    if roster is None:
                        roster = now_set                      # кто уже сидел, когда зашла сама, — не здороваемся
                        joined.update({n: time.time() for n in now_set})
                    elif now_set != roster:
                        came = [n for n in now_set - roster
                                if time.time() - greeted.get(n, 0) > GREET_AGAIN_AFTER]
                        gone = [n for n in roster - now_set if time.time() - joined.get(n, 0) > 60]
                        for n in now_set - roster:
                            joined[n] = time.time()
                        roster = now_set
                        if came:
                            for n in came:
                                greeted[n] = time.time()
                            who_c = ", ".join(came)
                            _speak_own(f"(в войс только что зашёл {who_c}. Поздоровайся по имени — коротко и "
                                       "по-своему, можешь вспомнить что-то о нём или спросить про то, что он любит; "
                                       "если ты на него зла — это слышно)", mouth, listener, st, who=came[0],
                                       tag=f"привет: {who_c}")
                            continue
                        if gone:
                            who_g = ", ".join(gone)
                            _speak_own(f"(из войса только что вышел {who_g}. Попрощайся коротко — или скажи "
                                       "остальным что-нибудь о нём по-дружески)", mouth, listener, st,
                                       who=gone[0], tag=f"пока: {who_g}")
                            continue
                # кнопка в окне: брат велел начать разговор с Морни прямо сейчас (в любом режиме)
                if MORNI_NOW_FLAG.exists() and not listener.hearing:
                    try:
                        topic = json.loads(MORNI_NOW_FLAG.read_text(encoding="utf-8") or "{}").get("topic", "")
                    except Exception:
                        topic = ""
                    MORNI_NOW_FLAG.unlink(missing_ok=True)
                    last_initiative = time.time()
                    if topic:
                        start_debate(topic)
                        _respond(f"(брат устроил дебаты: ты против Морни, тема «{DEBATE['topic']}». Объяви "
                                 "зрителям на сервере начало спора одной фразой, потом обратись к Морни, займи "
                                 "свою позицию и брось ей вызов)", mouth, listener, st, initiative=True)
                        st["morni_until"] = DEBATE["until"]
                    else:
                        _log("система", "брат попросил начать разговор с Морни")
                        _respond("(брат попросил: заведи разговор с Морни прямо сейчас — обратись к ней, задай "
                                 "колкий или интересный вопрос, можешь вспомнить что-то из прошлых разговоров)",
                                 mouth, listener, st, initiative=True)
                    continue
                # сама заводит разговор с Морни, когда в войсе давно тихо (не чаще раза в MORNI_START_EVERY)
                quiet_for = time.time() - max(listener.last_voice_at, st["last_reply_at"], started_at)
                if (not config.GOVORILKA_DIALOG and not pending and not listener.hearing
                        and quiet_for > next_initiative_quiet
                        and time.time() - last_initiative > MORNI_START_EVERY):
                    import random
                    last_initiative = time.time()
                    next_initiative_quiet = random.uniform(45, 120)
                    _log("система", "сама начинаю разговор с Морни")
                    _respond("(в войсе тихо — заведи разговор с Морни: задай ей колкий или интересный вопрос, "
                             "можешь вспомнить что-то из прошлых разговоров)", mouth, listener, st, initiative=True)
                # в войсе давно тихо, а люди сидят — сама решает, заговорить ли (о том, что им интересно)
                people = [n for n in (roster or set())]
                if (getattr(config, "GOVORILKA_CHIME", False) and watch.in_room and people and not pending
                        and not listener.hearing and quiet_for > QUIET_TALK_AFTER
                        and time.time() - last_quiet_talk > QUIET_TALK_EVERY):
                    last_quiet_talk = time.time()
                    _speak_own(f"(в войсе тихо уже несколько минут. Здесь: {', '.join(people)}. Хочешь — "
                               "заговори с кем-то из них о том, что ему интересно (смотри «любит говорить о»), "
                               "подколи или расскажи что-то своё. Не хочешь — ответь ровно [МОЛЧУ])",
                               mouth, listener, st, who=people[0], tag="сама заговорила в тишине")
                    continue
                # отвечаем, только когда собеседник ТОЧНО договорил: фраза закончена, в очереди пусто
                # (никаких ещё не распознанных кусков) и стоит тишина нужной длины
                idle = pending and not listener.hearing and listener.phrases.empty()
                if idle:
                    quiet = time.time() - max(pending_at, listener.last_voice_at)
                    finished = sentence_finished(pending)
                    # БЫСТРЕЕ: фраза похожа на законченную — начинаем думать над ответом уже во время паузы
                    if (spec is None and finished and quiet >= PRETHINK_AFTER
                            and getattr(config, "GOVORILKA_PRETHINK", True)):
                        msgs = _predict(pending, pending_who, st)
                        if msgs:
                            from . import longmem
                            spec = Prethink(msgs, longmem.anger(_who_of(pending_who, st)))
                            spec_text = pending
                    # МОРНИ говорит длинно, с паузами между предложениями (пока генерирует следующее) — после
                    # её фраз ждём дольше и только когда её подсветка погасла на несколько секунд, иначе перебьём
                    from_morni = pending_who.lower().startswith(MORNI_NAMES) or (
                        not pending_who and time.time() < st["morni_until"])
                    need = QUIET_MORNI if from_morni else (QUIET_SENTENCE if finished else QUIET_UNFINISHED)
                    morni_busy = from_morni and watch.lit_within(QUIET_MORNI - 1.0, MORNI_NAMES)
                    if quiet >= need and not morni_busy and not watch.current():
                        text, pending = pending, ""
                        pre = spec if (spec and spec_text == text) else None
                        _respond(text, mouth, listener, st, speaker=pending_who, pre=pre)
                        if spec:
                            spec.cancel()
                        spec = None
                        pending_who = ""
                    elif quiet >= QUIET_FORGET:
                        # фраза так и не к нам (не назвали по имени) — забываем, чтобы не приклеилась к следующей
                        _log("мимо", pending)
                        pending = ""
            except Exception as e:                       # одна неудачная фраза не должна выкидывать из сервера
                pending = ""
                _log("ошибка", f"{type(e).__name__}: {e}")
    finally:
        try:
            if voice_state:
                listener.paused.set()
                mouth.say("Ладно, я пошла. Пока!")
        except Exception:
            pass
        if client.poll() is None:
            client.terminate()                            # закрываем только СВОЮ копию Говорилки
        # флаг убираем, только если это мы уходим насовсем (а не новая копия уже записала свой)
        try:
            if json.loads(STATE_FILE.read_text(encoding="utf-8")).get("pid") == os.getpid():
                RUN_FLAG.unlink(missing_ok=True)
        except Exception:
            pass
        _log("система", "вышла из Говорилки")


def _greet_and_check_echo(mouth, listener) -> None:
    """Поздороваться при входе и заодно проверить, не слышит ли Мартышка в «петле» сама себя.
    Если в тишине во время её приветствия «кто-то говорит» больше половины времени — это её же голос
    (эхо): тогда режим «уступать слово» выключается, иначе она обрывала бы каждый свой ответ."""
    quiet_before = time.time() - listener.last_voice_at > 1.5
    samples: list[bool] = []
    done = threading.Event()

    def sample():
        while not done.is_set():
            samples.append(bool(listener.hearing))
            time.sleep(0.1)
    threading.Thread(target=sample, daemon=True).start()
    mouth.say("Всем привет! Это Мартышка. Зовите по имени, если что.")
    done.set()
    if quiet_before and len(samples) >= 10:
        share = sum(samples) / len(samples)
        ECHO["heard_self"] = share > 0.5
        _log("система", f"самопроверка эха: свой голос {'СЛЫШЕН' if ECHO['heard_self'] else 'не слышен'} "
                        f"({share:.0%}) — {'уступать слово не буду' if ECHO['heard_self'] else 'уступаю слово, если перебьют'}")


def _hear(audio, hallucinations) -> str:
    """Распознать кусок речи через DeepSeek."""
    from . import deepseek
    pad = np.zeros(int(SR * 0.25), dtype=np.float32)
    try:
        text = deepseek.transcribe(np.concatenate([pad, audio.astype(np.float32), pad]), SR).strip()
    except Exception as e:
        _log("ошибка", f"уши: {e}")
        return ""
    if not text or any(h in text.lower() for h in hallucinations) or len(text) < 3:
        return ""
    _log("сервер", text)
    return text


def _who_of(speaker: str, st: dict) -> str:
    from . import longmem
    who = longmem.norm(speaker) if speaker else SERVER
    # человек не назвался, но разговор продолжается — скорее всего, говорит тот же, кого узнали только что
    if not speaker and st.get("last_who") and time.time() - st.get("last_who_at", 0) < config.GOVORILKA_FOLLOWUP + 15:
        who = st["last_who"]
    return who


def _predict(text: str, speaker: str, st: dict) -> list[dict] | None:
    """Ответит ли Мартышка на эту фразу (как решит _respond) — и если да, с какими сообщениями к мозгу.
    Ничего не меняет: нужно, чтобы начать думать заранее, во время паузы."""
    from . import longmem
    who = _who_of(speaker, st)
    now = time.time()
    named = addressed_to_her(text)
    same = named or (bool(st.get("dialog_with")) and who == st.get("dialog_with") and now < st.get("dialog_until", 0))
    asked = bool(MORNI_TALK_RE.search(text)) and (named or now - st["last_reply_at"] < config.GOVORILKA_FOLLOWUP)
    morni_until = st["morni_until"]
    if asked:
        morni_until = now + MORNI_MINUTES * 60
    if speaker.lower().startswith(("морни", "мурни", "morni")):
        morni_until = max(morni_until, now + MORNI_MINUTES * 60)
    with_morni = now < morni_until
    ok, _ = should_answer(text, st["last_reply_at"], st["chain"], with_morni, same)
    if not (ok or asked):
        return None
    hist = st["history"] + [{"role": "user", "content": f"({who}): {text}"}]
    return build_msgs(hist, longmem.anger(who), with_morni, who, st["present"])


def _speak_own(instruction: str, mouth, listener, st, who: str = "", tag: str = "") -> str:
    """Мартышка говорит сама, без вопроса: поздороваться, попрощаться, заговорить, когда тихо.
    Она может и промолчать — ответить [МОЛЧУ]. Возвращает сказанное."""
    from . import longmem
    history = st["history"]
    history.append({"role": "user", "content": instruction})
    listener.paused.set()
    mouth.stopped.clear()
    sw = StopWatcher(mouth, listener)
    try:
        mouth.first_audio_at = 0.0
        mouth.synth_stats.clear()
        mouth.anger = longmem.anger(who) if who else 0
        raw = reply_stream(history, mouth, mouth.anger, False, who=who, present=st["present"])
        mouth.wait_yielding(listener)
    finally:
        sw.finish()
        mouth.stopped.clear()
        listener.paused.clear()
    _take_tags(raw, who)
    answer = _clean(raw)
    if answer:
        history.append({"role": "assistant", "content": answer})
        _log(config.GOVORILKA_NICK, f"{answer}   [{tag or 'сама'}]")
        longmem.log("говорилка", config.NAME, "", answer)
        st["last_reply_at"], st["chain"] = time.time(), 0
    else:
        history.pop()                                   # промолчала — подсказку из истории убираем
        _log("система", f"{tag or 'сама'}: решила промолчать")
    return answer


def _take_tags(raw: str, who: str) -> None:
    """Служебные метки ответа: факты о людях, их любимые темы, уроки."""
    from . import longmem
    for name, fact in ABOUT_RE.findall(raw):
        longmem.remember_about(name, fact)
    for name, topic in TOPIC_RE.findall(raw):
        if longmem.add_topic(name, topic):
            _log("система", f"запомнила: {name.strip()} любит говорить о «{topic.strip()}»")
    try:
        from . import growth
        growth.take_tags(raw, "говорилка")
    except Exception:
        pass


def _respond(text, mouth, listener, st, initiative=False, speaker="", pre=None):
    """Договорённая фраза: решить, отвечать ли, и ответить голосом. st — состояние разговора (история, цепочка…).
    speaker — кто сказал (если известно), initiative — Мартышка сама начинает разговор с Морни,
    pre — ответ, который начали обдумывать во время паузы (Prethink)."""
    from . import longmem
    t0 = time.time()
    history = st["history"]
    if initiative:
        history.append({"role": "user", "content": text})
        listener.paused.set()
        sw = StopWatcher(mouth, listener)
        try:
            mouth.first_audio_at = 0.0
            mouth.synth_stats.clear()
            mouth.stopped.clear()
            mouth.anger = longmem.anger("Морни")
            raw = reply_stream(history, mouth, mouth.anger, with_morni=True, who="Морни",
                               present=st["present"])
            mouth.wait_stoppable(listener)
        finally:
            sw.finish()
            mouth.stopped.clear()
            listener.paused.clear()
        answer = _clean(raw)
        if answer:
            history.append({"role": "assistant", "content": answer})
            _log(config.GOVORILKA_NICK, f"{answer}   [сама начала разговор с Морни]")
            longmem.log("говорилка", config.NAME, "", answer)
        st["last_reply_at"], st["chain"] = time.time(), 0
        st["morni_until"] = time.time() + MORNI_MINUTES * 60
        return
    who = _who_of(speaker, st)
    if is_stop(text):                                   # «Мартышка, стоп» — молчим, ничего не отвечаем
        _log("система", f"«{text}» — молчу")
        longmem.log("говорилка", who, text)
        return
    history.append({"role": "user", "content": f"({who}): {text}"})
    if speaker.lower().startswith(("морни", "мурни", "morni")):
        st["morni_until"] = max(st["morni_until"], time.time() + MORNI_MINUTES * 60)
    named = addressed_to_her(text)
    to_other = "" if named else addressed_to_other(text)
    if to_other:
        _log("система", f"это к {to_other} — слушаю, не отвечаю")
    # режим диалога: кто позвал по имени — тому Мартышка и отвечает дальше без обращения
    if named:
        st["dialog_with"] = who if who != SERVER else st.get("dialog_with", "")
        st["dialog_until"] = time.time() + config.GOVORILKA_FOLLOWUP
    same_speaker = bool(st.get("dialog_with")) and who == st.get("dialog_with") \
        and time.time() < st.get("dialog_until", 0.0)
    asked_morni = bool(MORNI_TALK_RE.search(text)) and (
        named or time.time() - st["last_reply_at"] < config.GOVORILKA_FOLLOWUP)
    if asked_morni:
        st["morni_until"] = time.time() + MORNI_MINUTES * 60    # брат попросил поболтать с Морни
        _log("система", "болтаю с Морни")
        d = DEBATE_RE.search(text)
        if d:                                                   # «поспорь с Морни о …» — дебаты на тему
            start_debate(d.group(1))
            st["morni_until"] = DEBATE["until"]
    if not debate_on() and DEBATE["topic"]:
        _log("система", f"дебаты «{DEBATE['topic']}» закончились")
        DEBATE["topic"] = ""
    with_morni = time.time() < st["morni_until"]
    ok, st["chain"] = should_answer(text, st["last_reply_at"], st["chain"], with_morni, same_speaker)
    if asked_morni:
        ok, st["chain"] = True, 0
    anger = longmem.anger(who)
    # САМА РЕШАЕТ, КОГДА ВМЕШАТЬСЯ: её не звали, но иногда (не чаще CHIME_EVERY) она прислушивается — и если
    # правда есть что сказать к месту, вступает; иначе молчит ([МОЛЧУ]). Говорит только в тишине — не перебивает.
    note = ""
    if (not ok and getattr(config, "GOVORILKA_CHIME", False) and len(text) > 20 and not with_morni
            and time.time() - max(st["last_reply_at"], st.get("last_chime", 0.0)) > CHIME_EVERY):
        import random
        if random.random() < CHIME_CHANCE:
            st["last_chime"] = time.time()
            ok, pre = True, None
            note = ("Тебя сейчас НЕ звали — это разговор людей между собой. Вмешайся, только если правда есть "
                    "что сказать к месту: смешная реплика, ответ на вопрос, который ты знаешь, своё мнение в споре, "
                    "тема, которую человек любит. Коротко, по-живому. Если нечего — ответь ровно [МОЛЧУ].")
    if not ok:
        longmem.log("говорилка", who, text)
        if with_morni and st["chain"] >= config.GOVORILKA_MAX_CHAIN * 2:
            st["morni_until"] = 0.0
        return
    # кто-то начал говорить, пока мы решали, — не перебиваем: ждём, пока он договорит (до 10 секунд),
    # и заодно, пока опустеет очередь ещё не распознанных кусков его речи
    wait_until = time.time() + 15
    while (listener.hearing or not listener.phrases.empty() or TALKING_NOW()) and time.time() < wait_until:
        time.sleep(0.1)
    listener.paused.set()
    yielded = False
    mouth.stopped.clear()
    sw = StopWatcher(mouth, listener)
    try:
        mouth.first_audio_at = 0.0
        mouth.synth_stats.clear()
        if named and not with_morni:
            mouth.filler(anger)                         # «Ага» сразу, пока мозг думает
        mouth.anger = anger
        raw = reply_stream(history, mouth, anger, with_morni, who=who, present=st["present"], note=note, pre=pre)
        t_text = time.time() - t0
        yielded = mouth.wait_yielding(listener)         # режим диалога: перебили — замолкаем, не говорим поверх
    finally:
        if sw.finish():
            yielded = False                             # замолчала по «стоп» — уже записано в журнал
        mouth.stopped.clear()
        listener.paused.clear()
    if yielded:
        _log("система", "замолчала — собеседник заговорил, слушаю его")
    # служебные метки: злость, кто говорил, факты о людях
    m = None if speaker else WHO_RE.search(raw)       # имя с экрана точнее догадки мозга
    if m and m.group(1).strip().lower() not in ("кто-то", "неизвестно", "?"):
        real = m.group(1).strip()
        history[-1]["content"] = f"({real}): {text}"
        if real not in st["present"]:
            st["present"] = (st["present"] + [real])[-8:]
        who = real
    if who != SERVER:
        st["last_who"], st["last_who_at"] = who, time.time()
    tone = TONE_RE.search(raw)
    if tone:
        new_anger = longmem.set_anger(who, int(tone.group(1)))
        if new_anger != anger:
            _log("система", f"злость на «{who}»: {anger} → {new_anger}")
        anger = new_anger
    _take_tags(raw, who)
    answer = _clean(raw)
    longmem.log("говорилка", who, text, answer)
    if not answer:
        if note:
            _log("система", "прислушалась к разговору — решила не вмешиваться")
        return
    history.append({"role": "assistant", "content": answer})
    del history[:-60]
    first = (mouth.first_audio_at - t0) if mouth.first_audio_at else -1
    sound = sum(x for x, _ in mouth.synth_stats)
    synth = sum(t for _, t in mouth.synth_stats)
    _log(config.GOVORILKA_NICK, f"{answer}   [злость {anger}/5{', с Морни' if with_morni else ''}; текст {t_text:.1f} с, "
                                f"первый звук через {first:.1f} с, голос {synth:.1f} с на {sound:.1f} с речи]")
    st["last_reply_at"] = time.time()


def _clean(raw: str) -> str:
    """Ответ без служебных меток (эмоции {…} оставляем в истории — так мозг помнит, как говорил)."""
    return re.sub(r"\s+", " ", ANY_TAG_RE.sub("", raw or "")).strip()


if __name__ == "__main__":
    if sys.stdout is None or sys.stderr is None:          # запуск без консоли (pythonw) — служебный вывод в файл
        BOT_DIR.mkdir(parents=True, exist_ok=True)
        sys.stdout = sys.stderr = open(BOT_DIR / "вывод.txt", "a", encoding="utf-8", buffering=1)
    for attempt in range(1000):                           # упала — встаёт снова (бот сидит всегда)
        try:
            main()
            break
        except Exception as e:
            _log("ошибка", f"{type(e).__name__}: {e} — перезапускаюсь через минуту")
            if not RUN_FLAG.exists() or LEFT_FLAG.exists():
                break
            time.sleep(60)
    RUN_FLAG.unlink(missing_ok=True) if LEFT_FLAG.exists() else None
