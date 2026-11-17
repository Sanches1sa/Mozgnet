# -*- coding: utf-8 -*-
"""
kbimages.py — v3.124: ИЛЛЮСТРАЦИИ И ФОРМУЛЫ-КАРТИНКИ СТРАНИЦ В БАЗЕ ЗНАНИЙ.

Раньше при чтении страницы (инструкции, обход сайта) из HTML оставался
только текст: <img> выбрасывались, а формулы, нарисованные картинкой или
MathJax-скриптом, пропадали. В базе оставалась одна подпись («Вылетающее при
распаде антинейтрино не показано»), а сама схема реакции — нет.

Теперь (webfetch._page_parts):
  • формула MathJax (<script type="math/tex">), KaTeX (<annotation
    encoding="application/x-tex">), картинка-формула (alt с TeX) -> $…$;
  • содержательная картинка -> метка
      [ИЛЛЮСТРАЦИЯ src=<адрес> url=<адрес>]подпись[/ИЛЛЮСТРАЦИЯ]
    (значки, логотипы, счётчики — нет);
  • при сохранении в базу (study) картинки СКАЧИВАЮТСЯ в данные облака
    (kbimg/), метка получает src=kbimg:<файл> — показ работает и без сети.
В ответе из статьи (brain) к тексту добавляются иллюстрации, стоящие рядом
с выбранными предложениями или подходящие к вопросу («покажи», «схема»,
«формул…»): веб-чат рисует их картинкой, МАКС шлёт фото, Telegram —
подпись со ссылкой.
"""
import hashlib
import os
import re

import store

MARK_RE = re.compile(r"\[ИЛЛЮСТРАЦИЯ src=(\S+?)(?: url=(\S+?))?\]([\s\S]*?)"
                     r"\[/ИЛЛЮСТРАЦИЯ\]")
MAX_PER_PAGE = 12
MAX_BYTES = 3 * 1024 * 1024
_EXT = {"image/png": "png", "image/jpeg": "jpg", "image/jpg": "jpg",
        "image/gif": "gif", "image/webp": "webp", "image/svg+xml": "svg"}
_CT = {v: k for k, v in _EXT.items()}
_CT["jpg"] = "image/jpeg"


def _dir():
    d = store.p("kbimg")
    os.makedirs(d, exist_ok=True)
    return d


def marker(src, alt="", url=""):
    alt = " ".join(str(alt or "").replace("[", "(").replace("]", ")").split())
    return "\n[ИЛЛЮСТРАЦИЯ src=%s%s]%s[/ИЛЛЮСТРАЦИЯ]\n" % (
        src, (" url=%s" % url) if url and url != src else "", alt[:200])


def _sniff(data, ctype=""):
    ct = str(ctype or "").split(";")[0].strip().lower()
    if ct in _EXT:
        return _EXT[ct]
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if data[:3] == b"\xff\xd8\xff":
        return "jpg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    if b"<svg" in data[:400]:
        return "svg"
    return ""


def localize(text):
    """Скачать картинки меток статьи в kbimg/ (src=kbimg:<файл>).
    Не скачалась — метка остаётся с адресом в интернете."""
    n = [0]

    def _one(m):
        src, url, alt = m.group(1), m.group(2) or m.group(1), m.group(3)
        if src.startswith("kbimg:") or n[0] >= MAX_PER_PAGE:
            return m.group(0)
        n[0] += 1
        try:
            import webfetch
            ok, data, _fn, ctype = webfetch.fetch_file(src, timeout=15,
                                                       max_bytes=MAX_BYTES)
            if not ok or not data or len(data) < 200:
                return m.group(0)
            ext = _sniff(data, ctype)
            if not ext:
                return m.group(0)
            name = hashlib.sha1(data).hexdigest()[:20] + "." + ext
            fp = os.path.join(_dir(), name)
            if not os.path.isfile(fp):
                with open(fp, "wb") as f:
                    f.write(data)
            return marker("kbimg:" + name, alt, url).strip("\n")
        except Exception:
            return m.group(0)
    return MARK_RE.sub(_one, str(text or ""))


def read(name):
    """kbimg:<файл> -> (bytes, content-type) | (None, '')."""
    name = os.path.basename(str(name or "").replace("kbimg:", ""))
    if not re.match(r"^[0-9a-f]{8,40}\.(png|jpg|gif|webp|svg)$", name):
        return None, ""
    fp = os.path.join(_dir(), name)
    if not os.path.isfile(fp):
        return None, ""
    with open(fp, "rb") as f:
        return f.read(), _CT.get(name.rsplit(".", 1)[1], "image/png")


def strip(text):
    """Текст статьи без меток (для поиска, обучения, предложений)."""
    return MARK_RE.sub(lambda m: (" " + m.group(3).strip() + " ")
                       if m.group(3).strip() else " ", str(text or ""))


def plain(text):
    """Метки -> строка «🖼 подпись (ссылка)» (Telegram и прочий текст)."""
    def _one(m):
        alt = m.group(3).strip() or "иллюстрация"
        url = m.group(2) or (m.group(1) if m.group(1).startswith("http")
                             else "")
        return "🖼 %s%s" % (alt, (" — %s" % url) if url else "")
    return MARK_RE.sub(_one, str(text or ""))


def items(text):
    """[(src, url, alt)] всех меток текста."""
    return [(m.group(1), m.group(2) or "", m.group(3).strip())
            for m in MARK_RE.finditer(str(text or ""))]


_ASK_RE = re.compile(r"покаж|схем|рисун|иллюстр|картин|изобра|формул|"
                     r"уравнени|реакци|график|диаграм|как выгляд", re.I)


def _stems(t):
    return set(w[:5] for w in re.findall(r"[а-яёa-z0-9]{4,}",
                                           str(t or "").lower()))


def for_answer(article_text, answer, question, k=2):
    """Иллюстрации статьи, подходящие к ответу: метка рядом с выбранными
    предложениями (±400 знаков) или по словам подписи; вопрос «покажи /
    схема / формулой» — берём ближайшие к ответу даже без совпадения."""
    art = str(article_text or "")
    if "[ИЛЛЮСТРАЦИЯ" not in art:
        return []
    ans_st = _stems(answer) | _stems(question)
    want = bool(_ASK_RE.search(str(question or "")) or
                _ASK_RE.search(str(answer or "")))
    # где в статье стоят предложения ответа
    pos = []
    for sn in re.split(r"(?<=[.!?…])\s+", str(answer or "")):
        sn = sn.strip()
        if len(sn) >= 25:
            i = art.find(sn[:40])
            if i >= 0:
                pos.append(i)
    scored = []
    for m in MARK_RE.finditer(art):
        near = art[max(0, m.start() - 400):m.end() + 400]
        sc = 0.0
        if pos and min(abs(m.start() - p) for p in pos) <= 600:
            sc += 2.0
        sc += 0.5 * len(_stems(m.group(3)) & ans_st)
        sc += 0.2 * len(_stems(strip(near)) & ans_st)
        if sc >= 1.0 or (want and sc > 0):
            scored.append((sc, m.start(), m.group(0)))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return [g for _s, _p, g in scored[:k]]


def extract(text):
    """Текст с метками -> (чистый текст, [{src, url, alt, at}]) — at: позиция
    в чистом тексте, где стояла картинка (рядом — её предложения)."""
    out, imgs, last = [], [], 0
    t = str(text or "")
    pos = 0
    for m in MARK_RE.finditer(t):
        chunk = t[last:m.start()]
        out.append(chunk)
        pos += len(chunk)
        imgs.append({"src": m.group(1), "url": m.group(2) or (
            m.group(1) if m.group(1).startswith("http") else ""),
            "alt": m.group(3).strip()[:200], "at": pos})
        last = m.end()
    out.append(t[last:])
    clean = re.sub(r"\n{3,}", "\n\n", "".join(out))
    return clean, imgs[:MAX_PER_PAGE * 2]


def for_article(art, answer, question, k=2):
    """Иллюстрации статьи (поле images), подходящие к ответу -> [метка]."""
    imgs = (art or {}).get("images") or []
    if not imgs:
        return []
    text = str(art.get("text") or "")
    ans_st = _stems(answer) | _stems(question)
    want = bool(_ASK_RE.search(str(question or "")) or
                _ASK_RE.search(str(answer or "")))
    pos = []
    for sn in re.split(r"(?<=[.!?…])\s+", str(answer or "")):
        sn = sn.strip()
        if len(sn) >= 25:
            i = text.find(sn[:40])
            if i >= 0:
                pos.append(i)
    scored = []
    for im in imgs:
        at = int(im.get("at") or 0)
        near = text[max(0, at - 400):at + 400]
        sc = 0.0
        if pos and min(abs(at - p) for p in pos) <= 700:
            sc += 2.0
        sc += 0.5 * len(_stems(im.get("alt")) & ans_st)
        sc += 0.2 * len(_stems(near) & ans_st)
        if sc >= 1.0 or (want and sc > 0):
            scored.append((sc, at, im))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return [marker(im["src"], im.get("alt"), im.get("url")).strip("\n")
            for _s, _a, im in scored[:k]]
