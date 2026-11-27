import ast, re, threading, time, unittest
from pathlib import Path
from unittest.mock import Mock
SRC = (Path(__file__).resolve().parents[1] / "python/main.py").read_text(encoding="utf-8")
tree = ast.parse(SRC)
want = {"_gen_rebuild", "_pick_next_sentence_seed"}
mod = ast.Module(body=[n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in want], type_ignores=[])
TEXT = ("Нейроны сети передают слова по шине. Слова образуют связи в памяти. "
        "Связи в памяти хранят опыт. Нейроны сети передают слова по шине быстро. "
        "Связи в памяти хранят опыт долго.")


def env():
    st = {"big": {}, "uni": {}, "tri": {}, "snext": {}}
    e = {"re": re, "time": time, "_GEN_STATE": st, "_GEN_FUNC": {"в", "по", "и"}, "TRI_MAX": 250000,
         "SH": Mock(lock=threading.RLock(), docs=[]), "_READ": {"items": []}, "_READ_LOCK": threading.RLock(),
         "_read_load": lambda: None, "neural_bridge": Mock(), "_gen_texts": lambda: [TEXT],
         "_norm_word": lambda w: w, "dict_has": lambda w: True, "log": lambda *a: None}
    exec(compile(mod, "m", "exec"), e)
    return e, st


class Tri(unittest.TestCase):
    def test_trigrams_and_sentence_transitions(self):
        e, st = env()
        e["_gen_rebuild"]()
        self.assertIn("нейроны сети", st["tri"])
        self.assertEqual(set(st["tri"]["нейроны сети"]), {"передают"})
        self.assertIn("сети передают", st["tri"])
        # после предложения про шину в тексте идёт «Слова…»/«Связи…»: переходы собраны
        self.assertTrue(st["snext"])
        self.assertEqual(st["snext"]["памят"], {"связи": 1})
        self.assertEqual(st["snext"]["шине"], {"слова": 1})

    def test_pick_next_seed_follows_statistics(self):
        e, st = env()
        st["snext"] = {"шине": {"связи": 5, "слова": 1}}
        pending = ["слова", "связи", "опыт"]
        got = e["_pick_next_sentence_seed"]("Нейроны передают слова по шине.", pending)
        self.assertEqual(got, "связи")
        self.assertNotIn("связи", pending)
        # нет статистики — по очереди
        st["snext"] = {}
        self.assertEqual(e["_pick_next_sentence_seed"]("Что-то другое.", ["a", "b"]), "a")

if __name__ == "__main__":
    unittest.main()
