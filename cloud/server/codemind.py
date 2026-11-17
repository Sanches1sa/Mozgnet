# -*- coding: utf-8 -*-
# ===========================================================================
# codemind.py — УМЕНИЕ ЧИТАТЬ ПРОГРАММНЫЙ КОД (v3.9.0, только stdlib)
# ---------------------------------------------------------------------------
# Зачем: владелец попросил «добавить умение читать программный код». Мозг
# облака до сих пор умел отвечать по СТАТЬЯМ, но код (main.py платы,
# sketch.ino, свои функции из App Lab) воспринимал как простой текст — без
# понимания, где функция, что она вызывает и за что отвечает.
#
# КАК УСТРОЕНО (по практике RAG-систем на кодовых базах):
#   1) ЧАНКИНГ ПО СУЩНОСТЯМ — код режется не по абзацам, а по функциям/
#      классам/defines: у каждой сущности имя, сигнатура, докстринг или
#      комментарий, вызовы, строки. Python разбирается модулем ast (честное
#      дерево), C/C++/ino — сканером с балансом скобок и регэкспами.
#   2) ТРИ ХРАНИЛИЩА ОДНОВРЕМЕННО:
#        - data/code_index.json — точный индекс (ответы «что делает X»);
#        - KB-статья на ФАЙЛ (оглавление функций) — поиск и платам;
#        - mozgnet учится на КАЖДОЙ функции как на материале — ассоциативная
#          сеть связывает имена функций с темами (генератор «умеет» код).
#   3) ОТВЕТ НА ВОПРОС: стемы запроса -> совпадения по именам функций,
#      докстрингам, вызовам, файлам -> сборка: сигнатура, файл/строки,
#      докстринг, вызовы, связанные функции.
# Всё атомарно хранится и переживает перезапуск. Потокобезопасность — LOCK.
# ===========================================================================
import ast
import json
import os
import re
import textwrap
import threading
import time

import kb
import store
import textutils

LOCK = threading.RLock()
_PATH = None

_WORD_RE = re.compile(r"[а-яёa-z0-9_]{2,}")

C_FUNC_RE = re.compile(
    r"^[\s]*(?:static\s+|inline\s+|const\s+|unsigned\s+|signed\s+|struct\s+"
    r"|volatile\s+|extern\s+)*[A-Za-z_][\w \t\*&:<>,]*?[\s\*]+"
    r"([A-Za-z_]\w*)\s*\(([^;{()]*)\)\s*\{")
C_DEFINE_RE = re.compile(r"^\s*#define\s+([A-Za-z_]\w*)\s+(.+?)\s*$")
C_CLASS_RE = re.compile(r"^\s*(?:class|struct)\s+([A-Za-z_]\w*)")


# ------------------------------ служебное -----------------------------------
def _path():
    global _PATH
    if _PATH is None:
        _PATH = store.p("code_index.json")
    return _PATH


def _load():
    try:
        with open(_path(), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"files": {}}


def _save(idx):
    tmp = _path() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(idx, f, ensure_ascii=False)
    os.replace(tmp, _path())


def _lang(name, text):
    n = str(name or "").lower()
    if n.endswith(".py"):
        return "python"
    if n.endswith((".ino", ".c", ".cpp", ".h", ".hpp")):
        return "c"
    if n.endswith((".js", ".ts")):
        return "js"
    if "def " in text and ":\n" in text:
        return "python"
    if re.search(r"\b(void|int|bool|String)\s+\w+\s*\(", text):
        return "c"
    return "text"


def _clip(s, n=220):
    s = re.sub(r"\s+", " ", str(s or "")).strip()
    return s[:n]


# ------------------------------ python AST ----------------------------------
def _parse_python(text, cap=400):
    """Разбор Python через ast: функции/классы/константы с вызовами."""
    out = {"functions": [], "classes": [], "defines": []}
    try:
        tree = ast.parse(text)
    except SyntaxError:
        # код может быть куском/с общим отступом — пробуем снять отступы
        try:
            tree = ast.parse(textwrap.dedent(text or ""))
        except Exception:
            return out
    lines = text.splitlines()

    def _calls_of(node):
        calls = []
        for c in ast.walk(node):
            if isinstance(c, ast.Call):
                f = c.func
                nm = getattr(f, "id", None) or getattr(f, "attr", None)
                if nm and nm not in calls:
                    calls.append(nm)
        return calls[:14]

    def _doc_of(node):
        d = ast.get_docstring(node)
        if d:
            return _clip(d, 260)
        return ""

    def _sig(node):
        try:
            a = []
            args = list(getattr(node.args, "args", []))
            for x in args:
                a.append(getattr(x, "arg", "?"))
            if getattr(node.args, "vararg", None):
                a.append("*" + node.args.vararg.arg)
            kw = getattr(node.args, "kwarg", None)
            if kw:
                a.append("**" + kw.arg)
            return "%s(%s)" % (node.name, ", ".join(a))
        except Exception:
            return node.name + "(...)"

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if len(out["functions"]) >= cap:
                break
            doc = _doc_of(node) or _comment_above(lines, node.lineno - 1)
            out["functions"].append({
                "name": node.name, "sig": _sig(node), "doc": doc,
                "calls": _calls_of(node), "line": node.lineno,
                "class": ""})
        elif isinstance(node, ast.ClassDef):
            methods = [n.name for n in node.body
                       if isinstance(n, (ast.FunctionDef,
                                         ast.AsyncFunctionDef))][:12]
            out["classes"].append({
                "name": node.name, "doc": _doc_of(node),
                "line": node.lineno, "methods": methods})
        elif isinstance(node, ast.Assign):
            t = node.targets[0] if node.targets else None
            if isinstance(t, ast.Name) and (t.id.isupper() or
                                            t.id.startswith("_RE")):
                try:
                    v = ast.literal_eval(node.value)
                except Exception:
                    v = ""
                out["defines"].append({"name": t.id, "value": _clip(v, 60),
                                       "line": node.lineno})
    return out


def _comment_above(lines, ln):
    """Комментарий-строка над определением (для кода без докстрингов)."""
    out = []
    i = min(ln, len(lines)) - 2
    while i >= 0 and len(out) < 3:
        s = lines[i].strip()
        if s.startswith("#") and not s.startswith("#!"):
            out.insert(0, s.lstrip("# ").strip())
            i -= 1
        else:
            break
    return _clip(" ".join(out), 260)


# ------------------------------ C/C++/ino -----------------------------------
def _parse_c(text):
    """Сканер C/C++: функции по балансу скобок, defines, классы/структуры."""
    out = {"functions": [], "classes": [], "defines": []}
    lines = text.splitlines()
    i = 0
    n = len(lines)
    while i < n and len(out["functions"]) < 400:
        s = lines[i]
        m = C_FUNC_RE.match(s)
        if not m and i + 1 < n:
            # сигнатура может занять две строки: тип и имя на разных
            m2 = C_FUNC_RE.match(s.strip() + " " + lines[i + 1].strip())
            m = m2 if m2 else None
        if m:
            fname, args = m.group(1), _clip(m.group(2), 120)
            # баланс скобок с этой строки
            depth = 0
            started = False
            j = i
            while j < n and j - i < 400:
                for ch in re.sub(r'"(?:\\.|[^"\\])*"', '""',
                                 re.sub(r"'(?:\\.|[^'\\])*'", "''",
                                        re.sub(r"//[^\n]*", "",
                                               re.sub(r"/\*.*?\*/", "",
                                                      lines[j], flags=re.S)))):
                    if ch == "{":
                        depth += 1
                        started = True
                    elif ch == "}":
                        depth -= 1
                if started and depth <= 0:
                    break
                j += 1
            doc = _comment_above(lines, i + 1)
            body_calls = []
            for k in range(i, min(j + 1, n)):
                for cm in re.finditer(
                        r"\b([A-Za-z_]\w{2,})\s*\(", lines[k]):
                    if cm.group(1) not in body_calls and \
                            cm.group(1) not in ("if", "for", "while",
                                                "switch", "return"):
                        body_calls.append(cm.group(1))
                if len(body_calls) >= 14:
                    break
            out["functions"].append({
                "name": fname, "sig": "%s(%s)" % (fname, args), "doc": doc,
                "calls": body_calls[:14], "line": i + 1, "class": ""})
            i = j + 1
            continue
        dm = C_DEFINE_RE.match(s)
        if dm:
            out["defines"].append({"name": dm.group(1),
                                   "value": _clip(dm.group(2), 60),
                                   "line": i + 1})
        cm = C_CLASS_RE.match(s)
        if cm:
            out["classes"].append({"name": cm.group(1), "doc": "",
                                   "line": i + 1, "methods": []})
        i += 1
    return out


# ------------------------------ разбор файла --------------------------------
def ingest(name, text):
    """Прочитать файл кода: разобрать на сущности, проиндексировать,
    выложить чанки в mozgnet и KB. Возврат: сводка {lang, functions, ...}."""
    name = str(name or "код.txt").strip()[:80]
    text = str(text or "")
    if len(text.strip()) < 20:
        return {"ok": False, "error": "код пустой или слишком короткий"}
    lang = _lang(name, text)
    if lang == "python":
        parsed = _parse_python(text)
    elif lang == "c":
        parsed = _parse_c(text)
    else:
        # js/text: грубые функции по регэкспу, чтобы зона не молчала
        parsed = {"functions": [], "classes": [], "defines": []}
        for m in re.finditer(r"function\s+([A-Za-z_]\w*)\s*\(([^)]*)\)",
                             text[:200000]):
            parsed["functions"].append({
                "name": m.group(1), "sig": "%s(%s)" % (m.group(1),
                                                       m.group(2)[:80]),
                "doc": "", "calls": [], "line": 0, "class": ""})
    funcs = parsed["functions"]
    if not funcs and not parsed["classes"]:
        return {"ok": False,
                "error": "не нашёл ни функций, ни классов — это код?"}
    with LOCK:
        idx = _load()
        files = idx.setdefault("files", {})
        files[name] = {
            "lang": lang, "ts": int(time.time()),
            "n_lines": text.count("\n") + 1,
            "functions": [
                {"name": f["name"], "sig": f["sig"][:140],
                 "doc": f["doc"][:260], "calls": f["calls"],
                 "line": f["line"], "class": f.get("class", "")}
                for f in funcs[:400]],
            "classes": parsed["classes"][:80],
            "defines": parsed["defines"][:160],
        }
        if len(files) > 30:                    # LRU по времени
            for k in sorted(files, key=lambda k: files[k].get("ts", 0))[
                    :-30]:
                files.pop(k, None)
        _save(idx)
    _teach(name, lang, parsed, text)
    return {"ok": True, "name": name, "lang": lang,
            "functions": len(funcs), "classes": len(parsed["classes"]),
            "defines": len(parsed["defines"]),
            "lines": text.count("\n") + 1}


def _teach(name, lang, parsed, text):
    """Код -> знания: чанк на функцию в mozgnet + статья-оглавление в KB."""
    import brain
    chunks = []
    for f in parsed["functions"][:400]:
        parts = ["Файл %s (%s): функция %s." % (name, lang, f["sig"])]
        if f.get("doc"):
            parts.append("Назначение: %s" % f["doc"])
        if f.get("calls"):
            parts.append("Вызывает: %s." % ", ".join(f["calls"][:10]))
        chunks.append(" ".join(parts))
    for c in parsed["classes"][:80]:
        m = ", ".join(c.get("methods") or [])
        chunks.append("Файл %s (%s): класс %s.%s%s" % (
            name, lang, c["name"],
            " Назначение: %s" % c["doc"] if c.get("doc") else "",
            " Методы: %s." % m if m else ""))
    for d in parsed["defines"][:160]:
        chunks.append("Файл %s: константа %s = %s." % (
            name, d["name"], d["value"]))
    for ch in chunks[:200]:
        try:
            brain.mn_learn(ch, title="Код: %s" % name[:60], src="код")
        except Exception:
            pass
    # KB: одна статья на файл (оглавление) — платам и поиску
    lines_out = ["Код %s: %s, %d строк." % (
        name, lang, text.count("\n") + 1)]
    if parsed["classes"]:
        lines_out.append("Классы: " + ", ".join(
            c["name"] for c in parsed["classes"][:20]) + ".")
    if parsed["functions"]:
        lines_out.append("Функции (%d):" % len(parsed["functions"]))
        for f in parsed["functions"][:120]:
            lines_out.append("- %s%s%s" % (
                f["sig"],
                (" — %s" % f["doc"]) if f.get("doc") else "",
                (" (вызывает %s)" % ", ".join(f["calls"][:5]))
                if f.get("calls") else ""))
    try:
        kb.add_article("Код: %s" % name[:60], "\n".join(lines_out)[:18000],
                       source="код (codemind)")
    except Exception:
        pass


# ------------------------------ вопросы -------------------------------------
def _match_score(q_stems, f, fname_l):
    sc = 0
    for st in q_stems:
        if len(st) < 3:
            continue
        if st in fname_l:
            sc += 6 if fname_l.startswith(st) else 4
        if f.get("doc") and st in f["doc"].lower():
            sc += 2
        if any(st in c.lower() for c in f.get("calls", [])):
            sc += 1
    return sc


def ask(q):
    """Ответ на вопрос про код. Возврат: (текст|"", уверенность 0..1)."""
    q = str(q or "").strip()
    if not q:
        return "", 0.0
    with LOCK:
        idx = _load()
    files = idx.get("files") or {}
    if not files:
        return "", 0.0
    low = q.lower()
    stems = set(textutils.stem(w) for w in _WORD_RE.findall(low) if len(w) > 2)
    # прямое имя: «что делает функция tts_say», «rpcSayWave()»
    direct = None
    for m in re.finditer(r"[A-Za-z_]\w{2,}", q):
        w = m.group(0)
        for fn, fd in files.items():
            for f in fd.get("functions", []):
                if f["name"].lower() == w.lower():
                    direct = (fn, f)
                    break
            if direct:
                break
        if direct:
            break
    if direct:
        fn, f = direct
        return _fmt_func(fn, f), 1.0
    scored = []
    for fn, fd in files.items():
        for f in fd.get("functions", []):
            sc = _match_score(stems, f, f["name"].lower())
            if sc:
                scored.append((sc, fn, f))
    if not scored:
        # может, спрашивают про файл целиком («расскажи про main.py»)
        for fn, fd in files.items():
            if fn.lower() in low or stems & set(
                    textutils.stem(w) for w in _WORD_RE.findall(fn.lower())):
                return _fmt_file(fn, fd), 0.8
        return "", 0.0
    scored.sort(key=lambda x: -x[0])
    top = scored[0]
    if top[0] < 6:
        return "", 0.0                      # неуверен — пусть отвечают зоны
    parts = [_fmt_func(top[1], top[2])]
    others = [s for s in scored[1:4] if s[0] >= top[0] * 0.5]
    if others:
        parts.append("Связанные функции: " + "; ".join(
            "%s (%s, стр. %s)" % (f["name"], fn, f.get("line") or "?")
            for _s, fn, f in others) + ".")
    return "\n\n".join(parts), min(1.0, 0.5 + top[0] / 20.0)


def _fmt_func(fn, f):
    out = ["%s — файл %s, строка %s." % (f["sig"] or f["name"], fn,
                                         f.get("line") or "?")]
    if f.get("doc"):
        out.append("Назначение: %s" % f["doc"])
    else:
        out.append("Докстринга нет: суть — по вызовам и месту в коде.")
    if f.get("calls"):
        out.append("Вызывает: %s." % ", ".join(f["calls"][:10]))
    return "\n".join(out)


def _fmt_file(fn, fd):
    funcs = fd.get("functions") or []
    return ("Файл %s: язык %s, строк %s, функций %s, классов %s.\n"
            "Главное: %s" % (fn, fd.get("lang", "?"), fd.get("n_lines", "?"),
                             len(funcs), len(fd.get("classes") or []),
                             ", ".join(f["name"] for f in funcs[:12]) or "—"))


def outline():
    """Сводка индекса для панели: файлы и счётчики."""
    with LOCK:
        idx = _load()
    out = []
    for fn, fd in (idx.get("files") or {}).items():
        out.append({"name": fn, "lang": fd.get("lang"),
                    "lines": fd.get("n_lines"),
                    "functions": len(fd.get("functions") or []),
                    "classes": len(fd.get("classes") or []),
                    "defines": len(fd.get("defines") or []),
                    "ts": fd.get("ts")})
    out.sort(key=lambda x: -float(x.get("ts") or 0))
    return {"ok": True, "files": out}


def stats():
    with LOCK:
        idx = _load()
    files = idx.get("files") or {}
    return {"code_files": len(files),
            "code_funcs": sum(len(fd.get("functions") or [])
                              for fd in files.values())}
