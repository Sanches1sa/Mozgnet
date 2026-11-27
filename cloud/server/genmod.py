# -*- coding: utf-8 -*-
# ===========================================================================
# genmod.py — ГЕНЕРАТОР СКЕТЧА ВЛИЯЕТ НА ВЕСА СВОЕЙ LLM (v3.47, stdlib+numpy)
# ---------------------------------------------------------------------------
# Заявка владельца: «сделай так, чтобы генератор на скетче влиял на веса
# своей LLM». Плата (neuro_12 v2.43) присылает в бандле памяти состояние
# генератора скетча: частоту шины D4 (Гц), её сглаженное значение и ленту
# последних отсчётов (частота + номер буквы буквогенератора). Облако
# использует это ТОЛЬКО при обучении LLM, двумя способами:
#   1) СКОРОСТЬ ОБУЧЕНИЯ: lr умножается на 1 + 0,2·сила·z, где z∈[-1;1] —
#      сглаженная частота генератора на лог-шкале 120…2000 Гц (мало Гц —
#      осторожнее учим, много — смелее; максимум ±20 % при силе 1);
#   2) ШУМ В ВЕСАХ: раз в N шагов к каждой матрице весов добавляется малый
#      гауссов шум σ = 0,001·сила·std(матрицы); зерно шума берётся из ленты
#      генератора (физический источник случайности) и номера шага.
# Это возмущение, а не обучающий сигнал: оно не «учит» модель смыслу, а
# слегка двигает веса и скорость; при малой силе вреда нет (обучение
# выравнивает), при большой — модель хуже. Настройка llm_gen_influence
# (0 — выкл., по умолчанию 0,5). Нет данных от платы (или старше часа) —
# влияния нет вообще.
# ===========================================================================
import hashlib
import math
import threading
import time

import store

HZ_LO, HZ_HI = 120.0, 2000.0
FRESH_S = 3600.0
NOISE_EVERY = 20
_LOCK = threading.Lock()
_ST = {"loaded": False, "devs": {}, "applied": 0, "noise_n": 0,
       "last": {}}


def _load():
    if not _ST["loaded"]:
        d = store.load_json("genstate.json", None) or {}
        _ST["devs"] = dict(d.get("devs") or {})
        _ST["loaded"] = True


def note(dev, gen):
    """Данные генератора платы (из бандла памяти)."""
    if not isinstance(gen, dict):
        return
    ring = [list(map(int, r[:3])) for r in (gen.get("ring") or [])[-120:]
            if isinstance(r, (list, tuple)) and len(r) >= 2]
    with _LOCK:
        _load()
        _ST["devs"][str(dev)[:40]] = {
            "hz": int(gen.get("hz") or 0), "ema": float(gen.get("ema") or 0),
            "shz": (float(gen["shz"]) if gen.get("shz") is not None else None),
            "ring": ring, "ts": time.time()}
        store.save_json("genstate.json", {"devs": _ST["devs"]})


def strength():
    """Frequency associations are retained for feelings, not lexical generation."""
    return 0.0


def signal():
    """(z, seed_bytes, устройств) по свежим данным генератора."""
    with _LOCK:
        _load()
        now = time.time()
        zs, blob = [], b""
        for dev, d in sorted(_ST["devs"].items()):
            if now - float(d.get("ts") or 0) > FRESH_S:
                continue
            if d.get("shz") is not None:     # v3.71: знаковая частота платы
                zs.append(max(-1.0, min(1.0, float(d["shz"]) / HZ_HI)))
                blob += repr((dev, d.get("shz"), d.get("ring"))).encode("utf-8")
                continue
            hz = float(d.get("ema") or d.get("hz") or 0)
            if hz <= 0:
                continue
            x = (math.log(max(HZ_LO, min(HZ_HI, hz))) - math.log(HZ_LO)) / (
                math.log(HZ_HI) - math.log(HZ_LO))
            zs.append(2.0 * x - 1.0)
            blob += repr((dev, d.get("hz"), d.get("ring"))).encode("utf-8")
    if not zs:
        return 0.0, b"", 0
    return sum(zs) / len(zs), blob, len(zs)


def lr_factor():
    s = strength()
    if s <= 0:
        return 1.0
    z, _b, n = signal()
    if not n:
        return 1.0
    f = 1.0 + 0.2 * s * z
    _ST["last"] = {"z": round(z, 3), "lr_factor": round(f, 3), "devices": n,
                   "ts": time.time()}
    return max(0.5, min(1.5, f))


def perturb(model, step):
    """Шум в матрицах весов (зерно — из ленты генератора). -> True/False."""
    s = strength()
    if s <= 0 or step % NOISE_EVERY:
        return False
    z, blob, n = signal()
    if not n:
        return False
    import numpy as np
    seed = int(hashlib.md5(blob + str(step).encode()).hexdigest()[:8], 16)
    r = np.random.RandomState(seed)
    sig = 0.001 * s
    for k, v in model.p.items():
        if getattr(v, "ndim", 0) != 2 or k in ("E", "P"):
            continue
        sd = float(v.std())
        if sd > 0:
            v += (r.normal(0, sig * sd, v.shape)).astype(v.dtype)
    _ST["noise_n"] += 1
    _ST["applied"] = _ST.get("applied", 0) + 1
    _ST["last"] = dict(_ST.get("last") or {}, noise_seed=seed,
                       noise_sigma=sig, ts=time.time())
    return True


def status():
    z, _b, n = signal()
    with _LOCK:
        _load()
        devs = {k: {"hz": v.get("hz"), "ema": round(float(v.get("ema") or 0)),
                    "age_s": int(time.time() - float(v.get("ts") or 0)),
                    "ring": len(v.get("ring") or [])}
                for k, v in _ST["devs"].items()}
    try:
        import freqprior
        fp = freqprior.status()
    except Exception:
        fp = {}
    return {"prior": fp, "meaning": meaning_status(), "strength": strength(), "z": round(z, 3), "fresh_devices": n,
            "noise_steps": _ST["noise_n"], "last": _ST.get("last") or {},
            "devices": devs}


# ===========================================================================
# v3.48 — СИГНАЛ УЧИТ СМЫСЛУ (привязка сигнала к тексту)
# ---------------------------------------------------------------------------
# Сама по себе частота генератора смысла не несёт: число «1400 Гц» ничего не
# значит. Смысл появляется только из СОВПАДЕНИЯ сигнала с тем, что в этот
# момент происходило и говорилось. Плата (v2.44) пишет Гц в каждую реплику
# памяти и чата. Облако:
#   1) БЫСТРО, ПО ОДНОМУ ПРИМЕРУ: уровень сигнала (5 ступеней по Гц) связывается
#      со словами реплики в ассоциативной таблице (без градиентов — работает
#      сразу после первой выгрузки). При генерации слова, которые чаще
#      встречались на текущем уровне сигнала, получают прибавку к логитам
#      (≈ PMI), и мысль идёт «в ту сторону», что значила эта частота;
#   2) В ВЕСА: реплики попадают в корпус LLM с пометкой «сигнал высокий.» и
#      после ≥12 новых пар запускается дообучение (learn_new, без сброса
#      модели); на генерации к затравке добавляется пометка текущего уровня,
#      и обученная модель продолжает в соответствии с ней.
# Честно: «смысл» здесь — статистическая связь «уровень сигнала ↔ слова»,
# а не понимание; чем больше разных реплик при разных уровнях, тем она
# точнее. С малым числом примеров связь шумная.
# ===========================================================================
import json as _json
import re as _re

BANDS = ["очень низкий", "низкий", "средний", "высокий", "очень высокий"]
_A = {"loaded": False, "a": [{} for _ in BANDS], "seen": {}, "pairs": [],
      "new": 0, "ft_ts": 0.0, "ft": "", "bias_key": None, "bias": None}
_ALOCK = threading.RLock()


def meaning_strength():
    """Frequency associations are retained for feelings, not lexical generation."""
    return 0.0


def band_of_hz(hz):
    hz = max(HZ_LO, min(HZ_HI, float(hz)))
    x = (math.log(hz) - math.log(HZ_LO)) / (math.log(HZ_HI) - math.log(HZ_LO))
    return min(len(BANDS) - 1, int(x * len(BANDS)))


def band_of_signed(shz):
    """v3.71: знаковая частота -2000..2000 Гц -> уровень 0..4 (линейно:
    отрицательные — «очень низкий/низкий», около нуля — «средний»)."""
    z = max(-1.0, min(1.0, float(shz) / HZ_HI))
    return min(len(BANDS) - 1, int((z + 1.0) / 2.0 * len(BANDS)))


def band_range(i):
    """Диапазон частот уровня i, Гц (знаковый): (от, до)."""
    n = len(BANDS)
    i = max(0, min(n - 1, int(i)))
    return (int(round(-HZ_HI + 2 * HZ_HI * i / n)),
            int(round(-HZ_HI + 2 * HZ_HI * (i + 1) / n)))


def band_phrase(i):
    return "сигнал %s." % BANDS[max(0, min(len(BANDS) - 1, int(i)))]


def current_band():
    """Текущий уровень сигнала по свежим данным плат или None."""
    z, _b, n = signal()
    if not n:
        return None
    return min(len(BANDS) - 1, int((z + 1.0) / 2.0 * len(BANDS)))


_JUNK_ST = frozenset(("англ", "лат", "греч", "франц", "итал", "польс",
                      "нидер", "санск"))


def _tclean(t):
    """v3.118: мысль без «англ», «н» и шума (llm2.thought_clean)."""
    try:
        import llm2
        return llm2.thought_clean(t)
    except Exception:
        return str(t or "")


def _aload():
    if _A["loaded"]:
        return
    d = store.load_json("genassoc.json", None) or {}
    a = d.get("a") or []
    if len(a) == len(BANDS):
        _A["a"] = [dict(x) for x in a]
    _A["seen"] = dict(d.get("seen") or {})
    _A["pairs"] = [tuple(x) for x in (d.get("pairs") or [])][-600:]
    # v3.118: пары с «англ н н» (шум прежней LLM/плат) — вон, ассоциации
    # слов-пометок — тоже
    cl = []
    for b_, t_ in _A["pairs"]:
        t2 = _tclean(t_)
        if len(t2) >= 12:
            cl.append((b_, t2))
    _A["pairs"] = cl
    for x in _A["a"]:
        for st in [k for k in x if k in _JUNK_ST]:
            x.pop(st, None)
    _A["loaded"] = True


def _asave():
    seen = _A["seen"]
    if len(seen) > 20000:
        for k in sorted(seen, key=seen.get)[:len(seen) - 20000]:
            seen.pop(k, None)
    store.save_json("genassoc.json", {"a": _A["a"], "seen": seen,
                                      "pairs": _A["pairs"][-600:]})


def _asave_locked():
    with _ALOCK:
        _asave()


def _asave_soon():
    """v3.186.2: genassoc.json (~7,5 МБ) — не чаще раза в минуту. Раньше
    писался целиком на КАЖДУЮ мысль платы под _ALOCK: связь плат и
    сохранение их памяти стояли в очереди за записью на диск часами."""
    try:
        import bgwork
        bgwork.debounce("genassoc", _asave, _asave_locked, 60.0)
    except Exception:
        _asave()


def _stems(text):
    return [w[:5] for w in _re.findall(r"[а-яё]{3,}", str(text).lower())]


def learn_bundle(dev, bundle):
    """Реплики платы с частотой -> ассоциации «уровень ↔ слова» (сразу) и
    пары для обучения весов. Возврат: сколько новых пар."""
    n = 0
    with _ALOCK:
        _aload()
        items = list((bundle or {}).get("memory") or []) + \
            list((bundle or {}).get("chat") or [])
        for m in items:
            if not isinstance(m, dict):
                continue
            # v3.71: плата шлёт знаковую частоту shz (может быть < 0); старые
            # платы шлют только hz (>0)
            try:
                hz = int(round(float(m["shz"]))) if m.get("shz") is not None \
                    else int(m.get("hz") or 0)
            except (TypeError, ValueError):
                hz = 0
            tx = _tclean(str(m.get("text") or "").strip())   # v3.118
            if hz == 0 or len(tx) < 12 or m.get("role") == "system":
                continue
            h = hashlib.md5(("%s|%d|%s" % (dev, hz, tx)).encode(
                "utf-8", "replace")).hexdigest()[:14]
            if h in _A["seen"]:
                continue
            _A["seen"][h] = time.time()
            b = band_of_signed(hz)
            # v3.57: вес наблюдения = понимание фразы тензорной сетью платы
            # (tn 0..1): 1 (сеть не уверена/не готова) .. 3 (уверена)
            try:
                w = 1 + int(round(2.0 * max(0.0, min(1.0, float(m["tn"])))))
            except (KeyError, TypeError, ValueError):
                w = 1
            for st in _stems(tx):
                _A["a"][b][st] = _A["a"][b].get(st, 0) + w
            _A["pairs"].append((b, tx[:300]))
            _A["new"] += 1
            n += 1
        if n:
            _A["bias_key"] = None
            _asave_soon()
    if n:
        maybe_finetune()
    return n


def word_levels(text):
    """v3.56: уровень каждого слова текста по таблице «уровень сигнала ↔ слова»:
    L = (средний уровень - 2) / 2 в [-1; 1] (-1 — слово шло с очень низким
    сигналом, +1 — с очень высоким); None — слово встречалось <3 раз. Слова —
    те же основы, что в _stems (≥3 букв, 5 первых). Возврат (levels, known)."""
    out, known = [], 0
    with _ALOCK:
        _aload()
        for st in _stems(text):
            c = [x.get(st, 0) for x in _A["a"]]
            tot = sum(c)
            if tot < 3:
                out.append(None)
                continue
            m = sum(i * v for i, v in enumerate(c)) / float(tot)
            out.append(round((m - (len(BANDS) - 1) / 2.0) /
                             ((len(BANDS) - 1) / 2.0), 3))
            known += 1
    return out, known


def learn_frame(dev, text, signed_hz, tn=None):
    """v3.56: «дыхание» кадра платы тоже сигнал: фраза + знаковая частота
    дыхания (-2000..2000, составляющая тензорной сети БЕЗ влияния слов) ->
    уровень -> ассоциации и пара для дообучения. Дедуп — по фразе, уровню и
    минуте. Возврат: True, если пара принята."""
    tx = _tclean(str(text or "").strip())                   # v3.118
    if len(tx) < 12:
        return False
    try:
        z = max(-1.0, min(1.0, float(signed_hz) / 2000.0))
    except (TypeError, ValueError):
        return False
    b = min(len(BANDS) - 1, int((z + 1.0) / 2.0 * len(BANDS)))
    with _ALOCK:
        _aload()
        h = hashlib.md5(("f|%s|%d|%d|%s" % (dev, b, int(time.time() // 60),
                                             tx)).encode("utf-8", "replace")
                        ).hexdigest()[:14]
        if h in _A["seen"]:
            return False
        _A["seen"][h] = time.time()
        try:
            w = 1 + int(round(2.0 * max(0.0, min(1.0, float(tn)))))
        except (TypeError, ValueError):
            w = 1
        try:                                  # v3.132: дыхание — вес пары ±15 %
            import breath as _brg
            w = round(_brg.mod("Модель «частота ↔ слова»", float(w), 0.15) *
                      _brg.strength("Модель «частота ↔ слова»"), 3)
        except Exception:
            pass
        try:                                  # v3.146: тензор модуляции — вес полосы
            import tensormod as _tm
            w = round(w * float(_tm.vec("Модель «частота ↔ слова»", len(BANDS))[b]), 3)
        except Exception:
            pass
        for st in _stems(tx):
            _A["a"][b][st] = _A["a"][b].get(st, 0) + w
        _A["pairs"].append((b, tx[:300]))
        _A["new"] += 1
        _A["frames"] = int(_A.get("frames") or 0) + 1
        _A["bias_key"] = None
        _asave_soon()
    maybe_finetune()
    return True


def word_bias(model):
    """No Hz-to-token bias; frequency remains available to feeling encoders."""
    return None


def corpus_texts(max_items=400):
    """Frequency associations are retained for feelings, not lexical generation."""
    return []


def prefix_for_prompt():
    """No frequency-derived words are injected into a language-model prompt."""
    return ""


def maybe_finetune():
    """≥12 новых пар и прошло ≥10 мин -> дообучение LLM на фоне."""
    with _ALOCK:
        if _A["new"] < 12 or time.time() - _A["ft_ts"] < 600 or \
                meaning_strength() <= 0:
            return False
        _A["ft_ts"], _A["new"] = time.time(), 0

    def run():
        try:
            import tinyllm
            _A["ft"] = str(tinyllm.learn_new())
        except Exception as e:
            _A["ft"] = "ошибка: %s" % e
    threading.Thread(target=run, daemon=True, name="genmod-ft").start()
    return True


def meaning_status():
    with _ALOCK:
        _aload()
        tot = [sum(x.values()) for x in _A["a"]]
        return {"strength": meaning_strength(), "pairs": len(_A["pairs"]),
                "new_pairs": _A["new"], "band_words": tot,
                "band": current_band(), "finetune": _A["ft"],
                "bias_words": int(_A.get("bias_n") or 0),
                "frames": int(_A.get("frames") or 0),
                "ranges": [list(band_range(i)) for i in range(len(BANDS))],
                "names": list(BANDS)}
