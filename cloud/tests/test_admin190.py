# -*- coding: utf-8 -*-
"""v3.190: правиловый агент не останавливается на трёх шагах.

Проблема владельца: «Выполняет только три шага, даже если меняешь задачу».
Причина: начальный план правил — очередь из 2–3 действий; когда она пустела,
агент сразу завершался. Теперь после начального плана агент смотрит на вывод
выполненных шагов и продолжает, пока есть за что зацепиться (занятый диск,
упавшая служба, нагрузка, вышедший контейнер, битые кадры камеры), а модель,
отвечающая без действий, заменяется правилами, а не пустым итогом.
Запуск: python -m unittest tests.test_admin190 -v
"""
import os
import sys
import time
import unittest
from unittest.mock import patch

if 'NEURO_DATA' not in os.environ:
    import tempfile
    os.environ['NEURO_DATA'] = tempfile.mkdtemp()
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'server'))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_repairs  # noqa: F401  (ставит NEURO_DATA и поднимает общий контекст)

import admin_agent
import ssh_agent
ssh_agent.seed_admin_knowledge()      # v3.218: планирует память мозга — засеваем уроки


def _hist(*pairs):
    """history как в _run: [(act, result), ...]"""
    return list(pairs)


TG = [{"target": "srv1", "id": "srv1", "name": "web1", "host": "10.0.0.5",
       "via": "ssh", "user": "root", "port": 22}]
JOB = {"task": "проверь web1", "target": "srv1"}


class NextRuleActTest(unittest.TestCase):
    """Подсказки продолжения: диск, службы, нагрузка, контейнеры."""

    def test_disk_full_continues(self):
        h = _hist(({"tool": "run", "command": "uptime; nproc; free -h; df -h / 2>/dev/null | tail -1"},
                   {"ok": True, "out": "/dev/sda1 100G 95G 5G 95% /"}))
        a = ssh_agent._next_rule_act(JOB, h, set())
        self.assertIsNotNone(a)
        self.assertEqual(a["tool"], "run")
        self.assertIn("du -xh", a["command"])

    def test_disk_ok_does_not_continue(self):
        h = _hist(({"tool": "run", "command": "uptime; nproc; free -h; df -h / 2>/dev/null | tail -1"},
                   {"ok": True, "out": "/dev/sda1 100G 20G 80G 20% /"}))
        self.assertIsNone(ssh_agent._next_rule_act(JOB, h, set()))

    def test_failed_unit_continues(self):
        h = _hist(({"tool": "run", "command": "systemctl --failed --no-pager | head -15"},
                   {"ok": True, "out": "  nginx.service loaded failed failed nginx\n1 loaded units listed"}))
        a = ssh_agent._next_rule_act(JOB, h, set())
        self.assertIsNotNone(a)
        self.assertIn("journalctl -u nginx.service", a["command"])
        self.assertIn("systemctl status nginx.service", a["command"])

    def test_failed_units_clean_no_continue(self):
        h = _hist(({"tool": "run", "command": "systemctl --failed --no-pager | head -15"},
                   {"ok": True, "out": "0 loaded units listed"}))
        self.assertIsNone(ssh_agent._next_rule_act(JOB, h, set()))

    def test_load_continues(self):
        h = _hist(({"tool": "run", "command": "uptime; nproc; free -h"},
                   {"ok": True, "out": "load average: 7,32, 5,10, 4,88"}))
        a = ssh_agent._next_rule_act(JOB, h, set())
        self.assertIsNotNone(a)
        self.assertIn("ps aux --sort=-%cpu", a["command"])
        # русская запятая тоже читается
        h2 = _hist(({"tool": "run", "command": "uptime"}, {"ok": True, "out": "load average: 0,15, 0,10, 0,05"}))
        self.assertIsNone(ssh_agent._next_rule_act(JOB, h2, set()))

    def test_docker_exited_continues(self):
        h = _hist(({"tool": "run", "command": "docker ps --format '{{.Names}}\\t{{.Status}}\\t{{.Image}}'"},
                   {"ok": True, "out": "cache\tExited (1) 2 hours ago\tredis:7"}))
        a = ssh_agent._next_rule_act(JOB, h, set())
        self.assertIsNotNone(a)
        self.assertEqual(a["command"], "docker logs --tail 30 cache 2>&1 | tail -30")

    def test_check_failed_wakes_only_when_asked(self):
        h = _hist(({"tool": "check", "server": "srv1"}, {"ok": False, "error": "10.0.0.5:22 недоступен"}))
        self.assertIsNone(ssh_agent._next_rule_act(JOB, h, set()))
        job = dict(JOB, task="включи web1")
        a = ssh_agent._next_rule_act(job, h, set())
        self.assertIsNotNone(a)
        self.assertEqual(a["tool"], "wake")

    def test_no_repeat_then_stop(self):
        cmd = "du -xh --max-depth=1 / 2>/dev/null | sort -h | tail -12"
        h = _hist(({"tool": "run", "command": "uptime; df -h /"}, {"ok": True, "out": "95% /"}),
                  ({"tool": "run", "command": cmd}, {"ok": True, "out": "95G /var"}))
        self.assertIsNone(ssh_agent._next_rule_act(JOB, h, {cmd}))

    def test_no_target_no_act(self):
        with patch.object(ssh_agent.servers, "targets", return_value=[]):
            h = _hist(({"tool": "run", "command": "uptime; df -h /"}, {"ok": True, "out": "95% /"}))
            self.assertIsNone(ssh_agent._next_rule_act({"task": "проверь", "target": ""}, h, set()))


class AdminContinuationTest(unittest.TestCase):
    """Администрирование: продолжение с учётом битых кадров и задач агента."""

    def test_broken_frames_lead_to_board_log(self):
        btg = {"target": "board:unoq", "id": "board:unoq", "name": "unoq",
               "via": "board", "kind": "board", "age": 5}
        h = _hist(({"tool": "vision"}, {"ok": True, "out": "cam1: кадр 2 с назад, ПОСЛЕДНИЙ КАДР БИТЫЙ, "
                                                       "битых в последних 24: 3; причина: JPEG не открылся"}))
        with patch.object(admin_agent.servers, "targets", return_value=[btg]):
            a = admin_agent._next_rule_act({"task": "что с камерами", "target": ""}, h, set())
        self.assertIsNotNone(a)
        self.assertEqual(a["server"], "board:unoq")
        self.assertIn("dmesg", a["command"])
        # повтор не предлагается
        with patch.object(admin_agent.servers, "targets", return_value=[btg]):
            self.assertIsNone(admin_agent._next_rule_act({"task": "что с камерами", "target": ""},
                                                         h, {a["command"]}))

    def test_delegates_to_server_hints(self):
        h = _hist(({"tool": "run", "command": "uptime; df -h /"}, {"ok": True, "out": "95% /"}))
        with patch.object(admin_agent.servers, "targets", return_value=TG):
            a = admin_agent._next_rule_act(dict(JOB), h, set())
        self.assertIsNotNone(a)
        self.assertIn("du -xh", a["command"])


class AdminJobStepsTest(unittest.TestCase):
    """Полное задание: одна и та же задача теперь идёт дальше трёх шагов."""

    def setUp(self):
        admin_agent._JOBS.clear()
        import brainplan
        _p = patch.object(brainplan, "learn", return_value=False)   # опыт заданий не должен менять порядок тестов
        _p.start()
        self.addCleanup(_p.stop)

    def _wait(self, jid, timeout=10):
        t0 = time.time()
        while time.time() - t0 < timeout:
            j = admin_agent.get_job(jid)
            if j and j["status"] not in ("работает", "ждёт разрешения"):
                return j
            time.sleep(0.05)
        raise AssertionError("задание не завершилось за %s с" % timeout)

    def _fake_run(self, cmd_map):
        def run(tg, cmd, timeout=90):
            for key, out in cmd_map.items():
                if key in cmd:
                    return {"ok": True, "code": 0, "out": out}
            return {"ok": True, "code": 0, "out": ""}
        return run

    def test_overview_job_extends_past_three_steps(self):
        """«Проверь сервер» + занятый диск и упавшая служба -> больше 3 шагов."""
        outs = {
            "du -xh": "96G /var\n  40G /var/log",
            "journalctl -u nginx.service": "nginx: worker process exited",
            "systemctl --failed": "  nginx.service loaded failed failed nginx\n\n1 loaded units listed",
            "df -h /": "/dev/sda1 100G 96G 4G 96% /",
            "uptime": " 09:00 up 2 days, load average: 0,20, 0,15, 0,10\n2\nMem: 7.8Gi",
        }
        with patch.object(admin_agent, "_ext_ready", return_value=False), \
             patch.object(admin_agent.servers, "targets", return_value=TG), \
             patch.object(admin_agent.servers, "check", return_value={"ok": True, "out": "порт 22 открыт"}), \
             patch.object(admin_agent.servers, "run", side_effect=self._fake_run(outs)):
            r = admin_agent.start_job("проверь web1", owner="test", mode="brain")
            j = self._wait(r["job_id"])
        self.assertEqual(j["status"], "done", j["result"])
        tools = [s["tool"] for s in j["steps"]]
        cmds = [s["act"].get("command") or "" for s in j["steps"]]
        self.assertGreater(len(j["steps"]), 3, "агент снова остановился на трёх шагах: %s" % cmds)
        self.assertTrue(any("du -xh" in c for c in cmds), cmds)
        self.assertTrue(any("journalctl" in c for c in cmds), cmds)
        self.assertIn("96%", j["result"])
        # шаги не повторяются
        self.assertEqual(len(cmds), len(set(cmds)), "есть повторяющиеся шаги: %s" % cmds)

    def test_healthy_server_stops_honestly(self):
        """Всё в порядке — честное завершение, без выдуманных продолжений."""
        outs = {
            "uptime": " 09:00 up 2 days, load average: 0,10, 0,08, 0,05\n2\nMem: 7.8Gi",
            "df -h /": "/dev/sda1 100G 20G 80G 20% /",
            "systemctl --failed": "0 loaded units listed",
        }
        with patch.object(admin_agent, "_ext_ready", return_value=False), \
             patch.object(admin_agent.servers, "targets", return_value=TG), \
             patch.object(admin_agent.servers, "check", return_value={"ok": True, "out": "порт 22 открыт"}), \
             patch.object(admin_agent.servers, "run", side_effect=self._fake_run(outs)):
            r = admin_agent.start_job("проверь web1", owner="test", mode="brain")
            j = self._wait(r["job_id"])
        self.assertEqual(j["status"], "done")
        self.assertEqual(len(j["steps"]), 3, j["result"])

    def test_garbage_llm_falls_back_to_rules(self):
        """Модель отвечает без действий — агент переходит на память мозга, а не
        завершается с пустым итогом."""
        with patch.object(admin_agent, "_ext_ready", return_value=True), \
             patch.object(admin_agent, "_ext_next", return_value=None), \
             patch.object(admin_agent.servers, "targets", return_value=TG), \
             patch.object(admin_agent.servers, "check", return_value={"ok": True, "out": "порт 22 открыт"}), \
             patch.object(admin_agent.servers, "run",
                          return_value={"ok": True, "code": 0, "out": "load average: 0,10, 0,08, 0,05\n0 loaded units listed\n20% /"}):
            r = admin_agent.start_job("проверь web1", owner="test", mode="llm")
            j = self._wait(r["job_id"])
        self.assertEqual(j["status"], "done")
        self.assertGreaterEqual(len(j["steps"]), 3, j["result"])
        self.assertIn("память", j["planner"])


class SshAgentJobStepsTest(unittest.TestCase):
    """Окно «Серверы»: тот же механизм продолжения после трёх шагов."""

    def setUp(self):
        ssh_agent._JOBS.clear()

    def _wait(self, jid, timeout=10):
        t0 = time.time()
        while time.time() - t0 < timeout:
            j = ssh_agent.get_job(jid)
            if j and j["status"] not in ("работает", "ждёт разрешения"):
                return j
            time.sleep(0.05)
        raise AssertionError("задание не завершилось за %s с" % timeout)

    def test_servers_window_job_extends(self):
        outs = {
            "du -xh": "91G /var",
            "systemctl --failed": "  nginx.service loaded failed failed nginx\n1 loaded units listed",
            "df -h /": "/dev/sda1 100G 91G 9G 91% /",
            "uptime": "load average: 0,30, 0,20, 0,10\n4\nMem: 7.8Gi",
        }
        def run(tg, cmd, timeout=90):
            for key, out in outs.items():
                if key in cmd:
                    return {"ok": True, "code": 0, "out": out}
            return {"ok": True, "code": 0, "out": ""}
        with patch.object(ssh_agent, "_ext_ready", return_value=False), \
             patch.object(ssh_agent.servers, "targets", return_value=TG), \
             patch.object(ssh_agent.servers, "check", return_value={"ok": True, "out": "порт 22 открыт"}), \
             patch.object(ssh_agent.servers, "run", side_effect=run):
            r = ssh_agent.start_job("проверь web1", owner="test", use_own_llm=False)
            j = self._wait(r["job_id"])
        self.assertEqual(j["status"], "done", j["result"])
        cmds = [s["act"].get("command") or "" for s in j["steps"]]
        self.assertGreater(len(j["steps"]), 3, "агент «Серверов» остановился на трёх шагах: %s" % cmds)
        self.assertTrue(any("du -xh" in c for c in cmds), cmds)
        self.assertTrue(any("journalctl" in c for c in cmds), cmds)
        self.assertEqual(len(cmds), len(set(cmds)), "повторяющиеся шаги: %s" % cmds)


if __name__ == "__main__":
    unittest.main()
