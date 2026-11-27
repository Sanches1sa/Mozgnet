# -*- coding: utf-8 -*-
# ===========================================================================
# applab.py — ЛАБОРАТОРИЯ ПРИЛОЖЕНИЙ (App Lab) в стиле Arduino App Lab
# ---------------------------------------------------------------------------
# Приложение = стопка КИРПИЧИКОВ (bricks). Каждый кирпичик — РЕАЛЬНАЯ
# функция платформы: мост к плате 127.0.0.1:7000, мозг облака (зонная
# сеть без LLM), база знаний, память, навыки, словарь, Википедия.
# Кирпичик берёт параметры, умеет подставлять переменные {имя} и {last}
# (выход предыдущего кирпича) и кладёт свой выход в переменную.
# Запуск — по шагам, с журналом: видно, какой кирпичик что сделал.
# Всё без LLM: LLM-учитель только объясняет кирпичики в экскурсии.
# Хранилище: data/applab.json (приложения владельца).
# ===========================================================================
import re
import time

import store

MAX_BRICKS = 24                     # кирпичиков в одном приложении
MAX_WAIT = 30.0                     # максимум одной паузы, сек
BUDGET = 150.0                      # общий бюджет запуска, сек
_TEXT = 2000                        # лимит текстового параметра


# ============================ КАТАЛОГ КИРПИЧИКОВ ============================
# cat: board=Плата, brain=Мозг, know=Знания, mem=Память, logic=Логика,
#      out=Вывод. params: name/label/ph/kind(text|num|sel|choice).
def _cat_meta():
    return {
        "board": {"name": "Плата", "color": "#10a37f",
                  "hint": "живая плата 127.0.0.1:7000 — думает сама, без LLM"},
        "brain": {"name": "Мозг", "color": "#f59e0b",
                  "hint": "мозг облака: зонная сеть, навыки, генератор"},
        "know": {"name": "Знания", "color": "#3b82f6",
                  "hint": "база знаний, словарь, Википедия"},
        "math": {"name": "Математика", "color": "#ec4899",
                  "hint": "v3.8: формулы и модели — мозг считает сам, "
                           "без LLM"},
        "mem": {"name": "Память", "color": "#eab308",
                  "hint": "облачная память: заметки и поиск по ним"},
        "logic": {"name": "Логика", "color": "#8b5cf6",
                  "hint": "переменные, шаблоны, условия, паузы"},
        "out": {"name": "Вывод", "color": "#64748b",
                  "hint": "что показать и куда положить результат"},
    }


def _p(name, label, ph="", kind="text"):
    return {"name": name, "label": label, "ph": ph, "kind": kind}


BRICKS = [
    # ------------------------------ ПЛАТА -----------------------------------
    {"id": "board.ask", "cat": "board", "name": "Спросить плату",
     "desc": "Мост пошлёт вопрос плате 127.0.0.1:7000 — плата подумает "
             "зонной сетью (без LLM) и ответит. Ждать до минуты.",
     "params": [_p("question", "Вопрос плате", "например: что ты помнишь?")],
     "doc": "bridge.send()"},
    {"id": "board.say", "cat": "board", "name": "Последняя мысль платы",
     "desc": "Взять последнюю мысль платы из её веб-хоста (say). "
             "Плата ничего не делает — просто делится мыслью.",
     "params": [], "doc": "bridge.status().state.say"},
    {"id": "board.state", "cat": "board", "name": "Состояние платы",
     "desc": "Версия мозга, нейроны онлайн, заметки в памяти платы, "
             "время работы. Живой снимок состояния.",
     "params": [], "doc": "bridge.status().state"},
    {"id": "board.thought", "cat": "board", "name": "Мысль в ленту плат",
     "desc": "Записать текст в общую ленту «Мысли плат» от имени облака — "
             "её увидят все платы в хронике.",
     "params": [_p("text", "Текст мысли", "что записать в ленту")],
     "doc": "store.thoughts_add()"},

    # ------------------------------ МОЗГ ------------------------------------
    {"id": "brain.think", "cat": "brain", "name": "Подумать над вопросом",
     "desc": "Полный ответ мозга облака: навыки, база знаний, сеть "
             "материалов, память. Без LLM.",
     "params": [_p("question", "Вопрос", "о чём подумать"),
                _p("long", "Длинный ответ (сеть материалов)", "", "sel")],
     "doc": "brain.think(route='cloud')"},
    {"id": "brain.article", "cat": "brain", "name": "Статья по теме",
     "desc": "Генератор пишет длинную статью по своим материалам "
             "(нейросеть материалов) и кладёт её в базу знаний.",
     "params": [_p("topic", "Тема статьи", "о чём написать")],
     "doc": "brain.write_article()"},
    {"id": "brain.thought", "cat": "brain", "name": "Свободная мысль",
     "desc": "Короткое рассуждение сети материалов на тему — как «мысль "
             "<тема>» в чате.",
     "params": [_p("topic", "Тема", "о чём порассуждать")],
     "doc": "brain.free_think()"},
    {"id": "brain.skill", "cat": "brain", "name": "Навык по фразе",
     "desc": "Подобрать и запустить функцию мозга по обычной фразе: "
             "«посчитай 7*6», «который час», «статус».",
     "params": [_p("phrase", "Фраза", "например: посчитай 12*7")],
     "doc": "skills.match() + skills.run_skill()"},
    {"id": "brain.func", "cat": "brain", "name": "Запустить функцию",
     "desc": "Запустить функцию из реестра по её id (вкладка «Функции "
             "мозга») с текстом-заданием.",
     "params": [_p("id", "id функции", "math"),
                _p("text", "Задание (текст)", "аргумент функции")],
     "doc": "skills.run_skill(id, text)"},
    {"id": "brain.compose", "cat": "brain", "name": "Собрать приложение",
     "desc": "Мозг сам программирует: выбирает рецепт из примеров кода "
             "(база рецептов) под вопрос, собирает приложение из "
             "кирпичиков и сохраняет его в App Lab.",
     "params": [_p("question", "О чём приложение",
                   "например: отчёт по плате"),
                _p("run", "Сразу запустить", "", "sel")],
     "doc": "recipe_pick() + app_save()"},
    {"id": "brain.autocode", "cat": "brain", "name": "Написать себе "
     "функцию",
     "desc": "v3.18: мозг сам пишет себе функцию по теме из своих "
             "заготовок, проверяет её тестами в песочнице и публикует в "
             "реестр функций (автор — мозг). Честно: тесты не прошли — "
             "функция не публикуется.",
     "params": [_p("topic", "Тема функции",
                   "например: конвертер км в мили")],
     "doc": "autocode.write_function()"},
    {"id": "brain.writer", "cat": "brain", "name": "Письмо по теме",
     "desc": "v3.18: инструмент самостоятельного письма — мозг собирает "
             "раз-вёрнутое письмо из ВСЕХ своих материалов (статьи базы "
             "знаний, сеть, словарь, память) с планом и подписями "
             "источников. Честно: не хватает материалов — так и пишет.",
     "params": [_p("topic", "Тема письма", "о чём написать письмо")],
     "doc": "writer.write_long()"},

    # ----------------------------- ЗНАНИЯ -----------------------------------
    {"id": "know.kb", "cat": "know", "name": "Искать в базе знаний",
     "desc": "Найти лучшую статью базы знаний по запросу — файлы, статьи "
             "плат, самоучитель.",
     "params": [_p("query", "Запрос", "что искать")],
     "doc": "kb.search()"},
    {"id": "know.wiki", "cat": "know", "name": "Википедия: выжимка",
     "desc": "Честная выжимка из Википедии (не LLM). Нужен включённый "
             "интернет (web_on).",
     "params": [_p("topic", "Тема", "о чём спросить Википедию")],
     "doc": "webfetch.wiki_search()"},
    {"id": "know.dict", "cat": "know", "name": "Словарь: что значит слово",
     "desc": "Посмотреть слово в общих словарях русского языка "
             "(ru_top + ru_pos + словари плат).",
     "params": [_p("word", "Слово", "например: нейрон")],
     "doc": "brain.dict_lookup()"},

    # --------------------------- МАТЕМАТИКА ---------------------------------
    {"id": "math.formula", "cat": "math", "name": "Формула: посчитать",
     "desc": "Мозг сам выбирает формулу из своей базы (площадь круга, "
             "путь/скорость, проценты, Ома, Пифагор, рост на проценты…), "
             "подставляет числа из вопроса и честно показывает расчёт. "
             "Без LLM.",
     "params": [_p("question", "Вопрос или тема",
                   "площадь круга радиусом 5")],
     "doc": "mathmodels.formula_solve()"},
    {"id": "math.model", "cat": "math", "name": "Модель по данным",
     "desc": "Строит математическую модель по парам чисел x,y — линейную, "
             "квадратичную или экспоненциальную (выбирает по R², слабую "
             "честно называет слабой) и делает прогноз.",
     "params": [_p("data", "Данные парами x,y через ;",
                   "1,2; 2,4; 3,5; 4,9"),
                _p("forecast", "Прогноз для x (пусто = без прогноза)",
                   "5", "num")],
     "doc": "mathmodels.fit_model()"},

    # ----------------------------- ПАМЯТЬ -----------------------------------
    {"id": "mem.remember", "cat": "mem", "name": "Запомнить заметку",
     "desc": "Положить заметку в облачную память — она переживёт "
             "перезапуск платы и облака.",
     "params": [_p("text", "Заметка", "что запомнить")],
     "doc": "store.notes_add()"},
    {"id": "mem.recall", "cat": "mem", "name": "Что помню про…",
     "desc": "Найти заметки памяти по теме.",
     "params": [_p("topic", "Тема", "что поискать в памяти")],
     "doc": "store.notes() + поиск"},
    {"id": "mem.list", "cat": "mem", "name": "Последние заметки",
     "desc": "Показать последние 5 заметок облачной памяти.",
     "params": [], "doc": "store.notes()"},

    # ----------------------------- ЛОГИКА -----------------------------------
    {"id": "logic.set", "cat": "logic", "name": "Положить в переменную",
     "desc": "Сохранить текст (можно с {переменными} и {last}) в "
             "именованную переменную.",
     "params": [_p("name", "Имя переменной", "например: город"),
                _p("value", "Значение", "текст; {last} = выход предыдущего")],
     "doc": "vars[name] = value"},
    {"id": "logic.template", "cat": "logic", "name": "Собрать текст",
     "desc": "Склеить текст из шаблона: подставить {last} и {переменные} "
             "в любой текст.",
     "params": [_p("template", "Шаблон",
                   "Плата подумала: {last}")],
     "doc": "format(template)"},
    {"id": "logic.if", "cat": "logic", "name": "Если… то… иначе",
     "desc": "Если в {last} (или в переменной) встречается слово — взять "
             "один текст, иначе другой.",
     "params": [_p("needle", "Что искать", "слово-признак"),
                _p("where", "Где искать", "", "sel"),
                _p("then", "Тогда (текст)", "если нашли"),
                _p("else", "Иначе (текст)", "если не нашли")],
     "doc": "if needle in source"},
    {"id": "logic.wait", "cat": "logic", "name": "Подождать секунд",
     "desc": "Пауза между кирпичиками (например, чтобы плата успела "
             "подумать). Максимум 30 секунд.",
     "params": [_p("sec", "Секунд", "5", "num")],
     "doc": "time.sleep(min(sec, 30))"},

    # ------------------------------ ВЫВОД -----------------------------------
    {"id": "out.result", "cat": "out", "name": "Показать результат",
     "desc": "Завершить приложение и показать текст (по умолчанию — "
             "выход последнего кирпичика).",
     "params": [_p("text", "Текст (пусто = {last})", "итог приложения")],
     "doc": "final result"},
    {"id": "out.note", "cat": "out", "name": "Результат в память",
     "desc": "Сохранить текущий результат приложения как заметку "
             "(продолжить кирпичики дальше).",
     "params": [_p("text", "Текст (пусто = {last})",
                   "что сохранить в память")],
     "doc": "store.notes_add(result)"},
]

_BRICK_BY_ID = {b["id"]: b for b in BRICKS}


def bricks_catalog():
    """Каталог для палитры интерфейса (категории + кирпичики)."""
    cats = _cat_meta()
    out = []
    for cid, meta in cats.items():
        out.append({"id": cid, "name": meta["name"], "color": meta["color"],
                    "hint": meta["hint"],
                    "bricks": [b for b in BRICKS if b["cat"] == cid]})
    return out


# ============================== ПАРАМЕТРЫ ===================================
_VAR_RE = re.compile(r"\{([a-zA-Zа-яёА-ЯЁ0-9_ -]{1,24})\}")


def _fmt(val, vars_):
    """Подставить {last} и {имена} в строковый параметр."""
    s = str(val or "")

    def sub(m):
        key = m.group(1).strip()
        if key == "last":
            return str(vars_.get("last", ""))
        if key in vars_:
            return str(vars_[key])
        return m.group(0)                     # неизвестная переменная — как есть
    return _VAR_RE.sub(sub, s)


def _bool(v, default=False):
    if isinstance(v, bool):
        return v
    s = str(v or "").strip().lower()
    if s in ("1", "да", "yes", "true", "on", "длинный", "длинно"):
        return True
    if s in ("0", "нет", "no", "false", "off", ""):
        return False
    return default


# ============================= КИРПИЧИКИ ====================================
def _run_board_ask(pr, ctx):
    import bridge
    q = str(pr.get("question") or "").strip()
    if not q:
        return False, ("вопрос не задан — открой кирпичик и впиши вопрос "
                       "(или подставь {имя} переменной)")
    r = bridge.send(q, user="app lab",
                    wait=min(60.0, float(store.settings().get("board_timeout")
                                          or 45)))
    if not r.get("ok"):
        return False, "плата недоступна: %s" % (r.get("error") or "?")
    a = str(r.get("answer") or "").strip()
    if not a:
        return True, ("плата получила вопрос и думает (мост %s) — ответа "
                      "пока нет" % r.get("url", ""))
    return True, a


def _run_board_say(pr, ctx):
    import bridge
    ok, st = bridge.probe(save=False)
    if not ok:
        return False, "плата недоступна: %s" % st
    say = str((bridge.status().get("state") or {}).get("say") or "").strip()
    return True, say or "плата пока молчит — мысли не было"


def _run_board_state(pr, ctx):
    import bridge
    ok, _st = bridge.probe(save=False)
    s = bridge.status()
    if not ok:
        return False, "плата недоступна: %s" % (s.get("last_err") or "?")
    st = s.get("state") or {}
    parts = ["Плата %s" % (st.get("version") or "?")]
    if st.get("device"):
        parts.append(str(st["device"]))
    if st.get("neurons_online") is not None:
        parts.append("нейронов %s/12" % st["neurons_online"])
    if st.get("mem_count") is not None:
        parts.append("заметок %s" % st["mem_count"])
    if st.get("up_sec") is not None:
        m = int(st["up_sec"]) // 60
        parts.append("работает %d мин" % m if m else
                     "работает %d сек" % int(st["up_sec"]))
    say = str(st.get("say") or "").strip()
    if say:
        parts.append("мысль: %s" % say[:200])
    return True, " · ".join(parts)


def _run_board_thought(pr, ctx):
    store.thoughts_add(board="app lab", text=pr["text"], kind="приложение")
    return True, "мысль записана в ленту «Мысли плат»"


def _run_brain_think(pr, ctx):
    import brain
    long_mode = _bool(pr.get("long"))
    reply, _zone, _info = brain.think(pr["question"], long_mode,
                                      route="cloud")
    return True, reply


def _run_brain_article(pr, ctx):
    import brain
    return True, brain.write_article(pr["topic"])


def _run_brain_thought(pr, ctx):
    import brain
    return True, brain.free_think(pr["topic"])


def _run_brain_skill(pr, ctx):
    import skills
    hit = skills.match(pr["phrase"])
    if not hit:
        return False, ("навык под фразу «%s» не подобрался — попробуй "
                       "«посчитай…», «который час», «статус»"
                       % pr["phrase"][:60])
    r = skills.run_skill(hit[0], pr["phrase"])
    if r.get("ok"):
        return True, str(r.get("result") or "")
    return False, str(r.get("error") or "навык не сработал")


def _run_brain_func(pr, ctx):
    import skills
    r = skills.run_skill(pr["id"], pr["text"])
    if r.get("ok"):
        return True, str(r.get("result") or "")
    return False, str(r.get("error") or "функция не сработала")


_COMPOSE_HINTS = (
    (("плат", "устройств", "нейрон"), ("board.state", "board.say")),
    (("памят", "заметк", "запомни"), ("mem.list", "mem.recall")),
    (("знан", "стать", "kb", "учебник"), ("know.kb",)),
    (("вики", "поиск", "найди", "справк"), ("know.wiki",)),
    (("посчитай", "формул", "числ", "расчёт"), ("math.formula",)),
    (("подумай", "мысл", "рассужд", "идея"), ("brain.thought",)),
    (("письмо", "напиши", "сочинен", "текст по теме"), ("brain.writer",)),
)


def compose_app(topic):
    """v3.18: мозг собирает приложение БЕЗ готового рецепта: по словам
    темы выбирает подходящие кирпичики, склеивает их логическим шаблоном,
    проверяет пробным запуском и сохраняет (имя «Автоприложение: …»).
    Возврат (отчёт, ok|False)."""
    topic = str(topic or "").strip()[:160]
    if not topic:
        return False, "не задано, о чём собрать приложение"
    low = topic.lower()
    picks = []
    for keys, bricks in _COMPOSE_HINTS:
        if any(k in low for k in keys):
            picks.append(bricks[0])
            if len(bricks) > 1 and len(picks) < 3:
                picks.append(bricks[1])
    if not picks:
        picks = ["brain.think"]          # универсальный: подумать над темой
    picks = picks[:3]
    bricks = []
    for btype in picks:
        params = {}
        if btype in ("know.kb", "know.wiki", "mem.recall"):
            params = {"query": topic, "topic": topic}
        elif btype in ("math.formula", "brain.thought", "brain.think"):
            params = {"question": topic}
        elif btype == "brain.writer":
            params = {"topic": topic}
        bricks.append({"type": btype, "params": params, "var": ""})
    bricks.append({"type": "out.result", "params": {"text": "{last}"},
                   "var": ""})
    rr = run_app(bricks)                 # пробный запуск — честный тест
    if not rr.get("steps"):
        return False, "пробный запуск не дал шагов — приложение не собрано"
    bad = [st for st in rr["steps"]
           if not st.get("ok") and st.get("type") != "out.result"]
    name = "Автоприложение: %s" % topic[:50]
    saved, err = app_save("", name, bricks,
                          vars_note="собрано мозгом само, без рецепта")
    if err:
        return False, "приложение собрано, но не сохранилось: %s" % err
    kinds = ", ".join((_BRICK_BY_ID.get(b, {}).get("name") or b)
                      for b in picks)
    out = ("собрала приложение «%s» сама (без готового рецепта): %d "
           "кирпичиков — %s. Пробный запуск: %s" %
           (saved.get("name"), len(bricks), kinds,
            (str(rr.get("result") or "готово")[:220] or "готово")))
    if bad:
        out += (". Честно: %d кирпичик(ов) пробного запуска вернули "
                "пустоту (нет данных/плат) — само приложение сохранено."
                % len(bad))
    return True, out


def _run_brain_compose(pr, ctx):
    """САМОПРОГРАММИРОВАНИЕ: выбор рецепта из примеров кода и сборка
    приложения из кирпичиков без человека и без LLM. v3.18: если готового
    рецепта нет — мозг собирает приложение САМ по эвристикам (compose_app)."""
    topic = str(pr.get("question") or "").strip()
    if not topic:
        return False, "не задано, о чём собрать приложение"
    r = recipe_pick(topic)
    if not r:
        return compose_app(topic)
    app = app_from_recipe(r["id"], topic)
    saved, err = app_save("", r["name"], app["bricks"],
                          vars_note="собрано мозгом, рецепт %s" % r["id"])
    if err:
        return False, "рецепт «%s» есть, но не сохранилось: %s" % \
            (r["name"], err)
    out = "собрала приложение «%s» из %d кирпичиков (рецепт «%s»)" % \
        (saved.get("name"), len(app["bricks"]), r["name"])
    if _bool(pr.get("run")):
        rr = run_app(app["bricks"])
        res = str(rr.get("result") or "").strip()
        out += "; запуск: " + (res[:400] if res else
                               "готово, шагов %d" % len(rr.get("steps") or []))
    return True, out


def _run_brain_autocode(pr, ctx):
    """v3.18: мозг сам пишет себе функцию (заготовки + тесты + реестр)."""
    import autocode
    rep, ok = autocode.write_function(pr["topic"])
    return bool(ok), rep


def _run_brain_writer(pr, ctx):
    """v3.18: письмо по теме — инструмент самостоятельного письма."""
    import writer
    return True, writer.write_long(pr["topic"], min_chars=1500)


def _run_know_kb(pr, ctx):
    import kb
    r = kb.search(pr["query"], limit=1)
    hits = [h for h in (r.get("results") or []) if not h.get("wiki")]
    if not hits:
        return False, "в базе знаний ничего не нашлось по «%s»" % \
            pr["query"][:60]
    h = hits[0]
    body = str(h.get("text") or "").strip()
    return True, "«%s»\n%s" % (h.get("title"), body[:900])


def _run_know_wiki(pr, ctx):
    import webfetch
    if not store.settings().get("web_on"):
        return False, "интернет выключен — включи «web_on» в Общих"
    try:                          # v3.12.1: честная причина вместо сырой
        hits = webfetch.wiki_search(pr["topic"], limit=1)
    except Exception as e:
        return False, ("Википедия недоступна по «%s»: %s"
                       % (pr["topic"][:60], webfetch.honest(e)))
    if not hits:
        tail = "; ".join(webfetch.diag_tail()[:1])
        return False, ("Википедия не ответила по «%s»%s"
                       % (pr["topic"][:60], (" — " + tail) if tail else ""))
    return True, "%s\n%s\n%s" % (hits[0].get("title"),
                                 str(hits[0].get("extract") or "")[:900],
                                 hits[0].get("url") or "")


def _run_know_dict(pr, ctx):
    import brain
    r = brain.dict_lookup(pr["word"])
    if not r.get("found"):
        return False, "слова «%s» нет в словарях (%s)" % (
            pr["word"][:40], r.get("total", 0))
    gloss = str(r.get("pos") or "").strip()
    words = ", ".join((r.get("syn") or [])[:8]) or "—"
    return True, "«%s»%s: %s" % (r.get("word"),
                                 " (%s)" % gloss if gloss else "", words)


def _run_math_formula(pr, ctx):
    import mathmodels
    q = str(pr.get("question") or "").strip()
    if not q:
        return False, ("тема не задана — впиши вопрос, например: "
                       "«площадь круга радиусом 5»")
    out, ok = mathmodels.formula_solve(q)
    return ok, out


def _run_math_model(pr, ctx):
    import mathmodels
    data = str(pr.get("data") or "").strip()
    fc = str(pr.get("forecast") or "").strip()
    if not data:
        return False, ("данных нет — впиши пары x,y через точку с запятой, "
                       "например: «1,2; 2,4; 3,5»")
    pairs, err = mathmodels.parse_pairs(data)
    if err:
        return False, "модель не построилась: %s" % err
    mdl, err = mathmodels.fit_model(
        pairs, fc if re.match(r"^-?\d+(?:[.,]\d+)?$", fc) else None)
    if err:
        return False, "модель не построилась: %s" % err
    out = ("Модель %s: %s (R² = %s, точек: %d)" %
           (mdl["name"], mdl["formula"],
            mathmodels._num(mdl["r2"]), mdl["points"]))
    if mdl.get("forecast") is not None:
        out += ". Прогноз при x=%s: %s" % (
            mathmodels._num(float(mdl["forecast_x"])),
            mathmodels._num(mdl["forecast"]))
    if mdl.get("note"):
        out += ". %s" % mdl["note"]
    return True, out


def _run_mem_remember(pr, ctx):
    store.notes_add(pr["text"], board="app lab")
    return True, "запомнил: %s" % pr["text"][:120]


def _run_mem_recall(pr, ctx):
    topic = pr["topic"].lower().strip()
    found = [n for n in store.notes()
             if topic and (topic in str(n.get("text", "")).lower() or
                           any(w and w in str(n.get("text", "")).lower()
                               for w in topic.split()))]
    if not found:
        return False, "в памяти ничего про «%s»" % pr["topic"][:60]
    out = "\n".join("— %s" % n.get("text") for n in found[-5:])
    return True, out[:1800]


def _run_mem_list(pr, ctx):
    ns = store.notes()[-5:]
    if not ns:
        return False, "память пуста — кирпичик «Запомнить заметку» ждёт"
    return True, "\n".join("— %s" % n.get("text") for n in ns)[:1800]


def _run_logic_set(pr, ctx):
    ctx["vars"][pr["name"]] = pr["value"]
    return True, "%s = %s" % (pr["name"], pr["value"][:200])


def _run_logic_template(pr, ctx):
    return True, pr["template"]


def _run_logic_if(pr, ctx):
    where = pr.get("where") or "last"
    src = str(ctx["vars"].get("last", "")) if where == "last" \
        else str(ctx["vars"].get(where, ""))
    if pr["needle"].lower() in src.lower():
        return True, pr["then"]
    return True, pr["else"]


def _run_logic_wait(pr, ctx):
    try:
        sec = min(MAX_WAIT, abs(float(pr.get("sec") or 0)))
    except ValueError:
        sec = 0.0
    if sec > 0:
        time.sleep(sec)
    return True, "прошло %d сек" % int(sec)


def _run_out_result(pr, ctx):
    ctx["final"] = pr["text"]
    return True, pr["text"]


def _run_out_note(pr, ctx):
    store.notes_add(pr["text"], board="app lab")
    return True, "в память: %s" % pr["text"][:120]


_RUNNERS = {
    "board.ask": _run_board_ask,
    "board.say": _run_board_say,
    "board.state": _run_board_state,
    "board.thought": _run_board_thought,
    "brain.think": _run_brain_think,
    "brain.article": _run_brain_article,
    "brain.thought": _run_brain_thought,
    "brain.skill": _run_brain_skill,
    "brain.func": _run_brain_func,
    "brain.compose": _run_brain_compose,
    "brain.autocode": _run_brain_autocode,
    "brain.writer": _run_brain_writer,
    "know.kb": _run_know_kb,
    "know.wiki": _run_know_wiki,
    "know.dict": _run_know_dict,
    "math.formula": _run_math_formula,
    "math.model": _run_math_model,
    "mem.remember": _run_mem_remember,
    "mem.recall": _run_mem_recall,
    "mem.list": _run_mem_list,
    "logic.set": _run_logic_set,
    "logic.template": _run_logic_template,
    "logic.if": _run_logic_if,
    "logic.wait": _run_logic_wait,
    "out.result": _run_out_result,
    "out.note": _run_out_note,
}


# =============================== ЗАПУСК =====================================
def _param_defaults(btype, raw, vars_):
    """Собрать параметры кирпичика: значения из UI + шаблоны {…}."""
    brick = _BRICK_BY_ID.get(btype) or {}
    out = {}
    for p in brick.get("params") or []:
        v = raw.get(p["name"])
        if p["kind"] == "sel":
            out[p["name"]] = _bool(v)
        elif p["kind"] == "num":
            out[p["name"]] = str(v if v is not None else "")
        else:
            out[p["name"]] = _fmt(v, vars_)[:_TEXT]
    return out


def run_app(bricks):
    """Исполнить приложение из кирпичиков. Возврат:
    {ok, steps:[{n,type,name,cat,ok,out,ms}], result, ms}"""
    steps, vars_ = [], {"last": ""}
    ctx = {"vars": vars_, "final": None}
    t0 = time.time()
    if not isinstance(bricks, list) or not bricks:
        return {"ok": False, "steps": [], "result": "",
                "error": "приложение пустое — добавь кирпичики",
                "ms": 0}
    bricks = bricks[:MAX_BRICKS]
    stopped_at = -1                     # v3.7.1: индекс терминального out.result
    for i, b in enumerate(bricks):
        if time.time() - t0 > BUDGET:
            steps.append({"n": i + 1, "type": "?", "name": "стоп",
                          "cat": "out", "ok": False,
                          "out": "бюджет запуска исчерпан (%d с)" % BUDGET,
                          "ms": 0})
            break
        btype = str(b.get("type") or "").strip()
        brick = _BRICK_BY_ID.get(btype)
        if not brick:
            steps.append({"n": i + 1, "type": btype[:40],
                          "name": "неизвестный кирпичик", "cat": "out",
                          "ok": False, "out": "нет кирпичика «%s»" % btype,
                          "ms": 0})
            continue
        raw = b.get("params") if isinstance(b.get("params"), dict) else {}
        pr = _param_defaults(btype, raw or {}, vars_)
        fn = _RUNNERS.get(btype)
        ts = time.time()
        try:
            ok, out = fn(pr, ctx)
        except Exception as e:
            ok, out = False, "ошибка: %s: %s" % (type(e).__name__, e)
        ms = int((time.time() - ts) * 1000)
        out = str(out or "")
        steps.append({"n": i + 1, "type": btype, "name": brick["name"],
                      "cat": brick["cat"], "ok": bool(ok), "out": out[:_TEXT],
                      "ms": ms})
        if out:
            vars_["last"] = out[:_TEXT]
        var = str(b.get("var") or "").strip()[:24]
        if var and re.match(r"^[a-zA-Zа-яёА-ЯЁ0-9_ -]{1,24}$", var) and ok:
            vars_[var] = out[:_TEXT]
        if btype == "out.result":
            stopped_at = i
            break
    # v3.7.1: ЧЕСТНОСТЬ — «Показать результат» завершает приложение, но
    # если в стопке ПОСЛЕ него остались кирпичики, молча их терять нельзя:
    # сообщаем, сколько и что с ними делать.
    if stopped_at >= 0 and stopped_at < len(bricks) - 1:
        tail = bricks[stopped_at + 1:]
        names = ", ".join(
            str((_BRICK_BY_ID.get(str(x.get("type") or "").strip(), {})
                 .get("name") or "?"))[:40]
            for x in tail[:4])
        more = "…" if len(tail) > 4 else ""
        steps.append({"n": len(steps) + 1, "type": "out.result",
                      "name": "стоп-кирпичик", "cat": "out", "ok": True,
                      "out": ("«Показать результат» завершил приложение; "
                              "после него %d кирпичиков не выполнялись (%s%s) "
                              "— перенеси их ВЫШЕ «Показать результат» или "
                              "убери его" % (len(tail), names, more)),
                      "ms": 0})
    result = str(ctx.get("final") or vars_.get("last") or "")
    return {"ok": all(s.get("ok", False) for s in steps) if steps else False,
            "steps": steps, "result": result,
            "ms": int((time.time() - t0) * 1000)}


# ====================== РЕЦЕПТЫ (примеры кода) ==============================
# Примеры кода в базе данных: рецепт = готовая последовательность кирпичиков
# (операторов) с ключами-темами. По рецепту мозг (кирпичик brain.compose,
# зона «Кирпичики» платы, владелец в редакторе) САМ собирает приложение.
# {question} в параметрах заменяется на тему вопроса при сборке.
RECIPES = [
    {"id": "rcp_board_report", "name": "Отчёт по плате",
     "keys": ("плат", "отчёт", "состояни", "статус плат", "как плата"),
     "desc": "Состояние платы + её последняя мысль одним текстом.",
     "bricks": [
         {"type": "board.state", "params": {}, "var": "состояние"},
         {"type": "board.say", "params": {}, "var": "мысль"},
         {"type": "logic.template",
          "params": {"template": "Отчёт по плате\n{состояние}\n"
                                 "Мысль: {мысль}"}, "var": ""},
         {"type": "out.result", "params": {"text": "{last}"}, "var": ""},
     ]},
    {"id": "rcp_ask_remember", "name": "Спросить плату и запомнить",
     "keys": ("спроси плат", "спросить плат", "запомни ответ",
              "плата ответил", "расспроси"),
     "desc": "Плата отвечает сама (без LLM), ответ падает в память облака.",
     "bricks": [
         {"type": "board.ask", "params": {"question": "{question}"},
          "var": "ответ"},
         {"type": "mem.remember", "params": {"text": "Плата: {ответ}"},
          "var": ""},
         {"type": "out.result", "params": {"text": "{last}"}, "var": ""},
     ]},
    {"id": "rcp_kb_answer", "name": "Ответ из базы знаний",
     "keys": ("баз", "знан", "статья", "kb", "учебник", "документ"),
     "desc": "Найти статью в базе знаний и показать её выжимку.",
     "bricks": [
         {"type": "know.kb", "params": {"query": "{question}"}, "var": ""},
         {"type": "out.result", "params": {"text": "{last}"}, "var": ""},
     ]},
    {"id": "rcp_wiki", "name": "Справка из Википедии",
     "keys": ("википед", "вики", "энциклопед", "справк", "кто такой",
              "что такое"),
     "desc": "Честная выжимка из Википедии по теме.",
     "bricks": [
         {"type": "know.wiki", "params": {"topic": "{question}"}, "var": ""},
         {"type": "out.result", "params": {"text": "{last}"}, "var": ""},
     ]},
    {"id": "rcp_digest", "name": "Утренний дайджест",
     "keys": ("дайджест", "утро", "сводк", "доброе утро", "как дела в целом"),
     "desc": "Состояние платы + мысли облака — сводка на старте дня.",
     "bricks": [
         {"type": "board.state", "params": {}, "var": "состояние"},
         {"type": "brain.think", "params": {"question": "что нового?",
                                            "long": "да"}, "var": "мысли"},
         {"type": "logic.template",
          "params": {"template": "Состояние: {состояние}\n"
                                 "Мысли облака: {мысли}"}, "var": ""},
         {"type": "out.result", "params": {"text": "{last}"}, "var": ""},
     ]},
    {"id": "rcp_think_note", "name": "Подумать и записать",
     "keys": ("подумай", "мысл", "идея", "размышля", "порассужда"),
     "desc": "Мозг размышляет, мысль уходит в память и в ленту плат.",
     "bricks": [
         {"type": "brain.thought", "params": {"topic": "{question}"},
          "var": "размышление"},
         {"type": "mem.remember",
          "params": {"text": "Мысль: {размышление}"}, "var": ""},
         {"type": "board.thought", "params": {"text": "{размышление}"},
          "var": ""},
         {"type": "out.result", "params": {"text": "{last}"}, "var": ""},
     ]},
    {"id": "rcp_memory_quiz", "name": "Что помню про…",
     "keys": ("что помнишь", "помнишь", "вспомни", "памят", "найди в памяти"),
     "desc": "Достать из облачной памяти заметки по теме.",
     "bricks": [
         {"type": "mem.recall", "params": {"topic": "{question}"},
          "var": "заметки"},
         {"type": "logic.template",
          "params": {"template": "Из памяти:\n{заметки}"}, "var": ""},
         {"type": "out.result", "params": {"text": "{last}"}, "var": ""},
     ]},
    {"id": "rcp_math_formula", "name": "Расчёт по формуле",
     "keys": ("формул", "посчитай по формуле", "вычисли", "площад",
              "скорост", "процент", "расчёт"),
     "desc": "Мозг сам выбирает формулу из базы и считает по числам "
             "из вопроса.",
     "bricks": [
         {"type": "math.formula", "params": {"question": "{question}"},
          "var": "расчёт"},
         {"type": "out.result", "params": {"text": "{last}"}, "var": ""},
     ]},
    {"id": "rcp_math_model", "name": "Модель и прогноз",
     "keys": ("модель", "прогноз", "тренд", "по данным", "регресс"),
     "desc": "Строит математическую модель по парам чисел и делает "
             "прогноз (выбор модели по R²).",
     "bricks": [
         {"type": "math.model",
          "params": {"data": "1,2; 2,4; 3,5; 4,9", "forecast": "5"},
          "var": "модель"},
         {"type": "out.result", "params": {"text": "{last}"}, "var": ""},
     ]},
]


def recipes_list():
    """Каталог рецептов для панели и платы (примеры кода)."""
    return [{"id": r["id"], "name": r["name"], "desc": r["desc"],
             "keys": list(r["keys"]), "count": len(r["bricks"])}
            for r in RECIPES]


def recipe_pick(topic):
    """Выбор рецепта под вопрос: по числу совпавших ключей-тем."""
    low = (topic or "").lower()
    best, hits = None, 0
    for r in RECIPES:
        n = sum(1 for k in r["keys"] if k in low)
        if n > hits:
            best, hits = r, n
    return best


def app_from_recipe(rid, topic=""):
    """Собрать приложение из рецепта: копия кирпичиков, {question} ->
    тема вопроса. Возврат {id:"", name, bricks} | None."""
    for r in RECIPES:
        if r["id"] == rid:
            bricks = []
            for b in r["bricks"]:
                params = {}
                for k, v in (b.get("params") or {}).items():
                    params[k] = str(v).replace("{question}",
                                               topic or "")[:_TEXT]
                bricks.append({"type": b["type"], "params": params,
                               "var": b.get("var", "")})
            return {"id": "", "name": r["name"], "bricks": bricks}
    return None


# ============================ ПРИЛОЖЕНИЯ ====================================
def _load_db():
    d = store.load_json("applab.json", {})
    return d if isinstance(d, dict) else {}


def _save_db(d):
    store.save_json("applab.json", d)


def apps_list():
    d = _load_db()
    out = []
    for a in d.values():
        if not isinstance(a, dict):
            continue          # v3.8.1: битая запись не роняет весь App Lab
        out.append({"id": str(a.get("id") or ""),
                    "name": a.get("name") or "приложение",
                    "count": len(a.get("bricks") or []),
                    "runs": int(a.get("runs") or 0),
                    "ts": float(a.get("ts") or 0)})
    out.sort(key=lambda x: -x["ts"])
    return out


def app_get(aid):
    return _load_db().get(str(aid)[:40])


def app_save(aid, name, bricks, vars_note=""):
    """Сохранить приложение (id пустой — создать). Кирпичики приводим к
    безопасному виду: только известные типы, строки-параметры обрезаны."""
    d = _load_db()
    aid = str(aid or "").strip()[:40]
    if not aid:
        aid = "a" + os_hex(5)
    clean = []
    for b in (bricks or [])[:MAX_BRICKS]:
        if not isinstance(b, dict):
            continue
        btype = str(b.get("type") or "").strip()
        if btype not in _BRICK_BY_ID:
            continue
        params = {}
        raw = b.get("params") if isinstance(b.get("params"), dict) else {}
        for p in (_BRICK_BY_ID[btype].get("params") or []):
            v = raw.get(p["name"])
            params[p["name"]] = str(v if v is not None else "")[:_TEXT]
        clean.append({"type": btype, "params": params,
                      "var": str(b.get("var") or "").strip()[:24]})
    if not clean:
        return None, "в приложении нет ни одного кирпичика"
    prev = d.get(aid) or {}
    d[aid] = {"id": aid, "name": str(name or "приложение")[:80],
              "bricks": clean, "runs": int(prev.get("runs") or 0),
              "ts": time.time()}
    _save_db(d)
    return d[aid], ""


def app_delete(aid):
    d = _load_db()
    aid = str(aid)[:40]
    if aid in d:
        d.pop(aid)
        _save_db(d)
        return True
    return False


def app_ran(aid):
    d = _load_db()
    if aid in d:
        d[aid]["runs"] = int(d[aid].get("runs") or 0) + 1
        d[aid]["ts"] = time.time()
        _save_db(d)


def os_hex(n):
    import os as _os
    return _os.urandom(n).hex()


# ============================== ПРИМЕРЫ =====================================
EXAMPLES = [
    {"id": "ex_diary", "name": "Пример: дневник платы",
     "hint": "Состояние и мысль платы — одним текстом",
     "bricks": [
         {"type": "board.state", "params": {}, "var": "состояние"},
         {"type": "board.say", "params": {}, "var": ""},
         {"type": "logic.template",
          "params": {"template": "Отчёт по плате\n{состояние}\nМысль: {last}"},
          "var": ""},
         {"type": "out.result", "params": {"text": ""}, "var": ""},
     ]},
    {"id": "ex_ask", "name": "Пример: спросить и запомнить",
     "hint": "Плата отвечает сама, ответ падает в облачную память",
     "bricks": [
         {"type": "board.ask",
          "params": {"question": "что ты помнишь?"}, "var": ""},
         {"type": "mem.remember",
          "params": {"text": "Ответ платы: {last}"}, "var": ""},
         {"type": "out.result", "params": {"text": "{last}"}, "var": ""},
     ]},
    {"id": "ex_morning", "name": "Пример: утренний дайджест",
     "hint": "Мозг облака + плата + память — сводка на старте дня",
     "bricks": [
         {"type": "brain.skill", "params": {"phrase": "статус"}, "var": ""},
         {"type": "board.say", "params": {}, "var": "мысль"},
         {"type": "mem.list", "params": {}, "var": ""},
         {"type": "logic.template",
          "params": {"template": "Сводка облака:\n{last}\n\nПлата сейчас "
                                "думает: {мысль}"}, "var": ""},
         {"type": "out.result", "params": {"text": ""}, "var": ""},
     ]},
    {"id": "ex_kb", "name": "Пример: радио базы знаний",
     "hint": "Статья из KB + заметка в память",
     "bricks": [
         {"type": "know.kb", "params": {"query": "Star Gate"}, "var": ""},
         {"type": "out.result", "params": {"text": ""}, "var": ""},
     ]},
]


def example(aid):
    for e in EXAMPLES:
        if e["id"] == aid:
            return app_save("", e["name"].replace("Пример: ", ""),
                            e["bricks"])
    return None, "пример не найден"
