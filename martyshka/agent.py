"""
РЕЖИМ АГЕНТА: сложная задача → Мартышка сама делает шаг, смотрит на экран (снимок идёт мозгу), проверяет результат,
исправляется и идёт дальше. До 10 шагов. Опасное (удаление, команды Windows, выключение) — всегда с вопросом брату.

И КОМАНДЫ WINDOWS: мозг пишет команду PowerShell, брату показываем её целиком, выполняем только после «да».
"""
import re
import subprocess
import time

from . import config, style

# ---------- команды Windows ----------
FORBIDDEN = re.compile(
    r"(format-volume|format\s+[a-z]:|diskpart|bcdedit|cipher\s+/w|clear-disk|initialize-disk|remove-partition|"
    r"reg\s+delete\s+hklm|remove-item\s+.*(c:\\\\?\s*$|c:\\windows|c:\\program\s*files|\$env:windir|-path\s+c:\\?\s)|"
    r"rd\s+/s\s+/q\s+c:\\|del\s+/[sfq].*c:\\(windows|program)|stop-computer|restart-computer|shutdown|"
    r"set-executionpolicy|disable-windowsdefender|set-mppreference|netsh\s+advfirewall\s+set|"
    r"invoke-webrequest.*\|\s*iex|iwr.*\|\s*iex|downloadstring|-enc(odedcommand)?\s)", re.IGNORECASE)


def check_command(cmd: str) -> str | None:
    """None — можно предложить брату; иначе — почему нельзя."""
    if FORBIDDEN.search(cmd):
        return "Эту команду я выполнять не стану: она может сломать систему или отключить защиту."
    if len(cmd) > 1500:
        return "Слишком длинная команда."
    return None


def run_command(cmd: str, timeout: int = 90) -> str:
    """Выполнить команду PowerShell (уже подтверждённую братом). Возвращает вывод (обрезанный)."""
    r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command",
                        "[Console]::OutputEncoding=[Text.Encoding]::UTF8; " + cmd],
                       capture_output=True, timeout=timeout, creationflags=0x08000000)   # без чёрного окна
    out = (r.stdout or b"").decode("utf-8", "replace").strip()
    err = (r.stderr or b"").decode("utf-8", "replace").strip()
    text = out[-2500:] if out else ""
    if err:
        text += ("\n" if text else "") + "ОШИБКА: " + err[-800:]
    return text or ("Готово (команда ничего не вывела)." if r.returncode == 0 else f"Код ошибки {r.returncode}.")


# ---------- агент ----------
AGENT_RULES = """Ты — {name}, исполнитель задач на компьютере Windows брата. Тебе дали ЗАДАЧУ. Ты выполняешь её по шагам.
На каждом шаге тебе приходят: задача, что уже сделано и с каким результатом, список окон и СНИМОК ЭКРАНА.
Ответ на каждом шаге — строго в таком виде:
Мысль: <одна короткая фраза: что видишь и что дальше>
[ДЕЛО: ...]   ← 1–3 метки дел для этого шага
ИЛИ, если задача уже выполнена: [ГОТОВО: итог для брата]
ИЛИ, если без брата не решить: [ВОПРОС: что нужно уточнить]
ИЛИ, если после нескольких попыток не выходит: [НЕ ВЫШЛО: почему]

Дела:
{actions}
  [ДЕЛО: нажать | <что нажать на экране: название кнопки/пункта/значка; можно «правой кнопкой по …», «дважды по …»>]
  [ДЕЛО: клавиши | <сочетание: ctrl+s / alt+f4 / enter / esc / ctrl+shift+esc / win>]
  [ДЕЛО: команда | <одна команда PowerShell>]  — для файлов, папок, установки (winget), сведений о системе
  [ДЕЛО: подождать | <секунд, до 15>]
Правила:
- Смотри на снимок и результаты: проверяй, получилось ли прошлое действие. Не получилось — попробуй ДРУГОЙ способ.
- Одно и то же действие больше 2 раз подряд не повторяй.
- Программы открывай делом «открыть», печатай делом «напечатать», кнопки — «нажать», меню — «клавиши».
- Удаление, команды и выключение программа сама согласует с братом — просто ставь метку.
- Не делай ничего, о чём брат не просил. Как только на снимке видно, что цель достигнута (нужная надпись появилась,
  программа открыта, файл создан) — СРАЗУ [ГОТОВО: …], лишних нажатий не делай.
"""

STOP_RE = re.compile(r"\[\s*(ГОТОВО|ВОПРОС|НЕ\s*ВЫШЛО)\s*:\s*(.+?)\s*\]", re.IGNORECASE | re.DOTALL)
THOUGHT_RE = re.compile(r"Мысль\s*:\s*(.+)", re.IGNORECASE)


def run_task(goal: str, confirm_fn, progress=None, max_steps: int = 10) -> str:
    """Выполнить задачу по шагам. progress(текст) — сообщать брату ход работы. Возвращает итог."""
    from . import deepseek, hands, vision
    from .hands import ACTION_RE, actions_help, parse_action
    say = progress or (lambda _t: None)
    # быстрый путь: «перейти в настройки кс», «открыть загрузки стима» — прямой ссылкой, без кликов
    try:
        from . import goto
        short = re.split(r"\s+и\s+(открыть|открой|показать|покажи)\b.*$", goal, flags=re.I)[0]
        said = goto.goto(short)
        if said:
            return said
    except Exception:
        pass
    system = style.adapt(AGENT_RULES.format(name=config.NAME, actions=actions_help()))
    log: list[str] = []
    repeats: dict = {}
    for step in range(1, max_steps + 1):
        time.sleep(0.8)                                          # даём экрану обновиться после прошлого шага
        img, _ = vision.grab(1024)
        wins = hands.windows_info().text
        user = (f"ЗАДАЧА: {goal}\n\nУже сделано:\n" + ("\n".join(log[-8:]) if log else "пока ничего") +
                f"\n\n{wins}\n\n(Это твой ход №{step}, осталось ходов: {max_steps - step}.) Снимок экрана приложен.\n"
                "СНАЧАЛА проверь по снимку: задача УЖЕ выполнена? Если да — ответь только [ГОТОВО: …] и ничего не нажимай.")
        msg = deepseek.complete([{"role": "system", "content": system},
                                 {"role": "user", "content": [{"type": "text", "text": user},
                                                              deepseek.image_part(vision._png(img))]}],
                                model=deepseek.FLASH, temperature=0.3, max_tokens=500)
        text = msg.get("content") or ""
        thought = THOUGHT_RE.search(text)
        stop = STOP_RE.search(text)
        acts = [parse_action(b) for b in ACTION_RE.findall(text)]
        step_lines = [f"Шаг {step}: {thought.group(1).strip()[:150]}" if thought else f"Шаг {step}:"]
        if stop and not acts:
            kind, msg = stop.group(1).upper(), stop.group(2).strip()
            if kind.startswith("ГОТОВО"):
                return f"Задача выполнена: {msg}"
            if kind.startswith("ВОПРОС"):
                return f"Нужно уточнение: {msg}"
            return f"Не вышло: {msg}"
        if not acts:
            log.append(f"шаг {step}: (не понял, что делать — ответ без метки)")
            continue
        for name, args in acts[:3]:
            key = (name, tuple(args))
            repeats[key] = repeats.get(key, 0) + 1
            if repeats[key] > 2:
                log.append(f"шаг {step}: {name} {' | '.join(args)} → ПРОПУЩЕНО (уже 2 раза, нужен другой способ)")
                continue
            if name == "подождать":
                try:
                    time.sleep(min(15, float(re.sub(r"[^\d.]", "", " ".join(args)) or 3)))
                except ValueError:
                    time.sleep(3)
                log.append(f"шаг {step}: подождал")
                continue
            if name == "задача":                                 # без вложенных задач
                continue
            res = hands.run(name, args)
            if res.confirm:
                if confirm_fn(res.confirm):
                    res = hands.run(name, args, confirmed=True)
                else:
                    return "Ок, остановился: брат не разрешил шаг «" + f"{name} {' | '.join(args)}»."
            short = res.text.replace("\n", " ")[:300]
            log.append(f"шаг {step}: {name} {' | '.join(args)} → {short}")
            step_lines.append(f"  {name} {' | '.join(args)} → {short[:120]}")
        say("\n".join(step_lines))
    return f"Остановился после {max_steps} шагов: " + (log[-1] if log else "ничего не вышло")
