"""v3.210: HTTPS облака + мысли LLM на D4."""
import os, sys, ssl, json, tempfile, threading, unittest, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
D = tempfile.mkdtemp()
os.environ["NEURO_DATA"] = D
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "server"))
import store, tls  # noqa: E402


class H(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.end_headers(); self.wfile.write(b"hi")
    def log_message(self, *a): pass


class TlsTest(unittest.TestCase):
    def test_selfsigned_and_https(self):
        store.ensure_dirs()
        os.environ["NEURO_TLS_PORT"] = "0"
        srv = tls.start(ThreadingHTTPServer, H, 8077)
        # порт 0 -> «нет» (port<=0 отключает); берём явный свободный
        self.assertIsNone(srv)
        os.environ["NEURO_TLS_PORT"] = "18443"
        srv = tls.start(ThreadingHTTPServer, H, 8077)
        self.assertIsNotNone(srv)
        port = srv.server_address[1]
        cert, _ = tls.paths()
        ctx = ssl.create_default_context(cafile=cert)    # проверка по сертификату
        r = urllib.request.urlopen("https://127.0.0.1:%d/" % port, context=ctx, timeout=5)
        self.assertEqual(r.read(), b"hi")
        self.assertEqual(len(tls.fingerprint()), 64)
        self.assertTrue(tls.status()["on"])
        srv.shutdown()

    def test_d4_box(self):
        store.ensure_dirs()
        self.assertTrue(store.d4_add("b1", "мысль облака про нейроны"))
        got = store.d4_take("b1")
        self.assertEqual(len(got), 1)
        self.assertEqual(store.d4_take("b1"), [])
        for i in range(10):
            store.d4_add("b1", "мысль %d" % i)
        self.assertEqual(len(store.d4_take("b1")), 6)

    def test_llm_d4_all(self):
        import llmlink
        store.boards()
        store.board_seen("zz1", ip="1.1.1.1")
        n = llmlink._d4_all("тест мысли", "z")
        self.assertGreaterEqual(n, 1)
        self.assertTrue(store.d4_take("zz1"))


if __name__ == "__main__":
    unittest.main()
