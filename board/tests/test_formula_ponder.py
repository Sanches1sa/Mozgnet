import ast, re, random, time, unittest
from pathlib import Path
SRC = (Path(__file__).resolve().parents[1] / "python/main.py").read_text(encoding="utf-8")
tree = ast.parse(SRC)
names = {"_formula_ponder", "_maths_load", "_math_pick", "_mm_eval", "_mm_num"}
body = []
for n in tree.body:
    if isinstance(n, ast.FunctionDef) and n.name in names:
        body.append(n)
    elif isinstance(n, ast.Assign) and any(getattr(t, "id", "") in {"_MATHS_BUILTIN", "_FORMULA_STATE", "_MM_NUM_RE", "_MM_PAIR2_RE", "_MATHS_DEFAULT"} or getattr(t, "id", "") in {"MATH_FORMULAS", "_MATH_PATH"} or getattr(t, "id", "").startswith("_MATHS") for t in n.targets):
        body.append(n)
mod = ast.Module(body=body, type_ignores=[])


def env(cfg=None):
    e = {"re": re, "random": random, "time": time, "os": __import__("os"), "json": __import__("json"),
         "math": __import__("math"), "ast": ast, "CFG": cfg or {}, "log": lambda *a: None,
         "_MM_NUM_RE": re.compile(r"-?\d+(?:[.,]\d+)?"), "_APP_DIR": "/nonexistent", "__file__": "/x/main.py", "_MATH_PATH": "/nonexistent/m.json", "__name__": "t"}
    try:
        exec(compile(mod, "m", "exec"), e)
    except NameError as ex:
        raise unittest.SkipTest("зависимости _mm_eval: %s" % ex)
    return e


class Ponder(unittest.TestCase):
    def test_topic_formula_is_pondered_with_real_calc(self):
        e = env()
        t = e["_formula_ponder"]("площадь круга", ["радиус"])
        self.assertIn("Обдумываю формулу", t)
        self.assertRegex(t, r"получается [\d.\-]+")

    def test_every_nth_thought_without_topic(self):
        e = env({"formula_every": 2})
        outs = [e["_formula_ponder"]("кошки и собаки", []) for _ in range(4)]
        self.assertEqual([bool(o) for o in outs], [False, True, False, True])

    def test_off(self):
        self.assertEqual(env({"formula_thoughts": False})["_formula_ponder"]("площадь круга", []), "")

if __name__ == "__main__":
    unittest.main()
