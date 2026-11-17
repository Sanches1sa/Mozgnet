# -*- coding: utf-8 -*-
# ===========================================================================
# lexicon.py — СЛОВАРЬ СЛОВОФОРМ ДЛЯ ГЕНЕРАЦИИ СВОЕЙ LLM (v3.45, stdlib)
# ---------------------------------------------------------------------------
# Свою LLM обучают на словах-токенах «▁слово» и буквах-осколках; недоученная
# модель выдаёт слова «по буквам» и получается тарабарщина. Этот словарь
# НЕ участвует в обучении — он только ограничивает выбор при генерации:
# слово, собранное из букв, допускается лишь если оно есть в словаре.
# Словарь = слова корпуса (база знаний, мысли; слово встретилось ≥2 раз)
# + слова из токенов модели + СКЛОНЕНИЯ/СПРЯЖЕНИЯ: если основа встретилась
# минимум с двумя окончаниями одной парадигмы (сеть, сети), достраиваются
# остальные формы этой парадигмы (сетью, сетей, сетям…). Это простая
# окончательная морфология, а не полный словарь русского языка — редкие
# формы могут быть и лишние, и пропущенные.
# ===========================================================================
import json
import os
import re
import threading
import time

import store

WORD_OK = re.compile(r"^(?:[а-яё]{1,24}|[a-z]{1,24})$")
MAX_WORDS = 200000
REFRESH_S = 3600.0

# парадигмы: набор окончаний одной модели склонения/спряжения
_PARADIGMS = [
    ("ый ого ому ым ом ая ой ую ое ые ых ыми".split()),          # новый
    ("ий его ему им ем яя ей юю ее ие их ими".split()),          # синий
    ("а ы е у ой ою ам ами ах".split()),                         # сеть→вода
    ("я и е ю ей ёй ею ям ями ях".split()),                      # земля
    ("ь и ью ей ям ями ях".split()),                             # сеть
    (["", "а", "у", "ом", "е", "ы", "ов", "ам", "ами", "ах"]),   # стол
    ("ь я ю ем е и ей ям ями ях".split()),                       # день
    ("о а у ом е ы ам ами ах".split()),                          # слово
    ("е я ю ем и ям ями ях".split()),                            # поле
    ("ть л ла ло ли ю ешь ет ем ете ют ться лся лась лись ется ются".split()),
    ("ить ил ила ило или ю ишь ит им ите ят ится ятся".split()),
]

_LOCK = threading.RLock()
_ST = {"lex": None, "ts": 0.0, "building": False, "src": ""}


class Lex(object):
    def __init__(self, words):
        self.words = set(words)
        pref = set()
        for w in self.words:
            for i in range(1, len(w)):
                pref.add(w[:i])
        self.pref = pref

    def __len__(self):
        return len(self.words)


def _expand(counts):
    """{слово: n} -> множество достроенных словоформ."""
    gen = set()
    for par in _PARADIGMS:
        ends = {}
        for e in par:
            ends.setdefault(len(e), set()).add(e)
        stems = {}
        for w in counts:
            for k, es in ends.items():
                if len(w) - k < 3:
                    continue
                e = w[len(w) - k:] if k else ""
                if e in es:
                    stems.setdefault(w[:len(w) - k], set()).add(e)
        for st, es in stems.items():
            if len(es) >= 2:
                for e in par:
                    gen.add(st + e)
        if len(gen) > MAX_WORDS:
            break
    return gen


def _good(w):
    return bool(WORD_OK.match(w)) and not re.search(r"(.)\1{3,}", w)


STRICT_MIN = 3        # строгий режим: слово должно встретиться в базе ≥3 раз


def _strict():
    try:
        return bool(store.settings().get("llm_words_strict", True))
    except Exception:
        return True


def _from_counts(counts, vocab_words, strict=False):
    if strict:
        # v3.66: БЕЗ достройки склонений (она порождала выдуманные формы) и
        # без «слов» из токенов, которых нет в базе: только то, что реально
        # встречается в материалах не реже STRICT_MIN раз
        base = {w for w, n in counts.items() if n >= STRICT_MIN and _good(w)}
        base |= {w for w in vocab_words
                 if _good(w) and counts.get(w, 0) >= 2}
        return base
    base = {w for w, n in counts.items() if n >= 2 and _good(w)}
    base |= {w for w in vocab_words if _good(w)}
    gen = _expand({w: 1 for w in base})
    words = base | {w for w in gen if _good(w)}
    if len(words) > MAX_WORDS:
        words = set(sorted(words, key=lambda w: (w not in base, len(w)))
                    [:MAX_WORDS])
    return words


def _vocab_words(model):
    out = []
    try:
        for tk in model.tok.vocab:
            if tk.startswith("▁") and len(tk) >= 2:
                out.append(tk[1:])
    except Exception:
        pass
    return out


def _path():
    return store.p("lexicon.json")


def _build(model):
    """Тяжёлая часть (корпус базы знаний): фон."""
    try:
        import tinyllm
        texts, _n = tinyllm._collect_corpus(400000)
        counts = {}
        for t in texts:
            if str(t).startswith("⟦"):          # код — не слова языка
                continue
            for w in re.findall(r"[а-яёa-z]+", str(t).lower()):
                counts[w] = counts.get(w, 0) + 1
        strict = _strict()
        words = _from_counts(counts, _vocab_words(model), strict)
        tmp = _path() + ".tmp"
        os.makedirs(os.path.dirname(_path()) or ".", exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"ts": time.time(), "strict": strict,
                       "words": sorted(words)}, f,
                      ensure_ascii=False)
        os.replace(tmp, _path())
        with _LOCK:
            _ST.update(lex=Lex(words), ts=time.time(), strict=strict,
                       src="корпус" + (" (строгий)" if strict else ""))
    except Exception as e:
        store.log("СЛОВАРЬ", "не собрался: %s: %s" % (type(e).__name__, e))
    finally:
        _ST["building"] = False


def get(model):
    """Словарь для генерации. Возвращается сразу (из файла или из токенов
    модели); устаревший пересобирается в фоне раз в час."""
    with _LOCK:
        lex, ts = _ST["lex"], _ST["ts"]
        if lex is None:
            try:
                with open(_path(), encoding="utf-8") as f:
                    d = json.load(f)
                lex = Lex(d.get("words") or [])
                ts = float(d.get("ts") or 0)
                if bool(d.get("strict")) != _strict():
                    ts = 0.0               # режим сменился — пересобрать
                _ST.update(lex=lex, ts=ts, strict=bool(d.get("strict")),
                           src="файл")
            except Exception:
                lex = None
        if lex is None:                         # пока только токены модели
            vw = _vocab_words(model)
            lex = Lex(_from_counts({}, vw) | set(vw))
            _ST.update(lex=lex, ts=0.0, src="токены модели")
            ts = 0.0
        if _ST.get("strict") is not None and _ST["lex"] is not None \
                and bool(_ST.get("strict")) != _strict():
            ts = 0.0
        if time.time() - ts > REFRESH_S and not _ST["building"]:
            _ST["building"] = True
            threading.Thread(target=_build, args=(model,), daemon=True,
                             name="lexicon").start()
    return lex


def status():
    lx = _ST["lex"]
    return {"words": len(lx) if lx else 0, "src": _ST["src"],
            "age_min": int((time.time() - _ST["ts"]) / 60) if _ST["ts"]
            else None, "building": _ST["building"]}


def purge(text, lex, extra_ok=None):
    """Выбросить из готового текста слова, которых нет в словаре (выдумки).
    -> (текст, сколько_слов_выброшено, сколько_слов_осталось)."""
    if lex is None:
        return text, 0, len(re.findall(r"[А-Яа-яЁёA-Za-z]+", str(text)))
    bad = [0]
    keep = [0]

    def rep(m):
        w = m.group(0)
        wl = w.lower()
        if len(wl) <= 2 or wl in lex.words or (extra_ok and wl in extra_ok):
            keep[0] += 1
            return w
        bad[0] += 1
        return ""
    out = re.sub(r"[А-Яа-яЁёA-Za-z]+", rep, str(text))
    out = re.sub(r"\s+([,.;:!?])", r"\1", out)
    out = re.sub(r"([,;:])(\s*[,;:])+", r"\1", out)
    out = re.sub(r"[,;:]+\s*([.!?…])", r"\1", out)
    out = re.sub(r"\s{2,}", " ", out).strip(" ,;:-")
    return out, bad[0], keep[0]
