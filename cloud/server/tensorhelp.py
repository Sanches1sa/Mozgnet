# -*- coding: utf-8 -*-
# ===========================================================================
# tensorhelp.py — ОБЛАКО СЧИТАЕТ ЗАДАЧИ ТЕНЗОРНОЙ СЕТИ ПЛАТЫ (v3.27)
# ---------------------------------------------------------------------------
# Плата neuro_12 v2.39 получила тензорную сеть (MLP: 76 входов -> H
# нейронов ReLU -> зоны, H = 8..64 по частоте генератора) и два маршрута
# своего веб-хоста :7000 для внешнего вычислителя:
#   GET  /api/tensor/job?bench=<оп/с>  -> {"ok", "job": {id, H, IN, Z,
#        epochs, lr, seed, samples: [[[[i, v], ...], z], ...],
#        init: {W1, b1, W2, b2} | null}} | {"ok", "job": null}
#   POST /api/tensor/result {id, W1[H][IN], b1[H], W2[Z][H], b2[Z],
#        loss, acc, ops} -> {"ok"} | {"ok": false, "error"}
# Плата отдаёт задачу «компьютеру», если тот опрашивал её не позже 8 с
# назад, и ждёт результат до 30 с; объём задачи подгоняет под bench (оп/с)
# — примерно на 3 секунды счёта. Раньше этим «компьютером» мог быть только
# браузер с открытой страницей :7000. Теперь им становится ОБЛАКО: мост
# видит плату — облако опрашивает её каждые 4 с и считает задачи тем же
# ядром, что и плата (одинаковые ГСЧ, инициализация, прямой проход, SGD).
# Плата сама проверяет форму и числа результата и принимает веса только
# если точность не хуже прежней — облако ничего «не навязывает».
# Выключатель — настройка tensor_help.
# ===========================================================================
import math
import threading
import time

import store

POLL_SEC = 4.0                 # < 8 с: плата считает помощника подключённым
BENCH_EVERY = 600.0            # перемерять скорость раз в 10 мин

STATE = {"on": None, "polls": 0, "jobs": 0, "done": 0, "fails": 0,
         "last_err": "", "bench": 0.0, "bench_ts": 0.0, "last": {},
         "busy": False, "last_poll": 0.0,
         # задачи, которые плата САМА прислала в песочницу облака (путь
         # «плата -> сервер» работает и когда мост до платы не достаёт)
         "sandbox_runs": 0, "sandbox_fails": 0, "sandbox_ts": 0.0}


def note_sandbox(ok):
    """Плата посчитала задачу тензора в песочнице облака (/api/sandbox/run)."""
    with _LOCK:
        STATE["sandbox_runs" if ok else "sandbox_fails"] += 1
        STATE["sandbox_ts"] = time.time()
_LOCK = threading.Lock()
_STARTED = [False]


def enabled():
    return bool(store.settings().get("tensor_help", True))


# ------------------- ядро (то же, что у платы, v2.39) -----------------------
def tn_rng(seed):
    st = [int(seed) % 2147483647 or 1]

    def nxt():
        st[0] = (st[0] * 48271) % 2147483647
        return st[0] / 2147483647.0
    return nxt


def tn_init(H, IN, Z, seed):
    r = tn_rng(seed)
    a2 = math.sqrt(6.0 / (H + Z))
    W1 = [[(r() * 2 - 1) * 0.3 for _i in range(IN)] for _j in range(H)]
    b1 = [0.01] * H
    W2 = [[(r() * 2 - 1) * a2 for _j in range(H)] for _k in range(Z)]
    b2 = [0.0] * Z
    return W1, b1, W2, b2


def tn_forward(W1, b1, W2, b2, x):
    h = []
    for j in range(len(W1)):
        row = W1[j]
        s = b1[j]
        for i, v in x:
            s += row[i] * v
        h.append(s if s > 0 else 0.0)
    lo = []
    for k in range(len(W2)):
        row = W2[k]
        s = b2[k]
        for j in range(len(h)):
            if h[j]:
                s += row[j] * h[j]
        lo.append(s)
    m = max(lo)
    ex = [math.exp(v - m) for v in lo]
    t = sum(ex)
    return h, [v / t for v in ex]


def tn_eval(W1, b1, W2, b2, samples):
    if not samples:
        return 0.0
    ok = 0
    for x, z, _w in samples:
        p = tn_forward(W1, b1, W2, b2, x)[1]
        if max(range(len(p)), key=lambda k: p[k]) == z:
            ok += 1
    return ok / float(len(samples))


def tn_train(samples, H, IN, Z, epochs, lr, seed, init=None, l2=0.0001):
    if init:
        W1, b1, W2, b2 = init
    else:
        W1, b1, W2, b2 = tn_init(H, IN, Z, seed)
    r = tn_rng(seed + 7)
    order = list(range(len(samples)))
    loss = 0.0
    ops = 0
    for ep in range(int(epochs)):
        for i in range(len(order) - 1, 0, -1):
            j = int(r() * (i + 1))
            order[i], order[j] = order[j], order[i]
        rate = lr / (1.0 + 0.15 * ep)
        loss = 0.0
        for n in order:
            x, z, w = samples[n]
            h, p = tn_forward(W1, b1, W2, b2, x)
            loss -= math.log(max(p[z], 1e-9)) * w
            gh = [0.0] * H
            for k in range(Z):
                gk = (p[k] - (1.0 if k == z else 0.0)) * w
                if not gk:
                    continue
                row = W2[k]
                for j in range(H):
                    if h[j]:
                        gh[j] += row[j] * gk
                        row[j] -= rate * (gk * h[j] + l2 * row[j])
                b2[k] -= rate * gk
            for j in range(H):
                gj = gh[j]
                if gj:
                    row = W1[j]
                    for i, v in x:
                        row[i] -= rate * gj * v
                    b1[j] -= rate * gj
            ops += H * len(x) * 2 + Z * H * 2
        loss = loss / max(1, len(samples))
    acc = tn_eval(W1, b1, W2, b2, samples)
    return W1, b1, W2, b2, loss, acc, ops


# ------------------------------ задача платы --------------------------------
def _shape(job):
    """Проверить и разобрать задачу платы. Возврат (args, ошибка)."""
    try:
        H, IN, Z = int(job["H"]), int(job["IN"]), int(job["Z"])
        ep, lr, seed = int(job["epochs"]), float(job["lr"]), int(job["seed"])
        if not (1 <= H <= 256 and 1 <= IN <= 512 and 1 <= Z <= 256 and
                1 <= ep <= 500):
            return None, "размеры задачи вне разумных границ"
        samples = []
        for item in job.get("samples") or []:
            feats, z = item[0], int(item[1])
            if not 0 <= z < Z:
                return None, "зона примера вне диапазона"
            x = [(int(i), float(v)) for i, v in feats if 0 <= int(i) < IN]
            samples.append((x, z, 1.0))
        if not samples:
            return None, "в задаче нет примеров"
        init = job.get("init")
        if init:
            W1 = [[float(v) for v in row] for row in init["W1"]]
            b1 = [float(v) for v in init["b1"]]
            W2 = [[float(v) for v in row] for row in init["W2"]]
            b2 = [float(v) for v in init["b2"]]
            if len(W1) != H or len(b1) != H or len(W2) != Z or \
                    len(b2) != Z or any(len(r) != IN for r in W1) or \
                    any(len(r) != H for r in W2):
                return None, "форма начальных весов не совпала"
            init = (W1, b1, W2, b2)
        return (samples, H, IN, Z, ep, lr, seed, init), ""
    except Exception as e:
        return None, "задача не разобрана: %s" % e


def solve(job):
    """Посчитать задачу платы. Возврат (тело результата, ошибка)."""
    args, err = _shape(job)
    if err:
        return None, err
    samples, H, IN, Z, ep, lr, seed, init = args
    t0 = time.time()
    W1, b1, W2, b2, loss, acc, ops = tn_train(samples, H, IN, Z, ep, lr,
                                              seed, init=init)
    ms = int((time.time() - t0) * 1000)
    body = {"id": str(job["id"]), "W1": W1, "b1": b1, "W2": W2, "b2": b2,
            "loss": round(float(loss), 6), "acc": round(float(acc), 4),
            "ops": int(ops)}
    meta = {"H": H, "Z": Z, "epochs": ep, "n": len(samples),
            "loss": round(loss, 4), "acc": round(acc, 3), "ms": ms,
            "ops": int(ops)}
    return (body, meta), ""


def bench():
    """Скорость облака в операциях ядра в секунду (честный замер)."""
    r = tn_rng(12345)
    samples = [([(int(r() * 76), r()) for _ in range(14)], int(r() * 8), 1.0)
               for _ in range(40)]
    t0 = time.time()
    _W1, _b1, _W2, _b2, _l, _a, ops = tn_train(samples, 16, 76, 8, 2, 0.05,
                                               7)
    dt = max(1e-3, time.time() - t0)
    return ops / dt


# --------------------------------- цикл -------------------------------------
def _work(base, job):
    import bridge
    with _LOCK:
        STATE["busy"] = True
        STATE["jobs"] += 1
    try:
        res, err = solve(job)
        if err:
            raise ValueError(err)
        body, meta = res
        r = bridge._post(base, "/api/tensor/result", body, timeout=10.0)
        if not isinstance(r, dict) or not r.get("ok"):
            raise ValueError("плата не приняла результат: %s"
                             % ((r or {}).get("error") or r))
        with _LOCK:
            STATE["done"] += 1
            STATE["last"] = dict(meta, ts=time.time(), id=str(job["id"]))
            STATE["last_err"] = ""
        store.log("ТЕНЗОР", "облако посчитало задачу платы: H=%d, примеров "
                  "%d, эпох %d, точность %.0f%%, %d мс" % (
                      meta["H"], meta["n"], meta["epochs"],
                      meta["acc"] * 100, meta["ms"]))
    except Exception as e:
        with _LOCK:
            STATE["fails"] += 1
            STATE["last_err"] = str(e)[:200]
    finally:
        with _LOCK:
            STATE["busy"] = False


def tick():
    """Один опрос платы (если мост её видит)."""
    import bridge
    on = enabled() and bridge.enabled()
    STATE["on"] = on
    if not on or bridge.STATE.get("online") is not True:
        return
    if time.time() - STATE["bench_ts"] > BENCH_EVERY:
        try:
            STATE["bench"] = bench()
        except Exception:
            STATE["bench"] = 0.0
        STATE["bench_ts"] = time.time()
    base = bridge.active_url()
    try:
        r = bridge._get(base, "/api/tensor/job?bench=%d"
                        % int(STATE["bench"] if not STATE["busy"] else 0),
                        timeout=3.0)
    except Exception as e:
        # старая прошивка без тензора отвечает 404 — это не ошибка облака
        STATE["last_err"] = ("плата без тензорной сети (нет /api/tensor/job)"
                             if "404" in str(e) else str(e)[:160])
        return
    with _LOCK:
        STATE["polls"] += 1
        STATE["last_poll"] = time.time()
    job = (r or {}).get("job") if isinstance(r, dict) else None
    if job and not STATE["busy"]:
        threading.Thread(target=_work, args=(base, job), daemon=True,
                         name="tensor-job").start()


def _loop():
    while True:
        try:
            tick()
        except Exception as e:
            STATE["last_err"] = str(e)[:160]
        time.sleep(POLL_SEC)


def start():
    if _STARTED[0]:
        return
    _STARTED[0] = True
    threading.Thread(target=_loop, daemon=True, name="tensor-help").start()


def status():
    with _LOCK:
        st = dict(STATE)
    if st.get("on") is None:            # цикл ещё не прошёл — по настройке
        try:
            st["on"] = enabled()
        except Exception:
            st["on"] = False
    st["bench"] = int(st.get("bench") or 0)
    st["alive"] = time.time() - float(st.get("last_poll") or 0) < 8.0
    return st
