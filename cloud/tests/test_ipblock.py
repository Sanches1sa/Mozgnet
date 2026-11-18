from pathlib import Path
import socket
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'server'))
import ipblock


class BlockTest(unittest.TestCase):
    def test_block_and_guard(self):
        ipblock.reload(["157.22.190.54", "10.9.9.0/24"])
        self.assertTrue(ipblock.blocked("157.22.190.54"))
        self.assertTrue(ipblock.blocked("10.9.9.7"))
        self.assertFalse(ipblock.blocked("192.168.1.10"))
        ipblock.install()
        ipblock.reload(["157.22.190.54"])
        s = socket.socket()
        try:
            with self.assertRaises(ConnectionRefusedError):
                s.connect(("157.22.190.54", 8077))
        finally:
            s.close()


if __name__ == "__main__":
    unittest.main()
