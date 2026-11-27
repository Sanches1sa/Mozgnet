import os, sys, tempfile, unittest
os.environ["NEURO_DATA"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "server"))
import store, pyapps, brickplan, applab_brain, applab_guide, project_brain, admincorpus, api_board  # noqa: E402


class Pa:
    def _event(self, *a, **k):
        pass


class Bricks230(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        store.ensure_dirs()
        store.update_settings({"web_on": False})

    def test_every_builtin_brick_passes_its_test(self):
        self.assertGreaterEqual(len(brickplan.B), 18)
        for b in brickplan.B:
            ok, err = brickplan.verify_brick(b)
            self.assertTrue(ok, "%s: %s" % (b["id"], err))

    def test_plans(self):
        cases = {
            "проверка инцидента: неудачные входы по ssh, больше 3 раз, сверить со списком блокировок":
                ["lines", "failed_logins", "count", "threshold", "blocklist", "report", "checklist"],
            "проверить ссылки в письме на фишинг": ["urls", "suspicious_urls", "report"],
            "проверь надёжность пароля": ["password"],
            "хронология событий в журнале и сохранить в базу знаний": ["lines", "timeline", "report", "save_kb"],
        }
        for t, want in cases.items():
            self.assertEqual(brickplan.plan(t)["chain"], want, t)
        for t in ("расскажи про кошек", "сделай отчёт", "хронология событий в журнале"):
            p = brickplan.plan(t)
            if t == "хронология событий в журнале":
                self.assertNotIn("save_kb", p["chain"])
            else:
                self.assertIsNone(p, t)

    def test_composed_app_runs_on_real_data_and_on_sample(self):
        c = brickplan.compose("проверка инцидента: неудачные входы по ssh больше 3 раз, сверить со списком блокировок")
        r = pyapps.run_code("x", c["code"], brickplan.AUTH_SAMPLE + "\nблок: 203.0.113.7")
        self.assertTrue(r["ok"], r)
        self.assertIn("203.0.113.7; n: 5; listed: True", r["result"]); self.assertIn("Сдержать", r["result"])
        self.assertNotIn("198.51.100.23", r["result"].split("Что делать")[0])          # 2 раза — ниже порога
        r = pyapps.run_code("x", c["code"], "проверь инцидент")
        self.assertIn("на образце", r["result"])

    def test_user_question_in_contour_builds_security_app(self):
        t = "Можешь написать приложение в апп лаб для проверки инцидента по информационной безопасности?"
        self.assertTrue(project_brain._APPLAB_TASK.search(t))
        s, _ = project_brain._run_applab({"task": t, "internet": False}, t, Pa(), Pa()._event)
        self.assertIn("загружено в App Lab", s)
        app = [a for a in pyapps.apps() if "инцидент" in a["name"].lower()][0]
        self.assertFalse(app["enabled"])
        self.assertTrue(app["name"].startswith("Проверки инцидента"))
        chk = applab_brain.check(app["id"], brickplan.AUTH_SAMPLE)
        self.assertTrue(chk["ok"], chk); self.assertIn("Что делать", chk["text"])

    def test_learn_from_enabled_app_and_reuse(self):
        code = 'def run(text, ctx):\n    """App Lab: курс кофе"""\n    return "кофе по 300"\n'
        pyapps.save({"id": "kofe_kurs", "name": "Цена кофе", "desc": "узнать цену кофе в кофейне", "triggers": [],
                     "code": code, "enabled": False}, origin="Star Gate")
        self.assertIsNone(applab_brain.learned_match("узнать цену кофе в кофейне у дома"))
        pyapps.set_flags("kofe_kurs", enabled=True)                 # владелец включил -> выучено
        lm = applab_brain.learned_match("узнать цену кофе в кофейне у дома")
        self.assertTrue(lm and lm[1]["id"] == "kofe_kurs")
        r = applab_brain.write("узнать цену кофе в кофейне у дома", internet=False)
        self.assertTrue(r["ok"]); self.assertIn("выученное приложение", r["base"])
        self.assertIn("кофе по 300", pyapps.get(r["id"], with_code=True)["code"])

    def test_learn_brick_from_enabled_app(self):
        code = ('def b_only_errors(x, ctx):\n    """Кирпичик: Только ошибки | этап: 3 | вход: lines | выход: lines | слова: ошибк, error"""\n'
                '    return [s for s in x if "error" in s.lower()]\n\n\ndef run(text, ctx):\n    return "ok"\n')
        pyapps.save({"id": "errs", "name": "Ошибки", "desc": "строки с ошибками", "code": code, "enabled": False}, origin="Star Gate")
        self.assertIsNone(brickplan.by_id("only_errors"))            # выключено — не берём
        pyapps.set_flags("errs", enabled=True)
        b = brickplan.by_id("only_errors")
        self.assertTrue(b and "выучен" in b["origin"])
        p = brickplan.plan("покажи ошибки из журнала")
        self.assertIn("only_errors", p["chain"])
        c = brickplan.compose("покажи ошибки из журнала")
        r = pyapps.run_code("y", c["code"], "ok\nERROR disk\nok")
        self.assertIn("ERROR disk", r["result"])

    def test_owner_fix_becomes_lesson(self):
        r = applab_brain.write("перевернуть строку для правки", kind="brick", internet=False)
        aid = r["id"]
        g = pyapps.get(aid, with_code=True)
        new = g["code"].replace("return s[::-1]", "return s[::-1].strip()")
        self.assertNotEqual(new, g["code"])
        pyapps.save({"id": aid, "code": new}, origin="владелец")
        fixes = applab_brain.fix_texts()
        self.assertTrue(fixes and "strip()" in fixes[-1])
        self.assertTrue(any("strip()" in t for t in admincorpus.applab_texts()))
        self.assertIn("Кирпичик:", g["code"])                        # кирпичик — в формате для библиотеки

    def test_chat_commands_and_api(self):
        self.assertIn("b_failed_logins", applab_brain.handle("какие кирпичики")[0])
        self.assertIn("Неудачные входы", applab_brain.handle("план: неудачные входы по ssh")[0])

        class R:
            method = "GET"; query = {"q": "фишинг ссылки"}; body = {}
        code, r = api_board.h_bricks(R())
        self.assertEqual(r["plan"]["chain"][-1], "report")
        R.query = {"verify": "1"}
        code, r = api_board.h_bricks(R())
        self.assertTrue(all(r["verify"].values()))

    def test_lessons_pack(self):
        ids = {x["id"] for x in applab_guide.load()}
        for i in ("bp.how", "bp.own", "sec.ssh", "sec.phish", "sec.ioc", "sec.password", "sec.ips", "log.timeline", "data.stats"):
            self.assertIn(i, ids)
        for it in applab_guide.load():
            ok, why = applab_guide.verify(it)
            self.assertTrue(ok, "%s: %s" % (it["id"], why))


if __name__ == "__main__":
    unittest.main()
