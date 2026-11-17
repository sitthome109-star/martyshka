"""
ГОЛОС МАРТЫШКИ
Превращает текст в речь и сразу проигрывает.

Как это работает:
  1. Мозг выдаёт ответ кусочками.
  2. Как только набралось целое предложение — оно уходит в «синтезатор».
  3. Готовый звук сразу идёт в колонки, пока синтезатор делает следующее предложение.
  Поэтому Мартышка начинает говорить, пока мозг ещё дописывает ответ.

Движки (выбираются в config.py → VOICE_ENGINE):
  espeech — русская модель F5-TTS, живая русская интонация, тембр берёт из образца
  qwen    — Qwen3-TTS, готовые голоса (ryan и др.), но с акцентом
"""
import os
import queue
import re
import threading
from typing import Iterable, Iterator

import numpy as np

from . import config

os.environ.setdefault("TQDM_DISABLE", "1")  # убираем полоски прогресса из консоли
import warnings
warnings.filterwarnings("ignore", message="Couldn't find ffmpeg")  # FFmpeg нам не нужен, WAV читаем сами

SAMPLE_RATE = 24000
MIN_CHUNK = 20  # совсем короткие предложения («Да.») склеиваем со следующим — так звучит естественнее


# ---------- подготовка текста для озвучки ----------
def _number_to_words(match: re.Match) -> str:
    from num2words import num2words
    s = match.group(0).replace(",", ".")
    try:
        return num2words(float(s) if "." in s else int(s), lang="ru")
    except Exception:
        return s


def _time_to_words(match: re.Match) -> str:
    from num2words import num2words
    h, m = int(match.group(1)), int(match.group(2))
    hours = num2words(h, lang="ru") if h else "ноль"
    if m == 0:
        return f"{hours} ровно"
    return f"{hours} {'ноль ' if m < 10 else ''}{num2words(m, lang='ru')}"


def prepare_text(text: str, lang: str = "ru") -> str:
    """Убирает то, что нельзя прочитать вслух, и (для русского) переводит цифры в слова."""
    text = re.sub(r"[*_#`~>|\[\]{}+]", " ", text)                     # разметка
    text = re.sub(r"[\U0001F000-\U0001FFFF☀-➿]", " ", text)  # эмодзи
    if lang != "ru":   # немецкий/английский/украинский голос сам читает числа на своём языке
        if lang in ("de", "en"):   # проскочившее русское обращение — по-местному (немецкий голос русское не прочтёт)
            buddy = "Alter" if lang == "de" else "bro"
            text = re.sub(r"\b(братан|братишка|брат|бро)\b", buddy, text, flags=re.IGNORECASE)
            text = re.sub(r"[А-Яа-яЁё]+", " ", text)   # остальная кириллица всё равно будет кашей
        return re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"(\d+)\s*%", r"\1 процентов", text)
    text = re.sub(r"\b(\d{1,2}):(\d{2})\b", _time_to_words, text)     # 16:07 → шестнадцать ноль семь
    text = re.sub(r"\d+(?:[.,]\d+)?", _number_to_words, text)
    return re.sub(r"\s+", " ", text).strip()


# Конец предложения: . ! ? … (можно с кавычкой/скобкой) и пробел после
_SENTENCE_END = re.compile(r"[.!?…]+[\"»)]*\s+")


def split_ready_sentences(buffer: str, max_len: int = 160) -> tuple[list[str], str]:
    """Отрезает от буфера готовые предложения. Возвращает (предложения, остаток)."""
    out = []
    while True:
        m = _SENTENCE_END.search(buffer)
        if m:
            out.append(buffer[: m.end()].strip())
            buffer = buffer[m.end():]
            continue
        if len(buffer) > max_len:  # очень длинное без точки — режем по запятой/пробелу
            cut = max(buffer.rfind(", ", 0, max_len), buffer.rfind(" ", 0, max_len))
            if cut > 20:
                out.append(buffer[: cut + 1].strip())
                buffer = buffer[cut + 1:]
                continue
        return out, buffer


# ---------- движки синтеза ----------
def _patch_onnx_token_type_ids() -> None:
    """Модели ударений RUAccent ждут поле token_type_ids, а новые версии transformers
    его больше не передают. Подставляем нули — как делали старые версии."""
    import onnxruntime as ort
    if getattr(ort.InferenceSession, "_martyshka_patched", False):
        return
    original_run = ort.InferenceSession.run

    def run(self, output_names, input_feed, *args, **kwargs):
        names = {i.name for i in self.get_inputs()}
        feed = {k: v for k, v in input_feed.items() if k in names}
        if "token_type_ids" in names and "token_type_ids" not in feed and "input_ids" in feed:
            feed["token_type_ids"] = np.zeros_like(feed["input_ids"])
        return original_run(self, output_names, feed, *args, **kwargs)

    ort.InferenceSession.run = run
    ort.InferenceSession._martyshka_patched = True


class _ESpeechEngine:
    """Русская модель ESpeech (F5-TTS). Выдаёт предложение целиком."""

    def __init__(self):
        import soundfile as sf
        import torch
        import torchaudio

        # torchaudio хочет FFmpeg для чтения WAV — подменяем на простой читатель
        def _load(path, *a, **k):
            data, sr = sf.read(str(path), dtype="float32", always_2d=True)
            return torch.from_numpy(data.T.copy()), sr
        torchaudio.load = _load
        _patch_onnx_token_type_ids()

        from ruaccent import RUAccent
        from f5_tts.infer.utils_infer import infer_batch_process, load_model, load_vocoder, preprocess_ref_audio_text
        from f5_tts.model import DiT
        import contextlib, io

        voice_dir = config.PROJECT_DIR / "models" / "voice"
        with contextlib.redirect_stdout(io.StringIO()):  # библиотека болтлива при загрузке
            self.accent = RUAccent()
            self.accent.load(omograph_model_size="turbo3.1", use_dictionary=True, tiny_mode=False)
            self.model = load_model(
                DiT, dict(dim=1024, depth=22, heads=16, ff_mult=2, text_dim=512, conv_layers=4),
                str(voice_dir / "ESpeech-TTS-1_SFT-256K" / "espeech_tts_256k.pt"),
                vocab_file=str(voice_dir / "ESpeech-TTS-1_SFT-256K" / "vocab.txt"),
            )
            self.vocoder = load_vocoder(is_local=True, local_path=str(voice_dir / "vocos-mel-24khz"))
            ref_path, self.ref_text = preprocess_ref_audio_text(
                str(config.VOICE_REFERENCE_WAV),
                self.accent.process_all(config.VOICE_REFERENCE_TEXT),
                show_info=lambda *a: None,
            )
        self.ref_audio = _load(ref_path)
        self._infer = infer_batch_process
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        # Прогрев: прогоняем фразы разной длины, чтобы первый настоящий ответ не тормозил
        for warm in ("Здарова, брат!", "Проверка связи, всё работает как надо, замок на месте.",
                     "Ну чё, погнали, брат, сейчас всё сделаю в лучшем виде, не переживай вообще."):
            for _ in self.synthesize(warm, fast=True):
                pass
            for _ in self.synthesize(warm):
                pass

    def synthesize(self, text: str, fast: bool = False) -> Iterator[np.ndarray]:
        try:
            text = self.accent.process_all(text)  # расставляем ударения: «з+амок» / «зам+ок»
        except Exception:
            pass  # не вышло — говорим без подсказок ударений
        steps = min(16, config.VOICE_QUALITY) if fast else config.VOICE_QUALITY
        # Модель недооценивает длительность коротких фраз и «съедает» последнее слово
        # («Здарова, брат!» → «Здарова!»). Даём коротким фразам больше времени.
        plain_len = len(text.replace("+", ""))
        speed = config.VOICE_SPEED * (0.8 if plain_len < 35 else 0.9 if plain_len < 60 else 1.0)
        for chunk, _sr in self._infer(
            self.ref_audio, self.ref_text, [text], self.model, self.vocoder,
            progress=None, nfe_step=steps, speed=speed,
            device=self.device, streaming=True, chunk_size=SAMPLE_RATE,
        ):
            yield np.asarray(chunk, dtype=np.float32).reshape(-1)

    def to(self, device: str) -> None:
        """Переложить модель голоса на видеокарту ("cuda") или в обычную память ("cpu") — для режима игр."""
        import torch
        self.model.to(device)
        self.vocoder.to(device)
        self.device = device
        torch.cuda.empty_cache()   # не держим лишний запас видеопамяти


class VoiceFX:
    """Эффект «голос ИИ» (как у компьютерного помощника в кино): чистый верх, лёгкий металлический блеск
    (хорус с короткой задержкой), мягкое «помещение» (ранние отражения) и ровная громкость.
    Работает кусочками подряд — помнит хвост прошлого кусочка, поэтому без щелчков и без задержки."""

    def __init__(self, sr: int = SAMPLE_RATE, amount: float = 1.0):
        from scipy.signal import butter
        self.sr, self.k = sr, max(0.0, min(1.5, amount))
        self.hp = butter(2, 110 / (sr / 2), "high", output="sos")          # убрать бубнёж снизу
        self.presence = butter(2, [2500 / (sr / 2), 7500 / (sr / 2)], "band", output="sos")   # «воздух» и чёткость
        self.zi_hp = self.zi_pr = None
        self.hist = np.zeros(int(sr * 0.09), np.float32)    # хвост прошлых кусочков (для эха и хоруса)
        self.t = 0                                           # счётчик сэмплов для плавной модуляции
        # ранние отражения: (задержка, громкость) — «небольшая металлическая комната»
        self.taps = [(int(sr * d), g) for d, g in ((0.011, 0.22), (0.019, 0.16), (0.029, 0.12), (0.043, 0.08),
                                                   (0.061, 0.05), (0.083, 0.03))]

    def reset(self) -> None:
        self.hist[:] = 0
        self.zi_hp = self.zi_pr = None

    def process(self, x: np.ndarray) -> np.ndarray:
        from scipy.signal import sosfilt, sosfilt_zi
        if self.k <= 0 or not len(x):
            return x
        x = x.astype(np.float32)
        if self.zi_hp is None:
            self.zi_hp = sosfilt_zi(self.hp) * 0
            self.zi_pr = sosfilt_zi(self.presence) * 0
        y, self.zi_hp = sosfilt(self.hp, x, zi=self.zi_hp)
        pres, self.zi_pr = sosfilt(self.presence, y, zi=self.zi_pr)
        y = y + 0.35 * self.k * pres
        buf = np.concatenate([self.hist, y.astype(np.float32)])
        n, off = len(y), len(self.hist)
        idx = np.arange(n) + off
        # хорус: задержка 5–9 мс плавно «плавает» → лёгкий металлический отлив
        t = (self.t + np.arange(n)) / self.sr
        d = (0.007 + 0.002 * np.sin(2 * np.pi * 0.6 * t)) * self.sr
        pos = idx - d
        i0 = np.floor(pos).astype(int)
        frac = pos - i0
        chorus = buf[i0] * (1 - frac) + buf[i0 + 1] * frac
        out = y + 0.28 * self.k * chorus
        for dly, g in self.taps:
            out = out + g * self.k * buf[idx - dly]
        self.hist = buf[-len(self.hist):].copy()
        self.t += n
        # ровная громкость: мягкий ограничитель
        out = np.tanh(out * 1.15) / np.tanh(1.15)
        return (out * 0.9).astype(np.float32)


def reference_text(wav) -> str:
    """Текст образца голоса. Если рядом с WAV нет готового .txt — его распознаёт Whisper (и запоминаем)."""
    from pathlib import Path
    wav = Path(wav)
    side = wav.with_suffix(".txt")
    try:
        t = side.read_text(encoding="utf-8").strip()
        if t:
            return t
    except Exception:
        pass
    import gc
    import soundfile as sf
    import torch  # noqa: F401 — библиотеки видеокарты для Whisper
    from faster_whisper import WhisperModel
    from scipy.signal import resample_poly
    a, sr = sf.read(str(wav), dtype="float32")
    if a.ndim > 1:
        a = a.mean(1)
    g = np.gcd(16000, sr)
    a = resample_poly(a, 16000 // g, sr // g).astype(np.float32)
    m = WhisperModel(str(config.WHISPER_DIR), device="cuda", compute_type="int8_float16")
    segs, _ = m.transcribe(a, beam_size=5)
    t = " ".join(s.text.strip() for s in segs).strip()
    del m
    gc.collect()
    try:
        side.write_text(t, encoding="utf-8")
    except Exception:
        pass
    return t


class _QwenCloneEngine:
    """Qwen3-TTS Base: живой голос, склонированный с образца (WAV). Говорит по-русски, по-немецки и по-английски
    одним и тем же голосом. Звук идёт маленькими порциями — первый звук очень быстро."""
    LANGS = {"ru": "russian", "de": "german", "en": "english"}

    def __init__(self):
        self.ref = str(config.VOICE_REFERENCE_WAV)
        self.ref_text = reference_text(self.ref)
        self._load()

    def _load(self):
        from faster_qwen3_tts import FasterQwen3TTS
        self.tts = FasterQwen3TTS.from_pretrained(str(config.QWEN_CLONE_DIR))
        self.tts.warmup()
        for _ in self.synthesize("Проверка связи, всё на месте."):   # прогрев с образцом голоса
            pass

    def synthesize(self, text: str, fast: bool = False, lang: str = "ru") -> Iterator[np.ndarray]:
        for chunk, _sr, _ in self.tts.generate_voice_clone_streaming(
            text=text, language=self.LANGS.get(lang, "russian"), ref_audio=self.ref, ref_text=self.ref_text,
            temperature=config.VOICE_TEMPERATURE,
        ):
            yield np.asarray(chunk, dtype=np.float32).reshape(-1)

    def to(self, device: str) -> None:
        """Режим игр: на «cpu» — выгрузить с видеокарты совсем, на «cuda» — загрузить заново (~10 сек)."""
        import gc
        import torch
        if device == "cpu":
            self.tts = None
            gc.collect()
            torch.cuda.empty_cache()
        elif self.tts is None:
            self._load()


class _PiperEngine:
    """Голоса для других языков (Piper, на процессоре, очень быстрые): немецкий, английский, украинский."""
    VOICES = {"de": ("de_DE-thorsten-medium", None), "en": ("en_US-ryan-medium", None),
              "uk": ("uk_UA-ukrainian_tts-medium", 1)}   # украинский: голос «Микита» (мужской)

    def __init__(self, lang: str):
        from piper import PiperVoice
        name, self.speaker = self.VOICES[lang]
        self.voice = PiperVoice.load(str(config.PROJECT_DIR / "models" / "voice" / f"{name}.onnx"), use_cuda=False)

    def synthesize(self, text: str, fast: bool = False) -> Iterator[np.ndarray]:
        from scipy.signal import resample_poly
        from piper import SynthesisConfig
        cfg = SynthesisConfig(speaker_id=self.speaker) if self.speaker is not None else None
        for chunk in self.voice.synthesize(text, syn_config=cfg):
            a = np.asarray(chunk.audio_float_array, dtype=np.float32)
            if chunk.sample_rate != SAMPLE_RATE:      # приводим к частоте нашего проигрывателя
                g = np.gcd(SAMPLE_RATE, chunk.sample_rate)
                a = resample_poly(a, SAMPLE_RATE // g, chunk.sample_rate // g).astype(np.float32)
            yield a * 0.8


class _QwenEngine:
    """Qwen3-TTS (ускоренная версия). Выдаёт звук маленькими порциями."""

    def __init__(self):
        from faster_qwen3_tts import FasterQwen3TTS
        self.tts = FasterQwen3TTS.from_pretrained(str(config.VOICE_MODEL_DIR))
        self.tts.warmup()

    def synthesize(self, text: str, fast: bool = False) -> Iterator[np.ndarray]:
        for chunk, _sr, _ in self.tts.generate_custom_voice_streaming(
            text=text, speaker=config.VOICE_SPEAKER, language="russian"
        ):
            yield np.asarray(chunk, dtype=np.float32).reshape(-1)


# ---------- сам голос ----------
class Voice:
    def __init__(self):
        self._text_q: queue.Queue = queue.Queue()
        self._audio_q: queue.Queue = queue.Queue()
        self._interrupt = threading.Event()
        self._ready = threading.Event()
        self.error: Exception | None = None
        self.is_speaking = False
        self.level = 0.0  # текущая громкость речи 0..1 (для пульсации шара)
        self._engine = None
        self._piper: dict = {}      # голоса для других языков (загружаются, когда понадобятся)
        self._lang = "ru"
        self._engine_lock = threading.Lock()
        self._on_cpu = False
        self._output_latency = 0.2

        threading.Thread(target=self._synth_loop, daemon=True, name="voice-synth").start()
        threading.Thread(target=self._play_loop, daemon=True, name="voice-play").start()

    # --- публичные методы ---
    def wait_ready(self, timeout: float | None = None) -> bool:
        """Ждёт, пока модель голоса загрузится. False — если не загрузилась."""
        self._ready.wait(timeout)
        return self._ready.is_set() and self.error is None

    def say(self, text: str, lang: str = "ru") -> None:
        """Сказать готовый текст целиком."""
        for _ in self.speak_stream([text], lang=lang):
            pass

    def speak_stream(self, pieces: Iterable[str], lang: str = "ru") -> Iterator[str]:
        """Принимает ответ мозга кусочками, озвучивает по предложениям
        и отдаёт те же кусочки дальше (например, чтобы печатать на экран).
        lang — на каком языке говорить: ru — голос Мартышки, de/en/uk — голоса Piper."""
        self._lang = lang
        buffer, pending, first = "", "", True
        for piece in pieces:
            yield piece
            buffer += piece
            sentences, buffer = split_ready_sentences(buffer)
            # Первая фраза длинная, а точки всё нет — начинаем говорить уже с первой запятой (быстрее звук)
            if first and not sentences and not pending and len(buffer) > 30:
                m = re.search(r"^(.{12,}?[,—:;])\s", buffer)
                if m:
                    sentences, buffer = [m.group(1)], buffer[m.end():]
            for s in sentences:
                pending = f"{pending} {s}".strip()
                # Первую фразу отправляем сразу (и в быстром качестве), чтобы Мартышка
                # заговорила как можно раньше. Дальше копим хотя бы MIN_CHUNK символов.
                if len(pending) >= (8 if first else MIN_CHUNK):
                    self._enqueue(pending, fast=first)
                    pending, first = "", False
        rest = f"{pending} {buffer}".strip()
        if rest:
            self._enqueue(rest, fast=first)

    def wait_done(self) -> None:
        """Ждёт, пока Мартышка договорит — до последнего звука в наушниках."""
        self._text_q.join()
        self._audio_q.join()
        # Звук уже отдан звуковой карте, но ещё доигрывает из её буфера
        import time
        time.sleep(self._output_latency + 0.15)

    def free_gpu(self) -> None:
        """Режим игр: убрать голос с видеокарты (Мартышка молчит, видеокарта свободна)."""
        if self._engine is not None and hasattr(self._engine, "to"):
            self.wait_done()
            with self._engine_lock:
                self._engine.to("cpu")
                self._on_cpu = True

    def use_gpu(self) -> None:
        """Конец режима игр: вернуть голос на видеокарту."""
        if self._engine is not None and hasattr(self._engine, "to"):
            with self._engine_lock:
                self._engine.to("cuda")
                self._on_cpu = False

    def stop(self) -> None:
        """Заткнуть Мартышку на полуслове."""
        self._interrupt.set()
        for q in (self._text_q, self._audio_q):
            try:
                while True:
                    q.get_nowait()
                    q.task_done()
            except queue.Empty:
                pass

    # --- внутренняя кухня ---
    def _enqueue(self, sentence: str, fast: bool = False) -> None:
        if not config.VOICE_ENABLED:   # голос выключен в настройках — отвечаем только текстом
            return
        lang = getattr(self, "_lang", "ru")
        clean = prepare_text(sentence, lang)
        if clean and re.search(r"\w", clean):
            self._interrupt.clear()
            self._text_q.put((clean, fast, lang))

    def _engine_for(self, lang: str):
        """Русский — основной голос Мартышки; другие языки — голоса Piper (загружаются при первой надобности)."""
        if lang == "ru" or lang not in _PiperEngine.VOICES:
            return self._engine
        if isinstance(self._engine, _QwenCloneEngine) and lang in _QwenCloneEngine.LANGS:
            return self._engine          # тот же живой голос, только на другом языке
        if lang not in self._piper:
            try:
                self._piper[lang] = _PiperEngine(lang)
            except Exception:
                return self._engine
        return self._piper[lang]

    def _synth_loop(self) -> None:
        try:
            engine = {"qwen": _QwenEngine, "qwen_clone": _QwenCloneEngine}.get(config.VOICE_ENGINE, _ESpeechEngine)()
        except Exception as e:  # голос не завёлся — Мартышка будет молча писать текстом
            self.error = e
            self._ready.set()
            return
        self._engine = engine
        self._ready.set()
        fx = VoiceFX(amount=config.VOICE_FX) if config.VOICE_FX > 0 else None

        while True:
            text, fast, lang = self._text_q.get()
            if fx is not None:
                fx.reset()
            try:
                if not self._interrupt.is_set():
                    with self._engine_lock:   # пока говорим — голос нельзя убрать с видеокарты
                        eng = self._engine_for(lang)
                        pieces = (eng.synthesize(text, fast=fast, lang=lang) if isinstance(eng, _QwenCloneEngine)
                                  else eng.synthesize(text, fast=fast))
                        for audio in pieces:
                            if self._interrupt.is_set():
                                break
                            self._audio_q.put(fx.process(audio) if fx is not None else audio)
            except Exception as e:
                self.error = e
            finally:
                self._text_q.task_done()

    def _play_loop(self) -> None:
        import sounddevice as sd
        stream = sd.OutputStream(
            samplerate=SAMPLE_RATE, channels=1, dtype="float32",
            device=config.VOICE_OUTPUT_DEVICE,
        )
        stream.start()
        self._output_latency = float(stream.latency or 0.2)
        piece = SAMPLE_RATE // 20  # по 50 мс: так шар пульсирует в такт, а «заткнись» срабатывает мгновенно
        while True:
            audio = self._audio_q.get()
            try:
                audio = np.clip(audio * config.VOICE_VOLUME, -1, 1).astype(np.float32)
                for i in range(0, len(audio), piece):
                    if self._interrupt.is_set():
                        break
                    self.is_speaking = True
                    chunk = audio[i:i + piece]
                    self.level = float(min(1.0, np.sqrt(np.mean(chunk ** 2)) * 4))  # громкость для шара
                    stream.write(chunk.reshape(-1, 1))
            finally:
                self._audio_q.task_done()
                if self._audio_q.empty() and self._text_q.unfinished_tasks == 0:
                    self.is_speaking = False
                    self.level = 0.0
