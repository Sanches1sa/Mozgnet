"""v3.175: своя LLM пишет свободно — предложения ответа и код."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'server'))
import test_repairs  # noqa: F401  (временная папка данных)
import store
import llmreply
import tinyllm
import codewrite

REPLY = ("Фотосинтез идёт в листьях растений на свету. "
         "Из углекислого газа и воды образуются глюкоза и кислород. "
         "Хлорофилл поглощает красный и синий свет.")


def fake_compose(q, facts, max_new=70, min_frac=0.0):
    # слова «вне факта» и другой порядок — раньше это всегда отбрасывалось
    return "Растение само делает себе еду из солнечного света и воздуха.", \
        {"llm": "ok", "grounded": 0.2, "steps": 100}


class FreeWriteTests(unittest.TestCase):
    def setUp(self):
        store.update_settings({"answer_honest": True, "llm_free": True})

    def test_free_llm_writes_sentences(self):
        with patch.object(tinyllm, "compose", fake_compose, create=True), \
                patch.object(tinyllm, "has_garbage", lambda t: False, create=True):
            txt, n_all, n_llm, _rest = llmreply.write_answer("что такое фотосинтез", REPLY)
        self.assertGreater(n_llm, 0, txt)
        self.assertIn("солнечного света", txt)
        self.assertLess(llmreply.write_answer.ground, 0.5)

    def test_strict_mode_is_old_behaviour(self):
        store.update_settings({"llm_free": False})
        with patch.object(tinyllm, "compose", fake_compose, create=True):
            _txt, _n_all, n_llm, _rest = llmreply.write_answer("что такое фотосинтез", REPLY)
        self.assertEqual(n_llm, 0)

    def test_garbage_still_dropped(self):
        with patch.object(tinyllm, "compose", fake_compose, create=True), \
                patch.object(tinyllm, "has_garbage", lambda t: True, create=True):
            _txt, _n, n_llm, _r = llmreply.write_answer("что такое фотосинтез", REPLY)
        self.assertEqual(n_llm, 0)
        self.assertIn("каша из букв", llmreply.write_answer.last_why)

    def test_unfinished_code_draft_shown(self):
        def wc(task, tries=4, **kw):
            wc.last_raw = "def add(a, b):\n    return a +"
            return []
        with patch.object(codewrite, "own_ready", lambda: True), \
                patch.object(tinyllm, "write_code", wc, create=True):
            o = codewrite.own_write("сложить два числа")
        self.assertFalse(o["ok"])
        self.assertIn("def add", o["draft"])
        self.assertIn("черновик", codewrite.draft_text(o))

    def test_failed_check_draft_shown_with_verified(self):
        o = {"ok": False, "draft": "def f(:\n  pass", "why": "синтаксис", "of": 2}
        with patch.object(codewrite, "own_write", lambda *a, **k: o), \
                patch.object(codewrite, "write", lambda *a, **k: None), \
                patch.object(codewrite, "learned_write", lambda *a, **k: {
                    "source": "x", "code": "def f():\n    pass", "check": "ok"}):
            r = codewrite.compose("функция f")
        self.assertTrue(r.startswith("Своя LLM написала код сама"), r)
        self.assertIn("Проверенный вариант", r)


if __name__ == "__main__":
    unittest.main()
