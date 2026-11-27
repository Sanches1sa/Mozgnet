import io, os, sys, tempfile, time, unittest, zipfile, ast
os.environ["NEURO_DATA"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "server"))
import store, project_agent, project_brain, admincorpus  # noqa: E402

BAD = '''"""Демо."""
import os
import json


def add(x, items=[], opts={}):
    """Добавить."""
    items.append(x)
    return items


def load(path):
    f = open(path)
    try:
        return f.read()
    except:
        return None


def check(v):
    if v == None:
        return 1
    if v != None:
        return 2   
'''


def mkzip(files):
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        for k, v in files.items():
            z.writestr(k, v)
    return b.getvalue()


def run_job(task, files=None, base=""):
    owner = "t"
    pr = project_agent.upload(mkzip(files or {"app/main.py": BAD, "README.md": "# Демо\nпроект"}), "p.zip", owner)
    jid = project_agent.start(pr["id"], task, owner, internet=False, model="brain")
    for _ in range(100):
        j = project_agent.status(jid, owner)
        if j["done"]:
            return j, owner
        time.sleep(0.1)
    raise AssertionError("не завершилось")


def result_files(j, owner):
    path = project_agent._base("jobs", j["id"]) / "work"
    return {p.relative_to(path).as_posix(): p.read_text(encoding="utf-8") for p in path.rglob("*") if p.is_file()}


class Contour221(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        store.ensure_dirs()

    def test_library(self):
        st = project_brain.status()
        self.assertGreaterEqual(st["count"], 100)
        self.assertEqual([i["id"] for i in project_brain.load_all() if project_brain.validate(i)], [])
        self.assertGreaterEqual(len(project_brain.corpus()), 200)

    def test_match(self):
        cases = {"сделай ревью кода": "ct.review.full", "исправь изменяемые аргументы по умолчанию": "ct.fix.defaults",
                 "добавь encoding в open": "ct.fix.encoding", "добавь тесты": "ct.tests", "найди todo": "ct.review.todo",
                 "добавь в проект функцию факториал": "ct.l.num.factorial"}
        for q, want in cases.items():
            m = project_brain.match(q, 3)
            self.assertTrue(m and m[0][1]["id"] == want, "%s -> %s" % (q, [(s, i["id"]) for s, i in m]))
        self.assertEqual(project_brain.match("расскажи про кошек", 3), [])

    def test_fixers_pure(self):
        t, n = project_brain.fix_mutable_defaults(BAD)
        self.assertEqual(n, 2); ast.parse(t); self.assertIn("if items is None:", t); self.assertNotIn("items=[]", t)
        t, n = project_brain.fix_open_encoding(BAD); self.assertEqual(n, 1); self.assertIn("open(path, encoding='utf-8')", t)
        t, n = project_brain.fix_eq_none(BAD); self.assertEqual(n, 2); self.assertIn("v is None", t); self.assertIn("v is not None", t)
        t, n = project_brain.fix_bare_except(BAD); self.assertEqual(n, 1); self.assertIn("except Exception:", t)
        t, n = project_brain.fix_whitespace(BAD); self.assertNotIn("return 2   \n", t)

    def test_defaults_behaviour_preserved(self):
        t, _ = project_brain.fix_mutable_defaults(BAD)
        ns = {}; exec(compile(t, "x", "exec"), ns)
        self.assertEqual(ns["add"](1), [1]); self.assertEqual(ns["add"](2), [2])

    def test_end_to_end_fix_all(self):
        j, owner = run_job("исправь безопасные проблемы")
        self.assertIn(j["state"], ("review",), j["summary"])
        f = result_files(j, owner)
        fixed = f["app/main.py"]
        ast.parse(fixed)
        self.assertIn("if items is None:", fixed); self.assertIn("encoding='utf-8'", fixed)
        self.assertIn("except Exception:", fixed); self.assertIn("is None", fixed)
        self.assertIn("CHANGELOG-agent.md", f)
        self.assertIn("не запускался", j["summary"])
        self.assertIn("app/main.py", j["changed"])

    def test_end_to_end_review_readonly(self):
        j, owner = run_job("сделай ревью кода")
        self.assertEqual(j["changed"], [])
        self.assertIn("замечаний", j["summary"])
        self.assertTrue(j["state"] in ("no_changes", "review"))

    def test_add_lesson_with_test(self):
        j, owner = run_job("добавь в проект функцию факториал")
        f = result_files(j, owner)
        self.assertTrue(any(k.startswith("lessons_added/lesson_num_factorial") for k in f), list(f))
        t = [k for k in f if k.startswith("tests/test_lesson_num_factorial")][0]
        d = tempfile.mkdtemp()
        for k, v in f.items():
            p = os.path.join(d, k); os.makedirs(os.path.dirname(p), exist_ok=True); open(p, "w", encoding="utf-8").write(v)
        import subprocess
        r = subprocess.run([sys.executable, "-m", "unittest", t.replace("/", ".")[:-3]], cwd=d, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        self.assertEqual(r.returncode, 0, r.stdout.decode())

    def test_unknown_task_is_honest_and_changes_nothing(self):
        j, owner = run_job("сочини оперу про кошек")
        self.assertEqual(j["changed"], [])
        self.assertIn("Ничего не менял", j["summary"])

    def test_brain_decides_additions_itself(self):
        j, owner = run_job("улучши проект")
        f = result_files(j, owner)
        self.assertIn("tests/test_compile_agent.py", f)          # тестов не было — добавил сам
        self.assertIn("Решил сам", j["summary"])
        self.assertIn("encoding='utf-8'", f["app/main.py"])      # «улучши» разрешает безопасные правки
        ast.parse(f["app/main.py"])

    def test_readonly_task_only_advises(self):
        j, owner = run_job("сделай ревью кода")
        self.assertEqual(j["changed"], [])
        self.assertIn("советует", j["summary"])

    def test_web_research_for_programming(self):
        from unittest import mock
        import project_web
        page = {"url": "https://x.example/a", "title": "Палиндром в Python",
                "html": "<pre>def is_pal(s):\n    s = s.lower()\n    return s == s[::-1]\n</pre><pre>import os\nos.system('rm -rf /')\nx=1</pre>", "text": ""}
        with mock.patch.object(project_web, "search", return_value=[{"url": "https://x.example/a", "title": "Палиндром в Python"}]), \
                mock.patch.object(project_web, "fetch_page", return_value=(True, page)):
            pr = project_agent.upload(mkzip({"a.py": "x = 1\n"}), "p.zip", "tw")
            jid = project_agent.start(pr["id"], "добавь проверку чего-то очень необычного про квантовый эффект", "tw", internet=True, model="brain")
            for _ in range(100):
                j = project_agent.status(jid, "tw")
                if j["done"]:
                    break
                time.sleep(0.1)
        f = result_files(j, "tw")
        self.assertIn("agent_proposals/web_1.py", f)
        self.assertIn("https://x.example/a", f["agent_proposals/web_1.py"])
        self.assertNotIn("os.system", f["agent_proposals/web_1.py"])
        self.assertIn("Интернет", j["summary"])

    def test_internet_off_is_honest(self):
        j, owner = run_job("добавь проверку чего-то очень необычного про квантовый эффект")
        self.assertEqual([k for k in j["changed"] if k.startswith("agent_proposals")], [])

    def test_versioned_names(self):
        v = project_brain.versioned_name
        self.assertEqual(v("a/b.py"), "a/b_v2.py"); self.assertEqual(v("b_v2.py"), "b_v3.py"); self.assertEqual(v("x-v9.js"), "x-v10.js")

    def test_parse_rename(self):
        inv = ["app/main.py", "README.md", "a/util.py", "b/util.py"]
        self.assertEqual(project_brain.parse_rename("переименуй main.py в app.py", inv)[:3], ("app/main.py", "app/app.py", "named"))
        self.assertEqual(project_brain.parse_rename("переименуй main.py в «core»", inv)[:3], ("app/main.py", "app/core.py", "named"))
        self.assertEqual(project_brain.parse_rename("переименуй app/main.py в lib/main2.py", inv)[:3], ("app/main.py", "lib/main2.py", "named"))
        self.assertEqual(project_brain.parse_rename("переименуй main.py", inv)[:3], ("app/main.py", "app/main_v2.py", "versioned"))
        self.assertIn("нескольких местах", project_brain.parse_rename("переименуй util.py в u.py", inv)[3])
        self.assertIn("Не нашёл", project_brain.parse_rename("переименуй nothing.py", inv)[3])
        self.assertIn("../", "../x") ; self.assertTrue(project_brain.parse_rename("переименуй main.py в ../x.py", inv)[3])

    RFILES = {"app/main.py": "import util\nfrom util import f\nimport os, util, sys\nx = util.f()\n# main.py тут\n",
              "app/util.py": "def f():\n    return 1\n", "pkg/__init__.py": "", "pkg/run.py": "from pkg import util\nimport pkg.util\n",
              "README.md": "см. app/util.py и util.py, а также xutil.py", "run.sh": "python app/main.py\npython util.py\n"}

    def test_rename_named_updates_refs(self):
        j, owner = run_job("переименуй util.py в helpers.py", self.RFILES)
        f = result_files(j, owner)
        self.assertNotIn("app/util.py", f); self.assertIn("app/helpers.py", f)
        self.assertIn("import helpers as util", f["app/main.py"]); self.assertIn("from helpers import f", f["app/main.py"])
        self.assertIn("import os, helpers as util, sys", f["app/main.py"]); self.assertIn("x = util.f()", f["app/main.py"])
        self.assertIn("app/helpers.py и helpers.py", f["README.md"]); self.assertIn("xutil.py", f["README.md"])
        self.assertIn("python helpers.py", f["run.sh"])
        self.assertIn("from pkg import helpers as util", f["pkg/run.py"])
        for k, v in f.items():
            if k.endswith(".py"):
                ast.parse(v)
        self.assertNotIn("CHANGELOG-agent.md", f); self.assertNotIn("tests/test_compile_agent.py", f)   # делает только это

    def test_rename_without_name_adds_version(self):
        j, owner = run_job("переименуй файл app/util.py", self.RFILES)
        f = result_files(j, owner)
        self.assertIn("app/util_v2.py", f); self.assertNotIn("app/util.py", f)
        self.assertIn("import util_v2 as util", f["app/main.py"]); self.assertIn("python util_v2.py", f["run.sh"])
        self.assertIn("индекс версии", j["summary"])
        j2, o2 = run_job("подними версию файла app/util_v2.py", {k.replace("util.py", "util_v2.py"): v.replace("util.py", "util_v2.py") for k, v in self.RFILES.items()})
        self.assertIn("app/util_v3.py", result_files(j2, o2))

    def test_rename_keep_copy_and_collision(self):
        j, owner = run_job("сделай копию файла app/util.py с индексом версии, сохрани старый", self.RFILES)
        f = result_files(j, owner)
        self.assertIn("app/util.py", f)
        files = dict(self.RFILES); files["app/helpers.py"] = "x = 1\n"
        j, owner = run_job("переименуй util.py в helpers.py", files)
        f = result_files(j, owner)
        self.assertIn("app/util.py", f); self.assertIn("уже существует", j["summary"])

    def test_crlf_and_bom_preserved(self):
        raw = b"\xef\xbb\xbfdef f(a=[]):\r\n    return open('x')\r\n"
        pr = project_agent.upload(mkzip({"m.py": "x=1\n"}), "p.zip", "tc")
        z = io.BytesIO()
        with zipfile.ZipFile(z, "w") as zz:
            zz.writestr("m.py", raw)
        pr = project_agent.upload(z.getvalue(), "p.zip", "tc")
        jid = project_agent.start(pr["id"], "исправь безопасные проблемы", "tc", internet=False, model="brain")
        for _ in range(100):
            j = project_agent.status(jid, "tc")
            if j["done"]:
                break
            time.sleep(0.1)
        b = (project_agent._base("jobs", jid) / "work" / "m.py").read_bytes()
        self.assertTrue(b.startswith(b"\xef\xbb\xbf"))
        self.assertEqual(b.count(b"\r\n"), b.count(b"\n"))          # нет «голых» \n
        self.assertIn(b"encoding='utf-8'", b); self.assertIn(b"if a is None:", b)
        import difflib
        sec = j["diff"].split("+++ b/m.py")[1].split("+++ b/")[0]
        self.assertLess(len([l for l in sec.splitlines() if l.startswith(("+", "-"))]), 12)    # правка точечная, не весь файл

    def test_lesson_recipe_uses_its_own_lesson(self):
        j, owner = run_job("Добавить: Проверить палиндром", {"a.py": "x = 1\n"})
        f = result_files(j, owner)
        self.assertTrue(any("str_palindrome" in k for k in f), list(f))
        self.assertFalse(any("oop_stack" in k for k in f))

    def test_web_blocks_filter_trivial(self):
        page = {"html": "<pre>print('Hello World!')\nx = 1\ny = 2</pre>", "text": ""}
        self.assertEqual(project_brain._code_blocks(page), [])

    def test_archive_names(self):
        a = project_agent.archive_name
        self.assertEqual(a("сделай ревью", "neuro12-cloud-3_222_0.zip"), "neuro12-cloud-3_222_0_v2.zip")
        self.assertEqual(a("сделай ревью", "x.zip", "x_v2.zip"), "x_v3.zip")
        self.assertEqual(a("назови архив мой_проект_финал", "x.zip"), "мой_проект_финал.zip")
        self.assertEqual(a("исправь ошибки и сохрани в итог.zip", "x.zip"), "итог.zip")
        self.assertEqual(a("переименуй архив в «релиз 5»", "x.zip"), "релиз 5.zip")
        self.assertEqual(a("добавь тесты", "my proj!.rar"), "my proj__v2.zip")
        self.assertEqual(a("добавь тесты", ""), "project_v2.zip")
        self.assertEqual(a("сохрани в памяти и сохрани как есть", "x.zip"), "x_v2.zip")     # не имя архива

    def test_archive_download_name_and_chain(self):
        j, owner = run_job("назови архив готово_v1 и сделай ревью", {"a.py": "x = 1\n"})
        self.assertEqual(project_agent.download_name(j["id"], owner), "готово_v1.zip")
        self.assertIn("Итоговый архив: готово_v1.zip", j["summary"])
        pr = project_agent.upload(mkzip({"a.py": "x = 1\n"}), "proj.zip", "ta")
        jid = project_agent.start(pr["id"], "сделай ревью кода", "ta", internet=False, model="brain")
        for _ in range(100):
            j1 = project_agent.status(jid, "ta")
            if j1["done"]:
                break
            time.sleep(0.1)
        self.assertEqual(j1["archive"], "proj_v2.zip")
        jid2 = project_agent.start(pr["id"], "сделай ревью кода", "ta", internet=False, model="brain", base_job=jid)
        for _ in range(100):
            j2 = project_agent.status(jid2, "ta")
            if j2["done"]:
                break
            time.sleep(0.1)
        self.assertEqual(j2["archive"], "proj_v3.zip")       # цепочка задач: индекс растёт
        with zipfile.ZipFile(project_agent.download(jid2, "ta")) as z:
            self.assertFalse(any("_v2" in n or "_v3" in n for n in z.namelist()))   # внутри индексов нет

    def test_archive_instruction_does_nothing_else(self):
        j, owner = run_job("назови архив итог", {"a.py": "x = 1\n"})
        self.assertEqual(j["changed"], []); self.assertEqual(project_agent.download_name(j["id"], owner), "итог.zip")

    def test_bump_version_only_on_request(self):
        j, owner = run_job("сделай ревью кода", {"cloud/VERSION": "3.222.0", "cloud/server/app.py": "x = 1\n"})
        self.assertEqual(result_files(j, owner)["cloud/VERSION"], "3.222.0")
        j, owner = run_job("подними версию проекта", {"cloud/VERSION": "3.222.0\n", "cloud/server/app.py": "x = 1\n"})
        self.assertEqual(result_files(j, owner)["cloud/VERSION"], "3.222.1\n")

    def test_updater_ignores_files_outside_package_folder(self):
        import updater
        pkg = {"project/cloud/server/app.py": "x = 1\n", "project/cloud/VERSION": "9.9.9", "project/lessons_added/a.py": "y = 2\n",
               "agent-report.json": "{}", "project/README.md": "x"}
        info = updater.parse_zip(mkzip(pkg))
        self.assertEqual(info["prefix"], "project/cloud/")
        self.assertEqual(sorted(r.replace(os.sep, "/") for _n, r in info["entries"]), ["VERSION", "server/app.py"])

    def test_llm_observes(self):
        run_job("найди todo")
        learned = project_brain.learned_texts()
        self.assertTrue(any("Наблюдение LLM" in t for t in learned))
        self.assertTrue(any("Контур" in t for t in admincorpus.contour_texts()))
        import kb
        res = (kb.search("Контур: наблюдение найди todo", limit=5) or {}).get("results") or []
        self.assertTrue(any("наблюдение" in str(r.get("title")) for r in res))

    def test_model_validation(self):
        with self.assertRaises(ValueError):
            pr = project_agent.upload(mkzip({"a.py": "x=1\n"}), "p.zip", "t2")
            project_agent.start(pr["id"], "x", "t2", model="bogus")


if __name__ == "__main__":
    unittest.main()
