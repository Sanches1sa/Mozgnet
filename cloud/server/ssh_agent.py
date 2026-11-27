# -*- coding: utf-8 -*-
"""ssh_agent.py — АГЕНТ ОКНА «СЕРВЕРЫ» (v3.178, переписан).

Владелец пишет задачу словами («покажи состояние web1», «перезапусти nginx
на web1», «установи htop на плату», «включи backup», «напиши на сервере
скрипт …»). Агент сам выбирает шаги:

  list  — какие цели есть;          check — доступна ли цель;
  wake  — включить по сети (WoL);    run   — команда на цели;
  write — записать файл (код) на цель;  finish — итог.

Кто планирует шаги:
  1. внешняя модель (⚙ → «Ответы LLM», OpenAI-совместимый адрес), если
     задана, — шаги JSON по одному, с выводом предыдущих;
  2. иначе — правила агента (намерение задачи -> набор проверенных команд);
  3. своя LLM учится на этом же (поток «Администрирование серверов») и в
     итоге добавляет свою короткую заметку, если её веса готовы.

Безопасность:
  * читающие команды выполняются сразу;
  * изменяющие (установка, перезапуск, запись, kill…) ждут кнопки
    «Разрешить», если владелец не включил «изменения без вопросов»;
  * опасные (rm -rf /, mkfs, dd, reboot, остановка sshd…) — всегда ждут
    разрешения, даже с галочкой;
  * не больше 12 шагов; всё пишется в журнал «Серверы».
"""
import json
import re
import threading
import time
import uuid

import store
import ssh_servers
import servers

MAX_STEPS = 12

SYSTEM = '''Ты агент-администратор облака «Star Gate» (окно «Серверы»). Ты управляешь
серверами владельца и его платами Arduino UNO Q. Отвечай ТОЛЬКО одним JSON-объектом
на строке, без пояснений вокруг. Инструменты:
{"tool":"list"} — список целей;
{"tool":"check","server":"имя"} — доступна ли цель;
{"tool":"wake","server":"имя"} — включить сервер по сети (Wake-on-LAN);
{"tool":"run","server":"имя","command":"команда"} — выполнить команду (Linux sh, на плате — контейнер приложения);
{"tool":"write","server":"имя","path":"~/neuro12-agent/файл","content":"текст"} — записать файл;
{"tool":"finish","summary":"итог для владельца по-русски"}.
Правила: сначала читающие команды (uptime, df -h, free -h, systemctl status, journalctl -n,
ss -tlnp, docker ps). Интерактивные программы (top без -bn1, vim, less) запрещены.
Изменения (установка, перезапуск, запись, kill) — по одному, владелец их подтверждает.
Никогда не предлагай rm -rf /, mkfs, dd в устройство, reboot/shutdown без прямой просьбы,
остановку sshd. Код пиши в ~/neuro12-agent/. Максимум 12 шагов, потом finish.'''

_LOCK = threading.RLock()
_JOBS = {}          # id -> задание


# ----------------------------------------------------------------- задания
def start_job(task, owner="local", use_own_llm=True, auto_ok=None, target=""):
    task = str(task or "").strip()[:1500]
    if not task:
        return {"ok": False, "error": "пустая задача"}
    if auto_ok is None:
        auto_ok = bool(store.settings().get("srv_auto_ok"))
    jid = uuid.uuid4().hex[:12]
    job = {"id": jid, "task": task, "owner": str(owner)[:40], "started": time.time(),
           "status": "работает", "steps": [], "result": None, "pending": None,
           "auto_ok": bool(auto_ok), "target": str(target or "")[:60],
           "planner": "", "own_note": ""}
    with _LOCK:
        _JOBS[jid] = job
        if len(_JOBS) > 60:
            for k in sorted(_JOBS, key=lambda k: _JOBS[k]["started"])[:len(_JOBS) - 60]:
                if _JOBS[k]["status"] not in ("работает", "ждёт разрешения"):
                    _JOBS.pop(k, None)
    job["_ev"] = threading.Event()
    threading.Thread(target=_run, args=(job, use_own_llm), daemon=True, name="srv-agent").start()
    store.log("СЕРВЕРЫ", "агент: задача «%s»" % task[:120])
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
        return [{"id": k, "task": v["task"][:100], "status": v["status"], "started": v["started"]}
                for k, v in sorted(_JOBS.items(), key=lambda kv: -kv[1]["started"])]


# ----------------------------------------------------------- планировщики
def _ext_ready():
    try:
        import codewrite
        return codewrite.available()
    except Exception:
        return False


def _ext_next(job, history):
    import codewrite
    msgs = [{"role": "system", "content": SYSTEM},
            {"role": "user", "content": "Цели: %s\nЗадача владельца: %s" % (
                json.dumps([{k: t.get(k) for k in ("name", "host", "via", "kind", "os", "blocked")}
                            for t in servers.targets()], ensure_ascii=False)[:2500], job["task"])}]
    for act, res in history[-8:]:
        msgs.append({"role": "assistant", "content": json.dumps(act, ensure_ascii=False)})
        msgs.append({"role": "user", "content": "Результат: " + _short(res, 1500)})
    raw = codewrite._call(msgs, timeout=90, max_tokens=700, temperature=0.1)
    m = re.search(r"\{[\s\S]*\}", raw or "")
    if not m:
        return None
    try:
        a = json.loads(m.group(0))
        return a if isinstance(a, dict) and a.get("tool") else None
    except Exception:
        return None


_SVC = r"([a-zA-Z0-9_.@-]+?)(?:\.service)?"


def _pick_target(job):
    if job.get("target"):
        return job["target"]
    t = job["task"].lower()
    ts = servers.targets()
    for x in ts:
        for key in (x.get("name"), x.get("host")):
            if key and str(key).lower() in t:
                return x["target"]
    loc = next((x["target"] for x in ts if x["via"] == "local"), "")
    if loc and re.search(r"на облаке|локально|на этом (компьютере|пк)|в облаке|на (самом )?облаке", t):
        return loc
    if re.search(r"\bплат[аеуы]|\bboard\b|uno\s*q", t):
        for x in ts:
            if x["via"] == "board":
                if loc and x.get("blocked") and re.search(r"(напиши|создай|сделай)\b.*(скрипт|код|программ)", t):
                    return loc                  # v3.216: плата не исполняет — код пишем и запускаем в облаке
                return x["target"]
    if loc and re.search(r"(напиши|создай|сделай)\b.*(скрипт|код|программ)", t) and len(ts) > 1:
        return loc
    return ts[0]["target"] if len(ts) == 1 else ""


def _is_win(tg):
    """Цель — Windows? (компьютер облака на Windows или SSH-сервер с пометкой windows)."""
    try:
        if str(tg).startswith("local:"):
            return servers.is_windows()
        s = ssh_servers.get(tg) or {}
        return "win" in str(s.get("kind") or s.get("note") or "").lower()
    except Exception:
        return False


def _rule_plan(job):
    """Намерение задачи -> список действий (правила агента без LLM)."""
    t = job["task"]
    lo = t.lower()
    tg = _pick_target(job)
    acts = []
    if not tg:
        return [{"tool": "list"}, {"tool": "finish", "summary":
                 "Не поняла, на какой цели работать. Назови сервер по имени или адресу "
                 "(список выше) или выбери цель в окне."}]
    is_board = tg.startswith("board:")
    m = re.search(r"(?:выполни|запусти команду|команда|run)\s*[:\-]?\s*[`«\"](.+?)[`»\"]", t, re.S) or \
        re.search(r"`([^`]+)`", t)
    if m:
        return [{"tool": "run", "server": tg, "command": m.group(1).strip()}]
    if re.search(r"включи\b|разбуди|wake|подними сервер|запусти сервер", lo) and not is_board:
        return [{"tool": "wake", "server": tg}, {"tool": "check", "server": tg}]
    svc = None
    ms = re.search(r"(?:перезапусти|рестарт\w*|restart|перезагрузи службу|запусти службу|"
                   r"останови|stop|start)\s+(?:службу\s+|сервис\s+)?" + _SVC + r"(?:\s|$|\.|,)", lo)
    if ms and ms.group(1) not in ("сервер", "плату", "на"):
        svc = ms.group(1)
        verb = "stop" if re.search(r"останови|stop", lo) else (
            "start" if re.search(r"запусти службу|\bstart\b", lo) else "restart")
        sudo = "" if is_board else "sudo -n "
        return [{"tool": "run", "server": tg, "command": "%ssystemctl %s %s" % (sudo, verb, svc)},
                {"tool": "run", "server": tg, "command": "systemctl status %s --no-pager | head -15" % svc}]
    mi = re.search(r"(?:установи|поставь|install)\s+([a-zA-Z0-9_.+-]+)", lo)
    if mi:
        pkg = mi.group(1)
        if is_board:
            cmd = "python3 -m pip install --user %s 2>&1 | tail -5" % pkg
        else:
            cmd = ("if command -v apt-get >/dev/null; then sudo -n apt-get install -y %s; "
                   "elif command -v dnf >/dev/null; then sudo -n dnf install -y %s; "
                   "else sudo -n yum install -y %s; fi 2>&1 | tail -8" % (pkg, pkg, pkg))
        return [{"tool": "run", "server": tg, "command": "command -v %s || echo 'не установлен'" % pkg},
                {"tool": "run", "server": tg, "command": cmd}]
    if re.search(r"(напиши|создай|сделай)\b.*(скрипт|код|программ)", lo):
        return [{"tool": "code", "server": tg}]
    if re.search(r"лог|журнал|ошибк", lo):
        ml = re.search(r"(?:лог\w*|журнал\w*)\s+(?:службы\s+)?" + _SVC + r"(?:\s|$)", lo)
        if ml and ml.group(1) not in ("на", "сервера", "платы", "ошибок"):
            acts.append({"tool": "run", "server": tg, "command": "journalctl -u %s -n 80 --no-pager" % ml.group(1)})
        else:
            acts.append({"tool": "run", "server": tg, "command":
                         "journalctl -p err -n 40 --no-pager 2>/dev/null || tail -n 60 /var/log/syslog 2>/dev/null || dmesg | tail -40"})
    if re.search(r"диск|мест[оа]|disk", lo):
        acts.append({"tool": "run", "server": tg, "command": "df -h -x tmpfs -x devtmpfs 2>/dev/null || df -h"})
        acts.append({"tool": "run", "server": tg, "command": "du -xh --max-depth=1 ~ 2>/dev/null | sort -h | tail -10"})
    if re.search(r"памят|процесс|нагруз|тормоз|ram|cpu", lo):
        acts.append({"tool": "run", "server": tg, "command": "free -h; uptime; nproc"})
        acts.append({"tool": "run", "server": tg, "command": "ps aux --sort=-%cpu | head -12"})
    if re.search(r"порт|сеть|сетев|ip\b|адрес", lo):
        acts.append({"tool": "run", "server": tg, "command": "ip -br a 2>/dev/null || hostname -I; (ss -tlnp 2>/dev/null || netstat -tln) | head -25"})
    if re.search(r"docker|контейнер", lo):
        acts.append({"tool": "run", "server": tg, "command": "docker ps --format '{{.Names}}\\t{{.Status}}\\t{{.Image}}'; docker stats --no-stream 2>/dev/null | head -15"})
    if re.search(r"обнов", lo):
        acts.append({"tool": "run", "server": tg, "command":
                     "(apt list --upgradable 2>/dev/null || dnf check-update 2>/dev/null) | head -30"})
    if re.search(r"микрофон|звук|arecord", lo):
        acts.append({"tool": "run", "server": tg, "command": "arecord -l 2>&1 | head -20; aplay -l 2>&1 | head -10"})
    if re.search(r"приложени|app lab|arduino-app", lo):
        acts.append({"tool": "run", "server": tg, "command": "arduino-app-cli app list 2>&1 | head -20"})
    win = _is_win(tg)
    if not acts:                         # v3.216: каталог команд (admincmds) по смыслу задачи
        try:
            import admincmds
            for _k, _c in admincmds.plan(lo, limit=3, win=win):
                acts.append({"tool": "run", "server": tg, "command": _c})
        except Exception:
            pass
    if not acts:                         # ничего не распознано — общий обзор, но честно
        try:
            import admincmds
            cmds = admincmds.overview(win)
        except Exception:
            cmds = ["uptime; free -h; df -h / | tail -1"]
        job["plan_note"] = ("Под запрос «%s» команд в справочнике не нашлось — выполнен общий обзор. "
                            "Назови точнее (диск, память, порты, службы, камера, звук…) или напиши команду в "
                            "обратных кавычках: `команда`." % t[:80])
        acts = [{"tool": "check", "server": tg}] + [{"tool": "run", "server": tg, "command": c} for c in cmds]
    return acts


def _next_rule_act(job, history, done):
    """Следующий шаг по правилам, исходя из уже увиденных результатов.

    Правиловый агент не заканчивает работу, когда кончился начальный план:
    он смотрит на вывод выполненных шагов и продолжает, пока в выводе есть
    за что зацепиться (занятый диск, упавшая служба, высокая нагрузка,
    вышедший контейнер). Зацепок нет — возвращает None, агент завершается.
    done — команды/инструменты, которые уже выполнялись (защита от повтора).
    """
    tg = _pick_target(job)
    if not tg:
        return None
    for act, res in reversed(history):
        res = res or {}
        out, ok = str(res.get("out") or ""), bool(res.get("ok"))
        cmd, tool = str(act.get("command") or ""), act.get("tool")
        cand = None
        if tool == "check" and not ok:
            # цель недоступна: продолжать диагностику на ней бессмысленно;
            # будим только если владелец прямо просил включить
            if re.search(r"включи\b|разбуди|wake|подними", job["task"].lower()):
                cand = {"tool": "wake", "server": tg}
        elif tool == "run" and "df -h" in cmd:
            best = None
            for mm in re.finditer(r"(\d{1,3})%\s+(/\S*)", out):
                if mm.group(2) in ("/dev", "/run", "/sys", "/proc", "/snap"):
                    continue
                if best is None or int(mm.group(1)) > int(best.group(1)):
                    best = mm
            if best and int(best.group(1)) >= 85:
                cand = {"tool": "run", "server": tg,
                        "command": "du -xh --max-depth=1 %s 2>/dev/null | sort -h | tail -12"
                                   % best.group(2)}
        elif tool == "run" and "--failed" in cmd and "0 loaded units" not in out:
            m = re.search(r"([a-zA-Z0-9_.@-]+\.service)", out)
            if m:
                cand = {"tool": "run", "server": tg,
                        "command": "systemctl status %s --no-pager -l 2>&1 | head -20; "
                                   "journalctl -u %s -n 30 --no-pager 2>&1 | tail -30"
                                   % (m.group(1), m.group(1))}
        elif tool == "run" and "uptime" in cmd:
            ml = re.search(r"load average:\s*(\d+(?:[.,]\d+)?)", out)
            if ml and float(ml.group(1).replace(",", ".")) >= 2.0:
                cand = {"tool": "run", "server": tg,
                        "command": "ps aux --sort=-%cpu | head -12"}
        elif tool == "run" and "docker ps" in cmd:
            m = re.search(r"^(\S+)\t[^\n]*Exited", out, re.M)
            if m:
                cand = {"tool": "run", "server": tg,
                        "command": "docker logs --tail 30 %s 2>&1 | tail -30" % m.group(1)}
        if cand and str(cand.get("command") or cand.get("tool")) not in done:
            return cand
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


def _exec(job, act):
    tool = act.get("tool")
    tg = act.get("server") or job.get("target") or ""
    if tg and not (str(tg).startswith(("board:", "local:")) or ssh_servers.get(tg)):
        tg = servers.resolve(tg) or tg
    if tool == "list":
        return {"ok": True, "out": "\n".join("%s — %s %s (%s)" % (t["name"], t.get("user") or "", t.get("host") or "",
                                                                   "плата, связь" if t["via"] == "board" else ("это облако" if t["via"] == "local" else "SSH"))
                                             for t in servers.targets()) or "целей нет"}
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
        if _need_ok(job, kind):
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
    return {"ok": False, "error": "неизвестный инструмент %s" % tool}


def _code_actions(job, tg):
    """«Напиши скрипт …» — код пишет раздел «Код» (своя LLM / модель /
    изученные примеры), агент кладёт его на цель, проверяет и запускает."""
    import codewrite
    lang = "bash" if re.search(r"\b(bash|sh|shell|баш)\b", job["task"].lower()) else "python"
    txt = codewrite.compose(job["task"], lang)
    m = re.search(r"```[ \t]*([A-Za-z0-9_+#.-]*)\s*\n([\s\S]*?)```", txt or "")
    if not m:
        return None, (txt or "раздел «Код» не дал кода")[:600]
    code = m.group(2).rstrip() + "\n"
    ext = ".sh" if lang == "bash" else ".py"
    name = "task_%s%s" % (time.strftime("%m%d_%H%M%S"), ext)
    path = "~/neuro12-agent/" + name
    run = ("timeout 60 bash %s" if ext == ".sh" else "timeout 60 python3 %s") % path
    if str(tg).startswith("local:"):                 # v3.216: код пишется и запускается в самом облаке
        import shutil
        import sys
        exe = sys.executable if not getattr(sys, "frozen", False) else (shutil.which("python") or "python")
        if servers.is_windows():
            run = '& "%s" "%s"' % (exe, path)
        else:
            run = ("bash %s" if ext == ".sh" else '"%s" %%s' % exe) % path
    return [{"tool": "write", "server": tg, "path": path, "content": code},
            {"tool": "run", "server": tg, "command": run}], txt[:300]


def _summary(job):
    """Итог без LLM: что выполнено и что бросилось в глаза в выводе."""
    lines, notes = [], []
    for s in job["steps"]:
        a, r = s["act"], s["result"] or {}
        what = a.get("command") or a.get("path") or a.get("tool")
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
    return txt


def _own_note(job):
    """Короткая заметка своей LLM (поток «Администрирование серверов»)."""
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


def _run(job, use_own_llm):
    history = []
    done = set()                     # уже выполненные команды/инструменты
    use_ext = _ext_ready()
    job["planner"] = "внешняя модель" if use_ext else "правила агента"
    queue = [] if use_ext else _rule_plan(job)
    garbage = 0                      # подряд отвеченные «мусором» шаги модели
    try:
        for step in range(MAX_STEPS):
            if job.get("stop"):
                job["status"], job["result"] = "остановлено", "остановлено владельцем"
                break
            act = None
            if use_ext:
                try:
                    act = _ext_next(job, history)
                    if act is None:
                        garbage += 1
                        if garbage >= 2:
                            # модель не даёт действий — додумываем правилами,
                            # а не завершаемся с пустым итогом
                            job["planner"] += " → правила (модель отвечает без действий)"
                            use_ext, garbage = False, 0
                            queue = _rule_plan(job)
                    else:
                        garbage = 0
                except Exception as e:
                    job["planner"] = "правила агента (модель: %s)" % str(e)[:80]
                    use_ext = False
                    queue = _rule_plan(job)
            if act is None:
                if use_ext and garbage < 2:
                    continue             # модели даём ещё попытку (до двух пустых)
                if queue:
                    act = queue.pop(0)
                else:
                    act = _next_rule_act(job, history, done)
            if act is None:                      # правилам больше нечего продолжить
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
            if act.get("tool") == "finish":
                job["result"] = (act.get("summary") or "").strip() or _summary(job)
                job["status"] = "done"
                break
            res = _exec(job, act)
            history.append((act, res))
            done.add(str(act.get("command") or act.get("tool")))
            job["steps"].append({"n": step, "tool": act.get("tool"), "act": act,
                                 "result": {k: (v[-6000:] if isinstance(v, str) else v)
                                            for k, v in (res or {}).items()}, "ts": time.time()})
        else:
            job["status"] = "done"
            job["result"] = _summary(job) + "\n(достигнут предел %d шагов)" % MAX_STEPS
    except Exception as e:
        job["status"] = "error"
        job["result"] = "%s: %s" % (type(e).__name__, str(e)[:300])
    job["finished"] = time.time()
    if use_own_llm and job["status"] == "done":
        job["own_note"] = _own_note(job)
    try:
        import admincorpus
        admincorpus.learn_from_job(job)       # опыт — в поток своей LLM
    except Exception:
        pass
    store.log("СЕРВЕРЫ", "агент: «%s» — %s" % (job["task"][:80], job["status"]))


def seed_admin_knowledge():
    """Уроки администрирования — в базу знаний (их же учит поток своей LLM)."""
    try:
        import admincorpus
        import kb
        n = 0
        import hashlib
        for title, body in (list(admincorpus.LESSONS) + list(admincorpus.catalog_lessons()) +
                            list(admincorpus.win_lessons()) + list(admincorpus.tool_lessons())):
            did = "admin_" + hashlib.md5(title.encode("utf-8")).hexdigest()[:12]   # v3.218: без дублей
            kb.add_article("Администрирование: " + title, body, source="servers_seed", did=did)
            n += 1
        try:
            import agent_instructions as instructions
            n += instructions.seed_kb()                 # v3.219: библиотека инструкций
            import codelessons
            n += codelessons.seed_kb()                  # v3.220: уроки программирования
            import applab_guide
            n += applab_guide.seed_kb()                 # v3.225: инструкции App Lab
            import project_brain
            n += project_brain.seed_kb()                # v3.221: инструкции Контура
        except Exception:
            pass
        return {"ok": True, "added": n}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}
