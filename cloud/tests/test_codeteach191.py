# -*- coding: utf-8 -*-
"""v3.191: учитель кода для своей LLM (вкладка «Учитель (LLM)», движки
«Яндекс (YandexGPT)» и «внешний учитель»). Уроки: задача -> одна функция ->
проверки assert в песочнице; принятые идут в курс кода (codeboot).
Запуск: python -m unittest tests.test_codeteach191 -v
"""
import os
import sys
import time
import unittest
from unittest.mock import patch

if 'NEURO_DATA' not in os.environ:
    import tempfile
    os.environ['NEURO_DATA'] = tempfile.mkdtemp()
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'server'))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_repairs  # noqa: F401  (ставит NEURO_DATA и поднимает общий контекст)

import codeteach
import store


GOOD_LESSON = {
    "task": "удвой число",
    "solution": "def double(n):\n    return n * 2",
    "checks": ["assert double(2) == 4", "assert double(0) == 0",
               "assert double(-3) == -6"],
}


def _raw(j):
    import json
    return json.dumps(j, ensure_ascii=False)


class ValidateTest(unittest.TestCase):
    """Песочница урока: принимаем верное, отбраковываем опасное и неверное."""

    def test_good_lesson_accepted(self):
        self.assertEqual(codeteach._validate(
            GOOD_LESSON["solution"], GOOD_LESSON["checks"]), "")

    def test_syntax_rejected(self):
        self.assertIn("синтаксическая", codeteach._validate(
            "def f(a:\n    return a", ["assert f(1)==1"]))

    def test_import_rejected(self):
        self.assertIn("запрещено", codeteach._validate(
            "def f(a):\n    import os\n    return a", ["assert f(1)==1"]))

    def test_wrong_result_rejected(self):
        self.assertIn("не сошлась", codeteach._validate(
            "def f(a):\n    return a + 1", ["assert f(2) == 4"]))

    def test_infinite_loop_rejected(self):
        code = "def f(n):\n    while True:\n        pass\n    return n"
        r = codeteach._validate(code, ["assert f(1) == 1"])
        # срабатывает либо счётчик итераций («слишком долго»), либо лимит
        # времени проверки («код отброшен») — урок обязан быть отбракован
        self.assertTrue(r and ("слишком долго" in r or "отброшен" in r), r)

    def test_two_functions_rejected(self):
        code = "def f(a):\n    return a\ndef g(a):\n    return a"
        self.assertIn("ровно одно", codeteach._validate(
            code, ["assert f(1) == 1"]))

    def test_fstring_rejected(self):
        self.assertIn("запрещено", codeteach._validate(
            'def f(a):\n    return f"{a}"', ["assert f(1) == '1'"]))


class EngineTest(unittest.TestCase):
    """Движки: Яндексу нужен ключ (тот же, что для поиска), внешнему — включён."""

    def test_yandex_needs_key(self):
        with patch.object(store, "settings", return_value={}):
            call, name, err = codeteach._engine({"engine": "yandex"})
        self.assertIsNone(call)
        self.assertIn("ключ API Яндекса", err)

    def test_yandex_uses_folder_and_model(self):
        s = {"yandex_api_key": "k1", "yandex_folder": "b1g"}
        with patch.object(store, "settings", return_value=s):
            call, name, err = codeteach._engine(
                {"engine": "yandex", "model": "", "endpoint": ""})
            self.assertEqual(err, "")
            url, key, model, folder = codeteach._yandex_parts(
                {"engine": "yandex", "model": "", "endpoint": ""})
        self.assertEqual(model, "gpt://b1g/yandexgpt/latest")
        self.assertEqual(folder, "b1g")
        self.assertEqual(key, "k1")

    def test_teacher_engine_needs_settings(self):
        with patch.object(store, "settings",
                          return_value={"teacher": {"llm_on": False}}):
            call, name, err = codeteach._engine({"engine": "teacher"})
        self.assertIsNone(call)
        self.assertIn("внешний учитель", err)

    def test_teacher_engine_uses_openai_chat(self):
        t = {"llm_on": True, "llm_url": "http://127.0.0.1:8000/v1",
             "llm_model": "qwen", "llm_key": "sk"}
        with patch.object(store, "settings", return_value={"teacher": t}):
            call, name, err = codeteach._engine({"engine": "teacher"})
        self.assertEqual(err, "")
        with patch("teacher._openai_chat",
                   return_value=("ок", "")) as m:
            reply, e2 = call("система", "вопрос")
        self.assertEqual(reply, "ок")
        self.assertEqual(m.call_args[0][0], "http://127.0.0.1:8000/v1")


class ParseTest(unittest.TestCase):
    """Разбор ответа учителя: JSON с fences и без, честные отказы."""

    def test_json_with_fences(self):
        raw = "```json\n%s\n```" % _raw(GOOD_LESSON)
        lesson, err = codeteach._parse_lesson(raw)
        self.assertEqual(err, "")
        task, code, checks = lesson
        self.assertEqual(task, "удвой число")
        self.assertFalse(code.startswith("```"))

    def test_no_json(self):
        lesson, err = codeteach._parse_lesson("привет, вот упражнение…")
        self.assertIsNone(lesson)
        self.assertIn("JSON", err)

    def test_missing_checks(self):
        lesson, err = codeteach._parse_lesson(_raw({"task": "т", "solution": "def f():\n    return 1"}))
        self.assertIsNone(lesson)
        self.assertIn("проверок", err)


class CourseRunTest(unittest.TestCase):
    """Полный прогон: уроки принимаются в банк и уходят в курс кода."""

    def setUp(self):
        codeteach._STATE = None
        store.save_json('code_teach.json', {"engine": "yandex", "bank": []})

    def _wait(self, timeout=15):
        t0 = time.time()
        while time.time() - t0 < timeout:
            st = codeteach.status()
            if not st.get("running"):
                return st
            time.sleep(0.05)
        raise AssertionError("курс не завершился")

    def test_course_accepts_and_trains(self):
        raws = [_raw(GOOD_LESSON),
                "мусор без JSON",                                   # отбракован
                _raw({"task": "инверсия", "solution": "def f(a):\n    return a+1",
                      "checks": ["assert f(1) == 2"]}),             # задача с junk? верная
                _raw({"task": "плохая", "solution": "def f(a):\n    return a+",
                      "checks": ["assert f(1) == 2"]})]             # синтаксис
        seq = list(raws)
        def fake_engine(st):
            def call(system, user):
                return (seq.pop(0) if seq else _raw(GOOD_LESSON)), ""
            return call, "Яндекс (YandexGPT)", ""
        started = {}
        def fake_boot(rows, mode, steps, seconds, source):
            started["rows"] = rows
            started["source"] = source
            return {"phase": "done", "message": "ок"}
        with patch.object(codeteach, "_engine", fake_engine), \
             patch("codeboot.start", side_effect=fake_boot):
            codeteach.configure(lessons=4)
            r = codeteach.start()
            self.assertTrue(r["ok"], r)
            st = self._wait()
        self.assertEqual(st["phase"], "готово", st)
        self.assertEqual(st["stats"]["accepted"], 2, st)
        self.assertEqual(st["stats"]["requested"], 4)
        self.assertEqual(st["stats"]["rejected"], 2)
        self.assertEqual(started["source"], "учитель кода")
        self.assertEqual(len(started["rows"]), 2)
        self.assertTrue(all("def " in row["c"] for row in started["rows"]))
        self.assertGreaterEqual(st["bank_size"], 2)

    def test_course_without_lessons_honest(self):
        def fake_engine(st):
            def call(system, user):
                return None, "HTTP 401: плохой ключ"
            return call, "Яндекс (YandexGPT)", ""
        with patch.object(codeteach, "_engine", fake_engine):
            codeteach.configure(lessons=1)
            codeteach.start()
            st = self._wait()
        self.assertEqual(st["phase"], "без уроков", st)
        self.assertIn("не дал", st["message"])

    def test_start_without_key_honest(self):
        with patch.object(codeteach, "_engine",
                          return_value=(None, "", "нужен ключ API Яндекса")):
            r = codeteach.start()
        self.assertFalse(r["ok"])
        self.assertIn("ключ", r["error"])

    def test_check_teacher_mock(self):
        def fake_engine(st):
            def call(system, user):
                return _raw(GOOD_LESSON), ""
            return call, "Яндекс (YandexGPT)", ""
        with patch.object(codeteach, "_engine", fake_engine):
            r = codeteach.check_teacher()
        self.assertTrue(r["ok"])
        self.assertTrue(r["valid"])
        self.assertEqual(r["task"], "удвой число")


class RouteTest(unittest.TestCase):
    """GET/POST api/code/teach — статус, настройка, честный отказ старта."""

    def setUp(self):
        codeteach._STATE = None
        store.save_json('code_teach.json', {"engine": "yandex", "bank": []})

    @staticmethod
    def _req(body=None, method="GET"):
        from types import SimpleNamespace
        return SimpleNamespace(body=body or {}, method=method, query={})

    def test_routes_registered(self):
        import api_ui
        self.assertIn(("GET", "api/code/teach"), api_ui.ROUTES_UI)
        self.assertIn(("POST", "api/code/teach"), api_ui.ROUTES_UI)

    def test_get_status(self):
        import api_ui
        code, data = api_ui.h_code_teach(self._req())
        self.assertEqual(code, 200)
        self.assertTrue(data["ok"])
        self.assertIn("bank_size", data["teach"])

    def test_configure_clamps(self):
        import api_ui
        code, data = api_ui.h_code_teach(
            self._req({"action": "configure", "lessons": 99,
                       "engine": "yandex", "topics": "строки"}, "POST"))
        self.assertTrue(data["ok"])
        # v3.193: уроков за курс стало больше — предел 40 (был 20)
        self.assertEqual(data["teach"]["lessons"], 40)
        self.assertEqual(data["teach"]["topics"], "строки")

    def test_start_no_key_honest(self):
        import api_ui
        with patch.object(store, "settings", return_value={}):
            code, data = api_ui.h_code_teach(
                self._req({"action": "start"}, "POST"))
        self.assertFalse(data["ok"])
        self.assertIn("ключ", data["error"])


if __name__ == "__main__":
    unittest.main()
