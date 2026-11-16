# -*- coding: utf-8 -*-
"""
qfocus.py — v3.126: ВИДИМОСТЬ КОНТЕКСТА ВОПРОСА.

Поиск по базе знаний смотрит на совпадение слов. На вопрос «какая
народность была в России во времена Римской империи?» он нашёл статью
«19 апреля» (там есть и «Римской империи», и «России») и ответил строками
1713, 1780 и 1783 годов: слова совпали, а смысл — нет.

Теперь вопрос сначала РАЗБИРАЕТСЯ:
  • ЧТО спрашивают (фокус): «какая народность» -> народ/племя/этнос…,
    «кто» -> человек, «когда / в каком году» -> дата, «где» -> место,
    «сколько» -> число, иначе — существительное после «какой/что такое»;
  • КОГДА (эпоха): «во времена Римской империи» -> −27…476 гг.,
    «в XII веке», «до нашей эры», «при Петре I», «в 1812 году»…
    Слова эпохи («Римской империи») — ВРЕМЯ, а не тема: в поисковый запрос
    они не идут (иначе находятся статьи про Священную Римскую империю);
  • ГДЕ / О ЧЁМ: остальные значимые слова.
Потом каждое предложение ответа (база знаний, интернет) проверяется:
  • год в предложении вне эпохи вопроса — не ответ (1713 — не времена Рима);
  • строки-«летописи» («1713 — …» из статей-дат) — только на вопросы о датах;
  • в ответе должно быть то, о чём спросили (народы — так народы).
Не осталось ни одного годного предложения — источник отвергается, мозг
идёт дальше (другая статья, интернет) или честно говорит «не знаю».
"""
import re

_MONTHS = ("января февраля марта апреля мая июня июля августа сентября "
           "октября ноября декабря").split()

# эпохи: шаблон -> (с, по) годы (до н. э. — отрицательные)
_ERAS = [
    (r"римск\w* импери\w*|древн\w* рим\w*|рим\w* цезар\w*|античн\w*", (-753, 476)),
    (r"священн\w* римск\w* импери\w*", (962, 1806)),
    (r"византи\w*", (330, 1453)),
    (r"древн\w* греци\w*|эллад\w*", (-1200, -146)),
    (r"древн\w* египт\w*|фараон\w*", (-3100, -30)),
    (r"каменн\w* век\w*|палеолит\w*|неолит\w*", (-3000000, -3000)),
    (r"бронзов\w* век\w*", (-3300, -1200)),
    (r"железн\w* век\w*", (-1200, 500)),
    (r"средневеков\w*|средн\w* век\w*", (476, 1500)),
    (r"киевск\w* рус\w*|древн\w* рус\w*", (862, 1240)),
    (r"монгол\w*[\s-]*татар\w*|золот\w* орд\w*|иго\b", (1237, 1480)),
    (r"петр\w* (?:перв\w*|i\b|1\b)|петровск\w*", (1682, 1725)),
    (r"екатерин\w* (?:втор\w*|ii\b|2\b|велик\w*)", (1762, 1796)),
    (r"российск\w* импери\w*", (1721, 1917)),
    (r"ссср|советск\w* союз\w*", (1922, 1991)),
    (r"перв\w* миров\w*", (1914, 1918)),
    (r"втор\w* миров\w*|велик\w* отечествен\w*", (1939, 1945)),
    (r"гражданск\w* войн\w* в росси\w*", (1917, 1922)),
]
_ERA_TRIG = r"(?:во\s+времена|во\s+время|в\s+эпоху|в\s+период|в\s+годы|при|до|после|в\s+дни)"

# что спрашивают: шаблон слова вопроса -> основы, которые должны быть в ответе
_WANT = [
    (r"народност\w*|народ\w*|племен\w*|племя|этнос\w*|этническ\w*|жил\w*|насел\w*",
     ("народ", "племе", "племя", "этно", "славя", "скиф", "сарма", "финн",
      "угор", "балт", "гот", "гунн", "тюрк", "кочев", "жител", "населе",
      "культур", "алан", "хазар", "меря", "чудь", "весь", "мордв", "булгар")),
    (r"язык\w*", ("язык", "наречи", "говор", "письм")),
    (r"религи\w*|вер\w*|бог\w*", ("религ", "вера", "веро", "бог", "культ",
                                  "язычес", "христ", "ислам", "будд")),
    (r"столиц\w*", ("столиц", "город", "центр")),
    (r"правител\w*|царь|князь|король|император\w*", ("прави", "царь", "царя",
                                                     "князь", "княз", "корол",
                                                     "импер", "вождь")),
]
_STOP = set("""какая какой какие каков какова кто что где когда сколько почему
зачем как была был были было есть это эта этот эти тот та те такое такой
такая во в на по о об при для из за от до и а но или ли же бы то
расскажи скажи объясни знаешь подскажи пожалуйста""".split())


def _num_years(t):
    """Годы в тексте: «1713», «1713 г.», «в 1812 году», «III век»,
    «287 до н. э.» -> список чисел (до н. э. — отрицательные)."""
    t = str(t or "")
    out = []
    for m in re.finditer(r"(?<![\d.,])(\d{3,4})(?:\s*(?:—|-|–|г\.|год\w*|гг\.?))?"
                         r"(\s*(?:до\s+н\.?\s*э|до\s+нашей\s+эры))?", t):
        y = int(m.group(1))
        if 100 <= y <= 2100:
            out.append(-y if m.group(2) else y)
    for m in re.finditer(r"\b([IVXL]{1,6})\s+(?:век|в\.)", t):
        n = _roman(m.group(1))
        if n:
            out.append((n - 1) * 100 + 50)
    return out


def _roman(s):
    v = {"I": 1, "V": 5, "X": 10, "L": 50}
    tot, prev = 0, 0
    for ch in reversed(s.upper()):
        x = v.get(ch, 0)
        if not x:
            return 0
        tot = tot - x if x < prev else tot + x
        prev = max(prev, x)
    return tot


def parse(question):
    """Разбор вопроса -> dict(want, want_stems, era, era_text, date_q,
    topic_query)."""
    q = str(question or "").strip()
    ql = q.lower().replace("ё", "е")
    out = {"want": "", "want_stems": (), "era": None, "era_text": "",
           "date_q": False, "topic_query": q}
    out["date_q"] = bool(re.search(r"\bкогда\b|в как\w+ год|какого числа|"
                                   r"\bдат[аыу]\b|\bгод[ау]?\b\s*\?|\bкакой год",
                                   ql))
    # эпоха: «во времена Римской империи», «в XII веке», «в 1812 году»
    era, era_txt = None, ""
    for pat, rng in _ERAS:
        m = re.search(r"%s\s+(?:\w+\s+){0,2}?(%s)" % (_ERA_TRIG, pat), ql) or \
            re.search(r"(%s)" % pat, ql)
        if m:
            trig = bool(re.search(_ERA_TRIG + r"\s+(?:\w+\s+){0,2}?" + pat, ql))
            if trig or not re.search(r"\b(что|кто)\s+так\w+|\bпро\b|\bо\b\s+" + pat, ql):
                era, era_txt = rng, m.group(0)
                if trig:
                    break
    m = re.search(r"\b([ivxl]{1,6}|\d{1,2})[\s-]*(?:м\s+)?(?:век|в\.)\w*", ql)
    if m and not era:
        n = _roman(m.group(1)) if not m.group(1).isdigit() else int(m.group(1))
        if n:
            bc = bool(re.search(r"до\s+н\.?\s*э|до\s+нашей\s+эры", ql[m.end():m.end() + 25]))
            lo, hi = (n - 1) * 100 + 1, n * 100
            era, era_txt = ((-hi, -lo) if bc else (lo, hi)), m.group(0)
    m = re.search(r"\b(?:в|к|до|после)\s+(\d{3,4})\s*(?:г\.|год\w*)", ql)
    if m and not era:
        y = int(m.group(1))
        era, era_txt = (y - 2, y + 2), m.group(0)
    out["era"], out["era_text"] = era, era_txt
    # что спрашивают
    m = re.search(r"\b(?:как\w+|чем\w*|кто|сколько)\s+([а-я]+)", ql)
    head = m.group(1) if m else ""
    for pat, stems in _WANT:
        if (head and re.fullmatch(pat, head)) or re.search(
                r"\b(?:как\w+)\s+(?:\w+\s+)?(?:%s)" % pat, ql):
            out["want"], out["want_stems"] = head or pat, stems
            break
    if not out["want"] and re.search(r"^\s*кто\b", ql):
        out["want"] = "кто"
    # поисковый запрос: без слов-триггеров эпохи и самой эпохи — эпоха
    # проверяется по годам, а слова «Римской империи» уводили к другим статьям
    tq = q
    if era_txt and re.search(_ERA_TRIG, era_txt):
        tq = re.sub(re.escape(era_txt), " ", tq, flags=re.I)
    out["topic_query"] = " ".join(tq.split()) or q
    return out


def active(qf):
    return bool(qf and (qf.get("era") or qf.get("want_stems")))


def _stems(t):
    return [w[:5] for w in re.findall(r"[а-яё]{3,}", str(t or "").lower().replace("ё", "е"))]


_CHRON = re.compile(r"^\s*(?:\d{3,4}|[IVXL]+ век)\s*(?:год\w*|г\.)?\s*[—–-]\s")


def sentence_ok(sn, qf):
    """Годится ли предложение для ответа на разобранный вопрос."""
    s = str(sn or "")
    if not qf:
        return True
    if _CHRON.match(s) and not qf.get("date_q"):
        # строка-летопись «1713 — …»: только если её год в эпохе вопроса
        era = qf.get("era")
        ys = _num_years(s[:12])
        if not era or not ys or not any(era[0] <= y <= era[1] for y in ys):
            return False
    era = qf.get("era")
    if era:
        ys = _num_years(s)
        if ys and not any(era[0] - 50 <= y <= era[1] + 50 for y in ys):
            return False                       # все годы — вне эпохи
    return True


def has_want(text, qf):
    ws = qf.get("want_stems") or ()
    if not ws:
        return True
    tl = str(text or "").lower().replace("ё", "е")
    return any(w in tl for w in ws)


def filter_answer(text, qf):
    """Ответ (текст) -> (отфильтрованный текст | '', причина).
    Служебные строки (_…_, Источник, заголовки) сохраняются."""
    if not active(qf):
        return text, ""
    lines = str(text or "").split("\n")
    out, kept, dropped = [], 0, 0
    for ln in lines:
        st = ln.strip()
        if not st or st.startswith(("_", "**", "Источник", "[", "#", "🖼")):
            out.append(ln)
            continue
        sents = re.split(r"(?<=[.!?…])\s+", st)
        good = [x for x in sents if sentence_ok(x, qf)]
        dropped += len(sents) - len(good)
        if good:
            out.append(" ".join(good))
            kept += len(good)
    body = "\n".join(out).strip()
    plain = "\n".join(x for x in out if x.strip() and not x.strip().startswith(("_", "**", "Источник", "[")))
    if not kept:
        return "", "ни одно предложение не подходит к вопросу (эпоха/летопись)"
    if not has_want(plain, qf):
        return "", "в найденном нет того, о чём спросили (%s)" % (qf.get("want") or "?")
    return body, ("убрано предложений не по вопросу: %d" % dropped) if dropped else ""


_DATE_TITLE = re.compile(r"(?:^|:\s*)\d{1,2}\s+(?:%s)\b|\b\d{3,4}\s+год\b|"
                         r"\bсписок\b|\bхронолог" % "|".join(_MONTHS), re.I)


def demote_hits(hits, qf):
    """Статьи-даты («19 апреля», «1713 год», «Список…») — в конец, если
    спрашивают не о дате."""
    if not hits or not qf or qf.get("date_q"):
        return hits
    good = [h for h in hits if not _DATE_TITLE.search(str(h.get("title") or ""))]
    bad = [h for h in hits if _DATE_TITLE.search(str(h.get("title") or ""))]
    return good + bad if good else []
