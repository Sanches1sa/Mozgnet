from pathlib import Path
import base64
import io
import sys
import unittest
import wave
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'server'))
import test_repairs  # noqa: F401  (временный каталог данных)
import micstream


class MicStreamTest(unittest.TestCase):
    def test_live_stream_roundtrip(self):
        self.assertEqual(micstream.want_for("b1"), 0)
        micstream.set_want("b1", True, 60)
        self.assertGreater(micstream.want_for("b1"), 0)
        pcm = b"\x10\x00\xf0\xff" * 8000          # 1 с при 16 кГц
        r = micstream.stream_in("b1", base64.b64encode(pcm).decode(), 16000, 1, 100)
        self.assertTrue(r["ok"])
        self.assertFalse(r["stop"])
        w = wave.open(io.BytesIO(micstream.live_wav("b1", 0.5)))
        self.assertEqual(w.getframerate(), 16000)
        self.assertEqual(w.getnframes(), 8000)
        micstream.note_link("b1", {"state": "слушаю", "rms": 200}, {"state": "нет кадра", "uart": {"bytes": 0}})
        st = micstream.status()
        b = [x for x in st["boards"] if x["board"] == "b1"][0]
        self.assertEqual(b["mic"]["state"], "слушаю")
        self.assertEqual(b["cam"]["uart"]["bytes"], 0)
        self.assertEqual(b["live"]["sec"], 1.0)
        micstream.set_want("b1", False)
        self.assertTrue(micstream.stream_in("b1", base64.b64encode(pcm).decode())["stop"])

    def test_bad_input(self):
        self.assertFalse(micstream.stream_in("b2", "")["ok"])
        self.assertFalse(micstream.stream_in("b2", base64.b64encode(b"ab").decode(), 1000)["ok"])

    def test_phrase_saved(self):
        micstream.save_phrase("b3/../x", b"RIFF1234")
        self.assertEqual(micstream.phrase_wav("b3/../x"), b"RIFF1234")


if __name__ == "__main__":
    unittest.main()
