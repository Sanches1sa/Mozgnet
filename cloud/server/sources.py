# -*- coding: utf-8 -*-
"""v3.174 — УНИВЕРСАЛЬНЫЙ ПОИСК ПО ССЫЛКАМ-ИСТОЧНИКАМ.

Заявка владельца: «универсальный поиск информации для ссылок в базе данных —
ИИ ищет информацию на всех площадках, ссылки которых указаны (например
confluence.softrust.ru/collector/pages.action?key=docEMIAS) — отсюда должна
нейросеть брать информацию».

Список источников — data/sources.json (раздел «Источники для поиска» в
настройках): ссылка, имя, при необходимости логин/пароль или токен. Сюда же
сами попадают ссылки «Учёбы по ссылкам» и прежняя ссылка Confluence.
Тип площадки облако определяет само:
  * confluence — пространство Confluence (REST API, поиск CQL);
  * mediawiki  — вики на MediaWiki (Википедия-подобные: api.php, поиск);
  * file       — ссылка прямо на документ (pdf, docx, xlsx, txt…);
  * site       — любой сайт: облако обходит его страницы в пределах ссылки
                 (тот же сайт и раздел, карта sitemap.xml, до max_pages),
                 строит свой поисковый индекс и ищет по нему.
Поиск идёт по ВСЕМ включённым источникам параллельно; ответ — связный кусок
лучшей страницы со ссылкой. В базу знаний ничего не пишется: индекс сайтов
лежит отдельно (data/sources_cache/), Confluence и вики — живой поиск.
Адреса задаёт владелец, поэтому разрешены и внутренние адреса компании,
и http; сертификат https проверяется.
"""
import hashlib
import html as _html
import json
import os
import re
import ssl
import threading
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

import store

_FILE = "sources.json"
_CACHE_DIR = "sources_cache"
_LOCK = threading.RLock()
_IDX = {}                     # id источника -> {"ts", "pages": [...]}
_BUSY = set()                 # id источников, которые сейчас обходятся
_STATE = {}                   # id -> {"ok", "err", "ts", "found"}
_UA = "neuro12-cloud (sources search)"
DOC_EXT = (".pdf", ".docx", ".doc", ".rtf", ".txt", ".md", ".csv", ".xlsx",
           ".xls", ".pptx", ".odt", ".xml", ".json")
SKIP_EXT = (".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".ico", ".css",
            ".js", ".zip", ".rar", ".7z", ".exe", ".msi", ".mp3", ".mp4",
            ".avi", ".mov", ".woff", ".woff2", ".ttf", ".iso", ".apk")


# ================================ список ===================================
def _norm(url):
    url = str(url or "").strip()
    if url and "://" not in url:
        url = "https://" + url
    return url[:500]


def _sid(url):
    return hashlib.sha1(_norm(url).lower().encode("utf-8")).hexdigest()[:12]


def _load():
    d = store.load_json(_FILE, {}) or {}
    return d if isinstance(d, dict) else {}


def _save(d):
    store.save_json(_FILE, d)


def detect(url):
    """Тип площадки по ссылке (без сети). Точнее — probe()."""
    u = urllib.parse.urlparse(_norm(url))
    path, q = (u.path or "").lower(), (u.query or "").lower()
    host = (u.hostname or "").lower()
    if path.endswith(DOC_EXT):
        return "file"
    if "confluence" in host or re.search(r"pages\.action|viewpage\.action|/display/|/spaces/", path) \
            or "spacekey=" in q:
        return "confluence"
    if "/wiki/" in path or "wiki" in host or path.endswith("api.php") or "index.php" in path:
        return "mediawiki"
    return "site"


def _sync_imports(d):
    """Ссылки «Учёбы по ссылкам» и прежний Confluence — тоже источники."""
    s = store.settings()
    urls = []
    if s.get("conf_url"):
        urls.append((s["conf_url"], "настройки Confluence"))
    for x in s.get("study_links") or []:
        if isinstance(x, dict) and x.get("url"):
            urls.append((x["url"], "учёба по ссылкам"))
    changed = False
    for url, frm in urls:
        sid = _sid(url)
        if sid not in d:
            d[sid] = {"id": sid, "url": _norm(url), "name": "", "kind": detect(url),
                      "on": True, "from": frm, "added": time.time()}
            if frm == "настройки Confluence":
                for k_from, k_to in (("conf_user", "user"), ("conf_password", "password"),
                                     ("conf_token", "token"), ("conf_space", "space")):
                    if s.get(k_from):
                        d[sid][k_to] = s[k_from]
            changed = True
    return changed


def sources(public=True):
    with _LOCK:
        d = _load()
        if _sync_imports(d):
            _save(d)
    out = []
    for x in sorted(d.values(), key=lambda v: v.get("added") or 0):
        r = dict(x)
        if public:
            r["has_password"] = bool(r.pop("password", ""))
            tok = r.pop("token", "")
            r["has_token"] = bool(tok)
        st = _STATE.get(x["id"]) or {}
        idx = _IDX.get(x["id"]) or _cache_load(x["id"])
        r.update(state_ok=st.get("ok"), state_err=st.get("err", ""),
                 pages=len((idx or {}).get("pages") or []) if r.get("kind") in ("site", "file") else None,
                 indexing=x["id"] in _BUSY)
        out.append(r)
    return out


def add(url, name="", user="", password="", token="", space="", max_pages=None):
    url = _norm(url)
    if not re.match(r"^https?://[^/\s]+", url):
        return {"ok": False, "error": "нужна ссылка http(s)://…"}
    sid = _sid(url)
    with _LOCK:
        d = _load()
        x = d.get(sid) or {"id": sid, "url": url, "added": time.time(), "from": "владелец"}
        x.update({"name": str(name or x.get("name") or "").strip()[:80],
                  "kind": detect(url), "on": True})
        if user:
            x["user"] = str(user).strip()[:120]
        if password and "…" not in str(password):
            x["password"] = str(password)[:200]
        if token and "…" not in str(token):
            x["token"] = str(token).strip()[:400]
        if space:
            x["space"] = re.sub(r"[^A-Za-z0-9_~-]", "", str(space))[:60]
        if max_pages:
            try:
                x["max_pages"] = max(5, min(2000, int(max_pages)))
            except (TypeError, ValueError):
                pass
        d[sid] = x
        _save(d)
    store.log("ИСТОЧНИКИ", "добавлен источник %s (%s)" % (url, x["kind"]))
    threading.Thread(target=probe, args=(sid,), daemon=True).start()
    return {"ok": True, "id": sid}


def remove(sid):
    with _LOCK:
        d = _load()
        x = d.pop(str(sid), None)
        _save(d)
    _IDX.pop(sid, None)
    try:
        os.remove(store.p(_CACHE_DIR, sid + ".json"))
    except OSError:
        pass
    if x and x.get("from") == "учёба по ссылкам":
        store.log("ИСТОЧНИКИ", "ссылка из «Учёбы по ссылкам» убрана из поиска; "
                  "в самой учёбе она остаётся")
        with _LOCK:            # не возвращать её при следующей синхронизации
            d = _load()
            d[str(sid)] = dict(x, on=False)
            _save(d)
    return {"ok": bool(x)}


def set_on(sid, on):
    with _LOCK:
        d = _load()
        if sid in d:
            d[sid]["on"] = bool(on)
            _save(d)
            return {"ok": True}
    return {"ok": False, "error": "нет источника"}


# ================================== сеть ===================================
def _auth_headers(x):
    h = {"User-Agent": _UA}
    if x.get("token"):
        h["Authorization"] = "Bearer " + x["token"]
    elif x.get("user") and x.get("password"):
        import base64
        h["Authorization"] = "Basic " + base64.b64encode(
            ("%s:%s" % (x["user"], x["password"])).encode("utf-8")).decode()
    return h


def _fetch(url, x, timeout=15, max_bytes=3000000):
    """GET -> (final_url, content_type, bytes) | исключение с понятным текстом."""
    req = urllib.request.Request(url, headers=_auth_headers(x))
    try:
        with urllib.request.urlopen(req, timeout=timeout,
                                    context=ssl.create_default_context()) as r:
            return r.geturl(), r.headers.get("Content-Type", ""), r.read(max_bytes)
    except urllib.error.HTTPError as e:
        raise RuntimeError({401: "нужен вход (логин/пароль или токен)",
                            403: "нет доступа", 404: "страница не найдена"}.get(
                                e.code, "HTTP %s" % e.code))
    except Exception as e:
        t = str(getattr(e, "reason", e))
        low = t.lower()
        if "certificate" in low:
            t = "сертификат сайта не прошёл проверку"
        elif "timed out" in low:
            t = "сайт не ответил"
        elif "name or service" in low or "getaddrinfo" in low or "11001" in low:
            t = "имя сайта не найдено (DNS)"
        raise RuntimeError(t[:160])


def _decode(raw, ctype):
    m = re.search(r"charset=([\w-]+)", ctype or "", re.I)
    for enc in ([m.group(1)] if m else []) + ["utf-8", "cp1251"]:
        try:
            return raw.decode(enc)
        except (LookupError, UnicodeDecodeError):
            continue
    return raw.decode("utf-8", "replace")


def _html_text(h):
    t = re.search(r"<title[^>]*>(.*?)</title>", h, re.S | re.I)
    title = " ".join(_html.unescape(t.group(1)).split())[:160] if t else ""
    body = re.sub(r"<(script|style|noscript|svg|nav|footer|header)\b.*?</\1>", " ", h,
                  flags=re.S | re.I)
    body = re.sub(r"<br\s*/?>|</(p|li|h[1-6]|tr|div|section|article)>", "\n", body, flags=re.I)
    body = re.sub(r"<[^>]+>", " ", body)
    body = _html.unescape(body)
    lines = [" ".join(x.split()) for x in body.split("\n")]
    return title, "\n".join(x for x in lines if len(x) > 1)


# ============================ индекс сайтов ================================
def _cache_load(sid):
    if sid in _IDX:
        return _IDX[sid]
    d = store.load_json(os.path.join(_CACHE_DIR, sid + ".json"), None)
    if isinstance(d, dict):
        _IDX[sid] = d
    return d


def _cache_save(sid, d):
    os.makedirs(store.p(_CACHE_DIR), exist_ok=True)
    store.save_json(os.path.join(_CACHE_DIR, sid + ".json"), d)
    _IDX[sid] = d


def _crawl(x):
    """Обход сайта в пределах ссылки (тот же хост и раздел) + sitemap.xml."""
    start = _norm(x["url"])
    u0 = urllib.parse.urlparse(start)
    host = (u0.hostname or "").lower()
    prefix = (u0.path or "/").rsplit("/", 1)[0] + "/"
    limit = int(x.get("max_pages") or store.settings().get("sources_max_pages") or 150)
    queue, seen, pages, files = [start], set(), [], 0
    try:                                       # карта сайта — быстрый список
        _f, ct, raw = _fetch("%s://%s/sitemap.xml" % (u0.scheme, u0.netloc), x, timeout=10)
        for loc in re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", _decode(raw, ct))[:limit * 3]:
            lu = urllib.parse.urlparse(loc)
            if (lu.hostname or "").lower() == host and (lu.path or "/").startswith(prefix):
                queue.append(loc)
    except Exception:
        pass
    t_end = time.time() + 900
    while queue and len(pages) < limit and time.time() < t_end:
        url = urllib.parse.urldefrag(queue.pop(0))[0]
        if url in seen:
            continue
        seen.add(url)
        low = urllib.parse.urlparse(url).path.lower()
        if low.endswith(SKIP_EXT):
            continue
        is_doc = low.endswith(DOC_EXT)
        if is_doc and files >= 40:
            continue
        try:
            fin, ct, raw = _fetch(url, x, max_bytes=20000000 if is_doc else 3000000)
        except Exception:
            continue
        if is_doc or ("html" not in ct and "text/plain" not in ct):
            try:
                import textutils
                txt = textutils.extract_text(os.path.basename(low) or "file", raw) or ""
                if isinstance(txt, tuple):
                    txt = txt[0]
            except Exception:
                txt = ""
            if len(txt) > 100:
                pages.append({"url": fin, "title": urllib.parse.unquote(os.path.basename(low)),
                              "text": str(txt)[:60000]})
                files += 1
            continue
        h = _decode(raw, ct)
        title, text = _html_text(h)
        if len(text) > 80:
            pages.append({"url": fin, "title": title or fin, "text": text[:40000]})
        for href in re.findall(r'href\s*=\s*["\']([^"\'#]+)', h, re.I):
            nu = urllib.parse.urljoin(fin, _html.unescape(href))
            pu = urllib.parse.urlparse(nu)
            if pu.scheme in ("http", "https") and (pu.hostname or "").lower() == host \
                    and (pu.path or "/").startswith(prefix) and nu not in seen:
                queue.append(nu)
        time.sleep(0.15)                       # не нагружать сайт
    return pages


def reindex(sid):
    """Построить индекс сайта/файла (в фоне)."""
    x = _load().get(sid)
    if not x or sid in _BUSY:
        return
    _BUSY.add(sid)
    t0 = time.time()
    try:
        if x.get("kind") == "file":
            fin, ct, raw = _fetch(_norm(x["url"]), x, timeout=30, max_bytes=20000000)
            import textutils
            txt = textutils.extract_text(os.path.basename(urllib.parse.urlparse(fin).path) or "file", raw)
            if isinstance(txt, tuple):
                txt = txt[0]
            pages = [{"url": fin, "title": x.get("name") or os.path.basename(fin), "text": str(txt or "")[:200000]}]
        else:
            pages = _crawl(x)
        _cache_save(sid, {"ts": time.time(), "pages": pages})
        _STATE[sid] = {"ok": bool(pages), "err": "" if pages else "страниц с текстом не нашлось",
                       "ts": time.time()}
        store.log("ИСТОЧНИКИ", "%s: проиндексировано страниц %d за %.0f с" % (
            x["url"], len(pages), time.time() - t0))
    except Exception as e:
        _STATE[sid] = {"ok": False, "err": str(e)[:160], "ts": time.time()}
        store.log("ИСТОЧНИКИ", "%s: индекс не построен — %s" % (x["url"], e))
    finally:
        _BUSY.discard(sid)


# ================================ адаптеры =================================
def _conf_cfg(x):
    u = urllib.parse.urlparse(_norm(x["url"]))
    space = x.get("space") or (urllib.parse.parse_qs(u.query).get("key") or
                               urllib.parse.parse_qs(u.query).get("spaceKey") or [""])[0]
    if not space:
        m = re.search(r"/(?:display|spaces)/([A-Za-z0-9_~-]+)", u.path or "")
        space = m.group(1) if m else ""
    base = "%s://%s" % (u.scheme, u.netloc)
    if (u.path or "").lower().startswith("/wiki"):
        base += "/wiki"
    return {"on": True, "base": base, "space": space, "user": x.get("user", ""),
            "pw": x.get("password", ""), "token": x.get("token", "")}


def _mw_api(x):
    u = urllib.parse.urlparse(_norm(x["url"]))
    if (u.path or "").endswith("api.php"):
        return _norm(x["url"]).split("?")[0]
    return x.get("api") or "%s://%s/w/api.php" % (u.scheme, u.netloc)


def _mw_json(x, params):
    api = _mw_api(x)
    for cand in ([api] if x.get("api") else [api, api.replace("/w/api.php", "/api.php")]):
        try:
            _f, ct, raw = _fetch(cand + "?" + urllib.parse.urlencode(dict(params, format="json")), x)
            d = json.loads(_decode(raw, ct))
            if cand != api or not x.get("api"):
                with _LOCK:
                    dd = _load()
                    if x["id"] in dd:
                        dd[x["id"]]["api"] = cand
                        _save(dd)
                x["api"] = cand
            return d
        except Exception as e:
            last = e
    raise RuntimeError("API вики не отвечает: %s" % last)


def _search_one(x, q, n=4):
    """Поиск в одном источнике -> [{title, url, text|None, getter, score, src}]."""
    kind = x.get("kind")
    name = x.get("name") or urllib.parse.urlparse(x["url"]).hostname
    if kind == "confluence":
        import confluence
        c = _conf_cfg(x)
        if not c["space"]:
            raise RuntimeError("в ссылке нет ключа пространства (key=…)")
        res = confluence.search(q, n, c=c)
        if not res and confluence._STATE.get("err"):
            raise RuntimeError(confluence._STATE["err"])
        return [{"title": r["title"], "url": r["url"], "snippet": r.get("excerpt", ""),
                 "get": (lambda pid=r["id"], c=c: confluence.page_text(pid, c=c)),
                 "src": name} for r in res]
    if kind == "mediawiki":
        d = _mw_json(x, {"action": "query", "list": "search", "srsearch": q, "srlimit": str(n)})
        out = []
        for it in ((d.get("query") or {}).get("search") or [])[:n]:
            t = it.get("title")
            out.append({"title": t, "snippet": _html_text(it.get("snippet") or "")[1],
                        "url": _mw_api(x).replace("api.php", "index.php") + "?title=" + urllib.parse.quote(t),
                        "get": (lambda t=t: _mw_text(x, t)), "src": name})
        return out
    idx = _cache_load(x["id"])                       # site / file
    if not idx or time.time() - float(idx.get("ts") or 0) > \
            float(store.settings().get("sources_refresh_h") or 24) * 3600:
        threading.Thread(target=reindex, args=(x["id"],), daemon=True).start()
        if not idx:
            raise RuntimeError("индекс строится — первый обход сайта (несколько минут)")
    return _rank_pages(idx.get("pages") or [], q, n, name)


def _mw_text(x, title):
    d = _mw_json(x, {"action": "query", "prop": "extracts", "explaintext": "1",
                     "titles": title, "redirects": "1"})
    for pg in ((d.get("query") or {}).get("pages") or {}).values():
        if pg.get("extract"):
            return pg["extract"]
    d = _mw_json(x, {"action": "parse", "page": title, "prop": "text", "redirects": "1"})
    return _html_text(str(((d.get("parse") or {}).get("text") or {}).get("*") or ""))[1]


def _stems(t):
    try:
        import kb
        return set(kb._stems(t)) - getattr(kb, "_STOP_STEMS", set())
    except Exception:
        return set(w[:5] for w in re.findall(r"[а-яёa-z0-9]{3,}", str(t).lower()))


def _rank_pages(pages, q, n, name):
    qs = _stems(q)
    if not qs:
        return []
    scored = []
    for pg in pages:
        ts = _stems(pg.get("title") or "")
        body = pg.get("text") or ""
        bs = _stems(body[:20000])
        sc = 3 * len(qs & ts) + len(qs & bs)
        if sc > 0:
            scored.append((sc, pg))
    scored.sort(key=lambda v: -v[0])
    return [{"title": pg.get("title"), "url": pg.get("url"), "text": pg.get("text"),
             "score": sc, "src": name} for sc, pg in scored[:n]]


def probe(sid):
    """Проверить источник (и уточнить тип): Confluence/вики отвечают?"""
    x = _load().get(sid)
    if not x:
        return {"ok": False, "error": "нет источника"}
    try:
        if x["kind"] == "site":                # вдруг это MediaWiki или Confluence
            try:
                _mw_json(dict(x), {"action": "query", "meta": "siteinfo"})
                x["kind"] = "mediawiki"
            except Exception:
                pass
        if x["kind"] in ("confluence", "mediawiki"):
            _search_one(x, "инструкция", 1)
        else:
            threading.Thread(target=reindex, args=(sid,), daemon=True).start()
        with _LOCK:
            d = _load()
            if sid in d:
                d[sid]["kind"] = x["kind"]
                _save(d)
        _STATE[sid] = {"ok": True, "err": "", "ts": time.time()}
        return {"ok": True, "kind": x["kind"]}
    except Exception as e:
        _STATE[sid] = {"ok": False, "err": str(e)[:160], "ts": time.time()}
        return {"ok": False, "error": str(e)[:160]}


# ================================== поиск ===================================
def search(q, limit=6):
    """По всем включённым источникам параллельно -> (results, errors)."""
    xs = [x for x in _all_raw() if x.get("on")]
    if not xs:
        return [], []
    res, errs = [], []
    pool = ThreadPoolExecutor(max_workers=min(8, len(xs)))
    futs = {pool.submit(_search_one, dict(x), q, 4): x for x in xs}
    try:
        for f, x in futs.items():
            try:
                r = f.result(timeout=15) or []
                res += r
                _STATE[x["id"]] = {"ok": True, "err": "", "ts": time.time(), "found": len(r)}
            except Exception as e:
                errs.append("%s: %s" % (x.get("name") or x["url"], str(e)[:120]))
                _STATE[x["id"]] = {"ok": False, "err": str(e)[:160], "ts": time.time()}
    finally:
        pool.shutdown(wait=False)
    qs = _stems(q)
    for r in res:                          # общий счёт: название + сниппет/текст
        if "score" not in r:
            r["score"] = 3 * len(qs & _stems(r.get("title") or "")) + \
                len(qs & _stems(r.get("snippet") or ""))
    res.sort(key=lambda r: -r["score"])
    return res[:limit], errs


def _all_raw():
    with _LOCK:
        d = _load()
        if _sync_imports(d):
            _save(d)
        return list(d.values())


def enabled():
    return bool(store.settings().get("sources_on", True)) and any(
        x.get("on") for x in _all_raw())


def answer(question, long_mode=False):
    """Ответ из источников -> {ok, text, title, url, src} | {ok: False, why}."""
    if not enabled():
        return {"ok": False, "why": "источников нет"}
    t0 = time.time()
    res, errs = search(question, 6)
    import confluence
    qs = _stems(question)
    for r in res[:3]:
        if r["score"] <= 0:
            continue
        try:
            text = r.get("text") if r.get("text") is not None else (r["get"]() if r.get("get") else "")
        except Exception as e:
            errs.append(str(e)[:100])
            continue
        if len(text or "") < 80:
            continue
        ps = confluence.passage(question, text, 1600 if long_mode else 900, 14 if long_mode else 7)
        if not ps or not (qs & _stems(ps + " " + (r.get("title") or ""))):
            continue
        store.log("ИСТОЧНИКИ", "«%s» -> %s «%s» (%.1f с, в базу не пишется)" % (
            str(question)[:60], r["src"], str(r["title"])[:60], time.time() - t0))
        return {"ok": True, "text": ps, "title": r["title"], "url": r["url"], "src": r["src"]}
    return {"ok": False, "why": "; ".join(errs[:3]) or "в источниках ничего не нашлось"}


def page_search(q, n=5):
    """Для App Lab (ctx.docs.search) и своей LLM: [{title, url, src, text?}]."""
    res, _errs = search(q, n)
    return [{"title": r["title"], "url": r["url"], "src": r["src"],
             "snippet": (r.get("snippet") or (r.get("text") or "")[:300])} for r in res]


def page_read(q):
    """Текст лучшей страницы по запросу -> (title, text, src) | (None, "", "")."""
    res, _e = search(q, 3)
    for r in res:
        try:
            t = r.get("text") if r.get("text") is not None else (r["get"]() if r.get("get") else "")
        except Exception:
            continue
        if len(t or "") > 300:
            return r["title"], t, r["src"]
    return None, "", ""
