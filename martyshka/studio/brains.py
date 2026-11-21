"""
МОЗГИ СТУДИИ — DeepSeek.

Разговор хранится в одном общем виде (список словарей):
  {"role": "user", "text": "..."}
  {"role": "assistant", "text": "...", "reasoning": "мысли", "tool_calls": [{"id", "name", "args"}]}
  {"role": "tool", "id": "...", "name": "...", "content": "...", "is_error": bool}

Выбор мозга (автоматически, по задаче):
  болтовня           → deepseek-flash без размышлений (мгновенно)
  простое дело       → deepseek-flash, думает коротко (видит экран)
  код / сложное      → deepseek-v4-pro, думает глубоко
Брат может закрепить выбор кнопкой в окне (Авто / Flash / Pro).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from .. import config, deepseek


@dataclass
class StepResult:
    text: str = ""
    reasoning: str = ""
    tool_calls: list[dict] = field(default_factory=list)
    stop: str = "end"          # end | tools | max_tokens | error
    error: str = ""
    gpu_crash: bool = False    # оставлено для совместимости


class DeepSeekBrain:
    def __init__(self, model: str, think: bool, effort: str = "high", use_tools: bool = True, role: str = ""):
        self.model = deepseek.normalize(model)
        self.think = think
        self.effort = effort
        self.use_tools = use_tools
        self.super = self.model == deepseek.PRO
        name = "Pro" if self.super else "Flash"
        self.label = f"DeepSeek {name}" + (f" · думает ({effort})" if think else "") + (f" · {role}" if role else "")

    @staticmethod
    def _convert(system: str, history: list[dict], with_reasoning: bool) -> list[dict]:
        msgs = [{"role": "system", "content": system}]
        for m in history:
            if m["role"] == "user":
                msgs.append({"role": "user", "content": m.get("text", "")})
            elif m["role"] == "assistant":
                one: dict = {"role": "assistant", "content": m.get("text", "") or ""}
                if with_reasoning:
                    one["reasoning_content"] = m.get("reasoning", "") or ""
                calls = m.get("tool_calls") or []
                if calls:
                    one["tool_calls"] = [{"id": c["id"], "type": "function",
                                          "function": {"name": c["name"],
                                                       "arguments": json.dumps(c.get("args") or {}, ensure_ascii=False)}}
                                         for c in calls]
                msgs.append(one)
            elif m["role"] == "tool":
                msgs.append({"role": "tool", "tool_call_id": m["id"], "content": m.get("content") or "(пусто)"})
        return msgs

    def step(self, system: str, history: list[dict], tools: list[dict], on_text, should_stop,
             on_progress=None) -> StepResult:
        api_tools = [{"type": "function", "function": {"name": t["name"], "description": t["description"],
                                                       "parameters": t["input_schema"]}}
                     for t in tools] if (self.use_tools and tools) else None
        think = self.think
        for attempt in range(2):
            res = StepResult()
            try:
                msgs = self._convert(system, history, with_reasoning=think)
                for kind, val in deepseek.stream(msgs, model=self.model, think=think, effort=self.effort,
                                                 temperature=None if think else 0.6,
                                                 max_tokens=64000 if think else 8000, tools=api_tools,
                                                 should_stop=should_stop):
                    if kind == "text":
                        res.text += val
                        on_text(val)
                    elif kind == "think":
                        res.reasoning += val
                        if on_progress:
                            on_progress(val)
                    elif kind == "tool_calls":
                        res.tool_calls = val
                    elif kind == "done":
                        if val == "stopped":
                            res.stop, res.error = "error", "Остановлено."
                            return res
                        if val == "length":
                            res.stop, res.error = "max_tokens", "Ответ получился слишком длинным и оборвался."
                            return res
            except deepseek.DeepSeekError as e:
                # старые ходы без мыслей + режим размышлений иногда не нравятся API — повторяем без размышлений
                if think and attempt == 0 and "reasoning" in str(e).lower() and not res.text:
                    think = False
                    continue
                res.stop, res.error = "error", str(e)
                return res
            except Exception as e:
                res.stop, res.error = "error", f"Мозг DeepSeek не ответил: {e}"
                return res
            res.stop = "tools" if res.tool_calls else "end"
            return res
        return res

    # совместимость со старым кодом
    def prepare(self) -> None:
        pass

    def warm(self) -> None:
        pass


def mode() -> str:
    return (getattr(config, "BRAIN_MODE", "auto") or "auto").lower()


def chat_brain() -> DeepSeekBrain:
    if mode() in ("pro", deepseek.PRO):
        return DeepSeekBrain(deepseek.PRO, think=False, role="разговор")
    return DeepSeekBrain(deepseek.FLASH, think=False, role="разговор")


def task_brain() -> DeepSeekBrain:
    if mode() in ("pro", deepseek.PRO):
        return DeepSeekBrain(deepseek.PRO, think=True, effort="high", role="дела")
    return DeepSeekBrain(deepseek.FLASH, think=True, effort="low", role="дела")


def code_brain() -> DeepSeekBrain:
    if mode() in ("flash", deepseek.FLASH):
        return DeepSeekBrain(deepseek.FLASH, think=True, effort="high", role="агент")
    return DeepSeekBrain(deepseek.PRO, think=True, effort="high", role="агент")


def effort_for(text: str) -> str:
    """Насколько глубоко думать — решает сама по задаче: большое и сложное — максимально."""
    import re
    big = len(text) > 500 or re.search(r"(с нуля|целиком|полностью|архитектур|сложн|большо|приложени|игр[ауы]|"
                                       r"оптимизир|переделай|рефактор|найди ошибк|почему не работает)", text, re.I)
    return "max" if big else "high"


# ---------- совместимость (раньше были модели на ПК) ----------
def super_model() -> str:
    return deepseek.PRO


def loaded_models() -> list[str]:
    return []


def unload(model: str) -> None:
    pass


def model_caps(model: str) -> list[str]:
    return ["completion", "tools", "thinking"] + (["vision"] if deepseek.normalize(model) == deepseek.FLASH else [])


_caps_cache: dict = {}
