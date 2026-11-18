"""
ОКНО СТУДИИ: pywebview (встроенный в Windows браузер Edge WebView2) + мост окно ↔ агент.

  - Окно вызывает методы Api (send, stop, answer…), Python шлёт окну события через evaluate_js — пачками.
  - Крестик: окно прячется, а на экране остаётся маленький шарик Мартышки (клик — окно обратно).
    В меню шарика можно закрыть студию совсем. Или в настройках выбрать «закрывать сразу».
  - Одна копия: повторный запуск просто показывает уже открытое окно.
"""
from __future__ import annotations

import ctypes
import json
import os
import queue
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

from .. import config
from . import agent_loop, brains, tools

UI = Path(__file__).parent / "ui" / "index.html"
ORB_UI = Path(__file__).parent / "ui" / "orb.html"
SHOW_FLAG = config.DATA_DIR / "studio" / "показать.flag"
SETTING_KEYS = ["STUDIO_TASK_BRAIN", "STUDIO_CLAUDE_KEY", "STUDIO_CLAUDE_MODEL", "STUDIO_CLAUDE_EFFORT",
                "STUDIO_OLLAMA_CLOUD_MODEL", "STUDIO_LOCAL_AGENT_MODEL", "STUDIO_SUPER_MODEL", "STUDIO_AUTO_COMMANDS",
                "WEB_ENABLED", "STUDIO_DISABLED_GROUPS", "STUDIO_CLOSE_TO_ORB"]


def _helper_llm(prompt: str, max_tokens: int = 1500) -> str:
    """Служебный мозг для «рук» и картинок (перевод описания на английский и т.п.)."""
    import ollama
    r = ollama.Client().chat(model=config.MODEL, keep_alive=config.KEEP_ALIVE,
                             messages=[{"role": "system", "content": "Ты аккуратный помощник. Отвечай строго в "
                                        "запрошенном формате, без пояснений и без markdown."},
                                       {"role": "user", "content": prompt}],
                             options={"num_ctx": int(config.STUDIO_LOCAL_CTX), "temperature": 0.3,
                                      "num_predict": max_tokens})
    return r["message"]["content"].strip()


class Api:
    def __init__(self):
        self._window = None
        self._orb = None
        self._quitting = False
        self._events: queue.Queue = queue.Queue()
        self._answers: dict[str, tuple[threading.Event, list]] = {}
        self._stop = threading.Event()
        self._busy = False
        self._chat = agent_loop.Chat()
        threading.Thread(target=self._pump, daemon=True).start()

    def attach(self, window, orb) -> None:
        self._window, self._orb = window, orb

    # ---------- события → окно ----------
    def _emit(self, ev: dict) -> None:
        self._events.put(ev)
        if self._orb is not None and ev["type"] in ("activity", "done", "confirm"):
            state = "busy" if ev["type"] == "activity" else "ask" if ev["type"] == "confirm" else "idle"
            try:
                self._orb.evaluate_js(f"setState('{state}')")
            except Exception:
                pass

    def _pump(self) -> None:
        while True:
            ev = self._events.get()
            batch = [ev]
            time.sleep(0.03)
            while not self._events.empty():
                batch.append(self._events.get_nowait())
            merged: list[dict] = []
            for e in batch:
                if merged and e["type"] in ("text", "progress") and merged[-1]["type"] == e["type"]:
                    merged[-1] = dict(merged[-1], text=merged[-1]["text"] + e["text"])
                else:
                    merged.append(e)
            if self._window is not None:
                try:
                    self._window.evaluate_js(f"window.onEvents({json.dumps(merged, ensure_ascii=False)})")
                except Exception:
                    pass

    # ---------- окно и шарик ----------
    def on_closing(self):
        """Крестик: спрятать окно в шарик (или закрыть совсем — как выбрано в настройках)."""
        if self._quitting or not getattr(config, "STUDIO_CLOSE_TO_ORB", True):
            self._quit_all()
            return True
        self._window.hide()
        if self._orb is not None:
            self._orb.show()
        return False

    def show_main(self) -> None:
        if self._orb is not None:
            self._orb.hide()
        self._window.show()
        self._window.restore()
        try:
            ctypes.windll.user32.SetForegroundWindow(ctypes.windll.user32.FindWindowW(None, self._window.title))
        except Exception:
            pass

    def quit(self) -> None:
        self._quitting = True
        self.stop()
        threading.Thread(target=self._quit_all, daemon=True).start()

    def restart(self) -> None:
        """Перезапуск Студии (после того как агент поменял её саму): новая копия стартует через 3 секунды,
        когда эта уже закроется."""
        exe = config.PROJECT_DIR / "Мартышка Студия.exe"
        subprocess.Popen(["cmd", "/c", f'timeout /t 3 /nobreak >nul & start "" "{exe}"'],
                         cwd=str(config.PROJECT_DIR), creationflags=0x08000000 | 0x00000008)   # без окна, отдельно
        self.quit()

    def _quit_all(self) -> None:
        self._quitting = True
        for w in (self._orb, self._window):
            try:
                w.destroy()
            except Exception:
                pass

    def _watch_show_flag(self) -> None:
        """Повторный запуск студии кладёт флажок — показываем уже открытое окно."""
        while not self._quitting:
            if SHOW_FLAG.exists():
                try:
                    SHOW_FLAG.unlink()
                except Exception:
                    pass
                self.show_main()
            time.sleep(0.7)

    # ---------- вызовы из окна ----------
    def init(self) -> dict:
        return {"chats": agent_loop.Chat.list_all(), "brains": self.brain_info(), "name": config.NAME,
                "workspace": str(tools.workspace()), "connectors": self.connectors(),
                "memory": tools.memory_facts()[-30:]}

    def brain_info(self) -> dict:
        try:
            task = brains.task_brain().label
            code = brains.code_brain().label
        except Exception as e:
            task = code = f"ошибка: {e}"
        return {"chat": f"{config.MODEL} (на ПК)", "task": task, "code": code,
                "super_ready": bool(brains.super_model()), "loaded": brains.loaded_models(),
                "claude": brains.claude_ready()}

    def connectors(self) -> list:
        off = set(getattr(config, "STUDIO_DISABLED_GROUPS", []) or [])
        out = []
        for key, (title, desc) in tools.GROUPS.items():
            names = [t["name"] for t in tools.TOOLS if t["group"] == key]
            on = key not in off and (key != "web" or config.WEB_ENABLED)
            out.append({"key": key, "title": title, "desc": desc, "on": on, "tools": len(names)})
        return out

    def toggle_connector(self, key: str, on: bool) -> list:
        off = set(getattr(config, "STUDIO_DISABLED_GROUPS", []) or [])
        (off.discard if on else off.add)(key)
        values: dict = {"STUDIO_DISABLED_GROUPS": sorted(off)}
        if key == "web":
            values["WEB_ENABLED"] = bool(on)
        self.save_settings(values)
        return self.connectors()

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
        for item in self._chat.display:
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
            if self._chat.restart_requested:
                self._chat.restart_requested = False
                time.sleep(2.5)                     # чтобы брат успел прочитать ответ
                self.restart()
        threading.Thread(target=work, daemon=True).start()
        return {"ok": True, "id": self._chat.id}

    def stop(self) -> None:
        self._stop.set()
        for ev, box in list(self._answers.values()):
            box.append("no")
            ev.set()

    def _ask(self, info: dict) -> str:
        """Окно разрешения. Ответ: once | scope | task | no."""
        qid = uuid.uuid4().hex[:8]
        ev, box = threading.Event(), []
        self._answers[qid] = (ev, box)
        self._emit(dict(info, type="confirm", id=qid))
        if self._window is not None and self._orb is not None:
            try:
                self.show_main()                 # вопрос важнее — показываем окно, даже если оно в шарике
            except Exception:
                pass
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

    def start_voice(self) -> dict:
        """Запустить голосовую Мартышку (если ещё не запущена — она сама следит, чтобы копия была одна)."""
        exe = config.PROJECT_DIR / ".venv" / "Scripts" / "pythonw.exe"
        subprocess.Popen([str(exe), str(config.PROJECT_DIR / "martyshka_main.py")], cwd=str(config.PROJECT_DIR),
                         creationflags=0x08000000)
        return {"ok": True}

    def forget_fact(self, fact: str) -> list:
        facts = [f for f in tools.memory_facts() if f != fact]
        tools.MEMORY_FILE.write_text(json.dumps(facts, ensure_ascii=False, indent=1), encoding="utf-8")
        return facts[-30:]

    # ---------- настройки ----------
    def get_settings(self) -> dict:
        s = {k: getattr(config, k, "") for k in SETTING_KEYS}
        key = s.get("STUDIO_CLAUDE_KEY") or ""
        s["STUDIO_CLAUDE_KEY"] = ("•" * 8 + key[-4:]) if key else ""
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
                continue
            saved[k] = v
            setattr(config, k, v)
        config.DATA_DIR.mkdir(parents=True, exist_ok=True)
        config.SETTINGS_FILE.write_text(json.dumps(saved, ensure_ascii=False, indent=2), encoding="utf-8")
        brains._caps_cache.clear()
        return {"ok": True, "brains": self.brain_info()}


def _free_gpu() -> None:
    """Перед рисованием выгружаем мозги из видеокарты — иначе картинка рисуется минуты вместо секунд."""
    for m in brains.loaded_models():
        brains.unload(m)


def setup_helpers() -> None:
    from .. import hands, media
    hands.set_llm(_helper_llm)
    media.set_hooks(gpu_before=_free_gpu, gpu_after=None, llm=_helper_llm)


ORB = 84   # размер шарика в пикселях


def _make_round(orb) -> None:
    """Вырезать окно шарика по кругу (прозрачные окна Windows с браузером внутри не умеет)."""
    try:
        from System import Action                          # pythonnet — уже есть внутри pywebview
        from System.Drawing import Region, Size
        from System.Drawing.Drawing2D import GraphicsPath
        form = orb.native

        def apply():
            form.MinimumSize = Size(ORB, ORB)
            form.Size = Size(ORB, ORB)
            gp = GraphicsPath()
            gp.AddEllipse(0, 0, form.Width, form.Height)
            form.Region = Region(gp)
        form.Invoke(Action(apply))
    except Exception:
        pass


def _already_running() -> bool:
    """Одна копия студии: именованный мьютекс Windows."""
    ctypes.windll.kernel32.CreateMutexW(None, False, "MartyshkaStudioSingleton")
    return ctypes.windll.kernel32.GetLastError() == 183          # ERROR_ALREADY_EXISTS


def main() -> None:
    if _already_running():
        SHOW_FLAG.parent.mkdir(parents=True, exist_ok=True)
        SHOW_FLAG.write_text("1")
        sys.exit(0)
    import webview
    setup_helpers()
    try:                                       # своя иконка на панели задач, а не значок Python
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Martyshka.Studio")
    except Exception:
        pass

    api = Api()
    sw, sh = ctypes.windll.user32.GetSystemMetrics(0), ctypes.windll.user32.GetSystemMetrics(1)
    window = webview.create_window(f"{config.NAME} — Студия", str(UI), js_api=api, width=1320, height=880,
                                   min_size=(900, 600), background_color="#07080c")
    orb = webview.create_window("Мартышка", str(ORB_UI), js_api=api, width=ORB, height=ORB, x=sw - ORB - 28,
                                y=sh - ORB - 90, frameless=True, on_top=True, easy_drag=True, hidden=True,
                                resizable=False, min_size=(ORB, ORB), background_color="#07080c")
    api.attach(window, orb)
    window.events.closing += api.on_closing
    orb.events.shown += lambda: _make_round(orb)

    def started():
        threading.Thread(target=api._watch_show_flag, daemon=True).start()
        brains.chat_brain().warm()             # лёгкий мозг заранее в памяти — первый ответ без ожидания

    from .build_exe import icon_path
    icon = icon_path()
    webview.start(started, gui="edgechromium", debug=bool(os.environ.get("STUDIO_DEBUG")),
                  icon=str(icon) if icon.exists() else None)
