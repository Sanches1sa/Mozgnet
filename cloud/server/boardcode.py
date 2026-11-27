# boardcode.py — v3.178: КОД И КОМАНДЫ НА ПЛАТЕ ЧЕРЕЗ СВЯЗЬ МОЗГОВ.
#
# Облако ставит задание в очередь платы; плата (v2.93+) забирает его в ответе
# /api/link (code_jobs), выполняет и возвращает результат POST
# /api/board/codejob. Режимы:
#   py_check — синтаксис Python на плате;
#   py_run   — запуск Python на плате (раздел «Код»: проверка на железе);
#   sh       — команда в контейнере приложения платы (окно «Серверы»).
# Плата может запретить это у себя (code_from_cloud / admin_from_cloud).
# Ставит задания только владелец (маршруты панели без токена плат).
import collections
import threading
import time
import uuid

import store

_LOCK = threading.RLock()
_Q = collections.defaultdict(collections.deque)    # плата -> задания
_RES = {}                                          # id -> результат
_EV = {}                                           # id -> Event
_JOBS = collections.OrderedDict()                  # id -> задание (журнал)
_BLOCKED = {}                                      # плата -> ts, когда она отказала в исполнении
_SEEN = {}                                         # плата -> {ts, ver, codejobs}


def note_link(board, ver=None, codejobs=None):
    with _LOCK:
        _SEEN[str(board or "?")[:40]] = {"ts": time.time(), "ver": str(ver or ""),
                                          "cj": codejobs if isinstance(codejobs, dict) else None}


def _ver_ok(v):
    try:
        p = [int(x) for x in str(v or "0").split(".")[:2]]
        return p[0] > 2 or (p[0] == 2 and p[1] >= 93)
    except Exception:
        return False


_REFUSAL = ("питоны убраны", "питоны» сняты", "исполнение кода отключено", "запрещено настройкой")


def is_refusal(out):
    o = str(out or "").lower()
    return any(x in o for x in _REFUSAL)


def mark_blocked(board, on=True):
    with _LOCK:
        if on:
            _BLOCKED[str(board)[:40]] = time.time()
        else:
            _BLOCKED.pop(str(board)[:40], None)


def blocked(board, ttl=1800):
    with _LOCK:
        return time.time() - _BLOCKED.get(str(board)[:40], 0) < ttl


def boards(max_age=40):
    """Платы, которые сейчас на связи и умеют задания (v2.93+)."""
    now = time.time()
    with _LOCK:
        out = []
        for b, d in _SEEN.items():
            if now - d["ts"] > max_age:
                continue
            cj = d.get("cj") or {}
            out.append({"board": b, "ver": d["ver"], "age": int(now - d["ts"]),
                        "ok": _ver_ok(d["ver"]) and bool(cj),
                        "code": cj.get("code", False) and not blocked(b),
                        "admin": cj.get("admin", False) and not blocked(b),
                        "hw": cj.get("hw", False) and not blocked(b),
                        "blocked": blocked(b),
                        "done": cj.get("done", 0)})
        return sorted(out, key=lambda x: x["age"])


def pick_board(need="code"):
    for b in boards():
        if b["ok"] and b.get(need):
            return b["board"]
    return ""


def submit(code, mode="py_check", board="", timeout=20):
    mode = mode if mode in ("py_check", "py_run", "sh", "hw") else "py_check"
    board = str(board or "")[:40] or pick_board({"sh": "admin", "hw": "hw"}.get(mode, "code"))
    if not board:
        return {"ok": False, "error": "нет платы на связи, которой разрешены задания%s" % (
            " аппаратного уровня (нужна плата 2.111+ с hw_on)" if mode == "hw" else "")}
    jid = uuid.uuid4().hex[:12]
    job = {"id": jid, "mode": mode, "code": str(code or "")[:60000],
           "timeout": max(2, min(120, int(timeout or 20)))}
    with _LOCK:
        _Q[board].append(job)
        _EV[jid] = threading.Event()
        _JOBS[jid] = {"id": jid, "board": board, "mode": mode, "ts": time.time(),
                      "code": job["code"][:300], "status": "в очереди"}
        while len(_JOBS) > 200:
            old, _ = _JOBS.popitem(last=False)
            _RES.pop(old, None)
            _EV.pop(old, None)
    store.log("ПЛАТА-КОД", "%s -> %s: %s" % (mode, board, job["code"][:100].replace("\n", " ⏎ ")))
    return {"ok": True, "id": jid, "board": board}


def take(board):
    """Для ответа link: задания этой платы (не больше 3)."""
    board = str(board or "")[:40]
    out = []
    with _LOCK:
        q = _Q.get(board)
        while q and len(out) < 3:
            j = q.popleft()
            out.append(j)
            if j["id"] in _JOBS:
                _JOBS[j["id"]]["status"] = "у платы"
    return out


def pending(board):
    with _LOCK:
        return bool(_Q.get(str(board or "")[:40]))


def result_in(b):
    jid = str(b.get("id") or "")[:40]
    with _LOCK:
        if jid not in _JOBS:
            return {"ok": False, "error": "нет такого задания"}
        res = {k: b.get(k) for k in ("ok", "rc", "out", "ms", "py", "machine", "board", "mode")}
        res["out"] = str(res.get("out") or "")[-8000:]
        _RES[jid] = res
        _JOBS[jid]["status"] = "готово" if res.get("ok") else "ошибка"
        ev = _EV.get(jid)
    if ev:
        ev.set()
    return {"ok": True}


def wait(jid, timeout=40):
    ev = _EV.get(jid)
    if ev:
        ev.wait(timeout)
    with _LOCK:
        r = _RES.get(jid)
        if r is None and jid in _JOBS:
            _JOBS[jid]["status"] = "плата не ответила"
    return r


def run(code, mode="py_check", board="", timeout=20, wait_s=None):
    """Поставить и дождаться: {ok, rc, out, board, ...} | {ok: False, error}."""
    s = submit(code, mode, board, timeout)
    if not s.get("ok"):
        return s
    r = wait(s["id"], wait_s or (timeout + 25))
    if r is None:
        return {"ok": False, "error": "плата %s не ответила (связь раз в 5 с)" % s["board"],
                "board": s["board"], "id": s["id"]}
    return dict(r, id=s["id"])


def jobs(n=30):
    with _LOCK:
        out = []
        for jid, j in reversed(list(_JOBS.items())[-n:]):
            out.append(dict(j, res=_RES.get(jid)))
        return out
