# thoughtextend.py — v3.210: ОБЛАКО ДОПОЛНЯЕТ МЫСЛЬ ПЛАТЫ
# Плата собирает мысль (37 оборотов по словам, в контексте вопроса) и просит
# облако дополнить: облако достраивает текст СВОЕЙ речью (wordmind) по теме
# вопроса и слов мысли — только то, что связано по словам с мыслью/вопросом,
# без повторов. POST /api/thought/extend {board, text, question, max_chars}.
import re

import store

_W = re.compile(r"[а-яё]{4,}")


def _stems(t):
    return {w[:5] for w in _W.findall(str(t or "").lower())}


def extend(text, question="", max_chars=1200):
    text = str(text or "").strip()
    question = str(question or "").strip()
    max_chars = max(200, min(3000, int(max_chars or 1200)))
    try:
        import wordmind
    except Exception as e:
        return {"ok": False, "error": "речь облака недоступна: %s" % e}
    ctx = _stems(question) | _stems(text)
    qs = _stems(question)
    words = [w for w in _W.findall((question + " " + text).lower())]
    topics, seen = [], set()
    for w in words:                    # темы: слова вопроса первыми
        if w[:5] not in seen:
            seen.add(w[:5]); topics.append(w)
    topics = ([w for w in topics if w[:5] in qs][:4] +
              [w for w in topics if w[:5] not in qs][:6]) or ["мысль"]
    have = {s.strip().lower() for s in re.split(r"[.!?]\s*", text) if s.strip()}
    add, total = [], 0
    for tp in topics:
        for _ in range(3):
            try:
                t, _i = wordmind.compose(tp)
            except Exception:
                t = ""
            t = store._tclean(t or "").strip()
            if not t:
                continue
            ws = _W.findall(t.lower())
            if len(ws) < 3 or len(set(ws)) < 0.6 * len(ws):
                continue
            if not (_stems(t) & ctx):          # не по теме
                continue
            key = t.lower().rstrip(".!? ")
            if key in have or any(key in a.lower() for a in add):
                continue
            if total + len(t) + 1 > max_chars:
                break
            add.append(t); have.add(key); total += len(t) + 1
        if total >= max_chars * 0.9:
            break
    out = " ".join(add)
    try:                              # v3.212: вопрос про код — дополнение кодом (латиница в ```)
        import articles
        if articles.is_code_q(question):
            import agent
            cr, matched, _i = agent.code_agent(question)
            if matched and "```" in str(cr):
                out = (out + "\n\n" + str(cr).strip()[:1800]).strip()
        elif articles.is_formula_q(question):
            fb = articles.formula_block(question, limit=3)
            if fb:
                out = (out + "\n\n" + fb).strip()
    except Exception:
        pass
    return {"ok": True, "addition": out, "sentences": len(add),
            "topics": topics[:6]}
