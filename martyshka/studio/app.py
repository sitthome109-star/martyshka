"""
ОКНО СТУДИИ: pywebview (встроенный в Windows браузер Edge WebView2) + мост окно ↔ агент.

Окно вызывает методы класса Api (send, stop, answer…), а Python шлёт окну события через window.evaluate_js.
Текст ответа приходит маленькими кусочками — склеиваем их и отправляем пачками, чтобы окно не тормозило.
"""
from __future__ import annotations

import json
import os
import queue
import threading
import time
import uuid
from pathlib import Path

from .. import config
from . import agent_loop, brains, tools

UI = Path(__file__).parent / "ui" / "index.html"
SETTING_KEYS = ["STUDIO_TASK_BRAIN", "STUDIO_CLAUDE_KEY", "STUDIO_CLAUDE_MODEL", "STUDIO_CLAUDE_EFFORT",
                "STUDIO_OLLAMA_CLOUD_MODEL", "STUDIO_LOCAL_AGENT_MODEL", "STUDIO_AUTO_COMMANDS", "WEB_ENABLED"]


def _helper_llm(prompt: str, max_tokens: int = 1500) -> str:
    """Служебный мозг для «рук» и картинок (перевод описания на английский и т.п.)."""
    import ollama
    r = ollama.Client().chat(model=config.MODEL, keep_alive=config.KEEP_ALIVE,
                             messages=[{"role": "system", "content": "Ты аккуратный помощник. Отвечай строго в "
                                        "запрошенном формате, без пояснений и без markdown."},
                                       {"role": "user", "content": prompt}],
                             options={"num_ctx": 4096, "temperature": 0.3, "num_predict": max_tokens})
    return r["message"]["content"].strip()


class Api:
    def __init__(self):
        self._window = None
        self._events: queue.Queue = queue.Queue()
        self._answers: dict[str, tuple[threading.Event, list]] = {}
        self._stop = threading.Event()
        self._busy = False
        self._chat = agent_loop.Chat()
        threading.Thread(target=self._pump, daemon=True).start()

    def attach(self, window) -> None:
        self._window = window

    # ---------- события → окно ----------
    def _emit(self, ev: dict) -> None:
        self._events.put(ev)

    def _pump(self) -> None:
        while True:
            ev = self._events.get()
            batch = [ev]
            time.sleep(0.03)
            while not self._events.empty():
                batch.append(self._events.get_nowait())
            merged: list[dict] = []
            for e in batch:                              # склеиваем подряд идущие кусочки текста
                if merged and e["type"] in ("text", "progress") and merged[-1]["type"] == e["type"]:
                    merged[-1] = dict(merged[-1], text=merged[-1]["text"] + e["text"])
                else:
                    merged.append(e)
            if self._window is not None:
                try:
                    self._window.evaluate_js(f"window.onEvents({json.dumps(merged, ensure_ascii=False)})")
                except Exception:
                    pass

    # ---------- вызовы из окна ----------
    def init(self) -> dict:
        return {"chats": agent_loop.Chat.list_all(), "brains": self.brain_info(), "name": config.NAME,
                "workspace": str(tools.workspace())}

    def brain_info(self) -> dict:
        try:
            task = brains.task_brain().label
        except Exception as e:
            task = f"ошибка: {e}"
        return {"task": task, "chat": f"{config.MODEL} (на ПК)", "claude": brains.claude_ready()}

    def list_chats(self) -> list:
        return agent_loop.Chat.list_all()

    def new_chat(self) -> dict:
        if self._busy:
            return {"ok": False}
        self._chat = agent_loop.Chat()
        return {"ok": True, "id": self._chat.id}

    def open_chat(self, chat_id: str) -> dict:
        if self._busy:
            return {"ok": False}
        self._chat = agent_loop.Chat(chat_id)
        display = []
        for item in self._chat.display:              # картинки показываем заново из файлов
            if item.get("type") == "tool" and item.get("name") == "draw_image" and item.get("file"):
                p = Path(item["file"])
                item = dict(item, image=tools._thumb(p) if p.exists() else "")
            display.append(item)
        return {"ok": True, "id": self._chat.id, "title": self._chat.title, "display": display}

    def delete_chat(self, chat_id: str) -> dict:
        if self._busy and chat_id == self._chat.id:
            return {"ok": False}
        agent_loop.Chat(chat_id).remove()
        if chat_id == self._chat.id:
            self._chat = agent_loop.Chat()
        return {"ok": True}

    def send(self, text: str, mode: str = "auto") -> dict:
        text = (text or "").strip()
        if not text or self._busy:
            return {"ok": False}
        self._busy = True
        self._stop.clear()

        def work():
            try:
                self._chat.run(text, mode, self._emit, self._ask, self._stop.is_set)
            except Exception as e:
                self._emit({"type": "error", "text": f"Сбой: {type(e).__name__}: {e}"})
                self._emit({"type": "done"})
            finally:
                self._busy = False
        threading.Thread(target=work, daemon=True).start()
        return {"ok": True, "id": self._chat.id}

    def stop(self) -> None:
        self._stop.set()
        for ev, box in list(self._answers.values()):     # висящие вопросы — считаем «нет»
            box.append("no")
            ev.set()

    def _ask(self, question: str) -> str:
        qid = uuid.uuid4().hex[:8]
        ev, box = threading.Event(), []
        self._answers[qid] = (ev, box)
        self._emit({"type": "confirm", "id": qid, "text": question})
        ev.wait()
        self._answers.pop(qid, None)
        return box[0] if box else "no"

    def answer(self, qid: str, value: str) -> None:
        if qid in self._answers:
            ev, box = self._answers[qid]
            box.append(value)
            ev.set()

    def open_path(self, path: str) -> None:
        try:
            os.startfile(path)
        except Exception:
            pass

    def open_workspace(self) -> None:
        os.startfile(str(tools.workspace()))

    # ---------- настройки ----------
    def get_settings(self) -> dict:
        s = {k: getattr(config, k, "") for k in SETTING_KEYS}
        key = s.get("STUDIO_CLAUDE_KEY") or ""
        s["STUDIO_CLAUDE_KEY"] = ("•" * 8 + key[-4:]) if key else ""     # сам ключ в окно не возвращаем
        s["anthropic_installed"] = self._has_anthropic()
        return s

    @staticmethod
    def _has_anthropic() -> bool:
        try:
            import anthropic  # noqa: F401
            return True
        except ImportError:
            return False

    def save_settings(self, values: dict) -> dict:
        try:
            saved = json.loads(config.SETTINGS_FILE.read_text(encoding="utf-8"))
        except Exception:
            saved = {}
        for k, v in values.items():
            if k not in SETTING_KEYS:
                continue
            if k == "STUDIO_CLAUDE_KEY" and isinstance(v, str) and v.startswith("•"):
                continue                                    # ключ не меняли
            saved[k] = v
            setattr(config, k, v)
        config.DATA_DIR.mkdir(parents=True, exist_ok=True)
        config.SETTINGS_FILE.write_text(json.dumps(saved, ensure_ascii=False, indent=2), encoding="utf-8")
        brains._caps_cache.clear()
        return {"ok": True, "brains": self.brain_info()}


def main() -> None:
    import webview
    from .. import hands, media
    hands.set_llm(_helper_llm)
    media.set_hooks(llm=_helper_llm)
    api = Api()
    window = webview.create_window(f"{config.NAME} — Студия", str(UI), js_api=api, width=1280, height=860,
                                   min_size=(820, 560), background_color="#0b0d12")
    api.attach(window)
    webview.start(gui="edgechromium", debug=bool(os.environ.get("STUDIO_DEBUG")))
