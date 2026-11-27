import io, os, sys, tempfile, time, unittest, zipfile
from unittest import mock
os.environ["NEURO_DATA"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "server"))
import store, api_ui, chat_agents, pyapps  # noqa: E402


class Req:
    def __init__(self, body=None, query=None, method="POST", files=None):
        self.body, self.query, self.method, self.client_ip, self.headers = body or {}, query or {}, method, "1.2.3.4", {}
        self.files_data = files or {}


def proj_zip():
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        z.writestr("demo/README.md", "# demo\n")
        z.writestr("demo/main.py", "def add(a, b):\n    return a + b\n")
    return b.getvalue()


def wait_final(cid, n=1, timeout=60):
    t0 = time.time()
    while time.time() - t0 < timeout:
        ms = store.chat_load(cid)["messages"]
        if sum(1 for m in ms if m.get("agent_final")) >= n:
            return ms
        time.sleep(0.3)
    raise AssertionError("нет итога агента: %s" % [m.get("text", "")[:80] for m in store.chat_load(cid)["messages"]])


class ChatAgents231(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        store.ensure_dirs()
        store.update_settings({"web_on": False})

    def new_chat(self):
        return store.chat_new(title="t", owner="1.2.3.4")["id"]

    def test_zip_in_chat_becomes_project_and_tasks_run_from_chat(self):
        cid = self.new_chat()
        code, r = api_ui.h_project_upload(Req(query={"chat": cid}, files={"file": proj_zip(), "filename": "demo.zip"}))
        self.assertEqual(code, 200, r)
        chat = store.chat_load(cid)
        self.assertEqual(chat["project"]["name"], "demo.zip")
        self.assertIn("взят в работу этого чата", chat["messages"][-1]["text"])
        # задача агенту из чата (настоящий агент, режим Star Gate, без интернета)
        # приложение App Lab — сразу в App Lab при любой модели (auto без LLM тоже)
        code, r = api_ui.h_chat_send(Req({"id": cid, "text": "Можешь написать приложение в апп лаб для рисования графиков функций?", "route": "code",
                                          "agent": {"model": "auto", "internet": False}}))
        self.assertIn("загружено в App Lab", r["reply"]); self.assertFalse(r.get("agent"))
        self.assertTrue(any("график" in a["triggers"] for a in pyapps.apps()))
        # задача по проекту — настоящий агент Контур, ход и итог приходят в чат
        code, r = api_ui.h_chat_send(Req({"id": cid, "text": "проверь синтаксис проекта", "route": "code",
                                          "agent": {"model": "brain", "internet": False}}))
        self.assertTrue(r["ok"], r); self.assertTrue(r.get("agent")); self.assertIn("Контур взял задачу", r["reply"])
        ms = wait_final(cid)
        final = [m for m in ms if m.get("agent_final")][-1]
        self.assertIn("Контур закончил", final["text"])
        self.assertIn("/api/code/project/download?id=", final["text"])
        roles = [m["role"] for m in ms]
        self.assertIn("user", roles)                                     # задачи сохранены в истории
        self.assertTrue(store.chat_load(cid)["project"]["last_job"])
        # вторая задача продолжает результат первой
        with mock.patch("project_agent.start", wraps=__import__("project_agent").start) as st:
            api_ui.h_chat_send(Req({"id": cid, "text": "сделай ревью кода проекта", "route": "code", "agent": {"model": "brain", "internet": False}}))
            self.assertEqual(st.call_args.kwargs["base_job"], store.chat_load(cid)["project"]["last_job"])
        wait_final(cid, 2)
        # «?» — вопрос мимо проекта; «убери проект»
        with mock.patch("project_agent.start") as st:
            api_ui.h_chat_send(Req({"id": cid, "text": "?что такое рекурсия", "route": "code"}))
            st.assert_not_called()
        code, r = api_ui.h_chat_send(Req({"id": cid, "text": "убери проект", "route": "code"}))
        self.assertIn("отвязан", r["reply"]); self.assertNotIn("project", store.chat_load(cid))

    def test_admin_from_chat_with_approval_saved_in_history(self):
        cid = self.new_chat()
        state = {"status": "работает", "steps": [], "pending": None, "result": None}
        calls = {}

        def start_job(task, owner="", mode="both", auto_ok=None, target=""):
            calls["start"] = (task, mode, auto_ok, target)
            return {"ok": True, "job_id": "abc123def456"}

        def get_job(jid):
            return dict(state, id=jid)

        def approve(jid, yes):
            calls["approve"] = yes
            state["pending"] = None
            state["steps"] = [{"act": {"command": "apt-get update"}, "result": {"ok": True, "out": "готово"}}]
            state["status"], state["result"] = "готово", "Пакеты обновлены"
            return {"ok": True}
        import admin_agent
        with mock.patch.object(admin_agent, "start_job", start_job), mock.patch.object(admin_agent, "get_job", get_job), \
                mock.patch.object(admin_agent, "approve", approve):
            code, r = api_ui.h_chat_send(Req({"id": cid, "text": "обнови пакеты на сервере", "route": "admin",
                                              "agent": {"mode": "brain", "auto_ok": False, "target": "srv1"}}))
            self.assertTrue(r.get("agent")); self.assertEqual(calls["start"], ("обнови пакеты на сервере", "brain", False, "srv1"))
            state["status"], state["pending"] = "ждёт разрешения", {"kind": "change", "why": "команда меняет систему",
                                                                    "act": {"command": "apt-get update"}}
            t0 = time.time()
            while time.time() - t0 < 10 and not any(m.get("agent_ask") for m in store.chat_load(cid)["messages"]):
                time.sleep(0.2)
            self.assertTrue(any(m.get("agent_ask") and "apt-get update" in m["text"] for m in store.chat_load(cid)["messages"]))
            code, r = api_ui.h_chat_send(Req({"id": cid, "text": "да", "route": "admin"}))
            self.assertIn("Разрешено", r["reply"]); self.assertTrue(calls["approve"])
            ms = wait_final(cid, timeout=15)
        self.assertIn("Пакеты обновлены", [m for m in ms if m.get("agent_final")][-1]["text"])
        texts = [m["text"] for m in ms]
        self.assertIn("обнови пакеты на сервере", texts); self.assertIn("да", texts)          # всё в истории

    def test_admin_question_uses_instructions_not_agent(self):
        cid = self.new_chat()
        import admin_agent
        with mock.patch.object(admin_agent, "start_job") as sj:
            api_ui.h_chat_send(Req({"id": cid, "text": "как посмотреть место на диске?", "route": "admin"}))
            sj.assert_not_called()

    def test_web_has_no_agent_panels_and_sends_settings(self):
        js = open(os.path.join(os.path.dirname(__file__), "..", "web", "app.js"), encoding="utf-8").read()
        self.assertNotIn("await runProjectTask(text)", js); self.assertNotIn("await runAdminTask(text)", js)
        self.assertIn("body.agent =", js); self.assertIn("uploadProjectToChat", js)
        self.assertIn("panel.hidden = true; return;", js)


if __name__ == "__main__":
    unittest.main()
