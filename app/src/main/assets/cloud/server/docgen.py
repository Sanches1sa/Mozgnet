# -*- coding: utf-8 -*-
# ===========================================================================
# docgen.py — ИИ СОЗДАЁТ ДОКУМЕНТЫ И ДАЁТ ИХ СКАЧАТЬ (v3.27, только stdlib)
# ---------------------------------------------------------------------------
# Заявка владельца: «надо научить ИИ создавать документы и предлагать к
# скачиванию в браузерной версии».
#   «создай документ про перенос записи пациента»   -> DOCX по теме
#   «сделай отчёт в txt о чёрных дырах»             -> TXT
#   «сохрани ответ в документ» / «… в html»          -> последний ответ
#   «сохрани наш разговор в файл»                   -> весь диалог
# Текст документа собирает сам мозг (база знаний, интернет — как в чате),
# здесь — только оформление: заголовок, абзацы, списки, ТАБЛИЦЫ (строки
# «ячейка | ячейка» становятся настоящей таблицей Word). DOCX собирается
# вручную (zipfile + OOXML), без сторонних библиотек.
# Файлы — data/files/документы/, скачивание — /api/files/get/<путь>.
# ===========================================================================
import io
import os
import re
import time
import zipfile
from xml.sax.saxutils import escape as _x

import store

DIR = "документы"
FORMATS = {"docx": "DOCX (Word)", "txt": "TXT", "html": "HTML",
           "md": "Markdown", "csv": "CSV (таблица)"}

_FMT_WORDS = [("docx", r"docx|ворд|word|вордовск"), ("txt", r"\btxt\b|текстов"),
              ("html", r"html|веб-?страниц|страниц[уа]"),
              ("md", r"\bmd\b|markdown|маркдаун"),
              ("csv", r"\bcsv\b|эксел|excel|таблиц[уыа]\s+csv")]

DOC_Q = re.compile(
    r"^\s*(?:пожалуйста[,\s]+)?(?:создай|сделай|составь|сформируй|подготовь|"
    r"оформи|сгенерируй|напиши|собери)\s+(?:мне\s+)?(?:(?:новый|новую)\s+)?"
    r"(?P<kind>документ\w*|файл\w*|отч[её]т\w*|docx|ворд|word|txt|html|csv|"
    r"памятк\w*|справк\w*|инструкци\w*\s+(?:в|файлом|документом)\b[^\n]*?)"
    r"(?P<rest>.*)$", re.I)
SAVE_Q = re.compile(
    r"^\s*(?:сохрани|запиши|выгрузи|экспортируй|скачай|дай\s+скачать|"
    r"сделай|оформи)\s+(?:мне\s+)?(?P<what>это|этот\s+ответ|ответ|"
    r"последн\w+\s+ответ|твой\s+ответ|наш\w*\s+(?:диалог|разговор)|"
    r"(?:весь\s+)?(?:диалог|разговор)|переписк\w*|всё\s+это)\b(?P<rest>.*)$",
    re.I)


def detect(text):
    """'save' | 'make' | None."""
    t = str(text or "")
    if SAVE_Q.match(t) and re.search(r"документ|файл|docx|ворд|word|txt|"
                                     r"html|csv|\bmd\b|скача", t, re.I):
        return "save"
    if DOC_Q.match(t):
        return "make"
    return None


def pick_format(text):
    low = str(text or "").lower()
    for fmt, rx in _FMT_WORDS:
        if re.search(rx, low):
            return fmt
    return "docx"


def topic_of(text):
    """«создай документ про перенос записи в docx» -> «перенос записи»."""
    m = DOC_Q.match(str(text or ""))
    rest = (m.group("rest") if m else str(text or "")).strip()
    rest = re.sub(r"\b(?:в|во|как)\s+(?:формате\s+)?(?:docx|ворд\w*|word|txt|"
                  r"html|csv|md|markdown|текстов\w*\s+файл\w*)\b", " ", rest,
                  flags=re.I)
    rest = re.sub(r"^\s*(?:по\s+теме|на\s+тему|про|о|об|обо|по|для|:|—|-)\s+",
                  "", rest.strip(), flags=re.I)
    return " ".join(rest.split()).strip(" .,:;!?")[:150]


def title_from_question(q):
    """«что ты знаешь про Антарктиду?» -> «Антарктида» (тема вопроса)."""
    t = " ".join(str(q or "").split()).strip(" ?!.")
    t = re.sub(r"^(?:а\s+)?(?:что\s+(?:ты\s+|вы\s+)?знаешь\s+(?:про|о|об)|"
               r"расскажи(?:те)?\s+(?:мне\s+)?(?:про|о|об)?|что\s+так(?:ое|ой|"
               r"ая)|кто\s+так(?:ой|ая)|объясни(?:те)?|опиши(?:те)?|"
               r"подскажи(?:те)?(?:\s+пожалуйста)?|как|какая|какой|какие|"
               r"сколько|где|когда|почему|зачем)\s+", "", t, flags=re.I)
    t = t.strip(" ,")
    if not t:
        return ""
    # «про Антарктиду» -> «Антарктида» (простая начальная форма)
    t = re.sub(r"(?<=[а-яё])у$", "а", t) if len(t.split()) == 1 else t
    return (t[:1].upper() + t[1:])[:100]


# ------------------------------ чистка текста --------------------------------
def clean_body(text):
    """Ответ чата -> текст документа: без SVG, служебных пометок и разметки."""
    t = re.sub(r"\[(ФОРМУЛА|КАРТИНКА)\][\s\S]*?\[/\1\]", " ", str(text or ""))
    t = re.sub(r"```[\w+#-]*\n?", "", t)
    t = re.sub(r"\*\*([^*\n]+)\*\*", r"\1", t)
    t = re.sub(r"(^|\n)_(Источник[^_\n]*)_", r"\1\2", t)
    t = re.sub(r"\[([^\]]+)\]\((?:https?:|/)[^)\s]+\)", r"\1", t)
    return re.sub(r"\n{3,}", "\n\n", t).strip()


def _blocks(text):
    """Текст -> блоки: ("p", строка) | ("li", строка) | ("tbl", [[ячейки]])."""
    out, rows = [], []
    for ln in str(text or "").split("\n"):
        s = ln.strip()
        if s.count("|") >= 1 and len([c for c in s.split("|")
                                      if c.strip()]) >= 2:
            rows.append([c.strip() for c in s.strip("|").split("|")])
            continue
        if rows:
            out.append(("tbl", rows))
            rows = []
        if not s:
            out.append(("gap", ""))
        elif re.match(r"^(?:[-•*–—]|\d{1,3}[.)])\s+", s):
            out.append(("li", s))
        else:
            out.append(("p", s))
    if rows:
        out.append(("tbl", rows))
    return out


# --------------------------------- DOCX --------------------------------------
_CT = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
       '<Types xmlns="http://schemas.openxmlformats.org/package/2006/'
       'content-types"><Default Extension="rels" ContentType="application/'
       'vnd.openxmlformats-package.relationships+xml"/><Default Extension='
       '"xml" ContentType="application/xml"/><Override PartName="/word/'
       'document.xml" ContentType="application/vnd.openxmlformats-'
       'officedocument.wordprocessingml.document.main+xml"/><Override '
       'PartName="/word/styles.xml" ContentType="application/vnd.'
       'openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
       '<Override PartName="/docProps/core.xml" ContentType="application/'
       'vnd.openxmlformats-package.core-properties+xml"/></Types>')
_RELS = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
         '<Relationships xmlns="http://schemas.openxmlformats.org/package/'
         '2006/relationships"><Relationship Id="rId1" Type="http://schemas.'
         'openxmlformats.org/officeDocument/2006/relationships/'
         'officeDocument" Target="word/document.xml"/><Relationship Id="rId2"'
         ' Type="http://schemas.openxmlformats.org/package/2006/relationships'
         '/metadata/core-properties" Target="docProps/core.xml"/>'
         '</Relationships>')
_DOC_RELS = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
             '<Relationships xmlns="http://schemas.openxmlformats.org/package/'
             '2006/relationships"><Relationship Id="rId1" Type="http://'
             'schemas.openxmlformats.org/officeDocument/2006/relationships/'
             'styles" Target="styles.xml"/></Relationships>')
_W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
_STYLES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:styles %s>'
    '<w:docDefaults><w:rPrDefault><w:rPr><w:rFonts w:ascii="Calibri" '
    'w:hAnsi="Calibri" w:cs="Calibri" w:eastAsia="Calibri"/><w:sz w:val="24"/>'
    '<w:lang w:val="ru-RU"/></w:rPr></w:rPrDefault><w:pPrDefault><w:pPr>'
    '<w:spacing w:after="120" w:line="276" w:lineRule="auto"/></w:pPr>'
    '</w:pPrDefault></w:docDefaults>'
    '<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name '
    'w:val="Normal"/></w:style>'
    '<w:style w:type="paragraph" w:styleId="Title"><w:name w:val="Title"/>'
    '<w:basedOn w:val="Normal"/><w:pPr><w:spacing w:after="240"/></w:pPr>'
    '<w:rPr><w:b/><w:sz w:val="36"/></w:rPr></w:style>'
    '<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"'
    '/><w:basedOn w:val="Normal"/><w:pPr><w:spacing w:before="240"/></w:pPr>'
    '<w:rPr><w:b/><w:sz w:val="28"/></w:rPr></w:style>'
    '<w:style w:type="table" w:styleId="TableGrid"><w:name w:val="Table Grid"'
    '/><w:tblPr><w:tblBorders><w:top w:val="single" w:sz="4"/><w:left '
    'w:val="single" w:sz="4"/><w:bottom w:val="single" w:sz="4"/><w:right '
    'w:val="single" w:sz="4"/><w:insideH w:val="single" w:sz="4"/><w:insideV '
    'w:val="single" w:sz="4"/></w:tblBorders></w:tblPr></w:style>'
    '</w:styles>' % _W)


def _wp(text, style=None, bold=False):
    ps = '<w:pPr><w:pStyle w:val="%s"/></w:pPr>' % style if style else ""
    rp = "<w:rPr><w:b/></w:rPr>" if bold else ""
    runs = []
    for i, part in enumerate(str(text).split("\t")):
        if i:
            runs.append("<w:r><w:tab/></w:r>")
        runs.append('<w:r>%s<w:t xml:space="preserve">%s</w:t></w:r>'
                    % (rp, _x(part)))
    return "<w:p>%s%s</w:p>" % (ps, "".join(runs))


def _wtbl(rows):
    n = max(len(r) for r in rows)
    out = ['<w:tbl><w:tblPr><w:tblStyle w:val="TableGrid"/><w:tblW w:w="0" '
           'w:type="auto"/></w:tblPr><w:tblGrid>%s</w:tblGrid>'
           % ('<w:gridCol w:w="%d"/>' % (9000 // n) * n)]
    for ri, r in enumerate(rows):
        out.append("<w:tr>")
        for ci in range(n):
            cell = r[ci] if ci < len(r) else ""
            out.append('<w:tc><w:tcPr><w:tcW w:w="%d" w:type="dxa"/>'
                       '</w:tcPr>%s</w:tc>' % (9000 // n,
                                              _wp(cell, bold=(ri == 0))))
        out.append("</w:tr>")
    out.append("</w:tbl>")
    return "".join(out)


def build_docx(title, text):
    body = [_wp(title, "Title")]
    for kind, val in _blocks(text):
        if kind == "tbl":
            body.append(_wtbl(val))
            body.append("<w:p/>")
        elif kind == "li":
            # нумерованный пункт остаётся «1. …», маркированный — «• …»
            body.append(_wp(val if re.match(r"^\d{1,3}[.)]\s", val) else
                            "• " + re.sub(r"^(?:[-•*–—])\s+", "", val)))
        elif kind == "p":
            head = len(body) > 1 and _looks_head(val) and \
                not val[:1].islower() and not val.endswith(":")
            body.append(_wp(val, "Heading1") if head else _wp(val))
    doc = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
           '<w:document %s><w:body>%s<w:sectPr><w:pgSz w:w="11906" '
           'w:h="16838"/><w:pgMar w:top="1134" w:right="850" w:bottom="1134"'
           ' w:left="1701" w:header="708" w:footer="708" w:gutter="0"/>'
           '</w:sectPr></w:body></w:document>' % (_W, "".join(body)))
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    core = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/'
            'package/2006/metadata/core-properties" xmlns:dc="http://purl.org/'
            'dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" '
            'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"><dc:title>'
            '%s</dc:title><dc:creator>мозг-12 (neuro12-cloud)</dc:creator>'
            '<dcterms:created xsi:type="dcterms:W3CDTF">%s</dcterms:created>'
            '</cp:coreProperties>' % (_x(title), now))
    bio = io.BytesIO()
    with zipfile.ZipFile(bio, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", _CT)
        z.writestr("_rels/.rels", _RELS)
        z.writestr("word/_rels/document.xml.rels", _DOC_RELS)
        z.writestr("word/document.xml", doc)
        z.writestr("word/styles.xml", _STYLES)
        z.writestr("docProps/core.xml", core)
    return bio.getvalue()


def _looks_head(s):
    """Подзаголовок: короткая строка без точки, за которой текст."""
    return len(s.split()) <= 8 and not re.search(r"[.!?…,;]$", s) and \
        not re.search(r"\d{3,}|[=/\\]", s)


def build_html(title, text):
    parts = ["<!doctype html><html lang=\"ru\"><head><meta charset=\"utf-8\">"
             "<title>%s</title><style>body{font:16px/1.5 system-ui,sans-serif;"
             "max-width:820px;margin:32px auto;padding:0 16px}table{border-"
             "collapse:collapse}td,th{border:1px solid #999;padding:4px 8px}"
             "</style></head><body><h1>%s</h1>" % (_x(title), _x(title))]
    in_ul = False
    for kind, val in _blocks(text):
        if kind != "li" and in_ul:
            parts.append("</ul>")
            in_ul = False
        if kind == "tbl":
            parts.append("<table>")
            for ri, r in enumerate(val):
                tag = "th" if ri == 0 else "td"
                parts.append("<tr>%s</tr>" % "".join(
                    "<%s>%s</%s>" % (tag, _x(c), tag) for c in r))
            parts.append("</table>")
        elif kind == "li":
            if not in_ul:
                parts.append("<ul>")
                in_ul = True
            parts.append("<li>%s</li>" % _x(re.sub(r"^(?:[-•*–—])\s+", "",
                                                   val)))
        elif kind == "p":
            parts.append("<p>%s</p>" % _x(val))
    if in_ul:
        parts.append("</ul>")
    parts.append("</body></html>")
    return "\n".join(parts).encode("utf-8")


def build_csv(title, text):
    import csv
    rows = []
    for kind, val in _blocks(text):
        if kind == "tbl":
            rows.extend(val)
    if not rows:                         # таблицы нет — строки текста
        rows = [[ln] for ln in str(text).split("\n") if ln.strip()]
    sio = io.StringIO()
    w = csv.writer(sio, delimiter=";")
    for r in rows:
        w.writerow(r)
    return ("\ufeff" + sio.getvalue()).encode("utf-8")   # BOM — для Excel


def _safe_name(title):
    s = re.sub(r"[^\w\s\-]", "", str(title or "документ"), flags=re.U)
    s = re.sub(r"\s+", "_", s.strip())[:60] or "документ"
    return s


def make_file(title, text, fmt="docx"):
    """Собрать и сохранить файл. Возврат: {ok, name, rel, url, size, fmt}."""
    title = " ".join(str(title or "Документ").split())[:120] or "Документ"
    text = clean_body(text)
    if len(text) < 3:
        return {"ok": False, "error": "нечего сохранять — текст пустой"}
    fmt = fmt if fmt in FORMATS else "docx"
    if fmt == "docx":
        data = build_docx(title, text)
    elif fmt == "html":
        data = build_html(title, text)
    elif fmt == "csv":
        data = build_csv(title, text)
    elif fmt == "md":
        data = ("# %s\n\n%s\n" % (title, text)).encode("utf-8")
    else:
        data = ("%s\n%s\n\n%s\n" % (title, "=" * min(60, len(title)),
                                    text)).encode("utf-8")
    name = "%s_%s.%s" % (_safe_name(title), time.strftime("%Y%m%d-%H%M%S"),
                         fmt)
    os.makedirs(store.p("files", DIR), exist_ok=True)
    with open(store.p("files", DIR, name), "wb") as f:
        f.write(data)
    rel = DIR + "/" + name
    import urllib.parse
    url = "/api/files/get/" + urllib.parse.quote(rel)
    store.log("ДОКУМЕНТЫ", "создан %s (%d байт)" % (rel, len(data)))
    return {"ok": True, "name": name, "rel": rel, "url": url,
            "size": len(data), "fmt": fmt, "chars": len(text)}


def reply_for(res, title, text):
    """Ответ в чат: ссылка на скачивание + начало текста документа."""
    if not res.get("ok"):
        return "Документ не создан: %s." % res.get("error")
    head = clean_body(text)
    try:
        import textutils
        head = textutils.cut_at_sentence(head, 500)
    except Exception:
        head = head[:500]
    kb = max(1, res["size"] // 1024)
    return ("Готово: документ «%s» (%s, %d КБ, %d знаков).\n"
            "[⬇ Скачать %s](%s)\n\nНачало документа:\n%s\n\n"
            "Другой формат: «сохрани ответ в txt / html / csv / docx»."
            % (title, FORMATS[res["fmt"]], kb, res["chars"], res["name"],
               res["url"], head))
