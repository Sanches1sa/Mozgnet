import os, subprocess, sys, tempfile, textwrap, threading, unittest
from unittest import mock
DATA = tempfile.mkdtemp()
os.environ["NEURO_DATA"] = DATA
SRV = os.path.join(os.path.dirname(__file__), "..", "server")
sys.path.insert(0, SRV)
import store, crashguard, api_ui  # noqa: E402


class Req:
    def __init__(self, body=None, query=None, method="POST"):
        self.body, self.query, self.method, self.client_ip, self.headers = body or {}, query or {}, method, "1.2.3.4", {}


def die_in_process(text, cid):
    """Настоящий процесс: принимает сообщение и умирает посреди ответа (как падение библиотеки)."""
    code = textwrap.dedent('''
        import os, sys
        sys.path.insert(0, %r)
        import store, api_ui, crashguard
        store.ensure_dirs()
        api_ui._chat_process = lambda *a, **k: os._exit(139)
        tok = crashguard.begin("http", "POST /api/chat/send")
        class R: pass
        r = R(); r.body = {"id": %r, "text": %r}; r.query = {}; r.method = "POST"; r.client_ip = "1.2.3.4"; r.headers = {}
        api_ui.h_chat_send(r)
    ''' % (SRV, cid, text))
    p = subprocess.run([sys.executable, "-c", code], env=dict(os.environ, NEURO_DATA=DATA), capture_output=True, timeout=60)
    return p.returncode


class CrashGuard(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        store.ensure_dirs()

    def new_chat(self):
        return store.chat_new(title="t", owner="")["id"]

    def test_real_death_recovery_notice_and_resume(self):
        cid = self.new_chat()
        self.assertEqual(die_in_process("посчитай хитрое", cid), 139)
        self.assertTrue(store.load_json("inflight.json", []))                     # запрос остался в журнале
        r = crashguard.recover()
        self.assertEqual(r["recovered"], 1)
        chat = store.chat_load(cid)
        self.assertEqual(chat["messages"][0]["text"], "посчитай хитрое")           # контекст на месте
        self.assertTrue(any(m.get("crash_notice") for m in chat["messages"]))
        self.assertEqual(chat["lost"]["text"], "посчитай хитрое")
        self.assertEqual(crashguard.resume_text(chat, "повтори"), "посчитай хитрое")
        self.assertEqual(crashguard.resume_text(chat, "а что дальше"), "а что дальше")
        self.assertFalse(store.load_json("inflight.json", []))

    def test_second_death_blocks_request(self):
        cid = self.new_chat()
        for _ in range(2):
            die_in_process("убийца запрос", cid)
            crashguard.recover()
        self.assertTrue(crashguard.is_poison("убийца  ЗАПРОС"))
        called = []
        with mock.patch.object(api_ui, "_chat_process", lambda *a, **k: called.append(1)):
            code, r = api_ui.h_chat_send(Req({"id": cid, "text": "убийца запрос"}))
        self.assertTrue(r["blocked"]); self.assertFalse(called)
        rep = crashguard.report()
        self.assertTrue(any(b["fp"] == crashguard.fingerprint("убийца запрос") for b in rep["blocked"]))
        code, r = api_ui.h_crash_report(Req({"clear": True}))
        self.assertFalse(r["blocked"]); self.assertFalse(crashguard.is_poison("убийца запрос"))

    def test_poison_cleared_on_success(self):
        cid = self.new_chat()
        die_in_process("разовый сбой", cid); crashguard.recover()
        self.assertFalse(crashguard.is_poison("разовый сбой"))                     # одного падения мало
        tok = crashguard.begin("http", "POST /x"); crashguard.annotate(chat=cid, text="разовый сбой")
        crashguard.end(tok, ok=True)
        self.assertNotIn(crashguard.fingerprint("разовый сбой"), store.load_json("poison.json", {}))

    def test_planned_restart_is_not_a_crash(self):
        cid = self.new_chat()
        die_in_process("во время обновления", cid)
        crashguard.mark_planned()
        crashguard.recover()
        self.assertNotIn(crashguard.fingerprint("во время обновления"), store.load_json("poison.json", {}))
        self.assertIn("перезапускалось", [m["text"] for m in store.chat_load(cid)["messages"] if m.get("crash_notice")][0])

    def test_normal_request_leaves_no_inflight(self):
        cid = self.new_chat()
        with mock.patch.object(api_ui, "_chat_process", lambda req, chat, cid, *a: (200, {"ok": True, "id": cid, "reply": "ок"})):
            tok = crashguard.begin("http", "POST /api/chat/send")
            api_ui.h_chat_send(Req({"id": cid, "text": "привет"}))
            crashguard.end(tok)
        self.assertFalse(store.load_json("inflight.json", []))

    def test_setup_stack_and_recursion(self):
        old = threading.stack_size()
        try:
            crashguard.setup()
            self.assertEqual(threading.stack_size(), 8 * 1024 * 1024)
            self.assertGreaterEqual(sys.getrecursionlimit(), 2500)
        finally:
            threading.stack_size(old)

    def test_safe_mode_disables_native_libs(self):
        code = textwrap.dedent('''
            import sys; sys.path.insert(0, %r)
            import tinyllm, hearing, photo_ocr, crashguard
            assert crashguard.safe()
            assert tinyllm.HAVE_NP is False
            assert hearing.have_vosk() is False
            try:
                photo_ocr._deps(); raise SystemExit("ocr не отключён")
            except RuntimeError:
                pass
            print("SAFE-OK")
        ''' % SRV)
        p = subprocess.run([sys.executable, "-c", code], env=dict(os.environ, NEURO_DATA=DATA, NEURO_SAFE="1"), capture_output=True, timeout=120)
        self.assertIn(b"SAFE-OK", p.stdout, p.stderr.decode()[-500:])

    def test_supervisor_enters_safe_mode_after_three_quick_crashes(self):
        import app
        envs, codes = [], iter([1, 1, 1, 1, 0])

        def fake_call(cmd, env=None, **k):
            envs.append(env.get("NEURO_SAFE"))
            return next(codes)
        with mock.patch("subprocess.call", fake_call), mock.patch.object(app.time, "sleep", lambda s: None):
            app._supervise()
        self.assertEqual(envs[:3], [None, None, None]); self.assertEqual(envs[3], "1")
        cr = store.load_json("crashes.json", [])
        self.assertTrue(cr and "inflight" in cr[-1])

    def test_cli_crashes_command(self):
        sys.path.insert(0, os.path.join(SRV, "..", "launcher"))
        import io, contextlib, n12ctl
        a = n12ctl.build_parser("desktop").parse_args(["crashes"])
        a.home_ = os.path.join(SRV, "..")
        buf = io.StringIO()
        with mock.patch.object(n12ctl, "data_dir", lambda h: DATA), contextlib.redirect_stdout(buf):
            self.assertEqual(n12ctl.cmd_crashes(a, "desktop"), 0)
        self.assertIn("Падений процесса", buf.getvalue()); self.assertIn("Оборванных запросов", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
