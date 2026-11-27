# -*- coding: utf-8 -*-
"""llmfact.py — v3.187: СВОЯ LLM ВЫХОДИТ В ИНТЕРНЕТ, ЧТОБЫ ПОДКРЕПИТЬ ФАКТ.

Заявка владельца: «своя LLM отвечает вместо облака — сделай так, чтобы и
LLM выходила в интернет, если нужно, например подкрепить факт».

Когда ответ написала своя LLM (режим «Разговор», «мозг + своя LLM» из
памяти, речь мозга), а не статья базы или страницы интернета, — факты в нём
ничем не подкреплены. Теперь:

  1) НУЖНО ЛИ (need): вопрос о факте («что такое…», «кто…», «когда…»,
     «сколько…», вопрос с темой и «?»), в ответе есть проверяемые
     утверждения (числа, годы, имена, «X — это …», «является»), или
     человек сам просит («проверь», «это правда?», «подтверди», «источник»).
     Приветствия и болтовня («как дела») — не проверяем;
  2) ИЩЕТ теми же безопасными функциями облака (только https, публичные
     адреса): статья Википедии/Рувики (readlike.fetch_article) и сниппеты
     поисковиков (webfetch.engine_snips) — параллельно, в бюджете времени
     llm_web_check_s (по умолчанию 12 с);
  3) СВЕРЯЕТ каждое утверждение LLM с предложениями источников: доля слов
     утверждения в одном месте источника; числа должны совпасть. Итог по
     каждому: подтверждено / источник пишет иначе / подтверждения нет;
  4) ДОПИСЫВАЕТ к ответу раздел «Проверка в интернете» (в «Разговоре» —
     сплошным текстом без значков и кавычек): что подтвердилось, главный факт
     из источника и ссылки. Ответ своей LLM не переписывается — человек видит
     и её слова, и что о них говорит источник;
  5) УЧИТСЯ: прочитанная статья — в свой корпус LLM (llmweb), она
     дообучится на ней; ничего не нашла — тема в очередь чтения llmweb.

Нет интернета или выключено (Настройки → «Своя LLM» → «Проверять факты в
интернете», llm_web_check) — ответ как раньше.
"""
import re
import threading
import time

import store

_ST = {"checks": 0, "ok": 0, "no": 0, "diff": 0, "nothing": 0, "last": "",
       "last_ts": 0.0, "err": ""}
_LOCK = threading.Lock()

_FORCE = re.compile(
    r"провер\w*|подтверд\w*|источник\w*|пруф\w*|ссылк\w* на|это правда|"
    r"правда ли|так ли это|точно ли|уверен\w*|факт\w* ли|докажи|откуда (ты )?"
    r"(это )?знаешь", re.I)
_FACTQ = re.compile(
    r"^\s*(а\s+)?(что так\w*|что значит|что означает|кто так\w*|кто (был|была|"
    r"был[аи]?|изобр\w*|открыл\w*|написал\w*|создал\w*|основал\w*)|кто это|"
    r"когда|где|сколько|какой|какая|какое|какие|каков\w*|почему|зачем|"
    r"отчего|как работа\w*|как устроен\w*|в каком|в какой|чем отлича\w*|"
    r"из чего|что будет|что произойд\w*|расскажи (о|об|про)|что известно)",
    re.I)
_DEF = re.compile(r"\s[—–-]\s(это\s)?|\bэто\b|\bявля\w+|\bназыва\w+|\bсостоит\b|"
                  r"\bнаходит\w*|\bбыл[аио]?\b|\bродил\w*|\bоснов\w+|\bоткры\w+|"
                  r"\bизобр\w+|\bсоздал\w*|\bнапис\w+", re.I)
_NUM = re.compile(r"\d+(?:[.,]\d+)?")
_SENT = re.compile(r"(?<=[.!?…])\s+(?=[А-ЯЁA-Z0-9«\"(])")
_STOPW = set("""что это как так все всё уже ещё еще его ее её их они она оно
мне меня тебя тебе себя свой своя свои который которая которые которое
этот эта эти того тому этим этой этого очень можно нужно надо есть был была
были было будет будут если когда тогда потом просто только также тоже
вообще сейчас здесь там тут где куда откуда зачем почему какой какая
какие каков такой такая такие такое кто чем чего между после перед через
более менее около""".split())


def enabled():
    s = store.settings()
    return bool(s.get("llm_web_check", True) and s.get("web_on", True))


def _budget():
    try:
        return max(3.0, min(40.0, float(store.settings().get("llm_web_check_s") or 12)))
    except Exception:
        return 12.0


def _online():
    try:
        import strict
        return strict.online()
    except Exception:
        return True


def _stems(text):
    words = [w for w in re.findall(r"[а-яёa-z]{3,}", str(text or "").lower().replace("ё", "е"))
             if w not in _STOPW]
    try:
        import kb
        stop = getattr(kb, "_STOP_STEMS", set())
        # первые 5 букв основы: «построили»/«построена», «Эйфелеву»/«Эйфеля»
        return [x[:5] for x in kb._stems(" ".join(words)) if x not in stop]
    except Exception:
        return [w[:5] for w in words]


def _nums(text):
    out = set()
    for n in _NUM.findall(str(text or "")):
        n = n.replace(",", ".")
        if n.endswith(".0"):
            n = n[:-2]
        out.add(n)
    return out


def _body(answer):
    """Текст ответа без разметки, подписей, источников, служебных строк."""
    keep = []
    for ln in str(answer or "").splitlines():
        t = ln.strip()
        if not t or t.startswith(("_", "📎", "Источник", "⚠", "🤖", "[", "|")):
            continue
        if re.match(r"^\*\*[^*]{1,40}\*\*\s*$", t):     # заголовок раздела
            continue
        t = re.sub(r"\*\*|__|`", "", t)
        t=re.sub(r"^(?:доброе\s+(?:утро|время)|добрый\s+(?:день|вечер)|здравствуй\w*|привет)(?:\b|[,!])[^.!?]{0,120}[.!?]\s*","",t,flags=re.I)
        if re.match(r"^(?:доброе\s+утро|добрый\s+(?:день|вечер)|здравствуй\w*|привет)\b",t,re.I) or not t:continue
        if re.match(r"^(?:пришли|уточни|укажите|подскажите|пожалуйста|можешь)\b",t,re.I):continue
        keep.append(t)
    return " ".join(keep)


def _sents(text):
    t = " ".join(str(text or "").split())
    return [s.strip() for s in _SENT.split(t) if len(s.strip()) >= 15]


def claims(answer, k=4):
    """Проверяемые утверждения ответа LLM (до k): с числами, именами,
    определениями — первыми."""
    out = []
    for s in _sents(_body(answer)):
        if s.endswith("?"):continue
        st = _stems(s)
        if len(st) < 3:
            continue
        w = 0.0
        if _NUM.search(s):
            w += 2.0
        if _DEF.search(s):
            w += 1.0
        if re.search(r"(?<!^)(?<![.!?]\s)\b[А-ЯЁA-Z][а-яёa-z]{2,}", s[1:]):
            w += 1.0                                  # имя собственное не в начале
        out.append((w, s))
    out.sort(key=lambda x: -x[0])
    return [s for _w, s in out[:k]]


def need(question, answer="", talk=False):
    """-> (нужно ли, почему)."""
    q = str(question or "").strip()
    if not q:
        return False, ""
    if _FORCE.search(q):
        return True, "попросили проверить"
    try:
        import smalltalk
        if smalltalk.kind_of(q):
            return False, ""
    except Exception:
        pass
    topic = _stems(q)
    if topic and _FACTQ.match(q):
        return True, "вопрос о факте"
    if topic and q.endswith("?") and not talk:
        return True, "вопрос с темой"
    cl = claims(answer, 4)
    strong = [c for c in cl if _NUM.search(c) or _DEF.search(c)]
    if strong:
        return True, "в ответе есть утверждения (числа, имена, определения)"
    if not topic:
        return False, ""
    if talk and q.endswith("?") and len(topic) >= 2:
        return True, "вопрос с темой"
    return False, ""


def _query(question):
    """Тема для поиска: вопрос без вводных слов и просьб."""
    q = re.sub(r"^\s*(а\s+|и\s+|ну\s+)?(скажи|расскажи|объясни|подскажи|"
               r"проверь|подтверди|ответь|напомни)(,)?\s+(мне\s+)?(пожалуйста\s+)?"
               r"((о|об|про)\s+)?", "", str(question or ""), flags=re.I)
    q = re.sub(r"\b(в интернете|по источникам|это правда|правда ли|так ли это|"
               r"пожалуйста)\b", " ", q, flags=re.I)
    q = re.sub(r"\s+", " ", q).strip(" ?!.,")
    return q[:150]


def _search(q, budget):
    """Параллельно: статья Википедии/Рувики и сниппеты поисковиков.
    -> ([{title, text, src, url}], [ошибки])"""
    res = {"art": None, "snips": [], "errs": []}

    def art():
        try:
            import readlike
            a, why = readlike.fetch_article(q)
            if a:
                res["art"] = a
            elif why:
                res["errs"].append(why)
        except Exception as e:
            res["errs"].append("Википедия: %s" % str(e)[:80])

    def snips():
        try:
            import webfetch
            res["snips"] = webfetch.engine_snips(q, limit=4) or []
        except Exception as e:
            res["errs"].append("поисковики: %s" % str(e)[:80])

    ths = [threading.Thread(target=f, daemon=True, name="llmfact-" + f.__name__)
           for f in (art, snips)]
    for t in ths:
        t.start()
    end = time.time() + budget
    for t in ths:
        t.join(max(0.0, end - time.time()))
    srcs = []
    a = res["art"]
    if a and a.get("text"):
        title = str(a.get("title") or "")
        lang = "ru"
        url = "https://ru.wikipedia.org/wiki/" + title.replace(" ", "_") \
            if "рувики" not in str(a.get("src") or "").lower() else \
            "https://ru.ruwiki.ru/wiki/" + title.replace(" ", "_")
        srcs.append({"title": title, "text": str(a["text"])[:20000],
                     "src": str(a.get("src") or "Википедия"), "url": url,
                     "lang": lang, "article": True})
    for s in res["snips"]:
        txt = " ".join(str(s.get("snippet") or "").split())
        if len(txt) >= 40:
            srcs.append({"title": str(s.get("title") or "")[:120], "text": txt,
                         "src": str(s.get("src") or "сайт"),
                         "url": str(s.get("url") or ""), "article": False})
    if any(t.is_alive() for t in ths):
        res["errs"].append("часть источников не ответила за %.0f с" % budget)
    return srcs, res["errs"]


def _windows(srcs):
    """Места в источниках: предложение и пара соседних предложений."""
    out = []
    for i, s in enumerate(srcs):
        ss = _sents(s["text"])[:250]
        for j, a in enumerate(ss):
            out.append((i, j, a))
            if j + 1 < len(ss):
                out.append((i, j, a + " " + ss[j + 1]))
    return [(i, j, t, set(_stems(t)), _nums(t)) for i, j, t in out]


def _verdict(claim, wins):
    """-> (статус ok|diff|no, лучшее место (i, текст), доля)."""
    cs = set(_stems(claim))
    if len(cs) < 3:
        return "no", None, 0.0
    cn = _nums(claim)
    best, bscore = None, 0.0
    for i, j, t, ts, tn in wins:
        sc = len(cs & ts) / float(len(cs))
        if sc > bscore:
            best, bscore = (i, j, t, tn), sc
    if not best or bscore < 0.45:
        return "no", None, bscore
    i, _j, t, tn = best
    if cn:
        if cn <= tn:
            return ("ok" if bscore >= 0.5 else "no"), (i, t), bscore
        if tn and bscore >= 0.5:
            # те же слова, другие числа — цитируем одно предложение с числом
            one = max((x for x in _sents(t) if _nums(x)),
                      key=lambda x: len(cs & set(_stems(x))), default=t)
            return "diff", (i, one), bscore
        return "no", (i, t), bscore
    return ("ok" if bscore >= 0.6 else "no"), (i, t), bscore


def _fact(question, srcs, wins):
    """Главный факт источника по вопросу: предложение с наибольшей долей
    темы вопроса; у статьи — ближе к началу (определение)."""
    qs = set(_stems(_query(question)))
    if not qs:
        return None
    ql = str(question or "").lower()
    kind = ("when" if re.search(r"\bкогда\b|в каком году|какого года|в какие годы", ql) else
            "num" if re.search(r"\bсколько\b|какой (высоты|длины|массы|площади|глубины)|"
                               r"какова|каков\b|насколько", ql) else
            "who" if re.search(r"^\s*(а\s+)?кто\b", ql) else "")
    best, bsc = None, 0.0
    for i, j, t, ts, _tn in wins:
        if len(t) > 360 or " " not in t:
            continue
        sc = len(qs & ts) / float(len(qs))
        if kind == "when" and re.search(r"\b(1[0-9]{3}|20[0-9]{2})\b|\b(январ|феврал|март|"
                                         r"апрел|ма[йя]|июн|июл|август|сентябр|октябр|"
                                         r"ноябр|декабр)\w*|\bвек\w*", t, re.I):
            sc += 0.4
        elif kind == "num" and _NUM.search(t):
            sc += 0.35
        elif kind == "who" and re.search(r"[А-ЯЁ][а-яё]+ [А-ЯЁ][а-яё]+", t[1:]):
            sc += 0.2
        if " " in t and len(_sents(t)) > 1:
            sc -= 0.05                             # одно предложение точнее пары
        if srcs[i].get("article"):
            sc += max(0.0, 0.25 - 0.02 * j)        # вводка статьи — определение
        if re.search(r"\s[—–-]\s", t[:80]):
            sc += 0.1
        if sc > bsc:
            best, bsc = (i, t), sc
    if not best or bsc < 0.5:
        return None
    return best


def check(question, answer, talk=False, why=""):
    """Проверить ответ своей LLM в интернете. -> dict | None (нечего/нельзя)."""
    if not enabled() or not _online():
        return None
    q = _query(question)
    if len(_stems(q)) < 1 or (not _FACTQ.match(str(question or "")) and
                              not _FORCE.search(str(question or "")) and
                              len(_stems(q)) < 2):
        # в реплике темы нет («ну да») — ищем по самому утверждению LLM
        cl = claims(answer, 1)
        if cl:
            ws = [w for w in re.findall(r"[А-ЯЁа-яёA-Za-z0-9-]{3,}", cl[0])
                  if w.lower() not in _STOPW]
            q = " ".join(ws[:7])
    if not _stems(q):
        return None
    t0 = time.time()
    try:
        import activity
        activity.update("Своя LLM проверяет факт в интернете…")
    except Exception:
        pass
    srcs, errs = _search(q, _budget())
    with _LOCK:
        _ST["checks"] += 1
        _ST["last_ts"] = time.time()
        _ST["last"] = q[:80]
    if not srcs:
        with _LOCK:
            _ST["nothing"] += 1
            _ST["err"] = "; ".join(errs)[:160]
        _learn_want(q)
        return {"q": q, "found": False, "errors": errs[:2], "why": why,
                "ms": int((time.time() - t0) * 1000)}
    wins = _windows(srcs)
    res = []
    for c in claims(answer, 4):
        st, place, sc = _verdict(c, wins)
        res.append({"claim": c, "status": st, "score": round(sc, 2),
                    "src": place[0] if place else None,
                    "quote": place[1] if place and st == "diff" else ""})
    fact = _fact(q, srcs, wins)
    with _LOCK:
        for r in res:
            _ST[{"ok": "ok", "diff": "diff"}.get(r["status"], "no")] += 1
    used = sorted({r["src"] for r in res if r["src"] is not None and r["status"] != "no"} |
                  ({fact[0]} if fact else set()))
    if not used:
        used = [0]
    out = {"q": q, "found": True, "why": why, "claims": res,
           "fact": {"text": fact[1], "src": fact[0]} if fact else None,
           "sources": [{"title": srcs[i]["title"], "src": srcs[i]["src"],
                        "url": srcs[i]["url"]} for i in used if i < len(srcs)],
           "all_sources": [{"title": s["title"], "src": s["src"], "url": s["url"]}
                           for s in srcs[:5]],
           "errors": errs[:2], "ms": int((time.time() - t0) * 1000)}
    out["_idx"] = {i: n for n, i in enumerate(used)}
    _learn(srcs, q)
    store.log("LLM-ФАКТЫ", "своя LLM проверила в интернете «%s» (%s): утверждений %d, "
              "подтверждено %d, иначе %d; источников %d, %.1f с" % (
                  q[:60], why or "нужно", len(res),
                  sum(1 for r in res if r["status"] == "ok"),
                  sum(1 for r in res if r["status"] == "diff"), len(srcs),
                  time.time() - t0))
    return out


def _name(s):
    t = str(s.get("title") or "").strip()
    src = str(s.get("src") or "").strip()
    if t and "википед" in src.lower():
        return "Википедия, %s" % t
    if t and "рувики" in src.lower():
        return "Рувики, %s" % t
    return src or t or "источник"


def _short(t, n=170):
    t = " ".join(str(t or "").split())
    if len(t) <= n:
        return t
    cut = t[:n].rsplit(" ", 1)[0]
    return cut + "…"


def render(res, talk=False):
    """Раздел для ответа. talk — сплошной текст без значков и кавычек."""
    if not res:
        return ""
    if not res.get("found"):
        if talk:
            return ("Проверить в интернете не вышло: источники не ответили. "
                    "Сказанное выше — без опоры на факты.")
        return ("**Проверка в интернете**\nПодкрепить ответ не вышло: %s. Сказанное "
                "выше своя LLM написала без опоры на факты." %
                ("; ".join(res.get("errors") or []) or "источники не ответили"))
    srcs = res.get("all_sources") or []
    idx = res.get("_idx") or {}
    cl = res.get("claims") or []
    ok = [c for c in cl if c["status"] == "ok"]
    diff = [c for c in cl if c["status"] == "diff"]
    no = [c for c in cl if c["status"] == "no"]
    fact = res.get("fact")
    if talk:
        parts = []
        if fact and fact["src"] < len(srcs):
            parts.append("Проверила в интернете, %s: %s" % (
                _name(srcs[fact["src"]]), _short(fact["text"], 300)))
        else:
            parts.append("Проверила в интернете, источники: %s." % ", ".join(
                _name(s) for s in srcs[:2]))
        if ok and not diff:
            parts.append("Сказанное выше источник подтверждает.")
        elif diff:
            parts.append("Но источник пишет иначе, чем сказано выше: %s" %
                         _short(diff[0]["quote"], 220))
        elif cl:
            parts.append("Подтверждения сказанному выше в источниках не нашла, "
                         "опирайся на факт из источника.")
        return " ".join(parts)
    lines = ["**Проверка в интернете**"]
    for c in ok:
        s = srcs[c["src"]] if c["src"] is not None and c["src"] < len(srcs) else {}
        lines.append("✓ подтверждено (%s): %s" % (_name(s), _short(c["claim"])))
    for c in diff:
        s = srcs[c["src"]] if c["src"] is not None and c["src"] < len(srcs) else {}
        lines.append("≠ источник пишет иначе: %s\n    %s: %s" % (
            _short(c["claim"]), _name(s), _short(c["quote"], 260)))
    for c in no:
        lines.append("? подтверждения не нашла: %s" % _short(c["claim"]))
    if fact and fact["src"] < len(srcs):
        lines.append("По данным источника (%s): %s" % (
            _name(srcs[fact["src"]]), _short(fact["text"], 360)))
    links = []
    seen = set()
    for s in ([srcs[i] for i in sorted(idx) if i < len(srcs)] + srcs)[:4]:
        if s.get("url") and s["url"] not in seen:
            seen.add(s["url"])
            links.append("[%s](%s)" % (_name(s).replace("[", "(").replace("]", ")"),
                                       s["url"]))
    if links:
        lines.append("_Своя LLM сверилась с интернетом: %s_" % " · ".join(links[:3]))
    return "\n".join(lines)


def summary(res):
    """Коротко — в info ответа (для «/почему» и ленты)."""
    if not res:
        return None
    cl = res.get("claims") or []
    return {"q": res.get("q"), "found": bool(res.get("found")),
            "ok": sum(1 for c in cl if c["status"] == "ok"),
            "diff": sum(1 for c in cl if c["status"] == "diff"),
            "no": sum(1 for c in cl if c["status"] == "no"),
            "sources": [s.get("url") for s in (res.get("sources") or [])][:3],
            "why": res.get("why"), "ms": res.get("ms")}


def _learn(srcs, q):
    """Прочитанная статья — в свой корпус LLM (дообучится на ней)."""
    try:
        import llmweb
        for s in srcs:
            if s.get("article"):
                llmweb.remember(s["title"], s["text"], s["src"], q, "проверка факта")
                break
    except Exception:
        pass


def _learn_want(q):
    try:
        import llmweb
        llmweb.want(q, "проверка факта: источники не ответили")
    except Exception:
        pass


def augment(question, answer, talk=False, force=False):
    """Главный вход: (ответ, info-сводка | None). Ничего не нужно/нельзя —
    ответ без изменений."""
    if not enabled():
        return answer, None
    ok, why = need(question, answer, talk=talk)
    if not ok and not force:
        return answer, None
    try:
        res = check(question, answer, talk=talk, why=why or "просили")
    except Exception as e:
        with _LOCK:
            _ST["err"] = str(e)[:160]
        store.log("LLM-ФАКТЫ", "проверка не удалась: %s" % e)
        return answer, None
    if not res:
        return answer, None
    import answer_focus
    if answer_focus.practical(question):
        return answer, summary(res)  # report verification in diagnostics, not a second answer
    block = render(res, talk=talk)
    if not block:
        return answer, summary(res)
    if talk:
        return answer, dict(summary(res), text=block)
    return str(answer or "").rstrip() + "\n\n" + block, summary(res)


def status():
    with _LOCK:
        return dict(_ST, on=enabled(), budget_s=_budget())
