import os, sys, tempfile, unittest
from unittest import mock
os.environ["NEURO_DATA"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "server"))
import store, pyapps, brickplan, applab_brain, api_board, boardcode  # noqa: E402

GOOD = {"id": "only_errors", "title": "Только ошибки", "stage": 3, "in": "lines", "out": "lines", "words": "ошибк, error, сбой",
        "code": 'def b_only_errors(x, ctx):\n    return [s for s in x if "error" in s.lower()]\n',
        "test": 'assert b_only_errors(["ok", "ERROR x"], None) == ["ERROR x"]', "sample": "ok\nERROR disk\nok"}


class R:
    def __init__(self, body=None, query=None, method="POST"):
        self.body, self.query, self.method = body or {}, query or {}, method


class Bricks234(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        store.ensure_dirs()
        store.update_settings({"web_on": False})

    def test_builtin_incl_hardware_pass_tests(self):
        for b in brickplan.B:
            ok, err = brickplan.verify_brick(b)
            self.assertTrue(ok, "%s: %s" % (b["id"], err))
        self.assertTrue({"hw_read", "hw_set", "hw_rule"} <= {b["id"] for b in brickplan.B})

    def test_brick_tool_validation_and_save(self):
        self.assertIn("встроенный", brickplan.check_brick(dict(GOOD, id="lines"))[1])
        self.assertIn("b_", brickplan.check_brick(dict(GOOD, code="def f(x, ctx):\n    return x\n"))[1])
        self.assertIn("запрещённое", brickplan.check_brick(dict(GOOD, code=GOOD["code"] + "import subprocess\n"))[1])
        self.assertIn("этап", brickplan.check_brick(dict(GOOD, stage=9))[1])
        r = brickplan.save_user_brick(dict(GOOD, test="assert b_only_errors(['ok'], None) == ['ok']"))
        self.assertFalse(r["ok"]); self.assertIn("тест", r["error"])
        code, r = api_board.h_bricks(R(dict(GOOD, action="save")))
        self.assertEqual(code, 200, r)
        b = brickplan.by_id("only_errors")
        self.assertTrue(b and b["origin"].startswith("свой"))
        p = brickplan.plan("покажи ошибки из журнала")
        self.assertIn("only_errors", p["chain"])
        c = brickplan.compose("покажи ошибки из журнала")
        out = pyapps.run_code("x", c["code"], "ok\nERROR disk\nok")
        self.assertIn("ERROR disk", out["result"])
        self.assertTrue(api_board.h_bricks(R({"action": "delete", "id": "only_errors"}))[1]["ok"])
        self.assertIsNone(brickplan.by_id("only_errors"))

    def test_autogen_templates_valid_and_not_mixed(self):
        r = brickplan.autogen()
        self.assertGreater(r["made"], 30)
        ts = brickplan.templates()
        for t in ts:
            doms = {brickplan.DOMAIN.get(c) for c in t["chain"]} - {None}
            self.assertLessEqual(len(doms - {"data"} if "hw" in doms else doms), 1, t["title"])   # ИБ и железо не смешаны
        self.assertTrue(any(t["chain"][:2] == ["hw_read", "hw_rule"] for t in ts))
        self.assertTrue(any("suspicious_urls" in t["chain"] for t in ts))
        self.assertFalse(any(t["chain"][0] == "hw_read" and "suspicious_urls" in t["chain"] for t in ts))
        t = next(t for t in ts if "suspicious_urls" in t["chain"])
        code, r = api_board.h_bricks(R({"action": "template_app", "id": t["id"]}))
        self.assertTrue(r["ok"], r); self.assertFalse(pyapps.get(r["id"])["enabled"])

    def test_hardware_plans(self):
        self.assertEqual(brickplan.plan("если показания датчика больше 600 включи D7")["chain"], ["hw_read", "hw_rule"])
        self.assertEqual(brickplan.plan("включи светодиод на D7")["chain"], ["hw_set"])
        self.assertEqual(brickplan.plan("проверка инцидента: неудачные входы по ssh, больше 3 раз, сверить со списком блокировок")["chain"],
                         ["lines", "failed_logins", "count", "threshold", "blocklist", "report", "checklist"])

    def test_hardware_app_goes_through_board_job(self):
        r = applab_brain.write("если показания датчика больше 600 включи D7", internet=False)
        self.assertTrue(r["ok"], r)
        sent = []

        def fake_run(code, mode="py_check", board="", timeout=20, wait_s=None):
            sent.append((mode, code))
            return {"ok": True, "out": "\n".join("%s → ok:%s" % (c.strip(), "700" if c.strip().startswith("a ") else "1")
                                                 for c in code.split(";"))}
        with mock.patch.object(boardcode, "run", fake_run):
            out = pyapps.run_code(r["id"], pyapps.get(r["id"], with_code=True)["code"], "если больше 600 включи D7")
        self.assertTrue(out["ok"], out)
        self.assertEqual([m for m, _ in sent], ["hw", "hw"]); self.assertTrue(sent[0][1].startswith("a A5")); self.assertEqual(sent[1][1], "w D7 1")
        self.assertIn("D7 = 1", out["result"])

    def test_boardcode_hw_mode_needs_capable_board(self):
        r = boardcode.submit("w D7 1", mode="hw")
        self.assertFalse(r["ok"]); self.assertIn("2.111", r["error"])
        boardcode.note_link("uno", ver="2.111.0", codejobs={"code": True, "admin": True, "hw": True})
        r = boardcode.submit("w D7 1", mode="hw")
        self.assertTrue(r["ok"]); self.assertEqual(boardcode.take("uno")[0]["mode"], "hw")

    def test_brain_written_brick_enters_library(self):
        r = applab_brain.write("перевернуть строку", kind="brick", internet=False)
        self.assertTrue(r["ok"], r); self.assertIn("добавлен в библиотеку", r["text"])
        self.assertTrue(any(b["origin"].startswith("свой: Star Gate") for b in brickplan.library()))

    def test_chat_templates_commands(self):
        self.assertIn("Сгенерировала шаблоны", applab_brain.handle("сгенерируй шаблоны")[0])
        self.assertIn("Шаблоны-цепочки", applab_brain.handle("шаблоны")[0])


if __name__ == "__main__":
    unittest.main()
