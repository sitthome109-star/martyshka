"""
ГОЛОС В ОБЛАКЕ — Fish Audio.

Модель озвучки: s2.1-pro-free (бесплатная до 30.11.2026), потом s2.1-pro (платная, ~15 $ за 1 млн байт текста).
Один и тот же голос говорит на любом языке: русский, украинский, немецкий, английский и ещё ~80.
Эмоции — метки в квадратных скобках прямо в тексте: [laughing], [sad], [whisper], [angry], [sigh]…

Каталог голосов: официальные голоса Fish + популярные голоса из библиотеки (русские, украинские, немецкие,
английские). Список хранится в data/голоса_fish.json и обновляется кнопкой в настройках.
Ключ — в data/settings.json (FISH_KEY).
"""
from __future__ import annotations

import json
import threading
import time
from typing import Iterator

import httpx
import numpy as np

from . import config

API = "https://api.fish.audio"
SAMPLE_RATE = 24000
CATALOG_FILE = config.DATA_DIR / "голоса_fish.json"
LANGS = {"ru": "русский", "uk": "украинский", "de": "немецкий", "en": "английский"}

# Официальные голоса Fish Audio (сделаны с нуля, работают на всех языках)
OFFICIAL = [
    ("b871e425b6c0442baa5cf73f6c336bbe", "Артём", "ru", "male"), ("2911dfe1c9d54419b24ed8c4a1489355", "Дмитрий", "ru", "male"),
    ("de9a2c99fcee4dfbbaec90327eab36d8", "Егор", "ru", "male"), ("55ece81d65564775ab448bab3b525599", "Игорь", "ru", "male"),
    ("bf75c2e80b0e441485eeec20330a75fa", "Полина", "ru", "female"), ("911cdc7841634850ba72d88f5ed8c576", "Кира", "ru", "female"),
    ("7adab54c80d34cba8c11463ab1502c9a", "Мария", "ru", "female"), ("6d23d3209d6849e0b24ddae6f734afe9", "Екатерина", "ru", "female"),
    ("cdfaf39672e840019d9d8788fade19c4", "Остап", "uk", "male"), ("a4ef77b234a14bc0a4c34984c3877caf", "Олексій", "uk", "male"),
    ("a34ffe8852f34de59f198630ffe641e1", "Соломія", "uk", "female"),
    ("c1e7cc926aa5469aa966c353094d4b0b", "Lukas", "de", "male"), ("7b7c88bf825a4ab3bca27d55c26ddf16", "Leon", "de", "male"),
    ("164f91694aef42509dc9ba1b7f8e2c7e", "Markus", "de", "male"), ("a3f28343dfac42358e1f4fdf7562a773", "Nico", "de", "male"),
    ("44f1ec96a51e429f8b87776cbc55a997", "Jonas", "de", "male"), ("86a132379c9e497785d53a4d1e05a2c1", "Stefan", "de", "male"),
    ("3937b44b604249f18b94913daef94043", "Lena", "de", "female"), ("b5b77e8422b948efbf13be1e567a406c", "Amelie", "de", "female"),
    ("d979e92f9c8a489fa581f55f6b9e48da", "Melanie", "de", "female"), ("3235abc9a84b407d92f73539a5651720", "Emma", "de", "female"),
    ("e5fb46af69e0481fa0d2d0f209b07ae2", "Valentina", "de", "female"),
    ("bf322df2096a46f18c579d0baa36f41d", "Adrian", "en", "male"), ("536d3a5e000945adb7038665781a4aca", "Ethan", "en", "male"),
    ("79d0bd3e4e5444b18f7b6d89b5927bf1", "Jordan", "en", "male"), ("933563129e564b19a115bedd57b7406a", "Sarah", "en", "female"),
    ("b347db033a6549378b48d00acb0d06cd", "Selene", "en", "female"), ("9a9cf47702da476aa4629e2506d4a857", "Hannah", "en", "female"),
    ("e3cd384158934cc9a01029cd7d278634", "Laura", "en", "female"), ("f3a2b90078d54a65af4b95f63bc7798e", "Sheila", "en", "female"),
    ("c70cfb98867a4bfb9ae20d2401257b92", "Delia", "en", "female"),
]
DEFAULT_VOICE = "b871e425b6c0442baa5cf73f6c336bbe"   # Артём

# Модели озвучки (переключаются в настройках голоса и Говорилки отдельно)
FREE, DRAMA = "s2.1-pro-free", "drama-3"
MODELS = [(FREE, "S2.1 Pro Free — бесплатно"), ("s2.1-pro", "S2.1 Pro — 15 $ за 1 млн байт"),
          (DRAMA, "Drama 3 — самая живая подача (платно: 10 $ за 1 млн байт до 30.11.2026, потом 50 $)")]
# Голоса, на которых Fish показывает Drama 3 (с ней звучат лучше всего); работают и все остальные голоса
DRAMA_PICKS = {"bf322df2096a46f18c579d0baa36f41d", "c70cfb98867a4bfb9ae20d2401257b92",
               "f3a2b90078d54a65af4b95f63bc7798e", "536d3a5e000945adb7038665781a4aca"}

_client: httpx.Client | None = None
_lock = threading.Lock()
_free_failed = False
_drama_off_until = 0.0          # на Drama кончились кредиты — какое-то время говорим бесплатной моделью


class FishError(RuntimeError):
    pass


def key() -> str:
    return (getattr(config, "FISH_KEY", "") or "").strip()


def http() -> httpx.Client:
    global _client
    with _lock:
        if _client is None:
            _client = httpx.Client(timeout=httpx.Timeout(connect=10, read=60, write=30, pool=10),
                                   limits=httpx.Limits(max_keepalive_connections=6))
        return _client


def _headers(model: str | None = None) -> dict:
    if not key():
        raise FishError("Нет ключа Fish Audio — вставь его в настройках (раздел «Голос»).")
    h = {"Authorization": f"Bearer {key()}"}
    if model:
        h["model"] = model
    return h


def tts_model(model: str | None = None) -> str:
    """Какой моделью озвучивать. model — своя модель (например, у Говорилки), иначе из настроек голоса."""
    m = (model or getattr(config, "FISH_MODEL", "") or FREE).strip()
    if m == DRAMA and time.time() < _drama_off_until:
        m = FREE
    return "s2.1-pro" if (m == FREE and _free_failed) else m


def _explain(status: int, body: str) -> str:
    if status == 401:
        return "Ключ Fish Audio не подходит."
    if status == 402:
        return "На счёте Fish Audio закончились кредиты."
    if status == 429:
        return "Fish Audio просит подождать (много запросов)."
    return f"Fish Audio ответил ошибкой {status}: {body[:200]}"


def speak(text: str, voice_id: str | None = None, speed: float | None = None, temperature: float | None = None,
          latency: str = "balanced", sample_rate: int = SAMPLE_RATE, should_stop=None,
          model: str | None = None) -> Iterator[np.ndarray]:
    """Озвучить текст. Звук приходит кусочками (float32, моно, 24 кГц) — можно играть сразу.
    model — модель озвучки (s2.1-pro-free / s2.1-pro / drama-3); не задана — из настроек голоса."""
    global _free_failed, _drama_off_until
    body = {"text": text, "reference_id": voice_id or getattr(config, "VOICE_ID", "") or DEFAULT_VOICE,
            "format": "pcm", "sample_rate": sample_rate, "latency": latency, "normalize": True,
            # выразительность и темп настроек больше не имеют — подачу Мартышка задаёт сама метками в тексте
            "temperature": float(temperature if temperature is not None else 0.8),
            "top_p": 0.8, "chunk_length": 200,
            "prosody": {"speed": float(speed if speed is not None else 1.0), "volume": 0, "normalize_loudness": True}}
    for attempt in range(3):
        use = tts_model(model)
        try:
            with http().stream("POST", API + "/v1/tts", headers=_headers(use), json=body) as r:
                if r.status_code != 200:
                    err = r.read().decode("utf-8", "replace")
                    # на Drama кончились кредиты — 30 минут говорим бесплатной моделью, чтобы не замолчать
                    if use == DRAMA and r.status_code in (402, 403):
                        _drama_off_until = time.time() + 30 * 60
                        continue
                    # бесплатная модель закончилась (после 30.11.2026) — дальше платная, тот же голос
                    if use == FREE and r.status_code in (400, 403, 404, 410, 422):
                        _free_failed = True
                        continue
                    if r.status_code in (429, 500, 502, 503, 504) and attempt < 2:
                        time.sleep(1.0 + attempt)
                        continue
                    raise FishError(_explain(r.status_code, err))
                tail = b""
                for chunk in r.iter_bytes(4800):
                    if should_stop and should_stop():
                        return
                    data = tail + chunk
                    cut = len(data) - (len(data) % 2)
                    tail = data[cut:]
                    if cut:
                        yield np.frombuffer(data[:cut], dtype="<i2").astype(np.float32) / 32768.0
                return
        except (httpx.ConnectError, httpx.ReadTimeout, httpx.RemoteProtocolError) as e:
            if attempt == 2:
                raise FishError(f"Нет связи с Fish Audio: {e}")
            time.sleep(1.0)


def speak_all(text: str, voice_id: str | None = None, **kw) -> np.ndarray:
    parts = list(speak(text, voice_id, **kw))
    return np.concatenate(parts) if parts else np.zeros(0, np.float32)


# ---------- каталог голосов ----------
def _official() -> list[dict]:
    return [{"id": i, "title": t, "lang": l, "gender": g, "official": True, "popular": 0, "desc": "Официальный голос Fish"}
            for i, t, l, g in OFFICIAL]


def _gender(tags: list[str]) -> str:
    t = [x.lower() for x in tags or []]
    return "male" if "male" in t else "female" if "female" in t else ""


ANIME_LANGS = ["ru", "uk", "de", "en", "ja"]
# голоса «с характером» — с Drama 3 играют лучше всего (Drama работает со всеми голосами)
ACTING_TAGS = {"drama", "dramatic", "expressive", "emotional", "villain", "cartoon", "theatrical"}


def _entry(i: dict, lang: str) -> dict:
    tags = [str(t).lower() for t in (i.get("tags") or [])]
    return {"id": i["_id"], "title": (i.get("title") or "").strip()[:60], "lang": lang,
            "gender": _gender(tags), "official": False, "popular": int(i.get("task_count") or 0),
            "desc": (i.get("description") or "")[:160], "anime": "anime" in tags,
            "acting": bool(ACTING_TAGS & set(tags))}


def _pages(params: dict, want: int, keep=lambda i: True, max_pages: int = 8):
    got, page = 0, 1
    while got < want and page <= max_pages:
        r = http().get(API + "/model", headers=_headers(), params={"page_size": 100, "page_number": page,
                                                                   "sort_by": "task_count", **params}, timeout=40)
        if r.status_code != 200:
            return
        items = r.json().get("items") or []
        if not items:
            return
        for i in items:
            if i.get("dmca_taken_down") or i.get("state") != "trained" or i.get("type") != "tts" or not keep(i):
                continue
            got += 1
            yield i
            if got >= want:
                return
        page += 1


def refresh_catalog(per_lang: int = 300, anime_per_lang: int = 100) -> list[dict]:
    """Скачать список голосов: официальные + самые популярные на 4 языках + аниме-голоса (ru/uk/de/en/ja)."""
    out = {v["id"]: v for v in _official()}
    for lang in LANGS:
        for i in _pages({"language": lang}, per_lang):
            out.setdefault(i["_id"], _entry(i, lang))
    is_anime = lambda i: "anime" in [str(t).lower() for t in (i.get("tags") or [])]
    for lang in ANIME_LANGS:
        for i in _pages({"language": lang, "tag": "anime"}, anime_per_lang, keep=is_anime):
            if i["_id"] in out:
                out[i["_id"]]["anime"] = True
            else:
                out[i["_id"]] = _entry(i, lang)
    order = list(LANGS) + ["ja"]
    voices = sorted(out.values(), key=lambda v: (not v["official"], order.index(v["lang"]) if v["lang"] in order else 9,
                                                -v["popular"]))
    CATALOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    CATALOG_FILE.write_text(json.dumps(voices, ensure_ascii=False, indent=1), encoding="utf-8")
    return voices


def catalog() -> list[dict]:
    v = []
    try:
        v = json.loads(CATALOG_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    if not v:
        v = _official()
    ids = {x["id"] for x in v}
    v += [o for o in _official() if o["id"] not in ids]        # новые официальные (например, Delia)
    for x in v:
        x["drama"] = x["id"] in DRAMA_PICKS or x.get("official") or x.get("acting") or x.get("anime")
    return v


def search(query: str, limit: int = 40) -> list[dict]:
    """Поиск голоса по названию во всей библиотеке Fish."""
    r = http().get(API + "/model", headers=_headers(), params={"page_size": limit, "title": query,
                                                               "sort_by": "task_count"}, timeout=30)
    if r.status_code != 200:
        raise FishError(_explain(r.status_code, r.text))
    res = []
    for i in r.json().get("items") or []:
        if i.get("dmca_taken_down") or i.get("type") != "tts":
            continue
        langs = i.get("languages") or [""]
        res.append(_entry(i, (i.get("languages") or [""])[0]))
    return res


def voice_name(voice_id: str) -> str:
    for v in catalog():
        if v["id"] == voice_id:
            return v["title"]
    return voice_id[:8]


def credits() -> str:
    try:
        r = http().get(API + "/wallet/self/api-credit", headers=_headers(), timeout=15)
        return f"{float(r.json().get('credit') or 0):.2f} $" if r.status_code == 200 else _explain(r.status_code, r.text)
    except Exception as e:
        return f"не узнать: {e}"
