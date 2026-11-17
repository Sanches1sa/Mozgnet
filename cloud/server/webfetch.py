# -*- coding: utf-8 -*-
# ===========================================================================
# webfetch.py — ИНТЕРНЕТ-ШЛЮЗ ОБЛАКА: поиск Википедии (только stdlib)
# ---------------------------------------------------------------------------
# POST /api/web/fetch {"q": "..."} -> {ok, results:[{title, snippet, url}]}
# Мозг neuro_12 лазит в интернет ТОЛЬКО через этот шлюз.
# SSRF-защита: разрешены ТОЛЬКО api-домены Википедии/Викимедиа по https;
# приватные адреса и перенаправления на чужие хосты — запрещены.
#
# v3.8.1 — ПОЧЕМУ ВИКИПЕДИЯ МОЛЧАЛА:
#   1) User-Agent содержал кириллицу — urllib падал ДО запроса;
#      теперь UA только ASCII.
#   2) Часть сетей режет HTTP/1.1 к Wikimedia — запасной источник
#      (v3.155: DuckDuckGo убран — сниппеты Google/Яндекса).
#   3) Честность: fetch_diag() возвращает ПРИЧИНЫ неудач, кэшируются
#      только непустые ответы.
#
# v3.10 — ГЛАВНЫЙ БЛОКИРАТОР НАЙДЕН И СНЯТ («облако ничего не открывает»):
#   заголовки Accept* вызывали HTTP 403 у Wikimedia; теперь уходит только
#   User-Agent, поиск дублирован через api.wikimedia.org, 429/403 —
#   повторная попытка, ошибки доносятся с причиной (fetch_diag).
#
# v3.12.1 — ДИАГНОЗ «ValueError: SSRF: приватный адрес запрещён» (жалоба
#   владельца): на машинах, где провайдер/роутер/DNS-фильтр блокирует
#   Википедию на уровне DNS, резолвер отдаёт ЗАГЛУШКУ (10.x, 127.0.0.1,
#   0.0.0.0), старая проверка честно обрывала запрос, а сырой текст
#   исключения падал прямо в чат. Теперь:
#   1) SSRF-гарантии усилены, а не ослаблены: https + жёсткий белый
#      список доменов; приватные адреса по-прежнему НЕ подключаются —
#      НИКОГДА; при обходе через DoH сертификат проверяется по имени
#      домена (SNI), так что подменить адрес нельзя;
#   2) если системный DNS отдаёт только приватные/заглушечные адреса —
#      это DNS-блокировка: облако спрашивает НАСТОЯЩИЙ адрес у запасного
#      DNS-over-HTTPS (dns.google, затем cloudflare-dns.com — константы
#      в коде) и соединяется с реальным IP Википедии с SNI;
#   3) НИ ОДНО сетевое исключение больше не доходит до чата в сыром
#      виде: только честные русские причины (WikiBlockedError,
#      fetch_diag, diag_tail для панели здоровья).
# ===========================================================================
import os
import ipaddress
import json
import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request

ALLOWED_HOSTS = ("ru.wikipedia.org", "en.wikipedia.org",
                 "ru.m.wikipedia.org", "en.m.wikipedia.org",
                 "api.wikimedia.org", "ru.ruwiki.ru")    # v3.167: Рувики
# UA строго ASCII: latin-1-совместимые заголовки; контакт — по правилам
# Wikimedia User-Agent policy (v3.10)
_UA = ("neuro12-cloud/3.14 (educational platform; stdlib; "
       "contact: owner@neuro12.local)")

# Запасной DNS-over-HTTPS (JSON API; только константы — белый список и тут)
_DOH = ("https://dns.google/resolve?name=%s&type=A",
        "https://cloudflare-dns.com/dns-query?name=%s&type=A")
_DOH_TTL = 600          # кэш DoH-ответов, 10 минут

_cache = {}          # q -> (ts, results, src)
_CACHE_TTL = 600
_LAST_BLOCK = [0.0]  # когда последний раз ловили 429/403 (для честного журнала)
_LAST_ERRS = []      # последние причины неудач (для /api/health, diag_tail)


class WikiBlockedError(ValueError):
    """Википедия недоступна не из-за бага облака, а из-за сети/фильтра.
    Текст — уже честная русская причина, готовая для чата."""


def _remember_err(msg):
    _LAST_ERRS.append("%s | %s" % (time.strftime("%H:%M:%S"), str(msg)[:160]))
    del _LAST_ERRS[:-6]


def diag_tail():
    """Последние причины неудач шлюза — для честной панели здоровья."""
    return list(_LAST_ERRS)


def _honest(e):
    """Исключение -> короткая честная причина (без сырых traceback-текстов)."""
    if isinstance(e, WikiBlockedError):
        return str(e)
    s = str(e).strip() or e.__class__.__name__
    low = s.lower()
    if "getaddrinfo failed" in low or "name or service not known" in low \
            or "nodename nor servname" in low or "temporary failure" in low:
        return "DNS-резолвер не смог найти адрес домена (сеть? фильтр?)"
    if "timed out" in low or "timeout" in low:
        return "сервер не ответил вовремя (таймаут)"
    if "connection refused" in low:
        return "соединение отклонено"
    if "unreachable" in low:
        return "сеть недостижима"
    if "certificate" in low or "ssl" in low:
        vm = ""
        try:
            for x in (e, getattr(e, "reason", None), e.__cause__):
                vm = vm or str(getattr(x, "verify_message", "") or "")
        except Exception:
            vm = ""
        return ("не прошла проверка сертификата (возможна подмена)"
                + ((": " + vm[:80]) if vm else ""))
    if "urllib.error.httperror" in low or "http error" in low:
        return "сервер вернул ошибку HTTP (%s)" % s[-3:]
    # v3.24.4: кириллица в адресе раньше падала сырым текстом исключения
    if "ordinal not in range" in low or "unicodeencodeerror" in low \
            or ("ascii" in low and "codec" in low):
        return ("в адресе запроса были символы вне латиницы — облако теперь "
                "кодирует их само; если ошибка повторилась, проверьте ссылку")
    return s[:120]


honest = _honest          # публичный вход для зон мозга (v3.12.1)


# ---------------- v3.24.4: адреса с кириллицей ------------------------------
# urllib/http.client требуют ТОЛЬКО ascii в строке запроса и заголовках:
# "GET /инструкция.pdf HTTP/1.1".encode("ascii") падает с
# «'ascii' codec can't encode characters in position …». Ссылки-инструкции
# и файлы с русских сайтов часто содержат кириллицу прямо в пути
# (и в домене: сайт.рф) — кодируем её в %-вид сами, как это делают браузеры.

_URL_PATH_SAFE = "/;:@&=+$,-_.!~*'()%"   # % в safe: уже готовое %-XX не портим
_URL_QS_SAFE = "=&?/%;:@$,!~*'()-_.+"
_URL_DIRTY = re.compile(r'[\s"<>\\^`{}|\x00-\x1f\x7f]')


def _idna_host(host):
    """Кириллический домен -> punycode (сайт.рф -> xn--…). ASCII-домен
    проходит как был; неудача idna (пустые/длинные метки) — домен как был."""
    host = str(host or "").strip().rstrip(".")
    if not host or host.isascii():
        return host
    try:
        return host.encode("idna").decode("ascii").lower()
    except Exception:
        return host


def _ascii_path_qs(path_qs):
    """«путь?запрос» -> чисто ascii: кириллица/пробелы -> %-кодирование.
    Уже закодированное не портится: знак % входит в safe. Путь всегда со
    слешем в начале — как его ждёт http.client."""
    s = str(path_qs or "/")
    if s.isascii() and not _URL_DIRTY.search(s):
        return s if s.startswith("/") else "/" + s
    p, _, q = s.partition("?")
    out = urllib.parse.quote(p.strip(), safe=_URL_PATH_SAFE) or "/"
    if not out.startswith("/"):
        out = "/" + out
    if q:
        out += "?" + urllib.parse.quote(q, safe=_URL_QS_SAFE)
    return out


def safe_url(url):
    """v3.24.4: ЛЮБОЙ адрес -> пригодный для urllib/http.client:
    домен кириллицей -> punycode (сайт.рф -> xn--…), путь/запрос ->
    %-кодирование, пробелы -> %20, #фрагмент отбрасывается (серверу он
    всё равно не отправляется). Уже закодированные адресы НЕ перекодируются
    повторно. Публичная функция — ею пользуются study, maxbot и сами
    fetch_page/fetch_file перед каждым скачиванием."""
    url = str(url or "").strip()
    if url.isascii() and not _URL_DIRTY.search(url):
        return url
    try:
        u = urllib.parse.urlparse(url)
        netloc = u.netloc
        if netloc and not netloc.isascii():
            at = netloc.rfind("@")
            head = netloc[:at + 1] if at >= 0 else ""
            hostport = netloc[at + 1:] if at >= 0 else netloc
            if head and not head.isascii():
                # v3.24.4: логин/пароль кириллицей — тоже в %-виде
                head = urllib.parse.quote(head, safe=":%@")
            if not hostport.startswith("[") and u.hostname \
                    and not u.hostname.isascii():
                h = _idna_host(u.hostname)
                hp, _, port = hostport.partition(":")
                if not port or port.isdigit():  # ipv6 не трогаем (ascii)
                    hostport = h + ((":" + port) if port else "")
            netloc = head + hostport
        out = urllib.parse.urlunparse((
            u.scheme, netloc,
            urllib.parse.quote(u.path, safe=_URL_PATH_SAFE) or "/",
            urllib.parse.quote(u.params, safe=_URL_PATH_SAFE),
            urllib.parse.quote(u.query, safe=_URL_QS_SAFE), ""))
        return out
    except Exception:
        return url


# --------------------------- SSRF-проверки ----------------------------------
def _wiki_hosts_check(url):
    """Белый список: только https и ТОЛЬКО домены Викимедиа. Это главная
    SSRF-гарантия — она не отключается никакими настройками."""
    u = urllib.parse.urlparse(url)
    if u.scheme != "https" or u.hostname not in ALLOWED_HOSTS:
        _remember_err("SSRF: домен не разрешён: %r" % (u.hostname,))
        raise ValueError("SSRF: домен не разрешён: %r" % u.hostname)
    return u


def _sys_resolve(hostname):
    """Адреса домена через СИСТЕМНЫЙ DNS (как их видит этот компьютер)."""
    try:
        infos = socket.getaddrinfo(hostname, 443)
        return sorted({i[4][0] for i in infos})
    except Exception:
        return []


def _public_ips(ips):
    """Оставить ТОЛЬКО публичные адреса: приватные, локальные, заглушки
    (0.0.0.0), link-local и зарезервированные — выбрасываются."""
    out = []
    for ip in ips or []:
        try:
            a = ipaddress.ip_address(ip)
        except ValueError:
            continue
        if a.is_private or a.is_loopback or a.is_unspecified \
                or a.is_link_local or a.is_multicast or a.is_reserved:
            continue
        out.append(str(a))
    return out


def _assert_wiki(url):
    """Совместимый вход: URL разрешён? -> поднять ошибку, если домен чужой.
    (Проверка приватности IP переехала в _get_json_wiki — v3.12.1.)"""
    return _wiki_hosts_check(url)


# ----------------------- запасной DNS (DoH) ---------------------------------
_doh_cache = {}      # hostname -> (ts, [публичные ip])


def _doh_resolve(hostname, timeout=6):
    """НАСТОЯЩИЙ адрес домена через DNS-over-HTTPS — обход DNS-блокировок.
    Только A-записи (IPv4), только публичные адреса."""
    hit = _doh_cache.get(hostname)
    if hit and time.time() - hit[0] < _DOH_TTL:
        return list(hit[1])
    for tpl in _DOH:
        try:
            # v3.24.4: имя домена в запросе DoH — тоже в %-виде (ascii)
            req = urllib.request.Request(
                tpl % urllib.parse.quote(str(hostname), safe=""),
                headers={"User-Agent": _UA, "Accept": "application/dns-json"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = json.loads(r.read().decode("utf-8", "replace"))
            ips = _public_ips([a.get("data")
                               for a in (data.get("Answer") or [])
                               if a.get("type") == 1])
            if ips:
                _doh_cache[hostname] = (time.time(), ips)
                return ips
        except Exception:
            continue
    return []


# ---------------- прямое https на реальный IP (SNI + сертификат) ------------
def _https_via_ip(hostname, ip, path_qs, timeout=12):
    """HTTPS-запрос КОНКРЕТНОМУ IP с SNI и полной проверкой сертификата по
    имени домена — подмена адреса невозможна (stdlib: socket+ssl+http.client).
    Возврат (status, bytes-тело). Заголовки только User-Agent (урок v3.10)."""
    import http.client
    import ssl
    hostname = _idna_host(hostname)          # v3.24.4: кириллический домен
    path_qs = _ascii_path_qs(path_qs)        # v3.24.4: кириллица в пути
    ctx = ssl.create_default_context()
    raw = socket.create_connection((ip, 443), timeout=timeout)
    try:
        tls = ctx.wrap_socket(raw, server_hostname=hostname)
        conn = http.client.HTTPSConnection(ip, 443, timeout=timeout)
        conn.sock = tls
        try:
            conn.request("GET", path_qs, headers={
                "Host": hostname, "User-Agent": _UA, "Connection": "close"})
            resp = conn.getresponse()
            return resp.status, resp.read()
        finally:
            try:
                conn.close()
            except Exception:
                pass
    finally:
        try:
            raw.close()
        except Exception:
            pass


def _get_json(url, timeout=10, retries=1):
    """GET -> dict. Ошибки НЕ глотаются: наверх уходят с причиной.
    v3.10: заголовки ТОЛЬКО User-Agent (Accept* вызывали 403 у Wikimedia);
    при 429/403 — одна повторная попытка через 1.5 c (rate-limit)."""
    last_err = None
    for attempt in range(retries + 1):
        try:
            # только latin-1-совместимые заголовки, ничего лишнего
            req = urllib.request.Request(url, headers={"User-Agent": _UA})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            last_err = e
            if e.code in (403, 429) and attempt < retries:
                _LAST_BLOCK[0] = time.time()
                time.sleep(1.5)
                continue
            raise
        except Exception as e:
            last_err = e
            raise
    raise last_err if last_err else IOError("запрос не выполнен")


def _get_json_wiki(url, timeout=10):
    """v3.12.1: главный вход wiki-запросов с защитой от DNS-блокировок.
    1) системный DNS дал публичные адреса -> обычный запрос;
    2) дал ТОЛЬКО приватные/заглушки -> DNS-блокировка: DoH -> реальный IP
       -> прямое https с SNI (приватные адреса по-прежнему мертвы);
    3) не вышло -> WikiBlockedError с честной причиной."""
    u = _wiki_hosts_check(url)
    path_qs = (u.path or "/") + (("?" + u.query) if u.query else "")
    pub = _public_ips(_sys_resolve(u.hostname))
    last = None
    if pub:
        try:
            return _get_json(url, timeout=timeout)
        except Exception as e:
            last = e
    else:
        _remember_err("DNS-блокировка: %s -> приватные адреса %s"
                      % (u.hostname, _sys_resolve(u.hostname)[:3] or "пусто"))
        last = WikiBlockedError(
            "DNS-резолвер компьютера отдаёт для %s только приватный/заглушечный "
            "адрес — домен заблокирован фильтром DNS (провайдер, роутер или "
            "антивирус)" % u.hostname)
    # DNS-блокировка или прямой путь не удался: запасной DNS-over-HTTPS
    ips = _doh_resolve(u.hostname)
    if ips:
        for ip in ips:
            try:
                status, body = _https_via_ip(u.hostname, ip, path_qs, timeout)
                if status == 200:
                    return json.loads(body.decode("utf-8", "replace"))
                last = WikiBlockedError(
                    "реальный адрес %s (%s, через DoH) ответил HTTP %s"
                    % (u.hostname, ip, status))
            except WikiBlockedError as e:
                last = e
            except Exception as e:
                last = WikiBlockedError(
                    "прямое соединение с реальным адресом %s (%s, DoH) не "
                    "прошло: %s — похоже, блокировка стоит и на уровне "
                    "трафика (DPI)" % (u.hostname, ip, _honest(e)))
    elif isinstance(last, WikiBlockedError):
        last = WikiBlockedError(str(last) + "; запасной DNS (DoH) тоже не ответил")
    if last:
        _remember_err(_honest(last))
        raise last
    raise WikiBlockedError("шлюз не выполнил ни одной попытки")  # не бывает


# ----------------------------- поиск ----------------------------------------
def _wm_core_search(lang, q, limit):
    """Запасной шлюз поиска: api.wikimedia.org core REST (v3.10).
    Тот же выход: [{title, snippet, url, lang, src}]."""
    url = ("https://api.wikimedia.org/core/v1/wikipedia/%s/search/page?%s"
           % (lang, urllib.parse.urlencode({"q": q, "limit": str(limit)})))
    data = _get_json_wiki(url, timeout=10)
    out = []
    for item in (data.get("pages") or [])[:limit]:
        title = str(item.get("title") or item.get("key") or "")[:120]
        if not title:
            continue
        out.append({
            "title": title,
            "snippet": _strip_html(item.get("excerpt") or "")[:500],
            "url": "https://%s.wikipedia.org/wiki/%s"
                   % (lang + ".wikipedia",
                      urllib.parse.quote(str(item.get("key") or title)
                                         .replace(" ", "_"))),
            "lang": lang, "src": "Википедия",
        })
    return out


def _wiki_lang(lang, q, limit, errors):
    """Поиск по одному языковому разделу. Список может быть пуст.
    v3.12.1: вся сетевая часть внутри try — никакое исключение не
    вылетает наружу, причины складываются в errors (честно)."""
    params = {
        "action": "query", "list": "search", "format": "json",
        "srsearch": q, "srlimit": str(limit),
    }
    url = ("https://%s/w/api.php?%s"
           % (lang + ".wikipedia.org", urllib.parse.urlencode(params)))
    try:
        data = _get_json_wiki(url)
    except urllib.error.HTTPError as e:
        # v3.10: запасной шлюз api.wikimedia.org — тот же поиск, другой домен
        try:
            alt = _wm_core_search(lang, q, limit)
            if alt:
                return alt
        except Exception:
            pass
        errors.append("вики-%s: HTTP %s%s" % (
            lang, e.code, (" (сеть режет api.wikipedia.org)" if e.code == 403
                           else "")))
        return []
    except WikiBlockedError as e:
        try:
            alt = _wm_core_search(lang, q, limit)
            if alt:
                return alt
        except Exception:
            pass
        errors.append("вики-%s: %s" % (lang, str(e)[:260]))
        return []
    except Exception as e:
        try:
            alt = _wm_core_search(lang, q, limit)
            if alt:
                return alt
        except Exception:
            pass
        errors.append("вики-%s: %s" % (lang, _honest(e)))
        return []
    out = []
    for item in ((data.get("query") or {}).get("search") or [])[:limit]:
        snippet = _strip_html(item.get("snippet") or "")
        out.append({
            "title": str(item.get("title") or "")[:120],
            "snippet": snippet[:500],
            "url": "https://%s.org/wiki/%s"
                   % (lang + ".wikipedia",
                      urllib.parse.quote(
                          str(item.get("title") or "")
                          .replace(" ", "_"))),
            "lang": lang, "src": "Википедия",
        })
    return out


# v3.167: РУВИКИ (ru.ruwiki.ru) — запасная энциклопедия, если Википедия
# недоступна или в ней нет статьи. Тот же движок MediaWiki и тот же API.
RUWIKI = "ru.ruwiki.ru"


def ruwiki_search(q, limit=3):
    q = str(q or "").strip()[:120]
    if not q:
        return []
    url = "https://%s/w/api.php?%s" % (RUWIKI, urllib.parse.urlencode(
        {"action": "query", "list": "search", "srsearch": q, "format": "json",
         "srlimit": str(int(limit)), "utf8": "1"}))
    try:
        data = _get_json_wiki(url, timeout=10)
    except Exception as e:
        _remember_err("Рувики: %s" % _honest(e))
        return []
    out = []
    for it in ((data.get("query") or {}).get("search") or [])[:limit]:
        t = str(it.get("title") or "")[:120]
        out.append({"title": t, "snippet": _strip_html(it.get("snippet") or "")[:500],
                    "url": "https://%s/wiki/%s" % (RUWIKI, urllib.parse.quote(t.replace(" ", "_"))),
                    "lang": "ruwiki", "src": "Рувики"})
    return out


def ruwiki_extract(title):
    """Текст статьи Рувики: extracts (если есть), иначе разбор HTML."""
    t = str(title or "")[:120]
    for params in ({"action": "query", "prop": "extracts", "explaintext": "1",
                    "format": "json", "titles": t, "redirects": "1"},):
        try:
            data = _get_json_wiki("https://%s/w/api.php?%s" % (
                RUWIKI, urllib.parse.urlencode(params)), timeout=15)
            for pg in ((data.get("query") or {}).get("pages") or {}).values():
                ex = str(pg.get("extract") or "").strip()
                if ex:
                    return _plain(ex)[:20000]
        except Exception:
            pass
    try:
        data = _get_json_wiki("https://%s/w/api.php?%s" % (RUWIKI, urllib.parse.urlencode(
            {"action": "parse", "page": t, "prop": "text", "format": "json",
             "redirects": "1", "disabletoc": "1"})), timeout=15)
        html_ = str(((data.get("parse") or {}).get("text") or {}).get("*") or "")
        html_ = re.sub(r"<(table|style|script|sup)\b.*?</\1>", " ", html_, flags=re.S | re.I)
        paras = [_strip_html(x) for x in re.findall(r"<p\b[^>]*>(.*?)</p>", html_, re.S)]
        return "\n".join(x for x in paras if len(x) > 30)[:20000]
    except Exception as e:
        _remember_err("Рувики: %s" % _honest(e))
    return ""


def wiki_search(q, limit=3):
    """Поиск Википедии: {title, snippet, url}[] (только Википедия).
    v3.12.1: не бросает исключений — при сети/блокировке вернёт []
    (причины можно посмотреть в diag_tail())."""
    q = str(q or "").strip()[:120]
    if not q:
        return []
    now = time.time()
    hit = _cache.get(q.lower())
    if hit and now - hit[0] < _CACHE_TTL and hit[1]:
        return hit[1]
    results = []
    for lang in ("ru", "en"):
        results = _wiki_lang(lang, q, limit, [])
        if results:
            break
        if lang == "ru":                  # v3.167: Рувики — до английской
            results = ruwiki_search(q, limit)
            if results:
                break
    if results:                       # кэш только непустых (v3.8.1)
        _cache[q.lower()] = (now, results, "Википедия")
        if len(_cache) > 200:
            for k in list(_cache)[:100]:
                _cache.pop(k, None)
    return results


def wiki_image(q, size=480):
    """v3.27: РЕФЕРЕНС ДЛЯ РИСОВАНИЯ — главная картинка статьи Википедии
    по теме (API pageimages) и её байты (скачивание с теми же гарантиями:
    только https, публичные адреса). Возврат {ok, data, mime, title, page}
    | {ok: False, error}."""
    try:
        hits = wiki_search(q, limit=1)
    except Exception as e:
        return {"ok": False, "error": _honest(e)}
    if not hits:
        return {"ok": False, "error": "в Википедии не нашлось статьи"}
    title = hits[0]["title"]
    lang = hits[0].get("lang") or "ru"
    url = ("https://%s.wikipedia.org/w/api.php?%s" % (lang, urllib.parse.urlencode(
        {"action": "query", "format": "json", "prop": "pageimages",
         "piprop": "thumbnail", "pithumbsize": str(int(size)),
         "titles": title, "redirects": "1"})))
    try:
        data = _get_json_wiki(url, timeout=10)
    except Exception as e:
        return {"ok": False, "error": _honest(e)}
    src = None
    for p in ((data.get("query") or {}).get("pages") or {}).values():
        th = p.get("thumbnail") or {}
        if th.get("source"):
            src = th["source"]
            break
    if not src:
        return {"ok": False, "error": "у статьи «%s» нет картинки" % title}
    ok, blob, _fn, ct = fetch_file(src, timeout=15, max_bytes=900000)
    if not ok:
        return {"ok": False, "error": str(blob)[:160]}
    ct = str(ct or "").split(";")[0].strip().lower()
    if not ct.startswith("image/"):
        ct = "image/png" if src.lower().endswith(".png") else "image/jpeg"
    return {"ok": True, "data": blob, "mime": ct, "title": title,
            "page": hits[0].get("url") or ""}


def wiki_extract(title, lang="ru", full_if_short=True):
    """Текст статьи (для KB-статей и длинных ответов). Сначала вводная
    часть; v3.10: если вводка короткая (< 400 знаков, как у «Полярного
    сияния»), добираем ПОЛНЫЙ текст статьи (explaintext, до 20000).
    v3.12.1: через _get_json_wiki — работает и при DNS-блокировке."""
    if lang == "ruwiki":                  # v3.167
        return ruwiki_extract(title)
    params = {
        "action": "query", "prop": "extracts", "exintro": "1",
        "explaintext": "1", "format": "json",
        "titles": str(title or "")[:120],
    }
    try:
        url = ("https://%s.wikipedia.org/w/api.php?%s"
               % (lang, urllib.parse.urlencode(params)))
        data = _get_json_wiki(url, timeout=12)
        pages = (data.get("query") or {}).get("pages") or {}
        for _pid, pg in pages.items():
            ex = str(pg.get("extract") or "").strip()
            if ex:
                ex = _plain(ex)          # v3.18: без {\displaystyle}
                if len(ex) >= 400 or not full_if_short:
                    return ex[:20000]
                break                                  # вводка короткая
    except Exception:
        pass
    # v3.10: полный текст вместо короткой вводки
    try:
        params.pop("exintro")
        url = ("https://%s.wikipedia.org/w/api.php?%s"
               % (lang, urllib.parse.urlencode(params)))
        data = _get_json_wiki(url, timeout=15)
        pages = (data.get("query") or {}).get("pages") or {}
        for _pid, pg in pages.items():
            ex = str(pg.get("extract") or "").strip()
            if ex:
                return _plain(ex)[:20000]   # v3.18: без {\displaystyle}
    except Exception:
        pass
    return ""


# v3.107: тег целиком, с учётом кавычек в атрибутах. Раньше «<[^>]+>»
# обрывался на «>» ВНУТРИ атрибута — например, data-mw формулы Википедии
# ({"name":"math","body":{"extsrc":"S=… </math>"},"ref":"wt…"}) — и в
# ответ попадал мусор вида «S=, VT³=, PV⁴/³=.</math> ","ref":"wt».
_TAG = re.compile(r"<(?:[^>\"']|\"[^\"]*\"|'[^']*')*>")
_MATH_TAG = re.compile(r"<math\b((?:[^>\"']|\"[^\"]*\"|'[^']*')*)>.*?</math>",
                       re.S | re.I)
_ALT = re.compile(r"\balttext\s*=\s*(\"[^\"]*\"|'[^']*')", re.I)


def _math_to_tex(t):
    """<math alttext="{\\displaystyle …}">…</math> -> « $…$ » (формула
    остаётся формулой: в веб-чате — окошко, в ботах — читаемый текст)."""
    import html as _h

    def _one(m):
        a = _ALT.search(m.group(1) or "")
        if not a:
            return " "
        tex = _h.unescape(a.group(1)[1:-1]).strip()
        if tex.startswith("{\\displaystyle") and tex.endswith("}"):
            tex = tex[len("{\\displaystyle"):-1].strip()
        tex = tex.replace("$", "")
        return " $%s$ " % tex if tex else " "
    t = _MATH_TAG.sub(_one, t)
    # запасные картинки формул (их alt дублирует формулу) — выбросить
    t = re.sub(r"<img\b(?:[^>\"']|\"[^\"]*\"|'[^']*')*mwe-math"
               r"(?:[^>\"']|\"[^\"]*\"|'[^']*')*>", " ", t, flags=re.I)
    return t


_JSON_JUNK = re.compile(r"\s*\"?,?\s*\"(?:ref|name|attrs|body|extsrc|parts|"
                        r"template|params|i|wt)\"\s*:\s*[\"{\[][^\n]{0,200}?"
                        r"(?=\s{2,}|\n|$)")


def _strip_html(s):
    s = _math_to_tex(str(s or ""))
    s = _TAG.sub("", s)
    import html as _h
    return _plain(_h.unescape(s))


def _plain(s):
    r"""v3.18: сырой TeX и {\displaystyle ...} из статей -> читаемый текст
    (\u00abлатекс движок не работает\u00bb в сниппетах выглядел именно так).
    Лениво: latexlite без зависимостей, чистый текст не трогает."""
    try:
        import latexlite
        return latexlite.plainify(s)
    except Exception:
        return s


def fetch_diag(q, limit=3):
    """Главный вход с ЧЕСТНОЙ диагностикой:
    (ok, results|None, errors[]) — Википедия, при пусто -> сниппеты Google/Яндекса.
    v3.12.1: исключений не бросает никогда — все причины в errors[]/diag_tail()."""
    q = str(q or "").strip()
    if not q:
        return False, "пустой запрос", []
    errors = []
    res = []
    for lang in ("ru", "en"):
        try:
            res = _wiki_lang(lang, q, limit, errors)
        except Exception as e:            # страховка: сырое не уходит в чат
            msg = _honest(e)
            errors.append("вики-%s: %s" % (lang, msg))
            _remember_err(msg)
        if res:
            break
    # v3.125: и другие сайты (выдача Google/Яндекс), не только Википедия
    try:
        import store as _st
        any_ = bool(_st.settings().get("web_any", True))
    except Exception:
        any_ = True
    if any_:
        try:
            have = {_norm_url(r.get("url")) for r in res}
            more = [r for r in top_pages(q, limit=limit + 3)
                    if "wikipedia.org" not in str(r.get("url") or "")
                    and _norm_url(r.get("url")) not in have
                    and r.get("snippet")][:max(1, limit - len(res)) + 1]
            res = list(res) + [dict(r, src=r.get("src") or
                                    (urllib.parse.urlparse(r["url"]).hostname or
                                     "сайт")) for r in more]
        except Exception as e:
            errors.append("другие сайты: %s" % _honest(e))
    if res:
        now = time.time()
        _cache[q.lower()] = (now, res, "интернет")
        return True, res, errors
    res2 = engine_snips(q, limit=limit) if any_ else []
    if res2:
        return True, res2, errors
    if not errors:
        errors.append("поиск не дал результатов (сети отвечают)")
    else:
        _remember_err(errors[-1])
    return True, [], errors


def fetch(q, limit=3):
    """Совместимый вход /api/web/fetch: (ok, results|error)."""
    ok, res, _errors = fetch_diag(q, limit=limit)
    if res is None:
        return False, "шлюз недоступен"
    if not ok:
        return False, "пустой запрос"
    return True, res


# ===========================================================================
# v3.13 — РЕЖИМ «АЛИСА»: ответ из НЕСКОЛЬКИХ источников сети, каждый
# ПОДПИСАН. Заявка владельца: «чтобы нейросеть искала информацию из
# нескольких источников в интернете и эти источники были подписаны».
# Честность: в ответ попадает ТОЛЬКО текст из источников (выжимки),
# ничего не сочиняется; каждый источник назван и снабжён ссылкой;
# не нашли — честно (None), мозг пойдёт дальше по конвейеру.
# ===========================================================================
def _norm_url(u):
    return str(u or "").strip().lower().replace("https://", "") \
        .replace("http://", "").rstrip("/")


def gather(q, limit=3):
    """Собрать информацию из нескольких открытых источников (без LLM).
    Возврат: {ok, lead, sources, errors}
      lead    — лучший фрагмент: {title, text, src, url}
      sources — ПОДПИСАННЫЕ источники: [{name, url, note}], максимум 4.
    Исключений не бросает; пусто -> ok=False с честными errors."""
    q = str(q or "").strip()[:120]
    out = {"ok": False, "lead": None, "sources": [], "errors": []}
    if not q:
        out["errors"] = ["пустой запрос"]
        return out
    errs = []
    # 1) Википедия ru -> en (первая непустая)
    wiki = _wiki_lang("ru", q, limit, errs)
    if not wiki:
        wiki = _wiki_lang("en", q, limit, errs)
    # 2) v3.155: сниппеты Google/Яндекса (подписаны именем сайта)
    ddg = engine_snips(q, limit=limit)
    # 3) сводим: дедупликация — тот же заголовок ОТ ТОГО ЖЕ источника и
    #    тот же URL выбрасываются; одно имя темы от РАЗНЫХ источников —
    #    честно остаётся (у Алисы источники именно РАЗНЫЕ)
    seen, seen_urls, sources, frags = set(), set(), [], []
    for r in (wiki + ddg):
        url = _norm_url(r.get("url"))
        title = str(r.get("title") or "").strip().lower()
        if not title or url in seen_urls:
            continue
        name = str(r.get("src") or "").strip() or "интернет"
        # подпись имени: различаем языковые разделы Википедии
        if name.lower() == "википедия" and url.startswith("en.wikipedia"):
            name = "Википедия (en)"
        key = title + "|" + name.lower()
        if key in seen:
            continue
        seen.add(key)
        if url:
            seen_urls.add(url)
        snippet = " ".join(str(r.get("snippet") or "").split())
        if snippet:
            frags.append({"title": str(r.get("title"))[:120],
                          "text": snippet, "src": name,
                          "url": str(r.get("url") or "")})
        if url:
            sources.append({"name": name, "url": str(r.get("url")),
                            "note": str(r.get("title") or "")[:120]})
    # 4) главный фрагмент: у статьи Википедии добираем полный текст
    lead = None
    if frags:
        best = frags[0]
        if "википедия" in best["src"].lower():
            full = wiki_extract(best["title"])
            if len(full) > len(best["text"]):
                best = dict(best, text=" ".join(full[:900].split()))
        lead = best
    if not sources:
        out["errors"] = errs or ["источники не ответили"]
        _remember_err("gather: %s" % "; ".join(out["errors"])[:120])
        return out
    out.update(ok=True, lead=lead, sources=sources[:4])
    return out


def _alice_simple(q, extra_errors=None, long_mode=False):
    """Старый режим v3.13 (выжимки API-источников) — резерв режима
    «первых страниц». Отделён, чтобы тесты v3.13 собирали его напрямую."""
    reply, meta = _alice_legacy(q, long_mode=long_mode)
    if extra_errors:
        meta = dict(meta or {})
        meta["errors"] = list(extra_errors) + list(meta.get("errors") or [])
    return reply, meta


# v3.32: фильтр сырых фрагментов выдачи (запасной путь alice).
_SNIP_SITE = re.compile(
    r"ответы\s*mail|otvet\.mail|mail\.ru|яндекс\s*кью|yandex\.ru/q|"
    r"\bдзен\b|pikabu|livejournal|\s[|»›]\s*[^|»›.]{2,40}$", re.I)


def snippet_ok(q, text):
    """Годится ли сырой фрагмент выдачи как ответ: достаточно длинный, не
    выжимка сайта (заголовок после «|», «Ответы Mail», многоточие вместо
    конца мысли) и действительно по теме вопроса (покрыта хотя бы половина
    значимых слов вопроса). Иначе честнее промолчать — зона вернёт «не знаю»."""
    t = " ".join(str(text or "").split())
    if len(t) < 60:
        return False
    if _SNIP_SITE.search(t) or t.count("|") >= 1:
        return False
    if t.endswith(("...", "…")) or "..." in t[:-3] or "…" in t[:-1]:
        return False                      # обрезок поисковой выдачи
    try:
        import kb
        import multisource
        qs = [x for x in kb._stems(q)
              if x not in kb._STOP_STEMS and x not in kb._CMD_STEMS]
        if not qs:
            return False
        ts = set(kb._stems(t))
        got = sum(1 for x in qs if any(multisource._same_root(x, y)
                                       for y in ts))
        return got >= max(1, (len(qs) + 1) // 2)
    except Exception:
        return False


def _alice_legacy(q, long_mode=False):
    """Бывшее тело alice() из v3.13: gather() (Википедия + сниппеты Google/Яндекса —
    работает, даже если страницы недоступны). v3.27: ответ — связный
    текст, собранный из найденного (multisource.synthesize), без перечня
    источников; сами источники — в meta."""
    g = gather(q)
    if not g.get("ok"):
        return None, {"sources": [], "errors": g.get("errors") or []}
    lead = g.get("lead") or {}
    sources = g.get("sources") or []
    srcs = []
    if lead:
        body = lead.get("text") or ""
        if "википедия" in str(lead.get("src") or "").lower():
            try:
                full = wiki_extract(lead.get("title") or q)
                if len(full) > len(body):
                    body = full
            except Exception:
                pass
        srcs.append({"text": body})
    reply = None
    try:
        import multisource
        reply, _used = multisource.synthesize(
            q, srcs, max_chars=1700 if long_mode else 800)
    except Exception:
        reply = None
    if not reply and lead:
        # запас: начало главного найденного текста, по границе предложения.
        # v3.32: НЕ любой фрагмент выдачи — раньше сюда попадал обрывок вида
        # «Дядьк. после … ты будешь выглядеть… | Ответы Mail» (заголовок
        # сайта, многоточие, не по теме). Теперь фрагмент идёт в ответ,
        # только если он по теме вопроса и не похож на выжимку сайта.
        t = " ".join(str(lead.get("text") or "").split())
        import textutils as _tu
        cand = _tu.cut_at_sentence(t, 800)    # по концу предложения
        reply = cand if snippet_ok(q, cand) else None
    if not reply:
        return None, {"sources": sources, "errors": g.get("errors") or
                      ["в найденном нет ответа на вопрос"]}
    return reply, {"sources": sources, "errors": g.get("errors") or []}


# ===========================================================================
# v3.14 — «ПЕРВЫЕ 10 СТРАНИЦ» (заявка владельца: «чтобы это были не
# вопросы по ключевым словам, а просто информация, предоставленная из
# нескольких источников в интернете, например по первым 10 страницам»):
#   1) google_search()/yandex_search() — первые ССЫЛКИ выдачи (v3.155);
#   2) fetch_page() — скачивает СТРАНИЦУ ЦЕЛИКОМ: https, публичные IP
#      (DNS -> DoH), SNI + сертификат, редиректы проверяются на каждом
#      шаге, лимиты размера/времени, честные причины (без сырых исключений);
#   3) top_pages() — ссылки выдачи + Википедия, дедупликация.
# Сборку ответа делает multisource.py (релевантность, подписи, список).
# Приватные адреса НЕ подключаются НИКОГДА — гарантии прежней строгости.
# ===========================================================================


def _hdr(headers, name):
    """Значение заголовка ответа без учёта регистра."""
    low = str(name).lower()
    for k, v in (headers or {}).items():
        if str(k).lower() == low:
            return v
    return None


def _https_call(hostname, ip, path_qs, timeout=12, max_bytes=400000,
                method="GET", form=None, extra_headers=None, raw_body=None,
                content_type=None, extra_ca=None):
    """HTTPS-запрос КОНКРЕТНОМУ IP с полной проверкой сертификата по имени
    домена (SNI). Возврат (status, headers, body|bytes-limited).
    Для POST-форм — form=dict (application/x-www-form-urlencoded).
    v3.20: extra_headers (Authorization для Bot API МАКС), raw_body+
    content_type (готовое тело, например JSON) и extra_ca (PEM-якорь
    дополнительного CA, например Минцифры для *.max.ru)."""
    import http.client
    import ssl
    hostname = _idna_host(hostname)          # v3.24.4: кириллический домен
    path_qs = _ascii_path_qs(path_qs)        # v3.24.4: кириллица в пути
    ctx = ssl.create_default_context()
    if extra_ca:
        try:
            ctx.load_verify_locations(cadata=extra_ca)
        except Exception:
            pass
    raw = socket.create_connection((ip, 443), timeout=timeout)
    try:
        tls = ctx.wrap_socket(raw, server_hostname=hostname)
        conn = http.client.HTTPSConnection(ip, 443, timeout=timeout)
        conn.sock = tls
        try:
            headers = {"Host": hostname, "User-Agent": _UA,
                       "Connection": "close"}
            body = None
            if raw_body is not None:
                body = raw_body
                headers["Content-Type"] = content_type or \
                    "application/octet-stream"
            elif form is not None:
                body = urllib.parse.urlencode(form)
                headers["Content-Type"] = "application/x-www-form-urlencoded"
            if extra_headers:
                for k, v in extra_headers.items():
                    headers[k] = v
            conn.request(method, path_qs, body=body, headers=headers)
            resp = conn.getresponse()
            try:
                data = resp.read(max_bytes)
            except Exception as rexc:
                # v3.24.5: сервер может оборвать chunked-ответ на середине
                # (Confluence softrust так делает на больших пачках):
                # http.client.IncompleteRead несёт .partial — честнее
                # дочитать то, что успело, чем валить запрос целиком;
                # частичного нет — отдаём ошибку как была
                part = getattr(rexc, "partial", None)
                if part:
                    data = bytes(part)
                else:
                    raise
            return resp.status, dict(resp.getheaders()), data
        finally:
            try:
                conn.close()
            except Exception:
                pass
    finally:
        try:
            raw.close()
        except Exception:
            pass


def _public_host_check(hostname):
    """SSRF-шлюз универсального загрузчика: IP-литерал в адресе — только
    публичный; доменное имя пройдёт проверку адресов при резолве.
    Приватные адреса запрещены всегда, настройками не отключается."""
    hostname = str(hostname or "").strip().lower().rstrip(".")
    if not hostname:
        raise ValueError("SSRF: пустой домен")
    try:
        a = ipaddress.ip_address(hostname)
    except ValueError:
        return hostname                      # обычный домен — проверим IP при резолве
    if not _public_ips([str(a)]):
        raise ValueError("SSRF: адрес %s приватный — подключение "
                         "запрещено" % hostname)
    return hostname


def _resolve_public(hostname):
    """Публичные адреса домена: системный DNS, а при DNS-блокировке —
    запасной DoH (урок v3.12.1). Только публичные — всегда."""
    pub = _public_ips(_sys_resolve(hostname))
    if pub:
        return pub
    return _doh_resolve(hostname)


def fetch_page(url, timeout=10, max_bytes=400000, hops=3):
    """Скачать страницу целиком (v3.14). Возврат (ok, page|причина):
    page = {url, domain, title, text}. Https-only (http повышается до
    https), каждый редирект проходит ту же SSRF-проверку, приватные
    адреса запрещены — НИКОГДА не отключается, размер и время ограничены."""
    # v3.24.4: кириллица в адресе (путь/домен) -> ascii, иначе http.client
    # падает «'ascii' codec can't encode characters in position …»
    u = urllib.parse.urlparse(safe_url(str(url or "").strip()))
    if u.scheme == "http":
        u = u._replace(scheme="https")       # честный апгрейд до https
    if u.scheme != "https" or not u.hostname:
        return False, "SSRF: только https-страницы"
    cur = u
    last = ""
    for _hop in range(hops + 1):
        try:
            host = _public_host_check(cur.hostname)
        except ValueError as e:
            return False, str(e)
        ips = _resolve_public(host)
        if not ips:
            return False, ("домен %s не резолвится в публичный адрес "
                           "(DNS-фильтр?)" % host)
        path_qs = (cur.path or "/") + (("?" + cur.query) if cur.query else "")
        status, headers, body = None, None, None
        err = None
        for ip in ips[:2]:
            try:
                status, headers, body = _https_call(host, ip, path_qs,
                                                    timeout=timeout,
                                                    max_bytes=max_bytes)
                break
            except Exception as e:
                err, last = e, _honest(e)
        if status is None:
            return False, last or "соединение не удалось"
        loc = _hdr(headers, "location")
        if status in (301, 302, 303, 307, 308) and loc:
            try:
                nxt = urllib.parse.urljoin(cur.geturl(), str(loc))
            except Exception:
                return False, "редирект не разобран"
            # v3.24.4: Location тоже может прийти с кириллицей
            nu = urllib.parse.urlparse(safe_url(nxt))
            if nu.scheme == "http":
                nu = nu._replace(scheme="https")
            if nu.scheme != "https":
                return False, "редирект на %s:// пропущен (только https)" \
                    % nu.scheme
            cur = nu
            continue
        if status != 200:
            return False, "сервер ответил HTTP %s" % status
        txt = _decode_body(body, _hdr(headers, "content-type"))
        text, title = _page_parts(txt, base=str(cur.geturl()))
        if not text:
            return False, "страница без текста"
        # v3.18: страницы Википедии с TeX-мусором; v3.124: метки
        # иллюстраций (адреса с «_») plainify не трогает
        _ill2 = []

        def _hide2(m):
            _ill2.append(m.group(0))
            return "⟦ИЛЛ%d⟧" % (len(_ill2) - 1)
        text = re.sub(r"\[ИЛЛЮСТРАЦИЯ src=[^\]]*\][^\n]*?\[/ИЛЛЮСТРАЦИЯ\]",
                      _hide2, text)
        text = _plain(text)
        if _ill2:
            text = re.sub(r"⟦ИЛЛ(\d+)⟧", lambda m: _ill2[int(m.group(1))], text)
        # v3.24.3: сырой HTML рядом с текстом — study.py ищет по нему
        # ссылки на файлы-инструкции (pdf/doc/docx…). Старым читателям
        # (maxbot, multisource) лишний ключ не мешает.
        return True, {"url": str(cur.geturl())[:300], "domain": host,
                      "title": title, "text": text,
                      "html": txt[:500000]}
    return False, "слишком много редиректов"


def fetch_json(url, timeout=15, max_bytes=30000000, hops=3):
    """v3.24.5: скачать JSON (REST API Confluence-пространств инструкций).
    Та же строгость, что у fetch_page: https, публичные адреса, каждый
    редирект проверяется заново, размер/время ограничены. Оборванный
    (chunked) ответ дочитывается по частям, но битый JSON — честный отказ.
    Возврат (True, объект) / (False, причина)."""
    ok, page = _fetch_bytes(url, timeout=timeout, max_bytes=max_bytes,
                            hops=hops)
    if not ok:
        return False, page
    txt = page
    try:
        import json as _json
        return True, _json.loads(txt)
    except Exception:
        head = str(txt)[:80].replace("\n", " ")
        return False, ("сервер ответил не JSON (нужен доступ без пароля? "
                       "это точно Confluence?) — начало ответа: %s" % head)


def _fetch_bytes(url, timeout=10, max_bytes=400000, hops=3):
    """v3.24.5: общий движок fetch_page/fetch_json — скачать и ДЕКОДИРОВАТЬ
    тело по https со всеми SSRF-проверками. Возврат (True, str-тело) /
    (False, причина). fetch_file остался на своём цикле (там bytes + имя)."""
    u = urllib.parse.urlparse(safe_url(str(url or "").strip()))
    if u.scheme == "http":
        u = u._replace(scheme="https")       # честный апгрейд до https
    if u.scheme != "https" or not u.hostname:
        return False, "SSRF: только https-страницы"
    cur = u
    last = ""
    for _hop in range(hops + 1):
        try:
            host = _public_host_check(cur.hostname)
        except ValueError as e:
            return False, str(e)
        ips = _resolve_public(host)
        if not ips:
            return False, ("домен %s не резолвится в публичный адрес "
                           "(DNS-фильтр?)" % host)
        path_qs = (cur.path or "/") + (("?" + cur.query) if cur.query else "")
        status, headers, body = None, None, None
        err = None
        for ip in ips[:2]:
            try:
                status, headers, body = _https_call(host, ip, path_qs,
                                                    timeout=timeout,
                                                    max_bytes=max_bytes)
                break
            except Exception as e:
                err, last = e, _honest(e)
        if status is None:
            return False, last or "соединение не удалось"
        loc = _hdr(headers, "location")
        if status in (301, 302, 303, 307, 308) and loc:
            try:
                nxt = urllib.parse.urljoin(cur.geturl(), str(loc))
            except Exception:
                return False, "редирект не разобран"
            nu = urllib.parse.urlparse(safe_url(nxt))
            if nu.scheme == "http":
                nu = nu._replace(scheme="https")
            if nu.scheme != "https":
                return False, "редирект на %s:// пропущен (только https)" \
                    % nu.scheme
            cur = nu
            continue
        if status != 200:
            return False, "сервер ответил HTTP %s" % status
        return True, _decode_body(body, _hdr(headers, "content-type"))
    return False, "слишком много редиректов"


def fetch_file(url, timeout=25, max_bytes=20971520, hops=3):
    """Скачать ФАЙЛ по ссылке (v3.24.3 — «инструкции с сайтов»: страницы
    содержат ссылки на pdf/doc/docx… — их тоже качаем в базу знаний).
    Возврат (ok, data|причина, имя_файла, content_type).
    Те же гарантии, что у fetch_page: только https, публичные адреса
    (DNS -> DoH), SNI + сертификат, каждый редирект проверяется заново,
    приватные адреса НЕ подключаются НИКОГДА, размер и время ограничены."""
    # v3.24.4: кириллица в адресе (путь/домен) -> ascii, иначе http.client
    # падает «'ascii' codec can't encode characters in position …»
    u = urllib.parse.urlparse(safe_url(str(url or "").strip()))
    if u.scheme == "http":
        u = u._replace(scheme="https")
    if u.scheme != "https" or not u.hostname:
        return False, "SSRF: только https-файлы", "", ""
    cur = u
    last = ""
    for _hop in range(hops + 1):
        try:
            host = _public_host_check(cur.hostname)
        except ValueError as e:
            return False, str(e), "", ""
        ips = _resolve_public(host)
        if not ips:
            return False, ("домен %s не резолвится в публичный адрес "
                           "(DNS-фильтр?)" % host), "", ""
        path_qs = (cur.path or "/") + (("?" + cur.query) if cur.query else "")
        status, headers, body = None, None, None
        err = None
        for ip in ips[:2]:
            try:
                # max_bytes+1 — чтобы честно понять «файл больше лимита»
                status, headers, body = _https_call(host, ip, path_qs,
                                                    timeout=timeout,
                                                    max_bytes=max_bytes + 1)
                break
            except Exception as e:
                err, last = e, _honest(e)
        if status is None:
            return False, (last or "соединение не удалось"), "", ""
        loc = _hdr(headers, "location")
        if status in (301, 302, 303, 307, 308) and loc:
            try:
                nxt = urllib.parse.urljoin(cur.geturl(), str(loc))
            except Exception:
                return False, "редирект не разобран", "", ""
            # v3.24.4: Location тоже может прийти с кириллицей
            nu = urllib.parse.urlparse(safe_url(nxt))
            if nu.scheme == "http":
                nu = nu._replace(scheme="https")
            if nu.scheme != "https":
                return False, ("редирект на %s:// пропущен (только https)"
                               % nu.scheme), "", ""
            cur = nu
            continue
        if status != 200:
            return False, "сервер ответил HTTP %s" % status, "", \
                   str(_hdr(headers, "content-type") or "")
        data = body if isinstance(body, (bytes, bytearray)) else bytes(body or "")
        if len(data) > max_bytes:
            return False, ("файл больше %d МБ — не качаю"
                           % (max_bytes // (1024 * 1024))), "", \
                   str(_hdr(headers, "content-type") or "")
        ctype = str(_hdr(headers, "content-type") or "")
        return True, bytes(data), _fname(cur.geturl(), headers), ctype
    return False, "слишком много редиректов", "", ""


def _fname(url, headers=None):
    """Имя файла: Content-Disposition -> путь URL. Честное «file.bin»,
    если ничего нет (как в Макс-боте, только без его токенов)."""
    try:
        for k, v in (headers or {}).items():
            if str(k).lower() == "content-disposition":
                m = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)"?',
                              str(v), re.IGNORECASE)
                if m:
                    return urllib.parse.unquote(m.group(1)).strip()[:120]
    except Exception:
        pass
    name = urllib.parse.urlparse(url).path.rsplit("/", 1)[-1]
    return (urllib.parse.unquote(name).strip()[:120]) or "file.bin"


def _decode_body(body, ctype=""):
    """bytes страницы -> str (v3.24.3: выделено из _page_text, чтобы и
    текст страницы, и её сырой HTML брались одним декодированием).
    Кодировка: заголовок -> meta -> utf-8 -> cp1251 (многие ру-сайты)."""
    raw = body or b""
    enc = None
    m = re.search(r"charset=([\w\-]+)", str(ctype or ""), re.I)
    if m:
        enc = m.group(1).lower()
    if not enc:
        m = re.search(rb'charset=["\']?([\w\-]+)', raw[:3000], re.I)
        if m:
            enc = m.group(1).decode("ascii", "replace").lower()
    txt = None
    for e in ([enc] if enc else []) + ["utf-8", "cp1251", "latin-1"]:
        try:
            txt = raw.decode(e)
            break
        except Exception:
            continue
    if txt is None:
        txt = raw.decode("utf-8", "replace")
    return txt


_IMG_TAG = re.compile(r"<img\b((?:[^>\"']|\"[^\"]*\"|'[^']*')*)>", re.I)
_ATTR = re.compile(r"([a-zA-Z_:-]+)\s*=\s*(\"[^\"]*\"|'[^']*'|[^\s>]+)")
_ICON_RE = re.compile(r"logo|icon|sprite|avatar|banner|counter|pixel|"
                      r"advert|\bads?\b|button|emoji|smile|spacer|blank|"
                      r"\.ico\b|metrika|liveinternet|rating|share|social", re.I)
_TEXLIKE = re.compile(r"\\[a-zA-Z]+|[_^]\{|\^\d|_\d|\{\\displaystyle")


def _attrs(s):
    import html as _h
    out = {}
    for k, v in _ATTR.findall(s or ""):
        v = v[1:-1] if v[:1] in "\"'" else v
        out[k.lower()] = _h.unescape(v)
    return out


def _tex_clean(t):
    t = str(t or "").strip()
    if t.startswith("{\\displaystyle") and t.endswith("}"):
        t = t[len("{\\displaystyle"):-1].strip()
    return t.replace("$", "").strip()


def _formulas_and_images(t, base=""):
    """v3.124: формулы MathJax/KaTeX/картинкой -> $…$; содержательные
    картинки -> метки [ИЛЛЮСТРАЦИЯ …] (kbimages.py)."""
    import html as _h
    t = re.sub(r"<script[^>]*type\s*=\s*[\"']math/tex[^\"']*[\"'][^>]*>"
               r"(.*?)</script>",
               lambda m: " $%s$ " % _tex_clean(_h.unescape(m.group(1))),
               t, flags=re.S | re.I)
    t = re.sub(r"<math\b[^>]*>(?:(?!</math>).)*?<annotation[^>]*"
               r"application/x-tex[^>]*>(.*?)</annotation>.*?</math>",
               lambda m: " $%s$ " % _tex_clean(_h.unescape(m.group(1))),
               t, flags=re.S | re.I)
    # KaTeX дублирует формулу «видимым» HTML — его не берём
    t = re.sub(r"<span[^>]*class=[\"'][^\"']*katex-html[^\"']*[\"'][^>]*>"
               r"(?:(?!</span>\s*</span>\s*</span>).)*?</span>\s*</span>\s*</span>",
               " ", t, flags=re.S | re.I)

    def _img(m):
        a = _attrs(m.group(1))
        cls = a.get("class", "")
        if "mwe-math" in cls:
            return m.group(0)                 # обработает _math_to_tex
        src = a.get("src") or a.get("data-src") or a.get("data-original") \
            or ""
        alt = (a.get("alt") or a.get("title") or "").strip()
        if alt and (_TEXLIKE.search(alt) or re.search(
                r"latex|\btex\b|math|formula|codecogs", cls + " " + src, re.I)):
            tx = _tex_clean(alt)
            return " $%s$ " % tx if tx else " "
        if not src or src.startswith("data:") or _ICON_RE.search(src + " " + cls):
            return " "
        try:
            w = int(re.sub(r"\D", "", a.get("width", "") or "0") or 0)
            h = int(re.sub(r"\D", "", a.get("height", "") or "0") or 0)
        except ValueError:
            w = h = 0
        if (w and w < 48) or (h and h < 48):
            return " "
        try:
            url = urllib.parse.urljoin(base or "", src)
        except Exception:
            url = src
        if not url.startswith("http"):
            return " "
        url = url.replace(" ", "%20")
        alt = " ".join(alt.replace("[", "(").replace("]", ")").split())[:200]
        return "\n[ИЛЛЮСТРАЦИЯ src=%s]%s[/ИЛЛЮСТРАЦИЯ]\n" % (url, alt)
    return _IMG_TAG.sub(_img, t)


def _page_parts(txt, limit=20000, base=""):
    """str страницы -> (текст, title). Теги/скрипты выкидываются,
    абзацы разделяются переводом строки (нужно для предложений).
    v3.24.5: limit — потолок текста (по умолчанию 20000, как раньше;
    читателям больших страниц — Confluence REST — можно больше)."""
    title = ""
    mt = re.search(r"<title[^>]*>(.*?)</title>", txt, re.S | re.I)
    if mt:
        title = " ".join(_strip_html(mt.group(1)).split())[:200]
    t = _formulas_and_images(txt, base)         # v3.124: формулы, картинки
    t = re.sub(r"<(script|style|noscript|svg|template|iframe)[^>]*>.*?</\1>",
               " ", t, flags=re.S | re.I)
    t = _math_to_tex(t)                         # v3.107: формулы — в $…$
    # v3.27: МЕНЮ, ШАПКА, ПОДВАЛ, «ХЛЕБНЫЕ КРОШКИ» — не текст страницы.
    # Раньше они попадали в базу и в ответы: «Каталог - Выживание» —
    # дерево сайта вместо информации.
    # (<form> не трогаем: на сайтах ASP.NET в нём ВСЯ страница)
    t = re.sub(r"<(nav|header|footer|aside|menu|select|button)\b[^>]*>"
               r".*?</\1>", " ", t, flags=re.S | re.I)
    t = re.sub(r"<(ul|ol|div|p|span|section)\b[^>]*\b(?:class|id|role)="
               r"[\"'][^\"']*(?:breadcrumb|crumbs|navbar|navigation|menu|"
               r"sidebar|footer|header|catalog-nav|pagination|share|social|"
               r"cookie)[^\"']*[\"'][^>]*>.*?</\1>", " ", t,
               flags=re.S | re.I)
    # основной текст — <main>/<article>, если он есть и не пустой
    blocks = re.findall(r"<(main|article)\b[^>]*>(.*?)</\1>", t,
                        flags=re.S | re.I)
    if blocks:
        best = max((b[1] for b in blocks), key=len)
        if len(re.sub(r"<[^>]+>", "", best).strip()) > 400:
            t = best
    t = re.sub(r"</(p|div|h[1-6]|li|tr|article|section|blockquote|td)>",
               "\n", t, flags=re.I)
    t = re.sub(r"<br\s*/?>", "\n", t, flags=re.I)
    t = _TAG.sub(" ", t)                        # v3.107: с кавычками атрибутов
    import html as _h
    t = _h.unescape(t).replace("\xa0", " ")
    t = re.sub(r"</?math\b[^>]*>", " ", t, flags=re.I)   # остатки разметки
    t = _JSON_JUNK.sub(" ", t)
    t = "\n".join(" ".join(ln.split()) for ln in t.splitlines())
    t = re.sub(r"\n{2,}", "\n", t).strip()
    # v3.124: метки иллюстраций не должны пропасть как «строки меню»
    _ill = []

    def _hide(m):
        _ill.append(m.group(0))
        return "⟦ИЛЛ%d⟧." % (len(_ill) - 1)
    t = re.sub(r"\[ИЛЛЮСТРАЦИЯ src=[^\]]*\][^\n]*?\[/ИЛЛЮСТРАЦИЯ\]", _hide, t)
    try:
        import textutils as _tu
        t = _tu.strip_nav(t, menus=True)       # крошки и столбики меню
        # v3.27: обрезка по концу предложения, а не посреди фразы
        t = _tu.cut_at_sentence(t, max(1, int(limit)))
    except Exception:
        t = t[:max(1, int(limit))]
    if _ill:
        t = re.sub(r"⟦ИЛЛ(\d+)⟧\.?",
                   lambda m: _ill[int(m.group(1))] if int(m.group(1)) < len(_ill)
                   else "", t)
    return t, title


def _page_text(body, ctype=""):
    """Совместимость (v3.24.3): то же самое через _decode_body/_page_parts."""
    return _page_parts(_decode_body(body, ctype))


# ===========================================================================
# v3.155 — ПОИСКОВИКИ GOOGLE И ЯНДЕКС (заявка владельца: «вместо DuckDuckGo
# и Bing — google.com и yandex.ru»). DuckDuckGo и Bing убраны полностью.
# Каждый поисковик — два пути:
#   1) официальный API, если владелец вписал ключ в настройках
#      (Google Programmable Search: ключ + cx; Яндекс Search API: ключ +
#      folderid) — стабильно, без капчи;
#   2) обычная страница выдачи (без ключа) — работает, пока поисковик не
#      попросит капчу. Капча/«включите JavaScript» распознаются и честно
#      пишутся в журнал сети (diag_tail), ответ берётся из второго
#      поисковика и Википедии.
# Гарантии прежние: только https, публичные адреса, сертификат по SNI.
# ===========================================================================
SEARCH_HOSTS = ("go.mail.ru", "www.mojeek.com", "www.google.com", "yandex.ru")
_BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
               "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
# Google отдаёт простую HTML-выдачу (без JavaScript) текстовым браузерам
_LYNX_UA = "Lynx/2.9.0dev.12 libwww-FM/2.14 SSL-MM/1.4.1 GNUTLS/3.7.8"
_ENGINE_STATE = {}       # движок -> {"ok": ts, "err": текст, "ts": ts}


def _engine_note(name, ok, err=""):
    st = _ENGINE_STATE.setdefault(name, {})
    st["ts"] = time.time()
    if ok:
        st["ok"] = st["ts"]
        st["err"] = ""
    else:
        st["err"] = str(err)[:200]
        _remember_err("%s: %s" % (name, err))


def engines_state():
    """Для панели: какие поисковики работают и почему нет."""
    out = {}
    for k, v in _ENGINE_STATE.items():
        out[k] = {"ok_ago": int(time.time() - v["ok"]) if v.get("ok") else None,
                  "err": v.get("err") or ""}
    return out


def _search_get(url, ua=_BROWSER_UA, timeout=9, max_bytes=1500000, hops=3):
    """GET страницы выдачи со своими заголовками (браузерный UA, язык ru).
    Возврат (status, final_url, text). Исключений не бросает."""
    cur = url
    for _ in range(hops + 1):
        u = urllib.parse.urlparse(cur)
        if u.scheme != "https" or not u.hostname:
            return 0, cur, "только https"
        try:
            host = _public_host_check(u.hostname)
        except ValueError as e:
            return 0, cur, str(e)
        ips = _resolve_public(host)
        if not ips:
            return 0, cur, "%s не резолвится в публичный адрес" % host
        pq = (u.path or "/") + (("?" + u.query) if u.query else "")
        hdr = {"User-Agent": ua,
               "Accept": "text/html,application/xhtml+xml,application/xml;"
                         "q=0.9,*/*;q=0.8",
               "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.6",
               # согласие на cookie Google (иначе редирект на consent)
               "Cookie": "CONSENT=YES+cb; SOCS=CAI"}
        status, headers, body, last = None, None, None, ""
        for ip in ips[:2]:
            try:
                status, headers, body = _https_call(
                    host, ip, pq, timeout=timeout, max_bytes=max_bytes,
                    extra_headers=hdr)
                break
            except Exception as e:
                last = _honest(e)
        if status is None:
            return 0, cur, last or "соединение не удалось"
        loc = _hdr(headers, "location")
        if status in (301, 302, 303, 307, 308) and loc:
            cur = urllib.parse.urljoin(cur, str(loc))
            if cur.startswith("http://"):
                cur = "https://" + cur[7:]
            continue
        return status, cur, _decode_body(body or b"",
                                         _hdr(headers, "content-type"))
    return 0, cur, "слишком много редиректов"


def _skey(name):
    try:
        import store as _st
        return str(_st.settings().get(name) or "").strip()
    except Exception:
        return ""


def _clean_txt(s):
    return " ".join(_strip_html(s or "").split())


# ---------------------------- Google ---------------------------------------
def google_api(q, limit=10):
    """Google Programmable Search JSON API (ключ + cx из настроек)."""
    key, cx = _skey("google_api_key"), _skey("google_cx")
    if not key or not cx:
        return None                       # ключа нет — не ошибка
    url = ("https://www.googleapis.com/customsearch/v1?" + urllib.parse.urlencode(
        {"key": key, "cx": cx, "q": q, "num": max(1, min(10, int(limit))),
         "hl": "ru", "lr": "lang_ru|lang_en"}))
    st, _f, txt = _search_get(url, ua=_UA, max_bytes=600000)
    if st != 200:
        _engine_note("google-api", False, "HTTP %s %s" % (st, txt[:120]
                                                          if st == 0 else ""))
        return []
    try:
        data = json.loads(txt)
    except Exception:
        _engine_note("google-api", False, "ответ не JSON")
        return []
    out = []
    for it in data.get("items") or []:
        link = str(it.get("link") or "")
        if link.startswith("http"):
            out.append({"title": _clean_txt(it.get("title"))[:160],
                        "url": link[:300],
                        "snippet": _clean_txt(it.get("snippet"))[:400],
                        "src": ""})
    _engine_note("google-api", True)
    return out[:limit]


def _google_real_url(href):
    import html as _h
    href = _h.unescape(str(href or ""))
    if href.startswith("/url?") or "google." in href and "/url?" in href:
        try:
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(href).query)
            href = (qs.get("q") or qs.get("url") or [""])[0]
        except Exception:
            return ""
    if not href.startswith("http"):
        return ""
    h = (urllib.parse.urlparse(href).hostname or "").lower()
    if not h or h.endswith("google.com") or h.endswith("google.ru") or \
            h.endswith("gstatic.com") or h.endswith("googleusercontent.com") \
            or ".google." in "." + h:
        return ""
    return href[:300]


def _parse_google_html(txt, limit=10):
    """Простая выдача Google: ссылки /url?q=<адрес>&sa=U…, заголовок —
    текст ссылки, сниппет — текст до следующей ссылки результата."""
    out, seen = [], set()
    anchors = list(re.finditer(
        r'<a[^>]+href="(/url\?[^"]+|https?://[^"]+)"[^>]*>(.*?)</a>',
        txt, re.S | re.I))
    for i, m in enumerate(anchors):
        real = _google_real_url(m.group(1))
        if not real:
            continue
        h3 = re.search(r"<h3[^>]*>(.*?)</h3>", m.group(2), re.S | re.I)
        title = _clean_txt(h3.group(1) if h3 else m.group(2))
        if len(title) < 3:
            continue
        nu = _norm_url(real)
        if nu in seen:
            continue
        seen.add(nu)
        end = anchors[i + 1].start() if i + 1 < len(anchors) else \
            m.end() + 1500
        sn = _clean_txt(txt[m.end():min(end, m.end() + 2500)])[:400]
        out.append({"title": title[:160], "url": real, "snippet": sn,
                    "src": ""})
        if len(out) >= limit:
            break
    return out


def google_html(q, limit=10):
    """Выдача google.com без ключа. Исключений не бросает."""
    url = "https://www.google.com/search?" + urllib.parse.urlencode(
        {"q": q, "hl": "ru", "num": max(5, min(20, int(limit) + 3)),
         "ie": "UTF-8", "oe": "UTF-8", "gbv": "1"})
    st, final, txt = _search_get(url, ua=_LYNX_UA)
    low = (txt or "")[:20000].lower()
    if "/sorry/" in final or "unusual traffic" in low or \
            "необычный трафик" in low or "recaptcha" in low:
        _engine_note("google", False, "Google попросил капчу (много "
                     "запросов с этого адреса) — нужен ключ API")
        return []
    if "enablejs" in final or ("enablejs" in low and "/url?" not in low):
        _engine_note("google", False, "Google требует JavaScript — "
                     "впишите ключ API Google в настройках")
        return []
    if st != 200:
        _engine_note("google", False, "HTTP %s %s" % (st, txt[:100]
                                                      if st == 0 else ""))
        return []
    out = _parse_google_html(txt, limit)
    if out:
        _engine_note("google", True)
    else:
        _engine_note("google", False, "выдача без ссылок (изменилась "
                     "разметка или антибот)")
    return out


def google_search(q, limit=10):
    q = str(q or "").strip()[:150]
    if not q:
        return []
    key = "g:" + q.lower()
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < _CACHE_TTL and hit[1]:
        return hit[1]
    out = google_api(q, limit)
    if not out:                      # нет ключа или API отказал — страница
        out = google_html(q, limit)
    if out:
        _cache[key] = (time.time(), out, "Google")
    return out


# ---------------------------- Яндекс ---------------------------------------
def yandex_api(q, limit=10):
    """Яндекс Search API (Yandex Cloud): ключ API + folderid из настроек.
    Ответ — XML (как у классического Яндекс.XML)."""
    key, folder = _skey("yandex_api_key"), _skey("yandex_folder")
    if not key or not folder:
        return None
    url = ("https://yandex.ru/search/xml?" + urllib.parse.urlencode(
        {"folderid": folder, "apikey": key, "query": q, "l10n": "ru",
         "sortby": "rlv", "filter": "moderate", "maxpassages": "2",
         "groupby": "attr=\"\".mode=flat.groups-on-page=%d.docs-in-group=1"
                    % max(5, min(20, int(limit)))}))
    st, _f, txt = _search_get(url, ua=_UA)
    if st != 200:
        _engine_note("yandex-api", False, "HTTP %s" % st)
        return []
    em = re.search(r'<error[^>]*code="(\d+)"[^>]*>(.*?)</error>', txt, re.S)
    if em:
        _engine_note("yandex-api", False, "ошибка %s: %s" % (
            em.group(1), _clean_txt(em.group(2))[:120]))
        return []
    out = []
    for doc in re.findall(r"<doc\b[^>]*>(.*?)</doc>", txt, re.S):
        um = re.search(r"<url>(.*?)</url>", doc, re.S)
        tm = re.search(r"<title>(.*?)</title>", doc, re.S)
        if not um:
            continue
        link = _clean_txt(um.group(1))
        pas = " ".join(_clean_txt(p) for p in
                       re.findall(r"<passage>(.*?)</passage>", doc, re.S))
        if not pas:
            hm = re.search(r"<headline>(.*?)</headline>", doc, re.S)
            pas = _clean_txt(hm.group(1)) if hm else ""
        out.append({"title": _clean_txt(tm.group(1) if tm else link)[:160],
                    "url": link[:300], "snippet": pas[:400], "src": ""})
        if len(out) >= limit:
            break
    _engine_note("yandex-api", True)
    return out


def _parse_yandex_html(txt, limit=10):
    out, seen = [], set()
    blocks = re.split(r'<li[^>]+class="[^"]*serp-item', txt)[1:]
    for blk in blocks:
        blk = blk[:12000]
        if "data-fast-name" in blk[:400] or "Реклама" in blk[:3000]:
            continue                       # колдунщики и реклама
        link = ""
        for h in re.findall(r'href="(https?://[^"]+)"', blk):
            host = (urllib.parse.urlparse(h).hostname or "").lower()
            if host and not re.search(r"(^|\.)(yandex\.[a-z]+|ya\.ru|yastatic"
                                      r"\.net|yandex-team\.ru)$", host):
                link = h
                break
        if not link:
            continue
        import html as _h
        link = _h.unescape(link)[:300]
        tm = re.search(r'<h2[^>]*>(.*?)</h2>', blk, re.S) or \
            re.search(r'OrganicTitleContentSpan[^>]*>(.*?)</span>', blk, re.S)
        title = _clean_txt(tm.group(1)) if tm else ""
        sm = re.search(r'OrganicTextContentSpan[^>]*>(.*?)</span>', blk, re.S) \
            or re.search(r'class="[^"]*(?:Organic-ContentWrapper|text-'
                         r'container)[^"]*"[^>]*>(.*?)</div>', blk, re.S)
        sn = _clean_txt(sm.group(1))[:400] if sm else ""
        nu = _norm_url(link)
        if not title or nu in seen:
            continue
        seen.add(nu)
        out.append({"title": title[:160], "url": link, "snippet": sn,
                    "src": ""})
        if len(out) >= limit:
            break
    return out


def yandex_html(q, limit=10):
    """Выдача yandex.ru без ключа. Исключений не бросает."""
    url = "https://yandex.ru/search/?" + urllib.parse.urlencode(
        {"text": q, "lr": "213", "numdoc": max(10, min(20, int(limit) + 3))})
    st, final, txt = _search_get(url)
    low = (txt or "")[:30000].lower()
    if "showcaptcha" in final or "/captcha" in final or \
            "smartcaptcha" in low or "checkbox-captcha" in low or \
            ("captcha" in low and "serp-item" not in low):
        _engine_note("yandex", False, "Яндекс попросил капчу (много "
                     "запросов с этого адреса) — нужен ключ Search API")
        return []
    if st != 200:
        _engine_note("yandex", False, "HTTP %s %s" % (st, txt[:100]
                                                      if st == 0 else ""))
        return []
    out = _parse_yandex_html(txt, limit)
    if out:
        _engine_note("yandex", True)
    else:
        _engine_note("yandex", False, "выдача без ссылок (изменилась "
                     "разметка или антибот)")
    return out


def yandex_search(q, limit=10):
    q = str(q or "").strip()[:150]
    if not q:
        return []
    key = "y:" + q.lower()
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < _CACHE_TTL and hit[1]:
        return hit[1]
    out = yandex_api(q, limit)
    if not out:
        out = yandex_html(q, limit)
    if out:
        _cache[key] = (time.time(), out, "Яндекс")
    return out




# ---------------------------------------------------------------------------
# v3.167: ПОИСКОВИКИ, ДОСТУПНЫЕ В РОССИИ БЕЗ КЛЮЧА. Владелец: «Яндекс и
# Гугл не работают» (без ключа API они отдают капчу/требуют JavaScript).
#   * Поиск Mail.ru (go.mail.ru) — российский, собственный индекс;
#   * Mojeek — независимый поисковик со своим индексом, отдаёт обычную
#     HTML-страницу без JavaScript.
# Google и Яндекс остаются, но только по ключу API (Настройки).
# Разметку выдачи поисковики меняют — разбор нарочно общий: внешние ссылки
# с заголовком + JSON-данные страницы. Капча — честно в журнал.
# ---------------------------------------------------------------------------
_OWN_HOST = re.compile(r"(^|\.)(mail\.ru|imgsmail\.ru|mradx\.net|vk\.com|"
                       r"vk\.ru|ok\.ru|mojeek\.com|mojeek\.co\.uk)$")


def _external(href):
    import html as _h
    href = _h.unescape(str(href or "")).strip()
    if href.startswith("//"):
        href = "https:" + href
    if not href.startswith("http"):
        return ""
    try:
        u = urllib.parse.urlparse(href)
        qs = urllib.parse.parse_qs(u.query)
        for k in ("url", "u", "q", "to"):            # редиректоры выдачи
            if (u.hostname or "").endswith(("mail.ru", "mojeek.com")) and \
                    qs.get(k) and str(qs[k][0]).startswith("http"):
                return _external(qs[k][0])
    except Exception:
        return ""
    h = (urllib.parse.urlparse(href).hostname or "").lower()
    if not h or _OWN_HOST.search(h):
        return ""
    return href[:300]


def _parse_generic(txt, limit=10):
    """Общий разбор выдачи: 1) JSON внутри страницы ("url"/"title"/
    "snippet|passage|text"); 2) ссылки <a href=внешний>заголовок</a>,
    сниппет — текст до следующей ссылки."""
    out, seen = [], set()
    import html as _h
    for m in re.finditer(r'"(?:url|link|href)"\s*:\s*"(https?:[^"]{8,300})"', txt):
        url = _external(m.group(1).replace("\\/", "/"))
        if not url:
            continue
        win = txt[max(0, m.start() - 1500):m.end() + 1500]
        tm = re.search(r'"(?:title|name)"\s*:\s*"([^"]{5,200})"', win)
        sm = re.search(r'"(?:snippet|passage|text|description|annotation)"'
                       r'\s*:\s*"([^"]{20,600})"', win)
        if not tm:
            continue
        nu = _norm_url(url)
        if nu in seen:
            continue
        seen.add(nu)
        dec = lambda x: _clean_txt(_h.unescape(
            x.encode("utf-8").decode("unicode_escape", "ignore")
            if "\\u" in x else x))
        try:
            title, sn = dec(tm.group(1)), dec(sm.group(1)) if sm else ""
        except Exception:
            title, sn = _clean_txt(tm.group(1)), _clean_txt(sm.group(1) if sm else "")
        out.append({"title": title[:160], "url": url, "snippet": sn[:400], "src": ""})
        if len(out) >= limit:
            return out
    anchors = list(re.finditer(r'<a\b[^>]*href="([^"]+)"[^>]*>(.*?)</a>', txt,
                               re.S | re.I))
    for i, m in enumerate(anchors):
        url = _external(m.group(1))
        if not url:
            continue
        title = _clean_txt(m.group(2))
        if len(title) < 8 or title.lower().startswith(("http", "www.")):
            continue
        nu = _norm_url(url)
        if nu in seen:
            continue
        seen.add(nu)
        end = anchors[i + 1].start() if i + 1 < len(anchors) else m.end() + 1500
        sn = _clean_txt(txt[m.end():min(end, m.end() + 2000)])[:400]
        out.append({"title": title[:160], "url": url, "snippet": sn, "src": ""})
        if len(out) >= limit:
            break
    return out


def _captcha(final, low):
    return any(x in final for x in ("/captcha", "showcaptcha", "/sorry/")) or \
        any(x in low for x in ("captcha", "recaptcha", "я не робот",
                               "подтвердите, что вы не робот"))


def mailru_search(q, limit=10):
    """Поиск Mail.ru (go.mail.ru) без ключа."""
    q = str(q or "").strip()[:150]
    if not q:
        return []
    key = "m:" + q.lower()
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < _CACHE_TTL and hit[1]:
        return hit[1]
    st, final, txt = _search_get("https://go.mail.ru/search?" +
                                 urllib.parse.urlencode({"q": q}))
    low = (txt or "")[:40000].lower()
    if st != 200:
        _engine_note("mailru", False, "HTTP %s %s" % (st, txt[:100] if st == 0 else ""))
        return []
    out = _parse_generic(txt, limit)
    if not out and _captcha(final, low):
        _engine_note("mailru", False, "Поиск Mail.ru попросил капчу")
        return []
    if out:
        _engine_note("mailru", True)
        _cache[key] = (time.time(), out, "Поиск Mail.ru")
    else:
        _engine_note("mailru", False, "выдача без ссылок (разметка "
                     "изменилась или нужен JavaScript)")
    return out


def _parse_mojeek(txt, limit=10):
    out, seen = [], set()
    for blk in re.split(r"<li\b[^>]*class=\"r\d*\"", txt)[1:] or \
            re.split(r"<li\b", txt)[1:]:
        blk = blk[:6000]
        tm = re.search(r'<a\b[^>]*class="title"[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
                       blk, re.S) or re.search(
            r'<h2[^>]*>\s*<a\b[^>]*href="([^"]+)"[^>]*>(.*?)</a>', blk, re.S)
        if not tm:
            continue
        url = _external(tm.group(1))
        if not url:
            continue
        sm = re.search(r'<p\b[^>]*class="s"[^>]*>(.*?)</p>', blk, re.S)
        nu = _norm_url(url)
        if nu in seen:
            continue
        seen.add(nu)
        out.append({"title": _clean_txt(tm.group(2))[:160], "url": url,
                    "snippet": _clean_txt(sm.group(1))[:400] if sm else "",
                    "src": ""})
        if len(out) >= limit:
            break
    return out or _parse_generic(txt, limit)


def mojeek_search(q, limit=10):
    """Mojeek (свой индекс, HTML без JavaScript) без ключа."""
    q = str(q or "").strip()[:150]
    if not q:
        return []
    key = "j:" + q.lower()
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < _CACHE_TTL and hit[1]:
        return hit[1]
    st, final, txt = _search_get("https://www.mojeek.com/search?" +
                                 urllib.parse.urlencode({"q": q, "lb": "ru"}))
    if st == 403 or st == 429:
        _engine_note("mojeek", False, "Mojeek ограничил частоту запросов (HTTP %s)" % st)
        return []
    if st != 200:
        _engine_note("mojeek", False, "HTTP %s %s" % (st, txt[:100] if st == 0 else ""))
        return []
    out = _parse_mojeek(txt, limit)
    if out:
        _engine_note("mojeek", True)
        _cache[key] = (time.time(), out, "Mojeek")
    else:
        _engine_note("mojeek", False, "выдача без ссылок")
    return out


def _keyed_google(q, limit=10):
    """Google — только по ключу API (страница выдачи без ключа даёт капчу)."""
    return google_api(q, limit) or []


def _keyed_yandex(q, limit=10):
    return yandex_api(q, limit) or []


_ENGINES = {"mailru": mailru_search, "mojeek": mojeek_search,
            "google": _keyed_google, "yandex": _keyed_yandex}


def engines_list():
    """Какие поисковики включены (настройка web_engines)."""
    try:
        import store as _st
        eng = str(_st.settings().get("web_engines") or "")
    except Exception:
        eng = ""
    names = [x.strip() for x in eng.lower().split(",") if x.strip() in _ENGINES]
    # v3.167: старое «google,yandex» без ключей — заменяем на доступные в РФ
    if not names or (set(names) <= {"google", "yandex"} and
                     not (_skey("google_api_key") or _skey("yandex_api_key"))):
        names = ["mailru", "mojeek"]
    if _skey("google_api_key") and _skey("google_cx") and "google" not in names:
        names.append("google")
    if _skey("yandex_api_key") and _skey("yandex_folder") and "yandex" not in names:
        names.append("yandex")
    return names


def engine_search(q, limit=10):
    """Первая непустая выдача включённых поисковиков (v3.167)."""
    for name in engines_list():
        try:
            out = _ENGINES[name](q, limit)
        except Exception:
            out = []
        if out:
            return out
    return []


def engine_snips(q, limit=3):
    """Замена DuckDuckGo Instant Answers: сниппеты Google/Яндекса,
    подписанные именем сайта. Поля {title, snippet, url, src}."""
    out = []
    for r in engine_search(q, limit + 3):
        if not r.get("snippet"):
            continue
        out.append(dict(r, src=urllib.parse.urlparse(r["url"]).hostname or
                        "сайт"))
        if len(out) >= limit:
            break
    return out


def top_pages(q, limit=10):
    """Ссылки ПЕРВЫХ страниц выдачи: Поиск Mail.ru + Mojeek (+ Google и
    Яндекс по ключу API) + Википедия (гарантированный источник).
    Параллельно, дедупликация по адресу, по очереди из каждой выдачи.
    Возврат [{title, url, snippet, src}]."""
    q = str(q or "").strip()[:150]
    if not q:
        return []
    try:
        import store as _st
        s = _st.settings()
        eng = engines_list()
        if not s.get("web_any", True):
            eng = []                     # только Википедия
    except Exception:
        eng = ["mailru", "mojeek"]
    from concurrent.futures import ThreadPoolExecutor as _TPE
    jobs, res = {}, {}
    pool = _TPE(max_workers=max(2, len(eng) + 1))
    try:
        for name in eng:
            jobs[name] = pool.submit(_ENGINES[name], q, limit)
        jobs["wiki"] = pool.submit(wiki_search, q, 2)
        for k, f in jobs.items():
            try:
                res[k] = f.result(timeout=10) or []
            except Exception:
                res[k] = []
    finally:
        try:
            pool.shutdown(wait=False, cancel_futures=True)
        except TypeError:
            pool.shutdown(wait=False)
    # по очереди из каждой выдачи: первые места всех поисковиков — первыми
    lists = [res.get(n, []) for n in eng]
    mixed = []
    for i in range(max([len(x) for x in lists] or [0])):
        for x in lists:
            if i < len(x):
                mixed.append(x[i])
    pages, seen = [], set()
    for r in mixed + res.get("wiki", []):
        nu = _norm_url(r.get("url"))
        if not nu or nu in seen:
            continue
        seen.add(nu)
        pages.append({"title": str(r.get("title") or "")[:160],
                      "url": str(r.get("url") or "")[:300],
                      "snippet": str(r.get("snippet") or "")[:400],
                      "src": str(r.get("src") or "")})
        if len(pages) >= limit:
            break
    return pages


def alice(q, long_mode=False):
    """v3.14 — СНАЧАЛА «ПЕРВЫЕ СТРАНИЦЫ» (multisource.pages_answer):
    мозг читает сами страницы выдачи. v3.27: и СОБИРАЕТ из них связный
    ответ (без перечня сайтов — источники в meta). Резерв — API-источники
    v3.13 (_alice_simple): работает, даже если выдача недоступна.
    long_mode — длинный ответ (галочка «длинно»).
    Возврат (reply|None, meta{sources, errors, mode})."""
    errs = []
    t0 = time.time()
    try:
        import multisource
        reply, meta = multisource.pages_answer(q, long_mode=long_mode)
        if reply:
            return reply, meta
        errs = list((meta or {}).get("errors") or [])
    except Exception as e:
        errs = [_honest(e)]
    if time.time() - t0 > 10.0:            # v3.135: было 20 с
        # v3.27: страницы уже съели бюджет — резерв (ещё до 20+ с сети)
        # не запускаем: человек в МАКС ждёт ответ, а не минуту тишины
        return None, {"sources": [], "errors": errs + [
            "интернет отвечает слишком медленно"]}
    reply, meta2 = _alice_simple(q, long_mode=long_mode)
    if reply:
        m = dict(meta2 or {})
        m["errors"] = errs + list(m.get("errors") or [])
        m.setdefault("mode", "alice")
        return reply, m
    return None, {"sources": [],
                  "errors": errs + list((meta2 or {}).get("errors") or [])}


def cert_info(url, timeout=8, port=443):
    """Диагностика (только чтение, ТЕКСТ СТРАНИЦЫ НЕ ЗАПРАШИВАЕТСЯ): кто подписал
    сертификат, который показывает сайт этому компьютеру. Проверку сертификата
    при загрузке статей это НЕ отключает — нужно лишь чтобы пользователь увидел,
    кто вмешивается (антивирус, провайдер, прокси) или что сайт неисправен.
    -> {"host","issuer","subject","verify","note"}"""
    import socket
    import ssl
    import tempfile
    import urllib.parse
    out = {"host": "", "issuer": "", "subject": "", "verify": "", "note": ""}
    try:
        host = urllib.parse.urlsplit(url).hostname or ""
        out["host"] = host
        if not host:
            return out
        # 1) стандартная проверка — берём её текст ошибки
        try:
            with socket.create_connection((host, port), timeout=timeout) as s:
                with ssl.create_default_context().wrap_socket(
                        s, server_hostname=host):
                    out["verify"] = "ok"
        except ssl.SSLCertVerificationError as e:
            out["verify"] = str(getattr(e, "verify_message", "") or e)[:120]
        except Exception as e:
            out["verify"] = "нет соединения: " + str(e)[:80]
        # 2) только рукопожатие, без отправки запроса — прочитать сертификат
        if out["verify"] and out["verify"] != "ok" \
                and not out["verify"].startswith("нет соединения"):
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            with socket.create_connection((host, port), timeout=timeout) as s:
                with ctx.wrap_socket(s, server_hostname=host) as t:
                    der = t.getpeercert(binary_form=True)
            pem = ssl.DER_cert_to_PEM_cert(der)
            with tempfile.NamedTemporaryFile("w", suffix=".pem",
                                             delete=False) as f:
                f.write(pem)
                fn = f.name
            try:
                d = ssl._ssl._test_decode_cert(fn)

                def _flat(t):
                    return ", ".join("%s" % v for r in (t or ()) for _k, v in r)
                out["issuer"] = _flat(d.get("issuer"))[:120]
                out["subject"] = _flat(d.get("subject"))[:120]
            finally:
                try:
                    os.unlink(fn)
                except Exception:
                    pass
            iss = out["issuer"].lower()
            if any(k in iss for k in ("kaspersky", "касперск", "eset", "avast",
                                      "bitdefender", "dr.web", "drweb",
                                      "mcafee", "norton", "fortinet",
                                      "zscaler", "sophos")):
                out["note"] = ("сертификат выдан антивирусом/фильтром — он "
                               "перехватывает HTTPS. Отключите проверку "
                               "HTTPS-трафика для python.exe или добавьте "
                               "сайт в исключения.")
            elif out["issuer"] and out["issuer"] == out["subject"]:
                out["note"] = "сертификат самоподписанный."
            elif "expired" in out["verify"].lower():
                out["note"] = "у сайта истёк сертификат — это его неисправность."
            else:
                out["note"] = ("корневой сертификат подписавшего не известен "
                               "этому компьютеру (антивирус, прокси, провайдер "
                               "или неполная цепочка у сайта).")
    except Exception as e:
        out["note"] = "диагностика не удалась: " + str(e)[:80]
    return out
