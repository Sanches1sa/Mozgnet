# -*- coding: utf-8 -*-
"""v3.169 — СВОЯ LLM САМА ЗАХОДИТ В ИНТЕРНЕТ.

Заявка владельца: «пусть (своя LLM) заходит в Интернет».

Раньше своя LLM училась только на том, что облако уже положило в базу
знаний. Теперь у неё своё чтение:
  * ТЕМЫ выбирает сама: вопросы владельца, на которые она в ответе ничего
    не добавила («LLM промолчала», «не опирается на факты»), слова мыслей
    мозга neuro-12 и темы последних вопросов в чате;
  * ЧИТАЕТ: Википедию (иначе Рувики) и документацию Confluence — те же
    безопасные функции облака (только https, публичные адреса; Confluence
    — адрес, заданный владельцем);
  * прочитанное — В СВОЙ КОРПУС (data/llm_web.json, до MAX_KEEP текстов),
    не в базу знаний: так страницы Confluence тоже учат LLM, но в базу не
    попадают (заявка v3.168). Дообучение LLM (learn_new, раз в 2 мин)
    подхватывает новые тексты само;
  * раз в PERIOD (по умолчанию 5 мин), только на вдохе дыхания и если
    мозг разрешил процесс «Своя LLM: интернет» (строгая связь v3.166).
Выключается: Настройки → «Своя LLM» → «llm_web_on».
"""
import json
import re
import threading
import time

import store

PERIOD = 300.0
MAX_KEEP = 80
MAX_CHARS = 8000
_FILE = "llm_web.json"
_LOCK = threading.RLock()
_ST = {"last": 0.0, "busy": False, "reads": 0, "err": "", "title": "",
       "loaded": False, "items": [], "want": []}


def enabled():
    s = store.settings()
    return bool(s.get("llm_web_on", True) and s.get("web_on"))


def _load():
    with _LOCK:
        if _ST["loaded"]:
            return
        _ST["loaded"] = True
        d = store.load_json(_FILE, {}) or {}
        _ST["items"] = [x for x in (d.get("items") or []) if isinstance(x, dict)][-MAX_KEEP:]
        _ST["want"] = [str(x)[:120] for x in (d.get("want") or [])][-30:]


def _save():
    with _LOCK:
        store.save_json(_FILE, {"items": _ST["items"][-MAX_KEEP:],
                                "want": _ST["want"][-30:]})


def want(topic, why=""):
    """Своя LLM не справилась с темой — прочитает её в интернете."""
    t = re.sub(r"\s+", " ", str(topic or "")).strip(" ?!.")[:120]
    if len(t) < 3:
        return
    _load()
    with _LOCK:
        if t.lower() in (x.lower() for x in _ST["want"]):
            return
        if any(t.lower() == str(x.get("q") or "").lower() for x in _ST["items"][-30:]):
            return
        _ST["want"].append(t)
        del _ST["want"][:-30]
    _save()


def corpus():
    """Тексты, прочитанные самой LLM, — для llmlink.corpus_extra."""
    _load()
    with _LOCK:
        return ["%s. %s" % (x.get("t", ""), x.get("x", "")) for x in _ST["items"]
                if x.get("x")]


def _pick_topic():
    with _LOCK:
        if _ST["want"]:
            return _ST["want"].pop(0), "не справилась в ответе"
    try:                                   # мысль мозга neuro-12
        import brainctl
        th = brainctl.current_thought("")
        if th:
            import textutils
            kw = textutils.keywords(th["text"], 2) or []
            if kw:
                return " ".join(kw[:2]), "мысль мозга"
    except Exception:
        pass
    try:                                   # последние вопросы в чате
        import textutils
        chats = sorted(store.chat_list() or [], key=lambda c: -float(c.get("updated") or c.get("ts") or 0))
        msgs = []
        for c in chats[:3]:                  # v3.172: последние чаты (chat_tail не было)
            msgs += (store.chat_load(c.get("id")) or {}).get("messages") or []
        for m in reversed(msgs[-40:]):
            if m.get("role") == "user":
                kw = textutils.keywords(m.get("text") or "", 2) or []
                if kw:
                    return " ".join(kw[:2]), "вопрос в чате"
    except Exception:
        pass
    return "", ""


def _read(topic):
    """Википедия/Рувики, затем Confluence (в памяти). -> (title, text, src)."""
    try:
        import readlike
        art, _why = readlike.fetch_article(topic)
        if art:
            return art["title"], art["text"], art["src"]
    except Exception:
        pass
    try:                                   # v3.174: все источники по ссылкам
        import sources
        if sources.enabled():
            t, tx, src = sources.page_read(topic)
            if t:
                return t, tx, src
    except Exception:
        pass
    return None, "", ""


def remember(title, text, src, topic="", why=""):
    """v3.187: прочитанное в другом месте (проверка факта в ответе —
    llmfact) — тоже в корпус своей LLM: она дообучится на этой статье."""
    title = str(title or "").strip()
    body = " ".join(str(text or "").split())[:MAX_CHARS]
    if not title or len(body) < 200:
        return False
    _load()
    with _LOCK:
        _ST["items"] = [x for x in _ST["items"] if x.get("t") != title]
        _ST["items"].append({"t": title[:160], "x": body, "src": src,
                             "q": str(topic or "")[:120], "why": why,
                             "ts": time.time()})
        del _ST["items"][:-MAX_KEEP]
        _ST["want"] = [w for w in _ST["want"] if w.lower() != str(topic or "").lower()]
    _save()
    return True


def read_once(topic=None, why="сама"):
    """Одно чтение. -> {"ok", "title"} | {"ok": False, "why"}."""
    _load()
    if not topic:
        topic, why = _pick_topic()
    if not topic:
        return {"ok": False, "why": "нет темы"}
    t0 = time.time()
    title, text, src = _read(topic)
    if not title:
        _ST["err"] = "«%s»: статьи не нашлось" % topic[:60]
        store.log("LLM-ИНТЕРНЕТ", _ST["err"])
        return {"ok": False, "why": _ST["err"]}
    body = " ".join(str(text).split())[:MAX_CHARS]
    with _LOCK:
        _ST["items"] = [x for x in _ST["items"] if x.get("t") != title]
        _ST["items"].append({"t": title[:160], "x": body, "src": src,
                             "q": topic[:120], "why": why, "ts": time.time()})
        del _ST["items"][:-MAX_KEEP]
        _ST["reads"] += 1
        _ST["title"] = title
        _ST["err"] = ""
    _save()
    store.log("LLM-ИНТЕРНЕТ", "своя LLM сама прочитала «%s» (%s, %d зн., %.1f с; "
              "тема «%s» — %s); дообучится на ней в своём корпусе" % (
                  title[:60], src, len(body), time.time() - t0, topic[:40], why))
    return {"ok": True, "title": title, "src": src}


def tick():
    """Из цикла своей LLM: раз в PERIOD, на вдохе, если мозг разрешил."""
    if not enabled() or _ST["busy"] or time.time() - _ST["last"] < PERIOD:
        return
    try:
        import breath
        if not breath.inhale("Своя LLM: интернет"):
            return
    except Exception:
        pass
    _ST["last"] = time.time()
    _ST["busy"] = True

    def _job():
        try:
            read_once()
        except Exception as e:
            _ST["err"] = str(e)[:120]
        finally:
            _ST["busy"] = False
    threading.Thread(target=_job, daemon=True, name="llm-web").start()


def status():
    _load()
    with _LOCK:
        return {"on": enabled(), "reads": _ST["reads"], "kept": len(_ST["items"]),
                "last": _ST["title"], "err": _ST["err"], "want": list(_ST["want"][:8]),
                "recent": [{"t": x.get("t"), "src": x.get("src"), "why": x.get("why")}
                           for x in _ST["items"][-6:]]}
