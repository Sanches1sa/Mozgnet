# -*- coding: utf-8 -*-
"""v3.177: ПОТОКИ СВОЕЙ LLM — модель учится несколькими потоками сразу.

Веса у своей LLM одни, поэтому «расколоть» её на независимые модели нельзя
без потери всего, что она знает. Зато можно учить её несколькими потоками.
Каждый поток в своей нити готовит СВОЙ материал, а шаги обучения на нём
вплетаются между шагами основного обучения (статьи базы знаний):

  * «Код» — функции из прочитанных репозиториев (codestream);
  * «Чаты МАКС» — инструкции и переписка из чатов бота МАКС (chatmem);
  * «Разговор» — пары «вопрос → ответ» с опорой на источник (qalearn:
    только ответы из базы знаний, интернета, расчёта; 👎 — мимо).

Сколько шагов достаётся какому потоку, решает МОЗГ-12. Доля потока равна
базе потока, умноженной на волю его зон на платах (0,5…1,5):
  код — «Кирпичики», МАКС — «Помощь» и «Память», разговор — «Приветствие»
  и «Вопросы».
Мозг не на связи — доли равны базе (нейтрально). Поэтому веса модели
«колеблются» вслед за мозгом, а не случайно.

Безопасность (как у курса кода):
  * шаг потока — не больше 5e-4 и половина шага текста, без множителей;
  * loss потока выше 2,5·ln(V)+5 — этот поток сам выключается (остальные
    учатся дальше) и сам включается, когда модель переучилась (+300 шагов
    и loss в норме);
  * основное обучение стоит — нить потоков сама делает «подход»: до 40
    шагов потоков, потом сохраняет модель.

Настройки: llm_code_stream (все потоки), llm_stream_code / llm_stream_max /
llm_stream_talk / llm_stream_admin (каждый), llm_code_mix (шаг потока на каждый N-й шаг текста).
"""
import collections
import hashlib
import math
import threading
import time

import store

STREAMS = [
    {"key": "code", "title": "Код из репозиториев", "zones": ("Кирпичики",), "base": 1.0},
    {"key": "max", "title": "Чаты МАКС", "zones": ("Помощь", "Память"), "base": 0.8},
    {"key": "talk", "title": "Разговор и ответы", "zones": ("Приветствие", "Вопросы"), "base": 0.8},
    # v3.178: отдельный поток — администрирование серверов и плат (окно «Серверы»)
    {"key": "admin", "title": "Администрирование серверов", "zones": ("Помощь", "Кирпичики"), "base": 0.6},
]
TEXT_KEYS = ("max", "talk", "admin")
_FILE = "llm_streams.json"
_MAX_SEEN = 30000

_LOCK = threading.Lock()
_TQ = {k: collections.deque(maxlen=48) for k in TEXT_KEYS}     # (подпись, окно)
_TQUEUED = {k: set() for k in TEXT_KEYS}
_TLEFT = {k: collections.Counter() for k in TEXT_KEYS}
_TDONE = {k: [] for k in TEXT_KEYS}
_CNT = [0]
_CREDIT = collections.Counter()           # плавный взвешенный выбор потока
_LIVE = {"steps": collections.Counter(), "loss": {}, "share": {}, "brain": {}}
_TH = [None]
_WAKE = threading.Event()
_MODE = ["ждёт"]


# ------------------------------------------------------------- настройки
def _s(k, d):
    v = store.settings().get(k)
    return d if v is None else v


def all_on():
    return bool(_s("llm_code_stream", True))


def on(key):
    st = _state()
    return all_on() and bool(_s("llm_stream_" + key, True)) and not (st["off"].get(key) or {}).get("why")


def _mix():
    try:
        return max(1, min(1000, int(float(_s("llm_code_mix", 2)))))
    except (TypeError, ValueError):
        return 2


def _state():
    st = store.load_json(_FILE, {}) or {}
    for k in ("seen", "learned", "steps", "off"):
        st.setdefault(k, {})
    return st


def _save(st):
    for k, v in list(st["seen"].items()):
        st["seen"][k] = v[-_MAX_SEEN:]
    store.save_json(_FILE, st)


def _sig(text):
    return hashlib.md5(str(text).encode("utf-8", "replace")).hexdigest()[:16]


# ------------------------------------------------------- воля мозга-12
def weights():
    """Доли потоков сейчас: база × воля зон мозга-12 (0,5…1,5)."""
    try:
        import breath
        wz = breath.will_zones()
    except Exception:
        wz = None
    out, brain = {}, {}
    for s in STREAMS:
        f = 1.0
        if wz:
            vals = [wz[z] for z in s["zones"] if z in wz]
            if vals:
                f = 0.5 + max(0.0, min(1.0, sum(vals) / len(vals)))
        brain[s["key"]] = round(f, 2)
        out[s["key"]] = s["base"] * f
    _LIVE["brain"] = brain
    return out, bool(wz)


# ------------------------------------------------------------ материалы
def texts(key):
    """[(подпись, текст)] материала потока (без кода — он в codestream)."""
    out = []
    if key == "max":
        try:
            import chatmem
            for it in (chatmem._load().get("items") or [])[-600:]:
                t = str(it.get("text") or "").strip()
                if len(t) >= 20:
                    head = str(it.get("title") or "").strip()
                    out.append((it.get("id") or _sig(t), (head + "\n" if head else "") + t[:2500]))
        except Exception:
            pass
    elif key == "admin":                   # v3.178: уроки + опыт агента «Серверы»
        try:
            import admincorpus
            out = admincorpus.texts()
        except Exception:
            pass
    elif key == "talk":
        try:
            import qalearn
            with qalearn._LOCK:
                pairs = list((qalearn._load().get("pairs") or {}).items())
            for k, p in pairs[-3000:]:
                q, a = str(p.get("q") or "").strip(), str(p.get("a") or "").strip()
                if not q or len(a) < 20 or int(p.get("bad") or 0) > int(p.get("good") or 0):
                    continue
                t = "Вопрос: %s\nОтвет: %s" % (q, a[:1500])
                out.append((_sig(t), t))
        except Exception:
            pass
    return out


def _fill(m, key):
    import codeboot
    q = _TQ[key]
    if len(q) >= 16:
        return 0
    st = _state()
    seen = set(st["seen"].get(key) or []) | set(_TDONE[key])
    todo = [(sg, t) for sg, t in texts(key) if sg not in seen and sg not in _TQUEUED[key]][:12]
    length = min(96, int(m.c.get("llm_ctx") or 96))
    n = 0
    for sg, t in todo:
        try:
            ws = codeboot._windows(m, [t], length)[:6]
        except Exception:
            ws = []
        with _LOCK:
            _TQUEUED[key].add(sg)
            if not ws:
                _TDONE[key].append(sg)
                continue
            for w in ws:
                q.append((sg, w))
            _TLEFT[key][sg] += len(ws)
        n += len(ws)
    return n


def _flush():
    st = None
    for key in TEXT_KEYS:
        with _LOCK:
            got = list(_TDONE[key])
            del _TDONE[key][:]
        if got:
            st = st or _state()
            st["seen"].setdefault(key, []).extend(got)
            st["learned"][key] = int(st["learned"].get(key) or 0) + len(got)
    if st is not None:
        _save(st)


# ------------------------------------------------------------- шаг потока
def loss_limit(m):
    try:
        V = max(10, int(m.tok.size))
    except Exception:
        V = 4000
    return 2.5 * math.log(V) + 5.0


def _off(key, why, m=None):
    st = _state()
    st["off"][key] = {"why": why, "ts": time.time(),
                      "steps": int(getattr(m, "steps", 0) or 0)}
    _save(st)
    store.log("LLM-ПОТОКИ", "поток «%s» выключен сам: %s" % (key, why))


def _step_text(m, lr, key):
    import numpy as np
    import tinyllm as tl
    with _LOCK:
        take = [_TQ[key].popleft() for _ in range(min(2, len(_TQ[key])))]
    if not take:
        return None
    rows = [w for _sg, w in take]
    L = min(len(w) for w in rows)
    X = np.stack([w[:L - 1] for w in rows])
    Y = np.stack([w[1:L] for w in rows])
    prev = getattr(tl._FP, "stable_code", False)
    tl._FP.stable_code = True                 # без множителей генератора и дыхания
    try:
        loss = m.train_step(X, Y, min(5e-4, lr * 0.5))
    finally:
        tl._FP.stable_code = prev
    with _LOCK:
        for sg, _w in take:
            _TLEFT[key][sg] -= 1
            if _TLEFT[key][sg] <= 0:
                del _TLEFT[key][sg]
                _TQUEUED[key].discard(sg)
                _TDONE[key].append(sg)
    return loss


def _ready(key):
    if not on(key):
        return False
    if key == "code":
        import codestream
        return bool(codestream._Q) and codestream.enabled()
    return bool(_TQ[key])


def interleave(m, lr):
    """Зовёт ОСНОВНОЕ обучение между своими шагами (и «подход» потоков):
    шаг на материале потока, выбранного по доле мозга-12."""
    if getattr(m, "_code_course", False) or not all_on():
        return
    _CNT[0] += 1
    if _CNT[0] % _mix():
        return
    w, _brain = weights()
    avail = [k for k in w if _ready(k)]
    if not avail:
        return
    tot = sum(w[k] for k in avail) or 1.0
    for k in avail:                            # плавный взвешенный круг
        _CREDIT[k] += w[k] / tot
    key = max(avail, key=lambda k: _CREDIT[k])
    _CREDIT[key] -= 1.0
    _LIVE["share"] = {k: round(100 * w[k] / tot) for k in avail}
    try:
        if key == "code":
            import codestream
            loss = codestream._step(m, lr)
        else:
            loss = _step_text(m, lr, key)
    except Exception as e:
        if type(e).__name__ == "_TrainingHandoff":
            raise
        _MODE[0] = "ошибка шага «%s»: %s" % (key, e)
        return
    if loss is None:
        return
    _LIVE["steps"][key] += 1
    _LIVE["loss"][key] = round(float(loss), 3) if math.isfinite(loss) else str(loss)
    if key != "code" and (not math.isfinite(loss) or loss > loss_limit(m)):
        _off(key, "loss %s выше предела %.0f" % (_LIVE["loss"][key], loss_limit(m)), m)


# ------------------------------------------------------------------ нить
def _model():
    try:
        import tinyllm as tl
        m = tl._M
        if not tl.HAVE_NP or m is None or not getattr(m, "p", None) or getattr(m, "steps", 0) < 1:
            return None
        return None if getattr(m, "_dead", False) else m
    except Exception:
        return None


def _revive(m):
    """Поток выключился сам (loss взлетел) — включить, когда модель
    переучилась: +300 шагов и loss в норме."""
    st = _state()
    changed = False
    for key, o in list(st["off"].items()):
        if not o or not o.get("why"):
            continue
        lo = getattr(m, "loss", None)
        if m.steps >= int(o.get("steps") or 0) + 300 and lo is not None and \
                math.isfinite(lo) and lo <= loss_limit(m):
            st["off"].pop(key, None)
            changed = True
            store.log("LLM-ПОТОКИ", "поток «%s» снова включён: модель переучилась (loss %.2f)" % (key, lo))
    if changed:
        _save(st)


def _burst(m):
    """Основное обучение стоит — подход потоков: до 40 шагов, потом
    сохранение модели. Веса заняты только этой нитью."""
    import tinyllm as tl
    with tl._LOCK:
        th = tl._THREAD[0]
        if m.training or (th and th.is_alive()) or getattr(m, "_code_course", False):
            return 0
        m.training = True
    n0 = sum(_LIVE["steps"].values())
    t0 = time.time()
    try:
        lr = float(m.c.get("llm_lr") or 1e-3)
        saved = _CNT[0]
        for _ in range(40 * _mix()):
            if time.time() - t0 > 90 or getattr(m, "_dead", False):
                break
            try:
                if tl._ANSWERING[0] > 0:
                    break
            except Exception:
                pass
            interleave(m, lr)
        _CNT[0] = saved
        n = sum(_LIVE["steps"].values()) - n0
        if n:
            m.save(tl._state_path())
            store.log("LLM-ПОТОКИ", "подход потоков (основное обучение стоит): шагов %d за %.0f с"
                      % (n, time.time() - t0))
        return n
    finally:
        m.training = False


def _loop():
    time.sleep(100)
    last_burst = 0.0
    while True:
        try:
            _flush()
            m = _model()
            if not all_on():
                _MODE[0] = "выключены"
            elif m is None:
                _MODE[0] = "ждут: модель ещё не обучена на текстах"
            else:
                _revive(m)
                for key in TEXT_KEYS:
                    if on(key):
                        _fill(m, key)
                import codestream
                busy = codestream._main_active()
                if busy:
                    _MODE[0] = "вплетаются в основное обучение (шаг потока на каждый %d-й шаг текста)" % _mix()
                elif time.time() - last_burst > 120:
                    _MODE[0] = "подход потоков"
                    if _burst(m):
                        last_burst = time.time()
                    _MODE[0] = "между подходами"
        except Exception as e:
            try:
                store.log("LLM-ПОТОКИ", "ошибка %s: %s" % (type(e).__name__, e))
            except Exception:
                pass
        _WAKE.wait(20)
        _WAKE.clear()


def start():
    if _TH[0] and _TH[0].is_alive():
        return
    _TH[0] = threading.Thread(target=_loop, daemon=True, name="llm-streams")
    _TH[0].start()


def wake():
    _WAKE.set()


def status():
    st = _state()
    w, brain_on = weights()
    tot = sum(w.values()) or 1.0
    rows = []
    for s in STREAMS:
        k = s["key"]
        if k == "code":
            try:
                import codestream
                cs = codestream.status()
                learned, left, q = cs.get("learned", 0), cs.get("left", 0), cs.get("queue", 0)
            except Exception:
                learned = left = q = 0
        else:
            seen = set(st["seen"].get(k) or [])
            mats = texts(k)
            learned = int(st["learned"].get(k) or 0)
            left = sum(1 for sg, _t in mats if sg not in seen)
            q = len(_TQ[k])
        rows.append({"key": k, "title": s["title"], "zones": list(s["zones"]),
                     "on": bool(_s("llm_stream_" + k, True)),
                     "off": (st["off"].get(k) or {}).get("why") or "",
                     "share": round(100 * w[k] / tot), "brain": _LIVE["brain"].get(k, 1.0),
                     "steps": int(_LIVE["steps"].get(k, 0)), "loss": _LIVE["loss"].get(k),
                     "learned": learned, "left": left, "queue": q})
    return {"on": all_on(), "mode": _MODE[0], "mix": _mix(), "brain": brain_on,
            "thread": bool(_TH[0] and _TH[0].is_alive()), "streams": rows}
