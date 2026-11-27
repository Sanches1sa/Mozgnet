import io, json, os, sys, tempfile, unittest, zipfile
from unittest import mock
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "launcher"))
import n12ctl as c  # noqa: E402


def mkzip(path, files):
    with zipfile.ZipFile(path, "w") as z:
        for k, v in files.items():
            z.writestr(k, v)


class Args:
    def __init__(self, **k):
        d = dict(dry_run=False, force=False, auto_rollback=False, keep_going=False, wait=1, jobs=2, report="",
                 only="", names="", init_fleet=False, fleet="", bundle=[])
        d.update(k)
        self.__dict__.update(d)


class N12Ctl(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.cz = os.path.join(self.d, "neuro12-cloud-3_227_0.zip")
        mkzip(self.cz, {"Mozgnet/cloud/server/app.py": "x", "Mozgnet/cloud/VERSION": "3.227.0\n"})
        self.cz_old = os.path.join(self.d, "neuro12-cloud-3_226_0.zip")
        mkzip(self.cz_old, {"Mozgnet/cloud/server/app.py": "x", "Mozgnet/cloud/VERSION": "3.226.0"})
        self.bz = os.path.join(self.d, "neuro12-board-2_110_0.zip")
        mkzip(self.bz, {"Mozgnet/board/app.yaml": "name: neuro_12", "Mozgnet/board/python/main.py": 'APP_VERSION = "2.110.0"\n',
                        "Mozgnet/board/sketch/sketch.ino": "//", "Mozgnet/board/tests/t.py": "no", "Mozgnet/board/UPDATE-1.md": "no",
                        "Mozgnet/board/assets/a.png": "p"})
        self.lb = os.path.join(self.d, "neuro12-start-linux-amd64")
        open(self.lb, "wb").write(b"ELF")

    def test_scan_picks_newest_and_kinds(self):
        b = c.scan_bundle([self.d])
        self.assertEqual(b["cloud"]["version"], "3.227.0")
        self.assertEqual(b["board"]["version"], "2.110.0")
        self.assertIn("launcher:amd64", b)

    def test_stage_board_filters(self):
        w = tempfile.mkdtemp()
        app = c.stage_board(self.bz, w)
        self.assertTrue(os.path.isfile(os.path.join(app, "python", "main.py")))
        self.assertTrue(os.path.isfile(os.path.join(app, "app.yaml")))
        self.assertFalse(os.path.exists(os.path.join(app, "tests")))
        self.assertFalse(os.path.exists(os.path.join(app, "UPDATE-1.md")))

    def test_ssh_and_secret(self):
        t = {"host": "h", "user": "u", "port": 2222, "key": "~/k"}
        self.assertEqual(c.ssh_target(t), "u@h")
        self.assertIn("-p", c.ssh_base(t))
        with mock.patch.object(c, "run", return_value=(0, "")) as r:
            c.scp_put(t, ["a"], "/tmp/x")
        cmd = r.call_args[0][0]
        self.assertEqual(cmd[0], "scp"); self.assertIn("-P", cmd); self.assertNotIn("-p", cmd)
        os.environ["N12_T"] = "sek"
        self.assertEqual(c.secret("env:N12_T"), "sek")
        self.assertEqual(c.shq("a'b"), "'a'\\''b'")

    def test_cloud_via_ssh_ok_and_restart(self):
        calls = []

        def fake_run(cmd, timeout=120, inp=None):
            calls.append(cmd)
            if cmd[0] == "ssh" and "updater.py apply" in cmd[-1]:
                return 0, 'шум\n{"backup": "pre-3.226.0-x", "to": "3.227.0"}'
            return 0, ""
        t = {"name": "vps", "type": "cloud", "via": "ssh", "host": "h", "user": "r", "path": "/opt/n12", "url": "http://h:8077"}
        pkg = c.scan_bundle([self.cz])["cloud"]
        with mock.patch.object(c, "run", fake_run), mock.patch.object(c, "health", return_value={"version": "3.226.0"}), \
                mock.patch.object(c, "wait_health", return_value={"version": "3.227.0"}):
            r = c.update_cloud(t, pkg, Args())
        self.assertTrue(r["ok"], r); self.assertEqual(r["backup"], "pre-3.226.0-x")
        kinds = [x[0] for x in calls]
        self.assertEqual(kinds, ["scp", "ssh", "ssh"])
        self.assertIn("restart", calls[2][-1]); self.assertIn("n12ctl.py", calls[2][-1])

    def test_failed_health_auto_rollback(self):
        calls = []

        def fake_run(cmd, timeout=120, inp=None):
            calls.append(" ".join(cmd))
            if "apply" in calls[-1]:
                return 0, '{"backup": "B1"}'
            return 0, ""
        t = {"name": "v", "type": "cloud", "via": "ssh", "host": "h", "path": "/p"}
        pkg = c.scan_bundle([self.cz])["cloud"]
        with mock.patch.object(c, "run", fake_run), mock.patch.object(c, "health", return_value={"version": "3.226.0"}), \
                mock.patch.object(c, "wait_health", return_value=None):
            r = c.update_cloud(t, pkg, Args(auto_rollback=True))
        self.assertFalse(r["ok"]); self.assertIn("откат выполнен", r["msg"])
        self.assertTrue(any("rollback 'B1'" in x for x in calls))

    def test_same_version_skipped_and_dry_run(self):
        t = {"name": "x", "type": "cloud", "via": "local", "path": "/nope"}
        pkg = c.scan_bundle([self.cz])["cloud"]
        with mock.patch.object(c, "health", return_value={"version": "3.227.0"}):
            r = c.update_cloud(t, pkg, Args())
            self.assertTrue(r["skipped"])
        with mock.patch.object(c, "health", return_value={"version": "3.226.0"}), mock.patch.object(c, "run") as rr:
            r = c.update_cloud(t, pkg, Args(dry_run=True))
            self.assertTrue(r["planned"]); rr.assert_not_called()

    def test_board_flow(self):
        calls = []

        def fake_ssh(t, remote, timeout=600):
            calls.append(remote)
            return 0, "ok" if "mkdir" in remote else "готово"
        scp = []
        t = {"name": "uno", "type": "board", "host": "b", "user": "arduino", "cloud_url": "http://c:8077", "token": "env:N12_TK"}
        os.environ["N12_TK"] = "tok'en"
        pkg = c.scan_bundle([self.bz])["board"]
        with mock.patch.object(c, "ssh_run", fake_ssh), mock.patch.object(c, "scp_put", lambda *a, **k: scp.append((a, k)) or (0, "")):
            r = c.update_board(t, pkg, Args())
        self.assertTrue(r["ok"], r)
        self.assertIn("n12_deploy.sh 'http://c:8077' 'tok'\\''en'", calls[-1])
        srcs = scp[0][0][1]
        self.assertTrue(any(s.endswith("neuro_12.new") for s in srcs)); self.assertTrue(scp[0][1].get("recursive"))
        t2 = dict(t, token="")
        with mock.patch.object(c, "ssh_run", fake_ssh):
            self.assertFalse(c.update_board(t2, pkg, Args())["ok"])

    def test_launcher_local_and_app(self):
        dst = os.path.join(self.d, "bin-neuro12-start")
        open(dst, "w").write("old")
        pkg = c.scan_bundle([self.lb])["launcher:amd64"]
        r = c.update_launcher({"name": "l", "type": "launcher", "via": "local", "path": dst}, pkg, Args())
        self.assertTrue(r["ok"]); self.assertEqual(open(dst, "rb").read(), b"ELF"); self.assertTrue(os.access(dst, os.X_OK))
        apk = os.path.join(self.d, "a.apk"); open(apk, "wb").write(b"x")
        with mock.patch.object(c, "run", return_value=(0, "Success\n")) as rr:
            r = c.update_app({"name": "p", "type": "app", "serial": "S1", "apk": apk}, {"path": apk}, Args())
        self.assertTrue(r["ok"]); self.assertEqual(rr.call_args[0][0][:3], ["adb", "-s", "S1"])

    def test_full_update_order_and_stop_after_cloud_failure(self):
        fleet = os.path.join(self.d, "f.json")
        json.dump({"targets": [
            {"name": "b", "type": "board", "host": "b", "cloud_url": "u", "token": "t"},
            {"name": "c", "type": "cloud", "via": "local", "path": self.d}]}, open(fleet, "w"))
        os.chmod(fleet, 0o600)
        order = []
        a = Args(fleet=fleet, bundle=[self.d])
        with mock.patch.dict(c.HANDLERS, {"cloud": lambda t, p, o: order.append("cloud") or c.res(t, False, "boom"),
                                          "board": lambda t, p, o: order.append("board") or c.res(t, True, "ok")}):
            rc = c.cmd_update(a, "desktop")
            self.assertEqual(order, ["cloud"]); self.assertEqual(rc, 1)       # облако упало — плату не трогаем
            order.clear()
            a.keep_going = True
            c.cmd_update(a, "desktop")
            self.assertEqual(order, ["cloud", "board"])                          # облако — первым
            order.clear()
            a.only, a.keep_going = "board", False
            c.cmd_update(a, "desktop")
            self.assertEqual(order, ["board"])

    def test_missing_package_and_bad_fleet(self):
        fleet = os.path.join(self.d, "g.json")
        json.dump({"targets": [{"name": "p", "type": "app", "serial": "s"}]}, open(fleet, "w"))
        rc = c.cmd_update(Args(fleet=fleet, bundle=[self.cz]), "desktop")
        self.assertEqual(rc, 1)                                                  # пакета apk нет — честная ошибка
        json.dump({"targets": [{"name": "p", "type": "wat"}]}, open(fleet, "w"))
        self.assertEqual(c.cmd_update(Args(fleet=fleet, bundle=[self.cz]), "desktop"), 2)

    def test_init_fleet_example_valid(self):
        for t in c.FLEET_EXAMPLE["targets"]:
            self.assertIn(t["type"], c.HANDLERS)

    def test_find_home_and_parser(self):
        self.assertTrue(c.find_home())
        for prof in ("desktop", "server"):
            p = c.build_parser(prof)
            self.assertEqual(p.parse_args(["status", "--json"]).cmd, "status")
        self.assertEqual(c.build_parser("server").parse_args(["firewall", "open"]).action, "open")
        with self.assertRaises(SystemExit):
            c.build_parser("desktop").parse_args(["backup"])


if __name__ == "__main__":
    unittest.main()
