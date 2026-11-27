import os, sys, tempfile, time, unittest
from unittest import mock
os.environ["NEURO_DATA"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "server"))
import store, api_ui, tinyllm  # noqa: E402


class R:
    def __init__(self, body=None, method="POST"):
        self.body, self.query, self.method = body or {}, {}, method


class LlmApply235(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        store.ensure_dirs()

    def wait_idle(self):
        t0 = time.time()
        while api_ui._LLM_RT["busy"] and time.time() - t0 < 30:
            time.sleep(0.05)

    def test_apply_same_sizes_still_retrains_and_does_not_block(self):
        cfg = {k: store.settings().get(k) for k in ("llm_d", "llm_layers", "llm_heads", "llm_ctx")}
        calls = []

        def slow():
            time.sleep(1.5); calls.append(1)
        with mock.patch.object(tinyllm, "retrain_now", slow):
            t0 = time.time()
            code, r = api_ui.h_tinyllm(R(dict(cfg, retrain=True)))
            self.assertLess(time.time() - t0, 1.0)                  # страница не ждёт остановки обучения
            self.assertTrue(r["retrain_started"]); self.assertTrue(r["retrain"]["busy"])
            code, r2 = api_ui.h_tinyllm(R({"retrain": True}))
            self.assertFalse(r2["retrain_started"])                 # повторное нажатие не плодит потоки
            self.wait_idle()
        self.assertEqual(calls, [1])

    def test_new_sizes_applied_and_training_starts(self):
        self.wait_idle()
        code, r = api_ui.h_tinyllm(R({"llm_d": 64, "llm_layers": 1, "llm_heads": 2, "llm_ctx": 32, "retrain": True}))
        self.assertEqual((r["config"]["llm_d"], r["config"]["llm_layers"]), (64, 1))
        self.wait_idle()
        self.assertFalse(api_ui._LLM_RT["error"])
        st = tinyllm.status()
        self.assertEqual((st["d_model"], st["layers"], st["heads"], st["ctx"]), (64, 1, 2, 32))

    def test_locked_weights_file_is_moved_aside(self):
        base = tinyllm._state_path()
        open(base + ".npz", "wb").write(b"old")
        real = os.remove

        def locked(p):
            if p.endswith(".npz"):
                raise PermissionError(13, "занят")
            return real(p)
        with mock.patch("os.remove", locked), mock.patch("time.sleep"):
            r = tinyllm.delete_model(restart=False)
        self.assertFalse(os.path.exists(base + ".npz"))
        self.assertTrue(any("переименован" in x for x in r["removed"]))

    def test_web_apply_sends_retrain(self):
        js = open(os.path.join(os.path.dirname(__file__), "..", "web", "app.js"), encoding="utf-8").read()
        self.assertIn("b.retrain = true;", js)



class Phase236(unittest.TestCase):
    def test_preparation_phase_visible_and_small_corpus_explained(self):
        import threading
        gate = threading.Event()

        def slow_corpus(n):
            gate.wait(10)
            return ["мало"], 1
        tinyllm.delete_model(restart=False)
        with mock.patch.object(tinyllm, "_collect_corpus", slow_corpus):
            th = threading.Thread(target=tinyllm._train_all, daemon=True)
            th.start()
            time.sleep(0.5)
            st = tinyllm.status()
            self.assertTrue(st["training"]); self.assertEqual(st["total_steps"], 0)
            self.assertIn("корпус", st["phase"])                     # вместо «шаг 0 из 0» — чем занято
            gate.set(); th.join(10)
        st = tinyllm.status()
        self.assertFalse(st["training"]); self.assertIn("мало текста", st["error"])

    def test_web_shows_phase(self):
        js = open(os.path.join(os.path.dirname(__file__), "..", "web", "app.js"), encoding="utf-8").read()
        self.assertIn("<b>подготовка</b>", js)


if __name__ == "__main__":
    unittest.main()
