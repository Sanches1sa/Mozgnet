# -*- coding: utf-8 -*-
"""
coherence.py — v3.121: СВЯЗНОСТЬ МЫСЛЕЙ МОЗГА И ОБУЧЕНИЕ ПЛАТЫ НЕ ПИСАТЬ АХИНЕЮ.

Мозг платы складывает фразу цепочкой «слово -> слово, которое где-то шло
после него». Каждая пара по отдельности бывает правильной, а фраза целиком —
«Какую капли воды облаках отвечать, вопросы всячески помогать тому кто».

Судья: доля пар соседних слов фразы, которые встречаются в НАСТОЯЩИХ текстах
(база знаний, подтверждённые ответы, заметки). Связная фраза из чужих слов
почти вся состоит из таких пар; «салат» — почти нет.

  • Ответом в чате мысль мозга идёт, только если связность ≥ порога
    (brain_coh_min, по умолчанию 0,5). Иначе — не показывается (мозг
    отвечает памятью/LLM или честным «не знаю»).
  • ОБУЧЕНИЕ: каждая мысль платы (канал связи, ответы по почте, прямые
    вопросы) получает вердикт; плата забирает вердикты по каналу связи
    и ослабляет пары слов, которых нет в настоящих текстах (и немного
    усиливает пары связных мыслей) — мозг постепенно перестаёт их собирать.

Пары хранятся как отсортированный массив 64-битных хешей (numpy): миллионы
пар — десятки МБ, проверка — двоичный поиск.
"""
import hashlib
import re
import threading
import time

import store

_W = re.compile(r"[а-яёa-z0-9]+", re.I)
_LOCK = threading.RLock()
_ST = {"arr": None, "n_pairs": 0, "n_words": 0, "ts": 0.0, "busy": False,
       "err": ""}
_V = {}                       # плата -> [вердикты]
_N = {"judged": 0, "bad": 0, "good": 0, "blocked": 0}
REBUILD_S = 1800.0
MAX_WORDS = 4000000


def _words(t):
    return [w.lower().replace("ё", "е") for w in _W.findall(str(t or ""))]


def _h(a, b):
    return int.from_bytes(hashlib.blake2b(
        (a + " " + b).encode("utf-8"), digest_size=8).digest(), "little",
        signed=True)


def _texts():
    try:
        import kb
        for a in kb.list_articles(limit=1000000, include_devices=True) or []:
            art = kb.article(a.get("id")) if a.get("id") else None
            if art and art.get("text"):
                yield str(art.get("title") or "") + ". " + str(art["text"])
    except Exception:
        pass
    try:
        import qalearn
        for p in qalearn.export_verified(0.0) or []:
            yield str(p.get("a") or "")
    except Exception:
        pass
    try:                                   # самоучитель/заметки владельца
        for n in (store.load_json("notes.json", []) or []):
            if isinstance(n, dict):
                yield str(n.get("text") or "")
    except Exception:
        pass


def _build():
    try:
        import numpy as np
        try:
            import llm2
            norm = llm2.normalize
        except Exception:
            norm = (lambda x: x)
        from array import array
        hs, nw = array("q"), 0
        for t in _texts():
            for sent in re.split(r"(?<=[.!?…])\s+|\n+", norm(t)):
                ws = _words(sent)
                nw += len(ws)
                for a, b in zip(ws, ws[1:]):
                    hs.append(_h(a, b))
            if nw >= MAX_WORDS:
                break
        arr = np.unique(np.frombuffer(hs, dtype=np.int64)) if len(hs) else None
        del hs
        with _LOCK:
            _ST.update(arr=arr, n_pairs=0 if arr is None else int(arr.size),
                       n_words=nw, ts=time.time(), err="")
        store.log("СВЯЗНОСТЬ", "судья мыслей готов: %d пар слов из %d слов "
                  "настоящих текстов" % (_ST["n_pairs"], nw))
    except Exception as e:
        _ST["err"] = "%s: %s" % (type(e).__name__, e)
    finally:
        _ST["busy"] = False


def warm():
    """Построить (или обновить) судью фоном."""
    with _LOCK:
        if _ST["busy"]:
            return
        if _ST["arr"] is not None and time.time() - _ST["ts"] < REBUILD_S:
            return
        if _ST["arr"] is not None:     # v3.132: перестройка — только на вдохе
            try:
                import breath as _brc
                if not _brc.inhale("Судья связности"):
                    return
            except Exception:
                pass
        _ST["busy"] = True
    threading.Thread(target=_build, daemon=True, name="coherence").start()


def threshold():
    try:
        t = max(0.0, min(1.0, float(store.settings().get("brain_coh_min", 0.5))))
    except (TypeError, ValueError):
        t = 0.5
    try:                     # v3.146: строгость дышит с платами (тензор модуляции)
        import tensormod
        t *= tensormod.scalar("Судья связности", 0, 1)
    except Exception:
        pass
    return max(0.0, min(1.0, t))


def judge(text):
    """-> {"ok": bool, "score": 0..1|None, "pairs": n, "bad": [(a,b)]}.
    Судья не готов или пар меньше 3 — ok (не мешаем)."""
    warm()
    ws = []
    for sent in re.split(r"(?<=[.!?…])\s+", str(text or "")):
        ws.append(_words(sent))
    pairs = [(a, b) for s in ws for a, b in zip(s, s[1:])]
    arr = _ST["arr"]
    if arr is None or len(pairs) < 3:
        return {"ok": True, "score": None, "pairs": len(pairs), "bad": []}
    import numpy as np
    hs = np.array([_h(a, b) for a, b in pairs], dtype=np.int64)
    idx = np.searchsorted(arr, hs)
    idx[idx >= arr.size] = arr.size - 1
    seen = arr[idx] == hs
    score = float(seen.mean())
    bad = [p for p, s in zip(pairs, seen) if not s]
    ok = score >= threshold()
    _N["judged"] += 1
    _N["good" if ok else "bad"] += 1
    return {"ok": ok, "score": round(score, 2), "pairs": len(pairs),
            "bad": bad[:12]}


def verdict(board, text, j):
    """Запомнить вердикт для платы (заберёт по каналу связи)."""
    if not board or j.get("score") is None:
        return
    with _LOCK:
        q = _V.setdefault(str(board)[:40], [])
        q.append({"t": str(text)[:240], "ok": bool(j["ok"]),
                  "score": j["score"], "bad": [list(p) for p in j["bad"]]})
        del q[:-20]


def take(board):
    with _LOCK:
        return _V.pop(str(board)[:40], [])


def gate(text, board="", where=""):
    """Мысль мозга как ответ: (годится?, оценка). Вердикт — плате."""
    j = judge(text)
    if board:
        verdict(board, text, j)
    if not j["ok"]:
        _N["blocked"] += 1
        store.log("СВЯЗНОСТЬ", "мысль мозга%s не показана как ответ "
                  "(связность %d%% < %d%%): %s" % (
                      (" «%s»" % board) if board else "",
                      int(100 * (j["score"] or 0)), int(100 * threshold()),
                      str(text)[:120]))
    return j["ok"], j


def status():
    return {"ready": _ST["arr"] is not None, "pairs": _ST["n_pairs"],
            "words": _ST["n_words"], "threshold": threshold(),
            "err": _ST["err"], **_N}
