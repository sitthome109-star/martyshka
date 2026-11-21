"""
СЛУЖЕБНОЕ: журнал, «только одна Мартышка», окошки с ошибками, ярлыки, автозапуск, иконка, открыть чат.
"""
import ctypes
import functools
import os
import re
import subprocess
import sys
import time
from pathlib import Path

from . import config

LOG_FILE = config.DATA_DIR / "журнал.txt"
ICON_FILE = config.PROJECT_DIR / "martyshka.ico"
MAIN_SCRIPT = config.PROJECT_DIR / "martyshka_main.py"
_HOST = config.PROJECT_DIR / ".venv" / "Scripts" / "Мартышка.exe"   # pythonw с иконкой и именем «Мартышка» (studio/build_exe.py)
PYTHONW = _HOST if _HOST.exists() else config.PROJECT_DIR / ".venv" / "Scripts" / "pythonw.exe"
PY_NAMES = ("python.exe", "pythonw.exe", "мартышка.exe")
SHORTCUT_NAME = "Мартышка.lnk"
_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


# ---------- журнал (когда нет чёрного окна) ----------
class _LogWriter:
    """Вместо экрана пишем в файл data/журнал.txt (без цветовых кодов)."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.stat().st_size > 2_000_000:   # журнал не растёт бесконечно
            old = path.with_name("журнал (старый).txt")
            old.unlink(missing_ok=True)
            path.rename(old)
        self.f = open(path, "a", encoding="utf-8", buffering=1)
        self.f.write(f"\n===== Запуск {time.strftime('%d.%m.%Y %H:%M:%S')} =====\n")

    def write(self, s):
        self.f.write(_ANSI.sub("", s))
        return len(s)

    def flush(self):
        self.f.flush()

    def reconfigure(self, **_):
        pass

    def isatty(self):
        return False


def setup_output() -> bool:
    """Если программа запущена без окна (pythonw) — весь вывод идёт в журнал. Возвращает True, если без окна."""
    if sys.stdout is None or "--hidden" in sys.argv:   # pythonw.exe запускает программу вообще без консоли
        w = _LogWriter(LOG_FILE)
        sys.stdout = sys.stderr = w
        return True
    return False


def message_box(text: str, title: str = "Мартышка") -> None:
    """Окошко Windows с сообщением (когда нет консоли, по-другому ошибку не показать)."""
    try:
        ctypes.windll.user32.MessageBoxW(None, text, title, 0x40 | 0x40000)  # «информация», поверх всех окон
    except Exception:
        pass


# ---------- только одна Мартышка ----------
_mutex = None


def already_running(wait: float = 0) -> bool:
    """True, если Мартышка уже запущена (ярлык нажали второй раз или автозапуск + ярлык).
    wait — сколько секунд подождать, пока старая закроется (при перезапуске)."""
    global _mutex
    k32 = ctypes.windll.kernel32
    deadline = time.time() + wait
    while True:
        _mutex = k32.CreateMutexW(None, False, "Local\\MartyshkaAssistant")
        if k32.GetLastError() != 183:          # ERROR_ALREADY_EXISTS
            return False
        if time.time() >= deadline:
            return True
        k32.CloseHandle(_mutex)
        time.sleep(0.5)


def restart_self() -> None:
    """Перезапустить Мартышку (после смены настроек): новая копия подождёт, пока эта закроется."""
    args = [a for a in sys.argv[1:] if a not in ("--autostart", "--restart")]
    exe = sys.executable
    flags = 0x00000008 if Path(exe).name.lower() in ("pythonw.exe", "мартышка.exe") else 0x00000010   # DETACHED / NEW_CONSOLE
    subprocess.Popen([exe, str(MAIN_SCRIPT), "--restart", *args], cwd=str(config.PROJECT_DIR), creationflags=flags)
    os._exit(0)


# ---------- голос ↔ чат: одна Мартышка ----------
def voice_running() -> bool:
    """Запущена ли голосовая Мартышка (её мьютекс существует)."""
    h = ctypes.windll.kernel32.OpenMutexW(0x00100000, False, "Local\\MartyshkaAssistant")   # SYNCHRONIZE
    if h:
        ctypes.windll.kernel32.CloseHandle(h)
        return True
    return False


def start_voice() -> None:
    """Запустить голосовую Мартышку (без окна). Сама следит, чтобы копия была одна."""
    (config.DATA_DIR / "выключить.flag").unlink(missing_ok=True)   # старый флажок «выключить» не должен её сразу закрыть
    subprocess.Popen([str(PYTHONW), str(MAIN_SCRIPT)], cwd=str(config.PROJECT_DIR), creationflags=0x08000000)


def voice_pids() -> list[int]:
    """Номера процессов именно голосовой Мартышки (martyshka_main.py), не чужие."""
    pids: list[int] = []
    try:
        import psutil
    except Exception:
        return pids
    for pr in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            info = pr.info
        except Exception:
            continue
        if (info.get("name") or "").lower() not in PY_NAMES:
            continue
        cmd = " ".join(info.get("cmdline") or [])
        if "martyshka_main.py" in cmd.replace("\\", "/") and str(pr.pid) != str(os.getpid()):
            pids.append(pr.pid)
    return pids


def stop_voice(timeout: float = 6.0) -> bool:
    """Выключить голосовую Мартышку (шар, микрофон, уши). Окно чата остаётся открытым."""
    import signal
    pids = voice_pids()
    if not pids:
        return True
    try:
        import psutil
        for pid in pids:
            try:
                psutil.Process(pid).terminate()          # вежливо: Windows закроет окно шара
            except Exception:
                pass
        for _ in range(int(timeout * 4)):
            if not voice_pids():
                return True
            time.sleep(0.25)
        for pid in voice_pids():                        # не закрылась — снимаем окончательно
            try:
                os.kill(pid, signal.SIGTERM)
            except Exception:
                pass
    except Exception:
        pass
    return not voice_pids()


CHAT_FLAG = config.DATA_DIR / "studio" / "показать.flag"


def chat_running() -> bool:
    h = ctypes.windll.kernel32.OpenMutexW(0x00100000, False, "MartyshkaStudioSingleton")
    if h:
        ctypes.windll.kernel32.CloseHandle(h)
        return True
    return False


def open_chat(tab: str = "chat") -> None:
    """Показать окно чата (из шара Мартышки). Уже открыт — просто выходит вперёд; нет — запускаем."""
    CHAT_FLAG.parent.mkdir(parents=True, exist_ok=True)
    if chat_running():
        CHAT_FLAG.write_text(tab or "chat", encoding="utf-8")
        return
    subprocess.Popen([str(PYTHONW), str(config.PROJECT_DIR / "studio.py"), "--tab", tab or "chat"],
                     cwd=str(config.PROJECT_DIR), creationflags=0x08000000)


def ensure_ollama(timeout: float = 0) -> bool:
    """Мозг теперь DeepSeek в облаке — Ollama не нужна."""
    return True


# ---------- ярлыки ----------
def _make_shortcut(path: Path, args: str = "") -> bool:
    """Создаёт ярлык Windows (.lnk) на запуск Мартышки без чёрного окна."""
    ps = (
        "$s=(New-Object -ComObject WScript.Shell).CreateShortcut($env:MT_LNK);"
        "$s.TargetPath=$env:MT_TARGET;$s.Arguments=$env:MT_ARGS;$s.WorkingDirectory=$env:MT_DIR;"
        "$s.IconLocation=$env:MT_ICON;$s.Description='Мартышка — голосовой помощник';$s.Save()"
    )
    env = dict(os.environ, MT_LNK=str(path), MT_TARGET=str(PYTHONW), MT_ARGS=f'"{MAIN_SCRIPT}" {args}'.strip(),
               MT_DIR=str(config.PROJECT_DIR), MT_ICON=str(ICON_FILE))
    r = subprocess.run(["powershell", "-NoProfile", "-Command", ps], env=env, capture_output=True,
                       creationflags=0x08000000)
    return r.returncode == 0 and path.exists()


@functools.lru_cache(maxsize=None)
def _special_folder(name: str) -> Path:
    r = subprocess.run(["powershell", "-NoProfile", "-Command",
                        f"[Console]::OutputEncoding=[Text.Encoding]::UTF8;[Environment]::GetFolderPath('{name}')"],
                       capture_output=True, creationflags=0x08000000)
    return Path(r.stdout.decode("utf-8").strip())


# Одно приложение: «Мартышка.exe» открывает окно, голосовой помощник включается кнопкой «Говорить» в нём
STUDIO_EXE = config.PROJECT_DIR / "Мартышка.exe"
STUDIO_SCRIPT = config.PROJECT_DIR / "studio.py"
STUDIO_SHORTCUT_NAME = "Мартышка.lnk"
STUDIO_ICON = config.PROJECT_DIR / "martyshka" / "studio" / "studio.ico"
OLD_SHORTCUTS = ("Мартышка Студия.lnk",)    # старые ярлыки отдельного чата


def startup_shortcut() -> Path:
    return _special_folder("Startup") / STUDIO_SHORTCUT_NAME


def autostart_enabled() -> bool:
    return startup_shortcut().exists()


def _make_chat_shortcut(path: Path, extra: str = "") -> bool:
    """Ярлык на приложение Мартышки (окно; голос — кнопкой в нём)."""
    exe = STUDIO_EXE if STUDIO_EXE.exists() else PYTHONW
    args = (extra if STUDIO_EXE.exists() else f'"{STUDIO_SCRIPT}" {extra}').strip()
    icon = STUDIO_ICON if STUDIO_ICON.exists() else ICON_FILE
    ps = (
        "$s=(New-Object -ComObject WScript.Shell).CreateShortcut($env:MT_LNK);"
        "$s.TargetPath=$env:MT_TARGET;$s.Arguments=$env:MT_ARGS;$s.WorkingDirectory=$env:MT_DIR;"
        "$s.IconLocation=$env:MT_ICON;$s.Description='Мартышка';$s.Save()"
    )
    env = dict(os.environ, MT_LNK=str(path), MT_TARGET=str(exe), MT_ARGS=args,
               MT_DIR=str(config.PROJECT_DIR), MT_ICON=str(icon))
    r = subprocess.run(["powershell", "-NoProfile", "-Command", ps], env=env, capture_output=True,
                       creationflags=0x08000000)
    return r.returncode == 0 and path.exists()


def set_autostart(on: bool) -> bool:
    """Включить/выключить запуск ЧАТА вместе с Windows.

    Голосовая Мартышка (шар, микрофон) сама больше не поднимается: её включает
    кнопка «Голос» в окне чата. Старый ярлык голоса убираем.
    """
    lnk = startup_shortcut()
    for old in OLD_SHORTCUTS:
        (_special_folder("Startup") / old).unlink(missing_ok=True)
    if on:
        return _make_chat_shortcut(lnk, "--autostart")
    lnk.unlink(missing_ok=True)
    return True


def make_desktop_shortcut() -> Path | None:
    """Один ярлык «Мартышка» на рабочем столе — на приложение (окно), а не на голос отдельно."""
    lnk = _special_folder("Desktop") / STUDIO_SHORTCUT_NAME
    return lnk if _make_chat_shortcut(lnk) else None


# ---------- иконка ----------
def make_icon(color: str | None = None) -> Path:
    """Рисует иконку из самого шара Мартышки (нужного цвета) → martyshka.ico."""
    from PIL import Image
    from .face import OrbRenderer, _hex_to_rgb
    size = 256
    r = OrbRenderer(int(size / 0.46 / 2 * 0.92))
    rgba = r.render(size, size, 2.0, 0.6, 0.04, 1.25, 1.6, _hex_to_rgb(color or config.FACE_COLOR),
                    (size / 2, size / 2), 1.0)
    import numpy as np
    img = Image.fromarray(rgba, "RGBA")
    # Маленькие иконки (как на рабочем столе) — ярче и плотнее, иначе шар там почти не видно
    smalls = []
    for s in (16, 24, 32, 48, 64, 128):
        im = np.asarray(img.resize((s, s), Image.LANCZOS)).astype(np.float32) / 255
        boost = 1 + 2.2 * (1 - s / 128)                       # чем меньше иконка — тем сильнее
        im[..., 3] = np.clip(im[..., 3] * boost, 0, 1)
        im[..., :3] = np.clip(im[..., :3] * (1 + 0.25 * (boost - 1)), 0, 1)
        smalls.append(Image.fromarray((im * 255).astype(np.uint8), "RGBA"))
    img.save(ICON_FILE, sizes=[(256, 256)] + [(s.width, s.height) for s in smalls], append_images=smalls)
    return ICON_FILE
