"""
БЫСТРЫЕ ПУТИ: «зайди в настройки кс», «открой настройки звука», «зайди в загрузки стима».

Вместо того чтобы кликать «глазами» по 10 шагов, открываем нужное место напрямую:
  • Steam — по ссылкам steam:// (свойства любой установленной игры, настройки, загрузки, друзья, библиотека…);
  • Параметры Windows — по ссылкам ms-settings: (звук, экран, блютуз, обновления…);
  • классические окна Windows — диспетчер устройств, панель управления, службы и т.п.
Если путь не найден — goto() возвращает None, и Мартышка делает по-старому (агент с глазами).
"""
import os
import re
import time
from pathlib import Path

# ---------- Steam ----------
_steam_games: dict | None = None
_steam_time = 0.0
_names: dict = {}           # appid → название как в Steam

STEAM_GAME_ALIASES = {      # как брат называет игру → как она называется в Steam (часть названия)
    "кс": "counter-strike 2", "кс2": "counter-strike 2", "кс 2": "counter-strike 2", "кска": "counter-strike 2",
    "контра": "counter-strike 2", "контр страйк": "counter-strike 2", "cs": "counter-strike 2",
    "cs2": "counter-strike 2", "кси": "counter-strike 2",
    "дота": "dota 2", "доту": "dota 2", "доты": "dota 2", "ets": "euro truck simulator 2", "етс 2": "euro truck simulator 2",
    "ets 2": "euro truck simulator 2", "ets2": "euro truck simulator 2", "дота 2": "dota 2", "пубг": "pubg", "пабг": "pubg",
    "раст": "rust", "апекс": "apex legends", "гта": "grand theft auto v", "гта 5": "grand theft auto v",
    "етс": "euro truck simulator 2", "евро трак": "euro truck simulator 2", "дальнобойщики": "euro truck simulator 2",
    "нфс": "need for speed", "нид фор спид": "need for speed", "репо": "r.e.p.o", "пуммел": "pummel party",
    "э вэй аут": "a way out", "вэй аут": "a way out",
}
KNOWN_APPIDS = {"counter-strike 2": 730, "dota 2": 570, "pubg": 578080, "rust": 252490, "apex legends": 1172470,
                "grand theft auto v": 271590, "euro truck simulator 2": 227300}


def steam_games() -> dict:
    """Установленные игры Steam: название (маленькими буквами) → appid. Читаем файлы Steam, раз в 10 минут."""
    global _steam_games, _steam_time
    if _steam_games is not None and time.time() - _steam_time < 600:
        return _steam_games
    games = {}
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as k:
            root = Path(winreg.QueryValueEx(k, "SteamPath")[0])
        libs = {root}
        vdf = root / "steamapps" / "libraryfolders.vdf"
        if vdf.exists():
            for p in re.findall(r'"path"\s+"([^"]+)"', vdf.read_text(encoding="utf-8", errors="replace")):
                libs.add(Path(p.replace("\\\\", "\\")))
        for lib in libs:
            for acf in (lib / "steamapps").glob("appmanifest_*.acf"):
                t = acf.read_text(encoding="utf-8", errors="replace")
                a, n = re.search(r'"appid"\s+"(\d+)"', t), re.search(r'"name"\s+"([^"]+)"', t)
                if a and n:
                    pretty = re.sub(r"[™®©]", "", n.group(1)).strip()
                    games[pretty.lower()] = int(a.group(1))
                    _names[int(a.group(1))] = pretty
    except Exception:
        pass
    _steam_games, _steam_time = games, time.time()
    return games


def _pretty(aid: int, fallback: str) -> str:
    """Название игры как в Steam: 730 → «Counter-Strike 2»."""
    steam_games()
    return _names.get(aid) or fallback.title()


def _norm(s: str) -> str:
    return re.sub(r"[^a-zа-яё0-9 .]+", " ", s.lower().replace("ё", "е")).strip()


def find_steam_game(name: str):
    """«кс» → (730, 'counter-strike 2'); None — если такой игры в Steam нет."""
    n = _norm(name)
    n = re.sub(r"^(игр[ыуае]|в|во)\s+", "", n).strip()
    if not n:
        return None
    n = _norm(STEAM_GAME_ALIASES.get(n, n))
    games = {_norm(g): aid for g, aid in steam_games().items()}
    if n in games:
        return games[n], n
    for g, aid in games.items():
        if g.startswith(n) or (len(n) >= 4 and n in g):
            return aid, g
    words = [w for w in n.split() if len(w) >= 4]
    for g, aid in games.items():
        if words and all(w in g for w in words):
            return aid, g
    known = {_norm(k): v for k, v in KNOWN_APPIDS.items()}
    if n in known:
        return known[n], n
    return None


STEAM_PLACES = [   # (слова, ссылка, как сказать)
    (r"настройк\w*|параметр\w*", "steam://open/settings", "настройки Steam"),
    (r"загрузк\w*|скачиван\w*|обновлени\w*", "steam://open/downloads", "загрузки Steam"),
    (r"друз\w*|друг\w*|чат\w*", "steam://open/friends", "друзей в Steam"),
    (r"библиотек\w*|игр[ыу]?$", "steam://open/games", "библиотеку Steam"),
    (r"магазин\w*|стор\w*", "steam://store", "магазин Steam"),
    (r"скриншот\w*", "steam://open/screenshots", "скриншоты Steam"),
    (r"консол\w*", "steam://open/console", "консоль Steam"),
]

# ---------- Windows ----------
WIN_SETTINGS = [   # (слова, ms-settings:…, как сказать)
    (r"звук\w*|громкост\w*|аудио|динамик\w*|наушник\w*|микрофон\w*", "ms-settings:sound", "настройки звука"),
    (r"экран\w*|дисплей\w*|монитор\w*|разрешени\w*|масштаб\w*|герц\w*|частот\w* обновлен\w*", "ms-settings:display",
     "настройки экрана"),
    (r"блют\w*|bluetooth|устройств\w*", "ms-settings:bluetooth", "Bluetooth и устройства"),
    (r"wi.?fi|вай.?фай\w*|вайфай\w*", "ms-settings:network-wifi", "настройки Wi‑Fi"),
    (r"сет[ьи]\w*|интернет\w*", "ms-settings:network", "настройки сети"),
    (r"vpn|впн", "ms-settings:network-vpn", "настройки VPN"),
    (r"обновлени\w*\s+(windows|винд\w*)|(windows|винд\w*)\s+обновлени\w*|центр\w* обновлени\w*", "ms-settings:windowsupdate",
     "Центр обновления Windows"),
    (r"автозагрузк\w*|автозапуск\w*", "ms-settings:startupapps", "автозагрузку"),
    (r"приложени\w*|программ\w*", "ms-settings:appsfeatures", "установленные приложения"),
    (r"мыш\w*|курсор\w*", "ms-settings:mousetouchpad", "настройки мыши"),
    (r"клавиатур\w*|ввод\w*", "ms-settings:typing", "настройки ввода"),
    (r"язык\w*|раскладк\w*", "ms-settings:regionlanguage", "язык и регион"),
    (r"врем\w*|дат\w*|час\w* пояс\w*", "ms-settings:dateandtime", "дату и время"),
    (r"обо[ий]\w*|фон\w*", "ms-settings:personalization-background", "фон рабочего стола"),
    (r"тем\w*|цвет\w*|тёмн\w*|темн\w*", "ms-settings:colors", "цвета и тему"),
    (r"ночн\w*", "ms-settings:nightlight", "ночной свет"),
    (r"питани\w*|батаре\w*|сон|спящ\w*|энерг\w*", "ms-settings:powersleep", "питание и сон"),
    (r"уведомлени\w*", "ms-settings:notifications", "уведомления"),
    (r"принтер\w*|сканер\w*", "ms-settings:printers", "принтеры и сканеры"),
    (r"памят\w*|хранилищ\w*|диск(а|и|ов|ам)?|мест\w*", "ms-settings:storagesense", "память устройства"),
    (r"игров\w* режим\w*|режим\w* игр\w*", "ms-settings:gaming-gamemode", "игровой режим"),
    (r"game ?bar|гейм ?бар\w*|xbox", "ms-settings:gaming-gamebar", "Xbox Game Bar"),
    (r"графи\w*|видеокарт\w*", "ms-settings:display-advancedgraphics", "настройки графики"),
    (r"конфиденциальн\w*|приватн\w*|безопасн\w*", "ms-settings:privacyandsecurity", "конфиденциальность и защиту"),
    (r"учетн\w*|учётн\w*|аккаунт\w*", "ms-settings:yourinfo", "учётную запись"),
    (r"о систем\w*|характеристик\w*|о компьютер\w*|систем\w*", "ms-settings:about", "сведения о системе"),
    (r"специальн\w* возможност\w*", "ms-settings:easeofaccess", "специальные возможности"),
]
WIN_TOOLS = [      # классические окна Windows
    (r"диспетчер\w* устройств", "devmgmt.msc", "диспетчер устройств"),
    (r"панел\w* управлени\w*", "control", "панель управления"),
    (r"управлени\w* диск\w*", "diskmgmt.msc", "управление дисками"),
    (r"служб\w*", "services.msc", "службы"),
    (r"сетев\w* подключени\w*|адаптер\w*", "ncpa.cpl", "сетевые подключения"),
    (r"звуков\w* устройств\w*|устройств\w* воспроизведени\w*|запис\w* звук\w*", "mmsys.cpl", "звуковые устройства"),
    (r"программ\w* и компонент\w*|удалени\w* программ\w*", "appwiz.cpl", "программы и компоненты"),
    (r"планировщик\w*", "taskschd.msc", "планировщик заданий"),
    (r"просмотр\w* событий", "eventvwr.msc", "просмотр событий"),
]

SETTINGS_WORDS = r"(настройк\w*|параметр\w*|свойств\w*|опци\w*)"
GO_VERB = r"(зайди|зайти|зайдём|зайдем|заходи|заходим|зайду|перейди|перейти|открой|открыть|покажи|включи|вруби|запусти|залезь|сади|садим)"


_dry = False      # True — только узнать, есть ли быстрый путь, ничего не открывая


def _open(target: str) -> None:
    if not _dry:
        os.startfile(target)


def can_go(text: str) -> bool:
    """Есть ли быстрый путь для этой фразы (ничего не открывает)."""
    global _dry
    _dry = True
    try:
        return goto(text) is not None
    except Exception:
        return False
    finally:
        _dry = False


def _front_soon(title_words: tuple, wait: float = 3.0) -> None:
    """Подождать окно и вывести его вперёд (Steam иногда открывает окно за другими)."""
    if _dry:
        return
    try:
        from .hands import list_windows, _bring_to_front
        end = time.time() + wait
        while time.time() < end:
            for w in list_windows(with_dark=False):
                t = w["title"].lower()
                if any(x in t for x in title_words):
                    _bring_to_front(int(w["hwnd"]))
                    return
            time.sleep(0.3)
    except Exception:
        pass


def goto(text: str):
    """«зайди в настройки кс» → открывает и возвращает фразу «Открыл …». None — быстрого пути нет."""
    t = re.sub(r"\.+(\s|$)", r"\1", _norm(text)).strip()      # точки в конце фраз (а «r.e.p.o» остаётся)
    t = re.sub(rf"^(мартышка\s+)?(можешь\s+|пожалуйста\s+|давай\s+)*(?:{GO_VERB}\b\s*)?(мне\s+)?(?:(в|во|на)\s+)?", "", t).strip()
    if not t or len(t) > 70 or re.search(r"\s(и|потом|затем|а потом|после)\s", t):
        return None          # несколько дел подряд — пусть мозг разложит по шагам
    steam_word = re.search(r"\b(стим\w*|steam)\b", t)
    # 1) настройки/свойства/параметры запуска игры из Steam: «настройки кс», «параметры запуска доты», «свойства кс в стиме»
    m = re.match(rf"^{SETTINGS_WORDS}(\s+запуск\w*)?\s+(игр\w*\s+)?(.+?)(\s+(в|через)\s+(стим\w*|steam))?$", t)
    if m:
        game = find_steam_game(m.group(4))
        if game and not re.fullmatch(r"(стим\w*|steam)", m.group(4)):
            aid, name = game
            _open(f"steam://gameproperties/{aid}")
            name = _pretty(aid, name)
            _front_soon(("свойства", "properties", name.split()[0].lower()))
            return (f"Открыл свойства {name} в Steam: там параметры запуска, обновления, файлы игры. "
                    f"Настройки графики и управления — внутри самой игры.")
    # 2) «страница кс в стиме», «открой кс в стиме» (без слова «запусти»)
    m = re.match(r"^(страниц\w*\s+)?(.+?)\s+(в|через)\s+(стим\w*|steam)$", t)
    if m and not re.search(SETTINGS_WORDS, t):
        game = find_steam_game(m.group(2))
        if game:
            aid, name = game
            _open(f"steam://nav/games/details/{aid}")
            _front_soon(("steam",))
            return f"Открыл страницу {_pretty(aid, name)} в библиотеке Steam."
    # 3) места в самом Steam: «настройки стима», «загрузки в стиме», «друзья стим»
    if steam_word:
        rest = re.sub(r"\b(стим\w*|steam|в|во|на|мои|мой)\b", " ", t).strip()
        for words, link, say in STEAM_PLACES:
            if re.search(rf"\b({words})", rest):
                _open(link)
                _front_soon(("steam", "настройки", "settings"))
                return f"Открыл {say}."
    # 4) настройки Windows: «настройки звука», «параметры экрана», «зайди в блютуз»
    has_settings = re.search(SETTINGS_WORDS, t)
    for words, cmd, say in WIN_TOOLS:
        if re.search(rf"\b({words})", t):
            _open(cmd)
            return f"Открыл {say}."
    if has_settings or re.match(r"^(блют\w*|bluetooth|wi.?fi|вай.?фай\w*|автозагрузк\w*|центр\w* обновлени\w*)", t):
        rest = re.sub(SETTINGS_WORDS, " ", t)
        rest = re.sub(r"\b(windows|виндовс\w*|винды|компа|компьютера|пк|системы)\b$", " ", rest).strip()
        if not rest:
            if re.search(r"\b(windows|виндовс\w*|винд\w*|компа|компьютера|пк)\b", t) or t in ("настройки", "параметры"):
                _open("ms-settings:")
                return "Открыл Параметры Windows."
            return None
        for words, uri, say in WIN_SETTINGS:
            if re.match(rf"^\s*({words})\b", rest) and len(rest.split()) <= 4:
                _open(uri)
                return f"Открыл {say}."
    return None


GOTO_RE = re.compile(rf"\b{GO_VERB}\b.{{0,25}}\b({SETTINGS_WORDS[1:-1]}|загрузк\w*|друз\w*|библиотек\w*|блют\w*|bluetooth|"
                     r"wi.?fi|вай.?фай\w*|автозагрузк\w*|диспетчер\w* устройств|панел\w* управлени\w*|служб\w*)"
                     rf"|^\W*(мартышка\W+)?{SETTINGS_WORDS}\b", re.IGNORECASE)
