"""
УШИ МАРТЫШКИ

Как это работает:
  1. Микрофон слушает постоянно. Маленькая модель Vosk (на процессоре) ждёт слово «Мартышка».
     Ничего, кроме этого, она никуда не отправляет — всё остаётся на компьютере.
  2. Услышала имя → пищит и записывает твою фразу, пока ты не замолчишь (детектор речи Silero).
  3. Запись уходит в DeepSeek (в облаке), он превращает речь в текст.
  4. После ответа Мартышка ещё несколько секунд слушает без имени — можно продолжить разговор.

События, которые уши отдают в очередь `events`:
  ("wake", None)        — услышала имя (можно затыкать голос — тебя перебили)
  ("audio", (np, why))  — записана фраза; why = "wake" (после имени) или "followup" (продолжение)
  ("timeout", None)     — ждала продолжения, но ты промолчал
"""
import collections
import json
import queue
import re
import threading
import time

import numpy as np

from . import config

SR = 16000
BLOCK = 1600  # 0,1 секунды

# Формы имени, на которые реагируем. «мартышки» (мн. ч.) — нет: «в зоопарке мартышки» — это не к нам.
WAKE_RE = re.compile(r"\bмартыш(?:ка|ку|ек|ке|кой|ко)\b", re.IGNORECASE)
# Перепроверка в Whisper: первую букву он иногда «съедает» («Артышка», «Партышка») — принимаем любую
#  (в немецкой/английской фразе имя может записаться латиницей: «Martyschka», «Martyshka»)
NAME_IN_TEXT_RE = re.compile(r"ртыш|rt[yiu]sch|rt[yi]sh|rtich", re.IGNORECASE)

# Что Whisper иногда «слышит» в тишине — выкидываем
HALLUCINATIONS = (
    "продолжение следует", "субтитры", "редактор субтитров", "спасибо за просмотр",
    "подписывайтесь", "dimatorzok", "благодарю за внимание",
)


def strip_wake_word(text: str) -> str:
    """«Слушай, Мартышка, включи музыку» → «Слушай, включи музыку»."""
    text = re.sub(r"(?i)\s*\b(?:эй|ну|слышь|hey|hallo)?,?\s*(?:[а-яё]{0,3}ртыш\w*|\w{0,3}rt[yiu]s?c?h\w*|\w{0,3}rtich\w*)"
                  r"\s*[,.!?…]*", " ", text)
    text = re.sub(r"\s+([,.!?])", r"\1", text)
    text = re.sub(r"\s+", " ", text).strip(" ,.-—")
    return text[:1].upper() + text[1:]


def speech_seconds(audio: np.ndarray) -> float:
    """Сколько секунд настоящей речи в записи (без тишины и шорохов)."""
    from faster_whisper.vad import VadOptions, get_speech_timestamps
    stamps = get_speech_timestamps(audio.astype(np.float32), VadOptions(threshold=0.5, min_speech_duration_ms=150))
    return sum(s["end"] - s["start"] for s in stamps) / SR


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[а-яёa-z]+", text.lower().replace("ё", "е")) if len(w) > 2}


def looks_like_echo(heard: str, said: str) -> bool:
    """Услышанное почти целиком состоит из слов, которые Мартышка только что сказала сама."""
    h = _words(heard)
    return bool(h) and len(h & _words(said)) / len(h) >= 0.7


def make_beep() -> np.ndarray:
    """Короткий двойной «пик»."""
    sr = 24000
    def tone(f, dur):
        t = np.arange(int(sr * dur)) / sr
        env = np.minimum(1, np.minimum(t, dur - t) / 0.01)  # плавные края, без щелчков
        return 0.18 * np.sin(2 * np.pi * f * t) * env
    return np.concatenate([tone(880, 0.07), np.zeros(int(sr * 0.03)), tone(1320, 0.09)]).astype(np.float32)


# ---------- распознавание речи (DeepSeek, в облаке) ----------
DE_WORDS = re.compile(r"\b(ich|nicht|und|der|die|das|ist|wie|was|bitte|danke|hallo|mach|kannst|du|mir|ein|eine|"
                      r"heute|morgen|warum|wo|wann)\b|[äöüß]", re.I)


def guess_language(text: str) -> str:
    """На каком языке сказано: по буквам и частым словам."""
    t = text.lower()
    cyr, lat = len(re.findall(r"[а-яёіїєґ]", t)), len(re.findall(r"[a-zäöüß]", t))
    if cyr >= lat:
        return "uk" if re.search(r"[іїєґ]", t) and not re.search(r"[ыэъё]", t) else "ru"
    return "de" if len(DE_WORDS.findall(t)) >= 1 else "en"


class Transcriber:
    """Речь → текст через DeepSeek ASR. Видеокарту не занимает; язык определяется сам."""
    def __init__(self):
        self.last_language = "ru"
        self.last_time = 0.0
        self.last_probs = {}
        self.last_candidates = None

    def load(self) -> None:
        pass

    def unload(self) -> None:
        pass

    def transcribe(self, audio: np.ndarray, strict: bool = False) -> str:
        from . import deepseek
        audio = np.asarray(audio, dtype=np.float32).reshape(-1)
        if len(audio) < SR * 0.3:
            return ""
        pad = np.zeros(int(SR * 0.2), dtype=np.float32)
        try:
            text = deepseek.transcribe(np.concatenate([pad, audio, pad]), SR)
        except Exception as e:
            from . import errlog
            errlog.write("уши не сработали", ошибка=str(e)[:300])
            return ""
        text = (text or "").strip()
        if any(h in text.lower() for h in HALLUCINATIONS):
            return ""
        if text:
            self.last_language = guess_language(text) if config.EARS_MULTILANG else "ru"
            self.last_time = time.time()
        return text


# ---------- уши ----------
class Ears:
    def __init__(self, on_wake=None):
        from faster_whisper.vad import get_vad_model
        from vosk import KaldiRecognizer, Model, SetLogLevel

        SetLogLevel(-1)
        self._vosk_model = Model(str(config.VOSK_DIR))
        self._vosk = KaldiRecognizer(self._vosk_model, SR)
        self._vad = get_vad_model()
        self.on_wake = on_wake  # вызывается сразу из потока ушей (чтобы мгновенно заткнуть голос)
        self.events: queue.Queue = queue.Queue()

        self._blocks: queue.Queue = queue.Queue()
        self._lock = threading.Lock()
        self._mode = "wake"        # wake | followup | capture
        self._capture: list = []
        self._capture_why = "wake"
        self._heard_speech = False
        self._silence = 0.0
        self._deadline = 0.0
        self._preroll = collections.deque(maxlen=25)   # 2,5 секунды назад
        self._listen_from = 0.0  # в режиме продолжения не слушаем раньше этого момента
        self._followup_kind = "followup"
        self._vad_window = collections.deque(maxlen=15)  # последние ~0,5 сек для детектора речи
        self._speech_run = 0
        self._stream = None
        self._resample = None

    # --- управление из основной программы ---
    def start(self) -> None:
        self._open_mic()
        threading.Thread(target=self._loop, daemon=True, name="ears").start()

    def expect_followup(self, seconds: float | None = None) -> None:
        """После ответа: слушать продолжение без имени."""
        with self._lock:
            if self._mode == "wake":
                self._begin_followup(seconds or config.FOLLOWUP_SECONDS)

    def listen_now(self, wait_seconds: float = 5.0) -> None:
        """Сразу начать запись (например, если сказали только «Мартышка» и замолчали)."""
        with self._lock:
            self._begin_followup(wait_seconds, kind="named")

    def _begin_followup(self, seconds: float, kind: str = "followup") -> None:
        self._followup_kind = kind     # "named" — после имени (без строгих проверок), "followup" — продолжение
        self._mode = "followup"
        self._deadline = time.time() + seconds
        self._speech_run = 0
        # Забываем всё, что было до этого момента (хвост голоса Мартышки, шорохи)
        self._preroll.clear()
        self._vad_window.clear()
        self._listen_from = time.time() + 0.2

    def transcribe_light(self, audio: np.ndarray) -> str:
        """Распознать фразу лёгкой моделью Vosk (на процессоре) — для режима игр, когда Whisper выгружен."""
        from vosk import KaldiRecognizer
        rec = KaldiRecognizer(self._vosk_model, SR)
        rec.AcceptWaveform((np.clip(audio, -1, 1) * 32767).astype(np.int16).tobytes())
        return json.loads(rec.FinalResult()).get("text", "")

    def go_to_sleep(self) -> None:
        with self._lock:
            self._mode = "wake"
            self._vosk.Reset()

    # --- внутренняя кухня ---
    def _open_mic(self) -> None:
        import sounddevice as sd

        def callback(indata, frames, time_info, status):
            self._blocks.put(indata[:, 0].copy())

        try:
            self._stream = sd.InputStream(samplerate=SR, channels=1, dtype="float32", blocksize=BLOCK,
                                          device=config.MIC_DEVICE, callback=callback)
        except Exception:
            # микрофон не умеет 16 кГц — пишем в его родной частоте и пересчитываем сами
            from scipy.signal import resample_poly
            native = int(sd.query_devices(config.MIC_DEVICE, "input")["default_samplerate"])
            self._resample = lambda x: resample_poly(x, SR, native).astype(np.float32)
            self._stream = sd.InputStream(samplerate=native, channels=1, dtype="float32",
                                          blocksize=int(native * 0.1), device=config.MIC_DEVICE, callback=callback)
        self._stream.start()

    def _speech_prob(self, block: np.ndarray) -> float:
        self._vad_window.append(block)
        window = np.concatenate(self._vad_window)
        n = (len(window) // 512) * 512
        if n == 0:
            return 0.0
        probs = self._vad(window[-n:]).reshape(-1)
        return float(probs[-3:].mean())  # последние ~0,1 сек

    def _check_wake(self, block: np.ndarray) -> bool:
        pcm = (np.clip(block, -1, 1) * 32767).astype(np.int16).tobytes()
        if self._vosk.AcceptWaveform(pcm):
            text = json.loads(self._vosk.Result()).get("text", "")
        else:
            text = json.loads(self._vosk.PartialResult()).get("partial", "")
        if WAKE_RE.search(text):
            self._vosk.Reset()
            return True
        return False

    def _start_capture(self, why: str, preroll_blocks: int) -> None:
        self._mode = "capture"
        self._capture_why = why
        self._capture = list(self._preroll)[-preroll_blocks:]
        self._heard_speech = why == "wake"  # имя уже прозвучало — значит, речь была
        self._silence = 0.0
        self._deadline = time.time() + config.MAX_UTTERANCE_SECONDS

    def _finish_capture(self) -> None:
        audio = np.concatenate(self._capture) if self._capture else np.zeros(0, np.float32)
        self._capture = []
        self._mode = "wake"
        self._vosk.Reset()
        self.events.put(("audio", (audio, self._capture_why)))

    def _loop(self) -> None:
        while True:
            block = self._blocks.get()
            if self._resample is not None:
                block = self._resample(block)
            self._preroll.append(block)
            prob = self._speech_prob(block)
            is_speech = prob >= config.SPEECH_THRESHOLD

            with self._lock:
                mode = self._mode
                # Имя слушаем всегда, кроме момента записи фразы — так можно перебить Мартышку
                if mode in ("wake", "followup") and self._check_wake(block):
                    self._start_capture("wake", preroll_blocks=22)
                    if self.on_wake:
                        self.on_wake()
                    self.events.put(("wake", None))
                    continue

                if mode == "followup":
                    if time.time() < self._listen_from:
                        continue
                    named = self._followup_kind == "named"   # только что позвали по имени — точно к нам
                    strong = prob >= (config.SPEECH_THRESHOLD if named else max(config.SPEECH_THRESHOLD, 0.6))
                    self._speech_run = self._speech_run + 1 if strong else 0
                    if self._speech_run >= (2 if named else 3):  # начал говорить
                        self._start_capture(self._followup_kind, preroll_blocks=8 if named else 6)
                        self._heard_speech = True
                    elif time.time() > self._deadline:
                        self._mode = "wake"
                        self.events.put(("timeout", None))
                    continue

                if mode == "capture":
                    self._capture.append(block)
                    if is_speech:
                        self._heard_speech = True
                        self._silence = 0.0
                    else:
                        self._silence += BLOCK / SR
                    if (self._heard_speech and self._silence >= config.END_OF_SPEECH_SILENCE) \
                            or time.time() > self._deadline:
                        self._finish_capture()
