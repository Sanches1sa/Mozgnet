# -*- coding: utf-8 -*-
# ===========================================================================
# writer.py — ИНСТРУМЕНТ САМОСТОЯТЕЛЬНОГО ПИСЬМА (v3.18, только stdlib)
# ---------------------------------------------------------------------------
# Заявка владельца: «Облако отвечает небольшим текстом, она сама
# отказывается писать большой текст — значит ей нужен инструмент
# самостоятельного написания».
#
# Что было: mozgnet.write(min_chars=…) тянул предложения из сети материалов,
# и когда их было мало — честно отказывался («материалов мало»). Письма как
# такового не было: никто не собирал РАЗВЁРНУТЫЙ текст из разных источников.
#
# Что теперь: мозг ПИШЕТ письмо сам, как писатель:
#   1) собирает ВСЁ, что знает по теме (статьи KB целиком — до 5, сеть
#      материалов, заметки памяти, словарные слова по теме, диалоги);
#   2) строит план: вступление → основное → подробности → связи → слова →
#      память → итог;
#   3) каждую часть ПИШЕТ: сжимает и пересказывает материал своими
#      связками (связки — свои, факты — только из материалов, каждый блок
#      подписан источником);
#   4) если знает мало — ЧЕСТНО пишет в конце, чего не хватило, и просит
#      статьи/файлы (выдумывать запрещено, как и везде).
#   5) в конце — помеченная фантазия мини-LLM (воображение), если готова.
#
# ЧЕСТНОСТЬ: ни одного факта «из головы» — только свои связки между
# кусками реальных материалов и честные пометки «это фантазия».
# ===========================================================================
import re

import kb
import store
import textutils

_MIN_BLOCK = 60            # короче — блок не тащим в письмо


def _sentences_paras(body, max_paras=8):
    """Абзацы/куски статьи: разбив по предложениям, склеивая в куски
    по 2 предложения (так текст «переписывается», а не цитируется целиком)."""
    sents = []
    for s in re.split(r"\n\s*\n|(?<=[.!?])\s+(?=[А-ЯЁA-Z0-9«(])",
                      str(body or "")):
        s = " ".join(s.split())
        if len(s) > 25:
            sents.append(s)
    out = []
    for i in range(0, len(sents), 2):
        chunk = " ".join(sents[i:i + 2])
        if chunk:
            out.append(chunk)
    return out[:max_paras]


def _compress(s, limit=420):
    """Сжать кусок до limit знаков по границе предложения (не рвать слова)."""
    s = " ".join(str(s or "").split())
    if len(s) <= limit:
        return s
    import textutils                 # v3.27: только по концу предложения
    return textutils.cut_at_sentence(s, limit)


def _own_connectors():
    """Связки-фразы самого мозга (не факты — скрепления письма)."""
    return ["Вот что я знаю по этому поводу.",
            "Разберём подробнее.",
            "Теперь о том, что стоит за этим.",
            "Что ещё важно понять.",
            "Продолжу — это связано напрямую.",
            "Тут есть ещё одна грань."]


_TOPIC_FILLER = re.compile(
    r"^\s*(?:подробн[ооыа\w]*|полностью|длинн\w+|больше|много)\s+", re.I)


def _search_topic(topic):
    """Тема для ПОИСКА: без служебных слов-наполнителей («подробно про
    воду» -> «воду») — иначе tf-idf ищет по слову «подробно»."""
    t = _TOPIC_FILLER.sub(" ", str(topic or "")).strip()
    t = re.sub(r"^\s*(?:про|об|о)\s+", "", t, flags=re.I).strip()
    return t or topic


def _collect(topic):
    """Собрать материалы по теме: (kb_blocks, net_text, notes, words)."""
    topic = _search_topic(topic)
    kb_blocks = []
    try:
        r = kb.search(topic, limit=8)
        for h in (r.get("results") or []):
            if h.get("wiki"):
                continue
            # v3.18: свои прошлые сочинения — не источники (иначе письма
            # начинали кормиться друг другом и вырождались)
            if str(h.get("title") or "").startswith("Сочинение:"):
                continue
            aid = h.get("id")
            art = kb.article(aid) if aid else None
            if not art:
                continue
            title = str(art.get("title") or "KB")
            text = str(art.get("text") or "")
            if len(text) < _MIN_BLOCK:
                continue
            kb_blocks.append({"title": title, "paras": _sentences_paras(text)})
    except Exception:
        pass
    # v3.18: если точный поиск не нашёл (короткие слова типа «воду»
    # стеммер не режет) — корневой fallback: сравниваем префиксы слов
    if not kb_blocks:
        try:
            toks = [w for w in textutils.WORD_RE.findall(
                _search_topic(topic).lower()) if len(w) >= 4]

            def _root(w):
                return w[:4] if len(w) >= 5 else w[:3]

            roots = {_root(w) for w in toks}
            for a in kb.list_articles(limit=200):
                title = str(a.get("title") or "")
                if title.startswith("Сочинение:") or \
                        title.startswith("Материалы:"):
                    continue
                art = kb.article(a.get("id"))
                body = str((art or {}).get("text") or "")
                if not body:
                    continue
                bw = textutils.WORD_RE.findall(body.lower())
                hits = sum(1 for r in roots
                           if any(x.startswith(r) for x in bw[:400]))
                if hits >= max(1, len(roots) // 2) and len(body) >= _MIN_BLOCK:
                    kb_blocks.append({
                        "title": title,
                        "paras": _sentences_paras(body)})
                if len(kb_blocks) >= 3:
                    break
        except Exception:
            pass

    net_text = ""
    try:
        import brain
        with brain._MN_LOCK:
            net_text = brain.mn().write(topic, words=160) or ""
        if ("Материалов по теме пока нет" in net_text
                or "не нашлось ничего" in net_text
                or "годных предложений мало" in net_text):
            net_text = ""
    except Exception:
        net_text = ""
    notes = []
    try:
        toks = [w for w in textutils.WORD_RE.findall(str(topic).lower())
                if len(w) > 3]
        for n in store.notes():
            t = str(n.get("text") or "")
            if any(w in t.lower() for w in toks):
                notes.append(_compress(t, 240))
            if len(notes) >= 3:
                break
    except Exception:
        pass
    words = []
    try:
        import brain
        for w in textutils.WORD_RE.findall(str(topic).lower()):
            if len(w) < 4:
                continue
            r = brain.dict_lookup(w)
            if r.get("found") and (r.get("pos") or r.get("close")):
                words.append({"w": r["word"],
                              "pos": r.get("pos") or "",
                              "close": (r.get("close") or [])[:4]})
            if len(words) >= 3:
                break
    except Exception:
        pass
    return kb_blocks, net_text, notes, words


def _fantasy(topic):
    """Помеченная фантазия мини-LLM (воображение) — опциональный финал."""
    try:
        import tinyllm
        text, info = tinyllm.fantasize(topic)
        if text and info and not info.get("warming"):
            return text[:300]
    except Exception:
        pass
    return ""


def write_long(topic, min_chars=1500, fantasy=True):
    """НАПИСАТЬ письмо по теме (инструмент самостоятельного письма).
    Возврат: готовый текст. Честно: если материалов меньше цели — в конце
    сказано, чего не хватило; выдумки нет ни в каком блоке."""
    topic = re.sub(r"\s+", " ", str(topic or "")).strip()[:200]
    if not topic:
        return "О чём писать? Скажи тему: «напиши подробно про океан»."
    kb_blocks, net_text, notes, words = _collect(topic)
    conn = iter(_own_connectors())
    parts = []
    total = 0
    parts.append("Письмо по теме «%s»." % topic)
    parts.append("")

    # 1) вступление: самое начало самой релевантной статьи
    if kb_blocks:
        b = kb_blocks[0]
        intro = _compress(" ".join(b["paras"][:2]), 500)
        if len(intro) >= _MIN_BLOCK:
            parts.append("Сначала — суть. %s" % next(conn))
            parts.append(intro)
            parts.append("_(из статьи «%s»)_" % b["title"])
            parts.append("")
            total += len(intro)

    # 2) основное: продолжение статей — новые куски, не повтор вступления
    seen = {" ".join(str(p).split()).lower()[:90]
            for p in [" ".join(x.split()) for x in
                      (kb_blocks[0]["paras"][:2] if kb_blocks else [])]}
    main_got = 0
    for b in kb_blocks:
        for p in b["paras"]:
            key = " ".join(str(p).split()).lower()[:90]
            if key in seen:
                continue
            seen.add(key)
            piece = _compress(p, 460)
            if len(piece) < _MIN_BLOCK:
                continue
            if main_got == 0:
                parts.append(next(conn))
            parts.append(piece)
            parts.append("_«%s»_" % b["title"])
            parts.append("")
            total += len(piece)
            main_got += 1
            if total >= min_chars * 0.75:
                break
        if total >= min_chars * 0.75:
            break

    # 3) сеть материалов — как сеть «это себе представляет»
    if net_text:
        parts.append(next(conn))
        parts.append(_compress(net_text, 900))
        parts.append("_(сеть материалов облака)_")
        parts.append("")
        total += len(net_text)

    # 4) слова по теме — словарь как строительный материал письма
    if words:
        lines = []
        for w in words:
            lines.append("• «%s»%s" % (
                w["w"], (" — " + w["pos"]) if w["pos"] else ""))
            if w["close"]:
                lines.append("  рядом в словаре: %s"
                             % ", ".join("«%s»" % c for c in w["close"]))
        parts.append("Слова, из которых я собиралась говорить об этом:")
        parts.append("\n".join(lines))
        parts.append("")
        total += sum(len(x) for x in lines)

    # 5) память
    if notes:
        parts.append("Из моей памяти:")
        parts.append("\n".join("• %s" % n for n in notes))
        parts.append("")
        total += sum(len(n) for n in notes)

    # 6) фантазия воображения (помечена честно)
    if fantasy and total >= min_chars * 0.6:
        f = _fantasy(topic)
        if f:
            parts.append("И в конце — воображение. Это ФАНТАЗИЯ мини-нейро-"
                         "сети, не факты:")
            parts.append("«%s»" % f)
            parts.append("")

    # 7) итог — счёт собственного письма (факты о себе, честные)
    n_src = len(kb_blocks) + (1 if net_text else 0) + len(notes)
    parts.append("Итог: письмо собрано из %d источников (статьи базы знаний, "
                 "сеть материалов, память). Свои слова здесь — только связки "
                 "между кусками; каждый факт — из подписанного материала."
                 % n_src)

    out = "\n".join(parts)
    # честный хвост, если цели не достигли
    if len(out) < min_chars:
        base = len(out)
        out += ("\n\n(Цель письма — не короче %d знаков, набралось %d. "
                "Материалов по теме мало, а выдумывать я не стану: дай мне "
                "статей или файлов по «%s» — скажи «открой %s» или загрузи "
                "файлы во вкладке «База знаний», и следующее письмо будет "
                "полным.)" % (min_chars, base, topic,
                              topic.split()[0] if topic.split() else topic))
    return out


def append_if_short(text, topic, min_chars):
    """Добор для режима «длинно» (brain._ensure_long): если ответ всё ещё
    короче цели — дописать ПИСЬМО по теме (без повторов уже написанного)."""
    text = str(text or "")
    if len(text) >= min_chars:
        return text
    letter = write_long(topic, min_chars=min_chars, fantasy=False)
    norm = " ".join(text.split()).lower()
    fresh = []
    got = 0
    for para in letter.split("\n\n"):
        p = para.strip()
        if len(p) < 40 or " ".join(p.split()).lower()[:90] in norm:
            continue
        fresh.append(p)
        got += len(p)
        if len(text) + got >= min_chars:
            break
    if not fresh:
        return text
    return text + "\n\nПисьмо по теме (самостоятельное письмо):\n" + \
        "\n\n".join(fresh)
