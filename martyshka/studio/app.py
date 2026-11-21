"""
ОКНО МАРТЫШКИ — главное приложение: pywebview (Edge WebView2) + мост окно ↔ агент.

  - Запускается первым (ярлык «Мартышка» → «Мартышка.exe»). Голосового помощника включают и выключают
    кнопкой «Говорить» — он работает отдельным процессом рядом, со своим шаром (клик по шару — это окно).
  - Отдельного шарика у окна больше нет: шар — один, голосовой Мартышки. Крестик прячет окно, шар его возвращает.
  - Наверху окна: режим мозга (Авто / Flash / Pro), Голос, Говорилка, Память, Настройки — всё в одном месте.
  - Одна копия: повторный запуск просто показывает уже открытое окно (флажок data/studio/показать.flag).
"""
from __future__ import annotations

import ctypes
import json
import os
import queue
import re
import sys
import threading
import time
import uuid
from pathlib import Path

from .. import config
from . import agent_loop, tools

UI = Path(__file__).parent / "ui" / "index.html"
SHOW_FLAG = config.DATA_DIR / "studio" / "показать.flag"


def _helper_llm(prompt: str, max_tokens: int = 1500) -> str:
    """Служебный мозг для «рук» (составить таблицу, написать текст)."""
    from .. import deepseek
    return deepseek.ask(prompt, system="Ты аккуратный помощник. Отвечай строго в запрошенном формате, без пояснений "
                                       "и без markdown.", max_tokens=max_tokens)


class Api:
    def __init__(self, tab: str = "chat"):
        self._window = None
        self._quitting = False
        self._events: queue.Queue = queue.Queue()
        self._answers: dict[str, tuple[threading.Event, list]] = {}
        self._stop = threading.Event()
        self._busy = False
        self._tab = tab
        self._chat = agent_loop.Chat()
        self._preview_lock = threading.Lock()
        self._dictate_stop = threading.Event()
        self._speaker = None
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

    # ---------- окно ----------
    def on_closing(self):
        """Крестик: если голосовая Мартышка работает — окно просто прячется (шар вернёт его). Иначе — закрыть."""
        from .. import system
        if self._quitting or not system.voice_running():
            self._quit_all()
            return True
        self._window.hide()
        return False

    def show_main(self, tab: str = "") -> None:
        self._window.show()
        self._window.restore()
        try:
            ctypes.windll.user32.SetForegroundWindow(ctypes.windll.user32.FindWindowW(None, self._window.title))
        except Exception:
            pass
        if tab and tab != "chat":
            self._emit({"type": "show_tab", "tab": tab})

    def quit(self) -> None:
        self._quitting = True
        self.stop()
        threading.Thread(target=self._quit_all, daemon=True).start()

    def restart(self) -> None:
        """Перезапуск окна (после того как агент поменял его самого)."""
        import subprocess
        exe = config.PROJECT_DIR / ".venv" / "Scripts" / "pythonw.exe"
        subprocess.Popen(["cmd", "/c", f'timeout /t 3 /nobreak >nul & start "" "{exe}" "{config.PROJECT_DIR / "studio.py"}"'],
                         cwd=str(config.PROJECT_DIR), creationflags=0x08000000 | 0x00000008)
        self.quit()

    def _quit_all(self) -> None:
        self._quitting = True
        try:
            self._window.destroy()
        except Exception:
            pass

    def _watch_show_flag(self) -> None:
        """Шар голосовой Мартышки (или повторный запуск) кладёт флажок — показываем окно на нужной вкладке."""
        while not self._quitting:
            if SHOW_FLAG.exists():
                try:
                    tab = SHOW_FLAG.read_text(encoding="utf-8").strip() or "chat"
                    SHOW_FLAG.unlink()
                except Exception:
                    tab = "chat"
                self.show_main(tab)
            time.sleep(0.4)

    # ---------- старт и состояние ----------
    def init(self) -> dict:
        from .. import deepseek
        face = {}
        try:
            face = json.loads((config.DATA_DIR / "face.json").read_text(encoding="utf-8"))
        except Exception:
            pass
        color = face.get("color") or config.FACE_COLOR
        return {"chats": agent_loop.Chat.list_all(), "name": config.NAME, "ready": deepseek.ready(),
                "chat_speak": bool(getattr(config, "CHAT_SPEAK", False)), "volume": float(config.VOICE_VOLUME),
                "brain_mode": (config.BRAIN_MODE or "auto").replace("deepseek-v4-", "").replace("deepseek-", ""),
                "theme": getattr(config, "UI_THEME", "cyan") or "cyan",
                "color": config.FACE_COLORS.get(color, color), "tab": self._tab, **self.status_dots()}

    def status_dots(self) -> dict:
        from .. import govorilka, system
        try:
            gov = govorilka.is_running()
        except Exception:
            gov = False
        return {"voice": system.voice_running(), "gov": gov}

    def balances(self) -> dict:
        from .. import deepseek, fish
        return {"deepseek": deepseek.balance() if deepseek.ready() else "нет ключа",
                "fish": fish.credits() if fish.key() else "нет ключа"}

    def set_brain_mode(self, mode: str) -> dict:
        from .. import settings_schema
        settings_schema.save({"BRAIN_MODE": mode if mode in ("auto", "flash", "pro") else "auto"})
        return {"ok": True}

    def set_theme(self, theme: str) -> dict:
        from .. import settings_schema
        t = theme if theme in ("cyan", "violet", "emerald", "graphite") else "cyan"
        settings_schema.save({"UI_THEME": t})
        return {"ok": True, "theme": t}

    def voice_set(self, on: bool) -> dict:
        """Кнопка «Говорить»: включить или выключить голосового помощника (и запомнить выбор)."""
        from .. import settings_schema, system
        settings_schema.save({"VOICE_AUTOSTART": bool(on)})
        if on and not system.voice_running():
            system.start_voice()
            for _ in range(20):                       # мьютекс появляется через пару секунд после запуска
                time.sleep(0.5)
                if system.voice_running():
                    break
        elif not on and system.voice_running():
            system.stop_voice()
            for _ in range(20):
                time.sleep(0.5)
                if not system.voice_running():
                    break
        return {"voice": system.voice_running()}

    # ---------- голос в самом окне: микрофон, чтение вслух ----------
    def dictate(self) -> dict:
        """Кнопка 🎤: записать фразу с микрофона до паузы и распознать (DeepSeek)."""
        import numpy as np
        import sounddevice as sd
        from .. import deepseek
        from faster_whisper.vad import get_vad_model
        self._dictate_stop.clear()
        sr, block = 16000, 1600
        vad = get_vad_model()
        audio, window, heard, silent, t0 = [], [], False, 0.0, time.time()
        try:
            with sd.InputStream(samplerate=sr, channels=1, dtype="float32", blocksize=block,
                                device=config.MIC_DEVICE or None) as stream:
                while not self._dictate_stop.is_set() and time.time() - t0 < 60:
                    b = stream.read(block)[0][:, 0].copy()
                    audio.append(b)
                    window = (window + [b])[-5:]
                    w = np.concatenate(window)
                    n = (len(w) // 512) * 512
                    speech = bool(n) and float(vad(w[-n:]).reshape(-1)[-3:].mean()) > config.SPEECH_THRESHOLD
                    if speech:
                        heard, silent = True, 0.0
                    elif heard:
                        silent += block / sr
                    if heard and silent >= max(1.2, float(config.DIALOG_SILENCE)):
                        break
                    if not heard and time.time() - t0 > 8:
                        return {"text": "", "error": "Не услышала речь — проверь микрофон в «Голос»."}
        except Exception as e:
            return {"text": "", "error": f"Микрофон не открылся: {e}"}
        if not heard:
            return {"text": ""}
        try:
            return {"text": deepseek.transcribe(np.concatenate(audio), sr)}
        except Exception as e:
            return {"text": "", "error": str(e)}

    def dictate_stop(self) -> None:
        self._dictate_stop.set()

    def speak(self, text: str) -> dict:
        """Прочитать ответ вслух (кнопка 🔊 в окне или под конкретным ответом). Без обрезки, с возможностью стоп."""
        from ..voice import Voice
        text = (text or "").strip()
        if not text:
            return {"ok": False}
        if self._speaker is None:
            self._speaker = Voice()
        self._speaker.stop()
        threading.Thread(target=self._speaker.say, args=(text,), daemon=True).start()
        return {"ok": True}

    def stop_speak(self) -> None:
        if self._speaker is not None:
            self._speaker.stop()

    def pick_file(self) -> dict:
        """Кнопка 📎: выбрать файл — путь вставится в сообщение, агент его прочитает."""
        import webview
        try:
            r = self._window.create_file_dialog(webview.OPEN_DIALOG, allow_multiple=False)
        except Exception:
            r = None
        return {"path": r[0] if r else ""}

    def toggle_voice(self) -> dict:
        """Кнопка «Голос» — настоящий выключатель: работает — выключаем, не работает — включаем."""
        from .. import system
        if system.voice_running():
            system.stop_voice()
        else:
            system.start_voice()
            time.sleep(1.0)
        d = self.status_dots()
        d["voice"] = system.voice_running()
        return d

    # ---------- чаты ----------
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
        return {"ok": True, "id": self._chat.id, "title": self._chat.title, "display": self._chat.display,
                "project": self._chat.project}

    def delete_chat(self, chat_id: str) -> dict:
        if self._busy and chat_id == self._chat.id:
            return {"ok": False}
        agent_loop.Chat(chat_id).remove()
        if chat_id == self._chat.id:
            self._chat = agent_loop.Chat()
        return {"ok": True}

    def pin_chat(self, chat_id: str, pinned: bool) -> dict:
        c = self._chat if chat_id == self._chat.id else agent_loop.Chat(chat_id)
        c.pinned = bool(pinned)
        c.save()
        return {"ok": True}

    def rename_chat(self, chat_id: str, title: str) -> dict:
        title = (title or "").strip()[:80]
        if not title:
            return {"ok": False}
        c = self._chat if chat_id == self._chat.id else agent_loop.Chat(chat_id)
        c.title = title
        c.save()
        return {"ok": True, "title": title}

    def regenerate(self) -> dict:
        """Перегенерировать последний ответ в текущем чате."""
        if self._busy:
            return {"ok": False}
        last_text = self._chat.pop_last_turn()
        if not last_text:
            return {"ok": False}
        return {"ok": True, "text": last_text, "display": self._chat.display}

    def edit_last_user(self) -> dict:
        """Вернуть последнее сообщение пользователя в поле ввода для редактирования."""
        if self._busy:
            return {"ok": False}
        last_text = self._chat.pop_last_turn()
        if not last_text:
            return {"ok": False}
        return {"ok": True, "text": last_text, "display": self._chat.display}

    def capture_screen(self) -> dict:
        """Сделать снимок экрана (1-клик 📸) и вернуть путь + превью."""
        import base64
        import io
        from PIL import ImageGrab
        try:
            if self._window is not None:
                self._window.hide()
                time.sleep(0.22)
            im = ImageGrab.grab(all_screens=False)
        finally:
            if self._window is not None:
                try:
                    self._window.show()
                except Exception:
                    pass
        up_dir = config.DATA_DIR / "studio" / "uploads"
        up_dir.mkdir(parents=True, exist_ok=True)
        p = up_dir / f"screen_{time.strftime('%Y%m%d_%H%M%S')}.png"
        im.save(p, "PNG")
        th = im.copy()
        th.thumbnail((320, 320))
        buf = io.BytesIO()
        th.save(buf, "JPEG", quality=80)
        prev = "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
        return {"ok": True, "path": str(p), "name": p.name, "preview": prev}

    def save_dropped_file(self, name: str, data_url: str) -> dict:
        """Сохранить файл, перетащенный мышкой в окно (Drag & Drop)."""
        import base64
        if not data_url or "," not in data_url:
            return {"ok": False}
        _, b64 = data_url.split(",", 1)
        safe = re.sub(r'[<>:"/\\|?*]', "_", (name or "file.bin").strip())[:120] or "file.bin"
        up_dir = config.DATA_DIR / "studio" / "uploads"
        up_dir.mkdir(parents=True, exist_ok=True)
        p = up_dir / f"{time.strftime('%Y%m%d_%H%M%S')}_{safe}"
        try:
            p.write_bytes(base64.b64decode(b64))
            is_img = p.suffix.lower() in agent_loop.IMG_EXT
            return {"ok": True, "path": str(p), "name": safe, "is_image": is_img}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def notify_done(self) -> None:
        """Мягкий сигнал и подсветка на панели задач, когда долгая задача завершилась в фоне."""
        try:
            import winsound
            winsound.MessageBeep(winsound.MB_OK)
        except Exception:
            pass
        try:
            if self._window is not None:
                hwnd = ctypes.windll.user32.FindWindowW(None, self._window.title)
                if hwnd:
                    ctypes.windll.user32.FlashWindow(hwnd, True)
        except Exception:
            pass

    def save_pasted_image(self, data_url: str) -> dict:
        """Сохранить вставленный из буфера (Ctrl+V) скриншот в data/studio/uploads/."""
        import base64
        if not data_url or "," not in data_url:
            return {"ok": False}
        hdr, b64 = data_url.split(",", 1)
        ext = ".jpg" if "jpeg" in hdr else ".webp" if "webp" in hdr else ".png"
        up_dir = config.DATA_DIR / "studio" / "uploads"
        up_dir.mkdir(parents=True, exist_ok=True)
        p = up_dir / f"shot_{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:4]}{ext}"
        try:
            p.write_bytes(base64.b64decode(b64))
            return {"ok": True, "path": str(p), "name": p.name}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def rollback_task(self, task_id: str) -> dict:
        return agent_loop.rollback_task(task_id)

    def projects_list(self) -> dict:
        ws = tools.workspace()
        items = []
        try:
            for p in sorted((d for d in ws.iterdir() if d.is_dir()), key=lambda d: d.stat().st_mtime, reverse=True)[:30]:
                items.append({"name": p.name, "path": str(p)})
        except Exception:
            pass
        return {"current": self._chat.project, "items": items}

    def set_project(self, path: str) -> dict:
        self._chat.project = (path or "").strip()
        if self._chat.history:
            self._chat.save()
        return {"ok": True, "current": self._chat.project}

    def usage_info(self) -> dict:
        from .. import deepseek
        u = dict(deepseek.USAGE)
        inp = int(u.get("in") or 0)
        cached = int(u.get("cached") or 0)
        out = int(u.get("out") or 0)
        pct = round(cached * 100 / inp) if inp else 0
        return {"requests": int(u.get("requests") or 0), "in": inp, "cached": cached, "out": out, "cache_pct": pct}

    def send(self, text: str, mode: str = "auto", images: list[str] | None = None) -> dict:
        text = (text or "").strip()
        imgs = [str(x) for x in (images or []) if x]
        if (not text and not imgs) or self._busy:
            return {"ok": False}
        if not text and imgs:
            text = "Посмотри на прикреплённую картинку и разберись."
        self._busy = True
        self._stop.clear()

        def work():
            try:
                self._chat.run(text, mode, self._emit, self._ask, self._stop.is_set, images=imgs)
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
        try:
            self.show_main()                 # вопрос важнее — показываем окно, даже если оно спрятано
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

    # ---------- настройки ----------
    def connectors(self) -> list:
        off = set(getattr(config, "STUDIO_DISABLED_GROUPS", []) or [])
        out = []
        for key, (title, desc) in tools.GROUPS.items():
            names = [t["name"] for t in tools.TOOLS if t["group"] == key]
            on = key not in off and (key != "web" or config.WEB_ENABLED)
            out.append({"key": key, "title": title, "desc": desc, "on": on, "tools": len(names)})
        return out

    def toggle_connector(self, key: str, on: bool) -> list:
        from .. import settings_schema
        off = set(getattr(config, "STUDIO_DISABLED_GROUPS", []) or [])
        (off.discard if on else off.add)(key)
        values: dict = {"STUDIO_DISABLED_GROUPS": sorted(off)}
        if key == "web":
            values["WEB_ENABLED"] = bool(on)
        settings_schema.save(values)
        return self.connectors()

    def settings_schema(self) -> dict:
        from .. import settings_schema, system
        b = self.balances()
        return settings_schema.schema({"ds_balance": b["deepseek"], "fish_balance": b["fish"],
                                       "connectors": self.connectors(), "autostart": system.autostart_enabled()})

    def save_settings(self, values: dict) -> dict:
        from .. import settings_schema, system
        values = dict(values or {})
        if "_autostart" in values:
            system.set_autostart(values.pop("_autostart") in ("1", True, 1))
        changed = settings_schema.save(values)
        msg = "Сохранено ✓" + (" Голосовая Мартышка подхватит сама." if system.voice_running() else "")
        need_restart = {"FACE_ENABLED", "MIC_DEVICE", "VOICE_OUTPUT_DEVICE", "FACE_CORNER", "TELEGRAM_TOKEN"}
        if system.voice_running() and need_restart & set(changed):
            msg = "Сохранено ✓ Для микрофона/шара/Telegram нажми «Перезапустить голосовую Мартышку» (Система)."
        color = config.FACE_COLORS.get(config.FACE_COLOR, config.FACE_COLOR) if "FACE_COLOR" in changed else None
        return {"ok": True, "message": msg, "color": color}

    def settings_action(self, key: str) -> dict:
        from .. import fish, system
        if key == "refresh_voices":
            try:
                n = len(fish.refresh_catalog())
                return {"message": f"Голосов в списке: {n}"}
            except Exception as e:
                return {"message": f"Не вышло: {e}"}
        if key == "restart_voice":
            if system.voice_running():
                stop_flag = config.DATA_DIR / "перезапуск.flag"
                stop_flag.write_text("1")
                return {"message": "Перезапускаю голосовую Мартышку…"}
            system.start_voice()
            return {"message": "Запускаю голосовую Мартышку…"}
        if key == "quit_all":
            (config.DATA_DIR / "выключить.flag").write_text("1")
            self.quit()
            return {"message": "Выключаю…"}
        return {"message": ""}

    # ---------- голоса Fish ----------
    def voices(self) -> list:
        from .. import fish
        v = fish.catalog()
        if len(v) <= len(fish.OFFICIAL) and fish.key():
            try:
                v = fish.refresh_catalog()
            except Exception:
                pass
        return v

    def search_voices(self, q: str) -> list:
        from .. import fish
        try:
            return fish.search(q)
        except Exception:
            return []

    def preview_voice(self, voice_id: str, lang: str = "ru", model: str = "") -> dict:
        """Послушать голос: короткая фраза на его языке через колонки."""
        from .. import fish
        phrase = {"ru": "{laughing} Привет! Я Мартышка. Как тебе мой голос?",
                  "uk": "Привіт! Я Мартишка. Як тобі мій голос?",
                  "de": "Hallo! Ich bin Martyshka. Wie gefällt dir meine Stimme?",
                  "en": "Hey! I'm Martyshka. How do you like my voice?"}.get(lang, "Привет! Я Мартышка.")
        if not self._preview_lock.acquire(blocking=False):
            return {"error": "Уже играет пример"}
        try:
            import sounddevice as sd
            from ..voice import prepare_text
            audio = fish.speak_all(prepare_text(phrase), voice_id, model=model or None)
            sd.play(audio * float(config.VOICE_VOLUME), fish.SAMPLE_RATE)
            sd.wait()
            return {"ok": True}
        except Exception as e:
            return {"error": str(e)}
        finally:
            self._preview_lock.release()

    # ---------- память ----------
    def memory_info(self) -> dict:
        from .. import growth, longmem
        from ..memory import Memory
        people = []
        for name, p in sorted(longmem.people().items(), key=lambda kv: kv[1].get("последний_раз", ""), reverse=True):
            people.append({"name": name, "facts": p.get("факты", []), "anger": longmem.anger(name),
                           "last": (p.get("последний_раз") or "").replace("T", " "), "count": p.get("реплик", 0)})
        days = sorted(longmem.days().items(), reverse=True)
        facts = [("голос", f) for f in Memory().fact_texts()] + [("чат", f) for f in tools.memory_facts()]
        lessons = sorted(growth.items(), key=lambda it: (int(it.get("подтверждений", 1)), it.get("когда", "")),
                         reverse=True)
        return {"people": people, "days": days, "facts": facts[::-1], "lessons": lessons}

    def forget_lesson(self, text: str) -> dict:
        from .. import growth
        growth.forget(text)
        return {"ok": True}

    # ---------- свои команды / сценарии ----------
    def commands_list(self) -> list:
        from .. import commands
        return commands.load()

    def save_command(self, phrase: str, steps_text: str, say: str = "") -> dict:
        from .. import commands
        phrase = (phrase or "").strip()
        steps = [s.strip() for s in re.split(r"[;\n]+", steps_text or "") if s.strip()]
        if not phrase or not steps:
            return {"ok": False, "message": "Укажи фразу и хотя бы один шаг."}
        items = [c for c in commands.load() if c.get("phrase", "").strip().lower() != phrase.lower()]
        item = {"phrase": phrase, "steps": steps}
        if (say or "").strip():
            item["say"] = say.strip()
        items.append(item)
        commands.save(items)
        return {"ok": True, "message": f"Команда «{phrase}» сохранена."}

    def delete_command(self, phrase: str) -> dict:
        from .. import commands
        items = [c for c in commands.load() if c.get("phrase", "").strip().lower() != (phrase or "").strip().lower()]
        commands.save(items)
        return {"ok": True}

    def forget_person_fact(self, name: str, fact: str) -> dict:
        from .. import longmem
        longmem.forget_about(name, fact)
        return {"ok": True}

    def calm_person(self, name: str) -> dict:
        from .. import longmem
        longmem.set_anger(name, 0)
        return {"ok": True}

    def forget_fact(self, src: str, fact: str) -> dict:
        if src == "голос":
            from ..memory import Memory
            Memory().forget(fact)
        else:
            facts = [f for f in tools.memory_facts() if f != fact]
            tools.MEMORY_FILE.write_text(json.dumps(facts, ensure_ascii=False, indent=1), encoding="utf-8")
        return {"ok": True}

    # ---------- Говорилка ----------
    def gov_info(self) -> dict:
        from .. import govorilka as g
        g._reload_settings()
        return {"status": g.status(), "running": g.is_running(), "cable": g.cable_ready(),
                "voice": config.GOVORILKA_VOICE_ID or config.VOICE_ID, "model": g.brain_model(),
                "autostart": bool(config.GOVORILKA_AUTOSTART) and g.autostart_enabled(),
                "nick": config.GOVORILKA_NICK, "log": g.recent_log(40), "dialog": bool(config.GOVORILKA_DIALOG),
                "fish_model": getattr(config, "GOVORILKA_FISH_MODEL", "") or "", "fish_models": fish_models(),
                "greet": bool(getattr(config, "GOVORILKA_GREET", True)),
                "chime": bool(getattr(config, "GOVORILKA_CHIME", False))}

    def gov_action(self, action: str, value=None) -> dict:
        from .. import govorilka as g
        msg = ""
        if action == "join":
            msg = g.start()
        elif action == "leave":
            msg = g.stop()
        elif action == "silence":
            msg = g.silence()
        elif action == "quit":
            msg = g.quit_server()
        elif action == "model" and value:
            g.save_setting("GOVORILKA_MODEL", value)
            msg = "Мозг в Говорилке: " + ("Pro" if "pro" in value else "Flash")
        elif action == "dialog":
            g.save_setting("GOVORILKA_DIALOG", bool(value))
            msg = ("Режим диалога: отвечаю только по имени, дослушиваю и не говорю поверх."
                   if value else "Режим диалога выключен: могу сама заводить разговор с Морни.")
        elif action in ("greet", "chime"):
            key = {"greet": "GOVORILKA_GREET", "chime": "GOVORILKA_CHIME"}[action]
            g.save_setting(key, bool(value))
            msg = {("greet", True): "Буду здороваться с теми, кто зашёл, и прощаться с ушедшими.",
                   ("greet", False): "Здороваться и прощаться не буду.",
                   ("chime", True): "Сама решаю, когда вмешаться в разговор и заговорить в тишине.",
                   ("chime", False): "Вмешиваться не буду — только когда зовут."}[(action, bool(value))]
        elif action == "fish_model":
            g.save_setting("GOVORILKA_FISH_MODEL", value or "")
            names = dict(fish_models())
            msg = "Озвучка в Говорилке: " + names.get(value or "", "как у голоса Мартышки")
        elif action in ("morni", "debate"):
            topic = (value or "").strip() if action == "debate" else ""
            if not g.is_running():
                msg = "Мартышки нет в Говорилке — сначала нажми «Зайти в войс»."
            elif action == "debate" and not topic:
                msg = "Напиши тему спора."
            else:
                g.BOT_DIR.mkdir(parents=True, exist_ok=True)
                g.MORNI_NOW_FLAG.write_text(json.dumps({"topic": topic}, ensure_ascii=False), encoding="utf-8")
                msg = f"Сейчас начнёт спор с Морни: «{topic}»." if topic else "Сейчас заговорит с Морни."
        elif action == "autostart":
            g.set_autostart(bool(value))
            msg = "Буду сидеть в Говорилке всегда." if value else "Сама заходить не буду."
        return dict(self.gov_info(), message=msg)


def fish_models() -> list:
    from .. import fish
    return [("", "Как у голоса Мартышки")] + [(k, v.split(" — ")[0]) for k, v in fish.MODELS]


def setup_helpers() -> None:
    from .. import hands, media
    hands.set_llm(_helper_llm)
    media.set_hooks(gpu_before=None, gpu_after=None, llm=_helper_llm)


def _already_running() -> bool:
    """Одна копия окна: именованный мьютекс Windows."""
    ctypes.windll.kernel32.CreateMutexW(None, False, "MartyshkaStudioSingleton")
    return ctypes.windll.kernel32.GetLastError() == 183          # ERROR_ALREADY_EXISTS


def main() -> None:
    tab = "chat"
    if "--tab" in sys.argv:
        try:
            tab = sys.argv[sys.argv.index("--tab") + 1]
        except IndexError:
            pass
    if _already_running():
        SHOW_FLAG.parent.mkdir(parents=True, exist_ok=True)
        SHOW_FLAG.write_text(tab, encoding="utf-8")
        sys.exit(0)
    # Голосовой помощник — отдельный процесс, который брат включает и выключает кнопкой «Говорить».
    # Выбор запоминается (VOICE_AUTOSTART): был включён — включится сам при следующем запуске.
    from .. import system
    want_voice = "--voice" in sys.argv or (getattr(config, "VOICE_AUTOSTART", False) and "--no-voice" not in sys.argv)
    if want_voice and not system.voice_running():
        try:
            system.start_voice()
        except Exception:
            pass
    # при включении ПК (--autostart) с включённым голосом — окно не показываем: шар на экране, клик — окно
    hidden = "--autostart" in sys.argv and want_voice
    import webview
    setup_helpers()

    def _telegram_log(text: str) -> None:
        try:
            with open(config.DATA_DIR / "журнал.txt", "a", encoding="utf-8") as f:
                f.write(time.strftime("%d.%m %H:%M:%S ") + str(text) + "\n")
        except Exception:
            pass

    # Telegram-бот работает, даже когда голос выключен: бота держит тот, кто запустился первым
    # (голосовая Мартышка или это окно чата). Здесь — фоновый запуск с собственным мозгом.
    try:
        from .. import telegram_service
        threading.Thread(target=telegram_service.start, kwargs={"log": _telegram_log}, daemon=True,
                         name="telegram-studio").start()
    except Exception:
        pass

    try:                                       # своя иконка на панели задач, а не значок Python
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Martyshka.Studio")
    except Exception:
        pass
    SHOW_FLAG.unlink(missing_ok=True)

    api = Api(tab)
    window = webview.create_window(config.NAME, str(UI), js_api=api, width=1180, height=860,
                                   min_size=(760, 560), background_color="#0c0c0f", hidden=hidden)
    api.attach(window)
    window.events.closing += api.on_closing

    def started():
        threading.Thread(target=api._watch_show_flag, daemon=True).start()

    from .build_exe import icon_path
    icon = icon_path()
    webview.start(started, gui="edgechromium", debug=bool(os.environ.get("STUDIO_DEBUG")),
                  icon=str(icon) if icon.exists() else None)
