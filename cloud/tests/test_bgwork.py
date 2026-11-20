"""v3.186.2: фоновые очереди и отложенное сохранение (bgwork)."""
import os
import sys
import threading
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "server"))
import bgwork  # noqa: E402


class T(unittest.TestCase):
    def test_coalesce_keeps_latest(self):
        ln = bgwork.Lane("t1", maxlen=10)
        gate = threading.Event()
        got = []
        ln.submit("x", gate.wait)                  # занять поток
        time.sleep(0.05)
        for i in range(5):
            ln.submit("dev", got.append, i, coalesce=True)
        gate.set()
        time.sleep(0.3)
        self.assertEqual(got, [4])
        self.assertEqual(ln.replaced, 4)

    def test_bounded(self):
        ln = bgwork.Lane("t2", maxlen=3)
        gate = threading.Event()
        got = []
        ln.submit(None, gate.wait)
        time.sleep(0.05)
        for i in range(10):
            ln.submit(None, got.append, i)
        gate.set()
        time.sleep(0.3)
        self.assertEqual(got, [7, 8, 9])
        self.assertEqual(ln.dropped, 7)

    def test_debounce(self):
        calls = []
        for i in range(20):
            bgwork.debounce("t3", lambda: calls.append("now"), lambda: calls.append("later"), 0.5)
        self.assertEqual(calls, ["now"])
        time.sleep(6.0)                              # поток сохранения — раз в 5 с
        self.assertEqual(calls, ["now", "later"])
        self.assertEqual(bgwork.flush(), 0)


if __name__ == "__main__":
    unittest.main()
