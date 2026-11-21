"""
ПАМЯТЬ МАРТЫШКИ
Хранит на диске (в папке data):
  facts.json   — факты о брате («любит пиццу», «зовут Саня»)
  history.json — последние реплики разговора
Это обычные текстовые файлы — их можно открыть Блокнотом.
"""
import json
from datetime import datetime
from pathlib import Path

from . import config


def _load(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def _save(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)  # сначала пишем во временный файл, чтобы не испортить память при сбое


STOP_WORDS = {"брат", "брата", "брату", "братан", "его", "что", "это", "про", "который", "очень"}


def _words(text: str) -> set[str]:
    """Значимые слова без окончаний: «рыбалку» и «рыбалка» → «рыбал»."""
    words = "".join(c if c.isalnum() else " " for c in text.lower().replace("ё", "е")).split()
    return {w[:5] for w in words if len(w) > 2 and w not in STOP_WORDS}


class Memory:
    def __init__(self, data_dir: Path = config.DATA_DIR):
        self.facts_path = data_dir / "facts.json"
        self.history_path = data_dir / "history.json"
        self.facts: list[dict] = _load(self.facts_path, [])
        self.history: list[dict] = _load(self.history_path, [])

    # ---------- факты ----------
    def fact_texts(self) -> list[str]:
        return [f["text"] for f in self.facts]

    def remember(self, text: str) -> bool:
        text = text.strip().rstrip(".")
        if not text:
            return False
        new_words = _words(text)
        for f in self.facts:  # не дублируем почти одинаковые факты
            old_words = _words(f["text"])
            if f["text"].lower() == text.lower() or (
                new_words and len(new_words & old_words) / len(new_words) >= 0.8
            ):
                return False
        self.facts.append({"text": text, "added": datetime.now().isoformat(timespec="seconds")})
        _save(self.facts_path, self.facts)
        return True

    def forget(self, what: str) -> list[str]:
        """Удаляет факты, похожие на `what`. Возвращает, что удалено."""
        what_l = what.strip().lower()
        what_words = _words(what)
        removed, kept = [], []
        for f in self.facts:
            t = f["text"].lower()
            overlap = len(what_words & _words(t)) / len(what_words) if what_words else 0
            if what_l and (what_l in t or t in what_l or overlap >= 0.5):
                removed.append(f["text"])
            else:
                kept.append(f)
        if removed:
            self.facts = kept
            _save(self.facts_path, self.facts)
        return removed

    def forget_everything(self) -> None:
        self.facts = []
        self.history = []
        _save(self.facts_path, self.facts)
        _save(self.history_path, self.history)

    # ---------- история разговора ----------
    def add_message(self, role: str, content: str) -> None:
        # голос и окно чата (Telegram) пишут в один файл — сначала подхватываем чужие свежие реплики
        disk = _load(self.history_path, None)
        if isinstance(disk, list) and len(disk) >= len(self.history):
            self.history = disk
        self.history.append({"role": role, "content": content})
        self.history = self.history[-config.HISTORY_ON_DISK:]
        _save(self.history_path, self.history)

    def recent_history(self) -> list[dict]:
        return self.history[-config.HISTORY_IN_PROMPT:]
