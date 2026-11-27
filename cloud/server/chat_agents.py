# -*- coding: utf-8 -*-
"""v3.231 — АГЕНТЫ «КОНТУР» И «АДМИНИСТРИРОВАНИЕ» РАБОТАЮТ ИЗ ЧАТА.

Раньше в режимах «Код» и «Администрирование» сообщения уходили в панели агентов
над чатом и в историю чата не попадали. Теперь всё идёт через чат:
  * ZIP/RAR, присланный в чат, становится ПРОЕКТОМ ЭТОГО ЧАТА (bind_project);
  * в режиме «Код» с проектом каждое сообщение — задача агенту «Контур»
    (настройки — из «Настроек агента» внизу чата); ход виден в чате, итог
    со ссылкой на архив приходит сообщением; следующая задача продолжает
    результат предыдущей;
  * в режиме «Администрирование» сообщение — задача агенту администрирования;
    запрос разрешения приходит сообщением, ответ «да»/«нет» в чате;
  * всё записывается в историю чата (chats/<id>.json).
Наблюдатель (поток) переносит события задания в чат."""
import re
import threading
import time

import store

_WATCH = {}
_LOCK = threading.Lock()
_YES = re.compile(r"^\s*(да|разрешаю|разреши|ок|okay|ok|выполняй|подтверждаю|yes)\s*[.!]*\s*$", re.I)
_NO = re.compile(r"^\s*(нет|отклони|отклоняю|запрещаю|отмена|no)\s*[.!]*\s*$", re.I)
_STOP = re.compile(r"^\s*(стоп|останови|прекрати|stop)\s*[.!]*\s*$", re.I)
_HELPED = re.compile(r"^\s*[✅👍]?\s*(помогло|сработало|получилось)\s*[.!]*\s*$", re.I)
_NOT_HELPED = re.compile(r"^\s*[❌👎]?\s*(не\s+помогло|не\s+сработало|не\s+получилось)\s*[.!]*\s*$", re.I)
_QUESTION = re.compile(r"^\s*(как|что такое|что значит|почему|зачем|объясни|расскажи)\b", re.I)


def _append(cid, text, **extra):
    with store.LOCK:
        chat = store.chat_load(cid)
        if not chat:
            return None
        m = {"role": "bot", "text": text, "ts": time.time(), "zone": extra.pop("zone", "Код")}
        m.update(extra)
        chat["messages"].append(m)
        chat["ts"] = time.time()
        for k, v in list(extra.items()):
            if k.startswith("_chat_"):
                chat[k[6:]] = v
                m.pop(k, None)
        store.chat_save(cid, chat)
        return m


def _set_chat(cid, **kw):
    with store.LOCK:
        chat = store.chat_load(cid)
        if not chat:
            return
        for k, v in kw.items():
            if v is None:
                chat.pop(k, None)
            else:
                chat[k] = v
        store.chat_save(cid, chat)


# ------------------------------- проект чата --------------------------------
def bind_project(cid, project):
    """ZIP из чата -> проект этого чата (прежний проект и его цепочка задач — заменяются)."""
    _set_chat(cid, project={"id": project["id"], "name": project.get("name"), "files": project.get("files"),
                            "bytes": project.get("bytes"), "last_job": "", "ts": time.time()})
    return _append(cid, "📦 Проект «%s» взят в работу этого чата: %s файлов, %d КБ.\nОпишите задачу сообщением — агент «Контур» "
                        "выполнит её с настройками агента (внизу чата). Каждая следующая задача продолжает результат предыдущей. "
                        "«убери проект» — отвязать; «?вопрос» — спросить, не трогая проект."
                   % (project.get("name"), project.get("files"), int(project.get("bytes") or 0) // 1024), zone="Код")


def code_reply(chat, text, cid, ip, opts):
    """Режим «Код» в чате с проектом. -> (ответ, зона, info) | None (не для агента проекта)."""
    p = chat.get("project")
    if not p:
        return None
    t = text.strip()
    if re.match(r"^\s*(убери|отвяжи|закрой)\s+проект\s*[.!]*\s*$", t, re.I):
        _set_chat(cid, project=None)
        return "Проект «%s» отвязан от чата. Пришлите новый ZIP, чтобы начать другой." % p.get("name"), "Код", {}
    if t.startswith("?"):
        return None                                   # вопрос мимо проекта — обычный кодинг-агент
    try:
        import applab_brain                           # «проверь/удали/включи приложение …», «какие кирпичики»
        ab = applab_brain.handle(t)
        if ab and ab[1].get("applab") != "write":
            return ab[0], "Код", ab[1]
    except Exception:
        pass
    try:                                          # «напиши/можешь написать приложение в апп лаб …» — сразу в App Lab, при любой модели
        import project_brain
        if project_brain._APPLAB_TASK.search(t):
            class _Pa:
                @staticmethod
                def _event(*a, **k):
                    pass
            summary, _done = project_brain._run_applab({"task": t, "internet": opts.get("internet", True) is not False}, t, _Pa(), _Pa._event)
            return summary.split("\nКод проекта не запускался")[0], "Код", {"applab": "write"}
    except Exception as e:
        store.log("КОД", "App Lab из чата: %s" % e)
    import project_agent
    base = p.get("last_job") or ""
    if base:
        try:
            st = project_agent.status(base, ip)
            if not st.get("done") or st.get("state") == "interrupted":
                base = ""
        except Exception:
            base = ""
    try:
        jid = project_agent.start(p["id"], t, ip, internet=opts.get("internet", True) is not False,
                                  model=str(opts.get("model") or "auto"), base_job=base,
                                  effort=opts.get("effort"), use_will=opts.get("will", True) is not False)
    except Exception as e:
        return "Агент «Контур» не взял задачу: %s" % e, "Код", {}
    _watch_project(cid, jid, ip)
    return ("▶ Контур взял задачу по проекту «%s»%s (модель: %s, интернет: %s). Ход и итог придут сюда сообщениями."
            % (p.get("name"), " — продолжает прошлый результат" if base else "", opts.get("model") or "auto",
               "да" if opts.get("internet", True) is not False else "нет")), "Код", {"agent_job": jid}


def _watch_project(cid, jid, ip):
    def loop():
        import project_agent
        shown, t0 = 0, time.time()
        while time.time() - t0 < 4 * 3600:
            time.sleep(2)
            try:
                job = project_agent.status(jid, ip)
            except Exception as e:
                _append(cid, "⚠ Задание Контура недоступно: %s" % e, agent_final=True)
                return
            ev = job.get("events") or []
            new = [e for e in ev[shown:] if e.get("kind") in ("plan", "web", "warning")][-3:]
            shown = len(ev)
            if new and not job.get("done"):
                _append(cid, "⏳ " + "\n".join("%s%s" % (e.get("status"), (": " + str(e.get("detail") or "")[:300].replace("\n", " ")) if e.get("detail") else "")
                                                 for e in new), agent_progress=True)
            if job.get("done"):
                checks = job.get("checks") or []
                txt = "%s\n\nИзменены: %s\nПроверка синтаксиса: успешно %d, ошибок %d, пропущено %d." % (
                    job.get("summary") or job.get("status"), ", ".join(job.get("changed") or []) or "нет изменений",
                    sum(1 for c in checks if c.get("ok") is True), sum(1 for c in checks if c.get("ok") is False),
                    sum(1 for c in checks if c.get("ok") is None))
                if job.get("download"):
                    txt += "\n[📥 Скачать проект, изменения и отчёт](/api/code/project/download?id=%s)" % jid
                if job.get("diff"):
                    txt += "\n\n```diff\n%s\n```" % str(job["diff"])[:6000]
                with store.LOCK:
                    chat = store.chat_load(cid)
                    if chat and chat.get("project"):
                        chat["project"]["last_job"] = jid if job.get("state") != "interrupted" else chat["project"].get("last_job", "")
                        store.chat_save(cid, chat)
                _append(cid, "✅ Контур закончил.\n" + txt if job.get("state") != "interrupted" else "⚠ " + txt, agent_final=True)
                return
        _append(cid, "⚠ Задание Контура идёт дольше 4 часов — наблюдение остановлено.", agent_final=True)
    threading.Thread(target=loop, daemon=True, name="chat-project-" + jid[:8]).start()


# ------------------------------- администрирование --------------------------
def admin_reply(chat, text, cid, ip, opts):
    """Режим «Администрирование» в чате. -> (ответ, зона, info)."""
    import admin_agent
    t = text.strip()
    jid = chat.get("admin_job")
    job = admin_agent.get_job(jid) if jid else None
    if job and job.get("pending") and (_YES.match(t) or _NO.match(t)):
        yes = bool(_YES.match(t))
        admin_agent.approve(jid, yes)
        return ("✅ Разрешено — продолжаю." if yes else "❌ Отклонено — агент обойдётся без этого шага."), "Система", {"admin_job": jid}
    if job and job.get("status") in ("работает", "ждёт разрешения") and _STOP.match(t):
        admin_agent.stop(jid)
        return "⏹ Останавливаю задание.", "Система", {"admin_job": jid}
    if jid and (_HELPED.match(t) or _NOT_HELPED.match(t)) and not (job and job.get("status") in ("работает", "ждёт разрешения")):
        import admin_selfwrite                       # v3.237: оценка владельца решает, переписывать ли инструкцию
        fb = admin_selfwrite.feedback(jid, bool(_HELPED.match(t)))
        if fb.get("job_id"):
            _set_chat(cid, admin_job=fb["job_id"])
            _watch_admin(cid, fb["job_id"])
            return fb["text"], "Система", {"admin_job": fb["job_id"]}
        return fb.get("text") or "Принято.", "Система", {"admin_job": jid}
    m = re.match(r"^\s*статус\s*(\w{6,20})?\s*$", t, re.I)
    if m:
        j = admin_agent.get_job(m.group(1) or jid or "")
        if not j:
            return "Заданий в этом чате нет.", "Система", {}
        return "Задание %s: %s, шагов %d. %s" % (j["id"], j.get("status"), len(j.get("steps") or []),
                                                 str(j.get("result") or "")[:800]), "Система", {"admin_job": j["id"]}
    if _QUESTION.match(t) and not re.search(r"\b(проверь|сделай|выполни|установи|перезапусти|почисти)\b", t, re.I):
        try:
            import kb
            hits = [h for h in (kb.search(t, limit=5) or {}).get("results") or []
                    if str(h.get("title", "")).startswith(("Администрирование", "Инструкция"))]
            if hits:
                return "По инструкции «%s»:\n%s\n\nЧтобы агент выполнил — напишите задачу без вопроса, например «проверь место на диске»." % (
                    hits[0]["title"], str(hits[0].get("snippet") or "")[:2500]), "Система", {"kb": hits[0]["title"]}
        except Exception:
            pass
        import brain                                   # вопрос, а не задача: отвечает мозг, агент не запускается
        r, z, i = brain.think(t, False, route="cloud", chat_id=cid)
        return (str(r or "") + "\n\n_Чтобы агент выполнил — напишите задачу без вопроса, например «проверь место на диске»._"), z, i
    if job and job.get("status") in ("работает", "ждёт разрешения"):
        return ("Агент ещё выполняет прошлое задание (%s). Ответьте «да»/«нет» на запрос или «стоп»." % job.get("status")), "Система", {"admin_job": jid}
    mode = opts.get("mode") if opts.get("mode") in ("both", "brain", "llm") else "both"
    t = re.sub(r"^\s*(?:выполни|задание|задача)\s*[:\-—]\s*", "", t, flags=re.I) or t
    r = admin_agent.start_job(t, owner=ip or "чат", mode=mode, auto_ok=bool(opts.get("auto_ok")),
                              target=str(opts.get("target") or ""))
    if not r.get("ok"):
        return "Не запустила: %s" % r.get("error"), "Система", {}
    _set_chat(cid, admin_job=r["job_id"])
    _watch_admin(cid, r["job_id"])
    return ("▶ Агент администрирования взял задачу (режим %s%s). Всё, что меняет систему, спрошу здесь — отвечайте «да» или «нет»; "
            "«стоп» — остановить." % (mode, ", цель " + opts["target"] if opts.get("target") else "")), "Система", {"admin_job": r["job_id"]}


def _watch_admin(cid, jid):
    def loop():
        import admin_agent
        asked, shown, t0 = None, 0, time.time()
        while time.time() - t0 < 4 * 3600:
            time.sleep(1.5)
            j = admin_agent.get_job(jid)
            if not j:
                _append(cid, "⚠ Задание администрирования пропало (облако перезапускалось).", zone="Система", agent_final=True)
                return
            steps = j.get("steps") or []
            if len(steps) > shown:
                lines = []
                for s in steps[shown:][-4:]:
                    a, res = s.get("act") or {}, s.get("result") or {}
                    lines.append("%s %s%s" % ("✓" if res.get("ok") else "✗", a.get("command") or a.get("tool") or "",
                                              ("\n" + str(res.get("out") or res.get("error") or "")[-500:]) if (res.get("out") or res.get("error")) else ""))
                shown = len(steps)
                _append(cid, "⏳ " + "\n".join(lines), zone="Система", agent_progress=True)
            pend = j.get("pending")
            if pend and asked != id(pend) and asked != str(pend.get("act")):
                asked = str(pend.get("act"))
                a = pend.get("act") or {}
                _append(cid, "%s Нужно разрешение: %s\n```\n%s\n```\nОтветьте «да» или «нет»." % (
                    "⚠" if pend.get("kind") == "danger" else "❓", pend.get("why"), a.get("command") or a.get("path") or a.get("tool")),
                    zone="Система", agent_ask=True, buttons=["да", "нет"])
            if j.get("status") not in ("работает", "ждёт разрешения"):
                txt = "✅ Задание завершено: %s" % (j.get("result") or j.get("status"))
                if j.get("own_note"):
                    txt += "\nСвоя LLM: %s" % j["own_note"]
                _append(cid, txt + "\n\nПомогло? Нажми кнопку — от этого зависит, перепишу ли я инструкцию по интернету и "
                                   "запущу ли задание снова.", zone="Система", agent_final=True,
                        buttons=["✅ Помогло", "❌ Не помогло"], feedback_job=jid)
                return
    threading.Thread(target=loop, daemon=True, name="chat-admin-" + jid[:8]).start()
