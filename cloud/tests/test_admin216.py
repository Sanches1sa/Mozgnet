import os, sys, tempfile, unittest
from unittest import mock
os.environ["NEURO_DATA"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "server"))
import store, servers, boardcode, admincmds, admincorpus, strict, ssh_agent  # noqa: E402


class Admin216(unittest.TestCase):
    def setUp(self):
        store.ensure_dirs()
        store.update_settings({"terminal_on": True})

    def test_catalog_is_large_and_plans_by_intent(self):
        self.assertGreaterEqual(len(admincmds.CATALOG), 50)
        cases = {"почему тормозит сервер": "proc", "сколько места на диске": "disk", "какие порты открыты": "ports",
                 "проверь температуру": "temp", "кто заходил по ssh": "auth", "проверь docker": "docker",
                 "покажи крон": "cron", "что с камерой usb": "usb", "проверь nginx": "nginx"}
        for q, key in cases.items():
            self.assertIn(key, [k for k, _ in admincmds.plan(q)], q)


    def test_latin_commands_not_blocked_for_admin_questions(self):
        self.assertTrue(strict.code_question("какая команда systemctl перезапускает службу"))
        self.assertFalse(strict.code_question("расскажи про кошек"))

    def test_lessons_contain_fenced_commands_for_llm_and_kb(self):
        ls = admincorpus.catalog_lessons()
        self.assertGreaterEqual(len(ls), 50)
        self.assertTrue(all("```bash" in b for _, b in ls))
        self.assertGreaterEqual(len(admincorpus.lesson_texts()), len(ls))

    def test_local_target_runs_and_writes_code(self):
        r = servers.run(servers.LOCAL, "echo привет")
        self.assertTrue(r["ok"]); self.assertIn("привет", r["out"])
        p = os.path.join(tempfile.mkdtemp(), "t.py")
        w = servers.write_file(servers.LOCAL, p, "print(6*7)\n")
        self.assertTrue(w["ok"], w); self.assertIn("синтаксис", w["out"])
        bad = servers.write_file(servers.LOCAL, p, "def (:\n")
        self.assertFalse(bad["ok"])
        store.update_settings({"terminal_on": False})
        self.assertFalse(servers.run(servers.LOCAL, "echo 1")["ok"])

    def test_board_refusal_is_reported_honestly_and_ssh_fallback(self):
        refusal = {"ok": False, "rc": -1, "out": "на плате убраны все «питоны» (v2.97.3): исполнение кода отключено"}
        with mock.patch.object(boardcode, "run", return_value=refusal), \
             mock.patch.object(servers, "_ssh_for_board", return_value=""):
            r = servers.run("board:uno", "uptime")
        self.assertFalse(r["ok"]); self.assertTrue(r["blocked"])
        self.assertIn("SSH", r["error"]); self.assertTrue(boardcode.blocked("uno"))
        with mock.patch.object(boardcode, "run", return_value=refusal), \
             mock.patch.object(servers, "_ssh_for_board", return_value="s9"), \
             mock.patch("ssh_servers.run", return_value={"ok": True, "out": "up 1 day"}):
            r = servers.run("board:uno", "uptime")
        self.assertTrue(r["ok"]); self.assertIn("SSH", r["note"])

    def test_code_for_blocked_board_goes_to_cloud(self):
        ts = [{"id": "board:uno", "target": "board:uno", "name": "uno", "host": "1.2.3.4", "via": "board", "blocked": True},
              {"id": servers.LOCAL, "target": servers.LOCAL, "name": "облако", "host": "localhost", "via": "local"}]
        with mock.patch.object(servers, "targets", return_value=ts):
            self.assertEqual(ssh_agent._pick_target({"task": "напиши скрипт на плате, который считает память"}), servers.LOCAL)

class Admin217(unittest.TestCase):
    def setUp(self):
        store.ensure_dirs()

    def test_windows_commands_are_powershell_not_linux(self):
        import admincmds
        for q in ("можешь слышать", "нет интернета", "почему медленно работает", "сколько места"):
            cmds = [c for _k, c in admincmds.plan(q, win=True)]
            self.assertTrue(cmds, q)
            self.assertFalse(any(c.startswith(("uptime", "free ", "df ", "systemctl")) for c in cmds), cmds)
        self.assertEqual([k for k, _ in admincmds.plan("можешь слышать")][:1], ["audio"])

    def test_fuzzy_and_generic_words(self):
        import admincmds
        self.assertEqual(admincmds.match("проверь"), [])
        self.assertIn("proc", admincmds.match("всё лагает"))
        self.assertIn("usb", admincmds.match("что с камерой"))


    def test_local_run_decodes_cp866(self):
        self.assertEqual(servers._decode("Привет".encode("cp866")), "Привет")
        self.assertEqual(servers._decode("Привет".encode("utf-8")), "Привет")




class Brain218(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        store.ensure_dirs()
        store.update_settings({"terminal_on": True})
        import ssh_agent as sa
        sa.seed_admin_knowledge()

    def top(self, q, win=False):
        import brainplan
        r = brainplan.recall(q, win)
        return (r[0][0], r[0][2][0]) if r else (None, None)

    def test_memory_picks_tool_and_commands_without_code_keywords(self):
        t, a = self.top("перезапусти облако")
        self.assertEqual(a["tool"], "restart_cloud")
        t, a = self.top("покажи экран", True)
        self.assertEqual(a["tool"], "shot")
        t, a = self.top("можешь слышать")
        self.assertIn("arecord", a["command"])
        t, a = self.top("можешь слышать", True)
        self.assertIn("Audio", a["command"])
        t, a = self.top("почему нет интернета")
        self.assertIn("ping", a["command"])

    def test_unknown_task_is_honest_not_a_blind_overview(self):
        import brainplan
        self.assertEqual(brainplan.recall("абракадабра квантовая", False), [])
        self.assertIsNone(brainplan.next_act({"task": "абракадабра квантовая"}, [], set(), "s1", False))

    def test_owner_command_in_backticks_and_no_target(self):
        import brainplan
        a = brainplan.next_act({"task": "выполни `uname -a`"}, [], set(), "s1", False)
        self.assertEqual(a["command"], "uname -a")
        self.assertEqual(brainplan.next_act({"task": "x"}, [], set(), "", False)["tool"], "list")

    def test_job_restart_cloud_via_memory(self):
        import admin_agent, updater, threading, time
        calls = []
        with mock.patch.object(updater, "schedule_restart", side_effect=lambda r="": calls.append(r) or {"mode": "auto"}), \
             mock.patch.object(threading, "Timer", side_effect=lambda d, fn: type("T", (), {"start": lambda self: fn()})()), \
             mock.patch.object(admin_agent, "_ext_ready", return_value=False), \
             mock.patch.object(admin_agent, "_pick_target", return_value="local:cloud"):
            jid = admin_agent.start_job("перезапусти облако", mode="brain", auto_ok=True)["job_id"]
            for _ in range(200):
                j = admin_agent.get_job(jid)
                if j and j.get("status") in ("done", "error"):
                    break
                time.sleep(0.05)
        j = admin_agent.get_job(jid)
        self.assertEqual(j["status"], "done", j)
        self.assertEqual(len(calls), 1)
        self.assertIn("память", j["steps"][0]["act"]["why"])

    def test_job_unknown_task_says_it_does_not_know(self):
        import admin_agent, time
        with mock.patch.object(admin_agent, "_ext_ready", return_value=False), \
             mock.patch.object(admin_agent, "_pick_target", return_value="local:cloud"):
            jid = admin_agent.start_job("абракадабра квантовая", mode="brain", auto_ok=True)["job_id"]
            for _ in range(200):
                j = admin_agent.get_job(jid)
                if j and j.get("status") in ("done", "error"):
                    break
                time.sleep(0.05)
        j = admin_agent.get_job(jid)
        self.assertEqual(j["steps"], [])
        self.assertIn("не нашёл в своей памяти", j["result"])

    def test_learn_remembers_successful_job(self):
        import brainplan, kb
        job = {"id": "j1", "task": "проверь питание камеры", "status": "done",
               "steps": [{"tool": "run", "act": {"tool": "run", "server": "s", "command": "lsusb"},
                          "result": {"ok": True, "out": "Bus 001"}}]}
        self.assertTrue(brainplan.learn(job))
        self.assertTrue(kb.article("admx_j1"))


if __name__ == "__main__":
    unittest.main()
