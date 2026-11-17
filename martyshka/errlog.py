"""
ДНЕВНИК ОШИБОК: data/ошибки.jsonl — по нему потом чиним Мартышку по РЕАЛЬНЫМ случаям, а не наугад.

Пишется, когда:
  - дело не получилось (ok=False), кроме честных «брат передумал»;
  - брат сразу после ответа жалуется: «не то», «не сделал», «я не просил», «не работает», «ты не понял»…
"""
import json
import re
import time

from . import config

FILE = config.DATA_DIR / "ошибки.jsonl"
COMPLAINT_RE = re.compile(
    r"(^\W*не\s+то\b|не\s+так\b|не\s+сделал|ничего\s+не\s+(сделал|произошло|открыл|случилось|поменялось)|"
    r"не\s+работает|неправильно|не\s+правильно|я\s+не\s+просил|зачем\s+ты|ты\s+ошиб|опять\s+не|не\s+открыл|"
    r"не\s+закрыл|не\s+включил|ты\s+не\s+понял|не\s+понял\s+меня|что\s+ты\s+сделал|не\s+выполнил|не\s+получилось|"
    r"фигн|хрен\w*\s+что|не\s+то\s+(открыл|сделал|включил))", re.IGNORECASE)


def write(kind: str, **data) -> None:
    try:
        FILE.parent.mkdir(parents=True, exist_ok=True)
        rec = {"когда": time.strftime("%Y-%m-%d %H:%M:%S"), "что": kind, **data}
        with FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass


class Turn:
    """Запоминаем прошлый ход разговора, чтобы при жалобе записать, что именно было не так."""

    def __init__(self):
        self.last = None

    def complaint(self, text: str, channel: str) -> None:
        if self.last and COMPLAINT_RE.search(text) and time.time() - self.last["t"] < 600:
            write("жалоба брата", канал=channel, жалоба=text, просьба=self.last["request"],
                  дела=self.last["actions"], результаты=self.last["lines"], ответ=self.last["reply"][:400])

    def done(self, request: str, actions, lines, reply: str, failed, channel: str) -> None:
        self.last = {"t": time.time(), "request": request, "actions": [f"{n} | {' | '.join(a)}" for n, a in actions],
                     "lines": [l[:300] for l in lines], "reply": reply or ""}
        for n, a, t in failed or []:
            if "передумал" not in t:
                write("дело не вышло", канал=channel, просьба=request, дело=f"{n} | {' | '.join(a)}", результат=t[:300])
