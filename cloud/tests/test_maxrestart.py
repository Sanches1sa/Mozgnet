from pathlib import Path
import sys
import time
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'server'))
import test_repairs  # noqa: F401
import maxbot


class RestartTest(unittest.TestCase):
    def test_restart_replaces_loop(self):
        with patch.object(maxbot, "enabled", return_value=False):
            maxbot.start()
            g0 = maxbot._GEN[0]
            old = maxbot._TH[0]
            r = maxbot.restart("тест")
            self.assertTrue(r["ok"])
            self.assertEqual(maxbot._GEN[0], g0 + 1)
            self.assertIsNot(maxbot._TH[0], old)
            for _ in range(80):
                if not old.is_alive():
                    break
                time.sleep(0.1)
            self.assertFalse(old.is_alive())


if __name__ == "__main__":
    unittest.main()
