# -*- coding: utf-8 -*-
# ===========================================================================
# wordmind.py — РЕЧЬ ИЗ СЛОВАРЯ (v3.12): нейросеть облака СОБИРАЕТ ОТВЕТЫ
# ИЗ СЛОВ СЛОВАРЯ — честно, по образцу brain_speak платы neuro_12 (v2.16).
# ---------------------------------------------------------------------------
# ЗАЯВКА ВЛАДЕЛЬЦА: «пусть нейросеть в мозге-12 собирает из слов в словаре
# ответы на вопросы в чате на сервере — только честно; убрать все
# автоматические ответы».
#
# КАК ЭТО РАБОТАЕТ (никакого LLM, никаких заготовленных фраз):
#   1) СЛОВАРЬ: все слова облачных словарей — ru_top, ru_pos, extra,
#      словари плат и встроенный словарь учителя. Узлы сети речи — ТОЛЬКО
#      словарные слова: выдуманное слово физически не может быть сказано.
#   2) СЕТЬ ЗНАНИЙ РЕЧИ: биграммы «слово -> слово» по ВСЕМ материалам
#      мозга (сеть материалов mozgnet: статьи базы знаний, файлы, диалоги,
#      мысли плат) и заметкам — но в сеть речи попадают только словарные
#      слова из этих материалов.
#   3) СБОРКА ОТВЕТА: вопрос зажигает узлы НЕЙРОСЕТИ МАТЕРИАЛОВ (mozgnet,
#      разрядка активации), активные основы дают бонус своим словарным
#      словам; дальше — ход по выученным биграммам, взвешенный выбор
#      (частота связи + активность нейронов). Так нейросеть «говорит».
#   4) ЧЕСТНОСТЬ: если по теме ни одного знакомого словарного слова со
#      связями нет — сеть речи молчит (compose вернёт ""), и чат честно
#      говорит «не знаю». Никаких заготовок: всё, что сказано, собрано из
#      словаря по выученным связям.
# ===========================================================================
import os
import random
import re
import threading
import time

import store

_WORD_RE = re.compile(r"[а-яёa-z]{3,}")

MIN_LINKS = 20          # меньше связей — сеть речи ещё не готова говорить
REBUILD_EVERY = 90.0    # сек между пересборками сети речи
VOCAB_TTL = 600.0       # сек кэша словаря
MAX_WORDS = 12          # слов в одной фразе (как на плате: GEN_MAX_WORDS)
MAX_PHRASES = 2         # фраз в ответе
SRC_TEXTS_MAX = 6000    # предел текстов-источников за сборку
SRC_LEN = 1200          # сколько знаков статьи брать в сеть речи

_ST = {"big": {},        # {слово: {следующее слово: счёт}}
       "uni": {},        # {слово: частота}
       "by_stem": {},    # {основа(5): [слова]} — от основы к словарным словам
       "links": 0,
       "built": 0.0,
       "busy": False,
       "errs": "",
       "srcs": 0}        # сколько текстов-источников ушло в сборку

_VOCAB = {"words": set(), "ts": 0.0}
_LOCK = threading.RLock()


# ------------------------------- словарь ------------------------------------
def _load_vocab():
    """Все слова облачных словарей: ru_top/extra (words), ru_pos (части
    речи) и встроенный словарь учителя. Кэш VOCAB_TTL секунд."""
    now = time.time()
    if _VOCAB["words"] and now - _VOCAB["ts"] < VOCAB_TTL:
        return _VOCAB["words"]
    words = set()
    for name in ("ru_top", "extra"):
        d = store.load_json("dicts/%s.json" % name, {}) or {}
        for w in (d.get("words") or []):
            w = str(w).lower().strip()
            if 2 < len(w) < 24 and re.match(r"^[а-яёa-z-]+$", w):
                words.add(w)
    d = store.load_json("dicts/ru_pos.json", {}) or {}
    for k in ("nouns", "verbs", "adjs"):
        for w in (d.get(k) or []):
            w = str(w).lower().strip()
            if 2 < len(w) < 24:
                words.add(w)
    try:
        import teacher
        words.update(w for w in teacher._BASIC if 2 < len(w) < 24)
    except Exception:
        pass
    try:                                   # v3.118: «англ», «лат» — не слова
        import llm2
        words -= llm2.JUNK_W
    except Exception:
        pass
    _VOCAB["words"] = words
    _VOCAB["ts"] = now
    return words


def dict_has(w):
    """Есть ли слово в словаре (сеть речи говорит только словарём)."""
    return str(w or "").lower() in _load_vocab()


def vocab_size():
    return len(_load_vocab())


# --------------------------- источники знаний --------------------------------
def _source_texts():
    """Все тексты, которые мозг знает: предложения сети материалов
    (статьи KB, файлы, диалоги, мысли плат — всё, что выучил mozgnet),
    статьи базы знаний и заметки владельца."""
    texts = []
    try:
        import brain
        with brain._MN_LOCK:
            m = brain.mn()
            texts.extend(str(s.get("t") or "") for s in m.sents)
    except Exception:
        pass
    try:
        import kb
        for meta in (kb.list_articles(limit=200) or []):
            art = kb.article(meta.get("id"))
            if art and art.get("text"):
                texts.append("%s. %s" % (art.get("title") or "",
                                         str(art["text"])[:SRC_LEN]))
    except Exception:
        pass
    try:
        for n in (store.notes() or [])[-100:]:
            texts.append(str(n.get("text") or ""))
    except Exception:
        pass
    return [t for t in texts if t.strip()][:SRC_TEXTS_MAX]


# ----------------------------- сборка сети -----------------------------------
def _rebuild():
    """Пересобрать биграммную сеть знаний речи. Узлы — только словарные
    слова: генерация физически не может собрать выдумку (как на плате)."""
    vocab = _load_vocab()
    big, uni = {}, {}
    srcs = 0
    for t in _source_texts():
        srcs += 1
        ws = [w for w in _WORD_RE.findall(str(t or "").lower())
              if w in vocab][:400]
        for i in range(len(ws) - 1):
            a, b = ws[i], ws[i + 1]
            if a == b:
                continue
            uni[a] = uni.get(a, 0) + 1
            nxt = big.setdefault(a, {})
            nxt[b] = nxt.get(b, 0) + 1
    by_stem = {}
    for w in uni:
        by_stem.setdefault(w[:5], []).append(w)
    with _LOCK:
        _ST["big"] = big
        _ST["uni"] = uni
        _ST["by_stem"] = by_stem
        _ST["links"] = sum(sum(d.values()) for d in big.values())
        _ST["built"] = time.time()
        _ST["busy"] = False
        _ST["errs"] = ""
        _ST["srcs"] = srcs
    return _ST["links"]


def _rebuild_async():
    if _ST.get("busy"):
        return

    def _job():
        try:
            _rebuild()
        except Exception as e:
            with _LOCK:
                _ST["busy"] = False
                _ST["errs"] = str(e)[:120]

    with _LOCK:
        _ST["busy"] = True
    threading.Thread(target=_job, daemon=True).start()


def maybe_rebuild(force=False):
    """Пересобрать сеть речи, если устарела (раз в REBUILD_EVERY сек)."""
    if force or (time.time() - _ST["built"] > REBUILD_EVERY and
                 not _ST["busy"]):
        _rebuild_async()
    return not _ST["busy"]


def tick():
    """Фон мозга (brain._worker): держать сеть речи свежей."""
    try:
        maybe_rebuild()
    except Exception:
        pass


# ------------------------------ выбор слова ----------------------------------
def _weighted(cands, hot):
    """v3.133: подбор слова — ТА ЖЕ формула, что у генератора платы
    (shareddict.SharedDict.pick: связь + нейроны + частота + дыхание)."""
    try:
        import worddict
        return worddict.pick(cands, hot=hot)
    except Exception:
        pass
    items, weights = [], []
    for w, c in cands:
        sc = 1.0 + (float(c) ** 0.5)
        if hot:
            sc += 2.0 * min(3.0, hot.get(w[:5], 0.0))
        items.append(w)
        weights.append(max(0.1, sc))
    total = sum(weights)
    r = random.uniform(0, total)
    acc = 0.0
    for w, wt in zip(items, weights):
        acc += wt
        if r <= acc:
            return w
    return items[-1] if items else ""

def _pool(top=120):
    """Частотный пул словарных слов сети речи — для «прыжков» (как на плате)."""
    with _LOCK:
        uni = _ST["uni"]
    if not uni:
        return []
    return [w for w, _c in sorted(uni.items(), key=lambda kv: -kv[1])[:top]]


def _phrase(start, hot, used):
    """Одна фраза: ход по биграммам от стартового слова, прыжки по пулу."""
    with _LOCK:
        big = _ST["big"]
    out = [start]
    cur = start
    jumps = 0
    for _ in range(MAX_WORDS - 1):
        succ = dict(big.get(cur) or {})
        try:                         # v3.133: + связи общего словаря (плата + облако)
            import worddict
            for w_, c_ in worddict.get().successors(cur):
                if w_ not in succ:
                    succ[w_] = c_
        except Exception:
            pass
        nxt = _weighted(list(succ.items()), hot) if succ else ""
        if (not nxt or nxt in out or nxt in used) and jumps < 2:
            jumps += 1
            pool = [w for w in _pool() if w not in out and w not in used]
            nxt = random.choice(pool) if pool else ""
        if not nxt or nxt in out or nxt in used:
            break
        out.append(nxt)
        cur = nxt
    return out


# ------------------------------- сборка ответа --------------------------------
def compose(topic):
    """СОБРАТЬ ОТВЕТ ИЗ СЛОВ СЛОВАРЯ по теме (вопросу).
    Возврат: (фраза | "", info). Честность: пусто — значит знаний нет."""
    topic = str(topic or "").strip()[:200]
    if not topic:
        return "", {"why": "пустая тема"}
    maybe_rebuild()
    with _LOCK:
        st = dict(_ST)
    if st["links"] < MIN_LINKS or not st["uni"]:
        return "", {"why": "сеть речи ещё не собрана (связей %d)"
                          % st["links"]}
    vocab = _load_vocab()
    # — нейросеть материалов зажигается вопросом: её активные основы
    #   будут вести выбор словарных слов
    hot = {}
    try:
        import brain
        q_stems = []
        with brain._MN_LOCK:
            m = brain.mn()
            seeds = m._query_stems(topic)
            act = m.spread(seeds) if seeds else {}
        hot = {k: v for k, v in act.items() if len(k) >= 4}
        q_stems = list(seeds or [])
    except Exception:
        q_stems = []
    # — старт: словарные слова темы (точные или по общей основе),
    #   приоритет — слова с выученными связями
    q_words = [w for w in _WORD_RE.findall(topic.lower())]
    cands = []
    for w in q_words:
        if w in st["big"]:
            cands.append((w, 3.0 + float(st["uni"].get(w, 0)) ** 0.5))
        elif w in vocab and st["uni"].get(w):
            cands.append((w, 1.5))
    if not cands:
        for stem in q_stems or [w[:5] for w in q_words]:
            for w in st["by_stem"].get(stem[:5], []):
                if w in st["big"]:
                    cands.append((w, 2.0 + float(st["uni"].get(w, 0)) ** 0.5))
    if not cands:
        # по активным основам сети: о чём «думает» мозг по этой теме
        for stem in sorted(hot, key=lambda k: -hot[k])[:12]:
            for w in st["by_stem"].get(stem[:5], []):
                if w in st["big"]:
                    cands.append((w, 1.0 + hot[stem]))
    if not cands:
        return "", {"why": "по теме нет знакомых словарных слов со связями",
                    "links": st["links"]}
    random.shuffle(cands)
    cands.sort(key=lambda wc: -wc[1])
    used = set()
    phrases = []
    for w, _sc in cands[:MAX_PHRASES * 2]:
        chain = _phrase(w, hot, used)
        if len(chain) >= 2:
            phrases.append(chain)
            used.update(chain)
        if len(phrases) >= MAX_PHRASES:
            break
    if not phrases:
        return "", {"why": "связей у знакомых слов не хватило на фразу",
                    "links": st["links"]}
    out = []
    for chain in phrases:
        p = " ".join(chain).strip(" ,")
        p = p[0].upper() + p[1:] + "."
        out.append(p)
    info = {"words": sum(len(c) for c in phrases), "links": st["links"],
            "hot": sorted(hot, key=lambda k: -hot[k])[:5],
            "vocab": len(vocab), "start": phrases[0][0]}
    return " ".join(out), info


def status():
    """Состояние сети речи (панель, /api/speech)."""
    with _LOCK:
        st = dict(_ST)
    return {"words": len(st["uni"]), "links": st["links"],
            "built": st["built"], "age": int(time.time() - st["built"])
            if st["built"] else 0,
            "vocab": vocab_size(), "srcs": st["srcs"],
            "busy": st["busy"], "errs": st["errs"],
            "ready": st["links"] >= MIN_LINKS}
