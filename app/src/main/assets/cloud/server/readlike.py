# -*- coding: utf-8 -*-
"""v3.165 — ВСЕ ЗОНЫ ЧИТАЮТ МАТЕРИАЛЫ, КАК «ЛЮБОПЫТСТВО».

Заявка владельца: «связнее всех отвечает зона любопытство, а остальные как
будто не читают материалы. Сделай так, чтобы все отвечали, как любопытство
облака. Своя LLM и мозг тоже должны читать материалы в интернете».

Почему «Любопытство» отвечало лучше: оно не собирает ответ из обрывков
поисковой выдачи и не вспоминает по памяти — оно находит статью по теме,
читает её ЦЕЛИКОМ, кладёт в базу знаний и отвечает связным куском статьи.

Теперь так делает каждый ответ на вопрос с темой (read_for_answer):
  1. в базе знаний уже есть статья, НАЗВАНИЕ которой про тему вопроса, —
     читать нечего, отвечает база;
  2. нет — облако, как любопытство, ищет статью в Википедии, читает её
     целиком и сохраняет в базу знаний (источник «прочитано для ответа»);
     в Википедии нет — отвечает зона «Интернет-источники» (страницы
     Google/Яндекса), тоже связным куском подряд;
  3. прочитанное сразу получают остальные «читатели»:
       * мозг облака — сеть материалов (kb.add_article учит её);
       * мозг neuro-12 на платах — выжимка статьи по мозг-почте/каналу
         связи (как дайджест любопытства);
       * своя LLM — читает статью (llmlink.read_new_articles), фоном.
  4. ответ пишется из статьи связным куском (kb.compose_answer) — дальше
     шаблон и дополнение своей LLM, как у всех зон.
Неудачные темы не перечитываются 10 минут (не тормозим каждый ответ).
Выключается в Настройки → Общие → «Интернет и знания» (answer_read_first).
"""
import re
import threading
import time

import kb
import store
import webfetch

_TRIED = {}                  # тема -> время неудачной попытки
_RETRY_S = 600
_MIN_CHARS = 300
_LOCK = threading.Lock()
_STAT = {"read": 0, "kb_had": 0, "failed": 0, "last": ""}

_GENERIC = set("""такое такой такая такие что кто как какой какая какие каков
где когда почему зачем сколько работает работают работать делает делают
является являются был была были было есть будет можно нужно надо очень
расскажи скажи объясни опиши покажи знаешь подскажи пожалуйста про об для
этот эта это эти того тому всего всех свой своя свои мне тебе меня тебя
вообще просто сейчас потом чем зачем откуда куда нужен нужна нужны""".split())


# Вопросы, на которые энциклопедия — не тот источник: практические
# («как установить / настроить / исправить ошибку»), свежие («сегодня»,
# «курс», «погода», «новости», «цена»). Для них — страницы выдачи как раньше.
_SKIP_Q = re.compile(
    r"\b(?:как\s+(?:установ|настро|подключ|исправ|убра|удал|включ|выключ|"
    r"сдела|почин|обнов|скача|запуст|сбро|восстанов|найти|купить|выбрать)\w*|"
    r"ошибк\w*|не\s+работает|не\s+запуска\w*|команд\w*|инструкц\w*|"
    r"сегодня|сейчас|завтра|вчера|новост\w*|курс\w*|погод\w*|цен[аеуы]\w*|"
    r"стоит|стоимост\w*|расписани\w*|адрес\w*|телефон\w*|рядом|"
    r"скачать|купить|отзыв\w*|рейтинг\w*|лучш\w*|топ)\b", re.I)


def wants_article(q):
    """Вопрос — о понятии/явлении/предмете (энциклопедия уместна)?"""
    return not _SKIP_Q.search(str(q or ""))


def enabled():
    s = store.settings()
    return bool(s.get("answer_read_first", True) and s.get("web_on"))


def _stems(t):
    """Основы слов темы (стеммер базы знаний), без служебных слов."""
    words = [w for w in re.findall(r"[а-яёa-z0-9]{3,}",
                                   str(t or "").lower().replace("ё", "е"))
             if w not in _GENERIC]
    try:
        stop = getattr(kb, "_STOP_STEMS", set())
        return [x for x in kb._stems(" ".join(words)) if x not in stop]
    except Exception:
        return [w[:5] for w in words]


def _title_on_topic(title, q):
    """Название статьи про тему вопроса: общие основы покрывают хотя бы
    половину названия («Чёрная дыра» ↔ «как образуются чёрные дыры»)."""
    qs, ts = set(_stems(q)), set(_stems(title))
    if not qs or not ts:
        return False
    common = len(qs & ts)
    return common >= 1 and common * 2 >= len(ts)


def kb_has(q, hits):
    """В базе уже есть статья ПРО тему вопроса (по названию)?"""
    for h in (hits or [])[:3]:
        if str(h.get("title") or "").startswith("Из интернета:"):
            continue          # кэш обрывков выдачи — не прочитанная статья
        if float(h.get("score") or 0) >= 2.0 and \
                _title_on_topic(h.get("title") or "", q):
            return True
    return False


def _pick(cands, q):
    """Кандидат, название которого ближе всего к теме вопроса."""
    qs = set(_stems(q))
    best, bs = None, -1.0
    for i, c in enumerate(cands):
        ts = set(_stems(c.get("title") or ""))
        s = len(qs & ts) - 0.3 * len(ts - qs) - 0.05 * i   # лишние слова — хуже
        if s > bs:
            best, bs = c, s
    if best is not None and bs > 0 and _title_on_topic(best.get("title"), q):
        return best
    return None                  # чужая статья — не читаем


def _share(title, text):
    """Прочитанное — мозгу плат и своей LLM (как дайджест любопытства)."""
    try:                       # мозгу neuro-12 на каждой свободной плате
        import brain
        digest = ("Облако прочитало к вопросу статью «%s». Коротко: %s "
                  "Полная статья — в базе знаний облака."
                  % (title, " ".join(str(text).split())[:600]))
        sent = []
        for b in (store.free_boards() or [])[:3]:
            brain.mail_deliver({"id": "r" + brain.os_urandom_hex(),
                                "from": "чтение облака", "to": b["id"],
                                "text": digest[:3800], "long": False,
                                "ts": time.time()})
            sent.append(str(b["id"]))
        if sent:
            store.log("ЧТЕНИЕ", "выжимка «%s» -> платы %s" % (
                title[:60], ", ".join(sent)))
    except Exception as e:
        store.log("ЧТЕНИЕ", "платам выжимка не ушла: %s" % e)
    try:
        import boardmind
        boardmind.link(title, text, "статья")
    except Exception:
        pass

    def _llm():
        try:
            import llmlink
            llmlink.read_new_articles(limit=2)
        except Exception:
            pass
    threading.Thread(target=_llm, daemon=True, name="llm-read").start()


def _from_wiki(q):
    try:
        cands = webfetch.wiki_search(q, limit=4) or []
    except Exception as e:
        return None, "Википедия: %s" % webfetch.honest(e)
    tried = []
    while cands:
        c = _pick(cands, q)
        if c is None:
            break
        cands = [x for x in cands if x is not c]
        title = str((c or {}).get("title") or "").strip()
        if not title or title in tried:
            continue
        tried.append(title)
        lang = str((c or {}).get("lang") or "ru")
        try:
            text = webfetch.wiki_extract(title, lang=lang) if lang != "ru" \
                else webfetch.wiki_extract(title)
        except Exception:
            continue
        if len(text or "") >= _MIN_CHARS:
            return {"title": title, "text": text,
                    "src": str((c or {}).get("src") or "Википедия")}, ""
        if len(tried) >= 3:
            break
    return None, "в Википедии статьи по теме нет"


def read_for_answer(q, hits=None):
    """Прочитать статью по теме вопроса (если её нет в базе).
    -> {"read": True, "title"} — прочитана и сохранена в базу;
       {"read": False, "why"} — читать не нужно или не вышло."""
    q = re.sub(r"\s+", " ", str(q or "")).strip()[:150]
    if not q or not _stems(q):
        return {"read": False, "why": "нет темы"}
    if not enabled():
        return {"read": False, "why": "выключено"}
    if not wants_article(q):
        return {"read": False, "why": "практический или свежий вопрос — "
                                      "отвечают страницы выдачи"}
    if kb_has(q, hits):
        _STAT["kb_had"] += 1
        return {"read": False, "why": "статья уже в базе"}
    key = " ".join(sorted(set(_stems(q))))
    with _LOCK:
        if time.time() - _TRIED.get(key, 0) < _RETRY_S:
            return {"read": False, "why": "недавно не нашлось"}
        _TRIED[key] = time.time()
    t0 = time.time()
    # как любопытство — статья Википедии целиком. Нет её — дальше обычная
    # зона «Интернет-источники» (страницы Google/Яндекса), без второго
    # похода в выдачу отсюда (иначе ответ ждал бы выдачу дважды)
    art, why = _from_wiki(q)
    if not art:
        _STAT["failed"] += 1
        store.log("ЧТЕНИЕ", "«%s»: прочитать не вышло — %s" % (q[:60], why))
        return {"read": False, "why": why}
    src = "%s (прочитано для ответа)" % art["src"]
    r = kb.add_article(art["title"], art["text"], source=src)
    if not r.get("ok"):
        return {"read": False, "why": r.get("error") or "не сохранилась"}
    with _LOCK:
        _TRIED.pop(key, None)
    _STAT["read"] += 1
    _STAT["last"] = art["title"]
    store.log("ЧТЕНИЕ", "к вопросу «%s» прочитал «%s» (%s, %d знаков, %.1f с) — "
              "в базу знаний, сети мозга, платам и своей LLM" % (
                  q[:60], art["title"], art["src"], len(art["text"]),
                  time.time() - t0))
    _share(art["title"], art["text"])
    return {"read": True, "title": art["title"], "src": art["src"],
            "id": r.get("id")}


def status():
    return dict(_STAT, on=enabled())


def fetch_article(topic):
    """v3.167: для мозга платы — найти и прочитать статью по теме (Википедия,
    иначе Рувики). Без проверки «уже в базе». -> (art | None, why)."""
    topic = re.sub(r"\s+", " ", str(topic or "")).strip()[:150]
    if not topic or not _stems(topic):
        return None, "нет темы"
    return _from_wiki(topic)
