import os, sys, tempfile, unittest
os.environ["NEURO_DATA"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "server"))
import store, agent_instructions as instructions, brainplan, admin_agent, admincorpus  # noqa: E402


class Instr219(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        store.ensure_dirs()
        cls.items = instructions.load_all()
        instructions.seed_kb(cls.items)

    def test_library_valid_and_large(self):
        st = instructions.status()
        self.assertGreaterEqual(st["count"], 150)
        self.assertEqual(st["invalid"], [])
        for t in ("board", "cloud", "linux", "windows"):
            self.assertGreater(st["by_target"].get(t, 0), 10, t)
        self.assertEqual(st["verified"], 0)             # честно: на железе не проверено
        for i in self.items:
            self.assertIn("не проверено", instructions.lesson_text(i))

    def test_cloud_ops_exist_and_risks(self):
        for i in self.items:
            for s in i["steps"]:
                if s["tool"] == "cloud":
                    self.assertIn(s["op"], admin_agent._CLOUD_OPS, i["id"])
        byid = {i["id"]: i for i in self.items}
        for k in ("bd.reboot", "lx.reboot", "wn.reboot", "lx.ssh.harden", "cl.rollback"):
            self.assertEqual(byid[k]["risk"], "danger", k)

    def test_roundtrip_actions_and_meta(self):
        i = next(x for x in self.items if x["id"] == "bd.reboot")
        t = instructions.lesson_text(i)
        meta = brainplan.parse_meta(t)
        self.assertEqual(meta.get("risk"), "danger")
        acts = brainplan.actions_from_text(t, False)
        self.assertTrue(any("reboot" in str(a.get("command")) for a in acts))
        self.assertTrue(any(a["tool"] == "wait" for a in acts))

    def test_recall_finds_board_reboot(self):
        found = brainplan.recall("перезагрузи плату", False)
        self.assertTrue(found)
        self.assertIn("плат", found[0][0].lower())

    def test_slots_fill_and_missing(self):
        i = next(x for x in self.items if x["id"] == "lx.svc.restart")
        meta = {"slots": i["slots"]}
        a, miss = brainplan.fill_slots({"tool": "run", "command": "systemctl is-active {service}"}, meta, "перезапусти службу nginx")
        self.assertEqual(miss, []); self.assertIn("nginx", a["command"])
        a, miss = brainplan.fill_slots({"tool": "run", "command": "systemctl is-active {service}"}, meta, "перезапусти что-нибудь")
        self.assertIsNone(a); self.assertEqual(miss, ["service"])
        a, miss = brainplan.fill_slots({"tool": "run", "command": "x {service}"}, meta, "служба nginx; rm -rf /")
        self.assertTrue(a is None or ";" not in a["command"])

    def test_os_filter(self):
        lin = brainplan.recall("состояние сервера", False)
        win = brainplan.recall("состояние компьютера windows", True)
        self.assertTrue(all("Get-" not in str(a.get("command")) for _t, _s, acts in lin for a in acts))
        self.assertTrue(all("systemctl" not in str(a.get("command")) for _t, _s, acts in win for a in acts))

    def test_corpus_for_llm(self):
        c = instructions.corpus(self.items)
        self.assertGreaterEqual(len(c), len(self.items) * 3)
        self.assertEqual(len(set(s for s, _ in c)), len(c))
        self.assertIn("Шаг:", instructions.corpus_text(self.items))
        self.assertGreaterEqual(len(admincorpus.texts()), len(self.items))

    def test_import_items(self):
        bad = {"id": "x", "title": "t"}
        good = {"id": "u.test", "title": "Тест", "ask": ["сделай тест"], "target": "any", "risk": "read",
                "steps": [{"tool": "run", "command": "echo ok"}]}
        r = instructions.import_items([bad, good])
        self.assertTrue(r.get("saved", r.get("ok")) or r)
        self.assertTrue(any(x["id"] == "u.test" for x in instructions.load_all()))

    def test_api_reference_present(self):
        import json
        p = os.path.join(os.path.dirname(__file__), "..", "server", "platform_api.json")
        self.assertGreater(len(json.load(open(p, encoding="utf-8"))), 100)


if __name__ == "__main__":
    unittest.main()
