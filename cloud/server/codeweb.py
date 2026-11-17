# -*- coding: utf-8 -*-
"""
codeweb.py — v3.129: раздел «Код» ищет готовый код в интернете и ПРОВЕРЯЕТ его.

Когда заготовки не подошли, а локальной модели (Ollama и т.п.) нет, код
берётся с настоящих страниц (выдача Google/Яндекс: Stack Overflow, GitHub,
документация, Хабр…): из блоков <pre>/<code> вынимается код нужного языка,
Python запускается в песочнице облака, остальные языки проверяются на
синтаксис. В ответ идёт только код, прошедший проверку, и ВСЕГДА со ссылкой
на страницу, откуда он взят: это чужой код, а не написанный мозгом.
"""
import html
import re
import time

_LANG_Q = {"python": "python", "javascript": "javascript", "arduino": "arduino",
           "html": "html", "bash": "bash", "sql": "sql"}
_PRE = re.compile(r"<pre[^>]*>([\s\S]*?)</pre>|<code[^>]*>([\s\S]{40,}?)</code>", re.I)
_TAG = re.compile(r"<[^>]+>")
_LOOKS = {
    "python": re.compile(r"^\s*(def |import |from \w+ import|print\(|for .+ in .+:|class \w+)", re.M),
    "javascript": re.compile(r"\b(function\s+\w+|const |let |=>|console\.log)"),
    "arduino": re.compile(r"\bvoid\s+(setup|loop)\s*\("),
    "bash": re.compile(r"^\s*(#!/bin/(ba)?sh|for .+; do|if \[|echo )", re.M),
    "sql": re.compile(r"\b(SELECT|CREATE TABLE|INSERT INTO)\b", re.I),
    "html": re.compile(r"<(html|body|div|!DOCTYPE)", re.I),
}
# латинские слова для поиска: «перевернуть строку» -> «reverse string»
_EN = {"строк": "string", "перевер": "reverse", "разверн": "reverse", "сорт": "sort",
       "спис": "list", "словар": "dict", "файл": "file", "чита": "read",
       "запис": "write", "числ": "number", "прост": "prime", "сумм": "sum",
       "дат": "date", "врем": "time", "случайн": "random", "пароль": "password",
       "json": "json", "csv": "csv", "удал": "remove", "дубл": "duplicates",
       "поиск": "search", "максим": "max", "миним": "min", "средн": "average",
       "гласн": "vowels", "слов": "words", "букв": "letters", "квадрат": "square",
       "корен": "root", "матриц": "matrix", "фактор": "factorial", "палиндр": "palindrome"}


def _query(task, lang):
    low = str(task or "").lower()
    en = []
    for k, v in _EN.items():
        if k in low and v not in en:
            en.append(v)
    q = "%s %s" % (_LANG_Q.get(lang, lang), " ".join(en) if len(en) >= 2 else task)
    return q[:120]


def _blocks(body, lang):
    out = []
    for m in _PRE.finditer(body or ""):
        raw = m.group(1) or m.group(2) or ""
        code = html.unescape(_TAG.sub("", raw)).replace("\r", "").strip("\n")
        lines = [ln for ln in code.split("\n") if ln.strip()]
        if not (3 <= len(lines) <= 80) or len(code) > 4000:
            continue
        if code.lstrip().startswith((">>>", "$ ", "In [")):
            continue                           # сессия интерпретатора — не программа
        pat = _LOOKS.get(lang)
        if pat and not pat.search(code):
            continue
        out.append(code)
    return out


LAST = {"why": ""}


def find(task, lang="python", budget_s=20.0, max_pages=5):
    """-> dict(ok, code, url, title, check) | dict(ok=False, why)."""
    r = _find(task, lang, budget_s, max_pages)
    LAST["why"] = "" if r.get("ok") else str(r.get("why") or "")
    return r


def _find(task, lang="python", budget_s=20.0, max_pages=5):
    import codewrite
    import webfetch
    t0 = time.time()
    try:
        pages = webfetch.top_pages(_query(task, lang), limit=8)
    except Exception as e:
        return {"ok": False, "why": "поиск не выполнился: %s" % e}
    if not pages:
        return {"ok": False, "why": "выдача пуста (интернет недоступен или заблокирован)"}
    tried, last = 0, ""
    for p in pages[:max_pages]:
        if time.time() - t0 > budget_s:
            last = "кончилось время поиска (%d с)" % budget_s
            break
        ok, body = webfetch._fetch_bytes(p["url"], timeout=8, max_bytes=600000)
        if not ok:
            last = str(body)[:80]
            continue
        for code in _blocks(body, lang)[:6]:
            tried += 1
            c_ok, what, _crit = codewrite._check(lang, code)
            if c_ok:
                return {"ok": True, "code": code, "url": p["url"],
                        "title": p.get("title") or p["url"], "check": what,
                        "tried": tried}
            last = what
            if time.time() - t0 > budget_s:
                break
    return {"ok": False, "why": ("на страницах выдачи не нашлось кода, прошедшего "
                                 "проверку (проверено блоков: %d%s)" %
                                 (tried, ("; " + last) if last else ""))}
