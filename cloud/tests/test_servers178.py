from pathlib import Path
import sys
import threading
import time
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'server'))
import test_repairs  # noqa: F401
import boardcode
import servers
import ssh_agent
import admincorpus
import llmstreams


class ClassifyTest(unittest.TestCase):
    def test_kinds(self):
        for c in ("uptime", "df -h", "journalctl -u nginx -n 50", "ps aux | head", "cat /etc/os-release",
                  "docker ps", "systemctl status nginx", "ls -la 2>&1"):
            self.assertEqual(servers.classify(c), "read", c)
        for c in ("sudo apt-get install -y htop", "systemctl restart nginx", "kill 123", "echo 1 > /tmp/x",
                  "docker restart web", "pip install requests", "rm /tmp/a.txt", "sed -i s/a/b/ f"):
            self.assertEqual(servers.classify(c), "change", c)
        for c in ("rm -rf /", "rm -rf ~", "mkfs.ext4 /dev/sda1", "dd if=/dev/zero of=/dev/sda", "reboot",
                  "sudo shutdown -h now", "systemctl stop sshd", ":(){ :|:& };:"):
            self.assertEqual(servers.classify(c), "danger", c)


class BoardCodeTest(unittest.TestCase):
    def test_roundtrip(self):
        boardcode.note_link("bq", "2.93.0", {"code": True, "admin": True})
        self.assertEqual(boardcode.pick_board("code"), "bq")
        boardcode.note_link("old", "2.92.0", {"code": True})
        self.assertNotIn("old", [b["board"] for b in boardcode.boards() if b["ok"]])

        def board():
            for _ in range(50):
                js = boardcode.take("bq")
                if js:
                    for j in js:
                        boardcode.result_in({"id": j["id"], "ok": True, "rc": 0, "out": "hi " + j["mode"],
                                             "board": "bq", "py": "3.13"})
                    return
                time.sleep(0.05)
        threading.Thread(target=board, daemon=True).start()
        r = boardcode.run("print('hi')", "py_run", "bq", timeout=5, wait_s=5)
        self.assertTrue(r["ok"])
        self.assertEqual(r["out"], "hi py_run")
        self.assertEqual(boardcode.jobs(1)[0]["status"], "готово")


class AgentTest(unittest.TestCase):
    def setUp(self):
        self.tg = [{"target": "abc123abc123", "id": "abc123abc123", "name": "web1", "host": "10.0.0.5",
                    "via": "ssh", "user": "root"},
                   {"target": "board:uno", "id": "board:uno", "name": "uno", "host": "10.0.0.9",
                    "via": "board", "ver": "2.93.0", "age": 1}]

    def plan(self, task):
        with patch.object(servers, "targets", return_value=self.tg):
            return ssh_agent._rule_plan({"task": task, "target": ""})

    def test_rules(self):
        p = self.plan("покажи состояние web1")
        self.assertTrue(all(a["server"] == "abc123abc123" for a in p))
        p = self.plan("перезапусти nginx на web1")
        self.assertIn("systemctl restart nginx", p[0]["command"])
        p = self.plan("установи htop на плату")
        self.assertEqual(p[0]["server"], "board:uno")
        self.assertIn("pip install", p[1]["command"])
        p = self.plan("включи web1")
        self.assertEqual(p[0]["tool"], "wake")
        p = self.plan("на web1 выполни `uname -a`")
        self.assertEqual(p, [{"tool": "run", "server": "abc123abc123", "command": "uname -a"}])
        p = self.plan("напиши скрипт на web1, который печатает дату")
        self.assertEqual(p[0]["tool"], "code")

    def test_job_waits_for_approval(self):
        calls = []
        with patch.object(servers, "targets", return_value=self.tg), \
                patch.object(ssh_agent, "_ext_ready", return_value=False), \
                patch.object(servers, "run", side_effect=lambda t, c, timeout=60: calls.append(c) or
                             {"ok": True, "code": 0, "out": "ok"}):
            r = ssh_agent.start_job("перезапусти nginx на web1", auto_ok=False, use_own_llm=False)
            jid = r["job_id"]
            for _ in range(100):
                if ssh_agent.get_job(jid)["status"] == "ждёт разрешения":
                    break
                time.sleep(0.02)
            self.assertEqual(calls, [])
            ssh_agent.approve(jid, True)
            for _ in range(100):
                if ssh_agent.get_job(jid)["status"] == "done":
                    break
                time.sleep(0.02)
        self.assertEqual(ssh_agent.get_job(jid)["status"], "done")
        self.assertIn("sudo -n systemctl restart nginx", calls[0])


class AdminStreamTest(unittest.TestCase):
    def test_material(self):
        t = llmstreams.texts("admin")
        self.assertGreaterEqual(len(t), len(admincorpus.LESSONS))
        self.assertIn("admin", [s["key"] for s in llmstreams.STREAMS])
        job = {"task": "диск", "status": "done", "result": "ок", "finished": 1,
               "steps": [{"tool": "run", "act": {"tool": "run", "command": "df -h"},
                          "result": {"ok": True, "out": "/dev/sda1 50%"}}]}
        self.assertTrue(admincorpus.learn_from_job(job))
        self.assertEqual(len(llmstreams.texts("admin")), len(t) + 1)


if __name__ == "__main__":
    unittest.main()
