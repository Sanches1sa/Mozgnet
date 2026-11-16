# -*- coding: utf-8 -*-
"""terminal.py — командная строка и рабочий стол для облака.

Включается настройкой terminal_on. Команды логируются.
Рабочий стол: снимок экрана (если есть scrot/import/spectacle).
"""
import os
import subprocess
import time
import store

_LOG = "terminal_log.json"
_MAX_OUT = 8000


def enabled():
    try:
        return bool(store.settings().get("terminal_on"))
    except Exception:
        return False


def _log(cmd, out, code):
    try:
        hist = store.load_json(_LOG, []) or []
        hist.append({"ts": time.time(), "cmd": str(cmd)[:200],
                     "code": code, "out": str(out)[:500]})
        store.save_json(_LOG, hist[-100:])
    except Exception:
        pass


def _dec(b):
    """v3.150: вывод cmd.exe в Windows — в кодировке OEM (cp866), не UTF-8."""
    if not b:
        return ""
    for enc in ("utf-8", "cp866", "cp1251"):
        try:
            return b.decode(enc)
        except Exception:
            continue
    return b.decode("utf-8", "replace")


def run(cmd, timeout=60):
    """Выполнить команду оболочки. v3.149.13: без ограничения длины."""
    if not enabled():
        return {"ok": False, "error": "терминал выключен в настройках"}
    cmd = str(cmd or "").strip()
    if not cmd:
        return {"ok": False, "error": "пустая команда"}
    try:
        p = subprocess.run(cmd, shell=True, capture_output=True,
                           timeout=timeout,
                           creationflags=0x08000000 if os.name == "nt" else 0)
        out = _dec(p.stdout) + _dec(p.stderr)
        out = out[:16000]
        _log(cmd, out, p.returncode)
        return {"ok": p.returncode == 0, "code": p.returncode, "out": out}
    except subprocess.TimeoutExpired:
        _log(cmd, "timeout", -1)
        return {"ok": False, "error": "таймаут %d с" % timeout}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


def screenshot(path=None):
    """Снимок рабочего стола. Возврат: {ok, path} или ошибка."""
    if not enabled():
        return {"ok": False, "error": "терминал выключен"}
    # v3.150: снимок — через desktop.py (Windows: Pillow/PowerShell); отдаётся
    # только владельцу (/api/desktop?shot=…), а не через общий /api/files
    if path is None:
        import desktop
        r = desktop.save_shot()
        if r.get("ok"):
            r["url"] = "/api/desktop?shot=" + r["file"]
        return r
    os.makedirs(os.path.dirname(path), exist_ok=True)
    for tool in (["scrot", path], ["import", "-window", "root", path],
                 ["spectacle", "-b", "-n", "-o", path]):
        try:
            subprocess.run(tool, timeout=8, capture_output=True)
            if os.path.isfile(path) and os.path.getsize(path) > 100:
                return {"ok": True, "path": path,
                        "url": "/api/files/get/" + os.path.basename(path)}
        except Exception:
            continue
    return {"ok": False, "error": "не найден инструмент снимка (scrot/import/spectacle)"}


def rotate_mac(iface="en0", key_path=None, host="localhost"):
    """Сменить MAC-адрес интерфейса. На Mac требует вход по ключу.
    key_path — путь к приватному ключу SSH. host — куда подключаться."""
    if not enabled():
        return {"ok": False, "error": "терминал выключен"}
    # генерируем случайный MAC
    import random
    mac = "02:%02x:%02x:%02x:%02x:%02x" % tuple(random.randint(0, 255) for _ in range(5))
    # команда для macOS
    cmd = "sudo ifconfig %s ether %s" % (iface, mac)
    if host and host != "localhost":
        ssh = "ssh -o StrictHostKeyChecking=no -o BatchMode=yes"
        if key_path:
            ssh += " -i %s" % key_path
        cmd = "%s %s '%s'" % (ssh, host, cmd)
    r = run(cmd, timeout=20)
    r["mac"] = mac
    r["iface"] = iface
    return r


def history(limit=30):
    try:
        return (store.load_json(_LOG, []) or [])[-limit:]
    except Exception:
        return []
