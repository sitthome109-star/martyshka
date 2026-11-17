"""
ЧАТ С МАРТЫШКОЙ (пишешь текстом — она отвечает текстом и голосом)

Служебные команды:
  /память      — показать, что Мартышка о тебе помнит
  /забудьвсё   — стереть всю память и историю
  /голос       — включить/выключить голос
  /выход       — выйти
Ctrl+C, пока Мартышка говорит, — заткнуть её.
"""
import os
import re
import sys

from martyshka import config, hands, style
from martyshka.brain import Brain

GREY, GREEN, CYAN, YELLOW, RESET = "\033[90m", "\033[92m", "\033[96m", "\033[93m", "\033[0m"


def main() -> None:
    if sys.platform == "win32":
        os.system("")  # включает цвета в консоли Windows
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    # Сначала мозг, потом голос: если грузить одновременно, они толкаются в видеопамяти
    # и первый ответ тормозит.
    print(f"{GREY}Загружаю мозг ({config.MODEL})...{RESET}")
    brain = Brain()
    try:
        brain.warm_up()
    except Exception as e:
        print(f"Не могу достучаться до Ollama: {e}\nПроверь, что Ollama запущена (иконка ламы в трее).")
        return

    voice = None
    if config.VOICE_ENABLED:
        print(f"{GREY}Загружаю голос ({config.VOICE_ENGINE})...{RESET}")
        from martyshka.voice import Voice
        voice = Voice()

    if voice is not None and not voice.wait_ready():
        print(f"{YELLOW}Голос не завёлся ({voice.error}). Работаю молча.{RESET}")
        voice = None
    voice_on = voice is not None
    hands.set_llm(brain.complete)
    hands.set_reminder_callback(lambda t: (print(f"\n{YELLOW}⏰ Напоминание: {t}{RESET}"),
                                           voice.say(style.phrase("remind", text=t)) if voice_on else None))

    print(f"{GREY}Готово. Пиши. /память, /забудьвсё, /голос, /выход{RESET}\n")

    while True:
        try:
            text = input(f"{CYAN}Ты:{RESET} ").strip().lstrip("﻿").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not text:
            continue
        cmd = text.lower().replace(" ", "")
        if cmd in ("/выход", "/exit", "/quit"):
            break
        if cmd == "/память":
            facts = brain.memory.fact_texts()
            print(GREY + ("\n".join(f"  • {f}" for f in facts) if facts else "  (пусто)") + RESET + "\n")
            continue
        if cmd in ("/забудьвсё", "/забудьвсе"):
            brain.memory.forget_everything()
            print(f"{GREY}  Память стёрта.{RESET}\n")
            continue
        if cmd == "/голос":
            if voice is None:
                print(f"{GREY}  Голос не загружен.{RESET}\n")
            else:
                voice_on = not voice_on
                print(f"{GREY}  Голос {'включён' if voice_on else 'выключен'}.{RESET}\n")
            continue

        def say(pieces) -> None:
            """Напечатать (и озвучить) ответ; строку «Мартышка:» — только если есть что сказать."""
            started = False
            if voice_on:
                pieces = voice.speak_stream(pieces)
            for piece in pieces:
                if not started:
                    print(f"{GREEN}{config.NAME}:{RESET} ", end="", flush=True)
                    started = True
                print(piece, end="", flush=True)
            if started:
                print()
            if voice_on:
                voice.wait_done()

        try:
            say(brain.reply(text))
        except KeyboardInterrupt:
            if voice is not None:
                voice.stop()
            print(f"\n{GREY}  (заткнулся){RESET}")
        except Exception as e:
            print(f"\n[ошибка: {e}]")

        ch = brain.last_changes
        for f in ch.remembered:
            print(f"{YELLOW}  (запомнил: {f}){RESET}")
        for f in ch.forgotten:
            print(f"{YELLOW}  (забыл: {f}){RESET}")

        # Не знает ответа — тихо ищем в интернете
        if not brain.last_actions and config.WEB_ENABLED and re.search(
                r"(не знаю|не в курсе|без понятия|не уверен|нет информации|не могу знать)",
                brain.memory.history[-1]["content"] if brain.memory.history else "", re.I):
            brain.last_actions = [("узнать", [text])]
            print(f"{GREY}  (ищу в интернете...){RESET}")

        # Руки: СНАЧАЛА выполняем дела, ПОТОМ говорим, что сделали
        if brain.last_actions:
            def ask(question: str) -> bool:
                return hands.is_yes(input(f"{YELLOW}  {question} (да/нет):{RESET} "))
            summary, need_report, lines = hands.execute_all(brain.last_actions, ask)
            for line in lines:
                print(f"{YELLOW}  {line}{RESET}")
            if need_report:                   # справка или что-то не вышло — мозг расскажет своими словами
                say(brain.report(summary))
            elif brain.pending_say:           # всё получилось — говорим заготовленную фразу
                say(brain.say_text(brain.pending_say))
        print()

    print(f"{GREY}Давай, братан.{RESET}")


if __name__ == "__main__":
    main()
