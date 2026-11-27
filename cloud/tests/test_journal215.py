import os, sys, tempfile, unittest
from unittest import mock
os.environ["NEURO_DATA"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "server"))
import store, thoughtsmooth, thoughtbank, thoughtjournal, llmlink  # noqa: E402

SCATTER = ("Нейроны сети передают слова по общей шине. Кошки любят молоко и тёплые окна. "
           "Слова нейронов хранятся в памяти сети. Весной на улицах пахнет мокрой землёй. "
           "Память сети укрепляет связи между словами нейронов. Погода завтра будет облачной и тёплой. "
           "Шина передаёт слова между нейронами сети быстро.")
COHERENT = ("Нейроны сети передают слова по общей шине. Слова нейронов хранятся в памяти сети. "
            "Память сети укрепляет связи между словами нейронов. Шина сети передаёт слова нейронов дальше. "
            "Связи между нейронами сети растут при повторении слов.")


class J(unittest.TestCase):
    def setUp(self):
        store.ensure_dirs(); thoughtjournal.clear()

    def test_coherent_brain_kept_and_logged(self):
        t, info = thoughtsmooth.smooth(COHERENT, board="b1", log=True)
        self.assertEqual(t, COHERENT)
        r = thoughtjournal.recent(5)
        self.assertEqual((r[0]["who"], r[0]["act"]), ("мозг", "kept"))
        self.assertIn("LLM не нужна", r[0]["note"])

    def test_scattered_llm_assembles_with_several_leads_and_logs(self):
        outs = iter([("Сеть нейронов хранит слова в памяти и связывает их.", {"llm": "ok", "grounded": 0.9}),
                     ("Шина передаёт слова между нейронами сети очень быстро.", {"llm": "ok", "grounded": 0.9})] * 3)
        with mock.patch("tinyllm.compose", side_effect=lambda *a, **k: next(outs)):
            t, info = thoughtsmooth.smooth(SCATTER, "нейроны сети", board="b1", log=True)
        self.assertTrue(info["changed"]); self.assertGreaterEqual(info["leads"], 1)
        self.assertNotIn("Кошки", t)
        e = thoughtjournal.recent(1)[0]
        self.assertEqual((e["who"], e["act"]), ("LLM", "assembled"))
        self.assertGreater(e["coh1"], e["coh0"]); self.assertIn("Итог", e["out"])

    def test_cold_llm_failure_reason_is_logged(self):
        with mock.patch("tinyllm.compose", return_value=("", {"llm": "cold"})):
            t, info = thoughtsmooth.smooth(SCATTER, board="b1", log=True)
        e = thoughtjournal.recent(1)[0]
        self.assertEqual(e["who"], "LLM")
        self.assertTrue(e["act"] in ("assembled", "failed"))
        self.assertIn("не обучена", e["note"] if e["act"] == "failed" else e["note"] + "не обучена")

    def test_bank_no_double_journal_on_ingest(self):
        with mock.patch("tinyllm.compose", return_value=("", {"llm": "cold"})):
            thoughtbank.add(COHERENT, "b1")
            store.thoughts_add("b1", COHERENT, "say")
            thoughtbank.ingest()
        n = len([i for i in thoughtjournal.recent(50) if i["who"] == "мозг" and i["act"] == "kept"])
        self.assertEqual(n, 1)

    def test_llm_own_thought_logged_and_grown(self):
        seq = iter(["Первое предложение про сеть нейронов и память слов.",
                    "Второе предложение рассказывает о шине данных между платами.",
                    "Третье предложение объясняет как растут связи при повторении."] * 5)
        store.settings()["llm_thought_chars"] = 150
        with mock.patch("tinyllm.sample", side_effect=lambda *a, **k: (next(seq), {})), \
             mock.patch.object(llmlink, "_quality", return_value=0.9):
            g = llmlink._grow("Начальная мысль про нейроны сети и их слова.")
        self.assertGreater(len(g), 120); self.assertGreaterEqual(g.count("."), 3)
        with mock.patch.object(llmlink, "_quality", return_value=0.9):
            llmlink.publish_thought(g, "тема", source="сама", quality=0.9)
        own = [i for i in thoughtjournal.recent(10) if i["act"] == "own"]
        self.assertTrue(own and own[0]["chars_out"] >= 120)



class Cold(unittest.TestCase):
    def test_cold_model_still_assembles_by_rules(self):
        thoughtjournal.clear()
        with mock.patch("tinyllm.compose", return_value=("", {"llm": "cold"})):
            t, info = thoughtsmooth.smooth(SCATTER, "нейроны сети", board="b1", log=True)
        self.assertTrue(info["changed"], info)
        self.assertEqual(info["mode"], "правила")
        self.assertGreater(info["coherence_after"], info["coherence_before"])
        e = thoughtjournal.recent(1)[0]
        self.assertEqual(e["act"], "assembled"); self.assertIn("правилами", e["note"])

    def test_chain_orders_sentences_by_overlap(self):
        st = [{"аа", "бб"}, {"вв", "гг"}, {"аа", "вв"}, {"бб", "дд"}]
        out = thoughtsmooth._chain([0, 1, 2, 3], st, set())
        self.assertEqual(sorted(out), [0, 1, 2, 3])
        self.assertTrue(st[out[0]] & st[out[1]])


if __name__ == "__main__":
    unittest.main()
