"""
СБОРКА EXE: игра Мартышки (HTML) → ОДИН .exe-файл для Windows.

Внутри exe: окно с встроенным браузером WebView2 (движок Edge, есть в Windows 11), сама игра и библиотеки WebView2.
Собираем компилятором C#, который уже есть в Windows (.NET Framework), — ничего скачивать не нужно.
Иконку рисуем из кадра самой игры. Готовый exe — в «Документы → Игры Мартышки», его можно просто скинуть другу.
"""
import difflib
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from . import config

CSC = Path(r"C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe")
WV2 = config.PROJECT_DIR / "tools" / "webview2"
HOST = Path(__file__).with_name("exe_host.cs")
DLLS = ("Microsoft.Web.WebView2.Core.dll", "Microsoft.Web.WebView2.WinForms.dll", "WebView2Loader.dll")


def find_game(hint: str = ""):
    """Какую игру собрать: по названию (примерно) или последнюю."""
    from . import games
    files = sorted(games.GAMES_DIR.glob("*.html"), key=lambda p: p.stat().st_mtime) if games.GAMES_DIR.exists() else []
    h = re.sub(r"\b(сделай|собери|упакуй|сохрани|из|игру|игры|в|exe|ехе|экзешник\w*|экзе|последн\w*|эту|её|ее)\b",
               " ", (hint or "").lower())
    h = re.sub(r"\s+", " ", h).strip(" .,!?")
    if h and files:
        names = {p.stem.lower(): p for p in files}
        hit = [p for n, p in names.items() if h in n or n in h]
        if hit:
            return hit[-1]
        close = difflib.get_close_matches(h, list(names), n=1, cutoff=0.5)
        if close:
            return names[close[0]]
        # по корням слов: «змейки» → «Классическая Змейка», «гонку» → «Гонки на выживание»
        stems = [w[:4] for w in re.findall(r"\w+", h) if len(w) >= 4]
        best = max(((sum(any(n2.startswith(s) for n2 in re.findall(r"\w+", n)) for s in stems), p)
                    for n, p in names.items()), key=lambda x: x[0], default=(0, None))
        if best[0] > 0:
            return best[1]
    if games.last_game and games.last_game.exists():
        return games.last_game
    return files[-1] if files else None


def _icon(html: str, out: Path) -> bool:
    """Иконка из кадра игры (по центру), размеры 256…16."""
    try:
        from . import games
        shots = games._shots(html)
        if not shots:
            return False
        im = shots[min(1, len(shots) - 1)]
        s = min(im.size)
        im = im.crop(((im.width - s) // 2, (im.height - s) // 2, (im.width + s) // 2, (im.height + s) // 2))
        im.convert("RGBA").resize((256, 256)).save(out, sizes=[(256, 256), (64, 64), (48, 48), (32, 32), (16, 16)])
        return True
    except Exception:
        return False


def build(game: Path) -> Path:
    """Собрать exe из игры. → путь к exe (рядом с игрой)."""
    if not CSC.exists():
        raise RuntimeError("не нашёл компилятор C# в Windows (.NET Framework 4)")
    missing = [d for d in DLLS if not (WV2 / d).exists()]
    if missing:
        raise RuntimeError("нет библиотек WebView2: " + ", ".join(missing))
    html = game.read_text(encoding="utf-8")
    title = game.stem
    gid = re.sub(r"[^\w]+", "_", title, flags=re.U).strip("_")[:40] or "game"
    tmp = Path(tempfile.mkdtemp(prefix="martyshka_exe_"))
    (tmp / "game.html").write_text(html, encoding="utf-8")
    for d in DLLS:
        shutil.copy(WV2 / d, tmp / d)
    src = HOST.read_text(encoding="utf-8").replace("__TITLE__", title.replace("\\", "\\\\").replace('"', '\\"')) \
                                          .replace("__ID__", gid)
    (tmp / "host.cs").write_text(src, encoding="utf-8")
    has_icon = _icon(html, tmp / "icon.ico")
    out = game.with_suffix(".exe")
    args = [str(CSC), "/nologo", "/target:winexe", "/platform:x64", "/optimize+", "/codepage:65001", f"/out:{out}",
            f"/r:{tmp / DLLS[0]}", f"/r:{tmp / DLLS[1]}", "/r:System.Windows.Forms.dll", "/r:System.Drawing.dll",
            f"/resource:{tmp / 'game.html'},game.html"]
    args += [f"/resource:{tmp / d},{d}" for d in DLLS]
    if has_icon:
        args.append(f"/win32icon:{tmp / 'icon.ico'}")
    args.append(str(tmp / "host.cs"))
    r = subprocess.run(args, capture_output=True, creationflags=0x08000000)
    if r.returncode != 0 or not out.exists():
        msg = (r.stdout or b"").decode("cp866", "replace") + (r.stderr or b"").decode("cp866", "replace")
        raise RuntimeError("компилятор ругается: " + msg.strip()[-500:])
    try:
        from .hands import remember_file
        remember_file(out)
    except Exception:
        pass
    return out
