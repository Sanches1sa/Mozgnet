# -*- coding: utf-8 -*-
"""v3.225 — ИНСТРУКЦИИ ПО НАПИСАНИЮ ФУНКЦИЙ И КИРПИЧИКОВ APP LAB.

applab_guide.json (собирает tools/build_applab_guide.py): уроки «как написать
функцию (приложение) App Lab и кирпичик», у каждого урока пример кода. Пример
при сборке и в тестах запускается как настоящий run(text, ctx) на заглушке ctx
с готовыми ответами (сети нет), результат сверяется с ожидаемым.
Уроки идут: в базу знаний («App Lab: …»), в поток обучения своей LLM (код с
задачей «# task: App Lab: …» — так она учится писать приложения), в ответ
раздела «Код», в инструкции Контура. Честно: это типовые приёмы; готовую функцию
облако проверяет запуском, а ВКЛЮЧАЕТ её владелец в App Lab."""
import hashlib
import json
import os
import re
import types

import store

_HERE = os.path.dirname(os.path.abspath(__file__))
_CACHE = {"items": None}
MIN_SCORE = 0.34


def load():
    if _CACHE["items"] is None:
        try:
            with open(os.path.join(_HERE, "applab_guide.json"), encoding="utf-8") as f:
                items = json.load(f)
        except Exception:
            items = []
        _CACHE["items"] = [x for x in items if isinstance(x, dict) and x.get("id") and x.get("title")]
    return _CACHE["items"]


def lesson_text(it):
    lines = ["Инструкция App Lab: %s" % it["title"], str(it.get("text") or "").strip()]
    if it.get("code"):
        lines.append("Пример python/main.py:\n```python\n%s```" % it["code"])
    if it.get("phrase"):
        lines.append("Фраза в чате: «%s»%s" % (it["phrase"], (" → " + it["expect"]) if it.get("expect") else ""))
    lines.append("Статус: пример запущен на заглушке ctx; на живом облаке и интернете не проверен."
                 if it.get("verified") else "Статус: описание приёма (кода нет).")
    return "\n".join(lines)


def _stems(s):
    import codelessons
    return codelessons._stems(s)


def match(task, limit=3):
    q = _stems(task)
    if not q:
        return []
    out = []
    for it in load():
        pool = set()
        for a in list(it.get("ask") or []) + [it["title"]]:
            pool |= _stems(a)
        if not pool:
            continue
        hit = q & pool
        sc = len(hit) / float(min(len(q), len(pool)) or 1)
        if hit and sc >= MIN_SCORE:
            out.append((round(sc, 2), it))
    out.sort(key=lambda x: -x[0])
    return out[:limit]


def compose(task):
    m = match(task, 1)
    if not m:
        return None
    return "Это про App Lab — беру инструкцию (совпадение %.2f).\n%s" % (m[0][0], lesson_text(m[0][1]))


def _did(it):
    return "applab_" + hashlib.md5(it["id"].encode("utf-8")).hexdigest()[:12]


def seed_kb():
    import kb
    n = 0
    for it in load():
        r = kb.add_article("App Lab: " + it["title"], lesson_text(it), source="applab_guide", did=_did(it))
        if r.get("ok"):
            n += 1
    return n


def corpus():
    """[(подпись, текст)] для потока обучения своей LLM."""
    out = []
    for it in load():
        t = lesson_text(it)
        out.append(("al-l-" + hashlib.md5(t.encode("utf-8")).hexdigest()[:14], t))
        if it.get("code"):
            doc = "# task: App Lab: " + " ".join((it.get("ask") or [it["title"]])[0].split())
            t2 = doc + "\n" + it["code"]
            out.append(("al-c-" + hashlib.md5(t2.encode("utf-8")).hexdigest()[:14], t2))
    return out


# ------------------------------ проверка -----------------------------------
def stub_ctx(phrase=""):
    """ctx с готовыми ответами: так пример запускается без сети и без мозга."""
    c = types.SimpleNamespace()
    c.text, c.app, c.board, c.out, c.log_lines = phrase, "demo", "владелец", [], []
    c.say = lambda *a: c.out.append(" ".join(str(x) for x in a))
    c.log = lambda *a: c.log_lines.append(" ".join(str(x) for x in a))
    ns = types.SimpleNamespace
    c.web = ns(search=lambda q, n=5: [{"title": "Страница про " + str(q)[:20], "url": "https://example.org/1", "snippet": "найдено"}],
               page=lambda u, chars=6000: {"url": u, "title": "T", "text": "текст страницы"},
               wiki=lambda t: {"title": str(t).title(), "text": "Статья про %s." % t, "src": "wiki"})
    c.docs = ns(search=lambda q, n=5: [{"title": "Док", "url": "https://wiki.local/1", "src": "wiki", "snippet": "s"}],
                page=lambda q: {"title": "Док", "text": "текст дока", "src": "wiki"})
    mem = []
    c.kb = ns(search=lambda q, n=3: [{"title": "Статья БЗ", "snippet": "кусок", "score": 1.0}],
              add=lambda t, x: {"ok": True})
    c.memory = ns(remember=lambda t: mem.append(t) or True,
                  recall=lambda q="": [m for m in mem if not q or q in m],
                  list=lambda n=10: mem[-n:])
    c.brain = ns(think=lambda q, long=False: "мозг думает про " + str(q), thought=lambda: "мысль мозга")
    c.boards = ns(ask=lambda q: "плата ответила", state=lambda: [{"id": "b1", "name": "UNO Q"}],
                  hw=lambda cmds: "\n".join("%s → ok:%s" % (x.strip(), "512" if x.strip().startswith("a ") else "1")
                                             for x in str(cmds).split(";") if x.strip()))
    c.llm = ns(write=lambda p, f="": "LLM: " + str(f)[:40])

    def _graph(expr, x0=-6.0, x1=6.0):
        import imagine
        svg, _l, err = imagine.render_graph({"expr": str(expr), "x0": float(x0), "x1": float(x1)})
        if err:
            raise RuntimeError(err)
        return "График y = %s.\n[КАРТИНКА]%s[/КАРТИНКА]" % (expr, svg)
    c.draw = ns(graph=_graph)
    c.calc = lambda e: "4" if "2+2" in str(e).replace(" ", "") else None
    c.builtin = lambda n, t=None: "builtin " + n
    c._st, c._bv = {}, {"last": ""}
    c.state = c._st
    c.save_state = lambda: None

    def brick(btype, **p):
        var = p.pop("var", "")
        import applab
        pr = applab._param_defaults(btype, p, c._bv)
        ok, o = applab._RUNNERS[btype](pr, {"vars": c._bv, "final": None})
        if not ok:
            raise RuntimeError(o)
        c._bv["last"] = str(o)
        if var:
            c._bv[var] = str(o)
        return str(o)
    c.brick = brick
    c.brick_reset = lambda: c._bv.update({"last": ""})
    c.bricks = lambda: [{"id": "logic.if", "name": "Если", "cat": "logic", "params": []}]
    return c


def verify(it):
    """-> (ok, пояснение). Нет кода — ok. Код: check_code + запуск на заглушке."""
    if not it.get("code"):
        return True, "без кода"
    import pyapps
    err = pyapps.check_code(it["code"])
    if err:
        return False, err
    ns = {"__name__": "demo"}
    try:
        exec(compile(it["code"], "main.py", "exec"), ns)
        c = stub_ctx(it.get("phrase", ""))
        for ph in it.get("pre") or []:
            c.text = ph
            ns["run"](ph, c)
        c.out.clear()
        c.text = it.get("phrase", "")
        ret = ns["run"](it.get("phrase", ""), c)
    except Exception as e:
        return False, "%s: %s" % (type(e).__name__, e)
    res = "\n".join(c.out + ([str(ret)] if ret not in (None, "") else []))
    if it.get("expect") and it["expect"].lower() not in res.lower():
        return False, "ждали «%s», получили «%s»" % (it["expect"], res[:120])
    return True, res[:200]


def status():
    items = load()
    return {"count": len(items), "with_code": sum(1 for x in items if x.get("code")),
            "verified": sum(1 for x in items if x.get("verified"))}
