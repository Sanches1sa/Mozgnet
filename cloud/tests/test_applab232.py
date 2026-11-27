import os, sys, tempfile, unittest
from unittest import mock
os.environ["NEURO_DATA"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "server"))
import store, pyapps, applab_brain, api_ui  # noqa: E402


class R:
    def __init__(self, body):
        self.body, self.query, self.method, self.client_ip, self.headers = body, {}, "POST", "1", {}


class AppLab232(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        store.ensure_dirs()
        store.update_settings({"web_on": False})

    def new_app(self):
        return applab_brain.write("апп лаб по развернутому предложению что делать с инцидентом по информационной безопасности", internet=False)["id"]

    def test_enable_survives_windows_file_lock(self):
        aid = self.new_app()
        real, n = os.replace, {"fail": 3}

        def flaky(a, b):
            if str(b).endswith("app.json") and n["fail"] > 0:
                n["fail"] -= 1
                raise PermissionError(13, "Процесс не может получить доступ к файлу")
            return real(a, b)
        with mock.patch("os.replace", flaky):
            code, r = api_ui.h_applab_flags(R({"id": aid, "enabled": True}))
        self.assertTrue(r["ok"], r); self.assertTrue(pyapps.get(aid)["enabled"]); self.assertEqual(n["fail"], 0)
        d = os.path.dirname(pyapps._code_path(aid))
        self.assertFalse([f for f in os.listdir(os.path.dirname(d)) if f.endswith(".tmp")])   # без мусора

    def test_flags_error_is_reported_not_silent(self):
        aid = self.new_app()
        with mock.patch("os.replace", side_effect=PermissionError(13, "занято")), mock.patch("time.sleep"):
            code, r = api_ui.h_applab_flags(R({"id": aid, "enabled": True}))
        self.assertFalse(r["ok"]); self.assertIn("не записалось", r["error"])
        self.assertFalse(pyapps.get(aid)["enabled"])

    def test_rename_api_and_chat(self):
        aid = self.new_app()
        code, r = api_ui.h_applab_rename(R({"id": aid, "name": "  Разбор инцидента ИБ "}))
        self.assertEqual(code, 200); self.assertEqual(pyapps.get(aid)["name"], "Разбор инцидента ИБ")
        self.assertTrue(pyapps.get(aid, with_code=True)["code"])                # код и id на месте
        out = applab_brain.handle("переименуй приложение %s в «Инцидент: чек-лист»" % aid)
        self.assertIn("Переименовала", out[0]); self.assertEqual(pyapps.get(aid)["name"], "Инцидент: чек-лист")
        self.assertEqual(api_ui.h_applab_rename(R({"id": aid, "name": "  "}))[0], 400)
        self.assertEqual(api_ui.h_applab_rename(R({"id": "net_takogo", "name": "x"}))[0], 400)

    def test_web_has_rename_and_checked_flags(self):
        js = open(os.path.join(os.path.dirname(__file__), "..", "web", "app.js"), encoding="utf-8").read()
        self.assertIn("data-ren=", js); self.assertIn("/api/applab/rename", js); self.assertIn("Не переключилось", js)


if __name__ == "__main__":
    unittest.main()
