# -*- coding: utf-8 -*-
"""v3.226 — STAR GATE ПИШЕТ ПРИЛОЖЕНИЯ И КИРПИЧИКИ APP LAB, СВЕРЯЯСЬ С ИНТЕРНЕТОМ.

Порядок «напиши приложение/кирпичик …»:
  1) интернет (если web_on): поиск, чтение до 2 страниц, из страниц берутся
     блоки кода — как НЕДОВЕРЕННЫЕ данные: проверяется синтаксис и опасные вызовы,
     в приложение они не вставляются;
  2) основа — проверенный шаблон: инструкции App Lab (applab_guide) или урок
     программирования (codelessons); нет основы — своя LLM (если обучена), иначе
     честное «не умею, дай пример»;
  3) СВЕРКА с интернетом: какие вызовы библиотек есть в примерах страниц и в моём коде,
     чего нет у меня, что на страницах опасно (requests, subprocess, os.system, eval…);
  4) проверка: синтаксис + запуск на заглушке ctx (без сети) -> запись; живой запуск —
     по «проверь <имя>»;
  5) приложение сохраняется ВЫКЛЮЧЕННЫМ (включает владелец), с контекстом (по нему
     Star Gate понимает, когда его звать), адресами источников и пометкой автора;
  6) каждое событие пишется в журнал — LLM наблюдает и учится (admincorpus).
Честно: свободно писать новое по незнакомой задаче этот модуль не умеет — только
типовое по шаблонам; страницы читаются как есть, понимание их смысла — задача LLM."""
import ast
import re
import threading
import time

import store

_LOCK = threading.RLock()
_OBS = "applab_observed.json"
_DANGER = ("subprocess", "os.system", "eval(", "exec(", "__import__", "shutil.rmtree", "os.remove", "socket", "ctypes")
_BASE_TRIG = []
_BASE_NOTE = []
_BASE_BRICK = {}                  # триггеры основы (если инструкция их задаёт)
_GENERIC = {"написать", "напиши", "создай", "сделай", "приложение", "функцию", "функция", "кирпичик", "скрипт", "апп", "лаб", "app", "lab"}


# ------------------------------ журнал -------------------------------------
def observe(kind, data):
    """Событие для LLM-наблюдателя (что мозг написал/проверил/вызвал)."""
    try:
        with _LOCK:
            lst = store.load_json(_OBS, []) or []
            lst.append({"ts": time.time(), "kind": kind, "data": data})
            store.save_json(_OBS, lst[-500:])
    except Exception:
        pass


def learned_texts():
    """Материал потока обучения своей LLM: что и как Star Gate писал в App Lab."""
    out = []
    for e in (store.load_json(_OBS, []) or [])[-300:]:
        d = e.get("data") or {}
        k = e.get("kind")
        if k == "write":
            t = "Star Gate написал приложение App Lab «%s»: %s\nОснова: %s. Интернет: %s\nПроверка: %s" % (
                d.get("name"), d.get("desc"), d.get("base"), d.get("refs_note"), d.get("check"))
            if d.get("code"):
                t += "\n# task: App Lab: %s\n%s" % (str(d.get("desc"))[:100], d["code"])
            out.append(t)
        elif k == "context_call":
            out.append("Фраза «%s» по смыслу вызвала приложение «%s» (совпадение %s)." % (d.get("text"), d.get("app"), d.get("score")))
        elif k == "learn":
            out.append("Владелец включил приложение «%s» — Star Gate запомнил его как шаблон." % d.get("name"))
        elif k in ("check", "run"):
            out.append("Приложение «%s»: %s на «%s» -> %s" % (d.get("app"), k, d.get("phrase"), str(d.get("result"))[:200]))
    return out


def fix_texts():
    return ["Задача App Lab: %s\nПравка владельца (было → стало):\n%s" % (x.get("desc"), x.get("diff"))
            for x in (store.load_json(_FIXES, []) or [])[-150:]]


def observed(n=30):
    return (store.load_json(_OBS, []) or [])[-n:]


# ------------------------- обучение на ваших приложениях -------------------
_LEARN = "applab_learned.json"
_FIXES = "applab_corrections.json"


def learn_app(aid, why="включено владельцем"):
    """Включённое владельцем приложение -> выученный шаблон (по нему мозг соберёт похожее);
    его функции b_* с docstring «Кирпичик: …» сами попадают в библиотеку кирпичиков."""
    import pyapps
    g = pyapps.get(aid, with_code=True)
    if not g or not g.get("desc") or pyapps.check_code(g.get("code") or ""):
        return False
    with _LOCK:
        lst = [x for x in (store.load_json(_LEARN, []) or []) if x.get("id") != aid]
        lst.append({"id": aid, "name": g.get("name"), "desc": g.get("desc"), "triggers": g.get("triggers") or [],
                    "ask": [g.get("desc"), g.get("name")] + list(g.get("examples") or []), "code": g["code"],
                    "ts": time.time(), "why": why})
        store.save_json(_LEARN, lst[-300:])
    observe("learn", {"app": aid, "name": g.get("name"), "why": why})
    return True


def learn_fix(aid, desc, before, after):
    """Владелец поправил код приложения мозга -> урок «было → стало» (LLM и база знаний)."""
    import difflib
    diff = "".join(difflib.unified_diff(before.splitlines(True), after.splitlines(True), "было", "стало", n=1))[:6000]
    if not diff.strip():
        return False
    with _LOCK:
        lst = store.load_json(_FIXES, []) or []
        lst.append({"app": aid, "desc": desc, "diff": diff, "ts": time.time()})
        store.save_json(_FIXES, lst[-200:])
    try:
        import kb
        kb.add_article("Правка владельца: App Lab/%s" % aid, "Задача: %s\nЧто поправил владелец:\n```diff\n%s```" % (desc, diff),
                       source="App Lab: правки владельца", did="applab_fix_%s_%d" % (re.sub(r"\W", "_", aid)[:30], int(time.time())))
    except Exception:
        pass
    observe("fix", {"app": aid, "desc": desc})
    return True


def learned_match(desc):
    """Выученный шаблон под задачу -> (оценка, запись) | None."""
    import codelessons
    q = codelessons._stems(desc)
    if not q:
        return None
    best = None
    for it in store.load_json(_LEARN, []) or []:
        a = set()
        for s in it.get("ask") or []:
            a |= codelessons._stems(s)
        hit = q & a
        sc = len(hit) / float(len(q))
        if len(hit) >= 2 and sc >= 0.6 and (not best or sc > best[0]):
            best = (round(sc, 2), it)
    return best


# --------------------------- интернет и сверка -----------------------------
def _calls(code):
    """Имена вызовов dotted-вида (re.findall, json.loads …) + импорты в коде."""
    names, imps = set(), set()
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return names, imps
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            imps |= {a.name.split(".")[0] for a in n.names}
        elif isinstance(n, ast.ImportFrom) and n.module:
            imps.add(n.module.split(".")[0])
        elif isinstance(n, ast.Call):
            f, parts = n.func, []
            while isinstance(f, ast.Attribute):
                parts.append(f.attr)
                f = f.value
            if isinstance(f, ast.Name):
                parts.append(f.id)
                names.add(".".join(reversed(parts)))
    return names, imps


def research(desc, limit=2):
    """Интернет: [{url,title,compiles,danger,calls,imports}] + текст-примечание."""
    if not store.settings().get("web_on", True):
        return [], "интернет выключен в настройках — сверки с сетью нет"
    try:
        import project_web
        import project_brain
        res = project_web.search(re.sub(r"\s+", " ", desc)[:140] + " python пример", limit=3) or []
    except Exception as e:
        return [], "поиск в интернете не удался: %s" % str(e)[:100]
    refs = []
    for r in res[:3]:
        if len(refs) >= limit:
            break
        url = str(r.get("url") or "")
        if not url:
            continue
        ok, page = project_web.fetch_page(url, timeout=12, max_bytes=300000)
        if not ok:
            continue
        blocks = []
        try:
            blocks = [b for b in project_brain._code_blocks(page)][:2]
        except Exception:
            pass
        info = {"url": page.get("url", url), "title": str(page.get("title") or r.get("title") or "")[:100],
                "compiles": False, "danger": [], "calls": [], "imports": []}
        for blk in blocks:
            code = blk if isinstance(blk, str) else str(blk.get("code") if isinstance(blk, dict) else blk)
            try:
                compile(code, "page", "exec")
            except SyntaxError:
                continue
            info["compiles"] = True
            calls, imps = _calls(code)
            info["calls"] = sorted(calls)[:30]
            info["imports"] = sorted(imps)
            info["danger"] = [d for d in _DANGER if d in code] + (["requests"] if "requests" in imps else [])
            break
        refs.append(info)
    note = ("прочитала %d стр.: %s" % (len(refs), "; ".join(x["url"] for x in refs))) if refs else "страниц с кодом не нашла"
    return refs, note


def compare(code, refs):
    """Сверка моего кода со страницами: общее, чего нет у меня, опасное на страницах."""
    mine, my_imp = _calls(code)
    theirs, their_imp = set(), set()
    danger = set()
    for r in refs:
        theirs |= set(r.get("calls") or [])
        their_imp |= set(r.get("imports") or [])
        danger |= set(r.get("danger") or [])
    common = sorted(mine & theirs)
    missing = sorted(x for x in theirs - mine if "." in x)[:8]
    out = {"common": common[:10], "only_theirs": missing, "my_imports": sorted(my_imp),
           "danger_on_pages": sorted(danger), "pages": len(refs)}
    out["verdict"] = ("совпадающих вызовов: %d; на страницах есть то, чего нет у меня: %s; опасное на страницах (не беру): %s"
                      % (len(common), ", ".join(missing) or "—", ", ".join(sorted(danger)) or "—")) if refs \
        else "сверить не с чем — страниц с кодом нет"
    return out


# ------------------------------ написание ----------------------------------
def _slug_words(desc):
    ws = [w for w in re.findall(r"[a-zа-яё]{4,}", desc.lower()) if w not in _GENERIC]
    return ws


def _triggers(desc):
    ws = _slug_words(desc)
    out = []
    for w in ws:
        stem = w[:-1] if len(w) > 6 else w            # «погоду» -> «погод» ловит «погода/погоду»
        if stem not in out:
            out.append(stem)
    return out[:3]


def _base(desc, kind):
    """-> (код, откуда) | (None, причина)."""
    del _BASE_TRIG[:]
    del _BASE_NOTE[:]
    _BASE_BRICK.clear()
    import applab_guide
    import codelessons
    import pyapps
    lm = learned_match(desc)
    if lm:
        sc, it = lm
        _BASE_TRIG[:] = list(it.get("triggers") or [])
        return "# Основа: ваше приложение «%s» (выучено, совпадение %.2f)\n%s" % (it["name"], sc, it["code"]), \
            "выученное приложение «%s»" % it["name"]
    if kind == "app":
        for sc, it in applab_guide.match(desc, 3):
            if it.get("code") and it.get("verified"):
                code = it["code"]
                _BASE_TRIG[:] = list(it.get("triggers") or [])
                return "# Основа: инструкция App Lab «%s» (%.2f)\n%s" % (it["title"], sc, code), "инструкция App Lab «%s»" % it["title"]
    if kind == "app":
        try:                                   # v3.230: сборка из кирпичиков
            import brickplan
            c = brickplan.compose(desc, desc)
            if c.get("ok"):
                _BASE_TRIG[:] = []
                _BASE_NOTE[:] = ["цепочка: " + " → ".join(brickplan.by_id(b)["title"] for b in c["chain"])] + \
                    (["не подошли: " + "; ".join(c["skipped"])] if c["skipped"] else [])
                return c["code"], "кирпичики (%s)" % " → ".join(c["chain"])
            t = brickplan.match_template(desc)        # v3.234: проверенный автошаблон-цепочка
            if t:
                c = brickplan.compose(desc, desc, chain=t[1]["chain"])
                if c.get("ok"):
                    _BASE_NOTE[:] = ["автошаблон: " + t[1]["title"]]
                    return c["code"], "автошаблон (%s)" % " → ".join(t[1]["chain"])
        except Exception as e:
            store.log("APP LAB", "сборка из кирпичиков: %s" % e)
    m = codelessons.match(desc, 1)
    if m:
        sc, it = m[0]
        ok, out = codelessons.verify(it)
        if ok:
            code = str(it.get("fixcode") or it["code"])
            if kind == "brick":
                code = _as_brick(it, code)
                fm = re.search(r"^def\s+b_([a-z][a-z0-9_]{1,30})\s*\(", code, re.M)
                if fm:                          # v3.234: кирпичик мозга — сразу в библиотеку цепочек
                    _BASE_BRICK.update(id=fm.group(1), title=it["title"], stage=2, out="report", **{"in": ["text"]},
                                       words=sorted({w[:6] for w in re.findall(r"[а-яёa-z]{4,}", " ".join(it.get("ask") or []).lower())})[:8],
                                       code=code, test=str(it.get("test") or ""))
            wrapped = pyapps._wrap_run(code)
            if wrapped:
                return "# Основа: урок «%s» (%.2f)\n%s" % (it["title"], sc, wrapped), "урок «%s»" % it["title"]
    return None, "нет типового шаблона и не собралась цепочка кирпичиков под эту задачу"


def _as_brick(it, code):
    """Функция урока -> кирпичик b_<имя>(x, ctx) в формате, который мозг сам берёт в библиотеку после включения."""
    m = re.search(r"^def\s+([A-Za-z_]\w*)\s*\(([^)]*)\)", code, re.M)
    if not m:
        return code
    fn = m.group(1)
    words = ", ".join(sorted({w[:6] for w in re.findall(r"[а-яёa-z]{4,}", " ".join(it.get("ask") or []).lower())})[:6])
    return (code.rstrip() + '\n\n\ndef b_%s(x, ctx):\n    """Кирпичик: %s | этап: 2 | вход: text | выход: report | слова: %s"""\n'
            "    return str(%s(x))\n" % (fn, it["title"], words, fn))


def write(desc, kind="app", name="", internet=True, origin="Star Gate"):
    """-> {ok, id, name, base, refs, compare, check, code, text}. Сохраняется ВЫКЛЮЧЕННЫМ."""
    import applab_guide
    import pyapps
    desc = " ".join(str(desc or "").split())[:300]
    kind = "brick" if kind == "brick" else "app"
    if not desc:
        return {"ok": False, "text": "Опишите, что должно делать приложение."}
    refs, refs_note = research(desc) if internet else ([], "интернет не использовался")
    code, base = _base(desc, kind)
    if not code:
        text = ("Не написала: %s. Свободно писать новое по незнакомой задаче умеет только обученная LLM; "
                "типовые приёмы — по инструкциям App Lab и урокам. %s" % (base, refs_note))
        if refs:
            text += "\nНашла страницы для примера: " + "; ".join(r["url"] for r in refs)
        observe("write_failed", {"desc": desc, "why": base, "refs_note": refs_note})
        return {"ok": False, "text": text, "refs": refs}
    cmp_ = compare(code, refs)
    phrase = desc
    probe = {"code": code, "phrase": phrase}
    ok, why = applab_guide.verify(probe)
    if not ok:
        return {"ok": False, "text": "Основа не прошла проверку на заглушке: %s" % why}
    trig = list(_BASE_TRIG) or _triggers(desc)
    ctx_words = _slug_words(desc)
    short = desc[:60].rsplit(" ", 1)[0] if len(desc) > 60 else desc
    fname = name or ("Кирпичик: " if kind == "brick" else "") + (short[:1].upper() + short[1:]).rstrip(" .,:;")
    res = pyapps.save({"name": fname, "desc": desc, "triggers": trig, "examples": [desc[:100]],
                       "code": code, "icon": "🧱" if kind == "brick" else "🧠", "enabled": False,
                       "context": ctx_words, "web_refs": [{k: r[k] for k in ("url", "title", "compiles", "danger")} for r in refs],
                       "bricks": [m.group(1) for m in re.finditer(r"^def\s+(brick_\w+|(?!run\b)\w+)\s*\(", code, re.M)][:6] if kind == "brick" else [],
                       "written_by": origin}, origin=origin)
    if not res.get("ok"):
        return {"ok": False, "text": "Не сохранила: %s" % res.get("error")}
    aid = res["id"]
    lib_note = ""
    if _BASE_BRICK:
        try:
            import brickplan
            br = brickplan.save_user_brick(dict(_BASE_BRICK), origin=origin)
            lib_note = ("\nКирпичик b_%s добавлен в библиотеку мозга (%s) — теперь он встаёт в цепочки." % (_BASE_BRICK["id"], br.get("test"))
                        if br.get("ok") else "\nВ библиотеку кирпичиков не добавлен: %s." % br.get("error"))
        except Exception as e:
            lib_note = "\nВ библиотеку кирпичиков не добавлен: %s." % e
    observe("write", {"app": aid, "name": fname, "desc": desc, "base": base, "refs_note": refs_note,
                      "check": "заглушка ок: %s" % why[:80], "code": code[:2500], "compare": cmp_["verdict"]})
    text = ("Написала %s «%s» (id %s), основа — %s.%s\nИнтернет: %s.\nСверка: %s.\n"
            "Проверка на заглушке ctx (без сети) прошла: «%s».\n"
            "Приложение ВЫКЛЮЧЕНО — прочитайте код в App Lab и включите. Проверить вживую: «проверь %s». "
            "Триггеры: %s." % ("кирпичик" if kind == "brick" else "приложение", fname, aid, base,
                               ("\n" + "\n".join(_BASE_NOTE)) if _BASE_NOTE else "", refs_note,
                               cmp_["verdict"], why[:100], aid, ", ".join(trig) or "нет (звать по имени или контексту)"))
    text += lib_note
    return {"ok": True, "id": aid, "name": fname, "base": base, "refs": refs, "compare": cmp_, "code": code, "text": text}


def write_chain(chain, title, origin="Star Gate (шаблон)"):
    """Приложение App Lab из готовой цепочки кирпичиков (автошаблона) — выключенным."""
    import brickplan
    import pyapps
    c = brickplan.compose(title, title, chain=chain)
    if not c.get("ok"):
        return {"ok": False, "error": c.get("why")}
    words = []
    for cid in chain:
        words += [w for w in brickplan.by_id(cid)["words"] if w not in words]
    res = pyapps.save({"name": title[:60], "desc": "Цепочка кирпичиков: " + title, "triggers": [], "examples": [],
                       "code": c["code"], "icon": "🔗", "enabled": False, "context": words[:20], "written_by": origin}, origin=origin)
    if not res.get("ok"):
        return {"ok": False, "error": res.get("error")}
    observe("write", {"app": res["id"], "name": title, "desc": title, "base": "автошаблон", "refs_note": "—",
                      "check": "шаблон проверен на образце", "code": c["code"][:2500]})
    return {"ok": True, "id": res["id"], "text": "Приложение «%s» (id %s) собрано из шаблона и добавлено в App Lab выключенным." % (title[:60], res["id"])}


# ------------------------- проверка, запуск, список ------------------------
def _find(name, exact=False):
    """Приложение по id или имени. exact=True (удаление, включение) — только точное совпадение,
    без «похожего»: чужое приложение не должно пострадать."""
    import pyapps
    n = str(name or "").lower().strip(" «»\"'.")
    if not n:
        return None
    for aid, m in pyapps._all().items():
        if n == aid or n == str(m.get("name") or "").lower().strip(" .") or n == str(m.get("name") or "").lower():
            return aid, m
    if exact:
        return None
    best = None
    for aid, m in pyapps._all().items():
        for nm in (aid, str(m.get("name") or "").lower()):
            if len(nm) >= 3 and (nm in n or n in nm) and (not best or len(nm) > best[0]):
                best = (len(nm), aid, m)
    return (best[1], best[2]) if best else None


def check(name, phrase=""):
    """Живая проверка (в т.ч. выключенного приложения): запуск run() на фразе с настоящим ctx."""
    import pyapps
    hit = _find(name)
    if not hit:
        return {"ok": False, "text": "Нет приложения «%s». Список: «приложения»." % name}
    aid, m = hit
    ph = phrase or (m.get("examples") or [m.get("desc") or aid])[0]
    g = pyapps.get(aid, with_code=True)
    err = pyapps.check_code(g.get("code") or "")
    if err:
        return {"ok": False, "text": "Код не проходит проверку: %s" % err}
    r = pyapps.run_code(aid, g["code"], ph, timeout=40)
    observe("check", {"app": aid, "phrase": ph, "result": r.get("result") or r.get("error")})
    if r.get("ok"):
        body = r.get("result") or "(пусто)"
        body = body[:60000] if "[КАРТИНКА]" in body else body[:1500]      # картинку не режем — она станет битой
        return {"ok": True, "id": aid, "text": "Проверка «%s» на фразе «%s»: ✅ ответ за %d мс:\n%s%s" % (
            m.get("name"), ph, r.get("ms", 0), body,
            "" if m.get("enabled") else "\n(приложение выключено — включите в App Lab, чтобы чат его звал)")}
    return {"ok": False, "id": aid, "text": "Проверка «%s»: ❌ %s" % (m.get("name"), r.get("error"))}


def run_app(name, phrase=""):
    import pyapps
    hit = _find(name)
    if not hit:
        return {"ok": False, "text": "Нет приложения «%s»." % name}
    aid, m = hit
    if not m.get("enabled"):
        return {"ok": False, "text": "Приложение «%s» выключено. «проверь %s» его запустит для проверки; "
                "включает владелец в App Lab." % (m.get("name"), aid)}
    r = pyapps.run(aid, phrase or m.get("name"), board="чат")
    observe("run", {"app": aid, "phrase": phrase, "result": r.get("result") or r.get("error")})
    body = r.get("result") or r.get("error") or "(пусто)"
    return {"ok": bool(r.get("ok")), "id": aid, "text": body[:60000] if "[КАРТИНКА]" in body else body[:3000]}


def listing():
    import pyapps
    ls = pyapps.apps()
    if not ls:
        return "Приложений App Lab пока нет. Скажите: «напиши приложение …»."
    return "Приложения App Lab:\n" + "\n".join("%s %s (%s) — %s%s" % (
        "✅" if m.get("enabled") else "⏸", m.get("name"), m["id"], str(m.get("desc") or "")[:60],
        "  [Star Gate]" if str(m.get("origin") or "").startswith("Star Gate") else "") for m in ls[:30])


_WRITE = re.compile(r"^\s*(?:пожалуйста[, ]+)?(?:напиши|создай|сделай|придумай|добавь|сочини)\s+(?:мне\s+)?(?:новый\s+|новую\s+)?"
                    r"(приложени\w+|функци\w+|кирпичик\w*|скрипт\w*)(?:\s+(?:для\s+)?app\s*lab|\s+в\s+app\s*lab|\s+в\s+апп\s*лаб)?\s*[:,\-—]?\s*(.*)$", re.I | re.S)
_CHECK = re.compile(r"^\s*(?:проверь|протестируй|прогони)\s+(?:приложение\s+|функцию\s+|кирпичик\s+)?(.+?)(?:\s+на\s+фразе\s+(.+))?\s*$", re.I | re.S)
_DEL = re.compile(r"^\s*(?:удали|удалить|убери)\s+(?:приложение\s+|функцию\s+|кирпичик\s+|навык\s+)?(.+?)(?:\s+из\s+app\s*lab)?\s*$", re.I)
_REN = re.compile(r"^\s*переименуй\s+(?:приложение\s+|функцию\s+|кирпичик\s+|навык\s+)?[«\"']?(.+?)[»\"']?\s+в\s+[«\"']?(.+?)[»\"']?\s*$", re.I)
_FLAG = re.compile(r"^\s*(включи|выключи|отключи)\s+(?:приложение\s+|функцию\s+|кирпичик\s+|навык\s+)?(.+?)\s*$", re.I)
_LIST = re.compile(r"^\s*(?:покажи\s+|какие\s+|список\s+)?(?:мои\s+)?(?:приложения|функции|кирпичики)(?:\s+app\s*lab)?\??\s*$", re.I)


_TPL_Q = re.compile(r"^\s*(?:покажи\s+|какие\s+)?шаблоны(?:\s+кирпичиков|\s+цепочек)?\s*\??\s*$", re.I)
_TPL_GEN = re.compile(r"^\s*(?:сгенерируй|обнови|создай)\s+шаблоны", re.I)
_BRICKS_Q = re.compile(r"^\s*(?:какие\s+|покажи\s+|список\s+)?кирпичики(?:\s+мозга)?\s*\??\s*$", re.I)
_PLAN_Q = re.compile(r"^\s*(?:план|из\s+чего\s+соберёшь|из\s+чего\s+соберешь)\s*[:\-]?\s+(.+)$", re.I | re.S)


def handle(text, origin="владелец"):
    """Разбор просьбы про App Lab -> (ответ, info) | None (не про App Lab)."""
    t = str(text or "").strip()
    import brickplan
    if _BRICKS_Q.match(t):
        lib = brickplan.library()
        return ("Кирпичики мозга-12 (%d, из них выучено из ваших приложений: %d):\n" % (len(lib), len(lib) - len(brickplan.B)) +
                "\n".join("• b_%s — %s (этап %g, %s → %s)%s" % (b["id"], b["title"], b["stage"], "/".join(b["in"]), b["out"],
                                                              "" if b["origin"] == "встроенный" else " [%s]" % b["origin"]) for b in lib)), {"applab": "bricks"}
    if _TPL_GEN.match(t):
        r = brickplan.autogen()
        return "Сгенерировала шаблоны: проверено цепочек %d, рабочих %d. «шаблоны» — список." % (r["tried"], r["made"]), {"applab": "templates"}
    if _TPL_Q.match(t):
        ts = brickplan.templates()
        if not ts:
            return "Шаблонов пока нет — «сгенерируй шаблоны».", {"applab": "templates"}
        return ("Шаблоны-цепочки (%d, проверены на образцах):\n" % len(ts) +
                "\n".join("• %s%s" % (x["title"], " 🔌" if x.get("hw") else "") for x in ts[:40]) +
                ("\n…и ещё %d" % (len(ts) - 40) if len(ts) > 40 else "")), {"applab": "templates"}
    m = _PLAN_Q.match(t)
    if m:
        p = brickplan.plan(m.group(1))
        if not p:
            return "Из моих кирпичиков под «%s» цепочка не собирается." % m.group(1)[:100], {"applab": "plan", "ok": False}
        return ("План для «%s»:\n%s%s" % (m.group(1)[:100], "\n".join("%d. %s" % (i + 1, w) for i, w in enumerate(p["why"])),
                                         ("\nНе подошли: " + "; ".join(p["skipped"])) if p["skipped"] else "")), {"applab": "plan", "ok": True}
    if _LIST.match(t):
        return listing(), {"applab": "list"}
    m = _WRITE.match(t)
    if m and m.group(2).strip():
        kind = "brick" if m.group(1).lower().startswith("кирпич") else "app"
        r = write(m.group(2), kind=kind, origin="Star Gate" if origin != "плата" else "плата→Star Gate")
        return r["text"], {"applab": "write", "id": r.get("id"), "ok": r.get("ok")}
    m = _DEL.match(t)
    if m and not _find(m.group(1), exact=True) and re.match(r"^\s*(?:удали|удалить|убери)\s+(?:приложение|функцию|кирпичик|навык)\s", t, re.I):
        return ("Нет приложения «%s» — удалять можно только по точному id или имени. %s" % (m.group(1), listing())), {"applab": "delete", "ok": False}
    if m and _find(m.group(1), exact=True):
        import pyapps
        aid, mm = _find(m.group(1), exact=True)
        pyapps.delete(aid)
        observe("delete", {"app": aid, "name": mm.get("name")})
        return "Приложение «%s» (%s) удалено из App Lab." % (mm.get("name"), aid), {"applab": "delete", "id": aid, "ok": True}
    m = _REN.match(t)
    if m and _find(m.group(1), exact=True):
        import pyapps
        aid, mm = _find(m.group(1), exact=True)
        r = pyapps.rename(aid, m.group(2))
        return ("Переименовала: «%s» → «%s» (id %s не изменился)." % (mm.get("name"), m.group(2), aid) if r.get("ok")
                else "Не переименовала: %s" % r.get("error")), {"applab": "rename", "id": aid, "ok": r.get("ok")}
    m = _FLAG.match(t)
    if m and _find(m.group(2), exact=True):
        import pyapps
        aid, mm = _find(m.group(2), exact=True)
        on = m.group(1).lower() == "включи"
        pyapps.set_flags(aid, enabled=on)
        observe("flag", {"app": aid, "enabled": on})
        return "Приложение «%s» %s." % (mm.get("name"), "включено — чат теперь его зовёт" if on else "выключено"), {"applab": "flag", "id": aid, "ok": True}
    m = _CHECK.match(t)
    if m:
        hit = _find(m.group(1))
        if hit:
            r = check(m.group(1), m.group(2) or "")
            return r["text"], {"applab": "check", "id": r.get("id"), "ok": r.get("ok")}
    try:
        import pyapps
        nm = pyapps.named(t)
        if nm and nm[1].get("enabled"):
            r = run_app(nm[0], t)
            return r["text"], {"applab": "run", "id": nm[0], "ok": r.get("ok")}
    except Exception:
        pass
    return None


def potential():
    """Что Star Gate умеет с App Lab — для ответа «потенциал»."""
    import applab_guide
    import codelessons
    return {"guides": applab_guide.status(), "lessons": codelessons.status(),
            "observed": len(store.load_json(_OBS, []) or [])}
