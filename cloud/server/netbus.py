# -*- coding: utf-8 -*-
"""netbus.py — v3.237: «ДРУЖБА НЕЙРОСЕТЕЙ» — ОБМЕН ЗНАНИЯМИ БЕЗ ЧАТА.

Сети Мозга-12 обмениваются не сообщениями чата, а ПАКЕТАМИ на общей шине:
    {id, ts, from, to, kind, keys[], text, conf}
Узлы шины:
    «мозг»        — мозг облака (база знаний + нейросеть материалов);
    «llm»         — своя LLM облака;
    «плата:<id>»  — нейроны каждой платы (через пин D6 и мозг-почту).
Один круг обмена (фоновый поток, раз в netbus_period секунд, по умолчанию 120):
  1) мозг выбирает тему (ключевые слова свежей статьи знаний) и шлёт пакет
     «знание» на шину; платы получают его в ящик D6 -> плата проигрывает текст
     на пине D6 (PWM), нейроны платы возбуждаются его словами;
  2) своя LLM, получив тему, пишет короткую мысль — пакет «мысль»; мозг
     проверяет её (качество текста модели + совпадение слов с базой знаний):
     принял — мысль идёт в нейросеть материалов и на D6 плат, доверие
     мозга к LLM растёт; отверг — падает;
  3) плата отвечает пакетом «отклик»: какие слова пакета узнали её нейроны
     (POST /api/netbus/post). Совпало с темой — дружба мозг↔плата крепнет.
ДОВЕРИЕ (0..1, начало 0,5) у каждой пары «от>к»: пакеты от сети с низким
доверием не идут в обучение и не уходят на D6. Это и есть «дружба»: сети,
чьи пакеты подтверждаются, слушают друг друга сильнее.
Честно: это обмен ключевыми словами и короткими текстами с проверкой, а не
общая «телепатия» весов; на D6 уходит текст пакета тем же каналом, что и
мысли зон облака (плата превращает буквы в PWM-байты)."""
import random
import re
import threading
import time

import store

_FILE = "netbus.json"
_LOCK = threading.Lock()
_RUN = {"th": None, "last": 0.0, "rounds": 0, "err": ""}
ZONE = "Дружба сетей"
MAX_PK = 200


def _db():
    return store.load_json(_FILE, {"packets": [], "trust": {}, "stats": {}}) or {"packets": [], "trust": {}, "stats": {}}


def enabled():
    return bool(store.settings().get("netbus_on", True))


def d6_enabled():
    return bool(store.settings().get("netbus_d6", True))


def trust(a, b, d=None):
    d = d or _db()
    return float((d.get("trust") or {}).get("%s>%s" % (a, b), 0.5))


def _bump(d, a, b, delta):
    k = "%s>%s" % (a, b)
    v = float(d["trust"].get(k, 0.5)) + delta
    d["trust"][k] = round(max(0.0, min(1.0, v)), 3)
    return d["trust"][k]


def _keys(text, n=6):
    try:
        import textutils
        return [str(w) for w in (textutils.keywords(text, n) or [])][:n]
    except Exception:
        ws = re.findall(r"[а-яёa-z]{4,}", str(text).lower())
        return list(dict.fromkeys(ws))[:n]


def post(frm, to, kind, text, keys=None, conf=0.5, re_id=""):
    """Пакет на шину -> пакет (dict). Доставка на платы — через ящик D6."""
    text = re.sub(r"\s+", " ", str(text or "")).strip()[:300]
    keys = [str(k)[:30] for k in (keys or _keys(text))][:8]
    if not text and not keys:
        return None
    pk = {"id": "nb%d%03d" % (int(time.time() * 1000), random.randint(0, 999)), "ts": time.time(),
          "from": str(frm)[:48], "to": str(to or "*")[:48], "kind": str(kind)[:16],
          "keys": keys, "text": text, "conf": round(float(conf), 2), "re": str(re_id or "")[:24]}
    with _LOCK:
        d = _db()
        d["packets"].append(pk)
        d["packets"] = d["packets"][-MAX_PK:]
        st = d.setdefault("stats", {})
        st[pk["from"]] = int(st.get(pk["from"]) or 0) + 1
        store.save_json(_FILE, d)
    return pk


def _to_boards(pk):
    """Пакет -> ящик D6 всех плат (или одной). Плата проиграет его на пине D6."""
    if not d6_enabled():
        return 0
    boards = list((store.boards() or {}).keys())
    to = pk.get("to") or "*"
    if to.startswith("плата:"):
        boards = [b for b in boards if b == to[6:]]
    n = 0
    line = "%s: %s" % ({"знание": "мозг", "мысль": "llm"}.get(pk["kind"], pk["from"]),
                       pk["text"] or " ".join(pk["keys"]))
    for b in boards:
        if pk.get("from") == "плата:" + b:
            continue                              # своё плате не возвращаем
        try:
            if store.d6_add(b, line[:240], ZONE, 0, "netbus", nb=pk.get("id") or ""):
                n += 1
        except Exception:
            pass
    return n


def _kb_overlap(text, keys):
    """Доля ключевых слов пакета, которые знает база знаний мозга (0..1)."""
    try:
        import kb
        hits = (kb.search(" ".join(keys) or text, limit=3) or {}).get("results") or []
    except Exception:
        return 0.0
    if not hits:
        return 0.0
    blob = " ".join(str(h.get("title") or "") + " " + str(h.get("snippet") or "") for h in hits).lower()
    ok = sum(1 for k in keys if k.lower()[:5] in blob)
    return ok / float(max(1, len(keys)))


def _topic():
    """Тема круга: ключевые слова одной из свежих статей знаний."""
    try:
        import kb
        arts = [a for a in kb.list_articles(limit=60, include_devices=False)
                if not str(a.get("title") or "").startswith("Администрирование")]
        if not arts:
            return None
        a = random.choice(arts[:30])
        full = kb.article(a.get("id")) or {}
        txt = str(full.get("text") or "")
        sent = re.split(r"(?<=[.!?])\s+", txt.strip())[0][:200] if txt else ""
        keys = (a.get("kw") or [])[:5] or _keys(txt, 5)
        return {"title": a.get("title"), "keys": [str(k) for k in keys], "text": sent}
    except Exception:
        return None


def _llm_thought(keys):
    try:
        import tinyllm
        r = tinyllm.sample(" ".join(keys[:4]))
        t = r[0] if isinstance(r, tuple) else r
        t = str(t or "").strip()
        if not t:
            return "", 0.0
        q = 0.0
        try:
            q = float(tinyllm.quality(t) or 0)
        except Exception:
            pass
        return t[:240], q
    except Exception:
        return "", 0.0


def round_once():
    """Один круг обмена. -> сводка."""
    rep = {"ok": True, "topic": None, "llm": None, "boards": 0}
    tp = _topic()
    if not tp:
        rep["ok"], rep["note"] = False, "в базе знаний нет статей для темы"
        return rep
    rep["topic"] = tp["title"]
    pk = post("мозг", "*", "знание", tp["text"] or tp["title"], tp["keys"], conf=0.8)
    if pk:
        rep["boards"] += _to_boards(pk)
    txt, q = _llm_thought(tp["keys"])
    if txt:
        ov = _kb_overlap(txt, _keys(txt))
        conf = round(0.5 * q + 0.5 * ov, 2)
        accepted = conf >= 0.35
        pk2 = post("llm", "мозг", "мысль", txt, conf=conf, re_id=pk["id"] if pk else "")
        with _LOCK:
            d = _db()
            tr = _bump(d, "мозг", "llm", 0.04 if accepted else -0.05)
            store.save_json(_FILE, d)
        rep["llm"] = {"text": txt, "quality": q, "overlap": round(ov, 2), "accepted": accepted, "trust": tr}
        if accepted and tr >= 0.4:
            try:
                import brain
                brain.mn_learn(txt, "дружба сетей: мысль LLM", src="netbus")
            except Exception:
                pass
            if pk2:
                rep["boards"] += _to_boards(dict(pk2, to="*"))
    with _LOCK:
        _RUN["rounds"] += 1
        _RUN["last"] = time.time()
    return rep


def board_packet(board, data):
    """Пакет от платы (POST /api/netbus/post). Мозг сверяет его со своей базой."""
    b = str(board or "")[:40]
    text = str(data.get("text") or "")[:300]
    keys = [str(k)[:30] for k in (data.get("keys") or [])][:8] or _keys(text)
    if not (text or keys):
        return {"ok": False, "error": "пустой пакет"}
    re_id = str(data.get("re") or "")[:24]
    with _LOCK:
        d = _db()
        orig = next((p for p in reversed(d["packets"]) if p["id"] == re_id), None) if re_id else None
    if orig:                                     # отклик на пакет мозга: совпадение слов = дружба
        ok = len(set(k.lower() for k in keys) & set(k.lower() for k in orig.get("keys") or []))
        delta = 0.03 if ok else -0.01
    else:
        delta = 0.03 if _kb_overlap(text, keys) >= 0.3 else -0.02
    pk = post("плата:" + b, data.get("to") or "мозг", str(data.get("kind") or "отклик"), text, keys,
              conf=float(data.get("conf") or 0.5), re_id=re_id)
    with _LOCK:
        d = _db()
        tr = _bump(d, "мозг", "плата:" + b, delta)
        _bump(d, "плата:" + b, "мозг", delta / 2)
        store.save_json(_FILE, d)
    if tr >= 0.3 and text:
        try:
            import brain
            brain.mn_learn(text, "дружба сетей: плата %s" % b, src="netbus")
        except Exception:
            pass
    return {"ok": True, "id": (pk or {}).get("id"), "trust": tr}


def status(limit=30):
    d = _db()
    return {"ok": True, "on": enabled(), "d6": d6_enabled(),
            "period": int(store.settings().get("netbus_period") or 120),
            "rounds": _RUN["rounds"], "last": _RUN["last"], "err": _RUN["err"],
            "trust": d.get("trust") or {}, "stats": d.get("stats") or {},
            "packets": (d.get("packets") or [])[-int(limit):]}


def _loop():
    time.sleep(45)
    while True:
        per = max(30, int(store.settings().get("netbus_period") or 120))
        try:
            if enabled():
                round_once()
                _RUN["err"] = ""
        except Exception as e:
            _RUN["err"] = "%s: %s" % (type(e).__name__, str(e)[:120])
        time.sleep(per)


def start():
    if _RUN["th"] is None:
        _RUN["th"] = threading.Thread(target=_loop, daemon=True, name="netbus")
        _RUN["th"].start()
