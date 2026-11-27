import os, re, sys, traceback, unittest
PY = os.path.join(os.path.dirname(__file__), "..", "python")
SRC = open(os.path.join(PY, "main.py"), encoding="utf-8").read()
i = SRC.index("def _applab_job("); j = SRC.index("def _code_job(")
MSG = []
NS = {"re": re, "traceback": traceback, "add_message": lambda role, text, zone="", user="": MSG.append((role, text)),
      "cloud_think": None}
exec(SRC[i:j], NS)


class AppLab210(unittest.TestCase):
    def setUp(self):
        del MSG[:]

    def test_sends_applab_mode(self):
        seen = {}
        NS["cloud_think"] = lambda q, mode="auto", ctx=None, timeout=40: (seen.update(q=q, mode=mode) or {"answer": "готово"}, "")
        NS["_applab_job"]("напиши приложение для заметок")
        self.assertEqual(seen["mode"], "applab"); self.assertEqual(seen["q"], "напиши приложение для заметок")
        self.assertIn("готово", MSG[-1][1])

    def test_bare_name_means_check(self):
        seen = {}
        NS["cloud_think"] = lambda q, mode="auto", ctx=None, timeout=40: (seen.update(q=q) or {"answer": "ок"}, "")
        NS["_applab_job"]("погода")
        self.assertEqual(seen["q"], "проверь погода")

    def test_cloud_down_is_honest(self):
        NS["cloud_think"] = lambda *a, **k: (None, "нет связи")
        NS["_applab_job"]("список")
        self.assertIn("не ответило", MSG[-1][1]); self.assertIn("нет связи", MSG[-1][1])

    def test_command_wired(self):
        self.assertIn('low.startswith("/апп")', SRC); self.assertIn("mode=\"applab\"", SRC)


if __name__ == "__main__":
    unittest.main()
