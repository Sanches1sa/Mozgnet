# -*- coding: utf-8 -*-
"""oscillations.py — v3.238: КОЛЕБАНИЯ ЧАСТИЦ НА ОТДЕЛЬНЫХ ПИНАХ ПЛАТЫ.

Облако считает три «колебания» — мюонное, гравитационное и фотонное — и отдаёт
их платам в ответе опроса /api/brain/d6 (поле osc). Плата выводит каждое своим
меандром на отдельный вывод (скетч, RPC osc): мюон → D5, гравитон → D7, фотон → A5.

Это смоделированные сигналы (на UNO Q нет детекторов частиц), а не измерения:
  * гравитационное — медленное (доли герца до нескольких Гц), плавная синусоида;
  * мюонное — пакетное, средней частоты (единицы–десятки Гц), случайные скачки;
  * фотонное — быстрое (десятки–сотни Гц), следует за «светом»/активностью облака.
Частоты ограничены (плата всё равно обрежет на 4 кГц — быстрее loop() не успеет).
Выключатель — настройка osc_on; отдельные частицы — osc_m / osc_g / osc_p.
"""
import hashlib
import math
import struct
import time

import store

MAX_HZ = 4000


def _noise(seed, lo, hi):
    """Детерминированный «шум» 0..1 из целого seed -> значение в [lo,hi]."""
    h = hashlib.sha256(struct.pack("<q", int(seed))).digest()
    u = struct.unpack("<I", h[:4])[0] / 4294967295.0
    return lo + (hi - lo) * u


def _activity():
    """Грубая «активность облака» 0..1 — ярче фотон, когда облако занято."""
    try:
        s = store.settings()
        g = float(s.get("osc_gain") or 1.0)
    except Exception:
        g = 1.0
    return max(0.1, min(2.0, g))


def current(now=None):
    """-> {m, g, p} — частоты (Гц) мюона, гравитона, фотона сейчас."""
    now = time.time() if now is None else float(now)
    act = _activity()
    # гравитон: медленная синусоида 0,3..3 Гц, период ~3 минуты
    g = 0.3 + 1.6 * (0.5 + 0.5 * math.sin(2 * math.pi * now / 180.0))
    # мюон: пакеты — случайный шаг каждые 2 с в диапазоне 6..70 Гц, со вспышками
    base_m = _noise(now // 2, 6, 70)
    burst = 1.6 if (_noise(now // 2 + 7, 0, 1) > 0.8) else 1.0
    m = base_m * burst
    # фотон: быстрое колебание 60..600 Гц + джиттер, ярче при активности
    p = (120 + 240 * (0.5 + 0.5 * math.sin(2 * math.pi * now / 6.5))) * act
    p += _noise(now // 1, -30, 30)
    out = {"m": int(max(0, min(MAX_HZ, round(m)))),
           "g": int(max(0, min(MAX_HZ, round(g)))),
           "p": int(max(0, min(MAX_HZ, round(p))))}
    # отдельные выключатели частиц
    s = store.settings()
    if not s.get("osc_m", True):
        out["m"] = 0
    if not s.get("osc_g", True):
        out["g"] = 0
    if not s.get("osc_p", True):
        out["p"] = 0
    return out


def enabled():
    return bool(store.settings().get("osc_on", True))


def wire():
    """Строка для RPC платы osc: «m:..,g:..,p:..» | "" (выключено)."""
    if not enabled():
        return ""
    o = current()
    return "m:%d,g:%d,p:%d" % (o["m"], o["g"], o["p"])


def status():
    o = current()
    s = store.settings()
    return {"ok": True, "on": enabled(), "gain": float(s.get("osc_gain") or 1.0),
            "parts": {"muon": s.get("osc_m", True), "grav": s.get("osc_g", True), "photon": s.get("osc_p", True)},
            "now": o, "pins": {"muon": "D5", "grav": "D7", "photon": "A5"}, "max_hz": MAX_HZ}
