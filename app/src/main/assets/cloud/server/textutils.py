# -*- coding: utf-8 -*-
# ===========================================================================
# textutils.py — ИЗВЛЕЧЕНИЕ ТЕКСТА ИЗ ФАЙЛОВ (только stdlib) + хелперы
# ---------------------------------------------------------------------------
# Поддержка: txt/md/log/csv/json/html/xml — напрямую; docx (word/document.xml);
# xlsx (sharedStrings + листы); pptx (слайды); pdf — best-effort (FlateDecode
# + текстовые операции Tj/TJ + UTF-16BE hex-строки); rtf — честный разбор
# контрольных слов, \'hh (cp1251) и \uN; doc — старый бинарный Word: best-
# effort пробеги текста (utf-16-le / cp1251), zip-подменыш читается как docx.
# docx/xlsx/pptx — это zip: zipfile + regex.
# Результат идёт в KB устройства и в нейросеть материалов облака.
# ===========================================================================
import html
import io
import json
import re
import zipfile

WORD_RE = re.compile(r"[а-яёa-z0-9]{3,}", re.IGNORECASE)
SENT_RE = re.compile(r"(?<=[.!?…])\s+")

_STOP = set("""
и в во не что он на я с со как а то все она так его но да ты к у же вы за бы по
только ее мне было вот от меня еще нет о из ему теперь когда даже ну вдруг ли
если уже или ни быть был него до вас нибудь опять уж вам сказал ведь там потом
себя ничего ей может они тут где есть надо ней для мы тебя их чем была сам чтоб
без будто человек чего раз тоже себе под жизнь будет ж тогда кто этот говорил
того потому этого какой совсем ним здесь этом один почти мой тем чтобы нее
кажется сейчас были куда зачем сказать всех никогда сегодня можно при наконец
два об другой хоть после над больше тот через эти нас про всего них какая много
разве три эту моя впрочем хорошо свою этой перед иногда лучше чуть том нельзя
такой им более всегда конечно всю между это также очень еще это как-то
""".split())

_SUFFIXES = ("иями", "ями", "ами", "иях", "ях", "ах", "ов", "ев",
             "ий", "ые", "ая", "ое", "ии", "ие", "ой", "ый", "ом", "ем",
             "ых", "их", "ую", "юю", "ья", "ье", "ьи", "ть", "ла", "ло",
             "ли", "ет", "ут", "ют", "ит", "ат", "ят", "ся", "сь", "ны",
             "на", "ко", "ка", "и", "ы", "а", "я", "о", "е", "у", "ю", "ь")


def stem(w):
    """Грубый стем (согласован с мозгом и mozgnet): нейронная~нейронной~нейрон."""
    w = str(w or "").lower()
    for suf in _SUFFIXES:
        if len(w) - len(suf) >= 4 and w.endswith(suf):
            return w[:len(w) - len(suf)]
    return w


def sentences(text):
    """Разбиение на предложения (согласовано с mozgnet)."""
    return [s.strip() for s in SENT_RE.split(str(text or "")) if s.strip()]


# ------------------- v3.27: чистка текста от мусора ------------------------
# Коды полей Word, попадающие в текст вместо оглавления/ссылок:
# «PAGEREF _Toc533069919 \h 10», «HYPERLINK \l "_Toc…"», «TOC \o "1-3" \h»,
# «\* MERGEFORMAT». Номер страницы после PAGEREF — тоже мусор.
_FIELD_RES = [
    re.compile(r"PAGEREF\s+_?[A-Za-z]*\d+(?:\s*\\[a-zA-Z])*(?:\s+\d+)?"),
    re.compile(r"HYPERLINK\s+(?:\\[a-zA-Z]\s+)?\"[^\"]*\"(?:\s*\\[a-zA-Z])*"),
    re.compile(r"\bTOC\s+(?:\\[a-zA-Z](?:\s+\"[^\"]*\")?\s*)+"),
    re.compile(r"(?:STYLEREF|NOTEREF|SEQ|REF)\s+_?(?:Ref|Toc)\d+"
               r"(?:\s*\\[a-zA-Z*])*"),
    re.compile(r"\\\*\s*MERGEFORMAT"),
    re.compile(r"\b(?:PAGE|NUMPAGES)\s+\\\*\s*\w+"),
]


def strip_fields(text):
    """Убрать коды полей Word из текста (оглавления, перекрёстные ссылки)."""
    t = str(text or "")
    if not re.search(r"PAGEREF|HYPERLINK|MERGEFORMAT|\bTOC\s+\\|_Toc\d|"
                     r"_Ref\d", t):
        return t
    for rx in _FIELD_RES:
        t = rx.sub(" ", t)
    t = re.sub(r"[ \t]{2,}", " ", t)
    return re.sub(r" +\n", "\n", t)


# «Хлебные крошки» и меню сайтов: «Каталог - Выживание», «Главная › Туризм
# › Снаряжение», «Новости | Статьи | Контакты» — это не текст, а дерево сайта
_NAV_SEP = re.compile(r"\s+(?:›|»|>|/|\||→|—|–|-|·|•)\s+")


def is_nav_line(line):
    """Строка — навигация сайта (хлебные крошки/пункты меню), а не текст."""
    s = " ".join(str(line or "").split())
    if not s or len(s) > 160 or s[-1] in ".!?…:;,»\"":
        return False
    if re.search(r"[;=$&{}()\[\]~]|\b(?:sudo|echo|cd|ls|cat|grep|dnf|yum|"
                 r"systemctl)\b", s):
        return False                      # команда терминала — не меню
    parts = [p for p in _NAV_SEP.split(s) if p]
    if len(parts) < 2:
        return False
    if any(len(p.split()) > 4 for p in parts):
        return False
    # в навигации каждый пункт — с заглавной («Каталог - Выживание»);
    # «Иван Петров — директор» (строчная) — обычный текст
    if not all(p[0].isupper() or p[0].isdigit() for p in parts):
        return False
    return sum(len(p.split()) for p in parts) <= 12


def strip_nav(text, menus=True):
    """Убрать навигацию сайта: строки-крошки и (menus=True) «столбики»
    меню — 4+ подряд коротких строк без знаков препинания."""
    lines = str(text or "").split("\n")
    keep = [ln for ln in lines if not is_nav_line(ln)]
    if not menus:
        return "\n".join(keep)
    out, run = [], []

    def short(ln):
        s = ln.strip()
        return bool(s) and len(s.split()) <= 3 and s[-1] not in ".!?…:;," \
            and not re.match(r"^\s*(?:\d{1,3}[.)]|[-•*–—])\s", s)

    for ln in keep + [""]:
        if short(ln):
            run.append(ln)
            continue
        if len(run) < 4:
            out.extend(run)
        run = []
        out.append(ln)
    return "\n".join(out[:-1] if out and out[-1] == "" else out)


_SENT_END_RE = re.compile(r"[.!?…][»\")\]]*(?=\s|$)")


def cut_at_sentence(text, limit):
    """Обрезать текст не длиннее limit — но ТОЛЬКО по концу предложения
    или абзаца (раньше резали посреди фразы). Нет ни одного конца
    предложения — по последнему переводу строки, иначе по слову + «…»."""
    t = str(text or "")
    if len(t) <= limit:
        return t
    cut = t[:limit]
    ends = [m.end() for m in _SENT_END_RE.finditer(cut)]
    if ends and ends[-1] >= limit * 0.35:
        return cut[:ends[-1]].rstrip()
    nl = cut.rfind("\n")
    if nl >= limit * 0.35:
        return cut[:nl].rstrip()
    if ends:
        return cut[:ends[-1]].rstrip()
    sp = cut.rfind(" ")
    return (cut[:sp] if sp > limit * 0.5 else cut).rstrip(" ,;:-") + "…"


def keywords(text, top=8):
    """Ключевые слова текста по частоте стемов (для KB/заголовков)."""
    freq = {}
    for w in WORD_RE.findall(str(text or "").lower()):
        if w in _STOP or len(w) < 4:
            continue
        s = stem(w)
        freq[s] = freq.get(s, 0) + 1
    return [w for w, _c in sorted(freq.items(), key=lambda kv: -kv[1])[:top]]


# ------------------------------ экстракторы ---------------------------------
def _xml_text(raw):
    """Текст из XML: выкинуть теги, раскрыть сущности, склеить абзацы."""
    raw = re.sub(rb"<(script|style)[^>]*>.*?</\1>", b" ",
                 raw, flags=re.S | re.I)
    raw = re.sub(rb"</(p|div|h[1-6]|li|tr|title)>", b"\n",
                 raw, flags=re.I)
    raw = re.sub(rb"<w:p[ >]", b"\n<w:p ", raw)
    txt = re.sub(rb"<[^>]+>", b" ", raw)
    txt = html.unescape(txt.decode("utf-8", "replace"))
    txt = txt.replace("\xa0", " ")
    lines = []
    for ln in txt.splitlines():
        ln = re.sub(r"\s+", " ", ln).strip()
        if ln:
            lines.append(ln)
    out, prev = [], None
    for ln in lines:
        if ln != prev:
            out.append(ln)
        prev = ln
    return "\n".join(out)[:200000]


_W_P = re.compile(r"<w:p[ >].*?</w:p>|<w:p/>", re.S)


def _w_para_text(p):
    """Текст одного абзаца Word: прогоны <w:t>, табуляции, переносы."""
    p = re.sub(r"<w:tab/>|<w:tab [^>]*/>", "\t", p)
    p = re.sub(r"<w:br/>|<w:br [^>]*/>|<w:cr/>", "\n", p)
    out = []
    for m in re.finditer(r"<w:t(?:\s[^>]*)?>(.*?)</w:t>|(\t)|(\n)", p, re.S):
        out.append(m.group(1) if m.group(1) is not None
                   else (" " if m.group(2) else "\n"))
    return html.unescape("".join(out))


def _docx_xml_text(xml):
    """v3.27: document.xml -> текст С ТАБЛИЦАМИ. Раньше таблицы
    рассыпались на отдельные слова, а коды полей оглавления («PAGEREF
    _Toc533069919 \\h 10») попадали в текст и в ответы."""
    s = xml.decode("utf-8", "replace") if isinstance(xml, bytes) else xml
    # результат поля PAGEREF/NUMPAGES — номер страницы («Введение10»): мимо
    s = re.sub(r"(<w:instrText[^>]*>\s*(?:PAGEREF|PAGE|NUMPAGES)\b[^<]*"
               r"</w:instrText>.*?<w:fldChar w:fldCharType=\"separate\"/>)"
               r".*?(<w:fldChar w:fldCharType=\"end\"/>)", r"\1\2", s,
               flags=re.S)
    # коды полей и удалённый (правкой) текст — не текст документа
    s = re.sub(r"<w:instrText[^>]*>.*?</w:instrText>", "", s, flags=re.S)
    s = re.sub(r"<w:delText[^>]*>.*?</w:delText>", "", s, flags=re.S)
    # автоматическое оглавление (блок sdt «Table of Contents») — мимо
    s = re.sub(r"<w:sdt>(?:(?!</w:sdt>).)*?w:docPartGallery w:val=\"Table "
               r"of Contents\".*?</w:sdt>", "", s, flags=re.S)
    # таблицы: сначала самые вложенные; строка таблицы = «ячейка | ячейка»
    tbl_rx = re.compile(r"<w:tbl>(?:(?!<w:tbl>).)*?</w:tbl>", re.S)
    for _ in range(6):
        if "<w:tbl>" not in s:
            break

        def _tbl(m):
            rows = []
            for tr in re.findall(r"<w:tr[ >].*?</w:tr>", m.group(0), re.S):
                cells = []
                for tc in re.findall(r"<w:tc[ >].*?</w:tc>", tr, re.S):
                    txt = " ".join(" ".join(_w_para_text(p).split())
                                   for p in _W_P.findall(tc))
                    cells.append(txt.strip())
                if any(cells):
                    rows.append(" | ".join(cells))
            body = "".join("<w:p><w:r><w:t>%s</w:t></w:r></w:p>"
                           % html.escape(r, quote=False) for r in rows)
            return body + "<w:p/>"
        s = tbl_rx.sub(_tbl, s)
    lines = []
    for p in _W_P.findall(s):
        if re.search(r"<w:pStyle w:val=\"(?:TOC|toc|TOCHeading)\d*\"", p):
            continue                     # строки оглавления — мимо
        t = _w_para_text(p)
        t = "\n".join(" ".join(x.split()) for x in t.split("\n"))
        lines.append(t.strip())
    out, prev = [], None
    for ln in lines:                     # пустой абзац -> граница абзацев
        if not ln:
            if prev:
                out.append("")
            prev = ""
            continue
        out.append(ln)
        prev = ln
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()


def _extract_docx(raw):
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        names = z.namelist()
        parts = []
        if "word/document.xml" in names:
            parts.append(_docx_xml_text(z.read("word/document.xml")))
        for n in ("word/footnotes.xml", "word/endnotes.xml"):
            if n in names:
                parts.append(_docx_xml_text(z.read(n)))
        return "\n\n".join(p for p in parts if p)


def _extract_xlsx(raw):
    out = []
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        shared = []
        if "xl/sharedStrings.xml" in z.namelist():
            ss = z.read("xl/sharedStrings.xml").decode("utf-8", "replace")
            shared = [re.sub(r"<[^>]+>", "", m)
                      for m in re.findall(r"<si>(.*?)</si>", ss, re.S)]
        for n in sorted(z.namelist()):
            if not re.match(r"xl/worksheets/sheet\d+\.xml$", n):
                continue
            sheet = z.read(n).decode("utf-8", "replace")
            rows = []
            for row in re.findall(r"<row[^>]*>(.*?)</row>", sheet, re.S):
                cells = []
                for ctype, cval in re.findall(
                        r'<c[^>]*?(?:t="(\w+)")?[^>]*>(?:<v>([^<]*)</v>)?',
                        row):
                    if ctype == "s" and cval.isdigit() and \
                            int(cval) < len(shared):
                        cells.append(shared[int(cval)])
                    elif cval:
                        cells.append(cval)
                if cells:
                    rows.append(" | ".join(cells)[:400])
            if rows:
                out.append("\n".join(rows[:5000]))
    return "\n\n".join(out)


def _extract_pptx(raw):
    out = []
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        for n in sorted(z.namelist()):
            if re.match(r"ppt/slides/slide\d+\.xml$", n):
                t = _xml_text(z.read(n))
                if t:
                    out.append(t)
    return "\n\n".join(out)


def _extract_pdf(raw):
    """v3.27: полноценный разбор (pdftext: ToUnicode, CID-шрифты, потоки
    объектов, порядок страниц); старый best-effort — запасной. Берём тот,
    где больше букв."""
    new = ""
    try:
        import pdftext
        new = pdftext.extract(raw)
    except Exception:
        new = ""
    old = ""
    try:
        old = _extract_pdf_legacy(raw)
    except Exception:
        old = ""

    if old and _func_rate(old) < 0.01:
        old = ""                  # байты картинок скана — не текст
    return new if _good_words(new) >= _good_words(old) else old


def _func_rate(s):
    toks = re.findall(r"[А-Яа-яЁёA-Za-z]+", str(s or "").lower())
    if not toks:
        return 0.0
    return sum(1 for t in toks if t in _FUNC_WORDS) / float(len(toks))


_VOW = re.compile(r"[аеёиоуыэюяaeiouy]", re.I)
_CONS5 = re.compile(r"[бвгджзйклмнпрстфхцчшщъьbcdfghjklmnpqrstvwxz]{5,}",
                    re.I)


def _good_words(s):
    """Сколько в тексте ОСМЫСЛЕННЫХ слов (есть гласная, нет 5+ согласных
    подряд). v3.27: так выбираем лучший разбор PDF — старый разбор на
    сканах «читал» байты картинок: тысячи букв мусора вместо честного 0."""
    s = str(s or "")
    n = 0
    for w in re.findall(r"[А-Яа-яЁё]{3,}|[A-Za-z]{3,}", s):
        if _VOW.search(w) and not _CONS5.search(w):
            n += 1
    # признак ЖИВОГО языка: служебные слова («и», «в», «на», «the»…) — в
    # настоящем тексте их 10-25%, в мусоре из байтов картинок почти нет
    toks = re.findall(r"[А-Яа-яЁёA-Za-z]+", s.lower())
    if toks:
        func = sum(1 for t in toks if t in _FUNC_WORDS)
        n = int(n * min(1.0, (func / float(len(toks))) / 0.04))
    return n


# только от 2 букв: однобуквенные «в», «и», «с» часты и в случайном мусоре
_FUNC_WORDS = frozenset("""во на не что со по но за из от до об для при как
это то же или мы вы он она они его её их бы ли так все уже если только the
and of to in is for on that with as by are be or""".split())


def _extract_pdf_legacy(raw):
    """Best-effort: надуть FlateDecode-потоки, вытащить текст из Tj/TJ
    (литералы в скобках, cp1251) и hex-строк <FEFF...> (UTF-16BE — так
    пишут Word и многие генераторы)."""
    out = []
    try:
        import zlib
        for m in re.finditer(rb"stream\r?\n(.*?)endstream", raw, re.S):
            blob = m.group(1)
            try:
                data = zlib.decompress(blob)
            except Exception:
                try:
                    data = zlib.decompress(blob.rstrip(b"\r\n"))
                except Exception:
                    continue
            if b"Tj" not in data and b"TJ" not in data:
                continue
            chunks = []
            for tm in re.finditer(rb"\((?:\\.|[^\\()])*\)", data):
                s = tm.group(0)[1:-1]
                s = s.replace(b"\\(", b"(").replace(b"\\)", b")") \
                     .replace(b"\\\\", b"\\") \
                     .replace(b"\\r", b"\n").replace(b"\\n", b"\n")
                chunks.append(s.decode("cp1251", "replace"))
            if not chunks:
                # hex-строки <FEFF...> — юникод в UTF-16BE
                hexs = []
                for hm in re.finditer(rb"<([0-9A-FaF\s]+)>", data):
                    hx = re.sub(rb"\s", b"", hm.group(1))
                    if len(hx) < 4 or len(hx) % 2:
                        continue
                    try:
                        bb = bytes.fromhex(hx.decode("ascii"))
                    except Exception:
                        continue
                    if bb[:2] == b"\xfe\xff":
                        bb = bb[2:]
                    elif bb[:2] in (b"\x00\x41", b"\x00\x2f", b"\x00\x3c"):
                        pass                       # похоже на UTF-16BE и без BOM
                    else:
                        continue                   # не текст, а числа/ID
                    try:
                        t = bb.decode("utf-16-be", "replace")
                    except Exception:
                        continue
                    if sum(ch.isalpha() for ch in t) >= max(2, len(t) // 3):
                        hexs.append(t)
                chunks = hexs
            if chunks:
                out.append(" ".join(chunks))
    except Exception:
        pass
    txt = " ".join(out)
    txt = re.sub(r"\s+", " ", txt).strip()
    return txt[:150000]


# --------------------------- rtf и старый doc (v3.23) ------------------------
def _rtf_byte(m):
    r"""\'hh — байт в ansi-кодировке документа (для русских это cp1251)."""
    try:
        return bytes([int(m.group(1), 16)]).decode("cp1251")
    except Exception:
        return " "


def _rtf_uni(m):
    r"""\uN — юникод-символ (отрицательные N — обычное дело в RTF)."""
    try:
        n = int(m.group(1))
    except Exception:
        return " "
    if n < 0:
        n += 65536
    if 32 <= n < 0xFFFE:
        return chr(n)
    return " "


def _extract_rtf(raw):
    r"""RTF -> текст, только stdlib: выбрасываем служебные группы (шрифты,
    цвета, стили, картинки), переводим \'hh (cp1251) и \uN, control-слова
    в переводы строк/табуляций, остальное — пробел."""
    try:
        s = raw.decode("latin-1", "replace")
    except Exception:
        return ""
    # служебные группы-destination (один уровень вложенности — хватает)
    s = re.sub(r"\{\\(?:\*\\?)?(?:fonttbl|colortbl|stylesheet|info|pict|object"
               r"|header|footer|headerf|footerf|themedata|datastore|list"
               r"|listtable|listoverridetable|rsidtbl|generator|xmlnstbl)"
               r"(?:[^{}]|\{[^{}]*\})*\}", " ", s, flags=re.I)
    s = re.sub(r"\\'([0-9a-fA-F]{2})", _rtf_byte, s)   # байты -> cp1251
    s = re.sub(r"\\u(-?\d+)\s?\??", _rtf_uni, s)        # юникод
    s = re.sub(r"\\(?:par[d]?|sect|page)\b", "\n", s)
    s = re.sub(r"\\line\b", "\n", s)
    s = re.sub(r"\\tab\b", "\t", s)
    s = re.sub(r"\\[a-zA-Z]+-?\d*\s?", " ", s)          # прочие control-слова
    s = s.replace("{", " ").replace("}", " ")
    s = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", " ", s)
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\n[ \t]+", "\n", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()[:200000]


def _doc_runs(txt):
    """Осмысленные пробеги текста из декодированного бинарника: буквы/цифры
    с пунктуацией и пробелами, не короче 25 знаков, достаточно букв."""
    out = []
    for m in re.finditer(r"[\x20-\x7E\u0400-\u04FF\u2010-\u2026\n\r\t]{25,}",
                         txt):
        s = m.group(0)
        letters = sum(ch.isalpha() for ch in s)
        if letters >= 8 and letters / max(1, len(s)) > 0.4:
            out.append(s.strip())
    return out


def _extract_doc(raw):
    """Старый бинарный .doc (OLE2, Word 97-2003) — честный best-effort без
    сторонних библиотек: пробегаем файл как utf-16-le и cp1251, собираем
    осмысленные пробеги текста; победляет вариант с большей долей букв.
    OLE-фрагментация может рвать текст на куски — но для базы знаний это
    лучше, чем ничего (честно помечаем в ответе бота вид "doc")."""
    best, best_score = "", (-1, -1, -1)
    for enc in ("utf-16-le", "cp1251"):
        try:
            txt = raw.decode(enc, "replace")
        except Exception:
            continue
        # v3.27: таблицы Word 97-2003: \x07 — конец ячейки, \x07\x07 —
        # конец строки таблицы. Раньше пробеги рвались на каждой ячейке
        txt = txt.replace("\x07\x07", "\n").replace("\x07", " | ") \
            .replace("\r", "\n")
        cand = "\n".join(_doc_runs(txt))
        if not cand:
            continue
        # скоринг: кириллица главнее (utf-16-le для русских Word-доков),
        # затем просто буквы, затем длина; английский .doc даёт пробеги
        # только в cp1251, русский — в utf-16-le, так что не путаемся
        cyr = sum(1 for ch in cand if "а" <= ch.lower() <= "я"
                  or ch.lower() == "ё")
        letters = sum(ch.isalpha() for ch in cand)
        score = (cyr, letters, len(cand))
        if score > best_score:
            best, best_score = cand, score
    return best[:200000]


def extract_text(name, raw):
    """Текст из файла по расширению. Возврат: (текст, вид)."""
    name = str(name or "").lower()
    kind = "bin"
    text = ""
    try:
        if name.endswith((".txt", ".md", ".log", ".csv", ".tsv", ".py",
                          ".ino", ".js", ".sh", ".yaml", ".yml", ".ini",
                          ".json")):
            kind = "text"
            text = raw.decode("utf-8", "replace")
            if name.endswith(".json"):
                try:
                    text = json.dumps(json.loads(text), ensure_ascii=False,
                                      indent=1)
                except Exception:
                    pass
        elif name.endswith((".html", ".htm")):
            kind = "html"
            try:                         # v3.27: без меню/крошек сайта
                import webfetch
                text, _t = webfetch._page_parts(
                    webfetch._decode_body(raw), limit=200000)
            except Exception:
                text = _xml_text(raw)
        elif name.endswith(".xml"):
            kind = "html"
            text = _xml_text(raw)
        elif name.endswith(".docx"):
            kind = "docx"
            text = _extract_docx(raw)
        elif name.endswith(".rtf"):
            kind = "rtf"
            text = _extract_rtf(raw)
        elif name.endswith(".doc"):
            # v3.23: старый Word; заодно ловим «переименованные» docx/rtf
            kind = "doc"
            if raw[:2] == b"PK":
                kind = "docx"
                text = _extract_docx(raw)
            elif raw[:5].lower() == b"{\\rtf":
                kind = "rtf"
                text = _extract_rtf(raw)
            else:
                text = _extract_doc(raw)
        elif name.endswith(".xlsx"):
            kind = "xlsx"
            text = _extract_xlsx(raw)
        elif name.endswith(".pptx"):
            kind = "pptx"
            text = _extract_pptx(raw)
        elif name.endswith(".pdf") or raw[:5] == b"%PDF-":
            kind = "pdf"
            text = _extract_pdf(raw)
        else:
            # попытка как текст (многие «безымянные» файлы — текст)
            try:
                s = raw.decode("utf-8")
                if sum(ch.isprintable() or ch in "\n\r\t"
                       for ch in s[:2000]) > 1950:
                    kind = "text"
                    text = s
            except Exception:
                pass
    except Exception as e:
        text = (text or "") + "\n[файл не разобран: %s]" % e
    # v3.27: коды полей Word («PAGEREF _Toc… \h 10») — из любого формата;
    # обрезка — по концу предложения, а не посреди фразы
    text = strip_fields(text)
    return cut_at_sentence(text.strip(), 200000), kind


# ---------------- жирный текст для мессенджеров (v3.45) ---------------------
_BOLD_RE = re.compile(r"\*\*([^*\n]+)\*\*")


def has_bold(text):
    return bool(_BOLD_RE.search(str(text or "")))


def strip_bold(text):
    """**жирный** -> жирный (если мессенджер разметку не принял)."""
    return _BOLD_RE.sub(r"\1", str(text or ""))


def bold_html(text):
    """Текст с **жирным** -> безопасный HTML для Telegram (<b>, <i>)."""
    import html as _h
    out = []
    for ln in str(text or "").split("\n"):
        e = _h.escape(ln, quote=False)
        e = _BOLD_RE.sub(r"<b>\1</b>", e)
        m = re.match(r"^_\(?(.+?)\)?_$", e.strip())
        if m and "<" not in e:
            e = "<i>(%s)</i>" % m.group(1) if e.strip().startswith("_(") \
                else "<i>%s</i>" % m.group(1)
        out.append(e)
    return "\n".join(out)


# ===========================================================================
# v3.144: «ВОПРОС НА ВОПРОС». Скан чатов МАКС кладёт в базу знаний сообщения
# людей — среди них много ВОПРОСОВ. Такой текст — не ответ: бот, найдя его,
# отвечал вопросом на вопрос. question_only() — текст, где содержательные
# предложения почти все вопросительные; echo_of() — «ответ» повторяет сам
# вопрос.
# ===========================================================================
_Q_START = re.compile(r"^(?:как|какой|какая|какое|какие|каким|что|чем|кто|где|"
                      r"когда|почему|зачем|сколько|можно ли|можно|нужно ли|"
                      r"есть ли|подскажите|подскажи|скажите|скажи|кто-нибудь|"
                      r"кто нибудь|а\s+как|а\s+что|а\s+где|ли\b|"
                      # v3.145: ПРОСЬБА — тоже не знание («Напиши команду …»)
                      r"напиши|напишите|подскажи|подскажите|дай|дайте|покажи|"
                      r"покажите|расскажи|расскажите|объясни|объясните|найди|"
                      r"найдите|сделай|сделайте|помоги|помогите|скинь|скиньте|"
                      r"пришли|пришлите|посоветуй|посоветуйте|нужна|нужен|нужно|"
                      r"хочу|help|how)\b", re.I)


def _norm_words(text):
    return re.findall(r"[а-яёa-z0-9]+", str(text or "").lower().replace("ё", "е"))


def question_only(text):
    """Текст — вопрос(ы), а не знание: ≥ 60 % предложений вопросительные
    (знак «?» или вопросное начало без точки в конце) и нет длинных
    повествовательных предложений. Пустое — тоже не знание."""
    t = re.sub(r"\s+", " ", str(text or "")).strip()
    if not t:
        return True
    parts = [p.strip() for p in re.split(r"(?<=[.!?…])\s+|\n+", t) if p.strip()]
    if not parts:
        return True
    q = 0
    long_decl = 0
    for p in parts:
        is_q = p.endswith("?") or p.endswith("?»") or \
            (bool(_Q_START.match(p)) and not p.endswith((".", "!", "…")))
        if is_q:
            q += 1
        elif len(_norm_words(p)) >= 8:
            long_decl += 1
    return q > 0 and q / float(len(parts)) >= 0.6 and long_decl == 0


def echo_of(question, answer):
    """«Ответ» начинается с того же вопроса или почти совпадает с ним."""
    qw, aw = _norm_words(question), _norm_words(answer)
    if not qw or not aw:
        return False
    n = min(len(qw), 8)
    if aw[:n] == qw[:n]:
        return True
    sq, sa = set(qw), set(aw[:max(12, len(qw) + 4)])
    return len(sa) <= len(sq) + 4 and len(sq & sa) / float(len(sq | sa)) >= 0.7


def not_an_answer(question, answer):
    """Это не ответ: вопрос вместо знания или эхо вопроса."""
    return question_only(answer) or echo_of(question, answer)
