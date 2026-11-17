# -*- coding: utf-8 -*-
# ===========================================================================
# llmlink.py — СВЯЗЬ СВОЕЙ LLM С МОЗГОМ ОБЛАКА И МОЗГОМ neuro12 (v3.33)
# ---------------------------------------------------------------------------
# Заявка владельца: «LLM должна работать объединённо с мозгом и платами и
# посылать данные так, чтобы мозг их видел — например через мысли LLM уже
# готовому модулю». Так и сделано: LLM — ещё один «думающий» участник, её
# мысли идут по ТЕМ ЖЕ каналам, что и мысли плат:
#   1) журнал мыслей (store.thoughts_add, плата «своя LLM», kind=llm) —
#      его читают: зона «Мысли плат» (brain.board_mind), «мысль облака»
#      (agent.cloud_thought: «Плата «своя LLM» недавно думала об этом»),
#      кэш плат (/api/board/cache -> thoughts) и вкладка «Мысли плат»;
#   2) лента «Мысли плат» (чат id=thoughts) — видна в панели;
#   3) сеть материалов мозга (brain.mn_learn, src «мысль LLM»), если
#      включено llm_link_net — мозг «видит» мысль как материал;
#   4) платы: пин D6 (unify.thought_to_boards), если включено llm_link_boards
#      и d6_on.
# Обратная связь: в корпус LLM попадают подтверждённые пары «вопрос/ответ»
# мозга облака (qalearn) и мысли плат; СВОИ мысли LLM в корпус не
# возвращаются (без самоподкрепления).
# Защита от мусора: мысль публикуется, только если её качество (доля слов,
# которые модель знает целыми словами / есть в словарях) не ниже порога
# llm_link_minq (0..1; 0 — пускать всё), без повторов недавних мыслей.
# Сама думает по расписанию (llm_link_every, сек; 0 — выкл.): тема — горячие
# узлы сети, недавние мысли, заголовки статей базы.
# ===========================================================================
import random
import re
import threading
import time

import store

BOARD = "своя LLM"
_LOCK = threading.RLock()
_ST = {"loaded": False, "published": 0, "rejected": 0, "last": {},
       "recent": [], "next": 0.0, "started": False, "err": ""}


def cfg():
    s = store.settings()
    try:
        every = int(s.get("llm_link_every", 600))
    except (TypeError, ValueError):
        every = 600
    try:
        minq = float(s.get("llm_link_minq", 0.5))
    except (TypeError, ValueError):
        minq = 0.5
    return {"on": bool(s.get("llm_link_on", True)),
            "reply": bool(s.get("llm_link_reply", False)),
            "boards": bool(s.get("llm_link_boards", True)),
            "net": bool(s.get("llm_link_net", True)),
            "gen": bool(s.get("llm_link_gen", True)),
            "ext": bool(s.get("llm_link_ext", True)),
            "read": bool(s.get("llm_link_read", True)),
            "learn": bool(s.get("llm_link_learn", True)),
            "gen_minq": max(0.3, min(1.0, float(
                s.get("llm_link_gen_minq", 0.5) or 0.5))),
            "every": max(0, every), "minq": max(0.0, min(1.0, minq))}


def _load():
    if not _ST["loaded"]:
        d = store.load_json("llmlink.json", {}) or {}
        _ST.update(published=int(d.get("published") or 0),
                   rejected=int(d.get("rejected") or 0),
                   last=d.get("last") or {},
                   recent=list(d.get("recent") or [])[-30:], loaded=True)
    return _ST


def _save():
    try:
        store.save_json("llmlink.json", {
            "published": _ST["published"], "rejected": _ST["rejected"],
            "last": _ST["last"], "recent": _ST["recent"][-30:]})
    except Exception:
        pass


# ------------------------------ корпус <- мозги -----------------------------
def corpus_extra(max_items=400):
    """Тексты обоих мозгов для обучения LLM: подтверждённые пары
    «вопрос/ответ» (облако и платы) и мысли плат. Свои мысли LLM — не берём."""
    if not cfg()["on"]:
        return []
    out = []
    try:
        import qalearn
        for p in qalearn.export_verified(0.0)[-max_items:]:
            q, a = str(p.get("q") or "").strip(), str(p.get("a") or "").strip()
            if q and a:
                a = store._tclean(a)               # v3.118
                if a:
                    out.append("Вопрос: %s Ответ: %s" % (q[:300], a[:1500]))
    except Exception:
        pass
    try:            # v3.115: только годные мысли (не шум, не «англ н англ»)
        import tinyllm as _tl
        _ok = getattr(_tl, "thought_ok", None)
        _cl = getattr(_tl, "clean_text_corpus", lambda x: x)
        seen_ = set()
        for t in store.thoughts_list(300):
            if t.get("text") and t.get("board") != BOARD and \
                    t.get("kind") not in ("llm", "link"):
                tx = re.sub(r"^\W*🧠\s*«|»\s*$", "", _cl(str(t["text"]))).strip()
                k_ = tx.lower()[:80]
                if k_ in seen_ or (_ok and not _ok(tx)):
                    continue
                seen_.add(k_)
                out.append(tx)
    except Exception:
        pass
    try:                                  # v3.169: прочитанное LLM в интернете
        import llmweb
        out.extend(llmweb.corpus())
    except Exception:
        pass
    try:                                  # v3.59: инструкция учителя
        import teacher
        ins = teacher.instruction()
        if ins:
            out.extend(s.strip() for s in ins.replace("\n", " ").split(". ")
                       if len(s.strip()) > 15)
    except Exception:
        pass
    try:                                  # v3.48: реплики с уровнем сигнала
        import genmod
        out.extend(genmod.corpus_texts(max_items))
    except Exception:
        pass
    try:                                  # v3.51: «частота минус/плюс. …»
        import negfreq
        out.extend(negfreq.corpus_texts(max_items))
    except Exception:
        pass
    try:                                  # v3.46: память плат — тот же граф
        import onenet
        out.extend(onenet.board_texts(max_items))
    except Exception:
        pass
    return out


# --------------------------- мысль LLM -> мозг и платы ----------------------
def _norm(t):
    return set(re.findall(r"[а-яёa-z]{3,}", str(t).lower()))


def _dup(text, recent):
    a = _norm(text)
    if not a:
        return True
    for r in recent[-20:]:
        b = _norm(r.get("text"))
        if b and len(a & b) / float(len(a | b)) >= 0.8:
            return True
    return False


def _quality(text):
    try:
        import tinyllm
        return float(tinyllm.quality(text))
    except Exception:
        return 0.0


def _to_feed(text):
    """Лента «Мысли плат» (чат id=thoughts) — как у мыслей плат."""
    with store.LOCK:
        chat = store.chat_load("thoughts") or \
            store.chat_new("thoughts", "Мысли плат", kind="thoughts")
        chat["messages"].append({"role": "bot", "text": text, "zone": "Мысль",
                                 "brain": BOARD, "ts": time.time()})
        chat["messages"] = chat["messages"][-400:]
        chat["ts"] = time.time()
        store.chat_save("thoughts", chat)


def publish_thought(text, topic="", source="авто", quality=None):
    """Мысль LLM -> готовый модуль мыслей, мозг и платы (см. шапку).
    Возврат dict: {ok, reason?, quality, via:[...], boards:n}."""
    try:                                  # v3.42.1: тарабарщину не публикуем
        import tinyllm
        if tinyllm.has_garbage(text):
            return {"ok": False, "reason": "текст рассыпался на буквы "
                    "(недоученная модель) — не публикую", "quality": 0.0,
                    "via": [], "boards": 0}
    except Exception:
        pass
    c = cfg()
    t = re.sub(r"\s+", " ", str(text or "")).strip()
    res = {"ok": False, "quality": 0.0, "via": [], "boards": 0}
    if degenerate(t):                      # v3.114: зацикленный шум — не мысль
        res["reason"] = "зацикленный повтор слов — не публикую"
        return res
    if not c["on"]:
        res["reason"] = "связь LLM с мозгом выключена"
        return res
    if not t:
        res["reason"] = "пустая мысль"
        return res
    q = _quality(t) if quality is None else float(quality)
    res["quality"] = q
    with _LOCK:
        st = _load()
        if q < c["minq"]:
            st["rejected"] += 1
            res["reason"] = ("качество %.2f ниже порога %.2f — мусор в мозг "
                             "не пускаю (порог меняется в настройках)"
                             % (q, c["minq"]))
            st["last"] = {"text": t, "quality": q, "ok": False,
                          "reason": res["reason"], "ts": time.time(),
                          "source": source}
            _save()
            return res
        if _dup(t, st["recent"]):
            st["rejected"] += 1
            res["reason"] = "повтор недавней мысли"
            _save()
            return res
    # 1) журнал мыслей — читают зона «Мысли плат», мысль облака, кэш плат
    try:
        store.thoughts_add(BOARD, t, kind="llm")
        res["via"].append("журнал мыслей")
    except Exception:
        pass
    # 2) лента «Мысли плат» в панели
    try:
        _to_feed(t)
        res["via"].append("лента «Мысли плат»")
    except Exception:
        pass
    # 3) сеть материалов мозга
    if c["net"]:
        try:
            import brain
            brain.mn_learn(t, title="мысль своей LLM (%s)" % BOARD,
                           src="мысль LLM")
            res["via"].append("сеть материалов мозга")
        except Exception:
            pass
    # 4) платы (пин D6, по настройке d6_on)
    if c["boards"]:
        try:
            import unify
            res["boards"] = _d6_all("🤖 " + t, "мысль своей LLM")

            if res["boards"]:
                res["via"].append("платы (D6): %d" % res["boards"])
        except Exception:
            pass
    try:                                  # 5) вкладка «Учитель»: мысли учителя
        import teacher
        teacher.note_thought(t, BOARD)
        res["via"].append("мысли учителя")
    except Exception:
        pass
    res["ok"] = bool(res["via"])
    with _LOCK:
        st = _load()
        st["published"] += 1
        st["recent"].append({"text": t, "ts": time.time(), "q": q})
        st["recent"] = st["recent"][-30:]
        st["last"] = {"text": t, "quality": q, "ok": True, "ts": time.time(),
                      "via": res["via"], "boards": res["boards"],
                      "source": source, "topic": topic}
        _save()
    return res


# --------------------------------- темы -------------------------------------
def _pick_topic():
    """Тема мысли: горячие узлы сети -> недавняя мысль платы -> заголовок
    статьи базы. Возврат строка (может быть пустой)."""
    cands = []
    try:
        import brain
        hot = brain.mn_readings().get("hot") or []
        if hot:
            cands.append(" ".join(str(h) for h in hot[:3]))
    except Exception:
        pass
    try:
        for t in store.thoughts_list(30):
            if t.get("board") not in (BOARD, "облако") and t.get("text"):
                cands.append(" ".join(str(t["text"]).split()[:4]))
                break
    except Exception:
        pass
    try:
        import kb
        arts = kb.list_articles(limit=200) or []
        if arts:
            cands.append(str(random.choice(arts).get("title") or "")[:60])
    except Exception:
        pass
    cands = [c for c in cands if c.strip()]
    return random.choice(cands) if cands else ""


def think_now(topic="", tries=6, source="кнопка", floor=0.0):
    """Подумать СЕЙЧАС: несколько образцов, берём лучший по качеству, и
    публикуем (если проходит порог). Возврат dict с текстом и причиной."""
    import tinyllm
    topic = (topic or "").strip()[:120] or _pick_topic()
    best, best_q, info = "", -1.0, {}
    for i in range(max(1, int(tries))):
        t, info = tinyllm.sample(topic, temp=0.5 + 0.1 * (i % 4),
                                 topk=20, rep=1.2)
        if not t:
            if info.get("llm") == "cold":
                return {"ok": False, "topic": topic,
                        "reason": "модель ещё не обучалась — подожди конца "
                                  "обучения (статус ниже) или нажми "
                                  "«Переобучить на текущей базе»"}
            continue
        q = _quality(t)
        if q > best_q:
            best, best_q = t, q
    if not best:
        return {"ok": False, "topic": topic, "reason": "модель молчит"}
    if floor and best_q < floor:
        return {"ok": False, "text": best, "topic": topic, "quality": best_q,
                "reason": "мысль LLM (%.2f) хуже мыслей платы (%.2f) — мозг "
                          "думает лучше, не публикую" % (best_q, floor / 0.9)}
    res = publish_thought(best, topic, source=source, quality=best_q)
    res.update(text=best, topic=topic)
    return res


# ------------- v3.36: LLM РАЗВИВАЕТ МЫСЛИ МОЗГА (а не соревнуется) -----------
def _is_board_thought(t):
    return bool(t.get("text")) and t.get("board") not in _SKIP_BOARDS and \
        t.get("kind") != "llm"


def board_baseline(n=10):
    """Средняя оценка качества последних мыслей плат по той же шкале, что у
    LLM: свободные мысли LLM хуже этой планки не публикуются."""
    qs = [_quality(t["text"]) for t in store.thoughts_list(80)
          if _is_board_thought(t)][:n]
    return (sum(qs) / len(qs)) if qs else 0.0


def extend(text, source="функция"):
    """Развить мысль мозга: мысль платы — основа (тема), факты берутся из
    базы знаний по её ключевым словам, LLM составляет продолжение только из
    этих фактов. Возврат: {ok, text, grounded, facts|reason}."""
    text = " ".join(str(text or "").split())[:300]
    if not text:
        for t in store.thoughts_list(40):
            if _is_board_thought(t):
                text = str(t["text"])[:300]
                break
    if not text:
        return {"ok": False, "reason": "нет мысли, которую можно развить"}
    facts = []
    try:
        import kb
        import textutils
        keys = textutils.keywords(text, 4) or []
        for q in ([" ".join(keys[:2])] if len(keys) > 1 else []) + keys[:3]:
            for r in (kb.search(q, limit=2) or {}).get("results") or []:
                sn = " ".join(str(r.get("snippet") or "").split())
                if len(sn) > 40 and sn not in facts:
                    facts.append(sn)
            if len(facts) >= 4:
                break
    except Exception:
        pass
    if not facts:
        return {"ok": False, "text": "", "reason":
                "в базе знаний нет фактов по теме мысли"}
    try:
        import tinyllm
        out, info = tinyllm.compose(text, " ".join(facts)[:1500])
    except Exception as e:
        return {"ok": False, "reason": "LLM: %s" % e}
    if info.get("llm") == "cold":
        return {"ok": False, "reason": "модель ещё не обучена"}
    g = float(info.get("grounded") or 0)
    if not out or g < cfg()["gen_minq"]:
        _ST["ext_rej"] = _ST.get("ext_rej", 0) + 1
        return {"ok": False, "text": out, "grounded": g,
                "reason": "продолжение не опирается на факты — не публикую"}
    if _norm(out) <= _norm(text):
        return {"ok": False, "text": out, "grounded": g,
                "reason": "нечего добавить к мысли"}
    return {"ok": True, "text": out, "grounded": g, "facts": len(facts),
            "seed": text, "source": source}


def develop_board_thoughts():
    """Фон: новейшая мысль платы, ещё не развитая, -> extend -> публикация
    «Развитие мысли платы …». Не чаще раза в 60 с."""
    c = cfg()
    if not (c["on"] and c["ext"]):
        return None
    now = time.time()
    if now - _ST.get("ext_t", 0) < 60:
        return None
    mark = float(store.load_json("llmext.json", {}).get("ts") or 0)
    cand = [t for t in store.thoughts_list(30)
            if _is_board_thought(t) and float(t.get("ts") or 0) > mark]
    if not cand:
        return None
    t = cand[0]                                   # самая свежая
    _ST["ext_t"] = now
    store.save_json("llmext.json", {"ts": float(t["ts"])})
    r = extend(t["text"], source="развитие")
    if not r.get("ok"):
        return r
    msg = "Развитие мысли платы «%s» (%s): %s" % (
        str(t["board"])[:20], str(t["text"])[:60], r["text"])
    pub = publish_thought(msg, str(t["text"])[:60], source="развитие",
                          quality=max(r["grounded"], c["minq"]))
    _ST["ext_ok"] = _ST.get("ext_ok", 0) + (1 if pub.get("ok") else 0)
    return pub


# ------------- v3.37: ОПЫТ ЧТЕНИЯ LLM -> МОЗГ ---------------------------------
def read_new_articles(limit=3):
    """LLM «читает» статьи базы знаний: для каждой, ещё не прочитанной (или
    изменившейся), берёт самые усвоенные моделью предложения, ключевые слова
    и показатель усвоения, и передаёт мозгу: (1) в сеть материалов мозга
    (net видит статью и связи ключевых слов), (2) в журнал мыслей
    («📖 прочитала статью …» — видят учитель и лента). Возврат: сколько."""
    c = cfg()
    if not (c["on"] and c["read"]):
        return 0
    import tinyllm
    import kb
    import textutils
    if not tinyllm.status().get("steps"):
        return 0
    state = store.load_json("llmread.json", {}) or {}
    done = 0
    for a in kb.list_articles(limit=3000, include_devices=True) or []:
        aid = str(a.get("id") or "")
        if not aid:
            continue
        art = kb.article(aid) or {}
        text = str(art.get("text") or "")
        if str(art.get("title") or "").startswith("Исходник:"):
            continue                       # код читает обучение, не «чтение»
        sig = "%d:%d" % (len(text), sum(map(ord, text[:200])))
        if not text or state.get(aid, {}).get("sig") == sig:
            continue
        r = tinyllm.read_article(art.get("title") or "", text)
        if not r.get("ok"):
            continue
        title = str(art.get("title") or aid)[:80]
        keys = textutils.keywords(text, 6) or []
        digest = "%s. %s Ключевые слова: %s." % (
            title, " ".join(r["best"]), ", ".join(keys))
        try:
            import brain
            brain.mn_learn(digest, title="прочитано LLM: " + title,
                           src="чтение LLM")
        except Exception:
            pass
        note = "📖 Прочитала статью «%s»: усвоено %d%%, ключевое — %s" % (
            title, int(100 * r["learned"]), r["best"][0][:140])
        try:
            store.thoughts_add(BOARD, note, kind="read")
            import teacher
            teacher.note_thought(note, BOARD)
        except Exception:
            pass
        state[aid] = {"sig": sig, "learned": r["learned"], "ts": time.time(),
                      "title": title}
        done += 1
        _ST["read_n"] = _ST.get("read_n", 0) + 1
        if done >= limit:
            break
    if done:
        store.save_json("llmread.json", state)
    return done


# ---------------------------- фоновое мышление ------------------------------
# ------------------- D6: мысли LLM и плат -> пин D6 всех плат ---------------
def _d6_all(text, zone, skip=""):
    """Мысль в ящик D6 каждой зарегистрированной платы (кроме skip).
    D6 включён всегда (v3.35). Возврат: сколько плат получили."""
    n = 0
    try:
        for bid in list(store.boards().keys()):
            if skip and str(bid) == str(skip):
                continue
            if store.d6_add(bid, text, zone):
                n += 1
    except Exception:
        pass
    return n


_SKIP_BOARDS = (BOARD, "облако", "учитель", "Мысли облака")


def relay_board_thoughts():
    """Мысли плат -> D6 ДРУГИХ плат («💭 плата: …»), каждая один раз.
    Мысли своей LLM и облака сюда не идут (у них свой путь)."""
    cur = float(store.load_json("d6relay.json", {}).get("ts") or 0)
    if not cur:                           # первый запуск: старое не гоним
        cur = time.time() - 600
    new = [t for t in reversed(store.thoughts_list(60))
           if float(t.get("ts") or 0) > cur and t.get("text") and
           t.get("board") not in _SKIP_BOARDS and t.get("kind") != "llm"]
    n = 0
    for t in new[-5:]:
        n += _d6_all("💭 %s: %s" % (str(t["board"])[:20], t["text"]),
                     "мысль платы", skip=t["board"])
    if new:
        store.save_json("d6relay.json", {"ts": float(new[-1]["ts"])})
        try:
            _ST["relayed"] = _ST.get("relayed", 0) + len(new)
        except Exception:
            pass
    return n


def _loop():
    while True:
        time.sleep(10)
        try:
            try:                       # v3.132: фоновая учёба — только на вдохе
                import breath as _brk
                if not _brk.inhale("Связь LLM с мозгом"):
                    continue
            except Exception:
                pass
            c = cfg()
            if c["on"] and c["boards"]:
                relay_board_thoughts()
            if c["on"] and c["ext"]:
                develop_board_thoughts()
            if c["on"] and c["learn"] and time.time() - _ST.get("learn_t", 0) > 120:
                _ST["learn_t"] = time.time()
                import tinyllm
                st_ = tinyllm.status()
                if st_.get("steps") and st_.get("steps") < st_.get("total_steps", 0):
                    tinyllm.ensure_training()           # прервали — продолжить
                _ST["learn"] = tinyllm.learn_new()     # дообучение, не с нуля
            if c["on"] and c["read"]:
                read_new_articles()
            if c["on"]:                       # v3.169: LLM сама в интернете
                try:
                    import llmweb
                    llmweb.tick()
                except Exception:
                    pass
            if not c["on"] or c["every"] <= 0:
                continue
            now = time.time()
            if now < _ST["next"]:
                continue
            _ST["next"] = now + max(20, c["every"])
            import tinyllm
            st = tinyllm.status()
            if not st.get("steps"):
                continue                       # ещё не училась
            r = think_now(source="сама", tries=4,
                          floor=0.9 * board_baseline())
            _ST["err"] = "" if r.get("ok") else str(r.get("reason") or "")
        except Exception as e:
            _ST["err"] = "%s: %s" % (type(e).__name__, e)


def start():
    """Фоновое «думание» LLM (вызывается из app.py)."""
    if _ST["started"]:
        return
    _ST["started"] = True
    _ST["next"] = time.time() + 60
    threading.Thread(target=_loop, daemon=True, name="llmlink").start()


# ------------------------- строка LLM в ответах мозга -----------------------
def degenerate(t):
    """v3.114: зацикленный «словесный шум» своей LLM: «Англ н англ н н англ н
    н н7 н7 н7…» — слова из фактов, поэтому проверку словаря проходил.
    Признаки: одно слово повторяется подряд, мало разных слов, много
    обрывков (1–2 буквы, буквы вперемешку с цифрами)."""
    import re as _re
    toks = _re.findall(r"\w+", str(t or "").lower())
    if len(toks) < 3:
        return False
    if _re.search(r"\b(\w+)\b(?:[\s,;.]+\1\b){2,}", str(t).lower()):
        return True                                   # «н н н», «н7 н7 н7»
    if len(set(toks)) < 0.55 * len(toks):
        return True                                   # мало разных слов
    from collections import Counter
    c = Counter(toks)
    for w, n in c.most_common(3):
        func = w in ("и", "в", "на", "с", "по", "не", "а", "о", "к", "у")
        if n >= (5 if func else 3) and n >= 0.25 * len(toks):
            return True                               # одно слово везде
    _fw = ("и", "в", "на", "с", "по", "не", "а", "о", "к", "у", "из", "за",
           "от", "до", "же", "ли", "бы", "то", "он", "мы", "вы", "я", "но",
           "во", "со", "об", "ко", "да", "ни")
    scraps = [x for x in toks if (len(x) <= 2 and x not in _fw and
                                  not x.isdigit()) or
              (_re.search(r"\d", x) and _re.search(r"[^\d]", x))]
    return len(scraps) > 0.35 * len(toks)


def _words_ok(t, facts):
    """Слова сгенерированного текста должны быть словами из фактов (по
    началу, 5 букв) — склеенные/выдуманные слова отсекаются.
    v3.114: и не зацикленный шум (degenerate)."""
    import re as _re
    if degenerate(t):
        return False
    try:
        import tinyllm
        if tinyllm.has_garbage(t):
            return False
    except Exception:
        pass
    known = set(w[:5] for w in _re.findall(r"[a-zа-яё]{4,}", facts.lower()))
    ws = _re.findall(r"[a-zа-яё]{4,}", t.lower())
    if not ws:
        return True
    bad = [w for w in ws if w[:5] not in known or len(w) > 18]
    return len(bad) <= 0.15 * len(ws)


def compose_reply(text, reply, zone=""):
    """v3.34: своя LLM КАК ГЕНЕРАТОР: ответ из базы знаний/интернета
    (reply) — это факты; модель составляет по ним текст (слова только из
    фактов, порядок и связки — её). Берём результат, лишь если он опирается
    на факты (доля пар слов ≥ llm_link_gen_minq); иначе остаётся исходный
    ответ. Возврат: (ответ, info|None)."""
    c = cfg()
    if not (c["on"] and c["gen"]):
        return reply, None
    r = str(reply or "")
    if len(r) < 80 or r.lstrip().startswith(("Не знаю", "⚠")):
        return reply, None
    try:
        import tinyllm
        facts = []
        for ln in r.splitlines():
            ln = ln.strip()
            if not ln or ln.startswith(("_", "🤖", "📎", "Источник")):
                continue
            facts.append(ln)
        facts = " ".join(facts)[:1500]
        t, i = tinyllm.compose(text, facts)
    except Exception:
        return reply, None
    if t and not _words_ok(t, facts):
        t = ""                       # тарабарщина/склеенные слова — не показываем
    try:                             # v3.122: «Не показывать бред»
        import strict
        if t and strict.on() and not strict.text_ok(t, text):
            strict.block("LLM по материалам", t)
            t = ""
    except Exception:
        pass
    if not t or float(i.get("grounded") or 0) < c["gen_minq"] or \
            len(t.split()) < 3:
        _ST["gen_rej"] = _ST.get("gen_rej", 0) + 1
        return reply, None
    _ST["gen_ok"] = _ST.get("gen_ok", 0) + 1
    return (t + "\n\n_Составлено своей LLM по найденным материалам "
            "(опора на факты %d%%)._" % int(100 * i["grounded"])), \
        {"llm_gen": True, "llm_grounded": i["grounded"]}


def annotate(text, reply, zone=""):
    """Дописать к ответу мозга строку мысли LLM (только llm_link_reply и
    мысль нужного качества). Возврат: (новый ответ, info|None)."""
    c = cfg()
    if not (c["on"] and c["reply"]):
        return reply, None
    try:
        import strict
        if strict.on():
            return reply, None       # v3.122: фантазия LLM — не в ответ
    except Exception:
        pass
    try:
        import tinyllm
        best, bq = "", -1.0
        for _ in range(3):
            t, _i = tinyllm.sample(str(text)[:120], temp=0.7)
            if t:
                q = _quality(t)
                if q > bq:
                    best, bq = t, q
        if not best or bq < c["minq"]:
            return reply, None
    except Exception:
        return reply, None
    return (str(reply) + "\n\n🤖 Мысль своей LLM по теме (фантазия, не факт): "
            "«%s»" % best), {"llm_link": True, "llm_q": bq}


def chat(text, ctx=None, sender="LLM-чат", long_mode=False):
    """Единый вход «своя LLM + весь мозг»: все функции платформы (зоны мозга
    через brain.think) + строка LLM. Возврат dict {ok, reply, zone, info}."""
    import brain
    reply, zone, info = brain.think(text, long_mode, route="cloud",
                                    sender=sender, ctx=ctx)
    out = {"ok": True, "reply": reply, "zone": zone, "info": info or {}}
    try:
        import tinyllm
        out["llm"] = {k: v for k, v in tinyllm.status().items()
                      if k in ("ready", "training", "pct", "loss", "params",
                               "d_model", "layers", "heads", "ctx", "numpy")}
    except Exception:
        out["llm"] = {}
    return out


def status():
    """Для панели: настройки связи, последняя мысль, счётчики."""
    c = cfg()
    with _LOCK:
        st = _load()
        last = dict(st.get("last") or {})
        if last.get("text") and not store._tclean(last["text"]):
            last = {}                      # v3.118: «англ н н» не показываем
        out = dict(c)
        out.update(published=st["published"], rejected=st["rejected"],
                   gen_ok=_ST.get("gen_ok", 0), relayed=_ST.get("relayed", 0), ext_ok=_ST.get("ext_ok", 0), learn_state=_ST.get("learn", ""), read_n=len(store.load_json("llmread.json", {}) or {}),
                   ext_rej=_ST.get("ext_rej", 0), baseline=round(board_baseline(), 2), gen_rej=_ST.get("gen_rej", 0),
                   last=last, err=st.get("err") or "",
                   next_in=max(0, int(st.get("next", 0) - time.time()))
                   if c["every"] else 0)
    # v3.113: БАГФИКС — поле «learn» (галочка «Дообучение») перезаписывалось
    # итогом последнего дообучения («idle», «busy»…) — непустая строка, и
    # галочка в панели всегда показывалась включённой, «не снималась»;
    # итог теперь — learn_state. Подсчёт корпуса — не чаще раза в минуту.
    try:
        now = time.time()
        if not c["on"]:
            out["corpus_extra"] = 0
        elif now - float(_ST.get("cx_ts") or 0) > 60:
            _ST["cx_n"] = len(corpus_extra())
            _ST["cx_ts"] = now
            out["corpus_extra"] = _ST["cx_n"]
        else:
            out["corpus_extra"] = _ST.get("cx_n", 0)
    except Exception:
        out["corpus_extra"] = 0
    out["functions"] = "все зоны мозга через brain.think (POST /api/llm/chat)"
    return out
