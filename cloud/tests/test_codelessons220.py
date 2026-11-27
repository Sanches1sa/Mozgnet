import os, sys, tempfile, unittest
os.environ["NEURO_DATA"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "server"))
import store, codelessons, admincorpus, api_board  # noqa: E402


class CodeLessons220(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        store.ensure_dirs()

    def test_library(self):
        st = codelessons.status()
        self.assertGreaterEqual(st["count"], 100)
        self.assertEqual(st["count"], st["tested"])
        ids = [x["id"] for x in codelessons.load()]
        self.assertEqual(len(ids), len(set(ids)))

    def test_every_template_runs_with_its_test(self):
        for it in codelessons.load():
            ok, out = codelessons.verify(it)
            self.assertTrue(ok, "%s: %s" % (it["id"], out))

    def test_match(self):
        cases = {"напиши функцию проверки палиндрома": "str.palindrome", "антидребезг кнопки": "hw.debounce",
                 "сделай бинарный поиск": "list.bsearch", "исправь ошибку IndexError: list index out of range": "fix.indexerror",
                 "нужен кольцевой буфер для датчика": "oop.ring", "перцептрон": "ai.perceptron"}
        for q, want in cases.items():
            m = codelessons.match(q, 3)
            self.assertTrue(m and want in [it["id"] for _s, it in m], "%s -> %s" % (q, m))
        self.assertEqual(codelessons.match("расскажи про кошек", 3), [])
        self.assertIsNone(codelessons.compose("расскажи про кошек"))

    def test_compose_contains_code(self):
        t = codelessons.compose("посчитай факториал")
        self.assertIn("```python", t); self.assertIn("def factorial", t)

    def test_corpus_and_kb(self):
        c = codelessons.corpus()
        self.assertGreaterEqual(len(c), 200)
        self.assertEqual(len(set(s for s, _ in c)), len(c))
        self.assertGreaterEqual(len(admincorpus.code_texts()), 200)
        self.assertGreaterEqual(codelessons.seed_kb(), 100)

    def test_api(self):
        class R:
            method = "GET"; query = {"q": "палиндром"}; body = {}
        code, r = api_board.h_codelessons(R())
        self.assertEqual(code, 200); self.assertTrue(r["matches"])
        R.query = {"id": "num.fib", "verify": "1"}
        code, r = api_board.h_codelessons(R())
        self.assertTrue(r["verified"])

    def test_codewrite_uses_lessons(self):
        import codewrite
        from unittest import mock
        with mock.patch.object(codewrite, "own_write", return_value=None):
            t = codewrite.compose("напиши факториал на python")
        self.assertIn("def factorial", t)


if __name__ == "__main__":
    unittest.main()
