# -*- coding: utf-8 -*-
"""v3.233 — ВХОД ПО ПОЧТЕ, ВЛАДЕЛЕЦ И РЕГИСТРАЦИЯ ЧЕРЕЗ ВЛАДЕЛЬЦА.

data/accounts.json: {почта: {name, role: owner|user, status: active|pending|rejected,
salt, hash, iter, created, approved, ip}}. Пароли — PBKDF2-HMAC-SHA256 (240 000 итераций,
соль), в открытом виде нигде не хранятся.
  * Владелец задан заранее (OWNER_EMAIL): при первом старте создаётся учётка владельца
    с начальным паролем — в коде только его хеш. Пароль меняется в «Настройки →
    Безопасность»; после смены начальный хеш больше не действует.
  * Регистрация — заявка (status pending); войти можно только после одобрения
    владельцем в «Настройки → Пользователи». Владелец получает уведомление в чате
    «Администрирование».
  * Роль user — чат и просмотр; настройки, серверы, администрирование, обновление,
    рабочий стол, изменение приложений и пользователи — только владелец (app.py).
Старый вход по паролю панели (без почты) остаётся и считается входом владельца."""
import hashlib
import hmac
import os
import re
import threading
import time

import store

OWNER_EMAIL = "alexandrvorobjev1996@gmail.com"
_OWNER_SEED = {"salt": "dd7f605f19d00e688474f51a0d1dbbf3", "iter": 240000, "hash": "58c63478532caa3ac49b090b3c020b0b2547829f5f4a0ec117fe3674be1e6081"}
ITER = 240000
_FILE = "accounts.json"
_LOCK = threading.RLock()
_EMAIL = re.compile(r"^[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9.-]{1,190}\.[A-Za-z]{2,24}$")
MAX_PENDING = 50


def norm(email):
    return str(email or "").strip().lower()[:254]


def _load():
    return store.load_json(_FILE, {}) or {}


def _save(d):
    store.save_json(_FILE, d)


def _hash(pw, salt, it=ITER):
    return hashlib.pbkdf2_hmac("sha256", str(pw).encode("utf-8"), bytes.fromhex(salt), int(it)).hex()


def ensure_owner():
    """Учётка владельца с начальным паролем — если её ещё нет."""
    with _LOCK:
        d = _load()
        if not any(a.get("role") == "owner" for a in d.values()):
            d[OWNER_EMAIL] = dict(_OWNER_SEED, name="Владелец", role="owner", status="active",
                                  created=time.time(), approved=time.time(), seed=True)
            _save(d)
            store.log("АВТОРИЗАЦИЯ", "создана учётка владельца %s (начальный пароль — смените в настройках)" % OWNER_EMAIL)
        return True


def exists_any():
    return bool(_load())


def public(email, a):
    return {"email": email, "name": a.get("name", ""), "role": a.get("role", "user"), "status": a.get("status"),
            "created": a.get("created"), "approved": a.get("approved"), "seed": bool(a.get("seed")),
            "note": a.get("note", ""), "ip": a.get("ip", "")}


def check(email, pw):
    """-> учётка (без хеша), если пароль верен (любой статус) | None."""
    a = _load().get(norm(email))
    if not a or not a.get("hash"):
        return None
    try:
        ok = hmac.compare_digest(_hash(pw, a["salt"], a.get("iter") or ITER), a["hash"])
    except Exception:
        ok = False
    return public(norm(email), a) if ok else None


def verify(email, pw):
    """-> активная учётка | None."""
    u = check(email, pw)
    return u if u and u["status"] == "active" else None


def get(email):
    a = _load().get(norm(email))
    return public(norm(email), a) if a else None


def register(email, name, pw, note="", ip=""):
    email = norm(email)
    if not _EMAIL.match(email):
        return {"ok": False, "error": "некорректная почта"}
    if len(str(pw or "")) < 8:
        return {"ok": False, "error": "пароль от 8 знаков"}
    with _LOCK:
        d = _load()
        if email in d:
            st = d[email].get("status")
            return {"ok": False, "error": "заявка уже есть и ждёт владельца" if st == "pending" else "такая почта уже зарегистрирована"}
        if sum(1 for a in d.values() if a.get("status") == "pending") >= MAX_PENDING:
            return {"ok": False, "error": "слишком много заявок — попробуйте позже"}
        salt = os.urandom(16).hex()
        d[email] = {"name": str(name or "").strip()[:80], "role": "user", "status": "pending", "salt": salt, "iter": ITER,
                    "hash": _hash(pw, salt), "created": time.time(), "note": str(note or "").strip()[:300], "ip": str(ip)[:45]}
        _save(d)
    store.log("АВТОРИЗАЦИЯ", "заявка на регистрацию: %s (%s) с %s" % (email, name, ip))
    try:                                      # уведомление владельцу — в чат «Администрирование»
        c = store.chat_special("admin")
        with store.LOCK:
            c = store.chat_load(c["id"]) or c
            c["messages"].append({"role": "bot", "zone": "Система", "ts": time.time(),
                                  "text": "🆕 Заявка на регистрацию: %s%s%s. Одобрить или отклонить — «Настройки → Пользователи»."
                                          % (email, (" — " + str(name)[:60]) if name else "", ("\nКомментарий: " + str(note)[:200]) if note else "")})
            c["ts"] = time.time()
            store.chat_save(c["id"], c)
    except Exception:
        pass
    return {"ok": True}


def users():
    return sorted((public(e, a) for e, a in _load().items()),
                  key=lambda u: ({"pending": 0, "active": 1}.get(u["status"], 2), -(u["created"] or 0)))


def pending_count():
    return sum(1 for a in _load().values() if a.get("status") == "pending")


def decide(email, action, by=""):
    email = norm(email)
    with _LOCK:
        d = _load()
        a = d.get(email)
        if not a:
            return {"ok": False, "error": "нет такого пользователя"}
        owners = [e for e, x in d.items() if x.get("role") == "owner" and x.get("status") == "active"]
        last_owner = a.get("role") == "owner" and owners == [email]
        if action == "approve":
            a.update(status="active", approved=time.time())
        elif action == "reject":
            if last_owner:
                return {"ok": False, "error": "нельзя отклонить единственного владельца"}
            a["status"] = "rejected"
        elif action == "delete":
            if last_owner:
                return {"ok": False, "error": "нельзя удалить единственного владельца"}
            d.pop(email)
        elif action in ("owner", "user"):
            if action == "user" and last_owner:
                return {"ok": False, "error": "нельзя снять роль с единственного владельца"}
            a["role"] = action
        else:
            return {"ok": False, "error": "действие: approve | reject | delete | owner | user"}
        _save(d)
    store.log("АВТОРИЗАЦИЯ", "%s: %s → %s" % (by or "владелец", email, action))
    return {"ok": True}


def set_password(email, old, new):
    if len(str(new or "")) < 8:
        return {"ok": False, "error": "новый пароль от 8 знаков"}
    if not verify(email, old):
        return {"ok": False, "error": "старый пароль неверен"}
    with _LOCK:
        d = _load()
        a = d[norm(email)]
        salt = os.urandom(16).hex()
        a.update(salt=salt, iter=ITER, hash=_hash(new, salt), seed=False, pw_changed=time.time())
        _save(d)
    store.log("АВТОРИЗАЦИЯ", "сменён пароль учётки %s" % norm(email))
    return {"ok": True}
