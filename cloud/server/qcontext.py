# -*- coding: utf-8 -*-
"""
qcontext.py — v3.131: ПОНИМАНИЕ КОНТЕКСТА ВОПРОСА ДО ОТВЕТА.

Заявка владельца: «пусть LLM тоже смотрит на контекст вопроса, а зоны
подбирают ответ исходя из понятого контекста».

Кто понимает вопрос (честно, по уровням):
  1) РАЗБОР ВОПРОСА (всегда): что спрашивают (определение, объяснение, дата,
     число, место, человек, список, сравнение, инструкция, код, расчёт…),
     тема (слова без вопросительных), эпоха/дата (qfocus), продолжение
     разговора («а почему?» — тема прошлого вопроса).
  2) ЛОКАЛЬНАЯ МОДЕЛЬ (если подключена в Настройках → API и ответы, Ollama и
     т.п.): ей уходит вопрос, она возвращает JSON {тема, что нужно, вид,
     поисковый запрос}. Это настоящая языковая модель — её понимание главнее
     разбора.
  3) СВОЯ LLM облака: участвует, ТОЛЬКО если прошла проверку точности на
     базе знаний: на вопросы «что такое <название статьи>» она должна
     выбрать первое предложение этой статьи среди чужих по тому, насколько
     вопрос делает предложение вероятнее (PMI = потеря без вопроса − потеря
     с вопросом). Порог 70 %. Не прошла — в понимании не участвует, и это
     видно в «/почему» (с её процентом). Маленькая сеть, обученная на мало
     шагов, выбирает почти наугад — выдавать это за понимание было бы враньём.

Как зоны используют понятое:
  • вид «код» — ответ даёт зона «Код» (заготовки, локальная модель, проверка);
  • поиск в базе знаний и интернете — по теме (и запросу модели), без
    вопросительных слов и слов эпохи;
  • в найденном тексте первым ставится предложение, которое отвечает на то,
    что спросили: на «когда» — с датой, на «сколько» — с числом, на «что
    такое» — определение, на «почему» — с причиной; предложения без слов
    темы, когда есть с ними, отбрасываются;
  • своя LLM (если прошла проверку) добавляет к этому выбору свою оценку.
"""
import json
import random
import re
import threading
import time
import urllib.request

import store

_STOP = set("""что такое такой такая такие кто как какой какая какие каков где когда
почему зачем сколько чем чего кого кому это эта этот эти был была были было
есть ли же бы а и в во на по о об от до из за для при про с со у к ко
расскажи скажи объясни опиши покажи знаешь подскажи пожалуйста мне тебе
можно нужно надо очень самый самая самое""".split())

_KINDS = [
    ("код", re.compile(r"\b(напиши|сделай|создай|составь)\b.{0,40}\b(код|функци\w*|программ\w*|"
                       r"скрипт\w*|скетч\w*|класс\w*|алгоритм\w*)|\bна\s+(python|питоне|javascript|js|"
                       r"c\+\+|ардуино|arduino|bash)\b", re.I)),
    ("расчёт", re.compile(r"\b(посчитай|вычисли|реши|сколько\s+будет|чему\s+равн\w+)\b", re.I)),
    ("инструкция", re.compile(r"\bкак\s+(сделать|установить|настроить|подключить|включить|"
                              r"исправить|починить|собрать|запустить|приготовить)\b|\bинструкци", re.I)),
    ("сравнение", re.compile(r"\b(чем\s+отличается|разница\s+между|отличи\w+\s+между|что\s+лучше|"
                             r"сравни)\b", re.I)),
    ("список", re.compile(r"\b(перечисли|назови|какие\s+(бывают|есть|существуют)|виды|типы|список)\b",
                          re.I)),
    ("дата", re.compile(r"\bкогда\b|в\s+как\w+\s+год|какого\s+числа|\bв\s+каком\s+веке", re.I)),
    ("число", re.compile(r"\bсколько\b|\bкакова\s+(масса|длина|высота|скорость|температура|"
                         r"площадь|численность)|\bкакой\s+(вес|рост|объём|радиус)", re.I)),
    ("место", re.compile(r"\bгде\b|\bв\s+какой\s+стране|\bоткуда\b|\bкуда\b", re.I)),
    ("человек", re.compile(r"^\s*кто\b(?!\s+так)|\bкто\s+(изобрёл|изобрел|открыл|написал|"
                           r"создал|придумал|основал|был)", re.I)),
    ("объяснение", re.compile(r"\bпочему\b|\bзачем\b|\bотчего\b|\bкак\s+работает|\bкак\s+устроен|"
                              r"\bкак\s+происходит|\bв\s+чём\s+причина", re.I)),
    ("определение", re.compile(r"\bчто\s+такое\b|\bкто\s+так(ой|ая|ие)\b|\bчто\s+значит\b|"
                               r"\bчто\s+означает\b|\bчто\s+за\b", re.I)),
]
_WANT_RU = {"код": "код", "расчёт": "расчёт", "инструкция": "как сделать (шаги)",
            "сравнение": "чем отличаются", "список": "перечень", "дата": "когда (дата)",
            "число": "сколько (число)", "место": "где (место)", "человек": "кто (человек)",
            "объяснение": "почему / как работает", "определение": "что это (определение)",
            "другое": "сведения по теме"}

_CACHE = {}
_REL = {"ts": 0.0, "steps": -1, "acc": None, "n": 0, "why": "", "busy": False}
_LOCK = threading.Lock()
REL_MIN = 0.70


def _stems(t):
    return [w[:5] for w in re.findall(r"[а-яёa-z0-9]{3,}", str(t or "").lower().replace("ё", "е"))
            if w not in _STOP]


# --------------------------------------------------------------- понимание
def understand(text, q_text=None, is_follow=False):
    """Вопрос -> dict понятого: kind, want, topic, topic_stems, era_text,
    search, by (кто понимал), llm_own (участвовала ли своя LLM и её точность)."""
    q = str(q_text or text or "").strip()
    key = q.lower()[:300]
    if key in _CACHE and time.time() - _CACHE[key]["ts"] < 600:
        return dict(_CACHE[key]["v"])
    try:
        import qfocus
        qf = qfocus.parse(q)
    except Exception:
        qf = {"topic_query": q, "want": "", "era": None, "era_text": ""}
    kind = "другое"
    for k, rx in _KINDS:
        if rx.search(q):
            kind = k
            break
    topic = qf.get("topic_query") or q
    topic_words = [w for w in re.findall(r"[А-Яа-яЁёA-Za-z0-9-]{2,}", topic)
                   if w.lower().replace("ё", "е") not in _STOP]
    out = {"kind": kind, "want": _WANT_RU.get(kind, kind),
           "want_word": qf.get("want") or "",
           "topic": " ".join(topic_words)[:160] or q[:160],
           "era_text": qf.get("era_text") or "", "search": "",
           "follow": bool(is_follow), "by": ["разбор вопроса"], "llm": ""}
    if kind == "другое" and qf.get("want"):      # «какая народность» и т.п.
        out["want"] = "какая/какой %s" % qf["want"]
    loc = _local_llm(q)
    if loc:
        out["by"].append("локальная модель")
        out["llm"] = "локальная модель"
        lk = str(loc.get("kind") or "").strip().lower()
        if lk in _WANT_RU:
            out["kind"], out["want"] = lk, _WANT_RU[lk]
        if str(loc.get("topic") or "").strip():
            out["topic"] = str(loc["topic"]).strip()[:160]
        if str(loc.get("search") or "").strip():
            out["search"] = str(loc["search"]).strip()[:160]
        if str(loc.get("want") or "").strip():
            out["want"] = str(loc["want"]).strip()[:120]
    rel = own_reliability(background=True)
    out["llm_own"] = {"used": bool(rel.get("acc") is not None and rel["acc"] >= REL_MIN),
                      "acc": rel.get("acc"), "n": rel.get("n"), "why": rel.get("why")}
    if out["llm_own"]["used"]:
        out["by"].append("своя LLM (точность %d%%)" % round(100 * rel["acc"]))
    out["topic_stems"] = _stems(out["topic"])
    with _LOCK:
        if len(_CACHE) > 300:
            _CACHE.clear()
        _CACHE[key] = {"ts": time.time(), "v": dict(out)}
    return out


def summary(u):
    """Понятое — одной строкой для «/почему»."""
    if not u:
        return ""
    s = "нужно — %s; тема — «%s»" % (u.get("want"), u.get("topic"))
    if u.get("era_text"):
        s += "; время — %s" % u["era_text"]
    if u.get("follow"):
        s += "; это продолжение прошлого вопроса"
    s += ". Понимали: %s" % ", ".join(u.get("by") or [])
    lo = u.get("llm_own") or {}
    if not lo.get("used"):
        if lo.get("acc") is not None:
            s += (" (своя LLM не участвовала: на проверке по базе знаний выбрала "
                  "верное предложение в %d%% случаев, нужно ≥%d%%)" % (
                      round(100 * lo["acc"]), round(100 * REL_MIN)))
        elif lo.get("why"):
            s += " (своя LLM не участвовала: %s)" % lo["why"]
    return s


# ------------------------------------------------- локальная модель (Ollama)
_SYS = ("Ты разбираешь вопрос пользователя, отвечать на него НЕ нужно. Верни "
        "только JSON без пояснений: {\"topic\": \"тема в 1-5 словах\", \"want\": "
        "\"что именно нужно в ответе, коротко\", \"kind\": одно из [определение, "
        "объяснение, дата, число, место, человек, список, сравнение, инструкция, "
        "код, расчёт, другое], \"search\": \"запрос для поиска в энциклопедии\"}")


def _local_llm(q):
    s = store.settings()
    url = str(s.get("llm_answer_url") or "").strip().rstrip("/")
    if not url or s.get("ctx_llm_on", True) is False:
        return None
    body = {"model": str(s.get("llm_answer_model") or "qwen2.5:1.5b"),
            "messages": [{"role": "system", "content": _SYS},
                         {"role": "user", "content": str(q)[:600]}],
            "temperature": 0.0}
    try:
        req = urllib.request.Request(
            url + ("" if url.endswith("completions") else "/chat/completions"),
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=min(4.0, float(s.get("ctx_llm_timeout", 3) or 3))) as r:
            d = json.loads(r.read().decode("utf-8", "replace"))
        txt = str(d["choices"][0]["message"]["content"])
        m = re.search(r"\{[\s\S]*\}", txt)
        return json.loads(m.group(0)) if m else None
    except Exception as e:
        store.log("КОНТЕКСТ", "локальная модель не разобрала вопрос: %s" % str(e)[:120])
        return None


# --------------------------------------------- своя LLM: проверка точности
def _pmi(m, q, sent):
    a = m.nll(" ", sent)
    b = m.nll(str(q) + " ", sent)
    if a is None or b is None:
        return None
    return a - b


def _measure():
    """Точность своей LLM на вопросах «что такое <статья>»: выбирает ли она
    первое предложение этой статьи среди 3 чужих."""
    try:
        import kb
        import tinyllm
        st = tinyllm.status()
        if not st.get("ready"):
            return None, 0, "своя LLM не обучена"
        m = tinyllm.model()
        if not hasattr(m, "nll"):
            return None, 0, "своя LLM без оценки вероятности (нет numpy)"
        arts = []
        for a in kb.list_articles(limit=400, include_devices=False):
            t = str(a.get("title") or "")
            full = kb.article(a.get("id")) or {}
            body = str(full.get("text") or "")
            first = re.split(r"(?<=[.!?])\s+", body.strip())[0] if body else ""
            if 3 <= len(t) <= 60 and 40 <= len(first) <= 300 and not re.match(r"^\d", t):
                arts.append((t, first))
        if len(arts) < 6:
            return None, 0, "в базе мало статей для проверки (%d)" % len(arts)
        rnd = random.Random(7)
        rnd.shuffle(arts)
        arts = arts[:24]
        ok = n = 0
        for i, (t, first) in enumerate(arts):
            others = [x[1] for j, x in enumerate(arts) if j != i]
            cands = [first] + rnd.sample(others, min(3, len(others)))
            q = "что такое %s" % t
            sc = [_pmi(m, q, c) for c in cands]
            if any(s is None for s in sc):
                continue
            n += 1
            if max(range(len(sc)), key=lambda k: sc[k]) == 0:
                ok += 1
        if n < 6:
            return None, n, "проверка не собралась"
        return ok / float(n), n, ""
    except Exception as e:
        return None, 0, "проверка упала: %s" % str(e)[:80]


def own_reliability(background=False):
    """Точность своей LLM в понимании (кэш до смены шагов обучения / 1 ч)."""
    try:
        import tinyllm
        steps = int(tinyllm.status().get("steps") or 0)
    except Exception:
        steps = 0
    fresh = (time.time() - _REL["ts"] < 3600 and abs(steps - _REL["steps"]) < 300)
    if fresh or _REL["busy"]:
        return dict(_REL)
    if _REL["ts"] and background:          # v3.132: перепроверка — на вдохе
        try:
            import breath as _brx
            if not _brx.inhale("Понимание вопроса"):
                return dict(_REL)
        except Exception:
            pass

    def _run():
        _REL["busy"] = True
        try:
            acc, n, why = _measure()
            _REL.update(acc=acc, n=n, why=why, ts=time.time(), steps=steps)
            store.log("КОНТЕКСТ", "своя LLM: точность понимания %s на %d вопросах%s" % (
                "—" if acc is None else "%d%%" % round(100 * acc), n,
                (" (" + why + ")") if why else ""))
        finally:
            _REL["busy"] = False
    if background:
        threading.Thread(target=_run, daemon=True).start()
        return dict(_REL)
    _run()
    return dict(_REL)


# ------------------------------------------- подбор ответа по понятому
_DATE = re.compile(r"\b(1[0-9]{3}|20[0-9]{2}|[IVXL]+\s+век\w*|\d{1,2}\s+(январ|феврал|март|апрел|ма[яй]|"
                   r"июн|июл|август|сентябр|октябр|ноябр|декабр)\w*)", re.I)
_NUM = re.compile(r"\d")
_DEF = re.compile(r"^\s*[^.]{0,80}?\s(—|–|-)\s|\bэто\b|\bназывается\b|\bназывают\b", re.I)
_WHY = re.compile(r"\b(потому|так как|из-за|поэтому|благодаря|вследствие|причин\w*|"
                  r"приводит|происходит|за счёт|за счет)\b", re.I)
_PLACE = re.compile(r"\b(в|на|во)\s+[А-ЯЁ][а-яё]+|\bнаходится\b|\bрасположен", re.U)
_PERSON = re.compile(r"\b[А-ЯЁ][а-яё]+\s+[А-ЯЁ][а-яё]+\b|\b[А-ЯЁ]\.\s?[А-ЯЁ]?\.?\s?[А-ЯЁ][а-яё]+")
_STEP = re.compile(r"^\s*(\d+[.)]|•|-)\s|\bнужно\b|\bследует\b|\bсначала\b|\bзатем\b", re.I)


def _kind_bonus(kind, s):
    if kind == "дата":
        return 1.5 if _DATE.search(s) else 0.0
    if kind == "число":
        return 1.2 if _NUM.search(s) else 0.0
    if kind == "определение":
        return 1.2 if _DEF.search(s[:160]) else 0.0
    if kind == "объяснение":
        return 1.2 if _WHY.search(s) else 0.0
    if kind == "место":
        return 1.0 if _PLACE.search(s) else 0.0
    if kind == "человек":
        return 1.0 if _PERSON.search(s) else 0.0
    if kind in ("инструкция", "список"):
        return 0.8 if _STEP.search(s) else 0.0
    return 0.0


def rerank(text, u, question=""):
    """Ответ-текст -> (текст, что сделано). Первым — предложение, которое
    отвечает на то, что спросили; без слов темы (если есть с ними) — долой."""
    if not u or not text:
        return text, ""
    lines = str(text).split("\n")
    # главный абзац ответа — первая «обычная» строка с несколькими предложениями
    idx = None
    for i, ln in enumerate(lines):
        st = ln.strip()
        if st and not st.startswith(("_", "**", "[", "#", "Источник", "🖼", "📷", "•", "```")) and \
                len(re.findall(r"[.!?…](\s|$)", st)) >= 2:
            idx = i
            break
    if idx is None:
        return text, ""
    sents = [x for x in re.split(r"(?<=[.!?…])\s+", lines[idx].strip()) if x]
    if len(sents) < 2:
        return text, ""
    ts = set(u.get("topic_stems") or [])
    use_llm = bool((u.get("llm_own") or {}).get("used"))
    pm = [0.0] * len(sents)
    if use_llm:
        try:
            import tinyllm
            m = tinyllm.model()
            raw = [_pmi(m, question or u.get("topic"), s_) for s_ in sents]
            vals = [v for v in raw if v is not None]
            if len(vals) >= 2:
                mu = sum(vals) / len(vals)
                sd = (sum((v - mu) ** 2 for v in vals) / len(vals)) ** 0.5 or 1.0
                pm = [((v - mu) / sd) * 0.5 if v is not None else 0.0 for v in raw]
        except Exception:
            pass
    sc, cov = [], []
    for i, s_ in enumerate(sents):
        ss = set(_stems(s_))
        c = (len(ts & ss) / float(len(ts))) if ts else 0.0
        cov.append(c)
        sc.append(2.0 * c + _kind_bonus(u.get("kind"), s_) + pm[i] - 0.05 * i)
    done = []
    keep = list(range(len(sents)))
    if ts and any(c > 0 for c in cov):
        keep = [i for i in keep if cov[i] > 0 or _kind_bonus(u.get("kind"), sents[i]) > 0]
        if len(keep) < len(sents):
            done.append("убрано не по теме: %d" % (len(sents) - len(keep)))
    best = max(keep, key=lambda i: sc[i])
    order = [best] + [i for i in keep if i != best]
    if best != keep[0]:
        done.append("первым — то, что спросили (%s)" % u.get("want"))
    if not done:
        return text, ""
    lines[idx] = " ".join(sents[i] for i in order)
    return "\n".join(lines), "; ".join(done)
