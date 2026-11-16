# -*- coding: utf-8 -*-
# ===========================================================================
# curiosity.py — ЛЮБОПЫТСТВО НЕЙРОНОВ (v3.15)
# ---------------------------------------------------------------------------
# Заявка владельца: «внедри любопытство для нейронов, пусть читают статьи
# Википедии». Нейроны сети материалов облака теперь ХОТЯТ ЗНАТЬ сами:
#
#   1) ОТКУДА БЕРЁТСЯ ЖЕЛАНИЕ ЗНАТЬ (очередь «хочу знать»):
#      - вопросы, на которые мозг честно ответил «не знаю» (brain.py кладёт
#        их в очередь через want());
#      - горячие узлы сети материалов (о чём больше всего говорили);
#      - стартовые темы-семена (космос, вулканы, океан, мозг...).
#
#   2) КАК ЧИТАЕТ: раз в curiosity_every секунд (фон brain._worker -> tick())
#      мозг берёт тему из очереди, ищет статью в Википедии через webfetch
#      (интернет-шлюз), выкачивает вводный текст статьи, сохраняет её в базу
#      знаний с источником «Википедия (любопытство)» — kb.add_article сам
#      обучает сеть материалов. Статьи, которые уже есть в KB, не повторяются.
#
#   3) ПЛАТА ТОЖЕ УЗНАЁТ: короткий дайджест прочитанного уходит онлайн-плате
#      мозг-почтой (from «любопытство облака») — нейроны платы получают
#      новые темы через тот же канал, что и уроки экскурсии.
#
#   4) ЧЕСТНОСТЬ: каждая попытка — в журнале (прочитал/не смог + причина);
#      при сетевых сбоях — пауза-бэкофф (удваивается до 30 минут), чтобы
#      не долбить молчащий шлюз; всё выключается настройкой curiosity_on
#      и не работает при web_on=False (интернет выключен владельцем).
#
#   v3.15 — ИСПРАВЛЕНИЕ «ЛЮБОПЫТСТВО УМЕРЛО» (заявка владельца: «при
#      подключении LLM любопытство работает, а сейчас не работает»):
#      а) «статья уже в базе знаний» — это НЕ сетевая неудача: раньше она
#         шла в fail_streak и раздувала бэкофф до 30 минут (нейроны
#         засыпали на полчаса после каждой уже известной статьи — со
#         стороны любопытство выглядело мёртвым). Теперь это «уже знаю»
#         (skip): без бэкоффа, без неудач, тема снимается с очереди;
#      б) тема из очереди «хочу знать» РЕАЛЬНО снимается с очереди
#         (раньше pop делался в локальной копии и не сохранялся — одна
#         застрявшая тема блокировала всю очередь навсегда);
#      в) за один такт нейроны пробуют до 3 тем: известная тема не
#         останавливает чтение — берётся следующая;
#      г) «узнай про X» от владельца сбрасывает бэкофф — ручная просьба
#         читает немедленно, а не после получасовой паузы.
#   v3.18 — САМОСТОЯТЕЛЬНОСТЬ (заявка владельца: «зона любопытство должна
#      работать самостоятельно, а сейчас она зависит от того, что напишешь
#      в настройках»):
#      а) прочитав статью, нейроны ВЫЦЕПЛЯЮТ из неё 1–2 новых темы — имена
#         и термины, которых ещё нет в базе знаний, и ставят их в очередь:
#         чтение само порождает следующее чтение (цепочки любопытства);
#      б) когда очередь пуста, тема ВЫБИРАЕТСЯ САМОЙ: свежие статьи KB ->
#         горячие узлы сети -> семена (настройка curiosity_self, по
#         умолчанию включена); очередь владельца — желанный подарок,
#         но не единственное топливо;
#      в) счётчик self — сколько тем нейроны нашли себе сами.
# ===========================================================================
import random
import re
import threading
import time

import kb
import store
import webfetch

_LOCK = threading.RLock()
_PATH = "curiosity.json"
_MAX_JOURNAL = 40
_MAX_QUEUE = 50
_MIN_CHARS = 400          # короче — статья мусорная, не учим
_RECENT = []              # последние темы/названия (не повторять)
_TICK_ATTEMPTS = 3        # v3.15: тем за один такт (известные не мешают)
_RECENT_ARTS = []         # v3.18: названия свежих статей (для самостоятельных тем)

# темы-семена: с чего нейронам начинать, пока сеть и очередь пусты
SEED_TOPICS = (
    "Космос", "Чёрная дыра", "Вулкан", "Океан", "Динозавры",
    "Электричество", "ДНК", "Солнечная система", "Головной мозг",
    "Антарктида", "Молния", "Лазер", "Медоносная пчела", "Гравитация",
    "Транзистор", "Фотосинтез", "Тайфун", "Марс", "Молекула воды",
    "Северное сияние", "Бамбук", "Число пи", "Древний Рим", "Компас",
)


def _load():
    d = store.load_json(_PATH, None)
    if not isinstance(d, dict):
        d = {}
    d.setdefault("journal", [])
    d.setdefault("queue", [])
    d.setdefault("read", 0)
    d.setdefault("failed", 0)
    d.setdefault("skipped", 0)      # v3.15: «уже знаю» — не неудача
    d.setdefault("backoff_until", 0.0)
    d.setdefault("fail_streak", 0)
    d.setdefault("last_ts", 0.0)
    d.setdefault("self", 0)          # v3.18: тем нашла себе сама
    return d


def _queue_pop():
    """v3.15: снять ПЕРВУЮ тему с очереди и СОХРАТЬ это (раньше pop
    делался в локальной копии словаря и терялся — очередь стояла)."""
    with _LOCK:
        d = _load()
        if not d["queue"]:
            return None
        t = d["queue"].pop(0)
        _save(d)
        return t


def _queue_drop(topic):
    """v3.15: убрать тему с очереди (нейроны её уже знают)."""
    t = str(topic or "").strip().lower()[:120]
    if not t:
        return
    with _LOCK:
        d = _load()
        keep = [q for q in d["queue"] if q.strip().lower() != t]
        if len(keep) != len(d["queue"]):
            d["queue"] = keep
            _save(d)


def _save(d):
    with _LOCK:
        store.save_json(_PATH, d)


def want(topic, why=""):
    """Нейрон ХОЧЕТ узнать тему. Тема попадает в очередь (без дублей)."""
    t = re.sub(r"\s+", " ", str(topic or "")).strip(" ?.!«»\"'")[:120]
    if not t or len(t) < 2:
        return False
    with _LOCK:
        d = _load()
        low = t.lower()
        if low not in [q.lower() for q in d["queue"]]:
            d["queue"].insert(0, t)
            d["queue"] = d["queue"][:_MAX_QUEUE]
            _save(d)
            store.log("ЛЮБОПЫТСТВО", "хочу знать: «%s»%s"
                      % (t, (" (%s)" % why) if why else ""))
            return True
    return False


# ------------------- v3.18: САМОСТОЯТЕЛЬНЫЕ ТЕМЫ ----------------------------
# Кандидат темы: 1–3 слова, ПЕРВОЕ с заглавной («Марианская впадина»,
# «Тихий океан», «Азии») — вторые слова могут быть строчными.
_TOPIC_CAND = re.compile(
    r"(?<![а-яёa-z0-9])([A-ZА-ЯЁ][а-яёa-z]{2,}(?:[\s-]+"
    r"[A-Za-zА-Яа-яЁё]{3,}){0,1})")
_STOPW = {"Глав", "Раздел", "Примечани", "Также", "Например", "Однако",
          "Поэтому", "Таким", "Этот", "Эта", "Эти", "Первый", "Второй",
          "Третий", "Когда", "Здесь", "Именно", "Кроме", "Среди",
          "Впрочем", "Несмотря", "Благодар", "Итак", "Фактически",
          "Самый", "Самая", "Самые", "Более", "Менее", "Очень"}


def _stopword(w):
    for s in _STOPW:
        if w.startswith(s):
            return True
    return False


def _kb_titles(lower=True):
    out = set()
    try:
        for a in kb.list_articles(limit=400):
            t = str(a.get("title") or "").strip()
            if t:
                out.add(t.lower() if lower else t)
    except Exception:
        pass
    return out


def _extract_topics(title, text, limit=2):
    """v3.18: выцепить из свежепрочитанной статьи темы-продолжения:
    имена собственные/термины (1–3 слова с заглавной), которых ещё нет
    в базе знаний и в очереди. Это и есть механика «захотелось знать
    ещё» — чтение само порождает следующее чтение."""
    if store.settings().get("curiosity_self") is False:
        return []
    known = _kb_titles()
    with _LOCK:
        d = _load()
        queued = {q.strip().lower() for q in d["queue"]}
    seen = {str(title or "").strip().lower()}
    out = []
    for m in _TOPIC_CAND.finditer(str(text or "")[:8000]):
        t = re.sub(r"\s+", " ", m.group(1)).strip(" .,;:!?«»()")
        if not 4 <= len(t) <= 44:
            continue
        low = t.lower()
        if low in seen or low in known or low in queued:
            continue
        words_t = t.split()
        # стоп-слова и короткие первые слова отсекаются в ЛЮБОЙ позиции
        if any(_stopword(w) for w in words_t) or len(words_t[0]) < 4:
            continue
        if len(words_t[-1]) < 4:          # «и», «на дне» — не тема
            continue
        out.append(t)
        seen.add(low)
        if len(out) >= limit:
            break
    return out


def _chain_topics(title, text):
    """Прочитал статью -> добавить найденные в ней темы в очередь
    (why = «сама нашла в статье …»). Возврат: сколько добавлено."""
    added = 0
    for t in _extract_topics(title, text):
        if want(t, why="сама нашла в статье «%s»" % str(title)[:40]):
            added += 1
    return added


def _self_topic():
    """v3.18: тема, выбранная САМОЙ (без подсказки владельца): случайная
    свежая статья KB -> термины из неё; иначе None (дальше горячие узлы
    и семена — старый механизм)."""
    if store.settings().get("curiosity_self") is False:
        return None
    try:
        arts = kb.list_articles(limit=40)
        arts = [a for a in arts if str(a.get("title") or "").strip()]
        if not arts:
            return None
        random.shuffle(arts)
        known = _kb_titles()
        with _LOCK:
            d = _load()
            queued = {q.strip().lower() for q in d["queue"]}
        for a in arts[:8]:
            title = str(a.get("title") or "").strip()
            # v3.18: свои сочинения/выжимки — не источник тем
            if title.startswith(("Сочинение:", "Материалы:")):
                continue
            if title.lower() not in _RECENT_ARTS:
                _RECENT_ARTS.append(title.lower())
                if len(_RECENT_ARTS) > 30:
                    del _RECENT_ARTS[:10]
            art = kb.article(a.get("id"))
            text = str((art or {}).get("text") or "")
            if len(text) < _MIN_CHARS:
                continue
            for t in _extract_topics(title, text, limit=1):
                if t.lower() not in known and t.lower() not in queued:
                    return t
    except Exception:
        pass
    return None


def _recent_guard(title):
    """Не читать одно и то же: название уже было недавно?"""
    low = str(title or "").lower()[:60]
    if low in [r.lower() for r in _RECENT[-25:]]:
        return False
    # и уже есть в KB с таким названием
    for a in kb.list_articles(limit=400):
        if str(a.get("title") or "").lower() == low:
            return False
    return True


def _pick_topic():
    """Выбрать тему: очередь «хочу знать» -> v3.18: тема, выбранная САМОЙ
    (из свежих статей KB) -> горячие узлы сети -> семена.
    v3.15: тема очереди снимается с сохранением (см. _queue_pop).
    v3.18: очередь владельца — приоритет, но не единственное топливо."""
    t = _queue_pop()
    if t:
        return t, "очередь «хочу знать»"
    try:                      # v3.95: о чём думают платы — читаем об этом
        import boardmind
        if random.random() < 0.6:
            bt = boardmind.topic()
            if bt:
                return bt, "мысли плат"
    except Exception:
        pass
    try:
        self_t = _self_topic()
    except Exception:
        self_t = None
    if self_t:
        return self_t, "сама нашла тему"
    hot = []
    try:
        import brain
        rd = brain.mn_readings()
        hot = [t for t in (rd.get("hot") or rd.get("top") or [])
               if isinstance(t, str) and 3 < len(t) < 40]
    except Exception:
        hot = []
    if hot and random.random() < 0.75:
        return random.choice(hot[:8]), "горячий узел сети"
    return random.choice(SEED_TOPICS), "тема-семя"


def read_topic(topic, why="любопытство"):
    """Прочитать ОДНУ тему: поиск Википедии -> текст статьи -> KB -> сеть
    -> журнал -> дайджест плате. Возврат записи журнала (dict).
    v3.15: если все найденные статьи уже в базе знаний — это «уже знаю»
    (skip): запись с ok=False, skip=True; БЕЗ fail_streak и БЕЗ бэкоффа,
    тема снимается с очереди, чтобы нейроны не пережёвывали её вечно."""
    entry = {"ts": time.time(), "topic": str(topic)[:120], "ok": False,
             "title": "", "chars": 0, "note": "", "skip": False,
             "why": str(why)[:60]}
    if not store.settings().get("web_on"):
        entry["note"] = "интернет выключен (web_on=False) — нейроны не читают"
        _journal(entry, failed=True)
        return entry
    try:
        hits = webfetch.wiki_search(topic, limit=4)
    except Exception as e:
        entry["note"] = "поиск не удался: %s" % str(e)[:90]
        _journal(entry, failed=True)
        return entry
    if not hits:
        entry["note"] = "в Википедии по теме ничего не нашлось"
        _journal(entry, failed=True)
        return entry
    # v3.10: пробуем до 3 кандидатов подряд — статья в базе/короткая не
    # останавливают чтение, берём следующего найденного
    title, text = "", ""
    last_note = "подходящих статей не нашлось"
    known = False                     # v3.15: была ли уже известная статья
    try:                      # v3.95: ближе к мыслям плат — первым
        import boardmind
        hits = boardmind.pick(hits, key=lambda h: "%s %s" % (
            h.get("title") or "", h.get("snippet") or ""))
    except Exception:
        pass
    for h in hits[:3]:
        cand = str(h.get("title") or "").strip()
        if not cand or not _recent_guard(cand):
            last_note = "найденные статьи уже в базе знаний"
            known = True
            continue
        try:
            t = webfetch.wiki_extract(cand)
        except Exception as e:
            last_note = "статья не выкачалась: %s" % str(e)[:80]
            continue
        if len(t) >= _MIN_CHARS:
            title, text = cand, t
            break
        last_note = ("«%s» слишком короткая (%d знаков)" % (cand, len(t)))
    if not title:
        entry["note"] = last_note
        if known:
            # v3.15: «уже знаю» — честный skip вместо ложной неудачи
            entry["skip"] = True
            _journal(entry, skipped=True)
            _queue_drop(topic)
        else:
            _journal(entry, failed=True)
        return entry
    entry["title"] = title
    entry["chars"] = len(text)
    r = kb.add_article(title, text, source="Википедия (любопытство)")
    if not r.get("ok"):
        entry["note"] = "статья не сохранилась: %s" % (r.get("error") or "?")
        _journal(entry, failed=True)
        return entry
    entry["ok"] = True
    entry["note"] = "прочитал статью, сеть материалов выросла"
    _RECENT.append(title)
    if len(_RECENT) > 40:
        del _RECENT[:20]
    _journal(entry, failed=False)
    # v3.18: ЦЕПОЧКА ЛЮБОПЫТСТВА — выцепить из статьи следующие темы
    try:
        entry["chained"] = _chain_topics(title, text)
    except Exception:
        entry["chained"] = 0
    _digest_to_boards(title, text)
    try:                      # v3.95: что в статье созвучно мыслям плат
        import boardmind
        entry["boards_link"] = boardmind.link(title, text, "статья")
    except Exception:
        pass
    return entry


def _journal(entry, failed=False, skipped=False):
    """Запись в журнал + статистика + бэкофф при сетевых сбоях.
    v3.15: skipped=True («уже знаю») — это НЕ неудача: только запись в
    журнал и счётчик skipped, без fail_streak и без бэкоффа."""
    with _LOCK:
        d = _load()
        d["journal"].insert(0, entry)
        d["journal"] = d["journal"][:_MAX_JOURNAL]
        if skipped:
            d["skipped"] = int(d.get("skipped") or 0) + 1
            store.log("ЛЮБОПЫТСТВО", "уже знаю: «%s» — %s"
                      % (entry.get("topic"), entry.get("note")))
        elif failed:
            d["failed"] = int(d.get("failed") or 0) + 1
            streak = int(d.get("fail_streak") or 0) + 1
            d["fail_streak"] = streak
            pause = min(1800.0, 60.0 * (2 ** min(streak, 5)))
            d["backoff_until"] = time.time() + pause
            store.log("ЛЮБОПЫТСТВО", "не смог: «%s» — %s"
                      % (entry.get("topic"), entry.get("note")))
        else:
            d["read"] = int(d.get("read") or 0) + 1
            d["fail_streak"] = 0
            d["backoff_until"] = 0.0
            d["last_ts"] = time.time()
            if str(entry.get("why") or "").startswith("сама нашла"):
                d["self"] = int(d.get("self") or 0) + 1
            store.log("ЛЮБОПЫТСТВО", "прочитал: «%s» -> KB (%d знаков)"
                      % (entry.get("title"), entry.get("chars") or 0))
        _save(d)


def _digest_to_boards(title, text):
    """Дайджест прочитанного — онлайн-плате мозг-почтой (нейроны платы
    узнают тему через тот же канал, что и уроки экскурсии)."""
    try:
        import brain
        free = store.free_boards()
        if not free:
            return
        b = free[0]
        digest = ("Любопытство облака: я прочитал статью «%s». Коротко: %s "
                  "Полная статья — в базе знаний облака."
                  % (title, " ".join(str(text).split())[:350]))
        brain.mail_deliver({"id": "c" + brain.os_urandom_hex(),
                            "from": "любопытство облака", "to": b["id"],
                            "text": digest[:3800], "long": False,
                            "ts": time.time()})
        store.log("ЛЮБОПЫТСТВО", "дайджест «%s» -> плата %s"
                  % (title[:60], b["id"]))
    except Exception:
        pass


def tick():
    """Фоновый такт (вызывается из brain._worker раз в ~20 c): сам решает,
    пора ли читать (curiosity_every, бэкофф, web_on).
    v3.15: за такт пробует до 3 тем — тема «уже знаю» не съедает такт:
    берём следующую, пока не прочитаем что-то новое или не кончатся
    темы. Отчёт — по последней попытке (или по удачной)."""
    s = store.settings()
    if not (s.get("curiosity_on") and s.get("web_on")):
        return None
    try:                                   # v3.132: читает только на вдохе
        import breath as _brq
        if not _brq.inhale("Любопытство"):
            return None
    except Exception:
        pass
    d = _load()
    now = time.time()
    if now < float(d.get("backoff_until") or 0):
        return None
    every = float(s.get("curiosity_every") or 120)
    if now - float(d.get("last_ts") or 0) < every:
        return None
    with _LOCK:
        d2 = _load()
        d2["last_ts"] = now            # занять слот до результата
        _save(d2)
    entry = None
    for _i in range(_TICK_ATTEMPTS):
        topic, src = _pick_topic()   # очередь -> горячие узлы -> семена
        if not topic:
            break
        entry = read_topic(topic, why=src)
        if entry.get("ok"):
            _maybe_video(entry.get("title") or topic)
        if entry.get("ok") or not entry.get("skip"):
            return entry               # прочитали ИЛИ настоящая неудача
        # «уже знаю» — пробуем следующую тему в этом же такте
    return entry


def _maybe_video(topic):
    """v3.30: к Википедии — ВИДЕО: каждую curiosity_video_every-ю
    прочитанную тему нейроны ещё и «смотрят» ролик о ней (субтитры,
    название, описание — media.py) и учат его в базу знаний."""
    s = store.settings()
    if not s.get("curiosity_video", True):
        return None
    with _LOCK:
        d = _load()
        n = int(d.get("video_n") or 0) + 1
        d["video_n"] = n
        _save(d)
    if n % max(1, int(s.get("curiosity_video_every") or 2)):
        return None
    return read_video(topic)


def read_video(topic, why="любопытство: видео"):
    """Найти и посмотреть ролик по теме -> база знаний -> журнал."""
    import media
    entry = {"ts": time.time(), "topic": str(topic)[:120], "ok": False,
             "title": "", "chars": 0, "note": "", "skip": False,
             "why": why, "kind": "видео"}
    try:
        vids = media.find_videos(topic, limit=5)
        try:                  # v3.95: ролик ближе к мыслям плат — первым
            import boardmind
            vids = boardmind.pick(vids, key=lambda x: x.get("title") or "")[:3]
        except Exception:
            vids = vids[:3]
        v = None
        for c in vids:
            v = media.watch(c["id"])
            # v3.125: RUTUBE/VK часто без субтитров — подробное описание тоже материал
            if v.get("ok") and (v.get("transcript") or
                                len(v.get("description") or "") >= 300):
                break
        if not v or not v.get("ok"):
            entry["note"] = "видео не нашлось/без текста: %s" % (
                (v or {}).get("err") or "выдача пустая")
            with _LOCK:
                d = _load()
                d["journal"].insert(0, entry)
                d["journal"] = d["journal"][:_MAX_JOURNAL]
                _save(d)
            return entry
        r = media.learn_video(v)
        try:                  # v3.95: что в ролике созвучно мыслям плат
            import boardmind
            entry["boards_link"] = boardmind.link(
                v.get("title") or topic, "%s %s" % (v.get("transcript") or "",
                                                    v.get("description") or ""),
                "ролик")
        except Exception:
            pass
        entry.update({"ok": bool(r.get("ok")), "title": "Видео: " + v["title"],
                      "chars": len(v.get("transcript") or "") +
                      len(v.get("description") or ""),
                      "note": "посмотрела ролик (%s)" % (
                          "субтитры" if v.get("transcript") else
                          "только описание")})
    except Exception as e:
        entry["note"] = "видео не вышло: %s" % str(e)[:80]
    with _LOCK:
        d = _load()
        d["journal"].insert(0, entry)
        d["journal"] = d["journal"][:_MAX_JOURNAL]
        if entry["ok"]:
            d["videos"] = int(d.get("videos") or 0) + 1
        _save(d)
    store.log("ЛЮБОПЫТСТВО", "видео «%s»: %s" % (topic[:60], entry["note"]))
    return entry


def learn_now(topic):
    """«узнай про X» / кнопка панели: немедленно хотеть + прочитать.
    v3.15: ручная просьба сбрасывает бэкофф и счётчик сбоев — владелец
    не должен ждать полчаса после сетевой неудачи."""
    t = str(topic or "").strip()[:120]
    if not t:
        return {"ok": False, "error": "пустая тема"}
    with _LOCK:
        d = _load()
        d["fail_streak"] = 0
        d["backoff_until"] = 0.0
        _save(d)
    want(t, why="просьба владельца")
    entry = read_topic(t, why="просьба владельца")
    return {"ok": bool(entry.get("ok")), "entry": entry,
            "title": entry.get("title") or "",
            "chars": entry.get("chars") or 0,
            "skip": bool(entry.get("skip")),
            "note": entry.get("note") or ""}


def set_enabled(on):
    store.update_settings({"curiosity_on": bool(on)})
    store.log("ЛЮБОПЫТСТВО", "включено" if on else "выключено")
    return status()


def status():
    """Сводка для панели и чата: очередь, журнал, статистика, бэкофф."""
    d = _load()
    s = store.settings()
    return {
        "on": bool(s.get("curiosity_on")),
        "self": bool(s.get("curiosity_self", True)),
        "every": int(s.get("curiosity_every") or 120),
        "read": int(d.get("read") or 0),
        "failed": int(d.get("failed") or 0),
        "skipped": int(d.get("skipped") or 0),
        "self_found": int(d.get("self") or 0),
        "queue": list(d.get("queue") or [])[:12],
        "journal": list(d.get("journal") or [])[:12],
        "backoff": max(0, int(float(d.get("backoff_until") or 0)
                              - time.time())),
        "last_ts": float(d.get("last_ts") or 0),
        "seeds": len(SEED_TOPICS),
        "boards": _boards_status(),
    }


def _boards_status():
    try:
        import boardmind
        return boardmind.status()
    except Exception:
        return {}
