# -*- coding: utf-8 -*-
"""2.112: плата скачивает свою LLM облака (/api/llm/bundle), запускает её
(board_llm.py, чистый Python без numpy) и сообщает итог; пакет «дружбы сетей»
из ящика D6 -> отклик /api/netbus/post. Облако подменено локальным HTTP-сервером."""
import http.server
import io
import json
import os
import random
import re
import sys
import tempfile
import threading
import time
import unittest
import zipfile
from array import array

PY = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "python")
sys.path.insert(0, PY)
SRC = open(os.path.join(PY, "main.py"), encoding="utf-8").read()
i = SRC.index("_BLLM = {"); j = SRC.index("def _cloud_d6(m):")
TMP = tempfile.mkdtemp()
POSTS, LOGS = [], []


def _bundle():
    D, L, H, T = 8, 1, 2, 12
    vocab = {"<pad>": 0, "<bos>": 1, "<eos>": 2, "<unk>": 3}
    for w in ["мозг", "плата", "свет", "сеть", "и"]:
        vocab["▁" + w] = len(vocab)
    V = len(vocab)
    rnd = random.Random(5)
    shapes = [("E", [V, D]), ("P", [T, D]), ("lnf_g", [D]), ("lnf_b", [D])]
    for nm in "qkvo":
        shapes += [("W%s0" % nm, [D, D]), ("b%s0" % nm, [D])]
    shapes += [("ln1g0", [D]), ("ln1b0", [D]), ("ln2g0", [D]), ("ln2b0", [D]),
               ("W1_0", [D, 4 * D]), ("b1_0", [4 * D]), ("W2_0", [4 * D, D]), ("b2_0", [D])]
    w, tensors, off = array("f"), [], 0
    for n, sh in shapes:
        cnt = 1
        for x in sh:
            cnt *= x
        vals = [1.0] * cnt if n.endswith("g") or n.endswith("g0") else [rnd.gauss(0, .4) for _ in range(cnt)]
        w.extend(vals)
        tensors.append([n, sh, off, cnt])
        off += cnt
    model = {"format": "neuro12-llm-1", "c": {"llm_d": D, "llm_layers": L, "llm_heads": H, "llm_ctx": T},
             "vocab": vocab, "merges": [], "steps": 5, "loss": 2.0, "tensors": tensors, "sig": "abc123"}
    bio = io.BytesIO()
    with zipfile.ZipFile(bio, "w") as zf:
        zf.writestr("model.json", json.dumps(model, ensure_ascii=False))
        zf.writestr("weights.f32", w.tobytes())
        zf.writestr("README.txt", "x")
        zf.writestr("board_llm.py", "# в пакете; плата берёт свою копию")
    return bio.getvalue()


BLOB = _bundle()


class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path.startswith("/api/llm/bundle") and self.headers.get("X-Mozg-Token") == "tok":
            self.send_response(200); self.send_header("Content-Length", str(len(BLOB))); self.end_headers()
            self.wfile.write(BLOB)
        else:
            self.send_response(403); self.end_headers()

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        POSTS.append((self.path, json.loads(self.rfile.read(n).decode("utf-8"))))
        b = b'{"ok": true}'
        self.send_response(200); self.send_header("Content-Length", str(len(b))); self.end_headers()
        self.wfile.write(b)


SRV = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
threading.Thread(target=SRV.serve_forever, daemon=True).start()
URL = "http://127.0.0.1:%d" % SRV.server_address[1]


def _http_json_err(url, payload, token="", timeout=30):
    import urllib.request
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json",
                                                                                   "X-Mozg-Token": token})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode()), ""


NS = {"os": os, "time": time, "threading": threading, "json": json, "re": re, "APP_VERSION": "2.112.0",
      "_mem_path": lambda: os.path.join(TMP, "mem.json"), "_cloud_base": lambda: (URL, "tok"),
      "_hostname": lambda: "uno1", "log": lambda *a: LOGS.append(a), "_http_json_err": _http_json_err}
import board_llm
board_llm._np = None                      # как на плате без numpy
exec(SRC[i:j], NS)


class Llm212(unittest.TestCase):
    def test_push_downloads_runs_and_reports(self):
        NS["_llm_push_on"]({"sig": "abc123"})
        t0 = time.time()
        while time.time() - t0 < 20 and NS["_BLLM"]["busy"]:
            time.sleep(0.05)
        self.assertIsNotNone(NS["_BLLM"]["m"], NS["_BLLM"]["err"])
        self.assertFalse(NS["_BLLM"]["m"].np)
        st = [b for p, b in POSTS if p == "/api/llm/board_state"]
        self.assertTrue(st and st[-1]["ok"], st)
        self.assertEqual(st[-1]["sig"], "abc123")
        self.assertTrue(os.path.isfile(os.path.join(TMP, "mozg_llm", "weights.f32")))
        n = len(POSTS)
        NS["_llm_push_on"]({"sig": "abc123"})            # та же версия — второй раз не качает
        time.sleep(0.3)
        self.assertEqual(len(POSTS), n)
        out = NS["_BLLM"]["m"].generate("мозг плата", max_new=5, seed=2)
        self.assertIsInstance(out, str)

    def test_netbus_reply(self):
        NS["_netbus_reply"]("nb123", [("свет", 1.0), ("сеть", 0.5)])
        p = [b for pp, b in POSTS if pp == "/api/netbus/post"][-1]
        self.assertEqual(p["re"], "nb123"); self.assertEqual(p["keys"], ["свет", "сеть"]); self.assertEqual(p["board"], "uno1")

    def test_wiring(self):
        self.assertIn('_llm_push_on(r6.get("llm_push"))', SRC)
        self.assertIn('if m.get("nb"):', SRC)
        self.assertIn('low.startswith(("/ллм", "/llm"))', SRC)


if __name__ == "__main__":
    unittest.main()
