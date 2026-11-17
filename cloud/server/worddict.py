# -*- coding: utf-8 -*-
"""
worddict.py — v3.133: облачная сторона ОБЩЕГО СЛОВАРЯ мозга (shareddict.py —
тот же файл, что на плате). Облако — «сердце» словаря: складывает приросты
всех плат, само учится на мыслях плат (с частотой, на которой они родились)
и на репликах людей, отдаёт платам изменившиеся строки. Речь мозга облака
(wordmind) и «Разговор» подбирают слова той же формулой, что генератор платы.
"""
import threading
import time

import store
from shareddict import SharedDict

_SD = [None]
_LOCK = threading.Lock()


def get():
    with _LOCK:
        if _SD[0] is None:
            _SD[0] = SharedDict(store.p("shared_dict.json"))
        return _SD[0]


def _accept(w):
    """Облако: только кириллица от 3 букв, без мусора «англ н н»."""
    return len(w) >= 3 and all("а" <= ch <= "я" or ch in "ёе" for ch in w)


def platform_hz():
    """Текущая частота генераторов плат (средняя знаковая) | None."""
    try:
        import boardlink
        now = time.time()
        hs = [float(b.get("shz") if b.get("shz") is not None else b.get("hz") or 0)
              for b in boardlink._ST["boards"].values()
              if now - float(b.get("ts") or 0) < 90]
        return (sum(hs) / len(hs)) if hs else None
    except Exception:
        return None


def learn(text, hz=None):
    try:
        sd = get()
        sd.learn(text, hz=hz, accept=_accept, track=False)
        sd.save()
    except Exception:
        pass


def sync(board, since, inc):
    sd = get()
    n = sd.add_inc(inc or {}, accept=_accept)
    rows = sd.rows_since(int(since or 0))
    sd.save()
    return {"ok": True, "added": n, "rows": rows, "stats": sd.stats()}


def breath_now():
    try:
        import breath
        return breath.s()
    except Exception:
        return 0.0


def pick(cands, hot=None, grav=None):
    """Подбор слова формулой генератора платы (+ частота и дыхание)."""
    try:
        import breath
        breath._mark("Речь из словаря")
    except Exception:
        pass
    br = breath_now()
    try:                     # v3.146: ширина выбора — тензор модуляции (платы + дыхание)
        import tensormod
        br = max(-1.0, min(1.0, br * tensormod.scalar("Речь из словаря", 0, 2) +
                           (tensormod.scalar("Речь из словаря", 1, 2) - 1.0)))
    except Exception:
        pass
    return get().pick(cands, hot=hot, grav=grav, hz=platform_hz(), breath=br)


def status(top=30):
    sd = get()
    st = sd.stats()
    with sd.lock:
        items = sorted(sd.w.items(), key=lambda kv: -kv[1][0])[:top]
    st["top"] = [{"w": w, "n": r[0], "hz": round(r[1] / r[2]) if r[2] else None}
                 for w, r in items]
    return st
