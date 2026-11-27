# -*- coding: utf-8 -*-
"""v3.238: «позвонить плате» — синхронный спин MAC двух плат.
Запуск: python -m unittest tests.test_spin238 -v
"""
import os
import sys
import time
import unittest

if 'NEURO_DATA' not in os.environ:
    import tempfile
    os.environ['NEURO_DATA'] = tempfile.mkdtemp()
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), 'server'))
sys.path.insert(0, HERE)
import test_repairs  # noqa: F401

import spin
import store


def _reg(bid, seen_ago=0.0):
    store.board_touch(bid, name=bid.upper(), kind="board", url="http://192.168.1.%d:7000" % (10 + hash(bid) % 50), ip="192.168.1.5")
    bs = store.boards()
    bs[bid]["last_seen"] = time.time() - seen_ago
    store.save_json("boards.json", bs)


class SpinTest(unittest.TestCase):
    def setUp(self):
        store.save_json("spin.json", {"sessions": {}})
        store.save_json("boards.json", {})
        _reg("alpha")
        _reg("beta")

    def test_mac_for_deterministic_and_local_unicast(self):
        m1 = spin.mac_for("seed123", "alpha", 5)
        m2 = spin.mac_for("seed123", "alpha", 5)
        self.assertEqual(m1, m2)
        self.assertNotEqual(m1, spin.mac_for("seed123", "beta", 5))     # разные платы — разные адреса
        self.assertNotEqual(m1, spin.mac_for("seed123", "alpha", 6))    # разные шаги — разные адреса
        first = int(m1.split(":")[0], 16)
        self.assertEqual(first & 0x01, 0)                              # одноадресный
        self.assertEqual(first & 0x02, 0x02)                          # локально заданный

    def test_call_creates_session_and_marks_busy(self):
        r = spin.call("alpha", "beta", period=5, owner="test")
        self.assertTrue(r["ok"], r)
        s = r["session"]
        self.assertEqual(sorted([s["a"], s["b"]]), ["alpha", "beta"])
        self.assertEqual(s["period"], 5)
        self.assertLessEqual(s["end"] - s["start"], spin.MAX_TTL + 0.1)   # не дольше 38 минут
        self.assertAlmostEqual(s["end"] - s["start"], spin.MAX_TTL, delta=0.1)
        bz = spin.busy_boards()
        self.assertIn("alpha", bz)
        self.assertIn("beta", bz)
        bl = {b["id"]: b for b in spin.boards()}
        self.assertTrue(bl["alpha"]["busy"] and bl["beta"]["busy"])

    def test_cannot_call_busy_or_self_or_offline(self):
        spin.call("alpha", "beta", 5)
        _reg("gamma")
        self.assertFalse(spin.call("alpha", "gamma", 5)["ok"])         # alpha занята
        self.assertFalse(spin.call("beta", "gamma", 5)["ok"])          # beta занята
        self.assertFalse(spin.call("gamma", "gamma", 5)["ok"])         # сама себе
        _reg("delta", seen_ago=999)                                   # не на связи
        self.assertFalse(spin.call("gamma", "delta", 5)["ok"])
        self.assertFalse(spin.call("gamma", "zzz", 5)["ok"])          # нет в реестре
        self.assertTrue(spin.call("gamma", "alpha", 5)["ok"] is False)  # alpha всё ещё занята
        # пока gamma свободна и delta офлайн — но gamma c beta нельзя (beta занята)

    def test_period_clamped(self):
        self.assertEqual(spin.call("alpha", "beta", period=0.1)["session"]["period"], spin.MIN_PERIOD)
        store.save_json("spin.json", {"sessions": {}})
        self.assertEqual(spin.call("alpha", "beta", period=99999)["session"]["period"], spin.MAX_PERIOD)

    def test_session_for_both_boards_synchronous(self):
        r = spin.call("alpha", "beta", period=5)
        sa = spin.session_for("alpha")
        sb = spin.session_for("beta")
        self.assertTrue(sa["active"] and sb["active"])
        # общий seed, старт и период -> обе платы считают один и тот же шаг
        self.assertEqual(sa["seed"], sb["seed"])
        self.assertEqual(sa["start"], sb["start"])
        self.assertEqual(sa["period"], sb["period"])
        self.assertEqual(sa["partner"], "beta")
        self.assertEqual(sb["partner"], "alpha")
        self.assertFalse(spin.session_for("someone")["active"])

    def test_report_shares_partner_and_predicts(self):
        spin.call("alpha", "beta", period=5)
        # beta ещё не отчиталась: alpha получает ПРЕДСКАЗАННЫЙ адрес beta (из seed)
        ra = spin.report("alpha", mac="aa:bb:cc:dd:ee:02", ip="192.168.1.21", step=0)
        self.assertTrue(ra["active"])
        self.assertEqual(ra["partner"]["id"], "beta")
        self.assertTrue(ra["partner"]["mac"])                        # предсказан из seed
        # beta отчитывается: теперь alpha видит реальный адрес и IP beta
        spin.report("beta", mac="aa:bb:cc:dd:ee:04", ip="192.168.1.22", step=3)
        ra2 = spin.report("alpha", mac="aa:bb:cc:dd:ee:06", ip="192.168.1.21", step=4)
        self.assertEqual(ra2["partner"]["ip"], "192.168.1.22")
        self.assertEqual(ra2["partner"]["step"], 3)

    def test_hangup_frees_boards(self):
        r = spin.call("alpha", "beta", period=5)
        sid = r["session"]["id"]
        self.assertTrue(spin.hangup(sid=sid)["stopped"])
        self.assertNotIn("alpha", spin.busy_boards())
        self.assertFalse(spin.session_for("alpha")["active"])
        # после отбоя можно звонить снова
        self.assertTrue(spin.call("alpha", "beta", period=5)["ok"])

    def test_expired_session_auto_frees(self):
        r = spin.call("alpha", "beta", period=5)
        d = store.load_json("spin.json", {})
        d["sessions"][r["session"]["id"]]["end"] = time.time() - 1     # уже истекла
        store.save_json("spin.json", d)
        self.assertFalse(spin.session_for("alpha")["active"])
        self.assertNotIn("alpha", spin.busy_boards())

    def test_routes_registered_board_auth(self):
        import api_board
        self.assertIn(("GET", "api/spin"), api_board.ROUTES)
        self.assertIn(("POST", "api/spin"), api_board.ROUTES)
        self.assertTrue(api_board.ROUTES[("GET", "api/spin")][1])     # только с токеном платы


class MacParityTest(unittest.TestCase):
    def test_board_mac_spin_matches_cloud(self):
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(HERE)), "board", "python"))
        import mac_spin
        for idx in (0, 1, 7, 100):
            self.assertEqual(mac_spin.mac_for("abc", "alpha", idx), spin.mac_for("abc", "alpha", idx))


if __name__ == "__main__":
    unittest.main()
