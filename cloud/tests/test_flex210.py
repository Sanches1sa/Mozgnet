import os, sys, tempfile, unittest
os.environ["NEURO_DATA"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "server"))
import store, llm_zones, thoughtextend  # noqa: E402


class Flex(unittest.TestCase):
    def setUp(self):
        store.ensure_dirs()

    def test_multi_zone_routing(self):
        z = llm_zones.route_multi("напиши код python и объясни почему так", k=3, rel=.4)
        self.assertGreaterEqual(len(z), 1)
        self.assertIn("Кирпичики", z)

    def test_flex_range_and_stagnation(self):
        for _ in range(24):
            llm_zones._RECENT.append("Знания")
        f = llm_zones.flexibility()
        self.assertTrue(0 <= f["flex"] <= 1)
        self.assertGreater(f["stagnation"], .9)
        base = f["base"]
        self.assertGreater(f["flex"], base)

    def test_condition_blends_zones(self):
        s = store.settings(); s["coupling_flex"] = 100; store.save_settings(s) if hasattr(store, "save_settings") else None
        t, z = llm_zones.condition("код python алгоритм и почему вопрос причина")
        self.assertTrue(t.startswith("Зона "))

    def test_extend_returns_dict(self):
        r = thoughtextend.extend("сеть нейронов думает", "что такое сеть")
        self.assertIn("ok", r)

if __name__ == "__main__":
    unittest.main()
