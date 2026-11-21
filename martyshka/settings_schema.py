"""
ВСЕ НАСТРОЙКИ МАРТЫШКИ — одно место для окна чата (кнопка «Настройки» сверху).
Хранятся в data/settings.json и перекрывают config.py. Голосовая Мартышка подхватывает их на ходу.
"""
from __future__ import annotations

import json

from . import config

SECRET = ("DEEPSEEK_KEY", "FISH_KEY", "TELEGRAM_TOKEN")


def _devices(kind: str) -> list[tuple]:
    try:
        import sounddevice as sd
        names = []
        for d in sd.query_devices():
            ch = d["max_input_channels"] if kind == "in" else d["max_output_channels"]
            host = sd.query_hostapis(d["hostapi"])["name"]
            if ch > 0 and "MME" in host and d["name"] not in names:
                names.append(d["name"])
        return [("", "Как в Windows (по умолчанию)")] + [(n, n) for n in names]
    except Exception:
        return [("", "Как в Windows (по умолчанию)")]


def _mask(v: str) -> str:
    return ("•" * 10 + v[-4:]) if v else ""


def _f(key, label, type_="text", desc="", **kw) -> dict:
    v = getattr(config, key, "")
    if key in SECRET:
        v = _mask(v or "")
    elif hasattr(v, "__fspath__"):
        v = str(v)
    elif v is None:
        v = ""
    return {"key": key, "label": label, "type": type_, "desc": desc, "value": v, **kw}


def schema(extra: dict | None = None) -> dict:
    from . import deepseek, fish
    extra = extra or {}
    colors = [(k, k) for k in config.FACE_COLORS]
    ds_ok, fish_ok = deepseek.ready(), bool(fish.key())
    sections = [
        {"key": "brain", "title": "Мозг", "desc": "DeepSeek думает за Мартышку везде: в голосе, в чате и в Говорилке.",
         "note": None if ds_ok else "Вставь ключ DeepSeek (platform.deepseek.com → API Keys) и пополни баланс — "
                                    "без этого Мартышка не сможет думать.",
         "fields": [
             _f("DEEPSEEK_KEY", "Ключ DeepSeek", "password", "Хранится только на этом ПК."),
             {"key": "_ds_balance", "label": "Баланс DeepSeek", "type": "info",
              "value": extra.get("ds_balance", "…"), "ok": None},
             _f("BRAIN_MODE", "Каким мозгом думать", "select",
                "Авто: простое — Flash (быстро и дёшево), сложное и код — Pro (умнее). Это же переключается "
                "кнопками сверху окна.",
                options=[("auto", "Авто — сама выбирает"), ("flash", "Всегда Flash"), ("pro", "Всегда Pro")]),
         ]},
        {"key": "character", "title": "Характер", "desc": "Как Мартышка общается. Характер нарастающий: как с ней "
                                                          "говорят — так и она.",
         "fields": [
             _f("PERSONALITY_MODE", "Манера", "select", "", options=[("bro", "Свой в доску (на «ты», с юмором)"),
                                                                   ("jarvis", "Джарвис (на «вы», вежливо)")]),
             _f("OWNER_NICKNAME", "Как обращаться к тебе (свой в доску)"),
             _f("JARVIS_ADDRESS", "Как обращаться к тебе (Джарвис)"),
             _f("PERSONALITY_EXTRA", "Дописать к характеру своими словами", "textarea",
                "Например: «шути чаще», «зови меня Саня», «меньше мата»."),
         ]},
        {"key": "voice", "title": "Голос", "desc": "Fish Audio: один голос говорит на русском, украинском, немецком, "
                                                   "английском и других языках. ▶ — послушать.",
         "note": None if fish_ok else "Вставь ключ Fish Audio (fish.audio → API Keys) — без него голоса не будет.",
         "fields": [
             _f("FISH_KEY", "Ключ Fish Audio", "password"),
             {"key": "_fish_balance", "label": "Кредиты Fish Audio", "type": "info",
              "value": extra.get("fish_balance", "…"), "ok": None},
             _f("VOICE_ENABLED", "Говорить голосом", "toggle", "Выключено — Мартышка отвечает только текстом."),
             _f("FISH_MODEL", "Модель озвучки", "select",
                "s2.1-pro-free бесплатна до 30.11.2026 — потом Мартышка сама перейдёт на s2.1-pro (~15 $ за "
                "1 млн байт текста ≈ 12 часов речи).",
                options=[("s2.1-pro-free", "S2.1 Pro Free (бесплатно)"), ("s2.1-pro", "S2.1 Pro")]),
             _f("VOICE_VOLUME", "Громкость", "slider", "", min=0.3, max=2.0, step=0.05),
             _f("VOICE_OUTPUT_DEVICE", "Куда выводить звук", "select", "", options=_devices("out")),
             _f("VOICE_ID", "Голос Мартышки", "voice"),
         ]},
        {"key": "ears", "title": "Уши", "desc": "Имя «Мартышка» слышит маленькая модель прямо на ПК; саму фразу "
                                                "распознаёт DeepSeek.",
         "fields": [
             _f("MIC_DEVICE", "Микрофон", "select", "", options=_devices("in")),
             _f("DIALOG_MODE", "Режим диалога (голосовой помощник)", "toggle",
                "Только по имени, без «продолжения» без имени, дослушивает длинные фразы. Для Говорилки — "
                "свой переключатель во вкладке «Говорилка»."),
             _f("DIALOG_SILENCE", "Пауза «договорил» в режиме диалога (сек)", "slider", "", min=0.8, max=4, step=0.1),
             _f("FOLLOWUP_SECONDS", "Без режима диалога: сколько ждать продолжения без имени (сек)", "slider", "",
                min=0, max=20, step=1),
             _f("END_OF_SPEECH_SILENCE", "Пауза «я договорил» (сек)", "slider", "", min=0.4, max=2.0, step=0.1),
             _f("SPEECH_THRESHOLD", "Чувствительность к речи", "slider",
                "Меньше — слышит тише, больше — меньше реагирует на шум.", min=0.2, max=0.9, step=0.05),
             _f("EARS_MULTILANG", "Понимать немецкий и английский", "toggle"),
             _f("WAKE_BEEP", "Пикать, когда услышала имя", "toggle"),
         ]},
        {"key": "agent", "title": "Агент и коннекторы", "desc": "Что Мартышка может делать в чате.",
         "fields": [
             {"key": "_connectors", "label": "Коннекторы", "type": "connectors", "value": extra.get("connectors", []),
              "desc": "Выключенное агент просто не увидит."},
             _f("STUDIO_WORKSPACE", "Папка проектов", "text", "Здесь агент свободно создаёт файлы и проекты."),
             _f("STUDIO_AUTO_COMMANDS", "Команды в папке проектов — без вопроса", "toggle",
                "Удаление и опасные команды всё равно спрашивает."),
             _f("STUDIO_MAX_STEPS", "Сколько шагов агент может сделать за задачу", "slider", "", min=10, max=150,
                step=5),
             _f("WEB_ENABLED", "Интернет-поиск", "toggle"),
         ]},
        {"key": "orb", "title": "Шар", "desc": "Шар Мартышки на экране. Клик — открыть чат, правая кнопка — меню.",
         "fields": [
             _f("FACE_ENABLED", "Показывать шар", "toggle"),
             _f("FACE_COLOR", "Цвет", "select", "", options=colors),
             _f("FACE_SIZE", "Размер", "select", "", options=[(v, k) for k, v in config.FACE_SIZES.items()]),
             _f("FACE_CORNER", "Угол экрана", "select", "", options=[(x, x) for x in
                                                                    ("лево-верх", "право-верх", "лево-низ", "право-низ")]),
         ]},
        {"key": "system", "title": "Система", "desc": "",
         "fields": [
             {"key": "_autostart", "label": "Запускать Мартышку вместе с Windows", "type": "select",
              "value": "1" if extra.get("autostart") else "0", "options": [("1", "Да"), ("0", "Нет")]},
             _f("WEATHER_CITY", "Город для погоды"),
             _f("BRIEFING_ENABLED", "Утренняя сводка", "toggle", "Погода, напоминания и заряд устройств при первом "
                                                                "включении за день."),
             _f("TELEGRAM_TOKEN", "Telegram-бот (ключ от @BotFather)", "password"),
             {"key": "refresh_voices", "label": "Обновить список голосов Fish", "type": "button",
              "text": "Обновить", "desc": "Скачает свежий каталог популярных голосов (русские, украинские, "
                                         "немецкие, английские)."},
             {"key": "restart_voice", "label": "Перезапустить голосовую Мартышку", "type": "button",
              "text": "Перезапустить"},
             {"key": "quit_all", "label": "Выключить Мартышку совсем", "type": "button", "text": "Выключить",
              "danger": True},
         ]},
    ]
    return {"sections": sections}


NUMERIC = {"TEMPERATURE": float, "VOICE_SPEED": float, "VOICE_TEMPERATURE": float, "VOICE_VOLUME": float,
           "FOLLOWUP_SECONDS": float, "DIALOG_SILENCE": float, "END_OF_SPEECH_SILENCE": float, "SPEECH_THRESHOLD": float,
           "STUDIO_MAX_STEPS": int, "FACE_SIZE": int}


def save(values: dict) -> list[str]:
    """Записать настройки в data/settings.json. Возвращает, что поменялось."""
    try:
        saved = json.loads(config.SETTINGS_FILE.read_text(encoding="utf-8"))
    except Exception:
        saved = {}
    changed = []
    for k, v in values.items():
        if not k.isupper() or not hasattr(config, k):
            continue
        if k in SECRET and isinstance(v, str) and v.startswith("•"):
            continue                                   # ключ не меняли (показывали звёздочки)
        if k in NUMERIC:
            try:
                v = NUMERIC[k](v)
            except Exception:
                continue
        if k in ("MIC_DEVICE", "VOICE_OUTPUT_DEVICE") and v == "":
            v = None
        if isinstance(v, str):
            v = v.strip()
        saved[k] = v
        setattr(config, k, v)
        changed.append(k)
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    config.SETTINGS_FILE.write_text(json.dumps(saved, ensure_ascii=False, indent=2), encoding="utf-8")
    # цвет и размер шара — сразу в его собственный файл (шар подхватит на ходу)
    if "FACE_COLOR" in changed or "FACE_SIZE" in changed:
        face_file = config.DATA_DIR / "face.json"
        try:
            prefs = json.loads(face_file.read_text(encoding="utf-8"))
        except Exception:
            prefs = {}
        if "FACE_COLOR" in changed:
            prefs["color"] = config.FACE_COLOR
        if "FACE_SIZE" in changed:
            prefs["size"] = int(config.FACE_SIZE)
        face_file.write_text(json.dumps(prefs, ensure_ascii=False), encoding="utf-8")
    return changed
