# -*- coding: utf-8 -*-
# ===========================================================================
# study.py — ССЫЛКИ-ИНСТРУКЦИИ (v3.25.0: ещё и ВЛОЖЕНИЯ Confluence,
# пространства — с v3.24.5, файлы — с v3.24.3; stdlib)
# ---------------------------------------------------------------------------
# Заявка владельца: «в настройках на сервере можно было добавить ссылку,
# откуда можно почитать инструкции, и пусть эти инструкции с ссылок
# сохраняются в базу данных».
#
# КАК РАБОТАЕТ:
#   Настройки -> База знаний -> «Ссылки с инструкциями»: владелец добавляет
#   https-ссылку, облако САМО скачивает страницу (webfetch.fetch_page —
#   только https, SSRF-защита, кодировки utf-8/cp1251, теги выкидываются),
#   вычленяет текст и сохраняет его в базу знаний (kb/common) статьёй
#   «Инструкции: <заголовок страницы>». Статья сразу уходит и в сеть
#   материалов (kb.add_article -> brain.mn_learn) — облако становится
#   умнее сразу, отвечать по инструкции можно из чата и Макс-бота.
#
#   v3.24.3 — «А она скачивает файлы с сайтов инструкций?» — ТЕПЕРЬ ДА:
#   1) прямая ссылка на файл (…/manual.pdf, …/ustroystvo.docx, …/shema.txt)
#      — файл качается целиком (webfetch.fetch_file, до 20 МБ), текст
#      вытаскивается тем же механизмом, что и у Макс-бота
#      (textutils.extract_text: pdf, docx, doc, rtf, txt, md, csv, xlsx,
#      pptx…) и сохраняется статьёй «Инструкции: <имя файла>»;
#   2) страница-сайт инструкций — из её HTML берутся ссылки на файлы
#      ТОГО ЖЕ домена (чтобы не качать весь интернет), каждый файл —
#      отдельной статьёй «Инструкции: <страница> — <файл>». Лимиты:
#      study_files (вкл/выкл), study_files_max (сколько файлов со
#      страницы, по умолчанию 20), 20 МБ на файл.
#
#   v3.24.5 — «он прочитывает, но знаков больше 2130 — пусть прочитает
#   ВСЮ базу» (confluence.softrust.ru/collector/pages.action?key=docEMIAS):
#   страницы Confluence рисуются скриптами, в статичном HTML видно только
#   шапку (~2 тыс. знаков из МИЛЛИОНОВ). Решение — REST API Confluence:
#   1) ссылка на ПРОСТРАНСТВО (…pages.action?key=KEY, …spaceKey=KEY,
#      /spaces/KEY, /display/KEY/…) — облако ФОНОВО перечисляет ВСЕ
#      страницы пространства (без тел — малые ответы) и читает их тексты
#      пачками по 10 (сервер обрывает большие пачки — пачка дробится до
#      5/2/1, каждый обрыв дочитывается по частям). Каждая страница —
#      статья «Инструкции: <заголовок страницы>». Прогресс живёт в панели
#      («читается… N/M»), список id->id статей — в data/study_confluence/.
#      Лимит страниц на ссылку — study_pages_max (по умолчанию 1000).
#   2) ссылка на СТРАНИЦУ по id (…viewpage.action?pageId=N) — полное тело
#      страницы через REST одним запросом, честный текст без «2130».
#   v3.25.0 — «Пусть и вложения качаются»: после страниц читатель ОБХОДИТ
#   ВЛОЖЕНИЯ Confluence (REST /child/attachment, постранично, пагинация):
#   текстовые файлы (pdf, doc, docx, rtf, txt, md, csv, xlsx, pptx, xml…)
#   скачиваются (до 20 МБ) и каждый становится статьёй «Инструкции:
#   <страница> — <файл>». Картинки/видео/архивы пропускаются честно
#   (текста в них нет), сканы без текста запоминаются и не перекачиваются.
#   Версия вложения хранится в side-файле: перечитка качает ТОЛЬКО
#   обновившиеся файлы. Лимит за круг — study_attach_max (по умолчанию
#   300, 0 = не качать): десятки тысяч вложений читаются за несколько
#   кругов, прогресс виден в панели. Ссылка на страницу по pageId теперь
#   тоже качает вложения этой страницы (до 50 за раз — чтение синхронное).
#
#   ДУБЛЕЙ НЕТ: у каждой ссылки/страницы/файла свой id статьи в KB,
#   перечитывание ОБНОВЛЯЕТ те же статьи (did). «Перечитать» — кнопкой;
#   фоновый поток перечитывает все ссылки раз в study_refresh часов
#   (настройка панели, 0 = только вручную). Удаление ссылки убирает из
#   базы страницу/пространство и его статьи/файлы.
#
#   Список ссылок живёт в settings.json (study_links):
#     [{"url", "title", "chars", "ts", "ok", "err", "aid",
#       "kind": "page"|"file"|"confluence",
#       "files": [{"url","name","chars","ok","err","aid","att"}…],
#       "space", "pages_read", "pages_bad", "pages_total", "reading",
#       "attach_read", "attach_chars", "attach_bad", "attach_skip",
#       "attach_page", "attach_pages", "note"}], максимум 50.
# ===========================================================================
import os
import re
import threading
import time
import urllib.parse

import latexgen
import kb
import store
import textutils
import webfetch

_LOCK = threading.RLock()
_EV = threading.Event()
_THREAD = None
_STOP = [False]
_LAST_ROUND = [0.0]            # ts последнего фонового круга перечитки
_CHECK_PERIOD = 300.0          # сек между проверками «пора ли перечитывать»
_MAX_LINKS = 50

# v3.24.3: файлы-инструкции, которые качаем (виды умеет textutils)
FILE_EXTS = (".pdf", ".doc", ".docx", ".rtf", ".txt", ".md", ".csv",
             ".tsv", ".xlsx", ".pptx", ".json", ".log", ".ini", ".xml")
FILE_BYTES = 20 * 1024 * 1024  # 20 МБ на файл — больше не тащим
MIN_FILE_CHARS = 40            # меньше знаков текста — считаем файл пустым

# ------------------- v3.24.5: Confluence (всё пространство) -----------------
SIDEDIR = "study_confluence"   # data/study_confluence/<hash>.json — id страниц
CF_LIST = 100                  # страниц за запрос при перечислении (без тел)
CF_BATCH = 10                  # страниц за запрос при чтении тел
CF_STEPS = (10, 5, 2, 1)       # дробление пачки: сервер обрывает большие
CF_ENUM_CAP = 20000            # потолок перечисления страниц (страховка)
CF_HARD_CAP = 5000             # жёсткий потолок чтения страниц на ссылку
CF_ATT_LIST = 50               # v3.25.0: вложений за запрос списка (пагинация)
CF_ATT_PAGE_CAP = 500          # v3.25.0: потолок вложений на одну страницу
CF_ATT_SYNC_CAP = 50           # v3.25.0: вложений за раз в pageId-режиме
CF_SQUARE_RE = re.compile(
    r"/(?:spaces|display)/([A-Za-z0-9][A-Za-z0-9~_.\-]*)", re.I)
CF_HINT_RE = re.compile(
    r"(pages\.action|viewpage\.action|pageid=|spacekey=|/spaces/|/display/"
    r"|collector/)", re.I)
_READING = set()               # ссылки, читаемые фоном прямо сейчас (lower)


def _hash(url):
    """Устойчивый короткий ключ ссылки для имени side-файла."""
    import hashlib
    return hashlib.md5(_norm(url).lower().encode("utf-8")).hexdigest()[:16]


def _side_path(url):
    return "%s/%s.json" % (SIDEDIR, _hash(url))


def _confluence_plan(url):
    """Ссылка на Confluence? Что читать. Возврат:
    {"space": KEY|None, "pageId": "N"|None, "certain": bool} — certain,
    когда Confluence виден прямо в адресе (домен/actions/пути); иначе
    (просто ?key=…) нужен пробный REST-запрос перед перехватом ссылки."""
    u = urllib.parse.urlparse(_norm(url))
    q = urllib.parse.parse_qs(u.query or "") if u.query else {}
    space = (q.get("key") or q.get("spaceKey") or [None])[0]
    page_id = (q.get("pageId") or [None])[0]
    if not space:
        m = CF_SQUARE_RE.search(u.path or "")
        if m:
            space = m.group(1)
    host = (u.hostname or "").lower()
    # certain — Confluence виден ПРЯМО в адресе (домен/actions/пути).
    # Голые ?key=/?spaceKey=/?pageId= — НЕ certain: у чужого сайта такой
    # параметр может быть своим; их перехватывает только пробный
    # REST-запрос (_cf_space_ok / _read_confluence_page с фолбэком).
    certain = ("confluence" in host
               or bool(CF_HINT_RE.search(u.path or "")))
    return {"space": space, "pageId": page_id, "certain": bool(certain)}


def _cf_bases(url):
    """Кандидаты базы REST API: origin и origin/wiki (Atlassian Cloud)."""
    u = urllib.parse.urlparse(_norm(url))
    origin = "%s://%s" % (u.scheme, u.netloc)
    if (u.path or "").lower().startswith("/wiki"):
        return [origin + "/wiki"]
    return [origin, origin + "/wiki"]


def _cf_json(url, tries=3, timeout=30):
    """REST-запрос с повторами: Confluence-серверы бывают капризными
    (обрывы chunked). Возврат (True, объект) / (False, причина)."""
    last = ""
    for i in range(max(1, int(tries))):
        ok, data = webfetch.fetch_json(url, timeout=timeout,
                                       max_bytes=40000000)
        if ok:
            return True, data
        last = str(data)
        time.sleep(0.5 * (i + 1))
    return False, last


def _cf_space_list(base, space, limit=CF_LIST):
    """Одна страница списка страниц пространства (без тел)."""
    url = ("%s/rest/api/content?type=page&spaceKey=%s&start=%d&limit=%d"
           % (base, urllib.parse.quote(str(space), safe=""), 0, limit))
    ok, d = _cf_json(url, tries=2)
    if ok and isinstance(d, dict) and isinstance(d.get("results"), list):
        return True, d
    return False, d


def _cf_space_ok(url, space):
    """Проба: это правда Confluence-пространство? (для ссылок без явных
    маркеров Confluence — чтобы не перехватывать чужие сайты с ?key=)"""
    for base in _cf_bases(url):
        ok, _d = _cf_space_list(base, space, limit=1)
        if ok:
            return True
    return False


def _html_to_text(xhtml, limit=200000):
    """XHTML-тело страницы Confluence (body.storage) -> чистый текст."""
    s = str(xhtml or "")
    if not s:
        return ""
    # v3.31: БАГФИКС «двоеточие — и пусто». Код страниц Confluence лежит в
    # <ac:plain-text-body><![CDATA[ sudo dnf … ]]> — прежний «съедатель
    # тегов» <[^>]+> выкидывал всю CDATA-секцию вместе с командой (внутри
    # нет «>»). Теперь содержимое CDATA сохраняется построчно (команды,
    # конфиги, формулы), а служебные макросы (дерево дочерних страниц,
    # оглавление, крошки, «недавно обновлённые») убираются целиком — это
    # и было «дерево ссылок посреди текста».
    s = re.sub(r"<ac:structured-macro[^>]*ac:name=\"(?:children|pagetree|"
               r"toc|toc-zone|recently-updated|contentbylabel|livesearch|"
               r"breadcrumbs?|pagetreesearch|include|excerpt-include|"
               r"create-from-template|jira|anchor|status|profile|"
               r"gallery|widget|attachments|navitabs|listlabels)\"[^>]*>"
               r".*?</ac:structured-macro>", " ", s, flags=re.S | re.I)
    s = re.sub(r"<ac:structured-macro[^>]*ac:name=\"(?:children|pagetree|toc"
               r"|breadcrumbs?)\"[^>]*/>", " ", s, flags=re.I)
    s = re.sub(r"<!\[CDATA\[(.*?)\]\]>",
               lambda m: "\n" + m.group(1).replace("<", "&lt;")
               .replace(">", "&gt;").strip("\n") + "\n", s, flags=re.S)
    # ссылка на страницу: оставить её название, не адрес
    s = re.sub(r"<ri:page[^>]*ri:content-title=\"([^\"]*)\"[^>]*/?>",
               r"\1", s, flags=re.I)
    s = re.sub(r"<ri:(?:attachment|user|url)[^>]*/?>", " ", s, flags=re.I)
    s = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", s, flags=re.S | re.I)
    s = re.sub(r"</(p|div|h[1-6]|li|tr|td|th|table|blockquote|pre|section"
               r"|article|ul|ol|ac:plain-text-body|ac:rich-text-body|"
               r"ac:structured-macro|ac:layout-cell)>", "\n", s, flags=re.I)
    s = re.sub(r"<br\s*/?>", "\n", s, flags=re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    import html as _h
    s = _h.unescape(s).replace("\xa0", " ")
    s = "\n".join(" ".join(ln.split()) for ln in s.splitlines())
    s = re.sub(r"\n{2,}", "\n", s).strip()
    return s[:max(1, int(limit))]


def _page_title_of(item):
    return (str((item or {}).get("title") or "").strip() or "Без названия")


# ------------------- v3.25.0: вложения страниц Confluence -------------------
def _att_ext_ok(title):
    """У вложения текстовое расширение? (по имени файла; картинки,
    видео и архивы пропускаем — текста в них textutils не достанет)."""
    try:
        t = urllib.parse.unquote(str(title or "")).lower()
    except Exception:
        return False
    return t.endswith(FILE_EXTS)


def _att_version(item):
    """Номер версии вложения (…/download?version=7) — перечитка качает
    ТОЛЬКО обновившиеся файлы. Версии нет -> None (качаем всегда)."""
    dl = str(((item or {}).get("_links") or {}).get("download") or "")
    try:
        q = urllib.parse.parse_qs(urllib.parse.urlparse(dl).query or "")
        v = (q.get("version") or [None])[0]
        return str(v) if v is not None else None
    except Exception:
        return None


def _cf_att_page(base, page_id, start, limit=CF_ATT_LIST):
    """Одна страница списка вложений (REST /child/attachment, без тел)."""
    url = ("%s/rest/api/content/%s/child/attachment?start=%d&limit=%d"
           % (base, urllib.parse.quote(str(page_id), safe=""),
              start, limit))
    ok, d = _cf_json(url, tries=2)
    if ok and isinstance(d, dict) and isinstance(d.get("results"), list):
        return True, d
    return False, d


def _att_size(item):
    """Размер вложения в байтах, если сервер его сообщил (0 — неизвестен)."""
    try:
        return int(((item or {}).get("extensions") or {}).get("fileSize")
                   or 0)
    except Exception:
        return 0


def _att_download_url(base, item):
    """Полная ссылка на скачивание вложения (base + _links.download)."""
    dl = str(((item or {}).get("_links") or {}).get("download") or "")
    if not dl:
        return ""
    return urllib.parse.urljoin(str(base).rstrip("/") + "/", dl)


def _att_flush(url, att, bad, skip, page_i, pages_n):
    """Живой прогресс фазы вложений; False — ссылку убрали, пора стопить."""
    done = sum(1 for a in att.values() if isinstance(a, dict) and a.get("ok"))
    chars = sum(int((a or {}).get("chars") or 0) for a in att.values()
                if isinstance(a, dict) and a.get("ok"))
    return _flush_confluence(url, attach_read=done, attach_chars=chars,
                             attach_bad=bad, attach_skip=skip,
                             attach_page=page_i, attach_pages=pages_n)


def _confluence_read_attachments(url, base, pages, att):
    """v3.25.0: обойти вложения страниц пространства и скачать ТЕКСТОВЫЕ
    файлы (pdf/doc/docx/rtf/txt/…) каждый статьёй «Инструкции:
    <страница> — <файл>» через _read_file (те же гарантии, что у файлов
    с обычных страниц). att — карта attId -> состояние из side-файла:
    перечитка качает только обновившиеся (version) файлы; сканы без
    текста и слишком большие запоминаются, чтобы не перекачивать.
    Лимит новых скачиваний за круг — study_attach_max (0 = не качать).
    Возврат: {read, bad, skip, chars, cap_hit, done, pages}."""
    try:
        amax = int(store.settings().get("study_attach_max"))
    except Exception:
        amax = 300
    amax = max(0, min(20000, amax))
    out = {"read": 0, "bad": 0, "skip": 0, "chars": 0, "cap_hit": False,
           "done": sum(1 for a in att.values()
                       if isinstance(a, dict) and a.get("ok")), "pages": 0}
    if amax <= 0:
        return out
    budget = amax
    for pi, pg in enumerate(pages, 1):
        out["pages"] = pi
        ptitle = pg.get("title") or ""
        start = 0
        while start < CF_ATT_PAGE_CAP:
            ok, d = _cf_att_page(base, pg["id"], start)
            if not ok:
                break
            items = d.get("results") or []
            for it in items:
                if not isinstance(it, dict) or not it.get("id"):
                    continue
                att_id = str(it["id"])
                atitle = (str(it.get("title") or "").strip()
                          or "вложение")
                ver = _att_version(it)
                prev = att.get(att_id) if isinstance(att.get(att_id),
                                                     dict) else {}
                if not _att_ext_ok(atitle) or _att_size(it) > FILE_BYTES:
                    out["skip"] += 1        # картинка/видео/архив/большое
                    continue
                same = ver is None or prev.get("v") == ver
                if (prev.get("ok") or prev.get("empty")) and same:
                    continue                # уже прочитано и не менялось
                if budget <= 0:
                    out["cap_hit"] = True
                    break
                budget -= 1
                furl = _att_download_url(base, it)
                fe = _read_file(furl, page_title=ptitle,
                                did=prev.get("aid") or "") if furl else \
                    {"ok": False, "err": "нет ссылки на скачивание",
                     "aid": "", "chars": 0, "name": atitle}
                rec = {"pid": pg["id"], "title": atitle, "v": ver,
                       "ok": bool(fe.get("ok")),
                       "aid": fe.get("aid") or "",
                       "chars": int(fe.get("chars") or 0)}
                if fe.get("ok"):
                    out["read"] += 1
                    out["chars"] += rec["chars"]
                    att[att_id] = rec
                elif fe.get("empty") or "больше" in str(fe.get("err")):
                    rec["empty"] = True     # скан/большой — запомнили
                    att[att_id] = rec
                    out["skip"] += 1
                else:
                    out["bad"] += 1         # сеть/сервер — попробуем ещё
            if out["cap_hit"]:
                break
            if len(items) < CF_ATT_LIST \
                    or not ((d.get("_links") or {}).get("next")):
                break
            start += CF_ATT_LIST
        if not _att_flush(url, att, out["bad"], out["skip"],
                          pi, len(pages)):
            return out
        time.sleep(0.1)                     # не душим чужой Confluence
        if out["cap_hit"]:
            break
    out["done"] = sum(1 for a in att.values()
                      if isinstance(a, dict) and a.get("ok"))
    return out


def _old_entry_of(url):
    """Старая запись ссылки (для перечиток без дублей) — модульный вход;
    в read_url у той же логики своё локальное замыкание."""
    with _LOCK:
        ls = _links_raw()
        i = _find(ls, url)
        return ls[i] if i >= 0 else {}


def _norm(url):
    """Нормализовать ссылку: без схемы -> https, трим, максимум 300."""
    url = str(url or "").strip()
    if url and "://" not in url:
        url = "https://" + url          # «example.ru/instrukciya» — ок
    return url[:300]


def _cap(ls):
    """v3.176: не больше _MAX_LINKS ссылок, но ссылки на репозитории
    (GitHub и т.п.) при обрезке не теряются — раньше новые ссылки
    вытесняли старую ссылку на репозиторий, и её чтение «сбрасывалось»."""
    if len(ls) <= _MAX_LINKS:
        return ls
    keep = [x for x in ls if x.get("kind") == "github"]
    room = max(0, _MAX_LINKS - len(keep))
    other = [x for x in ls if x.get("kind") != "github"]
    tail = set(id(x) for x in other[-room:]) if room else set()
    return [x for x in ls if x.get("kind") == "github" or id(x) in tail]


def _links_raw():
    ls = store.settings().get("study_links")
    return [dict(x) for x in ls] if isinstance(ls, list) else []


def links():
    with _LOCK:
        return _links_raw()


def _find(ls, url):
    u = _norm(url).lower()
    for i, x in enumerate(ls):
        if _norm(x.get("url")).lower() == u:
            return i
    return -1


def _is_file_url(url):
    """v3.24.3: ссылка похожа на прямой файл? — по расширению пути
    (…/manual.pdf?a=1 тоже считается). Схему уже проверил read_url."""
    try:
        path = urllib.parse.unquote(urllib.parse.urlparse(url).path or "")
    except Exception:
        return False
    return path.lower().endswith(FILE_EXTS)


def _file_links_from_html(html, page_url, limit=20):
    """v3.24.3: ссылки на файлы-инструкции из HTML страницы. Берём ТОЛЬКО
    тот же домен (чтобы из одной страницы не качать весь интернет),
    дедуп, максимум limit штук. Возврат (список url, чужих пропущено)."""
    import re
    try:
        page_host = (urllib.parse.urlparse(page_url).hostname or "").lower()
    except Exception:
        page_host = ""
    out, seen, skipped = [], set(), 0
    if not page_host or not html:
        return out, skipped
    for m in re.finditer(r'<a\s[^>]*?href=["\']([^"\']+)["\']',
                         str(html)[:500000], re.I | re.S):
        href = str(m.group(1) or "").strip()
        if not href or href.startswith(("#", "javascript:", "mailto:",
                                        "data:", "tel:")):
            continue
        full = urllib.parse.urljoin(page_url, href)
        u = urllib.parse.urlparse(full)
        if u.scheme not in ("http", "https"):
            continue
        if (u.hostname or "").lower() != page_host:
            skipped += 1
            continue
        if not _is_file_url(full):
            continue
        key = full.split("#", 1)[0].lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(full.split("#", 1)[0])
        if len(out) >= max(0, int(limit)):
            break
    return out, skipped


def _read_file(furl, page_title="", did=""):
    """v3.24.3: скачать ОДИН файл и сохранить его текст статьёй.
    Возврат: {"url", "name", "chars", "ok", "err", "aid"}.
    did — старый id статьи (перечитка обновляет ту же статью)."""
    entry = {"url": furl, "name": "", "chars": 0, "ok": False,
             "err": "", "aid": did or ""}
    ok, data, fname, _ct = webfetch.fetch_file(furl, timeout=25,
                                               max_bytes=FILE_BYTES)
    if not ok:
        entry["err"] = str(data)[:200]
        return entry
    entry["name"] = fname or "file"
    try:
        text, kind = textutils.extract_text(fname, data)
    except Exception as e:
        entry["err"] = "текст не вытащить: %s" % \
                       (("%s: %s" % (type(e).__name__, e))[:160],)
        return entry
    text = (text or "").strip()
    entry["chars"] = len(text)
    if len(text) < MIN_FILE_CHARS:
        entry["err"] = ("текста в файле нет (%s, %d знаков) — возможно, "
                        "скан-картинки" % (kind, len(text)))
        entry["empty"] = True        # v3.25.0: пустой — не перекачивать
        return entry
    stem = (fname or "Файл").rsplit(".", 1)[0] or "Файл"
    title = ("Инструкции: %s — %s" % (page_title[:120], stem[:80])
             if page_title else "Инструкции: %s" % stem[:150])
    r = kb.add_article(title[:200], text,
                       source=("инструкции файл %s" % furl)[:120],
                       did=did or None)
    if not r.get("ok"):
        entry["err"] = str(r.get("error") or "не сохранить статью")[:200]
        return entry
    entry["ok"] = True
    entry["aid"] = r.get("id") or did or ""
    entry["err"] = ""
    return entry


def _read_confluence_page(url, page_id):
    """v3.24.5: синхронно прочитать ОДНУ страницу Confluence по id через
    REST (полное тело — без «2130 знаков», которые дают скриптовые страницы).
    v3.25.0: вместе со страницей качаются её ВЛОЖЕНИЯ (текстовые файлы,
    до CF_ATT_SYNC_CAP=50 за раз — чтение синхронное).
    Возврат entry или None (REST не Confluence/не сохранилось — тогда
    read_url читает ссылку как обычную страницу, как раньше)."""
    for base in _cf_bases(url):
        ok, d = _cf_json("%s/rest/api/content/%s?expand=body.storage"
                         % (base, urllib.parse.quote(str(page_id),
                                                     safe="")), tries=2)
        if not (ok and isinstance(d, dict) and d.get("id")):
            continue
        text = _html_to_text(((d.get("body") or {}).get("storage") or {})
                             .get("value"))
        if len(text) < 10:
            return None
        title = _page_title_of(d)
        old = _old_entry_of(url)
        aid = old.get("aid") or ""
        r = kb.add_article("Инструкции: %s" % title[:180], text,
                           source=("инструкции confluence %s" % url)[:120],
                           did=aid or None)
        if not r.get("ok"):
            return None
        # ---- v3.25.0: вложения этой страницы — тоже в базу ---------------
        files = []
        try:
            attach_on = bool(store.settings().get("study_attach", True))
            try:
                amax = int(store.settings().get("study_attach_max"))
            except Exception:
                amax = 300
            amax = max(0, min(20000, amax))
        except Exception:
            attach_on, amax = True, 300
        cap = min(amax, CF_ATT_SYNC_CAP)
        if attach_on and cap > 0:
            old_att = {}
            for of in (old.get("files") or []):
                if isinstance(of, dict) and of.get("att"):
                    old_att[str(of["att"])] = of      # по id вложения
            start = 0
            while start < CF_ATT_PAGE_CAP and len(files) < cap:
                okl, da = _cf_att_page(base, page_id, start)
                if not okl:
                    break
                for it in (da.get("results") or []):
                    if not isinstance(it, dict) or not it.get("id"):
                        continue
                    if len(files) >= cap:
                        break
                    atitle = (str(it.get("title") or "").strip()
                              or "вложение")
                    if not _att_ext_ok(atitle) or _att_size(it) > FILE_BYTES:
                        continue
                    furl = _att_download_url(base, it)
                    if not furl:
                        continue
                    prev = old_att.get(str(it["id"])) or {}
                    # не менялось с прошлого раза — не перекачиваем,
                    # но в списке показать (панель видит «уже прочитано»)
                    if prev.get("ok") and prev.get("v") == _att_version(it):
                        files.append({"url": prev.get("url") or furl,
                                      "name": atitle,
                                      "chars": prev.get("chars") or 0,
                                      "ok": True, "err": "",
                                      "aid": prev.get("aid") or "",
                                      "att": str(it["id"])})
                        continue
                    fe = _read_file(furl, page_title=title,
                                    did=prev.get("aid") or "")
                    fe["att"] = str(it["id"])   # перечитка найдёт по id
                    fe["v"] = _att_version(it)  # и узнает версию файла
                    files.append(fe)
                    if fe.get("ok"):
                        store.log("ИНСТРУКЦИИ",
                                  "вложение страницы %s: «%s» (%d знаков)"
                                  % (str(page_id)[:20],
                                     (fe.get("name") or atitle)[:60],
                                     fe.get("chars") or 0))
                if len((da.get("results") or [])) < CF_ATT_LIST \
                        or not ((da.get("_links") or {}).get("next")):
                    break
                start += CF_ATT_LIST
        note = "полное тело страницы через REST Confluence"
        if files:
            note += "; вложений: прочитано %d из %d" \
                    % (sum(1 for f in files if f.get("ok")), len(files))
        return {"url": url, "kind": "page", "title": title,
                "chars": int(r.get("chars") or 0), "ts": time.time(),
                "ok": True, "err": "", "aid": r.get("id") or aid,
                "files": files, "note": note}
    return None


def _flush_confluence(url, **patch):
    """Обновить запись ссылки-пространства (живой прогресс). Возврат False —
    ссылку УБРАЛИ из списка: фоновому читателю пора остановиться."""
    with _LOCK:
        ls = _links_raw()
        i = _find(ls, url)
        if i < 0:
            return False
        ls[i].update(patch)
        store.update_settings({"study_links": ls})
        return True


def _confluence_worker(url, space):
    """ФОНОВОЕ чтение ВСЕГО пространства Confluence (v3.24.5): перечислить
    страницы (малые ответы), читать тексты пачками с дроблением, каждую
    страницу — статьёй в базу знаний (did -> перечитка без дублей).
    Прогресс пишется в settings.json после каждой пачки; карта
    id страницы -> id статьи живёт в data/study_confluence/<hash>.json."""
    try:
        _confluence_read_space(url, space)
    except Exception as e:
        try:
            # v3.25.0: снимок с _READING делаем ДО flush «reading=False» —
            # иначе между «отчиталось» и выходом из _READING новое чтение
            # может получить «уже читается» (гонка)
            with _LOCK:
                _READING.discard(url.lower())
            _flush_confluence(url, ok=False, reading=False,
                              err="чтение прервано: %s: %s"
                              % (type(e).__name__, str(e)[:150]))
        except Exception:
            pass
        store.log("ИНСТРУКЦИИ", "Confluence %s: ошибка чтения: %s"
                  % (str(space)[:40], str(e)[:120]))
    finally:
        with _LOCK:
            _READING.discard(url.lower())


def _confluence_read_space(url, space):
    side = store.load_json(_side_path(url), None)
    aids = dict(side.get("aids") or {}) if isinstance(side, dict) else {}
    att = dict(side.get("att") or {}) if isinstance(side, dict) else {}
    pages_info = {}
    if isinstance(side, dict) and isinstance(side.get("pages"), list):
        for pi in side["pages"]:
            if isinstance(pi, dict) and pi.get("id"):
                pages_info[str(pi["id"])] = pi

    def _save_side():
        store.save_json(_side_path(url), {
            "url": url, "space": space, "aids": aids, "att": att,
            "pages": list(pages_info.values())})

    # 1) рабочая база REST API
    base = ""
    last_cf_err = ""
    for b in _cf_bases(url):
        ok, _d = _cf_space_list(b, space, limit=1)
        if ok:
            base = b
            break
        last_cf_err = str(_d)[:120]
    if not base:
        _flush_confluence(url, ok=False, reading=False,
                          err=("Confluence не ответил REST API (пространство "
                               "закрыто паролем? это не Confluence?): %s"
                               % last_cf_err))
        return

    # 2) имя пространства — для панели
    title = ""
    try:
        ok, d = _cf_json(base + "/rest/api/space/"
                         + urllib.parse.quote(str(space), safe=""), tries=1)
        if ok and isinstance(d, dict) and d.get("name"):
            title = str(d["name"])[:160]
    except Exception:
        pass

    # 3) перечислить ВСЕ страницы (малые ответы, тел нет — быстро и надёжно)
    sq = urllib.parse.quote(str(space), safe="")
    ids, start = [], 0
    while start < CF_ENUM_CAP:
        ok, d = _cf_json("%s/rest/api/content?type=page&spaceKey=%s"
                         "&start=%d&limit=%d" % (base, sq, start, CF_LIST),
                         tries=2)
        if not (ok and isinstance(d, dict)
                and isinstance(d.get("results"), list)):
            _flush_confluence(url, ok=False, reading=False,
                              err="список страниц не дочитан: %s"
                              % str(d)[:150])
            return
        for it in d["results"]:
            if isinstance(it, dict) and it.get("id"):
                ids.append({"id": str(it["id"]), "title": _page_title_of(it)})
        if len(d["results"]) < CF_LIST \
                or not ((d.get("_links") or {}).get("next")):
            break
        start += CF_LIST
    if not ids:
        _flush_confluence(url, ok=False, reading=False,
                          err="в пространстве нет страниц")
        return

    # 4) сколько страниц читать в этом круге
    try:
        cap = int(store.settings().get("study_pages_max"))
    except Exception:
        cap = 1000
    cap = max(1, min(CF_HARD_CAP, cap))
    pending = ids[:cap]
    read_n, bad_n, chars_total = 0, 0, 0

    def _mark(pid, title_, ok_, err_="", chars_=0, aid_=""):
        pages_info[pid] = {"id": pid, "title": title_, "ok": bool(ok_),
                           "err": str(err_)[:150], "chars": int(chars_),
                           "aid": aid_}

    # 5) читать тела пачками (сервер обрывает большие — дробим 10->5->2->1)
    step_i = 0
    while step_i < len(pending):
        step = CF_STEPS[0]
        while step >= 1:
            ok, d = _cf_json("%s/rest/api/content?type=page&spaceKey=%s"
                             "&start=%d&limit=%d&expand=body.storage"
                             % (base, sq, step_i, step), tries=3)
            items = d.get("results") if ok and isinstance(d, dict) else None
            if isinstance(items, list):
                for item in items:
                    if not isinstance(item, dict) or not item.get("id"):
                        continue
                    pid = str(item["id"])
                    ttl = _page_title_of(item)
                    text = _html_to_text(
                        ((item.get("body") or {}).get("storage") or {})
                        .get("value"))
                    if len(text) < 10:
                        bad_n += 1
                        _mark(pid, ttl, False, "в странице нет текста")
                        continue
                    r = kb.add_article("Инструкции: %s" % ttl[:180], text,
                                       source=("инструкции confluence "
                                               "%s/pages/viewpage.action?"
                                               "pageId=%s" % (base, pid))[:120],
                                       did=aids.get(pid) or None)
                    if r.get("ok"):
                        aids[pid] = str(r.get("id") or aids.get(pid) or "")
                        chars_total += int(r.get("chars") or 0)
                        read_n += 1
                        _mark(pid, ttl, True, chars_=r.get("chars") or 0,
                              aid_=aids[pid])
                    else:
                        bad_n += 1
                        _mark(pid, ttl, False, r.get("error")
                              or "не сохранить статью")
                step_i += step
                break
            if step > 1:                    # пачку оборвало — дробим
                step = {10: 5, 5: 2, 2: 1}.get(step, 1)
                continue
            # даже одна страница не читается — честная пометка, идём дальше
            it = pending[step_i]
            bad_n += 1
            _mark(it["id"], it["title"], False, str(d)[:150])
            step_i += 1
            break
        # прогресс после каждой пачки; ссылку убрали — остановиться
        if not _flush_confluence(url, pages_read=read_n, pages_bad=bad_n,
                                 pages_total=len(ids), chars=chars_total):
            return
        _save_side()
        time.sleep(0.15)                     # не душим чужой Confluence

    # 6) страницы, исчезнувшие из пространства, — статьи тоже убрать
    live_ids = {x["id"] for x in ids}
    gone = [pid for pid in list(aids.keys()) if pid not in live_ids]
    for pid in gone:
        aid = aids.pop(pid, None)
        pages_info.pop(pid, None)
        if aid:
            try:
                kb.delete_article(aid)
            except Exception:
                pass
    # v3.25.0: вложения исчезнувших страниц — их статьи тоже убрать
    gone_att = [k for k, a in att.items()
                if not isinstance(a, dict)
                or not a.get("pid") or str(a["pid"]) not in live_ids]
    for k in gone_att:
        a = att.pop(k, None)
        if isinstance(a, dict) and a.get("aid"):
            try:
                kb.delete_article(a["aid"])
            except Exception:
                pass
    if gone or gone_att:
        store.log("ИНСТРУКЦИИ", "Confluence %s: убрано из базы: страниц %d, "
                  "вложений %d" % (str(space)[:40], len(gone), len(gone_att)))
    _save_side()

    # 7) v3.25.0: ВЛОЖЕНИЯ страниц (pdf/doc/docx/rtf/txt/…) — тоже в базу
    try:
        attach_on = bool(store.settings().get("study_attach", True))
    except Exception:
        attach_on = True
    att_info = {"read": 0, "bad": 0, "skip": 0, "chars": 0, "cap_hit": False,
                "done": sum(1 for a in att.values()
                            if isinstance(a, dict) and a.get("ok"))}
    if attach_on:
        att_info = _confluence_read_attachments(url, base, pending, att)
        _save_side()

    # 8) честный итог
    note = ("прочитано %d из %d страниц, %s знаков"
            % (read_n, len(ids), chars_total))
    if bad_n:
        note += ", не прочитано %d" % bad_n
    if len(ids) > cap:
        note += ("; сработал предел study_pages_max=%d — поднимите его "
                 "в панели, чтобы прочесть остальные" % cap)
    if not attach_on:
        note += "; вложения отключены (study_attach)"
    else:
        try:
            amax = max(0, min(20000, int(store.settings()
                                         .get("study_attach_max"))))
        except Exception:
            amax = 300
        if amax <= 0:
            note += "; вложения отключены (study_attach_max=0)"
        else:
            note += "; вложений прочитано %d (%d зн.)" \
                    % (att_info["done"], att_info["chars"])
            if att_info["skip"]:
                note += ", пропущено %d (не текстовые/сканы)" \
                        % att_info["skip"]
            if att_info["bad"]:
                note += ", ошибок %d" % att_info["bad"]
            if att_info["cap_hit"]:
                note += ("; сработал предел study_attach_max=%d — остальные "
                         "при следующих перечитках" % amax)
    good = read_n > 0 or att_info["done"] > 0
    fin = {"ok": good, "reading": False,
           "err": "" if good else "ни одной страницы/вложения не прочитано",
           "note": note, "chars": chars_total, "pages_read": read_n,
           "pages_bad": bad_n, "pages_total": len(ids),
           "attach_read": att_info["done"], "attach_chars": att_info["chars"],
           "attach_bad": att_info["bad"], "attach_skip": att_info["skip"]}
    if title:
        fin["title"] = title
    # v3.25.0: снимаем флаг «читается» ДО flush — гонки с повторным чтением
    with _LOCK:
        _READING.discard(url.lower())
    _flush_confluence(url, **fin)
    store.log("ИНСТРУКЦИИ", "Confluence «%s» -> база знаний: %s"
              % (str(space)[:40], note))


def _start_confluence(url, space):
    """Запустить фонового читателя пространства и СРАЗУ вернуть запись
    (POST не ждёт минуты чтения — прогресс виден в панели)."""
    ul = url.lower()
    with _LOCK:
        if ul in _READING:
            return {"ok": False, "url": url, "reading": True,
                    "space": space,
                    "err": "это пространство уже читается в фоне"}
        _READING.add(ul)
    try:
        old = _old_entry_of(url)
        entry = {"url": url, "kind": "confluence", "space": space,
                 "title": old.get("title")
                 or ("Confluence «%s»" % space),
                 "chars": old.get("chars") or 0, "ts": time.time(),
                 "ok": bool(old.get("ok")), "err": "",
                 "aid": old.get("aid") or "",
                 "reading": True, "note": "запущено чтение пространства…",
                 "pages_read": old.get("pages_read") or 0,
                 "pages_bad": old.get("pages_bad") or 0,
                 "pages_total": old.get("pages_total") or 0,
                 "attach_read": old.get("attach_read") or 0,
                 "attach_chars": old.get("attach_chars") or 0,
                 "attach_bad": 0, "attach_skip": 0,
                 "files": old.get("files") or []}
        with _LOCK:
            ls = _links_raw()
            i = _find(ls, url)
            if i >= 0:
                ls[i] = entry
            else:
                ls.append(entry)
            store.update_settings({"study_links": _cap(ls)})
        th = threading.Thread(target=_confluence_worker, args=(url, space),
                              name="study-confluence-%s" % str(space)[:20],
                              daemon=True)
        th.start()
        store.log("ИНСТРУКЦИИ", "Confluence «%s»: чтение пространства "
                  "запущено в фоне" % str(space)[:40])
        return dict(entry)
    except Exception:
        with _LOCK:
            _READING.discard(ul)
        raise


def read_url(url):
    """Скачать страницу (или ФАЙЛ, v3.24.3) и сохранить текст в базу знаний.
    Возврат: {"ok", "url", "title", "chars", "aid", "err"[, "kind",
    "files"]}.
    РЕЗУЛЬТАТ (и успех, и неудача) остаётся в списке ссылок: владелец
    видит в панели, почему ссылка пустая; у неудачной ссылки сохраняется
    старый aid — успешная перечитка обновит ту же статью.
    Для страницы-сайта дополнительно качаются файлы-инструкции (pdf/doc/
    docx/…) с ТОГО ЖЕ домена — каждая отдельной статьёй; их список — в
    entry["files"] (в панели видно, сколько файлов прочитано)."""
    url = _norm(url)
    if not url:
        return {"ok": False, "url": url, "err": "пустая ссылка"}
    try:                                   # v3.41: GitHub — свой читатель
        import ghlearn
        if ghlearn.is_github(url):
            return ghlearn.add_link(url)
    except Exception:
        pass
    u = urllib.parse.urlparse(url)
    if u.scheme not in ("http", "https") or not u.hostname:
        return {"ok": False, "url": url,
                "err": "нужна обычная web-ссылка (http/https)"}

    def _remember(entry):
        with _LOCK:
            ls = _links_raw()
            i = _find(ls, url)
            if i >= 0:
                ls[i] = entry
            else:
                ls.append(entry)
            store.update_settings({"study_links": _cap(ls)})

    def _old_entry():
        with _LOCK:
            ls = _links_raw()
            i = _find(ls, url)
            return ls[i] if i >= 0 else {}

    if _is_openalex(url):                  # v3.61: научная база
        return _read_openalex(url, _remember, _old_entry())
    if _is_arxiv(url):                     # v3.62: arXiv (формулы TeX)
        return _read_arxiv(url, _remember, _old_entry())

    # ---- v3.24.3: ПРЯМАЯ ССЫЛКА НА ФАЙЛ — файловый режим -------------
    if _is_file_url(url):
        old = _old_entry()
        fe = _read_file(url, page_title="", did=old.get("aid") or "")
        entry = {"url": url, "kind": "file",
                 "title": fe.get("name") or url,
                 "chars": fe.get("chars") or 0,
                 "ts": time.time(),
                 "ok": bool(fe.get("ok")),
                 "err": fe.get("err") or "",
                 "aid": fe.get("aid") or (old.get("aid") or ""),
                 "files": [dict(fe)]}
        _remember(entry)
        if fe.get("ok"):
            store.log("ИНСТРУКЦИИ", "файл %s -> база знаний (%d знаков)"
                      % (url[:80], entry["chars"]))
        return dict(entry)

    # ---- v3.24.5: Confluence — страница по id или ВСЁ пространство -----
    cf = _confluence_plan(url)
    if cf.get("pageId"):
        entry = _read_confluence_page(url, cf["pageId"])
        if entry is not None:
            _remember(entry)
            if entry.get("ok"):
                store.log("ИНСТРУКЦИИ", "страница Confluence %s -> база "
                          "знаний (%d знаков)"
                          % (url[:80], entry.get("chars") or 0))
            return dict(entry)
    elif cf.get("space"):
        # certain — Confluence виден в адресе; иначе пробный REST-запрос,
        # чтобы не перехватывать чужие сайты, у которых просто ?key=…
        if cf.get("certain") or _cf_space_ok(url, cf["space"]):
            return _start_confluence(url, cf["space"])

    # ---- обычная страница --------------------------------------------
    ok, page = webfetch.fetch_page(url, timeout=15, max_bytes=600000)
    if not ok:
        old = _old_entry()
        entry = {"url": url, "kind": "page",
                 "title": old.get("title") or url,
                 "chars": old.get("chars") or 0,
                 "ts": time.time(), "ok": False,
                 "err": str(page)[:200], "aid": old.get("aid") or ""}
        _remember(entry)
        return dict(entry)
    text = str(page.get("text") or "").strip()
    why = guard_reason(page, page.get("title"))
    if why:                                # v3.61: экран защиты — не содержимое
        old = _old_entry()
        entry = {"url": url, "kind": "page", "title": old.get("title") or url,
                 "chars": old.get("chars") or 0, "ts": time.time(),
                 "ok": False, "aid": old.get("aid") or "",
                 "err": "сайт закрыт от роботов: %s — облако не может прочитать "
                        "эту страницу, возьми другой источник" % why}
        _remember(entry)
        return dict(entry)
    if len(text) < 10:
        entry = {"url": url, "title": url, "chars": 0,
                 "ts": time.time(), "ok": False,
                 "err": "на странице нет текста (только скрипты/картинки?)",
                 "aid": ""}
        _remember(entry)
        return dict(entry)
    title = (str(page.get("title") or "").strip() or url)[:200]
    # старую запись (с id статей файлов) читаем ДО _remember — иначе она
    # уже будет затёрта новой и перечитка не найдёт старые id (дубли)
    old = _old_entry()
    old_files = {}
    for of in (old.get("files") or []):
        if isinstance(of, dict) and of.get("url"):
            old_files[_norm(of["url"]).lower()] = of
    with _LOCK:
        ls = _links_raw()
        i = _find(ls, url)
        aid = (ls[i].get("aid") if i >= 0 else "") or ""
        # статья ОБНОВЛЯЕТСЯ под тем же id — дубли в базе не плодятся
        r = kb.add_article("Инструкции: %s" % title[:180], latexgen.wiki_math(text),
                           source=("инструкции %s" % url)[:120],
                           did=aid or None)
        if not r.get("ok"):
            return {"ok": False, "url": url,
                    "err": r.get("error") or "не сохранить статью"}
        entry = {"url": url, "kind": "page", "title": title,
                 "chars": int(r.get("chars") or 0),
                 "ts": time.time(), "ok": True, "err": "",
                 "aid": r.get("id") or aid}
        _remember(entry)
    store.log("ИНСТРУКЦИИ", "%s -> база знаний (%d знаков)"
              % (url[:80], entry["chars"]))

    # ---- v3.24.3: ФАЙЛЫ СО СТРАНИЦЫ (того же домена) ------------------
    st = store.settings()
    if st.get("study_files", True):
        try:
            raw_max = st.get("study_files_max")
            fmax = int(raw_max) if raw_max is not None else 20
        except Exception:
            fmax = 20
        fmax = max(0, min(50, fmax))
        if fmax > 0:
            furls, skipped = _file_links_from_html(page.get("html"), url,
                                                   limit=fmax)
            files = []
            if furls:                 # v3.30: панель видит, что идёт чтение
                entry["reading"] = True
                entry["pages_total"] = len(furls)
                entry["note"] = "качаю файлы со страницы: %d" % len(furls)
                _remember(dict(entry))
            for furl in furls:
                prev = old_files.get(furl.lower()) or {}
                fe = _read_file(furl, page_title=title,
                                did=prev.get("aid") or "")
                files.append(fe)
                entry["pages_read"] = len(files)      # v3.30: прогресс
                _remember(dict(entry, files=list(files)))
                if fe.get("ok"):
                    store.log("ИНСТРУКЦИИ", "файл со страницы: %s «%s» "
                              "(%d знаков)"
                              % (furl[:70], (fe.get("name") or "")[:60],
                                 fe.get("chars") or 0))
            if skipped:
                store.log("ИНСТРУКЦИИ",
                          "файлов чужих доменов пропущено: %d" % skipped)
            entry["files"] = files
            for k in ("reading", "note", "pages_total"):
                entry.pop(k, None)
            _remember(entry)          # дописали файлы — обновили запись
            nok = sum(1 for f in files if f.get("ok"))
            if files:
                store.log("ИНСТРУКЦИИ",
                          "файлов со страницы %s: %d (прочитано %d)"
                          % (url[:70], len(files), nok))
    # ---- v3.61: обход всего раздела сайта ----------------------------
    try:
        cr = _crawl_site(url, entry, page.get("html"), _remember)
    except Exception as e:
        cr = None
        store.log("ИНСТРУКЦИИ", "обход раздела сорвался: %s" % e)
    if cr:
        aids, cchars, cbad, cleft = cr
        for k in ("reading", "note", "pages_total"):
            entry.pop(k, None)
        entry["crawl_aids"] = aids
        entry["pages_read"] = len(aids)
        entry["pages_bad"] = cbad
        entry["crawl_chars"] = cchars
        entry["crawl_left"] = cleft
        _remember(entry)
    else:
        for k in ("reading", "note", "pages_total"):
            entry.pop(k, None)
    return dict(entry)


# --------- v3.61: ЗАЩИТА ОТ РОБОТОВ и НАУЧНАЯ БАЗА (OpenAlex, по-русски) ---
_GUARD_SIGNS = (
    ("anubis", "защита Anubis (проверка «вы не робот» по JavaScript)"),
    ("вы точно человек", "капча «Вы точно человек?»"),
    ("are you really human", "капча «Вы точно человек?»"),
    ("checking your browser", "защита «проверка браузера»"),
    ("just a moment", "защита Cloudflare «Just a moment»"),
    ("enable javascript and cookies", "сайт требует JavaScript и cookies"),
    ("requires javascript and cookies", "сайт требует JavaScript и cookies"),
    ("access denied", "доступ запрещён (Access Denied)"),
)


def guard_reason(page_or_text, title=""):
    """Страница — не содержимое, а экран защиты от роботов? -> причина | ''.
    Проверяем только КОРОТКИЕ страницы (настоящая статья длиннее)."""
    t = page_or_text.get("text") if isinstance(page_or_text, dict) \
        else page_or_text
    t = str(t or "")
    if len(t) > 1500:
        return ""
    low = (t + " " + str(title or "")).lower()
    for sig, why in _GUARD_SIGNS:
        if sig in low:
            return why
    return ""


def _is_openalex(url):
    try:
        return (urllib.parse.urlparse(url).hostname or "").lower() == \
            "api.openalex.org"
    except Exception:
        return False


def _abstract_from_inv(inv):
    """OpenAlex хранит реферат как {слово: [позиции]} — собираем текст."""
    if not isinstance(inv, dict):
        return ""
    pos = []
    for w, ps in inv.items():
        if isinstance(ps, list):
            for p in ps:
                if isinstance(p, int):
                    pos.append((p, str(w)))
    pos.sort()
    return " ".join(w for _p, w in pos)


def _openalex_page_url(url, cursor, per=100):
    u = urllib.parse.urlparse(url)
    q = urllib.parse.parse_qs(u.query, keep_blank_values=True)
    q = {k: v[0] for k, v in q.items()}
    q["per-page"] = str(per)
    q["cursor"] = cursor
    q["select"] = ("id,doi,title,publication_year,abstract_inverted_index,"
                   "primary_location,authorships")
    q.pop("page", None)
    return urllib.parse.urlunparse(u._replace(
        path=u.path or "/works",
        query=urllib.parse.urlencode(q, safe=",:*")))


def _read_openalex(url, remember, old):
    """Научные статьи ИЗ ОТКРЫТОЙ БАЗЫ OpenAlex (api.openalex.org, без ключа):
    ссылка вида …/works?filter=language:ru,has_abstract:true — читаем по
    курсору до study_sci_max работ, каждая — статья «Наука: <название>»
    (авторы, журнал, год, DOI, реферат). Честно: это рефераты, а не полные
    тексты; доступ к полному тексту — по DOI у издателя."""
    import hashlib
    st = store.settings()
    try:
        mx = int(st.get("study_sci_max") if st.get("study_sci_max")
                 is not None else 500)
    except Exception:
        mx = 500
    mx = max(10, min(5000, mx))
    entry = {"url": url, "kind": "science",
             "title": old.get("title") or "OpenAlex: научные статьи",
             "chars": 0, "ts": time.time(), "ok": False, "err": "",
             "aid": "", "reading": True, "pages_read": 0, "pages_bad": 0,
             "pages_total": mx, "note": "читаю научную базу…"}
    remember(dict(entry))
    aids, chars, cursor, err, t0 = [], 0, "*", "", time.time()
    while len(aids) < mx and cursor and not _STOP[0] \
            and time.time() - t0 < CRAWL_TIME_MAX:
        j = None
        for attempt in range(4):             # 429/сбой — подождать и ещё раз
            ok, j = webfetch.fetch_json(_openalex_page_url(url, cursor),
                                        timeout=30)
            if ok and isinstance(j, dict):
                break
            err = str(j)[:160]
            j = None
            time.sleep(3 + 4 * attempt)
        if j is None:
            break
        res = j.get("results") or []
        for w in res:
            if len(aids) >= mx:
                break
            ttl = str(w.get("title") or "").strip()
            ab = _abstract_from_inv(w.get("abstract_inverted_index"))
            if not ttl or len(ab) < 100:
                continue
            au = [str((a.get("author") or {}).get("display_name") or "")
                  for a in (w.get("authorships") or [])[:6]]
            src = ((w.get("primary_location") or {}).get("source") or {}
                   ).get("display_name") or ""
            body = "%s\nАвторы: %s\nЖурнал: %s, %s\nDOI: %s\n\nРеферат: %s" % (
                ttl, ", ".join(a for a in au if a) or "—", src or "—",
                w.get("publication_year") or "—", w.get("doi") or "—", ab)
            did = "sci_" + hashlib.md5(str(w.get("id") or ttl).encode(
                "utf-8")).hexdigest()[:14]
            r = kb.add_article("Наука: %s" % ttl[:170], body,
                               source=("научная база OpenAlex %s"
                                       % (w.get("doi") or ""))[:120], did=did)
            if r.get("ok"):
                aids.append(r.get("id") or did)
                chars += int(r.get("chars") or 0)
        cursor = (j.get("meta") or {}).get("next_cursor") or ""
        entry.update(pages_read=len(aids), chars=chars,
                     note="прочитано статей: %d из %d" % (len(aids), mx))
        remember(dict(entry))
        if not res:
            break
        time.sleep(0.5)
    entry.pop("reading", None)
    entry.pop("note", None)
    entry.pop("pages_total", None)
    entry.update(ok=bool(aids), pages_read=len(aids), chars=chars,
                 crawl_aids=aids, ts=time.time(),
                 err="" if aids else ("не получено ни одной статьи: %s"
                                      % (err or "пустой ответ")))
    remember(dict(entry))
    store.log("НАУКА", "OpenAlex: статей %d (%d знаков)%s"
              % (len(aids), chars, (" — " + err) if err and not aids else ""))
    return dict(entry)


# ------------------- v3.62: arXiv (рефераты с формулами LaTeX) -------------
def _is_arxiv(url):
    try:
        h = (urllib.parse.urlparse(url).hostname or "").lower()
        p = urllib.parse.urlparse(url).path or ""
    except Exception:
        return False
    return h == "export.arxiv.org" and p.startswith("/api/query")


def _arxiv_page_url(url, start, per=100):
    u = urllib.parse.urlparse(url)
    q = {k: v[0] for k, v in urllib.parse.parse_qs(
        u.query, keep_blank_values=True).items()}
    q["start"] = str(start)
    q["max_results"] = str(per)
    if "search_query" not in q:
        q["search_query"] = "cat:cs.LG"
    return urllib.parse.urlunparse(u._replace(
        scheme="https", query=urllib.parse.urlencode(q, safe=":*")))


def _arxiv_parse(xml):
    """Atom arXiv -> (всего, [работы]). Формулы TeX в рефератах остаются как есть."""
    import xml.etree.ElementTree as ET
    ns = {"a": "http://www.w3.org/2005/Atom",
          "o": "http://a9.com/-/spec/opensearch/1.1/",
          "x": "http://arxiv.org/schemas/atom"}
    root = ET.fromstring(xml)
    tot = root.findtext("o:totalResults", default="0", namespaces=ns)
    out = []
    for e in root.findall("a:entry", ns):
        def tx(tag):
            return " ".join((e.findtext(tag, default="", namespaces=ns)
                             or "").split())
        pc = e.find("x:primary_category", ns)
        out.append({
            "id": tx("a:id"), "title": tx("a:title"),
            "summary": " ".join((e.findtext("a:summary", default="",
                                            namespaces=ns) or "").split()),
            "published": tx("a:published")[:10],
            "authors": [" ".join((a.findtext("a:name", default="",
                                             namespaces=ns) or "").split())
                        for a in e.findall("a:author", ns)][:6],
            "cat": (pc.get("term") if pc is not None else "")})
    try:
        tot = int(tot)
    except ValueError:
        tot = 0
    return tot, out


def _read_arxiv(url, remember, old):
    """Рефераты arXiv через открытый API (export.arxiv.org/api/query, без
    ключа; пауза ≥3 с между запросами — правило arXiv). Статья «arXiv: …»:
    авторы, категория, дата, ссылка и реферат С ФОРМУЛАМИ TeX — из них
    латекс-модуль (latexgen) учит формулы. До study_sci_max за круг."""
    import hashlib
    st = store.settings()
    try:
        mx = int(st.get("study_sci_max") if st.get("study_sci_max")
                 is not None else 500)
    except Exception:
        mx = 500
    mx = max(10, min(5000, mx))
    entry = {"url": url, "kind": "science",
             "title": old.get("title") or "arXiv: рефераты",
             "chars": 0, "ts": time.time(), "ok": False, "err": "",
             "aid": "", "reading": True, "pages_read": 0, "pages_bad": 0,
             "pages_total": mx, "note": "читаю arXiv…"}
    remember(dict(entry))
    aids, chars, start, err, t0 = [], 0, 0, "", time.time()
    while len(aids) < mx and not _STOP[0] \
            and time.time() - t0 < CRAWL_TIME_MAX:
        res = None
        for attempt in range(3):
            ok, body = webfetch._fetch_bytes(_arxiv_page_url(url, start),
                                             timeout=40, max_bytes=8000000)
            if ok:
                try:
                    _tot, res = _arxiv_parse(body)
                    break
                except Exception as e:
                    err = "ответ arXiv не разобран: %s" % str(e)[:80]
            else:
                err = str(body)[:160]
            time.sleep(4 + 4 * attempt)
        if res is None or not res:
            break
        for w in res:
            if len(aids) >= mx:
                break
            if len(w["summary"]) < 80 or not w["title"]:
                continue
            body_t = "%s\nАвторы: %s\nКатегория: %s, %s\nСсылка: %s\n\nРеферат: %s" % (
                w["title"], ", ".join(a for a in w["authors"] if a) or "—",
                w["cat"] or "—", w["published"] or "—", w["id"], w["summary"])
            did = "arx_" + hashlib.md5(w["id"].encode("utf-8")).hexdigest()[:14]
            r = kb.add_article("arXiv: %s" % w["title"][:170], body_t,
                               source=("arXiv %s" % w["id"])[:120], did=did)
            if r.get("ok"):
                aids.append(r.get("id") or did)
                chars += int(r.get("chars") or 0)
        start += len(res)
        entry.update(pages_read=len(aids), chars=chars,
                     note="прочитано рефератов: %d из %d" % (len(aids), mx))
        remember(dict(entry))
        time.sleep(3.2)                     # правило arXiv: не чаще раза в 3 с
    entry.pop("reading", None)
    entry.pop("note", None)
    entry.pop("pages_total", None)
    entry.update(ok=bool(aids), pages_read=len(aids), chars=chars,
                 crawl_aids=aids, ts=time.time(),
                 err="" if aids else ("не получено ни одного реферата: %s"
                                      % (err or "пустой ответ")))
    remember(dict(entry))
    store.log("НАУКА", "arXiv: рефератов %d (%d знаков)%s"
              % (len(aids), chars, (" — " + err) if err and not aids else ""))
    return dict(entry)


def check_url(url):
    r = _check_url(url)
    if not r.get("ok") and "сертификат" in str(r.get("err")):
        try:
            ci = webfetch.cert_info(url)
            r["cert"] = ci
            if ci.get("issuer") or ci.get("note"):
                r["err"] = (str(r["err"]) + " Подписал: "
                            + (ci.get("issuer") or "?") + ". "
                            + (ci.get("note") or ""))[:420]
        except Exception:
            pass
    return r


def _check_url(url):
    """Проверка «загрузится ли ссылка» ИЗ ЭТОГО облака, ничего не сохраняя.
    -> {"url","ok","chars","links","err"}."""
    url = _norm(url)
    if _is_openalex(url):
        ok, j = webfetch.fetch_json(_openalex_page_url(url, "*", per=3),
                                    timeout=20)
        if not ok or not isinstance(j, dict):
            return {"url": url, "ok": False, "chars": 0, "links": 0,
                    "err": str(j)[:160]}
        n = sum(1 for w in (j.get("results") or [])
                if len(_abstract_from_inv(w.get("abstract_inverted_index")))
                >= 100)
        return {"url": url, "ok": n > 0, "chars": 0, "links": n,
                "err": "" if n else "в ответе нет работ с рефератом"}
    if _is_arxiv(url):
        ok, body = webfetch._fetch_bytes(_arxiv_page_url(url, 0, per=3),
                                         timeout=25, max_bytes=2000000)
        if not ok:
            return {"url": url, "ok": False, "chars": 0, "links": 0,
                    "err": str(body)[:160]}
        try:
            _t, res = _arxiv_parse(body)
        except Exception as e:
            return {"url": url, "ok": False, "chars": 0, "links": 0,
                    "err": "ответ arXiv не разобран: %s" % str(e)[:80]}
        n = sum(1 for w in res if len(w["summary"]) >= 80)
        return {"url": url, "ok": n > 0, "chars": 0, "links": n,
                "err": "" if n else "в ответе нет рефератов"}
    ok, pg = webfetch.fetch_page(url, timeout=15, max_bytes=600000)
    if not ok:
        return {"url": url, "ok": False, "chars": 0, "links": 0,
                "err": str(pg)[:160]}
    why = guard_reason(pg, pg.get("title"))
    host, prefix = _crawl_scope(url)
    nl = len(_crawl_links(pg.get("html"), pg.get("url") or url, host, prefix))
    if why:
        return {"url": url, "ok": False, "chars": len(pg.get("text") or ""),
                "links": nl, "err": "закрыт от роботов: " + why}
    if nl == 0 and len(pg.get("text") or "") < 300:
        return {"url": url, "ok": False, "chars": len(pg.get("text") or ""),
                "links": 0,
                "err": "мало текста и нет ссылок (страница строится скриптами?)"}
    return {"url": url, "ok": True, "chars": len(pg.get("text") or ""),
            "links": nl, "err": ""}


# ------------------- v3.61: ОБХОД САЙТА (раздел целиком) -------------------
CRAWL_SKIP_EXT = (".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".ico",
                  ".css", ".js", ".zip", ".gz", ".tar", ".7z", ".rar",
                  ".mp3", ".mp4", ".avi", ".mov", ".woff", ".woff2", ".ttf",
                  ".exe", ".iso", ".deb", ".rpm", ".apk", ".bin")
CRAWL_TIME_MAX = 40 * 60       # сек — предел обхода одной ссылки
CRAWL_PAUSE = 0.3              # сек между страницами (вежливость к сайту)
CRAWL_MIN_CHARS = 200          # короче — страница-меню, статьёй не сохраняем


def _host_key(h):
    h = str(h or "").lower()
    return h[4:] if h.startswith("www.") else h


def _crawl_scope(url):
    """(host, prefix): обход идёт по тому же хосту и только внутри раздела
    (каталог адреса: /base/ -> /base/; корень «/» — весь сайт)."""
    u = urllib.parse.urlparse(url)
    path = urllib.parse.unquote(u.path or "/")
    pre = path if path.endswith("/") else path.rsplit("/", 1)[0] + "/"
    return _host_key(u.hostname), pre or "/"


def _crawl_links(html, page_url, host, prefix):
    out, seen = [], set()
    for m in re.finditer(r"""<a\b[^>]*?\bhref\s*=\s*["']([^"'#]+)""",
                         str(html or ""), flags=re.I):
        href = m.group(1).strip()
        if not href or href.lower().startswith(("mailto:", "javascript:",
                                                "tel:", "data:")):
            continue
        try:
            full = urllib.parse.urljoin(page_url, href.replace("&amp;", "&"))
            u = urllib.parse.urlparse(full)
        except Exception:
            continue
        if u.scheme not in ("http", "https") or _host_key(u.hostname) != host:
            continue
        if u.query:                      # ?sort=, ?action=… — бесконечность
            continue
        path = urllib.parse.unquote(u.path or "/")
        if not path.startswith(prefix):
            continue
        low = path.lower()
        if low.endswith(CRAWL_SKIP_EXT) or low.endswith(FILE_EXTS):
            continue
        seg = path.rstrip("/").rsplit("/", 1)[-1]
        if ":" in seg:                   # Special:/Category:/Служебная: вики
            continue
        key = "https://%s%s" % (u.hostname, u.path)
        if key.lower() in seen:
            continue
        seen.add(key.lower())
        out.append(key)
    return out


def _crawl_site(url, entry, html, remember):
    """Прочитать РАЗДЕЛ сайта (страницы того же хоста под тем же каталогом),
    каждую страницу — отдельной статьёй «Инструкции: <заголовок>» (id
    стабильный: перечитка обновляет, дублей нет). Возврат: список id."""
    st = store.settings()
    if not st.get("study_crawl", True):
        return []
    try:
        mx = int(st.get("study_crawl_max") if st.get("study_crawl_max")
                 is not None else 300)
    except Exception:
        mx = 300
    mx = max(0, min(5000, mx))
    if mx <= 0:
        return []
    import hashlib
    from collections import deque
    host, prefix = _crawl_scope(url)
    start_key = "https://%s%s" % (urllib.parse.urlparse(url).hostname,
                                  urllib.parse.urlparse(url).path)
    seen = {start_key.lower(), start_key.lower().rstrip("/"),
            start_key.lower().rstrip("/") + "/"}
    q = deque()
    for l in _crawl_links(html, url, host, prefix):
        if l.lower() not in seen:
            seen.add(l.lower())
            q.append(l)
    aids, chars, bad, t0 = [], 0, 0, time.time()
    entry = dict(entry, reading=True, pages_read=0, pages_bad=0,
                 pages_total=len(q) + 1,
                 note="обхожу раздел сайта…")
    remember(dict(entry))
    while q and len(aids) < mx and not _STOP[0] \
            and time.time() - t0 < CRAWL_TIME_MAX:
        cu = q.popleft()
        ok, pg = webfetch.fetch_page(cu, timeout=15, max_bytes=600000)
        if not ok:
            bad += 1
            continue
        text = str(pg.get("text") or "").strip()
        for l in _crawl_links(pg.get("html"), pg.get("url") or cu, host,
                              prefix):
            if l.lower() not in seen and len(seen) < mx * 6:
                seen.add(l.lower())
                q.append(l)
        if len(text) >= CRAWL_MIN_CHARS:
            ttl = (str(pg.get("title") or "").strip() or cu)[:180]
            did = "stc_" + hashlib.md5(cu.lower().encode("utf-8")).hexdigest()[:14]
            r = kb.add_article("Инструкции: %s" % ttl, latexgen.wiki_math(text),
                               source=("инструкции %s" % cu)[:120], did=did)
            if r.get("ok"):
                aids.append(r.get("id") or did)
                chars += int(r.get("chars") or 0)
        else:
            bad += 0
        if len(aids) % 5 == 0 or not q:
            entry.update(pages_read=len(aids), pages_bad=bad,
                         pages_total=len(aids) + len(q) + 1,
                         note="обход раздела: прочитано %d, в очереди %d"
                         % (len(aids), len(q)))
            remember(dict(entry))
        time.sleep(CRAWL_PAUSE)
    store.log("ИНСТРУКЦИИ", "обход раздела %s: страниц %d (%d знаков), "
              "ошибок %d, %.1f мин%s"
              % (url[:70], len(aids), chars, bad, (time.time() - t0) / 60.0,
                 ", остались в очереди: %d" % len(q) if q else ""))
    return aids, chars, bad, len(q)


def add(url, wait=False):
    """Добавить ссылку и прочитать. Повторное добавление не плодит дубли:
    статья перечитывается и обновляется.
    v3.30: по умолчанию — В ФОНЕ: раньше запрос панели ждал страницу и до
    20 файлов с неё (минуты), браузер обрывал его, и панель писала «панель
    недоступна». Теперь ссылка сразу встаёт в список с пометкой «читается»,
    панель опрашивает прогресс; итог (успех или честная причина) — в списке.
    wait=True — прежнее синхронное чтение (тесты, обход)."""
    url = _norm(url)
    if not url:
        return {"ok": False, "url": url, "err": "пустая ссылка"}
    try:                                   # v3.41: GitHub — свой читатель
        import ghlearn
        if ghlearn.is_github(url):
            return ghlearn.add_link(url, wait=wait)
    except Exception:
        pass
    if wait:
        return read_url(url)
    u = urllib.parse.urlparse(url)
    if u.scheme not in ("http", "https") or not u.hostname:
        return {"ok": False, "url": url,
                "err": "нужна обычная web-ссылка (http/https)"}
    with _LOCK:
        ls = _links_raw()
        i = _find(ls, url)
        ph = dict(ls[i]) if i >= 0 else {"url": url, "kind": "page",
                                         "title": url, "chars": 0,
                                         "ok": False, "err": "", "aid": ""}
        ph.update({"reading": True, "ts": time.time(),
                   "note": "читаю в фоне…"})
        if i >= 0:
            ls[i] = ph
        else:
            ls.append(ph)
        store.update_settings({"study_links": _cap(ls)})
    threading.Thread(target=_bg_read, args=(url,), daemon=True,
                     name="study-read").start()
    return dict(ph)


def _bg_read(url):
    """Фон: прочитать ссылку; что бы ни случилось — снять «читается»."""
    try:
        r = read_url(url) or {}
    except Exception as e:
        r = {"ok": False, "err": "сбой чтения: %s" % e}
    if r.get("reading"):
        return                       # Confluence-пространство: свой воркер
    with _LOCK:
        ls = _links_raw()
        i = _find(ls, url)
        if i >= 0 and ls[i].get("reading"):
            ls[i].pop("reading", None)
            ls[i].pop("note", None)
            ls[i]["ok"] = bool(r.get("ok"))
            ls[i]["err"] = str(r.get("err") or "")[:200]
            store.update_settings({"study_links": _cap(ls)})


def remove(url):
    """Убрать ссылку из настроек И её статьи из базы знаний (страницу И
    скачанные с неё файлы, v3.24.3 — чтобы база отражала ровно то, что
    владелец разрешил читать)."""
    url = _norm(url)
    with _LOCK:
        ls = _links_raw()
        i = _find(ls, url)
        if i < 0:
            return {"ok": False, "err": "такой ссылки нет в списке"}
        entry = ls.pop(i)
        store.update_settings({"study_links": ls})
    aids = []
    if entry.get("aid"):
        aids.append(entry["aid"])
    for f in (entry.get("files") or []):
        if isinstance(f, dict) and f.get("aid"):
            aids.append(f["aid"])
    aids += [a for a in (entry.get("crawl_aids") or []) if a]   # v3.61
    # v3.24.5: у ссылки-пространства Confluence статьи живут в side-файле
    side = store.load_json(_side_path(url), None)
    if isinstance(side, dict):
        for _pid, aid in (side.get("aids") or {}).items():
            if aid:
                aids.append(aid)
        for _k, a in (side.get("att") or {}).items():   # v3.25.0 вложения
            if isinstance(a, dict) and a.get("aid"):
                aids.append(a["aid"])
    try:
        os.remove(store.p(_side_path(url)))
    except Exception:
        pass
    for aid in aids:
        try:
            kb.delete_article(aid)
        except Exception:
            pass
    try:                                   # v3.41: статьи GitHub-ссылки
        import ghlearn
        if ghlearn.is_github(url):
            aids += [None] * ghlearn.forget(url)
    except Exception:
        pass
    store.log("ИНСТРУКЦИИ", "ссылка снята: %s (статей убрано: %d)"
              % (url[:80], len(aids)))
    return {"ok": True, "url": url, "removed": len(aids)}


def refresh_all():
    """Перечитать ВСЕ ссылки сейчас (кнопка панели / фоновый круг)."""
    out = []
    for ln in links():
        try:
            out.append(read_url(ln.get("url")))
        except Exception as e:
            out.append({"ok": False, "url": ln.get("url"),
                        "err": "%s: %s" % (type(e).__name__, e)})
    return out


# --------------------------- фоновая перечитка ------------------------------
def _worker():
    while not _STOP[0]:
        try:
            hours = float(store.settings().get("study_refresh") or 0)
        except Exception:
            hours = 6.0
        if hours <= 0:                        # авто-перечитка выключена
            if _EV.wait(300.0):
                break
            continue
        due = _LAST_ROUND[0] + hours * 3600.0
        now = time.time()
        if now < due:
            if _EV.wait(min(_CHECK_PERIOD, max(1.0, due - now))):
                break
            continue
        _LAST_ROUND[0] = now
        try:
            refresh_all()
        except Exception:
            pass


def start():
    """Запустить фоновую перечитку ссылок (идемпотентно — зовётся из main)."""
    global _THREAD
    with _LOCK:
        if _THREAD and _THREAD.is_alive():
            return
        _STOP[0] = False
        _LAST_ROUND[0] = time.time()          # первый круг — через N часов
        _THREAD = threading.Thread(target=_worker, name="study", daemon=True)
        _THREAD.start()
    # v3.24.5: после перезапуска облака фоновых читателей нет — сбросить
    # застрявшие «читается…», чтобы панель не ждала вечность
    try:
        with _LOCK:
            ls = _links_raw()
            changed = False
            for x in ls:
                if x.get("reading"):
                    x["reading"] = False
                    x["note"] = ((x.get("note") or "") +
                                 "; чтение прервано перезапуском облака").strip("; ")
                    changed = True
            if changed:
                store.update_settings({"study_links": ls})
    except Exception:
        pass
