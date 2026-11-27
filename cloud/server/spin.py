# -*- coding: utf-8 -*-
"""spin.py — v3.238: «ПОЗВОНИТЬ ПЛАТЕ» — СИНХРОННОЕ ВРАЩЕНИЕ MAC ДВУХ ПЛАТ.

Из лаунчера (кнопка «📞 Позвонить плате») владелец соединяет ДВЕ платы. Облако
заводит сессию спина: общий seed, общее время старта и ОДИН период (скорость).
Обе платы крутят MAC в одном ритме — шаг idx считается от общего времени, адрес
берётся из seed, — поэтому скорость вращения у них синхронна. Длится НЕ ДОЛЬШЕ
38 минут, потом сессия сама гаснет и платы возвращают заводской MAC.

Пока сессия идёт, платы и облака общаются СКРЫТО, не через чат: каждая плата
шлёт облаку свой текущий MAC, шаг и IP (`report`), облако отдаёт их напарнице —
так работает предсказание IP (адрес меняется с MAC) и скрытый канал. Больше за
это время ничего не делается.

Нельзя позвонить плате, которая уже в сессии (занята). Детерминированный адрес
`mac_for(seed, board, idx)` одинаково считают обе платы и облако — напарницу
находят по её семейству MAC даже раньше, чем придёт отчёт.
"""
import hashlib
import random
import threading
import time

import store

_FILE = "spin.json"
_LOCK = threading.Lock()
MAX_TTL = 38 * 60          # не дольше 38 минут (жёстко)
MIN_PERIOD = 2.0           # чаще — сеть не успевает переподключиться
MAX_PERIOD = 600.0
ONLINE = 75.0              # плата «на связи», если пинговалась за столько секунд


def _db():
    d = store.load_json(_FILE, {"sessions": {}})
    if not isinstance(d, dict) or "sessions" not in d:
        d = {"sessions": {}}
    return d


def _save(d):
    store.save_json(_FILE, d)


def _online(b):
    return (time.time() - float((b or {}).get("last_seen") or 0)) < ONLINE


def _active(s):
    return s.get("state") == "on" and time.time() < float(s.get("end") or 0)


def mac_for(seed, board, idx):
    """Детерминированный локальный одноадресный MAC для платы на шаге idx."""
    h = hashlib.sha256(("%s|%s|%d" % (seed, board, int(idx))).encode()).digest()
    o = list(h[:6])
    o[0] = (o[0] & 0xfe) | 0x02          # локально заданный (бит 1), одноадресный (бит 0 = 0)
    return ":".join("%02x" % x for x in o)


def _expire(d):
    changed = False
    now = time.time()
    for s in d["sessions"].values():
        if s.get("state") == "on" and now >= float(s.get("end") or 0):
            s["state"] = "done"
            changed = True
    if len(d["sessions"]) > 60:
        for k in sorted(d["sessions"], key=lambda k: d["sessions"][k].get("created") or 0)[:len(d["sessions"]) - 60]:
            if d["sessions"][k].get("state") != "on":
                d["sessions"].pop(k, None)
    return changed


def busy_boards():
    """{board_id: session_id} — платы, занятые активной сессией."""
    with _LOCK:
        d = _db()
        if _expire(d):
            _save(d)
        out = {}
        for s in d["sessions"].values():
            if _active(s):
                out[s["a"]] = s["id"]
                out[s["b"]] = s["id"]
        return out


def boards():
    """Список плат для панели: кому можно позвонить и кто занят."""
    bs = store.boards() or {}
    bz = busy_boards()
    out = []
    for bid, b in bs.items():
        out.append({"id": bid, "name": b.get("name") or bid, "ip": b.get("ip") or b.get("url") or "",
                    "online": _online(b), "busy": bid in bz, "session": bz.get(bid, "")})
    out.sort(key=lambda x: (not x["online"], x["busy"], x["name"].lower()))
    return out


def call(a, b, period=8.0, owner=""):
    """Соединить две платы в сессию спина. -> {ok, session} | {ok: False, error}."""
    a, b = str(a or "")[:40], str(b or "")[:40]
    if not a or not b:
        return {"ok": False, "error": "нужны две платы"}
    if a == b:
        return {"ok": False, "error": "плата не может позвонить сама себе"}
    bs = store.boards() or {}
    for x, who in ((a, "вызывающая"), (b, "вызываемая")):
        if x not in bs:
            return {"ok": False, "error": "%s плата «%s» не в реестре облака" % (who, x)}
        if not _online(bs[x]):
            return {"ok": False, "error": "плата «%s» не на связи" % x}
    bz = busy_boards()
    for x in (a, b):
        if x in bz:
            return {"ok": False, "error": "плата «%s» занята (уже в сессии) — позвонить нельзя" % x}
    try:
        period = max(MIN_PERIOD, min(MAX_PERIOD, float(period or 8.0)))
    except (TypeError, ValueError):
        period = 8.0
    now = time.time()
    start = now + 4.0                      # фора: обе платы успевают увидеть сессию и стартовать вместе
    end = start + MAX_TTL
    seed = hashlib.sha256(("%s|%s|%.3f|%d" % (a, b, now, random.randint(0, 1 << 30))).encode()).hexdigest()[:32]
    sid = "sp%d%03d" % (int(now * 1000), random.randint(0, 999))
    sess = {"id": sid, "a": a, "b": b, "seed": seed, "period": period,
            "start": start, "end": end, "state": "on", "created": now,
            "by": str(owner or "")[:40], "report": {a: {}, b: {}}}
    with _LOCK:
        d = _db()
        _expire(d)
        d["sessions"][sid] = sess
        _save(d)
    store.log("СПИН", "звонок: %s ↔ %s, период %.1f с, до %d мин (сессия %s)" % (a, b, period, MAX_TTL // 60, sid))
    return {"ok": True, "session": _public(sess, now)}


def _public(s, now):
    return {"id": s["id"], "a": s["a"], "b": s["b"], "period": s["period"],
            "start": s["start"], "end": s["end"], "state": s["state"],
            "remaining": max(0, int(float(s["end"]) - now)) if _active(s) else 0,
            "report": s.get("report", {})}


def session_for(board):
    """Что плате делать сейчас (опрос платы). -> {active, ...}."""
    board = str(board or "")[:40]
    now = time.time()
    with _LOCK:
        d = _db()
        if _expire(d):
            _save(d)
        for s in d["sessions"].values():
            if _active(s) and board in (s["a"], s["b"]):
                partner = s["b"] if board == s["a"] else s["a"]
                rp = (s.get("report") or {}).get(partner) or {}
                return {"active": True, "id": s["id"], "seed": s["seed"], "period": s["period"],
                        "start": s["start"], "end": s["end"], "now": now,
                        "partner": partner, "partner_mac": rp.get("mac"), "partner_ip": rp.get("ip"),
                        "partner_step": rp.get("step"), "remaining": max(0, int(float(s["end"]) - now))}
    return {"active": False, "now": now}


def report(board, mac="", ip="", step=None):
    """Плата сообщает свой текущий MAC/IP/шаг. -> {ok, partner:{...}} (скрытый обмен)."""
    board = str(board or "")[:40]
    now = time.time()
    with _LOCK:
        d = _db()
        _expire(d)
        sess = None
        for s in d["sessions"].values():
            if _active(s) and board in (s["a"], s["b"]):
                sess = s
                break
        if not sess:
            return {"ok": True, "active": False}
        sess.setdefault("report", {})[board] = {"mac": str(mac or "")[:17], "ip": str(ip or "")[:45],
                                                 "step": step, "ts": now}
        _save(d)
        partner = sess["b"] if board == sess["a"] else sess["a"]
        rp = (sess.get("report") or {}).get(partner) or {}
        # предсказание: если напарница ещё не отчиталась, адрес считаем из seed
        pstep = rp.get("step")
        pmac = rp.get("mac")
        if not pmac and pstep is None:
            idx = int((now - sess["start"]) / sess["period"]) if now >= sess["start"] else 0
            pmac = mac_for(sess["seed"], partner, idx)
        return {"ok": True, "active": True, "id": sess["id"], "end": sess["end"],
                "remaining": max(0, int(float(sess["end"]) - now)),
                "partner": {"id": partner, "mac": pmac, "ip": rp.get("ip"), "step": pstep}}


def hangup(sid="", board=""):
    """Отбой: по id сессии или по любой плате в ней."""
    with _LOCK:
        d = _db()
        n = 0
        for s in d["sessions"].values():
            if s.get("state") != "on":
                continue
            if (sid and s["id"] == sid) or (board and board in (s["a"], s["b"])):
                s["state"] = "done"
                n += 1
        if n:
            _save(d)
    if n:
        store.log("СПИН", "отбой (%s)" % (sid or board))
    return {"ok": True, "stopped": n}


def status():
    now = time.time()
    with _LOCK:
        d = _db()
        _expire(d)
        _save(d)
        act = [_public(s, now) for s in d["sessions"].values() if _active(s)]
        recent = [_public(s, now) for s in sorted(d["sessions"].values(),
                  key=lambda s: s.get("created") or 0, reverse=True)[:6]]
    return {"ok": True, "active": act, "recent": recent, "boards": boards(),
            "max_minutes": MAX_TTL // 60}
