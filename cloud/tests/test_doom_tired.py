from pathlib import Path
import sys
import time
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'server'))
import test_repairs  # noqa: F401
import boardlink
import doomgame


class TiredTest(unittest.TestCase):
    def test_llm_joins_when_board_tired_and_leaves(self):
        with boardlink._LOCK:
            boardlink._ST["boards"]["b1"] = {"hz": 450.0, "ts": time.time(), "recent": []}
        with patch.object(doomgame, "_llm_loop", lambda: None):
            r = doomgame.bot_call({"board": "b1", "want": {"join": True}})
            self.assertTrue(r["tired"])
            for _ in range(50):
                if doomgame.LLM_PID in doomgame._G["players"]:
                    break
                time.sleep(0.02)
            self.assertIn(doomgame.LLM_PID, doomgame._G["players"])
            self.assertTrue(doomgame._TIRED["auto"])
            # плата вышла из игры -> LLM уходит
            doomgame.bot_call({"board": "b1", "want": {"join": False}})
            doomgame._TIRED["ts"] = 0
            doomgame._tired_check("b1", time.time())
            for _ in range(50):
                if doomgame.LLM_PID not in doomgame._G["players"]:
                    break
                time.sleep(0.02)
            self.assertNotIn(doomgame.LLM_PID, doomgame._G["players"])

    def test_not_tired_above_threshold(self):
        with boardlink._LOCK:
            boardlink._ST["boards"]["b2"] = {"hz": 900.0, "ts": time.time(), "recent": []}
        doomgame._TIRED["ts"] = 0
        r = doomgame.bot_call({"board": "b2", "want": {"join": True}})
        self.assertFalse(r["tired"])


if __name__ == "__main__":
    unittest.main()
