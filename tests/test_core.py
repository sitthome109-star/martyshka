"""
Базовые автотесты ядра и инструментов разработки Мартышки.
Запуск: .venv\\Scripts\\python.exe -m unittest discover -s tests -v
Не обращаются к сети и не трогают реальную папку data/.
"""
from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from pathlib import Path

from martyshka import deepseek, growth, longmem
from martyshka.studio import agent_loop, brains, devtools, tools


class TestDeepSeekCacheAndBrains(unittest.TestCase):
    def test_system_prompt_has_dynamic_mark_and_cache_splits(self):
        from datetime import datetime
        today = datetime.now().strftime("%d.%m.%Y")
        for kind in ("chat", "task"):
            prompt = agent_loop.system_prompt(kind, "DeepSeek Pro", "", "напиши скрипт")
            self.assertIn(deepseek.DYNAMIC_MARK, prompt)
            msgs = [{"role": "system", "content": prompt}, {"role": "user", "content": "привет"}]
            friendly = deepseek._cache_friendly(msgs)
            self.assertEqual(len(friendly), 3)
            self.assertEqual(friendly[0]["role"], "system")
            self.assertNotIn(today, friendly[0]["content"])
            self.assertEqual(friendly[1]["role"], "system")
            self.assertIn("=== Сейчас (свежее) ===", friendly[1]["content"])
            self.assertIn(today, friendly[1]["content"])
            self.assertEqual(friendly[2]["role"], "user")

    def test_brains_convert_strips_old_reasoning_keeps_current(self):
        hist = [
            {"role": "user", "text": "старый вопрос"},
            {"role": "assistant", "text": "старый ответ", "reasoning": "старые мысли", "tool_calls": []},
            {"role": "user", "text": "новая задача"},
            {"role": "assistant", "text": "", "reasoning": "текущие мысли",
             "tool_calls": [{"id": "c1", "name": "read_file", "args": {"path": "a.py"}}]},
            {"role": "tool", "id": "c1", "name": "read_file", "content": "ok"},
        ]
        msgs = brains.DeepSeekBrain._convert("sys", hist, with_reasoning=True)
        old_asst = msgs[2]
        cur_asst = msgs[4]
        self.assertNotIn("reasoning_content", old_asst)
        self.assertEqual(cur_asst.get("reasoning_content"), "текущие мысли")

    def test_classify_uses_project_context(self):
        self.assertEqual(agent_loop.classify("привет, как дела"), "chat")
        self.assertEqual(agent_loop.classify("исправь кнопку", project=""), "task")
        self.assertEqual(agent_loop.classify("исправь кнопку", project="C:/proj/my_game"), "code")

    def test_history_for_preserves_current_turn(self):
        chat = agent_loop.Chat()
        chat.history = [{"role": "user", "text": "задача"}]
        for i in range(180):
            chat.history.append({"role": "assistant", "text": "", "tool_calls": [{"id": f"c{i}", "name": "t", "args": {}}]})
            chat.history.append({"role": "tool", "id": f"c{i}", "name": "t", "content": "ok"})
        hist = chat._history_for(None)
        self.assertEqual(hist[0]["role"], "user")
        self.assertEqual(hist[0]["text"], "задача")
        self.assertEqual(len(hist), len(chat.history))


class TestDevToolsAndFileEdits(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self._orig_ws = tools.config.STUDIO_WORKSPACE
        tools.config.STUDIO_WORKSPACE = str(self.root)

    def tearDown(self):
        tools.config.STUDIO_WORKSPACE = self._orig_ws
        self._tmp.cleanup()

    def test_project_map_and_glob_root_starstar(self):
        (self.root / "app.py").write_text("class Hero:\n    def run(self): pass\ndef helper(): return 1\n", encoding="utf-8")
        (self.root / "sub").mkdir()
        (self.root / "sub" / "mod.py").write_text("def sub_fn(): pass\n", encoding="utf-8")
        res = devtools.run("project_map", {"path": str(self.root)}, tools._path, tools.workspace)
        self.assertTrue(res["ok"])
        self.assertIn("Python", res["text"])
        self.assertIn("class Hero", res["text"])
        self.assertIn("def helper", res["text"])

        g = devtools.run("glob_files", {"pattern": "**/*.py", "path": str(self.root)}, tools._path, tools.workspace)
        self.assertTrue(g["ok"])
        self.assertIn("app.py", g["text"])
        self.assertIn("mod.py", g["text"])

    def test_grep_code_with_context(self):
        (self.root / "sample.py").write_text("line1\nline2\nTARGET_WORD = 42\nline4\nline5\n", encoding="utf-8")
        res = devtools.run("grep_code", {"pattern": "TARGET_WORD", "path": str(self.root), "context": 1},
                           tools._path, tools.workspace)
        self.assertTrue(res["ok"])
        self.assertIn("line2", res["text"])
        self.assertIn("TARGET_WORD = 42", res["text"])
        self.assertIn("line4", res["text"])

    def test_edit_file_crlf_hint_syntax_and_undo(self):
        f = self.root / "calc.py"
        f.write_bytes(b"def add(a, b):\r\n    return a + b\r\n")

        # 1) Неточное совпадение → подсказка с ближайшими строками
        miss = tools.run_tool("edit_file", {"path": str(f), "old": "def add(a, b, c):\n    return 0", "new": "x"}, {})
        self.assertFalse(miss["ok"])
        self.assertIn("Ближайшее похожее", miss["text"])

        # 2) Правка с LF по CRLF-файлу → сохраняет CRLF
        ok = tools.run_tool("edit_file", {"path": str(f), "old": "def add(a, b):\n    return a + b",
                                          "new": "def add(a, b):\n    return a + b + 1"}, {})
        self.assertTrue(ok["ok"])
        raw = f.read_bytes()
        self.assertIn(b"\r\n", raw)
        self.assertIn(b"return a + b + 1", raw)

        # 3) Откат возвращает исходный код
        undo = tools.run_tool("undo_edit", {"path": str(f)}, {})
        self.assertTrue(undo["ok"])
        self.assertIn(b"return a + b\r\n", f.read_bytes())

        # 4) Синтаксическая ошибка ловится мгновенно
        bad = tools.run_tool("write_file", {"path": str(f), "content": "def broken(:\n    pass\n"}, {})
        self.assertFalse(bad["ok"])
        self.assertIn("ОШИБКА СИНТАКСИСА", bad["text"])

    def test_check_code_catches_python_and_json_errors(self):
        (self.root / "good.py").write_text("x = 1 + 2\n", encoding="utf-8")
        (self.root / "bad.json").write_text("{broken json", encoding="utf-8")
        res = devtools.run("check_code", {"path": str(self.root), "run_tests": False}, tools._path, tools.workspace)
        self.assertFalse(res["ok"])
        self.assertIn("bad.json", res["text"])

        (self.root / "bad.json").write_text('{"ok": true}', encoding="utf-8")
        res2 = devtools.run("check_code", {"path": str(self.root), "run_tests": False}, tools._path, tools.workspace)
        self.assertTrue(res2["ok"])


class TestCrossProcessMemorySync(unittest.TestCase):
    def test_growth_reloads_on_external_change(self):
        with tempfile.TemporaryDirectory() as td:
            orig_file, orig_cache, orig_mt = growth.FILE, growth._cache, growth._cache_mtime
            try:
                growth.FILE = Path(td) / "опыт.json"
                growth._cache, growth._cache_mtime = None, -1.0
                growth.add("Первый урок о работе с проектом", "чат", "сама")
                # Имитируем запись из другого процесса (Говорилки)
                data = json.loads(growth.FILE.read_text(encoding="utf-8"))
                data.append({"text": "Урок из Говорилки про музыку", "где": "говорилка", "вид": "сама",
                             "когда": "2026-11-21T16:00", "подтверждений": 1})
                growth.FILE.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
                os.utime(growth.FILE, (time.time() + 2, time.time() + 2))
                texts = [it["text"] for it in growth.items()]
                self.assertIn("Урок из Говорилки про музыку", texts)
            finally:
                growth.FILE, growth._cache, growth._cache_mtime = orig_file, orig_cache, orig_mt

    def test_longmem_reloads_people_on_external_change(self):
        with tempfile.TemporaryDirectory() as td:
            orig_file, orig_people, orig_mt = longmem.PEOPLE, longmem._people, longmem._people_mtime
            try:
                longmem.PEOPLE = Path(td) / "люди.json"
                longmem._people, longmem._people_mtime = None, -1.0
                longmem.remember_about("Enbulih", "любит шутеры")
                # Имитируем обновление из другого процесса
                data = json.loads(longmem.PEOPLE.read_text(encoding="utf-8"))
                data["Enbulih"]["факты"].append("живёт в Германии")
                longmem.PEOPLE.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
                os.utime(longmem.PEOPLE, (time.time() + 2, time.time() + 2))
                self.assertIn("живёт в Германии", longmem.person("Enbulih")["факты"])
            finally:
                longmem.PEOPLE, longmem._people, longmem._people_mtime = orig_file, orig_people, orig_mt


class TestNewStudioAndTelegramFeatures(unittest.TestCase):
    def test_task_rollback_restores_edited_and_moves_new(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            existing = root / "a.txt"
            created = root / "b.txt"
            existing.write_bytes(b"original")
            orig_bytes = existing.read_bytes()
            existing.write_bytes(b"modified")
            created.write_bytes(b"brand new")
            agent_loop.TASK_ROLLBACKS["t_test"] = {str(existing): orig_bytes, str(created): None}
            res = agent_loop.rollback_task("t_test")
            self.assertTrue(res["ok"])
            self.assertEqual(existing.read_bytes(), b"original")
            self.assertFalse(created.exists())

    def test_telegram_agent_command_regex(self):
        from martyshka import telegram_service
        for msg, expected in [
            ("агент: почини тест", "почини тест"),
            ("Программируй: сделай калькулятор", "сделай калькулятор"),
            ("кодь - добавь кнопку", "добавь кнопку"),
            ("agent: check project", "check project"),
        ]:
            m = telegram_service.AGENT_CMD_RE.match(msg)
            self.assertIsNotNone(m, msg)
            self.assertEqual(m.group(1).strip(), expected)
        self.assertIsNone(telegram_service.AGENT_CMD_RE.match("привет, какая погода?"))

    def test_studio_api_usage_and_pasted_image(self):
        import base64
        from martyshka.studio import app
        api = app.Api()
        u = api.usage_info()
        self.assertIn("cache_pct", u)
        self.assertIn("requests", u)
        png_1x1 = base64.b64encode(
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde"
        ).decode()
        r = api.save_pasted_image("data:image/png;base64," + png_1x1)
        self.assertTrue(r["ok"])
        p = Path(r["path"])
        self.assertTrue(p.exists())
        p.unlink(missing_ok=True)


    def test_govorilka_speaker_watch_logic(self):
        from martyshka import govorilka as g
        self.assertEqual(g._clean_ocr_name("—l"), "")
        self.assertEqual(g._clean_ocr_name("• -"), "")
        self.assertEqual(g._clean_ocr_name("sitthome"), "sitthome")

        # Создаём SpeakerWatch без фонового потока
        w = object.__new__(g.SpeakerWatch)
        w.timeline = g.collections.deque(maxlen=100)
        w.last_overlap = []
        w._names_by_y = {100: "sitthome", 130: "Enbulih"}
        self.assertEqual(w._name_for_y(103), "sitthome")
        self.assertEqual(w._name_for_y(127), "Enbulih")

        # sitthome говорит один (4 кадра), потом микрофон Enbulih ловит колонки (3 совместных кадра) → sitthome
        t = 1000.0
        for i in range(4):
            w.timeline.append((t + i * 0.15, frozenset({"sitthome"})))
        for i in range(4, 7):
            w.timeline.append((t + i * 0.15, frozenset({"sitthome", "Enbulih"})))
        self.assertEqual(w.who(t, t + 1.0), "sitthome")

        # Реальное перекрытие: оба светятся только вместе → пусто + last_overlap
        w.timeline.clear()
        w.last_overlap = []
        for i in range(6):
            w.timeline.append((t + i * 0.15, frozenset({"Морни", "Enbulih"})))
        self.assertEqual(w.who(t, t + 1.0), "")
        self.assertEqual(set(w.last_overlap), {"Морни", "Enbulih"})

    def test_owner_sitth_recognition(self):
        from martyshka import govorilka as g
        for alias in ("sitth", "Sitth", "sitthome", "Ситх", "Сидхом", "брат"):
            self.assertEqual(longmem.norm(alias), longmem.OWNER, alias)
            self.assertTrue(longmem.is_owner(alias), alias)
        self.assertFalse(longmem.is_owner("Enbulih"))
        self.assertFalse(longmem.is_owner("Морни"))
        prompt = g.system_prompt(who="sitth")
        self.assertIn("ТВОЙ ХОЗЯИН", prompt)
        self.assertIn("sitth", prompt)

    def test_fish_tune_text_and_prosody(self):
        from martyshka import fish
        # Для бесплатной s2.1 русские теги переводятся в канонические английские
        s2 = fish.tune_text("[с усмешкой] Привет! [смешок] Как дела? [пауза]", fish.FREE)
        self.assertIn("[chuckle]", s2)
        self.assertIn("[pause]", s2)
        # Для Drama 3 сохраняется богатая русская режиссура
        dr = fish.tune_text("[холодно, с усмешкой] Ну ты и выдал.", fish.DRAMA)
        self.assertIn("[холодно, с усмешкой]", dr)
        # Динамическая просодия
        p_ang = fish.prosody_for("[angry] Хватит!", fish.FREE, anger=4)
        p_wh = fish.prosody_for("<whisper>Тихо…</whisper>", fish.DRAMA, anger=0)
        self.assertGreater(p_ang["speed"], 1.0)
        self.assertLess(p_wh["speed"], 1.0)
        self.assertGreaterEqual(p_wh["temperature"], 0.9)
        # mood_pick включает Drama 3 для шёпота/хохота/драмы (когда MOOD_VOICE включён)
        orig_mv = fish.config.MOOD_VOICE
        try:
            fish.config.MOOD_VOICE = True
            _, _, mdl = fish.mood_pick("[хохочет] Ха-ха!", "v1", fish.FREE, anger=0)
            self.assertEqual(mdl, fish.DRAMA)
        finally:
            fish.config.MOOD_VOICE = orig_mv

    def test_chat_diff_pop_turn_and_drop_file(self):
        import base64
        from martyshka.studio import app
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / "demo.py"
            f.write_text("x = 1\ny = 2\n", encoding="utf-8")
            orig_ws = tools.config.STUDIO_WORKSPACE
            try:
                tools.config.STUDIO_WORKSPACE = td
                r = tools.run_tool("edit_file", {"path": str(f), "old": "y = 2", "new": "y = 42"}, {})
                self.assertTrue(r["ok"])
                self.assertIn("-y = 2", r.get("diff", ""))
                self.assertIn("+y = 42", r.get("diff", ""))
            finally:
                tools.config.STUDIO_WORKSPACE = orig_ws

            orig_dir = agent_loop.CHATS_DIR
            try:
                agent_loop.CHATS_DIR = Path(td) / "chats"
                c = agent_loop.Chat()
                c.history = [{"role": "user", "text": "привет"}, {"role": "assistant", "text": "здарова", "tool_calls": []}]
                c.display = [{"type": "user", "text": "привет"}, {"type": "assistant", "text": "здарова"}]
                c.pinned = True
                c.save()
                listed = agent_loop.Chat.list_all()
                self.assertEqual(len(listed), 1)
                self.assertTrue(listed[0]["pinned"])
                popped = c.pop_last_turn()
                self.assertEqual(popped, "привет")
                self.assertEqual(c.history, [])
                self.assertEqual(c.display, [])
            finally:
                agent_loop.CHATS_DIR = orig_dir

        api = app.Api()
        d = api.save_dropped_file("note.txt", "data:text/plain;base64," + base64.b64encode(b"hello").decode())
        self.assertTrue(d["ok"])
        self.assertFalse(d["is_image"])
        Path(d["path"]).unlink(missing_ok=True)

    def test_govorilka_anger_does_not_attack_or_grow_wrongly(self):
        from unittest import mock
        from martyshka import govorilka as g
        angers = {"Enbulih": 5, "брат": 0, "sitthome": 1, "Вася": 2}
        # вход/выход: на кого злость ≥2 — молчит, остальным можно поздороваться
        self.assertEqual(g._greet_targets(["Enbulih", "брат", "sitthome", "Вася"], lambda n: angers.get(n, 0)),
                         ["брат", "sitthome"])
        self.assertEqual(g._greet_targets(["Enbulih"], lambda n: angers.get(n, 0)), [])

        store = {"брат": 0}
        g.PEACE_AT.clear()
        g.QUARREL.clear()
        with mock.patch.object(longmem, "set_anger", side_effect=lambda n, v: store.__setitem__(n, v) or v), \
                mock.patch.object(g, "_log"):
            # брат снова просит спеть: мозг поставил [ТОН: 3], оценщик сказал «не обида» → злость остаётся 0
            self.assertEqual(g._apply_tone("{поёт} ля-ля [ТОН: 3]", "брат", "спой ещё раз", 0, 0, rated=True), 0)
            # своя реплика (привет/пока) с [ТОН: 4] злость не поднимает
            self.assertEqual(g._apply_tone("Привет [ТОН: 4]", "брат", "", 0, rated=True), 0)
            # настоящая обида — растёт
            self.assertEqual(g._apply_tone("Сам дурак [ТОН: 1]", "брат", "ты тупая", 0, 2, rated=True), 2)

            # Морни мирится — злость падает сразу на 2 (а не на 1), даже если мозг оставил [ТОН: 5]
            self.assertEqual(g._apply_tone("Принято [ТОН: 5]", "Морни", "Ладно, прости, переборщила", 5, 0,
                                           rated=True), 3)
            # после мира следующая мирная реплика (без слов «прости») тоже остужает быстро
            self.assertEqual(g._apply_tone("Ок [ТОН: 3]", "Морни", "а что думаешь про новую песню?", 3, 0,
                                           rated=True), 1)
            self.assertEqual(g._apply_tone("Ладно [ТОН: 3]", "Морни", "песня правда зашла, затыкаюсь", 3, 0,
                                           rated=True), 1)
            # «прости, но ты тупая» — это не мир, а новый подкол: не падает
            self.assertEqual(g._apply_tone("[ТОН: 4]", "Морни", "прости, но ты тупая", 4, 1, rated=True), 5)

        self.assertEqual(store["Морни"], 5)

    def test_govorilka_quarrel_has_end(self):
        """Ссора: помнит, что уже сказала; долго на пределе не держится; «выключите её» — остывает."""
        from unittest import mock
        from martyshka import govorilka as g
        store = {"Морни": 5, "Вася": 0}
        g.QUARREL.clear()
        g.PEACE_AT.clear()
        with mock.patch.object(longmem, "set_anger", side_effect=lambda n, v: store.__setitem__(n, v) or v), \
                mock.patch.object(longmem, "anger", side_effect=lambda n: store.get(n, 0)), \
                mock.patch.object(g, "_log"):
            g._quarrel_track("Морни", 5, "Морни, ты склад брака")
            g._quarrel_track("Морни", 5, "Морни, трусиха")
            ctx = g._quarrel_context("Морни", 5)
            self.assertIn("склад брака", ctx)            # её упрёки видны мозгу — чтобы не повторяла
            self.assertIn("смени тактику", ctx)
            self.assertEqual(g._quarrel_context("Вася", 5), "")
            # ссора на злости 5 идёт дольше QUARREL_MAX — злость не выше 3, даже если мозг ставит 5
            g.QUARREL["Морни"]["since"] -= g.QUARREL_MAX + 1
            self.assertEqual(g._apply_tone("[ТОН: 5]", "Морни", "ты бот", 5, 1, rated=True), 3)
            # комната: «выключите её» во время ссоры — сигнал остыть (даже без её имени: «заебала»)
            self.assertTrue(g._is_cool_signal("Что-то она реально не в Prime"))
            self.assertTrue(g._is_cool_signal("заебала уже"))
            st = {"morni_until": 9e18}
            store["Морни"] = 5
            g._cool_down(st, "тест")
            self.assertEqual(store["Морни"], 1)
            self.assertEqual(st["morni_until"], 0.0)
            self.assertEqual(g.QUARREL, {})
            # ссоры нет — «заебали эти пробки» к ней не относится
            self.assertFalse(g._is_cool_signal("заебали эти пробки"))
            self.assertTrue(g._is_cool_signal("Выключите нахуй эту мартышку"))
            # злость упала до 1 — ссора закрыта
            g._quarrel_track("Морни", 3, "Морни, ну ты и …")
            g._quarrel_track("Морни", 1, "ладно, мир")
            self.assertNotIn("Морни", g.QUARREL)
            # брат просит начать разговор с Морни — чистый лист
            store["Морни"] = 5
            g._fresh_start_with_morni()
            self.assertEqual(store["Морни"], 1)
        g.QUARREL.clear()

    def test_govorilka_behind_back_not_rated_for_bots(self):
        from unittest import mock
        from martyshka import govorilka as g
        # о ней за спиной: Морни и «неизвестно кто» не оцениваются вовсе
        with mock.patch.object(g, "_rate_offense") as rate, mock.patch.object(g.threading, "Thread") as th:
            g._rate_offense_bg("Мартышка тупая", "Морни")
            g._rate_offense_bg("Мартышка тупая", g.SERVER)
            th.assert_not_called()
            rate.assert_not_called()

        # мозг написал ключ с опечаткой — оценка не теряется
        with mock.patch.object(deepseek, "ask", return_value='{"обина": 2}'):
            self.assertEqual(g._rate_offense("Мартышка тупая", "Enbulih", True), 2)
        with mock.patch.object(deepseek, "ask", return_value='{"обида": 0}'):
            self.assertEqual(g._rate_offense("спой ещё", "брат"), 0)

    def test_govorilka_short_no_sir_no_template(self):
        """Говорилка: «сэр» вырезается; обычный ответ короткий (песню не режем); та же подача подряд — без неё."""
        from unittest import mock
        from martyshka import govorilka as g
        self.assertEqual(g._no_sir("{спокойно} Утро, сэр, вы правы."), "{спокойно} Утро, вы правы.")
        self.assertEqual(g._no_sir("{спокойно} Сэр, я вроде стою на месте."), "{спокойно} Я вроде стою на месте.")
        self.assertEqual(g._no_sir("Вот, сэр, и всё. Дальше петь или хватит, сэр?"), "Вот, и всё. Дальше петь или хватит?")
        self.assertEqual(g._no_sir("Извольте, сэр. {пауза} Мужик"), "Извольте. {пауза} Мужик")
        self.assertEqual(g._no_sir("Серый сервис, серы нет"), "Серый сервис, серы нет")   # «сер…» не трогаем
        self.assertNotIn("сэр", g._clean("[ТОН: 0] Да, сэр, сейчас.").lower())

        class FakeMouth:
            def __init__(self):
                self.said = []

            def queue(self, s):
                self.said.append(s)

        sentence = "Это довольно длинное предложение, которое мозг пишет без остановки, как будто читает лекцию. "
        long_reply = "[ТОН: 2] {спокойно, с лёгкой усмешкой} Ну, сэр, начнём. " + sentence * 12 + "[ЗАПОМНИ О: Вася | любит лекции]"

        def fake_stream(*a, **k):
            for i in range(0, len(long_reply), 7):
                yield "text", long_reply[i:i + 7]
        history = [{"role": "user", "content": "(Вася): привет"},
                   {"role": "assistant", "content": "{Спокойно,  с лёгкой усмешкой} Привет."},
                   {"role": "user", "content": "(Вася): как дела"}]
        with mock.patch.object(g, "build_msgs", return_value=[]), \
                mock.patch.object(deepseek, "stream", side_effect=fake_stream):
            m = FakeMouth()
            raw = g.reply_stream(history, m, 2, who="Вася")
            spoken = sum(g._spoken_len(s) for s in m.said)
            self.assertLessEqual(spoken, g.SHORT_MAX_CHARS)             # монолог обрезан
            self.assertGreater(spoken, 50)
            self.assertNotIn("сэр", " ".join(m.said).lower())
            self.assertFalse(m.said[0].startswith("{"))                 # та же подача, что в прошлый раз, — снята
            self.assertIn("[ТОН: 2]", raw)                              # метки не потерялись
            self.assertIn("[ЗАПОМНИ О: Вася | любит лекции]", raw)
            # песня/рассказ по просьбе — не режем
            m2 = FakeMouth()
            g.reply_stream(history, m2, 0, who="Вася", long_ok=True)
            self.assertGreater(sum(g._spoken_len(s) for s in m2.said), 1000)
            # к Морни — первая фраза начинается с «Морни,» (после подачи), дальше имя не навязываем
            m3 = FakeMouth()
            g.reply_stream([], m3, 0, with_morni=True, who="Морни")
            self.assertTrue(m3.said[0].startswith("{спокойно, с лёгкой усмешкой} Морни, ну"))
            self.assertFalse(any(s.startswith("Морни") for s in m3.said[1:]))
            # во время разговора с Морни говорит брат — ему без «Морни,»
            m4 = FakeMouth()
            g.reply_stream([], m4, 0, with_morni=True, who="sitth")
            self.assertNotIn("Морни", m4.said[0])
        self.assertTrue(g.LONG_REQ_RE.search("Мартышка, расскажи подробно, как прошёл день"))
        self.assertTrue(g.LONG_REQ_RE.search("расскажи сказку про Васю"))
        self.assertFalse(g.LONG_REQ_RE.search("Мартышка, как дела?"))
        # подсказка о заезженных началах
        ctx = g._openings_context([{"role": "assistant", "content": "{драма, ярость} Морни, ты {пауза} зла"},
                                   {"role": "assistant", "content": "{драма, ярость} Морни, опять {пауза}"}])
        self.assertIn("{драма, ярость} ×2", ctx)
        self.assertIn("{пауза} ×2", ctx)


if __name__ == "__main__":
    unittest.main()
