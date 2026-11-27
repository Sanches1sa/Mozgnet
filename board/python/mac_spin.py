# -*- coding: utf-8 -*-
"""mac_spin.py — v2.113: СИНХРОННОЕ ВРАЩЕНИЕ MAC ДВУХ ПЛАТ («позвонить плате»).

Облако (spin.py) заводит сессию между двумя платами: общий seed, общее время
старта и ОДИН период. Этот модуль вращает MAC платы в ритме сессии:

    idx = floor((cloud_now - start) / period)        # общий для обеих плат
    mac = mac_for(seed, board_id, idx)               # детерминированный адрес

Обе платы считают idx от ОБЩЕГО облачного времени и одного периода — значит
меняют адрес синхронно (одна скорость вращения). Длится не дольше 38 минут
(жёсткий предел здесь и в облаке), потом возвращается заводской MAC.

На каждом шаге плата сообщает облаку свой MAC, шаг и IP (report) — это скрытый
канал между платами и облаками и предсказание IP (адрес меняется вместе с MAC).
Больше за это время плата ничего не делает.

Смена MAC требует прав root. Если `sudo -n` недоступен — «виртуальный» режим:
адрес НЕ меняется, но ритм и объявление адресов идут (видно в облаке). Скрипт
не тронул реальную сеть — честно пишем это в лог и в отчёт.

Запуск вручную (отладка):
    python3 mac_spin.py <seed> <start_unix> <period> <end_unix> <board_id>
"""
import hashlib
import os
import re
import subprocess
import sys
import time

HARD_LIMIT = 38 * 60                 # не дольше 38 минут, что бы ни прислало облако
_SKIP = re.compile(r"^(lo|docker|veth|br-|virbr|tun|tap|wg|zt|sit)", re.I)


def mac_for(seed, board, idx):
    """Тот же адрес, что считает облако (spin.mac_for)."""
    h = hashlib.sha256(("%s|%s|%d" % (seed, board, int(idx))).encode()).digest()
    o = list(h[:6])
    o[0] = (o[0] & 0xfe) | 0x02       # локально заданный, одноадресный
    return ":".join("%02x" % x for x in o)


def iface():
    """Основной сетевой интерфейс платы (не lo/docker/veth), у которого есть MAC."""
    base = "/sys/class/net"
    best = None
    try:
        for name in sorted(os.listdir(base)):
            if _SKIP.match(name):
                continue
            try:
                with open(os.path.join(base, name, "address")) as f:
                    mac = f.read().strip()
                with open(os.path.join(base, name, "operstate")) as f:
                    state = f.read().strip()
            except OSError:
                continue
            if not mac or mac == "00:00:00:00:00:00":
                continue
            if state == "up":
                return name
            best = best or name
    except OSError:
        pass
    return best


def cur_mac(ifc):
    try:
        with open("/sys/class/net/%s/address" % ifc) as f:
            return f.read().strip()
    except OSError:
        return ""


def my_ip():
    """IP платы в домашней сети (для предсказания адреса напарницей)."""
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        try:
            return socket.gethostbyname(socket.gethostname())
        except Exception:
            return ""


_sudo = None


def have_sudo():
    global _sudo
    if _sudo is None:
        try:
            r = subprocess.run(["sudo", "-n", "true"], capture_output=True, timeout=8)
            _sudo = r.returncode == 0
        except Exception:
            _sudo = False
    return _sudo


def _run(cmd, to=20):
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=to)
        return r.returncode, (r.stdout + r.stderr).decode("utf-8", "replace")
    except Exception as e:
        return 1, str(e)[:120]


def _has_nm():
    try:
        return subprocess.run(["nmcli", "-v"], capture_output=True, timeout=6).returncode == 0
    except Exception:
        return False


def _nm_con(ifc):
    rc, out = _run(["nmcli", "-g", "GENERAL.CONNECTION", "device", "show", ifc], 8)
    con = out.strip().splitlines()[0].strip() if out.strip() else ""
    rc2, typ = _run(["nmcli", "-g", "GENERAL.TYPE", "device", "show", ifc], 8)
    typ = typ.strip().splitlines()[0].strip() if typ.strip() else ""
    key = {"wifi": "802-11-wireless.cloned-mac-address", "ethernet": "802-3-ethernet.cloned-mac-address"}.get(typ, "")
    return con, key


def set_mac(ifc, mac):
    """Сменить MAC интерфейса через `sudo -n`. -> (ok, how). Связь на миг рвётся —
    это и есть смысл вращения; интерфейс переподключается с новым адресом.
    Главный путь — NetworkManager (cloned-mac-address, его NM не откатывает);
    запасной — `ip link`. Всё под `sudo -n`: соединение запускается с правами root
    без пароля (sudoers ставит лаунчер при звонке)."""
    if not have_sudo():
        return False, "no-sudo"
    # 1) NetworkManager: самый надёжный — NM сам переподключит с клон-MAC и не откатит его
    if _has_nm():
        con, key = _nm_con(ifc)
        if con and key:
            rc, out = _run(["sudo", "-n", "nmcli", "connection", "modify", con, key, mac], 15)
            if rc == 0:
                rc2, _ = _run(["sudo", "-n", "nmcli", "-w", "25", "connection", "up", con], 30)
                if rc2 == 0:
                    return True, "nmcli cloned-mac"
                # интерфейс перезапускается — иногда up возвращает ненулевой код, но адрес сменился
                return True, "nmcli cloned-mac (up async)"
    # 2) запасной путь — ip link
    for c in (["sudo", "-n", "ip", "link", "set", "dev", ifc, "down"],
              ["sudo", "-n", "ip", "link", "set", "dev", ifc, "address", mac],
              ["sudo", "-n", "ip", "link", "set", "dev", ifc, "up"]):
        rc, out = _run(c, 15)
        if rc != 0:
            return False, out.strip()[:90] or ("rc=%d" % rc)
    if _has_nm():
        _run(["sudo", "-n", "nmcli", "dev", "connect", ifc], 20)
    return True, "ip link"


def run(seed, start, period, end, board_id, report=None, log=None, stop=None, now_fn=None):
    """Вращать MAC в ритме сессии до end (но не дольше 38 минут).
    report(mac, ip, step) — отчёт облаку; log(str); stop() -> bool; now_fn() -> облачное время.
    -> сводка dict."""
    log = log or (lambda *a: None)
    report = report or (lambda *a, **k: None)
    stop = stop or (lambda: False)
    period = max(2.0, float(period))
    t_local0 = time.time()
    skew = (now_fn() if now_fn else start) - t_local0      # облачное время ≈ local + skew (если now_fn нет — от start)
    cloud_now = now_fn or (lambda: time.time() + skew)
    hard_end = t_local0 + HARD_LIMIT
    end = float(end)
    ifc = iface()
    if not ifc:
        log("MAC-спин: не нашёл сетевой интерфейс — вращать нечего")
        return {"ok": False, "error": "нет интерфейса"}
    orig = cur_mac(ifc)
    virtual = not have_sudo()
    if virtual:
        log("MAC-спин: нет прав root (sudo -n) — ВИРТУАЛЬНЫЙ режим: адрес не меняю, только держу ритм и "
            "объявляю адреса облаку. Чтобы вращалось по-настоящему, лаунчер при звонке сам ставит sudoers; "
            "вручную: «<пользователь> ALL=(root) NOPASSWD: /usr/bin/nmcli, /usr/bin/ip» в /etc/sudoers.d/neuro12-mac.")
    else:
        log("MAC-спин: интерфейс %s, заводской %s — вращаю синхронно с напарницей" % (ifc, orig))
    last_idx = -1
    changes = 0
    try:
        while not stop():
            n = cloud_now()
            if n >= end or time.time() >= hard_end:
                break
            if n < start:
                time.sleep(min(0.5, max(0.0, start - n)))
                continue
            idx = int((n - start) / period)
            if idx != last_idx:
                mac = mac_for(seed, board_id, idx)
                ok, how = (True, "virtual") if virtual else set_mac(ifc, mac)
                if ok:
                    changes += 1
                    last_idx = idx
                    try:
                        report(mac=mac, ip=my_ip(), step=idx)
                    except Exception:
                        pass
                    log("MAC-спин: шаг %d → %s%s" % (idx, mac, "" if not virtual else " (виртуально)"))
                else:
                    log("MAC-спин: шаг %d не удался (%s) — повтор" % (idx, how))
                    time.sleep(1.0)
                    continue
            # до следующего шага, но не спим дольше, чтобы вовремя остановиться
            nxt = start + (last_idx + 1) * period
            time.sleep(max(0.2, min(period, nxt - cloud_now())))
    finally:
        if not virtual and orig:
            ok, _ = set_mac(ifc, orig)
            log("MAC-спин: сессия окончена, вернул заводской MAC %s%s" % (orig, "" if ok else " (не удалось — перезагрузка платы вернёт адрес)"))
        try:
            report(mac=orig, ip=my_ip(), step=-1)
        except Exception:
            pass
    return {"ok": True, "iface": ifc, "changes": changes, "virtual": virtual}


if __name__ == "__main__":
    if len(sys.argv) < 6:
        print(__doc__)
        sys.exit(1)
    seed, start, period, end, bid = sys.argv[1], float(sys.argv[2]), float(sys.argv[3]), float(sys.argv[4]), sys.argv[5]
    print(run(seed, start, period, end, bid, log=lambda *a: print(*a)))
