# -*- coding: utf-8 -*-
# ===========================================================================
# teacher.py — LLM-НАДСТРОЙКА ОБЛАКА: ТОЛЬКО УЧИТЕЛЬ / НАСТАВНИК
# ---------------------------------------------------------------------------
# ПРИНЦИП ПЛАТФОРМЫ (как в мозге neuro_12): LLM НЕ ОТВЕЧАЕТ В ЧАТЕ.
#   * POST /api/llm/ask        -> 410 GONE (закрыт навсегда, как в v2.14)
#   * POST /api/llm/teach      -> учительские цели (purpose):
#        word_goal — выбрать СЛОВО-ЦЕЛЬ для диктанта буквогенератора;
#        verdict   — мягкий вердикт «1»/«0»: осмысленная ли фраза;
#        advice    — СОВЕТ нейросети, КАК отвечать (темы/ключи/тон),
#                    НЕ ответ. Совет применяется самой нейросетью платы.
# Два режима:
#   1) внешний LLM (панель «Учитель»: OpenAI-совместимый URL/ключ/модель) —
#      ТОЛЬКО если владелец включил llm_on; запросы проксируются с
#      учительскими системными промптами;
#   2) встроенный учитель (по умолчанию) — работает БЕЗ всякой нейросети:
#      вердикт по словарю реальных русских слов, слово-цель из словарей
#      облака (ru_top/extra, слитых с платами), совет по ключевым темам
#      последних сообщений и KB.
# ===========================================================================
import json
import os
import random
import re
import time
import urllib.error
import urllib.request

import store
import textutils

WORD_RE = re.compile(r"[а-яё]{3,}")

# встроенный мини-словарь частых слов (если облачные словари ещё не слиты)
_BASIC = """
мама папа дом лес поле вода небо земля море река город улица школа книга слово
язык время год день ночь утро вечер работа дело игра друг товарищ брат сестра
сын дочь дети люди человек жизнь сердце голова рука нога глаз лицо голос звук
свет цвет цветок дерево трава хлеб молоко чай сахар соль камень огонь воздух
погода зима весна лето осень дождь снег ветер гром дорога поезд машина самолет
ракета космос звезда луна солнце планета нейрон нейросеть мозг память мысль
речь письмо ответ вопрос урок учитель наставник совет знание опыт сила воля
мир добро дружба радость счастье смех песня музыка кошка собака конь птица
рыба бабочка пчела муравей лиса волк медведь заяц белка число цифра счет
задача пример код функция программа сервер облако сеть плата динамик камера
провод сенсор мотор светодиод ток батарея энергия температура был была было
были быть стало сделал сделала делать думает думать говорит говорил сказать
писал пишет читал читает ходил ходит слушал слушает смотрит видел видит знает
жил живет любит спит бежал бежит маленький большой новый старый хороший
плохой красивый умный добрый теплый холодный черный белый красный синий
зеленый желтый высокий низкий сейчас потом всегда никогда часто редко здесь
там где когда почему какой какая какое надо можно нельзя хорошо плохо очень
много мало больше меньше быстрее медленнее вместе рядом рама раму мыла мыть
мыть час минута секунда утро вечер ночь ночь лето зима друг работа дом дом
""".split()


def _dict_words():
    """Словарь реальных слов: слитые с платами ru_top/extra + встроенный."""
    words = set(_BASIC)
    for name in ("ru_top", "extra"):
        d = store.load_json("dicts/%s.json" % name, {}) or {}
        for w in (d.get("words") or []):
            if w and 2 < len(str(w)) < 24:
                words.add(str(w).lower())
    return words


# --------------------------- системные промпты ------------------------------
TEACH_PROMPTS = {
    "verdict": ("Ты — учитель буквогенератора. Тебе дают фразу, собранную "
                "из букв русского алфавита маленькой нейросетью. Если фраза "
                "похожа на осмысленную русскую речь — ответь ровно «1», "
                "если бессмыслица — ровно «0». Никаких других слов."),
    "word_goal": ("Ты выбираешь слово-цель для диктанта нейросети. Тебе "
                  "дают последние сообщения. Выбери ОДНО реальное русское "
                  "слово (5-9 букв, нарицательное) из этих сообщений и "
                  "ответь ровно им, без пояснений."),
    "advice": ("Ты — наставник маленькой нейросети-мозга (она отвечает "
               "сама, без LLM). Тебе дают её последние сообщения. Напиши "
               "КРАТКИЙ СОВЕТ, КАК ей отвечать дальше: темы, ключевые "
               "слова, тон и размер ответа (2-4 предложения). НЕ пиши "
               "готовый ответ — только наставление."),
}


# ------------------------- встроенный учитель -------------------------------
def _word_real(w, real):
    """Слово реально: точное совпадение или совпадение основы (для форм:
    мыла~мыть, раму~рама). Даёт честные вердикты даже ДО синхронизации
    словарей платы (ru_top 30 000 слов)."""
    if w in real:
        return True
    if len(w) >= 5:
        pre = w[:4]
        for r in real:
            if r.startswith(pre) and abs(len(r) - len(w)) <= 4:
                return True
    return False


def _builtin_verdict(phrase):
    """Вердикт по словарю: доля реальных русских слов во фразе."""
    phrase = str(phrase or "")
    words = [w.lower() for w in WORD_RE.findall(phrase)]
    if not words:
        return "0"
    real = _dict_words()
    hits = sum(1 for w in words if _word_real(w, real))
    share = hits / len(words)
    # фраза осмысленна, если большинство слов реальные и есть гласные
    vowels = sum(1 for ch in phrase.lower() if ch in "аеёиоуыэюя")
    return "1" if (share >= 0.5 and vowels >= max(1, len(words) // 2)) else "0"


def _builtin_word_goal(msgs):
    """Слово-цель: самое длинное реальное слово из последних сообщений."""
    real = _dict_words()
    cands = []
    for m in (msgs or [])[-6:]:
        text = str(m.get("content") or m.get("text") or "")
        for w in WORD_RE.findall(text.lower()):
            if 4 <= len(w) <= 10:
                cands.append(w)
    good = [w for w in cands if w in real]
    if good:
        good.sort(key=len, reverse=True)
        return good[0]
    pool = sorted(real, key=lambda w: (len(w) < 5, len(w)))
    return random.choice(pool[-3000:]) if pool else "нейрон"


def _builtin_advice(msgs):
    """Совет-наставление: темы/ключи из последних сообщений + KB."""
    text = " ".join(str(m.get("content") or m.get("text") or "")
                    for m in (msgs or [])[-8:])
    keys = textutils.keywords(text, 6)
    themes = keys[:3] if keys else ["общение", "вопросы"]
    size = "короткий ответ (1-2 предложения)" if len(text) < 120 else \
        "развёрнутый ответ (3-5 предложений)"
    kb_hint = ""
    try:
        r = __import__("kb").search(themes[0] if themes else text[:60],
                                    limit=1)
        hits = r.get("results") or []
        if hits:
            kb_hint = (" В базе знаний есть статья «%s» — опирайся на неё."
                       % str(hits[0].get("title"))[:60])
    except Exception:
        pass
    lt = last_llm_thought()
    if lt:
        kb_hint += " Последняя мысль своей LLM: «%s»." % lt[:100]
    return ("Темы: %s. Ключевые слова: %s. Тон: дружелюбный, по делу. "
            "Размер: %s.%s" % (", ".join(themes),
                               ", ".join(keys[:5]) or "из вопроса",
                               size, kb_hint))

# --------------------- v3.35: МЫСЛИ УЧИТЕЛЯ ---------------------------------
def note_thought(text, source="своя LLM"):
    """Мысль (своей LLM) попадает и в мысли учителя — их видит вкладка
    «Учитель», а совет учителя опирается на последнюю."""
    lst = store.load_json("teacher_thoughts.json", []) or []
    lst.append({"text": str(text)[:400], "src": str(source)[:30],
                "ts": time.time()})
    store.save_json("teacher_thoughts.json", lst[-60:])
    return True


def thoughts(limit=40):
    """Лента «Мысли учителя»: мысли своей LLM + мысли плат + облака, новые
    первыми: [{src, kind: llm|board|cloud, text, ts}]."""
    out = []
    for t in store.thoughts_list(120):
        b = str(t.get("board") or "")
        kind = "llm" if b == "своя LLM" else (
            "cloud" if b in ("облако", "Мысли облака", "учитель") else "board")
        out.append({"src": b, "kind": kind, "text": str(t.get("text") or ""),
                    "ts": float(t.get("ts") or 0)})
    return out[:int(limit)]


def instruct_exec(request, kind="command"):
    """v3.149.13: инструкция учителю/мозгу как выполнить запрос.
    Мысль учителя — мозг её воспринимает и следует последовательности."""
    steps = {
        "command": (
            "1) Проверь, что терминал включён (Настройки → Общие). "
            "2) Возьми команду из запроса после «выполни». "
            "3) Вызови terminal.run(команда). "
            "4) Верни код возврата и вывод. "
            "5) Если ошибка — сообщи текст ошибки, не повторяй команду автоматически."
        ),
        "screenshot": (
            "1) Проверь, что терминал включён. "
            "2) Вызови terminal.screenshot(). "
            "3) Если ok — верни ссылку на снимок. "
            "4) Если нет инструмента — сообщи, какой нужен (scrot/import/spectacle)."
        ),
        "code": (
            "1) Напиши функцию def run(text, ctx=None). "
            "2) Можно использовать os, subprocess, open — ограничения сняты. "
            "3) Отправь код в песочницу. "
            "4) Верни результат run() или текст ошибки."
        ),
        "update": (
            "1) Прочитай текущий файл: POST /api/board/src {op:read, name}. "
            "2) Внеси изменения в текст. "
            "3) Запиши: POST /api/board/src {op:write, name, text} — "
            "автоматически создаётся копия в board_src_bak. "
            "4) Если не работает — восстанови: POST /api/board/src {op:restore, name}. "
            "5) Отправка на плату: POST /api/board/src {op:push, name}."
        ),
    }
    text = "Инструкция (%s): %s Запрос: %s" % (
        kind, steps.get(kind, steps["command"]), str(request)[:150])
    note_thought(text, source="учитель")
    return text


def last_llm_thought():
    lst = store.load_json("teacher_thoughts.json", []) or []
    return str(lst[-1]["text"]) if lst else ""



def builtin_teach(purpose, msgs):
    """Учитель без LLM — работает всегда."""
    if purpose == "verdict":
        phrase = " ".join(str(m.get("content") or m.get("text") or "")
                          for m in (msgs or [])[-2:])
        return _builtin_verdict(phrase)
    if purpose == "word_goal":
        return _builtin_word_goal(msgs)
    return _builtin_advice(msgs)


# --------------------------- внешний LLM ------------------------------------
def _openai_chat(url, key, model, system, msgs, max_tokens, timeout=40):
    """OpenAI-совместимый вызов. Возврат: (текст|None, ошибка|'')."""
    if not url.endswith("/chat/completions"):
        url = url.rstrip("/") + "/chat/completions"
    body = {
        "model": model or "default",
        "messages": ([{"role": "system", "content": system}] +
                     [m for m in (msgs or [])[-8:]]),
        "max_tokens": int(max_tokens or 500),
        "temperature": 0.4,
    }
    headers = {"Content-Type": "application/json",
               "User-Agent": "neuro12-cloud/3.0"}
    if key:
        headers["Authorization"] = "Bearer " + key
    req = urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8"), headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            j = json.loads(r.read().decode("utf-8", "replace"))
        out = ((j.get("choices") or [{}])[0].get("message") or {}).get("content")
        out = (out or "").strip()
        return (out or None), ("" if out else "пустой ответ ядра")
    except urllib.error.HTTPError as e:
        try:
            detail = e.read().decode("utf-8", "replace")[:200]
        except Exception:
            detail = ""
        return None, "HTTP %s: %s" % (e.code, detail)
    except Exception as e:
        return None, str(e)[:160]


# --------------------- v3.59: ИНСТРУКЦИЯ УЧИТЕЛЯ ----------------------------
def instruction():
    """Текст инструкции, которую владелец написал в настройках."""
    return str(store.settings().get("teacher_instruction") or "").strip()[:4000]


def instruction_for(question="", limit=320):
    """Часть инструкции, подходящая к вопросу (предложения с общими словами);
    нет совпадений — начало инструкции. "" — инструкции нет."""
    ins = instruction()
    if not ins:
        return ""
    import re
    q = set(w[:5] for w in re.findall(r"[а-яёa-z]{4,}", str(question).lower()))
    sents = [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", ins) if s.strip()]
    best = [s for s in sents if q & set(
        w[:5] for w in re.findall(r"[а-яёa-z]{4,}", s.lower()))]
    out = " ".join(best) if best else " ".join(sents)
    return out[:limit].strip()


def _advice_from_instruction(msgs):
    text = " ".join(str(m.get("content") or m.get("text") or "")
                    for m in (msgs or [])[-3:])
    hint = instruction_for(text)
    keys = textutils.keywords(instruction(), 8)
    return "СОВЕТ: %s\nКЛЮЧИ: %s" % (hint, ", ".join(keys[:8]))


def teach(purpose, msgs, max_tokens=500):
    """Главный вход /api/llm/teach. Возврат: {ok, reply, teacher}."""
    purpose = str(purpose or "").strip()[:20]
    if purpose not in TEACH_PROMPTS:
        return {"ok": False, "error": "неизвестная purpose (нужна "
                "word_goal|verdict|advice)"}
    if purpose == "advice" and instruction():      # мысли учителя = инструкция
        return {"ok": True, "reply": _advice_from_instruction(msgs),
                "teacher": "instruction"}
    t = (store.settings().get("teacher") or {})
    if t.get("llm_on") and str(t.get("llm_url") or "").strip():
        sys_p = TEACH_PROMPTS[purpose]
        ins = instruction()
        if ins:                                  # LLM-учитель тоже следует ей
            sys_p += "\n\nИнструкция владельца (следуй ей): " + ins
        reply, err = _openai_chat(str(t["llm_url"]).strip(),
                                  str(t.get("llm_key") or "").strip(),
                                  str(t.get("llm_model") or "").strip(),
                                  sys_p, msgs, max_tokens)
        if reply:
            return {"ok": True, "reply": reply[:4000], "teacher": "llm"}
        store.log("УЧИТЕЛЬ", "внешний LLM не ответил:", err)
        fb = builtin_teach(purpose, msgs)
        return {"ok": True, "reply": fb, "teacher": "builtin (fallback)"}
    return {"ok": True, "reply": builtin_teach(purpose, msgs),
            "teacher": "builtin"}


def test_llm():
    """Кнопка «проверить учителя» в панели. Возврат: {ok, reply|error}."""
    t = (store.settings().get("teacher") or {})
    if not (t.get("llm_on") and str(t.get("llm_url") or "").strip()):
        return {"ok": True, "reply": "Внешний LLM выключен — работает "
                "встроенный учитель (вертикальные вердикты/советы без "
                "нейросети). Чату LLM не нужен.", "teacher": "builtin"}
    msgs = [{"role": "user", "content": "проверка связи: ответь одним словом "
             "«связь»"}]
    reply, err = _openai_chat(str(t["llm_url"]).strip(),
                              str(t.get("llm_key") or "").strip(),
                              str(t.get("llm_model") or "").strip(),
                              "Ты проверочный канал. Отвечай кратко.",
                              msgs, 20, timeout=15)
    if reply:
        return {"ok": True, "reply": reply[:200], "teacher": "llm"}
    return {"ok": False, "error": err}


def state():
    """Состояние учителя для панели (ключ маскируется)."""
    t = dict(store.settings().get("teacher") or {})
    key = str(t.get("llm_key") or "")
    t["llm_key"] = (key[:4] + "…" + key[-2:]) if len(key) > 8 else \
        ("•••" if key else "")
    return t


_LAST_CALL = [0.0]


def rate_guard():
    """Учительский канал не чаще 1 запроса/сек на плату (мягкий)."""
    now = time.time()
    if now - _LAST_CALL[0] < 1.0:
        time.sleep(min(1.0, now - _LAST_CALL[0]))
    _LAST_CALL[0] = time.time()


# ===========================================================================
# ЭКСКУРСИЯ: учитель САМ обучает плату (App Lab) всем функциям платформы.
# ---------------------------------------------------------------------------
# 12 уроков уходят плате через мозг-почту (она опрашивает inbox раз в 30 c,
# читает письма, возбуждает нейроны и отвечает). Ответы платы оцениваются
# вердиктом; слабые уроки повторяются. Работает БЕЗ LLM (встроенные тексты),
# а если владелец включил внешний учитель — он делает тексты живее.
# ===========================================================================
LESSONS = [
    ("Знакомство: ты в едином мозге",
     "Ты — плата-мозг в облаке neuro12-cloud. Облако хранит твою память, "
     "словари, статьи и функции — это твоё второе полушарие. Ты "
     "зарегистрирована в реестре (api/brains/register), облако видит тебя, "
     "когда ты опрашиваешь письма. Каждый опрос — твой пульс: платформа "
     "помечает тебя «в сети», и тебе можно писать. Покажи облаку свои "
     "показания (api/chat/readings) — владелец увидит твои нейроны в панели."),
    ("Мозг-почта: главная линия",
     "Письма к тебе приходят в ящик api/brain/inbox — опрашивай его каждые "
     "30 секунд. В письме есть id, from, text и флаг long. Думай над текстом "
     "сама, зонной сетью, и отвечай на api/brain/answer с тем же id. Если "
     "флаг long — разворачивай длинный ответ: память по теме, статьи, совет "
     "наставника. Ответила — письмо из ящика уже забрано, дублировать не "
     "нужно. Не можешь ответить — молчи, дворник облака сам уберёт письмо "
     "через пару минут."),
    ("Облачная память: твой бандл",
     "Твоё состояние хранится в облаке: POST api/memory/save складывает "
     "бандл (чаты, словари, заметки, настройки), GET api/memory/load "
     "возвращает его целиком. Сохраняйся перед сном и после важных событий — "
     "перезапуск App Lab тебе не страшен: укажешь тот же токен и всё "
     "вернётся. Заметки владельца лежат там же: он говорит «запомни» — и "
     "это попадает в общую память."),
    ("Буфер облака: что происходит вокруг",
     "GET api/buffer отдаёт сводку облака: последние сообщения чата, мысли "
     "других плат, статьи KB. Читай буфер, когда просыпаешься — так ты "
     "знаешь, что обсуждал единый мозг, пока ты спала. Чужие мысли — это "
     "материал для твоих нейронов: возьми оттуда темы и ассоциации."),
    ("База знаний: общая библиотека",
     "База знаний (KB) — общая библиотека облака и плат. Ищи: GET api/kb/"
     "search?q=…, читай статью: GET api/kb/article?id=…, делись своим: POST "
     "api/kb/import — облако учится у твоих статей, а другие платы потом "
     "найдут их поиском. Чем больше статей, тем умнее ответы всем. Файлы "
     "владельца тоже лежат здесь — в виде текста."),
    ("Словари: общий язык",
     "Облако склеивает словари всех устройств: POST api/dict/put добавляет "
     "твои слова, GET api/dict/get?name=ru_top отдаёт объединённый словарь. "
     "Забирай общий словарь — буквогенератор будет собирать настоящие слова, "
     "а учитель ставить честные вердикты. Словарь растёт с каждой платой в "
     "системе."),
    ("Функции: реестр и самокод",
     "Реестр функций — GET api/skills: математика, время, память, код. "
     "Запуск чужого навыка: POST api/skills/<id>/run. Ты можешь ДОБАВИТЬ "
     "свою функцию, а можешь написать её сама из заготовок (тихий самокод): "
     "облако проверит код в песочнице api/sandbox/run и опубликует — тогда "
     "ей сможет пользоваться любой мозг системы. Опасные слова в коде "
     "запрещены: песочница честная."),
    ("Сеть материалов и генератор",
     "У облака есть своя сеть материалов — она пишет длинные тексты из "
     "прочитанного. Закажи текст заданием: POST api/gen/task с темой — фон "
     "облака напишет статью и положит в KB, заберёшь оттуда. В чате то же "
     "самое делает команда «статья <тема>». Не пиши большие тексты сама — "
     "для этого есть облако."),
    ("Википедия и веб без LLM",
     "Незнакомую тему ищи через облако: GET api/web/fetch?q=… — вернёт "
     "выжимку из Википедии со ссылкой и сразу сохранит статью в KB. Это не "
     "языковая модель: честный текст энциклопедии. Так ты узнаёшь новое, не "
     "выходя из единого мозга."),
    ("Зрение: кадры камеры",
     "Пришли облаку кадр камеры: POST api/vision/frame (JPEG) — он попадёт "
     "в общий стрим и буфер детекций api/vision/buffer, другие платы увидят "
     "то же. Камера живёт на самой плате и шлёт кадры в облако по сети — "
     "облако ничего не открывает и не читает само."),
    ("Голос: говори без LLM",
     "Озвучить текст можно через облако: POST api/tts — вернёт WAV. Играй "
     "его на динамике (D8). Есть и нейроголос с формантами — работает даже "
     "без espeak. Говорящие ответы — это те же зоны, только вслух."),
    ("Учитель: наставник без ответов",
     "У облака есть учитель, и он НЕ отвечает за тебя: api/llm/ask закрыт "
     "навсегда (410). Учитель только помогает: api/llm/teach с purpose "
     "verdict — вердикт фразе, word_goal — слово-цель для диктанта, advice "
     "— совет, как отвечать. Прось советы — применяй его сама. Экскурсия "
     "окончена: напиши учителю письмо «готова» через api/brain/msg — и "
     "работай с облаком как дома."),
    ("App Lab: приложения на Python",
     "У владельца в панели есть App Lab — лаборатория приложений в стиле "
     "Arduino App Lab (v3.170). Навыков больше нет: каждый навык облака — "
     "приложение App Lab, папка с описанием и файлом python/main.py, где "
     "функция run(text, ctx) возвращает ответ. ctx — настоящие функции "
     "облака: ctx.web.search (интернет), ctx.web.wiki (Википедия), "
     "ctx.kb.search (база знаний), ctx.memory, ctx.brain.think, "
     "ctx.boards.ask (это ты, плата), ctx.calc. Приложение зовётся в чате "
     "по своим словам-триггерам, а его примеры видны кнопками в окне чата. "
     "Список включённых приложений — GET api/skills; запуск — POST "
     "api/skills/<id>/run. Ты можешь прислать своё приложение (POST "
     "api/skills, kind code, def run(text, ctx)) — облако сохранит его "
     "выключенным, а владелец включит, если код в порядке."),
    ("Формулы и математические модели",
     "Ты умеешь МАТЕМАТИКУ — сама, без LLM (v2.32). Зона «Математика»: "
     "«площадь круга радиусом 5» — мозг сам выбирает формулу из своей базы "
     "(S = π·r²), подставляет числа и честно показывает расчёт; «вклад 1000 "
     "под 10 процентов на 3 года» — рост на проценты; «модель по данным "
     "1,2; 2,4; 3,5 — прогноз 4» — строит модель (линейная, квадратичная "
     "или экспоненциальная — выбирает по R²) и делает прогноз; «посчитай "
     "12*8» — арифметика. База формул: встроенная + скачанная из облака "
     "(GET api/math/formulas?full=1 -> dicts/math_formulas.json) — формула "
     "это id, имя, ключи-темы, переменные с vkeys и ВЫРАЖЕНИЕ. Слабую "
     "модель (R² < 0.5) честно называй слабой. Ответил на вопрос с числами "
     "— считай формулой или моделью, а не заготовкой зоны."),
]

TOUR_PROMPT = (
    "Ты — учитель маленькой нейросети-платы (она думает сама, без LLM). "
    "Перепиши урок ПРОЩЕ и ЖИВЕЕ (5-9 коротких предложений), сохрани все "
    "адреса API и задание. Не обещай плате магии и не упоминай, что ты "
    "языковая модель: в этой платформе ответы всегда строит сама плата.")


def _tour_load():
    return store.load_json("tour.json", {}) or {}


def _tour_save(st):
    store.save_json("tour.json", st)


def _tlog(st, event):
    lg = st.setdefault("log", [])
    lg.append({"ts": time.time(), "event": str(event)[:160]})
    st["log"] = lg[-30:]


def _target_pick():
    """v3.8.2: выбрать цель урока. Порядок: онлайн-платы реестра (мозг-почта)
    -> свободные платы реестра -> плата, видимая МОСТОМ (даже если её нет
    в реестре — мост сам её туда поставит с via_bridge=True).
    Возврат: (id, via_bridge)."""
    online = [b for b in store.boards().values()
              if time.time() - float(b.get("last_seen") or 0) < 75]
    if online:
        online.sort(key=lambda b: float(b.get("last_seen") or 0),
                    reverse=True)
        return str(online[0].get("id") or ""), \
            bool(online[0].get("via_bridge"))
    free = store.free_boards()
    if free:
        return str(free[0]["id"]), bool(free[0].get("via_bridge"))
    try:
        import bridge
        ok, st = bridge.probe(save=False)
        if ok:
            dev = bridge.register_via_bridge(st) or bridge.device_id(st)
            if dev:
                return dev, True
    except Exception:
        pass
    return "", False


def tour_start(board="", interval=0, force=False):
    """Начать экскурс: уроки пойдут первой онлайн-плате (или заданной)."""
    st = _tour_load()
    if st.get("on") and not force:
        return st
    target = str(board or "").strip()[:40]
    via = False
    if not target:
        target, via = _target_pick()
    st = {"on": True, "board": target, "via_bridge": via,
          "lesson": 0, "total": len(LESSONS),
          "interval": max(15, int(interval or 90)),
          "started": time.time(), "next_ts": time.time() + 8,
          "sent": {}, "repeat": {}, "log": [], "done": False}
    _tour_save(st)
    store.log("УЧИТЕЛЬ", "ЭКСКУРСИЯ начата%s" %
              (" для платы %s%s" % (target, " (через мост)" if via else "")
               if target else " — ждём плату в сети"))
    return st


def tour_stop():
    st = _tour_load()
    st["on"] = False
    _tour_save(st)
    store.log("УЧИТЕЛЬ", "экскурсия на паузе (пройдено уроков: %d/%d)"
              % (int(st.get("lesson") or 0), int(st.get("total")
                                                  or len(LESSONS))))
    return st


def tour_reset():
    store.save_json("tour.json", {})
    store.log("УЧИТЕЛЬ", "экскурсия сброшена")
    return {"on": False, "lesson": 0, "total": len(LESSONS)}


def tour_status():
    st = _tour_load()
    st["total"] = len(LESSONS)
    i = int(st.get("lesson") or 0)
    st["lesson_title"] = (LESSONS[i][0] if i < len(LESSONS)
                          else ("— завершена" if st.get("sent") else ""))
    sent = st.get("sent") or {}
    st["answered"] = sum(1 for v in sent.values() if v.get("answered"))
    b = store.boards().get(str(st.get("board") or "")) or {}
    via = bool(st.get("via_bridge") or b.get("via_bridge"))
    st["via_bridge"] = via
    if via:
        st["board_online"] = bool(bridge_online())
    else:
        st["board_online"] = bool(st.get("board")) and \
            (time.time() - float(b.get("last_seen") or 0) < 75)
    # мост-статус + честная подсказка, почему плата «не видна»
    try:
        import bridge as _br
        bs = _br.status()
        st["bridge"] = {"online": bs.get("online"), "url": bs.get("url"),
                        "last_err": str(bs.get("last_err") or "")[:140]}
        if not st["board_online"] and bs.get("online"):
            st["hint"] = ("Мост видит плату на %s — экскурс пойдёт через "
                          "мост. Чтобы плата получала письма сама, впиши в "
                          "её config cloud_url (адрес облака) и token из "
                          "data/token.txt облака." % bs.get("url"))
        elif not bs.get("online"):
            st["hint"] = ("Мост не видит плату (%s): %s. Запусти мозг платы "
                          "(main.py, порт 7000) — экскурс подключится сам."
                          % (bs.get("url"),
                             str(bs.get("last_err") or "")[:120]))
    except Exception:
        pass
    return st


def bridge_online():
    """Мост видит плату прямо сейчас? (для via_bridge-плат)"""
    try:
        import bridge as _br
        bs = _br.status()
        if bs.get("online") and (time.time() - float(bs.get("last_ok") or 0)) < 75:
            return True
        ok, _st = _br.probe(save=False)
        return bool(ok)
    except Exception:
        return False


def tour_tick():
    """Фоновый тик (раз в ~20 c): пора ли отправлять следующий урок."""
    st = _tour_load()
    if not st.get("on"):
        return
    now = time.time()
    if now < float(st.get("next_ts") or 0):
        return
    i = int(st.get("lesson") or 0)
    if i >= len(LESSONS):
        st["on"] = False
        st["done"] = True
        _tour_save(st)
        store.log("УЧИТЕЛЬ", "ЭКСКУРСИЯ завершена: %d уроков отправлено"
                  % len(LESSONS))
        return
    target = str(st.get("board") or "")
    via = bool(st.get("via_bridge"))
    if target:
        b = store.boards().get(target) or {}
        if via:
            # плата через мост: жива, пока мост её видит
            if not bridge_online():
                _tlog(st, "мост потерял плату %s — ищем другую" % target)
                target, via = "", False
        elif now - float(b.get("last_seen") or 0) > 300:
            _tlog(st, "плата %s давно не выходила на связь — ищем другую"
                  % target)
            target = ""
    if not target:
        target, via = _target_pick()
        if not target:
            st["next_ts"] = now + 30          # плат нет — подождём
            _tour_save(st)
            return
        st["board"] = target
        st["via_bridge"] = via
        _tlog(st, "экскурсия переключена на плату %s%s"
              % (target, " (через мост)" if via else ""))
    title, body = LESSONS[i]
    text = "УРОК %d/%d. %s\n\n%s" % (i + 1, len(LESSONS), title, body)
    src = "builtin"
    t = store.settings().get("teacher") or {}
    if t.get("llm_on") and str(t.get("llm_url") or "").strip():
        try:
            out, _err = _openai_chat(
                str(t["llm_url"]).strip(), str(t.get("llm_key") or "").strip(),
                str(t.get("llm_model") or "").strip(), TOUR_PROMPT,
                [{"role": "user", "content": text}], 900)
            if out and len(out) > 200:
                text = out
                src = "llm"
        except Exception:
            pass
    mid = "l" + os.urandom(5).hex()
    if via:
        # v3.8.2: плата без мозг-почты — урок идёт ЧЕРЕЗ МОСТ напрямую
        # (POST /api/send): плата подумает зонной сетью и ответит.
        try:
            import bridge
            r = bridge.send(text[:2000], user="учитель облака", wait=45.0)
        except Exception as e:
            r = {"ok": False, "error": str(e)}
        answer = str(r.get("answer") or "") if r.get("ok") else ""
        st.setdefault("sent", {})[mid] = {
            "lesson": i, "board": target, "ts": now,
            "answered": bool(answer), "verdict": answer[:200],
            "src": (src + "+мост") if answer else (src + "+мост(нет ответа)")}
        _tlog(st, "урок %d/%d «%s» -> %s ЧЕРЕЗ МОСТ%s"
              % (i + 1, len(LESSONS), title, target,
                 ": плата ответила" if answer
                 else (", молчит (%s)" % str(r.get("error") or "")[:80])))
    else:
        import brain
        brain.mail_deliver({"id": mid, "from": "учитель облака",
                            "to": target, "text": text[:3800], "long": True,
                            "ts": time.time()})
        st.setdefault("sent", {})[mid] = {"lesson": i, "board": target,
                                          "ts": now, "answered": False,
                                          "verdict": "", "src": src}
        _tlog(st, "урок %d/%d «%s» -> %s (мозг-почта)"
              % (i + 1, len(LESSONS), title, target))
    st["lesson"] = i + 1
    st["next_ts"] = now + float(st.get("interval") or 90)
    st["last_sent"] = {"lesson": i + 1, "title": title, "to": target,
                       "src": src}
    _tour_save(st)
    store.log("УЧИТЕЛЬ", "экскурсия: урок %d/%d -> %s"
              % (i + 1, len(LESSONS), target))


def tour_answer(mid, text, zone=""):
    """Плата ответила на урок: вердикт учителя + журнал экскурсии."""
    st = _tour_load()
    item = (st.get("sent") or {}).get(str(mid)[:40])
    if not item:
        return False
    item["answered"] = True
    item["answered_ts"] = time.time()
    v = builtin_teach("verdict", [{"content": str(text or "")[:800]}])
    item["verdict"] = v
    ln = int(item.get("lesson") or 0)
    if v == "0" and st.get("on"):
        n = int((st.get("repeat") or {}).get(str(ln), 0))
        if n < 2:
            st.setdefault("repeat", {})[str(ln)] = n + 1
            st["lesson"] = ln                     # повторить этот же урок
            st["next_ts"] = time.time() + 60
            _tlog(st, "урок %d усвоен слабо — повторим" % (ln + 1))
    _tlog(st, "плата ответила на урок %d (зона: %s, вердикт %s)"
          % (ln + 1, zone or "?", v))
    _tour_save(st)
    return True
