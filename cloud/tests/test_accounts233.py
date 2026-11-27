import os, sys, tempfile, unittest, json, threading, time, urllib.request, http.cookiejar
os.environ["NEURO_DATA"] = tempfile.mkdtemp()
os.environ["NEURO_NO_SUPERVISOR"] = "1"
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "server"))
import store, accounts, auth  # noqa: E402

OWNER, PW = "alexandrvorobjev1996@gmail.com", "Test-Owner-Pass-1"
# настоящий начальный пароль в тестах не хранится: подменяем хеш владельца тестовым
_salt = os.urandom(16).hex()
accounts._OWNER_SEED = {"salt": _salt, "iter": accounts.ITER, "hash": accounts._hash(PW, _salt)}


class Accounts233(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        store.ensure_dirs()
        accounts.ensure_owner()

    def test_owner_seeded_and_password_not_in_source(self):
        self.assertEqual(accounts.verify(OWNER.upper(), PW)["role"], "owner")
        self.assertIsNone(accounts.verify(OWNER, "wrong"))
        src = open(accounts.__file__, encoding="utf-8").read()
        self.assertNotIn(PW, src)
        self.assertNotIn(PW, json.dumps(store.load_json("accounts.json", {})))

    def test_registration_needs_owner_approval(self):
        r = accounts.register("Ivan@Example.com", "Иван", "longpass1", "для чата", "1.2.3.4")
        self.assertTrue(r["ok"])
        self.assertIsNone(accounts.verify("ivan@example.com", "longpass1"))     # ещё не одобрен
        self.assertEqual(accounts.check("ivan@example.com", "longpass1")["status"], "pending")
        self.assertFalse(accounts.register("ivan@example.com", "x", "longpass1")["ok"])
        self.assertFalse(accounts.register("bad", "x", "longpass1")["ok"])
        self.assertFalse(accounts.register("a@b.ru", "x", "short")["ok"])
        self.assertTrue(any("Заявка на регистрацию" in m["text"] for m in store.chat_load("chat-admin")["messages"]))
        self.assertTrue(accounts.decide("ivan@example.com", "approve")["ok"])
        self.assertEqual(accounts.verify("ivan@example.com", "longpass1")["role"], "user")

    def test_last_owner_protected_and_password_change(self):
        self.assertFalse(accounts.decide(OWNER, "delete")["ok"])
        self.assertFalse(accounts.decide(OWNER, "user")["ok"])
        accounts.register("own2@example.com", "", "longpass2")
        self.assertFalse(accounts.set_password("own2@example.com", "longpass2", "newpass22")["ok"])   # не активен
        accounts.decide("own2@example.com", "approve")
        self.assertTrue(accounts.set_password("own2@example.com", "longpass2", "newpass22")["ok"])
        self.assertIsNone(accounts.verify("own2@example.com", "longpass2"))
        self.assertTrue(accounts.verify("own2@example.com", "newpass22"))


class Http233(unittest.TestCase):
    """Настоящий сервер: вход по почте, регистрация, одобрение, права пользователя."""
    @classmethod
    def setUpClass(cls):
        import app
        cls.port = 18233
        os.environ["NEURO_PORT"] = str(cls.port)
        threading.Thread(target=app.main, daemon=True).start()
        t0 = time.time()
        while time.time() - t0 < 120:                 # готово, когда обычные API перестали отвечать 503
            try:
                urllib.request.urlopen("http://127.0.0.1:%d/api/chat/list" % cls.port, timeout=3)
            except urllib.error.HTTPError as e:
                if e.code != 503:
                    break
            except Exception:
                pass
            time.sleep(0.5)

    def client(self):
        cj = http.cookiejar.CookieJar()
        return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))

    def call(self, op, path, body=None):
        url = "http://127.0.0.1:%d%s" % (self.port, path)
        req = urllib.request.Request(url, json.dumps(body).encode() if body is not None else None,
                                     {"Content-Type": "application/json"})
        try:
            with op.open(req, timeout=60) as r:
                return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    def test_flow(self):
        anon = self.client()
        st, j = self.call(anon, "/api/auth/check")
        self.assertTrue(j["has_password"])
        own = self.client()
        st, j = self.call(own, "/api/auth/login", {"email": OWNER, "password": "nope"})
        self.assertEqual(st, 401)
        st, j = self.call(own, "/api/auth/login", {"email": OWNER, "password": PW})
        self.assertEqual(st, 200, j); self.assertEqual(j["user"]["role"], "owner")
        st, j = self.call(own, "/api/auth/check"); self.assertTrue(j["seed_password"])
        # регистрация и одобрение
        usr = self.client()
        st, j = self.call(usr, "/api/auth/register", {"email": "petr@example.com", "name": "Пётр", "password": "petrpass1"})
        self.assertEqual(st, 200, j)
        st, j = self.call(usr, "/api/auth/login", {"email": "petr@example.com", "password": "petrpass1"})
        self.assertEqual(st, 403); self.assertIn("не одобрена", j["error"])
        st, j = self.call(usr, "/api/auth/users"); self.assertEqual(st, 401)
        st, j = self.call(own, "/api/auth/users"); self.assertTrue(any(u["email"] == "petr@example.com" for u in j["users"]))
        st, j = self.call(own, "/api/auth/users", {"email": "petr@example.com", "action": "approve"}); self.assertTrue(j["ok"])
        st, j = self.call(usr, "/api/auth/login", {"email": "petr@example.com", "password": "petrpass1"})
        self.assertEqual(st, 200); self.assertEqual(j["user"]["role"], "user")
        # права пользователя
        self.assertEqual(self.call(usr, "/api/auth/users")[0], 403)
        self.assertEqual(self.call(usr, "/api/settings")[0], 403)
        self.assertEqual(self.call(usr, "/api/applab/flags", {"id": "math", "enabled": False})[0], 403)
        self.assertEqual(self.call(usr, "/api/admin/agent", {"action": "start", "task": "x"})[0], 403)
        st, j = self.call(usr, "/api/chat/send", {"text": "сколько будет 2+2", "route": "admin"})
        self.assertEqual(st, 200, j); self.assertIn("4", j["reply"])            # админ-агент не запустился, ответил мозг
        cid = j["id"]
        st, j = self.call(usr, "/api/chat/list")
        kinds = {c["kind"] for c in j["chats"]}
        self.assertNotIn("admin", kinds); self.assertNotIn("code", kinds)
        self.assertIn(cid, [c["id"] for c in j["chats"]])
        self.assertEqual(self.call(usr, "/api/chat/get?id=chat-admin")[0], 403)   # служебный чат владельца
        st, j = self.call(own, "/api/chat/list")
        self.assertNotIn(cid, [c["id"] for c in j["chats"]])                      # чат пользователя — его
        # смена пароля владельца
        st, j = self.call(own, "/api/auth/password", {"old": PW, "new": "NewOwnerPass9"})
        self.assertEqual(st, 200, j)
        self.assertEqual(self.call(self.client(), "/api/auth/login", {"email": OWNER, "password": PW})[0], 401)
        self.assertEqual(self.call(self.client(), "/api/auth/login", {"email": OWNER, "password": "NewOwnerPass9"})[0], 200)
        # отклонение выкидывает пользователя
        self.call(own, "/api/auth/users", {"email": "petr@example.com", "action": "reject"})
        self.assertEqual(self.call(usr, "/api/chat/list")[0], 401)


if __name__ == "__main__":
    unittest.main()
