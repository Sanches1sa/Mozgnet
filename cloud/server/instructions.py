# -*- coding: utf-8 -*-
"""v3.170 — ИНСТРУКЦИЯ ИИ ОБЛАКА (кто я, что умею, как работаю).

Владелец: «видимо ИИ не знает инструкций — напиши ему инструкции, что
делать, только честно, без запрограммированных ответов».

Это не набор готовых ответов, а ДОКУМЕНТ, который ИИ читает, как статью:
  * собирается из настоящего состояния облака при старте и после каждого
    изменения App Lab: режимы чата, источники (база знаний, интернет,
    Confluence), платы на связи, своя LLM, приложения App Lab с примерами;
  * лежит в базе знаний статьёй «Инструкция ИИ облака мозг-12…» (её читает
    и своя LLM при дообучении) и файлом data/ai_instructions.md;
  * на вопросы о себе («что ты умеешь», «перечисли свои возможности»,
    «как ты работаешь», «как написать приложение») ИИ отвечает куском этой
    инструкции подряд — во всех режимах, и в «Разговоре» тоже, — а мозг и
    своя LLM дополняют своими словами.
Правила честности ИИ (раздел 3) — тоже здесь, их видно владельцу.
"""
import os
import re
import threading
import time

import store

DOC_ID = "ai_instructions"
TITLE = "Инструкция ИИ облака мозг-12: кто я, что умею и как работаю"
_LOCK = threading.Lock()
_LAST = {"ts": 0.0, "chars": 0}

_SELF = re.compile(
    r"(?:что|чего)\s+(?:ты\s+|вы\s+)?(?:умеешь|умеете|можешь\s+делать|можете\s+делать)|"
    r"(?:перечисли|расскажи|опиши|назови)\w*\s+(?:\w+\s+){0,3}(?:что\s+ты\s+)?(?:умеешь|можешь|"
    r"свои\s+(?:возможности|функции|умения|навыки|приложения))|"
    r"(?:твои|ваши|у\s+тебя)\s+(?:возможности|функции|умения|навыки|приложения|инструкци)|"
    r"(?:кто\s+ты|ты\s+кто|что\s+ты\s+(?:такое|за))|"
    r"как\s+ты\s+(?:работаешь|устроен|устроена|отвечаешь|думаешь)|"
    r"как\s+(?:тобой|с\s+тобой)\s+(?:пользоваться|работать)|"
    r"(?:как|можно\s+ли)\s+(?:написать|сделать|добавить|создать)\s+(?:\w+\s+){0,2}"
    r"(?:приложени\w*|навык\w*)|app\s*lab|апп\s*лаб", re.I)


def is_self_q(text):
    t = str(text or "")
    return len(t) <= 160 and bool(_SELF.search(t))


def _ru(n, a, b, c):
    n = abs(int(n))
    if n % 10 == 1 and n % 100 != 11:
        return a
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return b
    return c


def build():
    """Текст инструкции из настоящего состояния облака."""
    s = store.settings()
    L = []
    try:
        ver = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "VERSION"), encoding="utf-8").read().strip()
    except Exception:
        ver = "?"
    L.append("1. Кто я.")
    L.append("Я ИИ облака мозг-12 (neuro12-cloud, версия %s). Я отвечаю в чате панели, "
             "в ботах МАКС и Telegram и работаю вместе с платами neuro-12." % ver)
    try:
        st = store.stats()
        n = int(st.get("kb_articles") or 0)
        L.append("В моей базе знаний %d %s. Платы neuro-12 на связи: %d из %d." % (
            n, _ru(n, "статья", "статьи", "статей"), st.get("boards_online", 0),
            st.get("boards_total", 0)))
    except Exception:
        pass
    try:
        import tinyllm
        ts = tinyllm.status()
        L.append("Своя LLM %s." % ("обучена, шагов обучения %d" % ts.get("steps", 0)
                                    if ts.get("steps") else "ещё не обучена"))
    except Exception:
        pass
    src = ["из своей базы знаний"]
    if s.get("web_on"):
        try:
            import webfetch
            eng = ", ".join({"mailru": "Поиск Mail.ru", "mojeek": "Mojeek",
                             "google": "Google", "yandex": "Яндекс"}.get(x, x)
                            for x in webfetch.engines_list())
        except Exception:
            eng = "поисковики"
        src.append("из Википедии и Рувики")
        src.append("из поисковиков (%s)" % eng)
    try:                                   # v3.174: источники по ссылкам
        import sources
        xs = [x for x in sources.sources() if x.get("on")]
        if xs:
            src.append("из источников по ссылкам владельца (%s)" % ", ".join(
                "%s — %s" % (x.get("name") or x["url"], {"confluence": "Confluence",
                 "mediawiki": "вики", "site": "сайт", "file": "документ"}.get(x["kind"], x["kind"]))
                for x in xs[:8]))
    except Exception:
        pass
    L.append("Знания я беру %s." % ", ".join(src))

    L.append("")
    L.append("2. Что я умею.")
    L.append("В режимах Авто и Облако я отвечаю на вопросы по базе знаний, интернету и "
             "документации, считаю, решаю уравнения, пишу документы и рисую. "
             "В режиме Разговор я просто разговариваю: пишет мозг, своя LLM дополняет. "
             "В режиме Код я пишу и проверяю код. Если в базе нет статьи по теме, я сначала "
             "читаю её, а потом отвечаю.")
    try:
        import pyapps
        apps = [m for m in pyapps.apps() if m.get("enabled")]
    except Exception:
        apps = []
    if apps:
        L.append("Мои навыки — это приложения App Lab на Python, их %d:" % len(apps))
        for m in apps[:40]:
            ex = (m.get("examples") or [])[:1]
            L.append("• %s — %s%s" % (m.get("name"), (m.get("desc") or "без описания").rstrip("."),
                                      (". Пример: «%s»." % ex[0]) if ex else "."))
        L.append("Новое приложение владелец добавляет в App Lab, а я могу написать его "
                 "сама по описанию — кнопкой «ИИ, напиши приложение».")
    else:
        L.append("Приложений App Lab пока нет — их можно добавить в разделе App Lab.")

    L.append("")
    L.append("3. Как я отвечаю.")
    L.append("Факты я беру только из источника и указываю, откуда они: статья базы знаний, "
             "страница в интернете, документация. Чего нет в источниках, я не выдумываю и "
             "честно говорю, что не знаю. Мысли мозга и текст своей LLM — это не факты, я "
             "их так и подписываю. Команды на компьютере выполняю только для владельца в "
             "панели. Покупать, платить и вводить пароли от имени владельца я не могу.")

    L.append("")
    L.append("4. Как я пишу себе приложения App Lab.")
    try:
        import pyapps
        L.append(pyapps.APP_GUIDE.strip())
        L.append("")
        L.append(pyapps.API_DOC.strip())
        L.append("")
        L.append("Своя LLM участвует в программировании первой: по затравке «App Lab: описание» "
                 "она пишет кандидаты кода, облако проверяет каждый запуском, и первый рабочий "
                 "сохраняется с автором «своя LLM». Не справилась — пишет локальная модель, "
                 "затем заготовки. Учится своя LLM на коде включённых приложений и на своей "
                 "инструкции:")
        L.append(pyapps.LLM_GUIDE.strip())
    except Exception:
        pass
    return "\n".join(L)


def refresh():
    """Пересобрать инструкцию и положить в базу знаний (и в файл)."""
    with _LOCK:
        txt = build()
        try:
            import kb
            kb.add_article(TITLE, txt, source="инструкция облака (обновляется сама)",
                           did=DOC_ID)
        except Exception as e:
            store.log("ИНСТРУКЦИЯ", "в базу не легла: %s" % e)
        try:
            with open(store.p("ai_instructions.md"), "w", encoding="utf-8") as f:
                f.write("# " + TITLE + "\n\n" + txt + "\n")
        except Exception:
            pass
        _LAST.update(ts=time.time(), chars=len(txt))
    return txt


def text():
    try:
        with open(store.p("ai_instructions.md"), encoding="utf-8") as f:
            return f.read()
    except OSError:
        return refresh()


def answer(question, long_mode=False):
    """Кусок инструкции подряд по вопросу о себе. -> текст | ""."""
    t = text()
    low = str(question or "").lower()
    # раздел по смыслу вопроса — с него и читаем
    sec = "2. Что я умею."
    if re.search(r"кто\s+ты|ты\s+кто|что\s+ты\s+(?:такое|за)", low):
        sec = "1. Кто я."
    elif re.search(r"как\s+ты\s+(?:отвечаешь|думаешь)|честн", low):
        sec = "3. Как я отвечаю."
    elif re.search(r"приложени|app\s*lab|апп\s*лаб|навык", low) and \
            re.search(r"напис|сдела|добав|созда", low):
        sec = "4. Как я пишу себе приложения App Lab."
    i = t.find(sec)
    part = t[i:] if i >= 0 else t
    heads = ("1. Кто я.", "2. Что я умею.", "3. Как я отвечаю.",
             "4. Как я пишу себе приложения App Lab.")
    ends = [part.find("\n" + h, len(sec)) for h in heads if h != sec]
    ends = [e for e in ends if e > 0]
    body = part[len(sec):min(ends)] if ends else part[len(sec):]
    limit = 2600 if long_mode else 1500
    body = body.strip()
    if len(body) > limit:
        cut = body.rfind("\n", 0, limit)
        body = body[:cut if cut > 200 else limit].rstrip() + "\n…"
    return body


def start():
    def _go():
        time.sleep(8)
        try:
            refresh()
        except Exception as e:
            store.log("ИНСТРУКЦИЯ", "не собралась: %s" % e)
        try:                               # v3.171: код App Lab — своей LLM
            import pyapps
            pyapps.teach_llm()
        except Exception:
            pass
    threading.Thread(target=_go, daemon=True, name="ai-instructions").start()
