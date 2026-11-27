# -*- coding: utf-8 -*-
# ===========================================================================
# updater.py — ОБНОВЛЕНИЕ ОБЛАКА ИЗ ZIP-ПАКЕТА (v3.19, только stdlib)
# ---------------------------------------------------------------------------
# Зачем: владелец скачивает zip новой версии neuro12-cloud и загружает его
# в Настройки → «Обновление». Облако само проверяет пакет, делает резервную
# копию, заменяет код и перезапускается. Каталог данных (data/) — чаты,
# база знаний, настройки, платы — НИКОГДА не перезаписывается пакетом.
#
# Как работает apply_zip():
#   1) пакет распаковывается в СТАЖ-каталог (data/update_staging), при этом
#      проверяется каждый путь (без «..», абсолютных и дисков Windows),
#      выбрасывается мусор (__pycache__, .pyc, .DS_Store, __MACOSX) и
#      ВЕСЬ data/ пакета (данные владельца не заменяются);
#   2) все server/*.py стаж-каталога проверяются компиляцией (py_compile) —
#      сломанный пакет честно отклоняется, ничего не меняется;
#   3) текущие server/, web/, VERSION, README.md и скрипты копируются в
#      data/backups/pre-<версия>-<время> (хранятся последние 5);
#   4) файлы переносятся из стажа в корень установки;
#   5) пишется data/update_state.json (по нему панель и CLI видят итог);
#   6) планируется перезапуск: помощник data/_restart.py ждёт ~1.2 с (чтобы
#      ответ успел уйти в браузер), останавливает старый процесс и стартует
#      новый (python3 server/app.py, тот же порт и тот же NEURO_DATA).
#
# CLI (когда панель недоступна):
#   python3 server/updater.py status
#   python3 server/updater.py apply neuro12-cloud-3.19.0.zip
#   python3 server/updater.py rollback pre-3.18.0-20250101-120000
#   python3 server/updater.py restart
# ===========================================================================
import json
import os
import py_compile
import re
import shutil
import subprocess
import sys
import time
import zipfile

import store

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# --- лимиты безопасности пакета --------------------------------------------
MAX_ENTRIES = 4000
MAX_UNPACKED = 256 * 1024 * 1024          # 256 МБ распакованного
BACKUP_KEEP = 5                            # сколько резервных копий хранить

_JUNK_DIRS = ("__pycache__", "__MACOSX")
_JUNK_FILES = (".DS_Store",)


class UpdErr(ValueError):
    """Честная ошибка обновления (текст показывается владельцу)."""


# ------------------------------- утилиты ------------------------------------
def install_root():
    return ROOT


def _state_path():
    return store.p("update_state.json")


def _staging_dir():
    """v3.174.1: СВОЙ стаж-каталог на каждую попытку. Раньше общий
    data/update_staging: вторая загрузка (двойной клик, повтор браузера)
    стирала его, пока первая проверяла код, — «в пакете сломанный код
    (server\\memory_save.py): [Errno 2] No such file or directory»."""
    return store.p("update_staging_%d_%d" % (os.getpid(), int(time.time() * 1000)))


_APPLY_LOCK = __import__("threading").Lock()


def _backups_dir():
    return store.p("backups")


def _safe_rel(name):
    """Путь из архива -> безопасный относительный путь внутри установки.
    Возвращает None для мусора, UpdErr для опасных путей."""
    name = str(name or "").replace("\\", "/")
    if not name or name.endswith("/"):
        return None                          # каталог
    if name.startswith("/") or ":" in name.split("/")[0] or "\x00" in name:
        raise UpdErr("опасный путь в пакете: %s" % name[:120])
    parts = [pt for pt in name.split("/") if pt not in ("", ".")]
    if any(pt == ".." for pt in parts):
        raise UpdErr("опасный путь в пакете (..): %s" % name[:120])
    if any(pt in _JUNK_DIRS for pt in parts[:-1]) or \
            os.path.basename(name) in _JUNK_FILES:
        return None
    if name.endswith(".pyc"):
        return None
    return os.path.join(*parts)


def _prefix_of(zin):
    """Пакет может лежать в корне архива (server/app.py) или в папке
    (neuro12-cloud/server/app.py) — находим префикс автоматически."""
    names = {i.filename for i in zin.infolist()}
    for pfx in ("",):
        if (pfx + "server/app.py") in names and (pfx + "VERSION") in names:
            return pfx
    cand = set()
    for n in names:
        if n.endswith("server/app.py"):
            cand.add(n[:-len("server/app.py")])
    for pfx in sorted(cand, key=len):
        if (pfx + "VERSION") in names:
            return pfx
    raise UpdErr("это не пакет neuro12-cloud: в архиве нет server/app.py "
                 "и VERSION")


def parse_zip(blob):
    """Проверить пакет БЕЗ записи на диск: префикс, список файлов, версия.
    Чистая функция — используется и тестами, и apply_zip()."""
    if not blob or len(blob) < 22:
        raise UpdErr("пустой файл — это не zip-архив")
    try:
        zin = zipfile.ZipFile(__import__("io").BytesIO(blob))
    except Exception:
        raise UpdErr("файл не читается как zip-архив")
    try:
        pfx = _prefix_of(zin)
        entries, skipped_data, unpacked = [], 0, 0
        for info in zin.infolist():
            if pfx and not info.filename.startswith(pfx):
                continue                   # v3.224: файлы вне папки пакета (отчёт агента и т.п.) не ставим
            rel = _safe_rel(info.filename[len(pfx):]
                            if info.filename.startswith(pfx)
                            else info.filename)
            if rel is None:
                continue
            first = rel.split(os.sep)[0]
            if first == "data":
                skipped_data += 1           # данные владельца не заменяем
                continue
            if info.file_size > MAX_UNPACKED:
                raise UpdErr("в пакете слишком большой файл: %s" % rel)
            unpacked += info.file_size
            entries.append((info.filename, rel))
        if len(entries) > MAX_ENTRIES:
            raise UpdErr("слишком много файлов в пакете (%d)"
                         % len(entries))
        if unpacked > MAX_UNPACKED:
            raise UpdErr("пакет слишком большой в распакованном виде "
                         "(%d МБ)" % (unpacked // (1024 * 1024)))
        to_ver = ""
        try:
            to_ver = zin.read(pfx + "VERSION").decode(
                "utf-8", "replace").strip()
        except Exception:
            pass
        if not to_ver:
            raise UpdErr("в пакете нет читаемого файла VERSION")
        return {"prefix": pfx, "entries": entries, "to": to_ver,
                "skipped_data": skipped_data, "size": len(blob)}
    finally:
        zin.close()


# -------------------------- стаж + проверка кода -----------------------------
def _stage(zin, entries, staging):
    if os.path.isdir(staging):
        shutil.rmtree(staging, ignore_errors=True)
    os.makedirs(staging, exist_ok=True)
    for name_in_zip, rel in entries:
        dest = os.path.join(staging, rel)
        base = os.path.abspath(staging)
        if not os.path.abspath(dest).startswith(base + os.sep):
            raise UpdErr("путь вне стаж-каталога: %s" % rel)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with zin.open(name_in_zip) as src, open(dest, "wb") as out:
            shutil.copyfileobj(src, out)


def _compile_zip(zin, entries):
    """v3.174.1: проверка синтаксиса server/*.py ПРЯМО ИЗ АРХИВА, в памяти.
    Файлы на диске (стаж-каталог) для проверки больше не нужны — антивирус,
    параллельная загрузка или блокировка файла Windows не могут дать ложное
    «сломанный код … No such file or directory»; применяются файлы тоже из
    архива (_apply_from)."""
    checked = 0
    for name_in_zip, rel in entries:
        parts = rel.replace("\\", "/").split("/")
        if parts[0] != "server" or not rel.endswith(".py") or "__pycache__" in parts:
            continue
        try:
            compile(zin.read(name_in_zip), rel, "exec", dont_inherit=True)
            checked += 1
        except SyntaxError as e:
            raise UpdErr("в пакете сломанный код (%s, строка %s): %s"
                         % (rel, e.lineno, str(e.msg)[:200]))
        except ValueError as e:              # нулевые байты и т. п.
            raise UpdErr("в пакете испорченный файл %s: %s" % (rel, e))
    if not checked:
        raise UpdErr("в пакете нет ни одного server/*.py для проверки")


def _compile_staged(staging, zin=None, entries=None):
    """Проверка синтаксиса всех server/*.py пакета — сломанный код не дойдёт
    до рабочей установки. v3.174.1: компилируется ТЕКСТ файла в памяти
    (compile), без записи __pycache__ рядом — антивирус и параллельная
    запись не мешают; пропавший файл называется пропавшим, а не «сломанным»."""
    srv = os.path.join(staging, "server")
    checked = 0
    for root, _dirs, files in os.walk(srv):
        if "__pycache__" in root:
            continue
        for fn in files:
            if not fn.endswith(".py"):
                continue
            path = os.path.join(root, fn)
            rel = os.path.relpath(path, staging)
            src, err = None, None
            for attempt in range(4):       # файл пропал/занят — достать заново из zip
                try:
                    with open(path, "rb") as f:
                        src = f.read()
                    break
                except OSError as e:
                    err = e
                    time.sleep(0.4 * (attempt + 1))
                    if zin is not None:
                        for name_in_zip, r_ in entries or []:
                            if os.path.normpath(r_) == os.path.normpath(rel):
                                try:
                                    os.makedirs(os.path.dirname(path), exist_ok=True)
                                    with zin.open(name_in_zip) as zs:
                                        src = zs.read()
                                    with open(path, "wb") as out:
                                        out.write(src)
                                except OSError:
                                    pass
                                break
                        if src is not None:
                            break
            if src is None:
                e = err
                raise UpdErr("файл пакета %s пропал при проверке (%s) — его удалил "
                             "антивирус или параллельная загрузка; загрузите пакет "
                             "ещё раз одним нажатием" % (rel, e.strerror or e))
            try:
                compile(src, rel, "exec", dont_inherit=True)
                checked += 1
            except SyntaxError as e:
                raise UpdErr("в пакете сломанный код (%s, строка %s): %s"
                             % (rel, e.lineno, str(e.msg)[:200]))
    if not checked:
        raise UpdErr("в пакете нет ни одного server/*.py для проверки")


# ------------------------------ резервная копия ------------------------------
_BACKUP_FILES = ("VERSION", "README.md", "neuro12-cloud.exe",
                 "install.sh", "install.ps1", "install.bat",
                 "start.sh", "stop.sh", "uninstall.sh")
_BACKUP_DIRS = ("server", "web")


def _copy_tree(src, dst):
    for root, dirs, files in os.walk(src):
        dirs[:] = [d for d in dirs if d not in _JUNK_DIRS]
        rel = os.path.relpath(root, src)
        tgt = os.path.join(dst, rel) if rel != "." else dst
        os.makedirs(tgt, exist_ok=True)
        for fn in files:
            if fn.endswith(".pyc"):
                continue
            shutil.copy2(os.path.join(root, fn), os.path.join(tgt, fn))


def backup_current(stamp=None):
    """Копия текущего кода в data/backups/pre-<версия>-<время>."""
    stamp = stamp or time.strftime("%Y%m%d-%H%M%S")
    cur = store.version()
    name = "pre-%s-%s" % (re.sub(r"[^A-Za-z0-9.]", "_", cur), stamp)
    dst = os.path.join(_backups_dir(), name)
    if os.path.isdir(dst):
        shutil.rmtree(dst, ignore_errors=True)
    os.makedirs(dst, exist_ok=True)
    n = 0
    for d in _BACKUP_DIRS:
        src = os.path.join(ROOT, d)
        if os.path.isdir(src):
            _copy_tree(src, os.path.join(dst, d))
            n += 1
    for fn in _BACKUP_FILES:
        src = os.path.join(ROOT, fn)
        if os.path.isfile(src):
            shutil.copy2(src, os.path.join(dst, fn))
            n += 1
    _prune_backups()
    return name, n


def _prune_backups():
    bdir = _backups_dir()
    try:
        dirs = [d for d in os.listdir(bdir)
                if os.path.isdir(os.path.join(bdir, d))]
        dirs.sort()
        for old in dirs[:-BACKUP_KEEP]:
            shutil.rmtree(os.path.join(bdir, old), ignore_errors=True)
    except Exception:
        pass


def list_backups():
    out = []
    try:
        for d in sorted(os.listdir(_backups_dir()), reverse=True):
            full = os.path.join(_backups_dir(), d)
            if not os.path.isdir(full):
                continue
            ver = ""
            m = re.match(r"pre-(.*)-\d{8}-\d{6}$", d)
            if m:
                ver = m.group(1)
            out.append({"name": d, "ver": ver, "ts": os.path.getmtime(full)})
    except Exception:
        pass
    return out


# ------------------------------- применение ----------------------------------
def _apply_from(zin, entries, staging):
    for name_in_zip, rel in entries:
        dest = os.path.join(ROOT, rel)
        base = os.path.abspath(ROOT)
        if not os.path.abspath(dest).startswith(base + os.sep):
            raise UpdErr("путь вне установки: %s" % rel)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        data = zin.read(name_in_zip)
        try:                              # v3.38: тот же файл — не трогаем
            if os.path.isfile(dest) and os.path.getsize(dest) == len(data):
                with open(dest, "rb") as f0:
                    if f0.read() == data:
                        continue
        except Exception:
            pass
        try:
            with open(dest, "wb") as out:
                out.write(data)
        except PermissionError:
            # Windows: запущенный .exe/.dll заменить нельзя — кладём рядом
            # <имя>.new и идём дальше (обновление не обрывается на полпути)
            if rel.lower().endswith((".exe", ".dll")):
                with open(dest + ".new", "wb") as out:
                    out.write(data)
                continue
            raise
    shutil.rmtree(staging, ignore_errors=True)


def apply_zip(blob, restart=True, actor=""):
    """Полный цикл обновления. Возвращает честный отчёт (dict с ok)."""
    from io import BytesIO
    info = parse_zip(blob)                # может бросить UpdErr
    frm = store.version()
    if info["to"] == frm:
        raise UpdErr("в пакете та же версия (%s), что и установлена — "
                     "обновлять нечем" % frm)
    if not _APPLY_LOCK.acquire(blocking=False):
        raise UpdErr("обновление уже применяется (второй запрос — двойное "
                     "нажатие?) — дождитесь результата первого")
    staging = _staging_dir()
    zin = zipfile.ZipFile(BytesIO(blob))
    try:
        for old in os.listdir(store.p()):     # хвосты прошлых попыток
            if old.startswith("update_staging"):
                shutil.rmtree(store.p(old), ignore_errors=True)
        _compile_zip(zin, info["entries"])     # проверка — из архива, в памяти
        _stage(zin, info["entries"], staging)
        backup_name, n_backup = backup_current()
        _apply_from(zin, info["entries"], staging)
    finally:
        zin.close()
        shutil.rmtree(staging, ignore_errors=True)
        _APPLY_LOCK.release()
    new_ver = ""
    try:
        with open(os.path.join(ROOT, "VERSION"), "r",
                  encoding="utf-8") as f:
            new_ver = f.read().strip()
    except Exception:
        pass
    if new_ver != info["to"]:
        raise UpdErr("после записи файл VERSION не совпал с пакетом "
                     "(%r != %r)" % (new_ver, info["to"]))
    st = {"kind": "update", "from": frm, "to": info["to"],
          "ts": time.time(), "files": len(info["entries"]),
          "skipped_data": info["skipped_data"], "backup": backup_name,
          "backup_items": n_backup, "actor": str(actor)[:40],
          "status": "applied"}
    _save_state(st)
    store.log("ОБНОВЛЕНИЕ", "пакет применён: %s -> %s, файлов %s, копия %s"
              % (frm, info["to"], len(info["entries"]), backup_name))
    st["restart"] = schedule_restart("обновление %s" % info["to"]) \
        if restart else {"mode": "manual"}
    if not restart:
        st["status"] = "applied-norestart"
        _save_state(st)
    return st


def _exact_restore(src_dir, dst_dir):
    """Вернуть каталог РОВНО как в копии: файлы копии записываются поверх,
    файлы, которых в копии НЕ было, удаляются (чтобы модуль новой версии не
    остался мусором рядом с откатанным кодом)."""
    have = set()
    for root, _dirs, files in os.walk(src_dir):
        for fn in files:
            if fn.endswith(".pyc"):
                continue
            have.add(os.path.relpath(os.path.join(root, fn), src_dir))
    _copy_tree(src_dir, dst_dir)
    removed = 0
    for root, _dirs, files in os.walk(dst_dir):
        for fn in files:
            rel = os.path.relpath(os.path.join(root, fn), dst_dir)
            if rel not in have:
                try:
                    os.remove(os.path.join(root, fn))
                    removed += 1
                except Exception:
                    pass
    return removed


def rollback(name, restart=True):
    """Вернуть код из резервной копии (data/backups/<name>): server/ и web/
    становятся РОВНО как в копии, VERSION/README/скрипты перезаписываются."""
    if not re.fullmatch(r"[A-Za-z0-9._-]+", str(name or "")):
        raise UpdErr("некорректное имя копии")
    src = os.path.join(_backups_dir(), name)
    if not os.path.isdir(src):
        raise UpdErr("такой резервной копии нет: %s" % name)
    frm = store.version()
    to_ver = ""
    try:
        with open(os.path.join(src, "VERSION"), "r", encoding="utf-8") as f:
            to_ver = f.read().strip()
    except Exception:
        pass
    n = 0
    for d in _BACKUP_DIRS:
        p = os.path.join(src, d)
        if os.path.isdir(p):
            n += _exact_restore(p, os.path.join(ROOT, d))
    for fn in _BACKUP_FILES:
        p = os.path.join(src, fn)
        if os.path.isfile(p):
            shutil.copy2(p, os.path.join(ROOT, fn))
            n += 1
    if not to_ver:
        to_ver = "неизвестно"
    st = {"kind": "rollback", "from": frm, "to": to_ver,
          "ts": time.time(), "files": n, "backup": name,
          "actor": "rollback", "status": "applied"}
    _save_state(st)
    store.log("ОБНОВЛЕНИЕ", "откат из копии %s: %s -> %s"
              % (name, frm, to_ver))
    st["restart"] = schedule_restart("откат к %s" % to_ver) \
        if restart else {"mode": "manual"}
    if not restart:
        st["status"] = "applied-norestart"
        _save_state(st)
    return st


# ------------------------------- перезапуск ----------------------------------
_RESTART_HELPER = r'''#!/usr/bin/env python3
# _restart.py — помощник перезапуска (пишется updater.py, не редактируй)
import os, signal, subprocess, sys, time
PID, PORT, ROOT, PYEXE, DATA = sys.argv[1], sys.argv[2], sys.argv[3], \
    sys.argv[4], sys.argv[5]
PID = int(PID)
time.sleep(1.2)


def _dead(pid):
    """Процесс действительно умер: /proc-состояние 'Z' (зомби) или нет
    процесса. kill(pid, 0) на зомби отвечает «жив», поэтому без /proc
    перезапуск терял 6+ секунд."""
    try:
        with open("/proc/%d/stat" % pid, "rb") as f:
            data = f.read().decode("utf-8", "replace")
        return data[data.rindex(")") + 2] == "Z"
    except FileNotFoundError:
        return True
    except Exception:
        pass
    try:
        os.kill(pid, 0)
        return False
    except OSError:
        return True


def _port_free(port):
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        return s.connect_ex(("127.0.0.1", int(port))) != 0
    finally:
        s.close()


for sig in (getattr(signal, "SIGTERM", 15),
            getattr(signal, "SIGKILL", 9)):
    try:
        os.kill(PID, sig)
    except Exception:
        break
    for _ in range(60):
        if _dead(PID):
            break
        time.sleep(0.1)
    else:
        continue
    break
# ждём, пока порт реально освободится (до 5 с), чтобы новый сервер не
# уполз на соседний порт
for _ in range(25):
    if _port_free(PORT):
        break
    time.sleep(0.2)
env = dict(os.environ)
env["NEURO_PORT"] = str(PORT)
env["NEURO_DATA"] = DATA
logp = os.path.join(DATA, "restart.log")
try:                       # v3.186.2: журнал перезапусков не растёт бесконечно:
    sz = os.path.getsize(logp)  # больше 5 МБ — хвост 1 МБ в .1, сам файл заново
    if sz > 5 * 1048576:
        with open(logp, "rb") as f:
            f.seek(sz - 1048576)
            tail = f.read().replace(b"\x00", b"")
        with open(logp + ".1", "wb") as f:
            f.write(tail)
        os.remove(logp)
except Exception:
    pass
try:
    lf = open(logp, "ab")
    kw = {"start_new_session": True} if os.name == "posix" else {
        "creationflags": (getattr(subprocess, "DETACHED_PROCESS", 0) |
                          getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))}
    p = subprocess.Popen([PYEXE, os.path.join("server", "app.py")],
                         cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=lf,
                         stdin=subprocess.DEVNULL, **kw)
    with open(os.path.join(DATA, "restart_pid.txt"), "w") as f:
        f.write(str(p.pid))
    if os.path.exists("/tmp/neuro12.pid"):
        try:
            with open("/tmp/neuro12.pid", "w") as f:
                f.write(str(p.pid) + "\n")
        except Exception:
            pass
except Exception as e:
    with open(logp, "ab") as f:
        f.write(("restart FAIL: %r\n" % (e,)).encode("utf-8"))
'''


def schedule_restart(reason=""):
    """Планировщик перезапуска: отдельный процесс ждёт ~1.2 с (ответ уходит
    в браузер), гасит старый процесс и поднимает новый на том же порту
    с тем же каталогом данных."""
    try:                     # v3.88: обученное — на диск ДО перезапуска
        import brain
        saved = brain.persist_all()
        store.log("ОБНОВЛЕНИЕ", "перед перезапуском сохранено: %s"
                  % (", ".join(k for k, _v in saved) or "—"))
    except Exception as e:
        store.log("ОБНОВЛЕНИЕ", "сохранение перед перезапуском: %s" % e)
    try:
        import crashguard
        crashguard.mark_planned()            # v3.228: оборванные запросы — не падение
    except Exception:
        pass
    try:                     # v3.155: сторож не поднимает процесс сам —
        with open(store.p("_planned_restart"), "w") as f:   # это сделает helper
            f.write(str(int(time.time())))
    except Exception:
        pass
    try:
        helper = store.p("_restart.py")
        with open(helper, "w", encoding="utf-8") as f:
            f.write(_RESTART_HELPER)
        port = store.read_port()
        logf = open(store.p("restart.log"), "ab")
        kw = {"start_new_session": True} if os.name == "posix" else {
            "creationflags": (getattr(subprocess, "DETACHED_PROCESS", 0) |
                              getattr(subprocess, "CREATE_NEW_PROCESS_GROUP",
                                      0))}
        subprocess.Popen(
            [sys.executable, helper, str(os.getpid()), str(port), ROOT,
             sys.executable, store.DATA_DIR],
            stdout=logf, stderr=logf, stdin=subprocess.DEVNULL, **kw)
        store.log("ОБНОВЛЕНИЕ", "перезапуск запланирован (%s), порт %d"
                  % (reason or "по требованию", port))
        return {"mode": "auto", "port": port, "in_sec": 2}
    except Exception as e:
        store.log("ОБНОВЛЕНИЕ", "АВТО-ПЕРЕЗАПУСК НЕ УДАЛСЯ: %r — перезапусти "
                  "вручную (start.sh / neuro12-cloud.exe)" % e)
        return {"mode": "manual",
                "how": "останови и запусти сервер заново (start.sh или "
                       "neuro12-cloud.exe)"}


# ------------------------------ состояние ------------------------------------
def _save_state(st):
    try:
        with open(_state_path(), "w", encoding="utf-8") as f:
            json.dump(st, f, ensure_ascii=False, indent=1)
    except Exception:
        pass


def state():
    return store.load_json("update_state.json", None) or None


def mark_confirmed():
    """Новый процесс подтвердил старт после обновления/отката (зовёт
    app.main() при загрузке)."""
    st = state()
    if not st or st.get("status") != "applied":
        return
    if str(st.get("to") or "") == store.version():
        st["status"] = "confirmed"
        st["confirmed_ts"] = time.time()
        _save_state(st)
        store.log("ОБНОВЛЕНИЕ", "старт v%s подтверждён — обновление "
                  "успешно" % st["to"])


def status():
    st = state()
    if st:
        st = dict(st)
        st["ago_sec"] = int(time.time() - float(st.get("ts") or 0))
    return {"version": store.version(), "root": ROOT,
            "state": st, "backups": list_backups()}


# ---------------------------------- CLI --------------------------------------
def _cli():
    os.environ.setdefault("NEURO_DATA", os.path.join(ROOT, "data"))
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "status":
        print(json.dumps(status(), ensure_ascii=False, indent=1))
    elif cmd == "apply":
        path = sys.argv[2] if len(sys.argv) > 2 else ""
        # v3.227: перезапуск из CLI гасил бы САМ CLI, а не сервер — по умолчанию без него;
        # перезапускает лаунчер (n12ctl restart) или флаг --restart (если CLI запущен тем же процессом)
        with open(path, "rb") as f:
            r = apply_zip(f.read(), restart=("--restart" in sys.argv), actor="CLI")
        print(json.dumps(r, ensure_ascii=False, indent=1))
    elif cmd == "rollback":
        r = rollback(sys.argv[2], restart=("--restart" in sys.argv))
        print(json.dumps(r, ensure_ascii=False, indent=1))
    elif cmd == "restart":
        print(json.dumps(schedule_restart("CLI"), ensure_ascii=False))
    else:
        print("команды: status | apply <zip> | rollback <копия> | restart")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
