"""
МАНЕРА ОБЩЕНИЯ: два режима, выбираются в настройках (Мозг и характер → «Манера общения»)
или голосом: «Мартышка, режим Джарвис» / «Мартышка, режим брат».

  bro    — свой в доску: «брат», мат к месту, подколы
  jarvis — как ИИ-дворецкий из кино: спокойно, вежливо, на «вы», «сэр», без мата
"""
import re
from datetime import datetime

from . import config


def mode() -> str:
    return "jarvis" if str(getattr(config, "PERSONALITY_MODE", "bro")).lower() == "jarvis" else "bro"


def is_jarvis() -> bool:
    return mode() == "jarvis"


def sir() -> str:
    return (getattr(config, "JARVIS_ADDRESS", "сэр") or "сэр").strip()


def _greeting() -> str:
    h = datetime.now().hour
    return "Доброе утро" if 5 <= h < 12 else "Добрый день" if h < 17 else "Добрый вечер" if h < 23 else "Доброй ночи"


def phrase(key: str, **kw) -> str:
    """Готовые фразы Мартышки в нужной манере."""
    s = sir()
    j = {
        "hello": f"{_greeting()}, {s}. Все системы в норме, я на связи.",
        "hello_missed": f"{_greeting()}, {s}. Пока меня не было, вы просили напомнить: {{items}}",
        "remind": f"{s.capitalize()}, напоминаю: {{text}}",
        "game_on": f"Режим игр, {s}. Освобождаю видеокарту. Скажите «конец игр», и я вернусь.",
        "game_off": f"Снова в строю, {s}.",
        "dict_on": f"Записываю, {s}. Говорите — я печатаю там, где курсор. Чтобы закончить, скажите «хватит писать».",
        "dict_off": f"Диктовка завершена, {s}.",
        "dict_idle": f"Диктовка завершена, {s}.",
        "tg_online": f"Компьютер включён, {s}. Я на связи.",
        "tg_paired": f"Связь установлена, {s}. Отныне я подчиняюсь только вам. Пишите, что требуется.",
        "tg_start": f"Слушаю, {s}. Пишите, что требуется, или нажмите «Меню».",
        "tg_game": f"Сейчас режим игр, {s}: мозг выгружен, чтобы не мешать игре. Напишите «конец игр», и я вернусь.",
        "done": f"Готово, {s}.",
        "cancelled": f"Как скажете, {s}. Отменено.",
        "mode_jarvis": f"Режим Джарвиса включён, {s}. К вашим услугам.",
        "mode_bro": "Всё, брат, вернулся нормальный я. Погнали.",
    }
    b = {
        "hello": "Здарова, брат. Я на связи.",
        "hello_missed": "Здарова, брат. Пока меня не было, ты просил напомнить: {items}",
        "remind": "Брат, напоминаю: {text}",
        "game_on": "Режим игр. Видеокарта твоя, брат. Скажешь — конец игр, и я вернусь.",
        "game_off": "Я вернулся, брат.",
        "dict_on": "Пишу. Говори — я печатаю туда, где курсор. Закончить — скажи «хватит писать».",
        "dict_off": "Всё, больше не пишу.",
        "dict_idle": "Диктовку закончил.",
        "tg_online": "Я на связи, брат. Комп включён.",
        "tg_paired": "Привязал, брат. Теперь я слушаюсь только тебя. Пиши, что надо.",
        "tg_start": "Я тут. Пиши, что надо — или жми кнопку «Меню» внизу.",
        "tg_game": "Сейчас режим игр — мозг выгружен, чтобы не мешать игре. Напиши «конец игр», и я вернусь.",
        "done": "Сделано.",
        "cancelled": "Ну ок, отбой.",
        "mode_jarvis": f"Режим Джарвиса включён, {s}. К вашим услугам.",
        "mode_bro": "Всё, брат, вернулся нормальный я. Погнали.",
    }
    text = (j if is_jarvis() else b).get(key, key)
    return text.format(**kw) if kw else text


# «брат» в инструкциях для мозга → «хозяин» (в режиме Джарвиса мозг не должен даже видеть слово «брат»)
_OWNER = [(r"\bбратан\w*", "хозяин"), (r"\bбрата\b", "хозяина"), (r"\bбрату\b", "хозяину"),
          (r"\bбратом\b", "хозяином"), (r"\bбрате\b", "хозяине"), (r"\bбрат\b", "хозяин")]


def adapt(text: str) -> str:
    """Подогнать служебный текст (инструкцию мозгу) под режим."""
    if not is_jarvis():
        return text
    for pat, rep in _OWNER:
        text = re.sub(pat, rep, text)
        text = re.sub(pat.replace(r"\bб", r"\bБ"), rep.capitalize(), text)
    text = text.replace("«Alter» или «Bruder»", "«Sir»").replace("«bro»", "«sir»")
    return text


# Голосом: «режим Джарвис» / «режим брат» (или «включи Джарвиса», «верни нормального себя»)
MODE_JARVIS_RE = re.compile(r"(режим\w*\s+(джарвис\w*|дворецк\w*|вежлив\w*|спокойн\w*)|"
                            r"(включи|стань|будь)\s+(джарвис\w*|вежлив\w*|дворецк\w*)|jarvis\s+mode)", re.I)
MODE_BRO_RE = re.compile(r"(режим\w*\s+(брат\w*|кореш\w*|обычн\w*|нормальн\w*|с\s+матом)|"
                         r"(верни|включи|стань)\s+(брат\w*|кореш\w*|нормальн\w*\s+себя|обычн\w*\s+себя)|"
                         r"хватит\s+(джарвис\w*|вежлив\w*))", re.I)


def switch_request(text: str) -> str | None:
    """Если брат просит сменить манеру — вернёт 'jarvis' или 'bro'."""
    if MODE_JARVIS_RE.search(text):
        return "jarvis"
    if MODE_BRO_RE.search(text):
        return "bro"
    return None


def set_mode(new: str) -> None:
    """Сменить манеру сразу и запомнить в настройках."""
    import json
    config.PERSONALITY_MODE = new
    try:
        data = json.loads(config.SETTINGS_FILE.read_text(encoding="utf-8"))
    except Exception:
        data = {}
    data["PERSONALITY_MODE"] = new
    config.SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    config.SETTINGS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
