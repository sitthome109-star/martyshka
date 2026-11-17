"""
ОКНО НАСТРОЕК МАРТЫШКИ (открывается из меню шара: щелчок по шару → «Настройки…»).

Всё, что тут меняется, сохраняется в data/settings.json и перекрывает значения из config.py.
Цвет и размер шара меняются сразу; остальное — кнопкой «Сохранить и перезапустить».
"""
import json
import os
from pathlib import Path

from . import config

# Разделы: (значок, название, подзаголовок)
SECTIONS = [
    ("🧠", "Мозг и характер", "Как Мартышка думает и общается"),
    ("🔊", "Голос", "Как Мартышка говорит"),
    ("👂", "Уши", "Как Мартышка слышит тебя"),
    ("🔵", "Шар", "Как Мартышка выглядит на экране"),
    ("⚡", "Мои команды", "Одна фраза — и Мартышка делает сразу несколько дел"),
    ("🌅", "Утренняя сводка", "Что рассказать, когда включаешь комп первый раз за день"),
    ("📱", "Телефон", "Пиши Мартышке в Telegram, пока комп включён"),
    ("🌐", "Интернет", "Поиск, погода и подключения"),
    ("🧩", "Память", "Что Мартышка помнит о тебе"),
    ("🔧", "Система", "Запуск, видеокарта, файлы"),
]

# Настройки: (раздел, ключ из config, название, пояснение, тип, параметры)
#   типы: switch, slider(от, до, шаг, подписи), choice[(значение, подпись)], text, longtext, file, mic, model, color
FIELDS = [
    ("Мозг и характер", "MODEL", "Модель мозга", "Какая нейросеть думает за Мартышку. Другие можно скачать в Ollama.",
     "model", None),
    ("Мозг и характер", "PERSONALITY_MODE", "Манера общения", "«Свой в доску» — как сейчас: «брат», мат к месту, "
     "подколы. «Джарвис» — спокойный вежливый ИИ-дворецкий: на «вы», «сэр», без мата. Можно и голосом: "
     "«Мартышка, режим Джарвис» / «Мартышка, режим брат».", "choice",
     [("bro", "Свой в доску (с матом)"), ("jarvis", "Джарвис (спокойный, вежливый)")]),
    ("Мозг и характер", "JARVIS_ADDRESS", "Обращение в режиме Джарвиса", "Как Мартышка называет тебя в вежливом "
     "режиме: «сэр», «босс», «мистер Старк»…", "text", None),
    ("Мозг и характер", "OWNER_NICKNAME", "Обращение в режиме «свой в доску»", "Как Мартышка называет тебя по-свойски (по ситуации).",
     "text", None),
    ("Мозг и характер", "TEMPERATURE", "Креативность", "Левее — спокойные и предсказуемые ответы, правее — дерзкие и неожиданные.",
     "slider", (0.1, 1.5, 0.05, "спокойно", "дико")),
    ("Мозг и характер", "HISTORY_IN_PROMPT", "Память разговора", "Сколько последних реплик Мартышка держит в голове.",
     "slider", (4, 30, 2, "мало", "много")),
    ("Мозг и характер", "CONTEXT_SIZE", "Объём «рабочей памяти»", "Больше — длиннее разговоры, но занимает видеопамять. "
     "Больше 6144 может тормозить вместе с голосом и ушами.", "choice", [(4096, "4096 — экономно"),
                                                                          (6144, "6144 — оптимально"),
                                                                          (8192, "8192 — много (может тормозить)")]),
    ("Мозг и характер", "PERSONALITY_EXTRA", "Добавить к характеру", "Напиши своими словами, какой должна быть Мартышка. "
     "Например: «болей за Баварию», «чаще шути про работу».", "longtext", None),

    ("Голос", "VOICE_ENABLED", "Голос включён", "Выключишь — Мартышка будет отвечать только текстом в журнале.",
     "switch", None),
    ("Голос", "VOICE_REFERENCE_WAV", "Чей голос", "Каким голосом говорит Мартышка. «Прослушать» — как она звучит "
     "(с эффектом ниже). Свой голос можно записать внизу страницы или выбрать WAV-файл.", "voice", None),
    ("Голос", "VOICE_FX", "Эффект «голос ИИ»", "Металлический блеск и лёгкое эхо, как у компьютерного помощника "
     "в кино. 0 — обычный голос.", "slider", (0.0, 1.5, 0.1, "выкл", "сильно")),
    ("Голос", "VOICE_ENGINE", "Движок голоса", "Qwen3-TTS повторяет выбранный голос — живее и быстрее, "
     "говорит и по-немецки/английски тем же голосом. ESpeech — прежний вариант.", "choice",
     [("qwen_clone", "Qwen3-TTS — клон голоса"), ("espeech", "ESpeech (прежний)")]),
    ("Голос", "VOICE_TEMPERATURE", "Живость", "Левее — ровно и спокойно, правее — эмоциональнее, "
     "но иногда с причудами. (Для Qwen3-TTS.)", "slider", (0.5, 1.0, 0.05, "ровно", "живо")),
    ("Голос", "VOICE_VOLUME", "Громкость", "Громкость голоса Мартышки (не влияет на громкость Windows).",
     "slider", (0.3, 2.5, 0.1, "тихо", "громко")),
    ("Голос", "VOICE_SPEED", "Скорость речи", "Как быстро Мартышка говорит.", "slider", (0.7, 1.4, 0.05, "медленно", "быстро")),
    ("Голос", "VOICE_QUALITY", "Качество (для ESpeech)", "Чище — чуть дольше думает перед каждой фразой.",
     "choice", [(16, "быстро"), (24, "средне"), (32, "чище")]),
    ("Голос", "VOICE_REFERENCE_TEXT", "Текст образца (для ESpeech)", "Слово в слово то, что звучит в файле-образце. "
     "Для Qwen можно не заполнять.", "longtext", None),

    ("Уши", "MIC_DEVICE", "Микрофон", "Каким микрофоном Мартышка тебя слушает.", "mic", None),
    ("Уши", "WAKE_BEEP", "«Пик» на имя", "Короткий звук, когда Мартышка услышала «Мартышка».", "switch", None),
    ("Уши", "FOLLOWUP_SECONDS", "Ждать продолжения", "Сколько секунд после ответа можно говорить без имени. 0 — выключить.",
     "slider", (0, 20, 1, "0 сек", "20 сек")),
    ("Уши", "END_OF_SPEECH_SILENCE", "Пауза = конец фразы", "Сколько молчать, чтобы Мартышка поняла, что ты договорил.",
     "slider", (0.4, 2.0, 0.1, "быстро", "терпеливо")),
    ("Уши", "SPEECH_THRESHOLD", "Строгость к шуму", "Правее — меньше реагирует на фон, видео и шорохи.",
     "slider", (0.3, 0.9, 0.05, "чутко", "строго")),
    ("Уши", "EARS_MULTILANG", "Другие языки", "Понимать немецкий, английский и украинский и отвечать на них.",
     "switch", None),

    ("Шар", "FACE_ENABLED", "Шар на экране", "Показывать живой шар Мартышки.", "switch", None),
    ("Шар", "FACE_COLOR", "Цвет", "Цвет шара — меняется сразу.", "color", None),
    ("Шар", "FACE_SIZE", "Размер в разговоре", "Каким становится шар, когда ты говоришь с Мартышкой.",
     "slider", (80, 300, 10, "маленький", "огромный")),
    ("Шар", "FACE_IDLE_SIZE", "Размер в покое", "Маленький шар в углу, пока Мартышка ждёт имя.",
     "slider", (30, 120, 5, "крошечный", "крупный")),
    ("Шар", "FACE_CORNER", "Угол экрана", "Где шар живёт в покое.", "choice",
     [("лево-верх", "слева сверху"), ("право-верх", "справа сверху"), ("лево-низ", "слева снизу"), ("право-низ", "справа снизу")]),
    ("Шар", "FACE_IDLE_AFTER", "Уходить в угол через", "Сколько секунд после разговора шар ждёт, прежде чем уехать в угол.",
     "slider", (2, 60, 1, "2 сек", "60 сек")),
    ("Шар", "FACE_MOVE_SECONDS", "Длительность полёта", "Как долго волны летят между углом и рабочим местом.",
     "slider", (0.8, 5.0, 0.1, "быстро", "медленно")),
    ("Шар", "FACE_SATURATION", "Насыщенность", "Насколько сочный цвет.", "slider", (0.8, 2.5, 0.1, "мягко", "сочно")),
    ("Шар", "FACE_FILL", "Наполнение центра", "Сколько света внутри шара.", "slider", (0.0, 0.8, 0.05, "пусто", "густо")),
    ("Шар", "FACE_DENSITY", "Плотность частиц", "Больше частиц — тоньше текстура, но больше нагрузка.",
     "slider", (0.6, 2.0, 0.1, "реже", "гуще")),
    ("Шар", "FACE_SUPERSAMPLE", "Гладкость", "Сглаживание краёв частиц.", "choice", [(1, "обычная"), (2, "гладкая")]),
    ("Шар", "FACE_FPS", "Плавность", "Кадров в секунду.", "choice", [(30, "30 кадров"), (45, "45 кадров"), (60, "60 кадров")]),

    ("Утренняя сводка", "BRIEFING_ENABLED", "Сводка включена", "Первый раз за день после запуска Мартышка "
     "здоровается и коротко рассказывает, что нового. Голосом: «Мартышка, расскажи сводку».", "switch", None),
    ("Утренняя сводка", "BRIEFING_WEATHER", "Погода", "Погода в домашнем городе на сегодня.", "switch", None),
    ("Утренняя сводка", "BRIEFING_REMINDERS", "Напоминания", "Что ты просил напомнить сегодня "
     "(и что пропустил, пока комп был выключен).", "switch", None),
    ("Утренняя сводка", "BRIEFING_BATTERY", "Заряд мышки и наушников", "Берётся из Logitech G HUB. "
     "Голосом: «Мартышка, сколько заряда у мышки?»", "switch", None),

    ("Телефон", "TELEGRAM_TOKEN", "Ключ бота", "Длинный ключ от @BotFather (как его получить — написано ниже).",
     "secret", None),

    ("Интернет", "WEB_ENABLED", "Искать в интернете", "Если Мартышка чего-то не знает — тихо найдёт в интернете "
     "(без браузера на экране).", "switch", None),
    ("Интернет", "WEATHER_CITY", "Домашний город", "Для вопросов «какая погода?» без названия города.", "text", None),

    ("Система", "KEEP_ALIVE", "Мозг в видеопамяти", "«Всегда» — отвечает мгновенно. Для игр есть «Режим игр» в меню шара.",
     "choice", [(-1, "всегда"), ("30m", "выгружать через 30 мин тишины"), ("10m", "через 10 мин"), (0, "сразу выгружать")]),
    ("Система", "IMAGE_MODEL", "Генератор картинок", "RealVisXL — реалистично, 1024×1024, умеет 18+, ~3–6 сек. "
     "SDXL-Turbo — проще и быстрее, 512×512.", "choice",
     [("auto", "RealVisXL (лучше)"), ("turbo", "SDXL-Turbo (быстрее)")]),
    ("Система", "IMAGES_ADULT", "Картинки 18+", "Разрешить «нарисуй …» со взрослым содержанием (выдуманные взрослые). "
     "Всегда запрещено: что-то сексуальное с детьми/подростками и «раздевание» реальных людей на фото.", "switch", None),
]
LIVE = {"FACE_COLOR", "FACE_SIZE"}   # применяются сразу, без перезапуска


def load_saved() -> dict:
    try:
        return json.loads(config.SETTINGS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


KEEP_ON_RESET = {"TELEGRAM_TOKEN", "TELEGRAM_OWNER_ID"}
DEFAULTS_FILE = config.DATA_DIR / "settings_standard.json"   # «стандартные» = как было настроено в момент снимка


def snapshot_standard(force: bool = False) -> None:
    """Запомнить текущие настройки как стандартные (если снимка ещё нет или force)."""
    if DEFAULTS_FILE.exists() and not force:
        return
    saved = load_saved()
    values = {key: saved.get(key, getattr(config, key)) for _, key, *_ in FIELDS if key not in KEEP_ON_RESET}
    values = {k: (str(v) if isinstance(v, Path) else v) for k, v in values.items()}
    DEFAULTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    DEFAULTS_FILE.write_text(json.dumps(values, ensure_ascii=False, indent=2), encoding="utf-8")


def restore_standard() -> bool:
    try:
        values = json.loads(DEFAULTS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return False
    current = load_saved()
    for k in KEEP_ON_RESET:   # привязку телефона сброс не трогает
        if k in current:
            values[k] = current[k]
    save(values)
    return True


def save(values: dict) -> None:
    config.SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    clean = {k: (str(v) if isinstance(v, Path) else v) for k, v in values.items()}
    config.SETTINGS_FILE.write_text(json.dumps(clean, ensure_ascii=False, indent=2), encoding="utf-8")


def _rgba(hexv: str, a: float) -> str:
    h = hexv.lstrip("#")
    return f"rgba({int(h[0:2], 16)}, {int(h[2:4], 16)}, {int(h[4:6], 16)}, {a})"


def _style(accent: str) -> str:
    a20, a27, a35, a80 = (_rgba(accent, x) for x in (0.2, 0.27, 0.35, 0.8))
    return f"""
    QDialog {{ background: #0f131a; }}
    QWidget {{ color: #e6edf3; font-family: 'Segoe UI'; font-size: 10pt; }}
    QListWidget#nav {{ background: #0b0e13; border: none; padding: 10px 6px; outline: 0; }}
    QListWidget#nav::item {{ padding: 11px 12px; margin: 2px 4px; border-radius: 9px; color: #b9c4d0; }}
    QListWidget#nav::item:selected {{ background: {a20}; color: #ffffff; }}
    QListWidget#nav::item:hover:!selected {{ background: #ffffff10; }}
    QLabel#title {{ font-size: 17pt; font-weight: 600; color: #ffffff; }}
    QLabel#subtitle {{ color: #8b98a8; font-size: 10pt; }}
    QFrame#card {{ background: #171c25; border: 1px solid #222a35; border-radius: 12px; }}
    QLabel#name {{ font-weight: 600; font-size: 10.5pt; color: #f0f4f8; }}
    QLabel#desc {{ color: #8b98a8; font-size: 9pt; }}
    QLabel#val {{ color: {accent}; font-weight: 600; min-width: 46px; }}
    QLabel#hint {{ color: #6f7c8c; font-size: 9pt; }}
    QLineEdit, QPlainTextEdit, QComboBox {{ background: #0f131a; border: 1px solid #2a3340; border-radius: 8px;
        padding: 6px 9px; selection-background-color: {accent}; }}
    QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus {{ border: 1px solid {accent}; }}
    QComboBox::drop-down {{ border: none; width: 22px; }}
    QComboBox QAbstractItemView {{ background: #171c25; border: 1px solid #2a3340; selection-background-color: {a35}; }}
    QSlider::groove:horizontal {{ height: 6px; background: #2a3340; border-radius: 3px; }}
    QSlider::sub-page:horizontal {{ background: {accent}; border-radius: 3px; }}
    QSlider::handle:horizontal {{ background: #ffffff; width: 16px; height: 16px; margin: -6px 0; border-radius: 8px; }}
    QCheckBox::indicator {{ width: 40px; height: 22px; border-radius: 11px; background: #2a3340; }}
    QCheckBox::indicator:checked {{ background: {accent}; }}
    QPushButton {{ background: #222a35; border: none; border-radius: 9px; padding: 8px 16px; }}
    QPushButton:hover {{ background: #2c3644; }}
    QPushButton#primary {{ background: {accent}; color: #ffffff; font-weight: 600; }}
    QPushButton#primary:hover {{ background: {a80}; }}
    QListWidget#facts {{ background: #0f131a; border: 1px solid #2a3340; border-radius: 10px; padding: 4px; }}
    QListWidget#facts::item {{ padding: 7px; border-radius: 6px; }}
    QListWidget#facts::item:selected {{ background: {a27}; }}
    QScrollArea, QScrollArea > QWidget > QWidget#page {{ border: none; background: transparent; }}
    QScrollBar:vertical {{ background: transparent; width: 8px; }}
    QScrollBar::handle:vertical {{ background: #2a3340; border-radius: 4px; min-height: 30px; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
    """


SAMPLE_TEXT = ("Здорово, братан! Ну что, опять засиделся за компом до ночи? "
               "Ладно, рассказывай, чем помочь, я сегодня в хорошем настроении.")


def _voice_recorder(dlg, card, widgets, mic):
    """Карточка «Записать образец голоса»: 12 секунд с микрофона → WAV + текст в настройки."""
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget
    box = QWidget(); v = QVBoxLayout(box); v.setContentsMargins(0, 0, 0, 0)
    say = QLabel(f"Прочитай вслух спокойно, с выражением:\n«{SAMPLE_TEXT}»"); say.setWordWrap(True)
    say.setObjectName("name")
    row = QHBoxLayout(); rec = QPushButton("Записать (12 сек)"); rec.setObjectName("primary")
    play = QPushButton("Прослушать"); play.setEnabled(False)
    info = QLabel(""); info.setObjectName("hint")
    row.addWidget(rec); row.addWidget(play); row.addWidget(info, 1)
    v.addWidget(say); v.addLayout(row)
    state = {"file": None, "left": 0}
    SR, SEC = 24000, 12

    def tick():
        state["left"] -= 1
        if state["left"] > 0:
            info.setText(f"Говори… осталось {state['left']} сек")
            return
        timer.stop()
        import numpy as np
        import sounddevice as sd
        import soundfile as sf
        sd.wait()
        a = state["buf"][:, 0]
        loud = np.where(np.abs(a) > max(0.02, np.abs(a).max() * 0.08))[0]
        if len(loud) < SR:     # меньше секунды голоса
            info.setText("Почти ничего не слышно — проверь микрофон и попробуй ещё раз.")
            rec.setEnabled(True)
            return
        a = a[max(0, loud[0] - SR // 5): min(len(a), loud[-1] + SR // 3)]
        a = (a / max(1e-6, np.abs(a).max()) * 0.9).astype(np.float32)
        from datetime import datetime
        out = config.PROJECT_DIR / "models" / "voice" / "reference" / f"образец_{datetime.now():%Y%m%d_%H%M}.wav"
        out.parent.mkdir(parents=True, exist_ok=True)
        sf.write(str(out), a, SR)
        out.with_suffix(".txt").write_text(SAMPLE_TEXT, encoding="utf-8")   # текст известен — Whisper не нужен
        state["file"] = out
        if "VOICE_REFERENCE_WAV" in widgets:
            widgets["VOICE_REFERENCE_WAV"].setText(str(out))
        if "VOICE_REFERENCE_TEXT" in widgets:
            widgets["VOICE_REFERENCE_TEXT"].setPlainText(SAMPLE_TEXT)
        info.setText(f"Готово ({len(a) / SR:.1f} сек). Послушай и нажми «Сохранить и перезапустить».")
        rec.setEnabled(True); play.setEnabled(True)

    timer = QTimer(dlg); timer.setInterval(1000); timer.timeout.connect(tick)

    def start():
        import sounddevice as sd
        try:
            state["buf"] = sd.rec(SR * SEC, samplerate=SR, channels=1, dtype="float32", device=mic)
        except Exception as e:
            info.setText(f"Микрофон не открылся: {e}")
            return
        state["left"] = SEC
        rec.setEnabled(False); play.setEnabled(False)
        info.setText(f"Говори… осталось {SEC} сек")
        timer.start()

    def listen():
        if state["file"]:
            import sounddevice as sd
            import soundfile as sf
            a, sr = sf.read(str(state["file"]), dtype="float32")
            sd.play(a, sr)
    rec.clicked.connect(start); play.clicked.connect(listen)
    card("Голос", "Записать новый образец голоса",
         "Мартышка будет говорить тембром того, кто прочитает текст. Чужой голос — только если человек "
         "сам согласен. Записывай в тишине, микрофон поближе.", box, wide=True)


VOICE_NAMES = {   # имя файла образца → как показать в списке
    "джарвис_1_дворецкий": "Джарвис-стиль 1 — дворецкий (спокойный, низкий, с иронией)",
    "джарвис_2_бархат": "Джарвис-стиль 2 — бархат (мягкий баритон)",
    "джарвис_3_чёткий": "Джарвис-стиль 3 — чёткий (холодный, «компьютерный»)",
    "aidar": "Айдар (синтетический, прежний голос)",
    "eugene": "Евгений (синтетический)",
}


def _voice_label(path) -> str:
    p = Path(path)
    if p.stem in VOICE_NAMES:
        return VOICE_NAMES[p.stem]
    if p.stem.startswith("образец_"):
        return f"Мой образец от {p.stem[8:].replace('_', ' ')}"
    return p.stem


def _voice_picker(dlg, val, widgets, getters):
    """Список голосов (все WAV в models/voice/reference) + «Прослушать» + «Свой файл…»."""
    from PySide6.QtWidgets import QComboBox, QFileDialog, QHBoxLayout, QPushButton, QWidget
    ref_dir = config.PROJECT_DIR / "models" / "voice" / "reference"
    box = QWidget(); h = QHBoxLayout(box); h.setContentsMargins(0, 0, 0, 0)
    combo = QComboBox(); combo.setMinimumWidth(240)
    files = sorted(ref_dir.glob("*.wav"), key=lambda p: (not p.stem.startswith("джарвис"), p.stem))
    for f in files:
        combo.addItem(_voice_label(f), str(f))

    def select(path):
        path = str(path)
        i = combo.findData(path)
        if i < 0:
            combo.addItem(_voice_label(path), path)
            i = combo.count() - 1
        combo.setCurrentIndex(i)
    select(val)

    def on_change(_i=None):   # для ESpeech подставляем известный текст образца
        side = Path(combo.currentData() or "").with_suffix(".txt")
        if side.exists() and "VOICE_REFERENCE_TEXT" in widgets:
            widgets["VOICE_REFERENCE_TEXT"].setPlainText(side.read_text(encoding="utf-8").strip())
    combo.currentIndexChanged.connect(on_change)

    def listen():
        import numpy as np
        import sounddevice as sd
        import soundfile as sf
        cur = Path(combo.currentData() or "")
        demo = ref_dir / "демо" / cur.name          # как Мартышка говорит этим голосом (если есть пример)
        a, sr = sf.read(str(demo if demo.exists() else cur), dtype="float32")
        if a.ndim > 1:
            a = a.mean(1)
        amount = float(getters["VOICE_FX"]()) if "VOICE_FX" in getters else 0.0
        if amount > 0:
            from .voice import VoiceFX
            a = VoiceFX(sr, amount).process(a)
        sd.stop(); sd.play(np.asarray(a, np.float32) * 0.9, sr)

    def own_file():
        p = QFileDialog.getOpenFileName(dlg, "Образец голоса", str(ref_dir), "Звук (*.wav)")[0]
        if p:
            select(p)
    play = QPushButton("Прослушать"); play.clicked.connect(listen)
    pick = QPushButton("Свой файл…"); pick.clicked.connect(own_file)
    h.addWidget(combo, 1); h.addWidget(play); h.addWidget(pick)

    class _Adapter:   # чтобы запись нового образца могла сразу выбрать его в списке
        def setText(self, path):
            select(path)
    return box, (lambda: combo.currentData()), _Adapter()


def _commands_editor(v):
    """Редактор своих команд. Возвращает функцию, которая собирает их для сохранения."""
    from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QPushButton, QVBoxLayout
    from . import commands
    help_ = QLabel("Скажи «Мартышка, <фраза>» — и она по очереди сделает все шаги. Шаги пиши обычными словами, "
                   "каждый с новой строки — как если бы просил её голосом.")
    help_.setObjectName("desc"); help_.setWordWrap(True); v.addWidget(help_)
    holder = QVBoxLayout(); holder.setSpacing(10); v.addLayout(holder)
    rows = []

    def add(c=None):
        c = c or {"phrase": "", "steps": [], "say": ""}
        f = QFrame(); f.setObjectName("card"); lay = QVBoxLayout(f); lay.setContentsMargins(16, 12, 16, 12)
        top = QHBoxLayout()
        phrase = QLineEdit(c.get("phrase", "")); phrase.setPlaceholderText("фраза, например: рабочий режим")
        rm = QPushButton("Удалить"); top.addWidget(QLabel("Фраза:")); top.addWidget(phrase, 1); top.addWidget(rm)
        steps = QPlainTextEdit("\n".join(c.get("steps", []))); steps.setFixedHeight(90)
        steps.setPlaceholderText("открой стим\nоткрой дискорд\nвключи музыку Rammstein\nгромкость 30")
        say = QLineEdit(c.get("say", "")); say.setPlaceholderText("что сказать в конце (можно пусто — скажет «Сделано»)")
        lay.addLayout(top); lay.addWidget(QLabel("Что сделать (каждый шаг с новой строки):")); lay.addWidget(steps)
        lay.addWidget(say)
        holder.addWidget(f)
        row = (f, phrase, steps, say)
        rows.append(row)

        def remove():
            rows.remove(row); f.deleteLater()
        rm.clicked.connect(remove)

    for c in commands.load():
        add(c)
    if not rows:
        add()   # пустая карточка: серые подсказки показывают пример
    plus = QPushButton("+  Добавить команду"); plus.clicked.connect(lambda: add()); v.addWidget(plus)

    def collect():
        out = []
        for _f, phrase, steps, say in rows:
            st = [s.strip() for s in steps.toPlainText().splitlines() if s.strip()]
            if phrase.text().strip() and st:
                out.append({"phrase": phrase.text().strip(), "steps": st, "say": say.text().strip()})
        return out
    return collect


def _telegram_help(v, card, owner):
    from PySide6.QtWidgets import QLabel, QPushButton
    from . import telegram_bot
    how = QLabel(
        "<b>Как подключить (один раз, 2 минуты):</b><br>"
        "1. В Telegram найди <b>@BotFather</b> → нажми Start → напиши <b>/newbot</b>.<br>"
        "2. Придумай имя (например «Моя Мартышка») и адрес, который кончается на <b>bot</b>.<br>"
        "3. BotFather пришлёт длинный ключ вида <i>123456:ABC-…</i> — вставь его в поле выше.<br>"
        "4. Нажми «Сохранить и перезапустить».<br>"
        f"5. Открой своего бота в Telegram, нажми Start и отправь ему код: <b>{telegram_bot.pair_code()}</b><br><br>"
        "После этого бот слушается <b>только тебя</b>. Пиши ему как Мартышке: «сколько заряда у мышки», "
        "«напомни в 18:00 купить хлеб», «выключи комп через час». Работает, пока комп включён.")
    how.setObjectName("desc"); how.setWordWrap(True); v.addWidget(how)
    status = QLabel(); status.setObjectName("name")
    unpair = QPushButton("Отвязать телефон")

    def refresh():
        status.setText(f"Привязан Telegram (id {owner['id']})" if owner["id"] else "Телефон ещё не привязан")
        unpair.setEnabled(bool(owner["id"]))

    def do_unpair():
        owner["id"] = 0; refresh()
    unpair.clicked.connect(do_unpair); refresh()
    card("Телефон", "Состояние", "Отвязать — если сменил телефон или аккаунт. Потом снова отправь код боту.", unpair)
    v.addWidget(status)


def open_settings(parent=None, on_live_change=None, on_restart=None) -> None:
    """Показать окно настроек. on_live_change(ключ, значение) — для мгновенных (цвет/размер шара)."""
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QColor
    from PySide6.QtWidgets import (QCheckBox, QColorDialog, QComboBox, QDialog, QFileDialog, QFrame, QHBoxLayout,
                                   QLabel, QLineEdit, QListWidget, QMessageBox, QPlainTextEdit, QPushButton,
                                   QScrollArea, QSlider, QStackedWidget, QVBoxLayout, QWidget)
    from . import system
    from .memory import Memory, _save

    snapshot_standard()   # на всякий случай: если снимка ещё нет — делаем из текущих
    saved = load_saved()
    current = lambda k: saved.get(k, getattr(config, k))
    accent = config.FACE_COLORS.get(str(current("FACE_COLOR")), str(current("FACE_COLOR")))
    if not str(accent).startswith("#"):
        accent = "#2a9cff"

    dlg = QDialog(parent)
    dlg.setWindowTitle("Мартышка — настройки")
    dlg.setWindowFlags(dlg.windowFlags() | Qt.WindowStaysOnTopHint)
    dlg.resize(900, 640)
    dlg.setStyleSheet(_style(accent))
    outer = QHBoxLayout(dlg); outer.setContentsMargins(0, 0, 0, 0); outer.setSpacing(0)

    nav = QListWidget(); nav.setObjectName("nav"); nav.setFixedWidth(210)
    stack = QStackedWidget()
    right = QVBoxLayout(); right.setContentsMargins(26, 20, 26, 16)
    right.addWidget(stack, 1)
    outer.addWidget(nav); outer.addLayout(right, 1)

    getters = {}
    widgets = {}   # поля, которые меняют другие кнопки (запись образца голоса)
    pages = {}

    def page(section):
        if section in pages:
            return pages[section]
        icon, name, sub = next(s for s in SECTIONS if s[1] == section)
        nav.addItem(f"{icon}   {name}")
        wrap = QWidget(); wrap.setObjectName("page"); v = QVBoxLayout(wrap); v.setContentsMargins(0, 0, 8, 0); v.setSpacing(10)
        t = QLabel(name); t.setObjectName("title"); s = QLabel(sub); s.setObjectName("subtitle")
        v.addWidget(t); v.addWidget(s); v.addSpacing(6)
        scroll = QScrollArea(); scroll.setWidgetResizable(True); scroll.setWidget(wrap)
        scroll.viewport().setAutoFillBackground(False)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        stack.addWidget(scroll)
        pages[section] = v
        return v

    def card(section, name, desc, control, wide=False):
        """Карточка: название и пояснение слева, управление справа (или под ними, если wide)."""
        f = QFrame(); f.setObjectName("card")
        lay = QVBoxLayout(f) if wide else QHBoxLayout(f)
        lay.setContentsMargins(16, 12, 16, 12)
        texts = QVBoxLayout(); texts.setSpacing(2)
        n = QLabel(name); n.setObjectName("name"); d = QLabel(desc); d.setObjectName("desc"); d.setWordWrap(True)
        texts.addWidget(n); texts.addWidget(d)
        lay.addLayout(texts, 1)
        if wide:
            lay.addSpacing(4)
        lay.addWidget(control, 0 if not wide else 0)
        page(section).addWidget(f)

    for section in [s[1] for s in SECTIONS]:
        page(section)

    for section, key, name, desc, kind, opt in FIELDS:
        val = current(key)
        wide = False
        if kind == "switch":
            w = QCheckBox(); w.setChecked(bool(val)); getters[key] = w.isChecked
        elif kind == "slider":
            lo, hi, step, left, right_lbl = opt
            box = QWidget(); h = QHBoxLayout(box); h.setContentsMargins(0, 0, 0, 0)
            sl = QSlider(Qt.Horizontal); sl.setFixedWidth(220)
            n_steps = int(round((hi - lo) / step))
            sl.setRange(0, n_steps); sl.setValue(int(round((float(val) - lo) / step)))
            is_int = all(float(x).is_integer() for x in (lo, hi, step))
            lab = QLabel(); lab.setObjectName("val")
            conv = (lambda i, lo=lo, step=step, is_int=is_int: int(round(lo + i * step)) if is_int
                    else round(lo + i * step, 2))
            upd = lambda i, lab=lab, conv=conv: lab.setText(str(conv(i)))
            sl.valueChanged.connect(upd); upd(sl.value())
            l1, l2 = QLabel(left), QLabel(right_lbl); l1.setObjectName("hint"); l2.setObjectName("hint")
            h.addWidget(l1); h.addWidget(sl); h.addWidget(l2); h.addWidget(lab)
            w = box; getters[key] = (lambda sl=sl, conv=conv: conv(sl.value()))
            if key == "FACE_SIZE" and on_live_change:
                sl.valueChanged.connect(lambda i, conv=conv: on_live_change("FACE_SIZE", conv(i)))
        elif kind == "choice":
            w = QComboBox(); w.setMinimumWidth(230)
            for value, label in opt:
                w.addItem(label, value)
            w.setCurrentIndex(max(0, w.findData(val))); getters[key] = w.currentData
        elif kind == "text":
            w = QLineEdit(str(val)); w.setMinimumWidth(260); getters[key] = w.text
        elif kind == "secret":
            w = QLineEdit(str(val)); w.setMinimumWidth(300); w.setEchoMode(QLineEdit.Password)
            w.setPlaceholderText("вставь сюда ключ бота"); getters[key] = lambda w=w: w.text().strip()
        elif kind == "longtext":
            w = QPlainTextEdit(str(val)); w.setFixedHeight(80); getters[key] = w.toPlainText; wide = True
            widgets[key] = w
        elif kind == "file":
            box = QWidget(); h = QHBoxLayout(box); h.setContentsMargins(0, 0, 0, 0)
            line = QLineEdit(str(val)); btn = QPushButton("Выбрать…"); widgets[key] = line
            btn.clicked.connect(lambda _=False, l=line, o=opt: l.setText(
                QFileDialog.getOpenFileName(dlg, "Образец голоса", str(Path(l.text()).parent), o)[0] or l.text()))
            h.addWidget(line, 1); h.addWidget(btn); w = box; getters[key] = line.text; wide = True
        elif kind == "voice":
            w, getters[key], widgets[key] = _voice_picker(dlg, val, widgets, getters)
            wide = True
        elif kind == "model":
            w = QComboBox(); w.setMinimumWidth(230)
            try:
                import ollama
                names = [m.model for m in ollama.Client().list().models]
            except Exception:
                names = []
            for nm in sorted(set(names + [str(val)])):
                w.addItem(nm, nm)
            w.setCurrentIndex(max(0, w.findData(val))); getters[key] = w.currentData
        elif kind == "mic":
            w = QComboBox(); w.setMinimumWidth(260); w.addItem("как в Windows (по умолчанию)", None)
            try:
                import sounddevice as sd
                api = sd.default.hostapi
                for i, d in enumerate(sd.query_devices()):
                    if d["max_input_channels"] > 0 and d["hostapi"] == api:
                        w.addItem(d["name"], i)
            except Exception:
                pass
            w.setCurrentIndex(max(0, w.findData(val))); getters[key] = w.currentData
        elif kind == "color":
            box = QWidget(); h = QHBoxLayout(box); h.setContentsMargins(0, 0, 0, 0); h.setSpacing(6)
            chosen = {"v": val}
            buttons = []

            def pick(value, hexv):
                chosen["v"] = value
                for b2, hx2 in buttons:
                    b2.setStyleSheet(f"background:{hx2}; border-radius:13px; border:"
                                     f"{'3px solid #ffffff' if hx2 == hexv else '2px solid #0f131a'};")
                if on_live_change:
                    on_live_change("FACE_COLOR", value)
            for cname, hx in config.FACE_COLORS.items():
                b = QPushButton(); b.setFixedSize(26, 26); b.setToolTip(cname)
                b.clicked.connect(lambda _=False, c=cname, x=hx: pick(c, x))
                buttons.append((b, hx)); h.addWidget(b)
            own = QPushButton("свой…")

            def own_color():
                c = QColorDialog.getColor(QColor(accent), dlg, "Цвет шара")
                if c.isValid():
                    pick(c.name(), c.name())
            own.clicked.connect(own_color); h.addWidget(own)
            cur_hex = config.FACE_COLORS.get(str(val), str(val))
            for b, hx in buttons:
                b.setStyleSheet(f"background:{hx}; border-radius:13px; border:"
                                f"{'3px solid #ffffff' if hx == cur_hex else '2px solid #0f131a'};")
            w = box; getters[key] = lambda chosen=chosen: chosen["v"]
        card(section, name, desc, w, wide)

    # --- Голос: записать свой образец ---
    _voice_recorder(dlg, card, widgets, current("MIC_DEVICE"))

    # --- Мои команды ---
    cmd_cards = _commands_editor(page("Мои команды"))

    # --- Телефон (Telegram) ---
    owner = {"id": int(current("TELEGRAM_OWNER_ID") or 0)}
    _telegram_help(page("Телефон"), card, owner)
    getters["TELEGRAM_OWNER_ID"] = lambda: owner["id"]

    # --- Память ---
    mem = Memory()
    mv = page("Память")
    lst = QListWidget(); lst.setObjectName("facts"); lst.addItems(mem.fact_texts()); lst.setMinimumHeight(240)
    mv.addWidget(lst)
    row = QHBoxLayout(); new_fact = QLineEdit(); new_fact.setPlaceholderText("новый факт, например: болею за Баварию")
    add_b, del_b = QPushButton("Добавить"), QPushButton("Удалить выбранное")
    row.addWidget(new_fact, 1); row.addWidget(add_b); row.addWidget(del_b); mv.addLayout(row)
    clr = QPushButton("Очистить историю разговора (факты останутся)"); mv.addWidget(clr)

    def add_fact():
        if new_fact.text().strip() and mem.remember(new_fact.text()):
            lst.addItem(new_fact.text().strip()); new_fact.clear()

    def del_fact():
        for it in lst.selectedItems():
            mem.facts = [x for x in mem.facts if x["text"] != it.text()]
            lst.takeItem(lst.row(it))
        _save(mem.facts_path, mem.facts)

    def clear_history():
        if QMessageBox.question(dlg, "Мартышка", "Стереть историю разговора? Факты о тебе останутся.") == QMessageBox.Yes:
            mem.history = []; _save(mem.history_path, [])
    add_b.clicked.connect(add_fact); del_b.clicked.connect(del_fact); clr.clicked.connect(clear_history)

    # --- Система (дополнительно) ---
    auto = QCheckBox(); auto.setChecked(system.autostart_enabled())
    card("Система", "Запускать вместе с Windows", "Мартышка сама появится после включения компьютера.", auto)
    btns = QWidget(); bh = QHBoxLayout(btns); bh.setContentsMargins(0, 0, 0, 0)
    for text, fn in (("Журнал разговора", lambda: os.startfile(system.LOG_FILE) if system.LOG_FILE.exists() else None),
                     ("Папка Мартышки", lambda: os.startfile(config.PROJECT_DIR)),
                     ("Ярлык на рабочий стол", lambda: system.make_desktop_shortcut())):
        b = QPushButton(text); b.clicked.connect(fn); bh.addWidget(b)
    card("Система", "Полезное", "Посмотреть, что Мартышка слышала и отвечала, или открыть её файлы.", btns, wide=True)
    for v in pages.values():
        v.addStretch(1)

    # --- Нижняя панель ---
    bottom = QHBoxLayout()
    hint = QLabel("Цвет и размер шара — сразу,\nостальное — после перезапуска."); hint.setObjectName("hint")
    save_b, restart_b, cancel_b = QPushButton("Сохранить"), QPushButton("Сохранить и перезапустить"), QPushButton("Отмена")
    restart_b.setObjectName("primary")
    reset_b = QPushButton("Вернуть стандартные")
    reset_b.setToolTip("Если что-то сломалось — вернёт все настройки как было, когда всё работало.")

    def reset_standard():
        if QMessageBox.question(dlg, "Мартышка", "Вернуть все настройки к стандартным?\n"
                                "Твои изменения сбросятся, Мартышка перезапустится.\n"
                                "(Память и факты о тебе не трогаются.)") != QMessageBox.Yes:
            return
        if not restore_standard():
            QMessageBox.warning(dlg, "Мартышка", "Не нашёл файл стандартных настроек.")
            return
        dlg.accept()
        if on_restart:
            on_restart()
    reset_b.clicked.connect(reset_standard)
    restart_b.setToolTip(hint.text().replace("\n", " "))
    bottom.addWidget(reset_b); bottom.addStretch(); bottom.addWidget(cancel_b); bottom.addWidget(save_b); bottom.addWidget(restart_b)
    right.addLayout(bottom)

    nav.currentRowChanged.connect(stack.setCurrentIndex)
    nav.setCurrentRow(0)

    def collect_and_save():
        values = dict(saved)
        for key, get in getters.items():
            values[key] = get()
        save(values)
        from . import commands
        commands.save(cmd_cards())
        if auto.isChecked() != system.autostart_enabled():
            system.set_autostart(auto.isChecked())

    save_b.clicked.connect(lambda: (collect_and_save(), dlg.accept()))
    restart_b.clicked.connect(lambda: (collect_and_save(), dlg.accept(), on_restart and on_restart()))
    cancel_b.clicked.connect(dlg.reject)
    dlg.exec()
