# -*- coding: utf-8 -*-
# ===========================================================================
# onenet.py — ЕДИНАЯ СЕТЬ: ОБЛАКО + ПЛАТЫ + LLM В ОДНОМ ГРАФЕ (v3.46, stdlib)
# ---------------------------------------------------------------------------
# Заявка владельца: «сделай из этого одну сеть». Честно о том, что можно:
#   * ЕДИНАЯ ПАМЯТЬ-ГРАФ — сеть материалов облака (mozgnet) теперь одна для
#     всех: в неё попадает всё — база знаний и интернет, диалоги, мысли
#     плат, мысли своей LLM, коды, а с v3.46 и ПАМЯТЬ ПЛАТ: реплики и
#     диалоги, последняя фраза, заметка учителя, выученные слова. У каждого
#     материала метка происхождения (облако / плата «имя» / LLM / диалог…);
#     один вопрос ищет сразу по всем.
#   * ЯЗЫК СЕТИ — своя LLM учится на тексте из того же графа: к её корпусу
#     добавлена память плат, так что она «говорит» и о том, что видели платы.
#   * ОБРАТНО — платы получают подтверждённое облаком знание и мысли
#     (unify, D6), как и раньше.
# Чего это НЕ делает: веса тензорной сети платы (на устройстве, признаки в
# хэшах) нельзя слить с графом облака и трансформером LLM — это три РАЗНЫХ
# вида моделей; объединена память и поток знаний, а не веса.
# ===========================================================================
import glob
import hashlib
import json
import os
import re
import threading
import time

import store

_LOCK = threading.RLock()
_ST = {"seen": None}
SEEN_MAX = 30000


def _seen():
    if _ST["seen"] is None:
        d = store.load_json("onenet.json", None) or {}
        _ST["seen"] = dict(d.get("seen") or {})
        _ST["stats"] = dict(d.get("stats") or {})
    return _ST["seen"]


def _save():
    seen = _seen()
    if len(seen) > SEEN_MAX:
        for k in sorted(seen, key=seen.get)[:len(seen) - SEEN_MAX]:
            seen.pop(k, None)
    store.save_json("onenet.json", {"seen": seen, "stats": _ST.get("stats")})


def _items(dev, b):
    """Бандл памяти платы -> [(заголовок, текст)] (только текст людей)."""
    out = []
    for m in (b.get("memory") or [])[-60:]:
        t = str((m or {}).get("text") or "").strip()
        if len(t) >= 12:
            out.append(("память платы %s" % dev, t[:600]))
    for m in (b.get("chat") or [])[-60:]:
        t = str((m or {}).get("text") or "").strip()
        if len(t) >= 12:
            out.append(("диалог %s" % dev, t[:800]))
    st = b.get("state") or {}
    for k, ttl in (("last_say", "последняя фраза платы %s"),
                   ("teacher_note", "заметка учителя платы %s")):
        t = str(st.get(k) or "").strip()
        if len(t) >= 12:
            out.append((ttl % dev, t[:600]))
    words = [str(w) for w in (st.get("dict_extra") or []) if str(w).strip()]
    if len(words) >= 3:
        out.append(("слова, выученные платой %s" % dev,
                    "Плата %s выучила слова: %s." % (dev, ", ".join(
                        words[-80:]))))
    return out


def ingest_bundle(dev, bundle):
    """Память платы -> единый граф (каждый текст один раз). Возврат: сколько
    нового принято сетью."""
    import brain
    n = 0
    with _LOCK:
        seen = _seen()
        for title, text in _items(dev, bundle or {}):
            h = hashlib.md5((dev + "|" + text).encode("utf-8", "replace")
                            ).hexdigest()[:14]
            if h in seen:
                continue
            seen[h] = time.time()
            brain.mn_learn(text, title=title[:100], src="плата:%s" % dev[:24])
            n += 1
        if n:
            stt = _ST.setdefault("stats", {})
            stt["from_boards"] = int(stt.get("from_boards") or 0) + n
            _save()
    return n


def board_texts(max_items=300):
    """Тексты памяти плат — в корпус своей LLM (язык той же сети)."""
    out = []
    try:
        for p in glob.glob(store.p("memory/*.json")):
            if p.endswith(".history.json"):
                continue
            try:
                with open(p, encoding="utf-8") as f:
                    b = json.load(f)
            except Exception:
                continue
            dev = os.path.basename(p)[:-5]
            for _t, tx in _items(dev, b):
                if len(tx) >= 25:
                    out.append(tx)
    except Exception:
        pass
    return out[-max_items:]


def _origin(src):
    s = str(src or "")
    if s.startswith("плата"):
        return "платы"
    if s.startswith("мысль LLM") or "LLM" in s:
        return "своя LLM"
    if s.startswith(("мысль", "мост")):
        return "мысли плат"
    if s.startswith(("диалог", "вопрос")):
        return "диалоги и вопросы"
    if s.startswith("код") or s.startswith("Код"):
        return "код"
    return "база знаний и интернет"


def status():
    """Состав единой сети по происхождению материалов."""
    import brain
    out = {"origins": {}, "docs": 0, "nodes": 0, "links": 0, "boards": 0,
           "from_boards": int((_ST.get("stats") or {}).get("from_boards")
                              or 0)}
    try:
        _seen()
        out["from_boards"] = int((_ST.get("stats") or {}).get(
            "from_boards") or 0)
        if brain._MN_LOCK.acquire(timeout=0.3):
            try:
                m = brain.mn()
                out["docs"], out["nodes"] = len(m.docs), len(m.nodes)
                out["links"] = sum(len(v) for v in m.edges.values())
                for d in m.docs:
                    o = _origin(d.get("src"))
                    out["origins"][o] = out["origins"].get(o, 0) + 1
            finally:
                brain._MN_LOCK.release()
        out["boards"] = len(store.boards())
    except Exception:
        pass
    return out
