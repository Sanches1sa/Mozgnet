# -*- coding: utf-8 -*-
"""
gamechat.py — v3.181: ОБЩИЙ ЧАТ плата ↔ облако ↔ боты ↔ своя LLM.

Одна лента сообщений (кольцо на 300 записей, последние 120 — на диске,
data/gamechat.json). Кто пишет:
  • люди — со страниц /doom и /gubble (поле «сказать») и из чата ПЛАТЫ
    (плата присылает свои реплики в POST /api/link → chat_out);
  • мозг платы — мысли об игре (POST api/doom/bot → say) и ответы бота
    в чате платы;
  • своя LLM — мысли-тактики в играх и ОТВЕТЫ на реплики людей
    (обращение «ллм», «llm», «@llm» или вопрос к ней в общем чате);
  • облако — события игр: кто вошёл/вышел, уровень пройден, приглашения,
    переход LLM между РОК-12 и Gubble 2.

Доставка на плату: ответ /api/link несёт chat {seq, msgs} — новые записи
после курсора платы (chat_seq); плата кладёт их в свой чат (зона «Общий
чат») и не шлёт обратно (нет эха). Страницы игр читают GET api/gamechat.
"""
import threading
import time
from collections import deque

import store

_LOCK = threading.RLock()
_Q = deque(maxlen=300)
_ST = {"seq": 0, "loaded": False, "saved": 0.0, "llm_busy": False, "llm_ts": 0.0}
LLM_CALL = ("ллм", "llm", "@llm", "нейросеть", "своя llm", "своя ллм")


def _load():
    if _ST["loaded"]:
        return
    _ST["loaded"] = True
    try:
        d = store.load_json("gamechat.json", {}) or {}
        for m in d.get("msgs") or []:
            if isinstance(m, dict) and m.get("text"):
                _Q.append(m)
        _ST["seq"] = int(d.get("seq") or (max((m.get("seq", 0) for m in _Q), default=0)))
    except Exception:
        pass


def _save(force=False):
    now = time.time()
    if not force and now - _ST["saved"] < 15:
        return
    _ST["saved"] = now
    try:
        store.save_json("gamechat.json", {"seq": _ST["seq"], "msgs": list(_Q)[-120:]})
    except Exception:
        pass


def post(who, text, kind="chat", game="", src="cloud"):
    """Записать сообщение. kind: chat | thought | event | invite | llm.
    src: cloud | llm | board:<id> | human:<pid> — чтобы не слать эхо автору."""
    text = str(text or "").strip()
    if not text:
        return None
    with _LOCK:
        _load()
        _ST["seq"] += 1
        m = {"seq": _ST["seq"], "ts": time.time(), "who": str(who or "")[:40],
             "text": text[:600], "kind": str(kind or "chat")[:12],
             "game": str(game or "")[:12], "src": str(src or "cloud")[:60]}
        _Q.append(m)
        _save()
    if kind == "chat" and src != "llm" and _wants_llm(text):
        threading.Thread(target=_llm_reply, args=(who, text, game), daemon=True).start()
    return m


def since(seq=0, limit=40, exclude_src=None):
    try:
        seq = int(seq or 0)
    except (TypeError, ValueError):
        seq = 0
    with _LOCK:
        _load()
        out = [m for m in _Q if m["seq"] > seq and (not exclude_src or m["src"] != exclude_src)]
        if seq == 0:
            out = out[-20:]
        return out[-int(limit):], _ST["seq"]


def link(board, chat_seq, chat_out):
    """Из POST /api/link платы: принять её реплики, отдать новые чужие."""
    board = str(board or "board")[:40]
    src = "board:" + board
    if isinstance(chat_out, list):
        for m in chat_out[:10]:
            if not isinstance(m, dict) or not m.get("text"):
                continue
            role = str(m.get("role") or "")
            who = ("%s (чат платы)" % str(m.get("user") or "владелец")[:20]) if role == "user" \
                else "мозг %s" % board
            post(who, m["text"], kind="chat" if role == "user" else "thought",
                 game=str(m.get("zone") or "плата")[:12], src=src)
    msgs, cur = since(chat_seq, limit=20, exclude_src=src)
    if chat_seq in (None, "", 0, "0"):           # первая связь — не вываливать историю
        msgs = msgs[-5:]
    # мысли (тактики LLM каждые ~12 с) не должны забивать чат платы:
    # на плату — не чаще одной мысли на автора в минуту; реплики и события — все
    th = _THROTTLE.setdefault(board, {})
    out = []
    for m in msgs:
        if m["kind"] == "thought":
            if m["ts"] - th.get(m["who"], 0.0) < 60.0:
                continue
            th[m["who"]] = m["ts"]
        out.append(m)
    return {"seq": cur, "msgs": [{"seq": m["seq"], "ts": m["ts"], "who": m["who"],
                                  "text": m["text"], "kind": m["kind"], "game": m["game"]}
                                 for m in out]}


_THROTTLE = {}      # плата -> {автор: ts последней доставленной мысли}


def _wants_llm(text):
    t = text.lower()
    return any(k in t for k in LLM_CALL) or t.rstrip().endswith("?") and len(t) > 8


def _llm_reply(who, text, game):
    """Своя LLM отвечает в общем чате (не чаще раза в 4 с, по одному)."""
    now = time.time()
    with _LOCK:
        if _ST["llm_busy"] or now - _ST["llm_ts"] < 4.0:
            return
        _ST["llm_busy"], _ST["llm_ts"] = True, now
    try:
        import llmlink
        try:
            import games
            gs = games.status()
            where = ("Сейчас я играю в %s." % gs["llm_game_name"]) if gs.get("llm_game") else \
                "Сейчас я не играю."
        except Exception:
            where = ""
        q = text
        for k in LLM_CALL:
            q = q.replace(k, " ").replace(k.upper(), " ").replace(k.capitalize(), " ")
        q = " ".join(q.split()) or text
        r = llmlink.chat(q, ctx={"chat": "общий чат игр", "from": who, "game": game, "note": where},
                         sender="общий чат")
        reply = str((r or {}).get("reply") or "").strip()
        if reply:
            post("своя LLM", reply[:400], kind="llm", game=game or "", src="llm")
    except Exception as e:
        post("облако", "своя LLM не смогла ответить: %s" % str(e)[:80], kind="event", src="cloud")
    finally:
        _ST["llm_busy"] = False


def status():
    with _LOCK:
        _load()
        return {"seq": _ST["seq"], "count": len(_Q),
                "last": list(_Q)[-1] if _Q else None}
