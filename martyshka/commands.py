"""
СВОИ КОМАНДЫ: одна фраза → несколько дел подряд.
Например «рабочий режим» → открой стим; включи музыку Rammstein; громкость 30.

Хранятся в data/команды.json, редактируются в окне настроек («Мои команды»).
Шаги пишутся обычными словами; мозг один раз переводит их в дела, и это запоминается (кэш),
так что в следующий раз команда выполняется мгновенно.
"""
import difflib
import json
import re

from . import config

FILE = config.DATA_DIR / "команды.json"
CACHE_FILE = config.DATA_DIR / "команды_кэш.json"


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", s.lower().replace("ё", "е"))).strip()


def load() -> list[dict]:
    try:
        items = json.loads(FILE.read_text(encoding="utf-8"))
        return [c for c in items if c.get("phrase", "").strip() and c.get("steps")]
    except Exception:
        return []


def save(items: list[dict]) -> None:
    FILE.parent.mkdir(parents=True, exist_ok=True)
    FILE.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")


def match(text: str) -> dict | None:
    """Своя команда, если фраза брата на неё похожа («включи рабочий режим» ⊃ «рабочий режим»)."""
    t = _norm(text)
    if not t:
        return None
    best, best_score = None, 0.0
    for c in load():
        p = _norm(c["phrase"])
        if not p:
            continue
        if re.search(rf"(^|\s){re.escape(p)}(\s|$)", t):
            score = 1.0 + len(p) / 100          # точное вхождение; длиннее фраза — точнее
        else:
            score = difflib.SequenceMatcher(None, p, t).ratio()
        if score > best_score:
            best, best_score = c, score
    return best if best_score >= 0.85 else None


def _load_cache() -> dict:
    try:
        return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def actions_for(command: dict, to_actions) -> list[tuple[str, list[str]]]:
    """Дела для своей команды. to_actions(шаг) -> [(дело, [аргументы])] — мозг (только при первом запуске шага)."""
    cache = _load_cache()
    out, changed = [], False
    for step in command["steps"]:
        step = step.strip()
        if not step:
            continue
        if step not in cache:
            cache[step] = [list(a) for a in to_actions(step)]
            changed = True
        out += [(a[0], list(a[1])) for a in cache[step]]
    if changed:
        CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        CACHE_FILE.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")
    return out
