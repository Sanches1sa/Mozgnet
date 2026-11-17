# -*- coding: utf-8 -*-
"""ssh_servers.py — управление удалёнными серверами по SSH для нейросети.

Хранение: data/ssh_servers.json (список серверов).
Выполнение: через системный ssh (предпочтительно по ключу, без пароля в открытом виде).
Безопасность: команды логируются, опасные паттерны можно фильтровать в агенте.
"""
import json
import os
import re
import subprocess
import time
import uuid
import store

_PATH = "ssh_servers.json"
_LOG = "ssh_log.json"
_MAX_OUT = 16000
_SAFE_CMD_RE = re.compile(r"^[a-zA-Z0-9_\-\.\/\s:=,\"'<>|&;()\[\]{}*?~!@#$%^&+=]+$")


def _load():
    return store.load_json(_PATH, []) or []


def _save(servers):
    store.save_json(_PATH, servers)


def list_servers():
    """Список серверов без секретов."""
    out = []
    for s in _load():
        out.append({
            "id": s.get("id"),
            "name": s.get("name"),
            "host": s.get("host"),
            "user": s.get("user"),
            "port": s.get("port", 22),
            "has_key": bool(s.get("key_path")),
            "note": s.get("note", "")[:200],
            "mac": s.get("mac", ""),                   # v3.178: для включения по сети
            "kind": s.get("kind", "ssh"),
        })
    return out


def add_server(name, host, user="root", port=22, key_path="", password="", note=""):
    """Добавить сервер. password хранится только если нет ключа (не рекомендуется)."""
    if not host or not name:
        return {"ok": False, "error": "нужны name и host"}
    servers = _load()
    sid = uuid.uuid4().hex[:12]
    rec = {
        "id": sid,
        "name": str(name)[:80],
        "host": str(host)[:200],
        "user": str(user or "root")[:40],
        "port": int(port or 22),
        "key_path": str(key_path or "").strip(),
        "password": str(password or "").strip() if not key_path else "",  # не хранить если есть ключ
        "note": str(note or "")[:500],
        "added": time.time(),
    }
    servers.append(rec)
    _save(servers)
    return {"ok": True, "id": sid, "name": rec["name"]}


def set_field(sid, **kw):
    """v3.178: поменять поле сервера (mac, note, user, port)."""
    servers = _load()
    for s_ in servers:
        if s_.get("id") == sid:
            for k, v in kw.items():
                if k in ("mac", "note", "user", "port", "kind"):
                    s_[k] = v
            _save(servers)
            return {"ok": True}
    return {"ok": False, "error": "сервер не найден"}


def get(sid):
    return next((s_ for s_ in _load() if s_.get("id") == sid), None)


def remove_server(sid):
    servers = [s for s in _load() if s.get("id") != sid]
    _save(servers)
    return {"ok": True}


def _log(sid, cmd, out, code):
    try:
        hist = store.load_json(_LOG, []) or []
        hist.append({
            "ts": time.time(),
            "server": sid,
            "cmd": str(cmd)[:300],
            "code": code,
            "out": str(out)[:800],
        })
        store.save_json(_LOG, hist[-200:])
    except Exception:
        pass


def run(sid, command, timeout=60):
    """Выполнить команду на сервере по SSH. Возвращает {ok, code, out, error}."""
    servers = _load()
    srv = next((s for s in servers if s.get("id") == sid), None)
    if not srv:
        return {"ok": False, "error": "сервер не найден"}
    cmd = str(command or "").strip()
    if not cmd:
        return {"ok": False, "error": "пустая команда"}
    # простая защита: запрещаем некоторые опасные вещи на уровне агента лучше
    host = srv["host"]
    user = srv.get("user") or "root"
    port = int(srv.get("port") or 22)
    key = srv.get("key_path") or ""
    # строим ssh
    ssh_parts = ["ssh", "-o", "StrictHostKeyChecking=accept-new",
                 "-o", "BatchMode=yes", "-o", "ConnectTimeout=15",
                 "-p", str(port)]
    if not (key and os.path.isfile(key)):          # v3.178: свой ключ облака
        own = store.p("ssh", "id_ed25519")
        key = own if os.path.isfile(own) else ""
    if key and os.path.isfile(key):
        ssh_parts += ["-i", key]
    target = f"{user}@{host}"
    full = ssh_parts + [target, cmd]
    try:
        p = subprocess.run(full, capture_output=True, timeout=timeout,
                           text=False)
        out = (p.stdout or b"") + (p.stderr or b"")
        try:
            out_s = out.decode("utf-8", "replace")
        except Exception:
            out_s = out.decode("latin-1", "replace")
        out_s = out_s[:_MAX_OUT]
        _log(sid, cmd, out_s, p.returncode)
        return {
            "ok": p.returncode == 0,
            "code": p.returncode,
            "out": out_s,
            "server": srv["name"],
        }
    except subprocess.TimeoutExpired:
        _log(sid, cmd, "timeout", -1)
        return {"ok": False, "error": f"таймаут {timeout} с", "server": srv["name"]}
    except Exception as e:
        return {"ok": False, "error": str(e)[:300], "server": srv["name"]}


def status():
    return {"servers": len(_load()), "log_entries": len(store.load_json(_LOG, []) or [])}
