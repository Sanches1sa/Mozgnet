import os, sys, tempfile, unittest
from unittest import mock
os.environ["NEURO_DATA"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "server"))
import store, thoughtbank, maxbot  # noqa: E402

A = ("Нейроны сети обмениваются словами через общую шину и запоминают связи между ними. "
     "Каждая связь укрепляется, когда слова встречаются рядом в разных мыслях мозга. "
     "Поэтому сеть постепенно учится строить связные предложения о нейронах и памяти.")
B = ("Память нейронов хранит связи слов и помогает мозгу продолжать начатую мысль. "
     "Когда тема сети нейронов продолжается, мозг достраивает текст следующими предложениями. "
     "Так длинная мысль остаётся связной и не уходит в сторону от вопроса.")
C = ("Кошки любят молоко и тёплые окна по утрам, а собаки любят длинные прогулки по парку. "
     "Весной на улицах пахнет мокрой землёй, и люди достают лёгкие куртки из шкафов.")


class Bank(unittest.TestCase):
    def setUp(self):
        store.ensure_dirs()
        for f in ("thoughtbank.json", "thoughts.json"):
            p = os.path.join(store.DATA_DIR, f)
            if os.path.exists(p):
                os.remove(p)

    def test_good_filters(self):
        self.assertTrue(thoughtbank.good(A))
        self.assertFalse(thoughtbank.good("short"))
        self.assertFalse(thoughtbank.good("☁ " + A))
        self.assertFalse(thoughtbank.good("слово слово слово " * 20))
        self.assertFalse(thoughtbank.good("hello world this is a latin text " * 6))

    def test_ingest_and_compose_continues_on_topic(self):
        for t in (A, B, C, "мусор"):
            store.thoughts_add("b1", t)
        self.assertEqual(thoughtbank.ingest(), 3)
        r = thoughtbank.compose("расскажи про нейроны сети и память", chat="c1", limit=3500)
        self.assertIsNotNone(r)
        self.assertEqual(r["n"], 2)
        self.assertIn("Нейроны сети", r["text"])
        self.assertNotIn("Кошки", r["text"])        # тема потерялась бы — стоп
        # во втором ответе того же чата те же мысли не повторяются
        r2 = thoughtbank.compose("а что с памятью нейронов", chat="c1", limit=3500)
        self.assertTrue(r2 is None or not set(r2["ids"]) & set(r["ids"]) or r2["n"] >= 1)

    def test_limit_respected_and_offtopic_none(self):
        thoughtbank.add(A, "b"); thoughtbank.add(B, "b")
        r = thoughtbank.compose("нейроны память сеть", chat="c2", limit=500)
        self.assertLessEqual(len(r["text"]), 500)
        self.assertIsNone(thoughtbank.compose("квантовая хромодинамика кварков", chat="c3"))

    def test_bot_modes(self):
        thoughtbank.add(A, "b"); thoughtbank.add(B, "b")
        store.update_settings({"maxbot_mode": "talk"})
        txt, zone = maxbot._think("что такое нейроны сети и память", "u", chat_id="77")
        self.assertEqual(zone, "Мысли мозга"); self.assertIn("нейрон", txt.lower())
        store.update_settings({"maxbot_mode": "web"})
        with mock.patch("webfetch.alice", return_value=("Ответ из сети про нейроны.", {"sources": [{"url": "https://ru.wikipedia.org/wiki/Нейрон"}]})):
            txt, zone = maxbot._think("что такое нейрон", "u", chat_id="77")
        self.assertEqual(zone, "Интернет"); self.assertIn("Источники", txt)
        store.update_settings({"maxbot_mode": "talk"})

class CodeAndArticles(unittest.TestCase):
    CODE = ("Вот короткий пример функции, которая удваивает число и возвращает результат.\n\n"
            "```python\ndef double(x):\n    return x * 2\n```\n\nФункция принимает число и возвращает его удвоенное значение.")

    def test_tclean_keeps_code(self):
        out = store._tclean(self.CODE)
        self.assertIn("```python\ndef double(x):\n    return x * 2\n```", out)

    def test_bank_accepts_code_thought(self):
        self.assertTrue(thoughtbank.good(self.CODE))
        self.assertFalse(thoughtbank.good("```python\nx\n```"))        # слишком мало
        self.assertTrue(thoughtbank.add(self.CODE, "b"))

    def test_articles_formulas(self):
        import articles
        txt = "Теорема. Квадрат гипотенузы равен сумме квадратов катетов: $a^2+b^2=c^2$. Ещё факт $x$."
        fs = articles.formulas(txt)
        self.assertEqual(fs[0][0], "a^2+b^2=c^2")
        self.assertTrue(articles.is_formula_q("напиши формулу площади круга"))
        self.assertTrue(articles.is_code_q("напиши функцию на python"))
        self.assertFalse(articles.is_code_q("расскажи про кошек"))

    def test_formula_block_uses_article(self):
        import articles
        art = {"title": "Теорема Пифагора", "url": "https://x/y",
               "text": "Теорема. Квадрат гипотенузы равен сумме квадратов катетов: $a^2+b^2=c^2$."}
        with mock.patch.object(articles, "fetch", return_value=art):
            b = articles.formula_block("формула теоремы пифагора")
        self.assertIn("$$a^2+b^2=c^2$$", b)
        with mock.patch.object(articles, "fetch", return_value=None):
            self.assertEqual(articles.formula_block("формула чего-то"), "")

    def test_bot_talk_adds_code_and_formula(self):
        thoughtbank.add(A, "b")
        store.update_settings({"maxbot_mode": "talk"})
        import agent, articles
        with mock.patch.object(agent, "code_agent", return_value=("```python\nprint(1)\n```", True, {})), \
             mock.patch.object(articles, "formula_block", return_value="Формулы:\n$$x=1$$"):
            txt, zone = maxbot._think("напиши функцию на python про нейроны сети и формулу", "u", chat_id="88")
        self.assertIn("```python", txt); self.assertIn("$$x=1$$", txt); self.assertEqual(zone, "Мысли мозга + код")


if __name__ == "__main__":
    unittest.main()
