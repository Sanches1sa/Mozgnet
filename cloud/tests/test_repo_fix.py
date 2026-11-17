"""v3.176.1: архив не скачался — чтение не встаёт; loss кода не взрывается."""
from pathlib import Path
import io
import sys
import time
import unittest
import zipfile
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'server'))
import test_repairs  # noqa: F401
import store
import ghlearn
import codestream
import tinyllm as tl
from test_code_stream import small_model


def make_zip():
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        z.writestr("repo-main/calc.py", "def add(a, b):\n    '''Сумма.'''\n    return a + b\n\n\ndef sub(a, b):\n    return a - b\n")
    return b.getvalue()


class RepoTests(unittest.TestCase):
    def test_backoff_does_not_stop_zip_reading(self):
        store.save_json("ghlearn.json", {})
        zd = make_zip()
        items, strip = ghlearn._zip_items(zd)
        ghlearn._register_zip("o/r", zd, items, strip, {"branch": "main", "host": "github.com"})
        d = ghlearn._load(); d["backoff_until"] = time.time() + 3000; ghlearn._save(d)
        with patch.object(ghlearn, "_ingest", lambda *a: 2):
            self.assertTrue(ghlearn.read_one())
        self.assertIn("calc.py", ghlearn._load()["last_file"])

    def test_zip_errors_full_and_deduped(self):
        calls = []
        def ff(u, timeout=0, max_bytes=0):
            calls.append(u)
            return False, "сервер ответил HTTP 503 (перегрузка)", "", ""
        with patch.object(ghlearn.webfetch, "fetch_file", ff):
            with self.assertRaises(ghlearn.GhErr) as e:
                ghlearn._download_zip("github.com", "o", "r")
        self.assertIn("HTTP 503 (перегрузка)", str(e.exception))
        self.assertTrue(any("api.github.com" in c and "zipball" in c for c in calls))
        self.assertEqual(sum(1 for c in calls if c == calls[0]), 2)   # два захода

    def test_failed_link_retried(self):
        store.update_settings({"study_links": [{"url": "https://github.com/o/x", "kind": "github",
                                                "err": "архив не скачался"}]})
        store.save_json("ghlearn.json", {})
        ghlearn._RETRY.clear(); ghlearn._PLANNING.clear()
        got = []
        with patch.object(ghlearn, "_plan_link", lambda u: got.append(u)):
            self.assertEqual(ghlearn.retry_failed(1000.0), 0)        # первый — через 15 мин
            self.assertEqual(ghlearn.retry_failed(1000.0 + 901), 1)
            time.sleep(0.2)
        self.assertEqual(got, ["https://github.com/o/x"])
        ghlearn._PLANNING.clear()


class GuardTests(unittest.TestCase):
    def setUp(self):
        store.save_json("code_stream.json", {})
        store.save_json("llm_streams.json", {})
        store.update_settings({"llm_code_stream": True, "llm_code_mix": 1})
        codestream._Q.clear()

    def test_exploding_loss_stops_stream(self):
        class M:
            meta = {}
            class tok: size = 4000
            def train_step(self, X, Y, lr):
                self.lr = lr
                return 13729.0
        m = M()
        codestream._Q.append(("s", [1, 2, 3, 4]))
        codestream.interleave(m, 0.01)
        self.assertLessEqual(m.lr, 5e-4)
        import llmstreams
        self.assertTrue(store.settings()["llm_code_stream"])       # остальные потоки учатся
        self.assertFalse(llmstreams.on("code"))                    # выключен только код
        self.assertIn("разошлись", codestream.status()["diverged"])

    @unittest.skipUnless(tl.HAVE_NP, "numpy")
    def test_restore_good_backup(self):
        m = small_model()
        good = store.p("code_boot_backups", "t1", "model")
        Path(good).parent.mkdir(parents=True, exist_ok=True)
        m.save(good)
        E0 = m.p["E"].copy()
        m.p["E"][:] = 1e4                                 # «разошлись»
        st = codestream._state(); st["good"] = [{"path": good, "ts": time.time(), "loss": 5.0}]
        codestream._save(st)
        with patch.object(tl, "_M", m), patch.object(tl, "_THREAD", [None]), \
                patch.object(tl, "_state_path", lambda: store.p("tl_state")), \
                patch.object(tl, "ensure_training", lambda: None):
            r = codestream.restore_good()
        self.assertTrue(r["ok"], r)
        self.assertTrue((m.p["E"] == E0).all())


if __name__ == "__main__":
    unittest.main()
