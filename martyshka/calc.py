"""
СЧЁТ: мозг плохо считает в уме (256×4 у него «324»). Простую арифметику из фразы считаем сами
и подсказываем мозгу точный ответ.

Понимает: «сколько будет 256 умножить на 4», «17 на 23», «1200 разделить на 3», «15 процентов от 2400»,
«корень из 144», «5 в квадрате», «2 в степени 10», «12,5 плюс 7», «100 минус 37».
"""
import math
import re

_NUM = r"(\d+(?:[.,]\d+)?)"
_RULES = [
    (rf"{_NUM}\s*(?:%|процент\w*)\s+от\s+{_NUM}", lambda a, b: a * b / 100),
    (rf"корень\w*\s+(?:квадратн\w*\s+)?(?:из\s+)?{_NUM}", lambda a: math.sqrt(a)),
    (rf"{_NUM}\s+в\s+квадрате", lambda a: a * a),
    (rf"{_NUM}\s+в\s+кубе", lambda a: a ** 3),
    (rf"{_NUM}\s+в\s+степени\s+{_NUM}", lambda a, b: a ** b if b <= 64 else None),
    (rf"{_NUM}\s*(?:\*|×|x|х|умножить\s+на|умножь\s+на|помножить\s+на|на)\s*{_NUM}", lambda a, b: a * b),
    (rf"{_NUM}\s*(?:/|:|÷|разделить\s+на|делить\s+на|подели\w*\s+на|раздели\s+на)\s*{_NUM}",
     lambda a, b: a / b if b else None),
    (rf"{_NUM}\s*(?:\+|плюс|прибавить|сложить\s+с)\s*{_NUM}", lambda a, b: a + b),
    (rf"{_NUM}\s*(?:-|−|минус|отнять)\s*{_NUM}", lambda a, b: a - b),
]
# «на» как умножение — только в вопросах про счёт («сколько будет 17 на 23»), не «поставь громкость на 30»
_ASK = re.compile(r"(сколько\s+будет|сколько\s+это|посчитай|подсчитай|вычисли|реши|сколько\s+получится|"
                  r"чему\s+равн|сколько\s+[\d,.]+\s*(%|процент)|корень|в\s+квадрате|в\s+кубе|в\s+степени|"
                  r"умножить|разделить|плюс|минус|процент\w*\s+от)", re.IGNORECASE)


def _fmt(x: float) -> str:
    if abs(x - round(x)) < 1e-9:
        return str(int(round(x)))
    return f"{x:.4f}".rstrip("0").rstrip(".").replace(".", ",")


def solve(text: str) -> str | None:
    """Точный результат простого примера из фразы (строкой) или None."""
    t = text.lower().replace("ё", "е")
    if not _ASK.search(t):
        return None
    # Несколько действий подряд («5 плюс 3 умножить на 2») — тут порядок важен, пусть считает мозг
    ops = re.findall(r"(умнож|раздел|делить|подели|плюс|минус|прибав|отнять|процент|квадрат|куб|степен|корен|"
                     r"[*×/÷+−]|\d\s*-\s*\d|\d\s+на\s+\d)", t)
    if len(ops) != 1:
        return None
    for pat, fn in _RULES:
        m = re.search(pat, t)
        if m:
            try:
                nums = [float(g.replace(",", ".")) for g in m.groups()]
                r = fn(*nums)
            except (ValueError, OverflowError, ZeroDivisionError):
                return None
            if r is None or abs(r) > 1e15:
                return None
            return _fmt(r)
    return None
