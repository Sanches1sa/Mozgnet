# thoughtjournal.py — v3.215: ЖУРНАЛ «ЧТО ДЕЛАЛИ МОЗГ И LLM С МЫСЛЯМИ»
#
# Каждая мысль платы попадает в журнал с решением:
#   мозг  — мысль связна, облако её не трогает, ответы идут её словами;
#   LLM   — мысль разрознена: своя LLM собрала нити, добавила итог
#           (или записано, почему не смогла);
#   LLM   — собственная мысль LLM (источник, длина, качество, отказ).
# Файл thoughtjournal.json, последние MAX записей. Только облако.
import threading
import time

import store

_FILE = "thoughtjournal.json"
MAX = 300
_LOCK = threading.RLock()


def _load():
    d = store.load_json(_FILE, None)
    if not isinstance(d, dict):
        d = {}
    d.setdefault("items", [])
    d.setdefault("count", {})
    return d


def add(who, act, text="", out="", board="", **kw):
    """who: «мозг» | «LLM»; act: kept|assembled|failed|skip|own|rejected."""
    e = {"ts": time.time(), "who": str(who)[:8], "act": str(act)[:12],
         "board": str(board)[:40], "text": str(text or "")[:500],
         "out": str(out or "")[:2500],
         "chars_in": len(str(text or "")), "chars_out": len(str(out or ""))}
    for k, v in kw.items():
        if v is not None:
            e[k] = v
    try:
        with _LOCK:
            d = _load()
            d["items"].append(e)
            d["items"] = d["items"][-MAX:]
            key = "%s:%s" % (e["who"], e["act"])
            d["count"][key] = d["count"].get(key, 0) + 1
            store.save_json(_FILE, d)
    except Exception:
        pass
    return e


def recent(limit=60, who=""):
    with _LOCK:
        it = _load()["items"]
    if who:
        it = [i for i in it if i.get("who") == who]
    return list(reversed(it[-max(1, min(int(limit), MAX)):]))


def stats():
    with _LOCK:
        d = _load()
    c = d["count"]
    return {"count": c, "total": len(d["items"]),
            "brain_kept": c.get("мозг:kept", 0),
            "llm_assembled": c.get("LLM:assembled", 0),
            "llm_failed": c.get("LLM:failed", 0),
            "llm_own": c.get("LLM:own", 0)}


def clear():
    with _LOCK:
        store.save_json(_FILE, {"items": [], "count": {}})
