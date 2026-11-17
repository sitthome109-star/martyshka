"""
МАРТЫШКА — ГОЛОСОВОЙ РЕЖИМ (главная программа)

Скажи «Мартышка» — она пикнет и выслушает. Можно сразу одной фразой:
  «Мартышка, сколько времени?»
После ответа ~6 секунд можно продолжать без имени.
Пока она говорит, скажи «Мартышка» — заткнётся и выслушает.

«Мартышка, режим игр» — освобождает видеокарту (слышит только имя и «конец игр»).
«Мартышка, конец игр» — всё возвращает.

Запуск без чёрного окна — ярлык «Мартышка» (весь разговор пишется в data/журнал.txt).
Выключить — правый клик по шару → «Выключить Мартышку».
"""
import os
import queue
import re
import sys
import threading
import time
from pathlib import Path

from martyshka import commands, config, errlog, style, system
from martyshka.brain import Brain, reply_language


NO_RECOVER = {"узнать", "погода", "заряд", "сводка", "система", "заметки", "буфер", "питание", "отмена выключения",
              "экран", "нажать", "клавиши", "команда", "задача", "удалить", "окна", "игра", "доработать игру", "exe"}


def run_actions(brain, hands, actions, confirm_fn, request: str):
    """Выполнить дела; если что-то не вышло — один раз попросить мозг найти другой путь и попробовать его."""
    summary, need_report, lines = hands.execute_all(actions, confirm_fn)
    for n, a, t in hands.last_failed:                     # дневник ошибок: что не вышло
        if "передумал" not in t:
            errlog.write("дело не вышло", просьба=request, дело=f"{n} | {' | '.join(a)}", результат=t[:300])
    failed = [f for f in hands.last_failed if f[0] not in NO_RECOVER]
    if failed:
        what = "; ".join(f"{n} {' | '.join(a)} → {t}" for n, a, t in failed)
        try:
            plan_b = brain.recover(what, request)
        except Exception:
            plan_b = []
        plan_b = [p for p in plan_b if (p[0], p[1]) not in [(n, a) for n, a, _ in failed]]
        if plan_b:
            lines.append("(не вышло — пробую по-другому)")
            s2, n2, l2 = hands.execute_all(plan_b, confirm_fn)
            summary += "; ОБХОДНОЙ ПУТЬ: " + s2
            lines += l2
            need_report = True
    return summary, need_report, lines


def iter_paragraphs(text: str):
    """Текст для чтения вслух — по абзацам (так голос начинает читать сразу, не дожидаясь конца)."""
    for para in re.split(r"\n\s*\n|\n", text):
        para = para.strip()
        if para:
            yield para + ("" if para[-1] in ".!?…:" else ".") + " "

GREY, GREEN, CYAN, YELLOW, RED, RESET = "\033[90m", "\033[92m", "\033[96m", "\033[93m", "\033[91m", "\033[0m"

FACE = None     # шар на экране (если включён в настройках)
HIDDEN = False  # запущены без окна (через ярлык) — тогда ошибки показываем окошком

# «Не знаю» в ответе мозга — повод поискать в интернете
DONT_KNOW_RE = re.compile(r"(не знаю|не в курсе|без понятия|не уверен|нет (у меня )?(информации|данных)|"
                          r"не могу (знать|сказать|проверить)|не имею доступа|не слежу)", re.IGNORECASE)

# Диктовка: «пиши за мной», «начни диктовку» … «хватит писать», «стоп диктовка»
DICTATE_ON_RE = re.compile(r"(диктовк|под\s+диктовку|пиши\s+за\s+мной|печатай\s+за\s+мной|записывай\s+за\s+мной|"
                           r"пиши\s+что\s+я\s+(говорю|скажу)|diktier|dictation|dictate)", re.IGNORECASE)
DICTATE_OFF_RE = re.compile(r"((хватит|стоп|конец|закончи\w*|выключи\w*|останови\w*|заверши\w*|отключи\w*|прекрати\w*)"
                            r"\W+(\w+\W+)?(писать|печатать|диктовк\w*|записывать)|"
                            r"(конец|стоп)\s+диктовк\w*|^\W*стоп\W*$|не\s+пиши\s+больше)", re.IGNORECASE)
NEWLINE_RE = re.compile(r"(новая строка|с новой строки|новый абзац|абзац|enter|энтер|neue zeile)")
UNDO_RE = re.compile(r"(удали последнее|удали это|отмени|отмена|стереть последнее|сотри последнее|сотри это)")
DICTATION_IDLE = 120   # сколько секунд тишины — и диктовка сама заканчивается

GAME_ON_RE = re.compile(r"(режим\w*\s+игр|игров\w*\s+режим)", re.IGNORECASE)
GAME_OFF_RE = re.compile(r"((конец|хватит|выход\w*|выключ\w*|отключ\w*|законч\w*|стоп)\W*(\w+\W+)?(режим\w*\W+)?игр"
                         r"|наигрался|вернись|просыпайся)", re.IGNORECASE)


def status(text: str) -> None:
    print(f"{GREY}{text}{RESET}", flush=True)
    # Шар показывает то же, что и строчка статуса
    if FACE is not None:
        if "Жду имя" in text or "показалось" in text or "отбой" in text:
            FACE.set_state("sleep")
        elif "Слушаю" in text or "Жду «да»" in text:
            FACE.set_state("listen")
        elif "Загружаю" in text:
            FACE.set_state("think")


def fail(message: str) -> None:
    """Не можем работать — сказать об этом понятно и закрыться."""
    print(f"{RED}{message}{RESET}")
    if HIDDEN:
        system.message_box(message)
    else:
        input("Нажми Enter, чтобы закрыть.")
    os._exit(1)


def main() -> None:
    if sys.platform == "win32" and not HIDDEN:
        os.system("")  # цвета в консоли
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # русские буквы и значки в любой консоли
        except Exception:
            pass
    import sounddevice as sd

    # Мозг живёт в Ollama — ждём, пока она проснётся (после включения компа это бывает не сразу)
    status(f"Загружаю мозг ({config.MODEL})...")
    if not system.ensure_ollama():
        fail("Мартышка не может достучаться до Ollama (мозг).\nПроверь, что Ollama установлена и запущена.")
    # Порядок важен: мозг → голос → уши (иначе толкаются в видеопамяти и первый ответ тормозит)
    brain = Brain()
    try:
        brain.warm_up()
    except Exception as e:
        fail(f"Мозг не загрузился: {e}")

    status(f"Загружаю голос ({config.VOICE_ENGINE})...")
    from martyshka.voice import Voice
    voice = Voice()
    if not voice.wait_ready():
        fail(f"Голос не завёлся: {voice.error}")
    if FACE is not None:
        FACE.voice = voice  # шар пульсирует в такт голосу

    status("Загружаю уши (Whisper + Vosk)...")
    from martyshka.ears import (Ears, NAME_IN_TEXT_RE, Transcriber, looks_like_echo, make_beep,
                                speech_seconds, strip_wake_word)
    transcriber = Transcriber()
    beep = make_beep()
    interrupted = threading.Event()

    def on_wake():
        # Вызывается из потока ушей сразу, как прозвучало имя
        interrupted.set()
        voice.stop()
        if config.WAKE_BEEP:
            sd.play(beep, 24000)

    ears = Ears(on_wake=on_wake)
    try:
        ears.start()
    except Exception as e:
        fail(f"Не могу открыть микрофон: {e}")

    from martyshka import hands
    hands.load_apps()  # список программ из меню «Пуск»
    hands.set_llm(brain.complete)  # мозг помогает рукам: составить таблицу, написать текст
    from martyshka import media    # фото/видео/рисование: на время рисования мозг уходит с видеокарты
    media.set_hooks(gpu_before=brain.unload, gpu_after=brain.warm_up, llm=brain.complete)
    from martyshka import games      # игры: мозг пишет код с увеличенной «рабочей памятью»
    games.set_llm(brain.complete_raw)

    def speak(pieces, lang: str = "ru") -> str:
        """Озвучить ответ мозга и напечатать его. Возвращает сказанный текст."""
        said = ""
        try:
            for piece in voice.speak_stream(pieces, lang=lang):
                if interrupted.is_set():
                    break
                if not said:
                    print(f"{GREEN}{config.NAME}:{RESET} ", end="", flush=True)
                said += piece
                print(piece, end="", flush=True)
        except Exception as e:
            print(f"\n{RED}[ошибка: {e}]{RESET}", end="")
        finally:
            pieces.close()  # если перебили — мозг прекращает писать и сохраняет, что успел
        if said:
            print()
        if interrupted.is_set():
            voice.stop()
            status("   (перебил)")
        return said

    def ask_yes_no(question: str) -> bool:
        """Опасное дело: спросить «точно?» голосом и дождаться ответа."""
        print(f"{GREEN}{config.NAME}:{RESET} {question}")
        voice.say(question.split("\n")[0])   # команду целиком не зачитываем — она в журнале
        voice.wait_done()
        ears.listen_now(6)
        status("👂 Жду «да» или «нет»...")
        while True:
            try:
                kind, data = ears.events.get(timeout=15)
            except queue.Empty:
                return False
            if kind == "wake":
                continue
            if kind != "audio":
                status("   (не ответил — отбой)")
                return False
            answer = transcriber.transcribe(data[0])
            print(f"{CYAN}Ты:{RESET} {answer}")
            return hands.is_yes(answer)

    def on_reminder(text: str) -> None:
        print(f"\n{YELLOW}⏰ Напоминание: {text}{RESET}")
        sd.play(beep, 24000)
        voice.say(style.phrase("remind", text=text))

    hands.set_reminder_callback(on_reminder)

    dictation = {"on": False, "last": 0.0}
    turn_log = errlog.Turn()             # дневник ошибок: прошлый ход — чтобы при жалобе записать, что было не так

    # ---------- Telegram (писать с телефона) ----------
    tg = None
    if config.TELEGRAM_TOKEN.strip():
        try:
            from martyshka.telegram_bot import TelegramBot
            tg = TelegramBot(ears.events, log=status)
            if not tg.start():
                tg = None
        except Exception as e:
            status(f"   (Telegram не запустился: {e})")
            tg = None

    def handle_telegram(msg: dict) -> None:
        """Сообщение с телефона: то же, что голосом, только ответ — текстом в Telegram."""
        text = msg.get("text", "")
        if msg.get("voice"):
            data = tg.download_voice(msg["voice"])
            try:
                import io
                import numpy as np
                import soundfile as sf
                from scipy.signal import resample_poly
                a, sr = sf.read(io.BytesIO(data), dtype="float32")
                if a.ndim > 1:
                    a = a.mean(1)
                g = np.gcd(16000, sr)
                text = transcriber.transcribe(resample_poly(a, 16000 // g, sr // g).astype(np.float32))
            except Exception:
                text = ""
            if not text:
                tg.send("Не разобрал голосовое — напиши текстом.")
                return
            tg.send(f"🎙 Услышал: {text}")
        text = strip_wake_word(text) or text      # «Мартышка, …» — имя не нужно
        print(f"{CYAN}📱 Ты (Telegram):{RESET} {text}")
        turn_log.complaint(text, "telegram")
        lines = []
        if game["on"]:
            if GAME_OFF_RE.search(text):
                exit_game()
                tg.send(style.phrase("game_off"))
            else:
                tg.send(style.phrase("tg_game"))
            return
        tg.typing()
        own = commands.match(text)
        if own:
            _s, _n, lines = hands.execute_all(commands.actions_for(own, brain.actions_only), tg.confirm)
            for line in lines:
                print(f"{YELLOW}  {line}{RESET}")
            tg.send(own.get("say", "").strip() or "Сделано.")
            return
        lang = reply_language(text)
        answer = "".join(brain.reply(text, lang)).strip()
        if not brain.last_actions and config.WEB_ENABLED and DONT_KNOW_RE.search(answer):
            brain.last_actions = [("узнать", [text])]
            answer = ""
        if answer:
            tg.send(answer)
        if brain.last_actions:
            tg.typing()
            hands.set_progress(tg.send)
            summary, need_report, lines = run_actions(brain, hands, brain.last_actions, tg.confirm, text)
            hands.set_progress(status)
            hands.to_read.clear()           # «прочитать вслух» с телефона не нужно
            for line in lines:
                print(f"{YELLOW}  {line}{RESET}")
            if any(a[0] in ("фото", "видео", "нарисуй") for a in brain.last_actions):   # результат — на телефон
                from martyshka import media
                f = media.last_file
                if f and f.exists() and time.time() - f.stat().st_mtime < 600:
                    if f.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp") and f.stat().st_size < 9_000_000:
                        tg.send_photo(f)
                    else:
                        tg.send_document(f)
            if any(a[0] in ("документ", "таблица", "игра", "доработать игру", "exe") for a in brain.last_actions):   # готовый файл — на телефон
                files = sorted((Path.home() / "Documents").glob("*.*"), key=lambda p: p.stat().st_mtime)
                newest = hands.last_document or (files[-1] if files else None)
                if newest and newest.exists() and time.time() - newest.stat().st_mtime < 600:
                    tg.send_document(newest)
            if any(a[0] == "скриншот" for a in brain.last_actions):   # скриншот — сразу на телефон
                shots = sorted((Path.home() / "Pictures" / "Скриншоты Мартышки").glob("*.png"),
                               key=lambda p: p.stat().st_mtime)
                if shots:
                    tg.send_photo(shots[-1])
            if need_report:
                tg.send("".join(brain.report(summary)).strip())
            elif brain.pending_say:
                tg.send("".join(brain.say_text(brain.pending_say)))
        turn_log.done(text, brain.last_actions, lines, answer, None, "telegram")
        print(f"{GREEN}{config.NAME} → Telegram{RESET}")

    # ---------- режим игр ----------
    game = {"on": False}

    def enter_game() -> None:
        voice.say(style.phrase("game_on"))
        voice.wait_done()
        status("🎮 Режим игр: освобождаю видеокарту...")
        voice.free_gpu()
        transcriber.unload()
        brain.unload()
        game["on"] = True
        if FACE is not None:
            FACE.set_state("game")
        status("🎮 Режим игр. Слышу только «Мартышка, конец игр».")

    def exit_game() -> None:
        status("Загружаю мозг, голос и уши обратно...")
        brain.warm_up()
        voice.use_gpu()
        transcriber.load()
        game["on"] = False
        voice.say(style.phrase("game_off"))
        voice.wait_done()
        status("💤 Жду имя...")

    if FACE is not None:   # «Режим игр» в меню шара
        FACE.on_game_toggle = lambda: ears.events.put(("game_toggle", None))

    last_reply = ""
    missed = hands.restore_reminders()   # напоминания, сохранённые до перезапуска
    from martyshka import briefing as _b
    _b.start_battery_watch()             # пока брат за компом — тихо запоминаем заряд мышки, наушников, клавиатуры
    print(f"\n{GREEN}{config.NAME} на связи.{RESET} Скажи «{config.NAME}» — и говори.\n")
    from martyshka import briefing
    if config.BRIEFING_ENABLED and briefing.due_today():
        status("☀ Утренняя сводка...")
        briefing.mark_done()
        try:
            last_reply = speak(brain.briefing(briefing.collect(missed)))
        except Exception as e:
            status(f"   (сводка не вышла: {e})")
            voice.say(style.phrase("hello"))
    elif missed:
        voice.say(style.phrase("hello_missed", items="; ".join(missed)))
    else:
        voice.say(style.phrase("hello"))
    voice.wait_done()
    status("💤 Жду имя...")

    while True:
        kind, data = ears.events.get()

        if kind == "telegram":
            if tg is not None:
                try:
                    handle_telegram(data)
                except Exception as e:
                    status(f"   (Telegram: ошибка {e})")
                    tg.send(f"Что-то сломалось: {e}")
            continue
        if kind == "game_toggle":
            exit_game() if game["on"] else enter_game()
            continue
        if kind == "wake":
            if not game["on"]:
                status("👂 Слушаю...")
            continue
        if kind == "timeout" and dictation["on"]:
            if time.time() - dictation["last"] < DICTATION_IDLE:
                ears.listen_now(30)          # диктовка ещё идёт — слушаем дальше без имени
                continue
            dictation["on"] = False
            status("✍ Диктовка закончилась (долго молчал).")
            voice.say(style.phrase("dict_idle"))
        if kind == "timeout":
            status("🎮 Режим игр." if game["on"] else "💤 Жду имя...")
            if game["on"] and FACE is not None:
                FACE.set_state("game")
            continue

        # kind == "audio": фраза записана
        audio, why = data

        if game["on"]:
            # Режим игр: видеокарту не трогаем, разбираем фразу лёгкой моделью на процессоре
            heard = ears.transcribe_light(audio)
            print(f"{CYAN}Ты (режим игр):{RESET} {heard}")
            if GAME_OFF_RE.search(heard):
                exit_game()
            else:
                sd.play(make_beep(), 24000)   # «я в режиме игр» — просто пикнуть
                if FACE is not None:
                    FACE.set_state("game")
            continue

        if FACE is not None:
            FACE.set_state("think")
        if why == "followup" and speech_seconds(audio) < 0.5:
            status("💤 Жду имя...")  # это был шорох, а не речь
            continue
        text = transcriber.transcribe(audio, strict=(why == "followup"))

        # ---- диктовка: всё сказанное печатается туда, где курсор ----
        if dictation["on"] and not text:
            ears.listen_now(30)
            continue
        if dictation["on"] and text:
            spoken = strip_wake_word(text) if NAME_IN_TEXT_RE.search(text) else text
            if DICTATE_OFF_RE.search(spoken) or (NAME_IN_TEXT_RE.search(text) and spoken):
                dictation["on"] = False
                status("✍ Диктовка выключена.")
                if DICTATE_OFF_RE.search(spoken):
                    voice.say(style.phrase("dict_off"))
                    voice.wait_done()
                    status("💤 Жду имя...")
                    continue
                # позвали по имени с другой просьбой — диктовку закончили, просьбу выполняем как обычно
            else:
                dictation["last"] = time.time()
                if NEWLINE_RE.fullmatch(spoken.strip(" .!?,").lower()):
                    hands._press(0x0D)                      # Enter
                    print(f"{CYAN}✍ (новая строка){RESET}")
                elif UNDO_RE.fullmatch(spoken.strip(" .!?,").lower()):
                    hands._press(0x11, 0x5A)                # Ctrl+Z
                    print(f"{CYAN}✍ (отменил последнее){RESET}")
                elif spoken.strip():
                    hands.type_text(spoken.strip() + " ")
                    print(f"{CYAN}✍ {spoken.strip()}{RESET}")
                ears.listen_now(30)
                if FACE is not None:
                    FACE.set_state("listen")
                continue

        if why == "followup" and (not text or looks_like_echo(text, last_reply)):
            if text:
                status(f"   (это было эхо: «{text}»)")
            status("💤 Жду имя...")
            continue

        if why == "wake" and not NAME_IN_TEXT_RE.search(text):
            status(f"   (показалось: «{text}»)" if text else "   (показалось)")
            status("💤 Жду имя...")
            continue

        # Имя убираем всегда — и когда позвали заново, пока она ждала продолжения
        command = strip_wake_word(text)
        if not command:
            if NAME_IN_TEXT_RE.search(text):   # сказали только «Мартышка» — слушаем, что дальше
                ears.listen_now(5)
                status("👂 Слушаю...")
            else:
                status("💤 Жду имя...")
            continue

        print(f"{CYAN}Ты:{RESET} {command}")
        turn_log.complaint(command, "голос")
        lines = []
        if GAME_ON_RE.search(command) and not GAME_OFF_RE.search(command):
            enter_game()
            continue
        if DICTATE_ON_RE.search(command) and not DICTATE_OFF_RE.search(command):
            dictation.update(on=True, last=time.time())
            voice.say(style.phrase("dict_on"))
            voice.wait_done()
            status("✍ Диктовка: говори, печатаю. «Новая строка», «удали последнее», «хватит писать».")
            ears.listen_now(30)
            continue

        interrupted.clear()

        # ---- свои команды («рабочий режим» → несколько дел подряд) ----
        own = commands.match(command)
        if own:
            status(f"⚡ Своя команда: «{own['phrase']}»")
            if FACE is not None:
                FACE.set_state("think")
            acts = commands.actions_for(own, brain.actions_only)
            _summary, need_report, lines = hands.execute_all(acts, ask_yes_no)
            for line in lines:
                print(f"{YELLOW}  {line}{RESET}")
            last_reply = own.get("say", "").strip() or "Сделано."
            print(f"{GREEN}{config.NAME}:{RESET} {last_reply}")
            voice.say(last_reply)
            voice.wait_done()
            ears.expect_followup()
            continue

        lang = transcriber.last_language   # на каком языке говорит брат — на нём и отвечаем
        if transcriber.last_candidates:   # уши сомневались — пишем в журнал, из чего выбирали
            status("   (язык: " + " | ".join(f"{l} {s}: «{t}»" for l, t, s in transcriber.last_candidates)
                   + f" → {lang}; вероятности {transcriber.last_probs})")
        elif lang != "ru":
            status(f"   (язык: {lang})")
        last_reply = speak(brain.reply(command, lang), lang)
        if interrupted.is_set():
            continue  # уши уже записывают новую фразу

        voice.wait_done()
        ch = brain.last_changes
        for f in ch.remembered:
            print(f"{YELLOW}  (запомнил: {f}){RESET}")
        for f in ch.forgotten:
            print(f"{YELLOW}  (забыл: {f}){RESET}")

        # Не знает ответа и не пошёл в интернет сам — идём за него (тихо, без браузера)
        if not brain.last_actions and config.WEB_ENABLED and DONT_KNOW_RE.search(last_reply):
            brain.last_actions = [("узнать", [command])]
            status("🌐 Ищу в интернете...")

        # Руки: СНАЧАЛА выполняем дела, ПОТОМ говорим, что сделали
        if brain.last_actions:
            if FACE is not None:
                FACE.set_state("think")
            hands.set_progress(status)
            summary, need_report, lines = run_actions(brain, hands, brain.last_actions, ask_yes_no, command)
            for line in lines:
                print(f"{YELLOW}  {line}{RESET}")
            if not interrupted.is_set():
                if need_report:                       # справка или что-то не вышло — мозг расскажет своими словами
                    last_reply = speak(brain.report(summary), brain.lang)
                elif brain.pending_say:               # всё получилось — говорим заготовленную фразу
                    last_reply = speak(brain.say_text(brain.pending_say), brain.lang)
                if interrupted.is_set():
                    continue
                voice.wait_done()
                # «Прочитай вслух» — читаем текст целиком (перебить: «Мартышка»)
                for text in list(hands.to_read):
                    status(f"📖 Читаю вслух ({len(text)} знаков). Остановить — скажи «{config.NAME}».")
                    lang = reply_language(text[:3000])
                    if lang == "uk" and len(re.findall(r"[іїєґ]", text.lower())) < len(text) / 200:
                        lang = "ru"   # одно украинское слово в русской статье — читаем русским голосом
                    speak(iter_paragraphs(text), lang if lang in ("ru", "de", "en", "uk") else "ru")
                    if interrupted.is_set():
                        break
                    voice.wait_done()
                hands.to_read.clear()
                if interrupted.is_set():
                    continue

        turn_log.done(command, brain.last_actions, lines, last_reply, None, "голос")
        if not interrupted.is_set():
            ears.expect_followup()
            status(f"👂 Слушаю продолжение ({config.FOLLOWUP_SECONDS} сек)...")


if __name__ == "__main__":
    if system.already_running(wait=20 if "--restart" in sys.argv else 0):
        if "--autostart" not in sys.argv and "--restart" not in sys.argv:
            system.message_box("Мартышка уже запущена — шар в углу экрана.\n"
                               "Выключить: правый клик по шару → «Выключить Мартышку».")
        sys.exit(0)
    HIDDEN = system.setup_output()
    try:
        if config.FACE_ENABLED:
            # Шар живёт в отдельном процессе — так ему никто не мешает и анимация всегда плавная
            from martyshka.face import Face
            FACE = Face()
            FACE.set_state("think")
            FACE.start()
        if "--autostart" in sys.argv:
            time.sleep(8)   # после включения компа даём Windows и Ollama спокойно проснуться
        main()
    except KeyboardInterrupt:
        print(f"\n{GREY}Давай, братан.{RESET}")
    except Exception as e:
        import traceback
        traceback.print_exc()
        if HIDDEN:
            system.message_box(f"Мартышка упала с ошибкой:\n{e}\n\nПодробности — в data/журнал.txt")
        os._exit(1)
