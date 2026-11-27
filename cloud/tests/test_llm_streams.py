"""v3.177: потоки своей LLM — код, чаты МАКС, разговор; доли по воле мозга-12."""
from pathlib import Path
import sys
import time
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'server'))
import test_repairs  # noqa: F401
import store
import breath
import llmstreams
import codestream
import tinyllm as tl
from test_code_stream import small_model, ROWS

MAXT = [("m%d" % i, "Как перезапустить сервис %d: sudo systemctl restart demo%d" % (i, i)) for i in range(6)]
TALK = [("t%d" % i, "Вопрос: что такое фотосинтез %d\nОтвет: процесс в листьях растений на свету." % i) for i in range(6)]


def fake_texts(key):
    return {"max": MAXT, "talk": TALK}.get(key, [])


class StreamsTests(unittest.TestCase):
    def setUp(self):
        store.save_json("llm_streams.json", {})
        store.save_json("code_stream.json", {})
        store.update_settings({"llm_code_stream": True, "llm_code_mix": 1, "llm_stream_code": True,
                               "llm_stream_max": True, "llm_stream_talk": True})
        for k in llmstreams.TEXT_KEYS:
            llmstreams._TQ[k].clear(); llmstreams._TQUEUED[k].clear(); llmstreams._TLEFT[k].clear()
            del llmstreams._TDONE[k][:]
        codestream._Q.clear(); codestream._QUEUED.clear(); codestream._LEFT.clear()
        llmstreams._CREDIT.clear(); llmstreams._LIVE["steps"].clear()
        breath._WILL.clear()
        import llm_zones
        llm_zones._LIVE.clear()  # isolate measured training contexts from other tests

    def test_weights_follow_brain(self):
        w, on = llmstreams.weights()
        self.assertFalse(on)
        self.assertEqual(w["code"], 1.0)
        breath.note_will("b1", {"Кирпичики": 1.0, "Помощь": 0.0, "Память": 0.0})
        w, on = llmstreams.weights()
        self.assertTrue(on)
        self.assertGreater(w["code"] / w["max"], 3.0)          # 1.5 против 0.4

    @unittest.skipUnless(tl.HAVE_NP, "numpy")
    def test_three_streams_train_by_brain_share(self):
        m = small_model()
        breath.note_will("b1", {"Кирпичики": 1.0, "Помощь": 0.2, "Память": 0.2,
                                "Приветствие": 0.6, "Вопросы": 0.6})
        with patch.object(llmstreams, "texts", fake_texts), patch.object(codestream, "_rows", lambda: ROWS):
            for _ in range(4):
                codestream._fill(m)
                for k in llmstreams.TEXT_KEYS:
                    llmstreams._fill(m, k)
                for _ in range(12):
                    llmstreams.interleave(m, 1e-3)
            st = llmstreams._LIVE["steps"]
            self.assertTrue(all(st[k] > 0 for k in ("code", "max", "talk")), st)
            llmstreams._flush()
            stat = llmstreams.status()
        self.assertEqual({x["key"] for x in stat["streams"]}, {"code", "max", "talk", "admin"})

    def test_share_follows_brain(self):
        breath.note_will("b1", {"Кирпичики": 1.0, "Помощь": 0.0, "Память": 0.0,
                                "Приветствие": 0.5, "Вопросы": 0.5})
        class M:
            meta = {}
        with patch.object(llmstreams, "_ready", lambda k: True), \
                patch.object(llmstreams, "_step_text", lambda m, lr, k: 2.0), \
                patch.object(codestream, "_step", lambda m, lr: 2.0), \
                patch.object(llmstreams, "loss_limit", lambda m: 30):
            for _ in range(300):
                llmstreams.interleave(M(), 1e-3)
        st = llmstreams._LIVE["steps"]
        # доли 1.5 : 0.8*0.5 : 0.8*1.0 : 0.6*1.0 (админ: Помощь 0 + Кирпичики 1)
        self.assertAlmostEqual(st["code"] / 300, 1.5 / 3.3, delta=0.03)
        self.assertAlmostEqual(st["max"] / 300, 0.4 / 3.3, delta=0.03)
        self.assertAlmostEqual(st["admin"] / 300, 0.6 / 3.3, delta=0.03)

    def test_stream_off_and_revive(self):
        class M:
            steps = 10
            loss = 3.0
            class tok: size = 4000
        llmstreams._off("max", "loss 900", M())
        self.assertFalse(llmstreams.on("max"))
        self.assertTrue(llmstreams.on("talk"))
        M.steps = 400
        llmstreams._revive(M())
        self.assertTrue(llmstreams.on("max"))

    def test_migration_enables_once(self):
        store.update_settings({"llm_code_stream": False, "llm_streams_mig177": False})
        codestream._migrate177()
        self.assertTrue(store.settings()["llm_code_stream"])
        store.update_settings({"llm_code_stream": False})
        codestream._migrate177()                               # второй раз — не трогает
        self.assertFalse(store.settings()["llm_code_stream"])


if __name__ == "__main__":
    unittest.main()
