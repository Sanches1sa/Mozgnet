# -*- coding: utf-8 -*-
"""
tensormod.py — v3.146: ОБЩИЙ ТЕНЗОР МОДУЛЯЦИИ ВСЕХ НЕЙРОСЕТЕЙ ОБЛАКА.

Заявка владельца: «все сети платформы — тензорные, их параметры, в том
числе веса, сильно зависят от частот плат и дыхания мозга (код платы не
меняется)». Выбранный путь: одно ядро модуляции + регулятор силы на
каждую сеть.

ВХОД — вектор состояния x (8 чисел), всё уже приходит от плат по каналу
связи, плата ничего нового не шлёт:
    x0  s      — дыхание: −d…+d (вдох > 0), d — глубина тензорной сети платы
    x1  cos φ  ┐ фаза дыхания на круге (где сейчас вдох/выдох)
    x2  sin φ  ┘
    x3  z      — знаковый уровень частоты генераторов плат −1…+1
    x4  z·s    — частота × дыхание (совместный канал)
    x5  разброс частот плат 0…1 (платы в такт — 0, вразнобой — до 1)
    x6  воля   — активность зоны мозга этой сети −0,5…+0,5 (мозг Роуз)
    x7  1      — постоянный вход

ТЕНЗОР — у каждой сети своя матрица P (K × 8): K — число групп весов этой
сети (у модели «частота ↔ слова» — каждая полоса частот, у сети
материалов — четыре группы, у своей LLM — две ветви каждого слоя:
внимание и FFN, v3.147). P постоянна (зерно — имя сети), поэтому
одно и то же состояние плат всегда даёт ту же модуляцию:
    m = 1 + A · tanh(1,5 · P·x),   A = 0,5 · сила/100
m — вектор множителей весов (0,5…1,5 при силе 100 %). Покой (нет плат и
дыхания) — m ≈ 1, сеть работает как обучена.

Сила — регулятор в панели («Связь мозгов»), 0–100 % на каждую сеть.
"""
import math
import threading
import time
import zlib

import store

MAX_AMP = 0.5                       # сила 100 % → множители 0,5…1,5

# сеть: (по умолчанию %, что именно модулируется)
NETS = {
    "Своя LLM": (40, "веса ветвей внимания и FFN каждого слоя — в ответах и в учёбе (прямой и обратный ход)"),
    "Своя LLM: параметры": (60, "в ответах: окно контекста, активные головы и слои, температура, top-k, top-p, штраф повторов, длина — повышаются от частоты плат, воли и дыхания; чем длиннее вопрос — тем сильнее"),
    "Сеть материалов": (40, "тензор смыслов: шаг учёбы контекстов, доля смыслового поиска, частотный канал, дальность разрядки"),
    "Модель «частота ↔ слова»": (60, "вес новых пар «частота ↔ слова» в каждой полосе частот"),
    "Судья связности": (30, "строгость порога связности мыслей мозга"),
    "Речь из словаря": (50, "ширина выбора слова общего словаря"),
}

_LOCK = threading.Lock()
_P = {}                              # (сеть, K) -> матрица P
_X = {"x": None, "ts": 0.0}
_USE = {}                            # сеть -> {"n", "ts", "m": [..]}


def _np():
    import numpy as np
    return np


def k(name):
    """Сила сети 0…1 из настроек (регулятор в панели)."""
    try:
        v = (store.settings().get("tmod_k") or {}).get(name)
        if v is None:
            v = NETS.get(name, (40, ""))[0]
        return max(0.0, min(1.0, float(v) / 100.0))
    except Exception:
        return NETS.get(name, (40, ""))[0] / 100.0


def set_k(vals):
    cur = dict(store.settings().get("tmod_k") or {})
    for n_, v in (vals or {}).items():
        if n_ in NETS:
            try:
                cur[n_] = max(0, min(100, int(float(v))))
            except (TypeError, ValueError):
                pass
    store.update_settings({"tmod_k": cur})
    return cur


def _features():
    """Вектор состояния x (кэш 1 с)."""
    now = time.time()
    if _X["x"] is not None and now - _X["ts"] < 1.0:
        return _X["x"]
    s = ph = 0.0
    z = 0.0
    spread = 0.0
    try:
        import breath
        s = float(breath.s())
        ph = float(breath.phase())
    except Exception:
        pass
    try:
        import genmod
        z_, _b, n = genmod.signal()
        z = float(z_) if n else 0.0
        zs = []
        with genmod._LOCK:
            for d in (genmod._ST.get("devs") or {}).values():
                if now - float(d.get("ts") or 0) > genmod.FRESH_S:
                    continue
                if d.get("shz") is not None:
                    zs.append(max(-1.0, min(1.0, float(d["shz"]) / genmod.HZ_HI)))
        if len(zs) > 1:
            mu = sum(zs) / len(zs)
            spread = min(1.0, math.sqrt(sum((v - mu) ** 2 for v in zs) / len(zs)))
    except Exception:
        pass
    x = [s, math.cos(2 * math.pi * ph), math.sin(2 * math.pi * ph), z, z * s, spread, 0.0, 1.0]
    _X.update(x=x, ts=now)
    return x


def _mat(name, K):
    key = (name, int(K))
    with _LOCK:
        P = _P.get(key)
        if P is None:
            np = _np()
            rs = np.random.RandomState(zlib.crc32(name.encode("utf-8")) & 0x7fffffff)
            P = rs.normal(0.0, 1.0, (int(K), 8)) / math.sqrt(3.0)
            P[:, 7] = 0.0              # покой → m = 1
            _P[key] = P
        return P


LAST = {}                            # сеть -> последние «плавающие» значения


def vec(name, K, amp_mul=1.0):
    """Вектор множителей весов сети (длина K, 1 ± A). amp_mul — усиление
    размаха (v3.148: длина вопроса для параметров своей LLM)."""
    np = _np()
    A = min(MAX_AMP, MAX_AMP * k(name) * max(0.0, float(amp_mul)))
    if A <= 0:
        return np.ones(int(K), dtype=np.float32)
    x = list(_features())
    try:
        import breath
        x[6] = breath.strength(name) - 1.0      # воля мозга: −0,5…+0,5
    except Exception:
        x[6] = 0.0
    m = 1.0 + A * np.tanh(1.5 * (_mat(name, K) @ np.asarray(x, dtype=np.float64)))
    m = m.astype(np.float32)
    u = _USE.setdefault(name, {"n": 0, "ts": 0.0, "m": []})
    u["n"] += 1
    u["ts"] = time.time()
    u["m"] = [round(float(v), 3) for v in m[:16]]
    u["K"] = int(K)
    return m


def scalar(name, idx=0, K=4):
    """Один множитель (группа idx из K)."""
    try:
        return float(vec(name, K)[int(idx) % int(K)])
    except Exception:
        return 1.0


def status():
    x = _features()
    out = []
    for name, (dflt, how) in NETS.items():
        u = _USE.get(name) or {}
        ms = u.get("m") or []
        out.append({"name": name, "k": int(round(100 * k(name))), "default": dflt,
                    "how": how, "uses": u.get("n", 0), "K": u.get("K"),
                    "min": min(ms) if ms else None, "max": max(ms) if ms else None,
                    "m": ms[:8], "ago": int(time.time() - u["ts"]) if u.get("ts") else None,
                    "last": LAST.get(name)})
    return {"ok": True, "x": [round(v, 3) for v in x],
            "x_names": ["дыхание", "фаза cos", "фаза sin", "частота плат", "частота×дыхание",
                        "разброс частот", "воля мозга", "1"],
            "amp": MAX_AMP, "nets": out}
