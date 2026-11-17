# -*- coding: utf-8 -*-
# ===========================================================================
# pdftext.py — ТЕКСТ ИЗ PDF (v3.27, только stdlib)
# ---------------------------------------------------------------------------
# Заявка владельца: «ПДФ файлы не читает, пишет 0 знаков». Прежний разбор
# понимал только простые шрифты: большинство PDF из Word/госсистем пишут
# кириллицу шрифтами CID (2 байта на знак) и переводят коды в буквы через
# таблицу ToUnicode, а сами объекты хранят в сжатых «потоках объектов»
# (PDF 1.5+) — старый разбор их не видел вовсе. Здесь:
#   * объекты N 0 obj … endobj и потоки объектов /ObjStm;
#   * фильтры FlateDecode (+ PNG-предикторы), ASCII85, ASCIIHex, LZW;
#   * шрифты: ToUnicode (bfchar/bfrange), /Differences с именами глифов
#     (кириллица afii10017…, uniXXXX), WinAnsi/cp1251 для простых;
#   * страницы по дереву /Pages (порядок чтения), наследование ресурсов,
#     вложенные формы (Do), операторы Tj TJ ' " Td TD T* Tm Tf BT ET;
#   * переводы строк по смещению строки, пробелы — по разрывам в TJ.
# Скан без текстового слоя честно даёт пустую строку (текста там нет).
# Зашифрованные PDF (/Encrypt) — пустая строка + is_encrypted() = True.
# ===========================================================================
import base64
import re
import zlib

_OBJ_RE = re.compile(rb"(\d+)\s+(\d+)\s+obj\b(.*?)\bendobj", re.S)
_REF_RE = re.compile(rb"(\d+)\s+\d+\s+R\b")


# ------------------------------ фильтры -------------------------------------
def _png_unpredict(data, columns, colors=1, bpc=8):
    bpp = max(1, colors * bpc // 8)
    row = columns * bpp
    out, prev = bytearray(), bytearray(row)
    i = 0
    while i + 1 + row <= len(data):
        ft = data[i]
        cur = bytearray(data[i + 1:i + 1 + row])
        for k in range(row):
            a = cur[k - bpp] if k >= bpp else 0
            b = prev[k]
            c = prev[k - bpp] if k >= bpp else 0
            if ft == 1:
                cur[k] = (cur[k] + a) & 255
            elif ft == 2:
                cur[k] = (cur[k] + b) & 255
            elif ft == 3:
                cur[k] = (cur[k] + (a + b) // 2) & 255
            elif ft == 4:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pr = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                cur[k] = (cur[k] + pr) & 255
        out += cur
        prev = cur
        i += 1 + row
    return bytes(out)


def _lzw(data):
    out, table = bytearray(), [bytes([i]) for i in range(256)] + [b"", b""]
    bits, buf, nbits, prev = 9, 0, 0, None
    for byte in data:
        buf = (buf << 8) | byte
        nbits += 8
        while nbits >= bits:
            nbits -= bits
            code = (buf >> nbits) & ((1 << bits) - 1)
            if code == 256:
                table = table[:258]
                bits, prev = 9, None
                continue
            if code == 257:
                return bytes(out)
            if code < len(table):
                entry = table[code]
                if prev is not None:
                    table.append(prev + entry[:1])
            elif prev is not None:
                entry = prev + prev[:1]
                table.append(entry)
            else:
                return bytes(out)
            out += entry
            prev = entry
            if len(table) + 1 >= (1 << bits) and bits < 12:
                bits += 1
    return bytes(out)


def _inflate(data):
    try:
        return zlib.decompress(data)
    except Exception:
        pass
    try:                                   # обрезанный/с мусором в конце поток
        d = zlib.decompressobj()
        return d.decompress(data)
    except Exception:
        return b""


def decode_stream(d, data):
    """Раскодировать поток по его словарю d (цепочка фильтров)."""
    if data is None:
        return b""
    filters = re.findall(rb"/(FlateDecode|Fl|ASCII85Decode|A85|"
                         rb"ASCIIHexDecode|AHx|LZWDecode|LZW|DCTDecode|"
                         rb"JPXDecode|CCITTFaxDecode|JBIG2Decode)\b", d)
    for f in filters:
        if f in (b"FlateDecode", b"Fl"):
            data = _inflate(data)
        elif f in (b"ASCII85Decode", b"A85"):
            s = data.strip()
            if s.startswith(b"<~"):
                s = s[2:]
            s = s.split(b"~>")[0]
            try:
                data = base64.a85decode(re.sub(rb"\s", b"", s))
            except Exception:
                return b""
        elif f in (b"ASCIIHexDecode", b"AHx"):
            h = re.sub(rb"[^0-9A-Fa-f]", b"", data.split(b">")[0])
            if len(h) % 2:
                h += b"0"
            data = bytes.fromhex(h.decode("ascii"))
        elif f in (b"LZWDecode", b"LZW"):
            data = _lzw(data)
        else:
            return b""                    # картинки — не текст
    m = re.search(rb"/Predictor\s+(\d+)", d)
    if m and int(m.group(1)) >= 10:
        cm = re.search(rb"/Columns\s+(\d+)", d)
        co = re.search(rb"/Colors\s+(\d+)", d)
        data = _png_unpredict(data, int(cm.group(1)) if cm else 1,
                              int(co.group(1)) if co else 1)
    return data


# ------------------------------ объекты -------------------------------------
class _Doc(object):
    def __init__(self, raw):
        self.raw = raw
        self.objs = {}                      # num -> (dict_bytes, stream|None)
        lens = []
        for m in _OBJ_RE.finditer(raw):
            num, body = int(m.group(1)), m.group(3)
            d, stream = body, None
            sm = re.search(rb"stream\r?\n", body)
            if sm and b"endstream" in body[sm.end():]:
                d = body[:sm.start()]
                data = body[sm.end():]
                e = data.rfind(b"endstream")
                stream = data[:e].rstrip(b"\r\n") if e >= 0 else data
                # \b после числа: иначе «/Length 32 0 R» читалось как 3
                lm = re.search(rb"/Length\s+(\d+)\b(?!\s+\d+\s+R)", d)
                if lm and int(lm.group(1)) <= len(data):
                    stream = data[:int(lm.group(1))]
                else:
                    lr = re.search(rb"/Length\s+(\d+)\s+\d+\s+R", d)
                    if lr:
                        lens.append((num, int(lr.group(1)), data))
            self.objs[num] = (d, stream)
        for num, lnum, data in lens:         # длина — отдельным объектом
            mv = re.match(rb"\s*(\d+)\s*$", self.objs.get(lnum, (b"",))[0])
            if mv and int(mv.group(1)) <= len(data):
                self.objs[num] = (self.objs[num][0], data[:int(mv.group(1))])
        for num, (d, st) in list(self.objs.items()):
            if st is not None and re.search(rb"/Type\s*/ObjStm", d):
                self._objstm(d, st)
        self._streams = {}

    def _objstm(self, d, st):
        dec = decode_stream(d, st)
        mn = re.search(rb"/N\s+(\d+)", d)
        mf = re.search(rb"/First\s+(\d+)", d)
        if not dec or not mn or not mf:
            return
        n, first = int(mn.group(1)), int(mf.group(1))
        hdr = dec[:first].split()
        pairs = [(int(hdr[i]), int(hdr[i + 1]))
                 for i in range(0, min(len(hdr) - 1, 2 * n), 2)]
        for k, (onum, off) in enumerate(pairs):
            end = pairs[k + 1][1] if k + 1 < len(pairs) else len(dec) - first
            if onum not in self.objs:
                self.objs[onum] = (dec[first + off:first + end], None)

    def get(self, num):
        return self.objs.get(num, (b"", None))

    def stream(self, num):
        if num not in self._streams:
            d, st = self.get(num)
            self._streams[num] = decode_stream(d, st) if st is not None \
                else b""
        return self._streams[num]

    def resolve(self, val):
        """«N 0 R» -> словарь объекта; иначе как есть."""
        m = re.match(rb"\s*(\d+)\s+\d+\s+R", val or b"")
        if m:
            return self.get(int(m.group(1)))[0]
        return val


def _value_after(d, key):
    """Значение ключа словаря: <<…>> (со вложенностью), […], ref или токен."""
    m = re.search(re.escape(key) + rb"(?![A-Za-z])\s*", d)
    if not m:
        return None
    i = m.end()
    if d[i:i + 2] == b"<<":
        depth, j = 0, i
        while j < len(d) - 1:
            if d[j:j + 2] == b"<<":
                depth += 1
                j += 2
                continue
            if d[j:j + 2] == b">>":
                depth -= 1
                j += 2
                if depth == 0:
                    return d[i:j]
                continue
            j += 1
        return d[i:]
    if d[i:i + 1] == b"[":
        j = d.find(b"]", i)
        return d[i:j + 1] if j > 0 else d[i:]
    mr = re.match(rb"(\d+)\s+\d+\s+R", d[i:])
    if mr:
        return mr.group(0)
    mt = re.match(rb"[^\s/<>\[\]]+|/[^\s/<>\[\]]+", d[i:])
    return mt.group(0) if mt else None


# ------------------------------ шрифты --------------------------------------
_GLYPH = {"space": " ", "period": ".", "comma": ",", "hyphen": "-",
          "colon": ":", "semicolon": ";", "exclam": "!", "question": "?",
          "quotedbl": "\"", "quotesingle": "'", "quoteright": "'",
          "quoteleft": "'", "quotedblleft": "«", "quotedblright": "»",
          "guillemotleft": "«", "guillemotright": "»", "parenleft": "(",
          "parenright": ")", "bracketleft": "[", "bracketright": "]",
          "slash": "/", "backslash": "\\", "percent": "%", "numbersign": "#",
          "ampersand": "&", "asterisk": "*", "plus": "+", "equal": "=",
          "less": "<", "greater": ">", "at": "@", "underscore": "_",
          "endash": "–", "emdash": "—", "bullet": "•", "numero": "№",
          "afii61352": "№", "ellipsis": "…", "degree": "°",
          "zero": "0", "one": "1", "two": "2", "three": "3", "four": "4",
          "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9"}


def _glyph_char(name):
    name = name.decode("latin-1") if isinstance(name, bytes) else name
    if name in _GLYPH:
        return _GLYPH[name]
    if len(name) == 1:
        return name
    m = re.match(r"uni([0-9A-Fa-f]{4})", name)
    if m:
        return chr(int(m.group(1), 16))
    m = re.match(r"afii(\d{5})", name)
    if m:                                   # кириллица Adobe: afii10017 = А
        n = int(m.group(1))
        if n == 10023:
            return "Ё"
        if n == 10071:
            return "ё"
        if 10017 <= n <= 10022:
            return chr(0x0410 + n - 10017)
        if 10024 <= n <= 10049:
            return chr(0x0416 + n - 10024)
        if 10065 <= n <= 10070:
            return chr(0x0430 + n - 10065)
        if 10072 <= n <= 10097:
            return chr(0x0436 + n - 10072)
    return ""


def _utf16(h):
    try:
        b = bytes.fromhex(h.decode("ascii"))
        if len(b) % 2:
            b = b"\x00" + b
        return b.decode("utf-16-be", "replace")
    except Exception:
        return ""


def _parse_cmap(data):
    m, nb = {}, 0
    for blk in re.findall(rb"begincodespacerange(.*?)endcodespacerange",
                          data, re.S):
        hx = re.findall(rb"<([0-9A-Fa-f]+)>", blk)
        if hx:
            nb = max(nb, len(hx[0]) // 2)
    for blk in re.findall(rb"beginbfchar(.*?)endbfchar", data, re.S):
        for src, dst in re.findall(rb"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]*)>",
                                   blk):
            m[int(src, 16)] = _utf16(dst)
            nb = nb or len(src) // 2
    for blk in re.findall(rb"beginbfrange(.*?)endbfrange", data, re.S):
        for lo, hi, rest in re.findall(
                rb"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*"
                rb"(\[[^\]]*\]|<[0-9A-Fa-f]*>)", blk):
            nb = nb or len(lo) // 2
            a, b = int(lo, 16), int(hi, 16)
            if b < a or b - a > 65535:
                continue
            if rest.startswith(b"["):
                for i, dst in enumerate(re.findall(rb"<([0-9A-Fa-f]*)>",
                                                   rest)):
                    m[a + i] = _utf16(dst)
            else:
                try:
                    base = bytearray(bytes.fromhex(rest[1:-1].decode()))
                except Exception:
                    continue
                if len(base) % 2:
                    base = bytearray(b"\x00") + base
                if not base:
                    continue
                last = int.from_bytes(base[-2:], "big")
                for i in range(b - a + 1):
                    bb = bytearray(base)
                    bb[-2:] = ((last + i) & 0xFFFF).to_bytes(2, "big")
                    m[a + i] = bytes(bb).decode("utf-16-be", "replace")
    return m, (nb or 1)


class _Font(object):
    def __init__(self, doc, fdict):
        self.cmap, self.nb, self.enc = None, 1, {}
        self.two_byte = b"/Type0" in fdict or b"/Identity-H" in fdict
        tu = _value_after(fdict, b"/ToUnicode")
        if tu:
            mr = _REF_RE.match(tu)
            if mr:
                self.cmap, self.nb = _parse_cmap(doc.stream(int(mr.group(1))))
        if self.two_byte and not self.cmap:
            self.nb = 2
        enc = _value_after(fdict, b"/Encoding")
        if enc and _REF_RE.match(enc):
            enc = doc.resolve(enc)
        self.base = "cp1252"
        if enc and b"/Differences" in enc:
            arr = _value_after(enc, b"/Differences") or b""
            code = 0
            for tok in re.findall(rb"\d+|/[^\s/\[\]]+", arr):
                if tok[:1] == b"/":
                    ch = _glyph_char(tok[1:])
                    if ch:
                        self.enc[code] = ch
                    code += 1
                else:
                    code = int(tok)

    def decode(self, b):
        if self.cmap:
            out, nb = [], self.nb
            for i in range(0, len(b) - nb + 1, nb):
                code = int.from_bytes(b[i:i + nb], "big")
                ch = self.cmap.get(code)
                if ch is None and nb == 1:
                    ch = self.enc.get(code) or bytes([code]).decode(
                        "cp1252", "replace")
                out.append(ch or "")
            return "".join(out)
        if self.nb == 2:
            return ""                       # CID без ToUnicode — честно нечем
        out = []
        for c in b:
            ch = self.enc.get(c)
            if ch is None:
                ch = bytes([c]).decode("cp1251" if c >= 0xC0 else "cp1252",
                                       "replace")
            out.append(ch)
        return "".join(out)


# --------------------------- поток содержимого ------------------------------
_TOK = re.compile(
    rb"\((?:\\.|[^\\()]|\((?:\\.|[^\\()])*\))*\)"      # (строка)
    rb"|<<|>>|<[0-9A-Fa-f\s]*>"                         # словарь / <hex>
    rb"|\[|\]"
    rb"|/[^\s/\[\]()<>{}%]+"                            # /Имя
    rb"|[-+]?(?:\d+\.?\d*|\.\d+)"                       # число
    rb"|[A-Za-z'\"*]+[01]?", re.S)                      # оператор

_ESC = {b"n": b"\n", b"r": b"\r", b"t": b"\t", b"b": b"\b", b"f": b"\f",
        b"(": b"(", b")": b")", b"\\": b"\\"}


def _lit(s):
    s = s[1:-1]
    out, i = bytearray(), 0
    while i < len(s):
        c = s[i:i + 1]
        if c == b"\\" and i + 1 < len(s):
            n = s[i + 1:i + 2]
            if n in _ESC:
                out += _ESC[n]
                i += 2
                continue
            m = re.match(rb"[0-7]{1,3}", s[i + 1:i + 4])
            if m:
                out.append(int(m.group(0), 8) & 255)
                i += 1 + len(m.group(0))
                continue
            if n in (b"\n", b"\r"):
                i += 2
                continue
            i += 1
            continue
        out += c
        i += 1
    return bytes(out)


def _hex(s):
    h = re.sub(rb"[^0-9A-Fa-f]", b"", s)
    if len(h) % 2:
        h += b"0"
    return bytes.fromhex(h.decode("ascii")) if h else b""


class _Page(object):
    def __init__(self, doc, res):
        self.doc, self.fonts, self.xobj = doc, {}, {}
        self._load_res(res)

    def _load_res(self, res):
        if not res:
            return
        if _REF_RE.match(res):
            res = self.doc.resolve(res)
        fd = _value_after(res, b"/Font")
        if fd and _REF_RE.match(fd):
            fd = self.doc.resolve(fd)
        for name, num in re.findall(rb"/([^\s/<>\[\]]+)\s+(\d+)\s+\d+\s+R",
                                    fd or b""):
            try:
                self.fonts[name] = _Font(self.doc, self.doc.get(int(num))[0])
            except Exception:
                pass
        xd = _value_after(res, b"/XObject")
        if xd and _REF_RE.match(xd):
            xd = self.doc.resolve(xd)
        for name, num in re.findall(rb"/([^\s/<>\[\]]+)\s+(\d+)\s+\d+\s+R",
                                    xd or b""):
            self.xobj[name] = int(num)


def _run(doc, page, data, out, depth=0):
    data = re.sub(rb"\bBI\b.*?\bID\b.*?\bEI\b", b" ", data, flags=re.S)
    font = None
    ops, arr = [], None
    last_y = None
    for m in _TOK.finditer(data):
        t = m.group(0)
        c = t[:1]
        if c == b"(" or (c == b"<" and t[:2] != b"<<"):
            val = ("s", _lit(t) if c == b"(" else _hex(t[1:-1]))
            (arr if arr is not None else ops).append(val)
            continue
        if t == b"[":
            arr = []
            continue
        if t == b"]":
            ops.append(("a", arr or []))
            arr = None
            continue
        if c == b"/":
            (arr if arr is not None else ops).append(("n", t[1:]))
            continue
        if c in b"+-.0123456789":
            try:
                (arr if arr is not None else ops).append(("f", float(t)))
            except ValueError:
                pass
            continue
        if t in (b"<<", b">>"):
            continue
        op = t
        if op == b"Tf":
            names = [v for k, v in ops if k == "n"]
            if names:
                font = page.fonts.get(names[-1])
        elif op in (b"Tj", b"'", b'"'):
            ss = [v for k, v in ops if k == "s"]
            if op != b"Tj":
                out.append("\n")
            if ss:
                out.append(font.decode(ss[-1]) if font else
                           ss[-1].decode("cp1252", "replace"))
        elif op == b"TJ":
            arrs = [v for k, v in ops if k == "a"]
            for k, v in (arrs[-1] if arrs else []):
                if k == "s":
                    out.append(font.decode(v) if font else
                               v.decode("cp1252", "replace"))
                elif k == "f" and v < -180:
                    out.append(" ")
        elif op in (b"Td", b"TD"):
            nums = [v for k, v in ops if k == "f"]
            if len(nums) >= 2:
                if abs(nums[-1]) > 0.5:
                    out.append("\n")
                elif nums[-2] > 0.5:
                    out.append(" ")
        elif op == b"T*":
            out.append("\n")
        elif op == b"Tm":
            nums = [v for k, v in ops if k == "f"]
            if len(nums) >= 6:
                y = nums[-1]
                if last_y is not None and abs(y - last_y) > 0.5:
                    out.append("\n")
                elif last_y is not None:
                    out.append(" ")
                last_y = y
        elif op == b"ET":
            out.append(" ")
        elif op == b"Do" and depth < 3:
            names = [v for k, v in ops if k == "n"]
            num = page.xobj.get(names[-1]) if names else None
            if num is not None:
                d, _st = doc.get(num)
                if re.search(rb"/Subtype\s*/Form", d):
                    sub = _Page(doc, _value_after(d, b"/Resources"))
                    if not sub.fonts:
                        sub.fonts = page.fonts
                    _run(doc, sub, doc.stream(num), out, depth + 1)
        ops = []
        arr = None


def _pages(doc):
    """Страницы в порядке чтения: (dict, унаследованные ресурсы)."""
    root = None
    for num, (d, _st) in doc.objs.items():
        if re.search(rb"/Type\s*/Catalog", d):
            root = d
            break
    out, seen = [], set()

    def walk(num, res, depth=0):
        if num in seen or depth > 40:
            return
        seen.add(num)
        d = doc.get(num)[0]
        r = _value_after(d, b"/Resources") or res
        if re.search(rb"/Type\s*/Pages", d):
            kids = _value_after(d, b"/Kids") or b""
            for k in _REF_RE.findall(kids):
                walk(int(k), r, depth + 1)
        elif re.search(rb"/Type\s*/Page\b", d):
            out.append((d, r))

    if root:
        pr = _value_after(root, b"/Pages")
        mr = _REF_RE.match(pr or b"")
        if mr:
            walk(int(mr.group(1)), None)
    if not out:                             # дерево битое — по порядку объектов
        for num in sorted(doc.objs):
            d = doc.objs[num][0]
            if re.search(rb"/Type\s*/Page\b", d):
                out.append((d, _value_after(d, b"/Resources")))
    return out


def is_encrypted(raw):
    return bool(re.search(rb"/Encrypt\s+\d+\s+\d+\s+R|/Encrypt\s*<<", raw))


def extract(raw, max_chars=300000):
    """PDF (bytes) -> текст. Пусто: скан без текста или шифрование."""
    if not raw or raw[:5] != b"%PDF-" and b"%PDF-" not in raw[:1024]:
        return ""
    if is_encrypted(raw):
        return ""
    doc = _Doc(raw)
    parts = []
    for d, res in _pages(doc):
        page = _Page(doc, res)
        cont = _value_after(d, b"/Contents") or b""
        refs = []
        for n in _REF_RE.findall(cont):
            od, ost = doc.get(int(n))
            if ost is None and b"R" in od:      # косвенный массив потоков
                refs.extend(int(x) for x in _REF_RE.findall(od))
            else:
                refs.append(int(n))
        data = b"".join(doc.stream(n) + b"\n" for n in refs)
        if not data.strip():
            continue
        out = []
        try:
            _run(doc, page, data, out)
        except Exception:
            pass
        txt = "".join(out)
        txt = re.sub(r"[ \t]+", " ", txt)
        txt = re.sub(r" *\n *", "\n", txt)
        txt = re.sub(r"\n{3,}", "\n\n", txt).strip()
        if txt:
            parts.append(txt)
        if sum(len(p) for p in parts) > max_chars:
            break
    text = "\n\n".join(parts)
    # переносы слов на конце строки: «инфор-\nмация» -> «информация»
    text = re.sub(r"(\w)-\n(\w)", r"\1\2", text)
    text = "".join(ch for ch in text if ch == "\n" or ch == "\t" or
                   ord(ch) >= 32)
    return text[:max_chars].strip()
