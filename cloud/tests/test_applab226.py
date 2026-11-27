import os, sys, tempfile, unittest
from unittest import mock
os.environ["NEURO_DATA"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "server"))
import store, pyapps, applab_brain, admincorpus, api_ui, api_board, brain  # noqa: E402


class Req:
    def __init__(self, body=None, query=None, method="POST"):
        self.body, self.query, self.method, self.client_ip = body or {}, query or {}, method, "1.2.3.4"


class AppLab226(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        store.ensure_dirs()
        store.update_settings({"web_on": False})

    def test_write_app_disabled_with_context(self):
        r = applab_brain.write("поиск в интернете по запросу", kind="app", internet=False)
        self.assertTrue(r["ok"], r)
        m = pyapps.get(r["id"])
        self.assertFalse(m["enabled"]); self.assertEqual(m["written_by"], "Star Gate")
        self.assertTrue(m["context"]); self.assertTrue(m["triggers"])
        self.assertIn("ВЫКЛЮЧЕНО", r["text"])

    def test_write_brick(self):
        r = applab_brain.write("перевернуть строку", kind="brick", internet=False)
        self.assertTrue(r["ok"], r); self.assertIn("урок", r["base"])
        c = applab_brain.check(r["id"], "привет")
        self.assertTrue(c["ok"], c); self.assertIn("тевирп", c["text"])

    def test_unknown_task_honest(self):
        r = applab_brain.write("управлять космическим кораблём", internet=False)
        self.assertFalse(r["ok"]); self.assertIn("Не написала", r["text"])

    def test_internet_compare(self):
        page = {"url": "https://ex.org/a", "title": "t", "text": "x", "html": ""}
        import project_web, project_brain
        code_page = "import re\nimport subprocess\nm = re.findall('a', 'b')\nsubprocess.run(['ls'])\n"
        store.update_settings({"web_on": True})
        try:
            with mock.patch.object(project_web, "search", return_value=[{"url": "https://ex.org/a", "title": "t"}]), \
                 mock.patch.object(project_web, "fetch_page", return_value=(True, page)), \
                 mock.patch.object(project_brain, "_code_blocks", return_value=[code_page]):
                r = applab_brain.write("перевернуть строку тест сети", kind="brick", internet=True)
        finally:
            store.update_settings({"web_on": False})
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["refs"][0]["url"], "https://ex.org/a")
        self.assertIn("subprocess", r["compare"]["danger_on_pages"])
        self.assertNotIn("subprocess", r["code"])
        self.assertIn("https://ex.org/a", r["text"])
        self.assertTrue(pyapps.get(r["id"])["web_refs"])

    def test_observed_for_llm(self):
        applab_brain.write("перевернуть строку наблюдение", kind="brick", internet=False)
        txt = "\n".join(admincorpus.applab_texts())
        self.assertIn("Star Gate написал приложение App Lab", txt)
        self.assertIn("# task: App Lab:", txt)

    def test_context_call(self):
        pyapps.save({"id": "kofe2", "name": "Кофемашина", "desc": "сварить крепкий кофе на кухне",
                     "triggers": [], "code": 'def run(text, ctx):\n    return "варю кофе"\n', "enabled": True})
        reply, _z, info = brain.think("можешь сварить крепкий кофе", chat_id="t", sender="владелец")
        self.assertEqual(reply.strip(), "варю кофе"); self.assertIn("контекст", info.get("by", ""))
        reply, _z, _i = brain.think("расскажи про кошек", chat_id="t", sender="владелец")
        self.assertNotEqual(reply.strip(), "варю кофе")

    def test_context_not_for_disabled(self):
        pyapps.save({"id": "tea2", "name": "Чайник", "desc": "заварить зелёный чай вечером", "triggers": [],
                     "code": 'def run(text, ctx):\n    return "заварила"\n', "enabled": False})
        self.assertIsNone(pyapps.match_context("заварить зелёный чай", []))

    def test_special_chats_history(self):
        code, r = api_ui.h_chat_list(Req(method="GET"))
        kinds = {c["kind"] for c in r["chats"]}
        self.assertTrue({"admin", "code"} <= kinds)
        cid = store.chat_special("code")["id"]
        code, r = api_ui.h_chat_send(Req({"id": cid, "text": "напиши кирпичик перевернуть строку для чата", "route": "cloud"}))
        self.assertTrue(r["ok"], r); self.assertIn("Написала кирпичик", r["reply"])
        code, r = api_ui.h_chat_send(Req({"id": cid, "text": "приложения"}))
        self.assertIn("Приложения App Lab", r["reply"])
        hist = store.chat_load(cid)["messages"]
        self.assertGreaterEqual(len(hist), 4)                      # история записана
        self.assertEqual(store.chat_special("code")["id"], cid)    # тот же чат
        code, r = api_ui.h_chat_delete(Req({"id": cid}))
        self.assertFalse(r["ok"])

    def test_admin_chat_job_and_kb(self):
        cid = store.chat_special("admin")["id"]
        import admin_agent
        with mock.patch.object(admin_agent, "start_job", return_value={"ok": True, "job_id": "abc123def456"}) as sj:
            code, r = api_ui.h_chat_send(Req({"id": cid, "text": "выполни: проверь место на диске"}))
        self.assertTrue(r["ok"], r); self.assertIn("взял задачу", r["reply"]); sj.assert_called_once()
        self.assertEqual(store.chat_load(cid).get("admin_job"), "abc123def456")
        self.assertEqual(sj.call_args[0][0], "проверь место на диске")
        self.assertGreaterEqual(len(store.chat_load(cid)["messages"]), 2)

    def test_board_mode_applab(self):
        code, r = api_board.h_brain_think(Req({"text": "напиши кирпичик перевернуть строку с платы", "mode": "applab", "from": "uno"}))
        self.assertTrue(r["ok"], r); self.assertIn("Написала", r["answer"])
        m = [a for a in pyapps.apps() if "плата" in str(a.get("origin"))]
        self.assertTrue(m and not m[0]["enabled"])
        code, r = api_board.h_brain_think(Req({"text": "проверь " + m[0]["id"], "mode": "applab", "from": "uno"}))
        self.assertIn("Проверка", r["answer"])
        code, r = api_board.h_brain_think(Req({"text": "что-то непонятное", "mode": "applab", "from": "uno"}))
        self.assertIn("Команды", r["answer"])


if __name__ == "__main__":
    unittest.main()
