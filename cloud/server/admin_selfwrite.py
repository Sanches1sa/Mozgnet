# -*- coding: utf-8 -*-
"""admin_selfwrite.py — v3.237: САМОЗАПИСЬ ИНСТРУКЦИЙ АДМИНИСТРИРОВАНИЯ ИЗ ИНТЕРНЕТА.

Когда агент «Администрирование» не находит в памяти мозга, как выполнить задачу:
  1) ищет в интернете (project_web: Google/Яндекс через прокси облака);
  2) со страниц берёт команды из блоков <pre>/<code> и строк «$ команда»;
     опасное (rm -rf /, mkfs, dd of=/dev/…, curl|sh, форк-бомба, выключение…)
     и интерактивное (nano, vim, top, tail -f…) отбрасывается;
  3) пишет инструкцию в базу знаний: «Администрирование: из интернета — <задача> (вN)»
     с пометками «Источник: интернет», «Проверено владельцем: нет»;
  4) агент выполняет её — КАЖДАЯ команда из интернета спрашивает разрешения
     владельца, даже если в настройках включено «выполнять без спроса».
После задания в чате кнопки «✅ Помогло» / «❌ Не помогло»:
  * помогло — инструкция помечается «Проверено владельцем: да» (больше не
    спрашивает про каждую команду, кроме опасных), опыт задания остаётся в памяти;
  * не помогло — опыт задания стирается, инструкция помечается «НЕ помогло» и
    больше не вспоминается, мозг снова ищет в интернете (с ошибками из вывода, без
    прежних страниц), пишет версию N+1 и перезапускает задание. Не больше 3 попыток.
Честно о границах: это сбор команд со страниц, а не понимание. Поэтому каждая
команда из сети идёт через подтверждение владельца."""
import html as _h
import re
import threading
import time

import store

_FILE = "admin_selfwrite.json"
_LOCK = threading.Lock()
MAX_TRIES = 3
MAX_CMDS = 8

_DANGER = re.compile(
    r"rm\s+-[a-z]*r[a-z]*f?\s+(/|~|\*|/\*)(\s|$)|rm\s+-[a-z]*f[a-z]*r\s+(/|~|\*)(\s|$)|\bmkfs\b|\bdd\s+.*of=/dev/|"
    r":\(\)\s*\{|\b(curl|wget)\b[^|]*\|\s*(sudo\s+)?(ba|z)?sh\b|\bshutdown\b|\bpoweroff\b|\bhalt\b|\breboot\b|"
    r"\binit\s+0\b|>\s*/dev/sd|\bchmod\s+-R\s+777\s+/|\bchown\s+-R\s+\S+\s+/(\s|$)|\bfdisk\b|\bparted\b|\bwipefs\b|"
    r"\buserdel\b|\bpasswd\b|Format-Volume|Remove-Item\s+.*-Recurse.*\s[A-Z]:\\\\?(\s|$)|Clear-Disk|Stop-Computer|"
    r"Restart-Computer|\bdel\s+/[sq]|\brd\s+/s|iptables\s+-F|ufw\s+disable|Invoke-Expression|\biex\b|"
    r"DownloadString|base64\s+-d\s*\|", re.I)
_INTERACTIVE = re.compile(r"^(nano|vi|vim|emacs|top|htop|less|more|watch|ssh|mysql|psql|python3?|bash|sh|su|"
                          r"crontab\s+-e|visudo)\b|\s-f\b.*\b(tail|journalctl)|\b(tail|journalctl)\b.*\s-f\b|"
                          r"--follow\b|\bping\b(?!.*-c\s*\d)", re.I)
_LINUX_FIRST = re.compile(r"^(apt|apt-get|dnf|yum|zypper|pacman|systemctl|journalctl|service|df|du|free|uptime|ls|cat|"
                          r"grep|find|ip|ss|netstat|ping|docker|docker-compose|podman|nginx|apache2ctl|ufw|"
                          r"iptables|lsblk|mount|umount|chmod|chown|mkdir|cp|mv|tar|gzip|unzip|ps|kill|pkill|"
                          r"hostnamectl|timedatectl|lsof|curl|wget|git|pip3?|npm|snap|crontab|useradd|"
                          r"usermod|groups|id|who|w|last|dmesg|uname|lscpu|lsusb|nproc|sed|awk|tee|echo|"
                          r"certbot|openssl|ssh-keygen|resolvectl|nmcli|swapon|swapoff|fallocate|mkswap|"
                          r"logrotate|rsync|sysctl|modprobe|update-grub|locale|ln|touch|head|tail|wc|stat|"
                          r"which|whereis|env|export|sensors|vcgencmd|iostat|vmstat|nslookup|dig|host|"
                          r"traceroute|arduino-app-cli|adb)\b")
_WIN_FIRST = re.compile(r"^([A-Z][a-z]+-[A-Z][A-Za-z]+|ipconfig|netstat|sfc|DISM|chkdsk|winget|choco|sc(\.exe)?|"
                        r"tasklist|taskkill|systeminfo|wmic|net|powercfg|bcdedit|reg|netsh|whoami|hostname|"
                        r"Get-|Set-|Start-|Stop-|Restart-|Test-|New-|Remove-|Clear-|Enable-|Disable-|"
                        r"Install-|Update-|Repair-|Invoke-WebRequest|wsl|robocopy|icacls)", re.I)


def _db():
    return store.load_json(_FILE, {"tasks": {}, "jobs": {}})


def _save(d):
    store.save_json(_FILE, d)


def _key(task):
    t = re.sub(r"[`'\"«».,!?:;()]+", " ", str(task or "").lower())
    return re.sub(r"\s+", " ", t).strip()[:120]


def clean_cmd(ln, win):
    """Строка со страницы -> команда | None (без подсказки «$», без sudo, без опасного)."""
    ln = _h.unescape(str(ln or "")).replace("\u00a0", " ").strip()
    ln = re.sub(r"^(PS\s+[A-Z]:\\[^>]*>|PS>|[\w.@-]+[:~][^$#]*[$#]|\$|#|>|C:\\[^>]*>)\s+", "", ln)
    ln = re.sub(r"^sudo\s+(-\w+\s+)*", "", ln).strip()
    if not ln or len(ln) > 220 or "\t" in ln:
        return None
    if re.search(r"<[^>]*>|\[[A-Z_]{3,}\]|\byour[_-]|\bYOUR|example\.com|xxx|\.\.\.|…", ln):
        return None                                   # заглушки, которые надо заполнить вручную
    if _DANGER.search(ln) or _INTERACTIVE.search(ln):
        return None
    first = _WIN_FIRST if win else _LINUX_FIRST
    if not first.match(ln):
        return None
    if not win and re.match(r"^(apt|apt-get|dnf|yum)\s+(install|remove|upgrade|full-upgrade|dist-upgrade)\b", ln) \
            and not re.search(r"\s-y\b|--yes|--assume-yes", ln):
        ln = re.sub(r"^(apt|apt-get|dnf|yum)\s+(\w[\w-]*)", r"\1 \2 -y", ln, count=1)   # без вопроса «Продолжить? [Д/н]»
    return ln


def extract_cmds(page, win):
    """Команды со страницы: сначала <pre>/<code>, затем строки текста, похожие на команды."""
    raw = str((page or {}).get("html") or "")
    cand = []
    for m in re.finditer(r"<(pre|code)[^>]*>(.*?)</\1>", raw, re.S | re.I):
        t = re.sub(r"<[^>]+>", "", m.group(2))
        cand.extend(t.splitlines())
    if not cand:
        cand = str((page or {}).get("text") or "").splitlines()
    out = []
    for ln in cand:
        c = clean_cmd(ln, win)
        if c and c not in out:
            out.append(c)
        if len(out) >= MAX_CMDS:
            break
    return out


def research(task, win=False, exclude_urls=(), extra=""):
    """-> {ok, cmds, urls, note}. Ищет в интернете команды для задачи."""
    if not store.settings().get("web_on", True):
        return {"ok": False, "cmds": [], "urls": [], "note": "интернет выключен в настройках"}
    q = re.sub(r"\s+", " ", str(task or ""))[:120]
    q += " PowerShell команда" if win else " linux команда терминал"
    if extra:
        q += " " + re.sub(r"\s+", " ", str(extra))[:80]
    try:
        import project_web
        res = project_web.search(q, limit=5) or []
    except Exception as e:
        return {"ok": False, "cmds": [], "urls": [], "note": "поиск не удался: %s" % str(e)[:120]}
    cmds, urls = [], []
    for r in res[:5]:
        url = str(r.get("url") or "")
        if not url or url in exclude_urls:
            continue
        try:
            ok, page = project_web.fetch_page(url, timeout=12, max_bytes=400000)
        except Exception:
            ok = False
        if not ok:
            continue
        got = extract_cmds(page, win)
        if not got:
            continue
        urls.append(page.get("url", url))
        for c in got:
            if c not in cmds:
                cmds.append(c)
        if len(cmds) >= 4 or len(urls) >= 2:
            break
    cmds = cmds[:MAX_CMDS]
    if not cmds:
        return {"ok": False, "cmds": [], "urls": urls, "note": "на найденных страницах нет подходящих безопасных команд"}
    return {"ok": True, "cmds": cmds, "urls": urls, "note": "команд: %d со страниц: %s" % (len(cmds), ", ".join(urls))}


def write_instruction(task, win, found, version=1):
    """Инструкция в базу знаний -> id статьи."""
    import kb
    k = _key(task)
    aid = "admweb_%s_v%d" % (re.sub(r"[^\w]+", "_", k)[:40] or "task", version)
    lang = "powershell" if win else "bash"
    text = "\n".join([
        "Задача: %s." % str(task)[:300],
        "Цель: %s. Риск: change" % ("windows" if win else "linux"),
        "Источник: интернет (%s)" % ", ".join(found.get("urls") or []),
        "Проверено владельцем: нет",
        "Версия: %d" % version,
        "Записано мозгом-12 само: в памяти не было, как это сделать. Каждая команда спрашивает разрешения.",
        "```%s" % lang] + list(found.get("cmds") or []) + ["```"])
    kb.add_article("Администрирование: из интернета — %s (в%d)" % (str(task)[:60], version), text,
                   source="admin_selfwrite", did=aid)
    with _LOCK:
        d = _db()
        t = d["tasks"].setdefault(k, {"task": str(task)[:300], "versions": []})
        t["versions"].append({"aid": aid, "urls": found.get("urls") or [], "ts": time.time(), "status": "new"})
        t["versions"] = t["versions"][-10:]
        _save(d)
    return aid


def self_write(job, win):
    """Нет знаний — пишем инструкцию из интернета. -> (aid | None, заметка)."""
    task = str(job.get("task") or "")
    k = _key(task)
    d = _db()
    prev = (d["tasks"].get(k) or {}).get("versions") or []
    excl = [u for v in prev for u in v.get("urls") or []]
    found = research(task, win, exclude_urls=excl, extra=job.get("_sw_extra") or "")
    if not found["ok"]:
        return None, found["note"]
    aid = write_instruction(task, win, found, version=len(prev) + 1)
    job["web_aid"] = aid
    return aid, found["note"]


def note_job(job):
    """После задания: запомнить, какую инструкцию из интернета оно использовало."""
    with _LOCK:
        d = _db()
        d["jobs"][str(job.get("id"))] = {"task": str(job.get("task") or "")[:300], "web_aid": job.get("web_aid") or "",
                                         "mode": job.get("mode"), "target": job.get("target") or "",
                                         "auto_ok": bool(job.get("auto_ok")), "owner": job.get("owner"),
                                         "tries": int(job.get("_sw_try") or 1), "status": job.get("status"),
                                         "feedback": None, "ts": time.time()}
        if len(d["jobs"]) > 200:
            for j in sorted(d["jobs"], key=lambda j: d["jobs"][j].get("ts") or 0)[:len(d["jobs"]) - 200]:
                d["jobs"].pop(j, None)
        _save(d)


def _mark(aid, status):
    import kb
    art = kb.article(aid)
    if not art:
        return False
    text = str(art.get("text") or "")
    if status == "ok":
        text = re.sub(r"^Проверено владельцем:.*$", "Проверено владельцем: да (помогло)", text, flags=re.M)
    else:
        text = re.sub(r"^Проверено владельцем:.*$", "Проверено владельцем: НЕ помогло", text, flags=re.M)
    kb.add_article(art.get("title"), text, source=art.get("source") or "admin_selfwrite", did=aid)
    with _LOCK:
        d = _db()
        for t in d["tasks"].values():
            for v in t.get("versions") or []:
                if v.get("aid") == aid:
                    v["status"] = status
        _save(d)
    return True


def _errors(job):
    out = []
    for s in job.get("steps") or []:
        r = s.get("result") or {}
        if not r.get("ok"):
            e = str(r.get("error") or r.get("out") or "")
            ln = [l for l in e.splitlines() if re.search(r"error|ошибк|not found|failed|denied|не найден", l, re.I)]
            if ln:
                out.append(ln[0][:80])
    return " ".join(out[:2])


def feedback(jid, helped):
    """Кнопка «помогло / не помогло». -> {ok, text, job_id?}"""
    import admin_agent
    d = _db()
    rec = d["jobs"].get(str(jid))
    job = admin_agent.get_job(jid) or {}
    if not rec and not job:
        return {"ok": False, "text": "Не нашла это задание — оценивать нечего."}
    rec = rec or {"task": job.get("task"), "web_aid": job.get("web_aid") or "", "mode": job.get("mode"),
                  "target": job.get("target"), "auto_ok": job.get("auto_ok"), "owner": job.get("owner"), "tries": 1}
    if rec.get("feedback") is not None:
        return {"ok": True, "text": "Это задание уже оценено: %s." % ("помогло" if rec["feedback"] else "не помогло")}
    rec["feedback"] = bool(helped)
    with _LOCK:
        d = _db()
        d["jobs"][str(jid)] = rec
        _save(d)
    aid = rec.get("web_aid")
    if helped:
        if aid:
            _mark(aid, "ok")
        return {"ok": True, "text": "👍 Запомнила: сработало. %s Опыт задания остаётся в памяти мозга." % (
            "Инструкция из интернета помечена «проверено владельцем» — в следующий раз спрошу только опасное." if aid else "")}
    # не помогло: стираем опыт, помечаем инструкцию, переписываем из интернета и запускаем снова
    try:
        import kb
        kb.delete_article("admx_" + str(jid))
    except Exception:
        pass
    if aid:
        _mark(aid, "bad")
    tries = int(rec.get("tries") or 1)
    if tries >= MAX_TRIES:
        return {"ok": True, "text": "👎 Принято. Опыт стёрт. Уже %d попытки с инструкциями из интернета — больше сама не "
                                    "переписываю: дай команду в обратных кавычках или подскажи, что делать." % tries}
    import ssh_agent
    tg = rec.get("target") or job.get("target") or ""
    win = False
    try:
        win = ssh_agent._is_win(tg) if tg else False
    except Exception:
        pass
    pseudo = {"task": rec.get("task"), "_sw_extra": _errors(job)}
    new_aid, note = self_write(pseudo, win)
    if not new_aid:
        return {"ok": True, "text": "👎 Принято, опыт стёрт. Переписать инструкцию из интернета не вышло: %s." % note}
    r = admin_agent.start_job(rec.get("task"), owner=rec.get("owner") or "чат", mode=rec.get("mode") or "both",
                              auto_ok=bool(rec.get("auto_ok")), target=tg,
                              extra={"prefer_aid": new_aid, "web_aid": new_aid, "_sw_try": tries + 1})
    if not r.get("ok"):
        return {"ok": True, "text": "Инструкция переписана (%s), но задание не запустилось: %s" % (new_aid, r.get("error"))}
    return {"ok": True, "job_id": r["job_id"], "aid": new_aid,
            "text": "👎 Принято: опыт стёрт, старая инструкция помечена «не помогло». Нашла в интернете заново (%s) и "
                    "записала версию %d. Запускаю задание снова (попытка %d из %d) — каждую команду из сети спрошу." % (
                        note, tries + 1, tries + 1, MAX_TRIES)}


def status():
    d = _db()
    return {"tasks": [{"task": t.get("task"), "versions": t.get("versions")} for t in d["tasks"].values()][-30:],
            "jobs": len(d["jobs"])}
