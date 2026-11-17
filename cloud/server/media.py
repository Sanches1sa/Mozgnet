# -*- coding: utf-8 -*-
# ===========================================================================
# media.py — ВИДЕО И МУЗЫКА ИЗ ИНТЕРНЕТА (v3.30, только stdlib)
# ---------------------------------------------------------------------------
# Заявка владельца: «научи смотреть видео и слушать музыку в интернете;
# пусть к любопытству добавится не только Википедия, но и видео».
# ЧЕСТНО о том, что это значит без нейросетей распознавания:
#   * «смотреть видео» = читать то, что в видео СКАЗАНО: субтитры YouTube
#     (ручные или автоматические — дорожки captionTracks страницы ролика),
#     плюс название, автор и описание. Картинку кадров мозг не видит;
#   * «слушать музыку» = звук мозг не слышит; он читает о треке: название,
#     исполнителя, описание и текст песни, если он есть в субтитрах ролика.
# Прочитанное — статья базы знаний «Видео: …» и материал сети (облако учится).
# v3.125: YouTube заменён на RUTUBE и VK Видео (поиск роликов и чтение их
# страниц): RUTUBE — открытый API (поиск, название, автор, описание,
# длительность, субтитры, если есть); VK Видео — страница ролика (название,
# описание, длительность из разметки og:). Ссылка YouTube, присланная
# явно, по-прежнему читается, но в поиске YouTube больше не участвует.
# Все загрузки — через webfetch (https, SSRF-проверки, лимиты размера).
# ===========================================================================
import html
import json
import re
import urllib.parse

import webfetch

_YT_ID = re.compile(r"(?:youtube\.com/(?:watch\?(?:.*&)?v=|shorts/|embed/|"
                    r"live/)|youtu\.be/)([A-Za-z0-9_-]{11})")
WATCH_Q = re.compile(
    r"^\s*(?:посмотри|просмотри|глянь|изучи)\s+(?:видео|ролик\w*|клип\w*)"
    r"\s*(?:про|о|об|на тему)?\s*(.+)$|^\s*(?:посмотри|просмотри)\s+"
    r"(https?://\S*(?:youtube\.com|youtu\.be|rutube\.ru|vkvideo\.ru|"
    r"vk\.com/video|vk\.ru/video)\S*)\s*$", re.I)
_RT_ID = re.compile(r"rutube\.ru/(?:video|play/embed|shorts)/([0-9a-f]{32})", re.I)
_VK_ID = re.compile(r"(?:vkvideo\.ru|vk\.com|vk\.ru|m\.vk\.com)/(?:[^\s?#]*?)"
                    r"(video-?\d+_\d+)|[?&]z=(video-?\d+_\d+)", re.I)


def ref(s):
    """Ссылка/идентификатор -> (платформа, id) | ("", "")."""
    s = str(s or "")
    m = _RT_ID.search(s)
    if m:
        return "rutube", m.group(1).lower()
    m = _VK_ID.search(s)
    if m:
        return "vk", m.group(1) or m.group(2)
    if re.fullmatch(r"[0-9a-f]{32}", s.strip(), re.I):
        return "rutube", s.strip().lower()
    if re.fullmatch(r"video-?\d+_\d+", s.strip()):
        return "vk", s.strip()
    if video_id(s) or re.fullmatch(r"[A-Za-z0-9_-]{11}", s.strip()):
        return "youtube", video_id(s) or s.strip()
    return "", ""


def _get_json(url, timeout=12):
    ok, body = webfetch._fetch_bytes(url, timeout=timeout, max_bytes=3000000)
    if not ok:
        return None, str(body)[:160]
    try:
        return json.loads(body), ""
    except Exception:
        return None, "ответ не JSON"


def _rutube_subs(vid):
    """Субтитры RUTUBE (если автор их добавил) -> текст | ''."""
    d, _e = _get_json("https://rutube.ru/api/video/%s/subtitles/?format=json"
                      % vid)
    items = (d or {}).get("results") if isinstance(d, dict) else d
    for it in items or []:
        if not isinstance(it, dict):
            continue
        u = it.get("file") or it.get("url") or ""
        if not u:
            continue
        ok, body = webfetch._fetch_bytes(u, timeout=15, max_bytes=3000000)
        if ok:
            lines = [ln.strip() for ln in str(body).splitlines()
                     if ln.strip() and "-->" not in ln and
                     not re.fullmatch(r"\d+|WEBVTT.*", ln.strip())]
            txt = " ".join(re.sub(r"<[^>]+>", "", ln) for ln in lines)
            if txt:
                return txt[:120000]
    return ""


def _watch_rutube(vid):
    url = "https://rutube.ru/video/%s/" % vid
    d, err = _get_json("https://rutube.ru/api/video/%s/?format=json" % vid)
    out = {"ok": False, "url": url, "title": "", "author": "",
           "description": "", "seconds": 0, "transcript": "", "lang": "",
           "err": "", "audio_url": "", "platform": "RUTUBE"}
    if not isinstance(d, dict):
        out["err"] = "RUTUBE не ответил: %s" % (err or "пусто")
        return out
    out["title"] = str(d.get("title") or "")[:200]
    out["author"] = str((d.get("author") or {}).get("name") or "")[:100]
    out["description"] = html.unescape(re.sub(r"<[^>]+>", " ", str(
        d.get("description") or "")))[:6000]
    out["seconds"] = int(d.get("duration") or 0)
    out["transcript"] = _rutube_subs(vid)
    if out["transcript"]:
        out["lang"] = "ru"
    if not out["title"]:
        out["err"] = "страница ролика RUTUBE не разобралась"
        return out
    if not out["transcript"]:
        out["err"] = "у ролика нет субтитров — прочитала название и описание"
    out["ok"] = bool(out["transcript"] or out["description"])
    return out


def _meta(page, prop):
    m = re.search(r'<meta[^>]+(?:property|name)=["\']%s["\'][^>]+content=["\']'
                  r'([^"\']*)["\']' % re.escape(prop), page, re.I) or \
        re.search(r'<meta[^>]+content=["\']([^"\']*)["\'][^>]+(?:property|name)='
                  r'["\']%s["\']' % re.escape(prop), page, re.I)
    return html.unescape(m.group(1)).strip() if m else ""


def _watch_vk(vid):
    url = "https://vkvideo.ru/%s" % vid
    out = {"ok": False, "url": url, "title": "", "author": "",
           "description": "", "seconds": 0, "transcript": "", "lang": "",
           "err": "", "audio_url": "", "platform": "VK Видео"}
    ok, body = webfetch._fetch_bytes(url, timeout=15, max_bytes=3000000)
    if not ok:
        ok, body = webfetch._fetch_bytes("https://vk.com/%s" % vid,
                                         timeout=15, max_bytes=3000000)
    if not ok:
        out["err"] = "VK Видео не ответило: %s" % str(body)[:120]
        return out
    out["title"] = (_meta(body, "og:title") or "")[:200]
    out["description"] = (_meta(body, "og:description") or
                          _meta(body, "description"))[:6000]
    out["author"] = (_meta(body, "og:site_name") or "")[:100]
    try:
        out["seconds"] = int(_meta(body, "video:duration") or
                             _meta(body, "og:video:duration") or 0)
    except ValueError:
        out["seconds"] = 0
    if not out["title"]:
        out["err"] = "страница VK Видео не разобралась (просит вход?)"
        return out
    out["err"] = "субтитры VK Видео без входа недоступны — прочитала " \
                 "название и описание"
    out["ok"] = bool(out["description"])
    return out
LISTEN_Q = re.compile(r"^\s*(?:послушай|прослушай)\s+(?:песню|трек|музыку|"
                      r"композицию)?\s*(.+)$|^\s*включи\s+(?:песню|трек|"
                      r"музыку|композицию)\s+(.+)$", re.I)


def video_id(s):
    m = _YT_ID.search(str(s or ""))
    return m.group(1) if m else ""


def _json_after(text, key):
    """JSON-объект после «key = {» / «"key":{» на странице ролика."""
    i = text.find(key)
    if i < 0:
        return None
    j = text.find("{", i)
    if j < 0:
        return None
    depth, k, ins, esc = 0, j, False, False
    while k < len(text) and k - j < 3000000:
        c = text[k]
        if ins:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                ins = False
        elif c == '"':
            ins = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[j:k + 1])
                except Exception:
                    return None
        k += 1
    return None


def _captions_text(xml_text):
    """Субтитры (XML timedtext или srv3) -> сплошной текст."""
    parts = re.findall(r"<(?:text|p)\b[^>]*>(.*?)</(?:text|p)>",
                       str(xml_text or ""), re.S)
    out = []
    for p in parts:
        t = html.unescape(re.sub(r"<[^>]+>", "", html.unescape(p)))
        t = " ".join(t.split())
        if t and not re.fullmatch(r"\[[^\]]{1,30}\]", t):   # [музыка]
            if not out or out[-1] != t:
                out.append(t)
    return " ".join(out)


def watch(url_or_id, lang="ru"):
    """Посмотреть ролик (RUTUBE, VK Видео; YouTube — только по присланной
    ссылке). Возврат dict {ok, title, author, url, description, transcript,
    lang, err, platform}."""
    plat, rid = ref(url_or_id)
    if plat == "rutube":
        return _watch_rutube(rid)
    if plat == "vk":
        return _watch_vk(rid)
    return _watch_youtube(url_or_id, lang)


def _watch_youtube(url_or_id, lang="ru"):
    vid = video_id(url_or_id) or (url_or_id if re.fullmatch(
        r"[A-Za-z0-9_-]{11}", str(url_or_id or "")) else "")
    if not vid:
        return {"ok": False, "err": "это не ссылка на ролик YouTube"}
    url = "https://www.youtube.com/watch?v=%s&hl=ru" % vid
    ok, body = webfetch._fetch_bytes(url, timeout=15, max_bytes=3000000)
    if not ok:
        return {"ok": False, "url": url, "err": str(body)[:160]}
    pr = _json_after(body, "ytInitialPlayerResponse") or {}
    det = pr.get("videoDetails") or {}
    out = {"ok": False, "url": "https://www.youtube.com/watch?v=" + vid,
           "title": str(det.get("title") or "")[:200],
           "author": str(det.get("author") or "")[:100],
           "description": str(det.get("shortDescription") or "")[:6000],
           "seconds": int(det.get("lengthSeconds") or 0),
           "transcript": "", "lang": "", "err": ""}
    try:                                  # v3.39: звук ролика для «слуха»
        import hearing
        out["audio_url"] = hearing.audio_url(
            (pr.get("streamingData") or {}).get("adaptiveFormats"))
    except Exception:
        out["audio_url"] = ""
    tracks = (((pr.get("captions") or {}).get(
        "playerCaptionsTracklistRenderer") or {}).get("captionTracks")) or []
    tracks = [t for t in tracks if isinstance(t, dict) and t.get("baseUrl")]
    pick = None
    for want in (lang, "ru", "en"):
        pick = next((t for t in tracks
                     if str(t.get("languageCode") or "").startswith(want)),
                    None)
        if pick:
            break
    pick = pick or (tracks[0] if tracks else None)
    if pick:
        ok2, xml = webfetch._fetch_bytes(pick["baseUrl"], timeout=15,
                                         max_bytes=3000000)
        if ok2:
            out["transcript"] = _captions_text(xml)[:120000]
            out["lang"] = str(pick.get("languageCode") or "")
    if not out["title"]:
        out["err"] = "страница ролика не разобралась (YouTube мог сменить " \
                     "разметку или попросил вход)"
        return out
    if not out["transcript"]:
        out["err"] = "у ролика нет субтитров — прочитала только название " \
                     "и описание"
    out["ok"] = bool(out["transcript"] or out["description"])
    return out


def find_videos(topic, limit=3):
    """v3.125: ролики по теме — RUTUBE (открытый поиск API), затем VK Видео
    (выдача Google/Яндекс «site:vkvideo.ru …»). YouTube в поиске нет.
    Возврат [{id: ссылка, title, platform}]."""
    out = []
    d, _e = _get_json("https://rutube.ru/api/search/video/?query=%s&format=json"
                      % urllib.parse.quote(str(topic)[:100]))
    for it in ((d or {}).get("results") or [] if isinstance(d, dict) else []):
        vid = str(it.get("id") or "")
        if re.fullmatch(r"[0-9a-f]{32}", vid):
            out.append({"id": "https://rutube.ru/video/%s/" % vid,
                        "title": str(it.get("title") or "")[:160],
                        "platform": "RUTUBE"})
        if len(out) >= limit:
            break
    if len(out) < limit:
        hits = []
        for q in ("%s site:vkvideo.ru" % topic, "%s site:vk.com/video" % topic):
            try:
                hits += webfetch.engine_search(q, limit=10)
            except Exception:
                pass
            if hits:
                break
        for h in hits:
            plat, rid = ref(h.get("url"))
            if plat == "vk" and rid not in [x["id"] for x in out]:
                out.append({"id": "https://vkvideo.ru/%s" % rid,
                            "title": str(h.get("title") or "")[:160],
                            "platform": "VK Видео"})
            if len(out) >= limit:
                break
    return out


def learn_video(v, why="видео"):
    """Просмотренное -> база знаний + сеть. Возврат {ok, id, chars}."""
    import kb
    body = []
    try:                                  # v3.39: мозг СЛЫШИТ звук ролика
        if "hearing" not in v:
            import hearing
            v["hearing"] = hearing.hear_video(v)
    except Exception:
        v["hearing"] = {"ok": False, "err": "слух не сработал"}
    hr = v.get("hearing") or {}
    if hr.get("asr") and not v.get("transcript"):
        v["transcript"] = hr["asr"]       # субтитров нет — берём распознанное
        v["lang"] = "ru (на слух)"
    if v.get("author"):
        body.append("Автор: %s" % v["author"])
    if v.get("description"):
        body.append("Описание: %s" % v["description"])
    if v.get("transcript"):
        body.append("Что говорится в видео (%s): %s" % (
            "распознано на слух" if "(на слух)" in str(v.get("lang"))
            else "субтитры", v["transcript"]))
    if hr.get("ok") and hr.get("text"):
        body.append("Звук ролика (услышано): %s." % hr["text"])
    text = "\n\n".join(body)
    if hr.get("ok"):                      # мысль «услышала» -> мозг и учитель
        try:
            import store
            store.thoughts_add("облако", "🎧 Услышала ролик «%s»: %s%s" % (
                str(v.get("title") or "")[:60], hr.get("text", ""),
                " Речь распознана." if hr.get("asr") else ""), kind="hear")
        except Exception:
            pass
    if len(text) < 200:
        return {"ok": False, "error": "в ролике мало текста"}
    return kb.add_article("Видео: %s" % v.get("title", "")[:180], text,
                          source=("видео %s" % v.get("url", ""))[:120])


def _summary(v, chars=700):
    import textutils
    t = v.get("transcript") or v.get("description") or ""
    return textutils.cut_at_sentence(" ".join(t.split()), chars)


def watch_reply(arg):
    """Команда «посмотри видео <ссылка|тема>» -> (ответ, info)."""
    arg = str(arg or "").strip()
    if not ref(arg)[0] or not re.search(r"https?://|video-?\d|[0-9a-f]{32}", arg):
        vids = find_videos(arg, limit=3)
        if not vids:
            return ("Ролики про «%s» не нашла (выдача пустая или интернет "
                    "выключен)." % arg[:80]), {}
        v = None
        for c in vids:
            v = watch(c["id"])
            if v.get("ok") and (v.get("transcript") or
                                len(v.get("description") or "") >= 300):
                break
    else:
        v = watch(arg)
    if not v or not v.get("ok"):
        return ("Посмотреть не вышло: %s." % ((v or {}).get("err") or
                                             "нет данных")), {}
    r = learn_video(v)
    mins = (v.get("seconds") or 0) // 60
    head = "Посмотрела «%s»%s%s." % (
        v["title"], (" (%s)" % v["author"]) if v.get("author") else "",
        (", %d мин" % mins) if mins else "")
    how = ("Читала субтитры (%s) — %d знаков." % (v.get("lang") or "?",
                                                 len(v["transcript"]))
           if v.get("transcript") else
           "Субтитров у ролика нет — сужу только по названию и описанию.")
    hr = v.get("hearing") or {}
    if hr.get("ok"):
        how += " Звук слышала: %s." % hr.get("text", "")
        if hr.get("asr") and "(на слух)" in str(v.get("lang")):
            how += " Речь распознала на слух (субтитров не было)."
        elif hr.get("how"):
            how += " Слов не разобрала: %s." % hr["how"]
    elif hr.get("err"):
        how += " Звук не услышала: %s." % hr["err"]
    tail = ("Запомнила в базе знаний — спрашивай по содержанию."
            if r.get("ok") else "")
    return "%s %s\n\nО чём ролик: %s\n\n%s\n%s" % (
        head, how, _summary(v), v["url"], tail), {
            "video": v["url"], "sources": [{"name": v.get("platform") or
                                            "видео", "url": v["url"]}]}


def listen_reply(arg):
    """«послушай песню …»: звук не слышу — читаю о треке и текст."""
    arg = str(arg or "").strip()
    vids = [{"id": arg}] if ref(arg)[0] and re.search(
        r"https?://", arg) else find_videos(arg, limit=3)
    for c in vids:
        v = watch(c["id"])
        if v.get("ok"):
            learn_video(v)
            lyr = _summary(v, 900) if v.get("transcript") else ""
            return ("Звук я не слышу — у меня нет распознавания аудио, — но "
                    "прочитала всё о треке «%s»%s.\n\n%s%s\n\n%s" % (
                        v["title"], (" (%s)" % v["author"])
                        if v.get("author") else "",
                        ("Текст (из субтитров): %s\n\n" % lyr) if lyr else
                        "Субтитров с текстом у ролика нет.\n\n",
                        ("Описание: %s" % _summary(
                            {"description": v.get("description")}, 400))
                        if v.get("description") else "", v["url"])), \
                {"video": v["url"]}
    return ("Трек «%s» не нашла (или интернет выключен)." % arg[:80]), {}
