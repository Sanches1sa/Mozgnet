# -*- coding: utf-8 -*-
# ===========================================================================
# auth.py — ПАРОЛЬ ВЕБ-ПАНЕЛИ neuro12-cloud (только stdlib)
# ---------------------------------------------------------------------------
# Веб-панель (чат, настройки, зрение) закрыта паролем. Платформенный API
# плат остаётся на токене X-Mozg-Token (auth в store.py) — плата не знает
# про пароль панели и наоборот.
#
# Хранение: data/auth.json  — PBKDF2-HMAC-SHA256, 240000 итераций, соль.
#           Если файла нет — панель в режиме первичной настройки: при
#           первом входе владелец сам придумывает пароль (страница /login).
# Сессии:   data/sessions.json — cookie n12sid, срок 30 дней (скользящий).
# Защита:   не более 8 неудачных попыток входа за 15 минут с одного IP
#           (дальше 429), задержка 0.7 с на неверную попытку, сравнение
#           через hmac.compare_digest.
# Пароль можно сбросить, удалив файл data/auth.json (форма настройки
# откроется снова) или командой:
#   python3 -c "import auth; auth.set_password('новый')"  (из server/,
#           с заданным NEURO_DATA)  — либо через install.sh --password.
# ===========================================================================
import hashlib
import hmac
import json
import os
import threading
import time
import urllib.parse

import store

COOKIE = "n12sid"
TTL = 30 * 24 * 3600                    # 30 дней
ITER = 240000

_LOCK = threading.RLock()
# сессии: {sid: {"ts": ..., "ip": "...", "ua": "..."}}; ts — время создания
_SESSIONS = None
# журнал неудачных входов: {ip: [ts, ...]} — только в памяти
_FAILS = {}


# ------------------------------ пароль --------------------------------------
def _auth_file():
    return store.p("auth.json")


def has_password():
    return os.path.exists(_auth_file())


def _hash(pw, salt):
    return hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"), salt, ITER)


def set_password(pw):
    """Задать/сменить пароль панели (install.sh и install.ps1 зовут это)."""
    pw = str(pw or "").strip()
    if len(pw) < 4:
        raise ValueError("пароль короче 4 знаков")
    salt = os.urandom(16)
    with open(_auth_file(), "w", encoding="utf-8") as f:
        json.dump({"salt": salt.hex(), "iter": ITER,
                   "hash": _hash(pw, salt).hex()}, f)
    store.log("АВТОРИЗАЦИЯ", "пароль веб-панели установлен/обновлён")


def verify(pw):
    if not has_password():
        return False
    try:
        with open(_auth_file(), "r", encoding="utf-8") as f:
            a = json.load(f)
        salt = bytes.fromhex(a.get("salt", ""))
        want = bytes.fromhex(a.get("hash", ""))
        it = int(a.get("iter") or ITER)
        got = hashlib.pbkdf2_hmac("sha256", str(pw or "").encode("utf-8"),
                                  salt, it)
        return hmac.compare_digest(got, want)
    except Exception:
        return False


# ------------------------------ сессии --------------------------------------
def _sessions_path():
    return store.p("sessions.json")


def _load_sessions():
    global _SESSIONS
    if _SESSIONS is None:
        _SESSIONS = store.load_json("sessions.json", {}) or {}
        # подчистить просроченные
        now = time.time()
        for sid in [s for s, v in _SESSIONS.items()
                    if now - float(v.get("ts") or 0) > TTL]:
            _SESSIONS.pop(sid, None)
    return _SESSIONS


def _save_sessions():
    try:
        store.save_json("sessions.json", _SESSIONS or {})
    except Exception:
        pass


def create_session(ip="", ua=""):
    with _LOCK:
        ss = _load_sessions()
        sid = os.urandom(24).hex()
        ss[sid] = {"ts": time.time(), "ip": str(ip)[:45],
                   "ua": str(ua)[:120]}
        # не более 50 сессий — старые выкидываем
        if len(ss) > 50:
            for s in sorted(ss, key=lambda k: ss[k].get("ts", 0))[:-50]:
                ss.pop(s, None)
        _save_sessions()
        return sid


def session_ok(sid):
    """Скользящий срок: каждый успешный вход продлевает сессию."""
    if not sid:
        return False
    with _LOCK:
        ss = _load_sessions()
        v = ss.get(sid)
        if not v:
            return False
        if time.time() - float(v.get("ts") or 0) > TTL:
            ss.pop(sid, None)
            _save_sessions()
            return False
        return True


def touch_session(sid):
    with _LOCK:
        ss = _load_sessions()
        if sid in ss:
            ss[sid]["ts"] = time.time()
            _save_sessions()


def drop_session(sid):
    with _LOCK:
        ss = _load_sessions()
        if sid in ss:
            ss.pop(sid, None)
            _save_sessions()


def drop_all_sessions():
    with _LOCK:
        _load_sessions()
        _SESSIONS.clear()
        _save_sessions()


def session_count():
    with _LOCK:
        return len(_load_sessions())


# --------------------------- защита от подбора ------------------------------
FAIL_WINDOW = 15 * 60                   # 15 минут
FAIL_MAX = 8


def too_many_fails(ip):
    now = time.time()
    lst = [t for t in _FAILS.get(ip, []) if now - t < FAIL_WINDOW]
    _FAILS[ip] = lst
    return len(lst) >= FAIL_MAX


def register_fail(ip):
    _FAILS.setdefault(ip, []).append(time.time())


def clear_fails(ip):
    _FAILS.pop(ip, None)


# ------------------------------ cookie --------------------------------------
def cookie_header(sid, max_age=TTL):
    """Заголовок Set-Cookie для ответа сервера."""
    return ("%s=%s; Path=/; HttpOnly; SameSite=Strict; Max-Age=%d"
            % (COOKIE, sid, max_age))


def clear_cookie_header():
    return "%s=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0" % COOKIE


def parse_cookie(header_value):
    """Достать n12sid из строки Cookie."""
    try:
        c = urllib.parse.unquote(str(header_value or ""))
        for part in c.split(";"):
            k, _, v = part.strip().partition("=")
            if k == COOKIE:
                return v.strip()
    except Exception:
        pass
    return ""
