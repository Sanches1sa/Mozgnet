# -*- coding: utf-8 -*-
"""v3.176: ВТОРОЙ ПОТОК СВОЕЙ LLM — постоянная учёба коду на чтении репозиториев.

Первый поток (tinyllm) учит модель на статьях базы знаний. Второй поток
(этот модуль, нить «llm-code-stream») всё время смотрит, что облако
прочитало в репозиториях (ссылки GitHub/GitLab/… в «API и ответы», ZIP
репозиториев). Функции, на которых модель ещё не училась, он отдаёт ей
короткими курсами кода (codeboot):
  * по 80 новых функций за курс, по порядку чтения, пока не выучит все;
  * новых нет — раз в «повтор, мин» повторяет случайные уже выученные;
  * у каждого курса есть резервная копия весов: если контрольный loss кода
    или обычного текста стал хуже больше чем на 5 %, веса откатываются;
  * курс чередует код и обычные тексты 1:1 (режим dual), поэтому русский
    модель не забывает;
  * пока своя LLM пишет ответ, курс не начинается — ответ важнее.
Веса одни, поэтому два потока не обучают их в один и тот же миг:
  * первый поток учится (долгий план по статьям) — второй готовит окна кода
    из непрочитанных моделью функций, а первый между своими шагами делает
    шаг на коде (каждый llm_code_mix-й шаг, по умолчанию каждый 4-й). Так
    код учится, не останавливая учёбу текстам;
  * первый поток отдыхает — второй сам гоняет короткий курс кода (codeboot:
    с резервной копией весов и откатом). Первый курс ещё и добавляет в
    словарь токены кода — без них окна кода не собрать.

Настройки: llm_code_stream (вкл.), llm_code_every_min (пауза между курсами,
10), llm_code_repeat_min (повтор выученного, 60), llm_code_steps (120),
llm_code_seconds (150).
"""
import collections
import hashlib
import math
import random
import threading
import time

import store

_TH = [None]
_WAKE = threading.Event()
_MAX_SEEN = 50000
_BATCH = 80
_Q = collections.deque(maxlen=48)      # (подпись функции, окно токенов)
_QLOCK = threading.Lock()
_QUEUED = set()                        # подписи, чьи окна уже в очереди
_LEFT = collections.Counter()          # подпись -> окон ещё не пройдено
_DONE = []                             # выученные вплетением, ещё не в файле
_CNT = [0]
_MODE = ["ждёт"]


def _s(k, d):
    v = store.settings().get(k)
    return d if v is None else v


def enabled():
    """v3.177: поток кода — часть потоков своей LLM (llmstreams)."""
    try:
        import llmstreams
        return llmstreams.on("code")
    except Exception:
        return bool(_s("llm_code_stream", True))


def _num(k, d, lo, hi):
    try:
        return max(lo, min(hi, float(_s(k, d))))
    except (TypeError, ValueError):
        return d


def _sig(row):
    return hashlib.md5(str(row.get("c") or "").encode("utf-8", "replace")).hexdigest()[:16]


def _state():
    st = store.load_json("code_stream.json", {}) or {}
    st.setdefault("seen", [])
    st.setdefault("runs", 0)
    st.setdefault("learned", 0)
    st.setdefault("rollbacks", 0)
    st.setdefault("last", 0.0)
    st.setdefault("last_msg", "")
    return st


def _save(st):
    st["seen"] = st["seen"][-_MAX_SEEN:]
    store.save_json("code_stream.json", st)


def _rows():
    import codecorpus
    try:
        return list(codecorpus._load_snips().get("rows") or [])
    except Exception:
        return []


def pick(st, now=None):
    """-> (строки для курса, 'new'|'repeat'|None)."""
    now = now or time.time()
    rows = _rows()
    if not rows:
        return [], None
    seen = set(st.get("seen") or [])
    new = [r for r in rows if _sig(r) not in seen]
    if new:
        return new[:_BATCH], "new"
    rep = _num("llm_code_repeat_min", 60, 1, 100000) * 60
    if now - float(st.get("last") or 0) >= rep:
        return random.sample(rows, min(_BATCH, len(rows))), "repeat"
    return [], None


def _code_vocab(m):
    try:
        return any(k.startswith("\u00a2") for k in m.tok.vocab)
    except Exception:
        return False


def _main_active():
    try:
        import tinyllm as tl
        th = tl._THREAD[0]
        return bool(th and th.is_alive() and th.name != "tinyllm-code-course")
    except Exception:
        return False


def _fill(m):
    """Окна кода для вплетения в обучение первого потока (веса не трогает)."""
    import codeboot
    import codecorpus
    if len(_Q) >= 16:
        return 0
    st = _state()
    seen = set(st["seen"]) | set(_DONE)
    rows = [r for r in _rows() if _sig(r) not in seen and _sig(r) not in _QUEUED][:12]
    if not rows:
        return 0
    length = min(96, int(m.c.get("llm_ctx") or 96))
    n = 0
    for r in rows:
        sg = _sig(r)
        text = codecorpus.CODE_MARK + "# task: " + (codecorpus.ru_en(r.get("t") or "") or
                                                   str(r.get("t") or r.get("n") or "")[:120]) + \
            "\n" + str(r.get("c") or "") + "\n"
        try:
            ws = codeboot._windows(m, [text], length)[:6]
        except Exception:
            ws = []
        with _QLOCK:
            _QUEUED.add(sg)
            if not ws:
                _DONE.append(sg)
                continue
            for w in ws:
                _Q.append((sg, w))
            _LEFT[sg] += len(ws)
        n += len(ws)
    return n


def loss_limit(m):
    """Потолок разумного loss: модель, угадывающая наугад, даёт ln(словарь).
    Выше 2,5·ln(V)+5 — веса разошлись (вместо обучения — порча)."""
    try:
        V = max(10, int(m.tok.size))
    except Exception:
        V = 4000
    return 2.5 * math.log(V) + 5.0


def _diverged(why, m=None):
    """v3.177: выключается только поток кода и сам включается, когда модель
    переучилась (раньше выключались все потоки — навсегда, до ручного вкл.)."""
    st = _state()
    st["diverged"] = why
    st["diverged_ts"] = time.time()
    _save(st)
    try:
        import llmstreams
        llmstreams._off("code", why, m)
    except Exception:
        pass
    _MODE[0] = "остановлен: " + why
    try:
        store.log("LLM-КОД", "второй поток ОСТАНОВЛЕН: " + why)
    except Exception:
        pass


def interleave(m, lr):
    """v3.177: потоками распоряжается llmstreams (код, МАКС, разговор —
    доли по воле мозга-12). Оставлено для совместимости."""
    import llmstreams
    llmstreams.interleave(m, lr)


def _step(m, lr):
    """Один шаг на коде из очереди. -> loss | None (очередь пуста)."""
    import numpy as np
    with _QLOCK:
        take = [_Q.popleft() for _ in range(min(2, len(_Q)))]
    if not take:
        return None
    rows = [w for _sg, w in take]
    L = min(len(w) for w in rows)
    X = np.stack([w[:L - 1] for w in rows])
    Y = np.stack([w[1:L] for w in rows])
    # v3.176.1: шаг кода — мягкий и без множителей «воли» и генератора:
    # не больше 5e-4 (как в курсе кода) и половина шага текста
    import tinyllm as tl
    prev = getattr(tl._FP, "stable_code", False)
    tl._FP.stable_code = True
    try:
        loss = m.train_step(X, Y, min(5e-4, lr * 0.5))
    finally:
        tl._FP.stable_code = prev
    if not math.isfinite(loss) or loss > loss_limit(m):
        _diverged("loss кода %s выше предела %.0f — веса разошлись"
                  % (round(loss, 1) if math.isfinite(loss) else loss, loss_limit(m)), m)
        return loss
    m.meta["code_steps"] = int(m.meta.get("code_steps") or 0) + 1
    m.meta["code_loss"] = round(float(loss), 4)
    with _QLOCK:
        for sg, _w in take:
            _LEFT[sg] -= 1
            if _LEFT[sg] <= 0:
                del _LEFT[sg]
                _QUEUED.discard(sg)
                _DONE.append(sg)
    return loss


def _flush_done():
    with _QLOCK:
        if not _DONE:
            return 0
        got = list(_DONE)
        del _DONE[:]
    st = _state()
    st["seen"].extend(got)
    st["learned"] += len(got)
    st["woven"] = int(st.get("woven") or 0) + len(got)
    st["last_msg"] = "вплетено в обучение: ещё %d функций" % len(got)
    _save(st)
    return len(got)


def _model_ready():
    try:
        import tinyllm as tl
        if not tl.HAVE_NP:
            return False, "нужен numpy"
        m = tl._M
        if m is None or not getattr(m, "p", None) or getattr(m, "steps", 0) < 1:
            return False, "модель ещё не обучена на текстах"
        if getattr(m, "_dead", False):
            return False, "модель удалена"
        return True, ""
    except Exception as e:
        return False, "%s" % e


def _answering():
    try:
        import tinyllm as tl
        a = tl._ANSWERING
        return a[0] > 0 or time.time() - a[1] < 20
    except Exception:
        return False


def _scan_backups():
    """Копии весов от курсов кода до v3.176.1 (без отметки «хорошая»):
    загрузить каждую и проверить loss на тексте базы — берётся самая свежая
    исправная."""
    import os
    from pathlib import Path
    import tinyllm as tl
    import codeboot
    out = []
    try:
        root = Path(store.p("code_boot_backups"))
        cands = sorted((d / "model" for d in root.iterdir() if (d / "model.npz").exists()),
                       key=lambda q: q.with_suffix(".npz").stat().st_mtime, reverse=True)
    except Exception:
        return []
    try:
        texts = codeboot._ordinary_texts()[:4]
    except Exception:
        texts = []
    for q in cands[:4]:
        try:
            g = tl.GPT(dict(tl._M.c))
            if g.load(str(q)) is False:
                continue
            w = codeboot._windows(g, texts, min(96, int(g.c.get("llm_ctx") or 96))) if texts else []
            lo = codeboot._loss(g, w) if w else None
            if lo is None or (math.isfinite(lo) and lo <= loss_limit(g)):
                out.append({"path": str(q), "ts": os.path.getmtime(str(q) + ".npz"),
                            "loss": round(lo, 3) if lo is not None else None})
                break
        except Exception:
            continue
    return out


def restore_good():
    """Вернуть веса из последней хорошей резервной копии (до курса кода,
    loss которого был в норме). -> dict."""
    import tinyllm as tl
    import os
    st = _state()
    good = [g for g in (st.get("good") or []) if os.path.exists(str(g.get("path")) + ".npz")]
    if not good:
        good = _scan_backups()
    if not good:
        return {"ok": False, "error": "хорошей резервной копии нет — нажми «Переобучить на текущей базе»"}
    g = good[-1]
    m = tl._M
    if m is None:
        return {"ok": False, "error": "модель не загружена"}
    th = tl._THREAD[0]
    m._stop = True
    t0 = time.time()
    while th is not None and th.is_alive() and time.time() - t0 < 300:
        th.join(1.0)
    if th is not None and th.is_alive():
        m._stop = False
        return {"ok": False, "error": "обучение не остановилось за 5 мин — попробуй позже"}
    with tl._LOCK:
        if m.load(str(g["path"])) is False:
            m._stop = False
            return {"ok": False, "error": "копия не подходит к текущим размерам модели"}
        m.adam = None
        tl._adam_wipe()
        m.training = False
        m._stop = False
        m.save(tl._state_path())
    st = _state()
    st.pop("diverged", None)
    _save(st)
    try:
        import llmstreams
        ls_ = llmstreams._state(); ls_["off"] = {}; llmstreams._save(ls_)
    except Exception:
        pass
    store.log("LLM-КОД", "веса своей LLM возвращены из копии %s (loss кода был %s)"
              % (time.strftime("%d.%m %H:%M", time.localtime(g.get("ts") or 0)), g.get("loss")))
    try:
        tl.ensure_training()
    except Exception:
        pass
    return {"ok": True, "ts": g.get("ts"), "loss": g.get("loss")}


def run_once():
    """Один курс второго потока. -> dict (для статуса и тестов)."""
    import codeboot
    st = _state()
    ok, why = _model_ready()
    if not ok:
        st["last_msg"] = "ждёт: " + why
        _save(st)
        return {"ok": False, "why": why}
    cs = codeboot.status() or {}
    if cs.get("running"):
        return {"ok": False, "why": "идёт курс кода"}
    rows, kind = pick(st)
    if not rows:
        st["last_msg"] = "всё прочитанное выучено — жду новый код из репозиториев"
        _save(st)
        return {"ok": False, "why": "нечего учить"}
    if _answering():
        return {"ok": False, "why": "своя LLM отвечает"}
    steps = int(_num("llm_code_steps", 120, 1, 2000))
    secs = int(_num("llm_code_seconds", 150, 5, 600))
    try:
        codeboot.start(steps, secs, mode="dual", rows=rows, source="stream")
    except ValueError as e:
        st["last_msg"] = "не начат: %s" % e
        _save(st)
        return {"ok": False, "why": str(e)}
    th = codeboot._THREAD
    if th is not None:
        th.join(secs + 600)
    res = codeboot.status() or {}
    st = _state()
    st["runs"] += 1
    st["last"] = time.time()
    if res.get("backup") and (res.get("before") or {}).get("loss") is not None:
        try:                             # копия весов ДО курса, loss в норме
            import tinyllm as tl
            if float(res["before"]["loss"]) <= loss_limit(tl._M):
                st.setdefault("good", []).append({"path": res["backup"], "ts": time.time(),
                                                  "loss": res["before"]["loss"]})
                st["good"] = st["good"][-2:]
        except Exception:
            pass
    if res.get("phase") == "diverged":
        _diverged(res.get("message") or "веса разошлись")
    if res.get("phase") == "done" and not res.get("rollback"):
        if kind == "new":
            sigs = [_sig(r) for r in rows]
            st["seen"].extend(sigs)
            st["learned"] += len(sigs)
        st["last_msg"] = "%s: %d функций, шагов %s, loss кода %s → %s" % (
            "новый код" if kind == "new" else "повтор", len(rows), res.get("done"),
            (res.get("before") or {}).get("loss"), (res.get("after") or {}).get("loss"))
    elif res.get("rollback"):
        st["rollbacks"] += 1
        if kind == "new":            # не застревать на «трудной» пачке
            st["seen"].extend(_sig(r) for r in rows)
        st["last_msg"] = "курс откатан (loss стал хуже) — веса прежние"
    else:
        st["last_msg"] = "курс: %s" % (res.get("message") or res.get("phase") or "?")
    _save(st)
    try:
        store.log("LLM-КОД", "второй поток: " + st["last_msg"])
    except Exception:
        pass
    return {"ok": True, "kind": kind, "n": len(rows), "phase": res.get("phase"),
            "rollback": bool(res.get("rollback"))}


def _loop():
    time.sleep(90)                       # дать облаку и первому потоку стартовать
    last_course = 0.0
    while True:
        wait = 20
        try:
            _flush_done()
            ok, why = _model_ready()
            if not enabled():
                _MODE[0] = "выключен"
            elif not ok:
                _MODE[0] = "ждёт: " + why
            elif _main_active():
                import tinyllm as tl
                if _code_vocab(tl._M):
                    _fill(tl._M)
                    _MODE[0] = "вплетается в обучение (шаг кода на каждый %d-й шаг текста)" % int(
                        _num("llm_code_mix", 4, 1, 1000))
                else:
                    _MODE[0] = "ждёт паузы первого потока: курс добавит в словарь токены кода"
            elif time.time() - last_course >= _num("llm_code_every_min", 10, 0.5, 10000) * 60:
                _MODE[0] = "курс кода"
                r = run_once()
                if r.get("ok"):
                    last_course = time.time()
                _MODE[0] = "между курсами"
        except Exception as e:
            try:
                store.log("LLM-КОД", "второй поток: ошибка %s: %s" % (type(e).__name__, e))
            except Exception:
                pass
        _WAKE.wait(wait)
        _WAKE.clear()


def _migrate177():
    """v3.177: второй поток выключился сам в v3.176.1 (loss разошедшихся
    весов) и остался выключен навсегда. Модель переучена заново — включаем
    один раз; дальше сторож выключает только сам поток и включает его сам."""
    if store.settings().get("llm_streams_mig177"):
        return
    st = _state()
    st.pop("diverged", None)
    _save(st)
    store.update_settings({"llm_code_stream": True, "llm_streams_mig177": True})
    store.log("LLM-ПОТОКИ", "потоки своей LLM включены (после переобучения модели)")


def start():
    try:
        _migrate177()
    except Exception:
        pass
    try:
        import llmstreams
        llmstreams.start()
    except Exception as e:
        store.log("СТАРТ", "потоки LLM не запустились: %s" % e)
    if _TH[0] and _TH[0].is_alive():
        return
    _TH[0] = threading.Thread(target=_loop, daemon=True, name="llm-code-stream")
    _TH[0].start()


def wake():
    _WAKE.set()


def status():
    st = _state()
    rows = _rows()
    seen = set(st.get("seen") or [])
    left = sum(1 for r in rows if _sig(r) not in seen)
    ok, why = _model_ready()
    try:
        import codeboot
        cs = codeboot.status() or {}
    except Exception:
        cs = {}
    try:
        import tinyllm as tl
        cst = int((tl._M.meta or {}).get("code_steps") or 0) if tl._M is not None else 0
    except Exception:
        cst = 0
    return {"on": enabled(), "thread": bool(_TH[0] and _TH[0].is_alive()),
            "mode": _MODE[0], "queue": len(_Q), "code_steps": cst,
            "woven": int(st.get("woven") or 0),
            "funcs": len(rows), "learned": st["learned"], "left": left,
            "runs": st["runs"], "rollbacks": st["rollbacks"],
            "last": st["last"], "last_msg": st["last_msg"],
            "ready": ok, "why": why,
            "diverged": st.get("diverged") or "",
            "good": len([g for g in (st.get("good") or [])]),
            "running": bool(cs.get("running")) and cs.get("source") == "stream",
            "course_msg": cs.get("message") if cs.get("source") == "stream" else ""}
