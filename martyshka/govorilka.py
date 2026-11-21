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
QUIET_SENTENCE = 5.0      # с: столько тишины после ЗАКОНЧЕННОЙ фразы — и только тогда отвечаем (брат: 5 с)
QUIET_UNFINISHED = 7.0    # с: столько тишины после оборванной фразы (ждём, что человек ещё договорит)
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
              "BRAIN_MODE", "DEEPSEEK_KEY", "FISH_KEY", "FISH_MODEL", "GOVORILKA_DIALOG"):
        if k in saved:
            setattr(config, k, saved[k])


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
                recent.append(block)
                p = self._prob(np.concatenate(recent))
                speaking_now = p > config.SPEECH_THRESHOLD
                self.hearing = speaking_now or (heard and silence < 0.5)
                if speaking_now:
                    self.last_voice_at = time.time()
                # пока Мартышка говорит — обычно не слушаем; в режиме диалога слушаем и тогда: если её перебили,
                # фраза собеседника не должна потеряться (свой голос в эту петлю не попадает — сервер его не возвращает)
                if self.paused.is_set() and not (config.GOVORILKA_DIALOG and not ECHO["heard_self"]
                                                 and (speaking_now or heard)):
                    speech, silence, heard = [], 0.0, False
                    continue
                if speaking_now:
                    if not heard:
                        speech = list(recent)[:-1]       # немножко звука до начала речи
                    heard, silence = True, 0.0
                    speech.append(block)
                elif heard:
                    speech.append(block)
                    silence += BLOCK / SR
                    if silence >= SPEECH_GAP or len(speech) * BLOCK / SR > 25:
                        audio = np.concatenate(speech)
                        if len(audio) / SR > 0.6:
                            self.phrases.put(audio)
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
                audio = self.fish.speak_all(text, voice_id(), temperature=0.75)
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

    def wait_yielding(self, listener) -> bool:
        """Договорить ответ, но в режиме диалога уступить: кто-то заговорил поверх — замолкаем.
        Возвращает True, если пришлось замолчать."""
        talk_since = None
        while self._text_q.unfinished_tasks or self._audio_q.unfinished_tasks:
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
                    a = self.fish.speak_all(w, self._filler_voice)
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
MORNI_TALK_RE = re.compile(r"(пообща|поговор|побол|поспор|спроси|скажи|ответь|расскажи|познаком|позови|обратись)"
                           r"\w*.{0,25}\bм[оу]рни", re.I)
MORNI_RE = re.compile(r"\bм[оу]рни\w*", re.I)
MORNI_MINUTES = 4          # сколько длится разговор с Морни после просьбы
MORNI_START_EVERY = 6 * 60  # не чаще: сама заводит разговор с Морни, когда в войсе тихо
SERVER = "кто-то на сервере"   # когда неизвестно, кто говорит
TONE_RE = re.compile(r"\[\s*ТОН\s*:\s*(\d)\s*\]", re.I)
WHO_RE = re.compile(r"\[\s*КТО\s*:\s*([^\]]{1,40})\]", re.I)
ABOUT_RE = re.compile(r"\[\s*ЗАПОМНИ\s+О\s*:\s*([^|\]]+?)\s*\|\s*(.+?)\s*\]", re.I | re.S)
ANY_TAG_RE = re.compile(r"\[[^\]]*\]?")
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
            "Морни — бот, она слышит только фразы, которые НАЧИНАЮТСЯ с её имени: обращаешься к Морни — ВСЕГДА "
            "начинай фразу со слов «Морни,».\n"
            "ГОЛОС И ПОДАЧА — режиссируешь сама, как актриса (люди меток не видят, они их слышат): указание перед "
            "фразой в фигурных скобках своими словами, можно по-русски — {холодно и насмешливо}, {сдерживая смех}, "
            "{медленно, с угрозой в голосе}, {быстро, взахлёб}; звуки и паузы там, где они звучат — {смех}, "
            "{смешок}, {хохочет}, {фальшивый смех}, {вздох}, {тяжёлый вздох}, {фыркает}, {хмыкает}, {цокает}, "
            "{пауза}, {длинная пауза}, {зевает}; отдельные слова — <whisper>…</whisper>, <emphasis>…</emphasis>, "
            "<stress>…</stress>, <soft>…</soft>, <forceful>…</forceful>, <slow>…</slow>, <fast>…</fast>. "
            "Сколько и когда — решаешь сама.\n"
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


def reply_stream(history: list[dict], mouth: "Mouth", anger: int = 0, with_morni: bool = False, who: str = "",
                 present: list[str] | None = None) -> str:
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
    last = next((m["content"] for m in reversed(history) if m["role"] == "user"), "")
    msgs = [{"role": "system", "content": system_prompt(anger, with_morni, who, present, last)}] + history[-40:]
    full, buf = "", ""
    for kind, val in deepseek.stream(msgs, model=brain_model(), think=False,
                                     temperature=0.95, max_tokens=4000):
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


def should_answer(text: str, last_reply_at: float, chain: int, with_morni: bool = False,
                  same_speaker: bool = False) -> tuple[bool, int]:
    """Ответить ли на услышанное. Возвращает (ответить, новая длина цепочки ответов без имени).

    Главное правило (просьба брата): Мартышка откликается ТОЛЬКО когда её позвали по имени.
    Дальше — «режим диалога»: тот собеседник, который её позвал, может продолжать и без имени
    (config.GOVORILKA_FOLLOWUP секунд, не больше GOVORILKA_MAX_CHAIN реплик подряд)."""
    if NAME_RE.search(text):
        return True, 0
    limit = config.GOVORILKA_MAX_CHAIN * (2 if with_morni else 1)   # с Морни разговор может быть длиннее
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
                for chunk in chunks:
                    text = _hear(chunk, HALLUCINATIONS)
                    if text:
                        pending = f"{pending} {text}".strip()
                        pending_at = time.time()
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
                # отвечаем, только когда собеседник ТОЧНО договорил: фраза закончена, в очереди пусто
                # (никаких ещё не распознанных кусков) и стоит тишина нужной длины
                idle = pending and not listener.hearing and listener.phrases.empty()
                if idle:
                    quiet = time.time() - max(pending_at, listener.last_voice_at)
                    if quiet >= (QUIET_SENTENCE if sentence_finished(pending) else QUIET_UNFINISHED):
                        text, pending = pending, ""
                        _respond(text, mouth, listener, st)
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


def _respond(text, mouth, listener, st, initiative=False, speaker=""):
    """Договорённая фраза: решить, отвечать ли, и ответить голосом. st — состояние разговора (история, цепочка…).
    speaker — кто сказал (если известно), initiative — Мартышка сама начинает разговор с Морни."""
    from . import longmem
    t0 = time.time()
    history = st["history"]
    if initiative:
        history.append({"role": "user", "content": text})
        listener.paused.set()
        try:
            mouth.first_audio_at = 0.0
            mouth.synth_stats.clear()
            raw = reply_stream(history, mouth, longmem.anger("Морни"), with_morni=True, who="Морни",
                               present=st["present"])
            mouth.wait()
        finally:
            listener.paused.clear()
        answer = _clean(raw)
        if answer:
            history.append({"role": "assistant", "content": answer})
            _log(config.GOVORILKA_NICK, f"{answer}   [сама начала разговор с Морни]")
            longmem.log("говорилка", config.NAME, "", answer)
        st["last_reply_at"], st["chain"] = time.time(), 0
        st["morni_until"] = time.time() + MORNI_MINUTES * 60
        return
    who = speaker or SERVER
    # человек не назвался, но разговор продолжается — скорее всего, говорит тот же, кого узнали только что
    if not speaker and st.get("last_who") and time.time() - st.get("last_who_at", 0) < config.GOVORILKA_FOLLOWUP + 15:
        who = st["last_who"]
    history.append({"role": "user", "content": f"({who}): {text}"})
    if speaker.lower().startswith(("морни", "мурни", "morni")):
        st["morni_until"] = max(st["morni_until"], time.time() + MORNI_MINUTES * 60)
    named = bool(NAME_RE.search(text))
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
    with_morni = time.time() < st["morni_until"]
    ok, st["chain"] = should_answer(text, st["last_reply_at"], st["chain"], with_morni, same_speaker)
    if asked_morni:
        ok, st["chain"] = True, 0
    anger = longmem.anger(who)
    # злая Мартышка (злость 4–5) сама влезает в чужие разговоры с колкостью — но не чаще раза в 25 секунд
    if CHIME_IN and not ok and anger >= 4 and len(text) > 15 and time.time() - st["last_reply_at"] > 25:
        import random
        ok = random.random() < 0.4
    if not ok:
        longmem.log("говорилка", who, text)
        if with_morni and st["chain"] >= config.GOVORILKA_MAX_CHAIN * 2:
            st["morni_until"] = 0.0
        return
    # кто-то начал говорить, пока мы решали, — не перебиваем: ждём, пока он договорит (до 10 секунд),
    # и заодно, пока опустеет очередь ещё не распознанных кусков его речи
    wait_until = time.time() + 10
    while (listener.hearing or not listener.phrases.empty()) and time.time() < wait_until:
        time.sleep(0.1)
    listener.paused.set()
    yielded = False
    try:
        mouth.first_audio_at = 0.0
        mouth.synth_stats.clear()
        mouth.stopped.clear()
        if named and not with_morni:
            mouth.filler(anger)                         # «Ага» сразу, пока мозг думает
        raw = reply_stream(history, mouth, anger, with_morni, who=who, present=st["present"])
        t_text = time.time() - t0
        yielded = mouth.wait_yielding(listener)         # режим диалога: перебили — замолкаем, не говорим поверх
    finally:
        mouth.stopped.clear()
        listener.paused.clear()
    if yielded:
        _log("система", "замолчала — собеседник заговорил, слушаю его")
    # служебные метки: злость, кто говорил, факты о людях
    m = WHO_RE.search(raw)
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
    for name, fact in ABOUT_RE.findall(raw):
        longmem.remember_about(name, fact)
    try:
        from . import growth
        growth.take_tags(raw, "говорилка")
    except Exception:
        pass
    answer = _clean(raw)
    longmem.log("говорилка", who, text, answer)
    if not answer:
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
