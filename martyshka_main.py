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
              "экран", "нажать", "клавиши", "команда", "задача", "удалить", "окна", "игра", "доработать игру", "exe",
              "вспомнить"}


def _start_background_watchers() -> None:
    """1) Настройки поменяли в окне чата — подхватываем на ходу, без перезапуска.
    2) Сторож Говорилки: бот должен сидеть на сервере всегда (если брат сам не сказал «уйди совсем»)."""
    def settings_watch():
        last = None
        while True:
            # кнопки «Перезапустить» / «Выключить» в настройках окна чата
            for flag, act in ((config.DATA_DIR / "перезапуск.flag", "restart"), (config.DATA_DIR / "выключить.flag", "quit")):
                if flag.exists():
                    flag.unlink(missing_ok=True)
                    if act == "restart":
                        system.restart_self()
                    os._exit(0)
            try:
                m = config.SETTINGS_FILE.stat().st_mtime
                if last is not None and m != last:
                    config._apply_settings_file()
                    status("⚙ Настройки обновлены")
                last = m
            except Exception:
                pass
            time.sleep(2)

    def govorilka_watch():
        time.sleep(90)                       # после включения ПК дать всему прогрузиться
        from martyshka import govorilka
        while True:
            try:
                if (config.GOVORILKA_AUTOSTART and not govorilka.LEFT_FLAG.exists() and not govorilka.is_running()
                        and config.GOVORILKA_EXE.exists() and govorilka.cable_ready()):
                    status("🎙 Говорилка: бота нет на сервере — захожу")
                    govorilka.start()
            except Exception as e:
                status(f"   (сторож Говорилки: {e})")
            time.sleep(60)
    threading.Thread(target=settings_watch, daemon=True, name="settings-watch").start()
    threading.Thread(target=govorilka_watch, daemon=True, name="govorilka-watch").start()


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

    # Мозг (DeepSeek), голос (Fish Audio) и уши (DeepSeek) — в облаке: на ПК грузится только «ухо на имя» (Vosk)
    status(f"Мозг: DeepSeek ({config.BRAIN_MODE})")
    brain = Brain()
    try:
        brain.warm_up()
    except Exception as e:
        fail(f"Мозг не готов: {e}")

    status("Голос: Fish Audio")
    from martyshka.voice import Voice
    voice = Voice()
    if not voice.wait_ready():
        status(f"   голос не готов: {voice.error}")
    if FACE is not None:
        FACE.voice = voice  # шар пульсирует в такт голосу

    status("Уши: Vosk (имя) + DeepSeek (речь)")
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
    from martyshka import media    # фото/видео: обработка файлов (без генерации)
    media.set_hooks(gpu_before=None, gpu_after=None, llm=brain.complete)
    from martyshka import games      # игры: код пишет сильный мозг DeepSeek
    games.set_llm(brain.complete_raw)
    # ---------- Telegram (работает и без голоса, когда открыт чат) ----------
    from martyshka import telegram_service
    telegram_service.start(log=status, brain=brain, transcriber=transcriber)

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
            from martyshka import deepseek
            if isinstance(e, deepseek.DeepSeekError) and not said:   # мозг не ответил — скажем почему, вслух
                try:
                    voice.say(str(e).split(".")[0] + ".")
                except Exception:
                    pass
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

    # ---------- режим игр ----------
    game = {"on": False}

    def enter_game() -> None:
        voice.say(style.phrase("game_on"))
        voice.wait_done()
        status("🎮 Тихий режим...")
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
    _start_background_watchers()
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
