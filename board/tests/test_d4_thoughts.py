import os, re, unittest
SRC = open(os.path.join(os.path.dirname(__file__), "..", "python", "main.py"), encoding="utf-8").read()
i = SRC.index("def _text_to_d4_frames"); j = SRC.index("_D4T = ")
NS = {"re": re}; exec(SRC[i:j], NS)


class D4(unittest.TestCase):
    def test_frames(self):
        f = NS["_text_to_d4_frames"]("Привет мир " * 30)
        self.assertTrue(f and len(f) <= 6)
        txt = b""
        for h in f:
            self.assertEqual(len(h), 192)
            b = bytes.fromhex(h); txt += b[1:1 + b[0]]
        txt.decode("utf-8")                    # куски не рвут символы
    def test_wired(self):
        self.assertIn("/api/brain/d4?board=", SRC)
        self.assertIn('"d4pure"', SRC[SRC.index("def _cloud_d4"):][:2500])
        self.assertIn("_breath_profile()", SRC[SRC.index("def _cloud_d4"):][:2500])
    def test_https_pin(self):
        self.assertIn("def _pin_https_setup", SRC)

if __name__ == "__main__":
    unittest.main()
