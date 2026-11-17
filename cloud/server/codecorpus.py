# -*- coding: utf-8 -*-
# ===========================================================================
# codecorpus.py — КОД КАК ОБУЧАЮЩИЙ МАТЕРИАЛ ДЛЯ СВОЕЙ LLM (v3.41, stdlib)
# ---------------------------------------------------------------------------
# Статья базы знаний «Исходник: репо/путь» (её кладёт ghlearn из GitHub)
# превращается в текст для обучения: набор коротких функций вида
#     # task: <что делает>
#     def имя(...):
#         ...
# Python разбирается через ast и записывается в каноничном виде (отступы
# по 4 пробела), так что токенайзер учится на единообразном коде. Остальные
# языки режутся на блоки по пустым строкам. Здесь же: подсказка-перевод
# просьбы с русского на английские слова (код в основном англоязычный),
# и обрезка сгенерированного текста до одной законченной функции.
# ===========================================================================
import ast
import re

CODE_MARK = "⟦код⟧"
SNIP_MAX = 1500
ART_MAX = 14000

_GLOSS = {
    "сумм": "sum", "сложи": "sum add", "сложен": "add", "вычти": "subtract",
    "умнож": "multiply", "раздел": "divide", "средн": "average mean",
    "максимум": "max", "минимум": "min", "наибольш": "max", "наименьш": "min",
    "списк": "list", "массив": "array list", "строк": "string text",
    "слов": "word", "букв": "char letter", "символ": "char", "числ": "number",
    "цел": "int", "дробн": "float", "словар": "dict", "множеств": "set",
    "кортеж": "tuple", "палиндром": "palindrome", "факториал": "factorial",
    "фибоначчи": "fibonacci", "прост": "prime", "делител": "divisor",
    "нод": "gcd", "нок": "lcm", "четн": "even", "нечетн": "odd",
    "сортир": "sort", "поиск": "search find", "найди": "find",
    "найти": "find", "перевер": "reverse", "обрат": "reverse",
    "подсчит": "count", "посчит": "count", "счита": "count",
    "колич": "count", "уникальн": "unique", "дублик": "duplicate",
    "удали": "remove", "удалить": "remove", "добав": "add append",
    "вставь": "insert", "заменить": "replace", "замени": "replace",
    "раздели": "split", "объедин": "join merge", "соедин": "join",
    "фильтр": "filter", "преобраз": "convert", "перевед": "convert",
    "перевод": "convert", "температур": "temperature", "цельси": "celsius",
    "фаренгейт": "fahrenheit", "килограмм": "kilogram", "километр": "km",
    "миль": "mile", "файл": "file", "прочита": "read", "читай": "read",
    "запиш": "write", "сохран": "save", "json": "json", "дат": "date",
    "врем": "time", "год": "year", "високос": "leap", "проверь": "check is",
    "проверк": "check is", "является": "is", "содержит": "contains",
    "пуст": "empty", "длин": "length len", "первы": "first", "послед": "last",
    "квадрат": "square", "куб": "cube", "степен": "power pow",
    "корен": "sqrt root", "абсолют": "abs", "округл": "round",
    "случайн": "random", "пароль": "password", "генерир": "generate",
    "создай": "create make", "напиши": "", "функци": "function",
    "класс": "class", "стек": "stack", "очеред": "queue", "дерев": "tree",
    "граф": "graph", "матриц": "matrix", "бинарн": "binary", "двоичн": "binary",
    "шестнадцат": "hex", "регистр": "case", "заглавн": "upper capitalize",
    "строчн": "lower", "пробел": "space whitespace strip", "гласн": "vowel",
    "согласн": "consonant", "анаграмм": "anagram", "частот": "frequency",
    "путь": "path", "пут": "path", "склей": "join", "склеив": "join",
    "разбор": "parse", "распарс": "parse", "домашн": "home user",
    "расширен": "extension", "имя": "name", "папк": "directory dir",
    "каталог": "directory dir", "таблиц": "table", "csv": "csv", "url": "url", "email": "email",
    "почт": "email", "адрес": "address", "ошибк": "error",
}


def ru_en(task):
    """«напиши функцию суммы списка» -> «function sum list» (латиница и
    цифры из просьбы сохраняются)."""
    out = []
    for w in re.findall(r"[A-Za-z_][A-Za-z_0-9]*|\d+|[а-яё]+", str(task or "")
                        .lower()):
        if re.match(r"[a-z_0-9]", w):
            out.append(w)
            continue
        for k, v in _GLOSS.items():
            if w.startswith(k):
                if v:
                    out.append(v)
                break
    seen, res = set(), []
    for w in " ".join(out).split():
        if w not in seen:
            seen.add(w)
            res.append(w)
    return " ".join(res[:12])


def _split_ident(name):
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", name).replace("_", " ")
    return " ".join(s.lower().split())


def _py_items(text):
    """Python -> [(task, имя, исходник)] (ast, каноничное форматирование)."""
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError, RecursionError):
        return None
    out = []

    def one(node):
        doc = ast.get_docstring(node) or ""
        body = list(node.body)
        if body and isinstance(body[0], ast.Expr) and isinstance(
                getattr(body[0], "value", None), ast.Constant) and isinstance(
                body[0].value.value, str):
            body = body[1:] or [ast.Pass()]
        node.body = body
        try:
            src = ast.unparse(node)
        except Exception:
            return
        if len(src) > SNIP_MAX or src.count("\n") < 1:
            return
        task = " ".join(doc.strip().split("\n")[0].split())[:120] \
            or _split_ident(node.name)
        out.append((task, node.name, src))

    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if n.name.startswith("__") and n.name != "__init__":
                continue
            one(n)
        if len(out) >= 60:
            break
    return out


def _py_snippets(text):
    items = _py_items(text)
    if items is None:
        return None
    return ["# task: %s\n%s" % (t, src) for t, _n, src in items]


def article_text(title, text):
    """Статья «Исходник: …» -> текст для обучения (с меткой кода)."""
    title = str(title or "")
    text = str(text or "")
    snips = None
    if title.lower().endswith(".py"):
        snips = _py_snippets(text)
    if not snips:
        name = title.split(":", 1)[-1].strip()
        snips, cur = [], ""
        for blk in re.split(r"\n\s*\n", text):
            if len(cur) + len(blk) > 1200 and cur:
                snips.append("# file: %s\n%s" % (name, cur.strip("\n")))
                cur = ""
            cur += blk + "\n\n"
            if len(snips) >= 12:
                break
        if cur.strip() and len(snips) < 12:
            snips.append("# file: %s\n%s" % (name, cur.strip("\n")))
    body = "\n\n".join(snips)[:ART_MAX]
    return CODE_MARK + body


def is_code_title(title):
    return str(title or "").startswith("Исходник:")


def trim(text):
    """Сгенерированное -> одна законченная функция (str) | ''."""
    t = str(text or "")
    for stop in ("\n\n# task:", "\n# task:", "\n\n# file:"):
        i = t.find(stop)
        if i >= 0:
            t = t[:i]
    # после тела функции пустая строка и новый верхний уровень — конец
    m = re.search(r"\n\n(?=\S)", t)
    if m:
        t = t[:m.start()]
    lines = [l.rstrip() for l in t.split("\n")]
    while lines:
        src = "\n".join(lines).strip("\n")
        if src:
            try:
                tree = ast.parse(src)
                if any(isinstance(n, (ast.FunctionDef, ast.ClassDef,
                                      ast.AsyncFunctionDef)) for n in tree.body):
                    return src
            except (SyntaxError, ValueError):
                pass
        lines.pop()
    return ""


# ----------------------- ПОИСК ПОХОЖЕЙ ФУНКЦИИ В ИЗУЧЕННОМ ------------------
# Каждый прочитанный python-файл GitHub даёт функции (task, имя, код,
# источник) в data/code_snips.json. Когда просят написать код, сначала
# ищется самая подходящая изученная функция — она проверяется (синтаксис,
# lint, песочница) и предлагается как образец с указанием источника.
import json as _json
import math as _math
import os as _os
import threading as _th

_SN_LOCK = _th.RLock()
_SN_CACHE = {"mtime": None, "rows": [], "df": {}, "n": 0}
SNIPS_MAX = 20000


def _words(s):
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", str(s)).replace("_", " ")
    out = []
    for w in re.findall(r"[a-zA-Z]{2,}|\d+", s.lower()):
        if len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
            w = w[:-1]
        out.append(w)
    return out


def _snips_path():
    import store
    return store.p("code_snips.json")


def add_snips(source, text, lic=""):
    """Сохранить функции файла для поиска. Возврат: сколько добавлено."""
    items = _py_items(text) or []
    if not items:
        return 0
    with _SN_LOCK:
        try:
            with open(_snips_path(), encoding="utf-8") as f:
                rows = _json.load(f)
        except Exception:
            rows = []
        rows = [r for r in rows if r.get("s") != source]
        for t, n, src in items:
            rows.append({"t": t, "n": n, "c": src, "s": source, "l": lic})
        rows = rows[-SNIPS_MAX:]
        _os.makedirs(_os.path.dirname(_snips_path()) or ".", exist_ok=True)
        tmp = _snips_path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            _json.dump(rows, f, ensure_ascii=False)
        _os.replace(tmp, _snips_path())
    return len(items)


def _load_snips():
    p = _snips_path()
    try:
        mt = _os.path.getmtime(p)
    except OSError:
        return _SN_CACHE
    if _SN_CACHE["mtime"] == mt:
        return _SN_CACHE
    try:
        with open(p, encoding="utf-8") as f:
            rows = _json.load(f)
    except Exception:
        return _SN_CACHE
    df = {}
    for r in rows:
        r["_w"] = set(_words(r["t"]) + _words(r["n"]))
        for w in r["_w"]:
            df[w] = df.get(w, 0) + 1
    _SN_CACHE.update(mtime=mt, rows=rows, df=df, n=len(rows))
    return _SN_CACHE


def count_snips():
    with _SN_LOCK:
        return _load_snips()["n"]


def find(task, k=5, min_score=1.5):
    """Самые подходящие изученные функции: [{code, name, source, score}]."""
    stop = {"function", "make", "create", "check", "is", "code", "python",
            "def", "get", "write", "the", "for", "and", "with", "from"}
    q = set(_words(ru_en(task)) + _words(task)) - stop
    if not q:
        return []
    with _SN_LOCK:
        c = _load_snips()
        rows, df, n = c["rows"], c["df"], max(1, c["n"])
        scored = []
        for r in rows:
            common = q & r["_w"]
            if not common or len(common) / len(q) < 0.5:
                continue
            first = r["c"].split("\n")[0]
            if re.match(r"(async )?def \w+\((self|cls)\b", first):
                continue                       # методы — без контекста класса
            sc = sum(_math.log(1 + n / (1 + df.get(w, 0))) for w in common)
            sc /= _math.sqrt(len(r["_w"]) + 1)
            # доля слов просьбы, найденных в функции, и краткость кода
            sc *= (len(common) / len(q)) ** 0.5
            sc /= (1 + len(r["c"]) / 800.0)
            scored.append((sc, r))
    scored.sort(key=lambda x: -x[0])
    return [{"code": r["c"], "name": r["n"], "source": r["s"], "lic": r.get("l", ""),
             "score": round(sc, 2)} for sc, r in scored[:k] if sc >= min_score]
