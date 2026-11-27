import os, re, json, sys, tempfile, threading, unittest
PY = os.path.join(os.path.dirname(__file__), "..", "python")
SRC = open(os.path.join(PY, "main.py"), encoding="utf-8").read()
i = SRC.index("_CL = {"); j = SRC.index("# [8b] v2.41")
MSG = []
NS = {"re": re, "os": os, "json": json, "traceback": __import__("traceback"), "__file__": os.path.join(PY, "main.py"),
      "CFG": {"selfcode_on": True}, "log": lambda *a: None,
      "add_message": lambda role, text, zone="", user="": MSG.append((role, text)),
      "codemem_find": lambda t, s=0.6: None, "codemem_add": lambda *a: MSG.append(("mem", a[2])),
      "cloud_think": lambda *a, **k: (None, "нет облака"), "_mem_ctx": lambda: []}
exec(SRC[i:j], NS)


class SelfCode(unittest.TestCase):
    def setUp(self):
        del MSG[:]
        NS['cloud_think'] = lambda *a, **k: (None, 'нет облака')
        NS['CFG']['selfcode_on'] = True

    def test_lessons_loaded(self):
        self.assertGreaterEqual(len(NS["_cl_items"]()), 100)

    def test_match_and_run_on_board(self):
        for task, want in (("проверь палиндром", "палиндром"), ("антидребезг кнопки", "ребезг"), ("посчитай факториал", "def factorial")):
            del MSG[:]
            NS["_code_job"](task)
            txt = " ".join(t for _r, t in MSG)
            self.assertIn(want, txt.lower() if want.islower() else txt)
            self.assertIn("прошли", txt)
            self.assertTrue(any(r == "mem" for r, _t in MSG))

    def test_unknown_is_honest(self):
        NS["_code_job"]("расскажи про кошек")
        txt = " ".join(t for _r, t in MSG)
        self.assertIn("не умеет", txt)
        self.assertNotIn("```python", txt)

    def test_cloud_code_only_compiled_not_run(self):
        marker = os.path.join(tempfile.mkdtemp(), "ran")
        NS["cloud_think"] = lambda *a, **k: ({"answer": "```python\nopen(%r, 'w').write('x')\n```" % marker}, "")
        NS["_code_job"]("сделай чтото странное особенное")
        self.assertFalse(os.path.exists(marker))
        self.assertIn("НЕ запускался", " ".join(t for _r, t in MSG))

    def test_bad_syntax_from_cloud(self):
        NS["cloud_think"] = lambda *a, **k: ({"answer": "```python\ndef (:\n```"}, "")
        NS["_code_job"]("сделай чтото странное особенное")
        self.assertIn("синтаксис:", " ".join(t for _r, t in MSG))

    def test_switch_off(self):
        NS["CFG"]["selfcode_on"] = False
        NS["_code_job"]("проверь палиндром")
        self.assertIn("выключена", MSG[0][1])
        NS["CFG"]["selfcode_on"] = True

    def test_command_wired(self):
        k = SRC.index('if low.startswith("/код")')
        self.assertIn("_code_job", SRC[k:k + 900])
        self.assertIn('"selfcode_on": True', SRC)


if __name__ == "__main__":
    unittest.main()
