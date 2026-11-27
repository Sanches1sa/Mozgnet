import os, sys, tempfile, unittest
os.environ["NEURO_DATA"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "server"))
import store, pyapps, applab_brain, applab_guide, project_brain, brain  # noqa: E402


class Pa:
    def __init__(self):
        self.events = []

    def _event(self, job, status, detail="", kind="action"):
        self.events.append((status, detail))


class Graph229(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        store.ensure_dirs()
        store.update_settings({"web_on": False})

    def test_write_graph_app_into_applab(self):
        r = applab_brain.write("Напиши навык для апп лаб : рисовать графики функций.".split(":", 1)[1], internet=False)
        self.assertTrue(r["ok"], r)
        self.assertIn("графики функций", r["base"].lower() + r["text"].lower())
        m = pyapps.get(r["id"], with_code=True)
        self.assertFalse(m["enabled"]); self.assertIn("ctx.draw.graph", m["code"])
        self.assertIn("график", m["triggers"])
        self.assertTrue(pyapps.get(r["id"]))                    # лежит в среде App Lab
        self.aid = r["id"]
        # живой запуск с настоящим ctx: приходит SVG
        for phrase, want in (("график y=x^2 от -3 до 3", "y = x^2"), ("построй график sin(x)", "sin(x)"), ("нарисуй график y=1/x", "1/x")):
            c = applab_brain.check(r["id"], phrase)
            self.assertTrue(c["ok"], c); self.assertIn("<svg", c["text"] + "")
        c = applab_brain.check(r["id"], "график y=x^2 от -3 до 3")
        self.assertIn("polyline", c["text"]); self.assertIn("[/КАРТИНКА]", c["text"])      # картинка не обрезана

    def test_real_run_returns_svg_and_bad_expr_is_honest(self):
        r = pyapps.run_code("g1", applab_guide.load()[[x["id"] for x in applab_guide.load()].index("fn.graph")]["code"], "график y=x^2 от -3 до 3")
        self.assertTrue(r["ok"], r); self.assertIn("[КАРТИНКА]<svg", r["result"]); self.assertIn("polyline", r["result"])
        r = pyapps.run_code("g1", applab_guide.load()[[x["id"] for x in applab_guide.load()].index("fn.graph")]["code"], "график y=import_os")
        self.assertTrue(r["ok"]); self.assertIn("Не получилось", r["result"])
        r = pyapps.run_code("g1", applab_guide.load()[[x["id"] for x in applab_guide.load()].index("fn.graph")]["code"], "график")
        self.assertIn("Скажите", r["result"])

    def test_enable_then_chat_draws_and_delete_removes(self):
        r = applab_brain.write("рисовать графики функций ещё раз", internet=False)
        aid = r["id"]
        self.assertIn("включено", applab_brain.handle("включи " + aid)[0])
        reply, _z, _i = brain.think("построй график y=x^3 от -2 до 2", chat_id="t", sender="владелец")
        self.assertIn("svg", reply.lower())
        self.assertIn("выключено", applab_brain.handle("выключи " + aid)[0])
        out = applab_brain.handle("удали приложение " + aid)
        self.assertIn("удалено", out[0]); self.assertIsNone(pyapps.get(aid))
        self.assertIn("Нет приложения", applab_brain.handle("удали приложение " + aid)[0])
        # «похожее» имя не удаляет чужое
        other = applab_brain.write("перевернуть строку защита", kind="brick", internet=False)["id"]
        self.assertIn("Нет приложения", applab_brain.handle("удали приложение " + other[:10])[0])
        self.assertIsNotNone(pyapps.get(other))

    def test_contour_writes_into_applab_not_archive(self):
        before = set(pyapps._all())
        job = {"task": "Напиши навык для апп лаб : рисовать графики функций.", "internet": False, "archive": "x.zip"}
        pa = Pa()
        summary, done = project_brain._run_applab(job, job["task"], pa, pa._event)
        self.assertTrue(done)
        new = set(pyapps._all()) - before
        self.assertEqual(len(new), 1)
        aid = new.pop()
        self.assertIn("загружено в App Lab", summary); self.assertIn(aid, summary); self.assertIn("В архив проекта ничего не добавлено", summary)
        self.assertTrue(project_brain._APPLAB_TASK.search(job["task"]))
        self.assertFalse(project_brain._APPLAB_TASK.search("Улучши свой код и напиши что добавил"))
        self.assertIn("contour_applab", str(applab_brain.observed(5)))

    def test_ai_instruction_mentions_draw(self):
        import pyapps as p
        self.assertIn("ctx.draw.graph", p.API_DOC)


if __name__ == "__main__":
    unittest.main()
