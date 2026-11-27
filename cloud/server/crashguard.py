# -*- coding: utf-8 -*-
"""v3.228 — ЗАЩИТА ОТ ПАДЕНИЙ И ПАМЯТЬ КОНТЕКСТА (особенно для Windows).

Что делает (причину падения на Windows автоматически НЕ устраняет — она
неизвестна без crash.log; это слой, который сужает вред и показывает виновника):
  1) setup(): стек потоков 8 МБ (на Windows у потоков 1 МБ — глубокая рекурсия
     убивала ВЕСЬ процесс кодом 0xC00000FD) и разумный лимит рекурсии, чтобы
     Python ловил её исключением раньше;
  2) журнал «что обрабатывалось»: begin()/end() пишут data/inflight.json. Если
     процесс умер посреди запроса, при следующем старте recover() знает, какой
     запрос и в каком чате, пишет это в crash_requests.json и в чат;
  3) память контекста: чат хранит «потерянное» сообщение; «продолжи / повтори» после
     перезапуска повторяет его (если оно не опасное);
  4) защита от петли: запрос, дважды уронивший облако, больше не выполняется
     (до сброса owner’ом или 24 ч);
  5) безопасный режим: после 3 быстрых падений подряд сторож запускает облако с
     NEURO_SAFE=1 — без numpy-LLM, OCR и распознавания речи (самые частые источники
     машинных падений 0xC0000005); после минуты стабильной работы — обычный режим;
  6) автосохранение обученного раз в 5 минут.
"""
import hashlib
import json
import os
import re
import sys
import threading
import time

import store

_LOCK = threading.RLock()
_ACTIVE = {}
_TL = threading.local()
_INFLIGHT = "inflight.json"
_POISON = "poison.json"
_REQS = "crash_requests.json"
POISON_AT = 2
POISON_TTL = 24 * 3600
_RESUME = re.compile(r"^\s*(продолжи|продолжай|повтори|ещё раз|еще раз|давай заново|заново)[.!?\s]*$", re.I)


def safe():
    return os.environ.get("NEURO_SAFE") == "1"


def setup():
    """Вызывать ДО создания потоков."""
    try:
        threading.stack_size(8 * 1024 * 1024)
    except (ValueError, RuntimeError):
        pass
    try:
        if sys.getrecursionlimit() < 2500:
            sys.setrecursionlimit(2500)
    except Exception:
        pass


def fingerprint(text):
    t = " ".join(str(text or "").lower().split())[:400]
    return hashlib.sha1(t.encode("utf-8")).hexdigest()[:16]


def _write_active():
    try:
        store.save_json(_INFLIGHT, list(_ACTIVE.values()))
    except Exception:
        pass


def begin(kind, path=""):
    tok = "%d-%s" % (threading.get_ident(), os.urandom(3).hex())
    with _LOCK:
        _ACTIVE[tok] = {"tok": tok, "kind": kind, "path": str(path)[:80], "ts": time.time(), "chat": "", "text": "", "fp": ""}
        _write_active()
    _TL.tok = tok
    return tok


def annotate(chat="", text=""):
    """Дополнить текущий запрос потока: чат и текст (для восстановления)."""
    tok = getattr(_TL, "tok", None)
    with _LOCK:
        e = _ACTIVE.get(tok)
        if e:
            e["chat"] = str(chat)[:40]
            e["text"] = str(text)[:400]
            e["fp"] = fingerprint(text)
            _write_active()


def end(tok, ok=True):
    with _LOCK:
        e = _ACTIVE.pop(tok, None)
        if e is not None:
            _write_active()
            if ok and e.get("fp"):
                p = store.load_json(_POISON, {}) or {}
                if e["fp"] in p:
                    p.pop(e["fp"], None)
                    store.save_json(_POISON, p)
    if getattr(_TL, "tok", None) == tok:
        _TL.tok = None


def mark_planned():
    """Перед плановым перезапуском (обновление): оборванные запросы — не падение."""
    try:
        with open(store.p("inflight_planned"), "w") as f:
            f.write(str(int(time.time())))
    except Exception:
        pass


def is_poison(text):
    fp = fingerprint(text)
    p = store.load_json(_POISON, {}) or {}
    e = p.get(fp)
    if not e:
        return False
    if time.time() - float(e.get("ts") or 0) > POISON_TTL:
        p.pop(fp, None)
        store.save_json(_POISON, p)
        return False
    return int(e.get("n") or 0) >= POISON_AT


def recover():
    """При старте: что осталось в inflight.json — процесс умер посреди этих запросов."""
    left = store.load_json(_INFLIGHT, []) or []
    planned = os.path.exists(store.p("inflight_planned"))
    try:
        os.remove(store.p("inflight_planned"))
    except OSError:
        pass
    if not isinstance(left, list) or not left:
        return {"recovered": 0}
    store.save_json(_INFLIGHT, [])
    poison = store.load_json(_POISON, {}) or {}
    hist = store.load_json(_REQS, []) or []
    n = 0
    for e in left:
        if not isinstance(e, dict):
            continue
        n += 1
        hist.append({"ts": e.get("ts"), "kind": e.get("kind"), "path": e.get("path"), "chat": e.get("chat"),
                     "text": e.get("text"), "planned": planned, "found": time.time()})
        if e.get("chat") and e.get("text"):
            if not planned and e.get("fp"):
                ent = poison.get(e["fp"]) or {"n": 0}
                ent["n"] = int(ent.get("n") or 0) + 1
                ent["ts"] = time.time()
                poison[e["fp"]] = ent
            _note_chat(e, planned, int(poison.get(e.get("fp"), {}).get("n", 0)))
        try:
            store.log("ПЕРЕЗАПУСК" if planned else "ПАДЕНИЕ", "прервано при перезапуске: %s %s %s" % (
                e.get("kind"), e.get("path"), str(e.get("text") or "")[:60]))
        except Exception:
            pass
    store.save_json(_POISON, poison)
    store.save_json(_REQS, hist[-80:])
    return {"recovered": n, "planned": planned}


def _note_chat(e, planned, count):
    try:
        with store.LOCK:
            chat = store.chat_load(e["chat"])
            if not chat:
                return
            if planned:
                txt = "🔄 Облако перезапускалось (обновление) и не успело ответить на: «%s». Контекст чата сохранён — напишите «повтори»." % e["text"][:120]
            elif count >= POISON_AT:
                txt = ("⚠ Облако упало на сообщении «%s» уже %d раза. Я больше не буду его выполнять, чтобы не падать снова — "
                       "переформулируйте. Подробности: crash.log, /api/crash/report." % (e["text"][:120], count))
            else:
                txt = ("⚠ Облако упало, пока отвечало на: «%s». Контекст чата сохранён (вся переписка выше на месте). "
                       "Напишите «повтори» — повторю один раз; если упадёт снова, это сообщение будет заблокировано." % e["text"][:120])
            chat["messages"].append({"role": "bot", "text": txt, "zone": "Система", "ts": time.time(), "crash_notice": True})
            chat["lost"] = {"text": e["text"], "fp": e.get("fp"), "ts": time.time()}
            chat["ts"] = time.time()
            store.chat_save(chat["id"], chat)
    except Exception:
        pass


def resume_text(chat, text):
    """«повтори» после падения -> потерянный вопрос (если он не заблокирован), иначе исходный текст."""
    lost = (chat or {}).get("lost")
    if lost and lost.get("text") and _RESUME.match(str(text or "")):
        if is_poison(lost["text"]):
            return text
        return lost["text"]
    return text


def clear_lost(chat):
    if chat and chat.get("lost"):
        chat.pop("lost", None)


def report():
    return {"safe_mode": safe(), "inflight": list(_ACTIVE.values()),
            "crashes": (store.load_json("crashes.json", []) or [])[-20:],
            "crash_requests": (store.load_json(_REQS, []) or [])[-20:],
            "blocked": [{"fp": k, **v} for k, v in (store.load_json(_POISON, {}) or {}).items() if int(v.get("n") or 0) >= POISON_AT]}


def clear_blocked():
    store.save_json(_POISON, {})
    return True


def start_autosave(every=300):
    def loop():
        while True:
            time.sleep(every)
            try:
                import brain
                brain.persist_all()
            except Exception:
                pass
    t = threading.Thread(target=loop, daemon=True, name="autosave")
    t.start()
    return t
