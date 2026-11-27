import ast, re, random, time, unittest, threading
from pathlib import Path
from unittest.mock import Mock
SRC = (Path(__file__).resolve().parents[1] / "python/main.py").read_text(encoding="utf-8")
tree = ast.parse(SRC)
want = {"_loop_transfer", "_question_text", "_cloud_extend"}
mod = ast.Module(body=[n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in want], type_ignores=[])


def env(big, cfg=None):
    e = {"re": re, "random": random, "time": time, "LOOP_TURNS": 37, "THOUGHT_CHARS": 6000,
         "_LOOP": {}, "_GEN_FUNC": {"и", "в"}, "CFG": cfg or {}, "SH": Mock(lock=threading.RLock(), memory=[]),
         "dict_has": lambda w: True, "gen_pen": lambda a, b: 1.0,
         "sdict": lambda: Mock(successors=lambda cur, accept=None: []),
         "_GEN_STATE": {"big": big}, "_neuron_context": lambda: {"words": [("сеть", 1)]},
         "_gen_pool": lambda n: ["слово", "мысль"], "log": print,
         "scrub_thought": lambda t: t, "_cloud_base": lambda: ("", ""),
         "_http_json_err": Mock(), "_hostname": lambda: "b"}
    exec(compile(mod, "m", "exec"), e)
    return e


class Loop(unittest.TestCase):
    def test_37_turns_and_question_bias(self):
        words = ["вода", "река", "море", "берег", "песок", "волна", "ветер", "парус"]
        big = {w: {words[(i + 1) % 8]: 3, "сеть": 1} for i, w in enumerate(words)}
        big["сеть"] = {"вода": 2}
        e = env(big)
        chain = e["_loop_transfer"]("сеть", "расскажи про реку и море")
        self.assertEqual(len(chain), 37)
        self.assertEqual(e["_LOOP"]["turn"], 37)
        self.assertIn("река", chain)

    def test_jumps_when_no_successors(self):
        chain = env({})["_loop_transfer"]("слово", "")
        self.assertEqual(len(chain), 37)

    def test_extend_off_without_cloud(self):
        self.assertEqual(env({})["_cloud_extend"]("текст", "q"), "")

if __name__ == "__main__":
    unittest.main()
