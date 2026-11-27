# -*- coding: utf-8 -*-
# ===========================================================================
# dialog.py — ОБЩИЙ ДВИЖОК ДИАЛОГА: бот МАКС и режим «💬 Разговор» (v3.30)
# ---------------------------------------------------------------------------
# Заявка владельца: «сделай в настройках бота галочки: ответ облака и ответ
# мозга (первое сообщение в переписке)… общий движок раздела Облако — его
# ответы мне очень нравятся; раздел Разговор сделай таким же, как шлёт
# сообщения бот; постоянно здоровается — сохранять контекст, мысли оставить».
#   * «Ответ облака» (max_cloud_reply) — весь мозг облака, тот же движок,
#     что у раздела «Облако» (brain.think, route=cloud): база знаний,
#     интернет с фокусом вопроса, калькулятор, память ответов, единый мозг;
#   * «Ответ мозга» (max_brain_reply) — ПЕРВОЕ сообщение: мысли мозга о
#     реплике (agent.cloud_thought — связные предложения из его настоящего
#     состояния: связи сети, нить разговора, мысли платы, чего не знает).
#     Без галочки облака мозг отвечает сам — без интернета, своими
#     знаниями (база, сеть, память ответов);
#   * приветствие — только в начале диалога (replytpl.is_new_dialog);
#     длинный ответ — следующим сообщением (replytpl.split).
# Порядок сообщений: [мысли мозга] -> [ответ (1–2 сообщения)].
# ===========================================================================
import re

import store


def flags(brain_on=None, cloud_on=None):
    s = store.settings()
    b = bool(s.get("max_brain_reply", False)) if brain_on is None \
        else bool(brain_on)
    c = bool(s.get("max_cloud_reply", True)) if cloud_on is None \
        else bool(cloud_on)
    if not b and not c:
        c = True                       # кто-то должен ответить
    return b, c


def respond(text, ctx=None, chat_key="", name="", sender="владелец",
            deliver=None, brain_on=None, cloud_on=None, long_mode=False,
            limit=3800, template=True):
    """Реплика -> (список сообщений, зона, info)."""
    import agent
    import brain
    import replytpl
    try:                               # v3.133: реплика человека -> общий словарь
        import worddict
        worddict.learn(text, hz=worddict.platform_hz())
    except Exception:
        pass
    b_on, c_on = flags(brain_on, cloud_on)
    import conversation
    reply, zone, info = conversation.answer(text, ctx=ctx, sender=sender, deliver=deliver,
                                            long_mode=long_mode, no_web=not c_on)
    info = dict(info or {})
    seq = []
    import answer_focus
    if b_on and not info.get('clarification') and not answer_focus.practical(answer_focus.context_question(text,ctx)):
        try:
            th = agent.cloud_thought(text, reply, zone, info, ctx)
        except Exception:
            th = []
        if th:              # v3.97: «💭 мысль» отдельным сообщением не шлём
            t = " ".join(th)
            info["thought"] = t
            try:
                store.thoughts_add("облако", t, kind="cloud")
                import unify
                unify.thought_to_boards(t)
            except Exception:
                pass
    seq.append(str(reply or "").strip() or
               "Пока не нашла ответ — спроси иначе или добавь мне статей.")
    msgs = []
    for i, t in enumerate(seq):
        if template:
            part = replytpl.build(t, name=name, chat_key=chat_key,
                                  limit=limit, greet=(i == 0))
        else:
            part = replytpl.split(t, limit=limit, parts=2)
        if i == 0 and len(seq) > 1:
            info["n_thought"] = len(part)     # сколько сообщений — мысли
        msgs.extend(part)
    # v3.70: приветствие — один раз на реплику: если оно уже в первом
    # сообщении (мысли мозга), в шаблонном ответе его не повторяем
    try:
        if len(msgs) > 1 and replytpl._ALREADY.match(msgs[0].strip()):
            for j in range(1, len(msgs)):
                msgs[j] = re.sub(r"^\*\*Приветствие\*\*\n[^\n]*\n+", "",
                                 msgs[j], count=1)
    except Exception:
        pass
    info["src_zone"] = zone
    try:
        trace(chat_key, text, zone, info, reply)
    except Exception:
        pass
    return msgs, zone, info


# ------------- v3.125: «/почему» — откуда взялся ответ бота -------------------
_TR = "answer_trace.json"


def trace(chat_key, q, zone, info, reply=""):
    """Запомнить путь ответа (зона, источник, мысли мозга, плата, фильтры)."""
    if not chat_key:
        return
    info = info or {}
    r = str(reply or "")
    src = re.findall(r"Источник:\s*«([^»]{3,160})»", r)[:3]
    foot = re.findall(r"_Составлено:[^\n]{0,300}", r)[:1]
    t = {"q": str(q)[:200], "zone": zone, "src": src,
         "foot": foot[0] if foot else "",
         "mem_brain": info.get("mem_brain") or {},
         "brain_asked": info.get("brain_asked") or {},
         "unify": info.get("unify") or {}, "strict": info.get("strict") or "",
         "focus_rejected": bool(info.get("focus_rejected")), "focus_cleaned": bool(info.get("focus_cleaned")),
         "web_check": info.get("web_check") or {},
         "web_retry": bool(info.get("web_retry")), "sources": info.get("sources") or [],
         "web_errors": info.get("web_errors") or [],
         "offline": bool(info.get("offline")), "nuclear": bool(info.get("nuclear")),
         "llm_sents": info.get("llm_sents"), "ts": __import__("time").time(),
         "understood": info.get("understood") or "", "ctx_did": info.get("ctx_did") or ""}
    with store.LOCK:
        d = store.load_json(_TR, {}) or {}
        d[str(chat_key)[:80]] = t
        if len(d) > 200:
            for k in sorted(d, key=lambda k: d[k].get("ts", 0))[:len(d) - 200]:
                d.pop(k, None)
        store.save_json(_TR, d)


def explain(chat_key):
    """Текст «почему так ответил» по последнему ответу этого чата."""
    t = (store.load_json(_TR, {}) or {}).get(str(chat_key)[:80])
    if not t:
        return "Пока нечего объяснять — в этом чате ещё не было ответа."
    ln = ["Последний вопрос: «%s»" % t["q"]]
    if t.get("understood"):                     # v3.131: как понят вопрос
        ln.append("Понял вопрос так: " + t["understood"])
    ln.append("Ответила зона: %s" % t["zone"])
    if t.get("ctx_did"):
        ln.append("По понятому контексту: " + t["ctx_did"])
    if t.get("src"):
        ln.append("Источник: " + "; ".join("«%s»" % s for s in t["src"]))
    if t.get("focus_rejected"):
        ln.append("Первый вариант отклонён: не отвечал на вопрос или не содержал нужной команды")
    if t.get("focus_cleaned"):
        ln.append("Убраны посторонние дополнения и технические подписи")
    if t.get("web_check"):
        wc=t["web_check"]
        ln.append("Проверка источников: подтверждено %s, без подтверждения %s" % (wc.get("ok",0),wc.get("no",0)))
    if t.get("web_retry"):
        ln.append("Выполнена попытка поиска в интернете")
    for source in t.get("sources", [])[:3]:
        if isinstance(source, dict) and source.get("url"):
            ln.append("Сетевой источник: " + str(source["url"]))
    if t.get("web_errors"):
        ln.append("Диагностика поиска: " + str(t["web_errors"])[:600])
    mb = t.get("mem_brain") or {}
    if mb:
        if mb.get("memory"):
            ln.append("Память: " + ", ".join(mb["memory"][:4]))
        if mb.get("board"):
            ln.append("Мысль мозга платы «%s» — в ответе" % mb["board"])
        elif mb.get("thought"):
            ln.append("Мысль мозга облака — в ответе")
        if mb.get("llm_own"):
            ln.append("Своя LLM добавила свою мысль")
    ba = t.get("brain_asked") or {}
    if ba.get("error"):
        ln.append("Плату спрашивали, она не ответила: %s" % ba["error"])
    un = t.get("unify") or {}
    if un:
        ln.append("Единый мозг: плату «%s» спросили%s" % (
            un.get("board") or "?",
            " — её ответ придёт отдельным сообщением" if un.get("pending")
            else (" — её ответ влит в этот" if not un.get("pending") else "")))
    if t.get("strict"):
        ln.append("«Не показывать бред»: %s — заменено на «не знаю»" % t["strict"])
    if t.get("offline"):
        ln.append("Облако было без интернета («Отвечать без сети» выключено)")
    if t.get("nuclear"):
        ln.append("Уравнение распада — расчёт по законам сохранения")
    if t.get("foot"):
        ln.append(t["foot"].strip("_"))
    ln.append("Если ответ — бред, пришли «неверно» или «правильный ответ: …» — "
              "запомню; несвязные мысли платы фильтр «Не показывать бред» "
              "отсекает (Настройки → Общие).")
    return "\n".join(ln)


_WHY = re.compile(r"^\s*(?:/why|/почему|почему ты так (?:ответил[аи]?|сказал[аи]?)\??"
                  r"|откуда (?:такой )?ответ\??)\s*$", re.I)


def is_why(text):
    return bool(_WHY.match(str(text or "")))
