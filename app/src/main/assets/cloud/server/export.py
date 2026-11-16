# -*- coding: utf-8 -*-
# ===========================================================================
# export.py — «СКАЧАТЬ ОБЛАКО ЦЕЛИКОМ» (v3.30, только stdlib)
# ---------------------------------------------------------------------------
# Заявка владельца: кнопка «загрузить облачный сервис вместе с файлами,
# базой данных и обученной сетью», под паролем (PW12354, можно сменить);
# скачанную версию можно установить на Windows.
#   * пароль выгрузки — ОТДЕЛЬНЫЙ от пароля панели (архив содержит токены
#     и всю память); по умолчанию PW12354, хранится только PBKDF2-хеш
#     (data/export_pw.json); 5 неверных попыток — пауза 5 минут;
#   * архив: код платформы (server, web, установщики, лаунчеры) + data/
#     целиком (база знаний, файлы, чаты, сеть материалов, память ответов,
#     ключи, настройки) без временных файлов и прошлых выгрузок;
#   * установка: распаковать -> install.bat (данные перенесутся в пустую
#     установку; поверх существующей — install.bat -ImportData).
# Скачивание — одноразовая ссылка на 10 минут (большие архивы не грузятся
# в память: сервер отдаёт файл потоком).
# ===========================================================================
import hashlib
import hmac
import os
import threading
import time
import zipfile

import store

DEFAULT_PW = "PW12354"
_FILE = "export_pw.json"
_DIR = "_exports"
_LOCK = threading.Lock()
_FAILS = {"n": 0, "until": 0.0}
_TOKENS = {}                        # token -> (path, ts)
_SKIP_DIRS = ("_exports", "__pycache__", "backups", "updates")


def _hash(pw, salt):
    return hashlib.pbkdf2_hmac("sha256", str(pw).encode("utf-8"),
                               bytes.fromhex(salt), 120000).hex()


def check(pw):
    """Пароль выгрузки верный? С защитой от подбора."""
    now = time.time()
    with _LOCK:
        if now < _FAILS["until"]:
            return False, "слишком много попыток — подожди %d с" % int(
                _FAILS["until"] - now)
    d = store.load_json(_FILE, None)
    if d and d.get("salt") and d.get("hash"):
        ok = hmac.compare_digest(_hash(pw, d["salt"]), d["hash"])
    else:
        ok = hmac.compare_digest(str(pw or "").encode("utf-8"),
                                 DEFAULT_PW.encode("utf-8"))
    with _LOCK:
        if ok:
            _FAILS["n"] = 0
            return True, ""
        _FAILS["n"] += 1
        if _FAILS["n"] >= 5:
            _FAILS["n"] = 0
            _FAILS["until"] = now + 300
    time.sleep(1.0)
    store.log("ВЫГРУЗКА", "неверный пароль выгрузки")
    return False, "неверный пароль выгрузки"


def set_password(old, new):
    ok, why = check(old)
    if not ok:
        return {"ok": False, "error": why}
    new = str(new or "")
    if len(new) < 6:
        return {"ok": False, "error": "новый пароль — от 6 знаков"}
    salt = os.urandom(16).hex()
    store.save_json(_FILE, {"salt": salt, "hash": _hash(new, salt),
                            "ts": time.time()})
    store.log("ВЫГРУЗКА", "пароль выгрузки изменён")
    return {"ok": True}


def is_default():
    d = store.load_json(_FILE, None)
    return not (d and d.get("hash"))


def _root():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def build():
    """Собрать полный архив. Возврат {ok, token, name, size}."""
    root = _root()
    data = os.path.abspath(store.p(""))
    out_dir = store.p(_DIR)
    os.makedirs(out_dir, exist_ok=True)
    for fn in os.listdir(out_dir):          # прошлые выгрузки не копим
        try:
            os.remove(os.path.join(out_dir, fn))
        except Exception:
            pass
    try:                     # v3.89: всё обученное — на диск до упаковки
        import brain
        brain.persist_all()
    except Exception:
        pass
    name = "neuro12-cloud-full-%s-%s.zip" % (
        store.version().replace(".", "_"), time.strftime("%Y%m%d-%H%M"))
    path = os.path.join(out_dir, name)
    n = 0
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED,
                         allowZip64=True) as z:
        # 1) код платформы
        for top in ("server", "web", "launcher"):
            base = os.path.join(root, top)
            if not os.path.isdir(base):
                continue
            for r, dirs, files in os.walk(base):
                dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
                for fn in files:
                    if fn.endswith((".pyc", ".tmp")) or fn.startswith("_t_"):
                        continue
                    full = os.path.join(r, fn)
                    z.write(full, "neuro12-cloud/" +
                            os.path.relpath(full, root).replace(os.sep, "/"))
                    n += 1
        for fn in os.listdir(root):
            full = os.path.join(root, fn)
            if os.path.isfile(full) and not fn.startswith("_") and \
                    not fn.endswith((".zip", ".tmp")):
                z.write(full, "neuro12-cloud/" + fn)
                n += 1
        # 2) данные: база знаний, файлы, обученная сеть, память, настройки
        for r, dirs, files in os.walk(data):
            dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
            for fn in files:
                if fn.endswith(".tmp"):
                    continue
                full = os.path.join(r, fn)
                rel = os.path.relpath(full, data).replace(os.sep, "/")
                try:
                    # веса и файлы подкачки модели почти не сжимаются —
                    # кладём без сжатия (в разы быстрее)
                    ct = zipfile.ZIP_STORED if fn.endswith(
                        (".dat", ".npz", ".zip", ".jpg", ".png", ".mp4")) \
                        else zipfile.ZIP_DEFLATED
                    z.write(full, "neuro12-cloud/data/" + rel, compress_type=ct)
                    n += 1
                except (PermissionError, OSError):
                    continue                 # файл заняли прямо сейчас
        z.writestr("neuro12-cloud/УСТАНОВКА.txt", (
            "Полная копия облака neuro12-cloud v%s (код + данные).\n\n"
            "Windows: распакуй архив и запусти install.exe — облако "
            "встанет в %%LOCALAPPDATA%%\\neuro12-cloud вместе с базой "
            "знаний, файлами, сетью материалов и своей LLM (веса и файлы "
            "подкачки Adam: data\\llm_adam).\n"
            "Облако уже установлено — install.exe спросит: добавить новые и "
            "более свежие данные (по умолчанию), заменить целиком (с копией "
            "data.bak-<дата>) или обновить только код.\n"
            "Архивы neuro12-cloud-v*-update.zip, положенные рядом с "
            "install.exe, ставятся поверх (если они новее).\n"
            "Без install.exe: install.bat (-ImportData — заменить данные).\n"
            % store.version()).encode("utf-8"))
    tok = os.urandom(16).hex()
    with _LOCK:
        _TOKENS[tok] = (path, time.time())
    size = os.path.getsize(path)
    store.log("ВЫГРУЗКА", "собран полный архив: %s, файлов %d, %d МБ"
              % (name, n, size // 1048576))
    return {"ok": True, "token": tok, "name": name, "size": size,
            "files": n}


def take(token):
    """Одноразовая ссылка -> путь к архиву | None (10 минут)."""
    with _LOCK:
        rec = _TOKENS.pop(str(token or ""), None)
    if not rec or time.time() - rec[1] > 600 or not os.path.isfile(rec[0]):
        return None
    return rec[0]
