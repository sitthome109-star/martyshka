"""
ГОЛОС МАРТЫШКИ — Fish Audio (в облаке, видеокарту не занимает).

Как это работает:
  1. Мозг выдаёт ответ кусочками.
  2. Как только набралось целое предложение — оно уходит в Fish Audio.
  3. Звук идёт в колонки сразу, пока Fish делает следующее предложение.

Эмоции: мозг ставит в тексте метки в фигурных скобках — {laughing}, {sad}, {whisper}… Брат их не видит,
а для Fish они превращаются в [laughing] — и голос смеётся, грустит, шепчет.
Голос выбирается в настройках (VOICE_ID), список — fish.catalog().
"""
import queue
import re
import threading
from typing import Iterable, Iterator

import numpy as np

from . import config, fish

SAMPLE_RATE = fish.SAMPLE_RATE
MIN_CHUNK = 40  # короткие предложения склеиваем со следующими — интонация живее и запросов меньше

CUE_RE = re.compile(r"\{\s*([a-zA-Zа-яА-ЯёЁ][^{}]{0,90}?)\s*\}")
# парные метки Drama 3 / S2: меняют подачу только обёрнутых слов (Fish понимает и в бесплатной модели)
PAIR_TAGS = ("whisper", "emphasis", "soft", "fast", "slow", "stress", "forceful")
PAIR_RE = re.compile(r"</?\s*(?:" + "|".join(PAIR_TAGS) + r")\s*>", re.I)


def strip_cues(text: str) -> str:
    """Убрать метки эмоций и подачи из текста для экрана/журнала."""
    return re.sub(r"\s{2,}", " ", PAIR_RE.sub("", CUE_RE.sub("", text)))


class CueFilter:
    """Убирает {метки} из потока кусочков (метка может прийти разорванной: «{laug» + «hing}»)."""
    def __init__(self):
        self.hold = ""

    def feed(self, piece: str) -> str:
        s = self.hold + piece
        self.hold = ""
        for o, c, lim in (("{", "}", 95), ("<", ">", 14)):     # недописанная метка — придержать
            cut = s.rfind(o)
            if cut != -1 and c not in s[cut:] and len(s) - cut < lim:
                s, self.hold = s[:cut], s[cut:] + self.hold
        return strip_cues(s)

    def flush(self) -> str:
        s, self.hold = strip_cues(self.hold), ""
        return s


def prepare_text(text: str, lang: str = "ru") -> str:
    """Готовим текст для Fish: метки эмоций {x} → [x], разметку и эмодзи убираем. Числа Fish читает сам."""
    text = re.sub(r"\[[^\]]*\]", " ", text)                         # служебные метки дел — не читаем
    text = CUE_RE.sub(lambda m: f"[{m.group(1).strip()}]", text)   # эмоции и подача — Fish понимает [вздох]
    pairs = {}
    def keep(m):                                                    # парные метки сохраняем как есть
        pairs[f"\x00{len(pairs)}\x00"] = m.group(0).lower().replace(" ", "")
        return f" \x00{len(pairs) - 1}\x00 "
    text = PAIR_RE.sub(keep, text)
    text = re.sub(r"[*_#`~<>|{}+]", " ", text)
    for k, v in pairs.items():
        text = text.replace(k, v)
    text = re.sub(r"[\U0001F000-\U0001FFFF☀-➿]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


# Конец предложения: . ! ? … (можно с кавычкой/скобкой) и пробел после
_SENTENCE_END = re.compile(r"[.!?…]+[\"»)]*\s+")


def split_ready_sentences(buffer: str, max_len: int = 200) -> tuple[list[str], str]:
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


class Voice:
    def __init__(self, voice_id: str | None = None, output_device=None):
        self._text_q: queue.Queue = queue.Queue()
        self._audio_q: queue.Queue = queue.Queue()
        self._interrupt = threading.Event()
        self._ready = threading.Event()
        self.error: Exception | None = None
        self.is_speaking = False
        self.level = 0.0  # текущая громкость речи 0..1 (для пульсации шара)
        self.voice_id = voice_id
        self.output_device = output_device if output_device is not None else config.VOICE_OUTPUT_DEVICE
        self._lang = "ru"
        self._output_latency = 0.2
        if not fish.key():
            self.error = fish.FishError("Нет ключа Fish Audio — голос выключен, отвечаю текстом.")
        self._ready.set()
        threading.Thread(target=self._synth_loop, daemon=True, name="voice-synth").start()
        threading.Thread(target=self._play_loop, daemon=True, name="voice-play").start()

    # --- публичные методы ---
    def wait_ready(self, timeout: float | None = None) -> bool:
        self._ready.wait(timeout)
        return self.error is None

    def say(self, text: str, lang: str = "ru") -> None:
        for _ in self.speak_stream([text], lang=lang):
            pass

    def speak_stream(self, pieces: Iterable[str], lang: str = "ru") -> Iterator[str]:
        """Принимает ответ мозга кусочками, озвучивает по предложениям и отдаёт кусочки дальше
        (уже без меток эмоций) — например, чтобы печатать на экран."""
        self._lang = lang
        cf = CueFilter()
        buffer, pending, first = "", "", True
        for piece in pieces:
            shown = cf.feed(piece)
            if shown:
                yield shown
            buffer += piece
            sentences, buffer = split_ready_sentences(buffer)
            # первая фраза длинная, а точки нет — начинаем говорить с первой запятой (быстрее звук)
            if first and not sentences and not pending and len(buffer) > 40:
                m = re.search(r"^(.{15,}?[,—:;])\s", buffer)
                if m:
                    sentences, buffer = [m.group(1)], buffer[m.end():]
            for s in sentences:
                pending = f"{pending} {s}".strip()
                if len(pending) >= (8 if first else MIN_CHUNK):
                    self._enqueue(pending)
                    pending, first = "", False
        tail = cf.flush()
        if tail:
            yield tail
        rest = f"{pending} {buffer}".strip()
        if rest:
            self._enqueue(rest)

    def wait_done(self) -> None:
        """Ждёт, пока Мартышка договорит — до последнего звука."""
        self._text_q.join()
        self._audio_q.join()
        import time
        time.sleep(self._output_latency + 0.15)

    def free_gpu(self) -> None:
        """Голос в облаке — видеокарту не занимает."""

    def use_gpu(self) -> None:
        pass

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
    def _enqueue(self, sentence: str) -> None:
        if not config.VOICE_ENABLED or self.error is not None and not fish.key():
            return
        clean = prepare_text(sentence, self._lang)
        if clean and re.search(r"\w", clean):
            self._interrupt.clear()
            self._text_q.put(clean)

    def _synth_loop(self) -> None:
        while True:
            text = self._text_q.get()
            try:
                if not self._interrupt.is_set():
                    try:
                        from . import longmem
                        mood = longmem.anger(longmem.OWNER)
                    except Exception:
                        mood = 0
                    said, vid, mdl = fish.mood_pick(text, self.voice_id or getattr(config, "VOICE_ID", ""), None, mood)
                    for audio in fish.speak(said, vid, should_stop=self._interrupt.is_set, model=mdl):
                        if self._interrupt.is_set():
                            break
                        self._audio_q.put(audio)
                    self.error = None
            except Exception as e:
                self.error = e
                try:
                    from . import errlog
                    errlog.write("голос не сработал", ошибка=str(e)[:300])
                except Exception:
                    pass
            finally:
                self._text_q.task_done()

    def _play_loop(self) -> None:
        import sounddevice as sd
        stream = sd.OutputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32", device=self.output_device)
        stream.start()
        self._output_latency = float(stream.latency or 0.2)
        piece = SAMPLE_RATE // 20  # по 50 мс: шар пульсирует в такт, «заткнись» срабатывает мгновенно
        while True:
            audio = self._audio_q.get()
            try:
                audio = np.clip(audio * config.VOICE_VOLUME, -1, 1).astype(np.float32)
                for i in range(0, len(audio), piece):
                    if self._interrupt.is_set():
                        break
                    self.is_speaking = True
                    chunk = audio[i:i + piece]
                    self.level = float(min(1.0, np.sqrt(np.mean(chunk ** 2)) * 4))
                    stream.write(chunk.reshape(-1, 1))
            finally:
                self._audio_q.task_done()
                if self._audio_q.empty() and self._text_q.unfinished_tasks == 0:
                    self.is_speaking = False
                    self.level = 0.0
