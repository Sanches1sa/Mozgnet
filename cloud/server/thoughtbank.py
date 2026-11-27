# thoughtbank.py — v3.212: БАНК МЫСЛЕЙ МОЗГА и режим бота «Разговор».
#
# Мозг платы пишет длинные связные мысли (до 6000 знаков, 37 оборотов по
# словам). Облако их КОПИРУЕТ в банк — но только проверенные (русский текст,
# без мусора и повторов, ≥2 предложений) — и отвечает ими в разговоре:
# берёт мысль, лучше всего подходящую по теме вопроса, и, пока не кончился
# лимит символов и не теряется тема, достраивает следующими мыслями, которые
# пересекаются по словам с вопросом и уже выбранным текстом. Одну и ту же
# мысль в одном чате дважды не повторяет.
import hashlib
import re
import threading
import time

import store

_LOCK = threading.RLock()
_FILE = "thoughtbank.json"
MAX_ITEMS = 600
_WORD = re.compile(r"[а-яё]{4,}")
_LATIN = re.compile(r"[A-Za-z]{3,}")
_SENT = re.compile(r"(?<=[.!?…])\s+")
_STOP = {"этот", "этого", "этой", "того", "тоже", "если", "когда", "чтобы",
         "который", "которые", "очень", "можно", "также", "потому", "после",
         "между", "через", "более", "только", "быть", "есть", "было", "были"}


def stems(text):
    return {w[:5] for w in _WORD.findall(str(text or "").lower())
            if w not in _STOP}


_FENCE = re.compile(r"```[^\n]*\n.*?```", re.S)


def split_code(text):
    """(проза, [блоки кода]) — латиница в КОДЕ законна."""
    t = str(text or "")
    code = _FENCE.findall(t)
    return _FENCE.sub(" ", t), code


def good(text):
    """Годится ли мысль для ответа людям. v3.212: код в ```…``` допустим
    (он на латинице); проверки русской прозы — только по прозе вокруг."""
    full = str(text or "").strip()
    t, code = split_code(full)
    t = t.strip()
    if code:
        if len(full) > 6000 or sum(len(c) for c in code) > 4000:
            return False
        if len(t) < 30:
            return len(code[0]) >= 40 and t[:1] not in ("☁", "🤖", "⚠")
    if not (60 <= len(t) <= 6000):
        return False
    if t[:1] in ("☁", "🤖", "⚠", "["):
        return False
    letters = [c for c in t if c.isalpha()]
    if not letters:
        return False
    cyr = sum(1 for c in letters if "а" <= c.lower() <= "я" or c.lower() == "ё")
    if cyr / len(letters) < 0.85 or len(_LATIN.findall(t)) > 2:
        return False
    ws = re.findall(r"[а-яё]+", t.lower())
    if len(ws) < 12 or len(set(ws)) < 0.5 * len(ws):
        return False
    if any(ws[i] == ws[i + 1] == ws[i + 2] for i in range(len(ws) - 2)):
        return False
    return len([s for s in _SENT.split(t) if len(s) > 8]) >= (1 if code else 2)


def _raw_id(txt):
    return hashlib.sha1(re.sub(r"\W+", "", str(txt).lower()).encode()).hexdigest()[:12]


def _smooth_if_needed(txt, board=""):
    """v3.214: своя LLM собирает контекст в разрозненной мысли (только копия в
    облаке: на плате мысль не меняется)."""
    try:
        import thoughtsmooth
        if thoughtsmooth.enabled():
            return thoughtsmooth.smooth(txt, board=board, log=True, publish=True)[0]
    except Exception:
        pass
    return txt


def _load():
    d = store.load_json(_FILE, None)
    if not isinstance(d, dict):
        d = {}
    d.setdefault("items", [])
    d.setdefault("last_ts", 0.0)
    d.setdefault("used", {})
    return d


def ingest(limit=400):
    """Перенести новые хорошие мысли из thoughts.json в банк. -> сколько."""
    n = 0
    with _LOCK:
        d = _load()
        seen = {i["id"] for i in d["items"]}
        seen_raw = {i.get("oid") for i in d["items"] if i.get("oid")} | set(d.get("rawseen", []))
        last = float(d.get("last_ts") or 0.0)
        new_last = last
        raw = store.load_json("thoughts.json", []) or []
        for t in raw[-limit:]:
            ts = float(t.get("ts") or 0.0)
            if ts <= last:
                continue
            new_last = max(new_last, ts)
            raw_t = str(t.get("text") or "").strip()
            bd = str(t.get("board") or "")
            rid = _raw_id(raw_t)
            if rid in seen or rid in seen_raw:     # уже разобрана при добавлении
                continue
            seen_raw.add(rid)
            own = bd in ("своя LLM", "облако") or t.get("kind") == "llm"
            txt = raw_t if own else _smooth_if_needed(raw_t, bd)
            if not good(txt):
                continue
            h = hashlib.sha1(re.sub(r"\W+", "", txt.lower()).encode()).hexdigest()[:12]
            if h in seen:
                continue
            seen.add(h)
            d["items"].append({"id": h, "text": txt, "stems": sorted(stems(split_code(txt)[0]))[:80], "code": bool(split_code(txt)[1]),
                               "ts": ts, "board": str(t.get("board") or "")[:40],
                               "uses": 0, "oid": rid})
            n += 1
        if len(d["items"]) > MAX_ITEMS:       # лишнее: сначала часто используемые и старые
            d["items"].sort(key=lambda i: (i.get("uses", 0), i.get("ts", 0)))
            d["items"] = d["items"][-MAX_ITEMS:]
        d["last_ts"] = new_last
        if n or new_last != last:
            store.save_json(_FILE, d)
    return n


def add(text, board="", ts=None):
    """Добавить мысль напрямую (проверяется good; разрозненную сначала
    сглаживает своя LLM облака)."""
    raw = str(text or "")
    text = _smooth_if_needed(raw, board)
    if not good(text):
        with _LOCK:                          # чтобы ingest не разбирал её второй раз
            d = _load()
            d["rawseen"] = (d.get("rawseen", []) + [_raw_id(raw)])[-400:]
            store.save_json(_FILE, d)
        return False
    with _LOCK:
        d = _load()
        h = hashlib.sha1(re.sub(r"\W+", "", str(text).lower()).encode()).hexdigest()[:12]
        if any(i["id"] == h for i in d["items"]):
            return False
        d["items"].append({"id": h, "text": str(text).strip(), "stems": sorted(stems(split_code(text)[0]))[:80], "code": bool(split_code(text)[1]),
                           "ts": ts or time.time(), "board": board, "uses": 0,
                           "oid": _raw_id(raw)})
        d["items"] = d["items"][-MAX_ITEMS:]
        store.save_json(_FILE, d)
    return True


def _score(q, item):
    s = set(item.get("stems") or [])
    if not s or not q:
        return 0.0
    k = len(q & s)
    return k / (len(s) ** 0.5) if k else 0.0


def compose(question, chat="", limit=3500, ctx_text=""):
    """Ответ из мыслей мозга -> {text, ids, n, score} | None (если по теме
    ничего подходящего нет)."""
    ingest()
    q = stems(question) | (stems(ctx_text) if len(stems(question)) < 2 else set())
    with _LOCK:
        d = _load()
        items = d["items"]
        used = list(d["used"].get(chat, []))
        if not items:
            return None
        pool = [i for i in items if i["id"] not in used] or items
        best, bs = None, 0.0
        if q:
            for i in pool:
                sc = _score(q, i)
                if sc > bs:
                    best, bs = i, sc
            need = 0.12 if len(q) <= 2 else 0.2
            if not best or bs < need:
                return None                    # по теме нет — пусть ответит движок
        else:                                  # «привет», «расскажи что-нибудь»
            best, bs = max(pool, key=lambda i: i.get("ts", 0)), 0.0
        chosen = [best]
        text = best["text"]
        cur = set(q) | set(best.get("stems") or [])
        taken = {best["id"]}
        while len(text) < limit * 0.9:
            cand, cs = None, 0.0
            for i in pool:
                if i["id"] in taken:
                    continue
                s = set(i.get("stems") or [])
                shared = len(cur & s)
                if shared < 2:                 # тема теряется — стоп
                    continue
                sc = (shared / (len(s) ** 0.5)) + (0.3 if q & s else 0.0)
                if sc > cs:
                    cand, cs = i, sc
            if not cand or len(text) + 2 + len(cand["text"]) > limit:
                break
            chosen.append(cand)
            taken.add(cand["id"])
            text += "\n\n" + cand["text"]
            cur |= set(cand.get("stems") or [])
        if len(text) > limit:                   # одну длинную мысль — по границе предложения
            cut = text[:limit]
            p = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
            text = cut[:p + 1] if p > limit * 0.5 else cut
        for c in chosen:
            c["uses"] = int(c.get("uses", 0)) + 1
        used = (used + [c["id"] for c in chosen])[-40:]
        d["used"][chat] = used
        if len(d["used"]) > 200:
            d["used"] = dict(list(d["used"].items())[-100:])
        store.save_json(_FILE, d)
    return {"text": text, "ids": [c["id"] for c in chosen], "n": len(chosen),
            "score": round(bs, 3)}


def stats():
    with _LOCK:
        d = _load()
    out = {"items": len(d["items"]), "chars": sum(len(i["text"]) for i in d["items"]),
           "chats": len(d["used"])}
    try:
        import thoughtsmooth
        out["smooth"] = thoughtsmooth.status()
    except Exception:
        pass
    return out
