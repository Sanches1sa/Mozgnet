# -*- coding: utf-8 -*-
# ===========================================================================
# llmcfg.py — НАСТРОЙКИ СВОЕЙ LLM И ОЦЕНКА НАГРУЗКИ (v3.33, только stdlib)
# ---------------------------------------------------------------------------
# Вынесено из tinyllm.py: раньше без numpy (платформа ставится «без pip»)
# tinyllm.py подменялся старой мини-версией, у которой НЕТ ни cfg(), ни
# estimate(). Панель не получала настройки и расчёт нагрузки, а поля формы
# каждые 5 секунд возвращались к умолчаниям — владелец видел «параметры не
# сохраняются, нагрузку не считает». Здесь всё это работает и без numpy:
#   * cfg()/_fix() — настройки без верхних потолков (только нижние границы);
#   * estimate() — параметры, память (веса+Adam+активации), вердикт «потянет
#     ли этот компьютер» по ОЗУ всего и СВОБОДНОЙ сейчас (Windows —
#     GlobalMemoryStatusEx, Linux — /proc/meminfo), время обучения по замеру
#     скорости ЦП;
#   * numpy_status()/install_numpy() — честно сказать, что numpy нет, и
#     поставить его кнопкой из панели (pip install numpy в этот же Python).
# ===========================================================================
import os
import re
import subprocess
import sys
import threading
import time

DEFAULTS = {"llm_d": 128, "llm_layers": 2, "llm_heads": 4, "llm_ctx": 64,
            "llm_vocab": 4000, "llm_epochs": 3, "llm_batch": 16,
            "llm_corpus_tokens": 120000, "llm_cpu": 1.0, "llm_lr": 2e-3,
            "llm_inc_max_steps": 300, "llm_inc_max_min": 20,   # потолок шагов одного дообучения (0 — без)
            # параметры генерации
            "llm_temp": 0.9, "llm_topk": 40, "llm_topp": 0.92,
            "llm_rep": 1.3, "llm_max_new": 40,
            # обучать, даже если по оценке не хватит оперативной памяти
            "llm_force": False,
            # v3.71: слова вне словаря при обучении не разбираются по буквам
            # (модель не учится «собирать» слова); 0 — микро-батч подбирается сам
            "llm_time_h": 0.0, "llm_wordonly": True, "llm_micro_batch": 0,
            "llm_use_swap": True, "llm_disk_state": True, "llm_disk_mb": 0, "llm_answer_sents": 10,
            "llm_step_s": 10.0, "llm_plan_steps": 3000}   # v3.100: цель — секунд на один шаг обучения (0 — выкл.)


def _fix(c):
    """Только нижние границы здравого смысла (верхних потолков нет)."""
    c["llm_heads"] = max(1, int(c["llm_heads"]))
    c["llm_d"] = max(c["llm_heads"], int(c["llm_d"]))
    c["llm_d"] -= c["llm_d"] % c["llm_heads"]              # делится на головы
    c["llm_layers"] = max(1, int(c["llm_layers"]))
    c["llm_ctx"] = max(4, int(c["llm_ctx"]))
    c["llm_vocab"] = max(64, int(c["llm_vocab"]))
    c["llm_epochs"] = max(1, int(c["llm_epochs"]))
    c["llm_batch"] = max(1, int(c["llm_batch"]))
    c["llm_corpus_tokens"] = max(100, int(c["llm_corpus_tokens"]))
    c["llm_cpu"] = max(0.01, float(c["llm_cpu"]))          # >=1 — без пауз
    c["llm_inc_max_steps"] = max(0, int(c.get("llm_inc_max_steps", 300)))
    c["llm_inc_max_min"] = max(0, int(float(c.get("llm_inc_max_min", 20))))
    c["llm_time_h"] = max(0.0, float(c.get("llm_time_h", 0.0)))
    c["llm_step_s"] = max(0.0, float(c.get("llm_step_s", 10.0)))
    c["llm_plan_steps"] = max(0, int(float(c.get("llm_plan_steps", 3000))))
    c["llm_micro_batch"] = max(0, int(float(c.get("llm_micro_batch", 0))))
    c["llm_lr"] = max(1e-8, float(c["llm_lr"]))
    c["llm_temp"] = max(0.01, float(c["llm_temp"]))
    c["llm_topk"] = max(0, int(c["llm_topk"]))
    c["llm_topp"] = max(0.01, min(1.0, float(c["llm_topp"])))
    c["llm_rep"] = max(1.0, float(c["llm_rep"]))
    c["llm_max_new"] = max(1, int(c["llm_max_new"]))
    return c


def cfg():
    """Текущие настройки своей LLM из settings.json (с умолчаниями)."""
    try:
        import store
        s = store.settings()
    except Exception:
        s = {}
    try:                       # v3.76: один раз — доля ЦП 1.0 (по просьбе владельца)
        if s and not s.get("llm_cpu_mig"):
            import store as _st
            _st.update_settings({"llm_cpu": 1.0, "llm_cpu_mig": True})
            s = _st.settings()
    except Exception:
        pass
    try:                       # v3.81: один раз — без потолка времени, Adam на диске
        if s and not s.get("llm_mig81"):
            import store as _st2
            _st2.update_settings({"llm_time_h": 0.0, "llm_disk_state": True,
                                  "llm_mig81": True})
            s = _st2.settings()
    except Exception:
        pass
    c = {}
    for k, d in DEFAULTS.items():
        v = s.get(k)
        try:
            if isinstance(d, bool):
                c[k] = (v if isinstance(v, bool) else
                        str(v).strip().lower() in ("1", "true", "да", "on",
                                                   "yes")) \
                    if v is not None else d
            else:
                c[k] = type(d)(v) if v is not None else d
        except (TypeError, ValueError):
            c[k] = d
    return _fix(c)


# ------------------------------ компьютер -----------------------------------
def ram_mb():
    """Оперативная память компьютера, МБ (0 — узнать не удалось)."""
    try:
        import sysload
        tot, _free = sysload._sys_mem()
        if tot:
            return int(tot)
    except Exception:
        pass
    try:
        return int(os.sysconf("SC_PAGE_SIZE") *
                   os.sysconf("SC_PHYS_PAGES") / 1048576)
    except Exception:
        return 0


def ram_free_mb():
    """Доступная память прямо сейчас, МБ (0 — неизвестно)."""
    try:
        import sysload
        _tot, free = sysload._sys_mem()
        return int(free)
    except Exception:
        return 0


def swap_mb():
    """Файл подкачки / swap, МБ: (всего, свободно). (0, 0) — нет или не узнать.
    Linux: /proc/meminfo; Windows: GlobalMemoryStatusEx (предел файла подкачки
    = лимит фиксации минус физическая память)."""
    try:
        tot = free = 0
        with open("/proc/meminfo") as f:
            for ln in f:
                if ln.startswith("SwapTotal"):
                    tot = int(ln.split()[1]) // 1024
                elif ln.startswith("SwapFree"):
                    free = int(ln.split()[1]) // 1024
        return tot, free
    except Exception:
        pass
    try:
        import ctypes

        class MS(ctypes.Structure):
            _fields_ = [("l", ctypes.c_ulong), ("ml", ctypes.c_ulong),
                        ("tp", ctypes.c_ulonglong), ("ap", ctypes.c_ulonglong),
                        ("tpf", ctypes.c_ulonglong), ("apf", ctypes.c_ulonglong),
                        ("tv", ctypes.c_ulonglong), ("av", ctypes.c_ulonglong),
                        ("ae", ctypes.c_ulonglong)]
        m = MS()
        m.l = ctypes.sizeof(MS)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
        tot = max(0, int((m.tpf - m.tp) // 1048576))
        free = max(0, int((m.apf - m.ap) // 1048576))
        return tot, free
    except Exception:
        return 0, 0


def _use_swap():
    try:
        import store
        return bool(store.settings().get("llm_use_swap", True))
    except Exception:
        return True


def have_numpy():
    try:
        import numpy  # noqa: F401
        return True
    except Exception:
        return False


_BENCH = {"gflops": 0.0, "ts": 0.0, "np": None}


def bench_gflops():
    """Скорость вычислений ЭТОГО компьютера, ГФЛОПС: с numpy — умножение
    матриц 384x384 (~0,1 с), без numpy — цикл на чистом Python. Кэш 10 мин."""
    now = time.time()
    np_now = have_numpy()
    if _BENCH["gflops"] and now - _BENCH["ts"] < 600 and \
            _BENCH["np"] == np_now:
        return _BENCH["gflops"]
    g = 0.01
    try:
        if np_now:
            import numpy as np
            n, reps = 384, 6
            a = np.random.rand(n, n).astype(np.float32)
            b = np.random.rand(n, n).astype(np.float32)
            a @ b
            t0 = time.perf_counter()
            for _ in range(reps):
                a @ b
            dt = time.perf_counter() - t0
            g = 2.0 * n ** 3 * reps / max(dt, 1e-6) / 1e9
        else:
            # без numpy замерить скорость будущей модели нельзя (чистый
            # Python в ~100 раз медленнее) — оцениваем по числу ядер
            g = max(1.0, (os.cpu_count() or 1) * 4.0)
    except Exception:
        pass
    _BENCH.update(gflops=round(max(g, 0.001), 3), ts=now, np=np_now)
    return _BENCH["gflops"]


def _fmt_t(sec):
    sec = int(sec)
    if sec < 90:
        return "%d с" % sec
    if sec < 5400:
        return "%d мин" % round(sec / 60.0)
    if sec < 172800:
        return "%.1f ч" % (sec / 3600.0)
    return "%.1f сут" % (sec / 86400.0)


def estimate(c=None):
    """Параметры / память / время по конфигу (до обучения) и вердикт «потянет
    ли этот компьютер»: ОЗУ всего и свободная сейчас, время обучения по
    замеру скорости ЦП (ориентировочно, ~30% от пика)."""
    c = _fix(dict(c or cfg()))
    D, L, V, T = c["llm_d"], c["llm_layers"], c["llm_vocab"], c["llm_ctx"]
    H, B = c["llm_heads"], c["llm_batch"]
    params = V * D + T * D + L * (12 * D * D + 13 * D) + 2 * D
    flops = 2 * params + 2 * L * T * D                    # на токен, вперёд
    virtual = params * 4 * 4 / 1048576.0                  # веса + Adam (m, v) + градиенты
    disk_state = _disk_state()
    # v3.78: Adam (m, v) на диске -> в ОЗУ только веса и градиенты
    mem = params * 4 * (2 if disk_state else 4) / 1048576.0
    act = B * T * L * (16 * D + H * T) * 4 * 1.5 / 1048576.0   # активации
    need = mem + act
    ram, free = ram_mb(), ram_free_mb()
    g = bench_gflops()
    tok_s = g * 1e9 * 0.3 / max(1.0, flops * 3.0)
    tok_s = max(1e-3, tok_s * min(1.0, c["llm_cpu"]))
    train_s = c["llm_corpus_tokens"] * c["llm_epochs"] / tok_s
    if not ram:
        verdict, note = "unknown", "память компьютера узнать не удалось"
    elif need <= 0.5 * free:
        verdict, note = "ok", "потянет: нужно ~%d МБ, свободно %d из %d МБ" \
            % (need, free, ram)
    elif need <= 0.9 * free:
        verdict, note = "tight", ("впритык: нужно ~%d МБ, свободно %d из "
                                  "%d МБ — закрой лишнее" % (need, free, ram))
    elif _use_swap() and need <= 0.5 * free + 0.6 * swap_mb()[1] and \
            need <= 0.85 * (ram + swap_mb()[0]):
        sw = swap_mb()
        verdict, note = "swap", ("часть модели уйдёт в файл подкачки: нужно "
                                 "~%d МБ, свободно ОЗУ %d МБ + подкачки %d МБ "
                                 "— обучение будет заметно медленнее (диск)"
                                 % (need, free, sw[1]))
    elif need <= 0.85 * ram:
        verdict, note = "busy", ("сейчас не хватает свободной памяти (нужно "
                                 "~%d МБ, свободно %d из %d МБ): закрой "
                                 "программы, иначе будет своп"
                                 % (need, free, ram))
    else:
        verdict, note = "no", ("НЕ потянет: нужно ~%d МБ, всего в "
                               "компьютере %d МБ" % (need, ram))
    note = note[:1].upper() + note[1:]
    note += "; обучение ориентировочно %s (скорость ЦП ~%s ГФЛОПС%s, ~%d " \
            "токенов/с)" % (_fmt_t(train_s), g,
                            "" if have_numpy() else ", оценка по ядрам: без "
                            "numpy замер невозможен", tok_s)
    return {"params": int(params), "mem_mb": round(mem, 1),
            "virtual_mb": round(virtual, 1), "disk_state": disk_state,
            "disk_mb": round(params * 4 * 2 / 1048576.0, 1) if disk_state else 0,
            "act_mb": round(act, 1), "need_mb": round(need, 1),
            "flops_per_token": int(flops),
            "train_flops_per_token": int(flops * 3),
            "ram_mb": ram, "ram_free_mb": free,
            "fits": (not ram) or need <= ram * 0.85 or verdict == "swap",
            "swap_total_mb": swap_mb()[0], "swap_free_mb": swap_mb()[1],
            "verdict": verdict, "note": note, "gflops": g,
            "tok_s_est": round(tok_s, 1), "train_s_est": int(train_s)}


def _disk_state():
    try:
        import store
        return bool(store.settings().get("llm_disk_state", False))
    except Exception:
        return False


def disk_budget_mb():
    """Сколько диск готов отдать под состояние обучения: настройка llm_disk_mb
    (0 — автоматически: половина свободного места, не больше 20 ГБ)."""
    try:
        import store
        v = int(float(store.settings().get("llm_disk_mb", 0) or 0))
        if v > 0:
            return v
        import shutil
        free = shutil.disk_usage(store.p("")).free / 1048576.0
        return int(min(20480, free * 0.5))
    except Exception:
        return 0


def corpus_tokens_now():
    """Оценка размера задачи: токенов во всей базе знаний (≈ знаки / 4)."""
    try:
        import kb
        n = 0
        for a in kb.list_articles(limit=1000000, include_devices=True) or []:
            n += int(a.get("chars") or a.get("len") or 0)
        return int(n / 4.0)
    except Exception:
        return 0


_PRESETS = [  # (d, слоёв, голов, ctx, батч) — от малого к крупному
    (128, 2, 4, 64, 16), (192, 3, 6, 64, 16), (256, 4, 8, 96, 16),
    (384, 6, 6, 128, 12), (512, 6, 8, 128, 8), (768, 8, 12, 128, 8),
    (1024, 10, 16, 128, 4)]


def recommend(vocab=None, use_disk=None):
    """v3.78: размеры под ЗАДАЧУ и память. Берётся самый крупный набор, у
    которого (1) параметров не больше ~ токенов базы / 10 (больше — модель
    запомнит текст, а не обобщит), (2) веса+градиенты (+Adam, если он в ОЗУ)
    ≤ 40 % свободной ОЗУ, (3) при llm_disk_state состояние Adam помещается в
    диск-бюджет. Кэш процессора напрямую не читается (ОС не отдаёт его
    надёжно), косвенно его учитывает микро-батч. Скорость не гарантируется."""
    base = cfg()
    V = int(vocab or base.get("llm_vocab") or 4000)
    free = ram_free_mb() or ram_mb() or 2048
    sw_free = swap_mb()[1] if _use_swap() else 0
    free_eff = free + 0.5 * sw_free         # подкачка — на треть дороже ОЗУ по скорости
    disk = _disk_state() if use_disk is None else bool(use_disk)
    dbud = disk_budget_mb() if disk else 0
    toks = corpus_tokens_now()
    best = _PRESETS[0]
    for d, L, H, T, B in _PRESETS:
        params = V * d + T * d + L * (12 * d * d + 13 * d) + 2 * d
        ram = params * 4 * (2 if disk else 4) / 1048576.0
        if ram > 0.4 * free_eff:
            continue
        if disk and params * 8 / 1048576.0 > dbud:
            continue
        if toks and params > max(2e6, toks / 10.0) and (d, L, H, T, B) != _PRESETS[0]:
            continue
        best = (d, L, H, T, B)
    d, L, H, T, B = best
    return {"llm_d": d, "llm_layers": L, "llm_heads": H, "llm_ctx": T,
            "llm_batch": B, "llm_vocab": V, "free_mb": free,
            "corpus_tokens": toks, "disk_state": disk, "disk_mb": dbud,
            "swap_free_mb": sw_free}


# --------------------------- качество текста (без numpy) --------------------
_WORDS = re.compile(r"[а-яёa-z]+", re.I)
_SYMS = set("/\\<>{}()[]«»|=_*#@~^")


def text_quality(text, known):
    """Качество текста 0..1 по функции known(слово)->bool: доля БУКВ, лежащих
    в известных словах (длиннее 2 знаков), * поправка на повторы * штраф за
    «кодовые» символы. Меньше 4 известных слов — 0."""
    t = str(text or "")
    words = [w.lower() for w in _WORDS.findall(t)]
    if not words:
        return 0.0
    kn = [w for w in words if len(w) >= 3 and known(w)]
    if len(kn) < 4:
        return 0.0
    total = sum(len(w) for w in words)
    cov = sum(len(w) for w in kn) / float(max(1, total))
    distinct = len(set(kn)) / float(len(kn))
    sym = sum(1 for ch in t if ch in _SYMS) / float(max(1, len(t)))
    return round(max(0.0, cov * min(1.0, distinct * 1.3) *
                     max(0.0, 1.0 - 4.0 * sym)), 2)


def dict_quality(text):
    """То же по словарям облака (без модели): слово «известно», если оно
    есть в словарях (или его основа без 2 последних букв)."""
    try:
        import brain
        known_set = brain._dict_load_words()[0]
    except Exception:
        return 0.0
    return text_quality(text, lambda w: w in known_set or
                        (len(w) > 5 and w[:-2] in known_set))


# ------------------------------- numpy -------------------------------------
_PIP = {"state": "idle", "log": "", "ts": 0.0}


def numpy_status():
    return {"have": have_numpy(), "state": _PIP["state"],
            "log": _PIP["log"][-600:], "python": sys.executable,
            "cmd": "%s -m pip install numpy" % (sys.executable or "python")}


def install_numpy():
    """pip install numpy в ЭТОТ ЖЕ Python (кнопка панели; только владелец).
    Выполняется в фоне; по успеху tinyllm перечитывается без перезапуска."""
    if have_numpy():
        _PIP.update(state="done", log="numpy уже установлен")
        return numpy_status()
    if _PIP["state"] == "running":
        return numpy_status()
    _PIP.update(state="running", log="запускаю pip…", ts=time.time())

    def job():
        flags = {}
        if os.name == "nt":
            flags["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        out = ""
        ok = False
        for extra in ([], ["--user"]):
            try:
                r = subprocess.run(
                    [sys.executable, "-m", "pip", "install",
                     "--disable-pip-version-check", "numpy"] + extra,
                    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT, timeout=900, **flags)
                out = r.stdout.decode("utf-8", "replace")
                if r.returncode == 0:
                    ok = True
                    break
            except Exception as e:                      # нет сети/pip и т.п.
                out = "%s: %s" % (type(e).__name__, e)
        _PIP["log"] = out[-1500:]
        if not ok:
            _PIP["state"] = "error"
            return
        try:
            import importlib
            importlib.invalidate_caches()
            import numpy  # noqa: F401
            tl = sys.modules.get("tinyllm")
            if tl is not None:
                importlib.reload(tl)            # numpy-версия подхватывается
            try:
                import tinyllm
                tinyllm.ensure_training()
            except Exception:
                pass
            _PIP["state"] = "done"
        except Exception as e:
            _PIP["state"] = "error"
            _PIP["log"] += "\n%s: %s (перезапусти облако)" % (
                type(e).__name__, e)
    threading.Thread(target=job, daemon=True, name="pip-numpy").start()
    return numpy_status()
