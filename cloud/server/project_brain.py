# -*- coding: utf-8 -*-
"""v3.221 — «Контур» работает через Star Gate по ИНСТРУКЦИЯМ (model='brain').

Мозг не рассуждает свободно: он выбирает инструкцию Контура по задаче
(contour_instructions/*.json — они же лежат в базе знаний и в обучении LLM) и
выполняет её шаги — настоящие операции над рабочей копией проекта:
survey, locate, review (разбор AST), fix_* (механические правки с проверкой
синтаксиса и откатом), add_lesson (проверенный шаблон из codelessons),
add_tests, notes_md, changelog. Код проекта НЕ запускается.
Честно: это набор типовых процедур; задачу «сделай новую фичу с нуля» он не
решит — тогда скажет об этом и ничего не изменит.
LLM наблюдает: каждый прогон пишется как статья в базу знаний («Контур:
наблюдение …» — её читает своя LLM и отмечает усвоение) и в корпус обучения."""
import ast
import hashlib
import json
import os
import re
import threading
import time

import store

_HERE = os.path.dirname(os.path.abspath(__file__))
_DIR = os.path.join(_HERE, "contour_instructions")
_LOCK = threading.RLock()
_CACHE = {"items": None}
_LEARN = "contour_learned.json"
MIN_SCORE = 0.34
_GEN = {"сдела", "напиш", "созда", "прове", "найди", "найти", "посчит", "нужен", "нужна", "функци", "скрипт", "програ",
        "код", "как", "что", "это", "для", "над", "при", "помог", "хочу", "надо", "можно", "покаж", "дай", "мне", "проект",
        "проекте", "расска", "скажи", "объясн", "покажи", "пожалу", "пожалуйс", "задач", "сделай", "напиши", "создай", "проверь", "найди", "покажи"}
OPS = {"archive_name", "bump_version", "rename", "web_research", "survey", "locate", "read_file", "review", "fix_mutable_defaults", "fix_open_encoding", "fix_eq_none",
       "fix_bare_except", "fix_whitespace", "add_lesson", "add_tests", "notes_md", "changelog", "check", "finish"}


# ------------------------------------------------------------------ библиотека
def validate(i):
    if not isinstance(i, dict):
        return "не объект"
    for k in ("id", "title", "ask", "steps"):
        if not i.get(k):
            return "нет поля " + k
    if not re.fullmatch(r"[\w.\-]{2,60}", str(i["id"])):
        return "плохой id"
    for s in i["steps"]:
        if not isinstance(s, dict) or s.get("op") not in OPS:
            return "неизвестная операция: %s" % (s.get("op") if isinstance(s, dict) else s)
    return ""


def load_all():
    with _LOCK:
        if _CACHE["items"] is None:
            items, seen = [], set()
            dirs = [_DIR, os.path.join(store.DATA_DIR, "contour_instructions")]
            for d in dirs:
                if not os.path.isdir(d):
                    continue
                for fn in sorted(os.listdir(d)):
                    if not fn.endswith(".json"):
                        continue
                    try:
                        data = json.load(open(os.path.join(d, fn), encoding="utf-8"))
                    except Exception:
                        continue
                    for i in data if isinstance(data, list) else []:
                        if not validate(i) and i["id"] not in seen:
                            seen.add(i["id"])
                            items.append(i)
            _CACHE["items"] = items
        return _CACHE["items"]


def reload():
    _CACHE["items"] = None
    return len(load_all())


def _stems(s):
    return {w[:6] for w in re.findall(r"[a-zа-яё0-9]{3,}", str(s).lower())} - _GEN


def match(task, limit=3):
    q = _stems(task)
    out = []
    if not q:
        return out
    for it in load_all():
        best = 0.0
        for ph in list(it["ask"]) + [it["title"]]:
            p = _stems(ph)
            inter = len(q & p)
            if p and inter:
                best = max(best, inter / float(len(p)) * 0.7 + inter / float(len(q)) * 0.3)
        if best >= MIN_SCORE:
            out.append((round(best, 2), it))
    out.sort(key=lambda x: -x[0])
    return out[:limit]


def lesson_text(i):
    lines = ["Инструкция Контура: %s." % i["title"], "Задача владельца: %s." % "; ".join(i["ask"]),
             "Права: %s." % ("правит файлы рабочей копии" if i.get("writes") else "только читает")]
    for s in i["steps"]:
        lines.append("Шаг: " + json.dumps({k: v for k, v in s.items() if v not in (None, "")}, ensure_ascii=False))
    if i.get("notes"):
        lines.append("Примечание: " + str(i["notes"]))
    lines.append("Статус: код проекта не запускается; составлено по описанию операций, в живом проекте не проверено."
                 if not i.get("verified") else "Статус: проверено.")
    return "\n".join(lines)


def _did(i):
    return "contour_" + hashlib.md5(i["id"].encode("utf-8")).hexdigest()[:12]


def seed_kb():
    import kb
    n = 0
    for i in load_all():
        r = kb.add_article("Программирование: Контур — " + i["title"], lesson_text(i), source="contour", did=_did(i))
        n += 1 if r.get("ok") else 0
    return n


def corpus():
    out = []
    for i in load_all():
        t = lesson_text(i)
        out.append(("ct-l-" + hashlib.md5(t.encode()).hexdigest()[:14], t))
        for ph in i["ask"][1:3]:
            t2 = "Задача владельца: %s\n%s" % (ph, "\n".join("Шаг: " + json.dumps(s, ensure_ascii=False) for s in i["steps"]))
            out.append(("ct-e-" + hashlib.md5(t2.encode()).hexdigest()[:14], t2))
    return out


def learned_texts():
    return [x.get("text", "") for x in (store.load_json(_LEARN, []) or []) if x.get("text")]


def status():
    items = load_all()
    return {"count": len(items), "ops": sorted(OPS), "learned": len(store.load_json(_LEARN, []) or [])}


# ------------------------------------------------------------------ вспомогательное
def _py_files(work, inv):
    out = []
    for rel in inv:
        if rel.endswith(".py"):
            try:
                out.append((rel, _read_raw(work, rel)[0]))
            except Exception:
                pass
    return out


def _slots(task):
    paths = re.findall(r"[\w./-]+\.(?:py|js|json|md|txt|html|css|yml|yaml|ino|go|kt)\b", task)
    quoted = re.findall(r"[«\"'`]([^»\"'`]{2,80})[»\"'`]", task)
    idents = [w for w in re.findall(r"\b[A-Za-z_][A-Za-z0-9_]{3,}\b", task)]
    return {"path": paths[0] if paths else "", "query": (quoted[0] if quoted else (idents[0] if idents else "")),
            "paths": paths}


def _read_raw(work, rel):
    """-> (текст с \\n, перевод строки файла, BOM?) — чтобы правка не меняла CRLF/BOM."""
    b = (work / rel).read_bytes()
    bom = b[:3] == b"\xef\xbb\xbf"
    t = b[3:] if bom else b
    nl = "\r\n" if t.count(b"\r\n") > t.count(b"\n") // 2 and b"\r\n" in t else "\n"
    return t.decode("utf-8").replace("\r\n", "\n"), nl, bom


def _write_checked(work, rel, new, nl="\n", bom=False):
    """Записать .py только если синтаксис верен; перевод строки и BOM — как были в файле."""
    if rel.endswith(".py"):
        ast.parse(new)
    data = new.replace("\n", nl).encode("utf-8")
    (work / rel).write_bytes((b"\xef\xbb\xbf" if bom else b"") + data)


# ------------------------------------------------------------------ разбор и правки
def review_findings(files, rules):
    out = []
    for rel, text in files:
        try:
            tree = ast.parse(text)
        except SyntaxError as e:
            out.append((rel, e.lineno or 0, "syntax", "синтаксическая ошибка: %s" % e.msg))
            continue
        lines = text.splitlines()
        for n in ast.walk(tree):
            if "mutable_default" in rules and isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for d in n.args.defaults + [x for x in n.args.kw_defaults if x]:
                    if isinstance(d, (ast.List, ast.Dict, ast.Set)):
                        out.append((rel, n.lineno, "mutable_default", "изменяемое значение по умолчанию в «%s»" % n.name))
            if "bare_except" in rules and isinstance(n, ast.ExceptHandler) and n.type is None:
                out.append((rel, n.lineno, "bare_except", "голый except: ловит и KeyboardInterrupt"))
            if "eq_none" in rules and isinstance(n, ast.Compare):
                for op, c in zip(n.ops, n.comparators):
                    if isinstance(op, (ast.Eq, ast.NotEq)) and isinstance(c, ast.Constant) and c.value is None:
                        out.append((rel, n.lineno, "eq_none", "сравнение с None через ==/!= (нужно is / is not)"))
            if "open_no_encoding" in rules and isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "open":
                kws = {k.arg for k in n.keywords}
                mode = n.args[1].value if len(n.args) > 1 and isinstance(n.args[1], ast.Constant) else None
                for k in n.keywords:
                    if k.arg == "mode" and isinstance(k.value, ast.Constant):
                        mode = k.value.value
                if "encoding" not in kws and "b" not in str(mode or "") and len(n.args) < 4:
                    out.append((rel, n.lineno, "open_no_encoding", "open() без encoding — на Windows будет cp1251"))
            if "long_function" in rules and isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if (n.end_lineno or n.lineno) - n.lineno + 1 > 80:
                    out.append((rel, n.lineno, "long_function", "функция «%s» длиннее 80 строк" % n.name))
            if "missing_docstring" in rules and isinstance(n, (ast.FunctionDef, ast.ClassDef)) and not n.name.startswith("_"):
                if not ast.get_docstring(n):
                    out.append((rel, n.lineno, "missing_docstring", "нет описания у «%s»" % n.name))
            if "print_debug" in rules and isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "print":
                out.append((rel, n.lineno, "print_debug", "print в коде"))
        if "unused_import" in rules:
            names = []
            for n in tree.body:
                if isinstance(n, ast.Import):
                    names += [(a.asname or a.name.split(".")[0], n.lineno) for a in n.names]
                elif isinstance(n, ast.ImportFrom):
                    names += [(a.asname or a.name, n.lineno) for a in n.names if a.name != "*"]
            body = "\n".join(l for l in lines)
            for nm, ln in names:
                if len(re.findall(r"\b%s\b" % re.escape(nm), body)) <= 1:
                    out.append((rel, ln, "unused_import", "импорт «%s» не используется" % nm))
        if "todo" in rules:
            for i, l in enumerate(lines, 1):
                if re.search(r"\b(TODO|FIXME|XXX|HACK)\b", l):
                    out.append((rel, i, "todo", l.strip()[:100]))
        if "duplicate_defs" in rules:
            seen = {}
            for n in tree.body:
                if isinstance(n, (ast.FunctionDef, ast.ClassDef)):
                    if n.name in seen:
                        out.append((rel, n.lineno, "duplicate_defs", "«%s» определена повторно (первая — строка %d)" % (n.name, seen[n.name])))
                    seen[n.name] = n.lineno
    return out


def _edit_lines(text, edits):
    """edits: [(line, col, end_line, end_col, new)] с конца файла к началу."""
    lines = text.splitlines(keepends=True)
    for (l1, c1, l2, c2, new) in sorted(edits, key=lambda e: (e[0], e[1]), reverse=True):
        a, b = lines[l1 - 1], lines[l2 - 1]
        # ast даёт смещения в UTF-8 байтах
        ca = len(a.encode("utf-8")[:c1].decode("utf-8", "ignore"))
        cb = len(b.encode("utf-8")[:c2].decode("utf-8", "ignore"))
        merged = a[:ca] + new + b[cb:]
        lines[l1 - 1:l2] = [merged]
    return "".join(lines)


def fix_mutable_defaults(text):
    tree = ast.parse(text)
    edits, inserts, n_fixed = [], [], 0
    lines = text.splitlines()
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        a = fn.args
        pos = list(zip(a.args[len(a.args) - len(a.defaults):], a.defaults)) + [(k, d) for k, d in zip(a.kwonlyargs, a.kw_defaults) if d]
        bad = [(arg, d) for arg, d in pos if isinstance(d, (ast.List, ast.Dict)) and not (getattr(d, "elts", None) or getattr(d, "keys", None))]
        if not bad:
            continue
        first = fn.body[0]
        if first.lineno == fn.lineno:
            continue                                   # тело в той же строке — не трогаем
        doc = isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str)
        anchor = (first.end_lineno if doc else first.lineno - 1)
        body_stmt = fn.body[1] if doc and len(fn.body) > 1 else first
        indent = " " * (body_stmt.col_offset if not doc or len(fn.body) > 1 else first.col_offset)
        add = []
        for arg, d in bad:
            edits.append((d.lineno, d.col_offset, d.end_lineno, d.end_col_offset, "None"))
            add.append("%sif %s is None:\n%s    %s = %s\n" % (indent, arg.arg, indent, arg.arg, "[]" if isinstance(d, ast.List) else "{}"))
            n_fixed += 1
        inserts.append((anchor, "".join(add)))
    if not n_fixed:
        return text, 0
    new = _edit_lines(text, edits)
    out = new.splitlines(keepends=True)
    # вставки считаем по исходным номерам: правки по умолчанию не меняют число строк
    for anchor, add in sorted(inserts, reverse=True):
        out.insert(anchor, add)
    res = "".join(out)
    ast.parse(res)
    return res, n_fixed


def fix_open_encoding(text):
    tree = ast.parse(text)
    edits = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "open":
            kws = {k.arg for k in n.keywords}
            mode = n.args[1].value if len(n.args) > 1 and isinstance(n.args[1], ast.Constant) else None
            for k in n.keywords:
                if k.arg == "mode" and isinstance(k.value, ast.Constant):
                    mode = k.value.value
            if "encoding" in kws or "b" in str(mode or "") or len(n.args) >= 4 or not n.args:
                continue
            if any(isinstance(a, ast.Starred) for a in n.args) or any(k.arg is None for k in n.keywords):
                continue
            edits.append((n.end_lineno, n.end_col_offset - 1, n.end_lineno, n.end_col_offset - 1, ", encoding='utf-8'"))
    if not edits:
        return text, 0
    res = _edit_lines(text, edits)
    ast.parse(res)
    return res, len(edits)


def fix_eq_none(text):
    tree = ast.parse(text)
    lines = text.splitlines(keepends=True)
    edits = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Compare):
            left = n.left
            for op, c in zip(n.ops, n.comparators):
                if isinstance(op, (ast.Eq, ast.NotEq)) and isinstance(c, ast.Constant) and c.value is None \
                        and left.end_lineno == c.lineno:
                    seg = lines[c.lineno - 1].encode("utf-8")[left.end_col_offset:c.col_offset].decode("utf-8", "ignore")
                    if re.fullmatch(r"\s*(==|!=)\s*", seg):
                        new = " is " if isinstance(op, ast.Eq) else " is not "
                        edits.append((c.lineno, left.end_col_offset, c.lineno, c.col_offset, new))
                left = c
    if not edits:
        return text, 0
    res = _edit_lines(text, edits)
    ast.parse(res)
    return res, len(edits)


def fix_bare_except(text):
    tree = ast.parse(text)
    lines = text.splitlines(keepends=True)
    cnt = 0
    for n in ast.walk(tree):
        if isinstance(n, ast.ExceptHandler) and n.type is None:
            l = lines[n.lineno - 1]
            new = re.sub(r"\bexcept\s*:", "except Exception:", l, count=1)
            if new != l:
                lines[n.lineno - 1] = new
                cnt += 1
    if not cnt:
        return text, 0
    res = "".join(lines)
    ast.parse(res)
    return res, cnt


def fix_whitespace(text):
    new = "\n".join(l.rstrip() for l in text.split("\n"))
    if not new.endswith("\n"):
        new += "\n"
    return new, (1 if new != text else 0)


_FIXERS = {"fix_mutable_defaults": fix_mutable_defaults, "fix_open_encoding": fix_open_encoding,
           "fix_eq_none": fix_eq_none, "fix_bare_except": fix_bare_except, "fix_whitespace": fix_whitespace}


def notes_text(work, inv):
    rows = ["# Описание проекта (собрано агентом «Контур» автоматически)", "",
            "Файлов: %d. Код не запускался; описание построено разбором исходников." % len(inv), ""]
    ext = {}
    for rel in inv:
        e = os.path.splitext(rel)[1] or "(без расширения)"
        ext[e] = ext.get(e, 0) + 1
    rows.append("Типы файлов: " + ", ".join("%s — %d" % kv for kv in sorted(ext.items(), key=lambda x: -x[1])[:10]))
    for rel, text in _py_files(work, inv):
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        doc = (ast.get_docstring(tree) or "").split("\n")[0]
        rows += ["", "## %s" % rel] + (["%s" % doc] if doc else [])
        for n in tree.body:
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and not n.name.startswith("_"):
                rows.append("- функция `%s(%s)` — %s" % (n.name, ", ".join(a.arg for a in n.args.args),
                                                         (ast.get_docstring(n) or "без описания").split("\n")[0][:100]))
            elif isinstance(n, ast.ClassDef):
                rows.append("- класс `%s` — %s" % (n.name, (ast.get_docstring(n) or "без описания").split("\n")[0][:100]))
    return "\n".join(rows) + "\n"


def _lesson_module(it):
    name = re.sub(r"[^a-z0-9]+", "_", it["id"].lower()).strip("_")
    return "lesson_" + name


def _test_wrapper(mod, it):
    body = "\n".join("        " + l if l.strip() else "" for l in str(it.get("test") or "pass").splitlines())
    return ("import unittest\n\nfrom lessons_added.%s import *  # noqa: F401,F403\n\n\nclass Test_%s(unittest.TestCase):\n"
            "    def test_lesson(self):\n%s\n\n\nif __name__ == '__main__':\n    unittest.main()\n" % (mod, mod, body))


# ------------------------------------------------------------------ переименование файла
_TEXT_EXT = {".py", ".js", ".cjs", ".mjs", ".ts", ".json", ".md", ".txt", ".html", ".htm", ".css", ".yml", ".yaml", ".sh", ".bat",
             ".ps1", ".ino", ".go", ".kt", ".kts", ".gradle", ".toml", ".cfg", ".ini", ".xml", ".csv", ".rst", ".cmd", ".spec"}
_VER = re.compile(r"^(.*?)([._-]v)(\d+)$", re.I)
_PATHTOK = re.compile(r"[\w./\\-]+\.[A-Za-z0-9]{1,6}\b")


def versioned_name(rel):
    """a.py -> a_v2.py; a_v2.py -> a_v3.py (индекс версии добавляется или увеличивается)."""
    d, base = os.path.split(rel)
    stem, ext = os.path.splitext(base)
    m = _VER.match(stem)
    new = ("%s%s%d" % (m.group(1), m.group(2), int(m.group(3)) + 1)) if m else stem + "_v2"
    return (d + "/" if d else "") + new + ext


def parse_rename(task, inv):
    """Из задачи: какой файл и во что. -> (old, new, режим, ошибка).
    режим: 'named' (просили конкретное имя) | 'versioned' (имени нет — добавляем индекс версии)."""
    toks = [t.replace("\\", "/").lstrip("./") for t in _PATHTOK.findall(task)]
    old, err = "", ""
    for t in toks:
        hit = [p for p in inv if p == t] or [p for p in inv if os.path.basename(p) == os.path.basename(t) and p.endswith(t)]
        if len(hit) == 1:
            old = hit[0]
            break
        if len(hit) > 1:
            return "", "", "", "Имя «%s» есть в нескольких местах (%s) — укажите путь целиком." % (t, ", ".join(hit[:4]))
    if not old:
        return "", "", "", "Не нашёл в проекте файл из задачи. Назовите его с расширением, например: переименуй main.py в app.py."
    new = ""
    m = re.search(r"(?:\bв\b|\bна\b|\bкак\b|\bto\b|->|→)\s+[«\"'`]?([\w./\\-]{1,120})", task[task.find(os.path.basename(old)) + len(os.path.basename(old)):]) \
        if os.path.basename(old) in task else None
    cand = m.group(1).replace("\\", "/") if m else ""
    if not cand:
        others = [t for t in toks if t != old and os.path.basename(t) != os.path.basename(old)]
        cand = others[0] if others else ""
    cand = cand.rstrip(".,;:!?»\"'`").lstrip("«\"'`")
    if cand and cand != old and not re.fullmatch(r"(ему|него|нему|этот|файл|имя|название|версию|версия)", cand, re.I):
        oext = os.path.splitext(old)[1]
        if not os.path.splitext(cand)[1] and oext:
            cand += oext
        new = cand if "/" in cand else (os.path.dirname(old) + "/" if os.path.dirname(old) else "") + cand
        mode = "named"
    else:
        new, mode = versioned_name(old), "versioned"
    new = new.lstrip("/")
    if ".." in new.split("/") or not re.fullmatch(r"[\w .\-/]+", new):
        return "", "", "", "Недопустимое имя файла: %s" % new
    if new == old:
        return "", "", "", "Новое имя совпадает со старым."
    if new in inv:
        return "", "", "", "Файл %s уже существует — не перезаписываю." % new
    return old, new, mode, err


def _ref_patterns(old, new):
    ob, nb = os.path.basename(old), os.path.basename(new)
    same_dir = os.path.dirname(old) == os.path.dirname(new)
    pats = [(re.compile(r"(?<![\w-])" + re.escape(old) + r"(?![\w-])"), new),
            (re.compile(r"(?<![\w-])" + re.escape(old.replace("/", "\\")) + r"(?![\w-])"), new.replace("/", "\\"))]
    if same_dir:
        pats.append((re.compile(r"(?<![\w-])" + re.escape(ob) + r"(?![\w-])"), nb))
    return pats


def _py_import_rewrite(text, ostem, nstem):
    """import old -> import new as old; from old import x -> from new import x; from pkg import old -> from pkg import new as old."""
    n = 0
    out = []
    for ln in text.split("\n"):
        m = re.match(r"^(\s*)from\s+((?:[\w.]+\.)?)%s(\s+import\b.*)$" % re.escape(ostem), ln)
        if m:
            out.append("%sfrom %s%s%s" % (m.group(1), m.group(2), nstem, m.group(3)))
            n += 1
            continue
        m = re.match(r"^(\s*)from\s+([\w.]+)\s+import\s+(.+)$", ln)
        if m and re.search(r"(?<![\w.])%s(?![\w])" % re.escape(ostem), m.group(3)) and "(" not in m.group(3):
            items = []
            for it in [x.strip() for x in m.group(3).split(",")]:
                if it == ostem:
                    items.append("%s as %s" % (nstem, ostem))
                    n += 1
                else:
                    items.append(it)
            out.append("%sfrom %s import %s" % (m.group(1), m.group(2), ", ".join(items)))
            continue
        m = re.match(r"^(\s*)import\s+(.+?)(\s*#.*)?$", ln)
        if m and not m.group(2).strip().endswith("\\"):
            items = []
            hit = False
            for it in [x.strip() for x in m.group(2).split(",")]:
                mm = re.fullmatch(r"([\w.]*?)%s(\s+as\s+\w+)?" % re.escape(ostem), it)
                if mm and (not mm.group(1) or mm.group(1).endswith(".")):
                    alias = mm.group(2) or (" as " + ostem if not mm.group(1) else " as " + ostem)
                    items.append("%s%s%s" % (mm.group(1), nstem, alias))
                    hit = True
                    n += 1
                else:
                    items.append(it)
            if hit:
                out.append("%simport %s%s" % (m.group(1), ", ".join(items), m.group(3) or ""))
                continue
        out.append(ln)
    return "\n".join(out), n


def do_rename(job, work, inv, task, pa):
    """-> (отчёт, изменено файлов)."""
    old, new, mode, err = parse_rename(task, inv)
    if err:
        return err, 0
    ev = pa._event
    keep = bool(re.search(r"копи|сохрани\s+(?:старый|прежний|исходный)|оставь\s+(?:старый|прежний|исходный)", task, re.I))
    try:
        pa._file(work, old)
        pa._file(work, new)
    except ValueError as e:
        return str(e), 0
    ev(job, "Переименование файла", "%s → %s (%s)" % (old, new, "по просьбе" if mode == "named" else "индекс версии, имени в задаче нет"), "write")
    pats = _ref_patterns(old, new)
    ostem, nstem = os.path.splitext(os.path.basename(old))[0], os.path.splitext(os.path.basename(new))[0]
    is_py = old.endswith(".py") and new.endswith(".py")
    py_ok = is_py and ostem.isidentifier() and nstem.isidentifier()
    total, files = 0, []
    for rel in inv:
        if keep and rel == old:
            pass
        ext = os.path.splitext(rel)[1].lower()
        if ext not in _TEXT_EXT:
            continue
        try:
            text, nl, bom = _read_raw(work, rel)
        except Exception:
            continue
        new_text, cnt = text, 0
        for rx, rep in pats:
            new_text, k = rx.subn(lambda _m, _r=rep: _r, new_text)
            cnt += k
        if py_ok and ext == ".py":
            new_text, k = _py_import_rewrite(new_text, ostem, nstem)
            cnt += k
        if is_py and not py_ok and ext == ".py" and re.search(r"\b%s\b" % re.escape(ostem), text):
            pass                                       # предупреждение ниже
        if ext in (".js", ".cjs", ".mjs", ".ts") and old.endswith(tuple(_TEXT_EXT)) and os.path.splitext(old)[1].lower() in (".js", ".cjs", ".mjs", ".ts"):
            new_text, k = re.subn(r"(['\"/])%s(?=['\"])" % re.escape(ostem), lambda m: m.group(1) + nstem, new_text)
            cnt += k
        if cnt and new_text != text:
            try:
                _write_checked(work, rel, new_text, nl, bom)
            except SyntaxError:
                continue                                # правка сломала бы синтаксис — файл не трогаем
            total += cnt
            files.append(rel)
            ev(job, "Исправлены ссылки на переименованный файл", "%s: %d" % (rel, cnt), "write")
    dst = work / new
    dst.parent.mkdir(parents=True, exist_ok=True)
    if keep:
        import shutil as _sh
        _sh.copy2(work / old, dst)
    else:
        os.replace(work / old, dst)
    # старое имя внутри самого файла, если он двигался: содержимое уже поправлено выше по пути old
    lines = ["%s: %s → %s%s" % ("Скопирован" if keep else "Переименован", old, new,
                               " (имя не было задано — добавлен индекс версии)" if mode == "versioned" else ""),
             "Ссылок исправлено: %d в %d файлах%s" % (total, len(files), (": " + ", ".join(files[:8])) if files else "")]
    if is_py and not py_ok:
        lines.append("Внимание: новое имя не годится как имя модуля Python — импорты не переписывал.")
    left = [rel for rel in pa.inventory(work) if os.path.splitext(rel)[1].lower() in _TEXT_EXT and rel != new
            and re.search(r"(?<![\w-])%s(?![\w-])" % re.escape(os.path.basename(old)), (lambda r: _read_raw(work, r)[0] if (work / r).exists() else "")(rel))]
    if left and not keep:
        lines.append("Осталось упоминание старого имени (проверьте вручную): " + ", ".join(left[:6]))
    return "\n".join(lines), 1 + len(files)


# ------------------------------------------------------------------ интернет при программировании
_DANGER = re.compile(r"os\.system|subprocess|\beval\(|\bexec\(|__import__|shutil\.rmtree|socket\.|os\.remove|os\.unlink|ctypes|pickle\.loads", re.I)


def _code_blocks(page):
    """Python-фрагменты со страницы: <pre>/<code> из html, иначе строки, похожие на код."""
    import html as _h
    blocks = []
    raw = str(page.get("html") or "")
    for m in re.finditer(r"<pre[^>]*>(.*?)</pre>", raw, re.S | re.I):
        t = _h.unescape(re.sub(r"<[^>]+>", "", m.group(1))).strip("\n")
        blocks.append(t)
    if not blocks:
        cur = []
        for ln in str(page.get("text") or "").splitlines():
            if re.match(r"\s*(def |class |import |from \w+ import |@|for |if |while |return |\w+\s*=|\w+\(.*\)\s*$|#)", ln) or (cur and ln.startswith((" ", "\t"))):
                cur.append(ln)
            elif cur:
                if len(cur) >= 3:
                    blocks.append("\n".join(cur))
                cur = []
        if len(cur) >= 3:
            blocks.append("\n".join(cur))
    out = []
    for b in blocks:
        b = "\n".join(l for l in b.splitlines() if not l.startswith((">>> ", "... ")) or True)
        if not (3 <= len(b.splitlines()) <= 60) or _DANGER.search(b):
            continue
        if not re.search(r"^\s*(def|class)\s", b, re.M) and len(b.splitlines()) < 6:
            continue                                  # «print('Hello World')» — не пример решения
        try:
            ast.parse(b)
        except SyntaxError:
            continue
        out.append(b.rstrip() + "\n")
    return out


def web_research(job, pa, task, work):
    """Мозг идёт в интернет за примерами, когда программирует. Код со страниц —
    недоверенные данные: только синтаксис, без опасных вызовов, в agent_proposals/
    с адресом источника; в модули проекта сам не вставляется и не запускается.
    -> (текст отчёта, [заголовки страниц])"""
    if not job.get("internet"):
        return "Интернет выключен для этой задачи — примеры из сети не искал.", []
    import project_web
    q = re.sub(r"\s+", " ", task)[:160] + " python пример"
    pa._event(job, "Star Gate ищет примеры в интернете", q, "web")
    try:
        res = project_web.search(q, limit=3) or []
    except Exception as e:
        pa._event(job, "Поиск в интернете не удался", str(e)[:300], "warning")
        return "Интернет-поиск не удался: %s." % str(e)[:150], []
    titles, saved, urls = [], 0, []
    for r in res[:3]:
        url = str(r.get("url") or "")
        if not url:
            continue
        titles.append(str(r.get("title") or ""))
        urls.append(url)
    for url in urls[:2]:
        ok, page = project_web.fetch_page(url, timeout=12, max_bytes=300000)
        if not ok:
            pa._event(job, "Страница недоступна", "%s: %s" % (url, str(page)[:200]), "warning")
            continue
        pa._event(job, "Читаю страницу", "%s\n%s" % (page.get("url", url), page.get("title") or ""), "web")
        for blk in _code_blocks(page)[:1]:
            if saved >= 2:
                break
            rel = "agent_proposals/web_%d.py" % (saved + 1)
            hdr = ("# Предложение из интернета (не проверено, не запускалось).\n# Источник: %s\n# Заголовок: %s\n"
                   "# Лицензию источника проверьте сами; в модули проекта не вставлено.\n\n" % (page.get("url", url), str(page.get("title") or "")[:100]))
            pa._tool(job, work, {"tool": "write", "path": rel, "content": hdr + blk})
            saved += 1
    msg = "Интернет: найдено страниц %d; сохранено предложений кода %d (agent_proposals/, только синтаксис проверен)." % (len(urls), saved)
    job.setdefault("web_titles", []).extend(titles)
    return msg, titles


# ------------------------------------------------------------------ мозг решает сам, что добавить
_APPLAB_TASK = re.compile(r"(?:напиш\w*|написать|созда\w*|сдела\w*|добав\w*|придума\w*|нуж\w+|хочу|собер\w*)\s+(?:мне\s+)?(?:новый\s+|новое\s+|новую\s+)?(?:навык|приложени\w+|функци\w+|кирпичик\w*|скрипт\w*)"
                          r"[^.]{0,40}(?:апп\s*лаб|app\s*lab)|(?:апп\s*лаб|app\s*lab)[^.]{0,40}(?:навык|приложени|функци|кирпичик)", re.I)
_ADD = re.compile(r"добав|дополни|расшир|допиши|внедри|реализуй", re.I)
_IMPROVE = re.compile(r"улучш|доработ|почин|исправ|оптимиз|приведи в порядок|наведи порядок|рефактор|сделай лучше|подтяни", re.I)


def decide(job, work, inv, task, done_ops):
    """-> [(op, args, причина)]. Только то, для чего в проекте есть сигнал. Добавления
    (тесты, описание, шаблоны) мозг решает сам; правки существующего кода — только
    если в задаче есть слова «улучшить/исправить/доработать…»."""
    files = _py_files(work, inv)
    plan = []
    has_tests = any("test" in p.lower() for p in inv)
    has_readme = any(os.path.basename(p).lower().startswith("readme") for p in inv)
    has_notes = any(os.path.basename(p) == "PROJECT_NOTES.md" for p in inv)
    if files and not has_tests and "add_tests" not in done_ops:
        plan.append(("add_tests", {}, "в проекте нет тестов, а Python-файлов: %d" % len(files)))
    if files and not (has_readme or has_notes) and "notes_md" not in done_ops:
        plan.append(("notes_md", {}, "нет README и описания проекта"))
    if _IMPROVE.search(task) and files:
        f = review_findings(files, {"mutable_default", "bare_except", "eq_none", "open_no_encoding"})
        cnt = {}
        for x in f:
            cnt[x[2]] = cnt.get(x[2], 0) + 1
        for rule, op in (("mutable_default", "fix_mutable_defaults"), ("open_no_encoding", "fix_open_encoding"),
                         ("eq_none", "fix_eq_none"), ("bare_except", "fix_bare_except")):
            if cnt.get(rule) and op not in done_ops:
                plan.append((op, {}, "в коде найдено %d замечаний «%s», а в задаче просят улучшить" % (cnt[rule], rule)))
    return plan


# ------------------------------------------------------------------ исполнитель
def _do_step(step, c):
    """Выполнить одну операцию; результат — в c['report'], счётчики — в c."""
    job, work, pa, task, sl = c["job"], c["work"], c["pa"], c["task"], c["sl"]
    ev = pa._event
    op = step["op"]
    inv = pa.inventory(work)
    if op == "survey":
        res = pa._tool(job, work, {"tool": "list", "path": ""})
        readme = next((p for p in inv if p.lower().startswith("readme")), "")
        if readme:
            r = pa._tool(job, work, {"tool": "read", "path": readme, "limit": 3000})
            c["report"].append("README (%s): %s" % (readme, r["content"][:300].replace("\n", " ")))
        c["report"].append("Файлов: %d" % res["total_files"])
    elif op == "locate":
        q = step.get("query") or sl["query"]
        if not q:
            c["report"].append("Что искать? Назовите имя в кавычках, например «load_config».")
        else:
            hits = pa._tool(job, work, {"tool": "search", "query": q})
            c["report"].append("«%s»: найдено %d совпадений" % (q, len(hits)) + "".join(
                "\n  %s:%s %s" % (h["path"], h["line"], h["text"].strip()[:100]) for h in hits[:12]))
    elif op == "read_file":
        p = step.get("path") or sl["path"]
        if not p:
            c["report"].append("Какой файл читать? Укажите имя, например main.py.")
        else:
            r = pa._tool(job, work, {"tool": "read", "path": p, "limit": 6000})
            c["report"].append("%s (%d симв.):\n%s" % (p, r["total_chars"], r["content"][:1500]))
    elif op == "review":
        ev(job, "Разбираю код (AST)", ", ".join(step.get("rules") or []), "read")
        files = _py_files(work, inv)
        fnd = review_findings(files, set(step.get("rules") or []))
        c["report"].append("Ревью (%s): %d замечаний в %d файлах Python" % (", ".join(step.get("rules") or []), len(fnd), len(files)) +
                           "".join("\n  %s:%d [%s] %s" % f for f in fnd[:40]) + ("\n  …" if len(fnd) > 40 else ""))
        job.setdefault("review", []).extend([list(f) for f in fnd[:200]])
    elif op in _FIXERS:
        total, touched = 0, []
        for rel, text in _py_files(work, inv):
            if op == "fix_whitespace" and not rel.endswith(".py"):
                continue
            try:
                new, n = _FIXERS[op](text)
            except (SyntaxError, ValueError, IndexError):
                continue
            if n and new != text:
                _t, nl, bom = _read_raw(work, rel)
                _write_checked(work, rel, new, nl, bom)
                total += n
                touched.append(rel)
                ev(job, "Исправлено автоматически", "%s: %s (%d)" % (rel, op, n), "write")
        c["changed"] += len(touched)
        c["report"].append("%s: исправлений %d в %d файлах%s" % (op, total, len(touched), (" — " + ", ".join(touched[:8])) if touched else ""))
    elif op == "archive_name":
        c["report"].append("Итоговый архив будет называться: %s (имя из задачи, а если его нет — имя проекта с индексом версии; "
                           "файлы внутри архива индексов не получают)." % job.get("archive", "project-result.zip"))
    elif op == "bump_version":
        done = []
        for rel in inv:
            if os.path.basename(rel) == "VERSION" and rel.count("/") <= 1:
                t, nl, bom = _read_raw(work, rel)
                m = re.fullmatch(r"\s*(\d+)\.(\d+)\.(\d+)\s*", t)
                if m:
                    nv = "%s.%s.%d" % (m.group(1), m.group(2), int(m.group(3)) + 1)
                    _write_checked(work, rel, nv + ("\n" if t.endswith("\n") else ""), nl, bom)
                    done.append("%s: %s.%s.%s → %s" % (rel, m.group(1), m.group(2), m.group(3), nv))
                    ev(job, "Поднята версия", done[-1], "write")
        c["report"].append("Версия: " + ("; ".join(done) if done else "файл VERSION вида x.y.z не найден — ничего не менял."))
        c["changed"] += len(done)
    elif op == "rename":
        msg, n = do_rename(job, work, inv, task, pa)
        c["report"].append(msg)
        c["changed"] += n
    elif op == "web_research":
        msg, titles = web_research(job, pa, task, work)
        c["report"].append(msg)
        c["changed"] += 1 if "сохранено предложений кода 0" not in msg and "сохранено предложений кода" in msg else 0
    elif op == "add_lesson":
        import codelessons
        m = None
        if step.get("lesson"):                      # рецепт знает свой урок — берём его, не гадаем
            m = [(1.0, x) for x in codelessons.load() if x["id"] == step["lesson"]][:1]
        if not m:
            m = codelessons.match(task, 1)
        if not m and job.get("internet") and not c.get("web_done"):      # мозг идёт в интернет за подсказкой
            msg, titles = web_research(job, pa, task, work)
            c["web_done"] = True
            c["report"].append(msg)
            m2 = codelessons.match(task + " " + " ".join(titles), 1)
            if m2 and m2[0][0] >= 0.5:               # слабое совпадение по заголовкам страниц не берём
                m = m2
                c["report"].append("По заголовкам найденных страниц мозг выбрал шаблон «%s»." % m[0][1]["title"])
            elif m2:
                c["report"].append("По заголовкам страниц подходит только слабо «%s» (%.2f) — не беру." % (m2[0][1]["title"], m2[0][0]))
        if not m:
            c["report"].append("Подходящего проверенного шаблона в уроках нет — ничего не добавлено.")
            return
        sc, les = m[0]
        ok, out = codelessons.verify(les)
        if not ok:
            c["report"].append("Шаблон «%s» не прошёл собственную проверку здесь (%s) — не добавлен." % (les["title"], out.strip()[-120:]))
            return
        mod = _lesson_module(les)
        code = str(les.get("fixcode") or les["code"])
        target = sl["path"] if sl["path"].endswith(".py") else ""
        if target and (work / target).exists():
            old, nl, bom = _read_raw(work, target)
            fns = [n.name for n in ast.parse(code).body if isinstance(n, (ast.FunctionDef, ast.ClassDef))]
            if any(re.search(r"^(def|class)\s+%s\b" % re.escape(f), old, re.M) for f in fns):
                c["report"].append("В %s уже есть %s — не дублирую." % (target, ", ".join(fns)))
                return
            _write_checked(work, target, old.rstrip("\n") + "\n\n\n" + code, nl, bom)
            c["report"].append("Добавлен шаблон «%s» в конец %s." % (les["title"], target))
            ev(job, "Добавлен проверенный шаблон", "%s ← %s" % (target, les["id"]), "write")
        else:
            rel = "lessons_added/%s.py" % mod
            if (work / rel).exists():
                c["report"].append("Шаблон «%s» уже добавлен (%s)." % (les["title"], rel))
                return
            pa._tool(job, work, {"tool": "write", "path": rel, "content": code})
            pa._tool(job, work, {"tool": "write", "path": "lessons_added/__init__.py", "content": ""})
            c["report"].append("Шаблон «%s» (совпадение %.2f) записан в %s." % (les["title"], sc, rel))
        if les.get("test"):
            pa._tool(job, work, {"tool": "write", "path": "tests/test_%s.py" % mod, "content": _test_wrapper(mod, les)})
            c["report"].append("Тест шаблона записан в tests/test_%s.py (запустите сами: python -m unittest)." % mod)
        c["changed"] += 1
        job["lesson_id"] = les["id"]
    elif op == "add_tests":
        made = []
        for rel, text in _py_files(work, inv):
            if rel.startswith(("tests/", "test_")) or "/tests/" in rel:
                continue
            try:
                compile(text, rel, "exec")
            except SyntaxError:
                continue
            made.append(rel)
        if not made:
            c["report"].append("Нет Python-файлов для тестов.")
        else:
            body = ["import os", "import unittest", "", "ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))", "",
                    "FILES = %s" % json.dumps(made[:200], ensure_ascii=False), "", "", "class CompileTest(unittest.TestCase):",
                    "    \"\"\"Каждый файл проекта компилируется (код не выполняется).\"\"\"", "", "    def test_compile(self):",
                    "        for rel in FILES:", "            with self.subTest(rel):", "                path = os.path.join(ROOT, rel)",
                    "                with open(path, encoding='utf-8-sig') as f:", "                    compile(f.read(), path, 'exec')", "",
                    "", "if __name__ == '__main__':", "    unittest.main()", ""]
            pa._tool(job, work, {"tool": "write", "path": "tests/test_compile_agent.py", "content": "\n".join(body)})
            c["report"].append("Создан tests/test_compile_agent.py: проверяет компиляцию %d файлов. Это только проверка синтаксиса, "
                               "не поведения." % len(made))
            c["changed"] += 1
    elif op == "notes_md":
        pa._tool(job, work, {"tool": "write", "path": step.get("path") or "PROJECT_NOTES.md", "content": notes_text(work, inv)})
        c["report"].append("Описание проекта записано в %s." % (step.get("path") or "PROJECT_NOTES.md"))
        c["changed"] += 1
    elif op == "changelog":
        p = work / "CHANGELOG-agent.md"
        old = p.read_text(encoding="utf-8") if p.exists() else "# Изменения, сделанные агентом «Контур»\n"
        line = "\n- %s — %s\n" % (time.strftime("%Y-%m-%d"), task[:200].replace("\n", " "))
        for r in c["report"][-6:]:
            line += "  - " + r.split("\n")[0][:160] + "\n"
        pa._tool(job, work, {"tool": "write", "path": "CHANGELOG-agent.md", "content": old.rstrip("\n") + "\n" + line})
        c["report"].append("Запись добавлена в CHANGELOG-agent.md.")
    elif op == "check":
        res = pa._tool(job, work, {"tool": "check"})
        bad = [x for x in res if x["ok"] is False]
        c["report"].append("Проверка синтаксиса: ошибок %d из %d файлов" % (len(bad), len(res)))


def _run_applab(job, task, pa, ev):
    """Задача «напиши навык для апп лаб …»: Star Gate пишет приложение и ЗАГРУЖАЕТ его в App Lab этого облака
    (выключенным; проверка — «проверь <имя>» / кнопка в App Lab; удаление — «удали <имя>» / 🗑).
    В архив проекта ничего не пишется."""
    import applab_brain
    m = re.search(r"(?:апп\s*лаб\w*|app\s*lab)\s*(.*)$", task, re.I | re.S)
    desc = m.group(1) if m and len(m.group(1).strip()) > 5 else re.sub(
        r"^.*?(?:навык|приложени\w+|функци\w+|кирпичик\w*|скрипт\w*)\s*", "", task, count=1, flags=re.I)
    desc = re.sub(r"^\s*(?:[:,\-—]\s*)?(?:для|чтобы|который|которое|которая)?\s*", "", desc, flags=re.I)
    desc = re.sub(r"(?:\s*(?:для|в)\s*(?:апп\s*лаб\w*|app\s*lab))", "", desc, flags=re.I).strip(" .?!") or task
    kind = "brick" if re.search(r"кирпичик", task, re.I) else "app"
    ev(job, "Star Gate пишет приложение в App Lab", desc, "plan")
    r = applab_brain.write(desc, kind=kind, internet=bool(job.get("internet")), origin="Star Gate (Контур)")
    ev(job, "Готово" if r.get("ok") else "Не вышло", r["text"][:1500], "result" if r.get("ok") else "warning")
    applab_brain.observe("contour_applab", {"task": task, "ok": r.get("ok"), "id": r.get("id")})
    tail = ("Приложение загружено в App Lab этого облака (id %s) выключенным. Проверка: «проверь %s» или кнопка «Запустить» в App Lab; "
            "удалить — «удали %s» или 🗑. В архив проекта ничего не добавлено." % (r.get("id"), r.get("id"), r.get("id"))) if r.get("ok") else \
           "В App Lab ничего не добавлено. В архив проекта тоже."
    summary = "%s\n%s\nКод проекта не запускался; тесты проекта не выполнялись.\nИтоговый архив: %s (без изменений)" % (
        r["text"], tail, job.get("archive", "project-result.zip"))
    return summary, True


def run(job, work, baseline, deadline, pa):
    """Выполнить инструкцию Контура, затем мозг сам решает, что ещё добавить. -> (итог, закончено?)"""
    ev = pa._event
    task = job["task"]
    found = match(task, 3)
    ev(job, "Star Gate ищет инструкцию Контура в памяти", "; ".join("%s (%.2f)" % (it["title"], s) for s, it in found) or "совпадений нет", "plan")
    it = found[0][1] if found else None
    c = {"job": job, "work": work, "pa": pa, "task": task, "sl": _slots(task), "report": [], "changed": 0, "web_done": False}
    done_ops = []
    steps = []
    if _APPLAB_TASK.search(task):          # v3.229: приложение пишется ПРЯМО В СРЕДУ App Lab, не в архив
        return _run_applab(job, task, pa, ev)
    if it:
        ev(job, "Беру инструкцию", "%s\n%s" % (it["title"], it.get("notes") or ""), "plan")
        steps = list(it["steps"])
    else:
        ev(job, "Инструкции под задачу нет — мозг решает сам", "Смотрю проект, при необходимости иду в интернет", "plan")
        if job.get("internet"):
            steps = [{"op": "web_research"}, {"op": "add_lesson"}]
    def go(stp):
        if time.monotonic() > deadline:
            c["report"].append("Достигнут лимит времени.")
            return False
        try:
            if stp["op"] == "finish":
                return False
            _do_step(stp, c)
            done_ops.append(stp["op"])
        except Exception as e:
            ev(job, "Шаг инструкции не выполнен", "%s: %s" % (stp["op"], e), "warning")
            c["report"].append("Шаг «%s» не выполнен: %s" % (stp["op"], str(e)[:200]))
        return True
    for stp in steps:
        if not go(stp):
            break
    # --- мозг сам решает, что ещё добавить (только облако; на плате этого нет)
    extra = decide(job, work, pa.inventory(work), task, done_ops)
    allowed = bool(((it and it.get("writes")) or _IMPROVE.search(task) or _ADD.search(task)) and not (it and it.get("autonomy") is False))
    if extra and allowed:
        ev(job, "Star Gate решил добавить сам", "\n".join("%s — %s" % (op, why) for op, _a, why in extra), "plan")
        c["report"].append("Решил сам: " + "; ".join("%s (%s)" % (op, why) for op, _a, why in extra))
        for op, args, _why in extra:
            go(dict(args, op=op))
    elif extra and not (it and it.get("autonomy") is False):   # только читает — мозг лишь советует, ничего не пишет
        c["report"].append("Мозг советует добавить (сам не делал, задача только читает): " +
                           "; ".join("%s (%s)" % (op, why) for op, _a, why in extra))
    if not it and not done_ops:
        msg = ("Инструкции Контура под эту задачу нет, а сам решить, что добавить, мозгу не за что зацепиться (%d инструкций). Ничего не менял. "
               "Опишите задачу типовыми словами (ревью, исправление, тесты, описание проекта, «добавь в проект функцию …»)." % len(load_all()))
        ev(job, "Нечего делать", msg, "warning")
        observe(job, None, msg, [], False)
        return msg, True
    if c["changed"] and "changelog" not in done_ops and not (it and it.get("autonomy") is False):
        go({"op": "changelog"})
    title = it["title"] if it else "решение мозга по проекту"
    summary = "Инструкция «%s». %s\nКод проекта не запускался; тесты не выполнялись.\nИтоговый архив: %s" % (
        title, "\n".join(c["report"]), job.get("archive", "project-result.zip"))
    if not c["changed"]:
        summary += "\nИзменений не потребовалось или не нашлось."
    ev(job, "Star Gate завершил работу", summary[:3000], "result")
    observe(job, it, summary, done_ops, True)
    return summary, True


# ------------------------------------------------------------------ LLM наблюдает
def _board():
    try:
        import llmlink
        return llmlink.BOARD
    except Exception:
        return "LLM"


def observe(job, it, summary, ops, found):
    """LLM наблюдает за программированием мозга: статья в БЗ (её читает и отмечает
    «усвоено %» llmlink) + запись в корпус обучения + мысль в ленту."""
    try:
        title = "Контур: наблюдение — " + str(job.get("task") or "")[:60].replace("\n", " ")
        text = "\n".join(["Задача владельца: %s" % str(job.get("task") or "")[:400],
                          "Star Gate выбрал инструкцию: %s" % (it["title"] if it else "не нашёл"),
                          "Операции: %s" % (", ".join(ops) or "—"),
                          "Журнал действий:", *[("- %s: %s" % (e.get("status"), str(e.get("detail") or "")[:200].replace("\n", " ")))
                                                for e in (job.get("events") or [])[-14:] if e.get("kind") in ("write", "read", "check", "plan", "warning")],
                          "Итог: %s" % str(summary)[:900],
                          "Наблюдение LLM: запомни связку «задача → инструкция → операции → итог»; это обучающий пример."])
        import kb
        kb.add_article(title, text, source="contour-observe", did="obs_" + hashlib.md5(str(job.get("id")).encode()).hexdigest()[:12])
        lst = store.load_json(_LEARN, []) or []
        lst.append({"ts": time.time(), "text": text})
        store.save_json(_LEARN, lst[-500:])
        try:
            store.thoughts_add(_board(), "👁 LLM наблюдала, как мозг программирует: «%s» → %s" % (
                str(job.get("task") or "")[:80], it["title"] if it else "инструкции не нашлось"), kind="read")
        except Exception:
            pass
        return True
    except Exception:
        return False
