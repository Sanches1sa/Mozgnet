# -*- coding: utf-8 -*-
"""brainplan.py — v3.218: ПЛАНИРОВЩИК АГЕНТА «АДМИНИСТРИРОВАНИЕ» — ПАМЯТЬ МОЗГА.

Агент больше не выбирает команды по ключевым словам, зашитым в код. Когда
внешняя модель не настроена, шаги выбирает мозг облака по своей памяти:
  1) запрос = задача владельца (+ строки из вывода прошлого шага: ошибки,
     предупреждения, проценты) -> поиск по базе знаний облака (BM25, основы слов,
     покрытие вопроса — тот же поиск, которым мозг отвечает в чате);
  2) берутся уроки «Администрирование: …» — выученные команды Linux и
     Windows (PowerShell), инструменты агента и ОПЫТ прошлых заданий
     (запоминается, что сработало: admin_learn -> KB);
  3) из лучшего урока берутся команды/шаги по очереди, уже выполненные
     пропускаются; ничего не нашлось — агент честно говорит, что не знает,
     а не подставляет «общий обзор».
Честно о границах: это поиск по выученному, а не свободное рассуждение. Свободно
планирует внешняя модель (⚙ → «API и ответы»); своя LLM пишет заметку и код.
"""
import json
import re

import store

MIN_COVER = 0.3              # v3.237: ниже — урок «не про то», ищем в интернете
MAX_AUTO = 24                # шагов из памяти за одно задание (длинные инструкции: перезагрузка, обновление)
_LESSONS = {}                # id задания -> {заголовок урока: шаги} (чтобы довести урок до конца)


def _symptoms(res):
    """Что бросилось в выводе, обычными словами — запрос для поиска по памяти
    (не решение: что делать дальше, подскажет урок)."""
    out = str((res or {}).get("out") or (res or {}).get("error") or "")
    sy = []
    pct = [int(m) for m in re.findall(r"(\d{2,3})%", out)]
    if any(85 <= p <= 100 for p in pct):
        sy.append("диск заполнен место закончилось")
    if re.search(r"\bfailed\b", out) and "0 loaded units" not in out:
        sy.append("упавшая служба ошибка журнал службы")
    ml = re.search(r"load average:\s*(\d+(?:[.,]\d+)?)", out)
    if ml and float(ml.group(1).replace(",", ".")) >= 2.0:
        sy.append("высокая нагрузка процессы тормозит")
    if re.search(r"Exited|exited", out):
        sy.append("контейнер остановился docker")
    if re.search(r"permission denied|отказано в доступе", out, re.I):
        sy.append("нет прав доступа")
    if re.search(r"out of memory|oom", out, re.I):
        sy.append("не хватает памяти")
    return " ".join(sy)


def actions_from_text(text, win):
    """Шаги из текста урока: «Шаг: {json}» и команды из ```-блоков нужной ОС."""
    acts = []
    for m in re.finditer(r"^Шаг:\s*(\{.*\})\s*$", str(text), re.M):
        try:
            a = json.loads(m.group(1))
            if isinstance(a, dict) and a.get("tool"):
                acts.append(a)
        except Exception:
            pass
    for m in re.finditer(r"```(\w*)\n(.*?)```", str(text), re.S):
        lang = m.group(1).lower()
        if (lang == "powershell") != bool(win) and lang in ("powershell", "bash", "sh"):
            continue
        for ln in m.group(2).splitlines():
            ln = ln.strip()
            if ln and not ln.startswith("#") and "<" not in ln:
                acts.append({"tool": "run", "command": ln})
    return acts


def parse_meta(text):
    """Цель/риск/слоты инструкции из текста урока."""
    meta = {"target": "any", "risk": "read", "slots": {}}
    m = re.search(r"^Цель:\s*(\w+)\.?\s*(?:Риск:\s*(\w+))?", str(text), re.M)
    if m:
        meta["target"] = m.group(1)
        meta["risk"] = m.group(2) or "read"
    # v3.237: инструкция, записанная из интернета, и оценка владельца
    meta["web"] = bool(re.search(r"^Источник:\s*интернет", str(text), re.M))
    meta["verified"] = bool(re.search(r"^Проверено владельцем:\s*да", str(text), re.M))
    meta["bad"] = bool(re.search(r"^Проверено владельцем:\s*НЕ помогло", str(text), re.M))
    m = re.search(r"^Слоты:\s*(\{.*\})\s*$", str(text), re.M)
    if m:
        try:
            meta["slots"] = json.loads(m.group(1))
        except Exception:
            pass
    return meta


def _target_ok(meta, win):
    t = meta.get("target", "any")
    if t == "windows":
        return bool(win)
    if t in ("linux", "board"):
        return not win
    return True


_NAMED = {"board": r"плат|uno\s*q|arduino|neuro.?12|мост|скетч|сторож|neuro12",
          "cloud": r"облак|себя|мозг|llm|ллм|мысл|бот|макс|сертификат|https|знани|обучи|откат|копи"}


def _named_ok(meta, query):
    """Инструкции для платы/облака берём, только если задача их называет:
    «состояние web1» не должно превращаться в «состояние облака»."""
    rx = _NAMED.get(meta.get("target", "any"))
    return True if not rx else bool(re.search(rx, str(query or ""), re.I))


def _recall_ex(query, win, limit=6):
    """[(заголовок, оценка, шаги, мета)] — уроки администрирования по запросу."""
    import kb
    out = []
    try:
        # v3.231: берём шире и фильтруем по заголовку — статьи App Lab про журналы/ssh
        # (уроки ИБ) не должны вытеснять уроки администрирования из первых мест выдачи
        res = (kb.search(query, limit=limit * 5) or {}).get("results") or []
    except Exception:
        res = []
    res = [r for r in res if str(r.get("title") or "").startswith("Администрирование")][:limit]
    for r in res:
        title = str(r.get("title") or "")
        art = kb.article(r.get("id")) if r.get("id") else None
        text = (art or {}).get("text") or r.get("snippet") or ""
        meta = parse_meta(text)
        meta["aid"] = r.get("id") or ""
        if meta.get("bad"):                        # v3.237: владелец сказал «не помогло»
            continue
        if not _target_ok(meta, win):
            continue
        if not _named_ok(meta, query):
            continue
        acts = actions_from_text(text, win)
        if acts:
            out.append((title, float(r.get("score") or 0), acts, meta))
    return out


def _from_aid(aid, win):
    import kb
    art = kb.article(aid)
    if not art:
        return None
    text = str(art.get("text") or "")
    meta = parse_meta(text)
    meta["aid"] = aid
    if meta.get("bad") or not _target_ok(meta, win):
        return None
    acts = actions_from_text(text, win)
    return (str(art.get("title") or ""), 99.0, acts, meta) if acts else None


_STOP = {"на", "в", "во", "с", "со", "и", "по", "для", "мне", "все", "всё", "это", "сервер", "сервере", "сервера"}


def coverage(task, title, text):
    """v3.237: какая доля смысловых слов задачи есть в уроке (0..1). Имена целей
    (web1, srv2) и служебные слова не считаются. Низкое покрытие = мозг на самом
    деле не знает, как это сделать (BM25 всегда что-нибудь находит)."""
    import kb
    words = [w for w in re.findall(r"[а-яёa-z0-9_.-]+", str(task).lower())
             if len(w) > 2 and w not in _STOP and not (re.search(r"\d", w) and re.search(r"[a-z]", w))]
    qs = set(kb._stems(" ".join(words)))
    if not qs:
        return 1.0
    ts = set(kb._stems(str(title) + " " + str(text)[:6000]))
    hit = sum(1 for q in qs if q in ts or any(t.startswith(q[:5]) for t in ts if len(q) >= 5))
    return hit / float(len(qs))


def recall(query, win, limit=6):
    """[(заголовок, оценка, [шаги])] — уроки администрирования по запросу."""
    return [(t, s_, a) for t, s_, a, _m in _recall_ex(query, win, limit)]


def fill_slots(act, meta, task):
    """Подставить {слоты} в команду из текста задачи (регулярки слотов лежат в
    самой инструкции). -> (шаг | None, [не найденные слоты])."""
    cmd = str(act.get("command") or "")
    names = re.findall(r"\{(\w+)\}", cmd)
    if not names:
        return act, []
    miss = []
    for n in names:
        rx = (meta.get("slots") or {}).get(n)
        val = ""
        if rx:
            try:
                m = re.search(rx, task, re.I)
                val = m.group(1) if m and m.groups() else ""
            except re.error:
                val = ""
        if not val or not re.fullmatch(r"[\w.@:/\-]{1,80}", val):
            miss.append(n)
        else:
            cmd = cmd.replace("{%s}" % n, val)
    if miss:
        return None, miss
    return dict(act, command=cmd), []


def next_act(job, history, done, tg, win):
    """Следующее действие из памяти мозга | None (нечего предложить)."""
    task = str(job.get("task") or "")
    if not tg:                                         # цель не ясна: показать список и остановиться
        return None if history else {"tool": "list", "why": "цель не указана"}
    if not history:
        m = re.search(r"`([^`]+)`", task)             # команда, которую дал сам владелец
        if m and tg:
            return {"tool": "run", "server": tg, "command": m.group(1).strip(), "why": "команда владельца"}
    auto = sum(1 for a, _r in history if a.get("why"))
    if auto >= MAX_AUTO:
        return None
    def pick(found, skip=()):
        for title, score, acts, meta in found:
            if title in skip:
                continue
            for a in acts:
                key = str(a.get("command") or a.get("tool"))
                if key in done:
                    continue
                a = dict(a)
                a, miss = fill_slots(a, meta, task)
                if a is None:                       # нужен параметр, которого нет в задаче
                    job["plan_note"] = ("Для инструкции «%s» не хватает: %s. Назови в задаче, например: "
                                        "«перезапусти службу nginx»." % (title.replace("Администрирование: ", "")[:60],
                                                                         ", ".join(miss)))
                    return None
                if a["tool"] in ("run", "check", "wake", "code", "write") and tg and not a.get("server"):
                    a["server"] = tg
                a.setdefault("risk", meta.get("risk", "read"))
                if meta.get("web") and not meta.get("verified"):
                    a["web"] = True                 # каждую такую команду спросить у владельца
                if meta.get("web"):
                    a["web_aid"] = meta.get("aid") or ""
                a["why"] = "память: «%s» (%.1f)" % (title.replace("Администрирование: ", "")[:50], score)
                a["_lesson"] = title
                return a
        return None
    cache = _LESSONS.setdefault(str(job.get("id") or ""), {})
    if len(_LESSONS) > 80:
        _LESSONS.pop(next(iter(_LESSONS)), None)
    found = _recall_ex(task, win)
    pa = job.get("prefer_aid")                    # v3.237: свежая инструкция из интернета — первой
    if pa:
        pref = _from_aid(pa, win)
        if pref:
            found = [pref] + [f for f in found if f[0] != pref[0]]
    if found and not history and not pa and not job.get("_no_weak") and store.settings().get("admin_selfwrite", True):
        try:                              # v3.237: урок почти не про задачу — значит, не знаем
            import kb
            art = kb.article((found[0][3] or {}).get("aid") or "") or {}
            cv = coverage(task, found[0][0], art.get("text") or "")
        except Exception:
            cv = 1.0
        job["coverage"] = round(cv, 2)
        if cv < MIN_COVER and not re.search(r"`[^`]+`", task):
            job["weak_lesson"] = found[0][0]
            return None
    cur = found[:1]                       # 1) сначала довести до конца лучший урок по задаче
    if cur:
        cache.setdefault(cur[0][0], cur[0][2:])
    act = pick(cur)
    if act is None and cache and not job.get("plan_note"):    # 1б) и уроки, начатые по симптомам
        act = pick([(t, 0.0, v[0], v[1]) for t, v in reversed(list(cache.items()))])
    if act is None and history and not job.get("plan_note"):  # 2) по тому, что бросилось в выводе
        used = " ".join(str(a.get("why") or "") for a, _r in history)
        for _a, res in reversed(history):
            sym = _symptoms(res)
            if not sym:
                continue
            # v3.231: по симптомам — только читающие уроки (сам агент не берёт «перезагрузку» из-за похожих слов)
            found2 = [f for f in _recall_ex(sym, win, limit=10)
                      if ("«%s»" % f[0].replace("Администрирование: ", "")[:50]) not in used
                      and str((f[3] or {}).get("risk") or "read") == "read"]
            act = pick(found2[:1])
            if act:
                cache[act["_lesson"]] = next(f[2:] for f in found2 if f[0] == act["_lesson"])
                break
    if act:
        act.pop("_lesson", None)
    return act


def learn(job):
    """Успешное задание -> урок в базу знаний (мозг запоминает, что сработало)."""
    try:
        import kb
        steps = [s for s in (job.get("steps") or [])
                 if s.get("tool") in ("run", "restart_cloud", "shot", "vision") and (s.get("result") or {}).get("ok")]
        if not steps or job.get("status") != "done":
            return False
        lines = ["Задача владельца: %s." % str(job.get("task") or "")[:300]]
        for s in steps[:6]:
            a = {k: v for k, v in (s.get("act") or {}).items() if k in ("tool", "server", "command", "act")}
            lines.append("Шаг: " + json.dumps(a, ensure_ascii=False))
            lines.append("Результат: " + str((s.get("result") or {}).get("out") or "")[:200].replace("\n", " ⏎ "))
        kb.add_article("Администрирование: опыт — " + str(job.get("task") or "")[:60], "\n".join(lines),
                       source="admin_experience", did="admx_" + str(job.get("id") or ""))
        return True
    except Exception:
        return False
