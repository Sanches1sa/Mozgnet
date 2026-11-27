import os, re, sys, time, unittest
from collections import deque
PY = os.path.join(os.path.dirname(__file__), "..", "python")
SRC = open(os.path.join(PY, "main.py"), encoding="utf-8").read()
SK = open(os.path.join(os.path.dirname(__file__), "..", "sketch", "sketch.ino"), encoding="utf-8").read()
PV = open(os.path.join(os.path.dirname(__file__), "..", "sketch", "pwm_voice.h"), encoding="utf-8").read()
i = SRC.index("HW_PINS = ("); j = SRC.index("def _cloud_job_exec(")
CALLS, LOGS = [], []
NS = {"re": re, "time": time, "deque": deque, "CFG": {"hw_on": True}, "log": lambda *a: LOGS.append(a)}


def fake_bridge(method, arg, timeout=4):
    CALLS.append((method, arg))
    return "ok:512" if arg == "a" else "ok:1"
NS["_bridge_call"] = fake_bridge
exec(SRC[i:j], NS)


class Hw211(unittest.TestCase):
    def setUp(self):
        del CALLS[:]
        NS["CFG"]["hw_on"] = True
        NS["_bridge_call"] = fake_bridge

    def test_free_pins_only_and_compact_wire(self):
        r = NS["hw_io"]("W d7 1; a a5 ; PULSE d5 100; r D5; pins")
        self.assertTrue(r["ok"], r)
        self.assertEqual([a for _m, a in CALLS], ["w7=1", "a", "p5=100", "r5"])   # одна команда за вызов
        self.assertIn("a a5 → ok:512", r["out"]); self.assertIn("pins → ok:D5,D7,A5", r["out"])
        del CALLS[:]
        for bad in ("w D4 1", "w D6 1", "w D9 1", "pwm D5 10", "a A0", "w D13 1", "rm -rf /", "w D7 1; w D8 1"):
            r = NS["hw_io"](bad)
            self.assertFalse(r["ok"], bad); self.assertIn("не разрешено", r["out"])
        self.assertEqual(CALLS, [])                                    # до скетча запрещённое не дошло

    def test_off_and_old_sketch(self):
        NS["CFG"]["hw_on"] = False
        self.assertIn("hw_on", NS["hw_io"]("w D7 1")["out"])
        NS["CFG"]["hw_on"] = True
        NS["_bridge_call"] = lambda *a, **k: None
        self.assertIn("NEURO_HW_IO", NS["hw_io"]("a A5")["out"])

    def test_sketch_side_small_and_safe(self):
        self.assertIn('Bridge.provide_safe("hw_io", rpcHwIo)', SK)
        self.assertIn("#define NEURO_HW_IO 1", SK)
        hw = SK[SK.index("String rpcHwIo(String cmd)"):SK.index("#endif  /* NEURO_HW_IO */")]
        self.assertLess(hw.count("\n"), 40)                            # крошечный блок
        for risky in ("toUpperCase", "analogWrite", "static char", " == \"", "String s"):
            self.assertNotIn(risky, hw)
        self.assertEqual(set(re.findall(r"== '\d' \? (\d+) :", hw)), {"5", "7"})   # только D5 и D7 (+ A5 для АЦП)
        self.assertIn("analogRead(A5)", hw)
        self.assertIn("NEURO_PCM_RING_CAPACITY 6144", SK); self.assertIn("NEURO_PCM_RING_CAPACITY 16384", PV)

    def test_job_mode_and_chat_command(self):
        self.assertIn('if mode == "hw":', SRC); self.assertIn('low.startswith(("/пин"', SRC)


if __name__ == "__main__":
    unittest.main()
