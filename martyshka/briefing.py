"""
УТРЕННЯЯ СВОДКА: при первом включении компа за день Мартышка рассказывает
погоду, напоминания на сегодня и заряд мышки / наушников (из Logitech G HUB).
"""
import asyncio
import json
import threading
from datetime import date

from . import config

LAST_FILE = config.DATA_DIR / "последняя_сводка.txt"
TYPES = {"HEADSET": "наушники", "MOUSE": "мышка", "KEYBOARD": "клавиатура", "SPEAKER": "колонка"}


# ---------- заряд устройств (Logitech G HUB работает на этом же компе, порт 9010) ----------
async def _ghub_battery(timeout: float) -> list[dict]:
    import websockets
    out = []
    async with websockets.connect("ws://127.0.0.1:9010", subprotocols=["json"], origin="file://",
                                  open_timeout=timeout, max_size=None) as ws:
        async def ask(path):
            await ws.send(json.dumps({"msgId": "", "verb": "GET", "path": path}))
            for _ in range(30):
                m = json.loads(await asyncio.wait_for(ws.recv(), timeout))
                if m.get("path") == path:
                    return m
        devs = await ask("/devices/list") or {}
        for d in (devs.get("payload") or {}).get("deviceInfos", []):
            r = await ask(f"/battery/{d['id']}/state")
            p = (r or {}).get("payload") or {}
            if "percentage" not in p:
                continue
            out.append({"name": TYPES.get(d.get("deviceType"), d.get("displayName", "устройство")),
                        "model": d.get("displayName", ""), "percent": int(p["percentage"]),
                        "charging": bool(p.get("charging"))})
    return out


CACHE_FILE = config.DATA_DIR / "заряд.json"   # последний известный заряд (спящая мышка заряд не сообщает)


def battery(timeout: float = 3.0) -> list[dict]:
    """[{name, model, percent, charging, old}] — old=True: устройство спит, это последнее известное значение."""
    import time
    result = []

    def run():   # в своём потоке: у главной программы может уже крутиться свой asyncio
        try:
            result.extend(asyncio.run(_ghub_battery(timeout)))
        except Exception:
            pass
    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(timeout * 4)
    try:   # клавиатура AULA F75 Max — через свой приёмник 2.4G (так же, как в F75 Studio)
        from . import aula
        kb = aula.battery()
        if kb is not None:
            result.append({"name": "клавиатура", "model": "AULA F75 Max", "percent": kb, "charging": False})
    except Exception:
        pass
    try:
        cache = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    except Exception:
        cache = {}
    now = time.time()
    for b in result:
        cache[b["model"]] = dict(b, time=now)
    if result:
        CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        CACHE_FILE.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")
    fresh = {b["model"] for b in result}
    for model, b in cache.items():   # спит сейчас — берём последнее значение (не старше 2 суток)
        if model not in fresh and now - b.get("time", 0) < 2 * 86400:
            result.append(dict(b, old=True, hours=int((now - b["time"]) / 3600)))
    return result


KEYBOARD_NOTE = ("Заряд клавиатуры AULA F75 Max пока неизвестен: она спит или подключена кабелем. "
                 "Нажми на ней любую клавишу — и через минуту Мартышка его узнает.")


def battery_text(with_keyboard_note: bool = True) -> str:
    items = battery()
    has_kb = any(b.get("model") == "AULA F75 Max" for b in items)
    note = (" " + KEYBOARD_NOTE) if with_keyboard_note and not has_kb else ""
    if not items:
        return "Заряд мышки и наушников узнать не вышло: Logitech G HUB не запущен или устройства выключены." + note
    parts = []
    for b in items:
        s = f"{b['name']} ({b['model']}) — {b['percent']}%"
        if b.get("old"):
            s += (f" (сейчас спит, это было {b['hours']} ч назад)" if b["hours"] else " (сейчас спит, было недавно)")
        if b["charging"] and not b.get("old"):
            s += ", заряжается"
        elif b["percent"] <= 15:
            s += " — ПОЧТИ СЕЛА, надо зарядить"
        parts.append(s)
    return "Заряд: " + "; ".join(parts) + "." + note


# ---------- сводка ----------
def _idle_seconds() -> float:
    import ctypes
    import ctypes.wintypes as wt

    class _L(ctypes.Structure):
        _fields_ = [("cbSize", wt.UINT), ("dwTime", wt.DWORD)]
    li = _L(); li.cbSize = ctypes.sizeof(li)
    ctypes.windll.user32.GetLastInputInfo(ctypes.byref(li))
    return (ctypes.windll.kernel32.GetTickCount() - li.dwTime) / 1000


def start_battery_watch(every: int = 180) -> None:
    """Пока брат за компом (клавиатура и мышка не спят) — раз в 3 минуты тихо обновляем заряд и запоминаем.
    Тогда даже из Telegram, когда всё уснуло, Мартышка знает свежий заряд."""
    import time

    def loop():
        time.sleep(30)
        while True:
            try:
                if _idle_seconds() < 90:
                    battery()
            except Exception:
                pass
            time.sleep(every)
    threading.Thread(target=loop, daemon=True, name="battery-watch").start()


def due_today() -> bool:
    """Сегодня сводки ещё не было?"""
    try:
        return LAST_FILE.read_text(encoding="utf-8").strip() != date.today().isoformat()
    except Exception:
        return True


def mark_done() -> None:
    LAST_FILE.parent.mkdir(parents=True, exist_ok=True)
    LAST_FILE.write_text(date.today().isoformat(), encoding="utf-8")


def collect(missed: list[str] | None = None) -> str:
    """Собрать факты для сводки (мозг потом расскажет их своими словами)."""
    from . import hands, web
    facts = []
    if config.BRIEFING_WEATHER and config.WEB_ENABLED:
        try:
            facts.append("Погода: " + web.weather(""))
        except Exception:
            pass
    if config.BRIEFING_REMINDERS:
        today = hands.reminders_today()
        if today:
            facts.append("Напоминания на сегодня: " + "; ".join(today))
        if missed:
            facts.append("Пропущенные напоминания (комп был выключен): " + "; ".join(missed))
    if config.BRIEFING_BATTERY and battery():   # неизвестно — в сводке просто не упоминаем
        facts.append(battery_text(with_keyboard_note=False))   # в утренней сводке про клавиатуру не напоминаем
    return "\n".join(facts)
