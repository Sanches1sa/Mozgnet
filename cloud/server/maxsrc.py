# -*- coding: utf-8 -*-
"""maxsrc.py — v3.188: ЧАТЫ-ИСТОЧНИКИ ОТВЕТОВ ДЛЯ МАКС-БОТА.

Заявка владельца: «сделай так, чтобы можно было выбрать чаты, в которых
Макс-бот будет брать информацию; бот берёт информацию из чата из списка БЕЗ
добавления истории чата в базу знаний; пусть проходит по всей истории чата
и выбирает правильный ответ».

Как устроено:
  * СПИСОК — Настройки → «Макс-бот» → «Чаты-источники ответов»: галочки у
    известных чатов или ссылка/ID чата (maxbot_src_chats).
  * ИСТОРИЯ — бот читает выбранный чат ЦЕЛИКОМ (GET /messages, новейшие
    первыми, страницами по 100) в ОТДЕЛЬНЫЙ индекс data/maxsrc/<чат>.json.
    В базу знаний, статьи, «память нейросети» и инструкции из этих чатов
    НИЧЕГО не идёт: живая учёба и скан истории для них выключены. Новые
    сообщения дописываются в индекс сразу по событию, раз в 15 минут —
    сверка хвоста. Индекс можно перечитать заново кнопкой.
  * ОТВЕТ — на каждый вопрос бот проходит по ВСЕЙ истории выбранных чатов:
      1) похожие вопросы, которые там уже задавали (доля слов вопроса);
      2) ответы на них: прямой ответ (reply) на вопрос и сообщения других
         людей следом (в пределах 3 часов);
      3) просто сообщения по теме (без вопроса) — слабее;
    и выбирает ПРАВИЛЬНЫЙ ответ по признакам самого чата:
      + ответ прямо на вопрос («ответить на сообщение»);
      + потом подтвердили: «спасибо», «помогло», «работает», «получилось»,
        «верно», «👍», «+» — ответом на это сообщение или спросивший;
      − потом опровергли: «нет», «не так», «не работает», «неправильно»;
      + сам спросивший нашёл решение («решил», «разобрался», «сработало»);
      + несколько разных людей ответили одно и то же (согласие);
      + свежий ответ чуть весомее старого (правила меняются);
      − «не знаю», «хз», одни смайлы — не ответ.
  * В ОТВЕТЕ БОТА — найденный ответ из чата с автором, датой, на какой
    вопрос он был, кто подтвердил; если уверенность средняя — к обычному
    ответу мозга дописывается «Возможно, подойдёт из чата…». Ничего не
    выдумывается — только слова из истории чата.
  * КОМУ — maxbot_src_scope: "all" — в любом чате и в личке; "same" —
    только в том же чате, откуда ответ (чтобы переписка группы не уходила
    посторонним).
"""
import json
import os
import re
import threading
import time
import urllib.parse

import store

PAGE = 100                       # сообщений за запрос (МАКС разрешает до 100)
PAGE_GAP = 0.35                  # пауза между страницами (лимиты API)
MAX_MSGS = 200000                # потолок сообщений на чат в индексе
TEXT_MAX = 4000
SYNC_EVERY = 15 * 60             # сверка хвоста
ANSWER_WIN = 3 * 3600            # ответы на вопрос — в пределах 3 часов
CONFIRM_WIN = 48 * 3600          # подтверждения — в пределах 2 суток
GLUE_S = 180                     # продолжение ответа тем же автором
HIGH, MEDIUM = 3.2, 2.0          # пороги уверенности

_LOCK = threading.RLock()
_IDX = {}                        # chat_id -> индекс в памяти
_SYNC = {}                       # chat_id -> {"running", "pages", "new", "err", ...}
_STOP = threading.Event()
_LAST_TICK = [0.0]

_Q = re.compile(r"\?|^\s*(как|что|чем|где|куда|откуда|когда|почему|зачем|кто|"
                r"какой|какая|какое|какие|каким|сколько|подскаж\w*|помогите|"
                r"можно ли|есть ли|не подскаж\w*|кто[- ]нибудь|кто знает|"
                r"не работает|не получается|не могу)\b", re.I)
_CONFIRM = re.compile(r"\bспасибо\b|\bспс\b|\bблагодар\w*|помогл[оа]|"
                      r"\bработает\b|заработал\w*|получил\w*|\bсработал\w*|"
                      r"\bверно\b|\bточно\b|\bправильно\b|\bда,? так\b|"
                      r"\bименно\b|\bподтвержда\w*|👍|👌|✅|🙏|(^|\s)\+\s*1?\s*$|"
                      r"^\s*\+\s*$|\bпорешал\w*|\bрешил\w* проблем\w*", re.I)
_REFUTE = re.compile(r"^\s*нет\b|\bне так\b|\bне работает\b|\bне помогл\w*|"
                     r"\bнеправильн\w*|\bневерн\w*|\bошиб\w*|\bне то\b|"
                     r"\bбред\b|\bне получ\w*|\bне сработал\w*|👎", re.I)
_SOLVED = re.compile(r"\bрешил\w*|\bразобрал\w*|\bполучил\w*|\bсработал\w*|"
                     r"\bнаш[её]л\w*|\bпомогло\b|\bзаработал\w*", re.I)
_NOANS = re.compile(r"^\s*(не знаю|хз|без понятия|понятия не имею|"
                    r"не в курсе|ок|ага|угу|да|нет|ясно|понял\w*)\W*$", re.I)
_STOPW = set("""что это как так все всё уже ещё еще его ее её их они она оно
мне меня тебя тебе себя свой своя свои который которая которые которое этот
эта эти того тому этим этой этого очень можно нужно надо есть был была были
было будет будут если когда тогда потом просто только также тоже вообще
сейчас здесь там тут где куда откуда зачем почему какой какая какие каков
такой такая такие такое кто чем чего между после перед через более менее
около подскажите подскажи пожалуйста скажите скажи кто-нибудь знает ребята
народ привет всем бот мозг вопрос""".split())


# ------------------------------------------------------------ настройки
def enabled():
    return bool(store.settings().get("maxbot_src_on", True)) and bool(chats())


def chats():
    v = store.settings().get("maxbot_src_chats") or []
    if isinstance(v, str):
        v = [x for x in re.split(r"[,\s]+", v) if x]
    return [str(x).strip() for x in v if str(x).strip()]


def is_source(chat_id):
    return chat_id is not None and str(chat_id) in chats()


def scope():
    s = str(store.settings().get("maxbot_src_scope") or "all")
    return s if s in ("all", "same") else "all"


def parse_chat(s):
    """ссылка web.max.ru/-68414748216062 или ID -> ID (строкой)."""
    m = re.search(r"(-?\d{6,})\s*/?\s*$", str(s or "").strip())
    return m.group(1) if m else ""


def set_sources(ids, sc=None):
    """Сохранить список чатов-источников; новые — сразу читать историю."""
    clean = []
    for x in ids or []:
        c = parse_chat(x) or str(x).strip()
        if c and re.fullmatch(r"-?\d{3,}", c) and c not in clean:
            clean.append(c)
    old = set(chats())
    upd = {"maxbot_src_chats": clean}
    if sc in ("all", "same"):
        upd["maxbot_src_scope"] = sc
    store.update_settings(upd)
    for c in clean:
        if c not in old:
            start_sync(c, full=True)
    return clean


# ------------------------------------------------------------ индекс
def _path(chat_id):
    return store.p("maxsrc/%s.json" % re.sub(r"[^0-9-]", "", str(chat_id)))


def _stems(text):
    words = [w for w in re.findall(r"[а-яёa-z0-9]{2,}", str(text or "").lower()
                                   .replace("ё", "е")) if w not in _STOPW]
    try:
        import kb
        st = kb._stems(" ".join(w for w in words if not w.isdigit()))
        out = {x[:5] for x in st if len(x) >= 2}
    except Exception:
        out = {w[:5] for w in words if not w.isdigit()}
    out |= {w for w in words if w.isdigit() and len(w) >= 2}
    return out


def _load(chat_id):
    cid = str(chat_id)
    with _LOCK:
        ix = _IDX.get(cid)
        if ix is not None:
            return ix
        d = {}
        try:
            with open(_path(cid), "r", encoding="utf-8") as f:
                d = json.load(f)
        except Exception:
            d = {}
        ix = {"chat_id": cid, "title": d.get("title") or "",
              "msgs": list(d.get("msgs") or []),
              "complete": bool(d.get("complete")),
              "synced": float(d.get("synced") or 0),
              "full_ts": float(d.get("full_ts") or 0),
              "err": d.get("err") or "", "dirty": False}
        ix["msgs"].sort(key=lambda m: m.get("t") or 0)
        _build(ix)
        _IDX[cid] = ix
        return ix


def _build(ix):
    """Основы слов и обратный индекс «основа -> номера сообщений»."""
    ix["st"] = [_stems(m.get("x")) for m in ix["msgs"]]
    inv = {}
    for i, st in enumerate(ix["st"]):
        for s in st:
            inv.setdefault(s, []).append(i)
    ix["inv"] = inv
    ix["by_mid"] = {m.get("m"): i for i, m in enumerate(ix["msgs"]) if m.get("m")}


def _save(chat_id):
    cid = str(chat_id)
    with _LOCK:
        ix = _IDX.get(cid)
        if ix is None:
            return
        data = {"chat_id": cid, "title": ix["title"], "msgs": ix["msgs"],
                "complete": ix["complete"], "synced": ix["synced"],
                "full_ts": ix["full_ts"], "err": ix["err"]}
        ix["dirty"] = False
    try:
        os.makedirs(os.path.dirname(_path(cid)), exist_ok=True)
        tmp = _path(cid) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, _path(cid))
    except Exception as e:
        store.log("МАКС-ИСТОЧНИКИ", "не сохранил индекс чата %s: %s" % (cid, e))


def _rec(msg):
    """Сообщение МАКС -> запись индекса (или None — не текст/служебное)."""
    if not isinstance(msg, dict):
        return None
    body = msg.get("body") or {}
    text = " ".join(str(body.get("text") or "").split())
    if not text:
        return None
    try:
        import maxbot
        ts = maxbot._norm_ts(msg.get("timestamp"))
    except Exception:
        ts = float(msg.get("timestamp") or 0)
        ts = ts / 1000.0 if ts > 1e11 else ts
    snd = msg.get("sender") or {}
    name = (" ".join(x for x in (snd.get("first_name"), snd.get("last_name")) if x)
            or snd.get("name") or snd.get("username") or "участник")
    link = msg.get("link") or {}
    rmid = ""
    if isinstance(link, dict) and str(link.get("type") or "") == "reply":
        rmid = str(((link.get("message") or {}).get("mid")) or "")
    mid = str(body.get("mid") or msg.get("mid") or "")
    if not mid:
        mid = "%s|%d" % (int(ts), abs(hash(text)) % 10 ** 8)
    return {"m": mid, "t": round(ts, 3), "u": str(snd.get("user_id") or ""),
            "n": str(name)[:60], "x": text[:TEXT_MAX], "r": rmid}


def _bot_uid():
    try:
        import maxbot
        return str((maxbot._load().get("bot") or {}).get("user_id") or "")
    except Exception:
        return ""


def _add(ix, recs):
    """Добавить записи (без дублей по mid); вернуть число новых."""
    bot = _bot_uid()
    new = 0
    for r in recs:
        if not r or r["m"] in ix["by_mid"]:
            continue
        if bot and r["u"] == bot:
            continue                    # свои ответы бота — не источник
        ix["msgs"].append(r)
        ix["by_mid"][r["m"]] = -1
        new += 1
    if new:
        ix["msgs"].sort(key=lambda m: m.get("t") or 0)
        if len(ix["msgs"]) > MAX_MSGS:
            del ix["msgs"][:-MAX_MSGS]
        _build(ix)
        ix["dirty"] = True
    return new


def note(msg):
    """Живое сообщение из чата-источника — сразу в индекс (не в базу)."""
    rec = _rec(msg)
    cid = str(((msg or {}).get("recipient") or {}).get("chat_id") or "")
    if not rec or not cid or not is_source(cid):
        return False
    with _LOCK:
        ix = _load(cid)
        n = _add(ix, [rec])
    if n:
        try:
            import bgwork
            bgwork.debounce("maxsrc-" + cid, lambda: _save(cid), lambda: _save(cid), 30.0)
        except Exception:
            _save(cid)
    return bool(n)


# ------------------------------------------------------------ чтение истории
def start_sync(chat_id, full=False):
    cid = str(chat_id)
    with _LOCK:
        s = _SYNC.get(cid) or {}
        if s.get("running"):
            return False
        _SYNC[cid] = {"running": True, "full": bool(full), "pages": 0, "new": 0,
                      "err": "", "started": time.time()}
    threading.Thread(target=_sync, args=(cid, full), daemon=True,
                     name="maxsrc-" + cid).start()
    return True


def _sync(cid, full):
    import maxbot
    ix = _load(cid)
    st = _SYNC[cid]
    try:
        if not ix["title"]:
            try:
                ix["title"] = maxbot.chat_title(cid)
            except Exception:
                pass
        if full:
            with _LOCK:
                ix["complete"] = False
        newest = max((m.get("t") or 0 for m in ix["msgs"]), default=0)
        to = None
        key = "to"
        seen = set()
        while not _STOP.is_set():
            q = "/messages?chat_id=%s&count=%d" % (urllib.parse.quote(cid), PAGE)
            if to is not None:
                q += "&%s=%d" % (key, to)
            try:
                d = maxbot._call("GET", q)
            except Exception as e:
                t = str(e)
                if "403" in t:
                    t += " — у бота нет права читать этот чат (в группах бот должен " \
                         "быть администратором)"
                st["err"] = t[:200]
                break
            msgs = d.get("messages") if isinstance(d, dict) else None
            if not isinstance(msgs, list) or not msgs:
                with _LOCK:
                    ix["complete"] = True
                break
            uniq = [m for m in msgs if isinstance(m, dict) and
                    str((m.get("body") or {}).get("mid") or m.get("mid") or id(m)) not in seen]
            for m in uniq:
                seen.add(str((m.get("body") or {}).get("mid") or m.get("mid") or id(m)))
            if not uniq:
                if key == "to" and to is not None:
                    key = "from"        # параметр листания не сработал — другой
                    continue
                with _LOCK:
                    ix["complete"] = True
                break
            recs = [_rec(m) for m in uniq]
            with _LOCK:
                n = _add(ix, recs)
            st["pages"] += 1
            st["new"] += n
            oldest_raw = min((m.get("timestamp") for m in uniq
                              if isinstance(m.get("timestamp"), (int, float))), default=None)
            if not full and newest:
                ts = [r["t"] for r in recs if r]
                if ts and min(ts) <= newest:
                    break               # дошли до уже прочитанного — хвост сверен
            if len(msgs) < PAGE or oldest_raw is None:
                with _LOCK:
                    ix["complete"] = True
                break
            to = int(oldest_raw) - 1
            if st["pages"] % 10 == 0:
                _save(cid)
            time.sleep(PAGE_GAP)
        with _LOCK:
            ix["synced"] = time.time()
            ix["err"] = st["err"]
            if full and ix["complete"]:
                ix["full_ts"] = time.time()
        _save(cid)
        store.log("МАКС-ИСТОЧНИКИ", "чат «%s»: %s, страниц %d, новых сообщений %d, всего "
                  "в индексе %d%s" % (ix["title"] or cid, "прочитан целиком" if
                                      ix["complete"] else "прочитан частично",
                                      st["pages"], st["new"], len(ix["msgs"]),
                                      (" — " + st["err"]) if st["err"] else ""))
    except Exception as e:
        st["err"] = "%s: %s" % (type(e).__name__, str(e)[:160])
    finally:
        st["running"] = False
        st["done"] = time.time()


def tick():
    """Раз в 15 минут — сверить хвост всех чатов-источников; непрочитанные
    целиком — дочитать (зовётся из цикла бота)."""
    if time.time() - _LAST_TICK[0] < SYNC_EVERY or not enabled():
        return
    _LAST_TICK[0] = time.time()
    for cid in chats():
        ix = _load(cid)
        start_sync(cid, full=not ix["complete"])


def forget(chat_id):
    """Стереть индекс чата (данные бота, не база знаний)."""
    cid = str(chat_id)
    with _LOCK:
        _IDX.pop(cid, None)
    try:
        os.remove(_path(cid))
    except Exception:
        pass


# ------------------------------------------------------------ поиск ответа
def _is_q(text):
    return bool(_Q.search(str(text or "")))


def _glue(ix, i):
    """Ответ + его продолжение тем же автором подряд (до 3 минут)."""
    ms = ix["msgs"]
    a = ms[i]
    parts = [a["x"]]
    j = i + 1
    while j < len(ms) and len(" ".join(parts)) < 1500:
        b = ms[j]
        if b["u"] != a["u"] or b["t"] - ms[j - 1]["t"] > GLUE_S or _is_q(b["x"]):
            break
        parts.append(b["x"])
        j += 1
    return " ".join(parts)[:1600]


def _reactions(ix, i, asker=""):
    """Подтверждения/опровержения ответа i: ответы на него и реплики
    спросившего следом."""
    ms = ix["msgs"]
    a = ms[i]
    ok, bad = [], []
    for j in range(i + 1, len(ms)):
        b = ms[j]
        if b["t"] - a["t"] > CONFIRM_WIN:
            break
        to_it = b.get("r") == a["m"]
        from_asker = asker and b["u"] == asker and b["t"] - a["t"] < ANSWER_WIN
        if not (to_it or from_asker) or b["u"] == a["u"]:
            continue
        x = b["x"]
        if len(x) > 300:
            continue
        if _REFUTE.search(x) and not re.search(r"спасибо|помогл", x, re.I):
            bad.append((b["n"], x))
        elif _CONFIRM.search(x):
            ok.append((b["n"], x))
        if len(ok) + len(bad) >= 4:
            break
    return ok, bad


def _cands(ix, q):
    """Кандидаты-ответы чата на вопрос q (основы). -> [dict]"""
    ms, sts, inv = ix["msgs"], ix["st"], ix["inv"]
    hits = {}
    for s in q:
        for i in inv.get(s, ()):
            hits[i] = hits.get(i, 0) + 1
    need = min(2, len(q))
    out = []
    t0 = ms[0]["t"] if ms else 0
    span = max(1.0, (ms[-1]["t"] - t0) if ms else 1.0)
    for i, h in hits.items():
        if h < need:
            continue
        m = ms[i]
        sim = h / float(len(q))
        if _is_q(m["x"]) and sim >= 0.5:
            # похожий вопрос: ответы — reply на него и реплики других следом
            got = 0
            for j in range(i + 1, len(ms)):
                a = ms[j]
                if a["t"] - m["t"] > ANSWER_WIN or got >= 8:
                    break
                if a["u"] == m["u"]:
                    if _SOLVED.search(a["x"]) and not _is_q(a["x"]):
                        out.append({"i": j, "q": i, "sim": sim, "kind": "self"})
                        got += 1
                    elif _is_q(a["x"]) and j > i + 1:
                        break           # спросивший задал уже другой вопрос
                    continue
                direct = a.get("r") == m["m"]
                if a.get("r") and not direct and a["r"] in ix["by_mid"]:
                    continue            # ответ на другое сообщение
                out.append({"i": j, "q": i, "sim": sim,
                            "kind": "reply" if direct else "next"})
                got += 1
        elif not _is_q(m["x"]) and sim >= 0.6 and len(sts[i]) >= 3:
            out.append({"i": i, "q": None, "sim": sim, "kind": "fact"})
    # оценка
    for c in out:
        a = ms[c["i"]]
        x = a["x"]
        if _NOANS.match(x) or sum(ch.isalpha() for ch in x) < 4:
            c["score"] = -9
            continue
        s = 0.0
        if c["kind"] == "fact":
            s += 1.5 * c["sim"]
        else:
            s += 2.0 * c["sim"]
            s += {"reply": 1.0, "self": 1.5, "next": 0.3}[c["kind"]]
            if c["kind"] == "next" and a["t"] - ms[c["q"]]["t"] < 1800:
                s += 0.2
        qs = q | (sts[c["q"]] if c["q"] is not None else set())
        rel = len(qs & sts[c["i"]]) / float(max(1, len(q)))
        s += 0.6 * min(1.0, rel)
        asker = ms[c["q"]]["u"] if c["q"] is not None else ""
        ok, bad = _reactions(ix, c["i"], asker)
        s += 1.5 * min(2, len(ok)) - 2.0 * min(2, len(bad))
        s += 0.3 * (a["t"] - t0) / span
        if _is_q(x):
            s -= 1.0                    # встречный вопрос — не ответ
        c.update(score=round(s, 2), ok=ok, bad=bad)
    # согласие: разные люди ответили одно и то же
    good = [c for c in out if c.get("score", -9) > 0]
    for c in good:
        sc = sts[c["i"]]
        if len(sc) < 2:
            continue
        agree = set()
        for d in good:
            if d is c or ms[d["i"]]["u"] == ms[c["i"]]["u"]:
                continue
            sd = sts[d["i"]]
            if sd and len(sc & sd) / float(min(len(sc), len(sd))) >= 0.5:
                agree.add(ms[d["i"]]["u"])
        if agree:
            c["score"] += 0.5 * min(2, len(agree))
            c["agree"] = len(agree)
    return [c for c in out if c.get("score", -9) > 0]


def find(question, chat_ids=None):
    """Пройти по всей истории чатов-источников -> лучшие кандидаты
    [(score, chat_id, cand)] по убыванию."""
    q = _stems(question)
    if not q:
        return []
    res = []
    for cid in (chat_ids if chat_ids is not None else chats()):
        ix = _load(cid)
        if not ix["msgs"]:
            continue
        with _LOCK:
            for c in _cands(ix, q):
                res.append((c["score"], cid, c))
    res.sort(key=lambda x: -x[0])
    return res


def _date(ts):
    try:
        return time.strftime("%d.%m.%Y", time.localtime(ts))
    except Exception:
        return ""


def _short(t, n):
    t = " ".join(str(t or "").split())
    return t if len(t) <= n else t[:n].rsplit(" ", 1)[0] + "…"


def answer_for(question, asked_in=None, any_scope=False):
    """Ответ из чатов-источников. -> {"level": high|medium, "text", ...} | None
    any_scope — проверка из панели владельца (без ограничения «тот же чат»)."""
    if not enabled():
        return None
    ids = chats()
    if scope() == "same" and not any_scope:
        ids = [c for c in ids if asked_in is not None and c == str(asked_in)]
    if not ids:
        return None
    t0 = time.time()
    found = find(question, ids)
    if not found:
        return None
    best_s, cid, c = found[0]
    if best_s < MEDIUM:
        return None
    ix = _load(cid)
    ms = ix["msgs"]
    a = ms[c["i"]]
    title = ix["title"] or ("чат %s" % cid)
    level = "high" if best_s >= HIGH else "medium"
    head = ("📚 Из чата «%s»:" if level == "high" else
            "📚 Возможно, подойдёт ответ из чата «%s»:") % title
    lines = [head, _glue(ix, c["i"]), "— %s, %s" % (a["n"], _date(a["t"]))]
    if c["q"] is not None:
        qm = ms[c["q"]]
        lines.append("(на вопрос %s: «%s»)" % (qm["n"], _short(qm["x"], 160)))
    if c.get("ok"):
        lines.append("✓ подтвердили: %s" % "; ".join(
            "%s — «%s»" % (n, _short(x, 60)) for n, x in c["ok"][:2]))
    if c.get("agree"):
        lines.append("✓ так же ответили ещё %d чел." % c["agree"])
    # другой вариант — если близко по весу и о другом
    for s2, cid2, c2 in found[1:4]:
        if s2 < max(MEDIUM, best_s - 0.6):
            break
        ix2 = _load(cid2)
        a2 = ix2["msgs"][c2["i"]]
        if a2["m"] == a["m"] or (cid2 == cid and a2["u"] == a["u"] and
                                abs(a2["t"] - a["t"]) < GLUE_S):
            continue
        if len(ix["st"][c["i"]] & ix2["st"][c2["i"]]) / float(
                max(1, min(len(ix["st"][c["i"]]), len(ix2["st"][c2["i"]])))) >= 0.5:
            continue                    # то же самое другими словами
        lines.append("Другой вариант (%s, %s): %s" % (a2["n"], _date(a2["t"]),
                                                     _short(_glue(ix2, c2["i"]), 400)))
        break
    if not ix["complete"]:
        lines.append("(историю этого чата я ещё дочитываю — в индексе %d сообщений)"
                     % len(ms))
    out = {"level": level, "score": best_s, "chat_id": cid, "title": title,
           "text": "\n".join(lines), "author": a["n"], "ts": a["t"],
           "kind": c["kind"], "ms": int((time.time() - t0) * 1000),
           "searched": sum(len(_load(x)["msgs"]) for x in ids)}
    store.log("МАКС-ИСТОЧНИКИ", "ответ из чата «%s» (%s, вес %.1f, %s; просмотрено %d "
              "сообщений за %d мс)" % (title, level, best_s, c["kind"],
                                       out["searched"], out["ms"]))
    return out


def status():
    out = []
    for cid in chats():
        ix = _load(cid)
        s = dict(_SYNC.get(cid) or {})
        out.append({"chat_id": cid, "title": ix["title"], "msgs": len(ix["msgs"]),
                    "complete": ix["complete"], "synced": ix["synced"],
                    "full_ts": ix["full_ts"], "err": ix["err"] or s.get("err") or "",
                    "running": bool(s.get("running")), "pages": s.get("pages") or 0,
                    "new": s.get("new") or 0,
                    "oldest": ix["msgs"][0]["t"] if ix["msgs"] else 0})
    return {"on": bool(store.settings().get("maxbot_src_on", True)),
            "scope": scope(), "chats": out}
