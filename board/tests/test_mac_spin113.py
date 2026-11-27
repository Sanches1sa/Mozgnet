# -*- coding: utf-8 -*-
"""2.113: вращение MAC в ритме сессии (mac_spin) и поллер _spin_apply в main.py.
MAC не меняем по-настоящему (нет прав в тесте) — проверяем ритм, отчёты, предел.
"""
import os
import sys
import time
import unittest
from unittest import mock

PY = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "python")
sys.path.insert(0, PY)
import mac_spin


class MacForTest(unittest.TestCase):
    def test_deterministic_local_unicast(self):
        m = mac_spin.mac_for("s", "a", 3)
        self.assertEqual(m, mac_spin.mac_for("s", "a", 3))
        self.assertNotEqual(m, mac_spin.mac_for("s", "b", 3))
        first = int(m.split(":")[0], 16)
        self.assertEqual(first & 0x01, 0)
        self.assertEqual(first & 0x02, 0x02)


class RunLoopTest(unittest.TestCase):
    def test_virtual_mode_when_no_sudo(self):
        calls = {"n": 0}
        def stop():
            calls["n"] += 1
            return calls["n"] > 3            # остановиться быстро
        with mock.patch.object(mac_spin, "have_sudo", return_value=False), \
                mock.patch.object(mac_spin, "iface", return_value="eth0"), \
                mock.patch.object(mac_spin, "cur_mac", return_value="aa:bb:cc:00:00:00"), \
                mock.patch.object(mac_spin, "my_ip", return_value="192.168.1.9"), \
                mock.patch.object(mac_spin.time, "sleep", lambda *_: None):
            r = mac_spin.run("seed", 1000.0, 1.0, 2000.0, "alpha",
                             stop=stop, now_fn=lambda: 1000.5)
        self.assertTrue(r.get("virtual"))    # без sudo — виртуальный режим, сеть не трогали
        self.assertTrue(r["ok"])

    def test_steps_follow_cloud_time(self):
        reports = []
        clk = {"t": 1000.0}
        def now():
            v = clk["t"]
            clk["t"] += 0.3          # время растёт на каждый вызов
            return v
        with mock.patch.object(mac_spin, "have_sudo", return_value=False), \
                mock.patch.object(mac_spin, "iface", return_value="eth0"), \
                mock.patch.object(mac_spin, "cur_mac", return_value="aa:bb:cc:00:00:00"), \
                mock.patch.object(mac_spin, "my_ip", return_value="10.0.0.9"), \
                mock.patch.object(mac_spin.time, "sleep", lambda *_: None):
            r = mac_spin.run("seed", 1000.0, 1.0, 1004.0, "alpha",
                             report=lambda **k: reports.append(k), now_fn=now)
        steps = [x["step"] for x in reports if x.get("step", -1) >= 0]
        self.assertTrue(steps, "не было ни одного шага")
        self.assertEqual(steps[0], 0)                             # первый шаг — idx 0
        self.assertEqual(steps, sorted(set(steps)))              # по одному разу, по возрастанию
        self.assertEqual(steps, list(range(steps[0], steps[-1] + 1)))  # без пропусков — синхронный ритм
        for x in reports:
            if x.get("step", -1) >= 0:                            # адрес = общий детерминированный
                self.assertEqual(x["mac"], mac_spin.mac_for("seed", "alpha", x["step"]))
        self.assertTrue(any(x.get("step") == -1 for x in reports))   # финальный отчёт при остановке
        self.assertTrue(r["ok"])

    def test_hard_38min_limit(self):
        # end очень далеко, но локальный жёсткий предел = 38 мин
        self.assertEqual(mac_spin.HARD_LIMIT, 38 * 60)
        base = [5000.0]
        with mock.patch.object(mac_spin, "have_sudo", return_value=False), \
                mock.patch.object(mac_spin, "iface", return_value="eth0"), \
                mock.patch.object(mac_spin, "cur_mac", return_value="aa:bb:cc:00:00:00"), \
                mock.patch.object(mac_spin, "my_ip", return_value=""), \
                mock.patch.object(mac_spin.time, "sleep", lambda *_: None), \
                mock.patch.object(mac_spin.time, "time", side_effect=lambda: base.__setitem__(0, base[0] + 100) or base[0]):
            # now_fn всегда «в пределах», но time.time() улетает за hard_end за несколько итераций
            r = mac_spin.run("seed", 0.0, 1.0, 10 ** 12, "alpha", now_fn=lambda: 1.0)
        self.assertTrue(r["ok"])


class PollerWiringTest(unittest.TestCase):
    def test_main_has_spin_apply(self):
        src = open(os.path.join(PY, "main.py"), encoding="utf-8").read()
        self.assertIn("def _spin_apply(info):", src)
        self.assertIn("/api/spin?board=", src)
        self.assertIn("import mac_spin", src)
        self.assertIn("action=\"report\"", src)
        self.assertIn('"board_id": _hostname()', src)   # alive отдаёт id платы


if __name__ == "__main__":
    unittest.main()
