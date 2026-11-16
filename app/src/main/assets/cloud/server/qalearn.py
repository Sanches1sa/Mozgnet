# -*- coding: utf-8 -*-
# ===========================================================================
# qalearn.py — ОБУЧЕНИЕ НА ВОПРОСАХ И ОТВЕТАХ (v3.28, только stdlib)
# ---------------------------------------------------------------------------
# Заявка владельца: «нейросеть должна обучаться на ответах на вопросы и на
# вопросы. Только сделай честно». Что значит «честно» здесь:
#   * ВОПРОСЫ учат всегда: какие темы спрашивают, как часто, какими
#     зонами отвечено, на какие ответа не нашлось (они уходят любопытству
#     дочитать). Это факт — люди спросили.
#   * ОТВЕТЫ учат, только если они на что-то опираются: база знаний,
#     интернет-источники, Википедия, расчёт. Свои догадки, фразы сети и
#     «речи из словаря» как знание НЕ запоминаются — иначе сеть училась бы
#     на собственных выдумках и уверовала бы в них.
#   * ПОДТВЕРЖДЁННЫМ ответ становится только после оценки человека (👍 в
#     чате) или исправления владельца («правильный ответ: …»). Лишь такие
#     ответы мозг повторяет из памяти на тот же вопрос — с пометкой.
#   * 👎 честно запоминается: этот ответ больше не выдаётся из памяти, а
#     если конвейер соберёт его снова — к ответу добавится предупреждение;
#     вопрос уходит любопытству.
# Нейросеть материалов (mozgnet) получает подтверждённые пары
# «Вопрос/Ответ» как материал; исправление владельца — ещё и статья в базе
# знаний (это его знание, и KB находит его по формулировке вопроса).
# Файл: data/qa_learn.json.
# ===========================================================================
import hashlib
import re
import threading
import time

import store

_FILE = "qa_learn.json"
_LOCK = threading.RLock()
_DB = {}
_STATE = {"loaded": False, "dirty": False, "saved": 0.0}
MAX_PAIRS = 4000
MAX_QUESTIONS = 6000
SIM_RECALL = 0.78             # похожесть вопросов (Жаккар по основам слов)
SAVE_EVERY = 30.0

# зоны, чьи ответы опираются на источник или расчёт (их можно запомнить)
GROUNDED = frozenset(["База знаний", "Интернет-источники", "Википедия",
                      "Математика"])
# зоны с меняющимся ответом — из памяти не повторяем никогда
_VOLATILE = frozenset(["Навык", "Облако", "Плата-12", "Мысли платы",
                       "Документы", "Воображение", "Генератор",
                       "Любопытство", "Память",
                       # v3.135: «Разговор» — мозг и своя LLM просто говорят,
                       # без фактов: даже с 👍 это не знание для бота МАКС
                       "Разговор"])
_NOANSWER = re.compile(r"не знаю|не нашл|не нашёл|нет ответа|пока не знаю|"
                       r"не смогла|не получилось|не удалось|честно не",
                       re.I)


def _stems(text):
    """Содержательные основы вопроса (как в поиске по базе знаний)."""
    try:
        import kb
        st = frozenset(s for s in kb._stems(text)
                       if s not in kb._STOP_STEMS and len(s) >= 2)
        if not st:
            # v3.96: реплика из одних «служебных» слов («привет», «как
            # дела») — ключом служат сами слова, чтобы ответ на неё можно
            # было выучить («правильный ответ: …»)
            st = frozenset("~" + w for w in re.findall(
                r"[а-яёa-z0-9]{2,}", str(text or "").lower().replace("ё", "е")))
        return st
    except Exception:
        return frozenset(w for w in re.findall(r"[а-яёa-z0-9]{3,}",
                                               str(text or "").lower()))


def _key(stems):
    return " ".join(sorted(stems))[:300]


def _ahash(answer):
    t = re.sub(r"\s+", " ", str(answer or "")).strip().lower()[:400]
    return hashlib.sha1(t.encode("utf-8")).hexdigest()[:16]


def _load():
    if _STATE["loaded"]:
        return _DB
    d = store.load_json(_FILE, None) or {}
    _DB.clear()
    _DB.update({"pairs": d.get("pairs") or {},
                "questions": d.get("questions") or {},
                "stats": d.get("stats") or {},
                "suggest": d.get("suggest") or {},
                "rep": d.get("rep") or {}})
    for k in ("asked", "learned", "confirmed", "rejected", "corrected",
              "recalled", "skipped"):
        _DB["stats"].setdefault(k, 0)
    _STATE["loaded"] = True
    return _DB


def _trim():
    for name, cap in (("pairs", MAX_PAIRS), ("questions", MAX_QUESTIONS)):
        tb = _DB[name]
        if len(tb) > cap:
            # первыми уходят старые и неподтверждённые
            order = sorted(tb, key=lambda k: (bool(tb[k].get("verified")),
                                              tb[k].get("ts") or 0))
            for k in order[:len(tb) - cap]:
                tb.pop(k, None)


def save(force=False):
    with _LOCK:
        if not _STATE["loaded"] or not (_STATE["dirty"] or force):
            return False
        if not force and time.time() - _STATE["saved"] < SAVE_EVERY:
            return False
        _trim()
        snap = {"pairs": dict(_DB["pairs"]),
                "questions": dict(_DB["questions"]),
                "stats": dict(_DB["stats"]),
                "suggest": dict(_DB.get("suggest") or {}),
                "rep": dict(_DB.get("rep") or {})}
        _STATE["dirty"] = False
        _STATE["saved"] = time.time()
    store.save_json(_FILE, snap)
    return True


def _mark():
    _STATE["dirty"] = True


def _feed_net(q, a, src):
    try:
        import brain
        brain.mn_learn("Вопрос: %s\nОтвет: %s" % (q[:500], a[:2500]),
                       title=q[:60], src=src)
    except Exception:
        pass


# ------------------------------- обучение -----------------------------------
def note(q, a, zone, who="чат"):
    """После КАЖДОГО ответа: вопрос учит всегда, ответ — если опирается на
    источник/расчёт. Возврат: что выучено ('question'|'pair'|'')."""
    q = str(q or "").strip()
    st = _stems(q)
    if len(st) < 1 or len(q) > 2000:
        return ""
    k = _key(st)
    a = str(a or "").strip()
    answered = bool(a) and not _NOANSWER.search(a[:300])
    with _LOCK:
        db = _load()
        rec = db["questions"].get(k) or {"q": q[:300], "n": 0, "zones": {},
                                         "answered": 0}
        rec["n"] += 1
        rec["q"] = q[:300]
        rec["ts"] = time.time()
        rec["who"] = str(who)[:40]
        rec["zones"][zone or "?"] = rec["zones"].get(zone or "?", 0) + 1
        if answered:
            rec["answered"] = rec.get("answered", 0) + 1
        db["questions"][k] = rec
        db["stats"]["asked"] += 1
        got = "question"
        if answered and zone in GROUNDED and len(a) >= 20:
            p = db["pairs"].get(k)
            if p is None or not p.get("verified"):
                old_bad = (p or {}).get("bad_hashes") or []
                if _ahash(a) not in old_bad:
                    db["pairs"][k] = {"q": q[:300], "a": a[:6000],
                                      "zone": zone, "ts": time.time(),
                                      "verified": False,
                                      "good": (p or {}).get("good", 0),
                                      "bad": (p or {}).get("bad", 0),
                                      "bad_hashes": old_bad, "src": "конвейер"}
                    db["stats"]["learned"] += 1
                    got = "pair"
        elif answered:
            db["stats"]["skipped"] += 1       # догадки/фразы сети — не знание
        _mark()
    save()
    return got


def rate(q, a, v, zone="", who="владелец"):
    """Оценка человека: v>0 — ответ верный (подтверждён), v<0 — неверный."""
    q, a = str(q or "").strip(), str(a or "").strip()
    st = _stems(q)
    if not st or not a:
        return {"ok": False, "error": "нет вопроса или ответа"}
    k = _key(st)
    with _LOCK:
        db = _load()
        p = db["pairs"].get(k) or {"q": q[:300], "a": a[:6000], "zone": zone,
                                   "good": 0, "bad": 0, "bad_hashes": [],
                                   "verified": False, "src": "оценка"}
        if v > 0:
            p["a"], p["zone"] = a[:6000], zone or p.get("zone")
            p["good"] = int(p.get("good") or 0) + 1
            p["verified"] = True
            p["by"] = str(who)[:40]
            db["stats"]["confirmed"] += 1
        else:
            p["bad"] = int(p.get("bad") or 0) + 1
            h = _ahash(a)
            if h not in p.setdefault("bad_hashes", []):
                p["bad_hashes"] = (p["bad_hashes"] + [h])[-20:]
            if _ahash(p.get("a")) == h:
                p["verified"] = False
            db["stats"]["rejected"] += 1
        p["ts"] = time.time()
        db["pairs"][k] = p
        _mark()
    save(force=True)
    if v > 0:
        _feed_net(q, a, "ответ подтверждён")
        return {"ok": True, "verified": True}
    try:
        import curiosity
        curiosity.want(q, why="ответ отмечен как неверный")
    except Exception:
        pass
    return {"ok": True, "verified": False}


def correct(q, a, who="владелец", trusted=True):
    """«Правильный ответ: …» — исправление. От владельца (trusted) — сразу
    подтверждённое знание (+ статья в базе знаний); от собеседника в МАКС —
    только предложение, ждёт 👍 владельца в панели."""
    q, a = str(q or "").strip(), str(a or "").strip()
    st = _stems(q)
    if not st or len(a) < 2:
        return {"ok": False, "error": "нет вопроса или ответа"}
    k = _key(st)
    with _LOCK:
        db = _load()
        p = db["pairs"].get(k) or {"good": 0, "bad": 0, "bad_hashes": []}
        if trusted:
            p.update({"q": q[:300], "a": a[:6000], "zone": "Обучение",
                      "verified": True, "src": "исправление владельца",
                      "good": int(p.get("good") or 0) + 1, "by": who,
                      "ts": time.time()})
            db["pairs"][k] = p
        else:
            sug = db.setdefault("suggest", {})
            sug[k] = {"q": q[:300], "a": a[:3000], "by": str(who)[:40],
                      "ts": time.time()}
        db["stats"]["corrected"] += 1
        _mark()
    save(force=True)
    if trusted:
        _feed_net(q, a, "ответ владельца")
        try:
            import kb
            kb.add_article("Ответ: %s" % q[:160],
                           "Вопрос: %s\nОтвет: %s" % (q, a),
                           source="ответ владельца")
        except Exception:
            pass
    return {"ok": True, "trusted": bool(trusted)}


def learn_external(q, a, src, verified=False):
    """v3.29: ЕДИНЫЙ МОЗГ — знание от платы. verified — ответ подтверждён
    человеком на плате («верно» / «правильный ответ»); иначе — пара с
    опорой (её файл, память кода), которую облако повторит только после 👍.
    Подтверждённое облаком своё — не перетирается. Возврат: принято ли."""
    q, a = str(q or "").strip(), str(a or "").strip()
    st = _stems(q)
    if not st or len(a) < 2:
        return False
    if _bad_pair(q, a, src):            # v3.144: вопрос вместо ответа
        return False
    k = _key(st)
    with _LOCK:
        db = _load()
        p = db["pairs"].get(k)
        if p and p.get("verified") and not str(p.get("src") or "") \
                .startswith("плата"):
            return False
        if p and _ahash(a) in (p.get("bad_hashes") or []):
            return False          # этот ответ здесь уже отклонили
        db["pairs"][k] = {"q": q[:300], "a": a[:6000], "zone": "Обучение",
                          "ts": time.time(), "verified": bool(verified),
                          "good": max(1 if verified else 0,
                                      int((p or {}).get("good") or 0)),
                          "bad": int((p or {}).get("bad") or 0),
                          "bad_hashes": (p or {}).get("bad_hashes") or [],
                          "src": str(src)[:80]}
        db["stats"]["learned"] += 1
        _mark()
    save()
    if verified:
        _feed_net(q, a, "ответ платы")
    return True


# ----------------- v3.30: ОПЫТ ОБУЧЕНИЯ — репутация источников ---------------
def _domain(url):
    import re as _re
    h = _re.sub(r"^[a-z]+://", "", str(url or "").lower()).split("/")[0]
    return _re.sub(r"^(www|m)\.", "", h.split(":")[0])


def rep_update(urls, v):
    """👍/👎 ответа — опыт для сайтов, из которых он собран."""
    doms = {_domain(u) for u in (urls or []) if u}
    doms.discard("")
    if not doms:
        return 0
    with _LOCK:
        db = _load()
        rep = db.setdefault("rep", {})
        for d in doms:
            rep[d] = max(-5.0, min(10.0, float(rep.get(d) or 0) +
                                   (1.0 if v > 0 else -1.0)))
        _mark()
    save(force=True)
    return len(doms)


def rep(url_or_domain):
    with _LOCK:
        return float((_load().get("rep") or {}).get(
            _domain(url_or_domain) if "/" in str(url_or_domain) or
            "." in str(url_or_domain) else url_or_domain) or 0)


def export_verified(since=0.0):
    """v3.29: подтверждённые пары (для плат): новее since."""
    with _LOCK:
        db = _load()
        out = [{"q": p.get("q"), "a": p.get("a"), "src": p.get("src") or "",
                "ts": p.get("ts") or 0}
               for p in db["pairs"].values()
               if p.get("verified") and float(p.get("ts") or 0) > since and
               int(p.get("good") or 0) > int(p.get("bad") or 0) and
               p.get("zone") not in _VOLATILE and
               not _bad_pair(p.get("q"), p.get("a"), p.get("src"))]
    out.sort(key=lambda x: x["ts"])
    return out


# ------------------------------- память -------------------------------------
def _sim(a, b):
    if not a or not b:
        return 0.0
    return len(a & b) / float(len(a | b))


def _bad_pair(q, a, src=""):
    """v3.144: пара, которая ответом не является: «ответ» — вопрос или эхо
    вопроса, либо пара из «обучения всех сетей» (learnall v3.143 клал
    заголовок статьи вопросом, а её текст — «подтверждённым ответом»)."""
    if "облако-материалы" in str(src or ""):
        return True
    try:
        import textutils
        return textutils.not_an_answer(q, a)
    except Exception:
        return False


def purge_bad():
    """v3.144: убрать из памяти пар всё, что не ответ (см. _bad_pair).
    Возврат: сколько убрано."""
    n = 0
    with _LOCK:
        db = _load()
        for k in list(db["pairs"]):
            p = db["pairs"][k]
            if _bad_pair(p.get("q"), p.get("a"), p.get("src")):
                db["pairs"].pop(k, None)
                n += 1
        if n:
            db["stats"]["purged"] = int(db["stats"].get("purged") or 0) + n
            _mark()
    if n:
        save(force=True)
        try:
            store.log("ПАМЯТЬ", "убрано пар «вопрос вместо ответа»: %d" % n)
        except Exception:
            pass
    return n


def recall(q):
    """Подтверждённый ответ на тот же (очень похожий) вопрос | None."""
    st = _stems(q)
    if not st:
        return None
    k = _key(st)
    with _LOCK:
        db = _load()
        best, bs = None, 0.0
        p = db["pairs"].get(k)
        if p and p.get("verified"):
            best, bs = p, 1.0
        elif len(st) < 2:
            return None          # одно слово — только точное совпадение
        else:
            for p in db["pairs"].values():
                if not p.get("verified") or p.get("zone") in _VOLATILE:
                    continue
                s = _sim(st, _stems(p.get("q")))
                if s > bs:
                    best, bs = p, s
        if not best or bs < SIM_RECALL or best.get("zone") in _VOLATILE or \
                int(best.get("good") or 0) <= int(best.get("bad") or 0):
            return None
        if _bad_pair(best.get("q"), best.get("a"), best.get("src")):
            return None                  # v3.144: вопрос ответом не бывает
        db["stats"]["recalled"] += 1
        _mark()
        return dict(best, sim=round(bs, 2))


def related(q, k=2, min_sim=0.3):
    """v3.99: ПАМЯТЬ ДИАЛОГОВ для ответа «мозг + LLM»: подтверждённые пары
    (👍 / «правильный ответ: …»), ПОХОЖИЕ на вопрос, но не настолько, чтобы
    отдать их готовым ответом (recall). -> [{q, a, sim}] лучшие первыми."""
    st = _stems(q)
    if len(st) < 1:
        return []
    out = []
    with _LOCK:
        db = _load()
        for p in db["pairs"].values():
            if not p.get("verified") or p.get("zone") in _VOLATILE or \
                    int(p.get("good") or 0) <= int(p.get("bad") or 0) or \
                    _bad_pair(p.get("q"), p.get("a"), p.get("src")):
                continue
            ps = _stems(p.get("q"))
            s_ = _sim(st, ps)
            # одно общее смысловое слово при коротком вопросе — тоже память
            if s_ < min_sim and not (len(st & ps) >= 1 and len(st) <= 2 and
                                     s_ >= 0.2):
                continue
            out.append({"q": str(p.get("q") or ""), "a": str(p.get("a") or ""),
                        "sim": round(s_, 2)})
    out.sort(key=lambda x: -x["sim"])
    return out[:max(1, int(k))]


def was_rejected(q, a):
    """Этот ответ на этот вопрос уже отмечали как неверный?"""
    st = _stems(q)
    if not st or not a:
        return False
    with _LOCK:
        p = _load()["pairs"].get(_key(st))
        return bool(p and _ahash(a) in (p.get("bad_hashes") or []))


def suggest_apply(k, accept):
    """Владелец проверил исправление собеседника: принять — знание."""
    with _LOCK:
        db = _load()
        s = (db.get("suggest") or {}).pop(str(k), None)
        if s:
            _mark()
    if not s:
        return {"ok": False, "error": "предложение не найдено"}
    save(force=True)
    if accept:
        return correct(s["q"], s["a"], who="владелец (проверил %s)"
                       % s.get("by", "?"), trusted=True)
    return {"ok": True, "accepted": False}


def stats():
    with _LOCK:
        db = _load()
        pairs = db["pairs"].values()
        qs = db["questions"]
        top = sorted(qs.values(), key=lambda r: -r.get("n", 0))[:8]
        unanswered = [r for r in sorted(qs.values(),
                                        key=lambda r: -(r.get("ts") or 0))
                      if not r.get("answered")][:8]
        return {"questions": len(qs), "pairs": len(db["pairs"]),
                "verified": sum(1 for p in pairs if p.get("verified")),
                "suggest": len(db.get("suggest") or {}),
                "rep_top": sorted(((d, r) for d, r in (db.get("rep") or {})
                                   .items()), key=lambda x: -x[1])[:10],
                "suggest_list": [dict(v, k=k) for k, v in sorted(
                    (db.get("suggest") or {}).items(),
                    key=lambda kv: -(kv[1].get("ts") or 0))[:20]],
                "counters": dict(db["stats"]),
                "top": [{"q": r["q"], "n": r["n"]} for r in top],
                "unanswered": [{"q": r["q"], "n": r["n"]}
                               for r in unanswered]}


def consolidate():
    """v3.133: на вдохе общего дыхания — подрезать память пар (старые и
    неподтверждённые уходят первыми) и сохранить. Сами ответы не меняются."""
    with _LOCK:
        _load()
        _trim()
        _STATE["dirty"] = True
    save()
