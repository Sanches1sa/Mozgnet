# -*- coding: utf-8 -*-
# ===========================================================================
# smalltalk.py — ЧЕСТНЫЙ РАЗГОВОР: «как дела», «привет», «спасибо»… (v3.91)
# ---------------------------------------------------------------------------
# Заявка владельца: «пусть мозг и LLM отвечают на вопросы вроде „как дела“,
# только честно». Ответ собирается из НАСТОЯЩЕГО состояния облака (база
# знаний, своя LLM, платы, любопытство), без выдуманных чувств: у облака их
# нет, и оно так и говорит. Дальше эти предложения, как и ответы знаний,
# идут через шаблон «мозг + своя LLM» (LLM может их переписать — по тем же
# фактам, с проверкой опоры).
# ===========================================================================
import re

import store

_PAT = [
    ("how", re.compile(r"^\s*(?:ну\s+)?(?:а\s+)?(?:как\s+(?:у\s+тебя\s+|у\s+вас\s+|твои\s+|ваши\s+)?"
                       r"(?:дела|делишки|жизнь|поживаешь|поживаете|ты|вы|настроение|сам|сама|оно)"
                       r"|как\s+ты\s+себя\s+чувствуешь|что\s+нового)\s*[\?\.!]*\s*$", re.I)),
    ("hello", re.compile(r"^\s*(?:привет\w*|здравствуй\w*|добр(?:ое|ый|ой)\s+(?:утро|день|вечер|ночи)|"
                         r"доброго\s+\w+|салют|хай|хелло|hello|hi)[\s!\.,]*(?:бот|мозг|облако)?[\s!\.]*$", re.I)),
    ("thanks", re.compile(r"^\s*(?:спасибо|благодарю|спс|пасиб\w*|thanks?)\b[^?]{0,40}$", re.I)),
    ("who", re.compile(r"^\s*(?:а\s+)?(?:кто\s+ты|ты\s+кто|что\s+ты\s+такое|кто\s+вы|"
                       r"ты\s+(?:бот|робот|человек|ии|нейросеть))\s*[\?\.!]*\s*$", re.I)),
    ("bye", re.compile(r"^\s*(?:пока|до\s+свидания|до\s+связи|всего\s+доброго|"
                       r"спокойной\s+ночи|до\s+завтра)[\s!\.]*$", re.I)),
]


def _ru(n, a, b, c):
    n = abs(int(n))
    if n % 10 == 1 and n % 100 != 11:
        return a
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return b
    return c


def _facts():
    """Настоящее состояние облака одной-двумя фразами."""
    out = []
    try:
        st = store.stats()
        n = int(st.get("kb_articles") or 0)
        out.append("В базе знаний %d %s." % (n, _ru(n, "статья", "статьи", "статей")))
    except Exception:
        pass
    try:
        import tinyllm
        s = tinyllm.status()
        if s.get("training") and s.get("total_steps"):
            out.append("Своя LLM сейчас учится: шаг %d из %d%s." % (
                s.get("steps", 0), s.get("total_steps", 0),
                (", потери %.2f" % s["loss"]) if isinstance(s.get("loss"), float) else ""))
        elif s.get("inc_active"):
            out.append("Своя LLM дообучается на новых статьях.")
        elif s.get("ready"):
            out.append("Своя LLM обучена (шагов %d) и переписывает ответы по фактам." % s.get("steps", 0))
        else:
            out.append("Своя LLM ещё не обучена.")
    except Exception:
        pass
    try:
        k = len(store.free_boards() or [])
        out.append("Плат neuro-12 на связи: %d." % k if k else
                   "Плата neuro-12 сейчас не на связи.")
    except Exception:
        pass
    try:
        import curiosity
        q = (curiosity.status() or {}).get("queue") or []
        if q:
            out.append("Любопытство ждёт %d %s, чтобы прочитать о них." % (
                len(q), _ru(len(q), "тему", "темы", "тем")))
    except Exception:
        pass
    return out


# v3.96: заготовок нет (заявка владельца «убери шаблоны и заготовки
# совсем»): не ответила LLM и нет подтверждённого ответа — честно об этом
NO_ANSWER = ("Своя LLM пока не смогла ответить на это своими словами — она "
             "ещё учится, а готовых фраз у меня нет. Научить можно: напиши "
             "«правильный ответ: …», и я буду отвечать так.")


def kind_of(text):
    t = str(text or "").strip()
    if len(t) > 80:
        return None
    for k, rx in _PAT:
        if rx.match(t):
            return k
    return None


def _llm_reply(text):
    """Ответ своей LLM на реплику — её собственные слова (только из словаря
    базы, выдуманные вычищаются); первая законченная фраза. "" — молчит."""
    try:
        import tinyllm
        t, info = tinyllm.sample(str(text), max_new=28, temp=0.7)
    except Exception:
        return ""
    t = str(t or "").strip()
    if not t:
        return ""
    import re as _re
    first = _re.split(r"(?<=[.!?…])\s+", t)[0].strip()
    words = _re.findall(r"[А-Яа-яЁё]{2,}", first)
    if len(words) < 3 or len(first) > 220:
        return ""
    try:            # v3.99: мусор («С « й навыки math…») не выдаём за ответ
        import llmlink
        if float(tinyllm.quality(first)) < max(0.5, llmlink.cfg()["minq"]) \
                or llmlink.degenerate(first):
            return ""
        if len(_re.findall(r"[A-Za-z]", first)) > 0.25 * len(first) or \
                _re.search(r"«\s*\w?\s*»|\s[^\w\s«»]{1,2}\s", first):
            return ""
    except Exception:
        return ""
    try:            # v3.122: «Не показывать бред» — только связная фраза
        import strict
        if strict.on() and not strict.text_ok(first, "", 3):
            strict.block("реплика LLM", first)
            return ""
    except Exception:
        pass
    return first


def answer(text):
    """v3.92: реплику («как дела», «привет»…) отвечают САМИ мозг и своя LLM:
    1) мозг — подтверждённый ответ из памяти диалогов (👍 / «правильный
       ответ: …» владельца) — это делает qalearn ещё до этой функции;
    2) своя LLM — своими словами (только слова из базы, без выдумок);
    3) иначе — честно: «LLM пока не смогла ответить», без заготовок.
    -> (текст, кто) | None."""
    kind = kind_of(text)
    if not kind:
        return None
    if store.settings().get("answer_honest", True):
        # v3.129: своя LLM на реплики пишет несвязно («То же умеет то не план…») —
        # в честном режиме отвечаю правдой о своём состоянии, без её фантазий
        return _honest_reply(kind), "состояние облака"
    t = _llm_reply(text)
    if t:
        return t, "своя LLM"
    return NO_ANSWER, "нет ответа"


def _honest_reply(kind):
    facts = " ".join(_facts())
    if kind == "hello":
        return ("Привет! " + facts).strip()
    if kind == "how":
        return ("Работаю. " + facts).strip()
    if kind == "thanks":
        return "Пожалуйста."
    if kind == "bye":
        return "До связи."
    if kind == "who":
        return ("Я — облако «Star Gate». Отвечаю по базе знаний, интернету (с "
                "источниками), считаю, пишу и проверяю код, смотрю в камеру и "
                "слушаю микрофон мозга. Чего не знаю — так и говорю. " + facts).strip()
    return facts or "На связи."
