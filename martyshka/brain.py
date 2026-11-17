"""
МОЗГ МАРТЫШКИ
Отправляет твою фразу локальной модели (через Ollama) и возвращает ответ по кусочкам,
чтобы голос мог начать говорить, не дожидаясь конца ответа.

Из ответа вылавливаются служебные метки (брат их не видит и не слышит):
  [ЗАПОМНИ: ...] / [ЗАБУДЬ: ...]  — память о брате
  [ДЕЛО: открыть | стим]          — команда «рукам» (см. hands.py)
"""
import re
import time
from dataclasses import dataclass, field  # noqa: F401
from datetime import datetime
from dataclasses import dataclass, field
from typing import Iterator

import ollama

from . import calc, config, style
from .hands import ACTION_RE, APP_ALIASES, SITES, parse_action
from .memory import Memory
from .personality import build_system_prompt

TAG_RE = re.compile(r"\[\s*(ЗАПОМНИ|ЗАБУДЬ)\s*:\s*(.+?)\s*\]", re.IGNORECASE | re.DOTALL)

# Свежее напоминание к каждой фразе брата (в историю не сохраняется): на каком языке отвечать.
# Язык определяют уши по голосу; модель любит подражать своим прошлым ответам — поэтому напоминаем каждый раз.
LANG_REMINDERS = {
    "ru": "\n\n(Служебно: брат говорит по-русски — отвечай ТОЛЬКО по-русски, без украинского и суржика.)",
    "de": "\n\n(Служебно: брат говорит ПО-НЕМЕЦКИ — ты отлично понимаешь немецкий, переводить или искать его фразу "
          "в интернете НЕ нужно. Отвечай ТОЛЬКО по-немецки, на хорошем разговорном немецком, в своём характере, "
          "без русских слов (вместо «братан» — «Alter» или «Bruder»). Приветствие и болтовня — просто ответь. "
          "Дела и поиск свежих фактов (кто сейчас у власти, новости, цены) — метками, как обычно.)",
    "en": "\n\n(Служебно: брат говорит ПО-АНГЛИЙСКИ — ты отлично понимаешь английский, переводить или искать его "
          "фразу в интернете НЕ нужно. Отвечай ТОЛЬКО по-английски, в своём характере, без русских слов "
          "(вместо «братан» — «bro»). Приветствие и болтовня — просто ответь. "
          "Дела и поиск свежих фактов (кто сейчас у власти, новости, цены) — метками, как обычно.)",
    "uk": "\n\n(Службово: брат говорить УКРАЇНСЬКОЮ — відповідай ТІЛЬКИ українською, без суржику, у своєму характері. "
          "Мітки справ пиши як зазвичай.)",
}
WEATHER_ASK_RE = re.compile(r"(погод[аеуы]|прогноз|сколько\s+(сейчас\s+)?градус|какая\s+(сейчас\s+)?температур|"
                            r"будет\s+ли\s+дожд|\bwetter\b|wettervorhersage|wie\s+(viel|warm|kalt)\s+(grad|ist\s+es)|"
                            r"\bweather\b|how\s+(hot|cold|warm)\s+is\s+it|температур\w*\s+на\s+улиц|погоді|"
                            r"нуж\w*\s+ли\s+(сегодня\s+)?зонт|зонт\w*\s+брать)",
                            re.IGNORECASE)
# «погода» в другом смысле: запомнить, вписать в таблицу, напечатать, найти видео...
NOT_WEATHER_RE = re.compile(r"(запомни|забудь|таблиц|документ|напиши|напечатай|ютуб|youtube|видео|песн|"
                            r"music|musik|lied|song|вчера|gestern|yesterday|включи|поставь|запусти|мультик|"
                            r"ну,?\s+погоди)", re.IGNORECASE)
# «Напечатай: …» — вписать ровно эти слова (без перевода и пересказа)
TYPE_RE = re.compile(r"^\W*(?:напечатай|впиши|вбей|набери|введи)(?:\s+текст)?\s*[:—–-]\s*(.+)$", re.IGNORECASE)
TRANSLATE_RE = re.compile(r"(по-?\s?(немецки|английски|украински|русски|французски|испански)|перевед|на\s+"
                          r"(немецком|английском|украинском))", re.IGNORECASE)
# Просьба что-то сделать и ответ «сделал» — а метки дела нет (мозг «соврал»)
DO_RE = re.compile(r"^\W*(открой|запусти|закрой|выключи|включи|сверни|разверни|поставь|сделай|убавь|прибавь|"
                   r"заблокируй|перезагрузи|найди|напиши|напечатай|запиши|создай|переключи|покажи|вруби|вырубай|"
                   r"врубай|отмени|скопируй|прочитай|напомни|разбуди|засеки)\b|таймер|будильник", re.IGNORECASE)
FILE_MENTION_RE = re.compile(r"(скрин|загрузк|скачан|рабоч\w*\s+стол|нарисов|сгенер|\.\w{2,4}\b|под\s+названием|"
                             r"с\s+именем|из\s+папк|[a-z]:\\)", re.IGNORECASE)
OPEN_RE = re.compile(r"^\W*(открой|запусти|закрой|вруби|врубай|включи)\b", re.IGNORECASE)
REFUSE_RE = re.compile(r"(не\s+умею|не\s+могу|не\s+получится|не\s+в\s+силах|к\s+сожалению)", re.IGNORECASE)
CLAIM_RE = re.compile(r"\b(открыл\w*|закрыл\w*|запустил\w*|включил\w*|выключил\w*|поставил\w*|сделал\w*|свернул\w*|"
                      r"развернул\w*|напечатал\w*|записал\w*|создал\w*|нашёл|нашел|заблокировал\w*|отменил\w*|"
                      r"запущен\w*|открыт\w*|закрыт\w*|готово|напомню|разбужу|поставлю|засеку|сделаю|открою|"
                      r"включу|запишу|будет\s+сделано)\b", re.IGNORECASE)
DOC_KIND_RE = re.compile(r"\b(конспект|реферат|доклад|сочинени\w*|эссе|стать\w*|статейк\w*|шпаргалк\w*|"
                         r"инструкци\w*|документ\w*|текст)\b", re.IGNORECASE)
DOC_WRITE_RE = re.compile(r"\b(напиши|написать|напишешь|сделай|сделать|сделаешь|составь|составить|подготовь|"
                          r"набросай|накатай|создай|оформи)\b", re.IGNORECASE)
DOC_TOPIC_RE = re.compile(r"(?:на\s+тему|по\s+теме|по\s+поводу|насчёт|насчет|тема|про|об?|по)\s+[«\"]?(.+)",
                          re.IGNORECASE)
OTHER_CMD_RE = re.compile(r"\b(закрой|открой|запусти|включи|выключи|скинь|пришли|отправь|скриншот\w*|скрин|удали|"
                          r"сверни|напомни|поставь|перезагрузи)\w*", re.IGNORECASE)
PICS_RE = re.compile(r"(фот|картинк|изображени|иллюстрац|рисун)", re.IGNORECASE)
DOC_SIZE_RE = re.compile(r"(а4|a4|страниц\w*|лист\w*|фулл|full|полн\w*|подробн\w*|длинн\w*|больше|побольше|"
                         r"кратк\w*|коротк\w*|поменьше|\d+\s*слов)", re.IGNORECASE)
DOC_KINDS = {"конспект": "конспект", "реферат": "реферат", "доклад": "доклад", "сочинени": "сочинение",
             "эссе": "эссе", "стат": "статью", "шпаргалк": "шпаргалку", "инструкци": "инструкцию"}


def _doc_size(text: str) -> str:
    """Пожелания по объёму словами для мозга."""
    t = text.lower()
    m = re.search(r"(\d+)\s*(?:страниц|лист)", t)
    if m:
        n = int(m.group(1))
        return f"Объём: {n} полных страниц А4 (примерно {n * 450} слов)."
    if re.search(r"а4|a4|страниц|лист|фулл|full|полн", t):
        return "Объём: одна ПОЛНАЯ страница А4 — примерно 420 слов."
    m = re.search(r"(\d+)\s*слов", t)
    if m:
        return f"Объём: примерно {m.group(1)} слов."
    if re.search(r"подробн|длинн|больше", t):
        return "Объём: подробно, примерно 700–900 слов."
    if re.search(r"кратк|коротк|поменьше", t):
        return "Объём: коротко, примерно 200 слов."
    return ""


def document_request(text: str, last: dict | None = None) -> dict | None:
    """«Напиши конспект на тему КС2 на всю А4» → {topic, kind, details}. Иначе None.
    last — прошлый документ: «сделай на всю страницу А4» после него = переписать тот же документ."""
    kind_m = DOC_KIND_RE.search(text)
    if kind_m and DOC_WRITE_RE.search(text) and not re.search(r"напечатай|впиши|вставь|прочитай|открой\s+документ",
                                                               text, re.I):
        # В просьбе есть и другие дела («закрой F75…, напиши конспект…, скинь скриншот») — пусть мозг разложит
        # её на несколько дел по порядку (сюда не перехватываем, иначе всё станет «темой конспекта»)
        if OTHER_CMD_RE.search(re.sub(r"открой\s+(ворд|word)\w*\s+и", "", text, flags=re.I)):
            return None
        kind = next((v for k, v in DOC_KINDS.items() if kind_m.group(1).lower().startswith(k)), "документ")
        rest = text[kind_m.end():]
        tm = DOC_TOPIC_RE.search(rest) or DOC_TOPIC_RE.search(text)
        topic = tm.group(1) if tm else ""
        topic = re.split(r"[.;!?]|,\s|\s+(?:в\s+ворд\w*|в\s+word|на\s+(?:всю|весь|целую|целый|полную|фулл|\d+)|"
                         r"на\s+а4|объ[её]мом|размером|подробн\w*|кратк\w*|пожалуйста|плиз|чтобы|что\s*бы|"
                         r"с\s+фот\w*|с\s+картинк\w*|с\s+иллюстрац\w*|и\s+когда)\b", topic, flags=re.I)[0]
        topic = topic.strip(" .,!?«»\"")
        if not topic:
            return None
        details = f"Это {kind}. " + _doc_size(text)
        if kind == "конспект":
            details += " Конспект: чёткие подзаголовки и главные факты по пунктам, без воды."
        if PICS_RE.search(text):
            details += " С ИЛЛЮСТРАЦИЯМИ."
        return {"topic": topic, "kind": kind, "details": details.strip()}
    if last and DOC_SIZE_RE.search(text) and len(text.split()) <= 12:
        size = _doc_size(text)
        if size:
            details = re.sub(r"Объём:[^.]*\.", "", last["details"]).strip() + " " + size
            return dict(last, details=details.strip())
    return None


BRO_TALK_RE = re.compile(r"\b(брат\w*|бля\w*|ху[йяеё]\w*|пизд\w*|еба\w*|ёб\w*|чё|здарова|погнали|короче)\b",
                         re.IGNORECASE)
TIME_RE = re.compile(r"^\W*(который\s+час|сколько\s+(сейчас\s+)?времени|какое\s+сейчас\s+время|время\s+сколько|"
                     r"сколько\s+на\s+часах)\W*$", re.IGNORECASE)
DATE_RE = re.compile(r"^\W*(какое\s+(сегодня\s+)?число|какой\s+сегодня\s+день|какая\s+сегодня\s+дата|"
                     r"какое\s+число\s+сегодня|что\s+сегодня\s+за\s+день)\W*$", re.IGNORECASE)
DELETE_WHAT_RE = re.compile(r"\b(?:удали|удалить|удалите|сотри|стери|кдали|удоли|удолить)\w*\s+(.+?)(?=\s+и\s+|[.,;!?]|$)",
                            re.IGNORECASE)
TRANSLATE_TEXT_RE = re.compile(r"^\W*(переведи|перевод)\w*\s+(на|по-)\s*\w+\s*[:—–-]\s*\S", re.IGNORECASE)
REMEMBER_RE = re.compile(r"^\W*(?:мартышка\W+)?запомни\w*[,:]?\s+(?:что\s+|то,?\s+что\s+)?(.{3,})$", re.IGNORECASE)
GAME_NEW_RE = re.compile(r"\b(сделай|создай|напиши|придумай|запрограммируй|сгенерируй|разработай|сделать|создать|"
                         r"хочу)\b.{0,30}\bигр(у|ушку|улю|а|ы)?\b", re.IGNORECASE)
GAME_EDIT_RE = re.compile(r"\b(доработай|добавь|измени|исправь|улучши|поменяй|переделай|ускорь|замедли|сделай)\b"
                          r".{0,40}\b(в\s+игр[еу]|игру\s+(получше|лучше|сложнее|проще|быстрее|медленнее|красивее)|"
                          r"в\s+(неё|нее|ней))|^\W*доработай\s+игру", re.IGNORECASE)
DRAW_RE = re.compile(r"^\W*(?:мартышка\W+)?(?:можешь\s+|пожалуйста\s+)*(?:нарисуй|нарисовать|сгенерируй|сгенерировать|"
                     r"(?:сделай|создай)\s+(?:мне\s+)?(?:картинк\w*|изображени\w*|арт\w*|рисун\w*))\s*(?:мне\s+)?"
                     r"(?:картинк\w*|изображени\w*|арт)?\s*[:,—-]?\s*(.{3,})$", re.IGNORECASE)
EXE_RE = re.compile(r"\b(exe|ехе|екзе|экзе|экзешник\w*|ехешник\w*|исполняем\w+\s+файл\w*)\b|\.exe\b", re.IGNORECASE)


def exe_plan(text: str) -> list:
    """«сделай exe из змейки» → [exe]; «сделай игру гонки и собери exe» / «хочу игру про космос в exe» → [игра, exe]."""
    rest = re.split(r"\s+(?:и\s+)?(?:(?:сделай|собери|упакуй|сохрани|создай|преврати|скомпилируй)\w*\s+(?:\w+\s+){0,2})?"
                    r"(?:в\s+)?(?:exe|ехе|екзе|экзе|экзешник\w*|ехешник\w*|исполняем)", text, flags=re.I)[0]
    if EXE_RE.search(rest):          # фраза начинается с exe («сделай exe из змейки») — описания новой игры нет
        rest = ""
    words = re.findall(r"\w+", re.sub(r"\b(сделай|создай|напиши|придумай|хочу|игру|игра|мне|пожалуйста|в|из|"
                                      r"последн\w*|эту|её|ее|мою)\b", " ", rest, flags=re.I))
    if GAME_NEW_RE.search(rest) and len(words) >= 2:
        return [("игра", [rest.strip()]), ("exe", [""])]
    return [("exe", [text])]


STATE_QUESTIONS = [
    (re.compile(r"(что\s+нового\s+в\s+мире|какие\s+новости|последние\s+новости|новости\s+(сегодня|дня|за\s+сегодня)|"
                r"что\s+(сегодня\s+)?происходит\s+в\s+мире)", re.I), ("узнать", ["главные новости в мире сегодня"])),
    (re.compile(r"(что|чё|че)\s+(у\s+меня\s+)?(сейчас\s+)?на\s+экране|почему\s+(всё|все|экран)\s+(черн|чёрн|тёмн|темн)|"
                r"посмотри\s+на\s+экран|что\s+ты\s+видишь|что\s+за\s+ошибк", re.I), ("экран", [])),
    (re.compile(r"(как(ая|ой)|сколько|на\s+сколько).{0,20}(громкост|звук)|громкост\w*\s+(сейчас|какая)", re.I),
     ("громкость", ["сколько"])),
    (re.compile(r"(что|чего).{0,25}(в\s+буфер|буфер\w*\s+обмен|скопирован)", re.I), ("буфер", [])),
    (re.compile(r"(что|какие).{0,20}(в\s+(моих\s+)?заметках|мои\s+заметки)|прочитай\s+(мои\s+)?заметки", re.I),
     ("заметки", [])),
    (re.compile(r"(сколько|как).{0,20}(мест\w*\s+на\s+диск|свободн\w*\s+мест)|(загрузк|температур)\w*\s+(процессор|"
                r"видеокарт|компа|пк)|комп\w*\s+(греется|тормозит|грузится)", re.I), ("система", [])),
]
SUMMARY_RE = re.compile(r"(сводк|что\s+(у\s+нас|у\s+меня)\s+(на\s+)?сегодня|какие\s+планы\s+на\s+сегодня)",
                        re.IGNORECASE)
CITY_RE = re.compile(r"\b(?:в|во|у|in)\s+([A-ZА-ЯЁÄÖÜІЇЄ][\w\-() ]+)")
SMALLTALK_RE = re.compile(r"(\bhallo\b|\bhi\b|\bhey\b|\bhello\b|guten\s+(morgen|tag|abend)|wie\s+geht|was\s+geht|"
                          r"how\s+are\s+you|what'?s\s+up|how'?s\s+it\s+going|servus|moin|привіт|як\s+справи|"
                          r"was\s+machst|what\s+are\s+you\s+doing|danke|thank)", re.IGNORECASE)
LANG_NAMES = {"ru": "по-русски", "de": "по-немецки", "en": "по-английски", "uk": "по-украински"}
LANGUAGE_REMINDER = LANG_REMINDERS["ru"]


def reply_language(text: str) -> str:
    """На каком языке написан ответ: украинские буквы → uk, латиница → en/de, иначе ru."""
    if re.search(r"[іїєґІЇЄҐ]", text):
        return "uk"
    lat, cyr = len(re.findall(r"[A-Za-zÄÖÜäöüß]", text)), len(re.findall(r"[А-Яа-яЁё]", text))
    if lat > cyr:
        de = re.search(r"[äöüßÄÖÜ]|\b(und|ich|nicht|das|ist|du|ja|der|die|ein|was|wie|mit)\b", text, re.I)
        return "de" if de else "en"
    return "ru"

# «Открываю» → «Открыл»: фраза о деле звучит уже после того, как дело сделано
PAST_TENSE = {
    "открываю": "открыл", "закрываю": "закрыл", "запускаю": "запустил", "включаю": "включил",
    "выключаю": "выключил", "врубаю": "врубил", "врубал": "врубил", "вырубаю": "вырубил", "ставлю": "поставил",
    "делаю": "сделал", "создаю": "создал", "записываю": "записал", "ищу": "нашёл", "переключаю": "переключил",
    "убавляю": "убавил", "прибавляю": "прибавил", "сохраняю": "сохранил", "сворачиваю": "свернул",
}


def to_past_tense(text: str) -> str:
    def fix(m: re.Match) -> str:
        word = m.group(0)
        past = PAST_TENSE.get(word.lower())
        if not past:
            return word
        return past.capitalize() if word[0].isupper() else past
    return re.sub(r"[А-Яа-яЁё]+", fix, text)


# Буквы, которых нет в русском: если они в ответе — это украинский/суржик
NOT_RUSSIAN_RE = re.compile(r"[іїєґІЇЄҐ]")
ANY_TAG_RE = re.compile(r"\[[^\]]*\]?", re.DOTALL)  # любая метка (в том числе недописанная)


@dataclass
class MemoryChanges:
    remembered: list[str] = field(default_factory=list)
    forgotten: list[str] = field(default_factory=list)


class Brain:
    def __init__(self, memory: Memory | None = None):
        self.memory = memory or Memory()
        self.client = ollama.Client()
        self.last_changes = MemoryChanges()
        self.last_actions: list[tuple[str, list[str]]] = []
        self.pending_say = ""
        self.lang = "ru"   # на каком языке сейчас говорит брат (определяют уши)
        self.action_log: list = []          # недавние дела (время, дело, аргументы) — для «закрой его», «ещё»
        self.last_doc: dict | None = None   # последний заказанный документ («сделай подробнее» — переписать его)
        self.last_doc_time = 0.0

    @staticmethod
    def _think() -> dict:
        """Модели, которые умеют «думать вслух» (Qwen), — без этого: голосовому помощнику важна скорость."""
        return {"think": False} if config.MODEL.lower().startswith(("qwen3", "deepseek-r1", "gpt-oss", "gemma4")) else {}

    def _options(self, **extra) -> dict:
        return {"num_ctx": config.CONTEXT_SIZE, "temperature": config.TEMPERATURE, **extra}

    def _messages(self, user_text: str | None = None, extra_system: str | None = None,
                  history_n: int | None = None) -> list[dict]:
        msgs = [{"role": "system", "content": build_system_prompt(self.memory.fact_texts())}]
        hist = []
        for m in self.memory.recent_history():
            # Метки дел из прошлых ответов модели НЕ показываем — иначе она повторяет старые дела
            # («закрыть эксель» на вопрос «как дела»). Остаются только слова.
            content = ANY_TAG_RE.sub("", m["content"]).strip()
            if not content:
                if m["role"] == "assistant" and "[" in m["content"]:
                    content = "Сделано."   # ответ был только делом — помечаем, что просьба уже выполнена
                else:
                    continue
            # Говорим по-русски — старые ответы на суржике не показываем (и вопрос к ним тоже), иначе подражает
            if self.lang == "ru" and m["role"] == "assistant" and NOT_RUSSIAN_RE.search(content):
                if hist and hist[-1]["role"] == "user":
                    hist.pop()
                continue
            # Режим Джарвиса: старые «братские» ответы с матом не показываем — иначе мозг начнёт им подражать
            if style.is_jarvis() and m["role"] == "assistant" and BRO_TALK_RE.search(content):
                content = "Готово."
            hist.append({"role": m["role"], "content": content})
        msgs += hist[-history_n:] if history_n else hist
        if user_text is not None:
            reminder = style.adapt(LANG_REMINDERS.get(self.lang, LANGUAGE_REMINDER))
            if style.is_jarvis() and self.lang == "ru":
                reminder += f" (Манера: вежливо, на «вы», без мата и жаргона, обращение «{style.sir()}» — изредка.)"
            msgs.append({"role": "user", "content": user_text + reminder})
        if extra_system:
            msgs.append({"role": "system", "content": style.adapt(extra_system)})
        return msgs

    def warm_up(self) -> None:
        """Заранее загружает модель в видеокарту, чтобы первый ответ был быстрым."""
        # Важно: те же настройки, что и в разговоре, иначе Ollama перезагрузит модель на первом вопросе.
        # Заодно «прочитываем» инструкцию с характером — Ollama её запомнит, и первый ответ будет быстрым.
        self.client.chat(model=config.MODEL, **self._think(), messages=self._messages("привет"),
                         keep_alive=config.KEEP_ALIVE, options=self._options(num_predict=1))

    def unload(self) -> None:
        """Режим игр: выгрузить мозг из видеокарты."""
        try:
            self.client.generate(model=config.MODEL, prompt="", keep_alive=0)
        except Exception:
            pass

    def reply(self, user_text: str, lang: str = "ru") -> Iterator[str]:
        """Отвечает на фразу брата. Выдаёт текст кусочками (без служебных меток).
        lang — язык, на котором говорит брат (ru/de/en/uk): на нём и отвечаем.
        После окончания: self.last_actions — что попросили сделать руками, self.last_changes — память."""
        try:
            yield from self._reply(user_text, lang)
        finally:
            if self.last_actions:   # запоминаем, что делали — чтобы понимать «закрой его», «ещё», «обратно»
                now = time.time()
                self.action_log = ([e for e in self.action_log if now - e[0] < 1800]
                                   + [(now, n, a) for n, a in self.last_actions])[-6:]

    def recent_actions_hint(self) -> str:
        """Служебная подсказка мозгу: последние дела (для коротких и неполных фраз)."""
        now = time.time()
        recent = [e for e in self.action_log if now - e[0] < 1800]
        if not recent:
            return ""
        def ago(t):
            m = int((now - t) // 60)
            return "только что" if m < 1 else f"{m} мин назад"
        items = "; ".join(f"{n}{(' | ' + ' | '.join(a)) if a else ''} ({ago(t)})" for t, n, a in recent[-4:])
        return (f"\n(Служебно: недавние дела — {items}. Если фраза брата короткая или неполная («ещё», «громче», "
                "«закрой его», «обратно», «а теперь дискорд», «а в Берлине?») — пойми её по смыслу и по этим делам "
                "и выполни. Старые дела сами по себе НЕ повторяй.)")

    def _reply(self, user_text: str, lang: str = "ru") -> Iterator[str]:
        self.lang = lang if lang in LANG_REMINDERS else "ru"
        self.last_changes = MemoryChanges()
        self.last_actions = []
        self.pending_say = ""   # фраза, которую надо сказать ПОСЛЕ выполнения дела
        # «Режим Джарвис» / «режим брат» — сменить манеру общения прямо голосом
        new_mode = style.switch_request(user_text)
        if new_mode:
            style.set_mode(new_mode)
            self.lang = "ru"
            yield style.phrase("mode_" + new_mode)
            return
        # Конспект / реферат / доклад на тему … — сразу дело «документ» (мозг путал его с пустым Word)
        doc = document_request(user_text, self.last_doc if time.time() - self.last_doc_time < 1800 else None)
        if doc:
            self.last_doc, self.last_doc_time = doc, time.time()
            self.last_actions = [("документ", [doc["topic"], doc["details"]])]
            self.pending_say = f"Готово, написал {doc['kind']} про {doc['topic']}."
            self.memory.add_message("user", user_text)
            self.memory.add_message("assistant", f"[ДЕЛО: документ | {doc['topic']}] Пишу {doc['kind']} в Word.")
            yield f"Пишу {doc['kind']} в Word, смотри."
            return
        # «Зайди в настройки кс / звука / загрузки стима» — сразу прямой ссылкой (раньше агент кликал 10 шагов)
        from . import goto
        if goto.can_go(user_text):
            self.last_actions = [("зайти", [user_text])]
            self.memory.add_message("user", user_text)
            self.memory.add_message("assistant", "[ДЕЛО: зайти] Открываю.")
            yield style.phrase("done") if style.is_jarvis() else "Ща, открываю."
            return
        # «Нарисуй …» — прямо в генератор словами брата (мозг иногда переписывал или отказывался)
        dm = DRAW_RE.match(user_text.strip())
        if dm and not re.search(r"\b(по\s+(этому|этой|моему|моей)?\s*фот\w*|эт\w+\s+фот\w*|с\s+фот\w*|из\s+фот\w*|"
                                r"видео|exe|игр\w*)\b", user_text, re.I):
            desc = dm.group(1).strip(" .!")
            self.last_actions = [("нарисуй", [desc])]
            self.memory.add_message("user", user_text)
            self.memory.add_message("assistant", f"[ДЕЛО: нарисуй | {desc}] Рисую.")
            yield "Рисую, пару секунд." if not style.is_jarvis() else "Приступаю к изображению."
            return
        # EXE: «сделай exe из змейки» / «сделай игру гонки и собери exe»
        if EXE_RE.search(user_text):
            self.last_actions = exe_plan(user_text)
            new_game = self.last_actions[0][0] == "игра"
            self.memory.add_message("user", user_text)
            self.memory.add_message("assistant", "[ДЕЛО: exe] Собираю exe.")
            yield ("Сначала сделаю игру, потом упакую её в exe. Пару минут." if new_game
                   else ("Собираю exe." if style.is_jarvis() else "Ща соберу exe, секунду."))
            return
        # Игры: «сделай игру …» / «доработай игру: …» — сразу в мастерскую игр
        if GAME_EDIT_RE.search(user_text):
            self.last_actions = [("доработать игру", [user_text])]
            self.memory.add_message("user", user_text)
            self.memory.add_message("assistant", "[ДЕЛО: доработать игру] Дорабатываю игру.")
            yield style.phrase("done") if style.is_jarvis() else "Дорабатываю, сейчас."
            return
        if GAME_NEW_RE.search(user_text) and not re.search(r"режим\w*\s+игр|запусти|открой|включи", user_text, re.I):
            self.last_actions = [("игра", [user_text])]
            self.memory.add_message("user", user_text)
            self.memory.add_message("assistant", "[ДЕЛО: игра] Делаю игру.")
            yield ("Будет сделано. Продумываю игру, это займёт пару минут." if style.is_jarvis()
                   else "О, игру делаем! Сейчас продумаю и напишу, пару минут.")
            return
        # «Сводка» — сразу дело (мозг иногда отвечает из головы)
        if SUMMARY_RE.search(user_text):
            self.last_actions = [("сводка", [])]
            self.memory.add_message("user", user_text)
            self.memory.add_message("assistant", "[ДЕЛО: сводка]")
            return
        # Время и дата — знаем точно, отвечаем сразу (мозг иногда лезет за ними в интернет)
        if self.lang == "ru" and (TIME_RE.search(user_text) or DATE_RE.search(user_text)):
            from .personality import MONTHS, WEEKDAYS, time_in_words
            now = datetime.now()
            if TIME_RE.search(user_text):
                answer = f"Сейчас {time_in_words(now)}"
            else:
                answer = f"Сегодня {WEEKDAYS[now.weekday()]}, {now.day} {MONTHS[now.month - 1]}"
            answer += (f", {style.sir()}." if style.is_jarvis() else ".")
            self.memory.add_message("user", user_text)
            self.memory.add_message("assistant", answer)
            yield answer
            return
        # Одно название программы или сайта («ютуб», «стим», «дискорд») — просто открыть
        bare = re.sub(r"[^\wа-яё ]", "", user_text.lower()).strip()
        if bare and len(bare.split()) <= 2 and (bare in SITES or bare in APP_ALIASES) and bare not in ("погода",):
            self.last_actions = [("открыть", [bare])]
            self.pending_say = style.phrase("done")
            self.memory.add_message("user", user_text)
            self.memory.add_message("assistant", f"[ДЕЛО: открыть | {bare}]")
            return
        # Вопросы про состояние компа — мозг его не видит и может выдумать, поэтому сразу проверяем по-настоящему
        for pattern, action in STATE_QUESTIONS:
            if pattern.search(user_text):
                self.last_actions = [(action[0], [user_text] if action[0] == "экран" else action[1])]
                self.memory.add_message("user", user_text)
                self.memory.add_message("assistant", f"[ДЕЛО: {action[0]}]")
                return
        # Вопрос о погоде (на любом языке) — сразу в настоящую погоду, мозг её не выдумывает
        if WEATHER_ASK_RE.search(user_text) and not NOT_WEATHER_RE.search(user_text):
            city = CITY_RE.search(user_text)
            self.last_actions = [("погода", [city.group(1).strip(" ?.!,") if city else ""])]
            self.memory.add_message("user", user_text)
            self.memory.add_message("assistant", "[ДЕЛО: погода]")
            return
        # «Напечатай: привет, как дела» — вписать ровно это (раньше мозг мог перевести на немецкий)
        m = TYPE_RE.match(user_text.strip())
        if m and not TRANSLATE_RE.search(user_text):
            self.last_actions = [("напечатать", [m.group(1).strip()])]
            self.pending_say = style.phrase("done") if style.is_jarvis() else "Напечатал."
            self.memory.add_message("user", user_text)
            self.memory.add_message("assistant", "[ДЕЛО: напечатать] Напечатал.")
            return
        # Арифметику считаем сами и подсказываем мозгу точный ответ (в уме он ошибается)
        prompt_text = user_text + self.recent_actions_hint()
        if DOC_KIND_RE.search(user_text) and OTHER_CMD_RE.search(user_text):   # сложная просьба с документом
            prompt_text += ("\n(Служебно: это просьба из нескольких частей. Разложи её на дела ПО ПОРЯДКУ, каждое "
                            "своей меткой. Конспект/реферат/доклад → [ДЕЛО: документ | КОРОТКАЯ тема 1–4 слова | "
                            "пожелания: объём, «с иллюстрациями», если просят фото]. «Скинь/пришли скриншот, когда "
                            "готово» → [ДЕЛО: скриншот] последним.)")
        if TRANSLATE_TEXT_RE.search(user_text):   # «переведи на немецкий: …» — перевести самому, без дел
            prompt_text = user_text + ("\n\n(Служебно: просто переведи этот текст сам и скажи перевод. "
                                       "Никаких меток дел.)")
        exact = calc.solve(user_text) if self.lang == "ru" else None
        if exact is not None:
            prompt_text += f"\n\n(Служебно: точный ответ — {exact}. Назови именно это число, словами, сам не пересчитывай.)"
        full = yield from self._stream(self._messages(prompt_text), user_text)
        self.last_changes = self._apply_memory_tags(full)
        self.last_actions = [parse_action(body) for body in ACTION_RE.findall(full)]
        if TRANSLATE_TEXT_RE.search(user_text):
            self.last_actions = []
        # Удаление: ЧТО удалять — ровно словами брата, не пересказом мозга (он путал «документ» с «фото»)
        dm = DELETE_WHAT_RE.search(user_text)
        if dm and any(n == "удалить" for n, _ in self.last_actions):
            self.last_actions = [(n, [dm.group(1).strip()] if n == "удалить" else a) for n, a in self.last_actions]
        # «Включи последний клип …» — искать в интернете отдельно не нужно, «включить» само найдёт видео
        if any(n in ("включить", "таблица", "документ") for n, _ in self.last_actions):
            self.last_actions = [(n, a) for n, a in self.last_actions if n != "узнать"]   # они сами всё найдут
        elif (re.match(r"^\W*(включи|поставь|запусти)\s+\S*\s*(клип|видео|песн|трек|мультик|серию|ролик)", user_text,
                       re.I) and self.last_actions and all(n == "узнать" for n, _ in self.last_actions)):
            self.last_actions = [("включить", self.last_actions[0][1])]
        # «Запомни, что …» — а метки нет: запоминаем сами (иначе «запомнил» было бы враньём)
        rm = REMEMBER_RE.match(user_text)
        if rm and not self.last_changes.remembered:
            fact = re.sub(r"^я\s+", "", rm.group(1).strip(" .!")).strip()
            if fact and self.memory.remember(fact):
                self.last_changes.remembered.append(fact)
        # Фото/видео: «какой файл» — только если брат назвал его в ЭТОЙ фразе (мозг любит тащить из прошлых)
        if not FILE_MENTION_RE.search(user_text):
            self.last_actions = [(n, a[:1]) if n in ("фото", "видео") and len(a) > 1 else (n, a)
                                 for n, a in self.last_actions]
        # Мозг сказал «открыл», а метку дела не поставил — переспрашиваем у него только метку и делаем
        # …или зря отказался («не умею открывать калькулятор» — а открыть можно любую программу)
        if (not self.last_actions and DO_RE.search(user_text) and not TRANSLATE_RE.search(user_text)
                and (CLAIM_RE.search(full) or (OPEN_RE.search(user_text) and REFUSE_RE.search(full)))):
            try:
                self.last_actions = self.actions_only(user_text + self.recent_actions_hint())
            except Exception:
                self.last_actions = []
            if self.last_actions and REFUSE_RE.search(full):
                self.pending_say = style.phrase("done")   # сказал «не умею», но сделал — поправимся
        # Приветствие/болтовню на чужом языке мозг иногда «ищет в интернете» — не надо, просто отвечаем
        if (self.lang != "ru" and SMALLTALK_RE.search(user_text)
                and any(a[0] == "узнать" for a in self.last_actions)):
            self.last_actions = [a for a in self.last_actions if a[0] != "узнать"]
            hint = (f"Брат просто болтает ({user_text!r}) — ничего искать не надо. Ответь ему сам, коротко, "
                    f"в своём характере. Без меток. Отвечай {LANG_NAMES[self.lang]}.")
            self.memory.history = [m for m in self.memory.history if "[ДЕЛО: узнать" not in m["content"]]
            self.pending_say = ""
            yield from self._stream(self._messages(extra_system=hint, history_n=4), None)

    def say_text(self, text: str) -> Iterator[str]:
        """Просто отдать готовую фразу кусочком (чтобы озвучить так же, как ответ мозга).
        Фраза звучит ПОСЛЕ выполнения дела — поэтому «открываю» поправляем на «открыл»."""
        done = {"ru": "Готово.", "de": "Erledigt.", "en": "Done.", "uk": "Готово."}.get(self.lang, "Готово.")
        if self.lang == "ru":
            text = to_past_tense(text)
        text = text.strip()
        # Длинный текст (реферат, таблица) уже в документе — вслух только первое предложение
        if len(text) > 160:
            first = re.split(r"(?<=[.!?])\s", text, maxsplit=1)[0]
            text = first if len(first) <= 160 else done
        # Фраза не на языке разговора (например, то, что напечатали по-немецки) — вслух её не читаем
        if text and reply_language(text) != self.lang:
            text = done
        if text:
            yield text

    def report(self, results: str) -> Iterator[str]:
        """Пересказать брату результат дела (загрузка системы, заметки, ошибка...)."""
        if len(results) > 600:   # большой материал (из интернета) — пересказать по существу
            note = (f"Вот что ты нашёл (материалы из интернета и результаты дел):\n{results}\n\n"
                    "Ответь брату на его последний вопрос по этим материалам: по существу, своими словами, "
                    "с фактами и цифрами оттуда. Не выдумывай того, чего там нет. Не говори про «материалы» "
                    f"и «источники», просто отвечай. Без меток, без списков. Отвечай {LANG_NAMES[self.lang]}.")
            msgs = self._messages(extra_system=note, history_n=4)   # меньше истории — больше места материалу
        else:
            note = (f"Результат того, что ты сделал: {results}\n"
                    f"Скажи брату коротко и по-человечески, что вышло. Без меток. Отвечай {LANG_NAMES[self.lang]}.")
            msgs = self._messages(extra_system=note)
        yield from self._stream(msgs, None)

    def recover(self, failed: str, request: str) -> list[tuple[str, list[str]]]:
        """Дело не вышло — найти другой путь (до 3 дел). Пусто — другого пути нет."""
        note = (f"Брат попросил: «{request}». Ты попробовал, но не вышло: {failed}\n"
                "Найди ДРУГОЙ путь через доступные дела. Например: программы нет на компе — открой её сайт или "
                "найди в поиске; сайт не открылся — поиск; файл не нашёлся — поищи его под другим названием или открой "
                "папку, где он может лежать (документы, загрузки, рабочий стол) — НЕ ищи личные файлы в интернете; "
                "видео не нашлось — ютуб с поиском. Ответь ТОЛЬКО метками дел (до 3), без слов. Если разумного другого пути "
                "нет — ответь одним словом: НЕТ.")
        msgs = [{"role": "system", "content": build_system_prompt([])},
                {"role": "user", "content": style.adapt(note)}]
        r = self.client.chat(model=config.MODEL, **self._think(), messages=msgs, keep_alive=config.KEEP_ALIVE,
                             options=self._options(num_predict=150, temperature=0.3))
        return [parse_action(body) for body in ACTION_RE.findall(r["message"]["content"])][:3]

    def actions_only(self, step: str) -> list[tuple[str, list[str]]]:
        """Перевести шаг своей команды («открой стим») в дела. Без разговора и без истории."""
        msgs = [{"role": "system", "content": build_system_prompt([])},
                {"role": "user", "content": step + "\n\n(Служебно: только метки дел, никаких слов.)"}]
        r = self.client.chat(model=config.MODEL, **self._think(), messages=msgs, keep_alive=config.KEEP_ALIVE,
                             options=self._options(num_predict=120, temperature=0.2))
        return [parse_action(body) for body in ACTION_RE.findall(r["message"]["content"])]

    def complete_raw(self, system: str, prompt: str, max_tokens: int, num_ctx: int) -> str:
        """Большая служебная работа (код игры): своя инструкция и увеличенная «рабочая память».
        После неё мозг сам перезагрузится с обычной памятью на следующем вопросе."""
        r = self.client.chat(model=config.MODEL, **self._think(), keep_alive=config.KEEP_ALIVE,
                             messages=[{"role": "system", "content": system}, {"role": "user", "content": prompt}],
                             options={"num_ctx": num_ctx, "temperature": 0.3, "num_predict": max_tokens})
        return r["message"]["content"]

    def briefing(self, facts: str) -> Iterator[str]:
        """Утренняя сводка: поздороваться и коротко рассказать погоду, напоминания, заряд."""
        self.lang = "ru"
        from datetime import datetime
        h = datetime.now().hour
        part = "утро" if 5 <= h < 12 else "день" if h < 17 else "вечер" if h < 23 else "ночь"
        note = (f"Брат только что включил комп — первый раз за сегодня (сейчас {part}, {datetime.now():%H:%M}). "
                f"Вот свежие данные:\n{facts}\n\n"
                "Поздоровайся коротко и по-дружески, в своём характере (без блатного жаргона, без «давно не "
                "виделись»), и расскажи сводку в 3–5 предложениях: погода на сегодня (температура, дождь, "
                "одеться ли теплее), напоминания и пропущенные напоминания, заряд устройств — только то, что есть "
                "в данных. Если что-то почти село — скажи зарядить. Напоминаний нет — пропусти. "
                "Без меток, без списков, по-русски, без ошибок.")
        yield from self._stream(self._messages(extra_system=note, history_n=2), None)

    def complete(self, prompt: str, max_tokens: int = 1500) -> str:
        """Служебная просьба к мозгу (составить таблицу, написать текст) — без характера и без истории."""
        r = self.client.chat(model=config.MODEL, **self._think(), keep_alive=config.KEEP_ALIVE,
                             messages=[{"role": "system", "content": "Ты аккуратный помощник. Отвечай строго в "
                                        "запрошенном формате, без пояснений и без markdown."},
                                       {"role": "user", "content": prompt}],
                             options={"num_ctx": config.CONTEXT_SIZE, "temperature": 0.3, "num_predict": max_tokens})
        return r["message"]["content"].strip()

    # --- общая часть ---
    def _stream(self, messages: list[dict], user_text: str | None) -> Iterator[str]:
        stream = self.client.chat(model=config.MODEL, **self._think(), messages=messages, stream=True,
                                  keep_alive=config.KEEP_ALIVE, options=self._options())
        full = ""      # весь ответ модели как есть
        shown = 0      # сколько символов уже отдали наружу
        finished = False
        try:
            for chunk in stream:
                full += chunk["message"]["content"]
                # Всё, что начинается с «[», придерживаем: это может быть служебная метка
                cut = full.find("[", shown)
                safe_end = len(full) if cut == -1 else cut
                if safe_end > shown:
                    yield full[shown:safe_end]
                    shown = safe_end
            # Ответ закончился: убираем метки, остальной текст отдаём
            visible_tail = ANY_TAG_RE.sub("", full[shown:]).strip()
            if shown == 0 and ACTION_RE.match(full.lstrip()):
                # Ответ начался с метки дела: фразу НЕ озвучиваем сразу —
                # сначала программа выполнит дело, потом скажет её (self.pending_say)
                self.pending_say = visible_tail
            elif visible_tail:
                yield (" " if shown else "") + visible_tail
            finished = True
        finally:
            # Сохраняем разговор, даже если брат перебил на полуслове
            if finished:
                saved = TAG_RE.sub("", full).strip()  # метки дел оставляем — так модель помнит, что делала
            else:
                saved = (ANY_TAG_RE.sub("", full[:shown]).strip() + " … (брат перебил)").strip()
                if hasattr(stream, "close"):
                    stream.close()
            # Ответ на украинском/суржике в историю не пускаем — иначе модель начнёт ему подражать
            # Говорили по-русски, а ответ съехал в суржик — в историю не пускаем (иначе начнёт подражать)
            if self.lang == "ru" and NOT_RUSSIAN_RE.search(saved):
                saved = ""
            if user_text is not None and saved:
                self.memory.add_message("user", user_text)
            if saved:
                self.memory.add_message("assistant", saved)
        return full

    def reply_text(self, user_text: str) -> str:
        """То же самое, но сразу весь ответ одной строкой."""
        return "".join(self.reply(user_text)).strip()

    def _apply_memory_tags(self, text: str) -> MemoryChanges:
        changes = MemoryChanges()
        for kind, value in TAG_RE.findall(text):
            if kind.upper() == "ЗАПОМНИ":
                if self.memory.remember(value):
                    changes.remembered.append(value)
            else:
                changes.forgotten += self.memory.forget(value)
        return changes
