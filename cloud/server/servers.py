# -*- coding: utf-8 -*-
"""v3.178: ОКНО «СЕРВЕРЫ» — цели, включение, проверка, команды, файлы.

Цели бывают двух видов:
  * SSH-сервер (ssh_servers.py): Linux/Windows-машина, куда облако ходит по
    своему ключу (data/ssh/id_ed25519) — команды выполняет системный ssh;
  * плата (boardcode.py): плата v2.93+ на связи — команды выполняются в
    контейнере её приложения через канал связи мозгов (без SSH и ключей).
    Саму Linux-систему платы (arduino@адрес) можно добавить SSH-сервером —
    кнопка «Добавить плату как SSH-сервер».

Всё здесь вызывается только из панели владельца.
"""
import base64
import os
import re
import socket
import subprocess
import time

import store
import ssh_servers

_DANGER = [
    r"\brm\s+-[a-zA-Z]*[rR][a-zA-Z]*\s+(-[a-zA-Z]+\s+)*(/|/\*|~/?|\$HOME/?|\*)(\s|$|;)",
    r"\bmkfs(\.\w+)?\b", r"\bdd\b[^\n]*\bof=/dev/", r"\b(shutdown|poweroff|halt|reboot)\b",
    r"\binit\s+[06]\b", r":\(\)\s*\{", r"\bchmod\s+-R\s+0?777\s+/(\s|$)", r">\s*/dev/[sh]d[a-z]",
    r"\bsystemctl\s+(stop|disable|mask)\s+ssh(d)?\b", r"\buserdel\b", r"\bpasswd\b",
    r"/etc/(passwd|shadow|sudoers)\b[^\n]*(>|tee|sed\s+-i)", r"\bwipefs\b", r"\bfdisk\b|\bparted\b",
    r"\bformat\s+[a-zA-Z]:", r"\bdel\s+/[sS]\b", r"\bRemove-Item\b[^\n]*-Recurse",
]
_CHANGE = [
    r"\b(apt|apt-get|dnf|yum|zypper|pacman|apk)\s+(-\S+\s+)*(install|remove|purge|upgrade|dist-upgrade|full-upgrade|autoremove|update|erase|-S|add|del)\b",
    r"\bpip3?\s+install\b", r"\bnpm\s+(i|install|uninstall)\b",
    r"\bsystemctl\s+(start|stop|restart|reload|enable|disable|mask|unmask|daemon-reload)\b",
    r"\bservice\s+\S+\s+(start|stop|restart|reload)\b", r"\b(kill|pkill|killall)\b",
    r"\bdocker\s+(rm|rmi|stop|start|restart|run|kill|prune|system\s+prune|volume\s+rm|compose\s+(up|down|restart|pull))\b",
    r"\bcrontab\s+(-r|-e|-)\b|\|\s*crontab\b", r"\brm\b", r"\bmv\b", r"\bcp\b", r"\btee\b",
    r"(^|[^0-9&>2])>{1,2}\s*[^&\s]", r"\bsed\s+-i\b", r"\b(chmod|chown|chgrp|ln|mkdir|touch|rmdir|truncate)\b",
    r"\b(useradd|usermod|groupadd)\b", r"\bufw\s+(allow|deny|enable|disable|delete|reset)\b",
    r"\b(iptables|nft)\s+", r"\bgit\s+(pull|push|reset|checkout|clean|merge|rebase|commit)\b",
    r"\barduino-app-cli\s+app\s+(start|stop|restart|new|delete)\b", r"\bsudo\b",
    r"\bbase64\s+-d\b", r"\b(wget|curl)\b[^\n]*\s(-o|-O|--output)\b",
]


def classify(cmd):
    """read | change | danger — что команда делает с системой."""
    c = str(cmd or "")
    if any(re.search(p, c) for p in _DANGER):
        return "danger"
    if any(re.search(p, c) for p in _CHANGE):
        # sudo только ради чтения (sudo journalctl …) — тоже изменение прав; спросим
        return "change"
    return "read"


# ------------------------------------------------------------------ цели
def targets():
    """Все цели окна: SSH-серверы + платы на связи."""
    out = []
    for s in ssh_servers.list_servers():
        out.append(dict(s, target=s["id"], via="ssh"))
    try:
        import boardcode
        import boardlink
        st = boardlink.status() if hasattr(boardlink, "status") else {}
        ips = {b.get("id"): b.get("ip") for b in (st.get("boards") or []) if isinstance(b, dict)}
        for b in boardcode.boards(max_age=120):
            out.append({"id": "board:" + b["board"], "target": "board:" + b["board"],
                        "name": b["board"], "host": ips.get(b["board"]) or "", "user": "",
                        "via": "board", "kind": "board", "ver": b["ver"], "ok": b["ok"],
                        "admin": b.get("admin"), "code": b.get("code"), "age": b["age"]})
    except Exception:
        pass
    return out


def resolve(name):
    """Имя / хост / id / «плата» -> id цели ("" — не нашли)."""
    n = str(name or "").strip().lower()
    if not n:
        return ""
    ts = targets()
    for t in ts:
        if n in (str(t.get("id") or "").lower(), str(t.get("name") or "").lower(),
                 str(t.get("host") or "").lower()):
            return t["target"]
    if n in ("плата", "плату", "board", "uno-q", "uno q"):
        for t in ts:
            if t["via"] == "board":
                return t["target"]
    for t in ts:
        if n and n in str(t.get("name") or "").lower():
            return t["target"]
    return ""


def _host(target):
    if str(target).startswith("board:"):
        for t in targets():
            if t["target"] == target:
                return t.get("host") or "", 22
        return "", 22
    s = ssh_servers.get(target) or {}
    return s.get("host") or "", int(s.get("port") or 22)


def check(target, timeout=3.0):
    """Доступен ли сервер (порт SSH открыт) / плата на связи."""
    if str(target).startswith("board:"):
        for t in targets():
            if t["target"] == target:
                return {"ok": t["age"] < 30, "out": "плата на связи %d с назад, v%s" % (t["age"], t["ver"])}
        return {"ok": False, "error": "плата не на связи"}
    host, port = _host(target)
    if not host:
        return {"ok": False, "error": "сервер не найден"}
    t0 = time.time()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            pass
        return {"ok": True, "out": "порт %d открыт (%d мс)" % (port, int((time.time() - t0) * 1000))}
    except Exception as e:
        return {"ok": False, "error": "%s:%d недоступен: %s" % (host, port, str(e)[:80])}


def _mac_bytes(mac):
    h = re.sub(r"[^0-9a-fA-F]", "", str(mac or ""))
    if len(h) != 12:
        raise ValueError("MAC должен быть из 12 шестнадцатеричных цифр")
    return bytes.fromhex(h)


def wake(target, wait=0):
    """Wake-on-LAN: волшебный пакет на MAC сервера (UDP 9 и 7, широковещательно)."""
    s = ssh_servers.get(target) or {}
    mac = s.get("mac") or ""
    if not mac:
        return {"ok": False, "error": "у сервера не задан MAC — впиши его кнопкой «MAC»"}
    try:
        pkt = b"\xff" * 6 + _mac_bytes(mac) * 16
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    sent = []
    host = s.get("host") or ""
    dests = ["255.255.255.255"]
    m = re.match(r"^(\d+\.\d+\.\d+)\.\d+$", host)
    if m:
        dests.append(m.group(1) + ".255")          # широковещательный адрес подсети
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as so:
        so.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        for d in dests:
            for port in (9, 7):
                try:
                    so.sendto(pkt, (d, port))
                    sent.append("%s:%d" % (d, port))
                except Exception:
                    pass
    store.log("СЕРВЕРЫ", "Wake-on-LAN %s (%s) -> %s" % (s.get("name"), mac, ", ".join(sent)))
    out = {"ok": bool(sent), "out": "пакет Wake-on-LAN отправлен на %s (%s)" % (mac, ", ".join(sent))}
    if wait and sent:
        t_end = time.time() + min(180, wait)
        while time.time() < t_end:
            c = check(target, 2)
            if c.get("ok"):
                out["out"] += "; сервер поднялся: " + c["out"]
                out["up"] = True
                break
            time.sleep(5)
        else:
            out["out"] += "; за %d с порт SSH не открылся" % wait
    return out


# --------------------------------------------------------------- команды
def run(target, cmd, timeout=60):
    """Выполнить команду на цели. {ok, code, out | error}."""
    target = str(target or "")
    if target.startswith("board:"):
        import boardcode
        r = boardcode.run(cmd, "sh", target[6:], timeout=min(120, timeout))
        if r.get("error"):
            return {"ok": False, "error": r["error"]}
        return {"ok": bool(r.get("ok")), "code": r.get("rc"), "out": r.get("out") or "",
                "server": target[6:]}
    return ssh_servers.run(target, cmd, timeout=timeout)


def write_file(target, path, content, check_syntax=True):
    """Записать файл на цели через base64 (кавычки не ломают команду)."""
    path = str(path or "").strip()
    if not path or len(path) > 300 or "\n" in path or "'" in path:
        return {"ok": False, "error": "плохой путь"}
    b64 = base64.b64encode(str(content or "").encode("utf-8")).decode("ascii")
    if len(b64) > 90000:
        return {"ok": False, "error": "файл больше ~64 КБ — разбей на части"}
    q = path.replace("~/", "$HOME/", 1) if path.startswith("~/") else path
    d = os.path.dirname(path) or "."
    dq = d.replace("~/", "$HOME/", 1) if d.startswith("~") else d
    if dq == "~":
        dq = "$HOME"
    cmd = "mkdir -p \"%s\" && echo '%s' | base64 -d > \"%s\" && wc -c < \"%s\"" % (dq, b64, q, q)
    if check_syntax:
        if path.endswith(".py"):
            cmd += " && python3 -m py_compile \"%s\" && echo 'синтаксис Python — ок'" % q
        elif path.endswith(".sh"):
            cmd += " && bash -n \"%s\" && echo 'синтаксис sh — ок'" % q
    r = run(target, cmd, timeout=60)
    store.log("СЕРВЕРЫ", "файл %s -> %s: %s" % (path, target, "ок" if r.get("ok") else "ошибка"))
    return r


# ------------------------------------------------------------ ключ облака
def cloud_key(create=False):
    """Публичный ключ облака (ssh-ed25519 …) — его кладут в authorized_keys."""
    priv = store.p("ssh", "id_ed25519")
    pub = priv + ".pub"
    if create and not os.path.isfile(priv):
        os.makedirs(os.path.dirname(priv), exist_ok=True)
        flags = {}
        if os.name == "nt":
            flags["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            r = subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "neuro12-cloud",
                                "-f", priv], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, timeout=30, **flags)
            if r.returncode != 0:
                return {"ok": False, "error": "ssh-keygen: " + r.stdout.decode("utf-8", "replace")[-200:]}
        except FileNotFoundError:
            return {"ok": False, "error": "нет ssh-keygen (Windows: Параметры → Приложения → "
                    "Дополнительные компоненты → Клиент OpenSSH)"}
        except Exception as e:
            return {"ok": False, "error": str(e)[:200]}
    if not os.path.isfile(pub):
        return {"ok": False, "error": "ключа ещё нет", "missing": True}
    with open(pub, "r", encoding="utf-8", errors="replace") as f:
        key = f.read().strip()
    return {"ok": True, "pub": key,
            "hint": "На сервере: mkdir -p ~/.ssh && echo '%s' >> ~/.ssh/authorized_keys && "
                    "chmod 700 ~/.ssh && chmod 600 ~/.ssh/authorized_keys" % key}


def history(n=60):
    return list(reversed((store.load_json("ssh_log.json", []) or [])[-n:]))
