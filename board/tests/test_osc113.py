# -*- coding: utf-8 -*-
"""2.113: плата выводит колебания облака на пины (_osc_apply -> RPC osc),
и спин меняет MAC через sudo -n (NetworkManager cloned-mac)."""
import os
import sys
import unittest
from unittest import mock

PY = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "python")
sys.path.insert(0, PY)
SRC = open(os.path.join(PY, "main.py"), encoding="utf-8").read()
i = SRC.index('_OSC = {"last": None}')
j = SRC.index("def brainmail_worker():")
CALLS = []
NS = {"time": __import__("time"), "log": lambda *a: None,
      "_bridge_call": lambda method, *a, **k: CALLS.append((method,) + a) or "ok"}
exec(SRC[i:j], NS)


class OscApplyTest(unittest.TestCase):
    def setUp(self):
        del CALLS[:]
        NS["_OSC"]["last"] = None

    def test_applies_and_dedups(self):
        NS["_osc_apply"]("m:12,g:2,p:40")
        self.assertEqual(CALLS, [("osc", "m:12,g:2,p:40")])
        NS["_osc_apply"]("m:12,g:2,p:40")               # то же — второй раз не шлём
        self.assertEqual(len(CALLS), 1)
        NS["_osc_apply"]("m:20,g:2,p:40")               # изменилось — шлём
        self.assertEqual(len(CALLS), 2)

    def test_empty_turns_pins_off(self):
        NS["_osc_apply"]("m:5,g:1,p:9")
        del CALLS[:]
        NS["_osc_apply"]("")                             # облако выключило -> погасить
        self.assertEqual(CALLS, [("osc", "m:0,g:0,p:0")])


class MacSpinSudoTest(unittest.TestCase):
    def test_rotate_uses_nm_cloned_mac_under_sudo(self):
        import mac_spin
        ran = []

        def fake_run(cmd, to=20):
            ran.append(cmd)
            if cmd[:2] == ["nmcli", "-v"]:
                return 0, "nmcli 1.0"
            if cmd[:3] == ["nmcli", "-g", "GENERAL.CONNECTION"]:
                return 0, "Wired connection 1\n"
            if cmd[:3] == ["nmcli", "-g", "GENERAL.TYPE"]:
                return 0, "ethernet\n"
            if cmd[:4] == ["sudo", "-n", "nmcli", "connection"]:
                return 0, ""
            return 0, ""
        with mock.patch.object(mac_spin, "have_sudo", return_value=True), \
                mock.patch.object(mac_spin, "_has_nm", return_value=True), \
                mock.patch.object(mac_spin, "_run", side_effect=fake_run):
            ok, how = mac_spin.set_mac("eth0", "aa:bb:cc:dd:ee:02")
        self.assertTrue(ok)
        self.assertIn("nmcli", how)                     # пошёл по пути NetworkManager cloned-mac
        self.assertTrue(any(c[:4] == ["sudo", "-n", "nmcli", "connection"] and "802-3-ethernet.cloned-mac-address" in c for c in ran))

    def test_falls_back_to_ip_link(self):
        import mac_spin
        with mock.patch.object(mac_spin, "have_sudo", return_value=True), \
                mock.patch.object(mac_spin, "_has_nm", return_value=False), \
                mock.patch.object(mac_spin, "_run", return_value=(0, "")) as r:
            ok, how = mac_spin.set_mac("eth0", "aa:bb:cc:dd:ee:02")
        self.assertTrue(ok)
        self.assertEqual(how, "ip link")

    def test_no_sudo_is_virtual(self):
        import mac_spin
        with mock.patch.object(mac_spin, "have_sudo", return_value=False):
            ok, how = mac_spin.set_mac("eth0", "aa:bb:cc:dd:ee:02")
        self.assertFalse(ok)
        self.assertEqual(how, "no-sudo")


class ChatCommandTest(unittest.TestCase):
    def test_main_has_osc_command(self):
        self.assertIn('low.startswith(("/колеб", "/osc", "/частиц"))', SRC)
        self.assertIn("мюон → D5", SRC)


if __name__ == "__main__":
    unittest.main()
