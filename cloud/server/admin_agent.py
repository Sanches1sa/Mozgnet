# -*- coding: utf-8 -*-
"""admin_agent.py — АГЕНТ РЕЖИМА «АДМИНИСТРИРОВАНИЕ» (v3.189, только stdlib).

Отдельный режим чата «🗄 Администрирование»: агент, который умеет обращаться
к серверам и к рабочему столу ПК, где работает облако. Ядро мозга-12 и LLM
работают по переключателю:

  both  — «Ядро + LLM»: воля мозга-12 задаёт бюджет шагов и температуру,
          настроенная модель планирует действия, своя LLM добавляет заметку
          (если её веса готовы);
  brain — «Только ядро»: шаги планируют правила агента, бюджет задаёт воля
          мозга-12, LLM не используется;
  llm   — «Только LLM»: планирует настроенная модель, бюджет фиксированный,
          воля мозга-12 не применяется.

Инструменты — всё из окна «Серверы» плюс рабочий стол и зрение плат:
  list   — какие цели есть (SSH-серверы и платы);
  check  — доступна ли цель;      wake — включить сервер по сети (WoL);
  run    — команда на цели (Linux sh, на плате — контейнер приложения);
  write  — записать файл (код) на цель;
  shot   — снимок рабочего стола ПК (data/desktop/, отдаётся владельцу);
  desk   — действие на рабочем столе: клик, ввод текста, клавиши,
           открыть программу/сайт, окно, прокрутка (координаты — доли экрана);
  vision — состояние камер плат: свежие кадры, битые кадры и их причины;
  finish — итог.

Безопасность как в окне «Серверы» (ssh_agent.py):
  * читающие шаги выполняются сразу;
  * изменяющие (установка, перезапуск, запись, клики и ввод на ПК…) ждут
    кнопки «Разрешить», если владелец не включил «изменения без вопросов»;
  * опасные (rm -rf /, mkfs, dd, reboot, остановка sshd…) — всегда ждут
    разрешения, даже с галочкой;
  * не больше MAX_STEPS шагов; всё пишется в журнал.
"""
import json
import re
import threading
import time
import uuid

import store
import ssh_servers
import servers

MAX_STEPS = 14

SYSTEM = '''Ты агент режима «Администрирование» облака «Star Gate». Ты управляешь
серверами владельца, его платами Arduino UNO Q и рабочим столом компьютера,
где работает облако. Отвечай ТОЛЬКО одним JSON-объектом на строке, без
пояснений вокруг. Инструменты:
{"tool":"list"} — список целей;
{"tool":"check","server":"имя"} — доступна ли цель;
{"tool":"wake","server":"имя"} — включить сервер по сети (Wake-on-LAN);
{"tool":"run","server":"имя","command":"команда"} — выполнить команду (Linux sh, на плате — контейнер приложения);
{"tool":"write","server":"имя","path":"~/neuro12-agent/файл","content":"текст"} — записать файл;
{"tool":"code","server":"имя"} — мозг сам пишет скрипт по задаче владельца, кладёт на цель, проверяет и запускает;
{"tool":"wait","seconds":60} — подождать (после перезагрузки цели), не больше 180 с;
{"tool":"note","text":"…"} — шаг для владельца вручную (то, что агент сам сделать не может);
{"tool":"cloud","op":"status|health|logs|boards|llm_status|llm_train|kb_reindex|save_memory|maxbot_restart|tls_status|update_status|thoughts_stats|rollback","backup":"имя копии"} — управление самим облаком без команд ОС;
{"tool":"restart_cloud"} — перезапустить САМО облако (этот процесс); после него шагов не будет;
{"tool":"shot"} — снять рабочий стол ПК и показать снимок владельцу;
{"tool":"desk","act":"click|move|scroll|type|key|open|focus|windows","fx":0..1,"fy":0..1,"text":"…","keys":"ctrl+c","target":"что открыть","title":"часть заголовка окна"} — действие на рабочем столе ПК;
{"tool":"vision"} — состояние камер плат: свежие кадры, битые кадры, причины;
{"tool":"finish","summary":"итог для владельца по-русски"}.
Правила: сначала читающие шаги (list, check, run с uptime/df -h/free -h,
systemctl status, journalctl -n, ss -tlnp, docker ps; shot; vision;
desk act=windows). Интерактивные программы (top без -bn1, vim, less) запрещены.
Изменяющие шаги (установка, перезапуск, запись, desk click/type/key/open/focus)
владелец подтверждает кнопкой. Никогда не предлагай rm -rf /, mkfs, dd в
устройство, reboot/shutdown без прямой просьбы, остановку sshd. Координаты для
desk click — ДОЛИ экрана от 0 до 1 (после shot прикинь по снимку). Код пиши в
~/neuro12-agent/. Максимум 14 шагов, потом finish.
Цель «облако (этот компьютер)» (если есть в списке) — сам компьютер облака; в списке указана его ОС: на Windows команды — PowerShell (Get-Process, Get-Service, Get-PSDrive…), НЕ Linux-команды (uptime, free, df, systemctl там нет): там код
пишется и запускается безопасно под присмотром; плата может отказать в исполнении
(поле blocked) — тогда НЕ повторяй команду, а честно скажи об этом в finish и
используй SSH-запись платы или цель «облако». Не выдумывай вывод команд.
''' + __import__("admincmds").reference(1500)

_LOCK = threading.RLock()
_JOBS = {}          # id -> задание

_DESK_ACTS = ("click", "move", "scroll", "type", "key", "open", "focus", "windows")


# ----------------------------------------------------------------- задания
def start_job(task, owner="local", mode="both", auto_ok=None, target="", extra=None):
    task = str(task or "").strip()[:1500]
    if not task:
        return {"ok": False, "error": "пустая задача"}
    mode = mode if mode in ("both", "brain", "llm") else "both"
    if auto_ok is None:
        auto_ok = bool(store.settings().get("agent_auto_ok"))
    jid = uuid.uuid4().hex[:12]
    job = {"id": jid, "task": task, "owner": str(owner)[:40], "started": time.time(),
           "status": "работает", "steps": [], "result": None, "pending": None,
           "auto_ok": bool(auto_ok), "mode": mode, "target": str(target or "")[:60],
           "planner": "", "own_note": "", "shots": []}
    for k, v in (extra or {}).items():          # v3.237: prefer_aid / web_aid / _sw_try (повтор после «не помогло»)
        if k in ("prefer_aid", "web_aid", "_sw_try"):
            job[k] = v
    with _LOCK:
        _JOBS[jid] = job
        if len(_JOBS) > 60:
            for k in sorted(_JOBS, key=lambda k: _JOBS[k]["started"])[:len(_JOBS) - 60]:
                if _JOBS[k]["status"] not in ("работает", "ждёт разрешения"):
                    _JOBS.pop(k, None)
    job["_ev"] = threading.Event()
    threading.Thread(target=_run, args=(job,), daemon=True, name="admin-agent").start()
    store.log("АДМИН", "агент (режим %s): задача «%s»" % (mode, task[:120]))
    return {"ok": True, "job_id": jid}


def approve(jid, yes):
    job = _JOBS.get(jid)
    if not job or not job.get("pending"):
        return {"ok": False, "error": "нет действия, ждущего разрешения"}
    job["pending"]["decision"] = "yes" if yes else "no"
    job["_ev"].set()
    return {"ok": True}


def stop(jid):
    job = _JOBS.get(jid)
    if not job:
        return {"ok": False, "error": "нет задания"}
    job["stop"] = True
    if job.get("pending"):
        job["pending"]["decision"] = "no"
    job["_ev"].set()
    return {"ok": True}


def get_job(jid):
    job = _JOBS.get(jid)
    return {k: v for k, v in job.items() if not k.startswith("_")} if job else None


def list_jobs():
    with _LOCK:
        return [{"id": k, "task": v["task"][:100], "status": v["status"],
                 "mode": v.get("mode"), "started": v["started"]}
                for k, v in sorted(_JOBS.items(), key=lambda kv: -kv[1]["started"])]


# --------------------------------------------------------------- бюджет
def _will(job):
    """Воля мозга-12 для 'both' и 'brain'; для 'llm' — нейтральный бюджет."""
    base = MAX_STEPS
    if job.get("mode") == "llm":
        return {"source": "воля мозга-12 не применяется (режим «Только LLM»)",
                "connected": False, "zones": {}, "sources": [],
                "agent_strength": 1.0, "agent_effort": 1.0,
                "temperature_multiplier": 1.0, "step_limit": base}
    try:
        import brainwill
        return brainwill.agent(base)
    except Exception as e:
        return {"source": "воля мозга-12 недоступна: %s" % str(e)[:80],
                "connected": False, "zones": {}, "sources": [],
                "agent_strength": 1.0, "agent_effort": 1.0,
                "temperature_multiplier": 1.0, "step_limit": base}


# ----------------------------------------------------------- планировщики
def _ext_ready():
    try:
        import codewrite
        return codewrite.available()
    except Exception:
        return False


def _ext_next(job, history, temperature):
    import codewrite
    msgs = [{"role": "system", "content": SYSTEM},
            {"role": "user", "content": "Цели: %s\nРежим: %s\nЗадача владельца: %s" % (
                json.dumps([{k: t.get(k) for k in ("name", "host", "via", "kind", "os", "blocked")}
                            for t in servers.targets()], ensure_ascii=False)[:2500],
                job["mode"], job["task"])}]
    for act, res in history[-8:]:
        msgs.append({"role": "assistant", "content": json.dumps(act, ensure_ascii=False)})
        msgs.append({"role": "user", "content": "Результат: " + _short(res, 1500)})
    raw = codewrite._call(msgs, timeout=90, max_tokens=700, temperature=temperature)
    m = re.search(r"\{[\s\S]*\}", raw or "")
    if not m:
        return None
    try:
        a = json.loads(m.group(0))
        return a if isinstance(a, dict) and a.get("tool") else None
    except Exception:
        return None


def _pick_target(job):
    if job.get("target"):
        return job["target"]
    t = job["task"].lower()
    ts = servers.targets()
    for x in ts:
        for key in (x.get("name"), x.get("host")):
            if key and str(key).lower() in t:
                return x["target"]
    if re.search(r"\bплат[аеуы]|\bboard\b|uno\s*q", t):
        for x in ts:
            if x["via"] == "board":
                return x["target"]
    return ts[0]["target"] if len(ts) == 1 else ""


_RESTART_CLOUD = re.compile(
    r"(перезапуст|перезагруз|рестарт|restart|обнови процесс)\w*\W+(?:\w+\W+){0,2}?(облак|себя|neuro12|мозг-?12|сервер облака)"
    r"|(облак\w*|себя)\W+(?:\w+\W+){0,1}?(перезапуст|перезагруз|рестарт)")

_DESK_INTENTS = (
    (r"скриншот|снимок экрана|покажи (экран|рабочий стол|стол)", "shot"),
    (r"камер\w*|зрени\w*|бит\w* кадров?\w*|что видит", "vision"),
)


def _rule_plan(job):
    """Намерение задачи -> список действий. Сначала рабочие стол/зрение,
    затем правила окна «Серверы» (ssh_agent._rule_plan) как общая база."""
    lo = job["task"].lower()
    acts = []
    if _RESTART_CLOUD.search(lo):                 # v3.218: «перезапусти облако»
        return [{"tool": "restart_cloud"}]
    for rx, tool in _DESK_INTENTS:
        if re.search(rx, lo):
            if tool == "shot" and not re.search(r"камер\w*|плат\w*", lo):
                acts.append({"tool": "shot"})
            elif tool == "vision":
                acts.append({"tool": "vision"})
            break
    if re.search(r"(?:нажми|нажать|клавиши)\s+\S+", lo) and not re.search(r"на сервер|на плате", lo):
        m = re.search(r"(?:нажми|нажать|клавиши)\s+([a-zа-яё0-9+ ]{2,30})", lo)
        if m:
            # «нажми ctrl+alt+t на компьютере» -> только сочетание клавиш
            keys = re.split(r"\s+(?:на|и|затем|потом|в|во)\s+", m.group(1).strip())[0].strip()
            if keys:
                acts.append({"tool": "desk", "act": "key", "keys": keys})
    if re.search(r"(?:напечатай|введи текст|напиши на компьютере)\s*[:\-]?\s+\S+", lo):
        m = re.search(r"(?:напечатай|введи текст|напиши на компьютере)\s*[:\-]?\s+([\s\S]{1,200})",
                      job["task"], re.I)
        if m:
            acts.append({"tool": "desk", "act": "type", "text": m.group(1).strip()})
    mo = re.search(r"(?:открой на компьютере|запусти на компьютере|открой на пк)\s+(.+)", lo)
    if mo:
        acts.append({"tool": "desk", "act": "open", "target": mo.group(1).strip()[:120]})
    if acts:
        return acts
    try:
        import ssh_agent
        return ssh_agent._rule_plan(job)
    except Exception:
        tg = _pick_target(job)
        return [{"tool": "check", "server": tg}] if tg else [{"tool": "list"}]


def _next_rule_act(job, history, done):
    """Продолжение по правилам после начального плана: сначала свои подсказки
    (битые кадры камер — смотрим лог платы), затем общие серверные
    подсказки ssh_agent._next_rule_act (диск, службы, нагрузка, контейнеры).
    None — продолжить нечего, агент завершается с итогом."""
    for act, res in reversed(history):
        if act.get("tool") == "vision" and re.search(
                r"БИТЫЙ|битых в последних 24: [1-9]", str((res or {}).get("out") or "")):
            tg = next((t["target"] for t in servers.targets() if t["via"] == "board"), None)
            if tg:
                cand = {"tool": "run", "server": tg,
                        "command": "dmesg 2>/dev/null | grep -iE 'uvc|video|cam|usb' | tail -15; "
                                   "journalctl -k -n 20 --no-pager 2>/dev/null | tail -10"}
                if cand["command"] not in done:
                    return cand
    try:
        import ssh_agent
        return ssh_agent._next_rule_act(job, history, done)
    except Exception:
        return None


# -------------------------------------------------------------- выполнение
def _short(res, n=1200):
    if isinstance(res, dict):
        txt = res.get("out") or res.get("error") or json.dumps(res, ensure_ascii=False)
        if res.get("code") not in (None, 0):
            txt = "[код выхода %s] %s" % (res.get("code"), txt)
    else:
        txt = str(res)
    return str(txt)[-n:]


def _need_ok(job, kind):
    if kind == "danger":
        return True
    return kind == "change" and not job.get("auto_ok")


def _ask(job, act, kind, why):
    job["pending"] = {"act": act, "kind": kind, "why": why, "ts": time.time(), "decision": None}
    job["status"] = "ждёт разрешения"
    job["_ev"].clear()
    job["_ev"].wait(900)                 # 15 минут на решение владельца
    d = (job.get("pending") or {}).get("decision")
    job["pending"] = None
    job["status"] = "работает"
    return d == "yes"


def _desk(job, act):
    """Действие на рабочем столе ПК через desktop.act — только владелец."""
    import desktop
    if not desktop.enabled():
        return {"ok": False, "error": "«Терминал и рабочий стол» выключен в Настройках → Общие"}
    a = str(act.get("desk_act") or act.get("dact") or act.get("act") or "").strip().lower()
    if a not in _DESK_ACTS:
        return {"ok": False, "error": "неизвестное действие рабочего стола: %s" % a[:40]}
    b = {"act": a}
    for k in ("fx", "fy", "x", "y", "text", "keys", "target", "title", "dy", "button", "double"):
        if act.get(k) is not None:
            b[k] = act.get(k)
    # изменяющие действия — как «change»: спрашиваем (open/focus — тоже меняют
    # фокус; windows — чтение)
    if a != "windows" and _need_ok(job, "change"):
        why = {"click": "клик мышью на рабочем столе ПК", "move": "перемещение мыши",
               "scroll": "прокрутка колеса", "type": "ввод текста на ПК",
               "key": "нажатие клавиш на ПК", "open": "открыть программу/сайт на ПК",
               "focus": "переключить окно на ПК"}.get(a, a)
        if not _ask(job, {"tool": "desk", **b}, "change", why):
            return {"ok": False, "error": "владелец не разрешил"}
    r = desktop.act(b)
    if a == "move":
        r = dict(r, note="мышь перемещена — действия по-прежнему ждут разрешения")
    return r


_CLOUD_OPS = {"status": "read", "health": "read", "logs": "read", "boards": "read", "llm_status": "read",
              "tls_status": "read", "update_status": "read", "thoughts_stats": "read",
              "save_memory": "change", "kb_reindex": "change", "llm_train": "change",
              "maxbot_restart": "change", "rollback": "danger"}


def _cloud_op(job, act):
    """v3.219: управление самим облаком без команд ОС (работает и на Windows)."""
    op = str(act.get("op") or "")
    kind = _CLOUD_OPS.get(op)
    if kind is None:
        return {"ok": False, "error": "неизвестная операция облака: %s (есть: %s)" % (op, ", ".join(sorted(_CLOUD_OPS)))}
    if kind != "read" and _need_ok(job, kind) and not _ask(job, act, kind, "операция облака: %s" % op):
        return {"ok": False, "error": "владелец не разрешил"}
    try:
        if op in ("status", "health"):
            import boardcode
            out = ["облако %s, порт %s" % (store.version(), store.read_port())]
            try:
                import tls
                t = tls.status()
                out.append("HTTPS: %s" % ("включён" if t.get("on") else "выключен"))
            except Exception:
                pass
            out.append("плат на связи: %d" % len(boardcode.boards(max_age=120)))
            try:
                import kb
                out.append("база знаний: %s" % kb.index_status())
            except Exception:
                pass
            return {"ok": True, "out": "; ".join(out)}
        if op == "logs":
            return {"ok": True, "out": "\n".join(str(x) for x in store.log_tail(40))[-5000:]}
        if op == "boards":
            import boardcode
            return {"ok": True, "out": json.dumps(boardcode.boards(max_age=120), ensure_ascii=False)[:3000]}
        if op == "llm_status":
            import tinyllm
            return {"ok": True, "out": json.dumps(tinyllm.status(), ensure_ascii=False, default=str)[:2500]}
        if op == "llm_train":
            import tinyllm
            tinyllm.ensure_training()
            return {"ok": True, "out": "обучение своей LLM запущено/продолжается (смотри «Код» → статус)"}
        if op == "kb_reindex":
            import kb
            threading.Thread(target=kb.warm_index, daemon=True).start()
            return {"ok": True, "out": "переиндексация базы знаний запущена в фоне"}
        if op == "save_memory":
            import brain
            saved = brain.persist_all()
            return {"ok": True, "out": "сохранено: %s" % (", ".join(k for k, _v in saved) or "—")}
        if op == "maxbot_restart":
            import maxbot
            maxbot.restart("агент администрирования")
            return {"ok": True, "out": "бот МАКС перезагружен (облако не перезапускалось)"}
        if op == "tls_status":
            import tls
            return {"ok": True, "out": json.dumps(tls.status(), ensure_ascii=False, default=str)[:1500]}
        if op == "update_status":
            import updater
            return {"ok": True, "out": json.dumps(updater.status(), ensure_ascii=False, default=str)[:3000]}
        if op == "thoughts_stats":
            import thoughtbank
            import thoughtjournal
            return {"ok": True, "out": json.dumps({"bank": thoughtbank.stats(), "journal": thoughtjournal.stats()},
                                                  ensure_ascii=False, default=str)[:2500]}
        if op == "rollback":
            import updater
            name = str(act.get("backup") or "")
            if not name:
                return {"ok": False, "error": "не указана копия (backup); список: операция update_status"}
            r = updater.rollback(name, restart=True)
            return {"ok": True, "out": "откат к «%s» запланирован: %s" % (name, json.dumps(r, ensure_ascii=False, default=str)[:300])}
    except Exception as e:
        return {"ok": False, "error": "%s: %s" % (type(e).__name__, str(e)[:200])}
    return {"ok": False, "error": "не выполнено"}


def _exec(job, act):
    tool = act.get("tool")
    tg = act.get("server") or job.get("target") or ""
    if tg and not (str(tg).startswith(("board:", "local:")) or ssh_servers.get(tg)):
        tg = servers.resolve(tg) or tg
    if tool == "list":
        rows = ["%s — %s %s (%s)" % (t["name"], t.get("user") or "", t.get("host") or "",
                                     "плата, связь" if t["via"] == "board" else ("это облако" if t["via"] == "local" else "SSH"))
                for t in servers.targets()]
        import desktop
        rows.append("рабочий стол ПК — %s" % ("доступен" if desktop.enabled() else "выключен (⚙ Настройки → Общие)"))
        import vision
        cams = [c["cam"] for c in vision.cams()]
        if cams:
            rows.append("камеры плат — %s" % ", ".join(cams))
        return {"ok": True, "out": "\n".join(rows) or "целей нет"}
    if tool == "check":
        return servers.check(tg)
    if tool == "wake":
        if _need_ok(job, "change") and not _ask(job, act, "change", "включить сервер по сети"):
            return {"ok": False, "error": "владелец не разрешил"}
        return servers.wake(tg, wait=120)
    if tool == "run":
        cmd = str(act.get("command") or "").strip()
        if not cmd:
            return {"ok": False, "error": "пустая команда"}
        kind = servers.classify(cmd)
        if act.get("risk") == "danger":                 # инструкция помечена опасной — спросим всегда
            kind = "danger"
        elif act.get("risk") == "change" and kind == "read":
            kind = "change"
        if act.get("web") and kind != "danger":          # v3.237: команда из интернета, не проверенная владельцем
            if not _ask(job, act, "change", "команда из инструкции, записанной из интернета (ещё не проверена владельцем)"):
                return {"ok": False, "error": "владелец не разрешил"}
        elif _need_ok(job, kind):
            why = "ОПАСНАЯ команда — может уничтожить данные или доступ" if kind == "danger" \
                else "команда меняет систему"
            if not _ask(job, act, kind, why):
                return {"ok": False, "error": "владелец не разрешил"}
        return servers.run(tg, cmd, timeout=int(act.get("timeout") or 90))
    if tool == "write":
        path = str(act.get("path") or "")
        if not _need_ok(job, "change") or _ask(job, act, "change", "записать файл %s" % path):
            return servers.write_file(tg, path, act.get("content") or "")
        return {"ok": False, "error": "владелец не разрешил"}
    if tool == "cloud":
        return _cloud_op(job, act)
    if tool == "wait":
        try:
            sec = max(1, min(180, int(act.get("seconds") or 30)))
        except (TypeError, ValueError):
            sec = 30
        t0 = time.time()
        while time.time() - t0 < sec and not job.get("stop"):
            time.sleep(1)
        return {"ok": True, "out": "подождал %d с" % int(time.time() - t0)}
    if tool == "note":
        return {"ok": True, "out": "Шаг для владельца (вручную): %s" % str(act.get("text") or "")[:1200]}
    if tool == "restart_cloud":
        if _need_ok(job, "change") and not _ask(job, act, "change", "перезапустить облако (процесс остановится на ~10–20 с)"):
            return {"ok": False, "error": "владелец не разрешил"}
        try:
            import updater
            port = store.read_port()
            threading.Timer(2.5, lambda: updater.schedule_restart("агент администрирования")).start()
            store.log("АДМИН", "агент: перезапуск облака запланирован (порт %s)" % port)
            return {"ok": True, "final": True, "out":
                    "Перезапуск облака запланирован: данные сохраняются, процесс остановится через ~3 с и "
                    "поднимется на порту %s с тем же каталогом данных. Обнови страницу через 10–20 с; "
                    "журнал перезапуска — data/restart.log." % port}
        except Exception as e:
            return {"ok": False, "error": "перезапуск не удался: %s" % str(e)[:200]}
    if tool == "shot":
        import desktop
        if not desktop.enabled():
            return {"ok": False, "error": "«Терминал и рабочий стол» выключен в Настройках → Общие"}
        r = desktop.save_shot()
        if r.get("ok"):
            job.setdefault("shots", []).append(r["file"])
            if len(job["shots"]) > 6:
                job["shots"] = job["shots"][-6:]
            return {"ok": True, "out": "снимок снят: %s (владелец видит его в панели)" % r["file"], "file": r["file"]}
        return r
    if tool == "desk":
        return _desk(job, act)
    if tool == "vision":
        try:
            import vision
            cams = vision.cams()
            if not cams:
                return {"ok": True, "out": "камер нет: ни одна плата ещё не прислала кадр"}
            lines = []
            for c in cams[:8]:
                row = "%s: кадров нет" % c["cam"] if not c.get("ts") else \
                    "%s: кадр %d с назад, %d КБ" % (c["cam"], int(max(0, time.time() - c["ts"])),
                                                    (c.get("bytes") or 0) // 1024)
                if c.get("names"):
                    row += ", видит: %s" % ", ".join(c["names"][:4])
                if c.get("broken"):
                    row += ", ПОСЛЕДНИЙ КАДР БИТЫЙ"
                if c.get("broken_n"):
                    row += ", битых в последних 24: %d" % c["broken_n"]
                if c.get("bad_why"):
                    row += "; причина: %s" % c["bad_why"][:120]
                bd = c.get("board") or {}
                if bd.get("state") and bd["state"] not in ("кадры идут",):
                    row += "; плата: %s" % bd["state"][:80]
                lines.append(row)
            return {"ok": True, "out": "\n".join(lines)}
        except Exception as e:
            return {"ok": False, "error": str(e)[:200]}
    return {"ok": False, "error": "неизвестный инструмент %s" % tool}


def _code_actions(job, tg):
    """«Напиши скрипт …» — код пишет раздел «Код», агент кладёт на цель."""
    try:
        import ssh_agent
        return ssh_agent._code_actions(job, tg)
    except Exception as e:
        return None, str(e)[:300]


def _summary(job):
    """Итог без LLM: что выполнено и что бросилось в глаза в выводе."""
    lines, notes = [], []
    for s in job["steps"]:
        a, r = s["act"], s["result"] or {}
        what = a.get("command") or a.get("path") or a.get("target") or a.get("keys") or a.get("tool")
        if a.get("tool") == "desk":
            what = "стол: %s" % (a.get("desk_act") or a.get("act") or "")
        if a.get("tool") == "shot":
            what = "снимок рабочего стола"
        lines.append("%s %s" % ("✓" if r.get("ok") else "✗", str(what)[:120]))
        out = str(r.get("out") or "")
        for mm in re.finditer(r"(\d{2,3})%\s+(/\S*)", out):
            if int(mm.group(1)) >= 90:
                notes.append("диск %s заполнен на %s%%" % (mm.group(2), mm.group(1)))
        if re.search(r"\bfailed\b", out) and "0 loaded units" not in out:
            notes.append("есть упавшие службы или ошибки — смотри вывод")
        mm = re.search(r"load average:\s*([\d.,]+)", out)
        if mm:
            notes.append("нагрузка %s" % mm.group(1).rstrip(","))
        if r.get("error"):
            notes.append(str(r["error"])[:420 if r.get("blocked") else 150])
    txt = "Сделано шагов: %d.\n%s" % (len(job["steps"]), "\n".join(lines))
    if notes:
        txt += "\nЗамечено: " + "; ".join(dict.fromkeys(notes))
    if job.get("plan_note"):
        txt += "\n" + job["plan_note"]
    if job.get("selfwrite_note"):
        txt += "\n" + job["selfwrite_note"]
    return txt


def _own_note(job):
    """Короткая заметка своей LLM (она учится в потоке «Администрирование»)."""
    try:
        import tinyllm
        m = tinyllm.model()
        if not getattr(m, "ready", False):
            return ""
        prompt = "Задача владельца: %s\nИтог: " % job["task"][:200]
        out = m.generate(prompt, max_new=40, temperature=0.7)
        out = " ".join(str(out or "").split())[:240]
        return out if len(out) > 12 else ""
    except Exception:
        return ""


def _run(job):
    history = []
    done = set()                     # уже выполненные команды/инструменты
    mode = job.get("mode") or "both"
    will = _will(job)
    job["will"] = {k: will.get(k) for k in ("source", "agent_strength", "agent_effort",
                                            "temperature_multiplier", "step_limit")}
    step_limit = max(1, min(MAX_STEPS, int(will.get("step_limit") or MAX_STEPS)))
    use_ext = _ext_ready()
    if mode == "brain" or job.get("prefer_aid"):   # v3.237: повтор по переписанной инструкции — ведёт память мозга
        use_ext = False
    garbage = 0                      # подряд отвеченные «мусором» шаги модели
    if mode == "llm" and not use_ext:
        job["planner"] = "правила агента (модель не настроена — ⚙ → «API и ответы»)"
    else:
        job["planner"] = ("модель + воля мозга-12" if use_ext and mode == "both" else
                          "модель" if use_ext else "память мозга (поиск по знаниям) + воля мозга-12")
    queue = []                       # v3.218: планирует модель или память мозга, не ключевые слова
    _tg = job.get("target") or _pick_target(job)
    import ssh_agent as _sa
    _win = _sa._is_win(_tg) if _tg else False
    try:
        for step in range(step_limit):
            if job.get("stop"):
                job["status"], job["result"] = "остановлено", "остановлено владельцем"
                break
            act = None
            if use_ext:
                try:
                    act = _ext_next(job, history,
                                    temperature=0.1 * (will.get("temperature_multiplier") or 1.0))
                    if act is None:
                        garbage += 1
                        if garbage >= 2:
                            # модель не даёт действий — додумываем правилами,
                            # а не завершаемся с пустым итогом
                            job["planner"] += " → память мозга (модель отвечает без действий)"
                            use_ext, garbage = False, 0
                    else:
                        garbage = 0
                except Exception as e:
                    job["planner"] = "память мозга (модель: %s)" % str(e)[:80]
                    use_ext = False
            if act is None:
                if use_ext and garbage < 2:
                    continue             # модели даём ещё попытку (до двух пустых)
                if queue:
                    act = queue.pop(0)
                else:
                    import brainplan
                    act = brainplan.next_act(job, history, done, _tg, _win)
                    if act is None and not _tg:
                        job["plan_note"] = ("Не понял, на какой цели работать: выбери цель в списке окна "
                                            "или назови сервер по имени.")
                    elif act is None and not history and not job.get("_sw_done") \
                            and store.settings().get("admin_selfwrite", True):
                        # v3.237: в памяти нет — ищем в интернете и записываем инструкцию сами
                        job["_sw_done"] = True
                        try:
                            import admin_selfwrite
                            job["status_note"] = "в памяти нет — ищу в интернете и записываю инструкцию"
                            aid, swn = admin_selfwrite.self_write(job, _win)
                        except Exception as e:
                            aid, swn = None, "%s: %s" % (type(e).__name__, str(e)[:100])
                        job["selfwrite_note"] = "Искала в интернете: " + str(swn)
                        if not aid and job.get("weak_lesson"):
                            # интернет не помог — берём ближайший урок памяти, как раньше
                            job["_no_weak"] = True
                            act = brainplan.next_act(job, history, done, _tg, _win)
                            job["selfwrite_note"] += (". Взяла ближайший урок памяти «%s» (он лишь частично "
                                                      "про задачу)." % str(job["weak_lesson"]).replace("Администрирование: ", "")[:60])
                        if aid:
                            job["prefer_aid"] = aid
                            act = brainplan.next_act(job, history, done, _tg, _win)
                            job["selfwrite_note"] = ("В памяти не было, как это сделать — нашла в интернете и записала "
                                                "инструкцию «%s» (%s). Каждую команду из сети спрашиваю. После задания "
                                                "нажми «Помогло» или «Не помогло»." % (aid, swn))
                    if act is None and _tg and not history and not job.get("plan_note"):
                        job["plan_note"] = ("Мозг не нашёл в своей памяти, как это сделать (поиск по знаниям "
                                            "администрирования по запросу «%s» ничего не дал). Я не буду подставлять "
                                            "случайные команды. Уточни задачу, дай команду в обратных кавычках "
                                            "(`команда`) или настрой внешнюю модель (⚙ → «API и ответы») — она "
                                            "планирует свободно. Успешные задания мозг запоминает." % str(job.get("task"))[:80])
            if act is None:                      # из памяти больше нечего предложить
                act = {"tool": "finish"}
            if act.get("tool") == "finish":
                job["result"] = (act.get("summary") or "").strip() or _summary(job)
                job["status"] = "done"
                break
            if act.get("tool") == "code":
                acts, note = _code_actions(job, act.get("server"))
                if not acts:
                    job["steps"].append({"n": step, "act": act, "result": {"ok": False, "error": note},
                                         "ts": time.time()})
                    continue
                queue = acts + queue
                job["code_note"] = note
                act = queue.pop(0)
            if act.get("web_aid"):
                job["web_aid"] = act["web_aid"]
            res = _exec(job, act)
            if act.get("tool") == "restart_cloud":
                job["steps"].append({"n": step, "tool": "restart_cloud", "act": act, "result": res, "ts": time.time()})
                job["result"] = (res.get("out") or res.get("error") or "")
                job["status"] = "done" if res.get("ok") else "error"
                break
            exp = act.get("expect")
            if exp and res and res.get("ok"):
                try:
                    if not re.search(exp, str(res.get("out") or ""), re.I | re.S):
                        res = dict(res, ok=False, expect_failed=True,
                                   error="в выводе нет ожидаемого «%s»" % str(exp)[:80])
                except re.error:
                    pass
            history.append((act, res))
            done.add(str(act.get("command") or act.get("tool")))
            job["steps"].append({"n": step, "tool": act.get("tool"), "act": act,
                                 "result": {k: (v[-6000:] if isinstance(v, str) else v)
                                            for k, v in (res or {}).items()}, "ts": time.time()})
            if act.get("stop_on_fail") and not (res or {}).get("ok"):
                job["result"] = ("Остановился на шаге «%s»: %s. Дальше не пошёл, чтобы не навредить. "
                                 "Инструкция: %s" % (str(act.get("command") or act.get("tool"))[:100],
                                                     str((res or {}).get("error") or (res or {}).get("out") or "шаг не удался")[:300],
                                                     str(act.get("why") or "")[:100]))
                job["status"] = "done"
                break
        else:
            job["status"] = "done"
            job["result"] = _summary(job) + "\n(достигнут предел %d шагов)" % step_limit
    except Exception as e:
        job["status"] = "error"
        job["result"] = "%s: %s" % (type(e).__name__, str(e)[:300])
    job["finished"] = time.time()
    if mode in ("both", "llm") and job["status"] == "done":
        job["own_note"] = _own_note(job)
    try:
        import admincorpus
        admincorpus.learn_from_job(job)       # опыт — в поток своей LLM
        import brainplan
        brainplan.learn(job)                  # и в базу знаний: мозг запоминает, что сработало
    except Exception:
        pass
    try:
        import admin_selfwrite
        admin_selfwrite.note_job(job)         # v3.237: для кнопок «помогло / не помогло»
    except Exception:
        pass
    store.log("АДМИН", "агент (%s): «%s» — %s" % (mode, job["task"][:80], job["status"]))
