"""
МОЗГ В ОБЛАКЕ — DeepSeek API.

Здесь вся связь с DeepSeek: все части Мартышки (голос, Студия, Говорилка, экран, картинки) думают через этот файл.
  deepseek-flash   — быстрый и дешёвый, ВИДИТ картинки (снимки экрана)
  deepseek-v4-pro  — сильный: код, сложные задачи, длинные рассуждения (картинок не видит)

Ключ хранится в data/settings.json (DEEPSEEK_KEY) — эта папка в git не попадает.

Для старого кода есть «переходник» Client(): он принимает запросы в прежнем виде (как раньше к Ollama)
и отправляет их в DeepSeek. Поэтому старые части программы работают без переписывания.
"""
from __future__ import annotations

import base64
import json
import re
import threading
import time
from pathlib import Path
from typing import Iterator

import httpx

from . import config

FLASH = "deepseek-flash"
PRO = "deepseek-v4-pro"
MODELS = (FLASH, PRO)

_client: httpx.Client | None = None
_lock = threading.Lock()


class DeepSeekError(RuntimeError):
    pass


def key() -> str:
    return (getattr(config, "DEEPSEEK_KEY", "") or "").strip()


def base_url() -> str:
    return (getattr(config, "DEEPSEEK_URL", "") or "https://api.deepseek.com").rstrip("/")


def http() -> httpx.Client:
    global _client
    with _lock:
        if _client is None:
            # одно постоянное соединение на всю программу — каждый ответ начинается быстрее
            _client = httpx.Client(timeout=httpx.Timeout(connect=10, read=300, write=60, pool=10),
                                   http2=False, limits=httpx.Limits(max_keepalive_connections=8))
        return _client


def _headers() -> dict:
    if not key():
        raise DeepSeekError("Нет ключа DeepSeek. Вставь его в настройках (раздел «Мозг»).")
    return {"Authorization": f"Bearer {key()}", "Content-Type": "application/json"}


def _explain(status: int, body: str) -> str:
    if status == 401:
        return "Ключ DeepSeek не подходит. Проверь его в настройках."
    if status == 402:
        return "На счёте DeepSeek закончились деньги. Пополни баланс на platform.deepseek.com."
    if status == 429:
        return "DeepSeek просит подождать (слишком много запросов)."
    if status in (500, 502, 503, 504):
        return "DeepSeek сейчас перегружен. Попробуй ещё раз через минуту."
    if status == 400 and "Content Exists Risk" in body:
        return "DeepSeek отказался обрабатывать этот текст (его фильтр)."
    return f"DeepSeek ответил ошибкой {status}: {body[:300]}"


# ---------- выбор модели ----------
def normalize(model: str | None) -> str:
    """Любое старое имя модели (gemma, qwen…) → быстрый DeepSeek. Имена DeepSeek — как есть."""
    m = (model or "").strip()
    if m in MODELS:
        return m
    if m.lower() in ("pro", "v4-pro", "deepseek-v4-pro", "сильный", "умный"):
        return PRO
    return FLASH


HARD_RE = re.compile(
    r"(напиши\s+(код|программ|скрипт|сайт|приложени|бот)|код\w*|программир|python|javascript|html|"
    r"почему|объясни|разбер|проанализ|сравни|докажи|реши\s+задач|посчитай\s+сложн|план\s|стратег|"
    r"сочини|напиши\s+(рассказ|стих|песн|стать|реферат|эссе)|подробно|детально|"
    r"исправь\s+ошибк|баг|отлад|архитектур|оптимизир|алгоритм|уравнени|интеграл|теорем)", re.I)


def pick(text: str = "", hard: bool | None = None) -> str:
    """Какой мозг взять для этой фразы. BRAIN_MODE: auto / flash / pro."""
    mode = (getattr(config, "BRAIN_MODE", "auto") or "auto").lower()
    if mode in ("flash", FLASH):
        return FLASH
    if mode in ("pro", PRO):
        return PRO
    if hard is None:
        hard = len(text) > 260 or bool(HARD_RE.search(text or ""))
    return PRO if hard else FLASH


# ---------- картинки ----------
def image_part(img, detail: str = "high") -> dict:
    """Картинка (путь, байты, base64 или PIL) → кусок сообщения для DeepSeek."""
    if hasattr(img, "save"):                       # PIL.Image
        import io
        buf = io.BytesIO()
        img.convert("RGB").save(buf, "JPEG", quality=88)
        data, mime = buf.getvalue(), "image/jpeg"
    elif isinstance(img, (bytes, bytearray)):
        data, mime = bytes(img), "image/png"
    elif isinstance(img, (str, Path)) and len(str(img)) < 1000 and Path(str(img)).exists():
        p = Path(str(img))
        data = p.read_bytes()
        mime = "image/jpeg" if p.suffix.lower() in (".jpg", ".jpeg") else "image/png"
    else:                                          # уже base64
        return {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img}", "detail": detail}}
    b64 = base64.b64encode(data).decode()
    return {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}", "detail": detail}}


def _has_images(messages: list[dict]) -> bool:
    for m in messages:
        c = m.get("content")
        if isinstance(c, list) and any(isinstance(p, dict) and p.get("type") == "image_url" for p in c):
            return True
    return False


# ---------- главный запрос ----------
def _payload(messages, model, think, effort, temperature, max_tokens, tools, json_mode, stream):
    model = normalize(model)
    if _has_images(messages):
        model = FLASH                              # картинки видит только flash
    p: dict = {"model": model, "messages": messages, "stream": stream}
    if think:
        p["thinking"] = {"type": "enabled"}
        p["reasoning_effort"] = effort or "high"
    else:
        p["thinking"] = {"type": "disabled"}
        if temperature is not None:
            p["temperature"] = temperature
    if max_tokens:
        p["max_tokens"] = int(max_tokens)
    if tools:
        p["tools"] = tools
    if json_mode:
        p["response_format"] = {"type": "json_object"}
    if stream:
        p["stream_options"] = {"include_usage": True}
    return p


def _post(payload: dict, stream: bool):
    url = base_url() + "/chat/completions"
    last = None
    for attempt in range(3):
        try:
            if stream:
                req = http().build_request("POST", url, headers=_headers(), json=payload)
                r = http().send(req, stream=True)
            else:
                r = http().post(url, headers=_headers(), json=payload)
        except (httpx.ConnectError, httpx.ReadTimeout, httpx.RemoteProtocolError) as e:
            last = DeepSeekError(f"Нет связи с DeepSeek (интернет?): {e}")
            time.sleep(1.5 * (attempt + 1))
            continue
        if r.status_code == 200:
            return r
        body = r.read().decode("utf-8", "replace") if stream else r.text
        if stream:
            r.close()
        last = DeepSeekError(_explain(r.status_code, body))
        if r.status_code in (429, 500, 502, 503, 504) and attempt < 2:
            time.sleep(2.0 * (attempt + 1))
            continue
        raise last
    raise last  # type: ignore[misc]


USAGE = {"in": 0, "cached": 0, "out": 0, "requests": 0}


def _count(usage: dict | None) -> None:
    if not usage:
        return
    USAGE["requests"] += 1
    USAGE["in"] += int(usage.get("prompt_tokens") or 0)
    USAGE["cached"] += int(usage.get("prompt_cache_hit_tokens") or 0)
    USAGE["out"] += int(usage.get("completion_tokens") or 0)


def complete(messages: list[dict], model: str | None = None, think: bool = False, effort: str = "high",
             temperature: float | None = None, max_tokens: int | None = None, tools: list | None = None,
             json_mode: bool = False) -> dict:
    """Один ответ целиком. Возвращает сообщение: {"content", "reasoning_content", "tool_calls"}."""
    r = _post(_payload(messages, model, think, effort, temperature, max_tokens, tools, json_mode, False), False)
    data = r.json()
    _count(data.get("usage"))
    msg = data["choices"][0]["message"]
    msg["finish_reason"] = data["choices"][0].get("finish_reason")
    return msg


def ask(prompt: str, system: str = "", model: str | None = None, max_tokens: int = 1500,
        temperature: float = 0.3, think: bool = False, json_mode: bool = False) -> str:
    """Короткая служебная просьба: текст на входе — текст на выходе."""
    msgs = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": prompt}]
    return (complete(msgs, model=model, think=think, temperature=temperature, max_tokens=max_tokens,
                     json_mode=json_mode).get("content") or "").strip()


def stream(messages: list[dict], model: str | None = None, think: bool = False, effort: str = "high",
           temperature: float | None = None, max_tokens: int | None = None, tools: list | None = None,
           should_stop=None) -> Iterator[tuple[str, object]]:
    """Ответ по кусочкам. Выдаёт пары:
       ("text", кусок) / ("think", кусок мыслей) / ("tool_calls", [{id, name, args}]) / ("done", причина)."""
    r = _post(_payload(messages, model, think, effort, temperature, max_tokens, tools, False, True), True)
    calls: dict[int, dict] = {}
    finish = "stop"
    try:
        for line in r.iter_lines():
            if should_stop and should_stop():
                finish = "stopped"
                break
            if not line or not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                ev = json.loads(data)
            except json.JSONDecodeError:
                continue
            if ev.get("usage"):
                _count(ev["usage"])
            for ch in ev.get("choices") or []:
                d = ch.get("delta") or {}
                if d.get("reasoning_content"):
                    yield "think", d["reasoning_content"]
                if d.get("content"):
                    yield "text", d["content"]
                for tc in d.get("tool_calls") or []:
                    slot = calls.setdefault(tc.get("index", 0), {"id": "", "name": "", "args": ""})
                    if tc.get("id"):
                        slot["id"] = tc["id"]
                    fn = tc.get("function") or {}
                    if fn.get("name"):
                        slot["name"] += fn["name"]
                    if fn.get("arguments"):
                        slot["args"] += fn["arguments"]
                if ch.get("finish_reason"):
                    finish = ch["finish_reason"]
    finally:
        r.close()
    if calls:
        out = []
        for i in sorted(calls):
            c = calls[i]
            try:
                args = json.loads(c["args"]) if c["args"].strip() else {}
            except json.JSONDecodeError:
                args = {"_raw": c["args"]}
            out.append({"id": c["id"] or f"call_{i}", "name": c["name"], "args": args})
        yield "tool_calls", out
    yield "done", finish


def look(image, question: str, system: str = "", max_tokens: int = 600, detail: str = "high") -> str:
    """Посмотреть на картинку (снимок экрана, фото) и ответить на вопрос. Делает flash — он видит."""
    msgs = ([{"role": "system", "content": system}] if system else []) + [
        {"role": "user", "content": [{"type": "text", "text": question}, image_part(image, detail)]}]
    return (complete(msgs, model=FLASH, temperature=0.2, max_tokens=max_tokens).get("content") or "").strip()


# ---------- уши: речь → текст ----------
def transcribe(audio, sample_rate: int = 16000) -> str:
    """Распознать речь (до 120 с). audio — numpy-массив float32 (-1..1) или готовые байты WAV."""
    import io
    import wave
    if not isinstance(audio, (bytes, bytearray)):
        import numpy as np
        a = np.asarray(audio, dtype="float32").reshape(-1)
        a = a[: sample_rate * 118]
        pcm = (np.clip(a, -1, 1) * 32767).astype("<i2").tobytes()
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(sample_rate)
            w.writeframes(pcm)
        audio = buf.getvalue()
    hdr = _headers()
    hdr.pop("Content-Type", None)
    last = None
    for attempt in range(3):
        try:
            r = http().post(base_url() + "/v1/tools/asr", headers=hdr,
                            files={"file": ("speech.wav", bytes(audio), "audio/wav")}, timeout=60)
        except (httpx.ConnectError, httpx.ReadTimeout, httpx.RemoteProtocolError) as e:
            last = DeepSeekError(f"Нет связи с DeepSeek (интернет?): {e}")
            time.sleep(1.0)
            continue
        if r.status_code == 200:
            try:
                data = r.json()
                text = (data.get("text") or data.get("transcript") or "").strip()
            except Exception:
                text = r.text.strip()
            return _clean_transcript(text)
        last = DeepSeekError(_explain(r.status_code, r.text))
        if r.status_code not in (429, 500, 502, 503, 504):
            break
        time.sleep(1.5)
    raise last  # type: ignore[misc]


CJK_RE = re.compile(r"[　-ヿ㐀-鿿가-힯＀-￯]")
GLUED_RE = re.compile(r"[А-Яа-яЁёA-Za-z]{19,}")


def _clean_transcript(text: str) -> str:
    """Распознавание DeepSeek иногда: 1) на шуме выдаёт китайские иероглифы — это не речь, выкидываем;
    2) пишет русскую фразу слитно («Чтовсеунасздесьрабочие?») — быстро расставляем пробелы через flash."""
    if CJK_RE.search(text):
        text = CJK_RE.sub(" ", text)
        if len(re.findall(r"[А-Яа-яЁёA-Za-z]", text)) < 3:
            return ""
        text = re.sub(r"\s+", " ", text).strip(" 。，、")
    if GLUED_RE.search(text):
        try:
            fixed = ask("Расставь пробелы между словами в распознанной речи. Ничего не меняй, не исправляй и не "
                        "добавляй — только пробелы (и пробел после знаков препинания). Ответ — только текст.\n\n"
                        + text, model=FLASH, max_tokens=300, temperature=0)
            if fixed and fixed.replace(" ", "").lower() == text.replace(" ", "").lower():
                text = fixed
        except Exception:
            pass
    return text.strip()


def balance() -> str:
    """Сколько денег на счёте (для окна настроек)."""
    try:
        r = http().get(base_url() + "/user/balance", headers=_headers(), timeout=15)
        if r.status_code != 200:
            return _explain(r.status_code, r.text)
        info = (r.json().get("balance_infos") or [{}])[0]
        return f"{info.get('total_balance', '?')} {info.get('currency', '')}".strip()
    except Exception as e:
        return f"не узнать: {e}"


def ready() -> bool:
    return bool(key())


# ======================================================================
#  ПЕРЕХОДНИК для старого кода (раньше он говорил с Ollama).
#  Принимает те же параметры (model, messages, options, think, stream, images…) и шлёт всё в DeepSeek.
# ======================================================================
class _Obj(dict):
    """Словарь, к которому можно обращаться и r["message"]["content"], и r.message.content."""
    def __getattr__(self, k):
        try:
            return self[k]
        except KeyError:
            return None


def _convert_old(messages: list[dict]) -> list[dict]:
    out = []
    for m in messages:
        m = dict(m)
        imgs = m.pop("images", None)
        if imgs:
            parts = [{"type": "text", "text": m.get("content") or ""}] + [image_part(i) for i in imgs]
            m["content"] = parts
        if m.get("role") == "tool":
            m.pop("tool_name", None)
            m.setdefault("tool_call_id", m.get("id", "call_0"))
        out.append(m)
    return out


class Client:
    """Ведёт себя как ollama.Client(), но думает DeepSeek."""
    def __init__(self, *a, **kw):
        pass

    def chat(self, model=None, messages=None, stream=False, options=None, think=None, keep_alive=None,
             tools=None, format=None, **kw):
        opts = options or {}
        msgs = _convert_old(messages or [])
        # своё имя DeepSeek — как есть; старое имя (gemma…) — по выбору брата в настройках
        mdl = model if model in MODELS else (PRO if (getattr(config, "BRAIN_MODE", "auto") or "").lower()
                                             in ("pro", PRO) else FLASH)
        temp = opts.get("temperature")
        max_tokens = opts.get("num_predict")
        if max_tokens is not None and max_tokens <= 1:     # «прогрев» модели больше не нужен
            return _Obj(message=_Obj(content="", role="assistant"))
        api_tools = tools
        json_mode = format == "json" or isinstance(format, dict)
        if json_mode and msgs and "json" not in json.dumps(msgs, ensure_ascii=False).lower():
            msgs = msgs + [{"role": "system", "content": "Ответ — строго JSON."}]
        if not stream:
            msg = complete(msgs, model=mdl, think=bool(think), temperature=temp, max_tokens=max_tokens,
                           tools=api_tools, json_mode=json_mode)
            calls = [_Obj(function=_Obj(name=c["function"]["name"], arguments=c["function"]["arguments"]))
                     for c in (msg.get("tool_calls") or [])]
            return _Obj(message=_Obj(role="assistant", content=msg.get("content") or "",
                                     thinking=msg.get("reasoning_content"), tool_calls=calls or None))

        def gen():
            for kind, val in stream_(msgs, model=mdl, think=bool(think), temperature=temp, max_tokens=max_tokens,
                                     tools=api_tools):
                if kind == "text":
                    yield _Obj(message=_Obj(role="assistant", content=val, thinking=None, tool_calls=None))
                elif kind == "think":
                    yield _Obj(message=_Obj(role="assistant", content="", thinking=val, tool_calls=None))
                elif kind == "tool_calls":
                    calls = [_Obj(function=_Obj(name=c["name"], arguments=c["args"])) for c in val]
                    yield _Obj(message=_Obj(role="assistant", content="", thinking=None, tool_calls=calls))
            yield _Obj(message=_Obj(role="assistant", content="", thinking=None, tool_calls=None), done=True)
        return gen()

    def generate(self, model=None, prompt="", keep_alive=None, options=None, **kw):
        if not prompt:                                    # «загрузить/выгрузить модель» — в облаке не нужно
            return _Obj(response="")
        return _Obj(response=ask(prompt, model=model, max_tokens=(options or {}).get("num_predict") or 1500))

    def list(self):
        return _Obj(models=[_Obj(model=m) for m in MODELS])

    def ps(self):
        return _Obj(models=[])

    def show(self, model):
        caps = ["completion", "tools", "thinking"] + (["vision"] if normalize(model) == FLASH else [])
        return _Obj(capabilities=caps)


stream_ = stream
