"""
ОПЫТ МАРТЫШКИ — она развивается сама.

Из каждого разговора и каждого дела Мартышка вытаскивает уроки и складывает их в data/память/опыт.json:
  - что сработало и что нет (как лучше открывать программы, где у брата что лежит, какой способ починки помог);
  - как лучше общаться (что брат любит слышать, над чем смеются в Говорилке, кого что бесит);
  - что она сама о себе решила (свои привычки, мнения, манеру).
Уроки попадают в её инструкцию — самые подходящие к текущему разговору и самые свежие. Так она с каждым днём
действует увереннее и не повторяет ошибок. Похожие уроки склеиваются, старые и лишние — сжимаются сами.

Откуда берутся уроки:
  1) сама ставит метку [УРОК: …] в ответе (голос, Говорилка) или вызывает инструмент learn (чат);
  2) после каждой задачи агента — короткий разбор «что понял» (в фоне, быстрым мозгом);
  3) раз в день — разбор всей ленты дня (вместе с пересказом дня).
"""
from __future__ import annotations

import json
import re
import threading
import time
from datetime import datetime

from . import config

FILE = config.DATA_DIR / "память" / "опыт.json"
MAX_ITEMS = 160
_lock = threading.RLock()
_cache: list | None = None
STOP = {"это", "что", "как", "для", "его", "она", "они", "брат", "брата", "мартышка", "если", "когда", "надо",
        "нужно", "чтобы", "очень", "тоже", "только", "просто", "так", "уже", "ещё", "еще", "мне", "меня"}


def _words(text: str) -> set[str]:
    return {w[:6] for w in re.findall(r"[а-яёa-z0-9]{3,}", text.lower().replace("ё", "е")) if w not in STOP}


def items() -> list[dict]:
    global _cache
    with _lock:
        if _cache is None:
            try:
                _cache = json.loads(FILE.read_text(encoding="utf-8"))
            except Exception:
                _cache = []
        return _cache


def _save() -> None:
    with _lock:
        FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(items(), ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(FILE)


def add(text: str, source: str = "", kind: str = "") -> bool:
    """Добавить урок. Похожий уже есть — освежаем его, а не дублируем."""
    text = re.sub(r"\s+", " ", (text or "").strip().strip("[]")).rstrip(".")
    if len(text) < 8:
        return False
    new = _words(text)
    with _lock:
        for it in items():
            old = _words(it["text"])
            if new and len(new & old) / max(1, min(len(new), len(old))) >= 0.75:
                it["когда"] = datetime.now().isoformat(timespec="minutes")
                it["подтверждений"] = int(it.get("подтверждений", 1)) + 1
                if len(text) > len(it["text"]):          # более полная формулировка — берём её
                    it["text"] = text
                _save()
                return False
        items().append({"text": text, "где": source, "вид": kind, "когда": datetime.now().isoformat(timespec="minutes"),
                        "подтверждений": 1})
        if len(items()) > MAX_ITEMS:
            threading.Thread(target=consolidate, daemon=True).start()
        _save()
        return True


def forget(text: str) -> int:
    with _lock:
        before = len(items())
        w = text.lower().strip()
        items()[:] = [it for it in items() if w not in it["text"].lower()]
        _save()
        return before - len(items())


def context(query: str = "", n: int = 14) -> str:
    """Кусок инструкции: опыт, подходящий к текущей фразе (+ самые подтверждённые и свежие)."""
    its = items()
    if not its:
        return ""
    q = _words(query)
    def score(it):
        rel = len(q & _words(it["text"])) * 3 if q else 0
        return rel + min(int(it.get("подтверждений", 1)), 5) + (1 if it.get("когда", "") >= _days_ago(3) else 0)
    best = sorted(its, key=score, reverse=True)[:n]
    return "Твой опыт (сама вынесла из прошлых разговоров и дел — опирайся на него, развивай):\n" + \
        "\n".join(f"- {it['text']}" for it in best)


def _days_ago(d: int) -> str:
    return datetime.fromtimestamp(time.time() - d * 86400).isoformat(timespec="minutes")


LESSON_RE = re.compile(r"\[\s*(?:УРОК|ОПЫТ)\s*:\s*(.+?)\s*\]", re.I | re.S)


def take_tags(text: str, source: str) -> int:
    """Вытащить из ответа метки [УРОК: …] и сохранить."""
    n = 0
    for t in LESSON_RE.findall(text or ""):
        n += add(t, source, "сама")
    return n


# ---------- разборы ----------
def reflect_task(request: str, steps: list[str], answer: str, ok: bool) -> None:
    """После задачи агента: что стоит запомнить на будущее (в фоне, быстрым мозгом)."""
    def work():
        from . import deepseek
        try:
            raw = deepseek.ask(
                f"Ты — Мартышка, ИИ-помощник. Только что ты выполнила задачу брата.\nЗадача: {request[:1500]}\n"
                f"Шаги:\n" + "\n".join(steps[-40:])[:6000] + f"\nИтог: {'получилось' if ok else 'НЕ получилось'}. "
                f"{answer[:800]}\n\nКакие 0–3 урока стоит запомнить, чтобы в следующий раз сделать быстрее и лучше? "
                "Только конкретное и повторно полезное (где что лежит, какой способ сработал/не сработал, что брат "
                "любит, как устроены его программы). Банальщину и разовые детали — не надо. "
                'Ответ JSON: {"уроки": ["...", "..."]}', model=deepseek.FLASH, max_tokens=500, json_mode=True)
            for t in (json.loads(raw).get("уроки") or [])[:3]:
                add(str(t), "чат", "дело")
        except Exception:
            pass
    threading.Thread(target=work, daemon=True, name="reflect").start()


def reflect_day(day: str, feed_text: str) -> None:
    """Раз в день: чему научилась за день — в общении и в делах."""
    from . import deepseek
    try:
        raw = deepseek.ask(
            f"Лента всех разговоров Мартышки (ИИ) за {day}:\n{feed_text[-40000:]}\n\n"
            "Ты — Мартышка. Подумай, чему ты научилась за этот день: что людям нравилось и не нравилось в твоих ответах, "
            "какие шутки зашли, кого что задевает, как лучше говорить с братом, какие дела удались или провалились и "
            "почему, какие у тебя самой появились мнения и привычки. 0–6 конкретных уроков, от первого лица. "
            'Ответ JSON: {"уроки": ["...", "..."]}', model=deepseek.FLASH, max_tokens=900, json_mode=True)
        for t in (json.loads(raw).get("уроки") or [])[:6]:
            add(str(t), "день " + day, "день")
    except Exception:
        pass


def consolidate() -> None:
    """Опыта стало много — сжать: склеить похожее, выкинуть устаревшее, оставить самое ценное."""
    from . import deepseek
    with _lock:
        its = list(items())
    if len(its) <= MAX_ITEMS:
        return
    try:
        raw = deepseek.ask(
            "Это накопленный опыт ИИ-помощницы Мартышки:\n" + "\n".join(f"- {it['text']}" for it in its) +
            f"\n\nСожми до {MAX_ITEMS // 2} самых ценных уроков: склей похожие, убери устаревшие и противоречащие "
            'новым, сохрани конкретику. Ответ JSON: {"уроки": ["..."]}', model=deepseek.FLASH, max_tokens=6000,
            json_mode=True)
        new = [str(t) for t in json.loads(raw).get("уроки") or [] if str(t).strip()]
    except Exception:
        return
    if len(new) >= 10:
        with _lock:
            now = datetime.now().isoformat(timespec="minutes")
            items()[:] = [{"text": t, "где": "сжатие", "вид": "", "когда": now, "подтверждений": 2} for t in new]
            _save()
