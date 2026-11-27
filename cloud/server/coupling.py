# -*- coding: utf-8 -*-
"""Сопряжение всех известных зон платы и зонных контекстов общей LLM.

Объединённые сигналы используются генерацией и распределением потоков;
настройки разрешений и аппаратная прошивка не изменяются.
"""
import time

import brainctl
import brainwill
import breath
import store


def _f(v, d=0.0):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return d
    return x if x == x else d


def _board_side():
    """Воля платы: зоны, ведущая мысль, ворота, источники."""
    st = brainctl.status()
    bw = brainwill.state()
    zones = [{"zone": z, "a": round(_f(a), 2)}
             for z, a in sorted((st.get("will") or {}).items(),
                                key=lambda kv: -_f(kv[1]))]
    gates = st.get("gates") or []
    return {
        "online": st.get("boards_online") or 0,
        "boards": st.get("will_boards") or [],
        "connected": bool(bw.get("connected")),
        "source": bw.get("source") or "",
        "zones": zones,
        "lead": st.get("lead") or None,
        "thought": st.get("thought") or None,
        "strict": bool(st.get("strict")),
        "strict_min": _f(st.get("min"), 0.10),
        "nobrain": st.get("nobrain") or "pause",
        "gates": [{"name": g.get("name"), "zone": g.get("zone"),
                   "ok": bool(g.get("ok")), "why": g.get("why")}
                  for g in gates],
        "gates_run": st.get("running") or 0,
        "gates_stop": st.get("stopped") or 0,
        "power": st.get("power") or [],
    }


def _llm_side():
    """Воля LLM: свои решения, банк уроков учителя, параметры от воли платы."""
    s = store.settings()
    p = brainwill.policy()
    bank = 0
    engine_name = ""
    lessons = 0
    try:
        import codeteach
        t = codeteach.status()
        bank = int(t.get("bank_size") or 0)
        engine_name = str(t.get("engine_name") or "")
        lessons = int(t.get("lessons") or 0)
    except Exception:
        pass
    reads = facts = None
    try:
        import llmweb
        lw = llmweb.status()
        reads = int(lw.get("reads") or 0)
    except Exception:
        pass
    try:
        import llmfact
        lf = llmfact.status()
        facts = int(lf.get("checks") or 0)
    except Exception:
        pass
    courses = 0
    try:
        import curriculum
        courses = len(curriculum.CURRICULUM)
    except Exception:
        pass
    try:
        import llm_zones
        fx = llm_zones.flexibility()
    except Exception:
        fx = {"flex": 0.0, "why": []}
    return {
        "flex": fx.get("flex"), "flex_why": fx.get("why") or [],
        "flex_base": fx.get("base"),
        # свои решения (воля LLM)
        "free": s.get("llm_free", True),
        "web_on": s.get("llm_web_on", True),
        "fact_check": s.get("llm_web_check", True),
        "fact_talk": s.get("llm_web_check_talk", True),
        "from_thought": s.get("brain_llm_thought", True),
        "teacher": engine_name,
        "teacher_bank": bank,
        "teacher_lessons": lessons,
        "reads": reads, "facts": facts, "courses": courses,
        # параметры, которые LLM получает от воли платы (brainwill)
        "connected": bool(p.get("connected")),
        "source": p.get("source") or "",
        "zones": p.get("zones") or {},
        "agent_strength": _f(p.get("agent_strength"), 1.0),
        "code_strength": _f(p.get("code_strength"), 1.0),
        "learning_strength": _f(p.get("learning_strength"), 1.0),
        "agent_effort": _f(p.get("agent_effort"), 1.0),
        "temperature_multiplier": _f(p.get("temperature_multiplier"), 1.0),
        "learning_rate_multiplier": _f(p.get("learning_rate_multiplier"), 1.0),
    }


# зоны платы -> что они задают своя LLM (страница «Сопряжение»)
LINKS = (
    ("Кирпичики", "сила кода своей LLM: попытки, длина, температура",
     "code_strength", "temperature_multiplier"),
    ("Обучение", "темп учёбы своей LLM (шаг курса)",
     "learning_strength", "learning_rate_multiplier"),
    ("Помощь", "усилие агента «Контур»: шаги задач",
     "agent_strength", "agent_effort"),
    ("Гибкость", "ширина зонного контекста, температура и выбор слов своей LLM (сопряжение + застой)",
     None, None),
    ("Буквы", "параметры генерации LLM",
     None, None),
)


def status():
    llm = _llm_side()
    import llm_zones
    merged = llm_zones.combined()
    return {"ok": True, "ts": time.time(), "unified": merged,
            "board": _board_side(), "llm": llm,
            "links": [{"zone": row['zone'], "what": "единая активность: плата + зонный контекст LLM",
                       "strength": .5+(row['unified'] if row['unified'] is not None else .5),
                       "result": row['unified']} for row in merged['zones']]}


if __name__ == "__main__":
    import json
    print(json.dumps(status(), ensure_ascii=False, indent=1))
