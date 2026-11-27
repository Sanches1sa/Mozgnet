# thoughtsmooth.py — v3.214: СВОЯ LLM ОБЛАКА СГЛАЖИВАЕТ РАЗРОЗНЕННЫЕ МЫСЛИ
#
# Мысль платы длинная и собрана из слов нейронов — предложения могут быть про
# разное. Здесь оценивается связность и, если мысль разрознена, своя LLM
# облака (tinyllm, без внешних сервисов) собирает контекст:
#   1) предложения группируются по общим словам (основам) в смысловые нити;
#   2) нити упорядочиваются: сначала связанные с вопросом/самая крупная;
#      одиночные предложения без связей отбрасываются (если остаётся ≥3);
#   3) tinyllm.compose по фактам из самой главной нити пишет итоговое
#      предложение «Итог: …» — только из слов мысли (grounded >= порога).
# Обновляется ТОЛЬКО копия в облаке (банк мыслей, ответы бота); мысль на
# плате не меняется. Код в ```…``` и мысли короче 3 предложений не трогаются.
import re

import store

_WORD = re.compile(r"[а-яё]{4,}")
_SENT = re.compile(r"(?<=[.!?…])\s+")
_STOP = {"этот", "этого", "этой", "того", "тоже", "если", "когда", "чтобы",
         "который", "которые", "очень", "можно", "также", "потому", "после",
         "между", "через", "более", "только", "быть", "есть", "было", "были",
         "что", "как", "или", "для", "при", "над", "под", "про"}
THRESHOLD = 0.5
MAX_LEADS = 3                       # итоговых предложений от LLM (v3.215: больше)
_ST = {"seen": 0, "smoothed": 0, "dropped": 0, "llm": 0}


def _stems(s):
    return {w[:5] for w in _WORD.findall(str(s).lower()) if w not in _STOP}


def sentences(text):
    return [s.strip() for s in _SENT.split(str(text or "").strip()) if len(s.strip()) > 3]


def coherence(text):
    """0…1: доля соседних предложений с общими словами и доля предложений,
    связанных хоть с одним другим."""
    ss = sentences(text)
    if len(ss) < 2:
        return 1.0
    st = [_stems(s) for s in ss]
    adj = sum(1 for i in range(len(st) - 1) if st[i] & st[i + 1]) / (len(st) - 1)
    iso = sum(1 for i, a in enumerate(st)
              if not any(a & b for j, b in enumerate(st) if j != i)) / len(st)
    return round(0.6 * adj + 0.4 * (1 - iso), 3)


def _clusters(st):
    """Нити: объединяем предложения с общими основами (union-find)."""
    n = len(st)
    par = list(range(n))

    def find(x):
        while par[x] != x:
            par[x] = par[par[x]]
            x = par[x]
        return x
    owner = {}
    for i, a in enumerate(st):
        for w in a:
            if w in owner:
                par[find(i)] = find(owner[w])
            else:
                owner[w] = i
    groups = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)
    return list(groups.values())


def _lead(main_sents, topic):
    """Итоговое предложение от своей LLM, только из слов нити.
    -> (текст, причина_отказа)."""
    try:
        import llmlink
        import tinyllm
        out, info = tinyllm.compose(topic, " ".join(main_sents)[:1500])
        if info.get("llm") == "cold":
            return "", "модель LLM ещё не обучена"
        g = float(info.get("grounded") or 0)
        out = " ".join(str(out or "").split())
        if g < llmlink.cfg()["gen_minq"]:
            return "", "итог не опирается на слова мысли (%.2f)" % g
        if not (25 <= len(out) <= 400):
            return "", "итог неподходящей длины"
        if not _stems(out) & _stems(" ".join(main_sents)):
            return "", "итог про другое"
        first = sentences(out)
        return (first[0] if first else ""), ""
    except Exception as e:
        return "", "ошибка LLM: %s" % str(e)[:80]


def _chain(g, st, q):
    """v3.215.1: порядок предложений нити БЕЗ нейромодели — от самого
    «центрального» (больше всего общих слов с остальными, плюс слова вопроса)
    дальше цепочкой: каждое следующее — с наибольшим пересечением с прошлым."""
    g = list(g)
    if len(g) < 3:
        return sorted(g)
    cen = lambda i: sum(len(st[i] & st[j]) for j in g if j != i) + 2 * len(st[i] & q)
    cur = max(g, key=lambda i: (cen(i), -i))
    out, left = [cur], set(g) - {cur}
    while left:
        nxt = max(left, key=lambda j: (len(st[cur] & st[j]), cen(j), -j))
        out.append(nxt); left.discard(nxt); cur = nxt
    return out


def _threshold():
    try:
        return max(0.1, min(0.95, float(store.settings().get("thought_smooth_thr", THRESHOLD))))
    except Exception:
        return THRESHOLD


def _log(who, act, text, out="", board="", **kw):
    try:
        import thoughtjournal
        thoughtjournal.add(who, act, text, out, board, **kw)
    except Exception:
        pass


def smooth(text, question="", use_llm=True, board="", log=False, publish=False):
    """-> (текст, info). info: coherence_before/after, clusters, dropped, llm.
    log=True пишет решение в журнал (мозг/LLM); publish=True публикует
    собранную LLM мысль как мысль LLM (видна в журнале мыслей)."""
    text = str(text or "").strip()
    _ST["seen"] += 1
    info = {"changed": False}
    if not text:
        return text, info
    if "```" in text:
        if log:
            _log("мозг", "skip", text, board=board, note="в мысли код — не трогаю")
        return text, info
    ss = sentences(text)
    if len(ss) < 3:
        if log:
            _log("мозг", "skip", text, board=board, note="короткая мысль (<3 предложений)")
        return text, info
    c0 = coherence(text)
    info["coherence_before"] = c0
    thr = _threshold()
    if c0 >= thr:
        _ST["kept"] = _ST.get("kept", 0) + 1
        if log:
            _log("мозг", "kept", text, board=board, coh0=c0,
                 note="мысль связна (%.2f ≥ %.2f): LLM не нужна, мозг отвечает своими словами" % (c0, thr))
        return text, info
    st = [_stems(s) for s in ss]
    cl = _clusters(st)
    q = _stems(question)
    cl.sort(key=lambda g: (-len(set().union(*[st[i] for i in g]) & q), -len(g), min(g)))
    keep = [g for g in cl if len(g) >= 2]
    singles = [g for g in cl if len(g) == 1]
    ordered = [i for g in keep for i in _chain(g, st, q)]
    rest = [g[0] for g in singles if q & st[g[0]]]          # одиночки по теме вопроса — оставить
    dropped = [g[0] for g in singles if g[0] not in rest]
    if len(ordered) + len(rest) < 3:                        # слишком мало — одиночек не режем
        rest = [g[0] for g in singles]
        dropped = []
    seq = ordered + rest
    out = [ss[i] for i in seq]
    new = " ".join(out)
    leads, why = [], ""
    if use_llm and keep:
        for g in keep[:MAX_LEADS]:                          # v3.215: итог по каждой крупной нити
            main = [ss[i] for i in sorted(g)]
            topic = " ".join(sorted(set(w for i in g for w in st[i]))[:6])
            ld, why1 = _lead(main, topic)
            if ld and ld.lower() not in new.lower() and ld not in leads:
                leads.append(ld)
            elif why1:
                why = why1
                if "не обучена" in why1:
                    break
        if leads:
            new += " Итог: " + leads[0][0].lower() + leads[0][1:]
            if len(leads) > 1:
                new += " " + " ".join(leads[1:])
            _ST["llm"] += len(leads)
    elif not keep:
        why = "нет общих слов между предложениями — нечего собирать в нити"
    c1 = coherence(new)
    cold = "не обучена" in why
    if c1 <= c0 and not leads:                              # не стало лучше — не трогаем
        if log:
            _log("LLM", "failed", text, board=board, coh0=c0, coh1=c1,
                 note="мысль разрознена (%.2f), но собрать лучше не вышло%s"
                 % (c0, (": " + why) if why else ""))
        return text, info
    _ST["smoothed"] += 1
    _ST["dropped"] += len(dropped)
    info.update(changed=True, coherence_after=c1, clusters=len(cl),
                dropped=len(dropped), llm=bool(leads), leads=len(leads),
                mode=("нейромодель" if leads else "правила"))
    if log:
        _log("LLM", "assembled", text, new, board=board, coh0=c0, coh1=c1,
             threads=len(keep), dropped=len(dropped), leads=len(leads),
             note="мысль разрознена (%.2f): LLM собрала %d нитей, убрала %d лишних, итог: %s%s"
             % (c0, len(keep), len(dropped), ("%d предл." % len(leads)) if leads else "порядок по смыслу, главное — первым",
                (" — собрано правилами, нейромодель не использована (" + why + ")") if (why and not leads) else ""))
    if publish:
        try:
            import llmlink
            res = llmlink.publish_thought(new, " ".join(sorted(st[0])[:3]), source="сборка",
                                          quality=max(c1, llmlink.cfg()["minq"]))
            info["published"] = bool(res.get("ok"))
        except Exception:
            info["published"] = False
    return new, info


def enabled():
    return store.settings().get("thought_smooth", True) is not False


def status():
    return dict(_ST, on=enabled(), threshold=_threshold())
