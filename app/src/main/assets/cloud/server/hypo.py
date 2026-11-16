# -*- coding: utf-8 -*-
# ===========================================================================
# hypo.py — СМЕЛОСТЬ ПРЕДПОЛОЖЕНИЙ (v3.11)
# ---------------------------------------------------------------------------
# Заявка владельца: «Сделай так, чтобы мозг не боялся делать предположения».
#
# Раньше, если точного знания не было (KB ниже порога, сеть молчала,
# Википедия недоступна), мозг честно говорил «не знаю» — и слабые улики
# пропадали зря: недобор по tf-idf, отдалённые ассоциации сети, одиночные
# совпадения в заметках. Это и был «страх предположений»: лучше промолчать,
# чем догадаться.
#
# ТЕПЕРЬ (перед честным «не знаю») мозг собирает СЛАБЫЕ УЛИКИ и строит
# ЧЕСТНУЮ ГИПОТЕЗУ:
#   1) похожая статья базы знаний (совпадение ниже рабочего порога 2.0);
#   2) ассоциации сети материалов: разрядка активации от запроса — какие
#      узлы зажигаются РЯДОМ и в каких предложениях материалов они встречались;
#   3) заметки владельца (даже одно совпадение по основе слова).
#
# ПРАВИЛА ЧЕСТНОСТИ (гипотеза — не знание):
#   - каждая догадка помечается: «предполагаю, это гипотеза, не факт»;
#   - назван источник: что именно «всплыло» и откуда;
#   - уверенность честная: низкая/средняя (высокую слабые улики не дают);
#   - гипотеза ЖУРНАЛИТСЯ (hypotheses.json) и уходит нейронам-любопыткам
#     (curiosity.want) — мозг сам читает статью и заменяет догадку знанием;
#   - владелец отвечает «верно»/«неверно»: подтверждённая догадка
#     ВЫУЧИВАЕТСЯ в сеть материалов (смелость окупается), опровергнутая
#     становится поводом дочитать статью.
# Выключается настройкой hypo_on; сеть материалов догадками НЕ засоряется
# (гипотеза становится материалом только после подтверждения владельцем).
# ===========================================================================
import re
import threading
import time

import kb
import store
import textutils

_LOCK = threading.RLock()
_PATH = "hypotheses.json"
_MAX_LIST = 60

_WORD_RE = re.compile(r"[а-яёa-z0-9]{3,}")

# уверенность: слабые улики высокую не дают — и не должны
_CONF_WORD = ((0.62, "средняя"), (0.0, "низкая"))


def _load():
    d = store.load_json(_PATH, None)
    if not isinstance(d, dict):
        d = {}
    d.setdefault("list", [])
    d.setdefault("made", 0)
    d.setdefault("confirmed", 0)
    d.setdefault("denied", 0)
    return d


def _save(d):
    with _LOCK:
        store.save_json(_PATH, d)


def _cut(t, n):
    """Обрезка по границе слова с «…» (раньше рубило посреди фразы)."""
    t = re.sub(r"\s+", " ", str(t or "")).strip()
    if len(t) <= n:
        return t
    c = t[:n]
    sp = c.rfind(" ")
    return (c[:sp] if sp > n * 0.6 else c).rstrip(" ,;:—-") + "…"


def _words_for(stems, texts):
    """Основа слова («разде») -> настоящее слово из материалов («раздел»)."""
    want, out = set(stems), {}
    for t in texts:
        for w in _WORD_RE.findall(str(t or "").lower()):
            if len(w) < 4:
                continue
            for st in (textutils.stem(w), w[:5], w[:4]):
                if st in want and st not in out:
                    out[st] = w
                    break
        if len(out) == len(want):
            break
    return out


def _stems(text):
    out = []
    for w in _WORD_RE.findall(str(text or "").lower()):
        st = textutils.stem(w)
        if st and st not in out:
            out.append(st)
    return out


# ------------------------------ сбор улик ------------------------------------
def gather(topic):
    """Собрать СЛАБЫЕ улики по теме (точных знаний нет — иначе зона KB или
    сеть уже ответили бы). Возврат:
    {conf, why: [..], guess: str, src: kb|assoc|notes|none, topic}
    или None, если улик меньше порога (тогда честное «не знаю»)."""
    topic = re.sub(r"\s+", " ", str(topic or "")).strip(" ?.!«»\"'")[:120]
    if len(topic) < 2:
        return None
    conf = 0.0
    why = []
    guess = ""
    src = "none"

    # 1) похожая статья KB (слабое совпадение — НИЖЕ рабочего порога 2.0)
    weak_hit, weak_score = None, 0.0
    try:
        r = kb.search(topic, limit=3)
        for h in (r.get("results") or []):
            sc = float(h.get("score") or 0)
            if 0 < sc < 2.0 and sc > weak_score:
                weak_hit, weak_score = h, sc
    except Exception:
        weak_hit = None
    if weak_hit:
        conf += 0.35 if weak_score >= 1.2 else 0.2
        why.append("в базе знаний похожая статья «%s» (совпадение слабое, "
                   "%.1f из 2.0)" % (weak_hit.get("title", "?"), weak_score))
        src = "kb"

    # 2) ассоциации сети материалов: что зажигается РЯДОМ с темой
    assoc, assoc_sents = [], []
    try:
        import brain
        qs = set(_stems(topic))
        with brain._MN_LOCK:
            m = brain.mn()
            act = m.spread(qs)
            assoc = [st for st, _a in sorted(act.items(), key=lambda kv: -kv[1])
                     if st not in qs and len(st) >= 4][:8]
            # предложения материалов, где тема и ассоциация встретились вместе
            if qs and assoc:
                a_set = set(assoc)
                for s in m.sents:
                    st_in = set(s.get("tf") or {})
                    if st_in & qs and st_in & a_set:
                        assoc_sents.append(m._sent_clean(s.get("t")))
                        if len(assoc_sents) >= 3:
                            break
    except Exception:
        assoc, assoc_sents = [], []
    if assoc_sents:
        conf += 0.3
        try:
            pool = list(assoc_sents)
            with brain._MN_LOCK:
                pool += [x.get("t") for x in brain.mn().sents[:4000]]
            real = _words_for(assoc[:5], pool)
        except Exception:
            real = {}
        why.append("сеть материалов вспоминает рядом: %s" %
                   ", ".join(real.get(a, a) for a in assoc[:5]))
        if src == "none":
            src = "assoc"
    elif assoc:
        conf += 0.12
        why.append("узлы сети подсказывают тему рядом: %s" %
                   ", ".join(assoc[:5]))

    # 3) заметки владельца (даже одно совпадение по основе)
    note_hit = None
    try:
        tset = set(_stems(topic))
        for n in store.notes():
            if tset & set(_stems(n.get("text") or "")):
                note_hit = str(n.get("text") or "").strip()
                break
    except Exception:
        note_hit = None
    if note_hit:
        conf += 0.25
        why.append("в заметках владельца встречалось: «%s»" %
                   note_hit[:90])

    # догадка — из самой сильной улики
    if weak_hit and weak_score >= (0.9 if not assoc_sents else 1.3):
        snip = " ".join(str(weak_hit.get("snippet") or "").split())
        guess = ("Возможно, ближе всего статья «%s»: %s"
                 % (weak_hit.get("title", "?"), _cut(snip or "…", 260)))
    elif assoc_sents:
        guess = ("В материалах, которые я уже видел, родственное звучало "
                 "так: «%s»" % _cut(assoc_sents[0], 240))
    elif weak_hit:
        snip = " ".join(str(weak_hit.get("snippet") or "").split())
        guess = ("Возможно, это где-то рядом со статьёй «%s»: %s"
                 % (weak_hit.get("title", "?"), _cut(snip or "…", 220)))
    elif note_hit:
        guess = "Из заметок всплывает: «%s»" % _cut(note_hit, 200)
    elif assoc:
        guess = ("Скорее всего, это где-то рядом с темами: %s."
                 % ", ".join(assoc[:5]))
    else:
        return None                     # улик нет вообще — не догадываемся

    try:                                   # v3.132: смелость дышит ±10 %
        import breath as _brh
        conf = _brh.mod("Смелость предположений", conf, 0.10)
    except Exception:
        pass
    conf = round(min(0.8, conf), 2)
    if conf < 0.2:
        return None
    return {"topic": topic, "conf": conf, "why": why, "guess": guess,
            "src": src, "assoc": assoc}


def _conf_word(conf):
    for lim, w in _CONF_WORD:
        if conf >= lim:
            return w
    return "низкая"


# --------------------------- построение гипотезы ------------------------------
def build(text):
    """Построить и высказать гипотезу (зона «Предположение»).
    Возврат {id, topic, conf, reply} или None («не знаю» остаётся честным)."""
    if not store.settings().get("hypo_on"):
        return None
    g = gather(text)
    if not g:
        return None
    topic = g["topic"]
    with _LOCK:
        d = _load()
        # не дублируем: та же тема уже ждёт проверки — оживим запись
        # (совпадение основ не менее 60% с обеих сторон, иначе это другая тема)
        ts_now = time.time()
        dup = None
        tset = set(_stems(topic))
        for h in d["list"]:
            if h.get("status") != "ждёт проверки":
                continue
            hset = set(_stems(h.get("topic") or ""))
            if not hset or not tset:
                continue
            common = len(hset & tset)
            if common and common >= 0.6 * min(len(hset), len(tset)):
                dup = h
                break
        if dup:
            dup["ts"] = ts_now
            hid = dup["id"]
            d["made"] += 0            # повтор не считаем новой догадкой
        else:
            hid = "h%d" % int(ts_now * 1000 % 1e9)
            d["list"].insert(0, {
                "id": hid, "ts": ts_now, "topic": topic,
                "text": g["guess"], "why": g["why"], "conf": g["conf"],
                "src": g["src"], "status": "ждёт проверки"})
            d["list"] = d["list"][:_MAX_LIST]
            d["made"] = int(d.get("made") or 0) + 1
        _save(d)

    reply = ("Точных знаний по «%s» у меня пока нет — но я не боюсь "
             "предположить. Это ГИПОТЕЗА, не факт:\n\n%s" %
             (topic, g["guess"]))
    if g["why"]:
        reply += "\n\nПочему так думаю: %s." % "; ".join(g["why"][:3])
    reply += ("\n\nУверенность: %s. Проверка уже пошла: тема ушла "
              "нейронам-любопыткам — прочитаю статью и заменю догадку "
              "знанием. Скажи «верно» или «неверно» — учтём вместе."
              % _conf_word(g["conf"]))
    # проверка силами любопытства (если интернет включён)
    try:
        import curiosity
        curiosity.want(topic, why="проверить предположение")
    except Exception:
        pass
    store.log("ГИПОТЕЗА", "предположил по «%s» (уверенность %s, улик: %d)"
              % (topic, _conf_word(g["conf"]), len(g["why"])))
    return {"id": hid, "topic": topic, "conf": g["conf"], "reply": reply}


# ------------------------------ вердикты -------------------------------------
def verdict_latest(ok, by="владелец"):
    """Вердикт по последней гипотезе, что ждёт проверки.
    ok=True  — подтвердить: догадка ВЫУЧИВАЕТСЯ в сеть материалов;
    ok=False — опровергнуть: тема уходит любопытству дочитать статью.
    Возврат: текст-ответ или None (ждущих гипотез нет)."""
    with _LOCK:
        d = _load()
        target = None
        for h in d["list"]:
            if h.get("status") == "ждёт проверки":
                target = h
                break
        if not target:
            return None
        target["status"] = "подтверждено владельцем" if ok \
            else "опровергнуто владельцем"
        target["verdict_ts"] = time.time()
        if ok:
            d["confirmed"] = int(d.get("confirmed") or 0) + 1
        else:
            d["denied"] = int(d.get("denied") or 0) + 1
        _save(d)
    topic = str(target.get("topic") or "")[:120]
    if ok:
        try:
            import brain
            brain.mn_learn("Вопрос: %s\nОтвет (подтверждённая гипотеза): %s"
                           % (topic, target.get("text") or ""),
                           title="гипотеза: %s" % topic[:60],
                           src="гипотеза")
        except Exception:
            pass
        store.log("ГИПОТЕЗА", "подтверждено: «%s» — выучено в сеть" % topic)
        return ("Принял: моё предположение по «%s» подтвердилось — выучил "
                "его в сеть материалов как настоящее знание. Смелость "
                "окупилась: предположений сделано %d, подтвердилось %d, "
                "опровергнуто %d. Так и растём." %
                (topic, int(d.get("made") or 0),
                 int(d.get("confirmed") or 0), int(d.get("denied") or 0)))
    try:
        import curiosity
        curiosity.want(topic, why="предположение не подтвердилось — "
                                  "узнаю, как на самом деле")
    except Exception:
        pass
    store.log("ГИПОТЕЗА", "опровергнуто: «%s» — тема ушла любопытству"
              % topic)
    return ("Принял: гипотезу по «%s» помечаю опровергнутой и НЕ учу её — "
            "ошибаться не страшно. Тема ушла нейронам-любопыткам: прочитаю "
            "статью и в следующий раз отвечу знанием, а не догадкой." % topic)


_YES = re.compile(r"^\s*(верно|угадал|угадала|правильно|точно|да, верно|"
                  r"именно так|попал)\s*[!.?]*$", re.I)
_NO = re.compile(r"^\s*(неверно|не верно|не угадал|не угадала|неправильно|"
                 r"ошибся|ошиблась|не так|мимо|неа)\s*[!.?]*$", re.I)


def feedback(low_text):
    """Реплика-вердикт в чате («верно»/«неверно»)? Применить к последней
    ждущей гипотезе. Возврат: текст-ответ или None (это не вердикт)."""
    t = str(low_text or "").strip()
    if _YES.match(t):
        return verdict_latest(True)
    if _NO.match(t):
        return verdict_latest(False)
    return None


def set_enabled(on):
    store.update_settings({"hypo_on": bool(on)})
    store.log("ГИПОТЕЗА", "смелость предположений: %s"
              % ("включена" if on else "выключена"))
    return status()


def status():
    """Сводка для панели/чата/API: счётчики и последние гипотезы."""
    d = _load()
    lst = list(d.get("list") or [])
    waiting = sum(1 for h in lst if h.get("status") == "ждёт проверки")
    return {
        "on": bool(store.settings().get("hypo_on")),
        "made": int(d.get("made") or 0),
        "confirmed": int(d.get("confirmed") or 0),
        "denied": int(d.get("denied") or 0),
        "waiting": waiting,
        "list": lst[:12],
    }


def evidence(topic, kb_min=0.9):
    """v3.99: ПАМЯТЬ ДЛЯ ОТВЕТА «МОЗГ + СВОЯ LLM», когда точной статьи нет:
    те же слабые улики, что у догадки, но целыми предложениями —
    -> [(текст, откуда)]: похожая статья базы (ниже рабочего порога 2.0,
    из неё — предложения со словами вопроса), предложения сети материалов,
    где тема встретилась с ассоциацией, заметки владельца (по основе)."""
    topic = re.sub(r"\s+", " ", str(topic or "")).strip(" ?.!«»\"'")[:200]
    out = []
    if len(topic) < 2:
        return out
    qs = set(_stems(topic))
    try:                                   # 1) похожая статья базы знаний
        r = kb.search(topic, limit=3)
        hs = [h for h in (r.get("results") or []) if not h.get("wiki") and
              kb_min <= float(h.get("score") or 0) < 2.0]
        if hs:
            ca = kb.compose_answer(topic, hs[:1], max_chars=700)
            txt = str(ca.get("text") or "") if ca.get("ok") else ""
            body = "\n".join(ln for ln in txt.splitlines()
                             if not ln.strip().startswith(("_", "Источник")))
            if len(body) > 40 and qs & set(_stems(body)):
                out.append((body, "похожая статья базы «%s»"
                            % str(hs[0].get("title") or "?")[:60]))
    except Exception:
        pass
    try:                                   # 2) ассоциации сети материалов
        import brain
        sents = []
        with brain._MN_LOCK:
            m = brain.mn()
            act = m.spread(qs)
            assoc = set([st for st, _a in sorted(act.items(),
                                                 key=lambda kv: -kv[1])
                         if st not in qs and len(st) >= 4][:10])
            if qs and assoc:
                # v3.105: только свежие 30000 предложений — на большой сети
                # полный проход держал сеть занятой и тормозил панель
                for s in reversed(m.sents[-30000:]):
                    st_in = set(s.get("tf") or {})
                    if len(st_in & qs) >= min(2, len(qs)) and st_in & assoc:
                        t = m._sent_clean(s.get("t"))
                        if t and 25 <= len(t) <= 400 and not t.endswith("?") \
                                and t not in sents:
                            sents.append(t)
                            if len(sents) >= 4:
                                break
        if sents:
            out.append((" ".join(sents), "сеть материалов"))
    except Exception:
        pass
    try:                                   # 3) заметки владельца
        for n in store.notes():
            t = str(n.get("text") or "").strip()
            if t and qs & set(_stems(t)):
                if not re.search(r"[.!?…]$", t):
                    t += "."
                out.append((t[:1].upper() + t[1:400], "заметки"))
                if len(out) >= 4:
                    break
    except Exception:
        pass
    return out
