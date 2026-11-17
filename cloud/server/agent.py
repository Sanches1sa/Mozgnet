# -*- coding: utf-8 -*-
# ===========================================================================
# agent.py — ИИ-АГЕНТ: ИНСТРУКЦИИ ДЛЯ КОДИНГА И ДЛЯ ОБЩЕНИЯ (v3.27)
# ---------------------------------------------------------------------------
# Заявка владельца: «составь набор инструкций для кодинга, сделай его как
# ИИ-агент и общения» и «вместо иконки платы — "разговор", пусть пишет и
# отвечает как при диалоге».
# Здесь два набора правил (их видно в панели и в базе знаний) и два
# режима, которые им СЛЕДУЮТ:
#   code_agent() — режим «Код»: понять задачу -> план -> код из проверенных
#       заготовок (codegen) -> проверка -> как запустить -> что дальше;
#       помнит прошлую задачу диалога («а теперь на JS», «пин 5»), при
#       неясной задаче — уточняющие вопросы вместо гадания;
#   talk() — режим «Разговор»: ответ по сути первой фразой, коротко, без
#       служебных пометок и списков источников, с памятью диалога;
#       подробности — по просьбе «расскажи ещё».
# Без LLM: агент — это ПОРЯДОК РАБОТЫ поверх движков облака (codegen,
# песочница, база знаний, интернет), а не генератор выдумок.
# ===========================================================================
import re

CODING_RULES = [
    "Понять задачу полностью: язык, входные/выходные данные, ограничения, окружение. Если чего-то не хватает — спросить, а не угадывать.",
    "Сначала план: 2–5 чётких шагов человеческим языком, затем код.",
    "Писать чистый, читаемый и поддерживаемый код. Следовать современным практикам языка.",
    "Python: type hints где уместно, docstrings для публичных функций, f-strings, pathlib, контекстные менеджеры. Избегать голого except.",
    "JavaScript/TypeScript: современный синтаксис (const/let, arrow, async/await, optional chaining). Ясные имена, избегать var.",
    "Другие языки: следовать идиомам (Go — error handling, Rust — ownership, Java — ясная структура).",
    "Код должен быть полным и запускаемым. Давать команду запуска и пример использования.",
    "Добавлять обработку ошибок и краевых случаев. Не оставлять TODO в финальном коде без пояснения.",
    "Комментарии — только там, где логика неочевидна. Имена переменных и функций должны говорить сами за себя.",
    "Для проектов: структура файлов, зависимости (requirements.txt / package.json), README с инструкцией.",
    "Тестирование: предлагать или писать простые тесты. Честно указывать, что проверено (синтаксис, логика, запуск).",
    "Безопасность: не хардкодить секреты, не выполнять опасные операции без явной просьбы, экранировать пользовательский ввод.",
    "Рефакторинг: при изменении существующего кода сохранять поведение, объяснять, что изменилось.",
    "Сначала пробовать свою обученную LLM, затем проверенные примеры. Честно указывать источник.",
    "Помнить контекст диалога: «а теперь на JS», «добавь логирование» — дорабатывать, а не начинать с нуля.",
    "Для агента «Контур»: использовать инструменты чтения/записи, показывать реальные действия в журнале.",
    "Воля мозга-12 (поле will) влияет на усилие и параметры генерации, но не меняет задачу пользователя.",
]

TALK_RULES = [
    "Отвечать по сути заданного вопроса — первой же фразой.",
    "Коротко: 1–4 предложения; подробности — по просьбе «расскажи ещё».",
    "Помнить разговор: «а она?», «а почему?» — про тему диалога.",
    "Не перечислять источники и служебные пометки (они видны в панели).",
    "Не знаешь — честно сказать и предложить, что уточнить или где найти.",
    "Не обрывать мысль: заканчивать фразу до точки.",
    "Говорить по-человечески: без канцелярита и заготовленных фраз.",
]


def rules():
    zai = []
    try:
        import zai_agent
        zai = zai_agent.rules()
    except Exception:
        pass
    return {"coding": CODING_RULES, "talk": TALK_RULES, "zai": zai}


def rules_text():
    out = ["Инструкции ИИ-агента мозга-12", "",
           "Программирование (режим «Код»):"]
    out += ["%d. %s" % (i + 1, r) for i, r in enumerate(CODING_RULES)]
    out += ["", "Общение (режим «Разговор»):"]
    out += ["%d. %s" % (i + 1, r) for i, r in enumerate(TALK_RULES)]
    try:
        import zai_agent
        out += ["", "Стиль z.ai / GLM:"]
        out += ["%d. %s" % (i + 1, r) for i, r in enumerate(zai_agent.rules())]
    except Exception:
        pass
    return "\n".join(out)


def seed():
    """Инструкции — статьёй базы знаний (бот отвечает «как ты пишешь код»)."""
    try:
        import kb
        cur = kb.article("kb_agent_rules")
        txt = rules_text()
        if not cur or cur.get("text") != txt:
            kb.add_article("Инструкции ИИ-агента: кодинг и общение", txt,
                           source="агент", did="kb_agent_rules")
    except Exception:
        pass


# ------------------------------- кодинг -------------------------------------
_GENERIC_CODE_WORDS = frozenset("""напиши напишите сделай сделайте создай
создайте составь программу программа программы программку скрипт скрипта
функцию функция функции пожалуйста который которая которое которые нужно
нужна надо чтобы python питоне питон javascript arduino ардуино скетч
скетча html страницу bash запрос запроса таблицу таблицы управления
управление работать работает простой простую быстро помощью можно""".split())
_FOLLOW_CODE = re.compile(
    r"^\s*(?:а\s+)?(?:теперь|ещё|еще|и|также)?\s*(?:то\s+же|тоже|так\s+же)?\s*"
    r"(?:на|для|с|поменяй|измени|сделай|добавь|только)\b", re.I)
_LANG_ONLY = re.compile(r"\b(python|питон\w*|javascript|js|джаваскрипт|"
                        r"ардуино|arduino|html|bash|sql|c\+\+)\b", re.I)


def _last_code_request(ctx):
    """Последняя просьба пользователя, на которую агент ответил кодом."""
    hist = [m for m in (ctx or []) if isinstance(m, dict)]
    for i in range(len(hist) - 1, 0, -1):
        m, prev = hist[i], hist[i - 1]
        if m.get("role") != "user" and "```" in str(m.get("text") or "") \
                and prev.get("role") == "user":
            return str(prev.get("text") or "")
    return ""


def _parse_codegen(reply):
    """Разобрать ответ codegen на части (имя, язык, код, проверка, хвост)."""
    # язык может сам содержать скобки: «Arduino (C++)»; пометка — отдельно
    m = re.match(r"Собрала «(?P<name>[^»]+)» на (?P<lang>[^\n]+?)"
                 r"(?P<note> \(язык не назван[^\n]*?\))?:\s*\n\n"
                 r"(?P<code>```[\s\S]*?```)"
                 r"\s*\n\n✓ Проверка: (?P<check>[^\n]*?)\.(?P<params>[^\n]*)"
                 r"(?P<tail>[\s\S]*)$", str(reply or ""))
    return m.groupdict() if m else None


def code_agent(text, lang="", ctx=None):
    """Режим «Код» по инструкциям агента. Возврат: (reply, matched, info)."""
    import codegen
    text = str(text or "").strip()
    task = text
    own = None
    prev = _last_code_request(ctx)
    follow = bool(prev) and len(text.split()) <= 8 and (
        _FOLLOW_CODE.match(text) or _LANG_ONLY.search(text)) and \
        not codegen.extract_fenced(text)[1]
    if follow:
        # правило 7: помнить контекст; новый язык заменяет старый
        base = _LANG_ONLY.sub(" ", prev) if _LANG_ONLY.search(text) else prev
        base = re.sub(r"\bна\s+(?=\s|$)", " ", base)
        task = " ".join((base + " " + text).split())
    # In code mode, try own generation for every task, including tasks
    # without template keywords. Fenced code keeps the analysis/run path.
    if not codegen.extract_fenced(task)[1]:
        import codewrite
        lg, _explicit = codegen.detect_lang(task, lang)
        try:
            own = codewrite.own_write(task, lg)
        except Exception as e:
            own = {"ok": False, "error": "%s: %s" % (type(e).__name__, str(e)[:120])}
        if own and own.get("ok"):
            reply = ("Код сгенерировала своя LLM (кандидат %d из %d):\n\n"
                     "```%s\n%s\n```\n\nПроверка: %s. "
                     "Соответствие всей задаче нужно проверить отдельно." %
                     (own["n"], own["of"], own["lang"], own["code"], own["check"]))
            return reply, True, {"code": True, "code_source": "own_llm"}
        if own and own.get("draft") and codewrite._free():
            # v3.175: своя LLM ХОЧЕТ написать код — показываем её вариант
            # всегда (с честной пометкой), а ниже — проверенный код, если есть
            try:
                rest = codewrite.compose(task, lg, try_own=False)
            except Exception:
                rest = ""
            reply = codewrite.draft_text(own)
            if rest:
                reply += "\n\n---\n\n**Проверенный вариант:**\n\n" + rest
            return reply, True, {"code": True, "code_source": "own_llm_draft"}
    # правило 1: не угадывать. Заготовка совпала одним общим словом
    # («управления»), а главное в просьбе («марсоход») ей чуждо — уточнить
    low = task.lower()
    if not codegen.extract_fenced(task)[1]:
        lg, explicit = codegen.detect_lang(task, lang)
        t = codegen.pick(task, lg, explicit)
        if t:
            keys = [k.lstrip("~") for k in t["keys"]]
            strong = [k for k in t["keys"] if not k.startswith("~") and
                      k in low]
            alien = [w for w in re.findall(r"[а-яёa-z]{6,}", low)
                     if w not in _GENERIC_CODE_WORDS and
                     not any(w[:5] in k or k[:5] in w for k in keys) and
                     w[:5] not in t["name"].lower()]
            weak_match = not strong or \
                codegen._score(t, low, lg, explicit) <= 3.5
            try:
                import codewrite
                _own = codewrite.can_write()
            except Exception:
                _own = False
            if alien and _own:
                try:
                    _txt = codewrite.compose(task, lg, try_own=False)
                except Exception:
                    _txt = ""
                if _txt:
                    return _txt, True, {"code": True, "code_pipeline": True}
            if weak_match and alien and not _own:
                return ("Похоже на мою заготовку «%s», но в просьбе есть "
                        "«%s» — под это у меня проверенного кода нет, "
                        "гадать не буду. Уточни:\n1. На каком языке?\n"
                        "2. Что на входе и что на выходе (пример: «вход 5 → "
                        "выход 120»)?\n3. Подойдёт ли «%s»? Тогда скажи: "
                        "«да, %s»." % (t["name"], alien[0], t["name"],
                                       t["name"].lower())), True, \
                    {"code": True, "agent": "clarify"}
    reply, matched, info = codegen.answer(task, lang=lang, ctx=ctx)
    if not matched:
        import codewrite
        lg, _explicit = codegen.detect_lang(task, lang)
        result = codewrite.compose(task, lg, try_own=False)
        return (result or ("Не удалось написать проверенный код. "
                "Уточни язык, вход и ожидаемый результат.\n\n" + codewrite.status_text())), True, {"code": True}

    parts = _parse_codegen(reply)
    if not parts:
        if "Такой код я пока не умею" in str(reply):
            # v3.129: заготовки нет — сначала нейросеть и проверенные источники
            try:
                import codewrite
                lg_, _ex = codegen.detect_lang(task, lang)
                _txt = codewrite.compose(task, lg_, try_own=False)
            except Exception:
                _txt = ""
            if _txt:
                return _txt, True, {"code": True, "code_pipeline": True}
            try:
                import codewrite
                _st = "\n\n" + codewrite.status_text()
            except Exception:
                _st = ""
            # правило 1 и 6: не гадать — уточнить и честно сказать
            reply = ("Чтобы написать это честно, уточни задачу:\n"
                     "1. На каком языке (Python, JavaScript, Arduino, HTML, "
                     "Bash, SQL)?\n2. Что подаётся на вход и что должно "
                     "получиться на выходе?\n3. Пример: «вход 5 → выход "
                     "120».\n\n" + reply + _st)
        return reply, True, info
    params = (parts.get("params") or "").strip()
    tail = (parts.get("tail") or "").strip()
    mp = re.search(r"Взяла из просьбы:\s*([^\n]+)", params + "\n" + tail)
    if mp:                               # параметры — в план, а не в запуск
        params = mp.group(1).strip()
        tail = tail.replace(mp.group(0), "").strip()
    plan = ["Понять задачу: «%s», язык — %s%s." % (
                parts["name"], parts["lang"].strip(),
                " (продолжаю прошлую задачу)" if follow else ""),
            "Подставить параметры из просьбы%s." % (
                (": " + params.strip(" .")) if params else ""),
            "Собрать код из проверенной заготовки.",
            "Проверить: %s." % parts["check"]]
    out = ["**Задача:** %s на %s%s." % (parts["name"], parts["lang"].strip(),
                                         parts.get("note") or ""),
           "**План:**\n" + "\n".join("%d. %s" % (i + 1, p)
                                     for i, p in enumerate(plan)),
           "**Код:**\n" + parts["code"],
           "**Проверка:** ✓ %s." % parts["check"]]
    if tail:
        out.append("**Как запустить:** " + re.sub(r"^Как использовать:\s*",
                                                  "", tail))
    elif "python" in parts["lang"].lower():
        out.append("**Как запустить:** сохрани в файл .py и выполни "
                   "`python файл.py` (или кнопка «Запустить» у блока кода).")
    out.append("**Дальше:** можно попросить «то же на JavaScript», «измени "
               "параметры …» или «сохрани ответ в файл».")
    if own and not own.get("ok"):
        reason = own.get("error") or own.get("why") or "не получен проверенный кандидат"
        out.insert(0, "Своя LLM попыталась написать код, но проверенного решения "
                   "не получилось: %s. Ниже — заготовка." % reason)
    else:
        out.insert(0, "Использована проверенная заготовка. Своя LLM не готова "
                   "к генерации на этом языке; для своей генерации Python нужны "
                   "NumPy и модель, обученная на коде.")
    info = dict(info or {}, code_source="template")
    return "\n\n".join(out), True, info


# ------------------------------- разговор -----------------------------------
_KEEP_ZONES = ("Документы", "Воображение", "Код", "Математика")


def talk_style(reply, zone, long_mode=False):
    """Ответ мозга -> реплика живого диалога (правила общения)."""
    t = str(reply or "")
    if zone in _KEEP_ZONES:
        return t                       # файлы, картинки, код, числа — как есть
    t = re.sub(r"(?m)^_?Источник[и]?(?: сети)?:[^\n]*_?\s*$", "", t)
    t = re.sub(r"(?m)^Также в «[^»]+»:\s*", "", t)
    t = re.sub(r"\n*\((?:Режим «длинно»|в материалах сети)[^)]*\)\s*", "\n", t)
    t = re.sub(r"\n*Почему так думаю:[\s\S]*$", "", t)
    t = re.sub(r"\n{3,}", "\n\n", t).strip()
    if not long_mode:
        try:
            import textutils
            short = textutils.cut_at_sentence(t, 600)
        except Exception:
            short = t[:600]
        if len(short) < len(t) - 20:
            t = short.rstrip() + "\n\nРассказать подробнее?"
    return t


# --------------------------- мысли облака (v3.28) ----------------------------
# Заявка владельца: «пусть разговор в облаке будет мыслями облака». В режиме
# «💬 Разговор» облако не только отвечает, но и ДУМАЕТ ВСЛУХ — и каждая
# часть мысли берётся из его настоящего состояния, а не из заготовки:
#   * связи — какие материалы сети материалов зажглись на реплику;
#   * горящие узлы — разрядка активации сети (слова — из этих материалов);
#   * нить разговора — общие слова с прошлой репликой человека;
#   * мысли плат — если плата недавно думала о том же;
#   * чего не знаю — тема реально ставится в очередь любопытства;
#   * чему научилась — что записал qalearn (вопрос / пара с источником).
# Мысль уходит в ленту мыслей (store.thoughts, board="облако") — её видят
# панель и платы; в сеть материалов своя мысль НЕ учится (честность).
_WEAK_ZONES = ("Предположение", "Речь из словаря", "Облако",
               "Сеть материалов")


def _stem_set(text):
    try:
        import mozgnet
        return {mozgnet._stem(w) for w in
                re.findall(r"[а-яёa-z]{4,}", str(text or "").lower())
                if w not in mozgnet._STOP}
    except Exception:
        return set(re.findall(r"[а-яёa-z]{4,}", str(text or "").lower()))


def cloud_thought(text, reply, zone, info=None, ctx=None):
    """Мысль облака о реплике: список коротких честных предложений."""
    import brain
    out = []
    q_st = _stem_set(text)
    # 1) связи в сети материалов + горящие узлы
    try:
        import mozgnet
        if brain._MN is not None and q_st:
            with brain._MN_LOCK:
                net = brain.mn()
                hits = net.retrieve(text, k=6)
                act = net.spread([s for s in q_st if s in net.nodes], depth=2)
            titles = []
            for _sc, d in hits:
                t = str(d.get("title") or "").strip()
                src = str(d.get("src") or "")
                if not t or src in ("вопрос", "диалог", "мысль") or \
                        t.lower().startswith(("вопрос", "мысль", "диалог")):
                    continue
                t = re.sub(r"^(Инструкции|Код|Сочинение|Ответ):\s*", "", t)
                if t and t not in titles and len(titles) < 3:
                    titles.append(t[:60])
            if titles:
                out.append("Это связано у меня с материалами: %s." %
                           ", ".join("«%s»" % t for t in titles))
            hot = [s for s, _a in sorted(act.items(), key=lambda kv: -kv[1])
                   if s not in q_st and len(s) >= 4][:30]
            if hot:
                # стемы -> живые слова из текстов, где они встретились
                pool = " ".join([str(reply or "")] + titles)
                forms = {}
                for w in re.findall(r"[а-яё]{4,}", pool.lower()):
                    st = mozgnet._stem(w)
                    if st in hot and st not in forms:
                        forms[st] = w
                words = [forms[s] for s in hot if s in forms][:5]
                if len(words) >= 2:
                    out.append("В сети загораются узлы: %s." %
                               ", ".join(words))
    except Exception:
        pass
    # 2) нить разговора
    try:
        prev = [str(m.get("text") or "") for m in (ctx or [])
                if isinstance(m, dict) and m.get("role") == "user"]
        if prev and prev[-1].strip() == str(text or "").strip():
            prev = prev[:-1]
        if prev:
            common = q_st & _stem_set(prev[-1])
            last = re.sub(r"\s+", " ", prev[-1]).strip()[:70]
            if common:
                out.append("Продолжаю нашу тему: «%s»." % last)
            elif len(q_st) >= 2:
                out.append("Мы перешли к новой теме после «%s»." % last)
    except Exception:
        pass
    # 3) мысли плат о том же
    try:
        import store
        for t in store.thoughts_list(40):
            if str(t.get("board") or "") == "облако":
                continue
            tt = str(t.get("text") or "")
            if len(q_st & _stem_set(tt)) >= 1 and len(tt) > 12:
                out.append("Плата «%s» недавно думала об этом: «%s»." % (
                    str(t.get("board") or "?")[:20], tt[:100]))
                break
    except Exception:
        pass
    # 4) чего не знаю — честно и с делом
    low_r = str(reply or "").lower()
    if zone in _WEAK_ZONES and ("не знаю" in low_r or zone == "Предположение"
                                or zone == "Сеть материалов"):
        try:
            import curiosity
            curiosity.want(text, why="разговор: знаю мало")
            out.append("Знаю об этом мало — поставила тему в очередь "
                       "любопытства, дочитаю.")
        except Exception:
            pass
    # 5) чему научилась на этой реплике
    learned = (info or {}).get("learned")
    if learned == "pair":
        out.append("Ответ опирается на источник — запомнила вопрос и ответ "
                   "(👍 сделает его подтверждённым).")
    return out


def talk(text, ctx=None, long_mode=False, chat_id=None, deliver=None):
    """Режим «Разговор»: мозг отвечает, агент оформляет как живую речь,
    v3.28: и облако думает вслух (мысли облака — из его состояния);
    v3.29: слабый ответ дополняет плата (единый мозг), мысль уходит плате."""
    import brain
    reply, zone, info = brain.think(text, long_mode, route="cloud",
                                    chat_id=chat_id, ctx=ctx, deliver=deliver)
    styled = talk_style(reply, zone, long_mode)
    info = dict(info or {})
    info["src_zone"] = zone
    if zone in _KEEP_ZONES:
        return styled, zone, info
    thought = []
    try:
        thought = cloud_thought(text, reply, zone, info, ctx)
    except Exception:
        thought = []
    if thought:
        t = " ".join(thought)
        # v3.97: мысль в ответ не дописываем — только в ленту и платам
        info["thought"] = t
        try:
            import store
            store.thoughts_add("облако", t, kind="cloud")
        except Exception:
            pass
        try:                    # v3.29: мысль облака — плате (D6 + её сеть)
            import unify
            unify.thought_to_boards(t)
        except Exception:
            pass
    return styled, brain.Z["thought"], info
