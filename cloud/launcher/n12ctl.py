#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""n12ctl — командный лаунчер «Star Gate» (только stdlib, без графики).

Два профиля одним файлом:
  neuro12-cli     — Linux-компьютер: start/stop/restart/status/logs/open/doctor
                    (облако запускается обычным процессом пользователя).
  neuro12-server  — сервер: то же, но через systemd-службу neuro12-cloud
                    (install/uninstall/enable/disable, backup, firewall).
Обновлятор с ОДНОГО компьютера — общий для обоих:
  neuro12-cli update --bundle ПАПКА_С_АРХИВАМИ [--fleet fleet.json] [--only cloud,board]
                     [--dry-run] [--force] [--auto-rollback] [--jobs N]
Он сам определяет, какой zip — облако, плата, лаунчер (по содержимому), читает список машин
(fleet.json) и обновляет каждую: облако — локально, по SSH или по HTTP (пароль панели);
плату — по SSH штатным n12_deploy.sh; бинарник лаунчера — локально/по SSH; Android-приложение —
adb install -r. После обновления проверяет версию по /api/health и при желании откатывает.
Честно: логика проверена тестами с подменой ssh/scp/adb; на живых машинах и плате не запускалась."""
import argparse
import concurrent.futures as cf
import http.cookiejar
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
RES = os.path.join(HERE, "n12res")
SERVICE = "neuro12-cloud"
CFG_DIR = os.path.join(os.path.expanduser("~"), ".config", "neuro12")
FLEET_DEFAULT = os.path.join(CFG_DIR, "fleet.json")
SSH_OPTS = ["-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new", "-o", "ConnectTimeout=8",
            "-o", "ServerAliveInterval=15"]


def say(*a):
    print(*a, flush=True)


# ------------------------------ окружение ----------------------------------
def find_home(explicit=""):
    cands = [explicit, os.environ.get("N12_HOME", ""), os.path.dirname(HERE),
             os.path.expanduser("~/.local/share/neuro12-cloud"), "/opt/neuro12-cloud"]
    for c in cands:
        if c and os.path.isfile(os.path.join(c, "server", "app.py")):
            return os.path.abspath(c)
    return ""


def read_version(home):
    try:
        return open(os.path.join(home, "VERSION"), encoding="utf-8").read().strip()
    except OSError:
        return ""


def data_dir(home):
    return os.environ.get("NEURO_DATA") or os.path.join(home, "data")


def port_of(home):
    try:
        return int(open(os.path.join(data_dir(home), "port.txt")).read().strip())
    except Exception:
        return int(os.environ.get("NEURO_PORT") or 8077)


def health(url, timeout=3):
    try:
        with urllib.request.urlopen(url.rstrip("/") + "/api/health", timeout=timeout) as r:
            d = json.loads(r.read().decode("utf-8", "replace"))
        return d if d.get("ok") else None
    except Exception:
        return None


def run(cmd, timeout=120, inp=None):
    """-> (rc, вывод). Без shell: список аргументов."""
    try:
        r = subprocess.run(cmd, input=inp, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout)
        return r.returncode, r.stdout.decode("utf-8", "replace")
    except subprocess.TimeoutExpired:
        return -9, "таймаут %d с" % timeout
    except OSError as e:
        return -1, "%s: %s" % (type(e).__name__, e)


def have(x):
    return shutil.which(x) is not None


def service_installed():
    if not have("systemctl"):
        return False
    rc, out = run(["systemctl", "cat", SERVICE], 10)
    return rc == 0


def sctl(action):
    cmd = ["systemctl", action, SERVICE]
    if os.geteuid() != 0 and have("sudo"):
        cmd = ["sudo", "-n"] + cmd
    return run(cmd, 60)


# ------------------------------ запуск/стоп --------------------------------
def _alive(pid):
    try:
        with open("/proc/%d/stat" % pid, "rb") as f:
            d = f.read().decode("utf-8", "replace")
        return d[d.rindex(")") + 2] != "Z"
    except FileNotFoundError:
        return False
    except Exception:
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False


def pid_file(home):
    return os.path.join(data_dir(home), "n12ctl.pid")


def log_file(home):
    return os.path.join(data_dir(home), "n12ctl.log")


def read_pid(home):
    try:
        pid = int(open(pid_file(home)).read().strip())
        os.kill(pid, 0)
        return pid
    except Exception:
        return 0


def wait_health(url, seconds, ver=""):
    t0 = time.time()
    while time.time() - t0 < seconds:
        h = health(url, 2)
        if h and (not ver or h.get("version") == ver) and not h.get("starting"):
            return h
        time.sleep(1.5)
    return None


def use_systemd(profile):
    if not service_installed():
        return False
    if profile == "server":
        return True
    rc, out = run(["systemctl", "is-active", SERVICE], 10)
    return rc == 0


def find_pids(home):
    """PID серверов облака этой папки (процесс python с .../server/app.py и cwd = home) — по /proc."""
    out = []
    want = os.path.realpath(home)
    try:
        names = os.listdir("/proc")
    except OSError:
        return out
    for n in names:
        if not n.isdigit() or int(n) == os.getpid():
            continue
        try:
            cmd = open("/proc/%s/cmdline" % n, "rb").read().split(b"\0")
            if not any(c.endswith(b"server/app.py") for c in cmd[:4]):
                continue
            if os.path.realpath("/proc/%s/cwd" % n) == want:
                out.append(int(n))
        except OSError:
            continue
    return out


def cmd_start(a, profile):
    home = a.home_
    url = "http://127.0.0.1:%d" % port_of(home)
    h = health(url)
    if h:
        say("Уже работает: версия %s, %s" % (h.get("version"), url))
        return 0
    if use_systemd(profile):
        rc, out = sctl("start")
        if rc:
            say("systemctl start не удался: %s" % out.strip()[:300])
            return 1
    else:
        os.makedirs(data_dir(home), exist_ok=True)
        env = dict(os.environ, NEURO_DATA=data_dir(home))
        lf = open(log_file(home), "ab")
        p = subprocess.Popen([sys.executable, os.path.join(home, "server", "app.py")], cwd=home, env=env,
                             stdin=subprocess.DEVNULL, stdout=lf, stderr=lf, start_new_session=True)
        open(pid_file(home), "w").write(str(p.pid))
    h = wait_health(url, 40)
    if not h:
        say("Не поднялось за 40 с. Смотрите: %s logs" % os.path.basename(sys.argv[0]))
        return 1
    say("Запущено: версия %s, %s" % (h.get("version"), url))
    return 0


def cmd_stop(a, profile):
    home = a.home_
    if use_systemd(profile):
        rc, out = sctl("stop")
        say("Служба остановлена." if rc == 0 else "systemctl stop: %s" % out.strip()[:300])
        return rc
    pids = sorted(set(([read_pid(home)] if read_pid(home) else []) + find_pids(home)))
    if not pids:
        say("Процесса облака этой папки нет.")
        return 0
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
    for _ in range(40):
        time.sleep(0.5)
        pids = [p for p in pids if _alive(p)]
        if not pids:
            break
    for pid in pids:
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
    try:
        os.remove(pid_file(home))
    except OSError:
        pass
    say("Остановлено.")
    return 0


def cmd_status(a, profile):
    home = a.home_
    url = "http://127.0.0.1:%d" % port_of(home)
    h = health(url)
    info = {"home": home, "version_on_disk": read_version(home), "url": url, "running": bool(h),
            "version_running": (h or {}).get("version"), "uptime_s": (h or {}).get("uptime"),
            "mode": "systemd" if service_installed() else ("pid %d" % read_pid(home) if read_pid(home) else "—")}
    if a.json:
        say(json.dumps(info, ensure_ascii=False, indent=1))
    else:
        say("Папка:      %s\nВерсия:     на диске %s, запущена %s\nАдрес:      %s\nСостояние:  %s (%s)"
            % (home, info["version_on_disk"], info["version_running"] or "—", url,
               "работает" if h else "не отвечает", info["mode"]))
    return 0 if h else 3


def cmd_logs(a, profile):
    home = a.home_
    if profile == "server" and service_installed() and have("journalctl"):
        cmd = ["journalctl", "-u", SERVICE, "-n", str(a.n)] + (["-f"] if a.follow else [])
        os.execvp(cmd[0], cmd)
    lf = log_file(home)
    if not os.path.isfile(lf):
        lf = os.path.join(data_dir(home), "log.txt")
    if not os.path.isfile(lf):
        say("Журнала нет.")
        return 1
    if a.follow:
        os.execvp("tail", ["tail", "-n", str(a.n), "-f", lf])
    say("".join(open(lf, encoding="utf-8", errors="replace").readlines()[-a.n:]), end="")
    return 0


def cmd_doctor(a, profile):
    home = a.home_
    bad = 0

    def line(ok, msg):
        nonlocal bad
        bad += 0 if ok else 1
        say(("✅ " if ok else "❌ ") + msg)
    v = sys.version_info
    line(v >= (3, 8), "python %d.%d.%d (нужен ≥ 3.8)" % v[:3])
    line(bool(home), "папка облака: %s" % (home or "не найдена (N12_HOME или --home)"))
    if home:
        d = data_dir(home)
        os.makedirs(d, exist_ok=True)
        line(os.access(d, os.W_OK), "данные доступны на запись: %s" % d)
        free = shutil.disk_usage(d).free // (1 << 20)
        line(free > 500, "свободно места: %d МБ" % free)
        p = port_of(home)
        s = socket.socket()
        s.settimeout(1)
        busy = s.connect_ex(("127.0.0.1", p)) == 0
        s.close()
        line(True, "порт %d: %s" % (p, "занят (облако работает?)" if busy else "свободен"))
    for t, why in (("ssh", "обновление по SSH"), ("scp", "копирование по SSH"), ("adb", "Android-приложение"),
                   ("systemctl", "служба (сервер)"), ("curl", "ручные проверки")):
        ok = have(t)
        say(("✅ " if ok else "➖ ") + "%s — %s" % (t, why if ok else "нет (нужен для: %s)" % why))
    say("fleet: %s" % (FLEET_DEFAULT if os.path.isfile(FLEET_DEFAULT) else "нет — создайте: update --init-fleet"))
    return 1 if bad else 0


def cmd_crashes(a, profile):
    """Что и когда падало: crashes.json, прерванные запросы, блокировки, хвост crash.log — без пароля, из файлов данных."""
    d = data_dir(a.home_)

    def rj(n, default):
        try:
            return json.load(open(os.path.join(d, n), encoding="utf-8"))
        except Exception:
            return default
    cr, rq, po = rj("crashes.json", []), rj("crash_requests.json", []), rj("poison.json", {})
    say("Падений процесса: %d (последние 5):" % len(cr))
    for e in cr[-5:]:
        say("  %s код %s%s — %s%s" % (time.strftime("%d.%m %H:%M:%S", time.localtime(e.get("ts", 0))), e.get("code"),
                                     " [безопасный режим]" if e.get("safe") else "", e.get("hint") or e.get("why") or "причина в crash.log",
                                     ("; обрабатывал: " + "; ".join("%s %s" % (x.get("path"), str(x.get("text") or "")[:50]) for x in e.get("inflight") or [])) if e.get("inflight") else ""))
    say("Оборванных запросов: %d (последние 5):" % len(rq))
    for e in rq[-5:]:
        say("  %s %s %s %s" % (time.strftime("%d.%m %H:%M:%S", time.localtime(e.get("ts") or 0)), e.get("path"), "(плановый)" if e.get("planned") else "", str(e.get("text") or "")[:80]))
    bl = [k for k, v in po.items() if int(v.get("n") or 0) >= 2]
    say("Заблокированных запросов: %d (снять: панель → POST /api/crash/report {clear:true})" % len(bl))
    lf = os.path.join(d, "crash.log")
    if os.path.isfile(lf):
        say("--- хвост crash.log ---")
        say("".join(open(lf, encoding="utf-8", errors="replace").readlines()[-25:]), end="")
    return 0


def cmd_open(a, profile):
    url = "http://127.0.0.1:%d" % port_of(a.home_)
    say(url)
    for o in ("xdg-open", "gio"):
        if have(o) and os.environ.get("DISPLAY"):
            subprocess.Popen([o] + (["open"] if o == "gio" else []) + [url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            break
    return 0


# ------------------------------ сервер: служба -----------------------------
def cmd_install(a, profile):
    sh = os.path.join(a.home_, "install.sh")
    if not os.path.isfile(sh):
        say("Нет install.sh рядом с облаком.")
        return 1
    cmd = ["bash", sh] + a.rest
    if os.geteuid() != 0:
        cmd = ["sudo"] + cmd
    say("Запускаю штатный установщик: " + " ".join(cmd))
    return subprocess.call(cmd)


def cmd_uninstall(a, profile):
    sh = os.path.join(a.home_, "uninstall.sh")
    cmd = ["bash", sh] + a.rest
    if os.geteuid() != 0:
        cmd = ["sudo"] + cmd
    return subprocess.call(cmd)


def cmd_enable(a, profile):
    rc, out = sctl("enable" if a.cmd == "enable" else "disable")
    say(out.strip() or ("ок" if rc == 0 else "не вышло"))
    return rc


def cmd_backup(a, profile):
    home = a.home_
    out = a.out or os.path.join(os.path.expanduser("~"), "neuro12-data-%s.tar.gz" % time.strftime("%Y%m%d-%H%M%S"))
    if health("http://127.0.0.1:%d" % port_of(home)):
        say("Облако работает — копия будет «на лету»; для точной копии сначала остановите (stop).")
    with tarfile.open(out, "w:gz") as t:
        t.add(data_dir(home), arcname="data")
    os.chmod(out, 0o600)
    say("Копия данных: %s (%d КБ)" % (out, os.path.getsize(out) // 1024))
    return 0


def cmd_firewall(a, profile):
    p = port_of(a.home_)
    sudo = [] if os.geteuid() == 0 else ["sudo"]
    if have("ufw"):
        c = ["ufw", "status"] if a.action == "status" else sudo + ["ufw", "allow", "%d/tcp" % p]
        after = None
    elif have("firewall-cmd"):
        c = ["firewall-cmd", "--state"] if a.action == "status" else sudo + ["firewall-cmd", "--permanent", "--add-port=%d/tcp" % p]
        after = None if a.action == "status" else sudo + ["firewall-cmd", "--reload"]
    else:
        say("ufw/firewalld не найдены — откройте порт %d/tcp средствами системы." % p)
        return 1
    rc, out = run(c, 30)
    if rc == 0 and after:
        run(after, 30)
    say(out.strip())
    return rc


# =============================== ОБНОВЛЯТОР ================================
def _vkey(v):
    return tuple(int(x) for x in re.findall(r"\d+", str(v))[:4]) or (0,)


def classify(path):
    """Файл/zip -> {kind, version, path} | None."""
    try:
        if os.path.basename(path).startswith("neuro12-start-linux-"):
            return {"kind": "launcher", "version": "", "path": path, "arch": path.rsplit("-", 1)[-1]}
        if path.endswith(".apk"):
            return {"kind": "app", "version": "", "path": path}
        if not zipfile.is_zipfile(path):
            return None
        z = zipfile.ZipFile(path)
        names = z.namelist()
        app = [n for n in names if n.endswith("server/app.py")]
        if app:
            pre = app[0][:-len("server/app.py")]
            try:
                ver = z.read(pre + "VERSION").decode().strip()
            except KeyError:
                ver = ""
            return {"kind": "cloud", "version": ver, "path": path}
        mp = [n for n in names if n.endswith("python/main.py")]
        if mp and any(n.endswith("app.yaml") for n in names):
            m = re.search(r'APP_VERSION\s*=\s*"([^"]+)"', z.read(mp[0]).decode("utf-8", "replace"))
            return {"kind": "board", "version": m.group(1) if m else "", "path": path}
    except Exception:
        return None
    return None


def scan_bundle(paths):
    """Лучшие пакеты каждого вида: {cloud, board, launcher:{arch:..}, app}."""
    files = []
    for p in paths:
        p = os.path.expanduser(p)
        if os.path.isdir(p):
            files += [os.path.join(p, f) for f in sorted(os.listdir(p))]
        elif os.path.isfile(p):
            files.append(p)
    best = {}
    for f in files:
        c = classify(f)
        if not c:
            continue
        key = c["kind"] + (":" + c["arch"] if c["kind"] == "launcher" else "")
        if key not in best or _vkey(c["version"]) > _vkey(best[key]["version"]) or \
                (c["version"] == best[key]["version"] and os.path.getmtime(f) > os.path.getmtime(best[key]["path"])):
            best[key] = c
    return best


def secret(v):
    """«env:ИМЯ» -> значение из окружения; иначе как есть."""
    if isinstance(v, str) and v.startswith("env:"):
        return os.environ.get(v[4:], "")
    return v or ""


def ssh_base(t):
    a = list(SSH_OPTS)
    if t.get("port"):
        a += ["-p", str(t["port"])]
    if t.get("key"):
        a += ["-i", os.path.expanduser(t["key"])]
    return a


def ssh_target(t):
    return "%s@%s" % (t["user"], t["host"]) if t.get("user") else t["host"]


def ssh_run(t, remote, timeout=600):
    return run(["ssh"] + ssh_base(t) + [ssh_target(t), remote], timeout)


def scp_put(t, srcs, dst, timeout=900, recursive=False):
    a = ["scp"] + [("-P" if x == "-p" else x) for x in ssh_base(t)]      # у scp порт — -P
    if recursive:
        a.append("-r")
    return run(a + list(srcs) + ["%s:%s" % (ssh_target(t), dst)], timeout)


def shq(s):
    return "'" + str(s).replace("'", "'\\''") + "'"


def http_session(base, password):
    cj = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    req = urllib.request.Request(base.rstrip("/") + "/api/auth/login", json.dumps({"password": password}).encode(),
                                 {"Content-Type": "application/json"})
    with op.open(req, timeout=15) as r:
        r.read()
    return op


def http_post(op, url, body, ctype, timeout=300):
    req = urllib.request.Request(url, body, {"Content-Type": ctype})
    with op.open(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def cloud_url(t):
    if t.get("url"):
        return t["url"].rstrip("/")
    if t.get("host"):
        return "http://%s:%s" % (t["host"], t.get("cloud_port") or 8077)
    return "http://127.0.0.1:8077"


class Result(dict):
    pass


def res(t, ok, msg, **kw):
    r = Result(name=t.get("name"), type=t.get("type"), ok=ok, msg=msg)
    r.update(kw)
    return r


def _parse_json_tail(out):
    m = re.search(r"\{.*\}\s*$", out, re.S)
    try:
        return json.loads(m.group(0)) if m else {}
    except ValueError:
        return {}


def update_cloud(t, pkg, opts):
    via = t.get("via") or ("ssh" if t.get("host") else "local")
    new = pkg["version"]
    url = cloud_url(t)
    cur = (health(url) or {}).get("version")
    if cur and cur == new and not opts.force:
        return res(t, True, "уже %s — пропускаю" % new, skipped=True)
    if opts.dry_run:
        return res(t, True, "[план] облако %s -> %s через %s" % (cur or "?", new, via), planned=True)
    backup = ""
    if via == "local":
        home = os.path.expanduser(t.get("path") or "")
        rc, out = run([sys.executable, os.path.join(home, "server", "updater.py"), "apply", pkg["path"]], 300)
        if rc:
            return res(t, False, "updater: %s" % out.strip()[-300:])
        backup = _parse_json_tail(out).get("backup", "")
        rc, out = run([sys.executable, os.path.join(home, "launcher", "n12ctl.py"), "--home", home, "restart"], 150)
        if rc:
            return res(t, False, "перезапуск: %s" % out.strip()[-300:], backup=backup)
    elif via == "ssh":
        tmp = "/tmp/n12-update-%d.zip" % int(time.time())
        rc, out = scp_put(t, [pkg["path"]], tmp)
        if rc:
            return res(t, False, "scp: %s" % out.strip()[-300:])
        rc, out = ssh_run(t, "cd %s && python3 server/updater.py apply %s; rc=$?; rm -f %s; exit $rc" % (
            shq(t["path"]), shq(tmp), shq(tmp)), 300)
        if rc:
            return res(t, False, "updater на %s: %s" % (t["host"], out.strip()[-300:]))
        backup = _parse_json_tail(out).get("backup", "")
        rc, out = ssh_run(t, "cd %s && N12_PROFILE=%s python3 launcher/n12ctl.py --home %s restart" % (
            shq(t["path"]), shq(t.get("profile") or "desktop"), shq(t["path"])), 200)
        if rc:
            return res(t, False, "перезапуск на %s: %s" % (t["host"], out.strip()[-300:]), backup=backup)
    elif via == "http":
        pw = secret(t.get("password"))
        if not pw:
            return res(t, False, "нет пароля панели (password: \"env:ИМЯ\")")
        try:
            op = http_session(url, pw)
            r = http_post(op, url + "/api/update/upload", open(pkg["path"], "rb").read(), "application/zip")
        except Exception as e:
            return res(t, False, "HTTP: %s: %s" % (type(e).__name__, e))
        if not r.get("ok"):
            return res(t, False, "облако отклонило пакет: %s" % r.get("error"))
        backup = (r.get("update") or {}).get("backup", "")
    else:
        return res(t, False, "неизвестный via: %s" % via)
    h = wait_health(url, opts.wait, new)
    if h:
        return res(t, True, "%s -> %s" % (cur or "?", new), backup=backup, version=new)
    if opts.auto_rollback and backup:
        ok, msg = rollback_cloud(t, backup, via, url)
        return res(t, False, "новая версия не поднялась за %d с; откат %s: %s" % (opts.wait, "выполнен" if ok else "НЕ вышел", msg), backup=backup)
    return res(t, False, "версия %s не подтвердилась за %d с; копия для отката: %s" % (new, opts.wait, backup or "—"), backup=backup)


def rollback_cloud(t, backup, via, url):
    if via == "local":
        home = os.path.expanduser(t["path"])
        rc, out = run([sys.executable, os.path.join(home, "server", "updater.py"), "rollback", backup], 300)
        if rc == 0:
            rc, out = run([sys.executable, os.path.join(home, "launcher", "n12ctl.py"), "--home", home, "restart"], 150)
    elif via == "ssh":
        rc, out = ssh_run(t, "cd %s && python3 server/updater.py rollback %s && N12_PROFILE=%s python3 launcher/n12ctl.py --home %s restart" % (
            shq(t["path"]), shq(backup), shq(t.get("profile") or "desktop"), shq(t["path"])), 400)
    else:
        try:
            op = http_session(url, secret(t.get("password")))
            r = http_post(op, url + "/api/update/rollback", json.dumps({"backup": backup}).encode(), "application/json")
            rc, out = (0 if r.get("ok") else 1), json.dumps(r, ensure_ascii=False)[:200]
        except Exception as e:
            rc, out = 1, str(e)
    return rc == 0, out.strip()[-150:]


def stage_board(zip_path, work):
    """Распаковать приложение платы в work/neuro_12.new (app.yaml, python, sketch, assets)."""
    z = zipfile.ZipFile(zip_path)
    ym = [n for n in z.namelist() if n.endswith("app.yaml")]
    pre = ym[0][:-len("app.yaml")]
    dst = os.path.join(work, "neuro_12.new")
    for n in z.namelist():
        if not n.startswith(pre) or n.endswith("/"):
            continue
        rel = n[len(pre):]
        top = rel.split("/")[0]
        if top not in ("app.yaml", "python", "sketch", "assets"):
            continue
        if ".." in rel.split("/"):
            continue
        p = os.path.join(dst, *rel.split("/"))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as f:
            f.write(z.read(n))
    return dst


def update_board(t, pkg, opts):
    new = pkg["version"]
    if opts.dry_run:
        return res(t, True, "[план] плата %s -> %s по SSH (n12_deploy.sh, сборка на плате несколько минут)" % (t.get("host"), new), planned=True)
    cu, tok = t.get("cloud_url") or "", secret(t.get("token"))
    if not cu or not tok:
        return res(t, False, "у платы нужны cloud_url и token (token: \"env:ИМЯ\")")
    work = tempfile.mkdtemp(prefix="n12board_")
    try:
        app = stage_board(pkg["path"], work)
        for f in ("n12_deploy.sh", "n12_setcfg.py"):
            shutil.copy(os.path.join(RES, f), os.path.join(work, f))
        rc, out = ssh_run(t, "mkdir -p ~/ArduinoApps && rm -rf ~/ArduinoApps/neuro_12.new && echo ok", 40)
        if rc or "ok" not in out:
            return res(t, False, "плата не ответила по SSH: %s" % out.strip()[-200:])
        rc, out = scp_put(t, [app, os.path.join(work, "n12_setcfg.py"), os.path.join(work, "n12_deploy.sh")], "ArduinoApps/", 900, recursive=True)
        if rc:
            return res(t, False, "копирование на плату: %s" % out.strip()[-300:])
        rc, out = ssh_run(t, "bash ~/ArduinoApps/n12_deploy.sh %s %s" % (shq(cu), shq(tok)), 1800)
        tail = out.strip()[-400:]
        if rc:
            return res(t, False, "n12_deploy.sh завершился с ошибкой: %s" % tail)
        return res(t, True, "приложение %s залито, сборка и запуск выполнены на плате (проверьте её веб-страницу :7000)" % new, version=new, log_tail=tail)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def update_launcher(t, pkg, opts):
    via = t.get("via") or ("ssh" if t.get("host") else "local")
    if opts.dry_run:
        return res(t, True, "[план] бинарник лаунчера -> %s (%s)" % (t.get("path"), via), planned=True)
    dst = os.path.expanduser(t["path"]) if via == "local" else t["path"]
    if via == "local":
        tmp = dst + ".new"
        shutil.copy(pkg["path"], tmp)
        os.chmod(tmp, 0o755)
        os.replace(tmp, dst)
        return res(t, True, "заменён %s" % dst)
    rc, out = scp_put(t, [pkg["path"]], t["path"] + ".new")
    if rc:
        return res(t, False, "scp: %s" % out.strip()[-200:])
    rc, out = ssh_run(t, "chmod 755 %s && mv -f %s %s" % (shq(t["path"] + ".new"), shq(t["path"] + ".new"), shq(t["path"])), 30)
    return res(t, rc == 0, "заменён %s на %s" % (t["path"], t["host"]) if rc == 0 else out.strip()[-200:])


def update_app(t, pkg, opts):
    apk = os.path.expanduser(t.get("apk") or pkg["path"])
    if opts.dry_run:
        return res(t, True, "[план] adb install -r %s (%s)" % (os.path.basename(apk), t.get("serial") or "единственное устройство"), planned=True)
    cmd = ["adb"] + (["-s", t["serial"]] if t.get("serial") else []) + ["install", "-r", apk]
    rc, out = run(cmd, 300)
    return res(t, rc == 0 and "Success" in out, out.strip()[-200:] or "adb без вывода")


HANDLERS = {"cloud": update_cloud, "board": update_board, "launcher": update_launcher, "app": update_app}
ORDER = ["cloud", "board", "launcher", "app"]       # облако — первым: плата берёт конфиг у облака


def load_fleet(path):
    d = json.load(open(os.path.expanduser(path), encoding="utf-8"))
    ts = d.get("targets") if isinstance(d, dict) else d
    if not isinstance(ts, list):
        raise ValueError("fleet.json: нужен список targets")
    bad = [t for t in ts if not isinstance(t, dict) or t.get("type") not in HANDLERS]
    if bad:
        raise ValueError("неизвестный type у: %s" % bad[0])
    return ts


FLEET_EXAMPLE = {"targets": [
    {"name": "этот компьютер", "type": "cloud", "via": "local", "path": "~/.local/share/neuro12-cloud", "url": "http://127.0.0.1:8077"},
    {"name": "VPS", "type": "cloud", "via": "ssh", "host": "203.0.113.10", "user": "root", "path": "/opt/neuro12-cloud", "url": "http://203.0.113.10:8077"},
    {"name": "облако по HTTP", "type": "cloud", "via": "http", "url": "http://192.168.1.20:8077", "password": "env:N12_PASS_LAN"},
    {"name": "UNO Q", "type": "board", "host": "192.168.1.50", "user": "arduino", "cloud_url": "http://192.168.1.10:8077", "token": "env:N12_BOARD_TOKEN"},
    {"name": "лаунчер на ноутбуке", "type": "launcher", "via": "ssh", "host": "192.168.1.30", "user": "me", "path": "/home/me/.local/bin/neuro12-start", "arch": "amd64"},
    {"name": "телефон", "type": "app", "serial": "R58M123ABC", "apk": "~/neuro12.apk"}]}


def cmd_update(a, profile):
    if a.init_fleet:
        os.makedirs(CFG_DIR, exist_ok=True)
        p = a.fleet or FLEET_DEFAULT
        if os.path.exists(p):
            say("Уже есть: %s" % p)
            return 1
        with open(p, "w", encoding="utf-8") as f:
            json.dump(FLEET_EXAMPLE, f, ensure_ascii=False, indent=1)
        os.chmod(p, 0o600)
        say("Создан образец %s — поправьте под свои машины. Пароли и токены: \"env:ИМЯ\"." % p)
        return 0
    fleet_path = a.fleet or FLEET_DEFAULT
    try:
        targets = load_fleet(fleet_path)
    except Exception as e:
        say("Список машин: %s (%s). Создать образец: update --init-fleet" % (e, fleet_path))
        return 2
    if os.path.isfile(fleet_path) and os.stat(fleet_path).st_mode & 0o077:
        say("⚠ %s читаем другими пользователями; chmod 600." % fleet_path)
    bundle = scan_bundle(a.bundle or ["."])
    if not bundle:
        say("В --bundle нет пакетов облака/платы/лаунчера/apk.")
        return 2
    say("Пакеты: " + "; ".join("%s %s" % (k, v["version"] or os.path.basename(v["path"])) for k, v in sorted(bundle.items())))
    only = set(x.strip() for x in (a.only or "").split(",") if x.strip())
    names = set(x.strip() for x in (a.names or "").split(",") if x.strip())
    plan = []
    for kind in ORDER:
        for t in targets:
            if t["type"] != kind or (only and kind not in only) or (names and t.get("name") not in names):
                continue
            key = kind + (":" + (t.get("arch") or "amd64") if kind == "launcher" else "")
            if key not in bundle:
                plan.append((t, None))
                continue
            plan.append((t, bundle[key]))
    results = []
    for kind in ORDER:                       # виды — по порядку, внутри вида машины параллельно
        batch = [(t, p) for t, p in plan if t["type"] == kind]
        if not batch:
            continue

        def one(tp):
            t, p = tp
            if p is None:
                return res(t, False, "в --bundle нет пакета для этого вида")
            try:
                r = HANDLERS[kind](t, p, a)
            except Exception as e:
                r = res(t, False, "%s: %s" % (type(e).__name__, e))
            say(("✅" if r["ok"] else "❌") + " [%s] %s: %s" % (kind, t.get("name"), r["msg"]))
            return r
        with cf.ThreadPoolExecutor(max_workers=max(1, a.jobs)) as ex:
            results += list(ex.map(one, batch))
        if kind == "cloud" and any(not r["ok"] for r in results) and not a.keep_going:
            say("Облако обновилось с ошибкой — плату и остальное не трогаю (--keep-going, чтобы продолжить).")
            break
    ok = sum(1 for r in results if r["ok"])
    say("Итого: %d из %d успешно%s." % (ok, len(results), " (это был план, ничего не менялось)" if a.dry_run else ""))
    if a.report:
        json.dump(results, open(a.report, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return 0 if ok == len(results) and results else 1


# ------------------------------- разбор команд -----------------------------
def build_parser(profile):
    ap = argparse.ArgumentParser(prog="neuro12-" + ("server" if profile == "server" else "cli"),
                                 description="Командный лаунчер «Star Gate» (%s)" % ("сервер" if profile == "server" else "Linux"))
    ap.add_argument("--home", default="", help="папка облака (иначе N12_HOME, рядом, ~/.local/share/neuro12-cloud, /opt/neuro12-cloud)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for n, h in (("crashes", "что и когда падало (без пароля, из файлов данных)"), ("start", "запустить облако"), ("stop", "остановить"), ("restart", "перезапустить"),
                 ("doctor", "проверить окружение"), ("open", "открыть панель в браузере/показать адрес")):
        sub.add_parser(n, help=h)
    s = sub.add_parser("status", help="состояние")
    s.add_argument("--json", action="store_true")
    s = sub.add_parser("logs", help="журнал")
    s.add_argument("-n", type=int, default=60)
    s.add_argument("-f", "--follow", action="store_true")
    if profile == "server":
        for n in ("install", "uninstall"):
            s = sub.add_parser(n, help="штатный install.sh / uninstall.sh (передайте ключи после --)")
            s.add_argument("rest", nargs="*")
        sub.add_parser("enable", help="автозапуск службы")
        sub.add_parser("disable", help="выключить автозапуск")
        s = sub.add_parser("backup", help="копия данных (tar.gz)")
        s.add_argument("--out", default="")
        s = sub.add_parser("firewall", help="порт облака в файрволе")
        s.add_argument("action", choices=["status", "open"])
    u = sub.add_parser("update", help="обновить сразу ВСЕ модули с этого компьютера")
    u.add_argument("--bundle", nargs="*", help="папки/файлы с zip облака и платы, бинарниками лаунчера, apk")
    u.add_argument("--fleet", default="", help="список машин (по умолчанию %s)" % FLEET_DEFAULT)
    u.add_argument("--only", default="", help="виды через запятую: cloud,board,launcher,app")
    u.add_argument("--names", default="", help="только машины с этими именами")
    u.add_argument("--dry-run", action="store_true", help="показать план, ничего не менять")
    u.add_argument("--force", action="store_true", help="не пропускать машины с той же версией")
    u.add_argument("--auto-rollback", action="store_true", help="откатить облако, если новая версия не поднялась")
    u.add_argument("--keep-going", action="store_true", help="после сбоя облака всё равно обновлять остальное")
    u.add_argument("--wait", type=int, default=90, help="сколько секунд ждать новую версию облака")
    u.add_argument("--jobs", type=int, default=3, help="машин одного вида параллельно")
    u.add_argument("--report", default="", help="записать итог в JSON")
    u.add_argument("--init-fleet", action="store_true", help="создать образец списка машин")
    return ap


def main(argv=None, profile="desktop"):
    prof = os.environ.get("N12_PROFILE") or profile
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--" in argv:
        i = argv.index("--")
        argv = argv[:i] + argv[i + 1:]
    a = build_parser(prof).parse_args(argv)
    a.home_ = find_home(a.home)
    if a.cmd not in ("update", "doctor") and not a.home_:
        say("Папка облака не найдена: укажите --home или N12_HOME.")
        return 2
    table = {"start": cmd_start, "stop": cmd_stop, "status": cmd_status, "logs": cmd_logs, "doctor": cmd_doctor,
             "open": cmd_open, "crashes": cmd_crashes, "install": cmd_install, "uninstall": cmd_uninstall, "enable": cmd_enable,
             "disable": cmd_enable, "backup": cmd_backup, "firewall": cmd_firewall, "update": cmd_update}
    if a.cmd == "restart":
        cmd_stop(a, prof)
        return cmd_start(a, prof)
    return table[a.cmd](a, prof)


if __name__ == "__main__":
    sys.exit(main())
