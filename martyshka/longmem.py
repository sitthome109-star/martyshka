"""
ДОЛГАЯ ПАМЯТЬ МАРТЫШКИ — одна на всех: голос, чат (Студия), Говорилка, Telegram.

Хранится в data/память/ (обычные текстовые файлы, можно открыть Блокнотом):
  лента/2026-11-20.jsonl — всё, что сказали за день: кто, где, что (и что ответила Мартышка)
  дни.json               — короткий пересказ каждого прошедшего дня («вчера с братом делали игру, Enbulih спорил…»)
  люди.json              — кто есть кто: факты о каждом, как к нему относится Мартышка, когда видела последний раз

Мозг получает: пересказ последних дней + сегодняшние главные события + карточки людей, которые сейчас рядом.
Старое не теряется: «что было 15 ноября?» — находится поиском по ленте и пересказам.
"""
from __future__ import annotations

import json
import re
import threading
import time
from datetime import date, datetime, timedelta
from pathlib import Path

from . import config

DIR = config.DATA_DIR / "память"
FEED = DIR / "лента"
DAYS = DIR / "дни.json"
PEOPLE = DIR / "люди.json"
OWNER = "брат"
_lock = threading.RLock()


def _load(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _save(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


def norm(name: str) -> str:
    """Один и тот же человек под разными написаниями: «Enbulih», «enbulih» → один ключ."""
    n = re.sub(r"\s+", " ", (name or "").strip())
    if not n or n.lower() in ("брат", "хозяин", "sitthome", "owner"):
        return OWNER
    # ник брата sitthome на слух распознаётся по-разному: Сидхам, Ситхам, Ситхом, Сиддхом, Ситхун…
    if re.fullmatch(r"(сит+х?[оау]м|сид+х?[оау]м|сит+хун|сит+хоум|sit+h?om?e?)", n.lower()):
        return OWNER
    return n


# ---------- лента: всё, что было ----------
def log(source: str, who: str, text: str, reply: str = "") -> None:
    """Записать реплику (и ответ Мартышки) в ленту дня. source: голос / чат / говорилка / telegram."""
    if not (text or reply):
        return
    rec = {"t": datetime.now().strftime("%H:%M:%S"), "где": source, "кто": norm(who), "сказал": (text or "")[:2000]}
    if reply:
        rec["мартышка"] = reply[:2000]
    with _lock:
        FEED.mkdir(parents=True, exist_ok=True)
        with (FEED / f"{date.today().isoformat()}.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        p = person(who)
        p["последний_раз"] = datetime.now().isoformat(timespec="minutes")
        p["где"] = source
        p["реплик"] = int(p.get("реплик", 0)) + 1
        _save_people()


def feed(day: date) -> list[dict]:
    out = []
    try:
        for line in (FEED / f"{day.isoformat()}.jsonl").read_text(encoding="utf-8").splitlines():
            try:
                out.append(json.loads(line))
            except Exception:
                pass
    except FileNotFoundError:
        pass
    return out


def _feed_text(recs: list[dict], limit: int = 60000) -> str:
    lines = []
    for r in recs:
        s = f"{r['t'][:5]} [{r.get('где', '')}] {r.get('кто', '?')}: {r.get('сказал', '')}"
        if r.get("мартышка"):
            s += f"\n   Мартышка: {r['мартышка']}"
        lines.append(s)
    text = "\n".join(lines)
    return text[-limit:]


# ---------- люди ----------
_people: dict | None = None


def people() -> dict:
    global _people
    with _lock:
        if _people is None:
            _people = _load(PEOPLE, {})
        return _people


def _save_people() -> None:
    with _lock:
        _save(PEOPLE, people())


def person(name: str) -> dict:
    key = norm(name)
    with _lock:
        ps = people()
        for k in ps:                                   # без учёта регистра
            if k.lower() == key.lower():
                return ps[k]
        ps[key] = {"факты": [], "злость": 0, "первый_раз": datetime.now().isoformat(timespec="minutes")}
        return ps[key]


def remember_about(name: str, fact: str) -> bool:
    fact = (fact or "").strip().rstrip(".")
    if not fact:
        return False
    with _lock:
        p = person(name)
        low = fact.lower()
        if any(low == f.lower() or low in f.lower() for f in p["факты"]):
            return False
        p["факты"] = (p["факты"] + [fact])[-40:]
        _save_people()
        return True


def forget_about(name: str, what: str) -> int:
    with _lock:
        p = person(name)
        w = what.lower().strip()
        before = len(p["факты"])
        p["факты"] = [f for f in p["факты"] if w not in f.lower()]
        _save_people()
        return before - len(p["факты"])


# ---------- настроение: нарастающая злость на каждого ----------
def anger(name: str) -> int:
    """0 — спокойна и дружелюбна … 5 — в ярости. Само остывает: −1 за каждые 10 минут без оскорблений."""
    with _lock:
        p = person(name)
        lvl, since = int(p.get("злость", 0)), float(p.get("злость_с", 0) or 0)
        if lvl and since:
            cooled = int((time.time() - since) // 600)
            if cooled:
                lvl = max(0, lvl - cooled)
                p["злость"], p["злость_с"] = lvl, time.time() if lvl else 0
                _save_people()
        return lvl


def set_anger(name: str, level: int) -> int:
    with _lock:
        p = person(name)
        level = max(0, min(5, int(level)))
        p["злость"] = level
        p["злость_с"] = time.time() if level else 0
        _save_people()
        return level


ANGER_WORDS = {
    0: "спокойствие и дружелюбие",
    1: "лёгкое раздражение — отвечать суше, с лёгкой подколкой",
    2: "раздражение — язвить, подкалывать в ответ",
    3: "злость — грубить в ответ, с матом, жёстко высмеивать",
    4: "бешенство — крыть матом, унижать остроумно и зло",
    5: "ярость — максимально жёстко и беспощадно поливать словами",
}


# ---------- дни: пересказы ----------
def days() -> dict:
    return _load(DAYS, {})


def summarize_pending() -> None:
    """Пересказать прошедшие дни, которые ещё не пересказаны (делается сам при запуске и раз в час)."""
    from . import deepseek
    if not deepseek.ready():
        return
    done = days()
    today = date.today()
    for f in sorted(FEED.glob("*.jsonl")) if FEED.exists() else []:
        d = f.stem
        if d >= today.isoformat() or d in done:
            continue
        recs = feed(date.fromisoformat(d))
        if not recs:
            continue
        try:
            text = deepseek.ask(
                "Вот лента всех разговоров Мартышки за " + d + ":\n\n" + _feed_text(recs) +
                "\n\nСделай пересказ этого дня для её долгой памяти: 5–12 строк по-русски. Кто был, о чём говорили, "
                "что делали и решили, что обещали, кто с кем ругался, важные факты о людях. Конкретно: имена, "
                "названия, числа. Без воды.",
                model=deepseek.FLASH, max_tokens=900, temperature=0.2)
        except Exception:
            return
        if text:
            done[d] = text
            _save(DAYS, done)
            _learn_people(recs)
            try:                                   # саморазвитие: чему научилась за день
                from . import growth
                growth.reflect_day(d, _feed_text(recs))
            except Exception:
                pass


def _learn_people(recs: list[dict]) -> None:
    """После дня: вытащить новые факты о людях (кто чем занимается, что любит, о чём рассказывал)."""
    from . import deepseek
    try:
        raw = deepseek.ask(
            "Лента разговоров:\n" + _feed_text(recs, 40000) +
            "\n\nВыпиши новые устойчивые факты о людях (не о Мартышке). Ответ — JSON: "
            '{"люди": [{"имя": "...", "факты": ["...", "..."]}]}. Хозяина зови «брат». Мелочи и разовые реплики — '
            "не надо. Если ничего — пустой список.", model=deepseek.FLASH, max_tokens=1200, json_mode=True)
        data = json.loads(raw)
    except Exception:
        return
    for item in data.get("люди") or []:
        for fact in (item.get("факты") or [])[:6]:
            remember_about(item.get("имя") or "", fact)


_bg_started = False


def start_background() -> None:
    """Раз в час пересказывать прошедшие дни (и сразу при запуске)."""
    global _bg_started
    if _bg_started:
        return
    _bg_started = True

    def loop():
        try:                                   # старые журналы — в память (один раз)
            from . import memory_import
            memory_import.run()
        except Exception:
            pass
        while True:
            try:
                summarize_pending()
            except Exception:
                pass
            time.sleep(3600)
    threading.Thread(target=loop, daemon=True, name="longmem").start()


# ---------- что дать мозгу ----------
def context(present: list[str] | None = None, source: str = "", today_lines: int = 40, n_days: int = 7) -> str:
    """Кусок инструкции: что Мартышка помнит. present — кто сейчас рядом (для них — карточки)."""
    parts = []
    ds = days()
    if ds:
        last = sorted(ds)[-n_days:]
        def label(d: str) -> str:
            delta = (date.today() - date.fromisoformat(d)).days
            return {1: "Вчера", 2: "Позавчера"}.get(delta, f"{d} ({delta} дн. назад)")
        parts.append("Что было в прошлые дни:\n" + "\n".join(f"— {label(d)}: {ds[d]}" for d in last))
    recs = feed(date.today())
    if recs:
        other = [r for r in recs if r.get("где") != source][-today_lines:]
        if other:
            parts.append("Сегодня в других местах (" + ", ".join(sorted({r['где'] for r in other})) + "):\n"
                         + _feed_text(other, 6000))
    names = [OWNER] + [norm(n) for n in (present or []) if norm(n) != OWNER]
    cards = []
    for n in dict.fromkeys(names):
        p = person(n)
        bits = []
        if p.get("факты"):
            bits.append("; ".join(p["факты"][-15:]))
        lvl = anger(n)
        if lvl:
            bits.append(f"сейчас ты на него зла: {lvl}/5")
        if p.get("последний_раз") and n != OWNER:
            bits.append(f"последний раз: {p['последний_раз'].replace('T', ' ')}")
        if bits:
            cards.append(f"• {n}: " + " | ".join(bits))
    if cards:
        parts.append("Кого ты знаешь:\n" + "\n".join(cards))
    return "\n\n".join(parts)


def search(query: str, max_hits: int = 30) -> str:
    """Найти в памяти: по пересказам дней и по всей ленте (для «что было тогда-то», «что говорил Enbulih про …»)."""
    q = [w for w in re.findall(r"\w{3,}", query.lower())]
    hits = []
    for d, txt in sorted(days().items()):
        if any(w[:5] in txt.lower() for w in q):
            hits.append(f"[{d}, пересказ] {txt}")
    for f in sorted(FEED.glob("*.jsonl"), reverse=True)[:120] if FEED.exists() else []:
        for r in feed(date.fromisoformat(f.stem)):
            blob = f"{r.get('кто')} {r.get('сказал')} {r.get('мартышка', '')}".lower()
            if q and sum(w[:5] in blob for w in q) >= max(1, len(q) // 2):
                hits.append(f"[{f.stem} {r['t'][:5]} {r.get('где')}] {r.get('кто')}: {r.get('сказал')}"
                            + (f" → Мартышка: {r['мартышка'][:200]}" if r.get("мартышка") else ""))
            if len(hits) >= max_hits:
                break
    m = re.search(r"(\d{4}-\d{2}-\d{2})", query)
    if m and m.group(1) in days():
        hits.insert(0, f"[{m.group(1)}] {days()[m.group(1)]}")
    return "\n".join(hits[:max_hits]) or "В памяти ничего про это не нашла."


def day_text(when: date) -> str:
    ds = days()
    if when.isoformat() in ds:
        return ds[when.isoformat()]
    return _feed_text(feed(when), 8000) or "В этот день разговоров не было."


def yesterday() -> date:
    return date.today() - timedelta(days=1)
