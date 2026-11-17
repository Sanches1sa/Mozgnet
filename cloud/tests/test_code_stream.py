"""v3.176: второй поток своей LLM (код из репозиториев) и сохранность ссылок."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'server'))
import test_repairs  # noqa: F401
import store
import codecorpus
import codeboot
import codestream
import tinyllm as tl

ROWS = [{"t": "add numbers %d" % i, "n": "add%d" % i, "s": "repo/f%d.py" % i,
         "c": "def add%d(a, b):\n    return a + b + %d" % (i, i)} for i in range(5)]


def small_model():
    texts = codeboot._course(ROWS) + ["Фотосинтез идёт в листьях растений на свету."] * 3
    c = dict(tl.cfg(), llm_d=16, llm_layers=1, llm_heads=2, llm_ctx=32, llm_vocab=400)
    m = tl.GPT(c)
    m.tok.build(texts, 400)
    m.init_params(m.tok.size)
    m.steps = 1
    return m


class StreamTests(unittest.TestCase):
    def setUp(self):
        store.save_json("code_stream.json", {})
        store.save_json("llm_streams.json", {})
        codestream._Q.clear(); codestream._QUEUED.clear(); codestream._LEFT.clear()
        del codestream._DONE[:]
        store.update_settings({"llm_code_stream": True, "llm_code_mix": 1})

    def test_pick_new_then_nothing(self):
        with patch.object(codestream, "_rows", lambda: ROWS):
            rows, kind = codestream.pick(codestream._state())
            self.assertEqual((len(rows), kind), (5, "new"))
            st = codestream._state(); st["seen"] = [codestream._sig(r) for r in ROWS]
            st["last"] = 9e18
            self.assertEqual(codestream.pick(st)[1], None)

    @unittest.skipUnless(tl.HAVE_NP, "numpy")
    def test_weave_trains_on_code_and_marks_learned(self):
        m = small_model()
        self.assertTrue(codestream._code_vocab(m))
        with patch.object(codestream, "_rows", lambda: ROWS):
            n = codestream._fill(m)
            self.assertGreater(n, 0)
            before = m.p["E"].copy()
            for _ in range(60):
                codestream.interleave(m, 1e-3)
            self.assertGreater(m.meta.get("code_steps", 0), 0)
            self.assertFalse((m.p["E"] == before).all(), "веса не изменились")
            got = codestream._flush_done()
            self.assertEqual(got, 5)
            self.assertEqual(codestream.status()["learned"], 5)
            self.assertEqual(codestream.status()["left"], 0)

    def test_weave_respects_mix(self):
        store.update_settings({"llm_code_mix": 1000})
        class M:
            meta = {}
            def train_step(self, X, Y, lr):
                raise AssertionError("не должен учиться")
        codestream._Q.append(("x", [1, 2, 3]))
        codestream.interleave(M(), 1e-3)


class LinkTests(unittest.TestCase):
    def test_cap_keeps_repo_links(self):
        import study
        ls = [{"url": "https://github.com/a/b", "kind": "github"}] + \
            [{"url": "https://e.ru/%d" % i, "kind": "page"} for i in range(80)]
        out = study._cap(ls)
        self.assertLessEqual(len(out), study._MAX_LINKS)
        self.assertEqual(out[0]["url"], "https://github.com/a/b")
        self.assertEqual(out[-1]["url"], "https://e.ru/79")

    def test_resume_replans_unplanned_link(self):
        import ghlearn
        store.update_settings({"study_links": [{"url": "https://github.com/a/b", "kind": "github",
                                                "reading": True, "note": "составляю план файлов…"}]})
        store.save_json("ghlearn.json", {})
        called = []
        with patch.object(ghlearn, "_plan_link", lambda u: called.append(u)):
            n = ghlearn.resume()
            import time; time.sleep(0.2)
        self.assertEqual(n, 1)
        self.assertEqual(called, ["https://github.com/a/b"])
        ghlearn._PLANNING.clear()
        st = ghlearn.status()
        self.assertEqual(st["links"][0]["url"], "https://github.com/a/b")


if __name__ == "__main__":
    unittest.main()
