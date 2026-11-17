"""
ИНТЕРНЕТ МАРТЫШКИ — всё тихо, в фоне, без браузера на экране.

  research(вопрос)  — ищет в DuckDuckGo, читает 2–3 лучшие страницы, отдаёт текст мозгу для пересказа
  weather(город)    — погода через бесплатный Open-Meteo (без ключей и регистрации)
  find_table(тема)  — ищет готовую таблицу (обычно в Википедии) и возвращает её строками
"""
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor

from . import config

# Честно представляемся программой (сайты, которые не пускают программы, просто пропускаем)
UA = {"User-Agent": "MartyshkaAssistant/1.0 (personal voice assistant)"}
COORDS_FILE = config.DATA_DIR / "погода_координаты.json"


def _region(query: str) -> str:
    """Русский запрос — ищем по-русски, иностранный — по всему миру."""
    return "ru-ru" if re.search(r"[а-яё]", query, re.I) else "wt-wt"


def search(query: str, n: int = 6) -> list[dict]:
    from ddgs import DDGS
    try:
        return list(DDGS(timeout=8).text(query, region=_region(query), max_results=n))
    except Exception:
        return []


def fetch_text(url: str, limit: int = 2500) -> str:
    """Главный текст страницы (без меню, рекламы и прочего мусора)."""
    import trafilatura
    try:
        html = trafilatura.fetch_url(url)
        text = trafilatura.extract(html, include_comments=False, include_tables=False) if html else ""
        return (text or "")[:limit]
    except Exception:
        return ""


def research(question: str) -> str:
    """Найти ответ в интернете. Возвращает материал (выдержки), который мозг перескажет."""
    results = search(question, 6)
    if not results:
        return ""
    parts = [f"- {r.get('title', '')}: {r.get('body', '')}" for r in results[:5]]
    # Читаем 2 лучшие страницы целиком (параллельно — так быстрее)
    urls = [r["href"] for r in results[:3] if r.get("href")][:2]
    with ThreadPoolExecutor(2) as ex:
        texts = list(ex.map(lambda u: fetch_text(u, 1300), urls))
    for u, t in zip(urls, texts):
        if t:
            parts.append(f"\nСо страницы {u.split('/')[2]}:\n{t}")
    return "\n".join(parts)[:3500]   # больше не влезет в память мозга


# ---------- видео ----------
def find_video(query: str) -> tuple[str, str] | None:
    """Найти конкретное видео на ютубе. Возвращает (ссылка, название)."""
    from ddgs import DDGS
    yt = re.compile(r"(youtube\.com/watch\?v=|youtu\.be/)", re.I)
    try:
        for v in DDGS(timeout=8).videos(query, region=_region(query), max_results=10):
            url = v.get("content", "")
            if yt.search(url):
                return url, v.get("title", query)
    except Exception:
        pass
    for r in search(f"{query} site:youtube.com", 8):   # запасной путь — обычный поиск
        if yt.search(r.get("href", "")):
            title = re.split(r"\s+-\s+YouTube", r.get("title", query))[0]
            return r["href"], title[:90]
    return None


# ---------- погода ----------
WMO = {0: "ясно", 1: "в основном ясно", 2: "переменная облачность", 3: "пасмурно", 45: "туман", 48: "изморозь",
       51: "лёгкая морось", 53: "морось", 55: "сильная морось", 61: "небольшой дождь", 63: "дождь",
       65: "сильный дождь", 66: "ледяной дождь", 67: "сильный ледяной дождь", 71: "небольшой снег", 73: "снег",
       75: "сильный снег", 77: "снежная крупа", 80: "ливень", 81: "ливни", 82: "сильные ливни",
       85: "снегопад", 86: "сильный снегопад", 95: "гроза", 96: "гроза с градом", 99: "сильная гроза с градом"}


def _geocode(city: str) -> tuple[float, float, str] | None:
    import httpx
    cache = {}
    try:
        cache = json.loads(COORDS_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    key = city.lower().strip()
    if key in cache:
        return tuple(cache[key])
    # Open-Meteo ищет по названию города; почтовый индекс и страну отрезаем, но используем для выбора
    name = re.sub(r"\d{4,6}", "", city)
    country = "DE" if re.search(r"german|deutsch|германи", city, re.I) else None
    name = re.sub(r"(?i),?\s*(germany|deutschland|германия)", "", name).strip(" ,")
    name = re.sub(r"\s*\((\w+)\)", r" \1", name).strip()   # «gronau(leine)» → «gronau leine»
    first = name.split()[0] if name.split() else name
    # «в Берлине» → «Берлин», «в Москве» → «Москва», «в Гронау» → как есть
    variants = [name, first]
    if re.search(r"[а-яё]$", first, re.I):
        stem = first[:-1]
        variants += [stem, stem + "а", stem + "ь", stem + "я", first[:-2] + "ь"]
    extra = [w.lower() for w in name.split()[1:]]
    exact, fallback = [], None
    for q in dict.fromkeys(v for v in variants if v):
        r = httpx.get("https://geocoding-api.open-meteo.com/v1/search",
                      params={"name": q, "count": 10, "language": "ru"}, timeout=8).json()
        hits = r.get("results") or []
        if country:
            hits = [h for h in hits if h.get("country_code") == country] or hits
        exact += [h for h in hits if h.get("name", "").lower().replace("ё", "е") == q.lower().replace("ё", "е")]
        if hits and fallback is None and q == name:
            fallback = hits[0]
    # Точное совпадение названия; среди одноимённых — уточнение из запроса («Leine»), потом самый крупный
    if exact:
        exact.sort(key=lambda h: (-sum(w in json.dumps(h, ensure_ascii=False).lower() for w in extra),
                                  -(h.get("population") or 0)))
        fallback = exact[0]
    if fallback is None:
        return None
    res = (fallback["latitude"], fallback["longitude"], fallback.get("name", city))
    cache[key] = res
    COORDS_FILE.parent.mkdir(parents=True, exist_ok=True)
    COORDS_FILE.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    return res


def weather(city: str = "") -> str:
    import httpx
    city = city.strip() or config.WEATHER_CITY
    geo = _geocode(city)
    if not geo:
        return f"Не нашёл город «{city}»."
    lat, lon, name = geo
    r = httpx.get("https://api.open-meteo.com/v1/forecast", timeout=8, params={
        "latitude": lat, "longitude": lon, "timezone": "auto", "forecast_days": 3,
        "current": "temperature_2m,apparent_temperature,weather_code,wind_speed_10m,precipitation",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
    }).json()
    c, d = r["current"], r["daily"]
    lines = [f"Погода в {name}: сейчас {round(c['temperature_2m'])}°, ощущается как {round(c['apparent_temperature'])}°, "
             f"{WMO.get(c['weather_code'], 'без осадков')}, ветер {round(c['wind_speed_10m'])} км/ч."]
    for i, label in enumerate(["Сегодня", "Завтра", "Послезавтра"]):
        lines.append(f"{label}: от {round(d['temperature_2m_min'][i])}° до {round(d['temperature_2m_max'][i])}°, "
                     f"{WMO.get(d['weather_code'][i], '')}, вероятность осадков {d['precipitation_probability_max'][i]}%.")
    return " ".join(lines)


# ---------- таблицы ----------
def find_table(topic: str) -> tuple[list[str], list[list[str]], str] | None:
    """Ищет готовую таблицу по теме (обычно в Википедии). Возвращает (заголовки, строки, источник)."""
    import io
    import httpx
    import pandas as pd
    results = search(f"{topic} таблица", 8) + search(f"{topic} список", 5)
    urls, seen = [], set()
    for r in results:
        u = r.get("href", "")
        if u and u not in seen:
            urls.append(u); seen.add(u)
    for u in urls[:6]:
        try:
            resp = httpx.get(u, headers=UA, timeout=8, follow_redirects=True)
            if resp.status_code != 200:
                continue          # сайт не пускает программы — уважаем и идём дальше
            tables = pd.read_html(io.StringIO(resp.text))
        except Exception:
            continue
        # самая «содержательная» таблица: много строк и столбцов
        best = max(tables, key=lambda t: (len(t) * min(len(t.columns), 12)), default=None)
        if best is None or len(best) < 5:
            continue
        best = best.dropna(how="all").dropna(axis=1, how="all")
        if isinstance(best.columns, pd.MultiIndex):
            best.columns = [" ".join(dict.fromkeys(str(x) for x in col if "Unnamed" not in str(x))) for col in best.columns]
        headers = [re.sub(r"\[\d+\]", "", str(c)).strip() for c in best.columns]
        rows = [[("" if pd.isna(v) else re.sub(r"\[\d+\]", "", str(v)).strip()) for v in row]
                for row in best.itertuples(index=False)]
        if all(h.isdigit() for h in headers) and rows:   # заголовки оказались первой строкой таблицы
            headers, rows = rows[0], rows[1:]
        return headers[:15], [r[:15] for r in rows[:500]], u
    return None
