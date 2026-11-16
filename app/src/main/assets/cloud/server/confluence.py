# -*- coding: utf-8 -*-
"""v3.168 — ДОКУМЕНТАЦИЯ В CONFLUENCE КАК ВИКИПЕДИЯ (без записи в базу).

Заявка владельца: «пусть также по ссылке
https://confluence.softrust.ru/collector/pages.action?key=docEMIAS ищет
информацию, она как Википедия. Чтобы не добавлять в базу знаний».

Как работает:
  * в настройках — адрес Confluence и ключ пространства (по умолчанию
    confluence.softrust.ru, docEMIAS); если пространство закрыто — логин и
    пароль или личный токен (Personal Access Token). Пароль/токен хранятся
    только в облаке, в панель приходит маска;
  * вопрос с темой: в базе знаний статьи нет — облако ищет страницы
    пространства (REST API Confluence, поиск CQL: space = KEY AND
    text ~ "…"), берёт лучшую по совпадению названия и текста, читает её
    и отвечает связным куском подряд (как «Любопытство») со ссылкой;
  * в базу знаний НИЧЕГО не пишется: страницы держатся только в памяти
    (кэш 30 мин), всегда берётся актуальная версия документации;
  * Confluence первым, Википедия — если в документации ответа нет.
Адрес задаёт владелец, поэтому разрешён и адрес внутренней сети компании
(Confluence часто стоит во внутренней сети); сертификат проверяется.
"""
import base64
import html as _html
import json
import re
import ssl
import time
import urllib.parse
import urllib.request

import store

_CACHE = {}                  # ключ -> (время, данные)
_TTL = 1800
_STATE = {"ok": 0, "err": "", "last": "", "ts": 0.0}
_UA = "neuro12-cloud (docs reader)"


# ------------------------------------------------------------------ настройки
def cfg():
    s = store.settings()
    raw = str(s.get("conf_url") or "").strip()
    space = str(s.get("conf_space") or "").strip()
    u = urllib.parse.urlparse(raw)
    base = "%s://%s" % (u.scheme, u.netloc) if u.scheme == "https" and u.netloc else ""
    # ссылка вида …/collector/pages.action?key=docEMIAS — ключ берём из неё
    if not space:
        space = (urllib.parse.parse_qs(u.query).get("key") or [""])[0]
        if not space:
            m = re.search(r"/(?:display|spaces)/([A-Za-z0-9_~-]+)", u.path or "")
            space = m.group(1) if m else ""
    space = re.sub(r"[^A-Za-z0-9_~-]", "", space)[:60]
    return {"on": bool(s.get("conf_on", True)) and base.startswith("https://") and bool(space),
            "base": base, "space": space,
            "user": str(s.get("conf_user") or "").strip(),
            "pw": str(s.get("conf_password") or ""),
            "token": str(s.get("conf_token") or "").strip()}


def enabled():
    return cfg()["on"]


# --------------------------------------------------------------------- HTTP
def _get(path, c, timeout=12):
    """GET JSON с авторизацией (токен Bearer, иначе логин+пароль, иначе без).
    -> (status, data|None, текст ошибки)."""
    url = c["base"] + path
    hdr = {"User-Agent": _UA, "Accept": "application/json"}
    if c["token"]:
        hdr["Authorization"] = "Bearer " + c["token"]
    elif c["user"] and c["pw"]:
        hdr["Authorization"] = "Basic " + base64.b64encode(
            ("%s:%s" % (c["user"], c["pw"])).encode("utf-8")).decode()
    req = urllib.request.Request(url, headers=hdr)
    try:
        with urllib.request.urlopen(req, timeout=timeout,
                                    context=ssl.create_default_context()) as r:
            raw = r.read(3000000)
            ctype = r.headers.get("Content-Type", "")
            if "json" not in ctype:
                # редирект на страницу входа — пространство закрыто
                return r.status, None, "Confluence просит вход (нужен логин/пароль или токен)"
            return r.status, json.loads(raw.decode("utf-8", "replace")), ""
    except urllib.error.HTTPError as e:
        why = {401: "нужен вход: логин/пароль или токен неверны или не заданы",
               403: "нет прав на пространство %s" % c["space"],
               404: "адрес не найден (проверьте адрес Confluence)"}.get(e.code, "HTTP %s" % e.code)
        return e.code, None, why
    except Exception as e:
        t = str(getattr(e, "reason", e))
        low = t.lower()
        if "certificate" in low:
            t = "сертификат сайта не прошёл проверку"
        elif "timed out" in low:
            t = "Confluence не ответил (сеть/брандмауэр)"
        elif "name or service" in low or "getaddrinfo" in low or "11001" in low:
            t = "имя сервера Confluence не найдено (DNS)"
        return 0, None, t[:160]


def _note(ok, err=""):
    _STATE["ts"] = time.time()
    if ok:
        _STATE["ok"] += 1
        _STATE["err"] = ""
    else:
        _STATE["err"] = err
        try:
            store.log("CONFLUENCE", err)
        except Exception:
            pass


# -------------------------------------------------------------- текст/поиск
def _plain(h):
    h = re.sub(r"<(script|style|table[^>]*class=\"[^\"]*toc)[^>]*>.*?</\1>", " ",
               str(h or ""), flags=re.S | re.I)
    h = re.sub(r"<br\s*/?>|</(p|li|h[1-6]|tr|div)>", "\n", h, flags=re.I)
    h = re.sub(r"<li[^>]*>", "\n• ", h, flags=re.I)
    h = re.sub(r"<[^>]+>", " ", h)
    h = _html.unescape(h)
    lines = [" ".join(x.split()) for x in h.split("\n")]
    return "\n".join(x for x in lines if x)


_STOPW = set("что такое как где когда почему зачем какой какая какие сколько "
             "расскажи объясни скажи про это мне для или при над под".split())


def _terms(q):
    return [w for w in re.findall(r"[а-яёa-z0-9]{3,}", str(q or "").lower())
            if w not in _STOPW][:6]


def _stems(t):
    try:
        import kb
        return set(kb._stems(t))
    except Exception:
        return set(w[:5] for w in _terms(t))


def search(q, limit=5, c=None):
    """Страницы пространства по вопросу -> [{id, title, url, excerpt}].
    v3.174: c — настройки конкретного источника (sources.py)."""
    c = c or cfg()
    if not c["on"]:
        return []
    terms = _terms(q)
    if not terms:
        return []
    key = "s:" + c["base"] + ":" + c["space"] + ":" + " ".join(terms)
    hit = _CACHE.get(key)
    if hit and time.time() - hit[0] < _TTL:
        return hit[1]
    words = " ".join(terms).replace('"', " ")
    cql = 'space = "%s" AND type = page AND (title ~ "%s" OR text ~ "%s")' % (
        c["space"].replace('"', ""), words, words)
    st, data, err = _get("/rest/api/content/search?" + urllib.parse.urlencode(
        {"cql": cql, "limit": str(max(1, min(10, limit))), "expand": "space"}), c)
    if data is None and st not in (401, 403):     # старые версии — /rest/api/search
        st, data, err2 = _get("/rest/api/search?" + urllib.parse.urlencode(
            {"cql": cql, "limit": str(limit)}), c)
        err = err2 or err
    if data is None:
        _note(False, "поиск: " + err)
        return []
    out = []
    for r in data.get("results") or []:
        cont = r.get("content") if isinstance(r.get("content"), dict) else r
        pid = str(cont.get("id") or "")
        title = str(cont.get("title") or r.get("title") or "")
        if not pid or not title:
            continue
        link = ((cont.get("_links") or {}).get("webui") or
                (r.get("_links") or {}).get("webui") or r.get("url") or "")
        out.append({"id": pid, "title": title[:160],
                    "url": (c["base"] + link) if link.startswith("/") else (link or c["base"]),
                    "excerpt": _plain(r.get("excerpt") or "")[:300]})
    _note(True)
    _CACHE[key] = (time.time(), out)
    return out


def page_text(pid, c=None):
    c = c or cfg()
    key = "p:" + c["base"] + ":" + str(pid)
    hit = _CACHE.get(key)
    if hit and time.time() - hit[0] < _TTL:
        return hit[1]
    st, data, err = _get("/rest/api/content/%s?expand=body.view,version" %
                         urllib.parse.quote(str(pid)), c, timeout=20)
    if data is None:
        _note(False, "страница %s: %s" % (pid, err))
        return ""
    txt = _plain(((data.get("body") or {}).get("view") or {}).get("value") or "")
    _CACHE[key] = (time.time(), txt[:60000])
    return txt[:60000]


# ------------------------------------------------------------------- ответ
def _sentences(text):
    out = []
    for para in str(text or "").split("\n"):
        para = para.strip()
        if not para:
            continue
        if len(para) < 90 and not re.search(r"[.!?…:;]$", para) and \
                not para.startswith("•"):
            continue                     # заголовок раздела — не предложение
        parts = re.split(r"(?<=[.!?…])\s+(?=[А-ЯЁA-Z0-9«\"•(])", para)
        out += [p.strip() for p in parts if len(p.strip()) > 2]
    return out


def passage(question, text, max_chars=900, max_sents=7):
    """Связный кусок подряд: от первого предложения по теме и дальше."""
    sents = _sentences(text)
    if not sents:
        return ""
    qs = _stems(question)
    best, bs = 0, -1
    for i, s_ in enumerate(sents):
        sc = len(qs & _stems(s_))
        if sc > bs:
            best, bs = i, sc
        if sc >= max(2, len(qs)):          # первое полное попадание
            best = i
            break
    if bs <= 0:
        best = 0
    # v3.174: шаг назад, если предыдущее предложение тоже по теме (начало
    # рассказа: «Отмена записи выполняется…» перед «Нажмите «Отменить…»»)
    while best > 0 and (qs & _stems(sents[best - 1])) and best > 0:
        best -= 1
        if len(qs & _stems(sents[best])) < 1:
            break
    win, chars = [], 0
    for s_ in sents[best:]:
        if len(win) >= max_sents or (win and chars + len(s_) > max_chars):
            break
        win.append(s_)
        chars += len(s_)
    return " ".join(win)


def answer(question, long_mode=False):
    """Ответ из документации -> {ok, text, title, url} | {ok: False, why}."""
    if not enabled():
        return {"ok": False, "why": "выключено"}
    t0 = time.time()
    res = search(question, 5)
    if not res:
        return {"ok": False, "why": _STATE["err"] or "в документации ничего не нашлось"}
    qs = _stems(question)

    def _score(r):
        return 2 * len(qs & _stems(r["title"])) + len(qs & _stems(r["excerpt"]))
    res.sort(key=_score, reverse=True)
    for r in res[:2]:
        if _score(r) <= 0:
            continue
        txt = page_text(r["id"])
        if len(txt) < 80:
            continue
        ps = passage(question, txt, 1600 if long_mode else 900, 14 if long_mode else 7)
        if not ps or not (qs & _stems(ps + " " + r["title"])):
            continue
        _STATE["last"] = r["title"]
        store.log("CONFLUENCE", "«%s» -> страница «%s» (%.1f с, в базу не пишется)" % (
            str(question)[:60], r["title"][:60], time.time() - t0))
        return {"ok": True, "text": ps, "title": r["title"], "url": r["url"]}
    return {"ok": False, "why": "страницы нашлись, но не про это"}


def status():
    c = cfg()
    return {"on": c["on"], "base": c["base"], "space": c["space"],
            "auth": "токен" if c["token"] else ("логин" if c["user"] else "без входа"),
            "ok": _STATE["ok"], "err": _STATE["err"], "last": _STATE["last"]}
