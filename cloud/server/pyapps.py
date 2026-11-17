# -*- coding: utf-8 -*-
"""v3.170 — APP LAB НА PYTHON (навыки упразднены).

Заявка владельца: «навыки упразднить, навыки App Lab отображать в окне чата
вместо типичных вопросов (чтобы можно было дописать навык, например для
покупки в интернете); все навыки и саму модель написания навыков применить
к App Lab; инструкция для ИИ, как написать для себя App Lab; пусть там всё
программируется через Python».

ПРИЛОЖЕНИЕ (как в Arduino App Lab — папка с описанием и python/main.py):
    data/applab/<id>/app.json        имя, значок, описание, фразы-триггеры,
                                     примеры, «в окне чата», включено, автор
    data/applab/<id>/python/main.py  def run(text, ctx): ... -> str
    data/applab/<id>/state.json      память приложения (ctx.state)
ЗАПУСК: фраза в чате совпала с триггером включённого приложения -> run()
в отдельном потоке с таймаутом; ответ — то, что вернул run() (и ctx.say()).
ctx — настоящие функции облака (см. API_DOC и APP_GUIDE ниже): интернет,
Википедия, база знаний, память, мозг облака, плата neuro-12, своя LLM,
калькулятор, Confluence, кирпичики старого App Lab (ctx.brick).
МИГРАЦИЯ при старте: реестр навыков (skills/registry.json) и приложения из
кирпичиков (applab.json) превращаются в Python-приложения; старые файлы
остаются как *.migrated (ничего не теряется).
Безопасность: App Lab — только владелец в панели. Приложение, присланное
платой (POST /api/skills), сохраняется ВЫКЛЮЧЕННЫМ — его включает владелец.
"""
import json
import os
import re
import shutil
import threading
import time
import traceback

import store

ROOT = "applab"
TIMEOUT = 60.0
_LOCK = threading.RLock()
_ID_RE = re.compile(r"^[a-z0-9_-]{2,40}$")
_CACHE = {"mt": None, "apps": None}


# ============================== хранилище ==================================
def _dir(aid=""):
    return store.p(ROOT, aid) if aid else store.p(ROOT)


def _code_path(aid):
    return os.path.join(_dir(aid), "python", "main.py")


def _meta_default(aid):
    return {"id": aid, "name": aid, "icon": "🧩", "desc": "", "triggers": [],
            "examples": [], "chat": True, "enabled": True, "origin": "владелец",
            "ver": 1, "runs": 0, "ts": time.time(), "last_ok": None, "last_err": ""}


def _stamp():
    try:
        return max([os.stat(os.path.join(_dir(), d, "app.json")).st_mtime_ns
                    for d in os.listdir(_dir())
                    if os.path.exists(os.path.join(_dir(), d, "app.json"))] or [0]) + \
            len(os.listdir(_dir()))
    except OSError:
        return -1


def _all():
    with _LOCK:
        st = _stamp()
        if _CACHE["apps"] is not None and _CACHE["mt"] == st:
            return _CACHE["apps"]
        out = {}
        try:
            names = os.listdir(_dir())
        except OSError:
            names = []
        for d in names:
            fn = os.path.join(_dir(), d, "app.json")
            if not os.path.isfile(fn):
                continue
            try:
                with open(fn, encoding="utf-8") as f:
                    m = json.load(f)
                if isinstance(m, dict):
                    md = _meta_default(d)
                    md.update(m)
                    md["id"] = d
                    out[d] = md
            except Exception:
                continue
        _CACHE["apps"], _CACHE["mt"] = out, st
        return out


def _write_meta(m):
    os.makedirs(os.path.join(_dir(m["id"]), "python"), exist_ok=True)
    fn = os.path.join(_dir(m["id"]), "app.json")
    with open(fn + ".tmp", "w", encoding="utf-8") as f:
        json.dump(m, f, ensure_ascii=False, indent=1)
    os.replace(fn + ".tmp", fn)
    _CACHE["apps"] = None


def get(aid, with_code=False):
    m = _all().get(str(aid))
    if not m:
        return None
    m = dict(m)
    if with_code:
        try:
            with open(_code_path(aid), encoding="utf-8") as f:
                m["code"] = f.read()
        except OSError:
            m["code"] = ""
    return m


def apps(chat_only=False):
    out = [dict(m) for m in _all().values()
           if not chat_only or (m.get("chat") and m.get("enabled"))]
    out.sort(key=lambda m: (not m.get("enabled"), -float(m.get("runs") or 0),
                            m.get("name", "")))
    return out


def _slug(name):
    tr = dict(zip("абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
                  "a b v g d e e zh z i y k l m n o p r s t u f h c ch sh sch _ y _ e yu ya".split()))
    s = "".join(tr.get(c, c) for c in str(name or "").lower())
    s = re.sub(r"[^a-z0-9]+", "_", s).strip("_")[:30]
    return s or "app"


def save(b, origin="владелец"):
    """Создать/изменить приложение. b: id?, name, icon, desc, triggers,
    examples, chat, enabled, code. Код проверяется: синтаксис и run()."""
    code = str(b.get("code") or "")
    err = check_code(code)
    if err:
        return {"ok": False, "error": err}
    aid = str(b.get("id") or "").strip().lower()
    if aid and not _ID_RE.match(aid):
        return {"ok": False, "error": "id: латиница, цифры, _ и -"}
    with _LOCK:
        if not aid:
            base = _slug(b.get("name"))
            aid, n = base, 2
            while aid in _all():
                aid = "%s_%d" % (base, n)
                n += 1
        exists = aid in _all()
        prev = _all().get(aid) or dict(_meta_default(aid), ver=0, origin=origin)
        trig = b.get("triggers")
        if isinstance(trig, str):
            trig = [t for t in re.split(r"[,\n;]", trig)]
        ex = b.get("examples")
        if isinstance(ex, str):
            ex = [t for t in re.split(r"[\n;]", ex)]
        m = dict(prev)
        m.update({
            "id": aid,
            "name": str(b.get("name") or prev.get("name") or aid).strip()[:60],
            "icon": str(b.get("icon") or prev.get("icon") or "🧩").strip()[:4],
            "desc": str(b.get("desc") if b.get("desc") is not None else prev.get("desc", "")).strip()[:400],
            "triggers": [str(t).strip().lower()[:60] for t in (trig if trig is not None else prev.get("triggers", [])) if str(t).strip()][:20],
            "examples": [str(t).strip()[:120] for t in (ex if ex is not None else prev.get("examples", [])) if str(t).strip()][:6],
            "chat": bool(b.get("chat", prev.get("chat", True))),
            "enabled": bool(b.get("enabled", prev.get("enabled", True))),
            "origin": str(b.get("origin") or (prev.get("origin") if exists else origin) or origin)[:30],
            "ver": int(prev.get("ver") or 0) + 1, "ts": time.time()})
        _write_meta(m)
        with open(_code_path(aid), "w", encoding="utf-8") as f:
            f.write(code.rstrip() + "\n")
    store.log("APP LAB", "сохранено приложение «%s» (%s, v%d, автор: %s)" % (
        m["name"], aid, m["ver"], m["origin"]))
    _refresh_guide_later()
    return {"ok": True, "id": aid, "app": get(aid)}


def delete(aid):
    with _LOCK:
        d = _dir(str(aid))
        if not os.path.isdir(d) or not _ID_RE.match(str(aid)):
            return {"ok": False, "error": "нет такого приложения"}
        shutil.rmtree(d, ignore_errors=True)
        _CACHE["apps"] = None
    store.log("APP LAB", "удалено приложение %s" % aid)
    _refresh_guide_later()
    return {"ok": True}


def set_flags(aid, **kw):
    with _LOCK:
        m = _all().get(str(aid))
        if not m:
            return {"ok": False, "error": "нет такого приложения"}
        m = dict(m)
        for k in ("enabled", "chat"):
            if k in kw:
                m[k] = bool(kw[k])
        _write_meta(m)
    _refresh_guide_later()
    return {"ok": True, "app": get(aid)}


def check_code(code):
    if not code.strip():
        return "пустой код"
    if len(code) > 60000:
        return "код длиннее 60 000 знаков"
    try:
        compile(code, "main.py", "exec")
    except SyntaxError as e:
        return "синтаксическая ошибка, строка %s: %s" % (e.lineno, e.msg)
    if not re.search(r"^def\s+run\s*\(", code, re.M):
        return "нет функции def run(text, ctx)"
    return ""


# ================================ ctx ======================================
class _NS:
    pass


class AppCtx:
    """То, что получает run(text, ctx). Всё — настоящие функции облака."""

    def __init__(self, aid, text, board=""):
        self.app = aid
        self.text = text
        self.board = board
        self.out = []
        self.log_lines = []
        self.web, self.kb, self.memory, self.brain, self.boards, self.llm, \
            self.docs = _NS(), _NS(), _NS(), _NS(), _NS(), _NS(), _NS()
        self.web.search = self._web_search
        self.web.page = self._web_page
        self.web.wiki = self._wiki
        self.kb.search = self._kb_search
        self.kb.add = self._kb_add
        self.memory.remember = self._remember
        self.memory.recall = self._recall
        self.memory.list = lambda n=10: [x.get("text") for x in store.notes()[-n:]]
        self.brain.think = self._think
        self.brain.thought = self._brain_thought
        self.boards.ask = self._board_ask
        self.boards.state = self._board_state
        self.llm.write = self._llm_write
        self.docs.search = self._docs_search
        self.docs.page = self._docs_page

    # --- вывод
    def say(self, *parts):
        self.out.append(" ".join(str(p) for p in parts))

    def log(self, *parts):
        self.log_lines.append(" ".join(str(p) for p in parts)[:300])

    # --- интернет
    def _web_search(self, q, n=5):
        import webfetch
        return [dict(r) for r in (webfetch.top_pages(str(q), int(n)) or [])][:int(n)]

    def _web_page(self, url, chars=6000):
        import webfetch
        ok, page = webfetch.fetch_page(str(url))
        if not ok:
            raise RuntimeError(str(page))
        return {"url": page.get("url"), "title": page.get("title"),
                "text": str(page.get("text") or "")[:int(chars)]}

    def _wiki(self, topic):
        import readlike
        art, why = readlike.fetch_article(str(topic))
        if not art:
            return None
        return {"title": art["title"], "text": art["text"], "src": art["src"]}

    def _docs_search(self, q, n=5):
        import sources                     # v3.174: все источники по ссылкам
        return sources.page_search(str(q), int(n))

    def _docs_page(self, q):
        import sources
        t, text, src = sources.page_read(str(q))
        return {"title": t, "text": text, "src": src} if t else None

    # --- знания и память
    def _kb_search(self, q, n=3):
        import kb
        return [{"title": h.get("title"), "snippet": h.get("snippet"),
                 "score": h.get("score")}
                for h in (kb.search(str(q), limit=int(n)) or {}).get("results") or []]

    def _kb_add(self, title, text):
        import kb
        return kb.add_article(str(title)[:160], str(text), source="App Lab: " + self.app)

    def _remember(self, text):
        store.notes_add(str(text)[:500], board="App Lab: " + self.app)
        return True

    def _recall(self, q=""):
        q = str(q or "").lower()
        return [n.get("text") for n in store.notes()
                if not q or q in str(n.get("text", "")).lower()][-10:]

    # --- мозг, платы, LLM
    def _think(self, q, long=False):
        import brain
        r, _z, _i = brain.think(str(q), chat_id="applab", sender="App Lab",
                                long_mode=bool(long), route="cloud")
        return str(r or "")

    def _brain_thought(self):
        try:
            import brainctl
            th = brainctl.current_thought("")
            return th["text"] if th else ""
        except Exception:
            return ""

    def _board_ask(self, q):
        return self.brick("board.ask", question=str(q))

    def _board_state(self):
        return [{"id": b.get("id"), "name": b.get("name")} for b in (store.free_boards() or [])]

    def _llm_write(self, prompt, facts=""):
        import brainctl
        r = brainctl.llm_from_thought(str(prompt), str(prompt), str(facts))
        return r.get("text") if r.get("ok") else ""

    # --- прочее
    def calc(self, expr):
        import calc
        a = calc.answer(str(expr))
        return a[0] if a else None

    def builtin(self, name, text=None):
        """Старые встроенные навыки (math, time, wiki, memory, status,
        llm_extend) — как функции; удобно для своих приложений."""
        import skills
        r = skills.run_builtin(str(name), self.text if text is None else str(text),
                               self.board) or {}
        if not r.get("ok"):
            raise RuntimeError(r.get("error") or "не вышло")
        return str(r.get("result") or "")

    def brick(self, btype, **params):
        """Кирпичик старого App Lab (board.ask, know.kb, math.model, …)."""
        import applab
        fn = applab._RUNNERS.get(str(btype))
        if not fn:
            raise RuntimeError("нет кирпичика %s" % btype)
        pr = applab._param_defaults(str(btype), params, {"last": ""})
        ok, out = fn(pr, {"vars": {"last": ""}, "final": None})
        if not ok:
            raise RuntimeError(str(out))
        return str(out or "")

    @property
    def state(self):
        if not hasattr(self, "_state"):
            self._state = store.load_json(os.path.join(ROOT, self.app, "state.json"), {}) or {}
        return self._state

    def save_state(self):
        store.save_json(os.path.join(ROOT, self.app, "state.json"), getattr(self, "_state", {}))


# ================================ запуск ===================================
def run_code(aid, code, text, board="", timeout=TIMEOUT):
    """Исполнить код приложения. -> {ok, result, log, error, ms}."""
    ctx = AppCtx(aid, text, board)
    box = {}

    def _go():
        try:
            ns = {"__name__": "applab_" + aid}
            exec(compile(code, "%s/python/main.py" % aid, "exec"), ns)
            fn = ns.get("run")
            if not callable(fn):
                raise RuntimeError("нет функции run(text, ctx)")
            box["ret"] = fn(text, ctx)
        except Exception as e:
            tb = traceback.extract_tb(e.__traceback__)
            line = next((f.lineno for f in reversed(tb) if f.filename.endswith("main.py")), None)
            box["err"] = "%s: %s%s" % (type(e).__name__, e,
                                       " (строка %s)" % line if line else "")
    t0 = time.time()
    th = threading.Thread(target=_go, daemon=True, name="app-" + aid)
    th.start()
    th.join(timeout)
    ms = int((time.time() - t0) * 1000)
    if th.is_alive():
        return {"ok": False, "error": "приложение работает дольше %d с — ответа "
                "не дождалась (оно доработает в фоне)" % timeout, "log": ctx.log_lines,
                "result": "\n".join(ctx.out), "ms": ms}
    if "err" in box:
        return {"ok": False, "error": box["err"], "log": ctx.log_lines,
                "result": "\n".join(ctx.out), "ms": ms}
    ret = box.get("ret")
    parts = list(ctx.out) + ([str(ret)] if ret not in (None, "") else [])
    return {"ok": True, "result": "\n".join(parts).strip(), "log": ctx.log_lines, "ms": ms}


def run(aid, text="", board="", code=None):
    m = get(aid, with_code=code is None)
    if not m:
        return {"ok": False, "error": "нет приложения %s" % aid}
    r = run_code(aid, code if code is not None else m.get("code", ""), str(text or ""), board)
    if code is None:
        with _LOCK:
            mm = dict(_all().get(aid) or m)
            mm.pop("code", None)
            mm["runs"] = int(mm.get("runs") or 0) + 1
            mm["last_ok"] = bool(r.get("ok"))
            mm["last_err"] = str(r.get("error") or "")[:200]
            mm["last_ts"] = time.time()
            _write_meta(mm)
    r["app"] = m.get("name")
    return r


def match(text):
    """Включённое приложение, чей триггер есть во фразе. -> (id, meta) | None.
    Триггер: слово/фраза (по границам слов) или «re:<регулярное>»."""
    low = " " + str(text or "").lower().strip() + " "
    best, bs = None, 0
    for aid, m in _all().items():
        if not m.get("enabled"):
            continue
        sc = 0
        for t in m.get("triggers") or []:
            t = str(t).strip()
            if not t:
                continue
            if t.startswith("re:"):
                try:
                    if re.search(t[3:], low, re.I):
                        sc += 12
                except re.error:
                    pass
            elif re.search(r"(^|[^а-яёa-z0-9])" + re.escape(t) + r"([^а-яёa-z0-9]|$)", low):
                sc += len(t) + 2
        if sc > bs:
            best, bs = (aid, m), sc
    return best if bs >= 5 else None


# ============================ ИИ пишет приложение ===========================
def ai_write(desc, name=""):
    """ИИ пишет себе приложение по описанию. 1) локальная модель (если
    задана) по APP_GUIDE, с проверкой запуском и до 2 исправлений;
    2) заготовки самокода (autocode); 3) честное «не смогла»."""
    desc = str(desc or "").strip()[:600]
    if not desc:
        return {"ok": False, "error": "опиши, что должно делать приложение"}
    own = own_llm_write(desc, name)          # v3.171: своя LLM — первой
    if own.get("ok"):
        return own
    own_note = own.get("why") or ""
    try:
        import codewrite
        if codewrite.available():
            msgs = [{"role": "system", "content": APP_GUIDE + "\n\n" + API_DOC +
                     "\n\nВерни ОДИН блок ```python``` — файл python/main.py целиком. "
                     "Первой строкой комментарий: # name: <имя> | triggers: <слова через запятую> "
                     "| example: <пример фразы>."},
                    {"role": "user", "content": "Напиши приложение App Lab: " + desc}]
            last = ""
            for _try in range(3):
                reply = codewrite._call(msgs, timeout=180, max_tokens=1800)
                _l, code, _n = codewrite._extract(reply, "python")
                err = check_code(code) if code else "модель не вернула код"
                ex = desc
                hm = re.search(r"#\s*name:\s*([^|\n]+)\|\s*triggers:\s*([^|\n]+)(?:\|\s*example:\s*([^\n]+))?", code or "")
                if hm and hm.group(3):
                    ex = hm.group(3).strip()
                if not err:
                    r = run_code("draft", code, ex, timeout=40)
                    err = "" if r.get("ok") else r.get("error")
                if not err:
                    trig = [t.strip() for t in (hm.group(2) if hm else "").split(",") if t.strip()]
                    if own_note:
                        store.log("APP LAB", "своя LLM не справилась (%s) — пишет локальная модель" % own_note)
                    return save({"name": (hm.group(1).strip() if hm else name or desc[:40]),
                                 "desc": desc, "triggers": trig or [desc.split()[0].lower()],
                                 "examples": [ex], "code": code, "icon": "🤖"},
                                origin="ИИ (локальная модель)")
                last = err
                msgs += [{"role": "assistant", "content": reply},
                         {"role": "user", "content": "Ошибка при проверке: %s. Исправь и верни файл целиком." % err}]
            return {"ok": False, "error": "локальная модель не написала рабочий код: %s" % last}
    except Exception as e:
        store.log("APP LAB", "ИИ-автор: %s" % e)
    try:
        import autocode
        rep, ok = autocode.write_function(desc)
        if not ok and own_note:
            rep = "Своя LLM: %s. Заготовки: %s" % (own_note, rep)
        return {"ok": bool(ok), "result": rep, "error": "" if ok else rep}
    except Exception as e:
        return {"ok": False, "error": "не смогла: %s" % e}


# ========================= своя LLM в программировании =====================
LLM_TASK = "App Lab: "              # затравка задачи для своей LLM


def _wrap_run(code):
    """Кандидат своей LLM без run(): обернуть первую функцию в run()."""
    if re.search(r"^def\s+run\s*\(", code, re.M):
        return code
    m = re.search(r"^def\s+([A-Za-z_]\w*)\s*\(([^)]*)\)", code, re.M)
    if not m:
        return ""
    args = [a for a in m.group(2).split(",") if a.strip()]
    call = "%s(text)" % m.group(1) if len(args) >= 1 else "%s()" % m.group(1)
    return code.rstrip() + "\n\n\ndef run(text, ctx):\n    return str(%s)\n" % call


def own_llm_write(desc, name=""):
    """СВОЯ LLM пишет приложение: кандидаты кода по затравке
    «# task: App Lab: <описание>» (так она училась на приложениях), каждый
    проверяется запуском; первый рабочий — сохраняется (автор «своя LLM»).
    -> {ok, ...} | {ok: False, why, draft?}."""
    try:
        import codewrite
        if not codewrite.own_ready():
            st = {}
            try:
                import tinyllm
                st = tinyllm.status()
            except Exception:
                pass
            return {"ok": False, "why": "своя LLM ещё не училась коду" if st.get("steps")
                    else "своя LLM ещё не обучена"}
        import tinyllm
        cands = tinyllm.write_code(LLM_TASK + desc, tries=6) or []
    except Exception as e:
        return {"ok": False, "why": "своя LLM: %s" % str(e)[:80]}
    if not cands:
        return {"ok": False, "why": "своя LLM не собрала ни одной законченной функции"}
    draft, why = "", ""
    for i, c in enumerate(cands):
        code = _wrap_run(c)
        err = check_code(code) if code else "нет функции"
        if not err:
            r = run_code("draft", code, desc, timeout=20)
            err = "" if r.get("ok") and r.get("result") else (r.get("error") or "пустой ответ")
        if not err:
            res = save({"name": name or desc[:40], "desc": desc, "examples": [desc[:120]],
                        "triggers": [], "code": "# Написала своя LLM облака (кандидат %d из %d).\n%s"
                                                % (i + 1, len(cands), code),
                        "icon": "🧠", "chat": False}, origin="своя LLM")
            if res.get("ok"):
                res["result"] = ("Своя LLM написала приложение «%s» (кандидат %d из %d) и оно "
                                 "прошло запуск. Триггеры не заданы — проверьте код и допишите "
                                 "их в App Lab." % (res["app"]["name"], i + 1, len(cands)))
            return res
        if not draft:
            draft, why = c, err
    return {"ok": False, "why": "своя LLM написала %d кандидатов, ни один не прошёл запуск (%s)"
            % (len(cands), why[:80]), "draft": draft}


LLM_GUIDE = """Инструкция для своей LLM: как я пишу приложения App Lab.
Я своя LLM облака мозг-12. Я учусь на коде приложений App Lab и пишу новые.
Задачу мне дают затравкой: # task: App Lab: описание приложения.
После затравки я пишу функцию def run(text, ctx): и возвращаю ответ строкой.
text — это фраза из чата. ctx — функции облака.
Чтобы найти в интернете, я пишу hits = ctx.web.search(text, 5).
Чтобы взять статью Википедии, я пишу art = ctx.web.wiki(text).
Чтобы поискать в базе знаний, я пишу ctx.kb.search(text).
Чтобы посчитать, я пишу ctx.calc(выражение).
Чтобы запомнить, я пишу ctx.memory.remember(текст).
Если ничего не нашлось, я честно возвращаю: ничего не нашла.
Я не выдумываю цены, числа и факты: показываю только то, что вернули функции ctx.
Каждый мой код облако проверяет запуском. Код, который не прошёл проверку, не сохраняется.
Владелец читает мой код в App Lab и сам решает, включить ли приложение."""


def _with_doc(code, desc):
    """Docstring «App Lab: <описание>» в run() — так выглядит задача для LLM."""
    if re.search(r'^def\s+run\s*\([^)]*\)\s*:\s*\n\s+(\"\"\"|\'\'\')', code, re.M):
        return code
    doc = (LLM_TASK + " ".join(str(desc or "").split()))[:120].replace('"', "'")
    return re.sub(r"^(def\s+run\s*\([^)]*\)\s*:[^\n]*\n)", lambda m: m.group(1) + '    """%s"""\n' % doc,
                  code, count=1, flags=re.M)


def teach_llm():
    """Своя LLM учится программировать App Lab: код включённых приложений
    — статьями «Исходник: App Lab/<id>.py» (с задачей «App Lab: …»), плюс
    инструкция LLM_GUIDE. Дообучение LLM подхватывает их само."""
    try:
        import kb
    except Exception:
        return 0
    n = 0
    for m in apps():
        if not m.get("enabled") or not m.get("desc"):
            continue
        g = get(m["id"], with_code=True) or {}
        code = _with_doc(g.get("code") or "", m.get("desc"))
        if not code.strip():
            continue
        did = "applab_src_" + re.sub(r"[^a-z0-9_]", "_", m["id"])
        r = kb.add_article("Исходник: App Lab/%s.py" % m["id"], code,
                           source="App Lab: код для учёбы своей LLM", did=did)
        n += 1 if r.get("ok") else 0
    try:
        kb.add_article("Инструкция для своей LLM: приложения App Lab", LLM_GUIDE,
                       source="инструкция облака", did="llm_applab_guide")
    except Exception:
        pass
    return n


# ============================== миграция ===================================
_BUILTIN_CODE = '''# Встроенное приложение «{name}» (бывший навык облака).
# Логика — в облаке (ctx.builtin); здесь можно дописать своё до или после.
def run(text, ctx):
    return ctx.builtin("{sid}", text)
'''


def _from_skill(sid, sk, code_dir):
    kind = sk.get("kind")
    if kind == "builtin":
        code = _BUILTIN_CODE.format(name=sk.get("name", sid), sid=sid)
    elif kind == "code":
        try:
            with open(os.path.join(code_dir, sid + ".py"), encoding="utf-8") as f:
                code = f.read()
        except OSError:
            return None
        code = re.sub(r"def run\(text, ctx=None\)", "def run(text, ctx=None)", code)
    elif kind == "knowledge":
        reply = str(sk.get("reply") or "")
        code = ("# Выученная фраза (бывший навык-знание). {text} — фраза собеседника.\n"
                "def run(text, ctx):\n"
                "    return %r.replace('{text}', text)\n" % reply)
    else:
        return None
    return {"id": re.sub(r"[^a-z0-9_-]", "_", sid.lower())[:40] or "skill",
            "name": sk.get("name") or sid, "desc": sk.get("desc") or "",
            "triggers": sk.get("keywords") or [], "code": code,
            "icon": {"math": "🧮", "time": "🕒", "wiki": "📖", "memory": "📝",
                     "status": "📊", "llm_extend": "💭"}.get(sid, "🧩"),
            "examples": {"math": ["посчитай 12*8+4"], "time": ["который час"],
                         "wiki": ["что такое нейросеть"], "memory": ["запомни купить хлеб"],
                         "status": ["статус"], "llm_extend": ["развей мысль"]}.get(sid, []),
            "chat": kind != "knowledge", "origin": sk.get("origin") or "навык"}


def _from_bricks(a):
    lines = ["# Приложение «%s» перенесено из кирпичиков в Python." % a.get("name", ""),
             "# Каждый кирпичик стал вызовом ctx.brick(...); правьте как обычный код.",
             "def run(text, ctx):",
             "    v = {'last': '', 'question': text}"]
    for b in a.get("bricks") or []:
        params = {k: v for k, v in (b.get("params") or {}).items()}
        args = ", ".join("%s=%s" % (k, _fmt_expr(v)) for k, v in params.items()
                         if re.match(r"^[a-z_]+$", k))
        if b.get("type") == "out.result":
            lines.append("    return %s or v['last']" % (_fmt_expr(params.get("text", "")) if params.get("text") else "v['last']"))
            break
        lines.append("    v['last'] = ctx.brick(%r%s)" % (b.get("type"), (", " + args) if args else ""))
        if b.get("var"):
            lines.append("    v[%r] = v['last']" % b["var"])
    else:
        lines.append("    return v['last']")
    return "\n".join(lines) + "\n"


def _fmt_expr(s):
    s = str(s or "")
    if "{" not in s:
        return repr(s)
    return "%r.format_map(_V(v))" % s


_V_HELPER = '''

class _V(dict):
    def __missing__(self, k):
        return "{" + k + "}"
'''


def migrate():
    """Навыки и кирпичные приложения -> Python-приложения (один раз)."""
    os.makedirs(_dir(), exist_ok=True)
    moved = []
    reg_fn = store.p("skills", "registry.json")
    if os.path.isfile(reg_fn):
        try:
            with open(reg_fn, encoding="utf-8") as f:
                reg = json.load(f) or {}
        except Exception:
            reg = {}
        for sid, sk in reg.items():
            a = _from_skill(sid, sk, store.p("skills", "code"))
            if a and a["id"] not in _all():
                r = save(a, origin=a["origin"])
                if r.get("ok"):
                    moved.append(a["name"])
        os.replace(reg_fn, reg_fn + ".migrated")
    lab_fn = store.p("applab.json")
    if os.path.isfile(lab_fn):
        try:
            with open(lab_fn, encoding="utf-8") as f:
                d = json.load(f) or {}
        except Exception:
            d = {}
        for a in d.values():
            if not isinstance(a, dict) or not a.get("bricks"):
                continue
            code = _from_bricks(a)
            if ".format_map(_V(v))" in code:
                code += _V_HELPER
            r = save({"name": a.get("name") or "приложение", "code": code,
                      "desc": "перенесено из кирпичиков", "triggers": [], "chat": False},
                     origin="кирпичики")
            if r.get("ok"):
                moved.append(a.get("name") or "приложение")
        os.replace(lab_fn, lab_fn + ".migrated")
    for s in SEEDS:                               # свои примеры — один раз
        if s["id"] not in _all() and not os.path.exists(store.p(ROOT, ".seeded_" + s["id"])):
            save(dict(s), origin="пример")
            open(store.p(ROOT, ".seeded_" + s["id"]), "w").close()
    if moved:
        store.log("APP LAB", "навыки и кирпичики перенесены в Python-приложения: %s"
                  % ", ".join(moved[:20]))
    return moved


# ============================ примеры (seed) ===============================
SEEDS = [
    {"id": "shop_search", "name": "Покупки: найти товар", "icon": "🛒",
     "desc": "Ищет товар в интернете и показывает предложения со ссылками и ценами, "
             "если они есть в выдаче. Ничего не покупает сам — решение за вами.",
     "triggers": ["купить", "где купить", "найди товар", "сколько стоит", "цена на"],
     "examples": ["где купить Arduino UNO Q", "сколько стоит робот-пылесос"],
     "chat": True,
     "code": '''# Покупки в интернете: поиск предложений по товару.
# ctx.web.search(q, n) -> [{title, url, snippet, src}] (Mail.ru, Mojeek, Википедия…)
import re


def run(text, ctx):
    q = re.sub(r"^(где\\s+)?(купить|найди товар|сколько стоит|цена на)\\s*", "", text.strip(), flags=re.I)
    q = q or text
    hits = ctx.web.search(q + " купить цена", 8)
    if not hits:
        return "Не нашла предложений по «%s»: поисковики ничего не вернули." % q
    lines = []
    for h in hits[:6]:
        price = re.search(r"(\\d[\\d\\s]{1,8}(?:[.,]\\d{2})?)\\s*(?:₽|руб)", h.get("snippet") or "")
        lines.append("• %s%s\\n  %s" % (h["title"], " — " + price.group(0) if price else "", h["url"]))
    ctx.state["last_query"] = q
    ctx.save_state()
    return "Нашла предложения по «%s»:\\n%s\\n\\nЦены — из сниппетов выдачи; точную цену и наличие проверьте на сайте." % (q, "\\n".join(lines))
'''},
]


# ================================ документы ================================
API_DOC = """API ctx (то, что получает run(text, ctx)):
  ctx.text, ctx.app, ctx.board         — фраза, id приложения, кто спросил
  ctx.say(*части)                      — добавить строку к ответу
  ctx.log(*части)                      — строка в журнал запуска (видно в App Lab)
  ctx.web.search(q, n=5)               — [{title, url, snippet, src}] из поисковиков
  ctx.web.page(url, chars=6000)        — {url, title, text} страница целиком (только https)
  ctx.web.wiki(тема)                   — {title, text, src} статья Википедии/Рувики | None
  ctx.docs.search(q, n=5)              — [{title, url, src, snippet}] по ИСТОЧНИКАМ-ссылкам
                                         (Confluence, вики, сайты, документы из настроек)
  ctx.docs.page(q)                     — {title, text, src} лучшая страница источников | None
  ctx.kb.search(q, n=3)                — [{title, snippet, score}] база знаний
  ctx.kb.add(title, text)              — положить статью в базу знаний
  ctx.memory.remember(text), ctx.memory.recall(q=""), ctx.memory.list(n=10)
  ctx.brain.think(q, long=False)       — полный ответ мозга облака (строка)
  ctx.brain.thought()                  — текущая мысль мозга neuro-12 | ""
  ctx.boards.ask(q), ctx.boards.state() — спросить плату / какие платы на связи
  ctx.llm.write(тема, facts)           — своя LLM пишет по фактам | "" (не вышло)
  ctx.calc(выражение)                  — калькулятор облака | None
  ctx.builtin(имя, text=None)          — math, time, wiki, memory, status, llm_extend
  ctx.brick(тип, **параметры)          — кирпичик старого App Lab (board.ask, know.kb, math.model…)
  ctx.state (dict) + ctx.save_state()  — память приложения между запусками
Исключение в run() — ответ «ошибка» с номером строки; ответ дольше 60 с не ждём.
Своя LLM тоже пишет приложения: она учится на коде включённых приложений (их
описание — задача «App Lab: …» в docstring run()) и по своей инструкции LLM_GUIDE."""

APP_GUIDE = """КАК НАПИСАТЬ ПРИЛОЖЕНИЕ APP LAB (Python) — инструкция для ИИ облака мозг-12.

App Lab — это мои навыки. Каждое приложение — папка data/applab/<id>/ с описанием
app.json и кодом python/main.py. В коде одна обязательная функция:

```python
def run(text, ctx):
    ...
    return "ответ строкой"
```

text — фраза владельца из чата (или из окна приложения), ctx — мои настоящие
возможности: интернет, Википедия, база знаний, память, мозг, платы, своя LLM.

Как я пишу приложение для себя:
1. Понимаю задачу одной фразой: что спрашивают и что нужно вернуть.
2. Выбираю триггеры — слова, по которым чат позовёт приложение («купить»,
   «сколько стоит»). Слишком общие слова («что», «как») не беру: приложение
   будет перехватывать чужие вопросы. Можно регулярное: «re:^курс\\s+\\w+».
3. Пишу run(): разбираю text (re), зову ctx-функции, собираю ответ.
   Только честные данные: что нашла — то и показываю, со ссылками на источник;
   не нашла — так и пишу. Не выдумываю цены, числа и факты.
4. Обрабатываю пустые результаты и ошибки сети (try/except), чтобы ответ был
   понятным, а не трассировкой.
5. Даю 1–2 примера фраз — они станут кнопками в окне чата.
6. Проверяю запуском на примере (кнопка «Запустить» в App Lab) и сохраняю.

Можно импортировать стандартные модули Python (re, json, math, datetime…).
Покупать, платить, вводить пароли и отправлять сообщения от имени владельца
приложение не должно — только искать, считать и показывать.
"""

_GUIDE_TIMER = [None]


def _after_change():
    try:
        import instructions
        instructions.refresh()
    except Exception:
        pass
    try:
        teach_llm()                      # v3.171: своя LLM учится на коде
    except Exception as e:
        store.log("APP LAB", "своя LLM не получила код: %s" % e)


def _refresh_guide_later():
    """Инструкция облака и учёба своей LLM — после изменений App Lab."""
    try:
        if _GUIDE_TIMER[0]:
            _GUIDE_TIMER[0].cancel()
        t = threading.Timer(5.0, _after_change)
        t.daemon = True
        _GUIDE_TIMER[0] = t
        t.start()
    except Exception:
        pass
