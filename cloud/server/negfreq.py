# -*- coding: utf-8 -*-
# ===========================================================================
# negfreq.py — ЗНАК ЧАСТОТЫ ГЕНЕРАТОРА НА D6 РЕШАЕТ СВОЯ LLM (v3.51)
# ---------------------------------------------------------------------------
# Работает только при галочке «отрицательная частота на D6» (d6_negfreq) или
# на D4 (d4_negfreq, v3.52).
# Физика прежняя: на пине D6 нет ни отрицательного напряжения, ни отрицательной
# частоты (0…3,3 В, несущая 500 Гц), поэтому знак — это КОД в кадре (маркер
# 0xFF «−» / 0xFE «+» и число Гц), а плата показывает его в генераторе:
# «−440 Гц». Новое здесь — КТО решает знак:
#   1) LLM сравнивает, после какой затравки мысль вероятнее:
#      «частота минус. <мысль>» или «частота плюс. <мысль>» (прямой ход,
#      веса не меняются). Решение принимается, когда пар для обучения уже
#      >= 20 (по обе стороны >= 5) и разница потерь >= 0,03 нат/токен;
#   2) иначе (модель не обучена / занята / сомневается) — знак по Δf на плате:
#      частота генератора ниже сглаженной → «−».
# Обучение: реплики платы с частотой (бандл памяти) подписываются знаком по
# тому же Δf и попадают в корпус LLM строкой «частота минус. …»; дообучение —
# фоном, ≥12 новых пар и ≥10 минут. LLM «пытается» — не гарантия: пока пар
# мало, решает Δf.
# Не мешает работе LLM: оценка — в фоне, только если обучение не идёт
# (блокировка tinyllm берётся без ожидания), не чаще раза в 2 с, текст ≤160
# знаков. Заняты — решает Δf.
# ===========================================================================
import hashlib
import re
import threading
import time

import store

_LOCK = threading.Lock()
_ST = {"loaded": False, "pairs": [], "seen": {}, "new": 0, "ft_ts": 0.0,
       "ft": "", "llm": 0, "hz": 0, "busy": 0, "cold": 0, "last": {},
       "scored_ts": 0.0}
PLUS, MINUS = "частота плюс.", "частота минус."
MARGIN = 0.03


def enabled():
    st = store.settings()
    return bool(st.get("d6_negfreq") or st.get("d4_negfreq"))


def _load():
    if _ST["loaded"]:
        return
    d = store.load_json("negfreq.json", None) or {}
    _ST["pairs"] = [tuple(x) for x in (d.get("pairs") or [])][-600:]
    _ST["seen"] = dict(d.get("seen") or {})
    _ST["loaded"] = True


def _save():
    seen = _ST["seen"]
    if len(seen) > 20000:
        for k in sorted(seen, key=seen.get)[:len(seen) - 20000]:
            seen.pop(k, None)
    store.save_json("negfreq.json", {"pairs": _ST["pairs"][-600:],
                                     "seen": seen})


def sign_by_delta(hz, ema, dead=3.0):
    """-1, если частота ниже сглаженной больше чем на dead Гц, иначе +1."""
    try:
        return -1 if (float(ema) - float(hz)) > dead else 1
    except (TypeError, ValueError):
        return 1


def learn_bundle(dev, bundle, gen):
    """Реплики платы с частотой -> пары (знак, текст). Только при галочке.
    gen — блок «gen» бандла (hz/ema платы). Знак — по Δf: частота реплики
    против текущей сглаженной платы (грубо: ema одна на весь бандл)."""
    if not enabled() or not isinstance(gen, dict):
        return 0
    ema = float(gen.get("ema") or 0)
    if ema <= 0:
        return 0
    n = 0
    with _LOCK:
        _load()
        items = list((bundle or {}).get("memory") or []) + \
            list((bundle or {}).get("chat") or [])
        for m in items:
            if not isinstance(m, dict):
                continue
            try:
                hz = abs(int(m.get("hz") or 0))
            except (TypeError, ValueError):
                hz = 0
            tx = str(m.get("text") or "").strip()
            if hz <= 0 or len(tx) < 12 or m.get("role") == "system":
                continue
            h = hashlib.md5(("n|%s|%d|%s" % (dev, hz, tx)).encode(
                "utf-8", "replace")).hexdigest()[:14]
            if h in _ST["seen"]:
                continue
            _ST["seen"][h] = time.time()
            _ST["pairs"].append((sign_by_delta(hz, ema), tx[:300]))
            _ST["new"] += 1
            n += 1
        if n:
            _save()
    if n:
        _maybe_finetune()
    return n


def corpus_texts(max_items=300):
    """Строки «частота минус. …» — в корпус LLM (только при галочке)."""
    if not enabled():
        return []
    with _LOCK:
        _load()
        return ["%s %s" % (MINUS if s < 0 else PLUS, t)
                for s, t in _ST["pairs"][-max_items:]]


def _maybe_finetune():
    with _LOCK:
        if _ST["new"] < 12 or time.time() - _ST["ft_ts"] < 600:
            return False
        _ST["ft_ts"], _ST["new"] = time.time(), 0

    def run():
        try:
            import tinyllm
            _ST["ft"] = str(tinyllm.learn_new())
        except Exception as e:
            _ST["ft"] = "ошибка: %s" % e
    threading.Thread(target=run, daemon=True, name="negfreq-ft").start()
    return True


def _counts():
    neg = sum(1 for s, _t in _ST["pairs"] if s < 0)
    return neg, len(_ST["pairs"]) - neg


def llm_sign(text):
    """(знак, разница потерь) | (0, причина). Знак 0 — «LLM не решает»."""
    with _LOCK:
        _load()
        neg, pos = _counts()
        if len(_ST["pairs"]) < 20 or neg < 5 or pos < 5:
            _ST["cold"] += 1
            return 0, "мало пар"
        if time.time() - _ST["scored_ts"] < 2.0:
            _ST["busy"] += 1
            return 0, "часто"
        _ST["scored_ts"] = time.time()
    try:
        import tinyllm
        lk = getattr(tinyllm, "_LOCK", None)
        if lk is None:
            return 0, "нет numpy"
        if not lk.acquire(blocking=False):      # идёт обучение — не мешаем
            _ST["busy"] += 1
            return 0, "LLM занята"
        try:
            m = tinyllm.model()
            if not m.ready or not m.steps or m.tok.size < 10:
                _ST["cold"] += 1
                return 0, "LLM не обучена"
            t = str(text or "")[:160]
            a = m.nll(PLUS, t)
            b = m.nll(MINUS, t)
        finally:
            lk.release()
        if a is None or b is None:
            return 0, "пусто"
        d = a - b                      # >0: после «минус» текст вероятнее
        if abs(d) < MARGIN:
            return 0, "сомнение"
        return (-1 if d > 0 else 1), round(d, 3)
    except Exception as e:
        return 0, "ошибка: %s" % str(e)[:60]


def decide(text):
    """Решение для мысли: {"sign": -1|0|1, "by": "llm"|"", "margin": x}.
    Знак 0 — LLM не решила, плата определит по Δf. Выключено — None."""
    if not enabled():
        return None
    s, info = llm_sign(text)
    with _LOCK:
        if s:
            _ST["llm"] += 1
        else:
            _ST["hz"] += 1
        _ST["last"] = {"sign": s, "why": info, "ts": time.time()}
    return {"sign": s, "by": "llm" if s else "", "margin": info if s else 0.0}


def decide_async(text, done):
    """Оценка в фоне: ответ мысли не ждёт; done(decision|None)."""
    def run():
        try:
            done(decide(text))
        except Exception:
            done(None)
    threading.Thread(target=run, daemon=True, name="negfreq").start()


def status():
    with _LOCK:
        _load()
        neg, pos = _counts()
        return {"on": enabled(), "pairs": len(_ST["pairs"]), "neg": neg,
                "pos": pos, "by_llm": _ST["llm"], "by_delta": _ST["hz"],
                "busy": _ST["busy"], "cold": _ST["cold"],
                "ft": _ST["ft"], "last": dict(_ST["last"])}
