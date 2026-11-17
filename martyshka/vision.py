"""
ГЛАЗА И РУКИ НА ЭКРАНЕ.

Глаза: снимок экрана → мозг gemma4 (он умеет смотреть на картинки) отвечает, что видит: «что у меня на экране?»,
«что за ошибка?», «прочитай, что тут написано».

Мышка: «нажми Сохранить» — сначала спрашиваем саму Windows (служба специальных возможностей знает кнопки обычных
программ точно), а если не нашли (игры, программы-сайты) — глазами: мозг показывает место на снимке, потом
приближаем этот кусок и уточняем. Клавиатура: «нажми ctrl+s», «enter», «alt+tab».
"""
import ctypes
import ctypes.wintypes
import difflib
import io
import json
import re
import time

from . import config

u32 = ctypes.windll.user32


# ---------- снимок экрана ----------
def _virtual_origin():
    return u32.GetSystemMetrics(76), u32.GetSystemMetrics(77), u32.GetSystemMetrics(78) or 1


def grab(max_w: int = 1280, region=None):
    """Снимок (всех экранов или region в логических координатах). Возвращает (PIL-картинка, функция «доли→экран»)."""
    from PIL import ImageGrab
    full = ImageGrab.grab(all_screens=True)
    vx, vy, vw = _virtual_origin()
    k = full.width / vw                                   # физические пиксели на логическую точку
    if region:
        l, t, r, b = region
        img = full.crop((int((l - vx) * k), int((t - vy) * k), int((r - vx) * k), int((b - vy) * k)))
        ox, oy, lw, lh = l, t, r - l, b - t
    else:
        img = full
        ox, oy, lw, lh = vx, vy, vw, full.height / k
    if img.width > max_w:
        img = img.resize((max_w, int(img.height * max_w / img.width)))

    def to_screen(fx: float, fy: float):              # доли 0..1 → логические координаты экрана
        return int(ox + fx * lw), int(oy + fy * lh)
    return img, to_screen


def _png(img) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def _see(prompt: str, img, max_tokens: int = 400, temperature: float = 0.2) -> str:
    import ollama
    extra = {"think": False} if config.MODEL.lower().startswith(("qwen3", "gemma4")) else {}
    imgs = img if isinstance(img, list) else [img]
    r = ollama.Client().chat(model=config.MODEL, keep_alive=config.KEEP_ALIVE, **extra,
                             messages=[{"role": "user", "content": prompt, "images": [_png(i) for i in imgs]}],
                             options={"num_ctx": config.CONTEXT_SIZE, "temperature": temperature,
                                      "num_predict": max_tokens})
    return r["message"]["content"].strip()


def look(question: str = "") -> str:
    """Ответ про то, что сейчас на экране: весь экран + крупно активное окно + список окон (чтобы не фантазировал)."""
    from . import hands
    wins = hands.list_windows()
    full, _ = grab(1024)
    images = [full]
    active = next((w for w in wins if w["active"] and not w["minimized"]), None)
    if active:
        l, t, r, b = active["rect"]
        if (r - l) * (b - t) < 0.9 * u32.GetSystemMetrics(0) * u32.GetSystemMetrics(1):
            crop, _ = grab(1280, (max(l, 0), max(t, 0), r, b))
            images.append(crop)
    names = "; ".join(f"«{w['title'] or w['exe']}» ({w['exe']}{', активное' if w['active'] else ''}"
                      f"{', почти чёрное' if w.get('dark') else ''}{', свёрнуто' if w['minimized'] else ''})"
                      for w in wins[:10])
    q = question.strip() or "Что сейчас на экране?"
    return _see(f"Это снимки экрана компьютера брата на Windows 11 (первый — весь экран"
                f"{', второй — крупно активное окно' if len(images) > 1 else ''}). Открытые окна по данным Windows: "
                f"{names or 'нет'}.\nВопрос брата: {q}\nОтвечай по-русски, коротко и по делу, без markdown и списков: "
                "что видно, что написано (ошибки — дословно). Опирайся на снимки и список окон, не выдумывай. "
                "Если окно почти чёрное или пустое — так и скажи и предположи почему.", images, 350)


# ---------- мышка ----------
def _click_at(x: int, y: int, button: str = "left", double: bool = False) -> None:
    u32.SetCursorPos(int(x), int(y))
    time.sleep(0.05)
    down, up = (0x0008, 0x0010) if button == "right" else (0x0002, 0x0004)
    for _ in range(2 if double else 1):
        u32.mouse_event(down, 0, 0, 0, 0)
        time.sleep(0.03)
        u32.mouse_event(up, 0, 0, 0, 0)
        time.sleep(0.08)


def _uia_find(target: str):
    """Найти элемент по названию через службу специальных возможностей Windows. → (x, y, имя) или None."""
    try:
        import comtypes
        import comtypes.client
        comtypes.CoInitialize()
        comtypes.client.GetModule("UIAutomationCore.dll")
        from comtypes.gen.UIAutomationClient import CUIAutomation, IUIAutomation
        a = comtypes.client.CreateObject(CUIAutomation, interface=IUIAutomation)
        fg = u32.GetForegroundWindow()
        roots = [a.ElementFromHandle(fg)] if fg else []
        t = target.lower().strip(" «»\"'")
        best, score = None, 0.0
        for root in roots:
            items = root.FindAll(4, a.CreateTrueCondition())
            for i in range(min(items.Length, 3000)):
                e = items.GetElement(i)
                name = (e.CurrentName or "").strip()
                if not name or e.CurrentIsOffscreen:
                    continue
                n = name.lower()
                s = 1.0 if n == t else 0.9 if (t in n and len(t) >= 3) else difflib.SequenceMatcher(None, t, n).ratio()
                if s > score:
                    r = e.CurrentBoundingRectangle
                    if r.right - r.left > 2 and r.bottom - r.top > 2:
                        best, score = ((r.left + r.right) // 2, (r.top + r.bottom) // 2, name), s
        return best if score >= 0.75 else None
    except Exception:
        return None


def _parse_xy(text: str):
    m = re.search(r"\{[^{}]*\}", text)
    if m:
        try:
            d = json.loads(m.group(0))
            return float(d["x"]) / 1000, float(d["y"]) / 1000
        except Exception:
            pass
    nums = re.findall(r"\d+(?:\.\d+)?", text)
    if len(nums) >= 2:
        return float(nums[0]) / 1000, float(nums[1]) / 1000
    return None


def _point(ask: str, img):
    """Спросить у мозга точку на картинке. Картинку дополняем до КВАДРАТА: «глаз» мозга видит квадрат, и на
    вытянутом снимке координаты по вертикали съезжают (проверено: без этого 0–1 попадание из 4, с этим 3 из 4)."""
    from PIL import Image
    s = max(img.size)
    sq = Image.new("RGB", (s, s), (0, 0, 0))
    sq.paste(img.convert("RGB"), (0, 0))
    p = _parse_xy(_see(ask, sq, 60, 0))
    if not p or p[0] < 0 or p[1] < 0:
        return None
    fx, fy = p[0] * s / img.width, p[1] * s / img.height
    return (fx, fy) if 0 <= fx <= 1 and 0 <= fy <= 1 else None


def _vision_find(target: str):
    """Найти место на экране глазами: сначала грубо, потом дважды приближаем и уточняем. → (x, y) или None."""
    ask = (f'Найди на снимке КНОПКУ или элемент с надписью «{target}» — то, на что нажимают (не обычный текст и не '
           'поле ввода). Ответь ТОЛЬКО JSON {"x": .., "y": ..} — центр этого элемента в долях от 0 до 1000 по ширине '
           'и высоте снимка. Если такого нет — {"x": -1, "y": -1}.')
    img, to_screen = grab(1280)
    p = _point(ask, img)
    if not p:
        return None
    x, y = to_screen(*p)
    for half in (220, 110):                       # два приближения: квадраты 440 и 220 точек вокруг найденного
        img2, to_screen2 = grab(880, (x - half, y - half, x + half, y + half))
        p2 = _point(ask, img2)
        if not p2:
            break
        x, y = to_screen2(*p2)
    return x, y


def click(target: str) -> str:
    """«Сохранить», «правой кнопкой по корзине», «дважды по Steam». Возвращает, что сделали."""
    t = target.strip()
    button = "right" if re.search(r"прав\w*\s+кноп|правой", t, re.I) else "left"
    double = bool(re.search(r"дважды|двойн|два\s+раза", t, re.I))
    t = re.sub(r"(правой\s+кнопкой(\s+мыши)?|дважды|двойным\s+кликом|два\s+раза|\bна\s+кнопк\w*|\bкнопк\w*|"
               r"\bпункт\w*|\bзначок|\bиконк\w*|\bссылк\w*|\bпо\s+)", " ", t, flags=re.I)
    t = re.sub(r"\s+", " ", t).strip(" «»\"'")
    hit = _uia_find(t)
    if hit:
        _click_at(hit[0], hit[1], button, double)
        return f"Нажал «{hit[2]}»."
    xy = _vision_find(t)
    if xy:
        _click_at(xy[0], xy[1], button, double)
        return f"Нажал «{t}» в точке {xy[0]},{xy[1]} (нашёл глазами — проверь по экрану, попал ли)."
    raise LookupError(f"Не нашёл на экране «{t}».")


# ---------- клавиатура ----------
KEYS = {"ctrl": 0x11, "контрол": 0x11, "shift": 0x10, "шифт": 0x10, "alt": 0x12, "альт": 0x12, "win": 0x5B,
        "вин": 0x5B, "enter": 0x0D, "энтер": 0x0D, "ввод": 0x0D, "esc": 0x1B, "escape": 0x1B, "эскейп": 0x1B,
        "tab": 0x09, "таб": 0x09, "space": 0x20, "пробел": 0x20, "backspace": 0x08, "delete": 0x2E, "del": 0x2E,
        "up": 0x26, "вверх": 0x26, "down": 0x28, "вниз": 0x28, "left": 0x25, "влево": 0x25, "right": 0x27,
        "вправо": 0x27, "home": 0x24, "end": 0x23, "pageup": 0x21, "pagedown": 0x22, "printscreen": 0x2C,
        **{f"f{i}": 0x6F + i for i in range(1, 13)}}


def hotkey(combo: str) -> str:
    """«ctrl+s», «alt+f4», «enter», «ctrl+shift+esc»."""
    parts = [p.strip().lower() for p in re.split(r"[+\s]+", combo) if p.strip()]
    codes = []
    for p in parts:
        if p in KEYS:
            codes.append(KEYS[p])
        elif len(p) == 1 and p.isascii() and p.isalnum():
            codes.append(ord(p.upper()))
        else:
            raise ValueError(f"Не знаю клавишу «{p}».")
    from .hands import _press   # с аппаратными кодами — чтобы понимали и игры в браузере
    _press(*codes)
    return f"Нажал {combo}."
