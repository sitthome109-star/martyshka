"""
Один раз перенести старые журналы в долгую память (data/память/лента): Говорилка, голос, Telegram, чаты Студии.
Запуск: .venv\\Scripts\\python.exe -m martyshka.memory_import   (повторный запуск ничего не дублирует)
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime

from . import config, longmem

DONE = longmem.DIR / "импорт_сделан.txt"


def _write(day: str, recs: list[dict]) -> None:
    longmem.FEED.mkdir(parents=True, exist_ok=True)
    path = longmem.FEED / f"{day}.jsonl"
    with path.open("a", encoding="utf-8") as f:
        for r in recs:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def run() -> str:
    if DONE.exists():
        return "Уже импортировано."
    by_day: dict[str, list[dict]] = {}
    year = date.today().year

    # Говорилка: «19.11 00:38:27 Мартышка (ИИ): текст   [служебное]»
    gl = config.DATA_DIR / "govorilka_bot" / "журнал.txt"
    if gl.exists():
        last_server = None
        for line in gl.read_text(encoding="utf-8", errors="replace").splitlines():
            m = re.match(r"(\d\d)\.(\d\d) (\d\d:\d\d:\d\d) ([^:]+): (.*)$", line)
            if not m:
                continue
            d, mo, t, who, text = m.groups()
            day = f"{year}-{mo}-{d}"
            text = re.sub(r"\s{3}\[.*\]$", "", text).strip()
            text = re.sub(r"^\[[а-яё ]+\]\s*", "", text, flags=re.I)
            if who in ("система", "ошибка") or not text:
                continue
            if who == "сервер":
                last_server = {"t": t, "где": "говорилка", "кто": "кто-то на сервере", "сказал": text}
                by_day.setdefault(day, []).append(last_server)
            elif last_server is not None and not last_server.get("мартышка"):
                last_server["мартышка"] = text
            else:
                by_day.setdefault(day, []).append({"t": t, "где": "говорилка", "кто": config.NAME, "сказал": "",
                                                   "мартышка": text})

    # Чаты Студии
    chats = config.DATA_DIR / "studio" / "chats"
    for p in chats.glob("*.json") if chats.exists() else []:
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        when = datetime.fromtimestamp(d.get("created") or p.stat().st_mtime)
        disp = d.get("display", [])
        for i, it in enumerate(disp):
            if it.get("type") != "user":
                continue
            ans = next((x.get("text", "") for x in disp[i + 1:] if x.get("type") == "assistant"), "")
            by_day.setdefault(when.date().isoformat(), []).append(
                {"t": when.strftime("%H:%M:%S"), "где": "чат", "кто": longmem.OWNER, "сказал": it.get("text", "")[:2000],
                 "мартышка": ans[:1500]})

    # Голос и Telegram: журнал главной программы («===== Запуск 20.11.2026 13:00:00 =====»)
    vl = config.DATA_DIR / "журнал.txt"
    if vl.exists():
        day, pending = None, None
        for line in vl.read_text(encoding="utf-8", errors="replace").splitlines():
            m = re.match(r"=+ Запуск (\d\d)\.(\d\d)\.(\d{4}) (\d\d:\d\d:\d\d)", line)
            if m:
                day = f"{m.group(3)}-{m.group(2)}-{m.group(1)}"
                continue
            if not day:
                continue
            m = re.match(r"\s*(?:📱 )?Ты(?: \((Telegram)\))?: (.+)$", line)
            if m:
                pending = {"t": "00:00:00", "где": "telegram" if m.group(1) else "голос", "кто": longmem.OWNER,
                           "сказал": m.group(2).strip()}
                by_day.setdefault(day, []).append(pending)
                continue
            m = re.match(r"\s*Мартышка: (.+)$", line)
            if m and pending is not None and not pending.get("мартышка"):
                pending["мартышка"] = m.group(1).strip()

    total = 0
    for day, recs in sorted(by_day.items()):
        if day >= date.today().isoformat():
            continue                                  # сегодняшнее и так пишется само
        _write(day, recs)
        total += len(recs)
    DONE.parent.mkdir(parents=True, exist_ok=True)
    DONE.write_text(datetime.now().isoformat(), encoding="utf-8")
    return f"Перенесено реплик: {total}, дней: {len([d for d in by_day if d < date.today().isoformat()])}."


if __name__ == "__main__":
    print(run())
