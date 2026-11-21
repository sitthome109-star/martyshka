"""
РУКИ МАРТЫШКИ — управление компьютером.

Мозг пишет в конце ответа метку, например:
    [ДЕЛО: открыть | стим]
а этот модуль её выполняет. Список всех дел — в ACTIONS ниже (он же уходит в инструкцию мозгу).

Каждое дело возвращает Result:
    text     — что сделано (или почему не вышло)
    info     — True, если это справка (загрузка системы, заметки...) — мозг перескажет её брату своими словами
    confirm  — вопрос, если дело опасное и сначала надо спросить «точно?»
"""
import ctypes
import difflib
import json
import os
import re
import subprocess
import threading
import time
import webbrowser
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import quote_plus

from . import config

NOTES_FILE = config.DATA_DIR / "notes.txt"
APPS_CACHE = config.DATA_DIR / "apps.json"


@dataclass
class Result:
    text: str
    info: bool = False
    confirm: str | None = None
    ok: bool = True


# ---------- прозвища ----------
# Как брат может назвать программу → как она называется в Windows
APP_ALIASES = {
    "стим": "steam", "стимчик": "steam", "steam": "steam",
    "дискорд": "discord", "дис": "discord", "дискорт": "discord",
    "телега": "telegram", "телеграм": "telegram", "телеграмм": "telegram", "тг": "telegram",
    "хром": "google chrome", "гугл хром": "google chrome", "chrome": "google chrome",
    "браузер": "google chrome", "интернет": "google chrome",
    "эдж": "microsoft edge", "edge": "microsoft edge",
    "кс": "counter-strike 2", "кска": "counter-strike 2", "контра": "counter-strike 2",
    "контр страйк": "counter-strike 2", "кс2": "counter-strike 2", "кс 2": "counter-strike 2",
    "обс": "obs studio", "obs": "obs studio",
    "ворд": "word", "эксель": "excel", "ексель": "excel",
    "блокнот": "блокнот", "калькулятор": "калькулятор", "проводник": "проводник",
    "папки": "проводник", "мой компьютер": "проводник",
    "спотифай": "spotify", "яндекс музыка": "яндекс музыка", "яндекс браузер": "yandex",
    "настройки": "параметры", "параметры": "параметры", "диспетчер задач": "диспетчер задач",
    "клауд": "claude", "клод": "claude", "клоуд": "claude", "клауде": "claude", "клауда": "claude",
    "ватсап": "whatsapp", "вотсап": "whatsapp", "вацап": "whatsapp",
    "эпик": "epic games launcher", "эпик геймс": "epic games launcher", "епик": "epic games launcher",
    "майн": "minecraft launcher", "майнкрафт": "minecraft launcher", "тлаунчер": "tlauncher",
    "валорант": "valorant", "валик": "valorant", "фейсит": "faceit", "тимспик": "teamspeak",
    "джи хаб": "logitech g hub", "g hub": "logitech g hub", "логитек": "logitech g hub",
    "ауле": "aula f75 max gasket mechanical keyboard", "аула": "aula f75 max gasket mechanical keyboard",
    "ф75": "f75 studio", "f75": "f75 studio", "ф 75 студио": "f75 studio", "f75 studio": "f75 studio",
    "ф 75 студия": "f75 studio", "ф 75 студию": "f75 studio", "ф75 студия": "f75 studio", "ф75 студию": "f75 studio",
    "ф 75": "f75 studio", "студию ф 75": "f75 studio", "ф 75 студии": "f75 studio",
    "нвидиа": "nvidia app", "обсик": "obs studio", "капкат": "capcut", "рокстар": "rockstar games launcher",
    "гта рп": "gta5rp launcher", "ииде": "idle (python 3.12 64-bit)", "пайтон": "idle (python 3.12 64-bit)",
}

# Процессы — чтобы закрывать программы
PROCESS_NAMES = {
    "steam": ["steam.exe", "steamwebhelper.exe"], "discord": ["discord.exe"], "telegram": ["telegram.exe"],
    "google chrome": ["chrome.exe"], "microsoft edge": ["msedge.exe"], "counter-strike 2": ["cs2.exe"],
    "obs studio": ["obs64.exe"], "word": ["winword.exe"], "excel": ["excel.exe"],
    "блокнот": ["notepad.exe"], "калькулятор": ["calculatorapp.exe", "calculator.exe"], "powerpoint": ["powerpnt.exe"],
    "spotify": ["spotify.exe"], "yandex": ["browser.exe"], "диспетчер задач": ["taskmgr.exe"],
    "f75 studio": ["f75studio.exe"], "claude": ["claude.exe"], "whatsapp": ["whatsapp.exe"],
}

SITES = {
    "ютуб": "https://www.youtube.com", "youtube": "https://www.youtube.com", "ютубчик": "https://www.youtube.com",
    "вк": "https://vk.com", "вконтакте": "https://vk.com", "гугл": "https://www.google.com",
    "яндекс": "https://ya.ru", "твич": "https://www.twitch.tv", "twitch": "https://www.twitch.tv",
    "гитхаб": "https://github.com", "github": "https://github.com", "почта": "https://mail.google.com",
    "гугл почта": "https://mail.google.com", "переводчик": "https://translate.google.com",
    "кинопоиск": "https://www.kinopoisk.ru", "авито": "https://www.avito.ru", "озон": "https://www.ozon.ru",
    "вайлдберриз": "https://www.wildberries.ru", "вб": "https://www.wildberries.ru",
    "фейсит": "https://www.faceit.com", "faceit": "https://www.faceit.com", "чатгпт": "https://chatgpt.com",
    "погода": "https://yandex.ru/pogoda", "карты": "https://yandex.ru/maps",
}

_TRANSLIT = str.maketrans({
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh", "з": "z", "и": "i",
    "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t",
    "у": "u", "ф": "f", "х": "h", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sch", "ъ": "", "ы": "y", "ь": "",
    "э": "e", "ю": "yu", "я": "ya",
})


def _clean(s: str) -> str:
    return re.sub(r"[^a-zа-яё0-9 ]+", " ", s.lower().replace("ё", "е")).strip()


# Программы без ярлыка в «Пуске» (имя → путь к exe)
EXTRA_APPS = {
    "f75 studio": str(Path.home() / "F75 Studio" / "F75Studio.exe"),
}


# ---------- список программ ----------
_apps: dict[str, str] | None = None  # имя в нижнем регистре → AppID


def load_apps(refresh: bool = False) -> dict[str, str]:
    """Все программы из меню «Пуск» (как в поиске Windows). Кэшируется на сутки."""
    global _apps
    if _apps is not None and not refresh:
        return _apps
    try:
        if not refresh and APPS_CACHE.exists() and time.time() - APPS_CACHE.stat().st_mtime < 86400:
            _apps = json.loads(APPS_CACHE.read_text(encoding="utf-8"))
            return _apps
    except Exception:
        pass
    out = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "[Console]::OutputEncoding=[Text.Encoding]::UTF8; Get-StartApps | ConvertTo-Json -Compress"],
        capture_output=True, timeout=30,
    ).stdout.decode("utf-8", "replace")
    items = json.loads(out) if out.strip() else []
    skip = re.compile(r"(деинстал|uninstall|удалить|удаление|support|справка|readme|документац)", re.I)
    _apps = {i["Name"].lower(): i["AppID"] for i in items if not skip.search(i["Name"])}
    for name, exe in EXTRA_APPS.items():   # программы, которых нет в меню «Пуск»
        if Path(exe).exists():
            _apps[name] = exe
    try:
        APPS_CACHE.parent.mkdir(parents=True, exist_ok=True)
        APPS_CACHE.write_text(json.dumps(_apps, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass
    return _apps


def find_app(query: str) -> str | None:
    """«стим» → «steam», «телега» → «telegram», «кс» → «counter-strike 2». None — если не нашли."""
    apps = load_apps()
    q = _clean(query)
    q = APP_ALIASES.get(q, q)
    if q in apps:
        return q
    # совпадение по началу/вхождению
    for name in apps:
        if name.startswith(q) or (len(q) >= 4 and q in name):
            return name
    # транслит («дискорд» → «diskord») + нечёткое сравнение
    variants = {q, q.translate(_TRANSLIT)}
    best, score = None, 0.0
    for name in apps:
        for v in variants:
            s = difflib.SequenceMatcher(None, v, name).ratio()
            if s > score:
                best, score = name, s
    return best if score >= 0.72 else None


# ---------- дела ----------
def open_thing(what: str) -> Result:
    w = _clean(what)
    if not w:
        return Result("Не понял, что открыть.", ok=False)
    if w in SITES:
        webbrowser.open(SITES[w])
        return Result(f"Открыл сайт {what}.")
    if re.fullmatch(r"[\w.-]+\.(ru|com|net|org|tv|io|рф|me|gg)(/\S*)?", what.strip().lower()):
        webbrowser.open("https://" + what.strip())
        return Result(f"Открыл {what}.")
    # «открой загрузки / документы / рабочий стол» — это папка, а не программа
    if any(k in w for k in FOLDERS) and not (w in APP_ALIASES or w in load_apps()):
        return open_folder(what)
    name = find_app(what)
    if name is None:
        # «проект в клауде», «голосовой помощник в телеге» → программа после «в»; иначе — по отдельным словам
        tail = re.search(r"\b(?:в|во|через)\s+(.+)$", w)
        candidates = ([tail.group(1)] if tail else []) + sorted(w.split(), key=len, reverse=True)
        for c in candidates:
            if len(c) >= 3 and c not in ("проект", "файл", "папку", "окно", "программу", "приложение"):
                name = find_app(c) if (c in APP_ALIASES or len(c) >= 4) else None
                if name:
                    break
    if name is None:
        return Result(f"Не нашёл программу «{what}» на компе.", ok=False)
    app_id = load_apps()[name]
    # Уже запущена и есть окно — просто показываем его (а не запускаем вторую копию)
    existing = _app_window(name)
    if existing:
        u32 = ctypes.windll.user32
        u32.ShowWindow(existing["hwnd"], 9)                    # SW_RESTORE
        _bring_to_front(int(existing["hwnd"]))
        return Result(f"{name} уже был открыт — вывел его на экран.")
    if re.match(r"^(https?|steam)://", app_id):
        os.startfile(app_id)
    elif re.match(r"^[a-z]:\\", app_id, re.I) and app_id.lower().endswith(".exe"):
        subprocess.Popen([app_id], cwd=str(Path(app_id).parent))
    else:
        subprocess.Popen(["explorer.exe", f"shell:AppsFolder\\{app_id}"])
    # Самопроверка: появилось ли окно (до 8 секунд)
    exes = PROCESS_NAMES.get(name, [])
    for _ in range(16):
        time.sleep(0.5)
        if _app_window(name):
            return Result(f"Запустил {name}.")
    if exes and _running(exes):
        return Result(f"Запустил {name} — программа работает, окно ещё грузится.")
    return Result(f"Запустил {name}, но окно пока не появилось — возможно, она грузится или открылась в трее.")


def _app_window(name: str):
    """Окно программы среди открытых (по названию программы, её процессу или заголовку окна)."""
    try:
        exes = {e.removesuffix(".exe") for e in PROCESS_NAMES.get(name, [])}
        words = [w for w in re.findall(r"[\wа-яё]{3,}", name.lower())]
        for w in list_windows(with_dark=False):
            hay = f"{w['title']} {w['exe']}".lower()
            if w["exe"] in exes or (words and all(x in hay for x in words)) or name.lower() == w["exe"]:
                return w
    except Exception:
        pass
    return None


# Программы с документами: их НЕ закрываем силой — пусть сами спросят «Сохранить?», иначе пропадёт работа
NO_FORCE = {"word", "excel", "блокнот", "powerpoint", "obs studio"}


def _running(exes: list[str]) -> list:
    import psutil
    return [p for p in psutil.process_iter(["name"]) if (p.info["name"] or "").lower() in exes]


# ---------- недавние файлы (для «удали этот документ») и удаление в корзину ----------
recent_files: list = []   # (время, путь) — с какими файлами Мартышка недавно работала


def remember_file(path) -> None:
    try:
        p = Path(str(path))
        recent_files[:] = [(t, q) for t, q in recent_files if q != p][-9:] + [(time.time(), p)]
    except Exception:
        pass


THIS_RE = re.compile(r"^\W*(этот|эту|это|его|её|ее|последн\w*|только\s+что\w*|тот|ту)?\W*(документ|файл|ворд\w*|"
                     r"таблиц\w*|фот\w*|картинк\w*|видео|скриншот|конспект|реферат)?\W*$", re.I)


def _recycle(path: Path) -> bool:
    """Отправить файл в Корзину (его можно восстановить)."""
    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [("hwnd", ctypes.c_void_p), ("wFunc", ctypes.c_uint), ("pFrom", ctypes.c_wchar_p),
                    ("pTo", ctypes.c_wchar_p), ("fFlags", ctypes.c_ushort), ("fAnyOperationsAborted", ctypes.c_int),
                    ("hNameMappings", ctypes.c_void_p), ("lpszProgressTitle", ctypes.c_wchar_p)]
    op = SHFILEOPSTRUCTW(None, 3, str(path) + "\0\0", None, 0x0040 | 0x0010 | 0x0004 | 0x0400, 0, None, None)
    # FO_DELETE=3; FOF_ALLOWUNDO (в корзину) | FOF_NOCONFIRMATION | FOF_SILENT | FOF_NOERRORUI
    return ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op)) == 0 and not path.exists()


def _close_in_office(path: Path) -> None:
    """Если файл открыт в Word/Excel — закрыть его там (иначе Windows не даст удалить)."""
    import comtypes
    import comtypes.client
    comtypes.CoInitialize()
    for prog, coll in (("Word.Application", "Documents"), ("Excel.Application", "Workbooks")):
        try:
            app = comtypes.client.GetActiveObject(prog)
        except Exception:
            continue
        try:
            closed = False
            for d in list(getattr(app, coll)):
                if Path(d.FullName) == path:
                    d.Close(0 if prog.startswith("Word") else False)
                    closed = True
            if closed and getattr(app, coll).Count == 0:
                app.Quit()          # больше ничего не открыто — не оставляем пустое (чёрное) окно программы
        except Exception:
            pass


def delete_file(what: str = "", confirmed: bool = False) -> Result:
    """«Удали этот документ» / «удали файл отчёт» — в Корзину и только после «да»."""
    what = (what or "").strip().strip("«»\"'")
    target = None
    if THIS_RE.match(what):            # «этот документ», «его», пусто — последний файл, с которым работали
        now = time.time()
        recent = [p for t, p in reversed(recent_files) if now - t < 3 * 3600 and p.exists()]
        kind = what.lower()
        if re.search(r"фот|картинк|скрин", kind):
            recent = [p for p in recent if p.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp", ".gif")] or recent
        elif re.search(r"видео", kind):
            recent = [p for p in recent if p.suffix.lower() in (".mp4", ".mov", ".mkv", ".webm")] or recent
        elif re.search(r"документ|ворд|конспект|реферат", kind):
            recent = [p for p in recent if p.suffix.lower() in (".docx", ".doc", ".txt")] or recent
        elif re.search(r"таблиц", kind):
            recent = [p for p in recent if p.suffix.lower() in (".xlsx", ".xls", ".csv")] or recent
        target = recent[0] if recent else None
        if target is None:
            return Result("Не понял, какой файл удалить: я с файлами недавно не работал. Скажи название.", ok=False)
    else:
        p = Path(os.path.expandvars(what))
        if p.is_file():
            target = p
        else:
            words = [w for w in re.findall(r"[\wа-яё]{3,}", what.lower()) if w not in ("файл", "документ")]
            hits = []
            for d in (DOCS_DIR, Path.home() / "Desktop", Path.home() / "Downloads", Path.home() / "Pictures",
                      Path.home() / "Videos"):
                if d.exists():
                    for f in list(d.glob("*")) + list(d.glob("*/*")):
                        if f.is_file() and words and all(w in f.name.lower() for w in words):
                            hits.append(f)
            if not hits:
                return Result(f"Не нашёл файл «{what}».", ok=False)
            if len(hits) > 1:
                names = ", ".join(h.name for h in hits[:4])
                return Result(f"Нашёл несколько похожих: {names}. Скажи точнее, какой удалить.", ok=False)
            target = hits[0]
    # защита: только файлы в папке пользователя, не папки, не файлы самой Мартышки
    home = Path.home().resolve()
    t = target.resolve()
    if (not t.is_file() or home not in t.parents or config.PROJECT_DIR.resolve() in t.parents
            or "appdata" in str(t).lower()):
        return Result(f"Этот файл я удалять не стану: {target}", ok=False)
    if not confirmed:
        return Result("", confirm=f"Удалить «{target.name}» в корзину?")
    _close_in_office(t)
    time.sleep(0.4)
    if _recycle(t):
        return Result(f"Удалил «{target.name}» в корзину (оттуда можно восстановить).")
    return Result(f"Не смог удалить «{target.name}» — возможно, он открыт в какой-то программе.", ok=False)


# Word / Excel / PowerPoint: перед закрытием сами сохраняем все документы (через «пульт» Office — COM)
OFFICE = {
    "word": ("Word.Application", "Documents", ".docx"),
    "excel": ("Excel.Application", "Workbooks", ".xlsx"),
    "powerpoint": ("PowerPoint.Application", "Presentations", ".pptx"),
}


def _office_save_and_quit(name: str) -> list[str] | None:
    """Сохраняет все открытые документы программы Office и закрывает её без вопросов.
    Новые (ещё без имени) документы кладёт в «Документы» с датой в названии.
    Возвращает список сохранённых файлов или None, если до программы не достучаться."""
    import comtypes
    import comtypes.client
    prog_id, coll_name, ext = OFFICE[name]
    comtypes.CoInitialize()
    try:
        app = comtypes.client.GetActiveObject(prog_id)
    except Exception:
        return None
    saved = []
    try:
        try:
            # никаких окошек «Сохранить?» (у каждой программы Office своё значение «молчать»)
            app.DisplayAlerts = {"word": 0, "excel": False, "powerpoint": 1}[name]
        except Exception:
            pass
        for doc in list(getattr(app, coll_name)):
            try:
                if doc.Path:                                      # у документа уже есть файл — просто сохраняем
                    if not getattr(doc, "Saved", True):
                        doc.Save()
                    saved.append(doc.Name)
                    remember_file(doc.FullName)
                else:                                             # новый документ — даём имя и кладём в «Документы»
                    path = _unique(DOCS_DIR / f"{name.capitalize()} {datetime.now():%d.%m.%Y %H-%M}{ext}")
                    if name == "word":
                        doc.SaveAs2(str(path))
                    else:
                        doc.SaveAs(str(path))
                    saved.append(path.name + " (в Документах)")
                    remember_file(path)
            except Exception:
                pass
        app.Quit()
    except Exception:
        return None
    return saved


def close_app(what: str) -> Result:
    name = find_app(what) or _clean(what)
    exes = PROCESS_NAMES.get(name) or [n + ".exe" for n in {_clean(what).translate(_TRANSLIT).replace(" ", ""),
                                                              name.replace(" ", "")}]
    exes = [e.lower() for e in exes]
    if not _running(exes):
        return Result(f"{what} и так не запущен.", ok=False)

    # Word / Excel / PowerPoint: сами сохраняем всё и закрываем — без вопросов
    # (запущенных копий программы может быть несколько — обходим все)
    if name in OFFICE:
        saved = None
        for _ in range(6):
            part = _office_save_and_quit(name)
            if part is None:
                break
            saved = (saved or []) + part
            time.sleep(0.8)
            if not _running(exes):
                break
        if saved is not None:
            deadline = time.time() + 8
            while time.time() < deadline and _running(exes):
                time.sleep(0.3)
            what_saved = f" Сохранил: {', '.join(saved)}." if saved else ""
            return Result(f"Закрыл {name}.{what_saved}")

    # Steam на крестик прячется в трей — у него есть своя команда «выйти»
    if name == "steam":
        os.startfile("steam://exit")
    else:
        # Сначала вежливо, как крестик (/T — вместе с дочерними процессами)
        for exe in exes:
            subprocess.run(["taskkill", "/IM", exe, "/T"], capture_output=True)

    deadline = time.time() + (10 if name == "steam" else 3)
    while time.time() < deadline and _running(exes):
        time.sleep(0.3)
    if not _running(exes):
        return Result(f"Закрыл {name}.")

    if name in NO_FORCE:
        return Result(f"Закрываю {name} — если спросит про сохранение, ответь там.")
    # Не закрылся сам (спрятался в трей и т.п.) — закрываем принудительно
    for exe in exes:
        subprocess.run(["taskkill", "/IM", exe, "/T", "/F"], capture_output=True)
    time.sleep(0.5)
    return Result(f"Закрыл {name}." if not _running(exes) else f"{name} не закрывается, даже силой.",
                  ok=not _running(exes))


DOCS_DIR = Path.home() / "Documents"
_DOC_KINDS = {
    "word": "word", "ворд": "word", "документ": "word", "doc": "word", "docx": "word",
    "excel": "excel", "эксель": "excel", "ексель": "excel", "таблица": "excel", "таблицу": "excel", "xlsx": "excel",
    "блокнот": "txt", "текст": "txt", "txt": "txt", "текстовый": "txt", "заметка": "txt",
}


def _safe_filename(name: str) -> str:
    name = re.sub(r'[<>:"/\\|?*]+', " ", name).strip(" .")
    return name[:80] or f"Документ {datetime.now():%d.%m %H-%M}"


def _unique(path: Path) -> Path:
    if not path.exists():
        return path
    for i in range(2, 100):
        p = path.with_name(f"{path.stem} ({i}){path.suffix}")
        if not p.exists():
            return p
    return path


def create_document(kind: str, title: str = "", text: str = "") -> Result:
    """Новый документ Word / таблица Excel / файл Блокнота.
    С названием — сразу сохраняется в «Документы». С текстом — текст вписывается внутрь."""
    words = _clean(kind).split()
    k = next((_DOC_KINDS[w] for w in words if w in _DOC_KINDS), "word")
    title = title.strip()
    text = re.sub(r"\s*;\s*", "\n", text.strip())  # «хлеб; молоко; яйца» → каждый пункт с новой строки

    if k == "txt":
        path = _unique(DOCS_DIR / f"{_safe_filename(title)}.txt")
        path.write_text(text, encoding="utf-8")
        subprocess.Popen(["notepad.exe", str(path)])
        return Result(f"Создал файл «{path.name}» в Документах и открыл в блокноте.")

    import comtypes
    import comtypes.client
    comtypes.CoInitialize()
    app = None
    try:
        def office(prog_id):
            """Уже запущенная программа Office (чтобы не плодить копии) или новая."""
            try:
                return comtypes.client.GetActiveObject(prog_id)
            except Exception:
                return comtypes.client.CreateObject(prog_id)

        if k == "word":
            app = office("Word.Application")
            doc = app.Documents.Add()
            if text:
                doc.Content.Text = text
            app.Visible = True
            if title:
                path = _unique(DOCS_DIR / f"{_safe_filename(title)}.docx")
                doc.SaveAs2(str(path))
                remember_file(path)
                msg = f"Создал документ Word «{path.name}» в Документах"
            else:
                msg = "Открыл новый пустой документ Word"
        else:
            app = office("Excel.Application")
            book = app.Workbooks.Add()
            if text:
                for i, line in enumerate(l for l in text.splitlines() if l.strip()):
                    book.ActiveSheet.Range(f"A{i + 1}").Value2 = line.strip()  # по пункту в строку
            app.Visible = True
            if title:
                path = _unique(DOCS_DIR / f"{_safe_filename(title)}.xlsx")
                book.SaveAs(str(path))
                remember_file(path)
                msg = f"Создал таблицу Excel «{path.name}» в Документах"
            else:
                msg = "Открыл новую пустую таблицу Excel"
        try:  # вытаскиваем окно наверх
            app.WindowState = 1 if k == "word" else -4137
            app.Activate() if k == "word" else None
        except Exception:
            pass
        return Result(msg + (" и вписал текст." if text else "."))
    except Exception as e:
        # Не оставляем висеть невидимый Word/Excel («сироту» без окна)
        try:
            if app is not None and not app.Visible:
                app.DisplayAlerts = False
                app.Quit()
        except Exception:
            pass
        return Result(f"Не получилось создать документ: {e}", ok=False)


# --- буфер обмена и «напечатать» ---
def _clip_api():
    """Функции Windows для буфера обмена — с правильными 64-битными типами."""
    import ctypes.wintypes as wt
    k32, u32 = ctypes.WinDLL("kernel32"), ctypes.WinDLL("user32")
    k32.GlobalAlloc.argtypes, k32.GlobalAlloc.restype = [wt.UINT, ctypes.c_size_t], wt.HGLOBAL
    k32.GlobalLock.argtypes, k32.GlobalLock.restype = [wt.HGLOBAL], wt.LPVOID
    k32.GlobalUnlock.argtypes, k32.GlobalUnlock.restype = [wt.HGLOBAL], wt.BOOL
    u32.OpenClipboard.argtypes, u32.OpenClipboard.restype = [wt.HWND], wt.BOOL
    u32.GetClipboardData.argtypes, u32.GetClipboardData.restype = [wt.UINT], wt.HANDLE
    u32.SetClipboardData.argtypes, u32.SetClipboardData.restype = [wt.UINT, wt.HANDLE], wt.HANDLE
    return k32, u32


def _open_clipboard(u32) -> bool:
    for _ in range(20):  # буфер может быть занят другой программой — пробуем ещё раз
        if u32.OpenClipboard(None):
            return True
        time.sleep(0.05)
    return False


def _set_clipboard(text: str) -> None:
    """Положить текст в буфер обмена Windows (любой язык, любые буквы)."""
    k32, u32 = _clip_api()
    data = (text + "\0").encode("utf-16-le")
    if not _open_clipboard(u32):
        return
    try:
        u32.EmptyClipboard()
        h = k32.GlobalAlloc(0x0042, len(data))   # GMEM_MOVEABLE | GMEM_ZEROINIT
        ptr = k32.GlobalLock(h)
        ctypes.memmove(ptr, data, len(data))
        k32.GlobalUnlock(h)
        u32.SetClipboardData(13, h)               # CF_UNICODETEXT
    finally:
        u32.CloseClipboard()


def _get_clipboard() -> str | None:
    k32, u32 = _clip_api()
    if not _open_clipboard(u32):
        return None
    try:
        h = u32.GetClipboardData(13)
        if not h:
            return None
        ptr = k32.GlobalLock(h)
        try:
            return ctypes.wstring_at(ptr) if ptr else None
        finally:
            k32.GlobalUnlock(h)
    finally:
        u32.CloseClipboard()


_EXTENDED_VK = {0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28, 0x2D, 0x2E, 0x5B, 0x5C, 0xA3, 0xA5}


def _press(*keys: int) -> None:
    """Нажать клавиши как настоящая клавиатура: с аппаратным кодом (без него браузерные игры не видят, что нажато)."""
    u32 = ctypes.windll.user32

    def ev(k, up):
        sc = u32.MapVirtualKeyW(k, 0) & 0xFF
        flags = (2 if up else 0) | (1 if k in _EXTENDED_VK else 0)
        u32.keybd_event(k, sc, flags, 0)
    for k in keys:
        ev(k, False)
    for k in reversed(keys):
        ev(k, True)


def type_text(text: str) -> Result:
    """Вписать текст туда, где сейчас курсор (через буфер обмена — работает с любым языком)."""
    text = text.strip()
    if not text:
        return Result("Не понял, что напечатать.", ok=False)
    old = _get_clipboard()
    _set_clipboard(text)
    time.sleep(0.05)
    _press(0x11, 0x56)   # Ctrl+V
    time.sleep(0.25)
    if old is not None:  # возвращаем, что было в буфере
        _set_clipboard(old)
    return Result(f"Напечатал: {text}")


def play_video(query: str) -> Result:
    """Найти и сразу включить видео/песню на ютубе (не страницу поиска, а само видео)."""
    from . import web
    query = query.strip()
    if not query:
        return Result("Не понял, что включить.", ok=False)
    found = web.find_video(query)
    if not found:
        return youtube_search(query)          # не нашли конкретное — хотя бы поиск
    url, title = found
    webbrowser.open(url)
    return Result(f"Включил на ютубе: {title}.")


def copy_text(text: str) -> Result:
    _set_clipboard(text.strip())
    return Result("Скопировал в буфер.")


def web_search(query: str) -> Result:
    webbrowser.open("https://www.google.com/search?q=" + quote_plus(query))
    return Result(f"Открыл поиск: {query}.")


def youtube_search(query: str) -> Result:
    webbrowser.open("https://www.youtube.com/results?search_query=" + quote_plus(query))
    return Result(f"Открыл ютуб с поиском: {query}.")


# --- звук ---
def _volume_endpoint():
    from pycaw.pycaw import AudioUtilities
    speakers = AudioUtilities.GetSpeakers()
    if hasattr(speakers, "EndpointVolume"):
        return speakers.EndpointVolume
    from comtypes import CLSCTX_ALL
    from ctypes import POINTER, cast
    from pycaw.pycaw import IAudioEndpointVolume
    iface = speakers.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
    return cast(iface, POINTER(IAudioEndpointVolume))


def volume(arg: str) -> Result:
    import comtypes
    comtypes.CoInitialize()
    vol = _volume_endpoint()
    a = _clean(arg)
    current = round(vol.GetMasterVolumeLevelScalar() * 100)
    if a in ("выкл", "выключи", "без звука", "mute", "заглуши"):
        vol.SetMute(1, None)
        return Result("Звук выключил.")
    if a in ("вкл", "включи", "unmute", "верни"):
        vol.SetMute(0, None)
        return Result(f"Звук включил, громкость {current}.")
    if a in ("сколько", "какая", "текущая", "?"):
        return Result(f"Громкость сейчас {current} процентов.", info=True)
    m = re.search(r"([+-]?)\s*(\d+)", arg)
    if a.startswith(("громче", "выше", "погромче")):
        target = current + (int(m.group(2)) if m else 10)
    elif a.startswith(("тише", "ниже", "потише")):
        target = current - (int(m.group(2)) if m else 10)
    elif m:
        n = int(m.group(2))
        target = current + n if m.group(1) == "+" else current - n if m.group(1) == "-" else n
    else:
        return Result(f"Не понял, какую громкость поставить. Сейчас {current}.", ok=False)
    target = max(0, min(100, target))
    vol.SetMute(0, None)
    vol.SetMasterVolumeLevelScalar(target / 100, None)
    time.sleep(0.1)
    got = round(vol.GetMasterVolumeLevelScalar() * 100)        # самопроверка
    if abs(got - target) > 2:
        return Result(f"Хотел поставить громкость {target}, но стоит {got} — Windows не дала поменять.", ok=False)
    return Result(f"Громкость {target}.")


# --- музыка (медиаклавиши — работают с любым плеером, браузером, ютубом) ---
_MEDIA_KEYS = {"пауза": 0xB3, "играй": 0xB3, "плей": 0xB3, "стоп": 0xB3, "продолжи": 0xB3,
               "следующий": 0xB0, "дальше": 0xB0, "некст": 0xB0, "предыдущий": 0xB1, "назад": 0xB1}


def media(arg: str) -> Result:
    a = _clean(arg)
    key = next((v for k, v in _MEDIA_KEYS.items() if a.startswith(k)), None)
    if key is None:
        return Result("Не понял, что сделать с музыкой.", ok=False)
    ctypes.windll.user32.keybd_event(key, 0, 0, 0)
    ctypes.windll.user32.keybd_event(key, 0, 2, 0)
    return Result({0xB3: "Нажал паузу/плей.", 0xB0: "Следующий трек.", 0xB1: "Предыдущий трек."}[key])


# --- система ---
def system_info(_: str = "") -> Result:
    import psutil
    cpu = psutil.cpu_percent(interval=0.5)
    ram = psutil.virtual_memory()
    parts = [f"процессор загружен на {cpu:.0f}%",
             f"оперативка занята на {ram.percent:.0f}% ({ram.used / 2**30:.0f} из {ram.total / 2**30:.0f} ГБ)"]
    try:
        q = subprocess.run(["nvidia-smi", "--query-gpu=utilization.gpu,temperature.gpu,memory.used,memory.total",
                            "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=5).stdout
        u, t, mu, mt = [x.strip() for x in q.split(",")]
        parts.append(f"видеокарта загружена на {u}%, температура {round(float(t))} градусов, "
                     f"видеопамять {float(mu)/1024:.1f} из {float(mt)/1024:.0f} ГБ")
    except Exception:
        pass
    disk = psutil.disk_usage("C:\\")
    parts.append(f"на диске C свободно {disk.free / 2**30:.0f} ГБ")
    return Result("; ".join(parts) + ".", info=True)


def power(arg: str, confirmed: bool = False) -> Result:
    a = _clean(arg)
    if a.startswith(("заблок", "блок", "lock")):
        ctypes.windll.user32.LockWorkStation()
        return Result("Заблокировал комп.")
    kinds = {"выключ": ("выключить комп", ["shutdown", "/s", "/t", "5"]),
             "вырубить": ("выключить комп", ["shutdown", "/s", "/t", "5"]),
             "перезагр": ("перезагрузить комп", ["shutdown", "/r", "/t", "5"]),
             "ребут": ("перезагрузить комп", ["shutdown", "/r", "/t", "5"]),
             "сон": ("усыпить комп", None), "спать": ("усыпить комп", None)}
    kind = next((v for k, v in kinds.items() if a.startswith(k)), None)
    if kind is None:
        return Result("Не понял, что сделать с питанием.", ok=False)
    title, cmd = kind
    if not confirmed:
        return Result("", confirm=f"Точно {title}?")
    if cmd is None:
        subprocess.Popen(["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"])
    else:
        subprocess.Popen(cmd)
    return Result(f"Окей, {title} через пять секунд." if cmd else "Усыпляю.")


def cancel_shutdown(_: str = "") -> Result:
    r = subprocess.run(["shutdown", "/a"], capture_output=True)
    return Result("Отменил выключение." if r.returncode == 0 else "Нечего отменять.")


# --- заметки ---
def add_note(text: str) -> Result:
    NOTES_FILE.parent.mkdir(parents=True, exist_ok=True)
    with NOTES_FILE.open("a", encoding="utf-8") as f:
        f.write(f"{datetime.now():%d.%m.%Y %H:%M} — {text.strip()}\n")
    return Result("Записал.")


def read_notes(_: str = "") -> Result:
    if not NOTES_FILE.exists():
        return Result("Заметок пока нет.", info=True)
    lines = [l for l in NOTES_FILE.read_text(encoding="utf-8").splitlines() if l.strip()]
    return Result("Последние заметки: " + " | ".join(lines[-5:]), info=True)


def open_notes(_: str = "") -> Result:
    NOTES_FILE.parent.mkdir(parents=True, exist_ok=True)
    NOTES_FILE.touch()
    os.startfile(NOTES_FILE)
    return Result("Открыл файл с заметками.")


# --- напоминания ---
_reminder_callback = None  # что делать, когда пора напомнить (задаёт главная программа)
_reminders: list[threading.Timer] = []


def set_reminder_callback(fn) -> None:
    global _reminder_callback
    _reminder_callback = fn


def _parse_delay(s: str) -> float | None:
    # «в 18:30», «18.30», «в 7 утра», «в 9 вечера» — к точному времени (если уже прошло — завтра)
    m = re.search(r"(?:^|\bв\s+|\bна\s+|\bat\s+|\bum\s+)(\d{1,2})(?:[:.](\d{2}))?\s*(утра|дня|вечера|ночи)?\s*$", s.strip(), re.I)
    if m and (m.group(2) or m.group(3) or re.search(r"\bв\s+\d", s, re.I)):
        h, mi = int(m.group(1)), int(m.group(2) or 0)
        part = (m.group(3) or "").lower()
        if part in ("дня", "вечера") and h < 12:
            h += 12
        if part == "ночи" and h == 12:
            h = 0
        now = datetime.now()
        target = now.replace(hour=h % 24, minute=mi, second=0, microsecond=0)
        extra = 2 if re.search(r"послезавтра", s, re.I) else 1 if re.search(r"завтра", s, re.I) else 0
        if extra:
            target += timedelta(days=extra)
        elif target <= now:
            target += timedelta(days=1)
        return (target - now).total_seconds()
    s = _clean(s)
    total = 0.0
    for num, unit in re.findall(r"(\d+(?:[.,]\d+)?)\s*(час|ч|мин|м|сек|с)", s):
        n = float(num.replace(",", "."))
        total += n * (3600 if unit.startswith(("час", "ч")) else 60 if unit.startswith("м") else 1)
    if total:
        return total
    m = re.fullmatch(r"(\d+(?:[.,]\d+)?)", s)
    if m:
        return float(m.group(1).replace(",", ".")) * 60  # просто число — минуты
    if "полчаса" in s:
        return 1800
    if "час" in s:
        return 3600
    return None


_REMINDERS_FILE = config.DATA_DIR / "напоминания.json"   # чтобы напоминания пережили перезапуск
_rem_lock = threading.Lock()


def _load_reminders() -> list[dict]:
    try:
        return json.loads(_REMINDERS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []


def _save_reminders(items: list[dict]) -> None:
    _REMINDERS_FILE.parent.mkdir(parents=True, exist_ok=True)
    _REMINDERS_FILE.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")


def _schedule(item: dict) -> None:
    sec = max(1.0, (datetime.fromisoformat(item["when"]) - datetime.now()).total_seconds())

    def fire():
        with _rem_lock:
            _save_reminders([r for r in _load_reminders() if r.get("id") != item["id"]])
        if _reminder_callback:
            _reminder_callback(item["text"])
    t = threading.Timer(sec, fire)
    t.daemon = True
    t.start()
    _reminders.append(t)


def remind(delay: str, text: str) -> Result:
    sec = _parse_delay(delay)
    if not sec:
        return Result(f"Не понял, через сколько напомнить («{delay}»).", ok=False)
    when = datetime.now() + timedelta(seconds=sec)
    item = {"id": f"{time.time():.6f}", "when": when.isoformat(timespec="seconds"), "text": text}
    with _rem_lock:
        _save_reminders(_load_reminders() + [item])
    _schedule(item)
    day = "" if when.date() == datetime.now().date() else " завтра"
    return Result(f"Напомню{day} в {when:%H:%M}: {text}.")


def restore_reminders() -> list[str]:
    """При запуске: снова завести сохранённые напоминания. Возвращает пропущенные (пока комп был выключен)."""
    now = datetime.now()
    keep, missed = [], []
    with _rem_lock:
        for r in _load_reminders():
            try:
                when = datetime.fromisoformat(r["when"])
            except Exception:
                continue
            if when > now:
                keep.append(r)
            elif now - when < timedelta(hours=24):
                missed.append(f"{r['text']} (было на {when:%H:%M})")
        _save_reminders(keep)
    for r in keep:
        _schedule(r)
    return missed


def _photo(task: str = "", file: str = "") -> Result:
    from . import media
    return media.photo(task, file)


def _video(task: str = "", file: str = "") -> Result:
    from . import media
    return media.video(task, file)


def _draw(description: str = "") -> Result:
    from . import media
    if not description.strip():
        return Result("Не понял, что нарисовать.", ok=False)
    return media.draw(description)


# ---------- глаза, мышка, клавиатура, команды, агент ----------
_progress = None          # куда сообщать ход длинной задачи (голос/журнал или Telegram) — задаёт главная программа
_current_confirm = None   # как спросить «точно?» в текущем разговоре (передаётся из execute_all)


def set_progress(fn) -> None:
    global _progress
    _progress = fn


def screen_look(question: str = "") -> Result:
    from . import vision
    try:
        return Result("На экране: " + vision.look(question), info=True)
    except Exception as e:
        return Result(f"Не смог посмотреть на экран: {e}", ok=False)


def press_click(what: str = "") -> Result:
    from . import vision
    if not what.strip():
        return Result("Не понял, что нажать.", ok=False)
    try:
        before = _screen_thumb()
        text = vision.click(what)
        time.sleep(0.8)
        changed = _screen_diff(before, _screen_thumb())
        if changed < 0.003:                                     # самопроверка: на экране ничего не поменялось
            return Result(text + " Но на экране ничего не изменилось — возможно, промахнулся.", ok=False)
        return Result(text)
    except LookupError as e:
        return Result(str(e), ok=False)


def _screen_thumb():
    from PIL import ImageGrab
    return ImageGrab.grab(all_screens=True).convert("L").resize((160, 90))


def _screen_diff(a, b) -> float:
    """Доля заметно изменившихся точек между двумя уменьшенными снимками."""
    from PIL import ImageChops
    d = ImageChops.difference(a, b).point(lambda v: 255 if v > 25 else 0)
    return sum(1 for v in d.getdata() if v) / (a.width * a.height)


def press_keys(combo: str = "") -> Result:
    from . import vision
    try:
        return Result(vision.hotkey(combo))
    except ValueError as e:
        return Result(str(e), ok=False)


def ps_command(cmd: str = "", confirmed: bool = False) -> Result:
    from . import agent
    cmd = cmd.strip().strip("`")
    if not cmd:
        return Result("Пустая команда.", ok=False)
    why = agent.check_command(cmd)
    if why:
        return Result(why, ok=False)
    if not confirmed:
        return Result("", confirm=f"Выполнить команду Windows?\n{cmd}")
    try:
        return Result("Результат команды: " + agent.run_command(cmd), info=True)
    except subprocess.TimeoutExpired:
        return Result("Команда работала слишком долго, остановил.", ok=False)


def agent_task(goal: str = "") -> Result:
    from . import agent
    if not goal.strip():
        return Result("Не понял задачу.", ok=False)
    text = agent.run_task(goal, _current_confirm or (lambda q: False), _progress)
    return Result(text, info=True, ok=not text.startswith(("Не вышло", "Остановился")))


def go_to(where: str = "") -> Result:
    """«Зайди в настройки кс» — сразу нужное место по прямой ссылке; если такой нет — агент с глазами."""
    from . import goto
    w = (where or "").strip()
    if not w:
        return Result("Не понял, куда зайти.", ok=False)
    said = None
    for q in (w, "зайди в " + w):          # «зайди в настройки кс» / просто «настройки кс»
        try:
            said = goto.goto(q)
        except Exception:
            said = None
        if said:
            break
    if said:
        return Result(said)
    return agent_task("зайди в " + re.sub(r"^\W*(зайди|перейди|открой)\w*\s+(в|во|на)?\s*", "", w, flags=re.I))


def game_make(desc: str = "") -> Result:
    from . import games
    if not desc.strip():
        return Result("Не понял, какую игру сделать.", ok=False)
    ok, text, path = games.make(desc, _progress)
    if path:
        global last_document
        last_document = path
    return Result(text, ok=ok)


def game_improve(change: str = "") -> Result:
    from . import games
    ok, text, path = games.improve(change or "исправь ошибки", _progress)
    if path:
        global last_document
        last_document = path
    return Result(text, ok=ok)


def game_exe(hint: str = "") -> Result:
    """Упаковать игру в один .exe (по названию или последнюю)."""
    from . import exebuild
    game = exebuild.find_game(hint)
    if not game:
        return Result("Пока нет ни одной игры — сначала скажи «сделай игру …».", ok=False)
    if _progress:
        _progress(f"Собираю {game.stem}.exe…")
    try:
        exe = exebuild.build(game)
    except Exception as e:
        return Result(f"Не вышло собрать exe: {e}", ok=False)
    global last_document
    last_document = exe
    mb = exe.stat().st_size / 1_048_576
    return Result(f"Готово: «{exe.name}» ({mb:.1f} МБ) в папке «Документы → Игры Мартышки». "
                  f"Это один файл — можно скинуть другу. F11 — во весь экран.", info=True)


def battery_info(_: str = "") -> Result:
    from . import briefing
    return Result(briefing.battery_text(), info=True)


def recall(question: str = "") -> Result:
    """Вспомнить из долгой памяти: что было в какой-то день, что говорил кто-то, о чём договаривались."""
    from datetime import date, timedelta
    from . import longmem
    q = (question or "").lower()
    when = None
    if "позавчера" in q:
        when = date.today() - timedelta(days=2)
    elif "вчера" in q:
        when = date.today() - timedelta(days=1)
    elif "сегодня" in q:
        when = date.today()
    if when is not None:
        return Result(f"Память за {when.isoformat()}:\n" + longmem.day_text(when), info=True)
    return Result("Нашлось в памяти:\n" + longmem.search(question), info=True)


def briefing_info(_: str = "") -> Result:
    from . import briefing
    return Result(briefing.collect(), info=True)


def reminders_today() -> list[str]:
    """Что ещё напомнить сегодня — для утренней сводки."""
    today = datetime.now().date()
    out = []
    for r in sorted(_load_reminders(), key=lambda r: r.get("when", "")):
        try:
            when = datetime.fromisoformat(r["when"])
        except Exception:
            continue
        if when.date() == today and when > datetime.now():
            out.append(f"в {when:%H:%M} — {r['text']}")
    return out


# --- файлы ---
def find_file(name: str) -> Result:
    home = Path.home()
    roots = [home / d for d in ("Desktop", "Documents", "Downloads", "Pictures", "Videos", "Music")]
    roots += [home / "OneDrive" / d for d in ("Desktop", "Documents", "Pictures")]
    q = _clean(name)
    words = q.split()
    deadline = time.time() + 4
    found = []
    for root in roots:
        if not root.exists():
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if not d.startswith(".") and d not in ("node_modules", ".venv")]
            for f in filenames + dirnames:
                fl = _clean(f)
                if all(w in fl or w.translate(_TRANSLIT) in fl for w in words):
                    found.append(Path(dirpath) / f)
            if time.time() > deadline or len(found) > 20:
                break
    if not found:
        return Result(f"Не нашёл файлов с «{name}» в Документах, Загрузках, на Рабочем столе.", ok=False)
    found.sort(key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)
    subprocess.Popen(["explorer.exe", "/select,", str(found[0])])
    names = ", ".join(p.name for p in found[:3])
    return Result(f"Нашёл {len(found)}: {names}. Показал самый свежий в проводнике.")


# ---------- экран, окна, папки, выделенный текст ----------
def screenshot(_: str = "") -> Result:
    from PIL import ImageGrab
    folder = Path.home() / "Pictures" / "Скриншоты Мартышки"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"Скриншот {datetime.now():%Y-%m-%d %H-%M-%S}.png"
    ImageGrab.grab(all_screens=True).save(path)
    remember_file(path)
    return Result(f"Сделал скриншот, он в папке «Изображения → Скриншоты Мартышки».")


def list_windows(with_dark: bool = True) -> list[dict]:
    """Все видимые окна: название, программа, размер, свёрнуто ли, активное ли и тёмное ли (почти чёрное)."""
    import ctypes.wintypes
    import psutil
    u32 = ctypes.windll.user32
    u32.GetWindow.restype = ctypes.c_void_p
    u32.GetForegroundWindow.restype = ctypes.c_void_p
    out = []
    fg = u32.GetForegroundWindow()
    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    def cb(h, _):
        if not u32.IsWindowVisible(h) or u32.GetWindow(h, 4):          # 4 = GW_OWNER: всплывашки пропускаем
            return True
        n = u32.GetWindowTextLengthW(h)
        title = ctypes.create_unicode_buffer(n + 1)
        u32.GetWindowTextW(h, title, n + 1)
        r = ctypes.wintypes.RECT()
        u32.GetWindowRect(h, ctypes.byref(r))
        w_, h_ = r.right - r.left, r.bottom - r.top
        if w_ < 200 or h_ < 120:
            return True
        pid = ctypes.c_ulong()
        u32.GetWindowThreadProcessId(h, ctypes.byref(pid))
        try:
            exe = psutil.Process(pid.value).name().lower().removesuffix(".exe")
        except Exception:
            exe = ""
        if exe in ("textinputhost", "applicationframehost", "shellexperiencehost", "searchhost", "startmenuexperiencehost",
                   "explorer") and not title.value:
            return True
        if title.value in ("Program Manager",) or exe in ("pythonw", "мартышка", "textinputhost", "nvidia overlay",
                                                          "shellexperiencehost", "searchhost"):
            return True
        # внутренняя часть окна (без шапки с заголовком) — по ней судим, тёмное ли окно
        cr = ctypes.wintypes.RECT()
        u32.GetClientRect(h, ctypes.byref(cr))
        pt = ctypes.wintypes.POINT(0, 0)
        u32.ClientToScreen(h, ctypes.byref(pt))
        client = (pt.x, pt.y, pt.x + cr.right, pt.y + cr.bottom)
        out.append({"hwnd": h, "title": title.value, "exe": exe, "rect": (r.left, r.top, r.right, r.bottom),
                    "client": client, "minimized": bool(u32.IsIconic(h)), "active": h == fg})
        return True
    u32.EnumWindows(WNDENUMPROC(cb), 0)
    if with_dark:
        try:
            from PIL import ImageGrab
            shot = ImageGrab.grab(all_screens=True).convert("L")
            # координаты окон и снимок могут быть в разном масштабе (масштаб экрана Windows 125%/150%)
            vx, vy = u32.GetSystemMetrics(76), u32.GetSystemMetrics(77)          # левый верх всех экранов
            vw = u32.GetSystemMetrics(78) or shot.width
            k = shot.width / vw
            for w in out:
                if w["minimized"]:
                    w["dark"] = False
                    continue
                l, t, r, b = w["client"]
                box = (int((l - vx) * k), int((t - vy) * k), int((r - vx) * k), int((b - vy) * k))
                box = (max(0, box[0]), max(0, box[1]), min(shot.width, max(box[0] + 1, box[2])),
                       min(shot.height, max(box[1] + 1, box[3])))
                crop = shot.crop(box).resize((32, 18))
                px = list(crop.getdata())
                w["dark"] = sum(px) / len(px) < 22 and max(px) - min(px) < 60   # почти чёрное и почти пустое
        except Exception:
            pass
    return out


def _pick_window(target: str):
    """Какое окно имеет в виду брат: «чёрное/пустое», по названию программы или заголовку; иначе активное."""
    wins = [w for w in list_windows() if not w["minimized"] or re.search(r"разверн|покажи|открой", target)]
    t = _clean(target)
    if not t or re.fullmatch(r"(это|этот|эту|его|текущее|активное)?\s*(окно)?", t):
        return next((w for w in wins if w["active"]), None)
    if re.search(r"черн|тёмн|темн|пуст|чёрн", t):
        dark = [w for w in wins if w.get("dark")]
        return max(dark, key=lambda w: (w["rect"][2] - w["rect"][0]) * (w["rect"][3] - w["rect"][1])) if dark else None
    name = APP_ALIASES.get(t, t)
    for w in wins:
        hay = f"{w['title']} {w['exe']}".lower()
        if name in hay or t in hay or any(len(x) > 3 and x in hay for x in (name.split() + t.split())):
            return w
    best, score = None, 0.0
    for w in wins:
        s = max(difflib.SequenceMatcher(None, t.translate(_TRANSLIT), w["exe"]).ratio(),
                difflib.SequenceMatcher(None, t, w["title"].lower()[:len(t) + 10]).ratio())
        if s > score:
            best, score = w, s
    return best if score >= 0.6 else None


def windows_info(_: str = "") -> Result:
    """Что сейчас открыто на экране (для «что у меня открыто?» и для мозга)."""
    wins = list_windows()
    if not wins:
        return Result("Открытых окон не вижу.", info=True)
    parts = []
    for w in wins[:15]:
        flags = ", ".join(f for f, on in (("активное", w["active"]), ("свёрнуто", w["minimized"]),
                                          ("тёмное/пустое", w.get("dark"))) if on)
        parts.append(f"{w['title'] or '(без названия)'} — {w['exe']}{f' ({flags})' if flags else ''}")
    return Result("Открытые окна: " + "; ".join(parts), info=True)


def window_action(what: str, target: str = "") -> Result:
    u32 = ctypes.windll.user32
    w = _clean(what)
    # «закрой чёрное окно», «сверни телеграм»: цель может быть и в самом действии
    if not target:
        m = re.search(r"(закрыть|закрой|свернуть|сверни|развернуть|разверни|показать|покажи)\s+(.+)$", w)
        if m and not re.search(r"^(все|всё)\b", m.group(2)):
            target = m.group(2)
    if target and not re.search(r"все|всё|рабоч", w):
        win = _pick_window(target)
        if win is None:
            return Result(f"Не нашёл окно «{target}». {windows_info().text}", ok=False)
        hwnd = win["hwnd"]
        label = win["title"] or win["exe"]
        if re.search(r"закр", w):
            u32.PostMessageW(hwnd, 0x0010, 0, 0)
            for _ in range(10):                                   # самопроверка: закрылось ли
                time.sleep(0.2)
                if not u32.IsWindow(hwnd) or not u32.IsWindowVisible(hwnd):
                    return Result(f"Закрыл окно «{label}».")
            return Result(f"Окно «{label}» не закрылось — наверное, программа что-то спрашивает (например, "
                          "«Сохранить?»). Посмотри на экран.", ok=False)
        if re.search(r"сверн", w):
            u32.ShowWindow(hwnd, 6)
            time.sleep(0.2)
            return Result(f"Свернул «{label}»." if u32.IsIconic(hwnd) else f"«{label}» не свернулось.",
                          ok=bool(u32.IsIconic(hwnd)))
        u32.ShowWindow(hwnd, 9)                                  # SW_RESTORE
        u32.ShowWindow(hwnd, 3)
        u32.SetForegroundWindow(hwnd)
        return Result(f"Развернул «{label}».")
    hwnd = u32.GetForegroundWindow()
    if re.search(r"все|всё|рабоч", w):                          # свернуть всё / показать рабочий стол
        _press(0x5B, 0x44)                                      # Win+D
        return Result("Свернул все окна.")
    if re.search(r"переключ|следующ|другое", w):
        _press(0x12, 0x09)                                      # Alt+Tab
        return Result("Переключил окно.")
    if re.search(r"сверн", w):
        u32.ShowWindow(hwnd, 6)                                 # SW_MINIMIZE
        return Result("Свернул окно.")
    if re.search(r"развер|на весь|максим", w):
        u32.ShowWindow(hwnd, 3)                                 # SW_MAXIMIZE
        return Result("Развернул окно.")
    if re.search(r"закр", w):
        u32.PostMessageW(hwnd, 0x0010, 0, 0)                    # WM_CLOSE — как крестик
        return Result("Закрыл окно.")
    return Result("Не понял, что сделать с окном.", ok=False)


FOLDERS = {"загрузк": "Downloads", "скачан": "Downloads", "документ": "Documents", "рабоч": "Desktop",
           "картин": "Pictures", "изображ": "Pictures", "фото": "Pictures", "видео": "Videos", "музык": "Music",
           "скриншот": "Pictures/Скриншоты Мартышки", "мартышк": None}


def open_folder(what: str) -> Result:
    w = _clean(what)
    for key, sub in FOLDERS.items():
        if key in w:
            path = config.PROJECT_DIR if sub is None else Path.home() / sub
            if not path.exists() and sub and (Path.home() / "OneDrive" / sub).exists():
                path = Path.home() / "OneDrive" / sub
            os.startfile(path)
            return Result(f"Открыл папку {path.name}.")
    return Result(f"Не знаю папку «{what}».", ok=False)


def _grab_selection() -> str:
    """Скопировать то, что брат выделил (Ctrl+C), и вернуть текст. Буфер потом возвращаем как был."""
    old = _get_clipboard()
    _set_clipboard("")
    _press(0x11, 0x43)       # Ctrl+C
    time.sleep(0.35)
    text = _get_clipboard() or ""
    if old is not None:
        _set_clipboard(old)
    return text.strip()


def selection_action(task: str) -> Result:
    """Что-то сделать с выделенным текстом: прочитать, перевести, пересказать, исправить ошибки."""
    text = _grab_selection()
    if not text:
        return Result("Ничего не выделено — выдели текст и скажи ещё раз.", ok=False)
    t = _clean(task)
    # «Исправь / перепиши / переведи и вставь» — результат вставляем на место выделенного
    if re.search(r"исправ|перепиш|замени|вставь|сократи", t) and _llm is not None:
        new = _llm(f"Задача: {task}.\nТекст:\n{text}\n\nВерни ТОЛЬКО готовый текст, без пояснений и кавычек.", 1500)
        if new.strip():
            old = _get_clipboard()
            _set_clipboard(new.strip())
            _press(0x11, 0x56)   # Ctrl+V — заменяем выделенное
            time.sleep(0.25)
            if old is not None:
                _set_clipboard(old)
            return Result("Готово, заменил выделенный текст.")
    # Иначе — справка: мозг сам прочитает / переведёт / перескажет
    return Result(f"Выделенный текст (задача брата: {task}):\n{text[:3000]}", info=True)


BROWSERS = ("chrome", "msedge", "firefox", "opera", "brave", "browser", "vivaldi", "yandex")
to_read: list[str] = []   # что прочитать вслух целиком (заполняет read_aloud, озвучивает главная программа)


def _foreground_exe() -> str:
    """Имя программы активного окна (chrome, winword...)."""
    try:
        import psutil
        pid = ctypes.c_ulong()
        ctypes.windll.user32.GetWindowThreadProcessId(ctypes.windll.user32.GetForegroundWindow(), ctypes.byref(pid))
        return psutil.Process(pid.value).name().lower().removesuffix(".exe")
    except Exception:
        return ""


def _browser_url() -> str:
    """Адрес открытой вкладки браузера (Ctrl+L, Ctrl+C, Esc). Буфер потом возвращаем."""
    old = _get_clipboard()
    _set_clipboard("")
    _press(0x11, 0x4C)       # Ctrl+L — встать в адресную строку
    time.sleep(0.15)
    _press(0x11, 0x43)       # Ctrl+C
    time.sleep(0.3)
    _press(0x1B)             # Esc — вернуть фокус странице
    url = (_get_clipboard() or "").strip()
    if old is not None:
        _set_clipboard(old)
    return url if re.match(r"https?://", url) else ""


def read_aloud(what: str = "") -> Result:
    """Прочитать вслух: выделенный текст, а если ничего не выделено и открыт браузер — статью со страницы."""
    to_read.clear()
    want_page = bool(re.search(r"страниц|стать|сайт|вкладк|page|seite|artikel", what or "", re.I))
    text = "" if want_page else _grab_selection()
    if not text and _foreground_exe().startswith(BROWSERS):
        url = _browser_url()
        if url:
            from . import web
            text = web.fetch_text(url, limit=20000)
            if not text:
                return Result("Не смог достать текст с этой страницы (сайт не пускает или там одно видео).", ok=False)
    if not text:
        return Result("Нечего читать: выдели текст мышкой или открой статью в браузере и скажи ещё раз.", ok=False)
    to_read.append(text[:20000])
    return Result(f"Читаю вслух ({len(text)} знаков).")


def clipboard_info(_: str = "") -> Result:
    text = (_get_clipboard() or "").strip()
    return Result(f"В буфере обмена: {text[:2000]}" if text else "Буфер обмена пустой.", info=True)


# ---------- интернет и большие задачи ----------
_llm = None   # мозг для служебных задач (составить таблицу, написать текст) — задаёт главная программа


def set_llm(fn) -> None:
    global _llm
    _llm = fn


WEATHER_RE = re.compile(r"(погод|градус|температур|дожд|снег|ветр|ветер|холодн|тепл\w* на улиц|жарк\w* на улиц|"
                        r"прогноз|зонт|wetter|regen|weather|grad\b|темпер)", re.IGNORECASE)


def research(question: str) -> Result:
    """Тихо найти ответ в интернете (без браузера на экране)."""
    if not config.WEB_ENABLED:
        return Result("Интернет выключен в настройках.", ok=False)
    # Вопрос про погоду («сколько градусов», «будет дождь») — это погода, а не общий поиск
    if WEATHER_RE.search(question):
        city = re.search(r"\b(?:в|во|in)\s+([A-ZА-ЯЁ][\w\-() ]+)", question)
        return weather_now(city.group(1) if city else "")
    from . import web
    material = web.research(question)
    if not material:
        return Result(f"В интернете ничего толком не нашёл про «{question}».", ok=False)
    return Result(material, info=True)


def weather_now(city: str = "") -> Result:
    from . import web
    try:
        return Result(web.weather(city), info=True)
    except Exception as e:
        return Result(f"Не смог узнать погоду: {e}", ok=False)


def _num(v: str):
    """«1,5» / «1.008» → число (чтобы Excel считал), остальное — как текст."""
    s = str(v).strip().replace("−", "-").replace(" ", "")
    if re.fullmatch(r"-?\d+([.,]\d+)?", s):
        return float(s.replace(",", ".")) if re.search(r"[.,]", s) else int(s)
    return str(v)


def _llm_table(topic: str, material: str = "") -> tuple[list[str], list[list[str]]] | None:
    """Если готовой таблицы в интернете нет — мозг составляет её сам."""
    if _llm is None:
        return None
    hint = f"\nМатериалы из интернета (используй их):\n{material[:2500]}\n" if material else ""
    out = _llm(f"Составь таблицу: «{topic}».{hint}\nФормат: первая строка — заголовки столбцов, дальше строки данных; "
               "ячейки разделяй символом | ; одна строка таблицы — одна строка текста. Не больше 60 строк. "
               "Только таблица, без пояснений.", 3000)
    lines = [l.strip().strip("|") for l in out.splitlines() if "|" in l and not re.fullmatch(r"[\s|:\-]+", l)]
    if len(lines) < 2:
        return None
    rows = [[c.strip() for c in l.split("|")] for l in lines]
    return rows[0], rows[1:]


def _release(*objs) -> None:
    """Отпустить связь с Word/Excel (иначе после закрытия окна программа может висеть невидимой).
    Сами объекты освобождаются, когда функция заканчивается; тут просто подчищаем «мусор»."""
    import gc
    del objs
    gc.collect()


def _bring_to_front(hwnd: int) -> None:
    """Вывести окно на передний план (чтобы брат видел, как Мартышка работает)."""
    try:
        u32, k32 = ctypes.windll.user32, ctypes.windll.kernel32
        u32.ShowWindow(hwnd, 3)                                    # развернуть
        fg = u32.GetForegroundWindow()
        me, other = k32.GetCurrentThreadId(), u32.GetWindowThreadProcessId(fg, None)
        u32.AttachThreadInput(me, other, True)                     # «договариваемся» с активным окном
        u32.SetForegroundWindow(hwnd)
        u32.BringWindowToTop(hwnd)
        u32.AttachThreadInput(me, other, False)
    except Exception:
        pass


def _excel_app():
    import comtypes
    import comtypes.client
    comtypes.CoInitialize()
    try:
        return comtypes.client.GetActiveObject("Excel.Application")
    except Exception:
        return comtypes.client.CreateObject("Excel.Application")


def make_table(topic: str, details: str = "") -> Result:
    """Таблица по теме в Excel — собирается у брата на глазах, строка за строкой."""
    from . import web
    topic = topic.strip()
    if not topic:
        return Result("Не понял, какую таблицу делать.", ok=False)
    found = web.find_table(f"{topic} {details}".strip()) if config.WEB_ENABLED else None
    source = "из интернета"
    if found:
        headers, rows, url = found
        source = f"взял с {url.split('/')[2]}"
    else:
        material = web.research(topic) if config.WEB_ENABLED else ""
        made = _llm_table(f"{topic} {details}".strip(), material)
        if not made:
            return Result(f"Не смог собрать таблицу «{topic}».", ok=False)
        headers, rows = made
        source = "составил сам" + (" по материалам из интернета" if material else "")
    width = max(len(headers), max((len(r) for r in rows), default=0))
    headers = (headers + [""] * width)[:width]
    rows = [(r + [""] * width)[:width] for r in rows]

    app = _excel_app()
    app.Visible = True
    book = app.Workbooks.Add()
    try:
        app.WindowState = -4137          # развернуть окно
        _bring_to_front(int(app.Hwnd))
    except Exception:
        pass
    sh = book.ActiveSheet
    sh.Name = re.sub(r'[\[\]:*?/\\]', " ", topic)[:30] or "Таблица"
    col = lambda i: chr(65 + i) if i < 26 else chr(64 + i // 26) + chr(65 + i % 26)
    last = col(width - 1)
    # заголовок — жирный, с заливкой
    head = sh.Range(f"A1:{last}1")
    head.Value2 = tuple(headers)          # Excel через COM берёт строку только простым списком значений
    head.Font.Bold = True
    head.Interior.Color = 0xF2D9BD    # светло-голубая заливка (BGR)
    app.ActiveWindow.SplitRow = 1
    app.ActiveWindow.FreezePanes = True
    # строки — по одной, с маленькой паузой: видно, как таблица собирается
    pause = max(0.01, min(0.12, 4.0 / max(1, len(rows))))
    for i, r in enumerate(rows, start=2):
        sh.Range(f"A{i}:{last}{i}").Value2 = tuple(_num(v) for v in r)
        if i % 3 == 0:
            sh.Range(f"A{i}").Select()
        time.sleep(pause)
    sh.Range(f"A1:{last}{len(rows) + 1}").Columns.AutoFit()
    sh.Range("A1").Select()
    path = _unique(DOCS_DIR / f"{_safe_filename(topic)}.xlsx")
    book.SaveAs(str(path))
    remember_file(path)
    _release(sh, head, book, app)   # отпускаем Excel: брат закроет окно — программа закроется целиком
    return Result(f"Сделал таблицу «{topic}» в Excel: {len(rows)} строк, {width} столбцов, {source}. "
                  f"Сохранил в Документы как {path.name}.")


last_document: Path | None = None   # последний написанный документ (Telegram пришлёт его файлом)

# Сленг и сокращения → нормальное название (иначе поиск находит «КС-23» вместо Counter-Strike 2)
TOPIC_ALIASES = [
    (r"\b(кс|cs|контр\w*)\s*-?\s*2\b|\bкс2\b|\bcs2\b", "Counter-Strike 2 (CS2), компьютерная игра"),
    (r"\b(кс|cs)\s*:?\s*го\b|\bcs\s*:?\s*go\b|\bксго\b", "Counter-Strike: Global Offensive (CS:GO), компьютерная игра"),
    (r"\b(кс|cs)\s*1\.6\b", "Counter-Strike 1.6, компьютерная игра"),
    (r"\bдот\w*\s*2?\b|\bdota\s*2?\b", "Dota 2, компьютерная игра"),
    (r"\bгта\s*(5|v|пять)\b|\bgta\s*(5|v)\b", "Grand Theft Auto V (GTA 5), компьютерная игра"),
    (r"\bгта\b|\bgta\b", "серия игр Grand Theft Auto (GTA)"),
    (r"\bмайн\w*\b|\bminecraft\b", "Minecraft, компьютерная игра"),
    (r"\bвалорант\w*\b|\bвалик\w*\b|\bvalorant\b", "Valorant, компьютерная игра"),
    (r"\bпабг\w*\b|\bпубг\w*\b|\bpubg\b", "PUBG: Battlegrounds, компьютерная игра"),
    (r"\bфортнайт\w*\b|\bfortnite\b", "Fortnite, компьютерная игра"),
    (r"\bварфейс\w*\b|\bwarface\b", "Warface, компьютерная игра"),
    (r"\bрдр\s*2\b|\brdr\s*2\b", "Red Dead Redemption 2, компьютерная игра"),
    (r"\bетс\s*2\b|\bets\s*2\b|\bевро\s*трак\w*", "Euro Truck Simulator 2, компьютерная игра"),
]


def normalize_topic(topic: str) -> str:
    for pattern, full in TOPIC_ALIASES:
        if re.search(pattern, topic, re.IGNORECASE):
            rest = re.sub(pattern, "", topic, flags=re.IGNORECASE).strip(" ,.-")
            return f"{full}{(': ' + rest) if rest else ''}"
    return topic


def _clean_paras(text: str) -> list[str]:
    text = re.sub(r"[*#]+", "", text or "")
    return [re.sub(r"^\s*[-•]\s+", "", p).strip() for p in text.splitlines() if p.strip()]


def _page_fill(doc, target: int) -> float:
    """Какую долю текста надо оставить, чтобы влезть в target страниц (по высоте последней строки)."""
    try:
        last = doc.Paragraphs(doc.Paragraphs.Count).Range
        pages = int(last.Information(3))
        y = float(last.Information(6))
        ps = doc.PageSetup
        usable = ps.PageHeight - ps.TopMargin - ps.BottomMargin
        used = (pages - 1) * usable + max(0.0, y - ps.TopMargin)
        return max(0.4, min(1.0, target * usable / used))
    except Exception:
        return 0.75


def _fit_pages(doc, target: int) -> int:
    """Подогнать оформление, чтобы текст занял ровно target страниц: крупнее, если места много, мельче — если вылезает."""
    variants = [(14, 1.5, 10, 18), (13, 1.5, 8, 17), (13, 1.3, 8, 16), (12, 1.5, 6, 15), (12, 1.3, 6, 15),
                (12, 1.15, 6, 14), (11.5, 1.15, 6, 14), (11, 1.15, 4, 13), (11, 1.0, 4, 13), (10.5, 1.0, 3, 12),
                (10, 1.0, 2, 11)]
    normal, h1, h2 = doc.Styles(-1), doc.Styles(-2), doc.Styles(-3)
    best = None
    for size, line, after, hsize in variants:
        normal.Font.Size = size
        normal.ParagraphFormat.LineSpacingRule = 5            # множитель
        normal.ParagraphFormat.LineSpacing = 12 * line
        normal.ParagraphFormat.SpaceAfter = after
        h2.Font.Size = hsize; h2.ParagraphFormat.SpaceBefore = after + 4; h2.ParagraphFormat.SpaceAfter = 2
        h1.Font.Size = hsize + 4; h1.ParagraphFormat.SpaceBefore = 0
        doc.Repaginate()
        pages = int(doc.ComputeStatistics(2))
        if pages <= target:
            best = pages
            break
    return best or int(doc.ComputeStatistics(2))


def make_document(topic: str, details: str = "") -> Result:
    """Документ Word по теме: ищет материал, пишет текст — абзацы появляются на глазах у брата."""
    from . import web
    topic = topic.strip()
    if not topic or _llm is None:
        return Result("Не понял, про что писать документ.", ok=False)
    global last_document
    topic = re.sub(r"^(конспект|реферат|доклад|статья|сочинение|документ)\w*\s+(про|о|об|на\s+тему|по\s+теме|по)\s+",
                   "", topic.strip(), flags=re.I) or topic
    full_topic = normalize_topic(topic)
    material = web.research(full_topic) if config.WEB_ENABLED else ""
    pm = re.search(r"(\d+)\s+полн\w*\s+страниц", details)
    target_pages = int(pm.group(1)) if pm else (1 if re.search(r"одна\s+ПОЛНАЯ\s+страница", details, re.I) else 0)
    if not pm and target_pages == 0 and re.search(r"а4|a4|страниц", details, re.I):
        target_pages = 1
    hint = f"\nМатериалы из интернета (опирайся на них, факты не выдумывай):\n{material[:3000]}\n" if material else ""
    m = re.search(r"примерно\s+(\d+)", details)
    words = int(m.group(1)) if m else 450
    n_heads = f"{2 + 2 * max(1, target_pages)}–{3 + 3 * max(1, target_pages)}" if target_pages else "3–8"
    text = _llm(f"Напиши по-русски документ на тему «{full_topic}» (брат назвал её «{topic}»). {details}{hint}\nСтруктура: первая строка — заголовок, "
                f"дальше связный текст, разбитый на {n_heads} разделов с подзаголовками (подзаголовок — отдельная "
                "короткая строка без точки в конце; под каждым — абзац из нескольких предложений, не одна строчка). "
                f"Пиши содержательно, с конкретными фактами. Общий объём — около {words} слов, это важно. "
                "Без markdown, без звёздочек, решёток и маркеров списка.", min(words * 4 + 400, 4400))
    paras = _clean_paras(text)
    if not paras:
        return Result("Не получилось написать текст.", ok=False)
    # «С фото / с картинками» — рисуем иллюстрации ДО того, как открыть Word (на время рисования мозг уходит с видеокарты)
    pics = []
    if re.search(r"иллюстрац|фото|картинк|изображен", details, re.I):
        try:
            from . import media
            pics = media.illustrations(full_topic, 2)
        except Exception:
            pics = []
    import comtypes
    import comtypes.client
    comtypes.CoInitialize()
    try:
        app = comtypes.client.GetActiveObject("Word.Application")
    except Exception:
        app = comtypes.client.CreateObject("Word.Application")
    app.Visible = True
    doc = app.Documents.Add()
    try:
        app.WindowState = 1              # развернуть окно
        _bring_to_front(int(app.ActiveWindow.Hwnd))
    except Exception:
        pass
    sel = app.Selection

    def type_all(paras, delay):
        for i, p in enumerate(paras):
            is_title = i == 0
            is_sub = not is_title and len(p) < 70 and not p.endswith((".", "!", "?", ":"))
            sel.Style = doc.Styles(-2 if is_title else (-3 if is_sub else -1))   # Заголовок 1 / 2 / Обычный
            # печатаем кусками — видно, как текст появляется
            for chunk in re.findall(r".{1,60}(?:\s|$)", p) or [p]:
                sel.TypeText(chunk)
                time.sleep(delay)
            sel.TypeParagraph()
        insert_pics()

    def insert_pics():
        """Вставить картинки: после первого раздела и в середине текста, по центру, с подписью-отступом."""
        if not pics:
            return
        n = doc.Paragraphs.Count
        spots = sorted({min(n, 3), max(3, n // 2 + 1)}, reverse=True)[:len(pics)]
        width = 200 if target_pages == 1 else 340          # в пунктах (1 см ≈ 28 пт)
        for spot, pic in zip(spots, pics):
            try:
                rng = doc.Paragraphs(spot).Range
                rng.InsertParagraphAfter()
                after = doc.Paragraphs(spot + 1).Range
                after.Style = doc.Styles(-1)
                shape = doc.InlineShapes.AddPicture(str(pic), False, True, after)
                ratio = shape.Height / shape.Width
                shape.Width, shape.Height = width, width * ratio
                after.ParagraphFormat.Alignment = 1          # по центру
            except Exception:
                pass

    type_all(paras, 0.03)
    try:
        pages = _fit_pages(doc, target_pages) if target_pages else int(doc.ComputeStatistics(2))
        # Не влезло даже мелким шрифтом — просим мозг сократить и переписываем (до 2 раз)
        for _ in range(2):
            if not target_pages or pages <= target_pages:
                break
            ratio = _page_fill(doc, target_pages)
            n_now = len(" ".join(paras).split())
            shorter = _clean_paras(_llm(
                f"Сократи этот текст примерно до {int(n_now * ratio * 0.95)} слов, сохранив заголовок, главные факты "
                "и подзаголовки (можно убрать или объединить мелкие разделы). Верни только текст в том же формате: "
                f"первая строка — заголовок, подзаголовки отдельными строками.\n\n" + "\n".join(paras), 3000))
            if not shorter:
                break
            paras = shorter
            doc.Content.Delete()
            type_all(paras, 0.005)
            pages = _fit_pages(doc, target_pages)
    except Exception:
        pages = 0
    title = re.sub(r",\s*компьютерная игра.*|\s*\(.*?\)", "", full_topic).strip() or topic
    path = _unique(DOCS_DIR / f"{_safe_filename(paras[0][:60] if len(paras[0]) < 80 else title)}.docx")
    doc.SaveAs2(str(path))
    _release(sel, doc, app)
    last_document = path
    remember_file(path)
    n_words = len(" ".join(paras).split())
    return Result(f"Написал документ «{topic}» в Word: {n_words} слов{f', {len(pics)} картинки' if pics else ''}"
                  f"{f', {pages} стр.' if pages else ''}"
                  f"{' по материалам из интернета' if material else ''}. Сохранил в Документы как {path.name}.")


# ---------- таблица дел ----------
# имя → (функция, описание для мозга, опасное ли)
ACTIONS = {   # описания короткие: они идут в инструкцию мозгу, а её размер ограничен
    "открыть": (open_thing, "открыть | <ЛЮБАЯ программа или сайт>"),
    "закрыть": (close_app, "закрыть | <программа>"),
    "создать": (create_document, "создать | word/excel/блокнот | <название> | <текст внутри>  — новый документ"),
    "напечатать": (type_text, "напечатать | <текст>  — вписать туда, где курсор"),
    "скопировать": (copy_text, "скопировать | <текст>  — в буфер обмена"),
    "поиск": (web_search, "поиск | <запрос>  — поиск в браузере (брат сам посмотрит)"),
    "включить": (play_video, "включить | <видео/песня/клип/мультик>  — сразу включить на ютубе"),
    "ютуб": (youtube_search, "ютуб | <запрос>  — список видео на ютубе, выбрать самому"),
    "громкость": (volume, "громкость | громче/тише/+20/-20/50/выкл/вкл/сколько"),
    "музыка": (media, "музыка | пауза/играй/следующий/предыдущий"),
    "система": (system_info, "система  — загрузка, температура, место на диске"),
    "питание": (power, "питание | выключить/перезагрузить/сон/заблокировать"),
    "отмена выключения": (cancel_shutdown, "отмена выключения"),
    "заметка": (add_note, "заметка | <текст>"),
    "заметки": (read_notes, "заметки  — прочитать заметки"),
    "открыть заметки": (open_notes, "открыть заметки"),
    "напомнить": (remind, "напомнить | <через 10 мин / в 18:30 / завтра в 7 утра> | <о чём>  — и будильник, и таймер"),
    "найти файл": (find_file, "найти файл | <название>"),
    "удалить": (delete_file, "удалить | <этот документ / последнее фото / имя файла>  — в корзину, переспросит"),
    "скриншот": (screenshot, "скриншот"),
    "окно": (window_action, "окно | свернуть/развернуть/закрыть/свернуть все/переключить | <какое: чёрное, "
                            "телеграм, ворд… или пусто = активное>"),
    "окна": (windows_info, "окна  — что сейчас открыто на экране"),
    "экран": (screen_look, "экран | <вопрос: что на экране / что за ошибка / прочитай текст>  — посмотреть на экран"),
    "нажать": (press_click, "нажать | <кнопка или пункт на экране>  — кликнуть мышкой"),
    "клавиши": (press_keys, "клавиши | <ctrl+s / alt+f4 / enter / esc>"),
    "команда": (ps_command, "команда | <одна команда PowerShell>  — файлы, папки, установка (winget); спросит брата"),
    "игра": (game_make, "игра | <какую игру сделать, своими словами брата>  — придумать план, написать и проверить"),
    "доработать игру": (game_improve, "доработать игру | <что изменить в последней игре>"),
    "exe": (game_exe, "exe | <какую игру упаковать в .exe; пусто = последнюю>"),
    "зайти": (go_to, "зайти | <куда: настройки кс / звука / экрана, загрузки стима…>  — сразу, без кликов"),
    "задача": (agent_task,"задача | <цель>  — сложное дело в много шагов, где надо смотреть на экран и пробовать"),
    "папка": (open_folder, "папка | загрузки/документы/рабочий стол/картинки/видео/музыка/скриншоты"),
    "выделенное": (selection_action, "выделенное | прочитать/перевести на …/пересказать/объяснить/исправить ошибки"),
    "буфер": (clipboard_info, "буфер  — что в буфере обмена"),
    "узнать": (research, "узнать | <вопрос>  — тихо найти в интернете и рассказать"),
    "погода": (weather_now, "погода | <город или пусто>"),
    "прочитать": (read_aloud, "прочитать | выделенное/страницу  — вслух"),
    "фото": (_photo, "фото | <что: убрать/заменить/размыть фон, обрезать, в квадрат, повернуть, уменьшить, сжать, "
                     "ч/б, ярче, в png, в стиле …> | <файл, обычно пусто>"),
    "видео": (_video, "видео | <что: обрезать с 10 по 30 секунду, убрать звук, вытащить звук в mp3, сжать, гиф, "
                      "кадр, повернуть, ускорить, заменить фон> | <файл, обычно пусто>"),
    "заряд": (battery_info, "заряд  — мышка, наушники, клавиатура"),
    "сводка": (briefing_info, "сводка  — погода, напоминания, заряд"),
    "вспомнить": (recall, "вспомнить | <что вспомнить: вчера, 15 ноября, что говорил Enbulih про …>"),
    "таблица": (make_table, "таблица | <тема> | <столбцы>  — собрать в Excel по данным из интернета"),
    "документ": (make_document, "документ | <тема> | <пожелания>  — написать в Word (реферат, статья…)"),
}

ACTION_RE = re.compile(r"\[\s*ДЕЛО\s*:\s*(.+?)\s*\]", re.IGNORECASE | re.DOTALL)


# Мозг иногда пишет дело повелительной формой («узнай», «открой») — понимаем точно, без угадывания
ACTION_FORMS = {"узнай": "узнать", "открой": "открыть", "закрой": "закрыть", "включи": "включить",
                "напомни": "напомнить", "удали": "удалить", "нажми": "нажать", "напечатай": "напечатать",
                "создай": "создать", "скопируй": "скопировать", "прочитай": "прочитать", "найди файл": "найти файл",
                "нарисовать": "нарисуй", "посмотреть": "экран", "посмотри": "экран", "клавиша": "клавиши",
                "кнопка": "нажать", "задачу": "задача", "выполнить": "команда", "запомнить": "заметка",
                "зайди": "зайти", "перейти": "зайти", "перейди": "зайти"}


def parse_action(tag_body: str) -> tuple[str, list[str]]:
    parts = [p.strip() for p in tag_body.split("|")]
    args = parts[1:]
    while args and not args[-1]:  # пустые хвосты убираем, а пустые места в середине оставляем
        args.pop()
    name = parts[0].lower()
    return ACTION_FORMS.get(name, name), args


def run(name: str, args: list[str], confirmed: bool = False) -> Result:
    """Выполнить дело. Никогда не падает — ошибки возвращаются текстом."""
    entry = ACTIONS.get(name)
    if entry is None:
        # опечатка в названии дела («открой» вместо «открыть») — только очень похожее и с тем же началом
        # (раньше «удалить» превращалось в «узнать»!)
        close = difflib.get_close_matches(name, ACTIONS.keys(), n=1, cutoff=0.8)
        entry = ACTIONS.get(close[0]) if close and close[0][:3] == name[:3] else None
    if entry is None:
        return Result(f"Такого я не умею: {name}.", ok=False)
    fn = entry[0]
    try:
        if fn is power:
            return fn(*(args[:1] or [""]), confirmed=confirmed)
        if fn is delete_file:
            return fn(" ".join(args), confirmed=confirmed)
        if fn is ps_command:
            return fn(" | ".join(args), confirmed=confirmed)      # в команде PowerShell бывает «|» — склеиваем обратно
        if fn is create_document:
            return fn(*(args[:3] or ["word"]))
        if fn in (make_table, make_document):
            return fn(*args[:2]) if args else Result("Не понял тему.", ok=False)
        if fn is weather_now:
            return fn(" ".join(args))
        if fn is window_action:
            return fn(args[0] if args else "", " ".join(args[1:]))
        if fn in (_photo, _video):
            return fn(args[0] if args else "", " ".join(args[1:]))
        # «поиск»/«ютуб» без запроса — просто открыть сайт
        if fn is youtube_search and not any(args):
            return open_thing("ютуб")
        if fn is web_search and not any(args):
            return open_thing("гугл")
        if fn is remind:
            if len(args) < 2:
                return Result("Не понял, через сколько и о чём напомнить.", ok=False)
            return fn(args[0], " ".join(args[1:]))
        return fn(" ".join(args)) if args or fn in (system_info, read_notes, open_notes, cancel_shutdown,
                                                    screenshot, clipboard_info, battery_info, briefing_info,
                                                    read_aloud, windows_info, screen_look) \
            else Result("Не хватает подробностей.", ok=False)
    except Exception as e:
        return Result(f"Не вышло: {e}", ok=False)


YES_RE = re.compile(r"\b(да|давай|ага|угу|конечно|точно|ок|окей|вырубай|выключай|закрывай|перезагружай|"
                    r"подтверждаю|го|делай|можно|естественно|разумеется)\b", re.IGNORECASE)
NO_RE = re.compile(r"\b(нет|не надо|не|отмена|отбой|стоп|погоди|подожди)\b", re.IGNORECASE)


def is_yes(answer: str) -> bool:
    return bool(YES_RE.search(answer)) and not NO_RE.search(answer)


def execute_all(actions: list[tuple[str, list[str]]], confirm_fn) -> tuple[str, bool, list[str]]:
    """Выполняет все дела из ответа мозга.
    confirm_fn(вопрос) -> bool — как спросить брата «точно?» (голосом или в чате).
    Возвращает: (сводка результатов для мозга, нужно ли мозгу пересказать, строки для экрана)."""
    global _current_confirm
    _current_confirm = confirm_fn
    summary, lines, need_report = [], [], False
    to_read.clear()
    last_failed.clear()
    seen = set()
    for i, (name, args) in enumerate(actions[:8]):
        key = (name, tuple(args))
        if key in seen:          # одно и то же дело дважды в одном ответе — не повторяем
            continue
        seen.add(key)
        prev = actions[i - 1][0] if i else ""
        if name == "exe" and prev in ("игра", "доработать игру") and last_failed:
            lines.append("(дело: exe → пропустил: игра не получилась)")
            continue             # игра не вышла — не пакуем вместо неё старую
        if prev in ("открыть", "создать", "папка") and name in ("напечатать", "клавиши", "нажать"):
            time.sleep(2.0)      # только что открыли программу — даём ей появиться, прежде чем печатать или нажимать
        elif i and name in ("открыть", "включить", "папка", "закрыть"):
            time.sleep(0.7)      # несколько шагов подряд — даём прошлой программе открыться
        res = run(name, args)
        if res.confirm:
            if confirm_fn(res.confirm):
                res = run(name, args, confirmed=True)
            else:
                res = Result("Брат передумал, отбой.", ok=False)
                res.cancelled = True
        shown = res.text if len(res.text) < 160 else res.text[:150].replace("\n", " ") + "…"
        lines.append(f"(дело: {name} {' | '.join(args)} → {shown})")
        summary.append(f"{name} {' | '.join(args)}: {res.text}")
        if not res.ok and not getattr(res, "cancelled", False):
            last_failed.append((name, args, res.text))
        if res.info or not res.ok:
            need_report = True
    return "; ".join(summary), need_report, lines


last_failed: list = []   # дела из последнего execute_all, которые не вышли (для поиска обходного пути)


def actions_help() -> str:
    """Список дел для инструкции мозгу."""
    return "\n".join(f"  [ДЕЛО: {desc}" .replace("  —", "]  —", 1) if "  —" in desc else f"  [ДЕЛО: {desc}]"
                     for _fn, desc in ACTIONS.values())
