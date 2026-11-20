"""v3.187: своя LLM подкрепляет факты в интернете (llmfact) — без сети,
источники подменены."""
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "server"))
os.environ.setdefault("NEURO_DATA", tempfile.mkdtemp())
import store  # noqa: E402
store.DATA_DIR = os.environ["NEURO_DATA"]
import llmfact  # noqa: E402
import readlike  # noqa: E402
import webfetch  # noqa: E402
import strict  # noqa: E402

ART = ("Эйфелева башня — металлическая башня в центре Парижа, самая узнаваемая его "
       "архитектурная достопримечательность. Названа в честь главного конструктора "
       "Гюстава Эйфеля. Башня построена в 1889 году как вход на Всемирную выставку. "
       "Высота башни составляет 330 метров вместе с антенной. Ежегодно её посещают "
       "около семи миллионов человек. " * 2)


class T(unittest.TestCase):
    def setUp(self):
        readlike.fetch_article = lambda q: ({"title": "Эйфелева башня", "text": ART,
                                             "src": "Википедия"}, "")
        webfetch.engine_snips = lambda q, limit=4: [
            {"title": "Эйфелева башня — факты", "url": "https://example.org/eiffel",
             "snippet": "Эйфелева башня построена в 1889 году инженером Гюставом Эйфелем.",
             "src": "example.org"}]
        strict.online = lambda: True
        store.update_settings({"web_on": True, "llm_web_check": True})

    def test_need(self):
        self.assertTrue(llmfact.need("Когда построили Эйфелеву башню?")[0])
        self.assertTrue(llmfact.need("проверь, это правда?", "")[0])
        self.assertFalse(llmfact.need("привет, как дела")[0])
        self.assertTrue(llmfact.need("ну да", "Башня построена в 1889 году в Париже.")[0])

    def test_check_confirms_and_differs(self):
        ans = ("Эйфелева башня построена в 1889 году как вход на Всемирную выставку. "
               "Высота башни составляет 500 метров вместе с антенной. "
               "Башня сделана из шоколада и светится зелёным каждую ночь.")
        out, wc = llmfact.augment("Когда построили Эйфелеву башню?", ans)
        self.assertIn("**Проверка в интернете**", out)
        self.assertIn("✓ подтверждено", out)
        self.assertIn("≠ источник пишет иначе", out)          # 500 vs 330
        self.assertIn("330", out)
        self.assertIn("? подтверждения не нашла", out)
        self.assertIn("https://ru.wikipedia.org/wiki/Эйфелева_башня", out)
        self.assertEqual((wc["ok"], wc["diff"], wc["no"]), (1, 1, 1))
        import llmweb
        self.assertTrue(any("Эйфелева" in c for c in llmweb.corpus()))  # в обучение

    def test_talk_plain(self):
        out, wc = llmfact.augment("Когда построили Эйфелеву башню?",
                                  "Эйфелева башня построена в 1889 году как вход на Всемирную выставку.",
                                  talk=True)
        t = wc["text"]
        self.assertTrue(t.startswith("Проверила в интернете"))
        self.assertNotIn("**", t)
        self.assertIn("подтверждает", t)

    def test_no_sources(self):
        readlike.fetch_article = lambda q: (None, "нет статьи")
        webfetch.engine_snips = lambda q, limit=4: []
        out, wc = llmfact.augment("Когда построили Эйфелеву башню?", "Давно.")
        self.assertIn("Подкрепить ответ не вышло", out)
        self.assertFalse(wc["found"])

    def test_off(self):
        store.update_settings({"llm_web_check": False})
        out, wc = llmfact.augment("Когда построили Эйфелеву башню?", "Давно.")
        self.assertEqual(out, "Давно.")
        self.assertIsNone(wc)

    def test_offline(self):
        strict.online = lambda: False
        out, wc = llmfact.augment("Когда построили Эйфелеву башню?", "Давно.")
        self.assertEqual(out, "Давно.")



class Integration(unittest.TestCase):
    def setUp(self):
        T.setUp(self)

    def test_talk_mode_adds_message(self):
        import talkmind
        talkmind._brain_thought = lambda text: ("", "")
        talkmind._llm_continue = lambda prompt, a: (
            "Эйфелева башня построена в 1889 году как вход на Всемирную выставку")
        talkmind._acceptable = lambda t, own=False: True
        parts, info = talkmind.respond_parts("Когда построили Эйфелеву башню?")
        self.assertEqual(len(parts), 2)
        self.assertTrue(parts[1].startswith("Проверила в интернете"))
        self.assertNotIn("«", parts[1])
        self.assertFalse(info["no_facts"])
        self.assertEqual(info["web_check"]["ok"], 1)

    def test_talk_smalltalk_untouched(self):
        import talkmind
        talkmind._brain_thought = lambda text: ("", "")
        talkmind._llm_continue = lambda prompt, a: "Всё хорошо, думаю о свете и звуке"
        talkmind._acceptable = lambda t, own=False: True
        parts, info = talkmind.respond_parts("привет, как дела")
        self.assertEqual(len(parts), 1)
        self.assertNotIn("web_check", info)

    def test_brain_authored(self):
        import brain
        Z = brain.Z
        self.assertTrue(brain._llm_authored(Z["membrain"], {"llm": True}))
        self.assertTrue(brain._llm_authored(Z["speech"], {}))
        self.assertFalse(brain._llm_authored(Z["kb"], {"llm": True}))
        self.assertFalse(brain._llm_authored(Z["web"], {}))
        self.assertFalse(brain._llm_authored(Z["membrain"], {"llm": True, "src_zone": Z["kb"]}))
        self.assertFalse(brain._llm_authored(Z["talk"], {"offline": True}))


if __name__ == "__main__":
    unittest.main()
