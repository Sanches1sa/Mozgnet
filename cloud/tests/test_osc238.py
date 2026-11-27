# -*- coding: utf-8 -*-
"""v3.238: колебания частиц на отдельных пинах (мюон→D5, гравитон→D7, фотон→A5).
Запуск: python -m unittest tests.test_osc238 -v
"""
import os
import re
import sys
import unittest

if 'NEURO_DATA' not in os.environ:
    import tempfile
    os.environ['NEURO_DATA'] = tempfile.mkdtemp()
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), 'server'))
sys.path.insert(0, HERE)
import test_repairs  # noqa: F401

import oscillations
import store


class OscTest(unittest.TestCase):
    def setUp(self):
        store.update_settings({"osc_on": True, "osc_m": True, "osc_g": True, "osc_p": True, "osc_gain": 1.0})

    def test_current_ranges_and_character(self):
        # по многим моментам времени: гравитон медленный, мюон средний, фотон быстрый
        gs, ms, ps = [], [], []
        for t in range(0, 600, 3):
            o = oscillations.current(now=1000.0 + t)
            for k in ("m", "g", "p"):
                self.assertTrue(0 <= o[k] <= oscillations.MAX_HZ)
            gs.append(o["g"]); ms.append(o["m"]); ps.append(o["p"])
        self.assertLessEqual(max(gs), 4)                   # гравитон — доли..единицы Гц
        self.assertGreater(max(ms), max(gs))               # мюон быстрее гравитона
        self.assertGreater(max(ps), max(ms))               # фотон — самый быстрый
        self.assertLessEqual(max(ps), oscillations.MAX_HZ)

    def test_wire_format(self):
        w = oscillations.wire()
        self.assertRegex(w, r"^m:\d+,g:\d+,p:\d+$")

    def test_global_and_per_particle_off(self):
        store.update_settings({"osc_g": False})
        self.assertEqual(oscillations.current()["g"], 0)   # гравитон выключен
        self.assertGreaterEqual(oscillations.current()["p"], 0)
        store.update_settings({"osc_g": True, "osc_on": False})
        self.assertEqual(oscillations.wire(), "")          # всё выключено -> пустая строка
        self.assertFalse(oscillations.enabled())

    def test_gain_brightens_photon(self):
        store.update_settings({"osc_on": True, "osc_gain": 0.3})
        lo = max(oscillations.current(now=1000.0 + t)["p"] for t in range(0, 60))
        store.update_settings({"osc_gain": 2.0})
        hi = max(oscillations.current(now=1000.0 + t)["p"] for t in range(0, 60))
        self.assertGreater(hi, lo)

    def test_d6_response_includes_osc(self):
        import api_board
        self.assertTrue(re.match(r"^m:\d+,g:\d+,p:\d+$", api_board._osc_wire()))
        store.update_settings({"osc_on": False})
        self.assertEqual(api_board._osc_wire(), "")
        store.update_settings({"osc_on": True})

    def test_routes_registered_board_auth(self):
        import api_board
        self.assertIn(("GET", "api/osc"), api_board.ROUTES)
        self.assertIn(("POST", "api/osc"), api_board.ROUTES)
        self.assertTrue(api_board.ROUTES[("POST", "api/osc")][1])    # только с токеном платы

    def test_status_pins_mapping(self):
        st = oscillations.status()
        self.assertEqual(st["pins"], {"muon": "D5", "grav": "D7", "photon": "A5"})


class SketchWiringTest(unittest.TestCase):
    def test_sketch_has_osc_on_separate_pins(self):
        sk = open(os.path.join(os.path.dirname(os.path.dirname(HERE)), "board", "sketch", "sketch.ino"), encoding="utf-8").read()
        self.assertIn("NEURO_OSC", sk)
        self.assertIn("OSC_PIN[3] = {5, 7, A5}", sk)          # мюон D5, гравитон D7, фотон A5
        self.assertIn('Bridge.provide_safe("osc", rpcOsc)', sk)
        self.assertIn("oscTick();", sk)
        # три частицы — три разных пина (не смешаны на D6, как в particleTick)
        self.assertIn("void oscTick()", sk)


if __name__ == "__main__":
    unittest.main()
