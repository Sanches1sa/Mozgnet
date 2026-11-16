# -*- coding: utf-8 -*-
"""
strict.py — v3.122: две галочки ответов (Настройки → Общие).

  «Не показывать бред» (answer_strict, вкл.): в ответ НЕ идут
    • собственные фантазии своей LLM («Своя LLM от себя», «🤖 …», строка мысли);
    • фразы LLM «по найденным материалам», если они вырождены, с мусором
      («англ», «ан»), несвязны или почти пусты;
    • мысли мозга (платы и облака), если они несвязны (судья coherence,
      порог не ниже 0,6), повторяют сам вопрос или короче 5 слов;
    • исходники кода из базы («Исходник: …») на вопросы не про код.
    Нечего сказать — честное «не знаю».

  «Отвечать без сети» (answer_offline, вкл.): когда у облака нет интернета,
    бот отвечает всем, что есть (база, память, мозг, LLM). Выключено —
    без интернета отвечает только тем, что лежит в базе знаний / памяти
    подтверждённых ответов / навыках, остальное — честное сообщение.
"""
import re
import socket
import threading
import time

import store

_NET = {"ok": True, "ts": 0.0, "busy": False}
_N = {"blocked": 0, "offline": 0}

NO_ANSWER = ("Не знаю: в базе знаний нет статьи по этому вопросу, а мысли мозга "
             "и своей LLM по нему несвязные — не показываю (галочка «Не "
             "показывать бред»). Если знаешь ответ — напиши «правильный "
             "ответ: …», я запомню.")
OFFLINE = ("Облако сейчас без интернета, а в базе знаний ответа на этот "
           "вопрос нет. Отвечу, когда связь вернётся (или включи «Отвечать "
           "без сети» — тогда ответят мысли мозга и своей LLM, без проверки "
           "фактами).")


def _s(key, dflt=True):
    try:
        return bool(store.settings().get(key, dflt))
    except Exception:
        return dflt


def on():
    return _s("answer_strict", True)


def offline_ok():
    return _s("answer_offline", True)


def _probe():
    ok = False
    for host in ("ru.wikipedia.org", "ya.ru", "1.1.1.1"):
        try:
            port = 53 if host == "1.1.1.1" else 443
            socket.create_connection((host, port), timeout=3).close()
            ok = True
            break
        except Exception:
            continue
    _NET.update(ok=ok, ts=time.time(), busy=False)


def online():
    """Есть ли у облака интернет (проверка раз в минуту, фоном)."""
    if time.time() - _NET["ts"] > 60 and not _NET["busy"]:
        _NET["busy"] = True
        if _NET["ts"] == 0.0:
            _probe()                       # первый раз — сразу
        else:
            threading.Thread(target=_probe, daemon=True).start()
    return _NET["ok"]


_CODE_RE = re.compile(r"\b(код|кода|коде|скрипт\w*|функци\w*|python|питон\w*|"
                      r"javascript|c\+\+|arduino|скетч\w*|программ\w*|import|"
                      r"def|class|библиотек\w*|github|исходник\w*)\b", re.I)


def code_question(q):
    return bool(_CODE_RE.search(str(q or "")))


def _words(t):
    return re.findall(r"[а-яёa-z0-9]+", str(t or "").lower())


def text_ok(t, question="", min_words=5):
    """Сгенерированный текст (мысль мозга или LLM) годится в ответ?"""
    t = str(t or "").strip()
    ws = _words(t)
    if len(ws) < min_words:
        return False
    lat = [w for w in ws if re.match(r"^[a-z]+$", w)]
    if len(lat) > 0.2 * len(ws) and not code_question(question):
        return False                       # v3.124: «beat italia dashboard»
    try:
        import llm2
        if llm2.looks_degenerate(t):
            return False
        cl = llm2.thought_clean(t)
        if len(_words(cl)) < len(ws):          # был мусор («англ», «ан»…)
            return False
    except Exception:
        pass
    try:
        import llmlink
        if llmlink.degenerate(t):
            return False
    except Exception:
        pass
    if any(len(w) <= 2 and w not in ("в", "и", "к", "с", "у", "о", "а", "я",
                                     "на", "по", "не", "из", "за", "от", "до",
                                     "же", "ли", "бы", "то", "во", "со", "об",
                                     "но", "он", "мы", "вы", "ни", "да")
           and not w.isdigit() for w in ws):
        return False                           # обрывки «ан», «н7»
    if question:                               # эхо вопроса — не ответ
        qs = set(w[:5] for w in _words(question) if len(w) > 2)
        cs = [w[:5] for w in ws if len(w) > 2]
        if cs and sum(1 for w in cs if w in qs) >= 0.6 * len(cs):
            return False
    try:
        import coherence
        j = coherence.judge(t)
        if j.get("score") is not None and \
                j["score"] < max(0.6, coherence.threshold()):
            return False
    except Exception:
        pass
    return True


def block(where, text):
    _N["blocked"] += 1
    try:
        store.log("НЕ БРЕД", "%s: не показано: %s" % (where, str(text)[:120]))
    except Exception:
        pass


def status():
    return {"strict": on(), "offline_ok": offline_ok(), "online": _NET["ok"],
            **_N}
