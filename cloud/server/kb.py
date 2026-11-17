# -*- coding: utf-8 -*-
# ===========================================================================
# kb.py — БАЗА ЗНАНИЙ ОБЛАКА (только stdlib)
# ---------------------------------------------------------------------------
#   kb/common/<id>.json        — общие статьи (мозг пишет СЮДА свои статьи,
#                                владелец добавляет через панель/файлы)
#   kb/devices/<board>/*.json  — ЛИЧНАЯ база каждой платы (файлы, что она
#                                загружала; память устройства в облаке)
#   kb/search?q=               — tf-idf поиск по общим + личным статьям
#                                (+ опционально Википедия: &wiki=1)
# Мозг neuro_12 читает KB (список -> статья), учится на статьях (mozgnet
# платы) и ПИШЕТ свои (kb/import) — генератор облака учится у мозга.
# ===========================================================================
import math
import os
import re
import threading
import time
from array import array

import store
import textutils
import webfetch

_WORD_RE = re.compile(r"[а-яёa-z0-9]{3,}")


def _id_new(prefix="kb"):
    return "%s_%d_%s" % (prefix, int(time.time()), os.urandom(2).hex())


def _art_path(aid, device=None):
    if device:
        safe_dev = "".join(c for c in str(device) if c.isalnum() or c in "-_")[:40]
        return os.path.join("kb", "devices", safe_dev, aid + ".json")
    return os.path.join("kb", "common", aid + ".json")


def add_article(title, text, source="", device=None, did=None):
    """Добавить/обновить статью. Возврат: {ok, id, chars}."""
    title = str(title or "").strip()[:200] or "Без названия"
    text = str(text or "").strip()[:200000]
    if len(text) < 2:
        return {"ok": False, "error": "пустой текст статьи"}
    aid = did or _id_new()
    images = []
    if "[ИЛЛЮСТРАЦИЯ src=" in text:      # v3.124: иллюстрации страницы
        try:
            import kbimages
            text, images = kbimages.extract(kbimages.localize(text))
        except Exception:
            text = re.sub(r"\[ИЛЛЮСТРАЦИЯ[^\]]*\]([\s\S]*?)\[/ИЛЛЮСТРАЦИЯ\]",
                          r" \1 ", text)
    art = {"id": aid, "title": title, "text": text,
           "source": str(source or "")[:120],
           "device": str(device or "")[:40],
           "ts": time.time(),
           "chars": len(text),
           "kw": textutils.keywords(text, 10)}
    if images:
        art["images"] = images
    store.save_json(_art_path(aid, device), art)
    _touch_index()
    # статья — материал для нейросети материалов облака (облако учится)
    try:
        import brain
        brain.mn_learn(text, title, source or ("KB" if not device
                                               else "личная KB"))
    except Exception:
        pass
    try:                       # v3.62: формулы LaTeX из статьи -> библиотека
        import latexgen
        latexgen.learn_from_text(title, text)
    except Exception:
        pass
    try:                       # v3.57: запись в БД = сигнал обучения/платам
        import syncpoint
        syncpoint.mark("kb", "статья: " + title[:40])
    except Exception:
        pass
    return {"ok": True, "id": aid, "chars": len(text)}


def article(aid, device=None):
    art = store.load_json(_art_path(aid, device), None)
    if art:
        return art
    # поиск по всем личным базам (мозг спрашивает id без device)
    try:
        root = store.p("kb", "devices")
        for dev in os.listdir(root):
            art = store.load_json(
                os.path.join("kb", "devices", dev, aid + ".json"), None)
            if art:
                return art
    except Exception:
        pass
    return None


def delete_article(aid, device=None):
    _touch_index()
    for dev in ([device] if device else [None]) or []:
        try:
            os.remove(store.p(_art_path(aid, dev)))
            return True
        except Exception:
            continue
    try:
        root = store.p("kb", "devices")
        for d in os.listdir(root):
            try:
                os.remove(store.p("kb", "devices", d, aid + ".json"))
                return True
            except Exception:
                continue
    except Exception:
        pass
    return False


def list_articles(limit=100, device=None, include_devices=True):
    """Список статей (метаданные): общие + личные базы устройств.
    v3.27: из индекса (раньше — чтение ВСЕХ статей с диска на каждый
    вызов: /status бота, панель); индекс ещё строится — с диска."""
    if _IDX.get("ready"):
        with _IDX_LOCK:
            out = [_meta(d) for d in _IDX["docs"] if d.get("alive") and
                   (include_devices or not d.get("device"))]
        if device:
            dv = str(device)
            mine = [a for a in out if a.get("device") == dv]
            out = mine + [a for a in out if a.get("device") != dv]
        out.sort(key=lambda x: -float(x.get("ts") or 0))
        return out[:int(limit)]
    out = []
    try:
        for fn in os.listdir(store.p("kb", "common")):
            if not fn.endswith(".json"):
                continue
            a = store.load_json(os.path.join("kb", "common", fn), None)
            if a:
                out.append(_meta(a))
    except Exception:
        pass
    if include_devices:
        try:
            root = store.p("kb", "devices")
            for dev in os.listdir(root):
                for fn in os.listdir(store.p("kb", "devices", dev)):
                    if not fn.endswith(".json"):
                        continue
                    a = store.load_json(
                        os.path.join("kb", "devices", dev, fn), None)
                    if a:
                        out.append(_meta(a))
        except Exception:
            pass
    if device:
        dv = str(device)
        mine = [a for a in out if a.get("device") == dv]
        out = mine + [a for a in out if a.get("device") != dv]
    out.sort(key=lambda x: -float(x.get("ts") or 0))
    return out[:int(limit)]


def source_kind(src):
    """v3.28: откуда статья — 'page' (страница по ссылке, Confluence),
    'doc' (документ: файл из панели/МАКС/платы, файл или вложение со
    ссылки) или 'other' (Википедия, сочинения мозга, заметки…)."""
    s = str(src or "").lower()
    if s.startswith("инструкции файл") or s.startswith("файл ") or \
            s.startswith("документ") or s == "файл":
        return "doc"
    if s.startswith("инструкции"):
        return "page"
    return "other"


def stats():
    """v3.28: сводка для панели «Загрузка в базу»: сколько страниц прочитано
    по ссылкам, сколько документов и сколько в каждом случае знаков.
    Из индекса (быстро); индекс ещё строится — честно с диска."""
    out = {k: {"n": 0, "chars": 0} for k in ("page", "doc", "other")}
    last = {"page": 0.0, "doc": 0.0, "other": 0.0}
    src = "индекс"
    if _IDX.get("ready"):
        with _IDX_LOCK:
            items = [(d.get("source"), d.get("chars"), d.get("ts"))
                     for d in _IDX["docs"] if d.get("alive")]
    else:
        src = "диск"
        items = [(a.get("source"), a.get("chars"), a.get("ts"))
                 for a in list_articles(limit=100000)]
    for s, c, ts in items:
        k = source_kind(s)
        out[k]["n"] += 1
        out[k]["chars"] += int(c or 0)
        last[k] = max(last[k], float(ts or 0))
    for k in out:
        out[k]["last_ts"] = last[k]
    total = {"n": sum(v["n"] for v in out.values()),
             "chars": sum(v["chars"] for v in out.values())}
    return {"pages": out["page"], "docs": out["doc"], "other": out["other"],
            "total": total, "from": src, "index": index_status()}


def _meta(a):
    return {"id": a.get("id"), "title": a.get("title"),
            "source": a.get("source"), "device": a.get("device"),
            "ts": a.get("ts"), "chars": a.get("chars"),
            "kw": (a.get("kw") or [])[:5]}


# v3.27: нормализация как у поисковиков/LLM-токенизаторов: ударения
# Википедии («Полево́й»), мягкие переносы и zero-width не рвут слова,
# «ё» == «е», слова от 2 букв («пи», «ии») тоже считаются.
_ACCENT_RE = re.compile("[\u0300-\u036f\u00ad\u200b-\u200f\u2060\ufeff]")
_TOK_RE = re.compile(r"[а-яa-z0-9]+")
_STEM_CACHE = {}


# v3.27: названия ОС пишут то слитно, то раздельно («редОС» / «РЕД ОС» /
# «Ред ОС») — для поиска это одно слово
_OS_JOIN = re.compile(r"\b(ред|альт|астра|роса|аврора)[\s\-]+(ос|os|linux|"
                      r"линукс)\b")


def _norm_text(text):
    t = _ACCENT_RE.sub("", str(text or "").lower()).replace("ё", "е")
    return _OS_JOIN.sub(r"\1\2", t)


def _stem_c(w):
    s = _STEM_CACHE.get(w)
    if s is None:
        s = textutils.stem(w)
        # «запись» -> «запи» (срезано как возвратное «-сь»), а «записи» ->
        # «запис»: одно слово — разные основы, поиск их не сводил. Для
        # существительных на «-сь/-зь/-ть…» отрезаем только мягкий знак.
        if len(s) < 5 and len(w) >= 5 and w.endswith("сь"):
            s = w[:-1]
        # короткие слова стеммер не трогает: «дыра»/«дыры»/«дыру» были
        # тремя разными словами — сводим к основе «дыр»
        elif s == w and len(w) == 4 and w[-1] in "аяыиуюеоь" and \
                w[:-1].isalpha():
            s = w[:-1]
        if len(_STEM_CACHE) < 500000:
            _STEM_CACHE[w] = s
    return s


def _stems(text):
    return [_stem_c(w) for w in _TOK_RE.findall(_norm_text(text))
            if 2 <= len(w) <= 40]


# v3.24.1: вопросные слова запроса — «бесправные»: в старом score они
# веяли наравне с темой, и частое «что» в длинной статье вытягивало порог
# (Макс отвечал на «что ты знаешь про Бештау?» статьёй про Paramount).
# ВАЖНО: стоп-набор хранится В ФОРМЕ СТЕМОВ (стеммер режет «знаешь» ->
# «знаеш», «знаете» -> «знате»), иначе вопросные слова просачиваются в
# «содержательные» с максимальным idf и ломают шкалу.
_QSTOP_WORDS = frozenset("""знаешь знаете знает знать расскажи расскажу
скажи скажите покажи посмотри подскажи ответь ответьте пожалуйста спасибо
привет вопрос давай давайте хочу можете смотри слушай такое такая такие
всякий всякие объясни говори говорить думаешь считаешь about what who how
why when where you your the and tell know please
такое такой такая такие кто что это является являются каков какова
каковы сколько почему зачем когда где можно нужно надо мне нам
ли бы же по из от до за на не ни да но то во со ко об ну уж мы вы ты
он она их им ей ее его нем них is are of to in on for
подскажите помогите помоги объясните расскажите покажите напишите
уточните уточни здравствуйте коллеги""".split())
_STOP_STEMS = frozenset(_stem_c(_norm_text(w))
                        for w in (textutils._STOP | _QSTOP_WORDS))
# служебные слова названий («Инструкции: <страница>», «Код: …») — не
# снижают «точность» совпадения названия с вопросом
_TITLE_NOISE = frozenset(_stem_c(w) for w in (
    "инструкции", "инструкция", "код", "документ", "статья", "файл"))


# ===========================================================================
# v3.27: ИНДЕКС БАЗЫ ЗНАНИЙ (инвертированный, в памяти, инкрементальный)
# ---------------------------------------------------------------------------
# Раньше каждый поиск перечитывал с диска ВСЕ json-статьи и при этом брал
# только 400 самых новых: в базе с тысячами страниц Confluence и реплик из
# МАКС нужная статья в поиск просто не попадала — бот отвечал «не той».
# Теперь индекс строится один раз, а дальше обновляется по mtime файлов
# (новые/изменённые/удалённые статьи — без полной перестройки).
# Постинги — компактные array: тысячи длинных вложений не съедают память.
# ===========================================================================
_IDX_LOCK = threading.RLock()
_IDX = {}
_CHECK_EVERY = 4.0             # сек между проверками каталога на изменения
# v3.27: один «обновляльщик» индекса за раз; тяжёлая работа (чтение и
# разбор статей) — БЕЗ _IDX_LOCK, под ним только быстрая вставка
_REFRESH = threading.Lock()
_INLINE_MAX = 20               # столько изменённых статей поиск берёт сам
_BATCH = 40                    # статей за одну вставку под блокировкой
_TEXT_INDEX_MAX = 120000       # знаков статьи в индексе (огромные вложения)
_BG = {"started": False}


def _idx_new():
    return {"docs": [], "by_path": {}, "post": {}, "pref": {},
            "alive": 0, "dead": 0, "tot_len": 0, "check_ts": 0.0,
            "backlog": 0, "ready": False}


def _idx_reset():
    _IDX.clear()
    _IDX.update(_idx_new())


_idx_reset()


def _prep(rel):
    """Прочитать и разобрать статью — БЕЗ блокировки (это долго).
    Возврат: dict для вставки или None (пустая/битая статья)."""
    art = store.load_json(rel, None)
    if not isinstance(art, dict) or not str(art.get("text") or "").strip():
        return None
    title = str(art.get("title") or "")
    text = str(art.get("text") or "")
    sts = _stems(title + "\n" + text[:_TEXT_INDEX_MAX])
    if not sts:
        return None
    tf = {}
    for s in sts:
        tf[s] = tf.get(s, 0) + 1
    lines = [ln for ln in text[:_TEXT_INDEX_MAX].split("\n") if ln.strip()]
    has_cmd = any(_is_cmd(ln) for ln in lines[:2000]) or \
        bool(_HAS_CMD_RE.search(text[:_TEXT_INDEX_MAX]))
    return {"meta": {
        "id": art.get("id") or os.path.basename(rel)[:-5],
        "title": title, "source": art.get("source") or "KB",
        "device": art.get("device") or "", "ts": art.get("ts") or 0,
        "chars": art.get("chars") or len(text),
        "kw": (art.get("kw") or [])[:5],
        "path": rel, "len": len(sts), "alive": True, "cmd": has_cmd,
        # статья ЦЕЛИКОМ из команд (команда, выученная отдельным сообщением)
        "cmd_only": bool(lines) and len(lines) <= 5 and
        all(_is_cmd(ln) for ln in lines),
        "tt": frozenset(_stems(title))}, "tf": tf}


def _commit(idx, rel, mtime, pr):
    """Вставить разобранную статью в индекс (быстро; под _IDX_LOCK, если
    idx — живой индекс)."""
    _drop(idx, rel)
    if pr is None:
        idx["by_path"][rel] = (mtime, None)
        return
    di = len(idx["docs"])
    idx["docs"].append(pr["meta"])
    post, pref = idx["post"], idx["pref"]
    for s, c in pr["tf"].items():
        p = post.get(s)
        if p is None:
            p = post[s] = (array("I"), array("H"))
            if len(s) >= 5:
                pref.setdefault(s[:5], []).append(s)
        p[0].append(di)
        p[1].append(min(c, 65535))
    idx["alive"] += 1
    idx["tot_len"] += pr["meta"]["len"]
    idx["by_path"][rel] = (mtime, di)


def _drop(idx, rel):
    rec = idx["by_path"].pop(rel, None)
    if rec and rec[1] is not None:
        d = idx["docs"][rec[1]]
        if d["alive"]:
            d["alive"] = False
            idx["alive"] -= 1
            idx["dead"] += 1
            idx["tot_len"] -= d["len"]


def _idx_add(rel, mtime):
    """Совместимость: прочитать и вставить одну статью (под _IDX_LOCK)."""
    _commit(_IDX, rel, mtime, _prep(rel))


def _idx_drop(rel):
    _drop(_IDX, rel)


def _scan_files():
    """{rel_path: mtime} всех json-статей (общие + личные базы)."""
    out = {}
    dirs = [os.path.join("kb", "common")]
    try:
        with os.scandir(store.p("kb", "devices")) as it:
            for e in it:
                if e.is_dir():
                    dirs.append(os.path.join("kb", "devices", e.name))
    except Exception:
        pass
    for rel_dir in dirs:
        try:
            with os.scandir(store.p(rel_dir)) as it:
                for e in it:
                    if e.name.endswith(".json") and e.is_file():
                        try:
                            out[os.path.join(rel_dir, e.name)] = \
                                e.stat().st_mtime
                        except Exception:
                            pass
        except Exception:
            pass
    return out


def _rebuild(files):
    """Пересборка индекса В КОПИИ (без блокировки) и подмена за миг —
    когда «мёртвых» слотов (обновлённых статей) накопилось много."""
    fresh = _idx_new()
    for rel, mt in files.items():
        _commit(fresh, rel, mt, _prep(rel))
    fresh["check_ts"] = time.time()
    fresh["ready"] = True
    with _IDX_LOCK:
        # что поменялось, пока собирали, — доберёт следующий проход
        _IDX.clear()
        _IDX.update(fresh)
        _CACHE_STATE["dirty"] = True
    _READY.set()


def _idx_refresh(force=False, inline_max=None):
    """Синхронизировать индекс с диском. v3.27: НЕ держит _IDX_LOCK во
    время чтения статей (раньше первая сборка большой базы — десятки
    секунд/минуты — блокировала все вопросы: «облако зависло»).
    inline_max — сколько изменённых статей обработать прямо сейчас (поиск
    берёт немного: свежая статья видна сразу), остальное — фоном."""
    if not force and time.time() - _IDX["check_ts"] < _CHECK_EVERY:
        return
    if not _REFRESH.acquire(blocking=False):
        return                     # уже обновляет другой поток — ищем как есть
    try:
        files = _scan_files()
        if inline_max is None and _IDX["dead"] > 200 and \
                _IDX["dead"] > _IDX["alive"] * 0.3:
            _rebuild(files)
            return
        with _IDX_LOCK:
            known = _IDX["by_path"]
            gone = [r for r in known if r not in files]
            for rel in gone:
                _drop(_IDX, rel)
            todo = [(rel, mt) for rel, mt in files.items()
                    if known.get(rel) is None or known[rel][0] != mt]
            if gone or todo:
                _CACHE_STATE["dirty"] = True
        todo.sort(key=lambda x: -x[1])        # свежие — первыми
        rest = []
        if inline_max is not None and len(todo) > inline_max:
            todo, rest = todo[:inline_max], todo[inline_max:]
        for i in range(0, len(todo), _BATCH):
            preps = [(rel, mt, _prep(rel)) for rel, mt in todo[i:i + _BATCH]]
            with _IDX_LOCK:
                for rel, mt, pr in preps:
                    _commit(_IDX, rel, mt, pr)
        with _IDX_LOCK:
            _IDX["backlog"] = len(rest)
            if not rest:
                _IDX["check_ts"] = time.time()
                _IDX["ready"] = True
                _READY.set()
    finally:
        _REFRESH.release()
    if rest:
        _bg_kick()


def _touch_index():
    """Статья добавлена/удалена — следующий поиск сразу увидит изменения."""
    _IDX["check_ts"] = 0.0


_BG_WAKE = threading.Event()


def _bg_loop():
    while True:
        try:
            _idx_refresh(force=True)
        except Exception:
            pass
        try:                    # кэш индекса на диск: после сборки и не
            if _READY.is_set() and _CACHE_STATE["dirty"] and \
                    (not _CACHE_STATE["saved"] or
                     time.time() - _CACHE_STATE["saved"] > 300):
                _cache_save()   # чаще раза в 5 минут
        except Exception:
            pass
        _BG_WAKE.wait(_CHECK_EVERY * 2)
        _BG_WAKE.clear()


def _bg_kick():
    if not _BG["started"]:
        _BG["started"] = True
        threading.Thread(target=_bg_loop, daemon=True,
                         name="kb-index").start()
    _BG_WAKE.set()


_READY = threading.Event()      # индекс полностью собран (можно доверять)
_CACHE_FILE = "kb_index.cache"
_CACHE_VER = 3                 # v3: метки cmd/cmd_only, «РЕД ОС» = «редос»
_CACHE_STATE = {"saved": 0.0, "dirty": False}


def _cache_save():
    """v3.27: сохранить индекс на диск (снимок под блокировкой — копии
    массивов, это быстро; запись — без блокировки). После перезапуска
    индекс грузится за секунды, разбираются только изменённые статьи."""
    import pickle
    with _IDX_LOCK:
        snap = {"ver": _CACHE_VER,
                "docs": [dict(d) for d in _IDX["docs"]],
                "by_path": dict(_IDX["by_path"]),
                "post": {s: (array("I", p[0]), array("H", p[1]))
                         for s, p in _IDX["post"].items()},
                "pref": {k: list(v) for k, v in _IDX["pref"].items()},
                "alive": _IDX["alive"], "dead": _IDX["dead"],
                "tot_len": _IDX["tot_len"]}
        _CACHE_STATE["dirty"] = False
    try:
        path = store.p(_CACHE_FILE)
        tmp = path + ".tmp"
        with open(tmp, "wb") as f:
            pickle.dump(snap, f, protocol=pickle.HIGHEST_PROTOCOL)
        os.replace(tmp, path)
        _CACHE_STATE["saved"] = time.time()
        return True
    except Exception:
        return False


def _cache_load():
    """Загрузить сохранённый индекс (если есть и той же версии)."""
    import pickle
    try:
        with open(store.p(_CACHE_FILE), "rb") as f:
            snap = pickle.load(f)
        if not isinstance(snap, dict) or snap.get("ver") != _CACHE_VER:
            return False
        fresh = _idx_new()
        for k in ("docs", "by_path", "post", "pref", "alive", "dead",
                  "tot_len"):
            fresh[k] = snap[k]
        with _IDX_LOCK:
            _IDX.clear()
            _IDX.update(fresh)
        return True
    except Exception:
        return False


def warm_index():
    """Построить индекс заранее (фоном при старте сервера) и дальше
    держать его свежим фоновым потоком. v3.27: сначала — кэш с диска,
    потом досинхронизация только изменённых статей."""
    try:
        _cache_load()
        _idx_refresh(force=True)
    except Exception:
        pass
    _bg_kick()


def index_status():
    """Сколько статей в индексе и сколько ждут разбора (для панели)."""
    return {"alive": _IDX.get("alive", 0), "backlog": _IDX.get("backlog", 0),
            "ready": bool(_IDX.get("ready"))}


def _query_groups(q):
    """Содержательные стемы запроса -> группы {стем: вес} с формами слова
    из словаря индекса («записать»/«запись»/«записи» — одна группа).
    Вызывать под _IDX_LOCK."""
    out, seen = [], set()
    for t in _stems(q):
        if t in _STOP_STEMS or t in seen:
            continue
        seen.add(t)
        g = {t: 1.0}
        if len(t) >= 5:
            for s in _IDX["pref"].get(t[:5], ()):
                if s == t or abs(len(s) - len(t)) > 3:
                    continue
                # общий корень: «перенест»~«перенос», «запис»~«записа»,
                # но не «транзистор»~«транзит» (корень короче слова на 4+)
                cp = 0
                for a, b in zip(s, t):
                    if a != b:
                        break
                    cp += 1
                if cp >= max(5, max(len(s), len(t)) - 3):
                    g[s] = 0.7
        out.append(g)
        if len(out) >= 12:
            break
    return out


_CMD_STEMS = frozenset(_stem_c(_norm_text(w)) for w in """команда команду
команды командой команд командах терминал терминале терминала консоль
консоли cmd shell""".split())


def _cmd_neighbors(docs, window=300.0):
    """Индексы статей-описаний, СРАЗУ за которыми (тот же источник, до
    window секунд) выучена статья из одних команд. Под _IDX_LOCK."""
    cmd_docs = [d for d in docs if d.get("alive") and d.get("cmd_only")]
    if not cmd_docs:
        return set()
    out = set()
    for di, d in enumerate(docs):
        if not d.get("alive") or d.get("cmd_only"):
            continue
        t0, src = float(d.get("ts") or 0), d.get("source")
        if any(c.get("source") == src and
               0 < float(c.get("ts") or 0) - t0 < window for c in cmd_docs):
            out.add(di)
    return out


def _group_stats(groups):
    """Для каждой группы: ({doc: tf}, idf). Под _IDX_LOCK."""
    docs = _IDX["docs"]
    n = max(1, _IDX["alive"])
    stats = []
    for g in groups:
        hits = {}
        for s, w in g.items():
            p = _IDX["post"].get(s)
            if not p:
                continue
            for di, c in zip(p[0], p[1]):
                if di < len(docs) and docs[di]["alive"]:
                    hits[di] = hits.get(di, 0.0) + c * w
        if _CMD_STEMS.intersection(g):
            # v3.27: «какая КОМАНДА…» — слово «команда» в статье не пишут,
            # там сама команда («sudo …»): такая статья этому слову вопроса
            # соответствует (иначе верная статья не проходила порог).
            # И описание, за которым ОТДЕЛЬНОЙ статьёй выучена команда
            # (того же источника, в пределах 5 минут после) — тоже.
            near = _cmd_neighbors(docs)
            for di, d in enumerate(docs):
                if d["alive"] and (d.get("cmd") or di in near):
                    hits[di] = max(hits.get(di, 0.0), 1.0)
        df = len(hits)
        idf = max(0.05, math.log(1.0 + (n - df + 0.5) / (df + 0.5)))
        if df == 0:
            # слова нет НИ В ОДНОЙ статье («ЕМИАС», «подскажите»): оно не
            # отличает статьи друг от друга, а с максимальным весом топило
            # долю совпадения у верной статьи. Вес снижен, но не ноль —
            # вопрос о неизвестной теме не «совпадёт» по второстепенному слову
            idf *= 0.35
        stats.append((hits, idf))
    return stats


_K1, _B = 1.2, 0.75


def _rank(groups, device=None):
    """BM25 + покрытие запроса + совпадение с НАЗВАНИЕМ статьи.
    Шкала согласована с порогом 2.0 в brain.py:
      2.6·cov²  — доля (по idf) слов вопроса, найденных в статье;
      1.2·bm    — BM25 с поправкой на длину (длинная статья не «выигрывает
                  количеством», как «Сверхмассивная чёрная дыра» у «Чёрной
                  дыры»);
      1.6·tcov·(0.4+0.6·tprec) — слова вопроса в названии и точность
                  названия; +0.6 — название ровно о том, что спросили.
    Полное совпадение по тексту даёт ≥ 2.6, половина слов — ~1, т.е. ниже
    порога: «не та» статья честно не проходит."""
    stats = _group_stats(groups)
    sum_idf = sum(idf for _h, idf in stats) or 1.0
    docs = _IDX["docs"]
    avgl = _IDX["tot_len"] / max(1, _IDX["alive"])
    cand = set()
    for hits, _idf in stats:
        cand.update(hits)
    all_q = set()
    for g in groups:
        all_q.update(g)
    # v3.27: ГЛАВНОЕ слово вопроса — самое редкое из тех, что в базе вообще
    # есть («Бештау», «гравитон»). Статья без него совпала лишь общими
    # словами («что», «система», «работа») — это «не тот документ».
    key_gi = -1
    if len(groups) >= 2:
        present = [(idf, gi) for gi, (h, idf) in enumerate(stats) if h]
        if present:
            key_gi = max(present)[1]
    out = []
    for di in cand:
        d = docs[di]
        L = d["len"]
        cov = bm = tcov = 0.0
        norm = _K1 * (1 - _B + _B * L / max(1.0, avgl))
        for gi, (hits, idf) in enumerate(stats):
            tf = hits.get(di, 0.0)
            if tf > 0:
                cov += idf
                bm += idf * tf * (_K1 + 1) / (tf + norm)
            if any(s in d["tt"] for s in groups[gi]):
                tcov += idf
        cov /= sum_idf
        bm /= sum_idf * (_K1 + 1)
        tcov /= sum_idf
        tcontent = [s for s in d["tt"]
                    if s not in _STOP_STEMS and s not in _TITLE_NOISE]
        tprec = (sum(1 for s in tcontent if s in all_q) / len(tcontent)
                 if tcontent else 0.0)
        score = 2.6 * cov * cov + 1.2 * bm + \
            1.6 * tcov * (0.4 + 0.6 * tprec)
        if tcov >= 0.999 and tprec >= 0.999:
            score += 0.6
        if L < 15:
            score *= 0.85                 # реплика-обрывок, а не статья
        if key_gi >= 0 and cov < 0.5 and \
                stats[key_gi][0].get(di, 0.0) <= 0 and \
                not any(s in d["tt"] for s in groups[key_gi]):
            score *= 0.6                  # нет главного слова вопроса
        src_ = str(d.get("source") or "")
        if src_.startswith("интернет: выучено"):
            # v3.74: кэш прежних ответов из интернета — не знание владельца.
            # Его название = сам вопрос (полное совпадение), и неверный ответ
            # раньше сам себя подтверждал. Теперь он ниже материалов базы.
            score = score * 0.55 - 1.6 * tcov * (0.4 + 0.6 * tprec)
        elif src_.startswith(("группа МАКС", "история чата", "МАКС")):
            score *= 1.15                 # то, что люди написали в чате
        if device and d["device"] == str(device):
            score *= 1.05                 # своя база устройства — чуть ближе
        out.append((score, cov, tcov, di))
    out.sort(key=lambda x: (-x[0], -float(docs[x[3]]["ts"] or 0)))
    return out, stats


def search(q, limit=3, wiki=0, device=None):
    """Поиск по KB (v3.27): индекс всей базы + BM25 + покрытие вопроса +
    совпадение с названием (см. _rank). Правила честности прежние:
    вопросные слова не считаются темой; запрос из одних вопросных слов —
    результатов нет; статья без единого содержательного слова вопроса
    не проходит. snippet — связный фрагмент статьи по вопросу (а не одно
    случайное предложение). + Википедия если wiki=1 и включён интернет.
    Возврат: {ok, results:[{id?, title, snippet, source, wiki?, score}]}"""
    q = str(q or "").strip()[:200]
    if not q:
        return {"ok": True, "results": []}
    results = []
    _idx_refresh(inline_max=_INLINE_MAX)   # v3.27: вне блокировки и с лимитом
    partial = False
    if not _READY.is_set():
        # индекс ещё собирается (первый запуск на большой базе): ждём
        # немного, а дальше честно помечаем выдачу как НЕПОЛНУЮ — по ней
        # мозг не даёт уверенный ответ (иначе «Антарктида» -> «Тихий
        # океан»: нужная статья просто ещё не разобрана)
        _bg_kick()
        partial = not _READY.wait(5.0)
    with _IDX_LOCK:
        groups = _query_groups(q)
        if not groups:
            # запрос целиком из вопросных/стоп-слов — тем нет, KB молчит
            return {"ok": True, "results": []}
        ranked, stats = _rank(groups, device=device)
        # v3.144: берём с запасом — статьи-вопросы ниже отсеиваются
        top = [(sc, cov, tcov, dict(_IDX["docs"][di]))
               for sc, cov, tcov, di in ranked[:int(limit) * 3]]
    idfs = [idf for _h, idf in stats]
    for sc, cov, tcov, d in top:
        if partial and tcov < 0.999:
            # индекс неполный: верим только статье, чьё НАЗВАНИЕ целиком о
            # вопросе; остальным — оценка ниже порогов мозга (1.3 / 2.0)
            sc = min(sc, 1.2)
        art = store.load_json(d["path"], None) or {}
        snippet = _passage(str(art.get("text") or ""), groups, idfs,
                           max_chars=400, want_cmd=bool(_CMD_Q.search(q)))
        # v3.144: «вопрос на вопрос» — статья, где только вопросы (сообщение
        # из чата МАКС «как сделать …?»), ответом не бывает
        try:
            import textutils as _tu
            if _tu.question_only(snippet) and \
                    _tu.question_only(str(art.get("text") or "")[:1500]):
                continue
            # v3.145: статья — это сам вопрос пользователя (сообщение чата,
            # записанное в базу): «ответ» повторяет вопрос
            if _tu.echo_of(q, snippet) or _tu.echo_of(q, str(art.get("title") or "")) \
                    and _tu.echo_of(q, str(art.get("text") or "")[:400]):
                continue
        except Exception:
            pass
        if len(results) >= int(limit):
            break
        results.append({"id": d["id"], "title": d["title"],
                        "snippet": snippet[:400],
                        "source": d["source"], "device": d["device"],
                        "score": round(sc, 3), "cov": round(cov, 3),
                        "tcov": round(tcov, 3), "partial": partial})
    if wiki and store.settings().get("web_on"):
        have = {r.get("title", "").lower() for r in results}
        try:                          # v3.12.1: сеть не роняет поиск KB
            wiki_hits = webfetch.wiki_search(q, limit=1)
        except Exception:
            wiki_hits = []
        for w in wiki_hits:
            if w["title"].lower() not in have:
                results.append({"title": w["title"], "snippet": w["snippet"],
                                "url": w["url"], "source": "Википедия",
                                "wiki": True})
    try:                          # v3.73: опора на частоту — только порядок
        import freqprior          # (порог score не трогаем)
        zl = freqprior.level()
        if zl is not None:
            for r_ in results:
                r_["_fp"] = 0.1 * freqprior.bonus(
                    (r_.get("title") or "") + " " + (r_.get("snippet") or ""), z=zl)
    except Exception:
        pass
    results.sort(key=lambda x: -(float(x.get("score") or 0) + x.get("_fp", 0.0)))
    for r_ in results:
        r_.pop("_fp", None)
    return {"ok": True, "results": results[:int(limit) + 1],
            "partial": partial}


# ===========================================================================
# v3.27: СБОРКА ОТВЕТА ИЗ СТАТЕЙ «КАК У LLM» (извлечение + связный пересказ)
# ---------------------------------------------------------------------------
# Раньше ответом было ОДНО предложение статьи с наибольшим числом совпадений
# — часто обрывок из середины («(2013) включения семейств…»). Теперь как
# RAG у LLM: вопрос «что такое X» -> начало статьи (определение) по
# порядку; конкретный вопрос -> фрагмент, где ответ, с соседними
# предложениями (контекст, «Он/Это…» не висят без подлежащего, списки
# шагов после «:» не обрываются) + недостающие детали из других мест
# статьи; вторая статья — если она почти так же релевантна. Только текст
# самих статей, ничего не выдумываем; источники подписаны.
# ===========================================================================
_DEF_Q = re.compile(
    r"^\s*(?:а\s+)?(?:что\s+так(?:ое|ой|ая)|что\s+это|кто\s+так(?:ой|ая|ие)|"
    r"что\s+за\b|что\s+(?:ты\s+|вы\s+)?знаешь|что\s+(?:тебе\s+)?известно|"
    r"расскажи|опиши|объясни|кто\s+(?:был|была|это)|что\s+значит|"
    r"what\s+is|who\s+is|tell\s+me)", re.I)
_SPECIFIC_Q = re.compile(
    r"\b(сколько|когда|где|почему|зачем|как\s|каким\s+образом|какой\s+год|"
    r"в\s+каком|чем\s+отлича|можно\s+ли|нужно\s+ли|что\s+делать)", re.I)
_CONNECT_RE = re.compile(
    r"^(он|она|оно|они|это|этот|эта|эти|его|её|ее|их|ему|ей|там|здесь|"
    r"тогда|также|тоже|кроме\s+того|при\s+этом|поэтому|однако|но|а\s|"
    r"в\s+нём|в\s+нем|в\s+ней|в\s+них|данн\w+|такж\w+)\b", re.I)
_FOREIGN_PAREN = re.compile(
    r"\s*\((?=[^()]*\b(?:англ|лат|греч|нем|франц|фр|итал|исп|араб|япон|"
    r"кит|др\.-греч|МФА|ивр|санскр|укр|белор)\.)[^()]{0,200}\)")
_ABBR = frozenset("""англ лат греч нем франц фр итал исп араб япон кит др
т е к г гг в вв см им ул д р с стр н э рис табл млн млрд тыс руб коп кв км
мм ок прим ред изд вып св ст тт пр проф акад доц напр ср гл илл юж сев зап
вост обл ген оз пос устар сокр праслав букв разг совр род ед мн ч тж укр
белор яп фин швед перс тур санскр ивр польск чеш венг рум порт нидерл
рис прил гос тел""".split())
_SENT_END = re.compile(r"[.!?…]+[»\"')\]]*\s+")


def _split_sents(par):
    """Предложения абзаца без ложных разрезов на «англ.», «т. е.», «А. С.»."""
    out, start = [], 0
    for m in _SENT_END.finditer(par):
        nxt = par[m.end():m.end() + 1]
        if nxt and nxt.islower():
            continue                       # «… и т. д. продолжение» — не конец
        if re.fullmatch(r"\s*\d{1,3}", par[start:m.start()]):
            continue                       # «1. Откройте…» — номер шага
        if m.group(0)[0] == ".":
            w = re.search(r"([A-Za-zА-Яа-яЁё]+)$", par[start:m.start()])
            if w and (w.group(1).lower() in _ABBR or
                      (len(w.group(1)) == 1 and w.group(1).isupper())):
                continue
        s = par[start:m.end()].strip()
        if s:
            out.append(s)
        start = m.end()
    tail = par[start:].strip()
    if tail:
        out.append(tail)
    return out


def _clean_unit(s):
    s = textutils.strip_fields(s)          # «PAGEREF _Toc… \h 10» — мимо
    s = re.sub(r"\[\d{1,3}\]|\[источник[^\]]*\]|\[нет в источнике\]", "", s)
    s = _FOREIGN_PAREN.sub("", s)
    s = re.sub(r"\(\s*[;,]?\s*\)", "", s)
    s = re.sub(r"[ \t]+", " ", s).strip(" ;|")
    s = re.sub(r"\s+([,.;:!?])", r"\1", s)
    return s.lstrip(",.;:)] ")


def _strip_tex(t):
    """Формулы-разметку Википедии «{\\displaystyle …}» (с вложенными
    скобками) — в «$…$» (v3.107: раньше выбрасывались целиком)."""
    out, i = [], 0
    while True:
        j = t.find("{\\displaystyle", i)
        if j < 0:
            out.append(t[i:])
            break
        out.append(t[i:j])
        depth, k = 0, j
        while k < len(t):
            if t[k] == "{":
                depth += 1
            elif t[k] == "}":
                depth -= 1
                if depth == 0:
                    break
            k += 1
        # v3.107: формула не выбрасывается, а становится «$…$»: веб-чат
        # рисует её в окошке, МАКС/Telegram/API — читаемым текстом
        # (latexlite.render_dollars)
        tex = t[j + len("{\\displaystyle"):k].strip()
        out.append(" $%s$ " % tex.replace("$", "") if tex else " ")
        i = k + 1
    out = "".join(out)
    # остатки разметки формул Википедии («</math> ","ref":"wt…»)
    out = re.sub(r"</?math\b[^>\n]{0,200}>", " ", out, flags=re.I)
    out = re.sub(r"\s*\"?\s*,\s*\"(?:ref|name|attrs|body|extsrc)\"\s*:"
                 r"\s*\"[^\"\n]{0,80}\"?\}*", " ", out)
    try:
        import latexlite
        out = latexlite.drop_broken(out)
    except Exception:
        pass
    return out


_MATH_GAP = re.compile(r"[ \t]*\n(?:[ \t]*\n)*?[ \t]{4,}\n(?:[ \t]*\n)*[ \t]*")
_ITEM_RE = re.compile(r"^\s*(?:\d{1,3}[.)]|[-•*–—▪●◦]|[a-zа-я]\))\s+")


# v3.27: КОМАНДЫ (терминал Linux/РЕД ОС, сочетания клавиш) — латиница и
# символы, русских слов вопроса в них нет. Раньше короткая команда
# («sudo reboot», «sync») считалась мусором, а длинная не совпадала с
# вопросом — бот отвечал описанием «При зависании редос…» БЕЗ самой команды.
_CMD_RE = re.compile(
    r"^\s*(?:[$#>]\s*)?(?:sudo|su|dnf|yum|rpm|apt|apt-get|snap|flatpak|"
    r"systemctl|service|journalctl|echo|kill|killall|pkill|xkill|reboot|"
    r"shutdown|poweroff|halt|init|telinit|top|htop|free|sync|ps|pgrep|cat|"
    r"dmesg|mount|umount|chmod|chown|rm|cp|mv|ls|cd|grep|tail|head|less|"
    r"nano|vi|vim|mcedit|sysctl|swapoff|swapon|modprobe|lsmod|ip|ifconfig|"
    r"nmcli|ping|df|du|fsck|startx|export|crontab|passwd|useradd|usermod|"
    r"tar|unzip|wget|curl|ssh|scp|nice|renice|ulimit|lsof|fuser|loginctl|"
    r"xrandr|gsettings|update-grub|grub2-mkconfig|ctrl|alt|win|super)\b",
    re.I)
_CMD_Q = re.compile(r"команд|терминал|консол|cmd|\bshell\b|скрипт|"
                    r"что\s+(?:ввести|набрать|написать|прописать|выполнить)|"
                    r"как(?:ой|ую)?\s+(?:ввести|набрать|прописать)|"
                    r"сочетани\w*\s+клавиш|горяч\w*\s+клавиш", re.I)


# команда внутри обычной строки («…гравитон sudo sync; echo 3 …» — так
# бот склеивает сообщения из истории чата в одну строку)
_HAS_CMD_RE = re.compile(
    r"(?:^|[\s:;«\"(])(?:sudo|dnf|yum|apt-get|apt|systemctl|journalctl|"
    r"killall|pkill|xkill|reboot|shutdown|poweroff|sysctl|swapoff|"
    r"echo\s+\d|kill\s+-\d|htop)(?=\s|$)", re.I)


def _is_cmd(s):
    """Строка — команда терминала/сочетание клавиш, а не обычный текст."""
    s = str(s or "").strip()
    if not s or len(s) > 300:
        return False
    if _CMD_RE.match(s):
        return True
    lat = sum(1 for ch in s if "a" <= ch.lower() <= "z")
    cyr = sum(1 for ch in s if "а" <= ch.lower() <= "я" or ch in "ёЁ")
    return lat >= 3 and lat > 3 * cyr and bool(re.search(r"[/=|>&;+_-]", s))


def _is_heading(line, li, lines):
    """Подзаголовок Confluence/docx («Перенос записи»): короткая строка без
    знака конца, за которой в том же блоке идёт текст. Строки таблиц
    («поле | значение») и команды заголовками не считаются."""
    if li >= len(lines) - 1 or "|" in line or "\t" in line:
        return False
    if _is_cmd(line):
        return False
    if len(line) > 80 or len(line.split()) > 8:
        return False
    if line[-1] in ".!?:;…,»\")":
        return False
    return line[0].isupper() or line[0].isdigit()


def _units(text):
    """Текст статьи -> [(раздел, предложение, вид)] в порядке документа.
    вид: "i" — пункт списка (шаг инструкции), "t" — текст. Заголовки
    «== Раздел ==» и подзаголовки строк — границы разделов (в ответ не
    идут); строки таблиц Confluence/docx — отдельные единицы."""
    t = _ACCENT_RE.sub("", str(text or "")).replace("\r", "")
    t = _strip_tex(t)
    # формулы Википедии окружены «пустыми» строками с отступом: это НЕ
    # граница абзаца, а разрыв внутри предложения («π\n    \n, π (…)»)
    t = _MATH_GAP.sub(" ", t)
    # v3.110: формула ОТДЕЛЬНОЙ строкой («Уравнение адиабаты:\n $PV^γ=const$,\n
    # где γ — …») — часть предложения: приклеиваем её к соседним строкам.
    # Раньше такая строка (мало букв) отбрасывалась как мусор, и в ответе
    # оставалось «Параметр γ в формуле…», а самой формулы не было.
    t = re.sub(r"[ \t]*(?:\n[ \t]*)+(\$[^$\n]{1,400}\$[ \t,.;:]*)"
               r"[ \t]*(?:\n[ \t]*)+(?=[а-яёa-z,;(—–-])", r" \1 ", t)
    t = re.sub(r"[ \t]*(?:\n[ \t]*)+(\$[^$\n]{1,400}\$[ \t,.;:]*)"
               r"[ \t]*(?=\n|$)", r" \1", t)
    t = re.sub(r"={2,}\s*([^=\n]{1,120}?)\s*={2,}", "\n\n", t)
    out = []
    pi = 0
    carry = False                  # подзаголовок-абзац: раздел — следующий
    pars = re.split(r"\n\s*\n", t)
    for pn, par in enumerate(pars):
        if not carry:
            pi += 1
        carry = False
        lines = [ln.strip() for ln in par.split("\n") if ln.strip()]
        # абзац из одной короткой строки без точки, за которым идёт текст,
        # — подзаголовок («Перенос записи пациента» над шагами). v3.27: он
        # не выбрасывается, а остаётся единицей вида "h": сам ответом не
        # бывает, но если вопрос совпал с ним — ответ то, что ПОД ним
        if len(lines) == 1 and pn < len(pars) - 1 and \
                not _ITEM_RE.match(lines[0]) and \
                _is_heading(lines[0], 0, lines + [""]):
            pi += 1
            out.append((pi, _clean_unit(lines[0]), "h"))
            carry = True
            continue
        for li, line in enumerate(lines):
            item = bool(_ITEM_RE.match(line))
            if not item and _is_heading(line, li, lines):
                pi += 1
                out.append((pi, _clean_unit(line), "h"))
                continue
            for si, s in enumerate(_split_sents(line)):
                s = _clean_unit(s)
                if s:
                    out.append((pi, s, "i" if item and si == 0 else "t"))
    return out


_BOILER = re.compile(
    r"текст\s+научной\s+статьи|по\s+специальности\s*[«\"]|читать\s+далее|"
    r"скачать\s+(?:статью|pdf)|подписывайтесь|все\s+права\s+защищены|"
    r"^\s*(?:фото|рис(?:унок)?|источник)\s*[.:]|нажмите,?\s+чтобы\s+увеличить",
    re.I)
# признаки глагола/связки: у подписи к картинке («Площадь Минина и Пожарского
# в Нижнем Новгороде.») их нет, у настоящего предложения — есть
_VERBISH = re.compile(
    r"\b\w{2,}(?:[аеёиоуыэюя]ть|ет|ёт|ит|ют|ут|ят|ешь|ишь|ете|ите|йте|"
    r"ьте|ал[аио]?|ил[аио]?|ел[аио]?|ыл[аио]?|ул[аио]?|ол[аио]?|али|или|ели|"
    r"ыли|ули|ся|сь|ен[аоы]?|ан[аоы]?)\b|\b(?:был|была|было|были|есть|это|"
    r"является|являются|стал|стала|стали|будет|может|можно|нужно|надо)\b", re.I)


def junk_sentence(s):
    """v3.69: подпись к картинке / служебная строка сайта — не ответ."""
    s = str(s or "").strip()
    if _BOILER.search(s):
        return True
    w = s.split()
    if 2 <= len(w) <= 12 and not re.search(r"[—–:;\"«]|\s-\s|\d", s) \
            and not _VERBISH.search(s) and not _is_cmd(s):
        return True
    return False


def _junk(s, item=False):
    if "$" in s and re.search(r"\$[^$]{2,}\$", s):    # v3.110: с формулой
        s_ = re.sub(r"\$[^$]*\$", " формула ", s)     # формулу за мусор не считаем
        letters = sum(ch.isalpha() for ch in s_)
        return letters < 12 or (not item and junk_sentence(s_))
    letters = sum(ch.isalpha() for ch in s)
    if _is_cmd(s):
        return letters < 2         # «sudo reboot», «sync» — не мусор
    if textutils.is_nav_line(s):
        return True                # «Каталог - Выживание» — дерево сайта
    if item:
        return letters < 4
    if letters < 12 or letters < 0.45 * len(s):
        return True
    return junk_sentence(s)


def _join_units(units, ix):
    """Склеить единицы: текст — через пробел, пункты списка — с новой
    строки (шаги инструкции остаются шагами)."""
    buf, prev = "", None
    for k in ix:
        s, kind = units[k][1], units[k][2]
        if not buf:
            buf = s
        elif kind == "i" or _is_cmd(s) or (prev is not None and (
                units[prev][2] in ("i", "h") or _is_cmd(units[prev][1]))):
            buf += "\n" + s                # шаги, команды, текст под заголовком
        else:
            buf += " " + s
        prev = k
    return buf


def _unit_cov(s, groups, idfs, sum_idf):
    st = set(_stems(s))
    is_cmd = _is_cmd(s) or bool(_HAS_CMD_RE.search(s))
    got, miss = 0.0, []
    for gi, g in enumerate(groups):
        if any(x in st for x in g) or (is_cmd and _CMD_STEMS.intersection(g)):
            got += idfs[gi]
        else:
            miss.append(gi)
    return got / sum_idf, set(range(len(groups))) - set(miss)


def _passage(text, groups, idfs, max_chars=700, lead=False, avoid="",
             want_cmd=False):
    """Связный фрагмент статьи по вопросу (см. блок выше).
    avoid — уже сказанный текст (прошлый ответ): «расскажи ещё» продолжает
    статью, а не повторяет её начало. want_cmd — спросили КОМАНДУ («какая
    команда в редОС…»): в ответе обязана быть сама команда из статьи."""
    units = _units(text)
    if avoid:
        said = " ".join(str(avoid).split()).lower()
        units = [u for u in units
                 if " ".join(u[1].split()).lower()[:70] not in said]
    if not units:
        return ""
    sum_idf = sum(idfs) or 1.0
    n = len(units)
    scored = []
    for pi, s, kind in units:
        if _junk(s, kind == "i"):
            scored.append((0.0, set()))
            continue
        scored.append(_unit_cov(s, groups, idfs, sum_idf))
    pick = []

    def _len(ix):
        return sum(len(units[k][1]) + 1 for k in ix)

    head = -1
    if lead:
        for i, (pi, s, kind) in enumerate(units):
            if kind == "h" or _junk(s, kind == "i"):
                continue
            if pick and _len(pick) + len(s) > max_chars * 0.8:
                break
            pick.append(i)
            if len(pick) >= 5:
                break
    else:
        best_i, best_v = -1, 0.0
        for i, (cv, _g) in enumerate(scored):
            v = cv + 0.04 / (1.0 + i / 25.0)      # при равенстве — раньше
            if cv > 0 and v > best_v:
                best_i, best_v = i, v
        if best_i < 0:
            return ""
        if units[best_i][2] == "h":
            # вопрос совпал с ПОДЗАГОЛОВКОМ («При зависании редос гравитон»)
            # — ответ то, что под ним (часто это команда без русских слов)
            sec_h = units[best_i][0]
            body_i = next((k for k in range(best_i + 1, n)
                           if units[k][0] == sec_h and units[k][2] != "h"
                           and not _junk(units[k][1], units[k][2] == "i")),
                          -1)
            if body_i >= 0:
                if scored[body_i][0] < scored[best_i][0]:
                    head = best_i          # смысл — в заголовке: покажем его
                best_i = body_i
        start = best_i
        sec = units[best_i][0]
        if units[best_i][2] == "i":
            # ответ — шаг списка: показываем список целиком, с вводной
            while start > 0 and units[start - 1][0] == sec and \
                    units[start - 1][2] == "i":
                start -= 1
            if start > 0 and units[start - 1][0] == sec and \
                    units[start - 1][1].rstrip().endswith(":"):
                start -= 1
        elif start > 0 and units[start - 1][0] == sec and \
                _CONNECT_RE.match(units[start][1]) and \
                not _junk(units[start - 1][1]):
            start -= 1                     # «Он/Это…» — нужен предыдущий
        pick = list(range(start, best_i + 1))
        if head >= 0 and head not in pick:
            pick.insert(0, head)
        list_mode = units[best_i][1].rstrip().endswith(":") or \
            units[best_i][2] == "i" or \
            (best_i + 1 < n and units[best_i + 1][2] == "i")
        # конкретный вопрос — ответ + пара предложений контекста, а не
        # пол-статьи; список шагов берём целиком и на нём останавливаемся
        want = max_chars if list_mode else max_chars * 0.55
        in_list = units[best_i][2] == "i"
        k = best_i + 1
        while k < n:
            pk, sk, kind = units[k]
            if pk != sec:
                if list_mode and not in_list and kind == "i":
                    sec = pk               # список начался после пустой строки
                else:
                    break                  # другой раздел — не наш ответ
            if list_mode:
                if kind == "i":
                    in_list = True
                elif in_list:
                    break                  # список кончился — ответ тоже
                elif _len(pick) >= want:
                    break
            elif _len(pick) >= want:
                break
            if _junk(sk, kind == "i"):
                k += 1
                continue
            if _len(pick) + len(sk) > max_chars * 1.15:
                break
            pick.append(k)
            k += 1
    # недостающие слова вопроса — лучшие единицы из других мест статьи
    covered = set()
    for k in pick:
        covered |= scored[k][1]
    extra = []
    best_cov = max((cv for cv, _g in scored), default=0.0)
    if _len(pick) < max_chars * 1.3:
        for i, (cv, gs) in sorted(enumerate(scored),
                                  key=lambda x: -x[1][0]):
            if len(extra) >= 2 or cv <= 0 or cv < 0.6 * best_cov:
                break
            if i in pick or units[i][2] == "h" or not (gs - covered):
                continue
            extra.append(i)
            covered |= gs
    if not pick:
        return ""
    # v3.27: спросили КОМАНДУ, а в ответе её нет — берём команду(ы) из
    # статьи: сначала из того же раздела после ответа, иначе ближайшие
    if want_cmd and not any(_is_cmd(units[k][1]) for k in pick):
        cmds = [k for k in range(n) if units[k][2] != "h" and
                _is_cmd(units[k][1]) and k not in pick]
        if cmds:
            anchor = pick[-1]
            sec_a = units[anchor][0]
            same = [k for k in cmds if units[k][0] == sec_a and k > anchor]
            first = (same or sorted(cmds, key=lambda k: abs(k - anchor)))[0]
            got = [first]
            for k in range(first + 1, min(n, first + 6)):   # подряд идущие
                if k in cmds and units[k][0] == units[first][0]:
                    got.append(k)
                else:
                    break
            pick = sorted(set(pick) | set(got))
    main_txt = _join_units(units, sorted(pick))
    if main_txt.rstrip().endswith(":"):
        main_txt = main_txt.rstrip(" :") + "."
    parts = [main_txt]
    if extra:
        parts.append(_join_units(units, sorted(extra)))
    return "\n\n".join(p for p in parts if p).strip()


def _trim_chars(text, limit):
    """v3.27: сокращать только по концу предложения/абзаца (раньше резали
    по запятой и ставили «…» — ответ обрывался на полуфразе)."""
    return textutils.cut_at_sentence(text, limit)


def _neighbor_cmd(hit, window=300.0):
    """v3.27: команда, выученная ОТДЕЛЬНОЙ статьёй сразу после описания
    (группа МАКС: «При зависании редос…», а следующим сообщением —
    «sudo …»): статья того же источника, появившаяся в пределах window
    секунд ПОСЛЕ описания, и состоящая из команд. Возврат: текст|""."""
    try:
        with _IDX_LOCK:
            me = next((d for d in _IDX["docs"] if d.get("alive") and
                       d.get("id") == hit.get("id")), None)
            if not me:
                return ""
            t0, src = float(me.get("ts") or 0), me.get("source")
            near = sorted((d for d in _IDX["docs"] if d.get("alive") and
                           d.get("id") != me.get("id") and
                           d.get("source") == src and
                           0 < float(d.get("ts") or 0) - t0 < window),
                          key=lambda d: float(d.get("ts") or 0))[:3]
        for d in near:
            art = store.load_json(d["path"], None) or {}
            lines = [ln.strip() for ln in
                     str(art.get("text") or "").split("\n") if ln.strip()]
            if lines and all(_is_cmd(ln) for ln in lines[:5]):
                return "\n".join(lines[:5])
    except Exception:
        pass
    return ""


def compose_answer(q, hits=None, max_chars=900, focus=None, lead=None,
                   avoid=""):
    """Вопрос + найденные статьи -> связный ответ с подписью источников.
    hits — результаты search() (если None — ищет сам).
    focus — НОВЫЙ вопрос внутри темы диалога («а какая у неё площадь?»
    после «Антарктиды»): статья — по теме, а фрагмент — по focus, как
    LLM с памятью диалога. lead — принудительно (не)начинать с начала
    статьи; avoid — уже сказанное (не повторять). Возврат:
    {ok, text, sources:[title], article:id} или {ok: False}."""
    q = str(q or "").strip()[:200]
    if hits is None:
        hits = (search(q, limit=3).get("results") or [])
    hits = [h for h in (hits or []) if not h.get("wiki") and h.get("id")]
    if not q or not hits:
        return {"ok": False}
    with _IDX_LOCK:
        groups = _query_groups(focus) if focus else []
        focused = bool(groups)
        if not groups:
            groups = _query_groups(q)
        if not groups:
            return {"ok": False}
        idfs = [idf for _h, idf in _group_stats(groups)]
    main = hits[0]
    art = article(main["id"], main.get("device") or None) or {}
    text = str(art.get("text") or "")
    if not text:
        return {"ok": False}
    # «что такое X» про статью ровно о X — начинаем с определения
    if lead is None:
        lead = bool(not focused and main.get("tcov", 0) >= 0.99 and
                    (_DEF_Q.match(q) or (len(groups) <= 3 and
                                         not _SPECIFIC_Q.search(q))))
    # (если спрошено то, чего нет в начале, _passage сам добавит лучшие
    # недостающие фрагменты статьи)
    want_cmd = bool(_CMD_Q.search(focus or q))
    body = _passage(text, groups, idfs, max_chars=max_chars, lead=lead,
                    avoid=avoid, want_cmd=want_cmd)
    if not body and not lead and float(main.get("tcov") or 0) >= 0.5:
        # v3.27: вопрос совпал с НАЗВАНИЕМ статьи, а в тексте слова в других
        # формах («костёр» в названии — «костра» в тексте): отвечаем
        # началом статьи, а не «не знаю»
        body = _passage(text, groups, idfs, max_chars=max_chars, lead=True,
                        avoid=avoid, want_cmd=want_cmd)
    if not body:
        return {"ok": False}
    missing_cmd = False
    if want_cmd and not any(_is_cmd(ln) or _HAS_CMD_RE.search(ln)
                            for ln in body.split("\n")):
        cmd = _neighbor_cmd(main)
        if cmd:
            body = body.rstrip() + "\n" + cmd
        else:
            missing_cmd = True             # спросили команду — а её нет
    paras = [_trim_chars(body, int(max_chars * 1.4))]
    sources = [str(main.get("title") or "KB")]
    # вторая статья — только на конкретный вопрос, если она почти так же
    # релевантна; на «что такое X» статья о X самодостаточна (LLM не
    # приплетает «Флаг Антарктиды» к рассказу об Антарктиде)
    if len(hits) > 1 and not lead and not focused:
        h2 = hits[1]
        s1, s2 = float(main.get("score") or 0), float(h2.get("score") or 0)
        close = s2 >= 0.88 * s1 and float(h2.get("cov") or 0) >= 0.99
        if s2 >= 2.0 and close and \
                str(h2.get("title") or "") not in sources:
            a2 = article(h2["id"], h2.get("device") or None) or {}
            p2 = _passage(str(a2.get("text") or ""), groups, idfs,
                          max_chars=max(250, max_chars // 3))
            p2 = _trim_chars(p2.split("\n\n")[0], max(320, max_chars // 2))
            if p2 and p2[:80] not in paras[0]:
                paras.append("Также в «%s»: %s" % (h2.get("title"), p2))
                sources.append(str(h2.get("title") or "KB"))
    if len(sources) == 1:
        foot = "_Источник: «%s»_" % sources[0]
    else:
        foot = "_Источники: %s_" % ", ".join("«%s»" % s for s in sources)
    if missing_cmd:
        # честно: описание есть, самой команды в базе знаний нет
        paras.append("Самой команды в базе знаний нет — в статье только "
                     "описание. Пришли её («выучи: <команда>» или следующим "
                     "сообщением сразу после описания) — я запомню.")
    return {"ok": True, "text": "\n\n".join(paras) + "\n" + foot,
            "sources": sources, "article": main.get("id"),
            "missing_cmd": missing_cmd}


def remember_from_web(q):
    """Найти в Википедии и СОХРАНИТЬ статью в KB (облако накапливает знания,
    которые мозг потом читает даже без интернета). Возврат: id|None.
    v3.12.1: сеть/блокировка не роняют вызов — просто None (честно)."""
    try:
        res = webfetch.wiki_search(q, limit=1)
    except Exception:
        return None
    if not res:
        return None
    title = res[0]["title"]
    text = webfetch.wiki_extract(title)
    if len(text) < 80:
        return None
    r = add_article(title, text, source="Википедия (авто)")
    return r.get("id")


def purge_bad_web(min_cov=0.5):
    """v3.74: удалить из базы кэш ответов из интернета, в тексте которого нет
    слов вопроса (название статьи «Из интернета: <вопрос>»). Пример: ответ
    про конденсаторы монитора на вопрос про архиватор. Возврат: сколько."""
    n = 0
    for a in list_articles(limit=1000000, include_devices=True) or []:
        try:
            if not str(a.get("source") or "").startswith("интернет: выучено"):
                continue
            art = article(a.get("id")) or {}
            q = str(art.get("title") or "").split(":", 1)[-1]
            qs = set(_stems(q)) - _STOP_STEMS
            if len(qs) < 1:
                continue
            ts = set(_stems(str(art.get("text") or "")))
            if len(qs & ts) / float(len(qs)) < min_cov:
                if delete_article(a.get("id")):
                    n += 1
        except Exception:
            continue
    return n


def stem_idf(stem):
    """v3.94: редкость основы слова в базе (idf): «архиватор» > «компьютер»."""
    import math as _m
    with _IDX_LOCK:
        n = max(1, _IDX.get("alive") or 1)
        p = (_IDX.get("post") or {}).get(stem)
        df = len(p[0]) if p else 0
    return max(0.05, _m.log(1.0 + (n - df + 0.5) / (df + 0.5)))
