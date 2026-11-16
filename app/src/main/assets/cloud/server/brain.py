# -*- coding: utf-8 -*-
# ===========================================================================
# brain.py — МОЗГ ОБЛАКА: отвечает в чате БЕЗ всякого LLM
# ---------------------------------------------------------------------------
# Движок облака (по образцу самого neuro_12):
#   1) навыки реестра (математика, время, вики, память, code-функции...);
#   2) база знаний KB (tf-idf поиск по статьям и личным базам плат);
#   3) нейросеть материалов mozgnet (разрядка активации по ассоциациям);
#   4) память облака (заметки владельца, последние диалоги);
#   5) Википедия (если разрешена web_on и своего ответа не нашлось);
#   6) честное «не знаю» + передача вопроса живой плате (мозг-почта).
# КАЖДОЕ сообщение (вопрос и ответ) — материал для обучения сети облака:
# облако становится умнее после каждого диалога. LLM здесь НЕТ и не будет:
# /api/llm/teach — отдельный учительский канал (teacher.py).
# ===========================================================================
import random
import re
import threading
import time

import kb
import sandbox
import skills
import store
import textutils
import webfetch

_MN = None
class _TimedLock(object):
    """v3.27: RLock с ТАЙМАУТОМ для сети материалов. Раньше вопрос ждал
    общую блокировку сколько угодно (сохранение сети — десятки секунд), и
    облако «висло» после вопроса. Теперь `with _MN_LOCK:` ждёт не дольше
    timeout и бросает RuntimeError — все места сборки текста сетью уже в
    try и честно пропускают зону; ответ идёт дальше по конвейеру."""

    def __init__(self, timeout=8.0):
        self._l = threading.RLock()
        self.timeout = float(timeout)

    def acquire(self, blocking=True, timeout=-1):
        return self._l.acquire(blocking, timeout)

    def release(self):
        self._l.release()

    def __enter__(self):
        if not self._l.acquire(timeout=self.timeout):
            raise RuntimeError("сеть материалов занята — зона пропущена")
        return self

    def __exit__(self, *exc):
        self._l.release()
        return False


_MN_LOCK = _TimedLock(8.0)
_MN_DIRTY = [False]            # v3.27: сеть менялась с последнего сохранения
_MN_PENDING = []               # v3.27: материалы, ждущие обучения (сеть занята)
_MN_SIZE = [(0, 0, 0)]         # v3.27: последний известный размер сети
_MN_SAVE_EVERY = 180.0         # v3.27: сек между сохранениями (было 20/45)
_MN_PATH = None
_LAST_SAVE = [0.0]

# зоны — как на плате: видно в чате, откуда пришёл ответ
Z = {"skill": "Навык", "kb": "База знаний", "mn": "Сеть материалов",
     "wiki": "Википедия", "mem": "Память", "board": "Плата-12",
     "dict": "Словарь", "gen": "Генератор", "sys": "Облако",
     "math": "Математика", "code": "Код", "mind": "Мысли платы",
     "curiosity": "Любопытство", "hypo": "Предположение",
     "speech": "Речь из словаря", "web": "Интернет-источники",
     "imagine": "Воображение", "doc": "Документы", "talk": "Разговор",
     "qa": "Обучение", "thought": "Мысли облака",
     "media": "Видео", "vision": "Зрение", "llm": "Локальная LLM",
     "membrain": "Память и мозг", "hearing": "Слух"}

# v3.30: «что на картинке «имя»?» — описание присланной картинки из базы
_IMG_Q = re.compile(r"^\s*что\s+(?:на|изображено на|видно на)\s+(?:картинке|"
                    r"фото\w*|изображении|снимке)\s*«([^»]+)»", re.I)

# ПАНЕЛЬ «ЗОНЫ МЫСЛИ»: все зоны мозга облака (порядок = порядок проверки).
# Каждая зона — отдельный «орган» мышления; бейдж в чате показывает,
# какая зона ответила. Ни одна зона не должна быть декоративной.
ZONES = [
    ("membrain", "v3.99: ПАМЯТЬ И МОЗГ: статьи в базе нет — мозг отвечает "
     "сам и с опорой на память (сеть материалов, память диалогов, заметки, "
     "память платы); мысль мозга neuro-12 — прямым вопросом по мосту; своя "
     "LLM переписывает память и дополняет мысль",
     "любой вопрос, на который нет статьи: «зачем мозгу любопытство»"),
    ("skill", "Реестр функций: математика, время, заметки, статус",
     "«посчитай 7*6», «который час», «запомни: …», «статус»"),
    ("dict", "Словари русского языка: ru_top, ru_pos, extra + словари плат",
     "«что значит слово …», «проверь слово …»"),
    ("math", "v3.8: формулы и модели — мозг сам считает по своей базе "
     "(без LLM)",
     "«площадь круга радиусом 5», «вклад 1000 под 10 процентов на 3 года», "
     "«модель по данным 1,2; 2,4; 3,5 — прогноз 4»"),
    ("kb", "База знаний: статьи, файлы владельца, материалы плат",
     "«что такое мозг-12», загрузи файлы во вкладке «База знаний»"),
    ("mn", "Нейросеть материалов: ассоциации, растёт от каждого диалога",
     "«мысль <тема>», «статья <тема>», обычные вопросы по материалам"),
    ("mem", "Память владельца: заметки и их поиск по темам",
     "«что помнишь про …», «запомни: …»"),
    ("wiki", "Википедия через облако (честная выжимка, не LLM)",
     "«что такое …» — когда в KB и сети по теме пусто"),
    ("board", "Живая плата-мозг: ответы приходят через мозг-почту",
     "маршрут «плата» или авто-пересылка, когда облако не знает"),
    ("gen", "Генератор длинных текстов по материалам сети",
     "«статья <тема>», кнопка генерации, POST api/gen/task"),
    ("code", "v3.9: чтение программного кода: функции/классы/defines "
     "разбираются ast-парсером и сканером C, индекс + KB + сеть",
     "«что делает функция tts_say», «расскажи про main.py», «изучи код» "
     "(вкладка База знаний)"),
    ("mind", "v3.9: генератор НА ТОМ, ЧТО ДУМАЕТ ПЛАТА: мысли плат — "
     "тема и материал для сети",
     "«что думает плата», «мысли платы»"),
    ("curiosity", "v3.10: ЛЮБОПЫТСТВО НЕЙРОНОВ: сами хотят знать — читают "
     "статьи Википедии в базу знаний и сеть; «не знаю» становится темой",
     "«узнай про X», «открой X», «любопытство»"),
    ("hypo", "v3.11: СМЕЛОСТЬ ПРЕДПОЛОЖЕНИЙ: когда точных знаний нет, мозг "
     "собирает слабые улики (похожие статьи KB, ассоциации сети, заметки) "
     "и честно предполагает: догадка помечена как гипотеза, уходит на "
     "проверку любопытству; «верно/неверно» от владельца учит сеть",
     "спроси о незнакомом — услышишь гипотезу; ответь «верно»/«неверно»"),
    ("speech", "v3.12: РЕЧЬ ИЗ СЛОВАРЯ: нейросеть СОБИРАЕТ ответы на вопросы "
     "из СЛОВ СЛОВАРЯ (ru_top, ru_pos, extra + словари плат) по выученным "
     "связям материалов — только честно: ни одного выдуманного слова, "
     "нет знаний — честное «не знаю». Автоматических ответов больше нет",
     "«мысль <тема>» — сеть скажет мысль словарём; обычные вопросы"),
    ("web", "v3.14: ИНТЕРНЕТ-ИСТОЧНИКИ: на вопрос-формат читает ПЕРВЫЕ "
     "СТРАНИЦЫ выдачи поиска (до 10) и собирает ответ из текста КАЖДОЙ: "
     "каждый фрагмент ПОДПИСАН своим сайтом, в конце нумерованный список "
     "«Источники:»; ничего не сочиняет, не нашла — честно уходит дальше; "
     "резерв — выжимки API-источников (Википедия, Google, Яндекс)",
     "«почему небо голубое?», «кто такой Тесла?» — при включённом web_first"),
    ("imagine", "v3.16: ЗОНА ВООБРАЖЕНИЯ: рисует и ПЕРЕРИСОВЫВАЕТ картинки "
     "по описанию (дом, кот, ракета, ночь, закат, зима…), строит ГРАФИКИ "
     "функций, рисует формулы через собственный LaTeX-движок и мечтает "
     "мини-LLM (настоящий трансформер: токены, эмбеддинги, внимание, "
     "температура/top-k/top-p) — учится на базе знаний облака, без "
     "внешних сетей; всё нарисованное — картинки SVG прямо в чате",
     "«нарисуй дом у реки ночью», «перерисуй закат с котом», «график "
     "y=x^2», «представь …», «что нарисовано»"),
    ("doc", "v3.27: ДОКУМЕНТЫ: собирает документ по теме или сохраняет "
     "ответ/разговор в файл (docx, txt, html, csv) со ссылкой на скачивание",
     "«создай документ про перенос записи», «сохрани ответ в docx»"),
    ("talk", "v3.27: РАЗГОВОР: отвечает как в живом диалоге — коротко, по "
     "сути, помнит беседу (кнопка «💬 Разговор»)",
     "«что ты знаешь про Антарктиду» → «а какая у неё площадь?»"),
    ("qa", "v3.28: ОБУЧЕНИЕ НА ВОПРОСАХ И ОТВЕТАХ: вопросы учат всегда, "
     "ответы — только с опорой на источник или расчёт; подтверждённые 👍 "
     "или исправлением «правильный ответ: …» мозг повторяет из памяти, "
     "отклонённые 👎 — больше не выдаёт",
     "👍/👎 под ответом в чате; «правильный ответ: …» после ответа"),
    ("thought", "v3.28: МЫСЛИ ОБЛАКА: в режиме «💬 Разговор» облако думает "
     "вслух — ассоциации сети материалов, связь с прошлой репликой, что "
     "хочет узнать; мысли идут в ленту «Мысли плат» и на плату",
     "кнопка «💬 Разговор»: «расскажи про Антарктиду»"),
    ("sys", "Служебная зона облака: статус, экскурс, честное «не знаю»",
     "«статус», «проведи экскурс»"),
]

# v3.27: КТО ТЫ / ЧТО УМЕЕШЬ — вопросы О САМОМ мозге. Раньше они уходили в
# интернет, и ответ собирался со страниц про Яндекс.Алису («Что такое
# «Алиса, ты что умеешь» и зачем это спрашивать?»). Теперь мозг отвечает
# о себе — из реестра своих зон и функций (не заготовка: список живой).
_SELF_Q = re.compile(
    r"^\s*(?:а\s+)?(?:(?:ну\s+)?(?:и\s+)?(?:так\s+)?)?(?:"
    r"(?:что|чего)\s+(?:ты\s+|вы\s+)?(?:умеешь|умеете|можешь|можете|знаешь\s+"
    r"делать)(?:\s+делать)?|ты\s+(?:что|чего)\s+(?:умеешь|можешь)|"
    r"кто\s+ты(?:\s+такая|\s+такой)?|ты\s+кто(?:\s+такая|\s+такой)?|"
    r"что\s+ты\s+(?:такое|за\s+(?:бот|программа|ии))|"
    r"(?:какие|каковы)\s+(?:у\s+тебя\s+|твои\s+)?(?:возможности|функции|"
    r"умения)(?:\s+у\s+тебя)?|твои\s+(?:возможности|функции|умения)|"
    r"чем\s+(?:ты\s+)?(?:можешь\s+)?(?:мне\s+)?(?:помочь|полезна|полезен)|"
    r"как\s+(?:тобой|с\s+тобой)\s+(?:пользоваться|работать)|"
    r"помощь|справка|help)\s*[?!.…]*\s*$", re.I)

# что показывать в «что умеешь» (служебное и экзотику — не выпячиваем)
_SELF_ZONES = ("kb", "web", "math", "doc", "talk", "code", "imagine", "mem",
               "skill", "board", "curiosity", "gen", "dict")


# примеры для человека (в ZONES — подсказки для панели разработчика)
_SELF_EX = {
    "web": "«почему небо голубое?» — соберу ответ по страницам интернета",
    "math": "«посчитай 2+2*3», «15% от 2400», «реши x^2-5x+6=0», "
            "«площадь круга радиусом 5»",
    "code": "режим «⌨ Код»: «напиши на python факториал 5», «скетч мигания "
            "светодиодом на пине 7»; вставь свой код в ``` — разберу ошибки",
    "gen": "«статья <тема>» — длинный текст по моим материалам",
    "skill": "«который час», «запомни: …», «вспомни», «статус» и приложения App Lab, "
             "которые можно дописать на Python в разделе App Lab",
    "board": "работаю вместе с платой neuro_12: «тензор», режим «плата»",
}


def self_intro(who=False):
    """«что умеешь» / «кто ты» — честно о себе: зоны и функции реестра."""
    ex = {z: e for z, _d, e in ZONES}
    ex.update(_SELF_EX)
    lines = []
    if who:
        lines.append("Я — «мозг-12», облачный мозг платформы neuro12-cloud. "
                     "Отвечаю сама, без внешних LLM: из базы знаний, из "
                     "интернета (собираю ответ по найденным страницам), "
                     "считаю, пишу код и документы, рисую. Подключаюсь к "
                     "плате neuro_12 и к боту в МАКС.")
    lines.append("Вот что я умею:" if not who else "Что я умею:")
    names = dict(Z, code="Код (ИИ-агент)", skill="Функции из реестра")
    for z in _SELF_ZONES:
        if z in ex:
            lines.append("• %s — %s" % (names.get(z, z), ex[z]))
    try:
        own = [s.get("name") or s.get("id") for s in
               skills.public_list() if s.get("kind") != "builtin"][:6]
        if own:
            lines.append("Свои функции в реестре: %s." % ", ".join(own))
    except Exception:
        pass
    lines.append("Спроси о чём-нибудь или дай задание — например, "
                 "«посчитай 15% от 2400» или «создай документ про …».")
    return "\n".join(lines)

# зоны, чьи ответы имеет смысл удлинять в режиме «длинно» (знание);
# служебные/точечные ответы (статус, математика, словарь, «не знаю»)
# не раздуваем — это было бы нечестно и бесполезно
_ENRICH_ZONES = frozenset([
    Z["kb"], Z["mn"], Z["web"], Z["wiki"], Z["gen"], Z["hypo"],
    Z["mem"], Z["speech"],
])

_MN_AUTOSAVE = 45.0            # сек между сохранениями состояния сети

# v3.15: РЕЖИМ «ДЛИННО» (заявка владельца: «ответы очень короткие,
# сделай, если выбрать „длинно“, минимум 1500 знаков»). Честно:
# добираем только реальными материалами — полные статьи KB по теме и
# текст сети материалов; выдумывать по-прежнему нельзя.
_LONG_MIN = 1500               # знаков — цель ответа при long_mode


# --------------------------- нейросеть материалов ---------------------------
def mn():
    """Ленивая загрузка нейросети материалов облака (mozgnet)."""
    global _MN, _MN_PATH
    if _MN is None:
        import mozgnet
        _MN_PATH = store.p("mozgnet_state.json")
        _MN = mozgnet.MozgNet()
        try:
            _MN.load(_MN_PATH)
        except Exception:
            pass
        _mn_apply_limits()
    return _MN


def _mn_apply_limits():
    """v3.24.2: пределы сети из настроек (net_nodes_max / net_docs_max /
    net_sents_max). Прежний потолок 20000 узлов выглядел как «сеть зависла
    на 20000 узлов»: readings показывали ровно 20000 и не росли. Теперь
    по умолчанию 150000 узлов, меняется из панели и применяется к ЖИВОЙ
    сети при каждом обучении (apply_limits дёшев, без изменений — ничего)."""
    try:
        s = store.settings()
        if _MN is not None:
            _MN.apply_limits(
                node_max=s.get("net_nodes_max"),
                docs_max=s.get("net_docs_max"),
                sents_max=s.get("net_sents_max"))
    except Exception:
        pass


def mn_learn(text, title="", src="материал"):
    """Обучить сеть облака новому материалу (статья/диалог/мысль/файл).
    v3.27: НЕ сохраняет сеть на диск (это делает фон, снимком, вне
    блокировки) и не ждёт занятую сеть: материал встаёт в очередь, фон
    выучит его чуть позже — ответ на вопрос из-за обучения не задержится."""
    if not str(text or "").strip():
        return None
    item = (str(text)[:20000], str(title)[:120], str(src)[:40])
    if not _MN_LOCK.acquire(timeout=0.5):
        if len(_MN_PENDING) < 500:
            _MN_PENDING.append(item)
        return None
    try:
        _mn_apply_limits()     # v3.24.2: настройки панели доходят без рестарта
        r = mn().learn(item[0], title=item[1], src=item[2])
        _MN_DIRTY[0] = True
        return r
    except Exception:
        return None
    finally:
        _MN_LOCK.release()


def _mn_drain(limit=50):
    """Выучить материалы из очереди (фон)."""
    n = 0
    while _MN_PENDING and n < limit:
        if not _MN_LOCK.acquire(timeout=2.0):
            return n
        try:
            text, title, src = _MN_PENDING.pop(0)
            mn().learn(text, title=title, src=src)
            _MN_DIRTY[0] = True
        except Exception:
            pass
        finally:
            _MN_LOCK.release()
        n += 1
    return n


def mn_save(force=False):
    """v3.27: сохранить сеть: снимок под блокировкой (~1 с), запись JSON —
    без блокировки (на полной сети это 15+ с, раньше всё это время облако
    стояло — и так каждые 20 секунд)."""
    if not (force or _MN_DIRTY[0]) or _MN is None:
        return False
    if not _MN_LOCK.acquire(timeout=5.0):
        return False
    try:
        snap = mn().snapshot()
        _MN_DIRTY[0] = False
    except Exception:
        return False
    finally:
        _MN_LOCK.release()
    import mozgnet
    ok = mozgnet.MozgNet.save_snapshot(_MN_PATH, snap)
    _LAST_SAVE[0] = time.time()
    if not ok:
        _MN_DIRTY[0] = True
    elif _BT.get("done_ids") is not None:
        _bt_save()                    # v3.88: ход обучения мозга — вместе с сетью
    return ok


_MN_READ = [{}]


def mn_readings():
    """v3.105: панель не ждёт занятую сеть — последние известные показания
    (раньше /api/status ждал блокировку до 8 с, и при большой сети и частой
    работе с ней панель «переставала грузить информацию облака»)."""
    if not _MN_LOCK.acquire(timeout=0.3):
        return dict(_MN_READ[0], busy=True)
    try:
        _MN_READ[0] = mn().readings()
        return dict(_MN_READ[0])
    except Exception:
        return dict(_MN_READ[0])
    finally:
        _MN_LOCK.release()


def mn_size():
    """Размер сети (узлы, связи, материалы) — для честного «на сколько
    выросла сеть за этот ответ» (v3.9: владелец видит изменения сети).
    v3.27: сеть занята — последний известный размер, без ожидания."""
    if not _MN_LOCK.acquire(timeout=0.3):
        return _MN_SIZE[0]
    try:
        m = mn()
        _MN_SIZE[0] = (len(m.nodes), sum(len(v) for v in m.edges.values()),
                       len(m.docs))
    except Exception:
        pass
    finally:
        _MN_LOCK.release()
    return _MN_SIZE[0]


# ------------------------------ мозг-почта ----------------------------------
def _mailboxes():
    return store.load_json("mailboxes.json", {}) or {}


def mail_deliver(letter):
    """Положить письмо в ящик платы (или в чат-получатель)."""
    to = str(letter.get("to") or "")[:40]
    with store.LOCK:
        mb = _mailboxes()
        box = mb.setdefault(to, [])
        box.append(letter)
        mb[to] = box[-30:]
        store.save_json("mailboxes.json", mb)


def mail_take(board):
    """Забрать письма платы (GET /api/brain/inbox)."""
    with store.LOCK:
        mb = _mailboxes()
        box = mb.get(str(board)[:40]) or []
        if box:
            mb[str(board)[:40]] = []
            store.save_json("mailboxes.json", mb)
        else:
            store.save_json("mailboxes.json", mb)
        return box


def mail_answer(mid, text, frm, zone="", grounded=None, source=""):
    """Плата ответила на письмо (POST /api/brain/answer): доставить в чат.
    v3.29: письмо «единого мозга» — через unify.on_answer (влить в ждущий
    ответ МАКС/API, дописать позже, научиться на ответе с опорой)."""
    with store.LOCK:
        pend = store.load_json("mail_pending.json", {}) or {}
        item = pend.pop(str(mid)[:40], None)
        store.save_json("mail_pending.json", pend)
    if item and item.get("unify"):
        text = str(text or "")[:6000]
        frm = frm or item.get("board") or "плата"   # плата не шлёт своё имя
        if item.get("thought") and not grounded and text.strip():
            try:            # v3.121: несвязная мысль — не ответ (вердикт — плате)
                import coherence
                ok_, _j = coherence.gate(text, board=str(frm)[:40],
                                         where="почта")
                import strict           # v3.122: «Не показывать бред» / «без сети»
                if ok_ and strict.on() and not strict.text_ok(
                        text, item.get("text") or "", 4):
                    strict.block("поздний ответ-мысль платы", text)
                    ok_ = False
                if ok_ and not strict.offline_ok() and not strict.online():
                    ok_ = False
                if not ok_:
                    text = ""
            except Exception:
                pass
        elif text.strip():
            # v3.125: ответ платы «с опорой» тоже проверяется — в мессенджер
            # уходил её связный по виду, но не по теме текст («рассинхрон»
            # бота с платформой): не по теме вопроса или бред — не ответ
            try:
                import strict
                if strict.on():
                    q_ = item.get("text") or ""
                    qs = set(w[:5] for w in re.findall(r"[а-яё]{4,}", q_.lower()))
                    ts = set(w[:5] for w in re.findall(r"[а-яё]{4,}", text.lower()))
                    off_topic = bool(qs) and not (qs & ts)
                    if off_topic or not strict.text_ok(text, "", 4):
                        strict.block("ответ платы (не по теме/несвязный)", text)
                        text = ""
            except Exception:
                pass
        item.update({"answer": text, "zone": zone, "answered": True,
                     "brain": str(frm)[:40], "grounded": bool(grounded),
                     "source": str(source or "")[:120]})
        with store.LOCK:
            res = store.load_json("mail_results.json", {}) or {}
            res[str(mid)[:40]] = item
            store.save_json("mail_results.json", _cap(res, 200))
        import unify
        taken = unify.on_answer(str(mid)[:40], item, text, frm, zone,
                                grounded, source)
        cid = item.get("chat_id")
        if not taken and cid and text:
            chat = store.chat_load(cid)
            if chat:
                chat["messages"].append({
                    "role": "bot", "text": unify.supplement_text(
                        item, late=True), "zone": Z["board"],
                    "brain": str(frm)[:40], "ts": time.time()})
                chat["ts"] = time.time()
                store.chat_save(cid, chat)
        try:
            bump_zone(Z["board"])
        except Exception:
            pass
        return {"ok": True, "delivered": not taken, "unify": True}
    if not item:
        # ответ на урок экскурсии (письмо учителя) — вердикт + журнал
        try:
            import teacher
            if teacher.tour_answer(mid, text, zone):
                bump_zone(zone or Z["board"])
                return {"ok": True, "delivered": False, "tour": True}
        except Exception:
            pass
        return {"ok": True, "delivered": False}   # ответ на просроченное
    text = str(text or "")[:6000]
    cid = item.get("chat_id")
    if cid and text:
        chat = store.chat_load(cid)
        if chat:
            chat["messages"].append({
                "role": "bot", "text": text, "zone": zone or Z["board"],
                "brain": str(frm)[:40], "ts": time.time()})
            chat["ts"] = time.time()
            store.chat_save(cid, chat)
    item["answer"] = text
    item["zone"] = zone
    item["answered"] = True
    with store.LOCK:
        pend = store.load_json("mail_results.json", {}) or {}
        pend[str(mid)[:40]] = item
        store.save_json("mail_results.json", _cap(pend, 200))
    try:
        bump_zone(zone or Z["board"])
    except Exception:
        pass
    return {"ok": True, "delivered": bool(cid and text)}


def _cap(d, n):
    if len(d) > n:
        for k in list(d)[:len(d) - n]:
            d.pop(k, None)
    return d


def mail_result(mid):
    with store.LOCK:
        pend = store.load_json("mail_pending.json", {}) or {}
        if str(mid)[:40] in pend:
            return {"ok": True, "pending": True}
        res = store.load_json("mail_results.json", {}) or {}
        item = res.get(str(mid)[:40])
    if not item:
        return {"ok": True, "pending": False, "expired": True}
    return {"ok": True, "pending": False, "answer": item.get("answer") or "",
            "zone": item.get("zone") or "", "brain": item.get("brain") or "",
            "grounded": item.get("grounded"), "source": item.get("source")}


def ask_board(text, chat_id=None, frm="чат облака", long_mode=True,
              unify=False, deliver=None, thought=False):
    """Отправить вопрос живой плате (маршрут board/auto-добавка).
    v3.29: unify — письмо «единого мозга» (плата v2.41 отвечает с пометкой
    опоры и источника), deliver — куда дописать поздний ответ (unify.py).
    Возврат: {ok, id, brain} | {ok: False, error}"""
    free = store.free_boards()
    if not free:
        return {"ok": False, "error": "нет плат онлайн"}
    b = random.choice(free[:3])
    mid = "m" + os_urandom_hex()
    letter = {"id": mid, "from": str(frm)[:40], "to": b["id"],
              "text": str(text)[:2000], "long": bool(long_mode),
              "ts": time.time()}
    if unify:
        letter["unify"] = True
    if thought:                  # v3.97: ответ — фраза из мысли мозга платы
        letter["thought"] = True
    mail_deliver(letter)
    with store.LOCK:
        pend = store.load_json("mail_pending.json", {}) or {}
        pend[mid] = {"id": mid, "board": b["id"], "chat_id": chat_id,
                     "text": letter["text"], "ts": time.time()}
        if unify:
            pend[mid]["unify"] = True
            pend[mid]["deliver"] = deliver or {}
        if thought:
            pend[mid]["thought"] = True
        store.save_json("mail_pending.json", _cap(pend, 200))
    store.board_seen(b["id"])
    return {"ok": True, "id": mid, "brain": b["id"]}


def os_urandom_hex():
    import os
    return os.urandom(5).hex()


def mail_sweep():
    """Дворник: просроченные письма (плата молчит > board_timeout) —
    честный статус в чат, из ожидания убрать."""
    timeout = float(store.settings().get("board_timeout") or 75) + 30
    now = time.time()
    with store.LOCK:
        pend = store.load_json("mail_pending.json", {}) or {}
        expired = [mid for mid, it in pend.items()
                   if now - float(it.get("ts") or 0) > timeout]
        for mid in expired:
            item = pend.pop(mid)
            cid = item.get("chat_id")
            if cid:
                chat = store.chat_load(cid)
                if chat:
                    chat["messages"].append({
                        "role": "system",
                        "text": "Плата «%s» не ответила за %d c — вопрос "
                                "остался без ответа платы." %
                                (item.get("board", "?"),
                                 int(store.settings().get("board_timeout")
                                     or 75)),
                        "zone": Z["sys"], "ts": time.time()})
                    store.chat_save(cid, chat)
        if expired:
            store.save_json("mail_pending.json", pend)
    return len(expired)


# --------------------------- понимание запроса ------------------------------
# v3.12: авто-приветствие убрано (заготовленных ответов больше нет) —
# «привет» сеть честно собирает словарём, как и всякую фразу.
_MATH_Q = re.compile(r"(посчитай|вычисли|сколько будет)", re.I)
_WRITE_Q = re.compile(r"^\s*(статья|напиши|расскажи подробно|длинно)\s+(.+)$",
                      re.I)
_FIND_Q = re.compile(r"^\s*(что такое|кто такой|кто такая|что за|найди|поиск"
                     r"|погугли|расскажи про|расскажи о)\s+(.+)$", re.I)
# v3.10: «облако ничего не открывает» -> зона «Любопытство»: «открой X» /
# «прочитай X» — мозг открывает статью (KB -> Википедия) и сохраняет её
_OPEN_Q = re.compile(r"^\s*(открой|открыть|прочитай|прочесть)\s+(.+)$", re.I)
# v3.10: любопытство: немедленное чтение и управление
_LEARN_Q = re.compile(r"^\s*(узнай|узнать|изучи тему|поизучай)\s+(?:про\s+)?(.+)$",
                      re.I)
_CURIOSITY_Q = re.compile(r"^\s*(включи|выключи)?\s*любопытство", re.I)
_THINK_Q = re.compile(r"^\s*(мысль|подумай|что думаешь)\s*(.*)$", re.I)
_STATUS_Q = re.compile(r"^\s*(статус|диагностика|как дела у облака)\s*[\?.!]*$",
                       re.I)
# зона «Словарь»: проверка слова по словарям облака (v3.3 — зона подключена)
_DICT_Q = re.compile(r"^\s*(?:что значит|что означает|значение слова"
                     r"|что такое слово|проверь слово|есть ли слово)"
                     r"\s+(.+)$", re.I)
# зона «Память»: тематический вопрос к заметкам (раньше уходил в «Навык»)
_MEM_Q = re.compile(r"^\s*что\s+(?:ты\s+)?помнишь\s+про\s+(.+)$", re.I)
# v3.9: зона «Мысли платы» — генератор на том, что думает плата
_BOARDMIND_Q = re.compile(r"мысли плат|что думает плат|о ч[её]м думает плат|"
                          r"мысль платы|что у плат[ы]? на уме", re.I)
# v3.9: зона «Код» — вопросы про программный код
_CODE_HINT = re.compile(r"main\.py|sketch|скетч|\.ino\b|функци[июяэ]|def ",
                        re.I)
_CODE_Q = re.compile(r"что делает|как работает|где определен|изучи код|"
                     r"прочитай код|читай код|про функцию", re.I)
_POS_NAMES = {"nouns": "существительное", "verbs": "глагол",
              "adjs": "прилагательное"}
# v3.11: вердикты владельца по гипотезам (зона «Предположение»)
_VER_Q = re.compile(r"^\s*(верно|неверно|не верно|угадал|угадала|не угадал|"
                    r"не угадала|правильно|неправильно|точно|ошибся|"
                    r"ошиблась|мимо|попал)\s*[!.?]*$", re.I)
# v3.13: вопрос-формат — триггер интернет-источников (как у Алисы)
_Q_LIKE = re.compile(r"^\s*(что|кто|где|когда|почему|зачем|как|сколько|"
                     r"какой|какая|какие|каких|кому|чем|откуда|расскажи|"
                     r"покажи|найди|опиши|объясни)\b", re.I)
# v3.16: ЗОНА ВООБРАЖЕНИЯ — фантазии мини-LLM («нарисуй/перерисуй/график»
# ловит imagine.route — он вызывается раньше)
_IMAGINE_Q = re.compile(r"^\s*(представь|вообрази|пофантазируй|фантазируй|"
                        r"придумай|помечтай)\s*(.*)$", re.I)
# v3.18: САМОКОД — «напиши себе функцию …»: мозг пишет функцию из заготовок
# autocode, проверяет тестами в песочнице и публикует в реестр (зона «Код»).
# Ставим РАНЬШЕ _WRITE_Q: «напиши функцию …» — не сочинение.
_AUTOCODE_Q = re.compile(
    r"^\s*(?:напиши|создай|сочини|сделай|добавь)\s+(?:себе\s+|для\s+себя\s+)?"
    r"(?:новую\s+)?функцию\s+(.+)$", re.I)
# v3.18: «что нового ты узнала» — сводка знаний (любопытство, самокод,
# воображение, база): честный отчёт о собственном росте.
_NEWS_Q = re.compile(
    r"^\s*(что\s+нового|что\s+ты\s+узнал[аи]?|чем\s+новеньк\w*|"
    r"сводка\s+знаний|что\s+прочитал[аи]?|что\s+выучил[аи]?|"
    r"новости\s+знаний)\b", re.I)
# v3.26: ПАМЯТЬ ДИАЛОГА — признаки продолжения темы: в реплике есть отсылка
# к сказанному раньше (местоимения «он/она/это/его/…», «ещё», «подробнее»,
# «дальше»…). Тогда реплика склеивается с темой диалога даже если слов
# больше трёх и даже если начинается с «когда/где/сколько…»:
# «а когда он основан?» после «что ты знаешь про Бештау?» ищется по теме
# диалога, а не как отдельный вопрос про «основан».
_FOLLOW_RE = re.compile(
    r"\b(ещ[её]|тоже|также|снова|опять|точнее|подробнее|подробней|"
    r"продолжай|дальше|"
    r"он|она|оно|они|его|него|е[её]|не[её]|нему|ему|ей|ней|их|них|ним|ними|"
    r"ими|"
    r"н[её]м|"
    r"этот|эта|это|эти|этого|этой|этому|этих|этим|"
    r"тот|та|те|того|той|тому|тех|"
    r"там|туда|оттуда|выше|упомянут\w*|сказанн\w*)\b", re.I)
# v3.27: порог «среднего» совпадения с базой знаний (полное — 2.0)
_KB_MID = 1.3
# v3.27: реплики без темы — им не нужен ни поиск в базе, ни интернет
_SMALLTALK = frozenset("""дела делишки поживаешь жизнь настроение спасибо
благодарю пока здравствуй здравствуйте приветик хай hello hi доброе добрый
утро день вечер ночи хорошо отлично нормально ок окей ладно понятно ясно
круто класс супер молодец умница ага угу да нет
делаешь делаете занимаешься занимаетесь поделываешь занята занят слышишь
здесь тут""".split())
# v3.33: «что делаешь?», «чем занимаешься?» — вопрос к самому боту о его
# занятии, а не поиск в базе/интернете (раньше «делаешь» находило
# «что делается на небе…» и в МАКС приходила статья про богиню Фаму)
_DOING_Q = re.compile(
    r"^\s*(?:а\s+)?(?:ну\s+)?(?:что|чем)\s+(?:сейчас\s+|ты\s+|вы\s+)*"
    r"(?:делаешь|делаете|занимаешься|занимаетесь|поделываешь|занята|занят)"
    r"(?:\s+сейчас)?\s*[\?.!]*\s*$", re.I)


_FORMULA_ASK = re.compile(r"формул|уравнени|выражени\w* для|как (?:вычисл|рассчит|посчит)", re.I)
_HAS_FORMULA = re.compile(r"\$[^$\n]{2,}\$|\[ФОРМУЛА\]|[A-Za-zА-Яа-яα-ωΑ-Ω]\s*=\s*[^,.\s]")


def _formula_from_web(text, kb_reply, long_mode=False, no_web=False):
    """v3.108: спросили формулу, а статья базы её не содержит (раньше база
    ВЫБРАСЫВАЛА формулы, и сохранённая из интернета статья «перебивала»
    интернет: формула выводилась один раз, дальше — нет). Тогда — интернет;
    если там формула есть, ответ из интернета, иначе — статья базы."""
    if no_web or not _FORMULA_ASK.search(str(text)) or \
            _HAS_FORMULA.search(str(kb_reply or "")) or \
            not store.settings().get("web_on"):
        return None
    try:
        wr, wm = webfetch.alice(text, long_mode=long_mode)
    except Exception:
        return None
    if wr and _HAS_FORMULA.search(str(wr)) and not str(wr).startswith(
            "Именно про"):
        srcs = (wm or {}).get("sources") or []
        try:
            _web_to_kb(text, wr, srcs)
        except Exception:
            pass
        return wr, Z["web"], {"sources": srcs, "web": (wm or {}).get("mode")
                              or "synth", "formula_refetch": True}
    return None


def _web_to_kb(q, answer, sources):
    """v3.30: ОБУЧЕНИЕ КОНТЕКСТУ ПО ИНТЕРНЕТУ — ответ, собранный со страниц,
    становится статьёй базы знаний (одна статья на вопрос: повтор обновляет
    её, а не плодит дубли) вместе со списком источников. Следующий похожий
    вопрос найдёт ответ в базе без интернета; 👎 по-прежнему отключает его
    (qalearn). Настройка web_learn_kb."""
    if not store.settings().get("web_learn_kb", True):
        return
    if str(answer or "").startswith("Именно про"):
        return                       # без конкретики знанием не делаем
    import hashlib
    key = " ".join(sorted(set(kb._stems(q)) - kb._STOP_STEMS))
    if not key:
        return
    # v3.74: в ответе должны быть слова вопроса, иначе это чужая статья
    qs = set(kb._stems(q)) - kb._STOP_STEMS
    ts = set(kb._stems(str(answer)))
    if qs and len(qs & ts) / float(len(qs)) < 0.5:
        return
    did = "web_" + hashlib.md5(key.encode("utf-8")).hexdigest()[:14]
    # источники — в поле source (до 120 знаков), не в тексте: иначе ссылки
    # попадали бы в ответы базы знаний строками «• сайт — https://…»
    names = ", ".join(str(s.get("name") or "") for s in (sources or [])[:4])
    try:
        kb.add_article("Из интернета: %s" % str(q)[:150], str(answer),
                       source=("интернет: выучено по вопросу (%s)"
                               % names)[:120], did=did)
    except Exception:
        pass


def _web_cmd(text, long_mode=False):
    """v3.27: спросили КОМАНДУ, а в статье базы её нет (только описание) —
    ищем в интернете. Ответ берём, только если в нём ЕСТЬ команда; иначе
    None — и бот честно скажет, что команды в базе нет.
    Возврат: (ответ, info) | None."""
    if not store.settings().get("web_on"):
        return None
    try:
        wr, wm = webfetch.alice(text, long_mode=long_mode)
    except Exception:
        return None
    if not wr or not any(kb._is_cmd(ln) or kb._HAS_CMD_RE.search(ln)
                         for ln in str(wr).split("\n")):
        return None
    mn_learn("Вопрос: %s\nОтвет: %s" % (text, wr[:1500]), title=text[:60],
             src="интернет")
    return wr, {"sources": (wm or {}).get("sources") or [],
                "web": "cmd"}


def _has_topic(text):
    """Есть ли в реплике тема (содержательные слова), а не только «привет,
    как дела?». По стемам базы знаний — те же стоп-слова, что у поиска."""
    try:
        small = {kb._stem_c(kb._norm_text(w)) for w in _SMALLTALK}
        return any(s not in kb._STOP_STEMS and s not in small and
                   len(s) >= 2 for s in kb._stems(text))
    except Exception:
        return True


# v3.26: «о чём мы говорили?», «что ты мне отвечала?» — честный пересказ
# последних реплик диалога из ctx (историю ведёт вызывающий: чат облака
# или бот в МАКС; у Макс-бота она в data/maxbot.json).
_RECALL_Q = re.compile(
    r"о ч[её]м\s+(?:мы\s+|ты\s+|мы\s+с\s+тобой\s+)?(?:только\s+что\s+)?"
    r"(говорил\w*|разговаривал\w*|беседовал\w*|спрашивал\w*)|"
    r"что\s+(?:ты\s+)?(?:мне\s+)?(говорил\w*|рассказывал\w*|отвечал\w*)|"
    r"что\s+я\s+(спрашивал\w*|спросил\w*|просил\w*)|"
    r"(?:повтори|напомни)\s+(?:что\s+)?(?:ты\s+)?"
    r"(?:говорил\w*|отвечал\w*|рассказывал\w*|наш\w*\s+разговор|"
    r"диалог|последн\w+)|"
    r"(?:наш|текущий)\s+(?:диалог|разговор)|наши\s+реплики", re.I)
# v3.17: МЫСЛИ ЗОН ОБЛАКА -> ПИН D6 ПЛАТЫ («как уже реализовано» для фраз
# самого мозга). Каждый ответ зоны («Зоны мысли») после think() кладётся в
# ящик d6box.json; плата забирает его опросом /api/brain/d6 и проигрывает
# PWM-байтами на D6 через RPC d6seq (_gen_d6_seq) — тот же физический канал,
# что у её собственных фраз. Мысли О ПЛАТЕ (зоны «Мысли платы»/«Плата-12»)
# обратно на плату не идут — это была бы петля. SVG-блоки картинок/формул
# ([КАРТИНКА]/[ФОРМУЛА]) на D6 не играются — вырезаются, остаётся текст.
_D6_SVG = re.compile(r"\[(ФОРМУЛА|КАРТИНКА)\][\s\S]*?\[/\1\]")
_D6_MAX = 240                  # знаков мысли на D6 (плата сама режет до 96 букв)
_D6_SKIP_ZONES = frozenset((Z["mind"], Z["board"]))   # мысли О плате — не назад


def _d6_clean(text):
    """Ответ зоны -> компактная мысль для D6: без SVG-блоков ([КАРТИНКА]/
    [ФОРМУЛА]), ссылок и разметки markdown; одна строка, хвост — по границе
    слова. Плата сыграет только буквы русского алфавита (скетч RU33) —
    латиница/цифры честно пропускаются её _gen_d6_seq, как и для своих фраз."""
    t = _D6_SVG.sub(" ", str(text or ""))
    t = re.sub(r"\[ИЛЛЮСТРАЦИЯ[^\]]*\][\s\S]*?\[/ИЛЛЮСТРАЦИЯ\]", " ", t)
    t = re.sub(r"https?://\S+", " ", t)
    t = re.sub(r"[#*`>_\[\]|]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    if len(t) > _D6_MAX:
        cut = t[:_D6_MAX]
        sp = cut.rfind(" ")
        if sp > _D6_MAX // 2:
            cut = cut[:sp]
        t = cut.rstrip(" ,.;:-") + "…"
    return t


def _d6_send(text, zone):
    """Мысль зоны — всем зарегистрированным платам (ящик d6box). Выключается
    настройкой d6_on; мысли о плате не возвращаются на плату (петля)."""
    if zone in _D6_SKIP_ZONES:
        return
    if not store.settings().get("d6_on"):
        return
    t = _d6_clean(text)
    if len(t) < 2:
        return
    boards = list(store.boards().keys())

    def put(dec):
        sg = int((dec or {}).get("sign") or 0)
        by = str((dec or {}).get("by") or "")
        for bid in boards:
            try:
                store.d6_add(bid, t, zone, sg, by)
            except Exception:
                pass
        if boards:
            try:
                store.log("D6", "мысль зоны «%s» на D6 (%d плат%s): %s" %
                          (zone, len(boards),
                           (", знак %s от LLM" % ("−" if sg < 0 else "+"))
                           if sg else "", t[:60]))
            except Exception:
                pass
    # v3.51: знак частоты решает своя LLM — в фоне, ответы не задерживает
    try:
        import negfreq
        if negfreq.enabled():
            negfreq.decide_async(t, put)
            return
    except Exception:
        pass
    put(None)


_TENSOR_Q = re.compile(r"^\s*тензор\w*(?:\s+(обучи\w*|переобучи\w*))?\s*"
                       r"(?:отч[её]т|статус)?\s*[.!?]*$", re.I)


def _tensor_cmd(train=False, chat_id=None, sender="владелец"):
    """v3.27: «тензор» / «тензор обучить» — тензорная сеть платы v2.39.
    Возврат (текст, доп-инфо). Мост до платы не достаёт (сервер в другой
    сети) — команда уходит плате МОЗГ-ПОЧТОЙ, ответ придёт в чат."""
    import bridge
    import tensorhelp
    st = bridge.status()
    th = tensorhelp.status()
    if train:
        if st.get("online"):
            r = bridge.send("тензор обучить", user="облако", wait=30.0)
            if r.get("ok"):
                return ("Плата получила «тензор обучить». Её ответ: %s\n\n"
                        "Тяжёлую задачу посчитает облако (задач посчитано: "
                        "%d)." % (r.get("answer") or r.get("note") or
                                  "пока молчит",
                                  (th.get("done") or 0) +
                                  (th.get("sandbox_runs") or 0))), {}
        b = ask_board("тензор обучить", chat_id=chat_id, frm=sender,
                      long_mode=False)
        if b.get("ok"):
            return ("Мост до платы не достаёт, поэтому «тензор обучить» "
                    "ушло плате «%s» мозг-почтой — её ответ придёт сюда "
                    "отдельным сообщением." % b["brain"]), \
                {"pending_mid": b["id"], "brain": b["brain"]}
        return ("Не смогла передать плате «тензор обучить»: мост не видит "
                "плату (%s), а по мозг-почте свободных плат нет."
                % (st.get("last_err") or "нет связи")), {}
    tn = (st.get("state") or {}).get("tensor")
    lines = []
    if not st.get("online"):
        mb = st.get("mail_boards") or []
        lines.append("Мост не видит плату напрямую (%s), поэтому её "
                     "«тензор H×76» отсюда не прочитать%s." % (
                         st.get("last_err") or "нет связи",
                         ("; при этом плата «%s» в сети по мозг-почте — "
                          "спроси её «тензор» в чате платы или через режим "
                          "«плата»" % mb[0]["board"]) if mb else ""))
    elif not tn:
        lines.append("Плата на связи, но тензорной сети в её /api/state нет "
                     "— нужна прошивка neuro_12 v2.39 или новее.")
    else:
        acc = tn.get("acc")
        task = tn.get("task") or {}
        where = {"server": "сервер (песочница облака)",
                 "browser": "компьютер (облако или браузер :7000)",
                 "local": "сама плата"}.get(task.get("where"), "—")
        lines.append("Тензорная сеть платы: тензор %s×%s — %s нейронов "
                     "скрытого слоя (границы %s..%s, цель по частоте "
                     "генератора %s Гц: %s), зон %s, параметров %s." % (
                         tn.get("H"), tn.get("IN"), tn.get("H"),
                         tn.get("h_min"), tn.get("h_max"),
                         tn.get("hz_ema"), tn.get("h_target"), tn.get("Z"),
                         tn.get("params")))
        lines.append("Примеров %s (новых %s), обучений %s, точность %s; "
                     "последняя задача — %s, считал(а): %s." % (
                         tn.get("samples"), tn.get("new"), tn.get("trains"),
                         ("%d%%" % round(acc * 100)) if isinstance(
                             acc, (int, float)) else "—",
                         task.get("kind") or "—", where))
    if th.get("on"):
        last = th.get("last") or {}
        lines.append("Облако-вычислитель: %s, скорость ~%.1fM оп/с, задач "
                     "посчитано %d, сбоев %d%s." % (
                         "на связи с платой" if th.get("alive") else
                         "ждёт плату", (th.get("bench") or 0) / 1e6,
                         th.get("done") or 0, th.get("fails") or 0,
                         ("; последняя: H=%s, %s примеров, %s эпох, "
                          "точность %d%%, %d мс" % (
                              last.get("H"), last.get("n"),
                              last.get("epochs"),
                              round((last.get("acc") or 0) * 100),
                              last.get("ms") or 0)) if last else ""))
        if th.get("last_err"):
            lines.append("Последняя заминка вычислителя: %s." % th["last_err"])
    else:
        lines.append("Облако-вычислитель выключен (настройка tensor_help).")
    if th.get("sandbox_runs"):
        lines.append("Песочница облака посчитала задач тензора по просьбе "
                     "платы (POST /api/sandbox/run): %d, последняя — %s."
                     % (th["sandbox_runs"], time.strftime(
                         "%d.%m %H:%M", time.localtime(
                             th.get("sandbox_ts") or 0))))
    lines.append("«тензор обучить» — попросить плату переобучить сеть.")
    return "\n".join(lines), {}


def _make_document(text, kind, ctx, long_mode=False):
    """v3.27: собрать документ (docgen) и вернуть ответ со ссылкой."""
    import docgen
    fmt = docgen.pick_format(text)
    hist = [m for m in (ctx or []) if isinstance(m, dict) and
            str(m.get("text") or "").strip()]
    if hist and hist[-1].get("role") == "user" and \
            str(hist[-1].get("text") or "").strip() == text.strip():
        hist = hist[:-1]                       # текущая просьба — не текст
    if kind == "save":
        m = docgen.SAVE_Q.match(text)
        what = (m.group("what") if m else "").lower()
        if re.search(r"диалог|разговор|переписк", what):
            if not hist:
                return ("Сохранять пока нечего: в этом диалоге ещё не было "
                        "реплик."), Z["doc"], {}
            body = "\n\n".join("%s: %s" % ("Вы" if x.get("role") == "user"
                                           else "Мозг-12",
                                           docgen.clean_body(x.get("text")))
                               for x in hist)
            title = "Диалог от %s" % time.strftime("%d.%m.%Y %H:%M")
        else:
            bot = [x for x in hist if x.get("role") != "user"]
            if not bot:
                return ("Сохранять пока нечего — сначала спроси о чём-нибудь, "
                        "а потом скажи «сохрани ответ в документ»."), \
                    Z["doc"], {}
            body = str(bot[-1].get("text") or "")
            qs = [x for x in hist if x.get("role") == "user"]
            title = docgen.title_from_question(
                str(qs[-1].get("text") or "") if qs else "") or \
                "Ответ мозга-12"
    else:
        topic = docgen.topic_of(text)
        if not topic and hist:
            qs = [x for x in hist if x.get("role") == "user"]
            topic = str(qs[-1].get("text") or "").strip(" ?!.") if qs else ""
        if not topic:
            return ("О чём сделать документ? Напиши, например: «создай "
                    "документ про перенос записи пациента» или «сохрани "
                    "ответ в docx»."), Z["doc"], {}
        body, zone, _info = think(topic, long_mode=True, route="cloud")
        if zone == Z["sys"] and str(body).startswith("Не знаю"):
            return ("Документ по теме «%s» не создала: материалов нет ни в "
                    "базе знаний, ни в интернете — писать не из чего "
                    "(выдумывать не стану)." % topic), Z["doc"], {}
        title = topic[:1].upper() + topic[1:]
    res = docgen.make_file(title, body, fmt)
    return docgen.reply_for(res, title, body), Z["doc"], \
        {"doc": res.get("url"), "doc_fmt": res.get("fmt")}


def _ru_plural(n, one, few, many):
    """Русские формы «1 реплика / 2 реплики / 5 реплик»."""
    n = abs(int(n or 0)) % 100
    if 11 <= n <= 14:
        return many
    d = n % 10
    if d == 1:
        return one
    if 2 <= d <= 4:
        return few
    return many


# v3.28: исправление ответа человеком — «правильный ответ: …» сразу после
# моего ответа, или целиком «вопрос: … ответ: …»
_CORRECT_Q = re.compile(
    r"^\s*(?:правильн(?:ый|о)\s+ответ|верный\s+ответ|правильно\s+так|"
    r"запомни\s+ответ|на\s+самом\s+деле)\s*[:\-—–]\s*(.{2,})$", re.I | re.S)
_QA_PAIR_Q = re.compile(r"^\s*вопрос\s*[:\-—–]\s*(.{3,}?)\s*\n?\s*(?:правильный"
                        r"\s+)?ответ\s*[:\-—–]\s*(.{2,})$", re.I | re.S)


def _qa_pre(text, ctx, sender, route):
    """Раньше всего конвейера: исправление ответа и ПОДТВЕРЖДЁННАЯ память
    ответов (qalearn). Возврат (ответ, зона, info) | None."""
    import qalearn
    t = str(text or "").strip()
    trusted = str(sender or "") == "владелец"
    q = a = ""
    m = _QA_PAIR_Q.match(t)
    if m:
        q, a = m.group(1).strip(), m.group(2).strip()
    else:
        m = _CORRECT_Q.match(t)
        if m:
            a = m.group(1).strip()
            hist = [x for x in (ctx or []) if isinstance(x, dict)]
            if hist and hist[-1].get("role") == "user" and \
                    str(hist[-1].get("text") or "").strip() == t:
                hist = hist[:-1]
            q = next((str(x.get("text") or "") for x in reversed(hist)
                      if x.get("role") == "user" and
                      not _CORRECT_Q.match(str(x.get("text") or ""))), "")
            if not q:
                return ("Не вижу, к какому вопросу этот ответ. Напиши так: "
                        "«вопрос: … ответ: …» — и я запомню."), Z["qa"], {}
    if q and a:
        r = qalearn.correct(q, a, who=sender, trusted=trusted)
        if not r.get("ok"):
            return ("Не смогла запомнить: %s." % r.get("error")), Z["qa"], {}
        if trusted:
            return ("Запомнила. На вопрос «%s» теперь отвечаю: «%s». Это "
                    "подтверждённое знание: оно в базе знаний и в сети "
                    "материалов." % (q[:120], a[:300])), Z["qa"], \
                {"qa": "corrected"}
        return ("Спасибо! Записала исправление к вопросу «%s». Знанием оно "
                "станет после проверки владельцем — так честнее, чем верить "
                "на слово." % q[:120]), Z["qa"], {"qa": "suggested"}
    if route == "board":
        return None
    p = qalearn.recall(t)
    if p:
        try:                              # v3.96: реплика — ответ без пометок
            import smalltalk
            if smalltalk.kind_of(t) or all(s.startswith("~")
                                           for s in qalearn._stems(t)):
                return p["a"], Z["talk"], {"qa": "recall", "smalltalk": True}
        except Exception:
            pass
        by = "исправление владельца" if p.get("src") == \
            "исправление владельца" else "👍 %d" % int(p.get("good") or 0)
        return (p["a"] + "\n\n_Помню ответ на этот вопрос — он подтверждён "
                "(%s). Если устарел, отметь 👎 или напиши «правильный ответ: "
                "…»._" % by), Z["qa"], {"qa": "recall", "sim": p.get("sim")}
    return None


def _brain_only():
    """v3.97: галочка «Отвечает только мозг». v3.99: режима больше нет —
    мозг отвечает сам ВМЕСТЕ с памятью и своей LLM (_mem_brain)."""
    return False


def _brain_only_answer(text, chat_id=None, sender="", deliver=None):
    """Письмо читает мозг neuro-12 (плата), ответ — фраза из его мысли;
    оформление — тот же шаблон (Приветствие / Ответ / подпись)."""
    import unify
    import llmreply
    # v3.98: СНАЧАЛА — прямой вопрос мозгу по мосту (HTTP на плату, ответ
    # сразу); мозг-почта — только запасной путь
    import bridge
    dr = bridge.think(text)
    if dr.get("ok"):
        if dr.get("answer"):
            return llmreply.wrap_brain(
                text, dr["answer"], board=dr.get("board"), sender=sender,
                chat_id=chat_id or (deliver or {}).get("chat_id"),
                thought=True), Z["board"], {
                    "brain_only": True, "brain": dr.get("board"),
                    "direct": True}
        return ("Мозг neuro-12 «%s» прочитал сообщение, но мысль не "
                "собралась: %s." % (dr.get("board"), dr.get("why") or
                                    "мало связей в его словаре")), \
            Z["board"], {"brain_only": True, "direct": True}
    direct_err = dr.get("error") or ""
    if not store.free_boards():
        return ("Мозг neuro-12 не отвечает. Прямой путь: %s.%s Мозг-почта: "
                "плата не числится на связи. Режим «отвечает только мозг» "
                "выключается в Настройки → Общие." % (
                    direct_err, (" Что сделать: %s." % str(dr.get("fix")).rstrip(". "))
                    if dr.get("fix") else "")), Z["board"], \
            {"brain_only": True}
    dv = dict(deliver or {})
    if not dv and chat_id:
        dv = {"kind": "chat", "chat_id": chat_id}
    if not dv:
        dv = {"kind": "sync"}
    dv.setdefault("name", sender)
    try:
        wait = float(store.settings().get("brain_only_wait", 40) or 40)
    except (TypeError, ValueError):
        wait = 40.0
    res = unify.ask(text, dv, wait=wait, thought=True)
    if not res:
        return ("Мозг neuro-12 не принял письмо (единый мозг выключен или "
                "плата занята)."), Z["board"], {"brain_only": True}
    if res.get("answer") is not None and "pending" not in res:
        res = dict(res, thought=True, q=text, deliver=dv)
        return unify.supplement_text(res), Z["board"], {
            "brain_only": True, "brain": res.get("brain")}
    info = {"brain_only": True, "brain": res.get("brain")}
    if dv.get("kind") == "chat":
        info["pending_mid"] = res["pending"]
    return ("Мозг neuro-12 «%s» читает письмо и думает — ответ придёт "
            "следующим сообщением." % res.get("brain")), Z["board"], info


def _core():
    """v3.90: «ответы — мозг neuro-12 + своя LLM» (answer_core): сеть
    материалов и догадки (Предположение) ответами не выдаются; всё, что не
    из базы знаний / интернета / навыков / расчёта, — к плате neuro-12."""
    return True                 # v3.99: единственный режим ответов


_MN_EMPTY = ("Материалов по теме пока нет", "не нашлось ничего",
             "годных предложений мало", "В моих материалах не нашлось")


def _sent(t):
    t = re.sub(r"\s+", " ", str(t or "")).strip()
    if t and not re.search(r"[.!?…»)]$", t):
        t += "."
    return t


def _formula_query(text):
    """v3.106: «напиши формулу яркости квазара» -> «формула яркости квазара»
    (для поиска в базе, интернете и памяти — без слов-команд)."""
    t = re.sub(r"^\s*(?:пожалуйста[, ]*)?(?:напиши|составь|запиши|выведи|дай|"
               r"покажи|сделай|сформулируй|создай|скажи|какая|какой)?\s*"
               r"(?:мне\s+)?(?:формул\w*|уравнен\w*)\s*", "", str(text),
               flags=re.I).strip(" ?.!")
    return ("формула " + t) if t else str(text)


def _short_err(e):
    """Причина простоя интернета — одной короткой фразой (без простыни)."""
    e = re.sub(r"\s+", " ", str(e or "")).strip()
    e = re.split(r"(?<=[.;])\s", e)[0].rstrip(".;")
    return e[:140]


def _thought_on_topic(q, thought):
    """Мысль мозга касается вопроса: общая смысловая основа слова."""
    try:
        sq = {x for x in kb._stems(q) if x not in kb._STOP_STEMS}
        st = set(kb._stems(thought))
        return bool(sq & st) or not sq
    except Exception:
        return True


_LAST_UX = [None, []]          # v3.131: понятое для «/почему» (последний вопрос)


def _honest():
    """v3.129: ЧЕСТНЫЙ ОТВЕТ (по умолчанию ВКЛ.): ответ — только из того, что
    можно проверить (база знаний, интернет с источниками, расчёт, код,
    камера/микрофон, подтверждённая память диалогов, заметки владельца).
    Мысли плат, их «память», генератор слов и фантазии своей LLM ответом
    не выдаются; плату по ходу ответа не ждём (это и было «долго»)."""
    return bool(store.settings().get("answer_honest", True))


def _mem_brain(text, q_text="", long_mode=False, sender="", chat_id=None,
               talk=False, route="cloud"):
    """v3.99: СТАТЬИ В БАЗЕ НЕТ — мозг отвечает САМ и С ОПОРОЙ НА ПАМЯТЬ.
    Память: сеть материалов (всё прочитанное: статьи, файлы, диалоги, мысли
    плат), подтверждённая память диалогов, заметки владельца, память платы
    (её файлы, база на плате, воспоминания — плата v2.59). Сам: мысль мозга
    neuro-12 (прямой вопрос по мосту) или речь мозга облака из словаря.
    Своя LLM переписывает память и дополняет мысль (llmreply.mem_brain).
    -> (ответ, зона, info) | None — ни памяти, ни мысли."""
    q_text = q_text or text
    if _honest():
        return _mem_honest(text, q_text, long_mode, sender, chat_id, talk)
    mem = []
    if not talk:
        try:                                   # 1) сеть материалов
            if _MN is not None:
                with _MN_LOCK:
                    out = mn().write(q_text, words=90 if not long_mode
                                     else 340)
                if out and not any(x in out for x in _MN_EMPTY):
                    mem.append((out, "сеть материалов"))
        except Exception:
            pass
        try:        # 3) похожая статья базы, ассоциации сети, заметки
            import hypo
            have = " ".join(t for t, _s in mem)
            for t, src in hypo.evidence(q_text):
                if t[:60] not in have:
                    mem.append((t, src))
        except Exception:
            pass
    try:                                       # 2) память диалогов
        import qalearn
        for p in qalearn.related(q_text, k=2):
            a = str(p.get("a") or "").strip()
            if a:
                mem.append((_sent(a[:600]) if talk else
                            "%s — %s" % (str(p.get("q") or "").strip(" ?"),
                                         _sent(a[:600])),
                            "память диалогов"))
    except Exception:
        pass
    if not talk:     # v3.102: свежие мысли плат по теме (связь мозгов)
        try:
            import boardlink
            import llmreply as _lr
            seen_ = {t[:40] for t, _s in mem}
            for bid, t in boardlink.relevant(q_text, 2) + \
                    _lr.board_thoughts(q_text, 2):
                t = re.sub(r"^\W*🧠\s*«|»\s*$", "", str(t)).strip()
                try:        # v3.125: мысль платы — память, только если связна
                    import strict as _sx_
                    if _sx_.on() and not _sx_.text_ok(t, "", 5):
                        continue
                except Exception:
                    pass
                if len(t) >= 20 and t[:40] not in seen_:
                    seen_.add(t[:40])
                    mem.append((_sent(t), "мысль платы «%s»" % bid))
        except Exception:
            pass
    # 4) мозг neuro-12: прямой вопрос по мосту — его мысль и его память.
    #    Вопрос пришёл С ПЛАТЫ — её не спрашиваем (иначе круг).
    board, thought, thought_by, b_info = "", "", "", {}
    if route != "board" and not str(sender or "").startswith("плата"):
        try:
            import bridge
            dr = bridge.think(text, timeout=12.0)
            if dr.get("ok"):
                board = dr.get("board") or "плата"
                thought = dr.get("thought") or dr.get("answer") or ""
                for m in dr.get("memory") or []:
                    try:    # v3.125: память платы — только связная и по теме
                        import strict as _sx2
                        if _sx2.on() and not _sx2.text_ok(m["text"], "", 5):
                            continue
                    except Exception:
                        pass
                    mem.append((m["text"], "память платы: %s" % m["src"]))
                b_info = {"board": board, "ms": dr.get("ms")}
            else:
                b_info = {"error": dr.get("error")}
        except Exception as e:
            b_info = {"error": str(e)[:120]}
    if not thought:                            # 5) мысль мозга облака
        try:
            import wordmind
            thought, _si = wordmind.compose(q_text)
            thought_by = "мозг облака"
        except Exception:
            thought = ""
    # мысль не по теме вопроса («вопрос ответ мысль сеть…» на вопрос про
    # квазары) — не ответ: в разговоре («привет») тема не нужна
    if thought and not talk and not _thought_on_topic(q_text, thought):
        thought = ""
    if thought:             # v3.121: «салат из слов» — не ответ
        try:
            import coherence
            ok_, _j = coherence.gate(thought, board=board, where="ответ")
            if not ok_:
                thought = ""
        except Exception:
            pass
    if not mem and not thought:
        return None
    try:
        import llmreply
        reply, li = llmreply.mem_brain(text, mem, thought, board=board,
                                       sender=sender, chat_id=chat_id,
                                       talk=talk, thought_by=thought_by)
    except Exception as e:
        store.log("ПАМЯТЬ+МОЗГ", "шаблон не собрался: %s" % e)
        reply, li = "", None
    if not reply:
        return None
    info = dict(li or {}, llm=True, brain_asked=b_info)
    if mem and not talk:
        try:            # вопрос — материал сети (ответ — после 👍 человека)
            mn_learn(text, title=text[:60], src="вопрос")
        except Exception:
            pass
    return reply, (Z["membrain"] if mem else
                   (Z["board"] if board else Z["speech"])), info


_GENERIC_W = set("""такое такой такая такие что кто как какой какая какие каков
где когда почему зачем сколько работает работают работать делает делают
является являются был была были было есть будет можно нужно надо очень
расскажи скажи объясни опиши покажи знаешь подскажи пожалуйста про об для
этот эта это эти того тому всего всех свой своя свои мне тебе меня тебя
вообще просто сейчас потом""".split())


def _topic_stems(q):
    ws = re.findall(r"[а-яёa-z0-9]{4,}", str(q or "").lower().replace("ё", "е"))
    return [w[:5] for w in ws if w not in _GENERIC_W]


def _covers_topic(q, text):
    """v3.129: память — ответ, только если в ней есть ВСЕ слова темы вопроса
    («показатель адиабаты» — и «показатель», и «адиабата»), а не одно общее
    («работает» в статье про речь на вопрос про фотосинтез)."""
    qs = _topic_stems(q)
    if not qs:
        return False
    ts = set(w[:5] for w in re.findall(r"[а-яёa-z0-9]{4,}",
                                       str(text or "").lower().replace("ё", "е")))
    hit = sum(1 for w in qs if w in ts)
    return hit == len(qs) if len(qs) <= 3 else hit >= 0.75 * len(qs)


def _mem_honest(text, q_text, long_mode=False, sender="", chat_id=None,
                talk=False):
    """v3.129: память для ответа — только проверяемая: подтверждённые пары
    «вопрос — ответ» (👍 / «правильный ответ: …»), предложения похожей
    статьи базы, прочитанные материалы сети (без мыслей плат и фантазий),
    заметки владельца. Без мысли платы, без генератора слов, без «LLM от
    себя». Пусто — None (дальше интернет или честное «не знаю»)."""
    if talk:
        return None
    mem = []
    try:
        import qalearn
        for p in qalearn.related(q_text, k=2):
            a = str(p.get("a") or "").strip()
            if a:
                mem.append(("%s — %s" % (str(p.get("q") or "").strip(" ?"),
                                         _sent(a[:600])), "память диалогов (подтверждено)"))
    except Exception:
        pass
    try:
        import hypo
        have = " ".join(t for t, _s in mem)
        for t, src in hypo.evidence(q_text):
            if str(src).startswith(("похожая статья", "заметк")) and t[:60] not in have:
                mem.append((t, src))
    except Exception:
        pass
    try:
        if _MN is not None:
            with _MN_LOCK:
                out = mn().write(q_text, words=90 if not long_mode else 340)
            if out and not any(x in out for x in _MN_EMPTY):
                mem.append((out, "прочитанные материалы"))
    except Exception:
        pass
    mem = [(t, src) for t, src in mem if _covers_topic(q_text, t)]
    if not mem:
        return None
    try:
        import llmreply
        reply, li = llmreply.mem_brain(text, mem, "", board="", sender=sender,
                                       chat_id=chat_id, talk=False,
                                       thought_by="")
    except Exception as e:
        store.log("ПАМЯТЬ", "шаблон не собрался: %s" % e)
        reply, li = "", None
    if not reply:
        return None
    return reply, Z["membrain"], dict(li or {}, honest=True)


_CORE_OK = None


def _unify_step(text, reply, zone, info, deliver):
    """v3.29: ЕДИНЫЙ МОЗГ — слабый ответ облака дополняет плата."""
    import unify
    global _CORE_OK
    if _honest():          # v3.129: ответ платы-генератора — не ответ; не ждём
        return reply, zone, info
    if _CORE_OK is None:
        _CORE_OK = {Z["kb"], Z["web"], Z["qa"], Z["skill"], Z["math"],
                    Z["code"], Z["doc"], Z["media"], Z["vision"], Z["sys"],
                    Z["talk"], Z["curiosity"], Z["imagine"]}
    weak = unify.is_weak(reply, zone) or (
        _core() and zone not in _CORE_OK)
    # v3.99: мозг уже ответил вместе с памятью (прямой вопрос по мосту) —
    # второй раз плату не спрашиваем; не ответил — поздний ответ по почте
    mb = (info or {}).get("mem_brain") or {}
    if mb.get("board") or ((info or {}).get("brain_asked") or {}).get(
            "board"):
        weak = False
    if not deliver or not weak:
        return reply, zone, info
    kind = deliver.get("kind")
    wait = 0.0 if kind == "chat" else float(unify.cfg("unify_wait", 12) or 0)
    res = unify.ask(text, deliver, wait=wait)
    if not res:
        return reply, zone, info
    info = dict(info or {})
    if res.get("answer") is not None and "pending" not in res:
        reply, zone = unify.merge(reply, zone, res)
        info["unify"] = {"board": res.get("brain"),
                         "grounded": res.get("grounded"),
                         "source": res.get("source")}
        return reply, zone, info
    info["unify"] = {"board": res.get("brain"), "pending": res["pending"]}
    if kind == "chat":
        info["pending_mid"] = res["pending"]
        info["brain"] = res.get("brain")
    elif kind not in ("max", "tg"):
        info["pending"] = res["pending"]
    # v3.97: без служебных фраз «спросила плату…» — ответ платы придёт сам
    return reply, zone, info


def think(text, long_mode=False, route="cloud", chat_id=None,
          sender="владелец", ctx=None, deliver=None, no_web=False):
    """ГЛАВНЫЙ ВХОД: вопрос -> (ответ, зона, доп-инфо). Без LLM.
    Обёртка: отмечает ответ зоны в статистике (панель «Зоны мысли»);
    v3.9: ctx — история диалога (продолжение темы короткими репликами),
    и честный прирост сети за этот ответ (info["net"])."""
    if _brain_only() and route != "board":
        return _brain_only_answer(text, chat_id, sender, deliver)
    before = mn_size()
    pre = None
    _forced_long = False
    try:                    # v3.78: объём как у чат-ботов: ответ мозг + LLM — длинный
        import llmreply as _lr
        if not long_mode and store.settings().get("llm_answer_long", True) \
                and _lr.full_mode() and len(str(text)) > 25:
            long_mode = True
            _forced_long = True
    except Exception:
        pass
    try:                                  # v3.28: исправления и память ответов
        pre = _qa_pre(text, ctx, sender, route)
    except Exception:
        pre = None
    if not pre:             # v3.124: «покажи формулой» распада — расчёт уравнения
        try:
            import nuclear
            if nuclear._FORM_Q.search(str(text)):
                prev = [str(m.get("text") or "") for m in reversed(
                    [x for x in (ctx or []) if isinstance(x, dict)])
                    if m.get("role") == "user" and
                    str(m.get("text") or "").strip() != str(text).strip()][:3]
                na = nuclear.answer(text, prev)
                if na:
                    pre = (na, Z["math"], {"nuclear": True})
        except Exception:
            pass
    if not pre:             # v3.128: глаза и уши — камера и микрофон мозга
        try:
            import senses
            if senses.ask_kind(text):
                sa, si = senses.answer(text)
                if sa:
                    pre = (sa, Z["hearing"] if (si or {}).get("hear") is not None
                           else Z["vision"], dict(si or {}, senses=True))
        except Exception as e:
            store.log("ЧУВСТВА", "камера/микрофон: %s" % e)
    _LAST_UX[0], _LAST_UX[1] = None, []
    if pre:
        reply, zone, info = pre
    else:
        reply, zone, info = _think(text, long_mode, route, chat_id, sender,
                                   ctx, ask_late=deliver is None,
                                   no_web=no_web)
        if long_mode and not _forced_long:   # v3.85: без лишних поисков (скорость)
            try:                          # v3.15: «длинно» — не короче 1500
                reply = _ensure_long(reply, zone, info, text)
            except Exception:
                pass
        # v3.30: ЛОКАЛЬНАЯ LLM (если владелец подключил) переписывает ответ
        # знаний связно и по делу — ТОЛЬКО по материалам (llmzone.py)
        if (zone in (Z["kb"], Z["web"], Z["mn"], Z["hypo"], Z["speech"]) or
                (zone == Z["sys"] and str(reply).lstrip().startswith(
                    "Не знаю"))) and not (info or {}).get("mem_brain"):
            try:
                import llmzone
                if llmzone.enabled():
                    lt = llmzone.write(text, reply, ctx)
                    if lt:
                        info = dict(info or {}, llm=True, src_zone=zone)
                        reply = lt + ("\n\n_Написано локальной моделью по "
                                      "материалам базы знаний и интернета._")
            except Exception:
                pass
        if zone in (Z["kb"], Z["web"], Z["mn"], Z["wiki"]) \
                and not (info or {}).get("llm"):
            _plain = reply
            try:                          # v3.45: шаблон с жирными разделами
                import llmreply
                reply, _lt = llmreply.build(text, reply, zone, sender,
                                            chat_id)
                if _lt:
                    info = dict(info or {}, **_lt)
            except Exception:
                _lt = None
            if not (info or {}).get("llm_tpl"):
                try:                      # v3.34: своя LLM пишет по фактам
                    import llmlink
                    reply, _lg = llmlink.compose_reply(text, reply, zone)
                    if _lg:
                        info = dict(info or {}, **_lg)
                except Exception:
                    pass
        try:                              # v3.124: иллюстрации статьи-источника
            reply = _attach_images(text, reply, zone)
        except Exception as e:
            store.log("ИЛЛЮСТРАЦИИ", "не приложились: %s" % e)
        try:                              # v3.124: уравнение распада к ответу
            import nuclear
            if "$" not in str(reply) and "[ФОРМУЛА]" not in str(reply):
                na = nuclear.answer(text)
                if na:
                    na = "\n".join(ln for ln in na.split("\n")
                                   if not ln.startswith("_Составлено"))
                    head = str(reply or "").rstrip()
                    reply = (head + "\n\n" + na) if head and not \
                        head.lstrip().startswith("Не знаю") else (
                        na + "\n\n_Составлено: расчёт по законам сохранения "
                        "(статьи по этому вопросу в базе нет)_")
                    info = dict(info or {}, nuclear=True)
        except Exception:
            pass
        try:                              # v3.32: строка своей LLM (выкл.)
            import llmlink
            reply, _li = llmlink.annotate(text, reply, zone)
            if _li:
                info = dict(info or {}, **_li)
        except Exception:
            pass
        try:                              # v3.28: учимся на вопросе и ответе
            import qalearn
            got = qalearn.note(text, locals().get("_plain") or reply, zone,
                               who=sender) if (info or {}).get("llm_tpl") \
                else qalearn.note(text, reply, zone, who=sender)
            if isinstance(info, dict) and got:
                info["learned"] = got
            if qalearn.was_rejected(text, reply):
                reply += ("\n\n⚠ Этот ответ на такой вопрос раньше отметили "
                          "как неверный. Если знаешь верный — напиши "
                          "«правильный ответ: …», и я запомню.")
        except Exception:
            pass
        if route != "board":
            try:                          # v3.29: единый мозг
                reply, zone, info = _unify_step(text, reply, zone, info,
                                                deliver)
            except Exception as e:
                store.log("ЕДИНЫЙ МОЗГ", "шаг не удался: %s" % e)
    try:                                  # v3.122: «Не показывать бред», «без сети»
        reply, zone, info = _strict_post(text, reply, zone, info, route)
    except Exception as e:
        store.log("НЕ БРЕД", "проверка не удалась: %s" % e)
    after = mn_size()
    if after != before and isinstance(info, dict):
        info["net"] = {"nodes": after[0] - before[0],
                       "links": after[1] - before[1],
                       "docs": after[2] - before[2],
                       "total_nodes": after[0], "total_links": after[1]}
    try:
        bump_zone(zone)
    except Exception:
        pass
    try:                      # v3.125: ответ — удар «ритма» мозга платформы
        import boardlink
        boardlink.note_activity(1.0)
    except Exception:
        pass
    try:          # v3.17: мысль зоны — на пин D6 платы (как фразы самого мозга)
        _d6_send(reply, zone)
    except Exception:
        pass
    if isinstance(info, dict) and info.get("smalltalk") and reply:
        # v3.99.1: ответ на реплику — без шаблонного «Добрый день!»
        import replytpl
        reply = replytpl.NO_GREET + str(reply)
    if _LAST_UX[0]:                       # v3.131: что понято — в «/почему»
        try:
            import qcontext
            info = dict(info or {}, understood=qcontext.summary(_LAST_UX[0]),
                        ctx_did="; ".join(_LAST_UX[1][:3]))
        except Exception:
            pass
    return reply, zone, info


_STRICT_GEN = None
_TITLE_CACHE = {"n": -1, "map": {}}


def _art_by_title(title):
    import kb
    arts = kb.list_articles(limit=1000000, include_devices=True) or []
    if _TITLE_CACHE["n"] != len(arts):
        _TITLE_CACHE["map"] = {str(a.get("title") or "").strip(): a.get("id")
                               for a in arts}
        _TITLE_CACHE["n"] = len(arts)
    aid = _TITLE_CACHE["map"].get(str(title or "").strip())
    return kb.article(aid) if aid else None


def _attach_images(text, reply, zone):
    """v3.124: ответ из статьи — с её иллюстрациями (kbimages)."""
    r = str(reply or "")
    if "[ИЛЛЮСТРАЦИЯ" in r or zone not in (Z["kb"], Z["web"], Z["membrain"]):
        return reply
    titles = re.findall(r"Источник:\s*«([^»]{3,200})»", r) or \
        re.findall(r"похожая статья базы «([^»]{3,200})»", r)
    if not titles:
        return reply
    import kbimages
    marks = []
    body = _answer_body(r)
    for t in titles[:2]:
        art = _art_by_title(t)
        if art:
            marks += kbimages.for_article(art, body, text, k=2)
        if len(marks) >= 2:
            break
    if not marks:
        return reply
    block = "**Иллюстрация**\n" + "\n".join(marks[:2])
    lines = r.rstrip().split("\n")
    k = len(lines)
    while k > 0 and lines[k - 1].strip().startswith(("_Составлено",
                                                      "_Источник", "Источник")):
        k -= 1
    if k == len(lines):
        return r.rstrip() + "\n\n" + block
    return "\n".join(lines[:k]).rstrip() + "\n\n" + block + "\n\n" + \
        "\n".join(lines[k:])


def _answer_body(reply):
    """Текст раздела «Ответ» (без приветствия, подписей, источников)."""
    r = str(reply or "")
    m = re.search(r"\*\*Ответ\*\*\s*\n([\s\S]*?)(?:\n\n\*\*|\Z)", r)
    body = m.group(1) if m else r
    keep = [ln for ln in body.splitlines()
            if ln.strip() and not ln.strip().startswith(("_", "Источник", "⚠"))]
    return " ".join(keep).strip()


def _strict_post(text, reply, zone, info, route="cloud"):
    """v3.122: две галочки ответов (strict.py)."""
    import strict
    global _STRICT_GEN
    if _STRICT_GEN is None:
        _STRICT_GEN = {Z["speech"], Z["board"], Z["imagine"], Z["mind"],
                       Z["thought"], Z["hypo"], Z["gen"]}
    info = dict(info or {})
    if not reply or route == "board":
        return reply, zone, info
    # 1) без интернета — только то, что лежит в базе / памяти / навыках
    if not strict.offline_ok() and not strict.online():
        safe = {Z["kb"], Z["qa"], Z["skill"], Z["math"], Z["sys"], Z["talk"],
                Z["doc"], Z["code"], Z["media"], Z["vision"]}
        mb_ = info.get("mem_brain") or {}
        local_mem = bool(mb_.get("memory")) and not mb_.get("llm_own")
        un_ = info.get("unify") or {}
        if (zone not in safe and not (zone == Z["membrain"] and local_mem)) \
                or (un_ and not un_.get("grounded")):
            strict._N["offline"] += 1
            info["offline"] = True
            return strict.OFFLINE, Z["sys"], info
    if not strict.on():
        return reply, zone, info
    # 2) исходник кода из базы — только на вопрос про код
    if "Исходник:" in str(reply) and not strict.code_question(text):
        strict.block("исходник не по теме", text)
        info["strict"] = "исходник кода не по теме вопроса"
        return strict.NO_ANSWER, Z["sys"], info
    # 3) ответ-мысль (мозг/воображение/догадка) — только связный
    if zone in _STRICT_GEN:
        body = _answer_body(reply)
        if body and not strict.text_ok(body, text, 4):
            strict.block("ответ-мысль (%s)" % zone, body)
            info["strict"] = "мысль несвязная"
            return strict.NO_ANSWER, Z["sys"], info
    return reply, zone, info


def _think(text, long_mode=False, route="cloud", chat_id=None,
           sender="владелец", ctx=None, ask_late=True, no_web=False):
    text = str(text or "").strip()[:2000]
    if not text:
        return "", Z["sys"], {}
    low = text.lower()
    # v3.149.12: «выполни команду …» / «открой терминал» / «снимок экрана»
    # v3.150: БЕЗОПАСНОСТЬ — только владелец (чат панели). Раньше «выполни …»
    # от любого собеседника бота МАКС/Telegram выполнялось на компьютере.
    if sender == "владелец" and low.startswith(("выполни ", "команда ", "терминал ", "cmd ")):
        try:
            import terminal
            import teacher
            teacher.instruct_exec(text, "command")   # мысль учителя
            if not terminal.enabled():
                return ("Терминал выключен. Включите «Терминал и рабочий стол» "
                        "в Настройках → Общие.", Z["sys"], {})
            cmd = text.split(None, 1)[1] if " " in text else ""
            if not cmd:
                return "Напишите команду после «выполни».", Z["sys"], {}
            r = terminal.run(cmd, timeout=60)
            out = r.get("out") or r.get("error") or ""
            return ("Команда: %s\nКод: %s\n%s" % (cmd, r.get("code", "?"),
                    out[:2000]), Z["sys"], {"terminal": True})
        except Exception as e:
            return "Терминал: %s" % e, Z["sys"], {}
    if sender == "владелец" and ("снимок экрана" in low or "скриншот" in low):
        try:
            import desktop
            import teacher
            teacher.instruct_exec(text, "screenshot")
            r = desktop.panel_command("скриншот")
            if r:
                return r, Z["sys"], {}
        except Exception as e:
            return "Снимок: %s" % e, Z["sys"], {}
    # v3.9: ПОНИМАНИЕ ЗАПРОСА — продолжение темы. Короткая реплика (до 3
    # слов) без команды склеивается с последней репликой владельца: так
    # «а почему?» после «небо синее» ищется по теме «небо синее».
    q_text = text
    ctx_last_user = ""          # v3.26: чистая тема (для страховки в KB)
    ctx_topics = []             # v3.27: прошлые вопросы, новые -> старые
    ctx_last_bot = ""           # v3.27: что я уже ответила («расскажи ещё»)
    is_follow = False
    if ctx:
        try:
            hist = [m for m in ctx if isinstance(m, dict)]
            # v3.27: БАГФИКС — чат облака кладёт в ctx и ТЕКУЩЕЕ сообщение
            # (МАКС — нет): тема склеивалась сама с собой, продолжения
            # «а какая у неё площадь?» в чате облака не работали
            if hist and hist[-1].get("role") == "user" and \
                    str(hist[-1].get("text") or "").strip() == text:
                hist = hist[:-1]
            ctx_topics = [str(m.get("text") or "") for m in reversed(hist)
                          if m.get("role") == "user" and
                          str(m.get("text") or "").strip()][:3]
            # всё, что я уже сказала в этом диалоге («ещё» — без повторов)
            ctx_last_bot = "\n".join(str(m.get("text") or "")
                                     for m in hist
                                     if m.get("role") != "user")
            last_user = ctx_topics[0] if ctx_topics else ""
            ctx_last_user = str(last_user or "")
            if last_user:
                words = text.split()
                # v3.9: короткая реплика (до 3 слов) — продолжение темы,
                # кроме команд и новых вопросов «что такое/кто такой…»
                short = len(words) <= 3 and not re.match(
                    r"^\s*(статья|напиши|найди|статус|мысль|"
                    r"что|кто|где|когда|сколько|как)", low)
                # v3.26: есть отсылка к сказанному раньше (местоимения,
                # «ещё», «подробнее») — продолжение даже длиннее трёх слов
                # и даже с «когда/где/сколько…» в начале
                follow = len(words) <= 6 and \
                    bool(_FOLLOW_RE.search(low)) and not re.match(
                        r"^\s*(статья|напиши|найди|статус|мысль|выучи|"
                        r"запомни|что такое|что значит|что за|кто такой|"
                        r"кто такая|/)", low)
                if short or follow:
                    q_text = (last_user[:120].strip() + " " + text).strip()
                    is_follow = True
        except Exception:
            q_text = text

    # 0-0) v3.27: ДОКУМЕНТЫ — «создай документ про …», «сохрани ответ в
    # docx/txt/html», «сохрани наш разговор в файл»: файл + ссылка на
    # скачивание в браузерной версии (зона «Документы»). РАНЬШЕ «Памяти
    # диалога»: «сохрани наш разговор» — просьба о файле, не пересказ.
    try:
        import docgen
        dk = docgen.detect(text)
        if dk:
            return _make_document(text, dk, ctx, long_mode)
    except Exception as e:
        return ("Документ не получился: %s. Попробуй ещё раз или другой "
                "формат (docx, txt, html)." % e), Z["doc"], {}

    # 0-0а) v3.27: вопросы О САМОМ мозге — ответ о себе, не из интернета
    # (иначе «что умеешь» собиралось со страниц про Яндекс.Алису)
    # v3.170: ИИ отвечает о себе по ИНСТРУКЦИИ (instructions.py) — документ
    # из настоящего состояния облака, который ИИ читает, как статью
    try:
        import instructions
        _selfq = instructions.is_self_q(text) or _SELF_Q.match(low)
    except Exception:
        instructions, _selfq = None, _SELF_Q.match(low)
    if _selfq:
        body = ""
        try:
            body = instructions.answer(text, long_mode) if instructions else ""
        except Exception:
            body = ""
        if body:
            return (body + "\n\n_Из инструкции ИИ облака — она собирается сама из "
                    "настоящего состояния облака и приложений App Lab._"), Z["sys"], \
                {"self": True, "article": "ai_instructions"}
        who = bool(re.search(r"кто\s+ты|ты\s+кто|что\s+ты\s+(?:такое|за)",
                             low))
        return self_intro(who), Z["sys"], {"self": True}

    # 0-0ж) v3.41: ССЫЛКА НА GITHUB — читать репозиторий в фоне, учиться коду
    try:
        import ghlearn
        if ghlearn.find_link(text) and not no_web:
            if not store.settings().get("web_on"):
                return ("GitHub я читаю через интернет, а он выключен в "
                        "настройках (web_on)."), Z["media"], {}
            rep = ghlearn.chat_reply(text)
            if rep:
                return rep, Z["media"], {"github": True}
    except Exception as e:
        return "Ссылку GitHub не приняла: %s." % e, Z["media"], {}

    # 0-0в) v3.30: ВИДЕО И МУЗЫКА — «посмотри видео <ссылка|тема>»,
    # «послушай песню …»: субтитры, название, описание (звук и кадры мозг
    # не воспринимает — честно говорит об этом); всё — в базу знаний
    try:
        import media
        mv = media.WATCH_Q.match(text)
        ml = media.LISTEN_Q.match(text) if not mv else None
        if (mv or ml) and not no_web:
            if not store.settings().get("web_on"):
                return ("Видео и музыку я ищу в интернете, а он выключен в "
                        "настройках (web_on)."), Z["media"], {}
            arg = next(g for g in (mv or ml).groups() if g)
            rep, minfo = (media.watch_reply(arg) if mv else
                          media.listen_reply(arg))
            return rep, Z["media"], minfo
    except Exception as e:
        return "Видео не получилось: %s." % e, Z["media"], {}

    # 0-0г) v3.30: «что на картинке «имя»?» — описание присланной картинки
    m = _IMG_Q.match(text)
    if m:
        nm = m.group(1).strip()
        try:                       # картинку могли загрузить секунду назад
            kb._idx_refresh(inline_max=20)
        except Exception:
            pass
        arts = [a for a in kb.list_articles(limit=400)
                if str(a.get("title") or "").lower() ==
                ("картинка: %s" % nm).lower()]
        if arts:
            art = kb.article(arts[0]["id"]) or {}
            return (str(art.get("text") or "")), Z["vision"], {"image": nm}

    # 0-0б) v3.27: ТЕНЗОРНАЯ СЕТЬ ПЛАТЫ (v2.39): «тензор» — отчёт (сеть
    # платы + облако-вычислитель), «тензор обучить» — команда плате
    m = _TENSOR_Q.match(low)
    if m:
        rep, extra = _tensor_cmd(bool(m.group(1)), chat_id, sender)
        return rep, Z["board"], dict({"tensor": True}, **extra)

    # 0-а) v3.26: зона «Память диалога» — «о чём мы говорили?», «что ты
    # мне отвечала?»: честный пересказ последних реплик из ctx.
    if _RECALL_Q.search(low):
        try:
            rh = [m for m in (ctx or []) if isinstance(m, dict) and
                  str(m.get("text") or "").strip()][-8:]
        except Exception:
            rh = []
        if rh:
            lines = []
            for m in rh:
                who = "Ты" if m.get("role") == "user" else "Я"
                t = re.sub(r"\s+", " ", str(m.get("text") or "")).strip()
                if len(t) > 160:
                    cut = t[:160]
                    sp = cut.rfind(" ")
                    if sp > 80:
                        cut = cut[:sp]
                    t = cut.rstrip(" ,.;:-") + "…"
                lines.append("— %s: %s" % (who, t))
            return ("Наша недавняя беседа (я помню %d %s):\n%s\n\n"
                    "Это память текущего диалога; «забудь» у бота в МАКС "
                    "стирает её." % (len(lines), _ru_plural(len(lines),
                                   "реплика", "реплики", "реплик"),
                                   "\n".join(lines))), Z["mem"], \
                {"recall": True}
        return ("В этом диалоге мы ещё не говорили — истории нет. Спроси "
                "о чём-нибудь, и я запомню наш разговор."), Z["mem"], {}

    # 0-б) v3.18: «что нового ты узнала» — сводка знаний о собственном
    # росте (любопытство, самокод, воображение, база). Ставим РАНО: это
    # вопрос о САМОЙ мозге, интернету и навыкам его отдавать нельзя.
    if _NEWS_Q.match(low):
        return news_digest(), Z["curiosity"], {"curiosity": "digest"}

    # 0-б) v3.18: «что нового ты узнала» — сводка знаний о собственном
    # росте (любопытство, самокод, воображение, база). Ставим РАНО: это
    # вопрос о САМОЙ мозге, интернету и навыкам его отдавать нельзя.
    if _NEWS_Q.match(low):
        return news_digest(), Z["curiosity"], {"curiosity": "digest"}

    # 0а) v3.11: вердикты владельца по гипотезам: «верно» / «неверно» —
    # применяем к последней ждущей проверке гипотезе (зона «Предположение»).
    # Если ждущих догадок нет — честно говорим об этом, а не несём «верно»
    # в Википедию за французской коммуной.
    if _VER_Q.match(low):
        try:
            import hypo
            r = hypo.feedback(low)
            if r:
                return r, Z["hypo"], {"hypo": "verdict"}
            return ("Ждущих проверки догадок сейчас нет: «верно» и "
                    "«неверно» относятся к моему последнему предположению "
                    "(зона «Предположение»). Спроси о чём-нибудь "
                    "незнакомом — я попробую угадать, а не промолчу."), \
                Z["hypo"], {"hypo": "no-verdict"}
        except Exception:
            pass

    # 0а) зона «Словарь»: «что значит …», «проверь слово …» — проверяем
    # ДО навыков: «что такое слово …» иначе уводило бы в Википедию
    m = _DICT_Q.match(low)
    if m:
        r = dict_lookup(m.group(1))
        if r["found"] and r.get("stem"):
            reply = ("Формы «%s» в словарях нет, но корень знакомый: есть "
                     "слова %s (всего слов: %d). Считаю слово настоящим — "
                     "буквогенератор его соберёт, а учитель примет."
                     % (r["word"], ", ".join("«%s»" % c for c in r["close"]),
                        r["total"]))
        elif r["found"]:
            pos = (", " + r["pos"]) if r.get("pos") else ""
            reply = ("Слово «%s» — настоящее, оно есть в словарях облака%s. "
                     "Всего слов проверено: %d. Словарь — топливо "
                     "буквогенератора: из этих слов собираются осмысленные "
                     "фразы, а учитель по словарю ставит фразам вердикты."
                     % (r["word"], pos, r["total"]))
        else:
            extra = (" Похожие слова: %s." % ", ".join(r["close"][:6])) \
                    if r["close"] else ""
            reply = ("Слова «%s» в моих словарях нет (проверено %d слов: "
                     "ru_top, ru_pos, extra и встроенный словарь).%s "
                     "Платы могут дополнить словарь: POST api/dict/put — "
                     "после склейки слово появится и у меня."
                     % (r["word"], r["total"], extra))
        return reply, Z["dict"], {"word": r["word"], "found": r["found"]}

    # 0б) зона «Память»: «что помнишь про …» — тематический поиск по заметкам
    m = _MEM_Q.match(low)
    if m:
        topic = m.group(1).strip(" ?.!")[:120]
        words_t = [w for w in textutils.WORD_RE.findall(topic) if len(w) > 3]
        hits = [n for n in store.notes()
                if _overlap(topic, n["text"]) or
                any(w in n["text"].lower() for w in words_t)][:3]
        if hits:
            reply = "Из памяти облака про «%s»:\n%s" % (
                topic, "\n".join("• %s" % n["text"][:160] for n in hits))
        else:
            reply = ("Про «%s» в памяти заметок нет (всего заметок: %d). "
                     "Скажи «запомни: …» — и я это запомню."
                     % (topic, len(store.notes())))
        return reply, Z["mem"], {"topic": topic}

    # v3.131: ПОНИМАНИЕ КОНТЕКСТА до ответа (qcontext): что нужно, тема, кто
    # понимал (разбор вопроса / локальная модель / своя LLM, если прошла
    # проверку точности). Зоны дальше подбирают ответ по понятому.
    _ux = None
    try:
        import qcontext
        _ux = qcontext.understand(text, q_text, is_follow)
        _LAST_UX[0] = _ux
    except Exception:
        _ux = None
    if _ux and _ux.get("kind") == "код" and route != "code" and \
            not re.search(r"\bсеб[ея]\b", low):
        try:                                   # понят как просьба о коде — зона «Код»
            import agent
            c_reply, c_ok, c_info = agent.code_agent(text, ctx=ctx)
            if c_ok and c_reply:
                _LAST_UX[1].append("понят как просьба о коде — ответила зона «Код»")
                return c_reply, Z["code"], dict(c_info or {}, by_context=True)
        except Exception:
            pass

    # 0в) v3.9: зона «Мысли платы» — ГЕНЕРАТОР НА ТОМ, ЧТО ДУМАЕТ ПЛАТА.
    # Ставим РАНЬШЕ _THINK_Q: «мысль платы» иначе уходила бы в свободную
    # генерацию, не смотря в реальные мысли плат.
    if _BOARDMIND_Q.search(low):
        return board_mind("".join(
            w for w in re.split(r"мысли плат|что думает плат|о ч[её]м думает "
                                r"плат|мысль платы|что у плат[ы]? на уме",
                                low, flags=re.I)[1:]).strip(" ?.!"))

    # 0г) v3.16: ЗОНА ВООБРАЖЕНИЯ — рисование/перерисовка картинок, графики
    # функций и LaTeX-карточки формул. Рисунок и график — артефакты:
    # режим «длинно» их не раздувает.
    if store.settings().get("imagine_on", True):
        try:
            import imagine
            r = imagine.route(text, low)
            if r:
                reply, info = r
                return reply, Z["imagine"], info
        except Exception:
            pass

    # 0д) v3.27: КАЛЬКУЛЯТОР И УРАВНЕНИЯ (calc) — раньше навыка: навык
    # брал первые два числа («2+2*3» -> «2+2 = 4»), а проценты, корни и
    # уравнения уходили в «не знаю». Не математика — calc молчит (None).
    try:
        import calc
        ca_m = calc.answer(text)
        if ca_m:
            return ca_m[0], Z["math"], {"math": ca_m[1]}
    except Exception:
        pass

    # 1) реестр функций: ключевики + builtin
    #    v3.13: вопрос-формат при web_first не даёт встроенному wiki-навыку
    #    перехватывать вопрос одним источником — отвечает 16-я зона
    #    «Интернет-источники»; v3.14: она читает ПЕРВЫЕ СТРАНИЦЫ выдачи
    #    (до 10) и подписывает каждый сайт + список «Источники:»
    hit = skills.match(text)
    if hit and not (_Q_LIKE.match(low)
                    and store.settings().get("web_on")
                    and store.settings().get("web_first", True)
                    and hit[0] == "wiki"):
        sid, sk = hit
        r = skills.run_skill(sid, text, board=sender)
        if r and r.get("ok"):
            reply = str(r.get("result") or "").strip()
            if reply:
                mn_learn("Вопрос: %s\nОтвет: %s" % (text, reply),
                         title="диалог (%s)" % sk.get("name", sid),
                         src="диалог")
                return reply, Z["skill"], {"skill": sk.get("name", sid)}

    # 1б) зона «Математика» (v3.8): формулы и модели — мозг сам считает
    # по своей базе (без LLM). Проверяем ПОСЛЕ навыков: «посчитай 2+2» —
    # арифметика навыка, а «площадь круга радиусом 5» — уже формула.
    # v3.10: «составь/напиши формулу …» — СОСТАВЛЕНИЕ (карточка формулы),
    # работает даже без чисел (formula_reply сам различает режимы).
    try:
        import mathmodels
        kind = mathmodels.detect(low)
        if kind == "formula":
            reply, ok = mathmodels.formula_reply(text)
            if ok:
                mn_learn("Вопрос: %s\nОтвет: %s" % (text, reply),
                         title="формула", src="математика")
                return reply, Z["math"], {"math": "formula"}
            # v3.106: «в базе формул под это темы нет…» — заготовка, а не
            # ответ: вопрос идёт дальше (база знаний, интернет, память мозга)
            if not str(reply).startswith(("В базе формул",
                                          "Формул по этой теме")):
                return reply, Z["math"], {}
            text = q_text = _formula_query(text)
            low = text.lower()
        if kind == "model":
            reply, ok = mathmodels.model_reply(text)
            if ok:
                mn_learn("Вопрос: %s\nОтвет: %s" % (text, reply),
                         title="математическая модель", src="математика")
                return reply, Z["math"], {"math": "model"}
            return reply, Z["math"], {}
    except Exception:
        pass

    # 1в) v3.18: САМОКОД — «напиши себе функцию …»: мозг пишет функцию,
    # проверяет её тестами в песочнице и публикует в реестр (зона «Код»).
    # РАНЬШЕ _WRITE_Q: «напиши функцию …» — не сочинение, а код.
    m = _AUTOCODE_Q.match(text)
    if m:
        try:
            import autocode
            rep, ok = autocode.write_function(m.group(1))
            return rep, Z["code"], {"autocode": bool(ok)}
        except Exception as e:
            return ("Самокод не удался: %s. Но я честно попыталась — "
                    "скажи тему ещё раз или иначе." % e), Z["code"], {}

    # 1в) v3.18: САМОКОД — «напиши себе функцию …»: мозг пишет функцию,
    # проверяет её тестами в песочнице и публикует в реестр (зона «Код»).
    # РАНЬШЕ _WRITE_Q: «напиши функцию …» — не сочинение, а код.
    m = _AUTOCODE_Q.match(text)
    if m:
        try:
            import autocode
            rep, ok = autocode.write_function(m.group(1))
            return rep, Z["code"], {"autocode": bool(ok)}
        except Exception as e:
            return ("Самокод не удался: %s. Но я честно попыталась — "
                    "скажи тему ещё раз или иначе." % e), Z["code"], {}

    # 1г') v3.91: ЧЕСТНЫЙ РАЗГОВОР — «как дела», «привет», «спасибо», «кто ты»:
    # из настоящего состояния облака, без выдуманных чувств (smalltalk.py)
    # v3.99: на реплику отвечает МОЗГ (мысль платы / речь мозга облака) с
    # опорой на память диалогов, своя LLM дополняет; не вышло — своя LLM
    # своими словами (только качественная фраза); иначе — честно
    try:
        import smalltalk
        if smalltalk.kind_of(text):
            mb = None if _honest() else _mem_brain(
                text, text, False, sender, chat_id, talk=True, route=route)
            if mb:
                return mb[0], Z["talk"], dict(mb[2], smalltalk=True,
                                              by="мозг + своя LLM")
            st_ans = smalltalk.answer(text)
            if st_ans:
                txt, by = st_ans
                if by == "своя LLM":
                    txt += ("\n\n_— своими словами написала своя LLM (без "
                            "опоры на факты)_")
                return txt, Z["talk"], {"smalltalk": True, "by": by}
    except Exception:
        pass

    # 1д) v3.33: «что делаешь?» — честно о текущем занятии, без поиска
    if _DOING_Q.match(low):
        try:
            st = store.stats()
            rd = mn_readings()
            lines = ["Сейчас отвечаю тебе, а в фоне учусь: в базе знаний %d "
                     "%s, в сети материалов %s узлов."
                     % (st["kb_articles"], _ru_plural(
                         st["kb_articles"], "статья", "статьи", "статей"),
                        rd.get("nodes", "?"))]
            try:
                import curiosity
                q = curiosity.status() or {}
                if q.get("queue"):
                    lines.append("Любопытство читает очередь тем (%d в "
                                 "ожидании)." % len(q["queue"]))
            except Exception:
                pass
            lines.append("Спроси о чём-нибудь — отвечу по базе знаний, а "
                         "чего там нет, соберу из интернета.")
            return " ".join(lines), Z["sys"], {"doing": True}
        except Exception:
            return ("Отвечаю тебе и учусь. Спроси о чём-нибудь."), \
                Z["sys"], {"doing": True}

    # 2) явные команды платформы
    m = _STATUS_Q.match(low)
    if m:
        st = store.stats()
        rd = mn_readings()
        return ("Платформа жива: чатов %d, сообщений %d, статей KB %d, "
                "функций %d, слов в словарях %s. Плат онлайн %d из %d. "
                "Сеть материалов: узлов %s, связей %s, материалов %s, "
                "записей %s." % (
                    st["chats"], st["messages"], st["kb_articles"],
                    st["skills"], st["dict_words"], st["boards_online"],
                    st["boards_total"], rd.get("nodes", "?"),
                    rd.get("links", "?"), rd.get("docs", "?"),
                    rd.get("writes", "?"))), Z["sys"], {}

    # 2а) остановить экскурс (проверяем РАНЬШЕ старта: «стоп» содержит
    # то же слово «экскурс»)
    if re.search(r"останови экскурс|стоп экскурс|прерви экскурс", low):
        import teacher
        stt = teacher.tour_stop()
        return ("Экскурсия на паузе: отправлено уроков %d из %d."
                % (int(stt.get("lesson") or 0),
                   int(stt.get("total") or 12))), Z["sys"], {}

    # 2б) экскурс учителя для платы (App Lab) по всем функциям платформы
    if re.search(r"экскурс|научи плату|обучи плату|проведи урок", low):
        import teacher
        stt = teacher.tour_start()
        if stt.get("on"):
            via = bool(stt.get("via_bridge"))
            return ("Экскурсия начата: учитель пошлёт плате «%s» %d урока "
                    "%s, по одному каждые %d секунд — память, письма, база "
                    "знаний, функции, словари, сеть, Википедия, генератор, "
                    "зрение, голос, учитель. Слабые ответы плата будет "
                    "повторять. Прогресс — во вкладке «Учитель»."
                    % (stt.get("board") or "первой онлайн-плате",
                       stt["total"],
                       "ЧЕРЕЗ МОСТ (напрямую на 127.0.0.1:7000)" if via
                       else "через мозг-почту",
                       stt.get("interval") or 90)), \
                Z["sys"], {}
        return ("Экскурсия уже идёт: урок %d из %d, плата «%s». Пауза и "
                "сброс — во вкладке «Учитель» или скажи «останови экскурс»."
                % (int(stt.get("lesson") or 0), int(stt.get("total") or 12),
                   stt.get("board") or "?")), Z["sys"], {}

    m = _WRITE_Q.match(text)
    if m:
        topic = m.group(2).strip()[:200]
        # v3.10: «напиши формулу …» — это математика, а не генератор текста
        if re.search(r"формул|уравнен", topic.lower()):
            try:
                import mathmodels
                reply, ok = mathmodels.compose_reply(topic)
                if ok:
                    return reply, Z["math"], {"math": "compose"}
            except Exception:
                pass
            # v3.106: в карточках формул темы нет — без заготовки «формул по
            # этой теме нет…»: вопрос идёт дальше (база, интернет, память)
            text = q_text = _formula_query(text)
            low = text.lower()
        else:
            return write_article(topic), Z["gen"], {"topic": topic}

    m = _FIND_Q.match(text)
    if m:
        q = m.group(2).strip(" ?.!")[:120]
        ft = find_topic(q, long_mode)
        if ft[1] == Z["sys"] and (ft[2] or {}).get("nothing"):
            # v3.99: ни базы, ни интернета — мозг отвечает сам по памяти
            mb = _mem_brain(text, q, long_mode, sender, chat_id, route=route)
            if mb:
                return mb
        return ft

    # v3.10: «открой/прочитай X» — мозг ОТКРЫВАЕТ статью: KB -> Википедия,
    # сохраняет в базу знаний, возвращает выжимку и ссылку
    # (матч по ИСХОДНОМУ тексту: в KB и названиях статей сохраняется регистр)
    m = _OPEN_Q.match(text)
    if m:
        q = m.group(2).strip(" ?.!")[:120]
        return open_topic(q, long_mode)

    # v3.10: любопытство нейронов — немедленное чтение и управление
    m = _LEARN_Q.match(text)
    if m:
        topic = m.group(2).strip(" ?.!")[:120]
        import curiosity
        r = curiosity.learn_now(topic)
        e = r.get("entry") or {}
        if e.get("ok"):
            return ("Прочитал статью «%s» (%d знаков) — сохранил в базу "
                    "знаний и выучил в сеть материалов. Спроси что-нибудь "
                    "по теме!" % (e.get("title") or topic,
                                  e.get("chars") or 0)), Z["curiosity"], \
                {"curiosity": "learn"}
        note = e.get("note") or r.get("note") or "не получилось"
        if r.get("skip") or e.get("skip"):
            # v3.15: «уже знаю» — не ошибка чтения, а знание
            return ("Статьи по «%s» у меня уже в базе знаний — это я знаю "
                    "(спроси по теме — отвечу). Всё равно понаблюдать за "
                    "новым? Скажи «узнай про <другая тема>»." % topic), \
                Z["curiosity"], {"curiosity": "known"}
        return ("Не смог прочитать «%s»: %s. Тема осталась в очереди "
                "«хочу знать» — попробую позже." % (topic, note)), \
            Z["curiosity"], {"curiosity": "learn-fail"}

    m = _CURIOSITY_Q.match(low)
    if m:
        import curiosity
        if m.group(1):
            stt = curiosity.set_enabled(m.group(1).lower() == "включи")
        else:
            stt = curiosity.status()
        return curiosity_report(stt), Z["curiosity"], {"curiosity": "status"}

    # 2а) v3.16: ЗОНА ВООБРАЖЕНИЯ — «представь/вообрази/по фантазируй/"
    # «придумай …»: мини-LLM облака (настоящий трансформер на stdlib)
    # продолжает мысль. Честно: это фантазия выученными словами, не факты.
    m = _IMAGINE_Q.match(low)
    if m:
        topic = m.group(2).strip()[:120]
        try:
            import tinyllm
            text_out, linfo = tinyllm.fantasize(topic)
            if text_out:
                head = "Воображение (мини-LLM облака) представляет:"
                if linfo.get("warming"):
                    head = ("Воображение ещё учится (шагов %s из %s), но уже "
                            "мечтает:" % (linfo.get("steps"),
                                          linfo.get("total")))
                reply = "%s\n«%s»\n\nЭто фантазия нейросети из выученных "\
                        "слов — не факты. Знания — в других зонах; а ещё я "\
                        "умею рисовать: скажи «нарисуй …»." % (head, text_out)
                mn_learn("представь: %s\nфантазия: %s" % (topic, text_out),
                         title=("воображение: %s" % topic)[:60],
                         src="воображение")
                return reply, Z["imagine"], {"imagine": "fantasy",
                                             "llm": linfo}
            return ("Нейросеть воображения ещё готовится: словарь пуст "
                    "(обучение пошло в фоне — попробуй через минуту). "
                    "А рисовать я уже умею: скажи «нарисуй …»."), \
                Z["imagine"], {"imagine": "cold"}
        except Exception:
            pass

    m = _THINK_Q.match(low)
    if m:
        topic = m.group(2).strip()[:120]
        # v3.12: «мысль …» — как на плате: сеть ГОВОРИТ ИЗ СЛОВ СЛОВАРЯ,
        # никаких заготовок; не собралось — честный отказ
        try:
            import wordmind
            phrase, sinfo = wordmind.compose(topic)
            if phrase:
                mn_learn(topic, title=("мысль: %s" % topic)[:60],
                         src="речь из словаря")
                return phrase, Z["speech"], {"speech": sinfo}
            return ("Сеть речи пока не собрала фразу по «%s»: %s. Дай мне "
                    "материалов или диалогов по теме — и я скажу её своими "
                    "словами из словаря." % (topic[:60],
                                             sinfo.get("why") or "нет связей")), \
                Z["speech"], {"speech": sinfo}
        except Exception:
            pass
        return free_think(topic), Z["mn"], {}

    # 2в) v3.9: зона «Код» — умение читать программный код (codemind).
    # Отвечает ТОЛЬКО при уверенном совпадении: иначе — как раньше, дальше
    # по конвейеру (KB/сеть/Википедия).
    if _CODE_HINT.search(low) or _CODE_Q.search(low):
        try:
            import codemind
            code_reply, conf = codemind.ask(text)
            if code_reply and conf >= 0.5:
                mn_learn("Вопрос: %s\nОтвет: %s" % (text, code_reply[:1200]),
                         title="вопрос про код", src="код")
                return code_reply, Z["code"], {"conf": conf}
        except Exception:
            pass

    # 3) KB: точный поиск по базе знаний (порог косинуса внутри kb.search)
    kb_q = q_text
    focus = None
    # v3.126: РАЗБОР ВОПРОСА — что спрашивают и в какую эпоху (qfocus):
    # слова эпохи («во времена Римской империи») не идут в поиск, а годы
    # в найденном проверяются; статьи-даты («19 апреля») — в конец
    try:
        import qfocus
        _qf = qfocus.parse(text) if not is_follow else qfocus.parse(q_text)
        if not is_follow and _qf.get("topic_query") and \
                _qf["topic_query"] != text:
            kb_q = _qf["topic_query"]
    except Exception:
        _qf = None
    _qf_why = []
    if _ux and _ux.get("search") and not (_qf or {}).get("era") and not is_follow:
        kb_q = _ux["search"]                   # v3.131: запрос, который сформулировала модель

    def _qf_ok(t):
        """Ответ проходит проверку контекста вопроса? -> текст | ''."""
        if _qf:
            try:
                ft, why = qfocus.filter_answer(t, _qf)
                if why:
                    _qf_why.append(why)
                t = ft
            except Exception:
                pass
        if t and _ux:                          # v3.131: первым — то, что спросили
            try:
                import qcontext
                t2, did = qcontext.rerank(t, _ux, q_text)
                if did and did not in _LAST_UX[1]:
                    _LAST_UX[1].append(did)
                    t = t2
            except Exception:
                pass
        return t
    # v3.27: «как дела?», «спасибо» — не вопросы к базе: раньше «дела»
    # находило заметку платы со словом «ДЕЛА» и бот отвечал ею
    has_topic = _has_topic(text)
    if has_topic or is_follow:
        r = kb.search(kb_q, limit=3)
        hits = [h for h in (r.get("results") or []) if not h.get("wiki")]
        try:
            hits = qfocus.demote_hits(hits, _qf) if _qf else hits
        except Exception:
            pass
    else:
        hits = []
    # v3.165: КАК «ЛЮБОПЫТСТВО» — статьи по теме в базе нет: облако сначала
    # ЧИТАЕТ её целиком (Википедия, иначе страницы Google/Яндекса), кладёт
    # в базу, отдаёт мозгу плат и своей LLM — и отвечает связным куском
    # статьи, а не обрывками выдачи или по памяти (readlike.py)
    _read_now = None
    # v3.168: ДОКУМЕНТАЦИЯ CONFLUENCE (docEMIAS) — как Википедия, но ПЕРВОЙ и
    # без записи в базу знаний: в базе статьи по теме нет — ищем страницу в
    # пространстве и отвечаем связным куском со ссылкой (confluence.py)
    if has_topic and not is_follow and not no_web:
        _cr = _conf_answer(kb_q if len(kb_q) > len(text) // 2 else text, hits,
                           long_mode)
        if _cr:
            return _cr
    if has_topic and not is_follow and not no_web:
        hits, _read_now = _read_first(kb_q, hits)
    # v3.27: КОНТЕКСТ ДИАЛОГА при выборе статьи — из почти равных (в
    # пределах 12%) выбираем ту, что ближе к теме разговора (заявка: «ИИ
    # выбирает документы не всегда подходящие к контексту»)
    if len(hits) > 1 and ctx_topics and not is_follow:
        try:
            topic_st = {s for s in kb._stems(" ".join(ctx_topics[:2]))
                        if s not in kb._STOP_STEMS}
            s0 = float(hits[0].get("score") or 0)

            def _ctx_bonus(h):
                st = set(kb._stems(str(h.get("title") or "") + " " +
                                   str(h.get("snippet") or "")))
                return len(st & topic_st)
            close = [h for h in hits if float(h.get("score") or 0) >= 0.88 * s0]
            if len(close) > 1 and topic_st:
                best = max(close, key=lambda h: (_ctx_bonus(h),
                                                 float(h.get("score") or 0)))
                if best is not hits[0] and _ctx_bonus(best) > \
                        _ctx_bonus(hits[0]):
                    hits = [best] + [h for h in hits if h is not best]
        except Exception:
            pass
    # v3.27: ПРОДОЛЖЕНИЕ ТЕМЫ — как LLM с памятью диалога: статья берётся
    # по ТЕМЕ (прошлый вопрос), а фрагмент в ней — по НОВОМУ вопросу.
    # Раньше склейка «Антарктида + а какая у неё площадь» находила
    # «Атлантический океан» (там «площадь» встречается чаще).
    if is_follow:
        # тема — последний САМОСТОЯТЕЛЬНЫЙ вопрос: «расскажи ещё» после
        # «а какая у неё площадь?» продолжает Антарктиду, а не «площадь»
        def _follow_like(t):
            tl = t.lower()
            return len(t.split()) <= 3 or (len(t.split()) <= 6 and
                                           bool(_FOLLOW_RE.search(tl)))
        topics = [t for t in ctx_topics if not _follow_like(t)] + \
            [t for t in ctx_topics if _follow_like(t)]
        for topic in topics:
            try:
                rt = kb.search(topic[:200], limit=3)
            except Exception:
                continue
            ht = [h for h in (rt.get("results") or []) if not h.get("wiki")]
            if ht and ht[0].get("score", 0) >= 2.0:
                hits, kb_q, focus = ht, (topic[:120] + " " + text), text
                break
    if hits and hits[0].get("score", 0) >= 2.0:
        h = hits[0]
        # v3.27: связный ответ из статьи (как RAG у LLM), а не одно
        # случайное предложение; «расскажи ещё/подробнее» — продолжение
        # статьи без повтора уже сказанного
        more = bool(re.search(r"\b(ещ[её]|подробн\w*|дальше|продолж\w*)\b",
                              low))
        try:
            ca = kb.compose_answer(kb_q, hits,
                                   max_chars=1600 if long_mode else 900,
                                   focus=focus,
                                   lead=(True if (focus and more) else None),
                                   avoid=(ctx_last_bot if (focus and more)
                                          else ""))
        except Exception:
            ca = {"ok": False}
        web_cmd = _web_cmd(text, long_mode) if ca.get("missing_cmd") else None
        if web_cmd:
            return web_cmd[0], Z["web"], web_cmd[1]
        if ca.get("ok") and len(ca.get("text") or "") > 40 and _qf_ok(ca["text"]):
            reply = _qf_ok(ca["text"])
        elif _qf_why and not (focus and more):
            reply = ""                    # v3.126: статья не про то — дальше
        elif focus and more:
            # статья темы уже рассказана целиком — честно, без повтора
            reply = ("Про это я уже рассказала всё, что есть в базе знаний "
                     "(статья «%s»). Спроси о другом аспекте — или дай мне "
                     "больше материалов по теме, и я расскажу подробнее."
                     % h.get("title", "KB"))
        else:
            snippet = str(h.get("snippet") or "").strip()
            reply = ("%s\n_Источник: «%s»_" % (snippet, h.get("title", "KB"))
                     if len(snippet) > 60 and _qf_ok(snippet) else "")
        if reply:
            fw = _formula_from_web(text, reply, long_mode, no_web)
            if fw:
                return fw
            mn_learn("Вопрос: %s\nОтвет: %s" % (text, reply[:1500]),
                     title=str(h.get("title"))[:80], src="диалог+KB")
            return reply, Z["kb"], dict({"article": h.get("id"),
                                         "kb_sources": ca.get("sources") or []},
                                        **({"read_now": _read_now}
                                           if _read_now else {}))

    # 3б) v3.27: СРЕДНЕЕ совпадение с базой знаний. В режиме «длинно»
    # полные статьи KB и так дописывались к ответу (_ensure_long) — поэтому
    # в облаке с галочкой ответ был хорошим, а короткий ответ (бот МАКС)
    # проваливался в «речь из словаря» и сеть — в мусор. Длинный живой
    # вопрос («подскажите, как в ЕМИАС перенести запись к другому врачу»)
    # часто не набирает 2.0, хотя статья — та: если в ней большая часть
    # слов вопроса, отвечаем ИЗ НЕЁ (тем же связным сборщиком).
    if hits and focus is None:
        h = hits[0]
        if h.get("score", 0) >= _KB_MID and (
                h.get("cov", 0) >= 0.75 or
                (h.get("cov", 0) >= 0.55 and h.get("tcov", 0) >= 0.3)):
            try:
                ca = kb.compose_answer(kb_q, hits[:1],
                                       max_chars=1600 if long_mode else 900)
            except Exception:
                ca = {"ok": False}
            web_cmd = _web_cmd(text, long_mode) \
                if ca.get("missing_cmd") else None
            if web_cmd:
                return web_cmd[0], Z["web"], web_cmd[1]
            if ca.get("ok") and len(ca.get("text") or "") > 60 and \
                    _qf_ok(ca["text"]):
                ca["text"] = _qf_ok(ca["text"])
                fw = _formula_from_web(text, ca["text"], long_mode, no_web)
                if fw:
                    return fw
                mn_learn("Вопрос: %s\nОтвет: %s" % (text, ca["text"][:1500]),
                         title=str(h.get("title"))[:80], src="диалог+KB")
                return ca["text"], Z["kb"], {"article": h.get("id"),
                                             "kb_sources": ca.get("sources")
                                             or [], "kb_mid": True}

    # 3в) v3.14 -> v3.27: ИНТЕРНЕТ — заявка владельца: «если чего-то облако
    # или бот Макс не знает, информация собирается из интернета, но не
    # перечисляет источники, а собирает на основе этих источников текст».
    # webfetch.alice читает первые страницы выдачи и СОБИРАЕТ из них
    # связный ответ (multisource.synthesize); сайты в текст не выводятся
    # (они — в info["sources"]). Теперь — для ЛЮБОГО вопроса с темой, а не
    # только с «?»/«что/как…» в начале: в МАКС часто пишут без знаков.
    web_errs = []
    # v3.30: no_web — «ответ мозга» без галочки облака: только свои знания
    web_on = bool(store.settings().get("web_on")) and not no_web
    web_first = bool(store.settings().get("web_first", True))

    def _web():
        try:
            wr, wm = webfetch.alice(q_text if is_follow else text,
                                    long_mode=long_mode)
        except Exception as e:                # сеть не роняет конвейер
            wr, wm = None, {"errors": [webfetch.honest(e)]}
        if wr:
            wr = _qf_ok(wr)               # v3.126: и интернет — по контексту
        if wr:
            srcs = (wm or {}).get("sources") or []
            _web_to_kb(q_text if is_follow else text, wr, srcs)
            return wr, {"sources": srcs,
                        "web": (wm or {}).get("mode") or "synth"}
        web_errs.extend((wm or {}).get("errors") or [])
        return None, None

    web_generic = None
    if web_on and web_first and has_topic:
        wr, winfo = _web()
        if wr and str(wr).startswith("Именно про"):
            # v3.87: интернет честно сказал «именно про это ничего нет» — это
            # общий ответ. Сначала своё: статья базы, у которой в НАЗВАНИИ
            # есть слово вопроса («Архиватор»), и сеть материалов; общий
            # ответ из интернета — только если своего нет.
            web_generic = (wr, winfo)
        elif wr:
            return wr, Z["web"], winfo
    if web_generic and hits:
        own = _kb_title_hit(text, hits, kb_q, long_mode,
                            web_text=web_generic[0])
        if own:
            return own[0], Z["kb"], own[1]

    # 3г) v3.12: РЕЧЬ ИЗ СЛОВАРЯ — фраза из слов словаря по выученным
    # связям (как brain_speak на плате). v3.27: только для реплик БЕЗ темы
    # («привет», «как дела») — на вопросы она отвечала набором слов.
    # v3.99: реплика без темы — отвечает мозг сам (мысль платы / речь мозга
    # облака) с опорой на память диалогов; своя LLM дополняет его мысль
    if not has_topic and not is_follow:
        mb = _mem_brain(text, q_text, long_mode, sender, chat_id, talk=True,
                        route=route)
        if mb:
            return mb[0], mb[1], dict(mb[2], smalltalk=True)

    # 4) сеть материалов: разрядка активации, сборка ответа
    mn_out = None
    try:
        # v3.27: «как дела» — не тема сети; сеть ещё грузится после старта
        # (сотня МБ) — зону пропускаем сразу, а не ждём загрузку
        if (has_topic or is_follow) and _MN is not None and not _core():
            with _MN_LOCK:
                mn_out = mn().write(q_text,
                                    words=90 if not long_mode else 340,
                                    min_chars=_LONG_MIN if long_mode else 0)
    except Exception:
        mn_out = None
    if mn_out and _honest() and not _covers_topic(q_text, mn_out):
        mn_out = None              # v3.129: собранное не про то, о чём спросили
    if mn_out and "Материалов по теме пока нет" not in mn_out and \
            "не нашлось ничего" not in mn_out and \
            "годных предложений мало" not in mn_out:
        # v3.28: ЧЕСТНО — сеть не переучивается на собственной сборке (это
        # пересказ уже выученного: самоподкрепление без новых фактов);
        # в сеть идёт вопрос, а пара «вопрос/ответ» — после 👍 человека
        mn_learn(text, title=text[:60], src="вопрос")
        return mn_out, Z["mn"], {}
    # v3.99: «мозг + своя LLM» — статьи нет: мозг отвечает сам, с опорой на
    # память (сеть материалов, память диалогов, заметки, память платы)
    mb = None
    if _core() and (has_topic or is_follow):
        mb = _mem_brain(text, q_text, long_mode, sender, chat_id,
                        route=route)
        if mb and mb[1] == Z["membrain"]:       # своё знание из памяти
            if _qf_ok(mb[0]):                   # v3.126: по контексту вопроса
                return mb
            mb = None
    if web_generic:                       # своего нет — общий ответ интернета
        return web_generic[0], Z["web"], web_generic[1]

    # 5) ИНТЕРНЕТ, если web_first выключен (тогда — после сети материалов).
    #    Честные причины простоя доходят до финального «не знаю».
    if web_on and not web_first and has_topic:
        wr, winfo = _web()
        if wr:
            return wr, Z["web"], winfo
    if mb:                                # памяти нет — мысль мозга + LLM
        if web_errs:
            mb = (mb[0], mb[1], dict(mb[2], web_errors=web_errs[:2]))
        return mb

    # 5б) v3.27: реплика без темы («привет», «как дела», «спасибо»), а речь
    # из словаря промолчала — честный короткий ответ вместо «Не знаю. По
    # «привет» ни в материалах…» (и без очереди любопытства на «привет»)
    if not has_topic and not is_follow:
        try:                              # v3.96: без заготовок — своя LLM или честно
            import smalltalk
            t_ = smalltalk._llm_reply(text)
            if t_:
                return (t_ + "\n\n_— своими словами написала своя LLM (без "
                        "опоры на факты)_"), Z["talk"], {"smalltalk": True,
                                                        "by": "своя LLM"}
            return smalltalk.NO_ANSWER, Z["talk"], {"smalltalk": True,
                                                    "by": "нет ответа"}
        except Exception:
            return ("Своя LLM пока не смогла ответить на это своими словами."
                    ), Z["talk"], {}

    # 6) память владельца (заметки) — последняя надежда облака
    notes = [n for n in store.notes() if _overlap(text, n["text"])][:2]
    if notes:
        reply = "Из заметок в памяти:\n" + "\n".join(
            "• %s" % n["text"][:160] for n in notes)
        return reply, Z["mem"], {}

    # 6б) v3.11: СМЕЛОСТЬ ПРЕДПОЛОЖЕНИЙ — прежде чем «не знаю», собрать
    # слабые улики (похожая статья KB, ассоциации сети, заметки) и честно
    # предположить: догадка помечена как гипотеза и уходит на проверку
    # любопытству. Подтверждённая владельцем — выучится в сеть.
    # (голое «верно/неверно» без ждущих гипотез — не тема для догадок)
    if not _VER_Q.match(low) and has_topic and not _core():
        try:
            import hypo
            hb = hypo.build(text)
            if hb:
                mn_learn(text, title=text[:60], src="вопрос")
                return hb["reply"], Z["hypo"], {"hypo": hb["conf"]}
        except Exception:
            pass

    # 7) честное «не знаю» + живая плата (если маршрут auto и платы онлайн)
    mn_learn(text, title=text[:60], src="вопрос")       # вопрос — тоже материал
    # v3.10: «не знаю» — ЕДА ДЛЯ ЛЮБОПЫТСТВА: тема попадает в очередь,
    # нейроны сами прочитаю статью Википедии и выучат её
    # (v3.11: голый вердикт «верно/неверно» без гипотез в очередь не идёт)
    try:
        import curiosity
        if len(text) > 3 and not _VER_Q.match(low):
            curiosity.want(text, why="мозг не знал ответа")
    except Exception:
        pass
    board_add = None
    # v3.29: с deliver плату спрашивает единый мозг (think -> _unify_step)
    if (route == "auto" or (_core() and route != "cloud_only")) and \
            chat_id and ask_late and not _honest():
        board_add = ask_board(text, chat_id=chat_id, frm=sender,
                              long_mode=long_mode)
    reply = ("Не знаю. По «%s» нет ни статьи в базе знаний, ни "
             "подтверждённой памяти%s. Дай статью или файл по теме (или "
             "напиши «правильный ответ: …») — выучу и отвечу."
             % (text[:60], "" if web_on else ", а интернет выключен"))
    if web_errs:
        reply += "\n\n_Интернет не помог: %s._" % _short_err(web_errs[0])
    if _qf_why:             # v3.126: честно — почему найденное не подошло
        reply += "\n_Найденное не подошло к вопросу: %s%s._" % (
            _qf_why[0], (" (эпоха вопроса: %s)" % _qf.get("era_text"))
            if _qf and _qf.get("era_text") else "")
    try:                    # v3.99: почему молчит мозг neuro-12 — честно
        import bridge
        if bridge.enabled() and bridge.STATE.get("online") is False:
            reply += ("\n_Мозг neuro-12 (плата) не на связи — проверь "
                      "«Устройства»._")
    except Exception:
        pass
    info = {}
    if board_add and board_add.get("ok"):          # v3.97: без служебной фразы
        info = {"pending_mid": board_add["id"], "brain": board_add["brain"]}
    return reply, Z["sys"], info


# ------------------------------- словарь ------------------------------------
def _dict_load_words():
    """Все слова облачных словарей: ru_top/extra (words), ru_pos (части
    речи) и встроенный мини-словарь учителя. Возврат:
    (множество слов, {слово: часть речи}, {источник: сколько слов})."""
    words, pos, srcs = set(), {}, {}
    for name in ("ru_top", "extra"):
        d = store.load_json("dicts/%s.json" % name, {}) or {}
        ws = [str(w).lower() for w in (d.get("words") or [])
              if w and 2 < len(str(w)) < 24]
        if ws:
            words.update(ws)
            srcs[name] = len(ws)
    d = store.load_json("dicts/ru_pos.json", {}) or {}
    n_pos = 0
    for k, label in _POS_NAMES.items():
        ws = [str(w).lower() for w in (d.get(k) or []) if w]
        if ws:
            words.update(ws)
            n_pos += len(ws)
            for w in ws:
                pos.setdefault(w, label)
    if n_pos:
        srcs["ru_pos"] = n_pos
    try:
        import teacher
        words.update(w for w in teacher._BASIC if 2 < len(w) < 24)
        srcs["встроенный"] = len(teacher._BASIC)
    except Exception:
        pass
    return words, pos, srcs


def dict_stats():
    """Сводка словарей для панели «Зоны»."""
    words, _pos, srcs = _dict_load_words()
    return {"words": len(words), "src": srcs}


def dict_lookup(word):
    """Зона «Словарь»: есть ли слово в словарях облака; для ненайденного —
    похожие слова по общей основе. Возврат:
    {found, word, pos, total, src, close}.

    Примечание: слово может быть знакомо по ОСНОВЕ даже если точной формы
    нет в словаре (словари хранят начальные формы). Ищем и точное совпадение,
    и совпадение основы (первые 4 буквы)."""
    raw = str(word or "").lower().strip(" ?.!…")
    # «что значит слово кот» -> кот (срезаем служебное «слово/слова»)
    raw = re.sub(r"^\s*слов[оаеуыи]?\s+", " ", raw)
    m = re.search(r"[а-яёa-z-]{2,}", raw)
    if not m:
        return {"found": False, "word": raw[:24], "pos": "", "total": 0,
                "src": {}, "close": []}
    w = m.group(0).strip("-")
    words, pos, srcs = _dict_load_words()
    total = len(words)
    if w in words:
        return {"found": True, "word": w, "pos": pos.get(w, ""),
                "total": total, "src": srcs, "close": []}
    # совпадение по основе: «зонтиком» ~ «зонтик»
    stem = w[:4]
    exact_stem = [x for x in words if x.startswith(stem)]
    if exact_stem:
        return {"found": True, "word": w, "pos": pos.get(w, ""),
                "total": total, "src": srcs, "close": sorted(exact_stem)[:4],
                "stem": True}
    close = sorted(x for x in words if x.startswith(stem)
                   and abs(len(x) - len(w)) <= 3)[:6]
    return {"found": False, "word": w, "pos": "", "total": total,
            "src": srcs, "close": close}


# --------------------------- статистика зон ----------------------------------
def bump_zone(zone):
    """Отметить ответ зоны (панель «Зоны мысли»: сколько ответила, когда)."""
    if not zone:
        return
    with store.LOCK:
        st = store.load_json("zone_stats.json", {}) or {}
        item = st.get(zone) or {"n": 0, "last": 0}
        item["n"] = int(item.get("n") or 0) + 1
        item["last"] = time.time()
        st[zone] = item
        store.save_json("zone_stats.json", st)


def zone_stats():
    """Статистика ответов зон (для /api/zones)."""
    return store.load_json("zone_stats.json", {}) or {}


def _overlap(a, b):
    sa = set(textutils.stem(w) for w in textutils.WORD_RE.findall(a.lower()))
    sb = set(textutils.stem(w) for w in textutils.WORD_RE.findall(b.lower()))
    return len(sa & sb) >= 2


def _kb_title_hit(text, hits, kb_q, long_mode=False, web_text=""):
    """v3.87/v3.93: статья базы вместо общего ответа интернета «именно про …
    ничего нет». v3.93: одного слова вопроса в названии мало («Бештау» —
    статья про гору на вопрос «как установить архиватор на компьютер
    бештау»): статья должна покрывать БОЛЬШУЮ часть смысловых слов вопроса
    (≥ 50 % в тексте), и из подходящих берётся та, что покрывает больше.
    -> (текст, info) | None."""
    try:
        qst = set(kb._stems(text)) - kb._STOP_STEMS
        if not qst:
            return None
        # «Именно про «бештау» … ничего нет» — интернет сам назвал уточнение,
        # которого нет: статья, совпавшая ТОЛЬКО им (гора Бештау), — не та
        m_ = re.search(r"Именно про\s+«([^»]+)»", str(web_text or ""))
        fst = set(kb._stems(m_.group(1))) if m_ else set()
        core_q = (qst - fst) or qst
        # v3.94: статья выбирается по ГЛАВНОМУ слову в названии — по редкости
        # в базе (idf): «Архиватор» важнее «…на одном компьютере», где совпало
        # лишь частое слово «компьютер»; покрытие текста — второй признак
        w = {s: kb.stem_idf(s) for s in core_q}
        wsum = sum(w.values()) or 1.0
        best, bcov, bkey = None, 0.0, (-1.0, -1.0)
        for h in (hits or [])[:5]:
            if h.get("wiki") or float(h.get("score") or 0) < 0.3:
                continue          # слово вопроса в названии — главный признак
            tst = set(kb._stems(str(h.get("title") or "")))
            inter = core_q & tst
            if not inter:
                continue
            art = kb.article(h.get("id")) or {}
            ast = set(kb._stems(str(art.get("title") or "") + " " +
                                str(art.get("text") or "")[:20000]))
            cov = sum(w[s] for s in core_q & ast) / wsum
            key = (max(w[s] for s in inter), cov)
            if key > bkey:
                best, bcov, bkey = h, cov, key
        if best is None or bcov < (0.3 if fst else 0.5):
            return None
        ca = kb.compose_answer(kb_q, [best],
                               max_chars=1600 if long_mode else 900)
        if ca.get("ok") and len(ca.get("text") or "") > 60:
            return ca["text"], {"article": best.get("id"),
                                "kb_sources": ca.get("sources") or [],
                                "kb_over_web": True,
                                "kb_cov": round(bcov, 2)}
    except Exception:
        pass
    return None


def _conf_answer(q, hits, long_mode=False):
    """v3.174: ответ из ИСТОЧНИКОВ ПО ССЫЛКАМ (sources.py: Confluence, вики,
    сайты, документы) — если в базе нет статьи про тему. В базу не пишется."""
    try:
        import sources
        import readlike
        if not sources.enabled() or readlike.kb_has(q, hits):
            return None
        if not readlike.wants_article(q) and not re.search(
                r"\b(?:как|где|что|почему|ошибк)", str(q), re.I):
            return None
        r = sources.answer(q, long_mode=long_mode)
    except Exception as e:
        store.log("ИСТОЧНИКИ", "не удалось: %s" % e)
        return None
    if not r.get("ok"):
        return None
    reply = "%s\n\n_Источник: %s «%s» — %s (в базу знаний не сохраняется)_" % (
        r["text"], r["src"], r["title"], r["url"])
    return reply, Z["web"], {"confluence": {"title": r["title"], "url": r["url"]},
                             "sources": [{"name": r["src"], "note": r["title"],
                                          "url": r["url"]}]}


def _read_first(q, hits):
    """v3.165: как «Любопытство» — статьи про тему в базе нет: прочитать её
    (readlike) и заново найти в базе; прочитанная статья — первой.
    -> (hits, rr | None)."""
    try:
        import readlike
        rr = readlike.read_for_answer(q, hits)
    except Exception as e:
        store.log("ЧТЕНИЕ", "не удалось: %s" % e)
        return hits, None
    if not rr.get("read"):
        return hits, None
    r = kb.search(q, limit=4)
    nh = [h for h in (r.get("results") or []) if not h.get("wiki")]
    first = [h for h in nh if h.get("title") == rr.get("title")]
    if not first:
        if rr.get("id"):
            first = [{"id": rr["id"], "title": rr.get("title"), "score": 2.5,
                      "tcov": 1.0, "cov": 1.0, "snippet": ""}]
    if first:
        first[0] = dict(first[0], score=max(2.0, float(first[0].get("score") or 0)))
    nh = first + [h for h in nh if h.get("title") != rr.get("title")]
    return nh, rr


def find_topic(q, long_mode=False):
    """«что такое X / найди X»: KB -> НЕСКОЛЬКО источников сети «как у
    Алисы» (v3.13): выжимка + ПОДПИСАННЫЙ список источников; вики-статья
    сохраняется в KB и учит сеть."""
    r = kb.search(q, limit=3)
    hits = [h for h in (r.get("results") or []) if not h.get("wiki")]
    _cr = _conf_answer("что такое " + q, hits, long_mode)   # v3.168: Confluence
    if _cr:
        return _cr
    hits, rr = _read_first(q, hits)          # v3.165: прочитать, как любопытство
    if hits and hits[0].get("score", 0) >= 2.0:
        h = hits[0]
        try:                              # v3.27: связный ответ из статьи
            ca = kb.compose_answer("что такое " + q, hits,
                                   max_chars=1600 if long_mode else 900)
        except Exception:
            ca = {"ok": False}
        if ca.get("ok"):
            return ca["text"], Z["kb"], dict({"article": h.get("id")},
                                             **({"read_now": rr} if rr else {}))
        snippet = str(h.get("snippet") or "").strip()
        if snippet:
            return "%s\n_Источник: «%s»_" % (snippet, h.get("title", "KB")), \
                Z["kb"], {"article": h.get("id")}
    errs = []
    if store.settings().get("web_on"):
        try:
            reply, meta = webfetch.alice("что такое " + q,
                                         long_mode=long_mode)
        except Exception as e:            # v3.12.1: только честная причина
            reply, meta = None, {"errors": [webfetch.honest(e)]}
        errs = (meta or {}).get("errors") or errs
        if reply and str(reply).startswith("Именно про"):
            own = _kb_title_hit(q, hits, "что такое " + q, long_mode,
                                web_text=reply)
            if own:
                return own[0], Z["kb"], own[1]
        if reply:
            srcs = (meta or {}).get("sources") or []
            if srcs and "википедия" in str(srcs[0].get("name", "")).lower():
                try:
                    text = webfetch.wiki_extract(srcs[0].get("note") or q)
                    if len(text) > 200:
                        kb.add_article(srcs[0]["note"], text,
                                       source="Википедия (авто)")
                except Exception:
                    pass
            # v3.27: ответ — текст, собранный из страниц (и в «длинно» тоже:
            # раньше тут подменялся текстом сети материалов)
            return reply, Z["web"], {"sources": srcs}
    # v3.8.1: честно объясняем, ПОЧЕМУ пусто — базы нет или сети молчат
    if not store.settings().get("web_on"):
        tail = " Интернет выключен в настройках (web_on)."
    elif errs:
        tail = " Сети тоже молчат: %s." % "; ".join(errs[:2])
    else:
        tail = ""
    return ("По «%s» ничего не нашлось — ни в базе знаний, ни в сети.%s "
            "Попробуй переформулировать." % (q, tail)), Z["sys"], \
        {"nothing": True}


def open_topic(q, long_mode=False):
    """v3.10: «открой X / прочитай X» — ОТКРЫТЬ СТАТЬЮ: сначала база
    знаний, затем Википедия; найденная статья СОХРАНЯЕТСЯ в KB (источник
    «Википедия (открыто)») и учит сеть. Ответ — выжимка + ссылка."""
    q = str(q or "").strip()[:120]
    if not q:
        return "Что открыть? Скажи тему: «открой Чёрная дыра».", Z["curiosity"], {}
    r = kb.search(q, limit=2)
    hits = [h for h in (r.get("results") or []) if not h.get("wiki")]
    if hits and hits[0].get("score", 0) >= 2.0:
        h = hits[0]
        snippet = str(h.get("snippet") or "").strip()
        try:                              # v3.27: начало статьи по порядку
            ca = kb.compose_answer("что такое " + q, hits[:1], max_chars=700)
            if ca.get("ok"):
                snippet = ca["text"].rsplit("\n_Источник", 1)[0]
        except Exception:
            pass
        if snippet:
            return ("Открыл статью «%s» (она уже была в базе знаний):\n%s"
                    % (h.get("title", "KB"), snippet[:600])), \
                Z["curiosity"], {"article": h.get("id"), "opened": "kb"}
    if not store.settings().get("web_on"):
        return ("Открыть «%s» не могу: интернет выключен в настройках "
                "(web_on). Включи — и я открою статью из Википедии." % q), \
            Z["curiosity"], {}
    try:                                  # v3.13: мульти-источники «как у Алисы»
        reply, meta = webfetch.alice(q)
    except Exception as e:                # v3.12.1: сеть не роняет «открой»
        reply, meta = None, {"errors": [webfetch.honest(e)]}
    errs = (meta or {}).get("errors") or []
    if reply:
        srcs = (meta or {}).get("sources") or []
        if srcs and "википедия" in str(srcs[0].get("name", "")).lower():
            text = webfetch.wiki_extract(srcs[0].get("note") or q)
            if len(text) > 200:
                kb.add_article(srcs[0]["note"], text,
                               source="Википедия (открыто)")
                return ("Открыл:\n%s\n\nСтатья сохранена в базу знаний."
                        % reply), Z["curiosity"], {"opened": "web",
                                                   "sources": srcs}
        return reply, Z["curiosity"], {"opened": "web", "sources": srcs}
    tail = (" Сети тоже молчали: %s." % "; ".join(errs[:2])) if errs else ""
    return ("Не смог открыть «%s»: ни в базе знаний, ни в интернете.%s"
            % (q, tail)), Z["curiosity"], {}


def news_digest():
    """v3.18: «что нового ты узнала» — честный отчёт о собственном росте:
    что прочитало любопытство, какие функции написала себе сама, как
    растут воображение и сеть материалов, сколько в базе знаний."""
    lines = []
    try:
        import curiosity
        cs = curiosity.status()
        last = ""
        for j in (cs.get("journal") or []):
            if j.get("ok"):
                last = str(j.get("title") or j.get("topic") or "")[:60]
                break
        lines.append("Любопытство прочитало статей: %d (тем нашла сама: %d, "
                     "уже знала: %d)%s" % (cs.get("read") or 0,
                                           cs.get("self_found") or 0,
                                           cs.get("skipped") or 0,
                                           ("; последнее чтение — «%s»" % last)
                                           if last else ""))
        q = cs.get("queue") or []
        if q:
            lines.append("Хочу знать дальше: %s." %
                         ", ".join("«%s»" % x for x in q[:4]))
    except Exception:
        pass
    try:
        import autocode
        st = autocode.stats()
        if st.get("written"):
            lines.append("Написала себе функций: %d (заготовок в хозяйстве: "
                         "%s) — они отвечают в чате сами." %
                         (st["written"], st["templates"]))
        else:
            lines.append("Самокод готов писать себе функции: заготовок %s "
                         "(скажи «напиши себе функцию …»)." % st["templates"])
    except Exception:
        pass
    try:
        import tinyllm
        ls = tinyllm.status()
        if ls:
            lines.append("Воображение (мини-LLM): шагов %s из %s, словарь %s "
                         "слов%s." % (ls.get("steps"), ls.get("total_steps"),
                                      ls.get("vocab"),
                                      ", учится сейчас"
                                      if ls.get("training") else ""))
    except Exception:
        pass
    st = store.stats()
    lines.append("База знаний: %d статей, заметок в памяти %d, функций в "
                 "реестре %d." % (st.get("kb_articles") or 0,
                                  len(store.notes()),
                                  st.get("skills") or 0))
    try:
        rd = mn_readings()
        lines.append("Сеть материалов: узлов %s, связей %s, материалов %s."
                     % (rd.get("nodes", "?"), rd.get("links", "?"),
                        rd.get("docs", "?")))
    except Exception:
        pass
    head = "Вот что я узнала и чем выросла за последнее время:"
    return head + "\n" + "\n".join("• " + x for x in lines)


def curiosity_report(stt):
    """Человеческий отчёт о любопытстве (чат: «любопытство»)."""
    state = "включено" if stt.get("on") else "выключено"
    if not stt.get("web_on"):
        tail = " Но интернет выключен (web_on) — читать пока неоткуда."
    elif stt.get("backoff"):
        tail = " Сейчас пауза %d c после сетевого сбоя (бэкофф)." \
               % int(stt.get("backoff") or 0)
    else:
        tail = ""
    out = ("Любопытство нейронов %s: прочитано статей %d, неудач %d, "
           "уже знаю (без чтения) %d, чтение раз в %d c.%s"
           % (state, int(stt.get("read") or 0), int(stt.get("failed") or 0),
              int(stt.get("skipped") or 0),
              int(stt.get("every") or 120), tail))
    q = stt.get("queue") or []
    if q:
        out += "\nХочу знать: %s." % ", ".join("«%s»" % x for x in q[:5])
    jr = stt.get("journal") or []
    if jr:
        out += "\nПоследнее чтение: " + "; ".join(
            "«%s»" % (j.get("title") or j.get("topic") or "?")[:40]
            for j in jr[:3])
    return out


# --------------------- длинные ответы, режим «длинно» ------------------------
def _fresh_slice(body, existing, want):
    """Кусок ПОЛНОГО текста статьи, которого ещё нет в ответе: идём
    абзацами, пропуская уже вошедшее (сниппет и повторившееся), набираем
    до want знаков. Только реальный текст статьи — ничего не сочиняем."""
    norm = " ".join(str(existing or "").split()).lower()
    out, got = [], 0
    for para in re.split(r"\n\s*\n|(?<=[.!?])\s+(?=[А-ЯЁA-Z])",
                         str(body or "")):
        para = str(para or "").strip()
        if len(para) < 40:
            continue
        if " ".join(para.split()).lower()[:90] in norm:
            continue
        out.append(para)
        got += len(para) + 2
        if got >= max(0, want):
            break
    return "\n\n".join(out)


def _kb_long_blocks(q_text, reply, want_chars):
    """v3.15: добор для «длинно» — ПОЛНЫЕ тексты статей KB по теме
    (сниппет в ответе — это несколько строк, у статьи есть продолжение).
    Каждый блок подписан источником. Возврат: список блоков-строк."""
    blocks = []
    if want_chars <= 0:
        return blocks
    try:
        r = kb.search(q_text, limit=3)
    except Exception:
        return blocks
    got = 0
    for h in (r.get("results") or []):
        if h.get("wiki") or got >= want_chars:
            continue
        # v3.27: только статьи по теме (раньше — любые три первых, и к
        # ответу прилипали посторонние статьи)
        if h.get("score", 0) < _KB_MID or h.get("cov", 0) < 0.55:
            continue
        aid = h.get("id")
        art = kb.article(aid) if aid else None
        if not art:
            continue
        title = str(art.get("title") or "KB")
        frag = _fresh_slice(str(art.get("text") or ""),
                            str(reply) + "\n" + "\n".join(blocks),
                            want_chars - got)
        if frag:
            blocks.append("Подробнее из статьи «%s»:\n%s" % (title, frag))
            got += len(frag)
    return blocks


def _ensure_long(reply, zone, info, q_text):
    """v3.15: режим «длинно» — ответ не короче _LONG_MIN знаков, ЧЕСТНО:
    добираем только реальными материалами (полные статьи KB по теме,
    текст сети материалов). Нечего добирать — честная пометка, что
    материалов на длинный ответ не хватает (выдумывать не станем)."""
    reply = str(reply or "")
    if len(reply) >= _LONG_MIN or zone not in _ENRICH_ZONES:
        return reply
    # v3.30: заявка владельца «пусть не боится говорить, даже если знаков
    # меньше 1500, а то шлёт не то». Добираем ТОЛЬКО тем, что по делу
    # (есть фокус вопроса и его слова); нечем — короткий точный ответ
    # остаётся как есть, без пометок про «не хватило до 1500».
    try:
        import focus
        f_words = focus.terms(q_text)
    except Exception:
        f_words = []
    q_st = {s for s in kb._stems(q_text) if s not in kb._STOP_STEMS}

    def _on_topic(t):
        st = set(kb._stems(t))
        if f_words and not focus.covered(t, f_words):
            return False
        return len(st & q_st) >= max(1, min(2, len(q_st)))

    add = [b for b in _kb_long_blocks(q_text, reply, _LONG_MIN - len(reply))
           if _on_topic(b)]
    if zone not in (Z["mn"], Z["gen"]):
        try:
            with _MN_LOCK:
                net = mn().write(q_text, words=340)
            if net and "Материалов по теме пока нет" not in net \
                    and "не нашлось ничего" not in net \
                    and "годных предложений мало" not in net and \
                    _on_topic(net):
                add.append(net)
        except Exception:
            pass
    if not add:
        return reply
    return reply + "\n\n" + "\n\n".join(add)


def write_article(topic):
    """«статья <тема>»: v3.18 — ИНСТРУМЕНТ САМОСТОЯТЕЛЬНОГО ПИСЬМА
    (writer.write_long): мозг сам собирает развёрнутое письмо из ВСЕХ
    своих материалов (статьи KB целиком, сеть, словарь, память), с
    планом, связками и честными подписями источников. Цель — не короче
    _LONG_MIN знаков. Если writer недоступен — старый путь сети.
    Свои письма и выжимки сети не кормятся обратно в KB знаниями:
    сохраняется только СОСТОЯВШЕЕСЯ письмо с источниками (v3.18)."""
    topic = str(topic or "").strip()[:200]
    out = ""
    try:
        import writer
        out = writer.write_long(topic, min_chars=_LONG_MIN) or ""
    except Exception:
        out = ""
    if len(out) > 300:
        # сохраняем только состоявшееся письмо (есть источники и объём)
        if len(out) > 700 and "из 0 источников" not in out:
            try:
                ttl = "Сочинение: %s" % topic[:60]
                if not any(str(a.get("title") or "") == ttl
                           for a in kb.list_articles(limit=400)):
                    kb.add_article(ttl, out,
                                   source="самостоятельное письмо")
            except Exception:
                pass
        return out
    # резерв: сеть материалов (старый путь v3.15)
    try:
        with _MN_LOCK:
            out = mn().write(topic, words=340, min_chars=_LONG_MIN) or ""
    except Exception as e:
        return "Генератор не справился: %s" % e
    return out


def free_think(topic=""):
    """«мысль»: свободная генерация по горячим узлам сети."""
    rd = mn_readings()
    hot = rd.get("hot") or rd.get("top") or []
    if not hot:
        return ("Сеть только просыпается: узлов пока %s. Пообщайся со мной "
                "или загрузи материалы — и я высказываюсь по горячим узлам."
                % rd.get("nodes", 0)), Z["mn"], {}
    t = topic or ", ".join(hot[:4])
    try:
        with _MN_LOCK:
            return mn().write(t, words=200), Z["mn"], {}
    except Exception:
        return "Мысль не собралась — сеть занята.", Z["mn"], {}


def board_mind(topic=""):
    """v3.9: ГЕНЕРАТОР НА ТОМ, ЧТО ДУМАЕТ ПЛАТА. Облако берёт последние
    мысли плат (лента «Мысли плат»: мост 127.0.0.1:7000 + мозг-почта),
    учится на них в сети материалов и пишет связный текст об их темах.
    Это «соединение облака с нейросетью платы»: что плата думает — тем
    облако и живёт. Мысли плат попадают в сеть СРАЗУ при получении
    (bridge._mirror_thought / /api/chat/thought), так что сеть уже
    «пропитана» темами платы к моменту вопроса."""
    # v3.28: свои мысли облака (режим «Разговор») — не мысли плат
    ths = [t for t in store.thoughts_list(60)
           if str(t.get("board") or "") != "облако"][:40]
    if not ths:
        return ("Плата пока не думала вслух: лента мыслей пуста. Мысли "
                "приходят в облако сами (мост на 127.0.0.1:7000 и "
                "мозг-почта) — включи плату, и я перескажу её мысли и "
                "подумаю о них вслух."), Z["mind"], {}
    texts = [str(t.get("text") or "").strip() for t in ths[:20]]
    texts = [t for t in texts if t]
    seed = (topic or " ".join(texts[:8]))[:240]
    body = ""
    if texts:
        try:
            with _MN_LOCK:
                body = mn().write(seed, words=180,
                                  extra=" ".join(texts)[:2500])
        except Exception:
            body = ""
    low_body = (body or "").lower()
    if body and "материалов по теме пока нет" not in low_body and \
            "не нашлось ничего" not in low_body and \
            "годных предложений мало" not in low_body:
        reply = body
    else:
        reply = ("Вот о чём плата думала в последнее время:\n" + "\n".join(
            "• «%s» (%s)" % (t[:110], (ths[i].get("board") or "?")[:20])
            for i, t in enumerate(texts[:8])))
        reply += ("\n\nПо этим темам сеть материалов пока мало знает — "
                  "продолжай диалог с платой, и её мысли станут её "
                  "материалами.")
        return reply, Z["mind"], {"thoughts": len(ths)}
    head = "Мысли плат за последнее время:\n" + "\n".join(
        "• «%s»" % t[:100] for t in texts[:5])
    return head + "\n\n" + body, Z["mind"], {"thoughts": len(ths)}


# ------------------------- генератор по заданиям плат -----------------------
def gen_from_queue():
    """Взять задание плат (/api/gen/task) и написать текст по материалам."""
    task = store.gen_task_take()
    if not task:
        return None
    topic = str(task.get("text") or "")[:200]
    if not topic:
        return None
    out = write_article(topic)
    return {"task": task, "text": out}


# ------------------------------- фоновое ------------------------------------
# --------------- v3.71: обучение мозга на всей базе после запуска -------------
_BT = {"queue": None, "total": 0, "done": 0, "new": 0, "running": False,
       "started": 0.0, "finished": 0.0, "error": ""}


def brain_train_status():
    q = _BT["queue"]
    return {"total": _BT["total"], "done": _BT["done"], "new": _BT["new"],
            "running": bool(_BT["running"]), "left": len(q) if q else 0,
            "finished": _BT["finished"], "error": _BT["error"],
            "auto": bool(store.settings().get("brain_train_auto", True))}


def _bt_load():
    if _BT.get("done_ids") is None:
        d = store.load_json("brain_train.json", None) or {}
        _BT["done_ids"] = set(d.get("done") or [])
        _BT["new"] = int(d.get("new") or 0)
        _BT["finished"] = float(d.get("finished") or 0)
    return _BT["done_ids"]


def _bt_save():
    try:
        store.save_json("brain_train.json", {
            "done": sorted(_BT.get("done_ids") or []), "new": _BT["new"],
            "finished": _BT["finished"], "ts": time.time()})
    except Exception:
        pass


def brain_train_start(force=False):
    """Поставить в очередь статьи базы знаний для сети материалов (мозга).
    v3.88: пройденные статьи помнятся на диске (brain_train.json) — после
    обновления/перезапуска обучение ПРОДОЛЖАЕТСЯ, а не начинается заново.
    force=True (кнопка) — пройти всю базу ещё раз."""
    if _BT["running"] and not force:
        return False
    import kb
    done = _bt_load()
    if force:
        done.clear()
        _BT["new"] = 0
    ids = [a.get("id") for a in (kb.list_articles(
        limit=1000000, include_devices=True) or []) if a.get("id")]
    left = [i for i in ids if i not in done]
    _BT.update(queue=left, total=len(ids), done=len(ids) - len(left),
               running=bool(left), started=time.time(), error="")
    _bt_save()
    return bool(left)


def persist_all():
    """v3.88: сохранить всё обученное перед перезапуском (обновление):
    сеть материалов, ход обучения мозга, память вопросов, своя LLM."""
    out = []
    try:
        out.append(("сеть", mn_save(force=True)))
    except Exception:
        pass
    try:
        _bt_save()
        out.append(("ход обучения мозга", True))
    except Exception:
        pass
    try:
        import qalearn
        qalearn.save(force=True)
    except Exception:
        pass
    try:
        import tinyllm
        m = tinyllm.model()
        if m.p and m.steps:
            m.save(tinyllm._state_path())
            out.append(("LLM", True))
    except Exception:
        pass
    return out


def _brain_train_tick(batch=6):
    import kb                   # v3.172: до первого использования (иначе UnboundLocalError)
    if _BT["queue"] is None:
        try:                                   # v3.74: один раз — убрать мусорный кэш
            nb = kb.purge_bad_web()
            if nb:
                store.log("БАЗА", "удалено ответов-кэша из интернета без слов вопроса: %d" % nb)
        except Exception:
            pass
        if not store.settings().get("brain_train_auto", True):
            return 0
        brain_train_start()                    # первый тик после запуска
    q = _BT["queue"]
    if not q:
        if _BT["running"]:
            _BT.update(running=False, finished=time.time())
        return 0
    n = 0
    try:                      # статья могла уже попасть в сеть при добавлении
        have = {str(d.get("title") or "")[:120] for d in mn().docs}
    except Exception:
        have = set()
    t_tick = time.time()
    while q and n < batch and time.time() - t_tick < 3.0:   # не занимать GIL надолго
        aid = q.pop()
        n += 1
        _BT["done"] += 1
        _bt_load().add(aid)
        try:
            art = kb.article(aid)
            if not art or not art.get("text"):
                continue
            if str(art.get("title") or "")[:120] in have:
                continue
            try:                  # v3.144: сообщение-вопрос — не материал
                import textutils as _tu
                if _tu.question_only(str(art["text"])[:1500]):
                    continue
            except Exception:
                pass
            if not _MN_LOCK.acquire(timeout=2.0):
                q.append(aid)
                _BT["done"] -= 1
                _bt_load().discard(aid)
                break
            try:
                _mn_apply_limits()
                r = mn().learn(str(art["text"])[:20000],
                               title=str(art.get("title") or "")[:120],
                               src="база знаний", did="kb:" + str(aid))
                if r and r.get("doc"):
                    _BT["new"] += 1
                    _MN_DIRTY[0] = True
            finally:
                _MN_LOCK.release()
        except Exception as e:
            _BT["error"] = "%s: %s" % (type(e).__name__, e)
    if not q:
        _BT.update(running=False, finished=time.time())
    # ход сохраняется вместе с сетью (mn_save): отметка «пройдено» не
    # опередит сохранение самой сети
    return n


def _worker():
    """Фон облака: дворник писем + автосохранение сети + генерация по
    заданиям плат + ЛЮБОПЫТСТВО (v3.10: нейроны сами читают Википедию)."""
    while True:
        time.sleep(20)
        try:
            mail_sweep()
        except Exception:
            pass
        try:
            import teacher
            teacher.tour_tick()                # экскурсия: очередной урок
        except Exception:
            pass
        try:
            import curiosity
            curiosity.tick()                   # любопытство: очередная статья
        except Exception:
            pass
        try:                                   # v3.133: память пар — на вдохе
            import breath
            import qalearn
            if breath.inhale("Обучение на ответах"):
                qalearn.consolidate()
        except Exception:
            pass
        try:
            import wordmind
            wordmind.tick()                    # v3.12: сеть речи из словаря
        except Exception:
            pass
        try:
            import tinyllm
            tinyllm.ensure_training()          # v3.16: мини-LLM дорастает в фоне
        except Exception:
            pass
        try:                                   # v3.95: читать ВСЕ мысли плат
            import boardmind
            boardmind.ingest()
        except Exception:
            pass
        try:                                   # v3.71: мозг учится на всей базе
            _brain_train_tick()
        except Exception as e:
            _BT["error"] = "%s: %s" % (type(e).__name__, e)
        try:                                   # v3.27: очередь обучения
            _mn_drain(50)
        except Exception:
            pass
        try:                                   # v3.27: снимком, раз в 3 мин
            if time.time() - _LAST_SAVE[0] > _MN_SAVE_EVERY:
                mn_save()
        except Exception:
            pass
        try:                                   # v3.28: память вопросов/ответов
            import qalearn
            qalearn.save()
        except Exception:
            pass


def _preload():
    """v3.27: загрузить сеть материалов при старте В ФОНЕ (на полной сети —
    сотня МБ JSON): раньше её грузил первый вопрос, и он «висел»."""
    try:
        with _MN_LOCK._l:
            mn()
        mn_size()
    except Exception:
        pass


def start_background():
    threading.Thread(target=_preload, daemon=True, name="mn-load").start()
    threading.Thread(target=_worker, daemon=True).start()


# код-проверка навыков (мозг публикует функции после прогона здесь)
def verify_skill_code(code, tests):
    for t in (tests or [])[:3]:
        try:
            import teacher
            teacher.instruct_exec(str(t)[:100], "code")
        except Exception:
            pass
        ok, out = sandbox.run_code(code, str(t)[:500], timeout=30)
        if not ok:
            return False, out
    return True, ""
