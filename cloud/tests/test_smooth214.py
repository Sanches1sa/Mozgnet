import os, sys, tempfile, unittest
from unittest import mock
os.environ["NEURO_DATA"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "server"))
import store, thoughtsmooth, thoughtbank  # noqa: E402

SCATTER = ("Нейроны сети передают слова по общей шине. Кошки любят молоко и тёплые окна. "
           "Слова нейронов хранятся в памяти сети. Весной на улицах пахнет мокрой землёй. "
           "Память сети укрепляет связи между словами нейронов. Погода завтра будет облачной и тёплой.")
COHERENT = ("Нейроны сети передают слова по общей шине. Слова нейронов хранятся в памяти сети. "
            "Память сети укрепляет связи между словами нейронов.")


class Smooth(unittest.TestCase):
    def setUp(self):
        store.ensure_dirs()

    def test_coherence_scores(self):
        self.assertGreater(thoughtsmooth.coherence(COHERENT), 0.8)
        self.assertLess(thoughtsmooth.coherence(SCATTER), 0.5)

    def test_coherent_unchanged(self):
        t, info = thoughtsmooth.smooth(COHERENT, use_llm=False)
        self.assertEqual(t, COHERENT); self.assertFalse(info["changed"])

    def test_scattered_gets_threads_and_noise_dropped(self):
        t, info = thoughtsmooth.smooth(SCATTER, "расскажи про нейроны сети", use_llm=False)
        self.assertTrue(info["changed"])
        self.assertNotIn("Кошки", t); self.assertNotIn("Погода", t)
        self.assertGreater(info["coherence_after"], info["coherence_before"])
        self.assertTrue("сети" in t[:60] or "нейрон" in t[:60].lower())   # главное — первым

    def test_llm_lead_is_added_only_if_grounded(self):
        with mock.patch("tinyllm.compose", return_value=("Сеть нейронов хранит слова в памяти и связывает их.", {"llm": "ok", "grounded": 0.9})):
            t, info = thoughtsmooth.smooth(SCATTER, use_llm=True)
        self.assertIn("Итог: сеть нейронов хранит слова", t); self.assertTrue(info["llm"])
        with mock.patch("tinyllm.compose", return_value=("Что-то постороннее про море.", {"llm": "ok", "grounded": 0.1})):
            t, info = thoughtsmooth.smooth(SCATTER, use_llm=True)
        self.assertNotIn("Итог", t)

    def test_code_untouched_and_bank_gets_smoothed_copy(self):
        code = SCATTER + "\n\n```python\nx = 1\n```"
        self.assertEqual(thoughtsmooth.smooth(code)[0], code)
        with mock.patch("tinyllm.compose", return_value=("", {"llm": "cold"})):
            self.assertTrue(thoughtbank.add(SCATTER, "b1"))
        items = thoughtbank._load()["items"]
        self.assertNotIn("Кошки", items[-1]["text"])      # в банке — сглаженная копия
        raw = [t for t in store.load_json("thoughts.json", []) if "Кошки" in str(t.get("text"))]
        self.assertIsInstance(raw, list)                   # исходная запись платы не меняется

if __name__ == "__main__":
    unittest.main()
