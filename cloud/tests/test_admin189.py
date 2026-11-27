# -*- coding: utf-8 -*-
"""v3.189: режим «Администрирование» (admin_agent) и битые кадры «Зрения».
Запуск: python -m unittest discover -s tests -v (NEURO_DATA ставит test_repairs)."""
import json
import os
import sys
import threading
import time
import unittest
from unittest.mock import patch

if 'NEURO_DATA' not in os.environ:
    import tempfile
    os.environ['NEURO_DATA'] = tempfile.mkdtemp()
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'server'))
import test_repairs  # noqa: F401  (ставит NEURO_DATA и поднимает общий контекст)
import ssh_agent as _seed_sa  # v3.218: память мозга должна быть засеяна уроками
_seed_sa.seed_admin_knowledge()

import admin_agent
import vision
import store
import api_ui


class AdminAgentRulePlanTest(unittest.TestCase):
    """Правила агента «Администрирование»: рабочий стол, зрение, серверы."""

    def setUp(self):
        self.tg = [{"target": "srv1", "id": "srv1", "name": "web1", "host": "10.0.0.5",
                    "via": "ssh", "user": "root", "port": 22}]

    def plan(self, task):
        job = {"task": task, "target": ""}
        with patch.object(admin_agent.servers, "targets", return_value=self.tg):
            return admin_agent._rule_plan(job)

    def test_desktop_intents(self):
        self.assertEqual(admin_agent._rule_plan({"task": "сделай скриншот рабочего стола",
                                                 "target": ""}), [{"tool": "shot"}])
        self.assertEqual(admin_agent._rule_plan({"task": "что видит камера на плате",
                                                 "target": ""}), [{"tool": "vision"}])
        r = admin_agent._rule_plan({"task": "нажми ctrl+alt+t на компьютере", "target": ""})
        self.assertEqual(r, [{"tool": "desk", "act": "key", "keys": "ctrl+alt+t"}])
        r = admin_agent._rule_plan({"task": "напечатай: привет, мир", "target": ""})
        self.assertEqual(r[0]["tool"], "desk")
        self.assertEqual(r[0]["act"], "type")
        self.assertEqual(r[0]["text"], "привет, мир")
        r = admin_agent._rule_plan({"task": "открой на компьютере калькулятор", "target": ""})
        self.assertEqual(r, [{"tool": "desk", "act": "open", "target": "калькулятор"}])

    def test_server_fallback(self):
        r = self.plan("покажи состояние web1: диск, память")
        self.assertTrue(any(a.get("tool") in ("check", "run") for a in r), r)
        r = self.plan("перезапусти nginx на web1")
        self.assertTrue(any(a.get("tool") == "run" and "nginx" in a.get("command", "") for a in r), r)

    def test_will_budget(self):
        job = {"mode": "llm", "task": "", "target": ""}
        w = admin_agent._will(job)
        self.assertEqual(w["step_limit"], admin_agent.MAX_STEPS)
        self.assertFalse(w["connected"])
        job = {"mode": "brain", "task": "", "target": ""}
        w = admin_agent._will(job)
        self.assertIn("step_limit", w)


class AdminAgentJobTest(unittest.TestCase):
    """Задание агента: правила без модели, шаг run, разрешение на запись."""

    def setUp(self):
        self.tg = [{"target": "srv1", "id": "srv1", "name": "web1", "host": "10.0.0.5",
                    "via": "ssh", "user": "root", "port": 22}]

    def test_rule_job_runs_and_finishes(self):
        with patch.object(admin_agent, "_ext_ready", return_value=False), \
             patch.object(admin_agent.servers, "targets", return_value=self.tg), \
             patch.object(admin_agent.servers, "run",
                          return_value={"ok": True, "code": 0, "out": " 09:00 up 1 day"}), \
             patch.object(admin_agent.servers, "check",
                          return_value={"ok": True, "out": "порт 22 открыт"}):
            r = admin_agent.start_job("покажи состояние web1", owner="test", mode="brain")
            self.assertTrue(r["ok"])
            jid = r["job_id"]
            deadline = time.time() + 10
            job = None
            while time.time() < deadline:
                job = admin_agent.get_job(jid)
                if job and job["status"] == "done":
                    break
                time.sleep(0.1)
            self.assertIsNotNone(job)
            self.assertEqual(job["status"], "done")
            self.assertIn("память мозга", job["planner"])
            self.assertTrue(job["steps"], "ожидались шаги")
            self.assertIn("up 1 day", json.dumps(job["steps"], ensure_ascii=False))

    def test_change_requires_approval(self):
        with patch.object(admin_agent, "_ext_ready", return_value=False), \
             patch.object(admin_agent.servers, "targets", return_value=self.tg), \
             patch.object(admin_agent.ssh_servers, "get", return_value={"id": "srv1"}), \
             patch.object(admin_agent.servers, "write_file", return_value={"ok": True, "out": "записано"}):
            job = {"id": "t", "task": "запиши файл", "target": "srv1", "mode": "brain",
                   "auto_ok": False, "status": "работает", "steps": [], "pending": None}
            job["_ev"] = threading.Event()
            out = {}
            act = {"tool": "write", "server": "srv1", "path": "~/x.txt", "content": "hi"}
            th = threading.Thread(
                target=lambda: out.__setitem__("res", admin_agent._exec(job, act)), daemon=True)
            th.start()
            deadline = time.time() + 5
            while time.time() < deadline and not job.get("pending"):
                time.sleep(0.05)
            self.assertIsNotNone(job.get("pending"), "агент должен ждать разрешения на запись")
            job["pending"]["decision"] = "no"
            job["_ev"].set()
            th.join(5)
            self.assertFalse(out.get("res", {}).get("ok"), out)
            self.assertIn("не разрешил", str(out.get("res", {}).get("error")))

    def test_desk_disabled(self):
        import desktop
        with patch.object(desktop, "enabled", return_value=False):
            job = {"id": "t", "task": "клик", "target": "", "mode": "brain",
                   "auto_ok": True, "status": "работает", "steps": [], "pending": None}
            job["_ev"] = threading.Event()
            res = admin_agent._exec(job, {"tool": "desk", "act": "click", "fx": 0.5, "fy": 0.5})
            self.assertFalse(res["ok"])
            self.assertIn("Терминал и рабочий стол", res["error"])

    def test_vision_tool_shape(self):
        job = {"id": "t", "task": "камера", "target": "", "mode": "brain",
               "auto_ok": True, "status": "работает", "steps": [], "pending": None}
        job["_ev"] = threading.Event()
        res = admin_agent._exec(job, {"tool": "vision"})
        self.assertTrue(res.get("ok"), res)


class AdminRoutesTest(unittest.TestCase):
    """Маршруты api/admin/agent и api/admin/settings зарегистрированы."""

    def test_routes_present(self):
        self.assertIn(("GET", "api/admin/agent"), api_ui.ROUTES_UI)
        self.assertIn(("POST", "api/admin/agent"), api_ui.ROUTES_UI)
        self.assertIn(("GET", "api/admin/settings"), api_ui.ROUTES_UI)
        self.assertIn(("POST", "api/admin/settings"), api_ui.ROUTES_UI)
        # vision-маршруты живут в api_board
        import api_board
        self.assertIn(("GET", "api/vision/frame.jpg"), api_board.ROUTES)

    def test_settings_roundtrip(self):
        r = admin_agent.start_job  # только чтобы модуль инициализировался
        store.update_settings({"admin_mode": "brain", "agent_auto_ok": True})
        self.assertEqual(store.settings().get("admin_mode"), "brain")
        self.assertTrue(store.settings().get("agent_auto_ok"))


class VisionBrokenFramesTest(unittest.TestCase):
    """v3.189: битые кадры сохраняются файлом, видны в hist и отдаются обратно."""

    def test_broken_frame_ring(self):
        import base64
        # крошечный JPEG-начало без маркера конца — «оборванный» кадр
        head = b"\xff\xd8\xff\xe0" + b"\x00" * 40
        b64 = base64.b64encode(head).decode("ascii")
        r = vision.raw(b64, cam="unittest_cam", broken=1)
        self.assertTrue(r.get("ok"), r)
        self.assertTrue(r.get("broken"))
        self.assertTrue(r.get("why"))
        h = vision.hist("unittest_cam", 12)
        self.assertTrue(h)
        top = h[0]
        self.assertTrue(top["broken"])
        self.assertIn("file", top)
        data = vision.frame_jpeg("unittest_cam", top["file"])
        self.assertTrue(data.startswith(b"\xff\xd8"))
        self.assertEqual(vision.frame_jpeg("unittest_cam", "../../settings.json"), b"")
        self.assertEqual(vision.frame_jpeg("unittest_cam", "last.jpg"), b"")
        # буфер содержит флаг broken и причину
        buf = vision.buffer(4, "unittest_cam")
        self.assertTrue(buf["items"])
        self.assertTrue(buf["items"][0]["broken"])
        self.assertTrue(buf["items"][0].get("why"))

    def test_good_frame_ring(self):
        import base64
        try:
            import io
            from PIL import Image
            im = Image.new("RGB", (8, 8), (10, 200, 30))
            out = io.BytesIO()
            im.save(out, "JPEG", quality=80)
            data = out.getvalue()
        except Exception:
            self.skipTest("нет Pillow")
        b64 = base64.b64encode(data).decode("ascii")
        r = vision.raw(b64, cam="unittest_cam2")
        self.assertTrue(r["ok"])
        self.assertFalse(r["broken"])
        h = vision.hist("unittest_cam2")
        self.assertFalse(h[0]["broken"])
        self.assertEqual(vision.frame_jpeg("unittest_cam2", h[0]["file"]), data)

    def test_cam_page_routes(self):
        import api_board
        self.assertIn(("GET", "api/vision/hist"), api_board.ROUTES)
        self.assertIn(("GET", "api/vision/frame.jpg"), api_board.ROUTES)


class ProjectAgentEffortTest(unittest.TestCase):
    """Усилие агента «Контур»: бюджет шагов 6–36, нейтральная воля."""

    def test_base_steps(self):
        import project_agent
        self.assertEqual(project_agent._base_steps(None), project_agent._MAX_STEPS_BASE)
        self.assertEqual(project_agent._base_steps(1), project_agent._MAX_STEPS_BASE)
        self.assertEqual(project_agent._base_steps(1.5), 36)
        self.assertEqual(project_agent._base_steps(0.6), 14)   # round(24*0.6)=14
        self.assertEqual(project_agent._base_steps(9), 36)     # зажато сверху

    def test_neutral_will(self):
        import project_agent
        w = project_agent._neutral_will(12, "тест")
        self.assertEqual(w["step_limit"], 12)
        self.assertEqual(w["temperature_multiplier"], 1.0)
        self.assertFalse(w["connected"])


if __name__ == "__main__":
    unittest.main()
