# articles.py — v3.212: СТАТЬИ ИЗ ИНТЕРНЕТА С ФОРМУЛАМИ (LaTeX) ПО ЗАПРОСУ
#
# fetch(topic)   — найти статью (Рувики/Википедия), вернуть текст, в котором
#                  формулы Википедии «{\displaystyle …}» уже записаны как $…$.
# formulas(text) — формулы статьи ($…$), каждая с предложением-контекстом.
# formula_block(question) — блок «Формулы из статьи …» для ответа: ТОЛЬКО то,
#                  что реально написано в статье (мозг сам формулы не выдумывает).
import re

import store

FORMULA_ASK = re.compile(r"формул|уравнени|выражени\w* для|как (?:вычисл|рассчит|посчит)|"
                         r"латех|latex", re.I)
CODE_ASK = re.compile(r"\bкод\b|кодом|функци[юяи]|напиши\s+(?:\S+\s+){0,3}(?:скрипт|программ|класс|запрос)|"
                      r"скрипт|python|питон|javascript|js\b|\bsql\b|\bc\+\+|\bgo\b|java\b|алгоритм на|"
                      r"регулярк|\bregex\b|\bbash\b|\bhtml\b|\bcss\b|\bgit\b", re.I)


def is_formula_q(text):
    return bool(FORMULA_ASK.search(str(text or "")))


def is_code_q(text):
    return bool(CODE_ASK.search(str(text or "")))


def _topic(question):
    t = re.sub(r"(?i)\b(формул\w*|уравнени\w*|выражени\w*|напиши|покажи|дай|в\s+latex|латех|latex|"
               r"для|как|вычисл\w*|рассчит\w*|посчит\w*|что\s+такое|объясни|расскажи|про|о|об)\b", " ",
               str(question or ""))
    return re.sub(r"[^\w\s-]", " ", t).strip()[:120] or str(question or "")[:120]


def fetch(topic):
    """-> {title, text, url} | None. Сырой extract (с {\\displaystyle}) -> $…$."""
    import urllib.parse
    import webfetch
    import latexgen
    topic = _topic(topic)
    if not topic:
        return None
    for lang, host in (("ruwiki", webfetch.RUWIKI), ("ru", "ru.wikipedia.org")):
        try:
            hits = (webfetch.ruwiki_search(topic, 3) if lang == "ruwiki"
                    else webfetch.wiki_search(topic, 3)) or []
        except Exception:
            hits = []
        for h in hits[:2]:
            title = h.get("title") if isinstance(h, dict) else str(h)
            if not title:
                continue
            try:
                data = webfetch._get_json_wiki("https://%s/w/api.php?%s" % (host, urllib.parse.urlencode(
                    {"action": "query", "prop": "extracts", "explaintext": "1", "format": "json",
                     "titles": title, "redirects": "1"})), timeout=15)
                for pg in ((data.get("query") or {}).get("pages") or {}).values():
                    ex = str(pg.get("extract") or "").strip()
                    if len(ex) >= 200:
                        return {"title": title, "text": latexgen.wiki_math(ex)[:30000],
                                "url": "https://%s/wiki/%s" % (host, urllib.parse.quote(title.replace(" ", "_")))}
            except Exception:
                continue
    return None


def formulas(text, limit=5):
    """Формулы ($…$) статьи с контекстом -> [(формула, контекст)]."""
    out, seen = [], set()
    t = str(text or "")
    for m in re.finditer(r"\$([^$\n]{3,300})\$", t):
        f = re.sub(r"\s+", " ", m.group(1)).strip()
        if f in seen or not re.search(r"[=<>^_\\]|\\frac|\\sum", f) or len(f) < 3:
            continue
        seen.add(f)
        a = t.rfind(".", 0, m.start())
        ctx = re.sub(r"\s+", " ", t[a + 1:m.start()]).strip()[-160:]
        out.append((f, ctx))
        if len(out) >= limit:
            break
    return out


def formula_block(question, limit=4):
    """Блок формул для ответа или '' (честно — если в статье формул нет)."""
    a = fetch(question)
    if not a:
        return ""
    fs = formulas(a["text"], limit)
    if not fs:
        return "В статье «%s» формул не нашлось.\n%s" % (a["title"], a["url"])
    lines = ["Формулы из статьи «%s»:" % a["title"]]
    for f, ctx in fs:
        if ctx:
            lines.append(ctx.rstrip(":") + ":")
        lines.append("$$%s$$" % f)
    lines.append(a["url"])
    return "\n".join(lines)
