"""
МОЗГИ СТУДИИ.

Разговор хранится в одном общем виде (список словарей), а каждый мозг сам переводит его в свой формат:
  {"role": "user", "text": "..."}
  {"role": "assistant", "text": "...", "tool_calls": [{"id", "name", "args"}], "claude_raw": [...]}
  {"role": "tool", "id": "...", "name": "...", "content": "...", "is_error": bool}

Мозги:
  LocalBrain  — модель Ollama на этом ПК (бесплатно, ничего не уходит в интернет)
  ClaudeBrain — Claude API (самый умный, платно, нужен ключ)
  Ollama Cloud — тот же LocalBrain, только модель с окончанием «:cloud»
"""
from __future__ import annotations

import json
import re
import subprocess
import time
import uuid
from dataclasses import dataclass, field

from .. import config


@dataclass
class StepResult:
    text: str = ""
    tool_calls: list[dict] = field(default_factory=list)
    claude_raw: list | None = None
    stop: str = "end"          # end | tools | max_tokens | refusal | error
    error: str = ""
    gpu_crash: bool = False    # супер-мозг так и не влез в видеокарту


# ---------- какие модели есть и что умеют ----------
_caps_cache: dict[str, list[str]] = {}


def model_caps(model: str) -> list[str]:
    if model not in _caps_cache:
        try:
            import ollama
            _caps_cache[model] = list(ollama.Client().show(model).capabilities or [])
        except Exception:
            return []           # модели ещё нет (качается?) — не запоминаем, проверим в следующий раз
    return _caps_cache[model]


def super_model() -> str:
    """Супер-мозг для программирования (большая модель на ПК), если он скачан."""
    m = getattr(config, "STUDIO_SUPER_MODEL", "")
    return m if m and "tools" in model_caps(m) else ""


def loaded_models() -> list[str]:
    try:
        import ollama
        return [m.model for m in ollama.Client().ps().models]
    except Exception:
        return []


def free_vram_mb() -> int:
    """Сколько видеопамяти свободно прямо сейчас (МБ). Не вышло узнать — считаем, что мало."""
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=5, creationflags=0x08000000).stdout
        return int(out.strip().splitlines()[0])
    except Exception:
        return 4000


SUPER_LAYERS = 40          # слоёв у qwen3.6:35b-a3b
SUPER_LAYER_MB = 560       # примерно столько видеопамяти на один слой (22 ГБ / 40)
SUPER_RESERVE_MB = 2600    # кэш на 32K, рабочие буферы и сама CUDA — с запасом


def super_gpu_layers(free_mb: int) -> int:
    return max(0, min(SUPER_LAYERS, (free_mb - SUPER_RESERVE_MB) // SUPER_LAYER_MB))


def _gpu_crash(err: str) -> bool:
    return bool(re.search(r"CUDA|llama-server|terminated|out of memory|status code: 500", err, re.I))


def unload(model: str) -> None:
    """Выгрузить модель из видеопамяти (Ollama освобождает её сразу)."""
    try:
        import ollama
        ollama.Client().generate(model=model, prompt="", keep_alive=0)
    except Exception:
        pass


def local_tool_model() -> str:
    """Локальная модель, которая умеет вызывать инструменты. Выбор брата — в приоритете."""
    for m in (getattr(config, "STUDIO_LOCAL_AGENT_MODEL", "") or config.MODEL, "gemma4:12b-it-qat", "gemma4:12b"):
        if m and "tools" in model_caps(m):
            return m
    return config.MODEL


def claude_ready() -> bool:
    if not getattr(config, "STUDIO_CLAUDE_KEY", ""):
        return False
    try:
        import anthropic  # noqa: F401
        return True
    except ImportError:
        return False


# ---------- локальный мозг (и Ollama Cloud) ----------
class LocalBrain:
    def __init__(self, model: str, use_tools: bool = True, cloud: bool = False, super_: bool = False):
        self.model = model
        self.cloud = cloud
        self.super = super_
        self.use_tools = use_tools and (cloud or "tools" in model_caps(model))
        self.label = (f"⚡ супер-мозг {model} (на ПК)" if super_ else
                      f"{model} ({'облако Ollama' if cloud else 'на ПК'})")

        self.num_gpu: int | None = None        # сколько слоёв супер-мозга класть в видеокарту (остальное — в RAM)

    @property
    def ctx(self) -> int:
        if self.super:
            return int(getattr(config, "STUDIO_SUPER_CTX", 32768))
        if self.model == config.MODEL:
            return int(config.CONTEXT_SIZE)    # та же память, что у голосовой Мартышки — одна копия модели на двоих
        return int(getattr(config, "STUDIO_LOCAL_CTX", 16384))

    def prepare(self) -> None:
        """Освободить видеокарту под этот мозг: выгрузить остальные модели (они загрузятся обратно сами).
        Для супер-мозга ещё и посчитать, сколько его слоёв реально влезет: видеокарту делят голос и уши
        голосовой Мартышки, браузер, игры — если положить больше, Ollama падает с ошибкой CUDA."""
        if self.cloud:
            return
        others = [m for m in loaded_models() if m != self.model and not m.startswith(self.model + ":")]
        for m in others:
            unload(m)
        if self.super:
            if others:
                time.sleep(2.0)                # видеопамять освобождается не мгновенно
            self.num_gpu = super_gpu_layers(free_vram_mb())

    def warm(self) -> None:
        """Заранее загрузить модель с нужной памятью, чтобы первый ответ был быстрым."""
        try:
            import ollama
            ollama.Client().generate(model=self.model, prompt="", keep_alive=config.KEEP_ALIVE,
                                     options={"num_ctx": self.ctx})
        except Exception:
            pass

    @staticmethod
    def _convert(system: str, history: list[dict]) -> list[dict]:
        msgs = [{"role": "system", "content": system}]
        for m in history:
            if m["role"] == "user":
                msgs.append({"role": "user", "content": m["text"]})
            elif m["role"] == "assistant":
                one = {"role": "assistant", "content": m.get("text", "")}
                if m.get("tool_calls"):
                    one["tool_calls"] = [{"function": {"name": c["name"], "arguments": c["args"]}}
                                         for c in m["tool_calls"]]
                msgs.append(one)
            elif m["role"] == "tool":
                msgs.append({"role": "tool", "content": m["content"], "tool_name": m["name"]})
        return msgs

    def step(self, system: str, history: list[dict], tools: list[dict], on_text, should_stop,
             on_progress=None) -> StepResult:
        import ollama
        kw: dict = dict(model=self.model, messages=self._convert(system, history), stream=True,
                        keep_alive=config.KEEP_ALIVE)
        if not self.cloud:
            kw["options"] = {"num_ctx": self.ctx, "temperature": 0.6 if self.super else 0.4}
            if "thinking" in model_caps(self.model):
                kw["think"] = bool(self.super)        # супер-мозг сначала думает — так код получается лучше
        if self.use_tools and tools:
            kw["tools"] = [{"type": "function", "function": {"name": t["name"], "description": t["description"],
                                                             "parameters": t["input_schema"]}} for t in tools]
        for attempt in range(3):
            if self.super and self.num_gpu is not None:
                kw["options"]["num_gpu"] = self.num_gpu
            res = self._run(kw, on_text, should_stop, on_progress)
            # супер-мозг не влез в видеокарту — пробуем ещё раз, положив в неё меньше слоёв
            if (self.super and res.stop == "error" and _gpu_crash(res.error) and not res.text
                    and not res.tool_calls and attempt < 2):
                start = self.num_gpu if self.num_gpu is not None else SUPER_LAYERS
                self.num_gpu = max(0, start // 2 - 2) if attempt == 0 else 0
                if on_progress:
                    on_progress(f"\n[видеокарта занята — гружу супер-мозг иначе: {self.num_gpu} из "
                                f"{SUPER_LAYERS} слоёв в видеокарту, остальное в память]\n")
                time.sleep(1.5)
                continue
            if self.super and res.stop == "error" and _gpu_crash(res.error):
                res.gpu_crash = True
            return res
        return res

    def _run(self, kw: dict, on_text, should_stop, on_progress) -> StepResult:
        import ollama
        res = StepResult()
        try:
            for chunk in ollama.Client().chat(**kw):
                if should_stop():
                    res.stop = "error"
                    res.error = "Остановлено."
                    break
                msg = chunk.message
                if getattr(msg, "thinking", None) and on_progress:
                    on_progress(msg.thinking)
                if msg.content:
                    res.text += msg.content
                    on_text(msg.content)
                for tc in (msg.tool_calls or []):
                    args = tc.function.arguments
                    if isinstance(args, str):
                        try:
                            args = json.loads(args)
                        except Exception:
                            args = {}
                    res.tool_calls.append({"id": "t" + uuid.uuid4().hex[:10], "name": tc.function.name,
                                           "args": dict(args or {})})
        except Exception as e:
            res.stop, res.error = "error", f"Мозг {self.model} не ответил: {e}"
            return res
        if res.tool_calls and res.stop != "error":
            res.stop = "tools"
        return res


# ---------- Claude API ----------
CLAUDE_BETAS = ["server-side-fallback-2026-07-01", "thinking-display-updates-2026-08-18"]


class ClaudeBrain:
    def __init__(self):
        import anthropic
        self.client = anthropic.Anthropic(api_key=config.STUDIO_CLAUDE_KEY, max_retries=3)
        self.model = getattr(config, "STUDIO_CLAUDE_MODEL", "claude-opus-5-5") or "claude-opus-5-5"
        self.effort = getattr(config, "STUDIO_CLAUDE_EFFORT", "high") or "high"
        self.use_tools = True
        self.label = f"{self.model} (облако Claude)"

    @staticmethod
    def _convert(history: list[dict]) -> list[dict]:
        msgs: list[dict] = []
        results: list[dict] = []

        def flush():
            if results:
                msgs.append({"role": "user", "content": list(results)})
                results.clear()

        for m in history:
            if m["role"] == "tool":
                results.append({"type": "tool_result", "tool_use_id": m["id"],
                                "content": m["content"] or "(пусто)", "is_error": bool(m.get("is_error"))})
                continue
            flush()
            if m["role"] == "user":
                msgs.append({"role": "user", "content": m["text"]})
            elif m["role"] == "assistant":
                if m.get("claude_raw"):                     # родные блоки Claude (с мыслями) — без изменений
                    msgs.append({"role": "assistant", "content": m["claude_raw"]})
                else:                                       # ход другого мозга — пересказываем обычным текстом
                    blocks = []
                    if m.get("text"):
                        blocks.append({"type": "text", "text": m["text"]})
                    for c in m.get("tool_calls") or []:
                        blocks.append({"type": "tool_use", "id": c["id"], "name": c["name"], "input": c["args"]})
                    if blocks:
                        msgs.append({"role": "assistant", "content": blocks})
        flush()
        return msgs

    def step(self, system: str, history: list[dict], tools: list[dict], on_text, should_stop,
             on_progress=None) -> StepResult:
        import anthropic
        api_tools = [dict(t, eager_input_streaming=True) for t in tools]
        res = StepResult()
        try:
            with self.client.beta.messages.stream(
                model=self.model, max_tokens=64000, betas=CLAUDE_BETAS, fallbacks="default",
                system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                thinking={"type": "adaptive", "display": "updates"},
                output_config={"effort": self.effort},
                tools=api_tools, messages=self._convert(history),
            ) as stream:
                for ev in stream:
                    if should_stop():
                        res.stop, res.error = "error", "Остановлено."
                        return res
                    if ev.type == "text":
                        res.text += ev.text
                        on_text(ev.text)
                    elif ev.type == "thinking" and on_progress and ev.thinking:
                        on_progress(ev.thinking)
                final = stream.get_final_message()
        except anthropic.AuthenticationError:
            res.stop, res.error = "error", "Ключ Claude не подходит. Проверь его в настройках студии."
            return res
        except anthropic.RateLimitError:
            res.stop, res.error = "error", "Claude просит подождать (лимит запросов). Попробуй через минуту."
            return res
        except anthropic.APIStatusError as e:
            res.stop, res.error = "error", f"Claude ответил ошибкой {e.status_code}: {e.message}"
            return res
        except anthropic.APIConnectionError:
            res.stop, res.error = "error", "Нет связи с Claude (интернет?)."
            return res

        res.claude_raw = [b.to_dict() for b in final.content]
        if final.stop_reason == "refusal":
            res.stop, res.error = "refusal", "Claude отказался выполнять эту просьбу."
            return res
        if final.stop_reason == "max_tokens":
            res.stop, res.error = "max_tokens", "Ответ получился слишком длинным и оборвался."
            return res
        for b in final.content:
            if b.type == "tool_use":
                res.tool_calls.append({"id": b.id, "name": b.name, "args": dict(b.input or {})})
        res.stop = "tools" if res.tool_calls else "end"
        return res


# ---------- выбор мозга ----------
def _cloud_brain():
    pref = getattr(config, "STUDIO_TASK_BRAIN", "auto")
    if pref in ("auto", "claude") and claude_ready():
        return ClaudeBrain()
    if pref in ("auto", "ollama_cloud") and getattr(config, "STUDIO_OLLAMA_CLOUD_MODEL", ""):
        return LocalBrain(config.STUDIO_OLLAMA_CLOUD_MODEL, cloud=True)
    return None


def task_brain():
    """Мозг для простых задач (поиск, открыть программу, нарисовать): облако, если настроено, иначе лёгкий на ПК."""
    return _cloud_brain() or LocalBrain(local_tool_model())


def code_brain():
    """Мозг для программирования и сложных задач: облако, если настроено, иначе супер-мозг на ПК."""
    cloud = _cloud_brain()
    if cloud is not None:
        return cloud
    if getattr(config, "STUDIO_TASK_BRAIN", "auto") != "local_light" and super_model():
        return LocalBrain(super_model(), super_=True)
    return LocalBrain(local_tool_model())


def chat_brain():
    """Мозг для болтовни — модель на ПК, которую брат выбрал в настройках."""
    return LocalBrain(config.MODEL)
