# -*- coding: utf-8 -*-
# ===========================================================================
# boardlink.py — СВЯЗЬ МОЗГОВ: облако ↔ плата через ЧАСТОТУ ГЕНЕРАТОРА и
# МЫСЛИ, в реальном времени (v3.102, stdlib)
# ---------------------------------------------------------------------------
# Заявка владельца: «мозги облака должны быть связаны с мозгом плат — через
# частоту генератора в скетче и через мысли»: обучение LLM ведёт плата,
# мышление в реальном времени, ответы в чате и ботах.
#
# Канал: плата (neuro_12 v2.60) раз в ~10 с шлёт POST /api/link:
#   {board, hz, shz, ema, phrases: [{t, shz}]} — свою ЧАСТОТУ генератора (D4,
#   со знаком) и НОВЫЕ МЫСЛИ (каждая — с частотой, на которой родилась).
# Облако:
#   1) частота сразу становится текущим сигналом для всех нейросетей облака
#      (genmod: скорость обучения LLM, выбор слов LLM, freqprior в ответах и
#      сети материалов) — раньше она приходила раз в несколько минут с
#      бандлом памяти;
#   2) каждая мысль учит таблицу «частота ↔ слова» (genmod.learn_frame);
#   3) МОЗГ ОБЛАКА ОТВЕЧАЕТ НА МЫСЛЬ ПЛАТЫ: своя мысль (речь мозга облака по
#      словам мысли платы, из нескольких вариантов — самый созвучный частоте
#      ЭТОЙ платы) + своя LLM дополняет её по тем же словам (проверка опоры и
#      качества); ответ уходит назад в ответе на тот же запрос — плата
#      зажигает им нейроны, кладёт в свою сеть материалов, играет на D6 и
#      подаёт на текст-канал скетча (chat_stim) — от этого меняется ЧАСТОТА
#      ГЕНЕРАТОРА. Круг замкнут: частота -> мысль -> мысль облака -> частота.
# Ответы облака в чате и ботах уже уходят на платы (D6-ящик) — с v2.60 они
# тоже двигают генератор; мысли плат входят в память ответа (brain._mem_brain).
# Честно: «связь» — это обмен словами и числом частоты; облако не понимает
# смысл мысли платы, а подбирает созвучные слова.
# ===========================================================================
import random
import re
import threading
import time

import store

_LOCK = threading.RLock()
_ST = {"boards": {}, "log": [], "n": {"links": 0, "phrases": 0, "replies": 0,
                                     "llm": 0, "frames": 0}}
_BUSY = set()

# ---------- v3.125: СИНХРОНИЗАЦИЯ МОЗГОВ ПО ЧАСТОТЕ ГЕНЕРАТОРА ---------------
# Связанные осцилляторы (модель Курамото по частоте): у каждой платы свой
# генератор на скетче, у платформы — свой «ритм» (активность облака:
# вопросы, ответы, мысли). Облако считает ОБЩИЙ РИТМ (среднее остальных
# плат и платформы) и отдаёт его каждой плате в ответе /api/link; плата
# (v2.67, скетч с RPC sync_hz) подтягивает целевую частоту генератора к нему
# на долю «силы связи» (sync_k, %). Ритм платформы сам тянется к платам.
# Разброс частот (стандартное отклонение) — мера синхронности, виден в панели.
_SY = {"hz": 440.0, "act": [], "spread": [], "ts": 0.0}
# ---------- v3.173: ПЛАВАЮЩИЕ ПАРАМЕТРЫ (не только частота) ---------------
# Температура, фактор скорости обучения, top_p и сила связи плавно
# меняются вместе с общим ритмом. Используется облаком, Макс-ботом и LLM.
import math

_TS = r"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d"


def _crash_tidy(text):
    """v3.186.2: плата до 2.99.2 при каждой неудачной связи приклеивала к
    отчёту о падении ещё одну дату — приходило «дата дата дата … причина».
    Оставляем одну (самую раннюю — момент падения) и обрезанный хвост даты
    в начале убираем."""
    t = str(text or "")
    t = re.sub(r"^[\d:\- ]{1,18}(?=\d{4}-\d\d-\d\d )", "", t)        # обрубок даты
    t = re.sub(r"^-?\d\d-\d\d \d\d:\d\d:\d\d\s+", "", t)
    t = re.sub(r"((?:%s\s+){2,})" % _TS,
               lambda m: re.findall(_TS, m.group(1))[-1] + " ", t)
    return t.strip()

def floating_params(now=None):
    """Плавающие параметры на основе текущего ритма (hz) и времени."""
    now = now or time.time()
    hz = _SY.get("hz") or 440.0
    # Нормированная частота 0..1 относительно 120–2000 Гц
    norm = max(0.0, min(1.0, (hz - 120.0) / (2000.0 - 120.0)))
    # Медленные синусоиды для «дыхания» параметров
    breath = 0.5 + 0.5 * math.sin(now / 47.0)
    return {
        "temp": round(0.55 + 0.35 * norm * breath, 3),          # 0.55–0.90
        "lr_factor": round(0.85 + 0.30 * norm, 3),              # 0.85–1.15
        "top_p": round(0.88 + 0.08 * (1.0 - norm), 3),          # чуть выше при низкой частоте
        "sync_k": round(0.10 + 0.25 * breath, 3),               # сила связи 0.10–0.35
        "creativity": round(norm * breath, 3),
        "hz": int(round(hz)),
        # v3.173.1: spread — список пар (время, разброс); раньше int(список) падал
        "spread": int(_SY["spread"][-1][1]) if _SY.get("spread") else 0,
    }

HZ_MIN, HZ_MAX = 120.0, 2000.0


def note_activity(weight=1.0):
    """Отметка активности мозга облака (ответ, мысль) — его «ритм»."""
    now = time.time()
    with _LOCK:
        _SY["act"].append((now, float(weight)))
        _SY["act"] = [a for a in _SY["act"] if now - a[0] < 300][-500:]


def _platform_hz(board_mean):
    now = time.time()
    act = sum(w for t, w in _SY["act"] if now - t < 300)
    own = HZ_MIN + (HZ_MAX - HZ_MIN) * min(1.0, act / 20.0)
    tgt = own if board_mean is None else 0.5 * own + 0.5 * board_mean
    _SY["hz"] = 0.7 * _SY["hz"] + 0.3 * tgt
    return _SY["hz"]


def _sync_for(board, now):
    """Общий ритм для платы: среднее частот ОСТАЛЬНЫХ плат и платформы."""
    k = max(0, min(60, cfg("sync_k", 30)))
    if k <= 0 or not cfg("sync_on", True):
        return None
    try:
        import breath as _brs
        _brs._mark("Синхронизация частот плат")
    except Exception:
        pass
    fresh = {bid: float(b.get("hz") or 0) for bid, b in _ST["boards"].items()
             if now - float(b.get("ts") or 0) < 90 and float(b.get("hz") or 0) > 0}
    mean_all = (sum(fresh.values()) / len(fresh)) if fresh else None
    ph = _platform_hz(mean_all)
    others = [h for bid, h in fresh.items() if bid != board] + [ph]
    target = sum(others) / len(others)
    vals = list(fresh.values()) + [ph]
    m = sum(vals) / len(vals)
    spread = (sum((v - m) ** 2 for v in vals) / len(vals)) ** 0.5
    if now - _SY["ts"] > 20:
        _SY["spread"].append((int(now), int(spread)))
        _SY["spread"] = _SY["spread"][-60:]
        _SY["ts"] = now
    fp = floating_params(now)
    return {"hz": int(round(target)), "k": int(k), "spread": int(spread),
            "float": fp,  # v3.173: плавающие параметры для платы / LLM / бота
            "platform": int(round(ph)), "boards": len(fresh)}


def cfg(key, default):
    try:
        v = store.settings().get(key, default)
        return type(default)(v)
    except Exception:
        return default


def _z(shz):
    try:
        return max(-1.0, min(1.0, float(shz) / 2000.0))
    except (TypeError, ValueError):
        return None


def _note_signal(board, hz, shz, ema):
    """Частота платы — сразу текущий сигнал всех нейросетей облака."""
    try:
        import genmod
        with genmod._LOCK:
            genmod._load()
            d = genmod._ST["devs"].get(board) or {}
            d.update({"hz": int(hz or 0), "ts": time.time(),
                      "ema": float(ema or d.get("ema") or hz or 0)})
            if shz is not None:
                d["shz"] = float(shz)
            d.setdefault("ring", [])
            genmod._ST["devs"][board] = d
            # файл — не чаще раза в минуту (канал частый)
            if time.time() - float(_ST.get("saved") or 0) > 60:
                _ST["saved"] = time.time()
                store.save_json("genstate.json", {"devs": genmod._ST["devs"]})
    except Exception:
        pass


def _cloud_thought(phrase, z):
    """Речь мозга облака по словам мысли платы: несколько вариантов, берём
    самый созвучный частоте этой платы (freqprior.bonus с её уровнем)."""
    try:
        import wordmind
    except Exception:
        return ""
    cands = []
    for _ in range(4):
        try:
            t, _i = wordmind.compose(phrase)
        except Exception:
            t = ""
        if t and t not in cands:
            cands.append(t)
    if not cands:                         # речь не собралась — память облака
        cands = _memory_sents(phrase) or _evidence_sents(phrase)
    cands = [c for c in cands if not _echo(c, phrase)]
    if not cands:
        return ""
    try:
        import freqprior
        if z is not None:
            cands.sort(key=lambda c: -freqprior.bonus(c, z=z))
    except Exception:
        pass
    return cands[0][:240]


def _memory_sents(phrase, k=3):
    """Что мозг облака ПОМНИТ по словам мысли платы: предложения сети
    материалов (без служебных строк) -> [предложение]."""
    try:
        import brain
        if brain._MN is None:
            return []
        # v3.105: сеть занята (ответ, обучение) — не ждём: фон не должен
        # тормозить ответы и панель
        if not brain._MN_LOCK.acquire(timeout=0.2):
            return []
        try:
            out = brain.mn().write(phrase, words=60)
        finally:
            brain._MN_LOCK.release()
        if not out or any(x in out for x in brain._MN_EMPTY):
            return []
        sents = []
        for ln in out.splitlines():
            ln = ln.strip()
            if not ln or ln.startswith(("_", "#", "📝", "Источник")):
                continue
            for sn in re.split(r"(?<=[.!?])\s+", ln):
                sn = sn.strip()
                if 25 <= len(sn) <= 240 and sn not in sents:
                    sents.append(sn)
        return sents[:k]
    except Exception:
        return []


def _evidence_sents(phrase, k=3):
    """Похожая статья базы и ассоциации сети по словам мысли платы."""
    try:
        import brain
        import hypo
        if not brain._MN_LOCK.acquire(timeout=0.2):   # v3.105: занято — молчим
            return []
        brain._MN_LOCK.release()
        sents = []
        for t, _src in hypo.evidence(phrase):
            for sn in re.split(r"(?<=[.!?])\s+", str(t)):
                sn = sn.strip()
                if 25 <= len(sn) <= 240 and sn not in sents:
                    sents.append(sn)
        return sents[:k]
    except Exception:
        return []


def _echo(a, b):
    """Ответ — почти повтор мысли платы (не ответ)."""
    wa = set(re.findall(r"[а-яё]{3,}", str(a).lower()))
    wb = set(re.findall(r"[а-яё]{3,}", str(b).lower()))
    return bool(wa) and len(wa & wb) / float(len(wa)) >= 0.7


def _llm_thought(phrase, base):
    """Своя LLM дополняет мысль мозга — только словами из материала мозга."""
    try:
        import llmlink
        import tinyllm
        st = tinyllm.status()
        if not st.get("ready"):
            return ""
        t, i = tinyllm.compose(str(phrase)[:120], str(base)[:1200])
        if t and len(t.split()) >= 3 and llmlink._words_ok(t, base) and \
                float(i.get("grounded") or 0) >= llmlink.cfg()["gen_minq"] \
                and float(tinyllm.quality(t)) >= llmlink.cfg()["minq"] and \
                not tinyllm.has_garbage(t):
            return t.strip()[:240]
    except Exception:
        pass
    return ""


def _react(board, phrase, shz):
    """Мозг облака отвечает на мысль платы (в фоне)."""
    try:
        z = _z(shz)
        ct = _cloud_thought(phrase, z)
        lt = _llm_thought(phrase, (ct + " " + phrase).strip())
        if lt and (_echo(lt, phrase) or (ct and _echo(lt, ct))):
            lt = ""                       # повтор — не мысль
        parts = [p for p in (ct, lt) if p]
        if not parts:
            return
        if len(parts) == 2 and parts[1][:30].lower() in parts[0].lower():
            parts = parts[:1]
        reply = " ".join(parts)
        sign = 0
        if shz is not None:
            sign = -1 if float(shz) < 0 else 1
        store.d6_add(board, reply, "Связь мозгов", sign, "частота")
        with _LOCK:
            _ST["n"]["replies"] += 1
            if lt:
                _ST["n"]["llm"] += 1
            _ST["log"].append({"ts": time.time(), "board": board,
                               "shz": shz, "phrase": phrase[:200],
                               "cloud": ct[:200], "llm": lt[:200]})
            _ST["log"] = _ST["log"][-30:]
        try:
            store.thoughts_add("облако", "↔ «%s» (%s): «%s»" % (
                board, ("%+d Гц" % int(shz)) if shz is not None else "?",
                reply[:200]), kind="link")
        except Exception:
            pass
    except Exception as e:
        try:
            store.log("СВЯЗЬ МОЗГОВ", "ответ на мысль не собрался: %s" % e)
        except Exception:
            pass
    finally:
        with _LOCK:
            _BUSY.discard(board)


# ============ v3.104: ВОПРОС МОЗГУ ПО КАНАЛУ СВЯЗИ + ЗДОРОВЬЕ ПЛАТЫ ============
# Облако не всегда может само достучаться до платы (WinError 10061 —
# «подключение отвергнуто»: порт 7000 закрыт, приложение платы
# перезапускается, сменился IP, мешает брандмауэр). Канал связи идёт ОТ
# ПЛАТЫ (она сама шлёт /api/link раз в 10 с), поэтому через него вопрос
# мозгу проходит и тогда, когда мост не работает: облако кладёт вопрос в
# ответ на /api/link, плата тут же думает (как /api/think) и присылает
# ответ отдельным /api/link с полем answers. Плата v2.61+.
_ASK = {}                     # id -> {"text", "board", "ev", "res", "ts"}


def live_boards(max_age=30.0):
    now = time.time()
    with _LOCK:
        return [bid for bid, b in _ST["boards"].items()
                if now - float(b.get("ts") or 0) <= max_age and
                b.get("asks_ok")]


def ask(text, timeout=14.0):
    """Вопрос мозгу платы через канал связи -> как bridge.think | None."""
    bs = live_boards()
    if not bs:
        return None
    qid = "q%d" % int(time.time() * 1000)
    slot = {"text": str(text)[:2000], "board": bs[0], "ev": threading.Event(),
            "res": None, "ts": time.time(), "sent": False}
    with _LOCK:
        _ASK[qid] = slot
    slot["ev"].wait(float(timeout))
    with _LOCK:
        _ASK.pop(qid, None)
    r = slot["res"]
    if not r:
        return {"ok": False, "error": "плата не ответила по каналу связи за "
                "%d с" % int(timeout)}
    mem = []
    for m in (r.get("memory") or [])[:6]:
        if isinstance(m, dict) and str(m.get("text") or "").strip():
            mem.append({"text": str(m["text"]).strip()[:1500],
                        "src": str(m.get("src") or "память платы")[:80]})
    _ST["n"]["asks"] = int(_ST["n"].get("asks") or 0) + 1
    return {"ok": True, "answer": str(r.get("answer") or "").strip(),
            "thought": str(r.get("thought") or r.get("answer") or "").strip(),
            "memory": mem, "grounded": bool(mem), "board": slot["board"],
            "why": r.get("why") or "", "url": "канал связи",
            "ms": r.get("ms"), "via": "link"}


def _health(board, b, up, rss, crash, ver, ip):
    """Перезапуск приложения платы, память, причина падения — в журнал."""
    try:
        up = float(up) if up is not None else None
    except (TypeError, ValueError):
        up = None
    if up is not None:
        prev = b.get("up")
        if prev is not None and up + 30 < float(prev):
            b["restarts"] = int(b.get("restarts") or 0) + 1
            store.log("СВЯЗЬ МОЗГОВ", "приложение платы «%s» перезапустилось "
                      "(до этого работало %d мин%s)" % (
                          board, int(float(prev) / 60),
                          (", память была %s МБ" % b.get("rss"))
                          if b.get("rss") else ""))
        b["up"] = up
    if rss is not None:
        try:
            b["rss"] = int(rss)
            b.setdefault("rss_first", int(rss))
        except (TypeError, ValueError):
            pass
    if crash:
        crash = _crash_tidy(crash)
        b["crash"] = str(crash)[:1500]
        b["crash_ts"] = time.time()
        store.log("СВЯЗЬ МОЗГОВ", "плата «%s» сообщила о прошлом падении: %s"
                  % (board, str(crash)[:600]))
    if ver:
        b["ver"] = str(ver)[:20]
    if ip:
        b["ip"] = str(ip)[:60]


def _learn_phrases(board, ph):
    """Фоновая учёба на мыслях платы из /api/link (см. link)."""
    approved=[]
    for t, _s in ph:
        try:                               # v3.121: вердикт связности
            import coherence
            verdict=coherence.judge(t)
            coherence.verdict(board,t,verdict)
            if verdict.get("ok"):approved.append((t,_s))
        except Exception:
            pass
    try:                                   # v3.133: мысли плат -> общий словарь
        import worddict
        for t, s in approved:
            worddict.learn(t,hz=s)  # stored for feelings; lexical scoring ignores Hz
    except Exception:
        pass
    try:                                   # мысль + её частота -> смысл частоты
        import genmod
        for t, s in ph:
            if s is not None and genmod.learn_frame(board, t, s):
                with _LOCK:
                    _ST["n"]["frames"] += 1
    except Exception:
        pass


def link(board, hz=0, shz=None, ema=0, phrases=None, answers=None,
         up=None, rss=None, crash=None, ver=None, ip=None, asks_ok=False,
         bridge=None, grav=None, net=None, cam=None, breath=None, will=None,
         extras_err=None, lead=None, neuron_context=None):
    """POST /api/link от платы. -> {msgs (мысли облака для платы), z, ...}."""
    board = str(board or "")[:40]
    if not board:
        return {"ok": False, "error": "нет board"}
    if neuron_context is not None:
        from neuron_context import note as note_neurons
        note_neurons(board,neuron_context)
    now = time.time()
    for a in (answers or [])[:6]:          # ответы на вопросы по каналу
        if not isinstance(a, dict):
            continue
        with _LOCK:
            slot = _ASK.get(str(a.get("id") or ""))
        if slot:
            slot["res"] = a
            slot["ev"].set()
    _note_signal(board, hz, shz, ema)
    ph = []
    for p in (phrases or [])[:12]:
        if isinstance(p, dict):
            t, s = str(p.get("t") or "").strip(), p.get("shz", shz)
        else:
            t, s = str(p or "").strip(), shz
        t = store._tclean(t)                   # v3.118: без «англ н н»
        if len(t) >= 6:
            ph.append((t[:240], s))
    if ph:
        # v3.186.2: судья связности, словарь и «смысл частоты» — ФОНОМ (одна
        # очередь). Раньше прямо в запросе: genmod на каждую мысль переписывал
        # 7,5 МБ под общей блокировкой, и связь плат стояла в очереди часами.
        import bgwork
        bgwork.lane("link-learn", maxlen=400).submit(None, _learn_phrases, board, list(ph))
    with _LOCK:
        b = _ST["boards"].setdefault(board, {"recent": []})
        b.update({"hz": int(hz or 0), "shz": shz, "ts": now,
                  "asks_ok": bool(asks_ok)})
        if isinstance(grav, dict):           # v3.123: гравитация мыслей платы
            b["grav"] = {"strain": grav.get("strain"), "g": grav.get("g"),
                         "pulled": str(grav.get("pulled") or "")[:40],
                         "masses": [[str(m[0])[:12], m[1]] for m in
                                    (grav.get("masses") or [])[:3]
                                    if isinstance(m, (list, tuple)) and len(m) == 2]}
        if isinstance(extras_err, dict) and extras_err:   # v3.135: поле платы сломалось
            b["extras_err"] = {str(k)[:20]: str(v)[:120] for k, v in extras_err.items()}
        else:
            b.pop("extras_err", None)
        b["has_breath"] = isinstance(breath, dict)
        if isinstance(will, dict):           # v3.134: воля мозга Роуз (зоны)
            try:
                import breath as _bw
                _bw.note_will(board, will)
            except Exception:
                pass
        if isinstance(lead, dict):           # v3.166: ведущая зона и мысль мозга
            try:
                import brainctl as _bc
                _bc.note_lead(board, lead)
            except Exception:
                pass
        if isinstance(breath, dict):         # v3.132: дыхание платы (период, фаза)
            try:
                import breath as _br
                _br.note_board(board, breath.get("tn_period") or breath.get("period"),
                               breath.get("phase"), breath.get("depth"),
                               breath.get("uses"))
            except Exception:
                pass
        if isinstance(cam, dict):            # v3.130: состояние камеры платы
            b["cam"] = {k: cam.get(k) for k in ("state", "err", "up_err", "kb",
                                                 "ok_age", "sent_age", "url", "on", "uart")}
        if isinstance(net, str) and len(net) == 12 and set(net) <= set("01"):
            b["net"] = net                   # v3.129: карта связи с 12 нейронами
            b["net_ts"] = now
        if isinstance(bridge, dict):         # v3.120: почему «0 Гц»
            b["bridge"] = {"alive": bool(bridge.get("alive")),
                           "age": int(bridge.get("age") or 0),
                           "seen": bool(bridge.get("seen"))}
        try:
            _health(board, b, up, rss, crash, ver, ip)
        except Exception:
            pass
        for t, s in ph:
            b["recent"].append((now, t, s))
        b["recent"] = b["recent"][-40:]
        _ST["n"]["links"] += 1
        _ST["n"]["phrases"] += len(ph)
        every = cfg("boardlink_every", 30.0)
        go = ph and board not in _BUSY and \
            now - float(b.get("last_react") or 0) >= every
        if go:
            _BUSY.add(board)
            b["last_react"] = now
    if go:                                 # отвечаем на самую свежую мысль
        t, s = ph[-1]
        threading.Thread(target=_react, args=(board, t, s), daemon=True,
                         name="boardlink").start()
    try:
        store.board_seen(board, ip=ip or "")
    except Exception:
        pass
    asks = []
    with _LOCK:                            # вопросы мозгу — в ответ на запрос
        for qid, sl in _ASK.items():
            if sl["board"] == board and not sl["sent"]:
                sl["sent"] = True
                asks.append({"id": qid, "text": sl["text"]})
    try:
        import coherence
        _vd = coherence.take(board)
    except Exception:
        _vd = []
    try:
        with _LOCK:
            _sync = _sync_for(board, time.time())
    except Exception:
        _sync = None
    try:                                   # v3.132: общее дыхание — плате
        import breath as _br2
        if _sync is not None:
            _sync["breath"] = _br2.for_board()
    except Exception:
        pass
    try:                                   # v3.166: что мозг разрешил/остановил
        import brainctl as _bc2
        _ctl = _bc2.for_board()
    except Exception:
        _ctl = None
    return {"ok": True, "msgs": store.d6_take(board), "z": _z(shz),
            "verdicts": _vd, "sync": _sync, "ctl": _ctl,
            "asks": asks, "every": cfg("boardlink_every", 30.0)}


def recent(max_age=900.0, k=12):
    """Свежие мысли плат (с частотой), новые последними -> [(board, t, shz)]."""
    now = time.time()
    out = []
    with _LOCK:
        for bid, b in _ST["boards"].items():
            for ts, t, s in b.get("recent") or []:
                if now - ts <= max_age:
                    out.append((ts, bid, t, s))
    out.sort()
    return [(bid, t, s) for _ts, bid, t, s in out[-k:]]


def relevant(text, k=2, max_age=1800.0):
    """Свежие мысли плат, созвучные вопросу -> [(board, t)]."""
    q = {w[:5] for w in re.findall(r"[а-яё]{4,}", str(text or "").lower())}
    if not q:
        return []
    sc = []
    for bid, t, _s in recent(max_age, 40):
        n = len(q & {w[:5] for w in re.findall(r"[а-яё]{4,}", t.lower())})
        if n:
            sc.append((n, random.random(), bid, t))
    sc.sort(reverse=True)
    return [(bid, t) for _n, _r, bid, t in sc[:k]]


def status():
    now = time.time()
    with _LOCK:
        bs = [{"id": bid, "hz": b.get("hz"), "shz": b.get("shz"),
               "age": int(now - float(b.get("ts") or now)),
               "recent": len(b.get("recent") or []),
               "up": b.get("up"), "rss": b.get("rss"),
               "rss_first": b.get("rss_first"), "ver": b.get("ver"),
               "ip": b.get("ip"), "restarts": int(b.get("restarts") or 0),
               "crash": b.get("crash") or "", "asks_ok": b.get("asks_ok"),
               "bridge": b.get("bridge"), "grav": b.get("grav"),
               "net": b.get("net") or "", "cam": b.get("cam"),
               "neurons_online": (b.get("net") or "").count("1")
               if b.get("net") else None}
              for bid, b in _ST["boards"].items()]
        sy = {"platform": int(round(_SY["hz"])), "k": cfg("sync_k", 30),
              "on": cfg("sync_on", True), "spread": list(_SY["spread"][-12:])}
        return {"boards": bs, "n": dict(_ST["n"]), "sync": sy,
                "log": list(reversed(_ST["log"][-10:])),
                "every": cfg("boardlink_every", 30.0)}
