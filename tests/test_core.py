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


if __name__ == "__main__":
    unittest.main()
