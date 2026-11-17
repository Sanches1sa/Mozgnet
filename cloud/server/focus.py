# -*- coding: utf-8 -*-
# ===========================================================================
# focus.py — ФОКУС ВОПРОСА: о ЧЁМ КОНКРЕТНО спросили (v3.30, только stdlib)
# ---------------------------------------------------------------------------
# Заявка владельца: «Не работает архиватор на Бештау» — облако ответило
# общей статьёй про архиваторы, ни слова про Бештау («без конкретики»).
# Фокус — слова, без которых ответ не по делу: имена, марки, модели, ОС,
# программы. Признаки (по тексту вопроса, без LLM):
#   * слово с заглавной НЕ в начале предложения («на Бештау», «в Astra»);
#   * латиница и слова с цифрами («RED OS», «7.3», «X11», «1С»);
#   * слово, которого нет в словарях облака даже по корню («бештау»
#     строчными — в МАКС часто пишут без заглавных).
# Ответ, где фокуса нет, честно помечается: «именно про Бештау не нашла —
# общее по теме», а не выдаётся за конкретный.
# ===========================================================================
import re

_PREF = {"set": None}
_COMMON = frozenset("""привет здравствуйте спасибо пожалуйста помогите
подскажите скажите расскажи помоги объясни почему зачем когда сколько
который какой какая какие можно нужно надо есть нет""".split())
_TOK = re.compile(r"[A-Za-zА-Яа-яЁё0-9][A-Za-zА-Яа-яЁё0-9.\-+#]*")


def _prefixes():
    """Корни (5 букв) всех словарных слов — «есть ли такое слово вообще»."""
    if _PREF["set"] is None:
        try:
            import brain
            words, _p, _s = brain._dict_load_words()
            _PREF["set"] = frozenset(w[:5] for w in words if len(w) >= 4)
        except Exception:
            _PREF["set"] = frozenset()
    return _PREF["set"]


def terms(q, limit=3):
    """Слова-фокус вопроса (как написаны). Пусто — фокуса нет."""
    text = str(q or "").strip()
    out = []
    pref = _prefixes()
    for m in _TOK.finditer(text):
        w = m.group(0).strip(".-+")
        low = w.lower().replace("ё", "е")
        if len(w) < 2 or low in _COMMON:
            continue
        start = m.start() == 0 or bool(re.search(r"[.!?]\s*$",
                                                 text[:m.start()]))
        latin = bool(re.search(r"[a-z]", low)) and len(low) >= 2
        digits = bool(re.search(r"\d", low)) and bool(re.search(r"[a-zа-я]",
                                                                 low))
        capital = w[:1].isupper() and not start and len(w) >= 3
        # «нет в словаре» — только при настоящем словаре (десятки тысяч
        # слов): без него любое слово показалось бы редким
        rare = (len(low) >= 4 and re.fullmatch(r"[а-я]+", low) is not None
                and len(pref) > 5000 and low[:5] not in pref)
        if latin or digits or capital or rare:
            if low not in [x.lower() for x in out]:
                out.append(w)
        if len(out) >= limit:
            break
    return out


def stems(words):
    import kb
    out = set()
    for w in words or []:
        out.update(kb._stems(w))
    return out


def covered(text, words):
    """Есть ли в тексте хоть одно слово фокуса (по корню)."""
    if not words:
        return True
    import kb
    st = set(kb._stems(str(text or "")))
    fs = stems(words)
    return bool(st & fs) or any(str(w).lower() in str(text or "").lower()
                                for w in words)


def label(words):
    return ", ".join("«%s»" % w for w in words[:3])
