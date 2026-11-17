from pathlib import Path
import base64
import gzip
import json
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'server'))
import test_repairs  # noqa: F401
import boardsafe


def blob(d):
    return base64.b64encode(gzip.compress(json.dumps(d).encode())).decode()


class SafeTest(unittest.TestCase):
    def test_backup_and_restore_fresh_board(self):
        self.assertTrue(boardsafe.backup_in("oldbox", blob({"neuro12_memory.json": "{\"a\":1}"}))["ok"])
        with patch.object(boardsafe, "_online_boards", return_value={"newbox"}):
            r = boardsafe.restore_for("newbox", fresh=True)
        self.assertTrue(r["ok"])
        self.assertEqual(r["from"], "oldbox")
        files = json.loads(gzip.decompress(base64.b64decode(r["blob"])))
        self.assertIn("neuro12_memory.json", files)
        with patch.object(boardsafe, "_online_boards", return_value={"oldbox", "newbox"}):
            self.assertFalse(boardsafe.restore_for("newbox", fresh=True)["ok"])  # oldbox online -> not it

    def test_pairing_needs_owner(self):
        r = boardsafe.pair_request("b9", "1234", "10.0.0.5", "2.95.0")
        self.assertTrue(r.get("wait"))
        self.assertNotIn("token", r)
        boardsafe.pair_decide("b9", "1234", True)
        r = boardsafe.pair_request("b9", "1234", "10.0.0.5", "2.95.0")
        self.assertIn("token", r)

    def test_bad_blob(self):
        self.assertFalse(boardsafe.backup_in("x", "@@")["ok"])


if __name__ == "__main__":
    unittest.main()
