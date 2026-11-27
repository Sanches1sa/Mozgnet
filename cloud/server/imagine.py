# -*- coding: utf-8 -*-
# ===========================================================================
# imagine.py — ЗОНА ВООБРАЖЕНИЯ: РИСОВАНИЕ И ПЕРЕРИСОВКА КАРТИНОК (v3.16)
# ---------------------------------------------------------------------------
# Заявка владельца: «введи latex движок чтобы ИИ писал формулы и мог
# рисовать и перерисовывать картинки — назови это зоной воображения».
#
# КАК ЭТО РАБОТАЕТ (честно, без внешних сетей):
#   1) описание на русском разбирается на ПРЕДМЕТЫ (дом, дерево, кот…),
#      ЦВЕТА и ВРЕМЯ СУТОК (день/ночь/закат) — по словарю ключевых слов;
#   2) сцена собирается ПРОЦЕДУРНО в SVG: у каждого предмета — свой
#      рисовальщик, случайность детерминирована seed'ом описания
#      (hashlib), поэтому «перерисуй» даёт вариант ТОЙ ЖЕ сцены;
#   3) «перерисуй» = новый seed + правки из текста (добавить/убрать
#      предметы, перекрасить, сменить время суток); состояние холста
#      хранится в data/imagine.json;
#   4) «нарисуй график y=x^2» — график функции: выражение считается
#      безопасным вычислителем mathmodels.safe_eval и рисуется осями
#      и линией; рядом — LaTeX-карточка формулы (движок latexlite);
#   5) ничего не распознали — честный отказ со списком того, что зона
#      умеет рисовать. Никаких выдуманных картинок «в уме».
# ===========================================================================
import hashlib
import json
import math
import random
import re
import threading
import time

import latexlite
import store

LOCK = threading.RLock()
_STATE_PATH = "imagine.json"

# ------------------------------ словарь зоны --------------------------------
# ключевые слова -> (тип предмета, подпись). Одно слово может ловить несколько
# написаний; подпись видна владельцу в перечне «что нарисовано».
KINDS = {
    "sun": ("солнце", "солнце|солнца|солнцу|sun"),
    "moon": ("луна", "луна|луну|луны|месяц|месяцa"),
    "stars": ("звёзды", "звезд|звёзд|звезда|звёзды|звёздочку"),
    "cloud": ("облака", "облак|туч|тучу"),
    "rainbow": ("радуга", "радуг"),
    "mountains": ("горы", "гор[аыуо]|горы|скал|вулкан"),
    "water": ("вода", "мор[еюя]|озер|рек[аиуой]|река|вод[аоыуой]|пруд|океан"),
    "tree": ("дерево", "дерев|деревья|дуб|яблон"),
    "pine": ("ель", "ел[ьиокеу]|елку|ель|ёлк|сосн"),
    "house": ("дом", "дом|домик|избушк|хат|коттедж"),
    "castle": ("замок", "замок|крепост|дворец"),
    "lighthouse": ("маяк", "маяк"),
    "road": ("дорога", "дорог|тропинк|путь|шоссе"),
    "flower": ("цветок", "цвет[окте]|цветы|ромашк|тюльпан|роз[ауы]"),
    "grass": ("трава", "трав[аыу]|газон"),
    "snowman": ("снеговик", "снеговик|снеговича"),
    "robot": ("робот", "робот|робота|андроид"),
    "cat": ("кот", "кот|кошк|котик|котенк|котёнк"),
    "dog": ("пёс", "пес|пса|псин|собак|щенок|щенк"),
    "bird": ("птица", "птиц|птичку|воробе|ворон|голуб|чайк"),
    "fish": ("рыба", "рыб[ауыокеи]|рыбк"),
    "butterfly": ("бабочка", "бабочк"),
    "rocket": ("ракета", "ракет|шаттл"),
    "ufo": ("тарелка", "тарелк|нло|летающ"),
    "car": ("машина", "машин|автомобил|легковушк|грузовик|самосвал"),
    "ship": ("корабль", "корабл|парусник|лодк|катер|шхун"),
    "balloon": ("воздушный шар", "воздушн.*шар|шарик|шар\b|аэростат"),
    "plane": ("самолёт", "самолет|самолёт|лайнер|джет"),
    "person": ("человечек", "человек|человечек|мальчик|девочк|мужичок"),
    "cat_big": ("кот-великан", "котищ|кошщ"),
    "mushroom": ("гриб", "гриб[ауов]?|мухомор|боровик"),
    "dragon": ("дракон", "дракон|змей|ящер"),
    "city": ("город", "город|небоскреб|небоскрёб|многоэтажк"),
    "campfire": ("костёр", "костер|костёр|огон|пламя|огню"),
    "cactus": ("кактус", "кактус"),
    "bench": ("скамейка", "скамейк|лавочк"),
    "bridge_draw": ("мост", "мост[аикеу]?(?!ик)"),
    "hill": ("холм", "холм|пригорк|бугор"),
}

COLORS = {
    "красн": "#e04a3a", "оранжев": "#f08c2e", "жёлт": "#f2c744",
    "желт": "#f2c744", "зелён": "#3f9d4f", "зелен": "#3f9d4f",
    "голуб": "#57b8e8", "син": "#3a6fd8", "фиолет": "#8a5fd3",
    "розов": "#ef7fb0", "бел": "#f2f2f2", "чёрн": "#2c3038",
    "черн": "#2c3038", "сер": "#9aa2ad", "коричн": "#8a5a34",
    "золот": "#e8b93c", "бирюзов": "#3fc4b8", "лилов": "#a67fd6",
    "малин": "#d63a6e", "тёмн": "#3a4152", "темн": "#3a4152",
}
_NUM_WORDS = {"один": 1, "одну": 1, "два": 2, "две": 2, "три": 3, "четыре": 4,
              "пять": 5, "шесть": 6, "семь": 7, "восемь": 8, "девять": 9,
              "десять": 10, "пара": 2, "несколько": 3}
_KNOWN_LIST = ", ".join(sorted(set(v[0] for v in KINDS.values())))

# ------------------------------- состояние ----------------------------------
_state = None


def _load():
    global _state
    if _state is None:
        try:
            import store
            _state = store.load_json(_STATE_PATH, None)
        except Exception:
            _state = None
        if not isinstance(_state, dict):
            _state = {"scene": None, "counter": 1}
    return _state


def _save():
    try:
        import store
        with store.LOCK:
            store.save_json(_STATE_PATH, _state)
    except Exception:
        pass


def canvas_info():
    """Короткая сводка о холсте для панелей (без SVG)."""
    st = _load()
    sc = st.get("scene")
    if not sc:
        return {"has": False, "objects": [], "time": ""}
    return {"has": True,
            "objects": [o.get("kind", "?") for o in sc.get("objects", [])],
            "time": sc.get("time", "день"), "seed": sc.get("seed", 0)}

# --------------------------- разбор описания --------------------------------
_ALL_WORDS_RE = re.compile(r"[а-яёa-z0-9]+", re.I)


def _find_colors(text_low):
    """Цвета в порядке упоминания: ['#код', позиция]."""
    out = []
    for stem, code in COLORS.items():
        m = re.search(r"\b" + stem + r"[а-яё]*", text_low)
        if m:
            out.append((m.start(), code))
    return [c for _p, c in sorted(out)]


def _parse_objects(text_low):
    """Предметы: [{kind, n, color}]. Числительное перед словом или цифра
    задают количество; цвет привязывается к БЛИЖАЙШЕМУ предмету после
    цветового слова («красный дом» -> дом красный), иначе — к ближайшему
    до него; не нашлось — цвет расходится по предметам по порядку."""
    spans = []
    for kind, (_label, pat) in KINDS.items():
        for m in re.finditer(r"\b(" + pat + r")", text_low):
            spans.append((m.start(), m.end(), kind))
    spans.sort()
    found = []
    color_spans = []
    for stem, code in COLORS.items():
        m = re.search(r"\b" + stem + r"[а-яё]*", text_low)
        if m:
            color_spans.append((m.start(), m.end(), code))
    color_spans.sort()
    # пары (цвет, предмет) по дистанции: сначала «цвет ПЕРЕД предметом»
    # (русский порядок «красный дом»), затем остатки «предмет в цвет»
    pairs = []
    for ci, (cpos, cend, code) in enumerate(color_spans):
        for si, (start, end, kind) in enumerate(spans):
            if cpos < start and start - cend <= 30:
                pairs.append((start - cend, 'pre', ci, si, code))
            elif cpos >= end and cpos - start <= 30:
                pairs.append((cpos - end, 'post', ci, si, code))
    pairs.sort(key=lambda p: (p[1] != 'pre', p[0]))   # сначала pre, ближе
    used_c, used_o = set(), set()
    color_of = {}
    for _d, kind_pair, ci, si, code in pairs:
        if kind_pair == 'pre' and (ci in used_c or si in used_o):
            continue
        if kind_pair == 'post':
            if ci in used_c or si in used_o:
                continue
        used_c.add(ci)
        used_o.add(si)
        color_of[si] = code
    for si, (start, end, kind) in enumerate(spans):
        before = text_low[max(0, start - 12):start]
        count = 1
        mnum = re.search(r"(\d+)\s*$", before)
        if mnum:
            count = max(1, min(12, int(mnum.group(1))))
        else:
            for w, n in _NUM_WORDS.items():
                if re.search(r"\b" + w + r"\s*$", before):
                    count = n
                    break
        found.append({"kind": kind, "n": count,
                      "color": color_of.get(si)})
    # неназначенные цвета — по порядку оставшимся предметам
    rest = [code for ci, (_p, _e, code) in enumerate(color_spans)
            if ci not in used_c]
    if rest:
        for o in found:
            if o["color"] is None and rest:
                o["color"] = rest.pop(0)
    return found


def _parse_time(text_low):
    if re.search(r"ноч[bььюи]|вечер|звёздн|луна|лунн|тёмн.*неб|темн.*неб",
                 text_low):
        return "ночь"
    if re.search(r"закат|рассвет|сумерк|вечерн", text_low):
        return "закат"
    if re.search(r"зим|снег|мороз|вьюг", text_low):
        return "зима"
    return "день"


def _time_palette(time_name):
    if time_name == "ночь":
        return {"sky1": "#131a33", "sky2": "#2a3560", "sun": None,
                "moon": "#e8e4d0", "ground": "#22301f", "haze": "#1d2542",
                "line": "#e8e8f0"}
    if time_name == "закат":
        return {"sky1": "#f7b267", "sky2": "#f4845f", "sun": "#f25c54",
                "moon": None, "ground": "#4a3f2f", "haze": "#e8956d",
                "line": "#5a3d2e"}
    if time_name == "зима":
        return {"sky1": "#cfe4f7", "sky2": "#eef6fd", "sun": "#f7e9a0",
                "moon": None, "ground": "#eef3fa", "haze": "#dfe9f5",
                "line": "#7a8899"}
    return {"sky1": "#8ecff2", "sky2": "#dff1fc", "sun": "#f7d154",
            "moon": None, "ground": "#67b25c", "haze": "#bfe4f7",
            "line": "#41596b"}


def _seed_for(text, counter):
    h = hashlib.md5(("%d|%s" % (counter, str(text)[:200]))
                    .encode("utf-8", "replace")).hexdigest()
    return int(h[:8], 16)

# ------------------------------ рисовальщики --------------------------------
# Каждый возвращает фрагмент SVG. Координаты локальные, obj содержит
# x, y, s (масштаб), color. rng — детерминированный random.Random(seed).


def _rr(rng, a, b):
    return rng.uniform(a, b)


def _f(v):
    s = "%.1f" % (float(v) + 0.0)
    s = s.rstrip("0").rstrip(".")
    return s if s not in ("-0", "") else "0"


def _poly(pts, fill, stroke=None, sw=2):
    p = " ".join("%s,%s" % (_f(x), _f(y)) for x, y in pts)
    st = ' stroke="%s" stroke-width="%s" fill="none"' % (stroke, sw) \
        if stroke else ""
    return '<polygon points="%s" fill="%s"%s/>' % (p, fill, st)


def _line(x1, y1, x2, y2, color, sw=2, dash=None):
    d = ' stroke-dasharray="%s"' % dash if dash else ""
    return ('<line x1="%s" y1="%s" x2="%s" y2="%s" stroke="%s" '
            'stroke-width="%s"%s/>' % (_f(x1), _f(y1), _f(x2), _f(y2),
                                       color, _f(sw), d))


def _circ(cx, cy, r, fill, stroke=None, sw=2, op=None):
    o = ' opacity="%s"' % _f(op) if op is not None else ""
    st = ' stroke="%s" stroke-width="%s"' % (stroke, sw) if stroke else ""
    return ('<circle cx="%s" cy="%s" r="%s" fill="%s"%s%s/>'
            % (_f(cx), _f(cy), _f(r), fill, st, o))


def _rect(x, y, w, h, fill, rx=3, stroke=None, sw=2, op=None):
    o = ' opacity="%s"' % _f(op) if op is not None else ""
    st = ' stroke="%s" stroke-width="%s"' % (stroke, sw) if stroke else ""
    return ('<rect x="%s" y="%s" width="%s" height="%s" rx="%s" fill="%s"'
            '%s%s/>' % (_f(x), _f(y), _f(w), _f(h), _f(rx), fill, st, o))


def _path(d, fill="none", stroke=None, sw=2):
    st = ' stroke="%s" stroke-width="%s" stroke-linecap="round"' % (stroke, sw) \
        if stroke else ""
    return '<path d="%s" fill="%s"%s/>' % (d, fill, st)


def _txt(x, y, s, size=13, fill="#5a6472", anchor="middle"):
    return ('<text x="%s" y="%s" font-size="%s" fill="%s" text-anchor="%s" '
            'font-family="Arial, sans-serif">%s</text>'
            % (_f(x), _f(y), _f(size), fill, anchor, latexlite.esc(s)))


def _darken(hex_color, k=0.75):
    h = hex_color.lstrip("#")
    try:
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    except ValueError:
        return hex_color
    return "#%02x%02x%02x" % (int(r * k), int(g * k), int(b * k))


# ---- предметы ----
def _d_sun(rng, o, pal):
    r = 26 * o["s"]
    rays = "".join(
        _line(o["x"] + math.cos(a) * r * 1.25, o["y"] + math.sin(a) * r * 1.25,
              o["x"] + math.cos(a) * r * 1.75, o["y"] + math.sin(a) * r * 1.75,
              o["color"], 3) for a in
        [i * math.pi / 8 for i in range(16)])
    return (_circ(o["x"], o["y"], r * 1.9, o["color"], op=0.25) +
            _circ(o["x"], o["y"], r, o["color"]) + rays)


def _d_moon(rng, o, pal):
    r = 24 * o["s"]
    return (_circ(o["x"], o["y"], r * 1.6, "#e8e4d0", op=0.18) +
            _circ(o["x"], o["y"], r, o["color"]) +
            _circ(o["x"] + r * 0.45, o["y"] - r * 0.35, r * 0.8,
                  pal["sky1"], op=0.92))


def _d_stars(rng, o, pal):
    out = []
    for _i in range(int(o["n"] * 6) + 4):
        x = o["x"] + _rr(rng, -160, 160)
        y = o["y"] + _rr(rng, -120, 60)
        r = _rr(rng, 1.2, 2.8)
        out.append(_circ(x, y, r, o["color"], op=_rr(rng, 0.5, 1.0)))
    return "".join(out)


def _d_cloud(rng, o, pal):
    col = o["color"] if o["color"] != "#ffffff" else "#f4f7fb"
    if o.get("dark"):
        col = "#8e99ad"
    s = o["s"]
    y = o["y"]
    return (_circ(o["x"], y, 20 * s, col) +
            _circ(o["x"] + 22 * s, y + 4 * s, 15 * s, col) +
            _circ(o["x"] - 22 * s, y + 5 * s, 14 * s, col) +
            _rect(o["x"] - 30 * s, y + 2 * s, 60 * s, 14 * s, col, rx=10))


def _d_rainbow(rng, o, pal):
    cols = ["#e04a3a", "#f08c2e", "#f2c744", "#3f9d4f", "#57b8e8", "#8a5fd3"]
    out = []
    for i, c in enumerate(cols):
        r = 150 * o["s"] - i * 9 * o["s"]
        out.append('<path d="M %s %s A %s %s 0 0 1 %s %s" fill="none" '
                   'stroke="%s" stroke-width="%s" opacity="0.8"/>'
                   % (_f(o["x"] - r), _f(o["y"]), _f(r), _f(r),
                      _f(o["x"] + r), _f(o["y"]), c, _f(8 * o["s"])))
    return "".join(out)


def _d_mountains(rng, o, pal):
    col = o["color"] or "#7d8fa8"
    dark = _darken(col, 0.8)
    pts = [(o["x"] - 120 * o["s"], o["y"]),
           (o["x"] - 60 * o["s"], o["y"] - 90 * o["s"]),
           (o["x"] - 20 * o["s"], o["y"] - 40 * o["s"]),
           (o["x"] + 30 * o["s"], o["y"] - 120 * o["s"]),
           (o["x"] + 90 * o["s"], o["y"] - 50 * o["s"]),
           (o["x"] + 140 * o["s"], o["y"] - 85 * o["s"]),
           (o["x"] + 190 * o["s"], o["y"])]
    snow = (o["x"] + 30 * o["s"], o["y"] - 120 * o["s"])
    return (_poly(pts, col) +
            _poly([(snow[0], snow[1]), (snow[0] - 22 * o["s"], snow[1] + 32 * o["s"]),
                   (snow[0] - 8 * o["s"], snow[1] + 24 * o["s"]),
                   (snow[0] + 4 * o["s"], snow[1] + 34 * o["s"]),
                   (snow[0] + 20 * o["s"], snow[1] + 28 * o["s"])], "#f4f7fb")
            + _poly([(o["x"] - 60 * o["s"], o["y"]),
                     (o["x"] - 60 * o["s"] + 30, o["y"] + 2),
                     (o["x"] - 60 * o["s"] + 24, o["y"] - 40 * o["s"])], dark))


def _d_water(rng, o, pal):
    col = o["color"] or "#57b8e8"
    y0 = o["y"]
    out = [_rect(o["x"] - 220, y0, 440, 70 * o["s"], col, rx=0, op=0.9)]
    for i in range(4):
        yy = y0 + 14 + i * 14 * o["s"]
        out.append(_path("M %s %s q 20 -6 40 0 q 20 6 40 0" %
                         (_f(o["x"] - 160 + i * 40), _f(yy)),
                         stroke=_darken(col, 0.8), sw=2.4))
    return "".join(out)


def _d_tree(rng, o, pal):
    col = o["color"] or "#3f9d4f"
    h = 90 * o["s"]
    out = [_rect(o["x"] - 7 * o["s"], o["y"] - h * 0.45, 14 * o["s"],
                 h * 0.45, "#8a5a34", rx=4)]
    crown = [_circ(o["x"], o["y"] - h * 0.62, 34 * o["s"], col),
             _circ(o["x"] - 22 * o["s"], o["y"] - h * 0.48, 24 * o["s"], col),
             _circ(o["x"] + 22 * o["s"], o["y"] - h * 0.48, 24 * o["s"], col),
             _circ(o["x"], o["y"] - h * 0.45, 28 * o["s"], col)]
    if o.get("fruit"):
        for _i in range(5):
            crown.append(_circ(o["x"] + _rr(rng, -26, 26) * o["s"],
                               o["y"] - h * 0.62 + _rr(rng, -18, 18) * o["s"],
                               5 * o["s"], "#e04a3a"))
    return "".join(out + crown)


def _d_pine(rng, o, pal):
    col = o["color"] or "#2e7d43"
    h = 110 * o["s"]
    out = [_rect(o["x"] - 6 * o["s"], o["y"] - h * 0.2, 12 * o["s"],
                 h * 0.2, "#7a5230", rx=2)]
    for i, w in enumerate([46, 38, 30]):
        yy = o["y"] - h * (0.22 + i * 0.24)
        out.append(_poly([(o["x"] - w * o["s"] / 2, yy),
                          (o["x"] + w * o["s"] / 2, yy),
                          (o["x"], yy - h * 0.34)], col))
    if pal.get("ground") == "#eef3fa":            # зима — снег на ветках
        out.append(_circ(o["x"], o["y"] - h * 0.9, 6 * o["s"], "#f4f7fb"))
    return "".join(out)


def _d_house(rng, o, pal):
    col = o["color"] or "#e8c98f"
    w, h = 110 * o["s"], 80 * o["s"]
    roof_c = "#b0503c"
    out = [_rect(o["x"] - w / 2, o["y"] - h, w, h, col, rx=2),
           _poly([(o["x"] - w / 2 - 14 * o["s"], o["y"] - h),
                  (o["x"] + w / 2 + 14 * o["s"], o["y"] - h),
                  (o["x"], o["y"] - h - 46 * o["s"])], roof_c)]
    win_lights = pal["time"] == "ночь"
    out.append(_rect(o["x"] - 28 * o["s"], o["y"] - h * 0.72, 22 * o["s"],
                     22 * o["s"],
                     "#f7e9a0" if win_lights else "#bfe0f5", rx=2,
                     stroke="#6b5a43", sw=2))
    out.append(_rect(o["x"] + 6 * o["s"], o["y"] - h * 0.72, 22 * o["s"],
                     22 * o["s"],
                     "#f7e9a0" if win_lights else "#bfe0f5", rx=2,
                     stroke="#6b5a43", sw=2))
    out.append(_rect(o["x"] - 12 * o["s"], o["y"] - h * 0.34, 24 * o["s"],
                     h * 0.34, "#7a5230", rx=2))
    if pal["time"] == "зима":
        out.append(_poly([(o["x"] - w / 2 - 14 * o["s"], o["y"] - h),
                          (o["x"] + w / 2 + 14 * o["s"], o["y"] - h),
                          (o["x"], o["y"] - h - 46 * o["s"])], "#f4f7fb",
                         op=0.85))
    return "".join(out)


def _d_castle(rng, o, pal):
    col = o["color"] or "#a8b0bd"
    w, h = 140 * o["s"], 95 * o["s"]
    out = [_rect(o["x"] - w / 2, o["y"] - h, w, h, col, rx=2)]
    for dx in (-w / 2, 0, w / 2):
        out.append(_rect(o["x"] + dx - 14 * o["s"], o["y"] - h - 40 * o["s"],
                         28 * o["s"], h + 40 * o["s"], _darken(col, 0.9), rx=2))
        out.append(_poly([(o["x"] + dx - 17 * o["s"], o["y"] - h - 40 * o["s"]),
                          (o["x"] + dx + 17 * o["s"], o["y"] - h - 40 * o["s"]),
                          (o["x"] + dx, o["y"] - h - 68 * o["s"])],
                         "#7d4a6b"))
    out.append(_rect(o["x"] - 11 * o["s"], o["y"] - 34 * o["s"], 22 * o["s"],
                     34 * o["s"], "#4a3d33", rx=10))
    return "".join(out)


def _d_lighthouse(rng, o, pal):
    col = o["color"] or "#e04a3a"
    out = [_poly([(o["x"] - 26 * o["s"], o["y"]),
                  (o["x"] + 26 * o["s"], o["y"]),
                  (o["x"] + 14 * o["s"], o["y"] - 100 * o["s"]),
                  (o["x"] - 14 * o["s"], o["y"] - 100 * o["s"])], col)]
    out.append(_rect(o["x"] - 16 * o["s"], o["y"] - 100 * o["s"],
                     32 * o["s"], 12 * o["s"], "#f2f2f2"))
    out.append(_rect(o["x"] - 18 * o["s"], o["y"] - 66 * o["s"],
                     36 * o["s"], 12 * o["s"], "#f2f2f2"))
    out.append(_rect(o["x"] - 12 * o["s"], o["y"] - 122 * o["s"],
                     24 * o["s"], 22 * o["s"], "#f7e9a0", rx=3,
                     stroke="#5a6472", sw=2))
    out.append(_poly([(o["x"] - 16 * o["s"], o["y"] - 122 * o["s"]),
                      (o["x"] + 16 * o["s"], o["y"] - 122 * o["s"]),
                      (o["x"], o["y"] - 140 * o["s"])], "#41596b"))
    if pal["time"] == "ночь":
        out.append(_circ(o["x"], o["y"] - 111 * o["s"], 26 * o["s"],
                         "#f7e9a0", op=0.35))
    return "".join(out)


def _d_road(rng, o, pal):
    col = o["color"] or "#b9ac93"
    return (_poly([(o["x"] - 40 * o["s"], o["y"]),
                   (o["x"] + 40 * o["s"], o["y"]),
                   (o["x"] + 130 * o["s"], o["y"] + 70),
                   (o["x"] - 130 * o["s"], o["y"] + 70)], col) +
            _line(o["x"] - 6 * o["s"], o["y"] + 12, o["x"] - 30, o["y"] + 60,
                  "#f4f7fb", 4, dash="14,12"))


def _d_flower(rng, o, pal):
    col = o["color"] or "#ef7fb0"
    out = [_line(o["x"], o["y"], o["x"], o["y"] - 34 * o["s"],
                 "#3f9d4f", 3)]
    for a in range(6):
        aa = a * math.pi / 3
        out.append(_circ(o["x"] + math.cos(aa) * 9 * o["s"],
                         o["y"] - 34 * o["s"] + math.sin(aa) * 9 * o["s"],
                         6 * o["s"], col))
    out.append(_circ(o["x"], o["y"] - 34 * o["s"], 5 * o["s"], "#f7e9a0"))
    return "".join(out)


def _d_grass(rng, o, pal):
    col = o["color"] or "#4f9d43"
    out = []
    for _i in range(int(o["n"] * 8) + 6):
        x = o["x"] + _rr(rng, -60, 60)
        h = _rr(rng, 8, 20) * o["s"]
        out.append(_path("M %s %s q 3 -%s 8 -%s" % (_f(x), _f(o["y"]),
                                                    _f(h), _f(h * 1.2)),
                         stroke=col, sw=2))
    return "".join(out)


def _d_snowman(rng, o, pal):
    out = [_circ(o["x"], o["y"] - 22 * o["s"], 22 * o["s"], "#f4f7fb",
                 stroke="#c9d4e2", sw=2),
           _circ(o["x"], o["y"] - 55 * o["s"], 16 * o["s"], "#f4f7fb",
                 stroke="#c9d4e2", sw=2),
           _circ(o["x"], o["y"] - 82 * o["s"], 12 * o["s"], "#f4f7fb",
                 stroke="#c9d4e2", sw=2),
           _circ(o["x"] - 4 * o["s"], o["y"] - 85 * o["s"], 1.8, "#41596b"),
           _circ(o["x"] + 4 * o["s"], o["y"] - 85 * o["s"], 1.8, "#41596b"),
           _poly([(o["x"] - 2, o["y"] - 80 * o["s"]),
                  (o["x"] + 14 * o["s"], o["y"] - 77 * o["s"]),
                  (o["x"] - 2, o["y"] - 76 * o["s"])], "#f08c2e"),
           _rect(o["x"] - 12 * o["s"], o["y"] - 94 * o["s"], 24 * o["s"],
                 6 * o["s"], "#41596b", rx=2),
           _rect(o["x"] - 8 * o["s"], o["y"] - 104 * o["s"], 16 * o["s"],
                 11 * o["s"], "#41596b", rx=2)]
    return "".join(out)


def _d_robot(rng, o, pal):
    col = o["color"] or "#9aa2ad"
    dark = _darken(col, 0.7)
    out = [_line(o["x"], o["y"] - 118 * o["s"], o["x"], o["y"] - 100 * o["s"],
                 dark, 3),
           _circ(o["x"], o["y"] - 122 * o["s"], 5 * o["s"], "#e04a3a"),
           _rect(o["x"] - 20 * o["s"], o["y"] - 100 * o["s"], 40 * o["s"],
                 32 * o["s"], col, rx=8, stroke=dark, sw=2),
           _circ(o["x"] - 8 * o["s"], o["y"] - 84 * o["s"], 4.5 * o["s"],
                 "#57b8e8"),
           _circ(o["x"] + 8 * o["s"], o["y"] - 84 * o["s"], 4.5 * o["s"],
                 "#57b8e8"),
           _rect(o["x"] - 34 * o["s"], o["y"] - 92 * o["s"], 12 * o["s"],
                 36 * o["s"], dark, rx=6),
           _rect(o["x"] + 22 * o["s"], o["y"] - 92 * o["s"], 12 * o["s"],
                 36 * o["s"], dark, rx=6),
           _rect(o["x"] - 16 * o["s"], o["y"] - 62 * o["s"], 32 * o["s"],
                 30 * o["s"], dark, rx=4),
           _rect(o["x"] - 13 * o["s"], o["y"] - 32 * o["s"], 10 * o["s"],
                 32 * o["s"], dark, rx=3),
           _rect(o["x"] + 3 * o["s"], o["y"] - 32 * o["s"], 10 * o["s"],
                 32 * o["s"], dark, rx=3)]
    return "".join(out)


def _d_cat(rng, o, pal):
    col = o["color"] or "#e8a04c"
    out = [_rect(o["x"] - 22 * o["s"], o["y"] - 34 * o["s"], 44 * o["s"],
                 34 * o["s"], col, rx=16),
           _circ(o["x"] + 20 * o["s"], o["y"] - 46 * o["s"], 15 * o["s"], col),
           _poly([(o["x"] + 8 * o["s"], o["y"] - 56 * o["s"]),
                  (o["x"] + 10 * o["s"], o["y"] - 70 * o["s"]),
                  (o["x"] + 17 * o["s"], o["y"] - 58 * o["s"])], col),
           _poly([(o["x"] + 22 * o["s"], o["y"] - 58 * o["s"]),
                  (o["x"] + 27 * o["s"], o["y"] - 70 * o["s"]),
                  (o["x"] + 31 * o["s"], o["y"] - 55 * o["s"])], col),
           _path("M %s %s q 14 %s 24 -4" % (_f(o["x"] - 22 * o["s"]),
                                            _f(o["y"] - 26 * o["s"]),
                                            _f(_rr(rng, -6, 14) * o["s"])),
                 stroke=col, sw=6),
           _circ(o["x"] + 15 * o["s"], o["y"] - 48 * o["s"], 2, "#41596b"),
           _circ(o["x"] + 24 * o["s"], o["y"] - 48 * o["s"], 2, "#41596b")]
    for dx in (-14, -2, 10):
        out.append(_rect(o["x"] + dx * o["s"], o["y"] - 4, 8 * o["s"],
                         6 * o["s"], _darken(col, 0.8), rx=2))
    return "".join(out)


def _d_dog(rng, o, pal):
    col = o["color"] or "#b98a5a"
    out = [_rect(o["x"] - 24 * o["s"], o["y"] - 32 * o["s"], 48 * o["s"],
                 32 * o["s"], col, rx=14),
           _circ(o["x"] + 22 * o["s"], o["y"] - 42 * o["s"], 14 * o["s"], col),
           _poly([(o["x"] + 12 * o["s"], o["y"] - 52 * o["s"]),
                  (o["x"] + 8 * o["s"], o["y"] - 66 * o["s"]),
                  (o["x"] + 20 * o["s"], o["y"] - 55 * o["s"])],
                 _darken(col, 0.75)),
           _circ(o["x"] + 32 * o["s"], o["y"] - 38 * o["s"], 4.5, "#41596b"),
           _path("M %s %s q 16 %s 26 -2" % (_f(o["x"] - 24 * o["s"]),
                                            _f(o["y"] - 24 * o["s"]),
                                            _f(_rr(rng, 0, 12) * o["s"])),
                 stroke=col, sw=6)]
    for dx in (-15, -3, 9):
        out.append(_rect(o["x"] + dx * o["s"], o["y"] - 4, 9 * o["s"],
                         6 * o["s"], _darken(col, 0.8), rx=2))
    return "".join(out)


def _d_bird(rng, o, pal):
    col = o["color"] or "#41596b"
    n = max(1, o["n"])
    out = []
    for i in range(n):
        x = o["x"] + i * 34 * o["s"] + _rr(rng, -6, 6)
        y = o["y"] + _rr(rng, -10, 10)
        w = 14 * o["s"]
        out.append(_path("M %s %s q %s %s %s 0 q %s %s %s 0" % (
            _f(x - w), _f(y), _f(w / 2), _f(-w * 0.7), _f(w),
            _f(w / 2), _f(-w * 0.7), _f(w)), stroke=col, sw=3))
    return "".join(out)


def _d_fish(rng, o, pal):
    col = o["color"] or "#57b8e8"
    return (_path("M %s %s q 26 -%s 44 0 q -18 %s -44 0" % (
        _f(o["x"] - 22 * o["s"]), _f(o["y"]), _f(22 * o["s"]),
        _f(22 * o["s"])), fill=col) +
        _poly([(o["x"] - 22 * o["s"], o["y"]),
               (o["x"] - 34 * o["s"], o["y"] - 10 * o["s"]),
               (o["x"] - 34 * o["s"], o["y"] + 10 * o["s"])], col) +
        _circ(o["x"] + 12 * o["s"], o["y"] - 4 * o["s"], 2.5, "#22303f"))


def _d_butterfly(rng, o, pal):
    col = o["color"] or "#8a5fd3"
    out = [_circ(o["x"], o["y"] - 12 * o["s"], 3.5, "#41596b")]
    for sgn in (-1, 1):
        out.append(_circ(o["x"] + sgn * 10 * o["s"], o["y"] - 16 * o["s"],
                         9 * o["s"], col, op=0.85))
        out.append(_circ(o["x"] + sgn * 8 * o["s"], o["y"] - 6 * o["s"],
                         6.5 * o["s"], col, op=0.7))
    return "".join(out)


def _d_rocket(rng, o, pal):
    col = o["color"] or "#d8dee8"
    h = 110 * o["s"]
    out = [_path("M %s %s q -%s %s 0 %s q %s -%s 0 -%s" % (
        _f(o["x"]), _f(o["y"] - h), _f(16 * o["s"]), _f(h * 0.5),
        _f(h), _f(16 * o["s"]), _f(h * 0.5), _f(h)), fill=col,
        stroke="#9aa2ad", sw=2),
        _circ(o["x"], o["y"] - h * 0.72, 7 * o["s"], "#57b8e8",
              stroke="#41596b", sw=2),
        _poly([(o["x"] - 16 * o["s"], o["y"] - h * 0.3),
               (o["x"] - 30 * o["s"], o["y"]),
               (o["x"] - 16 * o["s"], o["y"] - h * 0.12)], "#e04a3a"),
        _poly([(o["x"] + 16 * o["s"], o["y"] - h * 0.3),
               (o["x"] + 30 * o["s"], o["y"]),
               (o["x"] + 16 * o["s"], o["y"] - h * 0.12)], "#e04a3a")]
    if pal["time"] == "ночь" or o.get("flame"):
        out.append(_path("M %s %s q %s %s 0 %s q -%s -%s 0 -%s" % (
            _f(o["x"] - 8 * o["s"]), _f(o["y"] + 2),
            _f(8 * o["s"]), _f(14 * o["s"]), _f(26 * o["s"]),
            _f(8 * o["s"]), _f(12 * o["s"]), _f(26 * o["s"])),
            fill="#f08c2e", op=0.9))
    return "".join(out)


def _d_ufo(rng, o, pal):
    col = o["color"] or "#b8c4d6"
    out = ['<ellipse cx="%s" cy="%s" rx="%s" ry="%s" fill="%s"/>'
           % (_f(o["x"]), _f(o["y"]), _f(42 * o["s"]), _f(12 * o["s"]), col),
           '<ellipse cx="%s" cy="%s" rx="%s" ry="%s" fill="%s" opacity="0.9"/>'
           % (_f(o["x"]), _f(o["y"] - 8 * o["s"]), _f(20 * o["s"]),
              _f(10 * o["s"]), "#9fe0f5")]
    for i in range(4):
        out.append(_circ(o["x"] - 27 * o["s"] + i * 18 * o["s"],
                         o["y"] + 4 * o["s"], 3 * o["s"], "#f7e9a0"))
    if pal["time"] == "ночь":
        out.append(_path("M %s %s L %s %s L %s %s" % (
            _f(o["x"] - 18 * o["s"]), _f(o["y"] + 10 * o["s"]),
            _f(o["x"] + 18 * o["s"]), _f(o["y"] + 10 * o["s"]),
            _f(o["x"]), _f(o["y"] + 60 * o["s"])), fill="#9fe0f5", op=0.25))
    return "".join(out)


def _d_car(rng, o, pal):
    col = o["color"] or "#e04a3a"
    out = [_path("M %s %s q 10 -%s %s -%s q %s -%s %s -%s L %s %s Z" % (
        _f(o["x"] - 50 * o["s"]), _f(o["y"] - 14 * o["s"]),
        _f(4), _f(20 * o["s"]), _f(14 * o["s"]),
        _f(24 * o["s"]), _f(12 * o["s"]), _f(44 * o["s"]), _f(2 * o["s"]),
        _f(o["x"] + 50 * o["s"]), _f(o["y"] - 14 * o["s"])), fill=col),
        _rect(o["x"] - 50 * o["s"], o["y"] - 16 * o["s"], 100 * o["s"],
              10 * o["s"], col, rx=4),
        _circ(o["x"] - 28 * o["s"], o["y"] - 6, 9 * o["s"], "#41596b"),
        _circ(o["x"] + 28 * o["s"], o["y"] - 6, 9 * o["s"], "#41596b"),
        _circ(o["x"] - 28 * o["s"], o["y"] - 6, 4 * o["s"], "#b8c4d6"),
        _circ(o["x"] + 28 * o["s"], o["y"] - 6, 4 * o["s"], "#b8c4d6"),
        _rect(o["x"] + 44 * o["s"], o["y"] - 22 * o["s"], 8 * o["s"],
              5 * o["s"], "#f7e9a0", rx=2)]
    return "".join(out)


def _d_ship(rng, o, pal):
    col = o["color"] or "#b0503c"
    out = [_path("M %s %s L %s %s L %s %s L %s %s Z" % (
        _f(o["x"] - 44 * o["s"]), _f(o["y"] - 14 * o["s"]),
        _f(o["x"] + 44 * o["s"]), _f(o["y"] - 14 * o["s"]),
        _f(o["x"] + 32 * o["s"]), _f(o["y"]),
        _f(o["x"] - 32 * o["s"]), _f(o["y"])), fill=col),
        _rect(o["x"] - 2 * o["s"], o["y"] - 64 * o["s"], 4 * o["s"],
              50 * o["s"], "#5a4a3a"),
        _poly([(o["x"] + 2 * o["s"], o["y"] - 62 * o["s"]),
               (o["x"] + 30 * o["s"], o["y"] - 18 * o["s"]),
               (o["x"] + 2 * o["s"], o["y"] - 18 * o["s"])], "#f4f7fb"),
        _poly([(o["x"] - 2 * o["s"], o["y"] - 56 * o["s"]),
               (o["x"] - 24 * o["s"], o["y"] - 18 * o["s"]),
               (o["x"] - 2 * o["s"], o["y"] - 18 * o["s"])], col, op=0.9)]
    return "".join(out)


def _d_balloon(rng, o, pal):
    col = o["color"] or "#e04a3a"
    out = ['<ellipse cx="%s" cy="%s" rx="%s" ry="%s" fill="%s"/>' % (
        _f(o["x"]), _f(o["y"] - 46 * o["s"]), _f(22 * o["s"]),
        _f(28 * o["s"]), col),
        _rect(o["x"] - 8 * o["s"], o["y"] - 14 * o["s"], 16 * o["s"],
              12 * o["s"], "#8a5a34", rx=3),
        _line(o["x"] - 8 * o["s"], o["y"] - 14 * o["s"],
              o["x"] - 4 * o["s"], o["y"] - 20 * o["s"], "#5a6472", 1.5),
        _line(o["x"] + 8 * o["s"], o["y"] - 14 * o["s"],
              o["x"] + 4 * o["s"], o["y"] - 20 * o["s"], "#5a6472", 1.5)]
    return "".join(out)


def _d_plane(rng, o, pal):
    col = o["color"] or "#d8dee8"
    return (_path("M %s %s q %s -%s %s -%s q %s 0 %s %s L %s %s Z" % (
        _f(o["x"] - 50 * o["s"]), _f(o["y"]),
        _f(30 * o["s"]), _f(10 * o["s"]), _f(70 * o["s"]), _f(12 * o["s"]),
        _f(14 * o["s"]), _f(12 * o["s"]), _f(4 * o["s"]),
        _f(o["x"] - 50 * o["s"]), _f(o["y"])), fill=col,
        stroke="#9aa2ad", sw=2) +
        _poly([(o["x"] - 6 * o["s"], o["y"]),
               (o["x"] + 6 * o["s"], o["y"]),
               (o["x"] - 12 * o["s"], o["y"] + 26 * o["s"])],
              _darken(col, 0.8)) +
        _poly([(o["x"] - 40 * o["s"], o["y"] - 2),
               (o["x"] - 32 * o["s"], o["y"] - 2),
               (o["x"] - 52 * o["s"], o["y"] - 20 * o["s"])],
              _darken(col, 0.8)))


def _d_person(rng, o, pal):
    col = o["color"] or "#4a6fd8"
    out = [_circ(o["x"], o["y"] - 74 * o["s"], 10 * o["s"], "#f0c8a0"),
           _rect(o["x"] - 11 * o["s"], o["y"] - 62 * o["s"], 22 * o["s"],
                 34 * o["s"], col, rx=8),
           _rect(o["x"] - 8 * o["s"], o["y"] - 28 * o["s"], 7 * o["s"],
                 28 * o["s"], _darken(col, 0.7), rx=3),
           _rect(o["x"] + 2 * o["s"], o["y"] - 28 * o["s"], 7 * o["s"],
                 28 * o["s"], _darken(col, 0.7), rx=3)]
    return "".join(out)


def _d_mushroom(rng, o, pal):
    col = o["color"] or "#e04a3a"
    return (_path("M %s %s q %s -%s %s 0 Z" % (
        _f(o["x"] - 22 * o["s"]), _f(o["y"] - 22 * o["s"]),
        _f(22 * o["s"]), _f(26 * o["s"]), _f(44 * o["s"])), fill=col) +
        _rect(o["x"] - 7 * o["s"], o["y"] - 22 * o["s"], 14 * o["s"],
              22 * o["s"], "#f4f0e6", rx=5) +
        _circ(o["x"] - 9 * o["s"], o["y"] - 32 * o["s"], 3.5, "#f4f0e6") +
        _circ(o["x"] + 8 * o["s"], o["y"] - 34 * o["s"], 3, "#f4f0e6"))


def _d_dragon(rng, o, pal):
    col = o["color"] or "#3f9d4f"
    out = [_path("M %s %s q %s -%s %s -%s q %s %s %s %s q %s -%s %s -%s" % (
        _f(o["x"] - 60 * o["s"]), _f(o["y"] - 30 * o["s"]),
        _f(40 * o["s"]), _f(30 * o["s"]), _f(80 * o["s"]), _f(10 * o["s"]),
        _f(20 * o["s"]), _f(20 * o["s"]), _f(50 * o["s"]), _f(20 * o["s"]),
        _f(10 * o["s"]), _f(26 * o["s"]), _f(46 * o["s"]), _f(6 * o["s"])),
        fill=col),
        _circ(o["x"] + 26 * o["s"], o["y"] - 46 * o["s"], 11 * o["s"], col),
        _circ(o["x"] + 29 * o["s"], o["y"] - 49 * o["s"], 2, "#f4f7fb"),
        _poly([(o["x"] + 22 * o["s"], o["y"] - 55 * o["s"]),
               (o["x"] + 18 * o["s"], o["y"] - 68 * o["s"]),
               (o["x"] + 30 * o["s"], o["y"] - 57 * o["s"])],
              _darken(col, 0.7)),
        _path("M %s %s q -%s -%s -%s -%s" % (
            _f(o["x"] - 58 * o["s"]), _f(o["y"] - 32 * o["s"]),
            _f(10 * o["s"]), _f(26 * o["s"]), _f(28 * o["s"]),
            _f(20 * o["s"])), stroke=col, sw=8)]
    return "".join(out)


def _d_city(rng, o, pal):
    col = o["color"] or "#8e99ad"
    out = []
    xx = o["x"] - 90 * o["s"]
    for i in range(5):
        w = _rr(rng, 26, 40) * o["s"]
        h = _rr(rng, 50, 110) * o["s"]
        out.append(_rect(xx, o["y"] - h, w, h, _darken(col, 0.8 + i * 0.06),
                         rx=2))
        for wx in range(2):
            for wy in range(int(h // 22)):
                if rng.random() < 0.7:
                    out.append(_rect(xx + 5 + wx * (w / 2), o["y"] - h + 6 +
                                     wy * 20, 5, 7,
                                     "#f7e9a0" if pal["time"] == "ночь"
                                     else "#cfe0ee", rx=1))
        xx += w + 8
    return "".join(out)


def _d_campfire(rng, o, pal):
    out = [_poly([(o["x"] - 20 * o["s"], o["y"]),
                  (o["x"] + 20 * o["s"], o["y"] - 4),
                  (o["x"] + 12 * o["s"], o["y"] + 2)], "#8a5a34"),
           _poly([(o["x"] - 20 * o["s"], o["y"] - 4),
                  (o["x"] + 20 * o["s"], o["y"]),
                  (o["x"] - 12 * o["s"], o["y"] + 2)], "#7a5230"),
           _path("M %s %s q -%s -%s %s -%s q %s %s %s %s q -%s -%s -%s %s" % (
               _f(o["x"] - 10 * o["s"]), _f(o["y"] - 2),
               _f(2), _f(20 * o["s"]), _f(10 * o["s"]), _f(26 * o["s"]),
               _f(6 * o["s"]), _f(-14 * o["s"]), _f(14 * o["s"]),
               _f(14 * o["s"]), _f(2 * o["s"]), _f(12 * o["s"]),
               _f(10 * o["s"]), _f(2)), fill="#f08c2e"),
           _path("M %s %s q %s -%s %s -%s q %s -%s %s %s" % (
               _f(o["x"] - 4 * o["s"]), _f(o["y"] - 4),
               _f(2), _f(12 * o["s"]), _f(4 * o["s"]), _f(16 * o["s"]),
               _f(4 * o["s"]), _f(4 * o["s"]), _f(2), _f(2)), fill="#f7e9a0")]
    if pal["time"] == "ночь":
        out.append(_circ(o["x"], o["y"] - 14 * o["s"], 34 * o["s"],
                         "#f08c2e", op=0.15))
    return "".join(out)


def _d_cactus(rng, o, pal):
    col = o["color"] or "#3f9d4f"
    return (_rect(o["x"] - 8 * o["s"], o["y"] - 64 * o["s"], 16 * o["s"],
                  64 * o["s"], col, rx=8) +
            _rect(o["x"] - 26 * o["s"], o["y"] - 44 * o["s"], 10 * o["s"],
                  26 * o["s"], col, rx=5) +
            _rect(o["x"] - 26 * o["s"], o["y"] - 44 * o["s"], 26 * o["s"],
                  9 * o["s"], col, rx=4) +
            _rect(o["x"] + 16 * o["s"], o["y"] - 52 * o["s"], 10 * o["s"],
                  30 * o["s"], col, rx=5) +
            _rect(o["x"] + 8 * o["s"], o["y"] - 52 * o["s"], 18 * o["s"],
                  9 * o["s"], col, rx=4))


def _d_bench(rng, o, pal):
    col = o["color"] or "#8a5a34"
    return (_rect(o["x"] - 34 * o["s"], o["y"] - 26 * o["s"], 68 * o["s"],
                  7 * o["s"], col, rx=2) +
            _rect(o["x"] - 34 * o["s"], o["y"] - 44 * o["s"], 68 * o["s"],
                  6 * o["s"], col, rx=2) +
            _rect(o["x"] - 30 * o["s"], o["y"] - 20 * o["s"], 6 * o["s"],
                  20 * o["s"], _darken(col, 0.8)) +
            _rect(o["x"] + 24 * o["s"], o["y"] - 20 * o["s"], 6 * o["s"],
                  20 * o["s"], _darken(col, 0.8)) +
            _rect(o["x"] - 30 * o["s"], o["y"] - 44 * o["s"], 6 * o["s"],
                  28 * o["s"], _darken(col, 0.8)) +
            _rect(o["x"] + 24 * o["s"], o["y"] - 44 * o["s"], 6 * o["s"],
                  28 * o["s"], _darken(col, 0.8)))


def _d_bridge(rng, o, pal):
    col = o["color"] or "#9aa2ad"
    out = [_rect(o["x"] - 120 * o["s"], o["y"] - 40 * o["s"],
                 240 * o["s"], 8 * o["s"], col, rx=3)]
    out.append(_path("M %s %s q %s %s %s 0" % (
        _f(o["x"] - 120 * o["s"]), _f(o["y"] - 40 * o["s"]),
        _f(120 * o["s"]), _f(34 * o["s"]), _f(240 * o["s"])),
        stroke=_darken(col, 0.8), sw=5))
    for dx in (-110, -70, 0, 70, 110):
        out.append(_line(o["x"] + dx * o["s"] * 0.9,
                         o["y"] - 40 * o["s"] - (14 if abs(dx) < 100 else 0),
                         o["x"] + dx * o["s"] * 0.9,
                         o["y"] - 40 * o["s"] + 26 * o["s"],
                         _darken(col, 0.8), 4))
    return "".join(out)


def _d_hill(rng, o, pal):
    col = o["color"] or None
    c = col or ("#eef3fa" if pal["time"] == "зима" else "#79c063")
    return '<ellipse cx="%s" cy="%s" rx="%s" ry="%s" fill="%s"/>' % (
        _f(o["x"]), _f(o["y"] + 26), _f(160 * o["s"]), _f(44 * o["s"]), c)


_DRAWERS = {"sun": _d_sun, "moon": _d_moon, "stars": _d_stars,
            "cloud": _d_cloud, "rainbow": _d_rainbow, "mountains": _d_mountains,
            "water": _d_water, "tree": _d_tree, "pine": _d_pine,
            "house": _d_house, "castle": _d_castle, "lighthouse": _d_lighthouse,
            "road": _d_road, "flower": _d_flower, "grass": _d_grass,
            "snowman": _d_snowman, "robot": _d_robot, "cat": _d_cat,
            "dog": _d_dog, "bird": _d_bird, "fish": _d_fish,
            "butterfly": _d_butterfly, "rocket": _d_rocket, "ufo": _d_ufo,
            "car": _d_car, "ship": _d_ship, "balloon": _d_balloon,
            "plane": _d_plane, "person": _d_person, "mushroom": _d_mushroom,
            "dragon": _d_dragon, "city": _d_city, "campfire": _d_campfire,
            "cactus": _d_cactus, "bench": _d_bench,
            "bridge_draw": _d_bridge, "hill": _d_hill, "cat_big": _d_cat}

# на «земле» стоят: рисуются у линии горизонта
_ON_GROUND = {"tree", "pine", "house", "castle", "lighthouse", "road",
              "flower", "grass", "snowman", "robot", "cat", "cat_big", "dog",
              "car", "person", "mushroom", "cactus", "bench", "campfire",
              "hill", "city", "ship", "bridge_draw"}
_IN_SKY = {"sun", "moon", "stars", "cloud", "rainbow", "bird", "ufo",
           "balloon", "plane", "rocket"}

# порядок отрисовки: дальние раньше
_DRAW_ORDER = {"rainbow": 0, "mountains": 1, "city": 2, "cloud": 3, "stars": 3,
               "sun": 4, "moon": 4, "ufo": 4, "bird": 5, "balloon": 5,
               "plane": 5, "rocket": 5, "hill": 6, "water": 7, "road": 8,
               "bridge_draw": 9, "house": 10, "castle": 10, "lighthouse": 10,
               "tree": 11, "pine": 11, "robot": 11, "person": 11, "cat": 12,
               "cat_big": 12, "dog": 12, "car": 12, "ship": 12, "snowman": 12,
               "dragon": 12, "mushroom": 13, "cactus": 13, "bench": 13,
               "campfire": 13, "flower": 14, "grass": 14,
               "butterfly": 15, "fish": 15}

W, H = 640, 430
GROUND_Y = 340

# --------------------------- сборка сцены -----------------------------------


def _place_objects(objs, rng, seen_kinds):
    """Разложить предметы по холсту: наземные — у горизонта, небесные —
    в небе, вода — под горизонтом. Не даём предметам слипаться."""
    placed = []
    ground_x = [0.0]              # занятые наземные позиции (центры)
    for o in objs:
        kind, n = o["kind"], o["n"]
        for i in range(n):
            ob = {"kind": kind, "s": _rr(rng, 0.85, 1.15),
                  "color": o.get("color")}
            if o.get("color"):
                ob["color"] = o["color"] if i == 0 else None
            if kind in ("mountains", "rainbow", "city", "bridge_draw"):
                if any(p["kind"] == kind for p in placed):
                    continue                      # фон — один раз
                ob["x"], ob["y"] = W / 2, GROUND_Y
                ob["s"] = _rr(rng, 0.9, 1.2)
            elif kind in _IN_SKY:
                ob["x"] = _rr(rng, 90, W - 90)
                ob["y"] = _rr(rng, 55, 150)
                if kind == "stars":
                    ob["x"], ob["y"] = W / 2, 110
                if kind == "rocket":
                    ob["y"] = _rr(rng, 90, 160)
                if kind == "sun" or kind == "moon":
                    ob["x"] = _rr(rng, 430, 560)
                    ob["y"] = _rr(rng, 70, 110)
            elif kind == "water":
                if any(p["kind"] == "water" for p in placed):
                    continue
                ob["x"], ob["y"] = W / 2, GROUND_Y + 4
                ob["s"] = 1.1
            elif kind == "fish":
                ob["x"], ob["y"] = _rr(rng, 200, 440), GROUND_Y + 30
                ob["s"] = _rr(rng, 0.8, 1.0)
            else:
                # наземные: раскладываем по земле слева направо
                x = _rr(rng, 60, W - 60)
                tries = 0
                while any(abs(x - g) < 80 for g in ground_x) and tries < 12:
                    x = _rr(rng, 60, W - 60)
                    tries += 1
                ground_x.append(x)
                ob["x"] = x
                ob["y"] = GROUND_Y + _rr(rng, 0, 24)
                if kind == "cat_big":
                    ob["s"] *= 1.9
                if kind in ("flower", "grass", "mushroom"):
                    ob["s"] = _rr(rng, 0.7, 1.1)
            placed.append(ob)
    return placed


def build_scene(description, seed=None):
    """Описание -> сцена {objects, time, seed, text}. Возврат (scene, ok,
    msg): если предметы не распознаны — ok=False и честное сообщение."""
    text_low = str(description or "").lower()
    objs = _parse_objects(text_low)
    if not objs:
        return None, False, (
            "Не распознал, что рисовать. Я умею изображать предметы: %s — "
            "и понимать время суток (ночь, закат, зима) и цвета. Скажи, "
            "например: «нарисуй дом у реки и дерево ночью»." % _KNOWN_LIST)
    time_name = _parse_time(text_low)
    if seed is None:
        st = _load()
        st["counter"] = int(st.get("counter") or 1) + 1
        seed = _seed_for(text_low, st["counter"])
    rng = random.Random(seed)
    if time_name == "ночь" or "звезд" in text_low or "звёзд" in text_low:
        if not any(o["kind"] == "stars" for o in objs):
            objs.append({"kind": "stars", "n": 1, "color": "#f4f7fb"})
    if time_name == "день" and "звезд" not in text_low and \
            not any(o["kind"] in ("sun", "cloud", "moon", "rainbow")
                    for o in objs):
        objs.append({"kind": "cloud", "n": 1, "color": None})
    placed = _place_objects(objs, rng, set())
    placed.sort(key=lambda o: _DRAW_ORDER.get(o["kind"], 10))
    scene = {"objects": placed, "time": time_name, "seed": seed,
             "text": str(description)[:160]}
    return scene, True, ""


def render_scene(scene, width=640):
    """Сцена -> SVG (односторонний, без переносов строк)."""
    pal = _time_palette(scene.get("time", "день"))
    pal["time"] = scene.get("time", "день")
    rng = random.Random(int(scene.get("seed") or 0) + 7)
    parts = ['<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %d %d" '
             'width="%d" height="%d" class="im-svg">' % (W, H, width,
                                                         int(width * H / W))]
    parts.append('<defs><linearGradient id="imgsky" x1="0" y1="0" x2="0" '
                 'y2="1"><stop offset="0" stop-color="%s"/><stop offset="1" '
                 'stop-color="%s"/></linearGradient></defs>'
                 % (pal["sky1"], pal["sky2"]))
    parts.append(_rect(0, 0, W, GROUND_Y + 30, "url(#imgsky)", rx=0))
    parts.append('<ellipse cx="%d" cy="%d" rx="%d" ry="%d" fill="%s" '
                 'opacity="0.7"/>' % (W / 2, GROUND_Y + 55, 380, 60,
                                      pal["haze"]))
    parts.append(_rect(0, GROUND_Y + 20, W, H - GROUND_Y - 20,
                       pal["ground"], rx=0))
    if pal["time"] == "зима":
        for _i in range(40):
            parts.append(_circ(_rr(rng, 0, W), _rr(rng, 0, H), _rr(rng, 1, 3),
                               "#ffffff", op=_rr(rng, 0.5, 0.95)))
    for o in scene.get("objects", []):
        fn = _DRAWERS.get(o["kind"])
        if fn:
            try:
                parts.append(fn(rng, o, pal))
            except Exception:
                continue
    parts.append(_rect(0, 0, W, H, "none", rx=14, stroke="#c9d4e2", sw=2))
    parts.append("</svg>")
    return "".join(parts)

# ------------------------------ графики -------------------------------------
_GRAPH_Q = re.compile(r"график|построй функци|начерти график", re.I)
_RANGE_RE = re.compile(r"от\s*(-?\d+(?:[.,]\d+)?)\s*до\s*(-?\d+(?:[.,]\d+)?)",
                       re.I)
_FUNC_CLEAN_RE = re.compile(r"[.;!?,]+$")


def _parse_graph(text):
    """«нарисуй график y=x^2 от -2 до 3» -> {expr, x0, x1} | (None, why)."""
    low = text.lower()
    m = re.search(r"y\s*=\s*(.+)", low)
    expr = m.group(1) if m else None
    if not expr:
        m = re.search(r"график[а-яё]*\s+(.+)", low)
        expr = m.group(1) if m else None
    if not expr:
        return None, "не понял выражение — напиши, например: «график y=x^2»"
    expr = _FUNC_CLEAN_RE.sub("", expr).strip()
    # хвост с диапазоном вырезаем из выражения («x^2 от -3 до 3» -> «x^2»)
    expr = re.split(r"\s+(?:от|на|при|между)\s+", expr)[0].strip()
    x0, x1 = -6.0, 6.0
    mr = _RANGE_RE.search(low)
    if mr:
        try:
            x0 = float(mr.group(1).replace(",", "."))
            x1 = float(mr.group(2).replace(",", "."))
        except ValueError:
            pass
    if re.search(r"\b(sin|cos|tan|tg)\b", expr) and not mr:
        x0, x1 = -6.5, 6.5
    # «2пи» / «2π» в границах
    expr = expr.replace("π", "pi").replace("²", "^2").replace("³", "^3")
    return {"expr": expr, "x0": x0, "x1": x1}, ""


def render_graph(spec):
    """График функции -> (svg, latex_expr, err). Считает по точкам через
    mathmodels.safe_eval (белый список операций) — без exec."""
    import mathmodels
    expr = spec["expr"]
    x0, x1 = float(spec["x0"]), float(spec["x1"])
    if x1 <= x0:
        x0, x1 = x1, x0
    if x1 - x0 > 1e9:
        return "", "", "слишком широкий диапазон"
    N = 220
    xs, ys = [], []
    err = ""
    for i in range(N + 1):
        x = x0 + (x1 - x0) * i / N
        try:
            y = mathmodels.safe_eval(expr, {"x": x})
        except Exception:
            # точка не считается (деление на ноль, корень) — пропускаем
            continue
        if isinstance(y, (int, float)) and math.isfinite(y):
            xs.append(x)
            ys.append(float(y))
    if len(xs) < 5:
        return "", "", ("функция не посчиталась ни в одной точке — проверь "
                        "выражение (я понимаю + - * / ^, скобки, sin, cos, "
                        "tan, sqrt, log, exp, pi)")
    ymin, ymax = min(ys), max(ys)
    if ymax - ymin < 1e-9:
        ymin, ymax = ymin - 1, ymax + 1
    pad = (ymax - ymin) * 0.12
    ymin -= pad
    ymax += pad
    gw, gh = 560, 360
    ml, mr_, mt, mb = 46, 14, 18, 34
    def X(x):
        return ml + (x - x0) / (x1 - x0) * (gw - ml - mr_)
    def Y(y):
        return mt + (ymax - y) / (ymax - ymin) * (gh - mt - mb)
    parts = ['<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %d %d" '
             'width="%d" height="%d" class="gr-svg">'
             % (gw, gh, min(gw, 560), gh)]
    parts.append(_rect(0, 0, gw, gh, "#ffffff", rx=10))
    # сетка
    for i in range(9):
        gx = ml + i * (gw - ml - mr_) / 8.0
        parts.append(_line(gx, mt, gx, gh - mb, "#eef1f6", 1))
    for i in range(7):
        gy = mt + i * (gh - mt - mb) / 6.0
        parts.append(_line(ml, gy, gw - mr_, gy, "#eef1f6", 1))
    # оси (если в кадре)
    if x0 <= 0 <= x1:
        parts.append(_line(X(0), mt, X(0), gh - mb, "#9aa2ad", 1.6))
    if ymin <= 0 <= ymax:
        parts.append(_line(ml, Y(0), gw - mr_, Y(0), "#9aa2ad", 1.6))
    # подписи осей
    def _fmt(v):
        return ("%.4g" % v)            # v3.229: длинные подписи оси Y обрезались слева
    for i in range(5):
        v = x0 + (x1 - x0) * i / 4.0
        parts.append(_txt(X(v), gh - mb + 16, _fmt(v), 11, "#7a8899"))
        vv = ymax - (ymax - ymin) * i / 4.0
        parts.append(_txt(ml - 8, Y(vv) + 4, _fmt(vv), 11, "#7a8899",
                          anchor="end"))
    # кривая (ломаной по точкам, разрывы — отдельными кусками)
    seg, segs = [], []
    prev_x = None
    for x, y in zip(xs, ys):
        if prev_x is not None and x - prev_x > (x1 - x0) / N * 1.5:
            if len(seg) > 1:
                segs.append(seg)
            seg = []
        seg.append((X(x), Y(y)))
        prev_x = x
    if len(seg) > 1:
        segs.append(seg)
    for seg in segs:
        parts.append('<polyline points="%s" fill="none" stroke="#3a6fd8" '
                     'stroke-width="2.6" stroke-linejoin="round"/>'
                     % " ".join("%s,%s" % (_f(px), _f(py)) for px, py in seg))
    parts.append(_txt(gw / 2, mt - 4, "y = %s" % expr[:60], 13, "#41596b"))
    parts.append("</svg>")
    latex = latexlite.from_plain("y = " + expr.replace("**", "^"))
    return "".join(parts), latex, err

# ------------------------------- команды ------------------------------------
_DRAW_Q = re.compile(r"^\s*(нарисуй|нарисовать|изобрази|сгенерируй\s+картинку"
                     r"|создай\s+картинку|сделай\s+картинку|нарисуйте)\s+(.+)$",
                     re.I)
_REDRAW_Q = re.compile(r"^\s*(перерисуй|перерисовать|измени\s+картинку"
                       r"|дорисуй|добавь\s+на\s+картинку|перекрась|перерисовка"
                       r"|убери\s+(?:с\s+картинк[иа]\s+)?"
                       r"|удали\s+(?:с\s+картинк[иа]\s+)?)\s*(.*)$", re.I)
_ERASE_Q = re.compile(r"^\s*(сотри\s+картинку|очисти\s+холст|убери\s+картинку"
                      r"|сотри всё)\s*[.!]*$", re.I)
_INFO_Q = re.compile(r"^\s*(что\s+нарисовано|какая\s+картинка|покажи\s+"
                     r"картинку|что\s+на\s+холсте|что\s+ты\s+нарисовал)"
                     r"\s*[?.!]*$", re.I)
# только график (без «нарисуй» — его ловит _DRAW_Q, но график проверяем в нём)
# v3.27: «найди референс слона», «покажи как выглядит жираф»
_REF_Q = re.compile(r"^\s*(?:найди|покажи|дай|подбери)?\s*(?:референс\w*|"
                    r"образ[еэ]ц\w*|картинк\w*|фото\w*|изображени\w*)\s+"
                    r"(?:для\s+|с\s+)?(.+)$|^\s*(?:покажи\s+)?как\s+выгляд\w+"
                    r"\s+(.+?)[?.!]*$", re.I)


def reference(subject):
    """v3.27: РЕФЕРЕНС ДЛЯ РИСОВАНИЯ — заявка владельца: «надо найти
    референсы для рисования картинок, так как он не рисует». Предмета нет
    среди заготовок — облако находит его изображение в Википедии и
    показывает с подписью (честно: это фото-референс, а не рисунок).
    Возврат (reply, info)."""
    import base64
    subj = re.sub(r"\b(ночью|днём|днем|вечером|утром|на\s+закате|зимой|"
                  r"летом|красн\w+|зел[её]н\w+|син\w+|ж[её]лт\w+)\b", " ",
                  str(subject or ""), flags=re.I)
    subj = " ".join(subj.split()).strip(" ,.!?")[:80]
    if not subj:
        return ("Чей референс найти? Скажи, например: «найди референс "
                "слона»."), {"imagine": "ref-empty"}
    if not store.settings().get("web_on"):
        return ("Нарисовать «%s» своими фигурами я пока не умею, а "
                "референс искать негде: интернет выключен в настройках "
                "(web_on). Умею рисовать: %s." % (subj, _KNOWN_LIST)), \
            {"imagine": "ref-offline"}
    try:
        import webfetch
        r = webfetch.wiki_image(subj)
    except Exception as e:
        r = {"ok": False, "error": str(e)}
    if not r.get("ok"):
        return ("Нарисовать «%s» своими фигурами пока не умею, и референс "
                "не нашёлся: %s. Умею рисовать: %s." % (
                    subj, r.get("error"), _KNOWN_LIST)), \
            {"imagine": "ref-fail"}
    b64 = base64.b64encode(r["data"]).decode("ascii")
    cap = "Референс: Википедия «%s»" % r["title"]
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 480 440" '
           'width="480" height="440"><rect width="480" height="440" '
           'fill="#f4f6f8" rx="8"/><image href="data:%s;base64,%s" x="10" '
           'y="10" width="460" height="390" preserveAspectRatio="xMidYMid '
           'meet"/>%s</svg>' % (r["mime"], b64,
                               _txt(240, 426, cap[:70], 13, "#41596b")))
    return _wrap("Своими фигурами «%s» пока не рисую — нашла референс: "
                 "изображение из Википедии (статья «%s»)%s. Нарисовать могу "
                 "сцену из того, что умею: %s." % (
                     subj, r["title"],
                     (" — " + r["page"]) if r.get("page") else "",
                     _KNOWN_LIST), svg), \
        {"imagine": "reference", "ref": r["title"]}


def route(text, low):
    """Точка входа от brain._think. Возврат (reply, info) или None,
    если реплика не про рисование. long_mode не раздуваем: рисунок и
    формула — артефакты, а не текст."""
    try:                                 # v3.62: составить формулу в LaTeX
        import latexgen
        r_lx = latexgen.route(text, low)
        if r_lx:
            return r_lx
    except Exception:
        pass
    m = _REF_Q.match(text)
    if m and not _INFO_Q.match(low):
        return reference(m.group(1) or m.group(2))
    if _ERASE_Q.match(low):
        with LOCK:
            st = _load()
            st["scene"] = None
            _save()
        return ("Холст очищен: картинки больше нет. Скажи «нарисуй …» — "
                "нарисую новую."), {"imagine": "erase"}

    if _INFO_Q.match(low):
        with LOCK:
            st = _load()
            sc = st.get("scene")
        if not sc:
            return ("Пока ничего не нарисовано — холст пуст. Скажи «нарисуй "
                    "…»: я умею дом, дерево, кота, робота, ракету, горы, "
                    "море, ночь, закат, зиму и графики функций."), \
                {"imagine": "info"}
        names = []
        for o in sc.get("objects", []):
            lab = next((v[0] for k, v in KINDS.items() if k == o["kind"]),
                       o["kind"])
            names.append(lab)
        cnt = {}
        for n in names:
            cnt[n] = cnt.get(n, 0) + 1
        listing = ", ".join(
            ("%d× %s" % (c, n) if c > 1 else n) for n, c in cnt.items())
        svg = render_scene(sc)
        return _wrap("Сейчас на холсте: %s (время суток: %s). Могу изменить: "
                "скажи «перерисуй …» (добавить предмет, ночь/закат/зима, "
                "цвет) или «сотри картинку»." % (listing, sc.get("time")),
                svg), {"imagine": "info", "objects": names}

    m = _DRAW_Q.match(text)
    if m:
        desc = m.group(2).strip()[:200]
        # график?
        if _GRAPH_Q.search(desc):
            return _do_graph(desc)
        scene, ok, msg = build_scene(desc)
        if not ok:
            # v3.27: предмета нет среди заготовок — референс из Википедии
            return reference(desc)
        with LOCK:
            st = _load()
            st["scene"] = scene
            _save()
        svg = render_scene(scene)
        names = _obj_labels(scene)
        return _wrap("Нарисовал: %s (время суток: %s). Скажи «перерисуй …» — "
                "изменю картинку, или «сотри картинку»." % (
                    ", ".join(names), scene["time"]), svg), \
            {"imagine": "draw", "objects": names}


    m = _REDRAW_Q.match(text)
    if m:
        mods = m.group(2).strip()[:200]
        with LOCK:
            st = _load()
            sc = st.get("scene")
        if not sc:
            return ("Перерисовывать пока нечего: холст пуст. Скажи «нарисуй "
                    "…», и появится первая картинка."), {"imagine": "redraw"}
        if _GRAPH_Q.search((mods or "")):
            return _do_graph(mods)
        # ВАЖНО: в правки уходит ПОЛНАЯ команда — иначе маркеры
        # «убери/без/удали» съедаются регэкспом глагола
        new_scene, added, removed = _apply_mods(sc, low)
        with LOCK:
            st = _load()
            st["scene"] = new_scene
            _save()
        svg = render_scene(new_scene)
        names = _obj_labels(new_scene)
        note = ("Обновил картинку: %s. Время суток: %s." %
                (", ".join(names), new_scene["time"]))
        if added or removed:
            extra = ""
            if added:
                extra += " Добавил: %s." % ", ".join(added)
            if removed:
                extra += " Убрал: %s." % ", ".join(removed)
            note = ("Перерисовал:%s На холсте теперь: %s. Время суток: %s."
                    % (extra, ", ".join(names), new_scene["time"]))
        elif mods:
            note = ("Перерисовал («%s»): %s. Время суток: %s." %
                    (mods, ", ".join(names), new_scene["time"]))
        return _wrap(note, svg), {"imagine": "redraw", "objects": names}

    return None


def _obj_labels(scene):
    out = []
    for o in scene.get("objects", []):
        lab = next((v[0] for k, v in KINDS.items() if k == o["kind"]),
                   o["kind"])
        out.append(lab)
    return out


def _apply_mods(scene, mods):
    """Перерисовка: новый seed + правки из текста. Поддерживаем: убрать
    предметы («без кота», «убери кота»), добавить («дорисуй кота»,
    «и ракету» — всё, что названо и ещё не нарисовано), сменить время
    суток (ночь/закат/день/зима), перекрасить (названные цвета — по
    порядку предметов). Ничего не названо — просто новый вариант той
    же сцены (другая расстановка)."""
    low = str(mods or "").lower().strip()
    objs = [{"kind": o["kind"], "n": o.get("n", 1), "color": o.get("color")}
            for o in scene.get("objects", [])]

    def _rm_marked(s):
        return re.search(r"(без\s+|убери\s+|удали\s+)" + "(" + s + ")", low)

    removed = []
    for kind, (label, pat) in KINDS.items():
        if _rm_marked(pat):
            removed.append(label)
            objs = [o for o in objs if o["kind"] != kind]
    # добавить предметы: всё названное, чего на холсте ещё нет
    added = []
    if low:
        for kind, (label, pat) in KINDS.items():
            if any(o["kind"] == kind for o in objs):
                continue
            if _rm_marked(pat):
                continue
            if re.search(r"\b(" + pat + r")", low):
                added.append(label)
                objs.append({"kind": kind, "n": 1, "color": None})
    # время суток: меняем только если явно названо
    new_time = scene.get("time", "день")
    if re.search(r"ноч|закат|рассвет|сумерк|вечер|зим|снег|день|утр", low):
        new_time = _parse_time(low)
    # цвета: названные — по порядку предметов
    colors = _find_colors(low)
    if colors:
        for i, o in enumerate(objs):
            o["color"] = colors[i % len(colors)]
    # новая вариация (другой seed = другая расстановка)
    st = _load()
    st["counter"] = int(st.get("counter") or 1) + 1
    seed = _seed_for(str(mods) + str(st["counter"]), st["counter"])
    rng = random.Random(seed)
    placed = _place_objects(objs, rng, set())
    placed.sort(key=lambda o: _DRAW_ORDER.get(o["kind"], 10))
    ns = {"objects": placed, "time": new_time, "seed": seed,
          "text": (scene.get("text", "") + " | " + low)[:160]}
    return ns, added, removed


def _do_graph(desc):
    spec, err = _parse_graph(desc)
    if not spec:
        return ("Не понял, какой график рисовать. %s" % err), \
            {"imagine": "graph-fail"}
    svg, latex, err2 = render_graph(spec)
    if err2:
        return ("График не получился: %s." % err2), {"imagine": "graph-fail"}
    reply = ("График функции y = %s на отрезке от %s до %s. Ось X — "
             "горизонтальная, ось Y — вертикальная; сетка показывает шаг. "
             "Скажи «график …» с новым выражением или диапазоном («от -2 "
             "до 5») — нарисую другой." % (spec["expr"], _g(spec["x0"]),
                                           _g(spec["x1"])))
    if latex:
        reply += "\n" + latexlite.block(latex, "y = " + spec["expr"])
    reply = _wrap(reply, svg)
    return reply, {"imagine": "graph", "expr": spec["expr"]}


def _g(v):
    return ("%g" % float(v))


def _wrap(reply, svg):
    """Добавить картинку в ответ (чат-разметка)."""
    return reply + "\n[КАРТИНКА]%s[/КАРТИНКА]" % svg
