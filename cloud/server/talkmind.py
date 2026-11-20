# -*- coding: utf-8 -*-
"""
talkmind.py — v3.132: режим «Разговор» в панели облака — ПРОСТО ПОГОВОРИТЬ.

Заявка владельца: «пусть разговор пишет сам мозг, а своя LLM дополняет его,
без опоры на факты и статьи базы знаний, чисто чтобы поговорить — без
смайликов и кавычек, сплошным текстом».

Кто пишет:
  1) МОЗГ — мысль мозга платы neuro_12 на реплику (прямой вопрос по мосту
     или по каналу связи); плата не на связи — речь мозга облака из словаря
     (wordmind: слова, которые сейчас «думает» сеть материалов);
  2) СВОЯ LLM продолжает мысль мозга своими словами (затравка — мысль мозга
     и реплика), температура дышит вместе с платами.
База знаний здесь не участвует — это разговор, а не справка (для справки —
режимы «Авто» и «Облако»). v3.187: если нужно подкрепить факт (вопрос о
факте, числа/имена в ответе, «проверь») — своя LLM выходит в интернет и
пишет проверку следующим сообщением (llmfact.py, llm_web_check_talk).
Ответ — одним сплошным текстом: без смайликов, кавычек, заголовков, меток.
"""
import re
import time

import store
import activity

_EMOJI = re.compile("[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U0001F900-\U0001F9FF"
                    "\U00002190-\U000021FF\U00002B00-\U00002BFF️‍]+")
_QUOTES = re.compile(r"[«»\"“”„‟'‘’`]")
_SERVICE = re.compile(r"(Мысли платы\s+\S+\s*\([^)]*\)\s*:?|Мозг neuro-12\s*\S*\s*от себя:?|"
                      r"Своя LLM от себя:?|собрано за [^)]*\)|\[[^\]]*\]|\*\*[^*]*\*\*|"
                      r"_[^_]{0,200}_)", re.I)


def clean(t):
    """Сплошной текст: без смайликов, кавычек, разметки и служебных меток."""
    t = str(t or "")
    t = _SERVICE.sub(" ", t)
    t = _EMOJI.sub(" ", t)
    t = _QUOTES.sub("", t)
    t = re.sub(r"[#*_~|<>•]+", " ", t)
    t = re.sub(r"\s+", " ", t).strip(" -—–:;,")
    if t and t[0].islower():
        t = t[0].upper() + t[1:]
    if t and t[-1] not in ".!?…":
        t += "."
    return t


def _punct(t):
    """v3.170: запятые в порождённом тексте (мозг и своя LLM)."""
    try:
        import punct
        return punct.fix(t)
    except Exception:
        return t


def _brain_thought(text):
    """Мысль мозга: плата (мост / канал связи), иначе мозг облака."""
    wait = float(store.settings().get("talk_board_wait", 4) or 0)
    if wait > 0:
        try:
            import bridge
            dr = bridge.think(text, timeout=wait)
            if dr.get("ok"):
                th = dr.get("thought") or dr.get("answer") or ""
                if th:
                    return th, "мозг платы %s" % (dr.get("board") or "")
        except Exception:
            pass
    try:
        import wordmind
        th, _i = wordmind.compose(text)
        if th:
            return th, "мозг облака"
    except Exception:
        pass
    return "", ""


def _llm_continue(text, thought):
    """Своя LLM продолжает мысль мозга (без фактов)."""
    try:
        import tinyllm
        topic = (clean(thought) + " " + str(text))[-240:] if thought else str(text)
        max_new = 40
        try:
            import breath
            max_new = int(round(breath.mod("Разговор (мозг + LLM)", 40, 0.25)))
        except Exception:
            pass
        t, info = tinyllm.sample(topic, max_new=max_new)
        if info.get("llm") == "cold":
            return ""
        return t or ""
    except Exception:
        return ""


def _novel(t, seen):
    """Новое ли сообщение (не повтор уже сказанного)."""
    ws = set(re.findall(r"[а-яёa-z]{3,}", t.lower()))
    if len(ws) < 3:
        return False
    for prev in seen:
        pw = set(re.findall(r"[а-яёa-z]{3,}", prev.lower()))
        if pw and len(ws & pw) / float(len(ws)) > 0.7:
            return False
    return True


SAFETY_MSGS = 40          # технический предел от бесконечного цикла
SAFETY_S = 60.0           # и по времени


def _acceptable(text, own=False):
    """Fail closed if there is no evidence of coherent generated speech.

    Corpus adjacency is a heuristic, not a guarantee of meaning or truth.
    """
    words = re.findall(r"[а-яёa-z]+", text.lower())
    if len(words) < 4 or len(set(words)) / len(words) < 0.45:
        return False
    try:
        import coherence
        verdict = coherence.judge(text)
        if verdict.get("score") is not None and not verdict.get("ok"):
            return False
        if own:
            import tinyllm
            return tinyllm.quality(text) >= 0.65 and tinyllm.coherence(text) >= 0.55
        return verdict.get("score") is not None and bool(verdict.get("ok"))
    except Exception:
        return False


def respond_parts(text, ctx=None):
    """One bounded answer; unverified associations never become chat replies."""
    t0 = time.time()
    a, who = "", ""
    try:                       # v3.170: о себе — ИИ читает свою инструкцию
        import instructions
        if instructions.is_self_q(text):
            activity.update("Читаю инструкцию ИИ облака…")
            body = instructions.answer(text)
            sents = re.split(r"(?<=[.!?…])\s+", clean(body))
            a, n = [], 0
            for s_ in sents:                 # связный кусок до ~1000 знаков
                if a and n + len(s_) > 1000:
                    break
                a.append(s_)
                n += len(s_)
            a = " ".join(a).strip()
            who = "инструкция ИИ (прочитана)" if a else ""
    except Exception:
        a = ""
    if not a:
        activity.update("Запрашиваю мысль мозга…")
        thought, who = _brain_thought(text)
        activity.update("Проверяю связность мысли мозга…")
        a = clean(_punct(thought))
        if not _acceptable(a):
            a = ""
    # Include recent user turns to keep the subject, without copying old answers.
    history = " ".join(str(m.get("text") or "") for m in (ctx or [])[-4:]
                       if m.get("role") == "user" and m.get("text") != text)
    prompt = (history[-300:] + " " + str(text)).strip()
    activity.update("Своя LLM готовит короткий ответ…")
    b = clean(_punct(_llm_continue(prompt, a)))
    activity.update("Проверяю связность ответа своей LLM…")
    if not _acceptable(b, own=True) or (a and not _novel(b, [a])):
        b = ""
    parts = [" ".join(x for x in (a, b) if x)]
    by = [x for x in (who if a else "", "своя LLM" if b else "") if x]
    if not parts[0]:
        parts = ["Пока не удалось сформулировать связный ответ. "
                 "Модель могла ещё не обучиться; случайные слова я не буду выдавать за ответ."]
    # v3.187: своя LLM выходит в интернет, если нужно подкрепить факт —
    # вопрос о факте, в ответе числа/имена/определения или просят «проверь».
    # Проверка — отдельным сообщением, сплошным текстом (llmfact.py).
    wc = None
    if store.settings().get("llm_web_check_talk", True):
        try:
            import llmfact
            _a, wc = llmfact.augment(text, parts[0] if by else "", talk=True)
            if wc and wc.get("text"):
                parts.append(clean(wc.pop("text")))
        except Exception as e:
            store.log("LLM-ФАКТЫ", "разговор: %s" % e)
            wc = None
    out = {"talk_by": by, "talk_msgs": len(parts),
           "talk_stop": "ответ проверен" if by else "нет связного ответа",
           "talk_ms": int((time.time() - t0) * 1000),
           "no_facts": not (wc and wc.get("found"))}
    if wc:
        out["web_check"] = wc
    return parts, out


def respond(text, ctx=None):
    """Совместимость: всё одним текстом."""
    parts, info = respond_parts(text, ctx)
    return "\n\n".join(parts), info
