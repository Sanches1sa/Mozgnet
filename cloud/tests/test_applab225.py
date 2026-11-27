import os, sys, tempfile, unittest
os.environ["NEURO_DATA"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "server"))
import store, applab_guide, pyapps, admincorpus, api_board, brain  # noqa: E402

CODE = 'def run(text, ctx):\n    return "кофе готов: " + text\n'


class AppLab225(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        store.ensure_dirs()

    def test_library(self):
        st = applab_guide.status()
        self.assertGreaterEqual(st["count"], 25)
        self.assertGreaterEqual(st["verified"], 18)
        ids = [x["id"] for x in applab_guide.load()]
        self.assertEqual(len(ids), len(set(ids)))

    def test_examples_run(self):
        for it in applab_guide.load():
            ok, out = applab_guide.verify(it)
            self.assertTrue(ok, "%s: %s" % (it["id"], out))

    def test_match(self):
        cases = {"как написать функцию app lab": "fn.skeleton", "как выбрать триггеры функции": "fn.triggers",
                 "как вызвать кирпичик в функции": "brick.call", "цепочка кирпичиков": "brick.chain",
                 "как функция вызывается из чата": "chat.call"}
        for q, want in cases.items():
            m = applab_guide.match(q, 3)
            self.assertTrue(m and want in [i["id"] for _s, i in m], "%s -> %s" % (q, m))
        self.assertEqual(applab_guide.match("расскажи про кошек", 3), [])

    def test_corpus_kb_api(self):
        c = applab_guide.corpus()
        self.assertTrue(any("# task: App Lab:" in t for _s, t in c))
        self.assertGreaterEqual(len(admincorpus.applab_texts()), 40)
        self.assertGreaterEqual(applab_guide.seed_kb(), 25)

        class R:
            method = "GET"; query = {"id": "fn.state", "verify": "1"}; body = {}
        code, r = api_board.h_applab_guide(R())
        self.assertTrue(r["verified"])

    def test_every_code_passes_check_code(self):
        for it in applab_guide.load():
            if it.get("code"):
                self.assertEqual(pyapps.check_code(it["code"]), "", it["id"])

    def test_real_ctx_bricks_chain(self):
        code = ('def run(text, ctx):\n    ctx.brick_reset()\n    ctx.brick("logic.template", template=text.upper())\n'
                '    return ctx.brick("logic.if", needle="СРОЧНО", then="срочно!", **{"else": "обычно"})\n')
        r = pyapps.run_code("t1", code, "это срочно")
        self.assertTrue(r["ok"], r); self.assertEqual(r["result"], "срочно!")
        r = pyapps.run_code("t1", 'def run(text, ctx):\n    return str(len(ctx.bricks()) > 20)\n', "x")
        self.assertEqual(r["result"], "True")

    def test_chat_calls_function(self):
        res = pyapps.save({"id": "kofe", "name": "Кофемашина", "triggers": ["сварить кофе"],
                           "code": CODE, "enabled": True})
        self.assertTrue(res["ok"], res)
        # по триггеру
        reply, _z, _i = brain.think("сварить кофе пожалуйста", chat_id="t", sender="владелец")
        self.assertIn("кофе готов", reply)
        # по имени, без триггера
        pyapps.save({"id": "tea", "name": "Чайник", "triggers": [], "code": 'def run(text, ctx):\n    return "чай"\n'})
        reply, _z, _i = brain.think("запусти функцию Чайник", chat_id="t", sender="владелец")
        self.assertEqual(reply.strip(), "чай")
        reply, _z, _i = brain.think("вызови приложение tea", chat_id="t", sender="владелец")
        self.assertEqual(reply.strip(), "чай")

    def test_disabled_not_run(self):
        pyapps.save({"id": "boardapp", "name": "Платный", "triggers": [], "enabled": False,
                     "code": 'def run(text, ctx):\n    return "ЗАПУЩЕНО"\n'})
        reply, _z, _i = brain.think("запусти функцию Платный", chat_id="t", sender="владелец")
        self.assertIn("выключено", reply); self.assertNotIn("ЗАПУЩЕНО", reply)

    def test_codewrite_answers_applab_question(self):
        import codewrite
        from unittest import mock
        with mock.patch.object(codewrite, "own_write", return_value=None):
            t = codewrite.compose("как написать функцию app lab на python")
        self.assertIn("def run(text, ctx)", t)


if __name__ == "__main__":
    unittest.main()
