# -*- coding: utf-8 -*-
# ===========================================================================
# multisource.py — ИНФОРМАЦИЯ С ПЕРВЫХ СТРАНИЦ ПОИСКА (v3.14, «как у
# Алисы», заявка владельца): «чтобы это были не вопросы по ключевым
# словам, а просто информация, предоставленная из нескольких источников
# в интернете, например по первым 10 страницам».
#
# Как работает (без LLM, только честно):
#   1) webfetch.top_pages() даёт первые ~10 ссылок выдачи (DDG html/lite
#      + Википедия как гарантированный источник);
#   2) каждая страница СКАЧИВАЕТСЯ ЦЕЛИКОМ (webfetch.fetch_page: https,
#      публичные IP, DoH при DNS-блокировке, SNI + сертификат, редиректы
#      под проверкой, лимиты; приватные адреса запрещены всегда);
#   3) из текста каждой страницы берутся самые РЕЛЕВАНТНЫЕ вопросу
#      предложения (пересечение корней слов — никаких выдуманных фраз);
#   4) ответ собирается так: каждый фрагмент ПОДПИСАН своим сайтом
#      («• Википедия — «Транзистор»»), в конце — нумерованный список
#      «Источники:» со ссылками; что не открылось — честная причина.
# Резерв при пустой выдаче — webfetch._alice_simple (v3.13, API-выжимки
# Википедии и сниппетов Google/Яндекса); диспетчер — webfetch.alice().
# ===========================================================================
import math
import re
import time
import urllib.parse
from concurrent import futures as _futures
from concurrent.futures import ThreadPoolExecutor

import store
import textutils
import webfetch

_CACHE = {}            # q.lower() -> (ts, reply|None, meta)
_CACHE_TTL = 600       # 10 минут: не дёргаем сайты повторно
_NEG_TTL = 120         # пустой ответ держим 2 минуты (быстрый честный отказ)
_BUDGET = 9.0          # v3.135: секунд на выгрузку страниц (было 18, раньше 30)
_ENOUGH = 3            # v3.135: столько годных страниц — хватит, не ждём остальные
_TIMEOUT = 9.0         # секунд на одну страницу
_WORKERS = 8
_MAX_PAGES = 10        # «по первым 10 страницам» — заявка владельца
_MAX_EXCERPT = 6       # сколько фрагментов в ответе (список — весь)
_EXCERPT_CHARS = 400
_TEXT_MAX = 40000      # v3.27: знаков текста страницы для сборки ответа

# служебные слова вопроса не ищем в тексте страниц
_QSTOP = set("""
что кто где когда почему зачем как какой какая какие каких каким кому чем
откуда расскажи покажи найди объясни опиши такое такой такая такое сколько
это мне тебя скажи скажет пожалуйста скажи-ка про об о
""".split())


def _qwords(q):
    """Корни слов вопроса (без служебных) — по ним ищем релевантное."""
    out = set()
    for w in textutils.WORD_RE.findall(str(q or "").lower()):
        if w in _QSTOP:
            continue
        s = textutils.stem(w)
        if len(s) >= 3:
            out.add(s)
    return out


def _source_name(url):
    """Подпись источника: Википедия / Википедия (en) / домен без www."""
    host = str(url or "").lower()
    host = re.sub(r"^[a-z]+://", "", host).split("/")[0].split(":")[0]
    host = re.sub(r"^(www|m|mobile)\.", "", host)
    if host.endswith("wikipedia.org"):
        return "Википедия (en)" if host.startswith("en.") else "Википедия"
    return host or "интернет"


def _relevant(text, q, max_sents=2, min_chars=50):
    """Самые релевантные вопросу предложения САМОЙ страницы (пересечение
    корней слов). При равном счёте раньше идёт более ранняя фраза —
    вводка статьи естественнее середины. Ничего не сочиняет."""
    want = _qwords(q)
    if not want:
        return ""
    scored = []
    for idx, s in enumerate(textutils.sentences(str(text or ""))[:500]):
        s = " ".join(s.split())
        if len(s) < min_chars or len(s) > 420:
            continue
        ss = {textutils.stem(w)
              for w in textutils.WORD_RE.findall(s.lower())}
        score = len(ss & want)
        if score:
            scored.append((-score, idx, s))
    scored.sort()
    out, used = [], set()
    for _neg, _idx, s in scored:
        k = s[:40].lower()
        if k in used:
            continue
        used.add(k)
        out.append(s)
        if len(out) >= max_sents:
            break
    return " ".join(out)


def _first(text, chars=320):
    """Запас, когда по корням ничего не совпало: первые осмысленные
    предложения страницы (всё равно честный текст источника)."""
    for s in textutils.sentences(str(text or "")):
        s = " ".join(s.split())
        if len(s) >= 40:
            return s[:chars]
    return " ".join(str(text or "").split())[:chars]


def _wiki_title(url, fallback=""):
    m = re.search(r"/wiki/([^/?#]+)", str(url or ""))
    if m:
        try:
            t = urllib.parse.unquote(m.group(1)).replace("_", " ").strip()
            if t:
                return t
        except Exception:
            pass
    return fallback


def _wiki_source(q, r):
    """Источник «Википедия»: чистый plaintext статьи через API (быстрее
    и чище, чем разбор html-страницы энциклопедии)."""
    url = str(r.get("url") or "")
    title = _wiki_title(url, r.get("title") or "")
    lang = "en" if re.search(r"//en\.", url) else "ru"
    try:
        text = webfetch.wiki_extract(title, lang=lang)
    except Exception as e:
        return None, webfetch.honest(e)
    if not text:
        return None, "статья не открылась"
    # вики-разметка разделов («== Устройство ==») — это НЕ предложения:
    # строки-заголовки убираем, чтобы выжимка читалась как текст
    text = re.sub(r"^\s*={2,}[^=\n]*={2,}\s*$", "", str(text), flags=re.M)
    text = re.sub(r"\n{2,}", "\n", text)
    rel = _relevant(text, q) or _first(text)
    if not rel:
        return None, "в статье нет текста"
    return {"name": _source_name(url), "url": url, "title": title[:160],
            "excerpt": rel, "text": str(text)[:_TEXT_MAX]}, None


def _page_source(q, r):
    """Обычная страница выдачи: скачать целиком и взять релевантное."""
    try:
        ok, page = webfetch.fetch_page(r.get("url"), timeout=_TIMEOUT)
    except Exception as e:
        return None, webfetch.honest(e)
    if not ok:
        return None, str(page)[:160]
    text = page.get("text") or ""
    rel = _relevant(text, q) or _first(text)
    if not rel:
        return None, "страница без текста"
    return {"name": _source_name(page.get("url") or r.get("url")),
            "url": page.get("url") or r.get("url"),
            "title": (page.get("title") or r.get("title") or "")[:160],
            "excerpt": rel, "text": str(text)[:_TEXT_MAX]}, None


def gather_pages(q, max_pages=_MAX_PAGES, errors=None):
    """Скачать первые страницы выдачи и собрать подписанные фрагменты.
    Возврат (sources[{name,url,title,excerpt}], tried, failed[{name,why}]).
    Параллельно (ThreadPoolExecutor), в пределах бюджета времени;
    не уложившиеся — честная причина «бюджет времени»."""
    errors = errors if errors is not None else []
    try:
        links = webfetch.top_pages(q, limit=max_pages)
    except Exception as e:
        errors.append(webfetch.honest(e))
        links = []
    if not links:
        return [], 0, []
    jobs = []
    for i, r in enumerate(links):
        kind = "wiki" if "wikipedia.org/wiki/" in \
            str(r.get("url") or "").lower() else "page"
        jobs.append((i, kind, r))
    tried = len(jobs)
    results = {}

    def run(kind, r):
        if kind == "wiki":
            return _wiki_source(q, r)
        return _page_source(q, r)

    # v3.27: БЕЗ `with`: выход из with ждал ВСЕ зависшие страницы даже
    # после бюджета времени — вопрос висел минутами. Теперь бюджет
    # честный: не успевшие страницы бросаем (их потоки доработают сами).
    pool = ThreadPoolExecutor(max_workers=_WORKERS)
    try:
        futs = {pool.submit(run, k, r): (i, k, r) for i, k, r in jobs}
        pending = dict(futs)
        try:
            for fut in _futures.as_completed(list(futs), timeout=_BUDGET):
                i, _k, _r = futs[fut]
                try:
                    src, why = fut.result()
                except Exception as e:
                    src, why = None, webfetch.honest(e)
                results[i] = (src, why)
                pending.pop(fut, None)
                if sum(1 for s_, _w in results.values() if s_) >= _ENOUGH:
                    break                  # v3.135: годных страниц хватает
        except _futures.TimeoutError:
            pass
        for fut, (i, _k, _r) in pending.items():
            try:
                fut.cancel()
            except Exception:
                pass
            if i not in results:
                results[i] = (None, "не дождался ответа (бюджет времени)")
    finally:
        try:
            pool.shutdown(wait=False, cancel_futures=True)
        except TypeError:                  # Python < 3.9
            pool.shutdown(wait=False)
    sources, failed = [], []
    for i, _k, r in jobs:
        src, why = results.get(i, (None, "не выполнено"))
        if src:
            sources.append(src)
        else:
            failed.append({"name": _source_name(r.get("url")),
                           "why": (why or "не открылась")[:160]})
    # дедуп по адресу (у разных страниц совпадающие заголовки честно остаются)
    seen, uniq = set(), []
    for s in sources:
        nu = webfetch._norm_url(s["url"])
        if nu in seen:
            continue
        seen.add(nu)
        uniq.append(s)
    return uniq, tried, failed


# ===========================================================================
# v3.27: СИНТЕЗ ОТВЕТА ИЗ СТРАНИЦ «КАК У LLM» (заявка владельца: «не
# перечисляет источники, а собирает на основе этих источников текст»).
# Без LLM и без выдумок — только предложения самих страниц, но собранные
# в связный ответ:
#   1) каждая страница -> предложения (тот же разбор, что у базы знаний:
#      формулы, сокращения «англ.», списки шагов); мусор сайтов (cookie,
#      меню, «подпишитесь», копирайты) отбрасывается;
#   2) вес предложения: доля слов вопроса (по редкости), ПОДТВЕРЖДЕНИЕ
#      другими сайтами (факт, который повторяют несколько источников,
#      надёжнее), место в тексте (вводка статьи — определение), для «что
#      такое X» — предложения вида «X — это …»;
#   3) ответ = лучшее предложение + его продолжение на той же странице
#      (контекст) + недостающие факты с других сайтов без повторов
#      (отбор по новизне, MMR); источники в тексте не перечисляются.
# ===========================================================================
_BOILER = re.compile(
    r"cookie|куки|javascript|подпис(ыва|а)т|подпишитесь|рассылк|реклам|"
    r"войти|вход в|регистрац|авториз|©|все права|политик\w* конфиденц|"
    r"пользовательск\w* соглаш|наш сайт|на сайте|перейти|читать (далее|"
    r"также|ещё|еще)|подробнее на|скачать|загрузк|корзин|telegram|"
    r"vk\.com|whatsapp|поделиться|комментари|оцените|понравил|"
    r"нажмите здесь|click|subscribe|copyright|share|источник:|"
    r"фото:|изображени|видео:|^\s*\d{1,2}[:.]\d\d\b", re.I)


_FORMULA_SCRAP = re.compile(r"[:=]\s*[^\sА-Яа-яЁё]{1,3}\s+[А-ЯЁA-Z]|"
                            r"\s[α-ωΑ-Ω]\s+[А-ЯЁ]")


def _same_root(a, b):
    """Одно слово в разных формах: «перенест»~«перенос», «запис»~«записа»."""
    if a == b:
        return True
    if abs(len(a) - len(b)) > 3 or min(len(a), len(b)) < 5:
        return False
    cp = 0
    for x, y in zip(a, b):
        if x != y:
            break
        cp += 1
    return cp >= max(5, max(len(a), len(b)) - 3)


def synthesize(q, sources, max_chars=800):
    """Связный ответ на вопрос из текстов страниц (см. блок выше).
    Возврат: (текст|None, сколько источников реально использовано)."""
    import kb                               # разбор текста — как у базы знаний
    q = str(q or "").strip()
    qst = []
    for s in kb._stems(q):
        # «команда/терминал» — намерение вопроса, а не слово ответа: его
        # закрывает сама команда, которую ответ добавит обязательно
        if s not in kb._STOP_STEMS and s not in qst and \
                s not in kb._CMD_STEMS:
            qst.append(s)
    if not qst or not sources:
        return None, 0
    # единицы текста: (источник, позиция, текст, стемы)
    units = []
    page_sets = []
    for si, src in enumerate(sources):
        body = str(src.get("text") or src.get("excerpt") or "")
        pset = set()
        k = 0
        for _sec, s, _kind in kb._units(body)[:600]:
            if _kind == "h":
                continue                  # подзаголовки страниц — не текст
            s = " ".join(s.split())
            cmd = kb._is_cmd(s)
            if cmd:
                # v3.27: команда терминала короткая и со строчной буквы
                # («sudo reboot») — это не обрывок, а ответ
                if kb._junk(s) or _BOILER.search(s):
                    continue
            else:
                if not (40 <= len(s) <= (900 if "$" in s else 450)) or kb._junk(s) or \
                        _BOILER.search(s) or s.count("|") > 1:
                    continue
                # обрывки: пункт списка со строчной буквы, остаток формулы
                # («…массы: ρ Сверхмассивные…»)
                if s[0].islower() or (_FORMULA_SCRAP.search(s) and
                                      "$" not in s):   # v3.110: формула — не обрывок
                    continue
                # v3.27: вопрос со страницы — не ответ («Что такое «Алиса,
                # ты что умеешь» и зачем это спрашивать?»)
                if s.rstrip(" »\")").endswith("?"):
                    continue
            st = set(kb._stems(s))
            pset |= st
            units.append({"src": si, "pos": k, "t": s, "st": st,
                          "cmd": cmd})
            k += 1
        page_sets.append(pset)
    if not units:
        return None, 0
    vocab = set()
    for ps in page_sets:
        vocab |= ps
    # группы форм слова вопроса — по словарю самих страниц
    groups = []
    for t in qst:
        groups.append({v for v in vocab if _same_root(t, v)} | {t})
    n_units = len(units)
    idf = []
    for g in groups:
        df = sum(1 for u in units if u["st"] & g)
        w = math.log(1.0 + n_units / (1.0 + df)) + 0.3
        # слова нет НИ НА ОДНОЙ странице («остановит») — оно не отличает
        # предложения, а с максимальным весом топило верный ответ
        idf.append(w * 0.35 if df == 0 else w)
    sum_idf = sum(idf) or 1.0
    definitional = bool(kb._DEF_Q.match(q) or (len(groups) <= 3 and
                                                not kb._SPECIFIC_Q.search(q)))
    first_g = groups[0] if groups else set()
    n_src = len(sources)
    for u in units:
        got = [gi for gi, g in enumerate(groups) if u["st"] & g or
               (u["cmd"] and kb._CMD_STEMS & g)]
        u["cov"] = sum(idf[gi] for gi in got) / sum_idf
        u["got"] = set(got)
        content = {x for x in u["st"] if x not in kb._STOP_STEMS}
        agree = 0.0
        if content and n_src > 1:
            others = [ps for i, ps in enumerate(page_sets) if i != u["src"]]
            agree = sum(len(content & ps) / len(content)
                        for ps in others) / len(others)
        u["content"] = content
        # подтверждение другими сайтами — без премии за длину (длинное
        # общее предложение «совпадает» со всеми просто количеством слов)
        # «плотность ответа»: в коротком предложении слова вопроса — суть
        # («…с массой 10⁵–10¹¹ масс Солнца»), в длинном — теряются
        dens = min(1.0, 2.0 * len(got) / max(1, len(content)))
        sc = 0.62 * u["cov"] + 0.10 * agree + 0.15 * dens + \
            0.12 / (1.0 + u["pos"] / 3.0) + 0.06 / (1.0 + u["src"]) - \
            0.06 * max(0.0, (len(u["t"]) - 220) / 220.0)
        u["def"] = False
        if definitional or len(groups) <= 4:
            head = u["t"][:90]
            if re.search(r"\s[—–-]\s|\bэто\b|\bявляется\b", head) and \
                    set(kb._stems(head[:60])) & first_g:
                u["def"] = True
                if definitional:
                    sc += 0.25
        if kb._CONNECT_RE.match(u["t"]):
            sc -= 0.08                       # «Он/Это…» без подлежащего
        u["sc"] = sc
    # v3.30: ФОКУС ВОПРОСА (focus.py): «архиватор на Бештау» — предложения
    # с «Бештау» главнее общих фраз про архиваторы; на страницах фокуса
    # нет совсем — ответ честно помечается как общий
    try:
        import focus
        f_words = focus.terms(q)
        f_st = focus.stems(f_words)
    except Exception:
        f_words, f_st = [], set()
    if f_st:
        for u in units:
            if u["st"] & f_st:
                u["sc"] += 0.35
    # v3.30: ОПЫТ ОБУЧЕНИЯ — сайты, чьи ответы люди подтверждали 👍,
    # ценятся выше; отклонённые 👎 — ниже (qalearn.rep, от −5 до +10)
    try:
        import qalearn
        reps = [qalearn.rep(s.get("url")) for s in sources]
        for u in units:
            r = reps[u["src"]]
            if r:
                u["sc"] += 0.04 * max(-3.0, min(5.0, r))
    except Exception:
        pass
    # v3.110: спросили ФОРМУЛУ — предложение, где формула записана целиком
    # («…уравнением Пуассона: $pV^γ=const$»), главнее рассказа о ней
    want_f = bool(re.search(r"формул|уравнени|выражени|как (?:вычисл|рассчит|"
                            r"посчит)", q, re.I))
    if want_f:
        for u in units:
            if re.search(r"\$[^$]*=[^$]*\$", u["t"]):
                u["sc"] += 0.45
                u["cov"] = max(u["cov"], 0.5)
    need_cov = 0.5 if len(groups) >= 2 else 0.99
    cands = [u for u in units if u["cov"] >= need_cov or
             (f_st and u["st"] & f_st and u["cov"] >= 0.25)]
    if not cands:
        return None, 0
    cands.sort(key=lambda u: -u["sc"])
    anchor = cands[0]
    picked = [anchor]
    long_ans = max_chars > 1000

    def _dup(u):
        """Повтор уже сказанного: похожее предложение или почти все его
        слова уже прозвучали (два определения подряд — тоже повтор)."""
        if u["def"] and any(p["def"] for p in picked):
            return True
        said = set()
        for p in picked:
            inter = len(u["content"] & p["content"])
            union = len(u["content"] | p["content"]) or 1
            if inter / union >= 0.45:
                return True
            said |= p["content"]
        new = [x for x in u["content"] if x not in said]
        return len(new) < max(2, 0.4 * len(u["content"]))

    def _size():
        return sum(len(p["t"]) + 1 for p in picked)

    # продолжение лучшего предложения на ТОЙ ЖЕ странице — контекст
    # «что такое X» — вводка страницы целиком (определение продолжается
    # фразами без слов вопроса: «Граница этой области называется…»);
    # конкретный вопрос — ответ и пара фраз контекста по теме
    same = [u for u in units if u["src"] == anchor["src"] and
            u["pos"] > anchor["pos"]]
    same.sort(key=lambda u: u["pos"])
    cont_max, cont_share = (8, 0.75) if definitional else (3, 0.55)
    for u in same[:cont_max]:
        if u["pos"] - picked[-1]["pos"] > 1 or \
                _size() + len(u["t"]) > max_chars * cont_share:
            break
        if u["t"].rstrip().endswith(":"):
            break                          # дальше список — его тут нет
        if (definitional or u["cov"] > 0 or kb._CONNECT_RE.match(u["t"])) \
                and not _dup(u):
            picked.append(u)
        else:
            break
    main_n = len(picked)
    covered = set()
    for p in picked:
        covered |= p["got"]
    # недостающие факты с других мест/сайтов — по новизне, без повторов;
    # на «что такое X» — только из вводных частей страниц (дальше там
    # частности: «в 2019 году сфотографировали…» — это не определение)
    for u in cands[1:]:
        if _size() >= max_chars:
            break
        if u in picked or _dup(u) or kb._CONNECT_RE.match(u["t"]) or \
                u["t"].rstrip().endswith(":"):
            continue
        if definitional and u["pos"] > (20 if long_ans else 6):
            continue
        if u["sc"] < (0.6 if long_ans else 0.7) * anchor["sc"]:
            break
        if _size() + len(u["t"]) > max_chars * 1.15:
            continue
        picked.append(u)
        covered |= u["got"]
    # v3.27: спросили КОМАНДУ — в ответе обязана быть сама команда:
    # ближайшая после лучшего предложения на той же странице, иначе
    # лучшая команда с любой страницы (рядом с текстом по вопросу)
    if kb._CMD_Q.search(q) and not any(p["cmd"] for p in picked):
        cmds = [u for u in units if u["cmd"]]
        same = sorted((u for u in cmds if u["src"] == anchor["src"] and
                       u["pos"] > anchor["pos"]), key=lambda u: u["pos"])
        if same:
            pick_c = [same[0]] + [u for u in same[1:4]
                                  if u["pos"] - same[0]["pos"] <= 3]
        else:
            near = [u for u in cmds if any(
                v["src"] == u["src"] and abs(v["pos"] - u["pos"]) <= 2 and
                v["cov"] > 0 for v in units)]
            pick_c = sorted(near, key=lambda u: -u["sc"])[:1]
        picked[main_n:main_n] = pick_c
        main_n += len(pick_c)
    used = len({p["src"] for p in picked})

    def _j(seq):                         # команды — отдельными строками
        out, prev = "", None
        for p in seq:
            if not out:
                out = p["t"]
            else:
                sep = "\n" if (p["cmd"] or (prev and prev["cmd"])) else " "
                out += sep + p["t"]
            prev = p
        return out

    main = _j(picked[:main_n])
    rest = sorted(picked[main_n:], key=lambda u: (u["src"], u["pos"]))
    paras = [main]
    last_src = None
    for p in rest:                       # абзац = факты одного источника
        if len(paras) > 1 and p["src"] == last_src:
            paras[-1] += ("\n" if p["cmd"] else " ") + p["t"]
        else:
            paras.append(p["t"])
        last_src = p["src"]
    text = "\n\n".join(paras)
    text = kb._trim_chars(text, int(max_chars * 1.3))
    if f_st and not any(p["st"] & f_st for p in picked):
        # честно: конкретики по фокусу на страницах нет — это общее
        text = ("Именно про %s на найденных страницах ничего нет — вот "
                "общее по теме:\n\n%s" % (focus.label(f_words), text))
    return text, used


def compose(q, sources, tried, failed, errors=None):
    """Собрать ответ: фрагменты с ПОДПИСЬЮ каждого сайта + нумерованный
    список «Источники:» + честные причины неоткрывшихся страниц."""
    lines = ["Нашёл в интернете по «%s»: источников %d, страниц "
             "запрошено %d." % (q, len(sources), tried)]
    for s in sources[:_MAX_EXCERPT]:
        lines.append("")
        lines.append("• %s — «%s»" % (s["name"],
                                      (s.get("title") or s.get("url", ""))[:120]))
        lines.append(s["excerpt"][:_EXCERPT_CHARS])
    lines.append("")
    lines.append("Источники:")
    for i, s in enumerate(sources, 1):
        lines.append("%d. %s — %s" % (i, s["name"], s["url"]))
    if failed:
        lines.append("")
        lines.append("Не открылось (%d из %d): %s." % (
            len(failed), tried,
            "; ".join("%s — %s" % (f["name"], f["why"])
                      for f in failed[:3])))
    if errors:
        lines.append("Примечание: %s." % "; ".join(errors[:2]))
    return "\n".join(lines)[:6000]


def pages_answer(q, long_mode=False):
    """Главный вход «первых страниц»: (reply|None, meta{sources, errors}).
    v3.27: ответ — связный текст, СОБРАННЫЙ из страниц (synthesize), без
    перечня сайтов; сами источники остаются в meta (для панели и журнала).
    long_mode — длинный ответ (галочка «длинно»).
    Ответ кэшируется (10 минут), пустой результат — 2 минуты, чтобы
    конвейер мозга не дёргал сайты дважды на один вопрос."""
    q = str(q or "").strip()[:120]
    if not q:
        return None, {"sources": [], "errors": ["пустой запрос"]}
    now = time.time()
    ckey = q.lower() + ("|long" if long_mode else "")
    hit = _CACHE.get(ckey)
    if hit and now - hit[0] < _CACHE_TTL:
        if hit[1]:
            return hit[1], dict(hit[2])
        if now - hit[0] < _NEG_TTL:
            return None, dict(hit[2])
    try:
        max_pages = int(store.settings().get("web_pages") or _MAX_PAGES)
    except Exception:
        max_pages = _MAX_PAGES
    max_pages = max(1, min(max_pages, _MAX_PAGES))
    errors = []
    try:
        sources, tried, failed = gather_pages(q, max_pages=max_pages,
                                              errors=errors)
    except Exception as e:
        return None, {"sources": [], "errors": errors + [webfetch.honest(e)]}
    if sources:
        try:
            reply, used = synthesize(q, sources,
                                     max_chars=1700 if long_mode else 800)
        except Exception as e:
            reply, used = None, 0
            errors.append("сборка ответа: %s" % e)
        # v3.30: в ответе нет фокуса вопроса («Бештау») — второй поиск
        # с фокусом в кавычках, если бюджет времени позволяет
        if reply and reply.startswith("Именно про") and \
                time.time() - now < 9.0:
            try:
                import focus
                fw = focus.terms(q)
                if fw:
                    q2 = ('"%s" %s' % (fw[0], q))[:120]
                    s2, t2, _f2 = gather_pages(q2, max_pages=max_pages,
                                               errors=errors)
                    have = {webfetch._norm_url(s["url"]) for s in sources}
                    more = [s for s in s2
                            if webfetch._norm_url(s["url"]) not in have]
                    if more:
                        r2, u2 = synthesize(q, more + sources,
                                            max_chars=1700 if long_mode
                                            else 800)
                        if r2 and not r2.startswith("Именно про"):
                            reply, used = r2, u2
                            sources = more + sources
                            tried += t2
            except Exception as e:
                errors.append("поиск по фокусу: %s" % webfetch.honest(e))
        meta = {"sources": [{"name": s["name"], "url": s["url"],
                             "note": (s.get("title") or "")[:120],
                             "excerpt": (s.get("excerpt") or "")[:200]}
                            for s in sources],
                "errors": errors, "mode": "synth", "used": used}
        if reply:
            _CACHE[ckey] = (now, reply, meta)
            return reply, meta
        # страницы открылись, но ни одно предложение не отвечает на вопрос
        errors = errors + ["на страницах выдачи ответа на вопрос нет"]
        _CACHE[ckey] = (now, None, {"sources": [], "errors": errors})
        return None, {"sources": [], "errors": errors}
    errors = errors or ["страницы выдачи не ответили"]
    _CACHE[ckey] = (now, None, {"sources": [], "errors": errors})
    return None, {"sources": [], "errors": errors}
