"""
ИГРЫ: Мартышка сама придумывает план игры, пишет код, запускает его в невидимом браузере, ловит ошибки,
исправляет и только потом отдаёт брату.

Технология по умолчанию — HTML5 + JavaScript в ОДНОМ файле: открывается в любом браузере, ничего ставить не надо,
можно скинуть другу. Игры лежат в «Документы → Игры Мартышки».

Шаги:
  1) план: жанр, цель, управление, механики, как выглядит, победа/поражение, на чём делаем;
  2) код по плану (большая «рабочая память» мозга на время написания);
  3) проверка: невидимый браузер запускает игру, жмёт клавиши и мышку, ловит ошибки и пустой экран;
  4) нашлась ошибка — мозг исправляет (до 3 раз);
  5) готовую игру открываем у брата; «доработай игру: …» — меняем последнюю игру тем же способом.
"""
import json
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path

from . import config

GAMES_DIR = Path.home() / "Documents" / "Игры Мартышки"
BIG_CTX = 12288          # на время написания игры мозгу нужна большая «рабочая память»
last_game: Path | None = None
_llm_raw = None          # (system, prompt, max_tokens, num_ctx) -> текст; задаёт главная программа


def set_llm(fn) -> None:
    global _llm_raw
    _llm_raw = fn


def _ask(prompt: str, max_tokens: int, system: str = "Ты опытный разработчик браузерных игр. Пиши аккуратно и "
                                                          "только то, что просят.") -> str:
    if _llm_raw is None:
        raise RuntimeError("мозг не подключён")
    return _llm_raw(system, prompt, max_tokens, BIG_CTX)


# ---------- 1. план ----------
PLAN_PROMPT = """Брат хочет игру: «{desc}».
Составь КОРОТКИЙ план игры по-русски (8–12 строк), строго по пунктам:
Название: …
Жанр и цель: …
Управление: … (клавиатура; если уместно — мышь/тач)
Механики: … (3–6 главных, только то, что реально сделать в одном файле)
Как выглядит: … (рисуем фигурами и цветом на canvas, без картинок из интернета)
Победа и поражение: …
Счёт и уровни: …
Технология: HTML5 Canvas + JavaScript, один файл, без библиотек.
Делай игру простой, но интересной и законченной. Ничего лишнего, без markdown."""


def plan(desc: str) -> str:
    return _ask(PLAN_PROMPT.format(desc=desc), 500).strip()


# ---------- 2. код ----------
CODE_PROMPT = """Напиши игру по этому плану:
{plan}

Требования к коду (ОБЯЗАТЕЛЬНО):
- Один HTML-файл: <!DOCTYPE html>, <style> и <script> внутри. Никаких внешних файлов, библиотек и картинок.
- Холст <canvas> на всё окно, подстраивается под размер окна (resize). Тёмный фон, яркие понятные цвета.
- Игровой цикл через requestAnimationFrame с учётом времени кадра (dt), чтобы скорость не зависела от монитора.
- Экран старта: название и «Нажми ПРОБЕЛ или кликни, чтобы начать». Экран проигрыша/победы со счётом и
  «R — заново». Пауза на P. Счёт и рекорд (localStorage, в try/catch) в углу.
- Управление стрелками и WASD (если подходит), пробел; event.preventDefault() для стрелок и пробела.
- Все надписи в игре — по-русски.
- Простые звуки через Web Audio (короткие «бипы»), создавай AudioContext только после первого нажатия.
- Код аккуратный, без ошибок, все переменные объявлены (let/const), не больше ~350 строк.
Ответь ТОЛЬКО кодом в одном блоке ```html ... ```."""


SKELETON = Path(__file__).with_name("game_skeleton.html")
GAME_START = "// ===================== ИГРА (пишется под каждую игру) ====================="
GAME_END = "// ===================== ЗАПУСК ====================="

GAME_PROMPT = """Напиши браузерную игру по этому плану:
{plan}

Игра вставляется в ГОТОВЫЙ каркас. Каркас уже делает: холст на всё окно (W, H — размеры, ctx — рисование),
игровой цикл, экраны «Старт / Пауза / Конец игры», счёт и рекорд, клавиши, мышь, звук. Тебе нужно написать ТОЛЬКО
игровую часть на JavaScript:

const TITLE = 'Название игры', HELP = 'Подсказка про управление (по-русски)', BG = '#цвет фона', ACCENT = '#яркий цвет';
// свои переменные игры (let …)
function initGame() {{ … }}            // создать/сбросить мир: игрок, враги, уровень (вызывается при каждом старте)
function updateGame(dt) {{ … }}        // логика за кадр. dt — В СЕКУНДАХ (~0.016). Скорости — в пикселях В СЕКУНДУ.
function drawGame() {{ … }}            // нарисовать мир (фон и счёт каркас рисует сам)

Что уже есть в каркасе (используй, НЕ объявляй заново):
- keys['ArrowLeft'|'ArrowRight'|'ArrowUp'|'ArrowDown'|'KeyA'|'KeyD'|'KeyW'|'KeyS'|'Space'] — зажата ли клавиша;
  pressed[код] — нажата в этом кадре (для прыжка/выстрела); mouse.x, mouse.y, mouse.down, mouse.clicked
- score (прибавляй сам), gameOver('текст') — закончить игру (проигрыш или 'Победа!')
- beep(частота, длительность) — звук; rand(a, b), clamp(v, a, b), dist(a, b), hit(a, b) — столкновение
  прямоугольников {{x, y, w, h}}; text(строка, x, y, размер, цвет, выравнивание)
Правила: всё объявлено через let/const; объекты появляются СРАЗУ в initGame; движение через dt; всё в пределах
экрана (W, H); надписи по-русски; рисуй фигурами и цветом (fillRect, arc, fillText), без картинок; не больше ~200 строк.
Скорости живые: змейка/сетка — 8–10 шагов в секунду, игрок 250–400 пикс/с, враги 100–300 пикс/с, гравитация
~1500 пикс/с², прыжок ~-650 пикс/с. Рисуй ТОЛЬКО в drawGame (в updateGame ничего не рисуй).
НЕ объявляй startGame, gameOver, state, score и свои обработчики старта: старт по пробелу/клику, паузу и рестарт
делает каркас (он сам вызовет initGame). Счёт и рекорд каркас рисует сам слева вверху — НЕ рисуй их. Свои надписи (жизни, уровень, время) — справа вверху:
text('Жизни: ' + lives, W - 16, 34, 20, '#fff', 'right').
Ответь ТОЛЬКО кодом игровой части в одном блоке ```javascript ... ```."""


def _extract_js(text: str) -> str:
    m = re.search(r"```(?:javascript|js)?\s*(.*?)```", text, re.S | re.I)
    js = (m.group(1) if m else text).strip()
    # если мозг всё-таки прислал целый HTML — достаём из него скрипт
    if "<script" in js.lower():
        s = re.search(r"<script[^>]*>(.*?)</script>", js, re.S | re.I)
        js = s.group(1).strip() if s else js
    return js


RESERVED = ("startGame", "gameOver", "beep", "rand", "clamp", "hit", "dist", "text", "frame", "resize")


def _sanitize(game_js: str) -> str:
    """Мозг иногда объявляет заново функции каркаса (startGame, gameOver…) — его объявление «перебивает» каркас,
    и игра не стартует. Переименовываем ТОЛЬКО объявления; вызовы остаются и идут в надёжные функции каркаса."""
    for name in RESERVED:
        game_js = re.sub(rf"\bfunction\s+{name}\s*\(", f"function __own_{name}(", game_js)
        game_js = re.sub(rf"\b(const|let|var)\s+{name}\s*=", rf"\1 __own_{name} =", game_js)
    # повторное объявление переменных каркаса (let score = 0; let state …) → просто присваивание
    game_js = re.sub(r"\b(let|var|const)\s+(score|state|best|keys|pressed|mouse|W|H|ctx|canvas)\s*=", r"\2 =", game_js)
    return game_js


def _assemble(game_js: str, title: str) -> str:
    game_js = _sanitize(game_js)
    sk = SKELETON.read_text(encoding="utf-8")
    gid = re.sub(r"\W+", "_", title.lower())[:30] or "game"
    return sk.replace("__GAME__", game_js).replace("__TITLE__", title).replace("__ID__", gid)


def _game_part(html: str) -> str | None:
    """Игровая часть из собранного файла (между метками каркаса)."""
    if GAME_START in html and GAME_END in html:
        return html.split(GAME_START, 1)[1].split(GAME_END, 1)[0].strip()
    return None


def _game_line(html: str, line: int) -> int:
    """Номер строки ошибки в файле → номер строки внутри игровой части."""
    before = html.split(GAME_START, 1)[0]
    return line - before.count("\n") - 1


def _extract_html(text: str) -> str:
    m = re.search(r"```(?:html)?\s*(.*?)```", text, re.S | re.I)
    html = (m.group(1) if m else text).strip()
    if "<html" not in html.lower() and "<canvas" in html.lower():
        html = f"<!DOCTYPE html><html><head><meta charset='utf-8'></head><body>{html}</body></html>"
    return html


def write_code(desc: str, game_plan: str, title: str = "Игра") -> str:
    """Игровая часть в готовый каркас (надёжнее, чем каждый раз писать всю игру с нуля)."""
    if SKELETON.exists():
        return _assemble(_extract_js(_ask(GAME_PROMPT.format(plan=game_plan), 4500)), title)
    return _extract_html(_ask(CODE_PROMPT.format(plan=game_plan), 7000))


# ---------- 3. проверка в невидимом браузере ----------
CATCH = ("<script>window.__errs=[];window.onerror=function(m,s,l,c,e){window.__errs.push(m+' (строка '+l+')');"
         "document.title='ERR:'+window.__errs[0];};window.addEventListener('unhandledrejection',function(e){"
         "window.__errs.push('Promise: '+e.reason);document.title='ERR:'+window.__errs[0];});</script>")
# «играем» за брата: старт, стрелки, WASD, пробел, клики — чтобы ошибки в обработчиках тоже вылезли
PLAY = """<script>(function(){var keys=[' ','ArrowRight','ArrowRight','ArrowUp','ArrowRight','d','ArrowDown','ArrowLeft','w','Enter',' ','ArrowUp'];
var codes={' ':'Space','ArrowRight':'ArrowRight','ArrowLeft':'ArrowLeft','ArrowUp':'ArrowUp','ArrowDown':'ArrowDown',
'w':'KeyW','a':'KeyA','s':'KeyS','d':'KeyD','Enter':'Enter','r':'KeyR','p':'KeyP'};var i=0;
function k(t,key){var e=new KeyboardEvent(t,{key:key,code:codes[key],keyCode:key===' '?32:key.length==1?key.toUpperCase().charCodeAt(0):
({ArrowLeft:37,ArrowUp:38,ArrowRight:39,ArrowDown:40,Enter:13})[key],bubbles:true,cancelable:true});
(document.body||document).dispatchEvent(e);}
function c(t){var cv=document.querySelector('canvas')||document.body;if(!cv)return;var r=cv.getBoundingClientRect();
var e=new MouseEvent(t,{clientX:r.left+r.width/2,clientY:r.top+r.height/2,bubbles:true});cv.dispatchEvent(e);}
var timer=setInterval(function(){try{var key=keys[i%keys.length];k('keydown',key);setTimeout(function(){k('keyup',key)},60);
if(i%4==0){c('mousedown');c('click');c('mouseup');}}catch(e){}i++;if(i>60)clearInterval(timer);},90);
setTimeout(function(){if(!window.__errs.length)document.title='OK';},5500);})();</script>"""


def _browser() -> str | None:
    for p in (r"C:\Program Files\Google\Chrome\Application\chrome.exe",
              r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
              r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"):
        if Path(p).exists():
            return p
    return None


def test(html: str) -> dict:
    """Запустить игру в невидимом браузере. → {ok, error, blank}"""
    exe = _browser()
    if not exe:
        return {"ok": True, "error": "", "blank": False, "skipped": True}
    # ловушку ошибок ставим В ТУ ЖЕ строку, что <head>, — номера строк в ошибках совпадут с файлом
    if re.search(r"<head[^>]*>", html, re.I):
        probe = re.sub(r"(<head[^>]*>)", lambda m: m.group(1) + CATCH, html, count=1, flags=re.I)
    else:
        probe = CATCH + html
    probe = re.sub(r"</body>", PLAY + "</body>", probe, count=1, flags=re.I) if re.search(r"</body>", probe, re.I) \
        else probe + PLAY
    tmp = Path(tempfile.mkdtemp(prefix="martyshka_game_"))
    page, shot = tmp / "game.html", tmp / "shot.png"
    page.write_text(probe, encoding="utf-8")
    url = "file:///" + str(page).replace("\\", "/")
    common = [exe, "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check", "--mute-audio",
              f"--user-data-dir={tmp / 'profile'}", "--window-size=1000,700", "--virtual-time-budget=6500"]
    try:
        dom = subprocess.run(common + ["--dump-dom", url], capture_output=True, timeout=60,
                             creationflags=0x08000000).stdout.decode("utf-8", "replace")
        subprocess.run(common + [f"--screenshot={shot}", url], capture_output=True, timeout=60,
                       creationflags=0x08000000)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "игра зависла (бесконечный цикл?)", "blank": False}
    title = re.search(r"<title>(.*?)</title>", dom, re.S)
    title = (title.group(1) if title else "").strip()
    error = title[4:] if title.startswith("ERR:") else ""
    blank = False
    try:
        from PIL import Image
        im = Image.open(shot).convert("L").resize((100, 70))
        px = list(im.getdata())
        blank = max(px) - min(px) < 12          # экран одного цвета — ничего не нарисовано
    except Exception:
        pass
    return {"ok": not error and not blank, "error": error, "blank": blank}


# ---------- 4. исправление ----------
FIX_PROMPT = """Вот код браузерной игры:
```html
{code}
```
Проблема при запуске: {problem}
Найди причину и исправь. Сохрани всю игру и всё, что работает. Ответь ТОЛЬКО полным исправленным кодом в
одном блоке ```html ... ```."""


def fix(html: str, problem: str) -> str:
    part = _game_part(html)
    if part is not None:                                    # игра на каркасе — чиним только игровую часть
        m = re.search(r"строка\s+(\d+)", problem)
        if m:
            problem = re.sub(r"строка\s+\d+", f"строка {_game_line(html, int(m.group(1)))} игровой части", problem)
        new = _extract_js(_ask(
            f"Вот игровая часть браузерной игры (вставляется в каркас; в каркасе уже есть keys, pressed, mouse, score, "
            f"gameOver, beep, rand, clamp, dist, hit, text, W, H, ctx — их НЕ объявляй):\n```javascript\n{part}\n```\n"
            f"Проблема при запуске: {problem}\nНайди причину и исправь. Сохрани всё, что работает. Ответь ТОЛЬКО "
            "полной исправленной игровой частью в одном блоке ```javascript ... ```.", 4500))
        if len(new) < len(part) * 0.5:
            return html
        return html.replace(part, _sanitize(new))
    out = _extract_html(_ask(FIX_PROMPT.format(code=html, problem=problem), 7000))
    return out if len(out) > len(html) * 0.5 else html      # мозг вернул огрызок — оставляем как было


def _problem_text(r: dict) -> str:
    if r.get("error"):
        return f"ошибка JavaScript: {r['error']}"
    if r.get("blank"):
        return "экран остаётся пустым — на холсте ничего не рисуется (проверь отрисовку, размер canvas, цикл)"
    return ""


def _build(html: str, say) -> tuple[str, dict, int]:
    """Проверить и починить (до 3 раз). → (код, итог проверки, сколько раз чинили)"""
    r = test(html)
    fixes = 0
    while not r["ok"] and fixes < 3:
        fixes += 1
        say(f"Нашёл проблему: {_problem_text(r)}. Исправляю (попытка {fixes})…")
        html = fix(html, _problem_text(r))
        r = test(html)
    return html, r, fixes


def _shots(html: str) -> list:
    """Снимки игры в невидимом браузере: в начале и через ~3 секунды «игры» (с нажатиями)."""
    from PIL import Image
    exe = _browser()
    if not exe:
        return []
    probe = re.sub(r"(<head[^>]*>)", lambda m: m.group(1) + CATCH, html, count=1, flags=re.I)
    probe = re.sub(r"</body>", PLAY + "</body>", probe, count=1, flags=re.I) if re.search(r"</body>", probe, re.I) \
        else probe + PLAY
    tmp = Path(tempfile.mkdtemp(prefix="martyshka_shot_"))
    page = tmp / "g.html"
    page.write_text(probe, encoding="utf-8")
    out = []
    for budget in (400, 1200, 3000):
        shot = tmp / f"s{budget}.png"
        try:
            subprocess.run([exe, "--headless=new", "--disable-gpu", "--mute-audio", f"--user-data-dir={tmp / 'p'}",
                            "--window-size=900,620", f"--virtual-time-budget={budget}", f"--screenshot={shot}",
                            "file:///" + str(page).replace("\\", "/")], capture_output=True, timeout=60,
                           creationflags=0x08000000)
            out.append(Image.open(shot).convert("RGB"))
        except Exception:
            pass
    return out


def review(html: str, game_plan: str) -> str:
    """Посмотреть на запущенную игру глазами и сравнить с планом. → «» если всё хорошо, иначе список проблем."""
    try:
        from PIL import ImageChops
        from . import vision
        shots = _shots(html)
        if len(shots) < 2:
            return ""
        problems = []
        # точная проверка без мозга: меняется ли картинка во время игры (между любыми соседними снимками —
        # бот играет плохо, проигрывает, и к 3-й секунде игра может начаться заново и выглядеть «как в начале»)
        small = [s.convert("L").resize((450, 310)) for s in shots]
        moved = max(sum(1 for v in ImageChops.difference(a, b).getdata() if v > 25) / (450 * 310)
                    for a, b in zip(small, small[1:]))
        if moved < 0.0002:                                   # ~30 точек — заметно даже маленькой змейке
            problems.append("за 3 секунды игры на экране ничего не поменялось — игра не идёт или не реагирует "
                            "на клавиши (проверь старт по пробелу/клику, игровой цикл и управление)")
        # (проверку «глазами» убрал: мозг выдумывал проблемы — «нет врагов» там, где их и не должно быть)
        return "\n".join(problems)
    except Exception:
        return ""


def polish(html: str, game_plan: str, say) -> str:
    """Один раунд доводки по замечаниям глаз (если они есть)."""
    problems = review(html, game_plan)
    if not problems:
        return html
    say("Посмотрел на игру — доделываю: " + problems.replace("\n", "; ")[:300])
    better = fix(html, problems)
    if better == html:
        return html
    r = test(better)
    return better if r["ok"] else html          # доводка сломала игру — оставляем рабочую версию


def _save(html: str, name: str, game_plan: str = "") -> Path:
    global last_game
    GAMES_DIR.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^\w\- ]+", "", name, flags=re.U).strip()[:50] or "Игра"
    path = GAMES_DIR / f"{safe}.html"
    n = 2
    while path.exists() and path != last_game:
        path = GAMES_DIR / f"{safe} {n}.html"
        n += 1
    path.write_text(html, encoding="utf-8")
    if game_plan:
        path.with_suffix(".план.txt").write_text(game_plan, encoding="utf-8")
    last_game = path
    try:
        from .hands import remember_file
        remember_file(path)
    except Exception:
        pass
    return path


def make(desc: str, say=None) -> tuple[bool, str, Path | None]:
    """Создать игру. say(текст) — сообщать брату ход работы. → (успех, итог, файл)"""
    say = say or (lambda _t: None)
    t0 = time.time()
    say("Продумываю игру…")
    game_plan = plan(desc)
    say("План игры:\n" + game_plan)
    name = (re.search(r"Название\s*:\s*(.+)", game_plan) or re.search(r"(.+)", desc)).group(1).strip(" «»\"'.")
    say("Пишу код…")
    html = write_code(desc, game_plan, name)
    say("Запускаю и проверяю…")
    html, r, fixes = _build(html, say)
    if r["ok"]:
        html = polish(html, game_plan, say)          # смотрю на игру глазами и доделываю, чего не хватает
    path = _save(html, name, game_plan)
    os.startfile(str(path))
    took = int(time.time() - t0)
    if r["ok"]:
        extra = f", исправил {fixes} ошибк{'у' if fixes == 1 else 'и'}" if fixes else ""
        return True, f"Игра «{name}» готова за {took} с{extra}, проверил — запускается без ошибок. Открыл её.", path
    return False, (f"Игру «{name}» написал и открыл, но проверка ещё ругается: {_problem_text(r)}. "
                   "Скажи «доработай игру» — попробую ещё раз."), path


def improve(change: str, say=None) -> tuple[bool, str, Path | None]:
    """Доработать последнюю игру: «добавь врагов», «сделай быстрее», «исправь, что не работает прыжок»."""
    global last_game
    say = say or (lambda _t: None)
    path = last_game
    if path is None or not path.exists():
        files = sorted(GAMES_DIR.glob("*.html"), key=lambda p: p.stat().st_mtime) if GAMES_DIR.exists() else []
        path = files[-1] if files else None
    if path is None:
        return False, "Пока нет игры, которую можно доработать. Скажи, какую игру сделать.", None
    html = path.read_text(encoding="utf-8")
    say(f"Дорабатываю «{path.stem}»: {change}…")
    part = _game_part(html)
    if part is not None:                                    # игра на каркасе — меняем только игровую часть
        js = _extract_js(_ask(
            f"Вот игровая часть браузерной игры (в каркасе уже есть keys, pressed, mouse, score, gameOver, beep, rand, "
            f"clamp, dist, hit, text, W, H, ctx — их НЕ объявляй):\n```javascript\n{part}\n```\nБрат просит изменить: "
            f"«{change}». Внеси изменения аккуратно, сохрани всё остальное, надписи по-русски, движение через dt (секунды). "
            "Ответь ТОЛЬКО полной новой игровой частью в одном блоке ```javascript ... ```.", 4500))
        new = html.replace(part, _sanitize(js)) if len(js) > len(part) * 0.5 else ""
    else:
        new = _extract_html(_ask(f"Вот код браузерной игры:\n```html\n{html}\n```\nБрат просит изменить: «{change}».\n"
                                 "Внеси изменения аккуратно, сохрани всё остальное, все надписи по-русски. Ответь ТОЛЬКО "
                                 "полным новым кодом в одном блоке ```html ... ```.", 7000))
    if len(new) < len(html) * 0.5:
        return False, "Не получилось аккуратно доработать — мозг вернул обрывок кода. Попробуй сформулировать проще.", path
    new, r, fixes = _build(new, say)
    last_game = path
    path.write_text(new, encoding="utf-8")
    os.startfile(str(path))
    if r["ok"]:
        return True, f"Доработал «{path.stem}» и проверил — работает. Открыл.", path
    return False, f"Доработал «{path.stem}», но проверка ругается: {_problem_text(r)}.", path
