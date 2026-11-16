# -*- coding: utf-8 -*-
"""v3.166 — СТРОГАЯ СВЯЗЬ С МОЗГОМ neuro-12.

Заявка владельца: «пусть мозг жёстче контролирует процессы, происходящие в
облаке платформы. Своя LLM должна читать мысли мозга neuro12 и писать,
отталкиваясь от этой мысли».

Было (v3.134): воля мозга (активность зон 0..1, плата присылает каждые 10 с)
лишь чуть подкручивала алгоритмы: сила 0.5…1.5, а нет мозга — всё как обычно.

Стало (строгий режим, «brain_strict», по умолчанию включён):
  1. ВОРОТА МОЗГА. Каждый фоновый ИИ-процесс облака привязан к зоне мозга
     (breath.ALGOS). Процесс работает, только если его зона мозга активна
     (воля ≥ порога «brain_strict_min», 0.10). Зона молчит — процесс стоит.
     Сила работающего — 2×воля (0…2) вместо 0.5…1.5.
  2. НЕТ МОЗГА — НЕТ ФОНА. Плат на связи нет (воля старше 90 с) — фоновые
     процессы (любопытство, учёба, чтение GitHub, впитывание мыслей…) ждут
     мозг («brain_strict_nobrain» = "pause"; "neutral" — работать как
     раньше). Ответы в чате, задачи, которые владелец запустил сам, и сам
     канал связи — не останавливаются никогда.
  3. ВЕДУЩАЯ МЫСЛЬ. Плата v2.90 присылает «lead»: ведущую зону, её силу и
     последнюю мысль мозга. Облако отдаёт ей в ответ, какие процессы мозг
     сейчас разрешил и какие остановил («ctl»).
  4. СВОЯ LLM ПИШЕТ ОТ МЫСЛИ МОЗГА. В ответ на вопрос своя LLM берёт мысль
     мозга (свежую, по теме вопроса; нет — ведущую) как затравку и пишет
     продолжение по фактам ответа и базы. Показывается отдельным разделом
     «Мысль мозга → своя LLM» (мысль — не факт; факты ответа — дословно).
Команд от мозга облако не исполняет: мозг разрешает или останавливает только
фоновые процессы и их силу — права, лимиты задач и ответы владельцу прежние.
"""
import re
import threading
import time

import store

_LOCK = threading.Lock()
_LEAD = {}                    # плата -> {"zone", "a", "thought", "ts", "t_ts"}
_GATE = {}                    # процесс -> {"ok": n, "stop": n, "why", "ts"}
LEAD_FRESH = 60.0
THOUGHT_FRESH = 900.0
_SKIP = ("облако", "учитель", "Мысли облака", "своя LLM")
# Фоновые процессы, которые проходят через ворота мозга (breath.inhale).
# Остальные алгоритмы мозг не останавливает, а задаёт им силу (breath.mod):
# ответы, задачи владельца, речь — работают всегда.
GATED = ("Любопытство", "Учёба коду (GitHub)", "Мысли плат",
         "Связь LLM с мозгом", "Судья связности", "Понимание вопроса",
         "Обучение на ответах", "Своя LLM: интернет")


def _gated():
    with _LOCK:
        extra = [n for n in _GATE if n not in GATED]
    return list(GATED) + extra


# ------------------------------------------------------------------ настройки
def strict():
    try:
        return bool(store.settings().get("brain_strict", True))
    except Exception:
        return False


def _min_will():
    try:
        return max(0.0, min(0.9, float(store.settings().get("brain_strict_min", 0.10))))
    except Exception:
        return 0.10


def _nobrain_pause():
    try:
        return str(store.settings().get("brain_strict_nobrain", "pause")) == "pause"
    except Exception:
        return True


# -------------------------------------------------------------- ворота мозга
def gate(name):
    """Разрешает ли мозг фоновый процесс name сейчас? -> (bool, причина)."""
    if not strict():
        return True, "строгий режим выключен"
    import breath
    zone = breath.ZONE_OF.get(name)
    if not zone:
        return True, "процесс не привязан к зоне"
    wz = breath.will_zones()
    if wz is None:
        # v3.172: плата на связи, но воли зон не присылает (старое приложение
        # платы, связь через мост/почту) — это не «мозга нет»
        if _boards_online():
            return True, ("плата на связи, но не присылает активность зон — "
                          "нейтрально (обновите приложение платы до v2.77+)")
        if _nobrain_pause():
            return False, "мозг не на связи — процесс ждёт мозг"
        return True, "мозг не на связи — нейтральный режим"
    w = wz.get(zone)
    if w is None:
        return True, "плата не прислала зону «%s»" % zone
    if w < _min_will():
        return False, "зона «%s» молчит (%.2f < %.2f)" % (zone, w, _min_will())
    return True, "зона «%s» активна (%.2f)" % (zone, w)


def _boards_online():
    try:
        return len(store.free_boards() or [])
    except Exception:
        return 0


def note_gate(name, ok, why):
    with _LOCK:
        g = _GATE.setdefault(name, {"ok": 0, "stop": 0, "why": "", "ts": 0.0})
        g["ok" if ok else "stop"] += 1
        g["why"], g["ts"] = why, time.time()


def allowed(name):
    """gate() + учёт для панели. Для фоновых процессов."""
    ok, why = gate(name)
    note_gate(name, ok, why)
    return ok


def strength(will):
    """Сила процесса по воле его зоны: строгий режим — 2×воля (0…2)."""
    w = max(0.0, min(1.0, float(will)))
    return 2.0 * w if strict() else 0.5 + w


# ----------------------------------------------------------- ведущая мысль
def note_lead(board, lead):
    """Плата v2.90 прислала ведущую зону и последнюю мысль мозга."""
    if not isinstance(lead, dict):
        return
    try:
        a = max(0.0, min(1.0, float(lead.get("a") or 0)))
    except (TypeError, ValueError):
        a = 0.0
    th = store._tclean(str(lead.get("thought") or "").strip())[:260]
    try:
        age = max(0.0, float(lead.get("t_age") or 0))
    except (TypeError, ValueError):
        age = 0.0
    now = time.time()
    with _LOCK:
        _LEAD[str(board)[:40]] = {"zone": str(lead.get("zone") or "")[:30],
                                  "a": a, "thought": th, "ts": now,
                                  "t_ts": now - age}


def lead():
    """Ведущая зона мозга по свежим платам (самая активная) | None."""
    now = time.time()
    with _LOCK:
        fresh = [dict(v, board=b) for b, v in _LEAD.items()
                 if now - v["ts"] < LEAD_FRESH]
    if fresh:
        return max(fresh, key=lambda v: v["a"])
    # v3.172: плата без «lead» (приложение до v2.90) — ведущая зона по воле
    try:
        import breath
        wz = breath.will_zones()
    except Exception:
        wz = None
    if wz:
        z, a = max(wz.items(), key=lambda kv: kv[1])
        return {"zone": z, "a": round(float(a), 3), "thought": "", "ts": now,
                "t_ts": 0.0, "board": "по воле зон", "derived": True}
    return None


def _words(t):
    return set(w[:5] for w in re.findall(r"[а-яёa-z]{4,}",
                                         str(t or "").lower()))


def current_thought(question=""):
    """Мысль мозга neuro-12, от которой писать: свежая (≤15 мин) мысль платы
    по теме вопроса; нет такой — ведущая мысль; нет — самая свежая мысль.
    -> {"board", "text", "age", "why"} | None."""
    now = time.time()
    qw = _words(question)
    cands = []
    try:
        for t in store.thoughts_list(80):
            if t.get("kind") == "llm" or t.get("board") in _SKIP:
                continue
            tx = str(t.get("text") or "").strip()
            ts = float(t.get("ts") or 0)
            if len(tx) < 15 or now - ts > THOUGHT_FRESH:
                continue
            cands.append((len(qw & _words(tx)), ts, str(t.get("board") or "плата"), tx))
    except Exception:
        pass
    ld = lead()
    if ld and ld.get("thought") and now - ld["t_ts"] < THOUGHT_FRESH:
        cands.append((len(qw & _words(ld["thought"])), ld["t_ts"] + 0.5,
                      ld["board"], ld["thought"]))
    if not cands:
        return None
    on = [c for c in cands if c[0] > 0]
    if on:
        c = max(on, key=lambda x: (x[0], x[1]))
        why = "мысль по теме вопроса"
    else:
        c = max(cands, key=lambda x: x[1])
        why = "последняя мысль мозга (по теме вопроса мыслей нет)"
    return {"board": c[2], "text": c[3][:260], "age": int(now - c[1]), "why": why}


# ------------------------------------------------- своя LLM от мысли мозга
def llm_from_thought(question, thought, answer_text=""):
    """Своя LLM пишет, ОТТАЛКИВАЯСЬ от мысли мозга: мысль — затравка (тема),
    факты — ответ + база знаний по словам мысли. -> {ok, text, grounded |
    reason}."""
    thought = " ".join(str(thought or "").split())[:260]
    if not thought:
        return {"ok": False, "reason": "нет мысли мозга"}
    facts = [str(answer_text or "")[:1200]] if answer_text else []
    try:
        import kb
        import textutils
        keys = textutils.keywords(thought + " " + str(question or ""), 4) or []
        for q in ([" ".join(keys[:2])] if len(keys) > 1 else []) + keys[:3]:
            for r in (kb.search(q, limit=2) or {}).get("results") or []:
                sn = " ".join(str(r.get("snippet") or "").split())
                if len(sn) > 40 and sn not in facts:
                    facts.append(sn)
            if len(facts) >= 5:
                break
    except Exception:
        pass
    base = " ".join(facts)[:1800]
    if len(base) < 60:
        return {"ok": False, "reason": "нет фактов, на которые LLM опёрлась бы"}
    try:
        import tinyllm
        import llmlink
        seed = (thought + " — " + str(question or "")).strip(" —")[:200]
        best, bg = "", 0.0
        for _i in range(2):
            t, info = tinyllm.compose(seed, base)
            if info.get("llm") == "cold":
                return {"ok": False, "reason": "своя LLM ещё не обучена"}
            if info.get("llm") == "nonumpy":
                return {"ok": False, "reason": "нет numpy — своя LLM умеет "
                        "только мини-режим (Настройки LLM → «Установить numpy»)"}
            g = float(info.get("grounded") or 0)
            if t and g > bg:
                best, bg = t, g
        if not best:
            return {"ok": False, "reason": "LLM промолчала"}
        if bg < float(llmlink.cfg()["gen_minq"]):
            return {"ok": False, "text": best, "grounded": bg,
                    "reason": "продолжение не опирается на факты (%d%%)" % int(bg * 100)}
        if not llmlink._words_ok(best, base + " " + thought):
            return {"ok": False, "text": best, "reason": "выдуманные слова"}
        try:
            import strict as _s
            if _s.on() and not _s.text_ok(best, question or thought):
                return {"ok": False, "text": best, "reason": "несвязно — не показываю"}
        except Exception:
            pass
        if llmlink.degenerate(best):
            return {"ok": False, "text": best, "reason": "вырожденный текст"}
        try:
            import punct
            best = punct.fix(best)       # v3.170: запятые
        except Exception:
            pass
        return {"ok": True, "text": best, "grounded": bg}
    except Exception as e:
        return {"ok": False, "reason": "LLM: %s" % str(e)[:80]}


def section(question, answer_text=""):
    """Раздел ответа «Мысль мозга → своя LLM» | "" (мыслей мозга нет)."""
    if not strict() or not store.settings().get("brain_llm_thought", True):
        return ""
    th = current_thought(question)
    if not th:
        return ""
    r = llm_from_thought(question, th["text"], answer_text)
    head = "💭 Мозг neuro-12 «%s» (%s, %s): «%s»" % (
        th["board"][:20], th["why"], _ago(th["age"]), th["text"])
    if r.get("ok"):
        body = "🤖 Своя LLM от этой мысли: «%s»\n_(мысль — не факт; LLM писала " \
               "от мысли по фактам ответа и базы, опора %d%%)_" % (
                   r["text"], int(r.get("grounded", 0) * 100))
    else:
        body = "_(своя LLM от мысли не написала: %s)_" % r.get("reason", "?")
    return "**Мысль мозга → своя LLM**\n" + head + "\n" + body


def _ago(s):
    s = int(s or 0)
    return "%d с назад" % s if s < 90 else "%d мин назад" % (s // 60)


# -------------------------------------------------------------- для платы
def for_board():
    """Отдать плате: строгий режим, что мозг разрешил и что остановил."""
    import breath
    run, stop = [], []
    for name in _gated():
        ok, _why = gate(name)
        (run if ok else stop).append(name)
    return {"strict": strict(), "run": run[:30], "stop": stop[:30],
            "min": _min_will(), "ts": time.time()}


def status():
    import breath
    ld = lead()
    rows = []
    for name in _gated():
        ok, why = gate(name)
        g = _GATE.get(name) or {}
        rows.append({"name": name, "zone": breath.ZONE_OF.get(name, ""),
                     "ok": ok, "why": why,
                     "passed": g.get("ok", 0), "stopped": g.get("stop", 0)})
    power = [{"name": n, "zone": z, "strength": round(breath.strength(n), 2)}
             for n, _h, ob, z in breath.ALGOS if not ob and n not in GATED]
    th = current_thought("")
    try:
        wz = breath.will_zones()
        with breath._LOCK:
            will_boards = sorted(b for b, v in breath._WILL.items()
                                 if time.time() - v["ts"] < 90)
    except Exception:
        wz, will_boards = None, []
    return {"strict": strict(), "min": _min_will(),
            "boards_online": _boards_online(), "will_boards": will_boards,
            "will": wz,
            "nobrain": "pause" if _nobrain_pause() else "neutral",
            "lead": ld, "thought": th, "gates": rows, "power": power,
            "running": sum(1 for r in rows if r["ok"]),
            "stopped": sum(1 for r in rows if not r["ok"])}
