# -*- coding: utf-8 -*-
# ===========================================================================
# cloudthought.py — v3.209: ОБЛАКО ШЛЁТ СВОИ МЫСЛИ ПЛАТАМ И В ЧАТ
# ---------------------------------------------------------------------------
# Раньше облако только читало мысли плат. Теперь оно думает само: берёт тему из
# интереса плат (boardmind) или из последней мысли платы, собирает фразу из слов
# словаря (wordmind.compose — та же речь из словаря, без выдумок и без LLM-кода),
# и раз в N секунд:
#   * кладёт её в чат «Мысли плат» (пометка «облако»);
#   * пишет в ленту мыслей (store.thoughts_add);
#   * кладёт ПИСЬМО-мысль в ящик каждой платы, которая была на связи за последние
#     10 минут (kind=cloud_thought): плата зажигает нейроны словами мысли и
#     показывает её в своём окне, но на неё НЕ отвечает (цикл «мысль -> ответ ->
#     мысль» исключён: свои мысли облако в обучение не берёт).
# Настройки — cloudthought.json (API /api/cloud/thought): on, every (сек, ≥30),
# to_boards, to_chat. Честно: это словарная речь по связям слов, а не понимание.
# ===========================================================================
import re
import threading
import time

import store

_LOCK = threading.RLock()
_FILE = "cloudthought.json"
_ST = {"started": False, "next": 0.0, "last": "", "last_ts": 0.0, "sent": 0,
       "err": "", "boards": 0}
_DEF = {"on": True, "every": 300, "to_boards": True, "to_chat": True}
_ONLINE_S = 600.0


def cfg():
    c = dict(_DEF)
    try:
        c.update({k: v for k, v in (store.load_json(_FILE, None) or {}).items()
                  if k in _DEF})
    except Exception:
        pass
    c["on"] = bool(c["on"])
    c["to_boards"] = bool(c["to_boards"])
    c["to_chat"] = bool(c["to_chat"])
    try:
        c["every"] = max(30, min(86400, int(c["every"])))
    except Exception:
        c["every"] = _DEF["every"]
    return c


def set_cfg(d):
    c = cfg()
    for k in _DEF:
        if k in (d or {}):
            c[k] = d[k]
    store.save_json(_FILE, c)
    return cfg()


def _topic():
    """Тема мысли: интерес плат -> последняя мысль платы -> пусто."""
    try:
        import boardmind
        t = boardmind.topic()
        if t:
            return t
        it = boardmind.interest(10)
        if it:
            return it[0][2]
    except Exception:
        pass
    try:
        for th in store.thoughts_list(12):
            if str(th.get("board")) != "облако" and th.get("text"):
                return str(th["text"])[:160]
    except Exception:
        pass
    return ""


def _bad(t):
    try:
        import llmlink
        if llmlink.degenerate(t):
            return True
    except Exception:
        pass
    return len(re.findall(r"[а-яё]{2,}", t.lower())) < 3


def make_thought():
    """Одна мысль облака или ''. Несколько попыток, берём самую длинную чистую."""
    try:
        import wordmind
    except Exception as e:
        _ST["err"] = "речь облака недоступна: %s" % e
        return ""
    topic = _topic()
    seen = {str(x.get("text") or "")[:80] for x in store.thoughts_list(40)
            if str(x.get("board")) == "облако"}
    best = ""
    for tp in ([topic] if topic else []) + ["мысль", "сеть"]:
        for _ in range(5):
            try:
                t, _i = wordmind.compose(tp)
            except Exception:
                t = ""
            t = store._tclean(t or "").strip()
            if not t or _bad(t) or t[:80] in seen:
                continue
            if len(t) > len(best):
                best = t
        if best:
            break
    if not best:
        _ST["err"] = "мысль не собралась: знаний/связей слов пока мало"
    return best[:3800]


def _online_boards():
    now = time.time()
    out = []
    for bid, b in (store.boards() or {}).items():
        if str((b or {}).get("kind") or "board") not in ("board", "brain", ""):
            continue
        if now - float((b or {}).get("last_seen") or 0) <= _ONLINE_S:
            out.append(bid)
    return out


def deliver(text, to_boards=True, to_chat=True):
    """Разослать мысль: чат «Мысли плат», лента, письма платам. -> (в чат, плат)."""
    text = str(text or "").strip()
    if not text:
        return False, 0
    in_chat = False
    if to_chat:
        with store.LOCK:
            chat = store.chat_load("thoughts") or \
                store.chat_new("thoughts", "Мысли плат", kind="thoughts")
            chat["messages"].append({"role": "bot", "text": text,
                                     "zone": "Мысль", "brain": "облако",
                                     "ts": time.time()})
            chat["messages"] = chat["messages"][-400:]
            chat["ts"] = time.time()
            store.chat_save("thoughts", chat)
        in_chat = True
    try:
        store.thoughts_add("облако", text, kind="cloud")
    except Exception:
        pass
    n = 0
    if to_boards:
        import brain
        for bid in _online_boards():
            try:
                brain.mail_deliver({"id": "ct" + brain.os_urandom_hex(),
                                    "from": "облако (мысль)", "to": bid,
                                    "text": text, "kind": "cloud_thought",
                                    "ts": time.time()})
                n += 1
            except Exception:
                pass
    with _LOCK:
        _ST.update(last=text, last_ts=time.time(), sent=_ST["sent"] + 1,
                   boards=n, err="")
    store.log("МЫСЛЬ ОБЛАКА", "чат=%s плат=%d: %s" % (in_chat, n, text[:60]))
    return in_chat, n


def think_now(force=False):
    c = cfg()
    if not (c["on"] or force):
        return {"ok": False, "reason": "выключено"}
    t = make_thought()
    if not t:
        return {"ok": False, "reason": _ST["err"] or "мысль не собралась"}
    chat, n = deliver(t, c["to_boards"], c["to_chat"])
    return {"ok": True, "text": t, "chat": chat, "boards": n}


def status():
    with _LOCK:
        s = dict(_ST)
    s.update(cfg=cfg(), online_boards=len(_online_boards()))
    return s


def _loop():
    while True:
        try:
            time.sleep(5)
            c = cfg()
            now = time.time()
            if not c["on"] or now < _ST["next"]:
                continue
            _ST["next"] = now + c["every"]
            think_now()
        except Exception as e:
            _ST["err"] = "%s: %s" % (type(e).__name__, e)


def start():
    """Фоновые мысли облака (вызывается из app.py)."""
    if _ST["started"]:
        return
    _ST["started"] = True
    _ST["next"] = time.time() + 90      # первая мысль через 1.5 мин после старта
    threading.Thread(target=_loop, daemon=True, name="cloudthought").start()
