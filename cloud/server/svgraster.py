# -*- coding: utf-8 -*-
# ===========================================================================
# svgraster.py — ФОРМУЛА (SVG latexlite) -> PNG для мессенджера (v3.30)
# ---------------------------------------------------------------------------
# МАКС не показывает SVG, поэтому формулы раньше пропадали из ответов бота.
# SVG движка latexlite устроен просто: <text> (глифы), <line> (черта дроби)
# и <path> из команд M/L (знак корня) — их рисует Pillow. Нужен Pillow; нет —
# None (бот честно отправит формулу текстом).
# ===========================================================================
import io
import os
import re

_FONTS = {
    "n": ["/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
          "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
          r"C:\Windows\Fonts\cambria.ttc", r"C:\Windows\Fonts\times.ttf",
          r"C:\Windows\Fonts\arial.ttf"],
    "i": ["/usr/share/fonts/truetype/dejavu/DejaVuSerif-Italic.ttf",
          "/usr/share/fonts/truetype/dejavu/DejaVuSans-Oblique.ttf",
          r"C:\Windows\Fonts\cambriai.ttf", r"C:\Windows\Fonts\timesi.ttf",
          r"C:\Windows\Fonts\ariali.ttf"],
}
_CACHE = {}


def _font(size, italic=False):
    from PIL import ImageFont
    key = (int(size), italic)
    if key in _CACHE:
        return _CACHE[key]
    f = None
    for p in _FONTS["i" if italic else "n"] + _FONTS["n"]:
        if os.path.isfile(p):
            try:
                f = ImageFont.truetype(p, int(size))
                break
            except Exception:
                continue
    if f is None:
        try:
            f = ImageFont.load_default(size=int(size))
        except TypeError:
            f = ImageFont.load_default()
    _CACHE[key] = f
    return f


def _attrs(tag):
    return dict(re.findall(r'([\w:-]+)="([^"]*)"', tag))


def rasterize(svg, scale=3.0, pad=14):
    """SVG формулы -> PNG (bytes) | None."""
    try:
        from PIL import Image, ImageDraw
    except Exception:
        return None
    import html
    m = re.search(r'viewBox="([\d.\s-]+)"', svg or "")
    if m:
        _x0, _y0, w, h = [float(v) for v in m.group(1).split()]
    else:                       # заглушка движка: только width/height
        mw = re.search(r'<svg[^>]*\swidth="([\d.]+)"', svg or "")
        mh = re.search(r'<svg[^>]*\sheight="([\d.]+)"', svg or "")
        if not (mw and mh):
            return None
        w, h = float(mw.group(1)), float(mh.group(1))
    W, H = int(w * scale) + 2 * pad, int(h * scale) + 2 * pad
    im = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(im)
    ink = (20, 24, 33)

    def X(v):
        return pad + float(v) * scale

    for tm in re.finditer(r"<text\b([^>]*)>(.*?)</text>", svg, re.S):
        a = _attrs(tm.group(1))
        txt = html.unescape(re.sub(r"<[^>]+>", "", tm.group(2)))
        if not txt:
            continue
        size = float(a.get("font-size") or 16) * scale
        f = _font(size, a.get("font-style") == "italic")
        try:
            d.text((X(a.get("x", 0)), X(a.get("y", 0))), txt, font=f,
                   fill=ink, anchor="ls")
        except Exception:
            d.text((X(a.get("x", 0)), X(a.get("y", 0)) - size * 0.8), txt,
                   font=f, fill=ink)
    for lm in re.finditer(r"<line\b([^>]*)/?>", svg):
        a = _attrs(lm.group(1))
        sw = max(1, int(float(a.get("stroke-width") or 1) * scale))
        d.line([(X(a.get("x1", 0)), X(a.get("y1", 0))),
                (X(a.get("x2", 0)), X(a.get("y2", 0)))], fill=ink, width=sw)
    for pm in re.finditer(r"<path\b([^>]*)/?>", svg):
        a = _attrs(pm.group(1))
        sw = max(1, int(float(a.get("stroke-width") or 1) * scale))
        pts = []
        for cmd, xs, ys in re.findall(r"([ML])\s*([-\d.]+)[\s,]+([-\d.]+)",
                                      a.get("d", "")):
            if cmd == "M" and len(pts) > 1:
                d.line(pts, fill=ink, width=sw)
                pts = []
            elif cmd == "M":
                pts = []
            pts.append((X(xs), X(ys)))
        if len(pts) > 1:
            d.line(pts, fill=ink, width=sw)
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def formula_blocks(text):
    """[ФОРМУЛА]<svg>[/ФОРМУЛА] из ответа -> список SVG."""
    return re.findall(r"\[ФОРМУЛА\]([\s\S]*?)\[/ФОРМУЛА\]", str(text or ""))
