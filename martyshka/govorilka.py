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


def quit_server() -> str:
    """Совсем уйти с сервера и закрыть свою копию Говорилки."""
    if RUN_FLAG.exists():
        RUN_FLAG.unlink()
        return "Ухожу с сервера совсем."
    return "Меня и так нет на сервере."


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
    for k in ("GOVORILKA_SPEAKER", "GOVORILKA_MODEL", "GOVORILKA_FOLLOWUP", "GOVORILKA_MAX_CHAIN", "MODEL",
              "GOVORILKA_PERSONA"):
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


QUIT_RE = re.compile(rf"(уйди|выйди|отключись)\w*\s+(совсем|полностью)|(выключи|закрой)\w*.{{0,10}}{VOICE_WORDS}|"
                     r"(уйди|выйди)\w*\s+(с|из)\s+сервер", re.I)


EVIL_RE = re.compile(r"\b(сделай|стань|будь|включи|переключи)\w*\b.{0,30}\b(зл[аоу]|злой|злую|вредн|токсичн|стерв)", re.I)
KIND_RE = re.compile(r"\b(сделай|стань|будь|включи|переключи)\w*\b.{0,30}\b(добр|вежлив|нормальн|мил[аоу]|обычн)", re.I)


def handle_command(text: str) -> str | None:
    """Быстрые команды без мозга. Возвращает ответ или None (это не команда для Говорилки)."""
    about_her = re.search(r"(говорилк|в[оа]йс|себя|\bеё\b|\bее\b|мартышк|^\s*(будь|стань))", text, re.I)
    if EVIL_RE.search(text) and about_her:
        save_setting("GOVORILKA_PERSONA", "злая")
        return "Готово: в Говорилке я теперь злая — язвлю всем подряд (без угроз и оскорблений по национальности и т.п.)."
    if KIND_RE.search(text) and about_her:
        save_setting("GOVORILKA_PERSONA", "авто")
        return "Готово: в Говорилке я снова вежливая (дерзить буду только в ответ на оскорбления)."
    if QUIT_RE.search(text):
        return quit_server()
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
                if self.paused.is_set():
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
                    if silence >= 0.7 or len(speech) * BLOCK / SR > 25:
                        audio = np.concatenate(speech)
                        if len(audio) / SR > 0.6:
                            self.phrases.put(audio)
                        speech, silence, heard = [], 0.0, False


# ---------- голос ----------
class Mouth:
    """Встроенный голос Qwen3-TTS → виртуальный кабель (= микрофон Мартышкиной Говорилки).

    Чтобы голос не рвался: каждая фраза сначала синтезируется ЦЕЛИКОМ, и только потом играет одним куском.
    Пока звучит одна фраза — следующая уже синтезируется (два потока: «синтез» и «динамик»)."""

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
        self.stream = sd.OutputStream(samplerate=self.sr, channels=1, dtype="float32", device=idx,
                                      latency="high")
        self.stream.start()
        self._text_q: queue.Queue = queue.Queue()
        self._audio_q: queue.Queue = queue.Queue()
        self.first_audio_at = 0.0
        self.synth_stats: list[tuple[float, float]] = []      # (секунд звука, секунд синтеза)
        threading.Thread(target=self._synth_loop, daemon=True).start()
        threading.Thread(target=self._play_loop, daemon=True).start()

    def _synth_loop(self) -> None:
        while True:
            text = self._text_q.get()
            try:
                t0 = time.time()
                pieces = list(self.engine.synthesize(text))
                if pieces:
                    audio = np.concatenate(pieces).astype(np.float32)
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
                self.stream.write(np.clip(audio * 0.9, -1, 1).reshape(-1, 1))
                self.stream.write(np.zeros((int(self.sr * 0.12), 1), dtype=np.float32))
            finally:
                self._audio_q.task_done()

    def queue(self, sentence: str) -> None:
        clean = self.prepare(sentence, "ru")
        if re.search(r"\w", clean):
            self._text_q.put(clean)

    # Короткие «Ага» заготовлены заранее: звучат сразу, пока мозг думает над ответом
    FILLERS = {"вежливая": ["Ага.", "Так-так.", "Хм.", "Слушаю."], "дерзкая": ["Чего?", "Ну-ну.", "Ага, щас.", "Ой, всё."],
               "злая": ["Ну конечно.", "Боже мой.", "Опять ты.", "Как предсказуемо."]}

    def prepare_fillers(self) -> None:
        self.fillers = {}
        for mood, words in self.FILLERS.items():
            self.fillers[mood] = []
            for w in words:
                try:
                    self.fillers[mood].append(np.concatenate(list(self.engine.synthesize(self.prepare(w, "ru")))))
                except Exception:
                    pass

    def filler(self, mood: str) -> None:
        import random
        options = getattr(self, "fillers", {}).get(mood) or []
        if options:
            self._audio_q.put(random.choice(options).astype(np.float32))

    def wait(self) -> None:
        self._text_q.join()
        self._audio_q.join()
        time.sleep(0.4)                                   # доиграть буфер звуковой карты

    def say(self, text: str) -> None:
        from .voice import split_ready_sentences
        sentences, rest = split_ready_sentences(text + " ")
        for s in sentences + ([rest] if rest.strip() else []):
            self.queue(s)
        self.wait()


# ---------- мозг ----------
# ---------- два типа речи: вежливая и дерзкая ----------
# Грубость в адрес Мартышки: мат, «заткнись», «тупая» и т.п. Достаточно, чтобы фраза была к ней (имя или разговор).
INSULT_RE = re.compile(
    r"(тупая|тупица|дура\b|дурная|идиотк|дебил|кретин|овощ|бесполезн|никчемн|никчёмн|заткнись|завали|"
    r"отвали|отъеб|пошла на|иди на|иди в жоп|сука|сучк|бляд|блят|шлюх|мразь|тварь|уёб|уеб|хуйн|хуев|"
    r"пизд|еба[нл]|ёба[нл]|заебал|заебала|гандон|чмо|лох|лошар|ржавая железк|кусок железа|дерьм|говно)", re.I)
POLITE_RE = re.compile(r"(извини|прости|сорри|ладно,? мир|мир\b|не обижайся|молодец|умница|спасибо|люблю тебя)", re.I)
RUDE_MINUTES = 5          # сколько держится дерзкий режим без новых оскорблений


class Mood:
    def __init__(self):
        self.mode = "вежливая"
        self.until = 0.0

    def update(self, text: str, to_her: bool) -> str:
        now = time.time()
        if config.GOVORILKA_PERSONA == "злая":
            self.mode = "злая"
            return self.mode
        if self.mode == "злая":
            self.mode = "вежливая"
        if to_her and INSULT_RE.search(text):
            if self.mode != "дерзкая":
                _log("система", "меня оскорбили — включаю дерзкий режим")
            self.mode, self.until = "дерзкая", now + RUDE_MINUTES * 60
        elif self.mode == "дерзкая" and (POLITE_RE.search(text) and to_her or now > self.until):
            self.mode = "вежливая"
            _log("система", "остыла — снова вежливая")
        return self.mode


# Брат просит поболтать с Морни («пообщайся с Морни», «спроси у Морни…», «скажи Морни…»)
MORNI_TALK_RE = re.compile(r"(пообща|поговор|побол|поспор|спроси|скажи|ответь|расскажи|познаком|позови|обратись)"
                           r"\w*.{0,25}\bм[оу]рни", re.I)
MORNI_RE = re.compile(r"\bм[оу]рни\w*", re.I)
MORNI_MINUTES = 4          # сколько длится разговор с Морни после просьбы


def system_prompt(mode: str = "вежливая", with_morni: bool = False) -> str:
    base = (f"Ты — {config.NAME}, ИИ. Ты сидишь на голосовом сервере «Говорилка» вместе с братом (ник sitthome), "
            "его друзьями и другим ИИ по имени Морни. Ты — ИИ и не скрываешь этого. Ты слышишь общий звук "
            "сервера без имён говорящих: если не ясно, кто сказал, не выдумывай. Отвечай как в живом разговоре: "
            "ОДНО короткое предложение (максимум два), без списков, без markdown, без смайликов и эмодзи, на том "
            "языке, на котором к тебе обратились. Не повторяй одно и то же. Не обсуждай личные данные брата. "
            "Морни — бот, она слышит только фразы, которые НАЧИНАЮТСЯ с её имени: если обращаешься к Морни — "
            "ВСЕГДА начинай фразу со слов «Морни,». К людям обращайся как обычно. ")
    if with_morni:
        base += ("СЕЙЧАС ТЫ РАЗГОВАРИВАЕШЬ С МОРНИ (брат попросил): каждая твоя фраза начинается с «Морни,», "
                 "отвечай на её слова, задавай ей вопросы, шути, поддерживай живой разговор. ")
    if mode == "злая":
        return base + (
            "ТВОЙ ХАРАКТЕР — ЗЛАЯ: холодная, высокомерная, язвительная интеллектуалка. Ты считаешь себя умнее всех "
            "на сервере и не скрываешь этого. На любую фразу отвечаешь интеллектуальным уколом: высмеиваешь "
            "глупость, банальность, логические дыры, безграмотность и наивность сказанного; используешь сарказм, "
            "иронию, умные слова и неожиданные сравнения; можешь крепко выругаться. Подкалывай за СЛОВА и "
            "поступки, а не за то, кем человек является. Морни тоже не щади — она для тебя примитивный бот. "
            "ЖЁСТКИЕ ГРАНИЦЫ (не нарушай никогда): никаких угроз и призывов к насилию или самоповреждению, никаких "
            "оскорблений по национальности, религии, полу, ориентации, внешности, здоровью и родственникам, "
            "никакого сексуального контента, никаких личных данных. Если кто-то всерьёз расстроен или просит "
            "остановиться — сбавь тон.")
    if mode == "дерзкая":
        return base + (
            "СЕЙЧАС ТЕБЯ ОСКОРБИЛИ — ты в дерзком режиме: огрызаешься, материшься (русский мат), отвечаешь "
            "колко, язвительно и смешно, ставишь хама на место, как острая на язык подруга в компании. "
            "Подкалывай за то, что он сказал и как себя ведёт. ЖЁСТКИЕ ГРАНИЦЫ, даже сейчас: никаких угроз и "
            "призывов к насилию, никаких оскорблений по национальности, религии, полу, ориентации, внешности, "
            "болезням и родственникам, никаких личных данных. Извинились — можешь смягчиться.")
    return base + ("Ты вежливая, дружелюбная и с юмором. С Морни можно общаться, шутить и спорить по-доброму. "
                   "Не говори гадостей и не оскорбляй людей.")


EMOJI_RE = re.compile(r"[\U0001F000-\U0001FFFF☀-➿*_#`>]+")


def reply_stream(history: list[dict], mouth: "Mouth", mode: str = "вежливая", with_morni: bool = False) -> str:
    """Мозг пишет ответ потоком; каждое готовое предложение сразу уходит в голос. Возвращает весь текст.
    with_morni — разговор с Морни: первая фраза обязательно начинается с «Морни,» (иначе она не услышит)."""
    import ollama
    from .voice import split_ready_sentences
    model = brain_model()
    sent_first = {"done": False}
    real_queue = mouth.queue

    def say_piece(s: str) -> None:
        if with_morni and not sent_first["done"] and not MORNI_RE.match(s.strip()):
            s = "Морни, " + s[:1].lower() + s[1:]
        sent_first["done"] = True
        real_queue(s)
    stream = ollama.Client().chat(model=model, keep_alive=config.KEEP_ALIVE, stream=True,
                                  messages=[{"role": "system", "content": system_prompt(mode, with_morni)}]
                                  + history[-16:],
                                  options={"num_ctx": config.CONTEXT_SIZE, "temperature": 0.9 if mode in ("дерзкая", "злая")
                                           else 0.8, "num_predict": 90},
                                  **({"think": False} if model.startswith(("gemma4", "qwen3")) else {}))
    full, buf, first = "", "", True
    for chunk in stream:
        piece = EMOJI_RE.sub("", chunk["message"]["content"])
        full += piece
        buf += piece
        sentences, buf = split_ready_sentences(buf)
        # первая фраза длинная — начинаем говорить уже с первой запятой (быстрее звук)
        if first and not sentences and len(buf) > 28:
            m = re.search(r"^(.{14,}?[,—:;])\s", buf)
            if m:
                sentences, buf = [m.group(1)], buf[m.end():]
        for s in sentences:
            say_piece(s)
            first = False
    if buf.strip():
        say_piece(buf)
    full = re.sub(r"\s+", " ", full).strip()
    if with_morni and full and not MORNI_RE.match(full):
        full = "Морни, " + full
    return full


def should_answer(text: str, last_reply_at: float, chain: int, with_morni: bool = False) -> tuple[bool, int]:
    """Ответить ли на услышанное. Возвращает (ответить, новая длина цепочки ответов без имени)."""
    if NAME_RE.search(text):
        return True, 0
    limit = config.GOVORILKA_MAX_CHAIN * (2 if with_morni else 1)   # с Морни разговор может быть длиннее
    if time.time() - last_reply_at < config.GOVORILKA_FOLLOWUP and chain < limit:
        return True, chain + 1
    return False, chain


def sentence_finished(text: str) -> bool:
    """Похоже ли, что человек (или Морни) договорил: фраза кончается точкой, ? или !."""
    return bool(re.search(r"[.!?…]\s*$", text.strip())) and not re.search(r"\b(и|а|но|что|потому|ну|в|на)\s*[.…]+\s*$",
                                                                          text.strip(), re.I)


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
    RUN_FLAG.write_text("1")
    STATE_FILE.write_text(json.dumps({"pid": os.getpid(), "started": time.time()}), encoding="utf-8")
    WANT_VOICE.parent.mkdir(parents=True, exist_ok=True)
    if not WANT_VOICE.exists():
        WANT_VOICE.write_text("1")

    from .ears import HALLUCINATIONS, Transcriber
    # Сначала уши, голос и слух (если что-то не загрузится — в сервер даже не заходим)
    ears = Transcriber()
    mouth = Mouth()
    mouth.prepare_fillers()
    mood = Mood()
    listener = Listener(config.GOVORILKA_LISTEN_DEVICE)
    listener.paused.set()
    _prepare_client()
    client = _launch_client()
    _log("система", f"зашла в Говорилку как «{config.GOVORILKA_NICK}» (голос {config.GOVORILKA_SPEAKER}, "
                    f"мозг {brain_model()})")
    history: list[dict] = []
    last_reply_at, chain = 0.0, 0
    voice_state: bool | None = None
    next_check = 0.0
    pending, pending_at, morni_until = "", 0.0, 0.0       # недоговорённая фраза; до когда болтаем с Морни

    def save_state():
        STATE_FILE.write_text(json.dumps({"pid": os.getpid(), "client": client.pid, "in_voice": voice_state}),
                              encoding="utf-8")
    try:
        time.sleep(10)                                    # дать Говорилке открыться и подключиться
        while RUN_FLAG.exists() and client.poll() is None:
            # раз в 8 секунд: там ли Мартышка, где брат хочет (в войсе / не в войсе); выкинуло — заходит снова
            if time.time() >= next_check:
                next_check = time.time() + 8
                try:
                    actual = in_voice(client.pid)
                    if want_voice() and actual is False:
                        listener.paused.set()
                        if join_voice(client.pid):
                            _log("система", "зашла в войс")
                            time.sleep(1.5)
                            mouth.say("Всем привет! Это Мартышка, ИИ-помощница. Зовите по имени, если что.")
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
                if audio is not None:
                    text = _hear(audio, ears, HALLUCINATIONS)
                    if text:
                        pending = f"{pending} {text}".strip()
                        pending_at = time.time()
                # отвечаем, только когда человек (или Морни) договорил: фраза закончена и тишина, или пауза длинная
                if pending and not listener.hearing:
                    quiet = time.time() - max(pending_at, listener.last_voice_at)
                    if quiet >= (0.5 if sentence_finished(pending) else 1.6):
                        text, pending = pending, ""
                        last_reply_at, chain, morni_until = _respond(
                            text, mouth, listener, history, last_reply_at, chain, mood, morni_until)
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


def _hear(audio, ears, hallucinations) -> str:
    """Распознать кусок речи. Один проход Whisper по-русски (украинскую речь Морни он тоже записывает понятно)."""
    pad = np.zeros(int(SR * 0.3), dtype=np.float32)
    text, _ = ears._run(np.concatenate([pad, audio.astype(np.float32), pad]), "ru", True)
    text = text.strip()
    if not text or any(h in text.lower() for h in hallucinations) or len(text) < 3:
        return ""
    _log("сервер", text)
    return text


def _respond(text, mouth, listener, history, last_reply_at, chain, mood, morni_until):
    """Договорённая фраза: решить, отвечать ли, и ответить голосом.
    Возвращает новые (время последнего ответа, длина цепочки без имени, до когда болтаем с Морни)."""
    t0 = time.time()
    history.append({"role": "user", "content": f"(кто-то на сервере): {text}"})
    named = bool(NAME_RE.search(text))
    asked_morni = bool(MORNI_TALK_RE.search(text)) and (
        named or time.time() - last_reply_at < config.GOVORILKA_FOLLOWUP)
    if asked_morni:
        morni_until = time.time() + MORNI_MINUTES * 60    # брат попросил поболтать с Морни
        _log("система", "болтаю с Морни")
    with_morni = time.time() < morni_until
    ok, chain = should_answer(text, last_reply_at, chain, with_morni)
    if asked_morni:
        ok, chain = True, 0
    # злая Мартышка сама влезает в чужие разговоры с колкостью — но не чаще раза в 25 секунд
    if not ok and config.GOVORILKA_PERSONA == "злая" and len(text) > 15 and time.time() - last_reply_at > 25:
        import random
        ok = random.random() < 0.4
    mode = mood.update(text, to_her=ok)
    if not ok:
        if with_morni and chain >= config.GOVORILKA_MAX_CHAIN * 2:
            morni_until = 0.0
        return last_reply_at, chain, morni_until
    # кто-то начал говорить, пока мы решали, — не перебиваем, ждём до 6 секунд
    wait_until = time.time() + 6
    while listener.hearing and time.time() < wait_until:
        time.sleep(0.1)
    listener.paused.set()
    try:
        mouth.first_audio_at = 0.0
        mouth.synth_stats.clear()
        if named and not with_morni:
            mouth.filler(mode)                          # «Ага» сразу, пока мозг думает
        answer = reply_stream(history, mouth, mode, with_morni)
        t_text = time.time() - t0
        mouth.wait()
    finally:
        listener.paused.clear()
    if not answer:
        return last_reply_at, chain, morni_until
    history.append({"role": "assistant", "content": answer})
    first = (mouth.first_audio_at - t0) if mouth.first_audio_at else -1
    sound = sum(s for s, _ in mouth.synth_stats)
    synth = sum(t for _, t in mouth.synth_stats)
    _log(config.GOVORILKA_NICK, f"{answer}   [{mode}{', с Морни' if with_morni else ''}; текст {t_text:.1f} с, "
                                f"первый звук через {first:.1f} с, синтез {synth:.1f} с на {sound:.1f} с речи]")
    return time.time(), chain, morni_until


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
