# -*- coding: utf-8 -*-
# ===========================================================================
# boardmind.py — ОБЛАКО ЧИТАЕТ МЫСЛИ ПЛАТ ЦЕЛИКОМ (v3.95, только stdlib)
# ---------------------------------------------------------------------------
# Заявка владельца: «облако полностью прочитывает мысли плат и учитывает их
# при прочитывании материалов, просмотре роликов».
#   * читает ВСЕ мысли плат: ленту мыслей (thoughts.json) и чат «Мысли плат»;
#     прочитанное помнится по времени — каждая мысль читается один раз;
#   * из мыслей строится «интерес плат» — основы слов с весом (свежие мысли
#     весят больше, старое затухает: полураспад ~2 суток);
#   * любопытство берёт тему из интереса плат, из найденных статей Википедии
#     и роликов выбирает те, что ближе к мыслям плат;
#   * прочитав статью/ролик, облако выписывает предложения, созвучные
#     мыслям плат, и кладёт связку «мысль платы ↔ материал» в сеть
#     материалов и ленту мыслей (плата видит, что её мысль нашла ответ).
# Честно: это совпадение слов (основ), а не понимание смысла мыслей.
# ===========================================================================
import math
import re
import threading
import time

import store

_LOCK = threading.RLock()
_FILE = "boardmind.json"
_HALF = 2 * 86400.0               # полураспад веса интереса
_WORD = re.compile(r"[а-яё]{4,}", re.I)
_STOP = frozenset("""этот этого этому этой which тоже также потом сейчас когда
который которая которые которых будет было были была есть может можно нужно
очень всего всех себя свой своя свои люблю любит хочет хотят мысль мысли плата платы облако сеть
сети слово слова фраза фразы частота сигнал хочу знать думаю
англ греч франц итал польск нидерл санскр""".split())


def _stem(w):
    return w.lower().replace("ё", "е")[:6]


def _load():
    d = store.load_json(_FILE, None) or {}
    d.setdefault("seen_ts", 0.0)
    d.setdefault("chat_ts", 0.0)
    d.setdefault("w", {})            # основа -> [вес, ts, слово]
    d.setdefault("read", 0)
    d.setdefault("links", 0)
    for k_ in [k_ for k_ in d["w"] if k_[:4] in ("англ", "греч", "фран")
               and len(k_) <= 5]:
        d["w"].pop(k_, None)        # v3.118: «англ» — не интерес платы
    return d


def _save(d):
    w = d["w"]
    if len(w) > 4000:                # самые слабые — прочь
        for k in sorted(w, key=lambda k: w[k][0])[:len(w) - 4000]:
            w.pop(k, None)
    store.save_json(_FILE, d)


def _decayed(v, now):
    return float(v[0]) * math.pow(0.5, (now - float(v[1])) / _HALF)


def _absorb(d, text, now):
    for m in _WORD.finditer(str(text)):
        word = m.group(0).lower()
        st = _stem(word)
        if st in _STOP or word in _STOP:
            continue
        v = d["w"].get(st)
        cur = _decayed(v, now) if v else 0.0
        forms = dict(v[3]) if v and len(v) > 3 else {}
        forms[word] = forms.get(word, 0) + 1
        if len(forms) > 6:
            forms = dict(sorted(forms.items(), key=lambda kv: -kv[1])[:6])
        best = sorted(forms.items(), key=lambda kv: (-kv[1], len(kv[0])))[0][0]
        d["w"][st] = [round(cur + 1.0, 4), now, best, forms]


def ingest():
    """Прочитать все НОВЫЕ мысли плат. Возврат: сколько прочитано."""
    n = 0
    try:                                   # v3.132: впитывает на вдохе
        import breath as _brb
        if not _brb.inhale("Мысли плат"):
            return 0
    except Exception:
        pass
    now = time.time()
    with _LOCK:
        d = _load()
        try:
            for t in reversed(store.thoughts_list(100000) or []):
                ts = float(t.get("ts") or 0)
                if ts <= d["seen_ts"] or t.get("kind") in ("llm", "read"):
                    continue
                _absorb(d, t.get("text"), now)
                d["seen_ts"] = max(d["seen_ts"], ts)
                n += 1
        except Exception:
            pass
        try:
            chat = store.chat_load("thoughts") or {}
            for m in chat.get("messages") or []:
                ts = float(m.get("ts") or 0)
                if ts <= d["chat_ts"]:
                    continue
                _absorb(d, m.get("text"), now)
                d["chat_ts"] = max(d["chat_ts"], ts)
                n += 1
        except Exception:
            pass
        if n:
            d["read"] = int(d["read"]) + n
            _save(d)
    return n


def interest(top=40):
    """[(основа, вес, слово)] — о чём сейчас думают платы, сильные первыми."""
    now = time.time()
    with _LOCK:
        d = _load()
        out = [(k, _decayed(v, now), v[2]) for k, v in d["w"].items()]
    out = [x for x in out if x[1] > 0.05]
    out.sort(key=lambda x: -x[1])
    return out[:top]


def score(text, it=None):
    """Насколько текст созвучен мыслям плат (сумма весов общих основ)."""
    it = it if it is not None else interest(80)
    if not it:
        return 0.0
    w = {k: v for k, v, _w in it}
    st = {_stem(m.group(0)) for m in _WORD.finditer(str(text or ""))}
    return round(sum(w[s] for s in st if s in w), 3)


def topic():
    """Тема для любопытства из мыслей плат (слово с наибольшим весом, о
    котором в базе ещё нет статьи). None — мыслей нет."""
    it = interest(30)
    if not it:
        return None
    try:
        import kb
        titles = {str(a.get("title") or "").lower()
                  for a in kb.list_articles(limit=100000)}
    except Exception:
        titles = set()
    for _st, _w, word in it:
        if word not in titles and len(word) >= 5:
            return word
    return None


def pick(cands, key=lambda c: c):
    """Из кандидатов (статьи, ролики) — ближайший к мыслям плат (стабильно:
    при равенстве — прежний порядок)."""
    it = interest(80)
    if not it or not cands:
        return list(cands)
    sc = [(-score(key(c), it), i, c) for i, c in enumerate(cands)]
    sc.sort()
    return [c for _s, _i, c in sc]


def link(title, text, kind="статья"):
    """Материал прочитан: предложения, созвучные мыслям плат -> связка в сеть
    материалов и ленту мыслей. Возврат: сколько предложений связано."""
    it = interest(80)
    if not it:
        return 0
    sents = re.split(r"(?<=[.!?])\s+", str(text or ""))
    good = []
    for s in sents:
        s = s.strip()
        if 30 <= len(s) <= 300:
            sc = score(s, it)
            if sc >= 1.0:
                good.append((sc, s))
    if not good:
        return 0
    good.sort(key=lambda x: -x[0])
    top = [s for _sc, s in good[:3]]
    words = ", ".join(w for _s, _w, w in it[:5])
    note = "Мысли плат (%s) ↔ %s «%s»: %s" % (words, kind, str(title)[:80],
                                            " ".join(top))[:390]
    try:
        import brain
        brain.mn_learn(note, title=("мысли плат ↔ %s" % title)[:80],
                       src="мысли плат")
    except Exception:
        pass
    try:
        store.thoughts_add("облако", note, kind="read")
    except Exception:
        pass
    with _LOCK:
        d = _load()
        d["links"] = int(d["links"]) + 1
        _save(d)
    return len(top)


def status():
    with _LOCK:
        d = _load()
    return {"read": d["read"], "links": d["links"],
            "interest": [w for _s, _v, w in interest(10)]}
