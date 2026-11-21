"""Собрать «Мартышка.exe» в папке проекта (компилятор C# из .NET Framework, он есть в каждой Windows)
и «движок» `.venv\\Scripts\\Мартышка.exe` — копию pythonw.exe с иконкой шара и подписью «Мартышка».
Через движок работают окно чата и голос, поэтому на панели задач и в диспетчере задач — «Мартышка», а не Python."""
import ctypes
import shutil
import subprocess
import sys
from ctypes import wintypes
from pathlib import Path

from .. import config

CSC = Path(r"C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe")
HOST = config.PROJECT_DIR / ".venv" / "Scripts" / "Мартышка.exe"
LAUNCHER = config.PROJECT_DIR / "Мартышка.exe"
RT_ICON, RT_GROUP_ICON, RT_VERSION = 3, 14, 16


def icon_path() -> Path:
    """Иконка приложения — шар Мартышки (studio.ico); если нет — общая иконка."""
    own = Path(__file__).parent / "studio.ico"
    return own if own.exists() else config.PROJECT_DIR / "martyshka" / "martyshka.ico"


def python_exe(console: bool = False) -> Path:
    """Чем запускать окно и голос: движок «Мартышка.exe», если собран, иначе обычный pythonw."""
    if not console and HOST.exists():
        return HOST
    return config.PROJECT_DIR / ".venv" / "Scripts" / ("python.exe" if console else "pythonw.exe")


def build() -> Path:
    src = Path(__file__).parent / "launcher.cs"
    out = LAUNCHER
    icon = icon_path()
    args = [str(CSC), "/nologo", "/target:winexe", "/platform:anycpu", "/optimize+", "/codepage:65001",
            f"/out:{out}", "/r:System.Windows.Forms.dll"]
    if icon.exists():
        args.append(f"/win32icon:{icon}")
    args.append(str(src))
    r = subprocess.run(args, capture_output=True, text=True, encoding="cp866", errors="replace")
    if r.returncode != 0:
        raise RuntimeError(r.stdout + r.stderr)
    return out


# ---------- движок: pythonw.exe под именем «Мартышка.exe» ----------
_k = ctypes.WinDLL("kernel32", use_last_error=True)
_ENUMTYPE = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HMODULE, wintypes.LPVOID, wintypes.LPARAM)
_ENUMNAME = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HMODULE, wintypes.LPVOID, wintypes.LPVOID, wintypes.LPARAM)
_ENUMLANG = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HMODULE, wintypes.LPVOID, wintypes.LPVOID, wintypes.WORD,
                               wintypes.LPARAM)
for _n, _t in (("EnumResourceTypesW", [wintypes.HMODULE, _ENUMTYPE, wintypes.LPARAM]),
               ("EnumResourceNamesW", [wintypes.HMODULE, wintypes.LPVOID, _ENUMNAME, wintypes.LPARAM]),
               ("EnumResourceLanguagesW", [wintypes.HMODULE, wintypes.LPVOID, wintypes.LPVOID, _ENUMLANG,
                                           wintypes.LPARAM]),
               ("FindResourceExW", [wintypes.HMODULE, wintypes.LPVOID, wintypes.LPVOID, wintypes.WORD]),
               ("LoadResource", [wintypes.HMODULE, wintypes.HANDLE]),
               ("LockResource", [wintypes.HANDLE]),
               ("SizeofResource", [wintypes.HMODULE, wintypes.HANDLE]),
               ("LoadLibraryExW", [wintypes.LPCWSTR, wintypes.HANDLE, wintypes.DWORD]),
               ("FreeLibrary", [wintypes.HMODULE]),
               ("BeginUpdateResourceW", [wintypes.LPCWSTR, wintypes.BOOL]),
               ("UpdateResourceW", [wintypes.HANDLE, wintypes.LPVOID, wintypes.LPVOID, wintypes.WORD,
                                    wintypes.LPVOID, wintypes.DWORD]),
               ("EndUpdateResourceW", [wintypes.HANDLE, wintypes.BOOL])):
    getattr(_k, _n).argtypes = _t
_k.FindResourceExW.restype = _k.LoadResource.restype = _k.LockResource.restype = wintypes.HANDLE
_k.LoadLibraryExW.restype = _k.BeginUpdateResourceW.restype = wintypes.HANDLE
_k.SizeofResource.restype = wintypes.DWORD


def _key(p):
    """Имя/тип ресурса: число (если < 65536) или строка."""
    return p if p is not None and p < 0x10000 else ctypes.wstring_at(p)


def _resources(path: Path) -> list[tuple]:
    """Все ресурсы файла: [(тип, имя, язык, байты)]."""
    h = _k.LoadLibraryExW(str(path), None, 0x2 | 0x20)   # как данные, без запуска
    if not h:
        raise OSError(f"не открыть {path}: {ctypes.get_last_error()}")
    out, types = [], []
    _k.EnumResourceTypesW(h, _ENUMTYPE(lambda m, t, l: types.append(_key(t)) or True), 0)
    try:
        for t in types:
            names = []
            _k.EnumResourceNamesW(h, t if isinstance(t, int) else ctypes.c_wchar_p(t),
                                  _ENUMNAME(lambda m, tt, n, l: names.append(_key(n)) or True), 0)
            for n in names:
                langs = []
                tp = t if isinstance(t, int) else ctypes.c_wchar_p(t)
                np_ = n if isinstance(n, int) else ctypes.c_wchar_p(n)
                _k.EnumResourceLanguagesW(h, tp, np_, _ENUMLANG(lambda m, a, b, lg, l: langs.append(lg) or True), 0)
                for lg in langs:
                    r = _k.FindResourceExW(h, tp, np_, lg)
                    size = _k.SizeofResource(h, r)
                    data = ctypes.string_at(_k.LockResource(_k.LoadResource(h, r)), size)
                    out.append((t, n, lg, data))
    finally:
        _k.FreeLibrary(h)
    return out


def build_host() -> Path:
    """Копия pythonw.exe из Python, на котором сделан .venv, с иконкой и подписью «Мартышка».
    Лежит в .venv\\Scripts — там Python сам находит pyvenv.cfg и все библиотеки проекта."""
    if not LAUNCHER.exists():
        build()
    base = Path(sys.base_prefix)
    scripts = HOST.parent
    for dll in ("python3.dll", f"python{sys.version_info[0]}{sys.version_info[1]}.dll",
                "vcruntime140.dll", "vcruntime140_1.dll"):
        if (base / dll).exists():
            shutil.copy2(base / dll, scripts / dll)
    tmp = HOST.with_suffix(".tmp.exe")
    shutil.copy2(base / "pythonw.exe", tmp)
    keep = [r for r in _resources(tmp) if r[0] not in (RT_ICON, RT_GROUP_ICON, RT_VERSION)]
    mine = [r for r in _resources(LAUNCHER) if r[0] in (RT_ICON, RT_GROUP_ICON, RT_VERSION)]
    h = _k.BeginUpdateResourceW(str(tmp), True)
    if not h:
        raise OSError(f"не открыть для правки: {ctypes.get_last_error()}")
    bufs = []
    for t, n, lg, data in keep + mine:
        buf = ctypes.create_string_buffer(data, len(data))
        bufs.append(buf)
        tp = t if isinstance(t, int) else ctypes.c_wchar_p(t)
        np_ = n if isinstance(n, int) else ctypes.c_wchar_p(n)
        if not _k.UpdateResourceW(h, tp, np_, lg, buf, len(data)):
            _k.EndUpdateResourceW(h, True)
            raise OSError(f"ресурс {t}/{n}: {ctypes.get_last_error()}")
    if not _k.EndUpdateResourceW(h, False):
        raise OSError(f"не сохранить: {ctypes.get_last_error()}")
    tmp.replace(HOST)
    return HOST


if __name__ == "__main__":
    print(build())
    print(build_host())
