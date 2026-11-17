# -*- coding: utf-8 -*-
# ===========================================================================
# latexlite.py — LATEX-ДВИЖОК ОБЛАКА (v3.16, только stdlib)
# ---------------------------------------------------------------------------
# ЗАЧЕМ (заявка владельца): «введи latex движок, чтобы ИИ писал формулы».
# Мозг должен не только ВЫЧИСЛЯТЬ формулы (mathmodels), но и красиво
# РИСОВАТЬ их в чате. Внешние движки (KaTeX/MathJax/latex) нельзя — платформа
# живёт на чистом stdlib. Поэтому свой движок: подмножество LaTeX-математики
# разбирается парсером в дерево и верстается в SVG (box-модель с базовой
# линией — так же, как в настоящих движках).
#
# ЧТО УМЕЕТ (подмножество, честно описано в README):
#   дроби  \frac{a}{b}          корни  \sqrt{x}, \sqrt[3]{x}
#   степени/индексы  x^2, a_i   большие операторы \sum \prod \int (с
#                    пределами: \sum_{i=1}^{n}, \int_0^1)
#   греческие   \alpha..\omega, \Gamma..\Omega, \pi
#   операции    \times \cdot \div \pm \mp \leq \geq \neq \approx \equiv
#               \to \rightarrow \Rightarrow \infty \partial \nabla
#   функции     \sin \cos \tan \log \ln \lg \exp \lim (предел ПОД функцией)
#   текст       \text{...}, \mathrm{...}, \mathbf{...}
#   пробелы     \, \; \: \quad \qquad;  скобки ( ) [ ] |;  \left \right
#
#   + from_plain(): автопревращение «обычной» формулы (E=mc^2, S = π·r²,
#   y = 2*x + 1) в LaTeX — чтобы старые ответы мозга тоже рисовались.
#
# БЕЗОПАСНОСТЬ: всё, что попадает в <text> SVG, экранируется (&, <, >);
# неизвестная команда — честная TexError, а не тишина. Движок возвращает
# ОДНОСТРОЧНЫЙ SVG (без \n) — безопасно кладётся в текст чата.
# ===========================================================================
import re

class TexError(ValueError):
    """Честная ошибка разбора LaTeX (текст — для человека)."""

# ------------------------- таблицы символов ---------------------------------
GREEK = {
    "alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ", "epsilon": "ε",
    "varepsilon": "ε", "zeta": "ζ", "eta": "η", "theta": "θ", "iota": "ι",
    "kappa": "κ", "lambda": "λ", "mu": "μ", "nu": "ν", "xi": "ξ", "pi": "π",
    "rho": "ρ", "sigma": "σ", "tau": "τ", "upsilon": "υ", "phi": "φ",
    "varphi": "φ", "chi": "χ", "psi": "ψ", "omega": "ω",
    "Gamma": "Γ", "Delta": "Δ", "Theta": "Θ", "Lambda": "Λ", "Xi": "Ξ",
    "Pi": "Π", "Sigma": "Σ", "Phi": "Φ", "Psi": "Ψ", "Omega": "Ω",
}
OPS = {
    "times": "×", "cdot": "·", "div": "÷", "pm": "±", "mp": "∓",
    "leq": "≤", "le": "≤", "geq": "≥", "ge": "≥", "neq": "≠", "ne": "≠",
    "approx": "≈", "equiv": "≡", "to": "→", "rightarrow": "→",
    "leftarrow": "←", "Rightarrow": "⇒", "Leftarrow": "⇐",
    "Leftrightarrow": "⇔", "leftrightarrow": "↔", "mapsto": "↦",
    "infty": "∞", "partial": "∂", "nabla": "∇", "circ": "°", "bullet": "•",
    "ast": "∗", "star": "⋆", "oplus": "⊕", "otimes": "⊗", "perp": "⊥",
    "parallel": "∥", "angle": "∠", "triangle": "△", "square": "□",
    "propto": "∝", "because": "∵", "therefore": "∴", "in": "∈", "notin": "∉",
    "subset": "⊂", "cup": "∪", "cap": "∩", "emptyset": "∅", "forall": "∀",
    "exists": "∃", "neg": "¬", "land": "∧", "lor": "∨", "vee": "∨",
    "wedge": "∧", "ldots": "…", "dots": "…", "cdots": "⋯", "prime": "′",
    "ell": "ℓ", "aleph": "ℵ", "hbar": "ℏ", "ell": "ℓ", "Re": "ℜ", "Im": "ℑ",
    "nat": "ℕ", "R": "ℝ",
}
FUNCS = {"sin": 1, "cos": 1, "tan": 1, "cot": 1, "arcsin": 1, "arccos": 1,
         "arctan": 1, "sinh": 1, "cosh": 1, "tanh": 1, "log": 1, "ln": 1,
         "lg": 1, "exp": 1, "max": 1, "min": 1, "det": 1, "deg": 1,
         "dim": 1, "gcd": 1, "hom": 1, "ker": 1, "arg": 1}
BIGOPS = {"sum": ("∑", True), "prod": ("∏", True), "int": ("∫", False),
          "oint": ("∮", False), "iint": ("∬", False),
          "iiint": ("∭", False)}
SPACES = {",": 0.17, ";": 0.28, ":": 0.20, "!": -0.08,
          "quad": 1.0, "qquad": 1.7}
SUPERS = {"⁰": "0", "¹": "1", "²": "2", "³": "3", "⁴": "4", "⁵": "5",
          "⁶": "6", "⁷": "7", "⁸": "8", "⁹": "9", "⁺": "+", "⁻": "-",
          "ⁿ": "n", "ⁱ": "i"}
SUBS = {"₀": "0", "₁": "1", "₂": "2", "₃": "3", "₄": "4", "₅": "5",
        "₆": "6", "₇": "7", "₈": "8", "₉": "9", "₊": "+", "₋": "-",
        "ₙ": "n", "ᵢ": "i", "ₖ": "k", "ₘ": "m"}
# имена-слова, которые в «обычной» формуле превращаются в LaTeX-команды
# (pi -> \pi); значение — просто ИМЯ КОМАНДЫ, без обратного слэша
NAME_MAP = frozenset(("pi", "alpha", "beta", "gamma", "delta", "omega",
                      "lambda", "mu", "phi", "theta", "sigma", "tau",
                      "epsilon", "infty", "Delta", "Omega", "Sigma",
                      "Gamma", "Lambda", "Theta", "Phi", "Psi", "Pi"))

# ширины глифов (в em) — приближение, как в реальных движках таблица метрик
_W_NARROW = set("iljtf!|,.;:Ιiј")
_W_WIDE = set("mwMWШЩмжыюмwМЖЫЮШЩ@&")


def _char_w(c):
    if c == " ":
        return 0.30
    if c in _W_NARROW:
        return 0.30
    if c in _W_WIDE:
        return 0.78
    if c in "—…∞∑∏∮∬∭":
        return 0.95
    if c in "→←⇒⇐⇔↔↦∫":
        return 0.60
    if c in "√°·":
        return 0.42
    if c in "()[]{}":
        return 0.34
    if c in "ΑΒΓΔΕΖΗΘΙΚΛΜΝΞΟΠΡΣΤΥΦΧΨΩ":
        return 0.68
    if c in "αβγδεζηθικλμνξπρστυφχψω":
        return 0.52
    return 0.53


def _text_w(s, size):
    return sum(_char_w(c) for c in str(s)) * size

# ============================== ПАРСЕР ======================================
# Дерево: ("sym", текст, стиль) стиль: "var"|"num"|"op"|"rel"|"text"|"bold"
#         ("seq", [узлы]) ("frac", n, d) ("sqrt", тело, индекс|None)
#         ("scr", база, sub|None, sup|None)
#         ("big", глиф, sub|None, sup|None, над_под|True/сбоку|False)
#         ("fnc", имя, sub|None)  — имя прямое, sub ПОД именем (\lim_{x\to 0})
#         ("txt", s, bold)  ("sp", w_em)

_TOKEN_RE = re.compile(r"\\[a-zA-Zа-яёА-ЯЁ]+|\\.|[{}^_&~]|\S|\s+")


def _cmd_arg(tokens, i):
    """Аргумент команды: {группа} или один токен. Возврат (узел, i)."""
    if i < len(tokens) and tokens[i] == "{":
        node, i = _parse_group(tokens, i + 1)
        return node, i
    if i < len(tokens) and tokens[i] == "[":
        # [n] как аргумент (для \sqrt[3]) — разбирается вызывающим
        raise TexError("ожидался аргумент {…}")
    if i >= len(tokens):
        raise TexError("команда без аргумента")
    node, i = _parse_atom(tokens, i)
    return node, i


_SCRIPTABLE = ("sym", "seq", "fnc", "big", "frac", "sqrt", "over", "hat",
               "vec", "style", "txt")


def _parse_item(tokens, i):
    """v3.62: атом + его степени/индексы. Раньше скрипты цеплялись только на
    верхнем уровне, и \\frac{x^2}{2}, \\sqrt{b^2} не рисовались."""
    node, i = _parse_atom(tokens, i)
    if node is not None and node[0] in _SCRIPTABLE:
        node, i = _parse_script(tokens, i, node)
    return node, i


def _parse_group(tokens, i):
    out = []
    while i < len(tokens) and tokens[i] != "}":
        node, i = _parse_item(tokens, i)
        if node is not None:
            out.append(node)
    if i >= len(tokens):
        raise TexError("не закрыта скобка { — проверь формулу")
    return ("seq", out), i + 1


def _parse_atom(tokens, i):
    """Один атом формулы. Возврат (узел|None, следующий индекс)."""
    t = tokens[i]
    if t.isspace() or t == "~":
        return ("sp", 0.28), i + 1
    if t == "&":
        raise TexError("многострочные формулы (таблички &) не поддерживаются")
    if t == "{":
        node, i2 = _parse_group(tokens, i + 1)
        return node, i2
    if t == "}":
        raise TexError("лишняя закрывающая скобка }")
    if t == "^" or t == "_":
        raise TexError("«%s» без основания — что возводить/индексировать?"
                       % t)
    if t.startswith("\\"):
        name = t[1:]
        if len(name) == 1 and not name.isalpha():
            # одиночный экранированный символ: \{ \} \$ \% \& \# \_ \\
            ch = name
            if ch == "\\":
                return ("sp", 0.5), i + 1
            if ch == "~":
                return ("sp", 0.28), i + 1
            return ("sym", ch, "op"), i + 1
        if not re.match(r"^[a-zA-Z]+$", name):
            # команда кириллицей/смесь — честно: такой команды в LaTeX нет
            raise TexError("неизвестная команда \\%s — её нет в движке "
                           "latexlite" % name)
        if name in ("frac", "dfrac", "tfrac"):
            n, i = _cmd_arg(tokens, i + 1)
            d, i = _cmd_arg(tokens, i)
            return ("frac", n, d), i
        if name == "sqrt":
            i += 1
            idx = None
            if i < len(tokens) and tokens[i] == "[":
                j = i + 1
                buf = []
                while j < len(tokens) and tokens[j] != "]":
                    buf.append(tokens[j])
                    j += 1
                if j >= len(tokens):
                    raise TexError("не закрыта [ у корня")
                idx = ("seq", _inline(buf))
                i = j + 1
            body, i = _cmd_arg(tokens, i)
            return ("sqrt", body, idx), i
        if name in BIGOPS:
            g, over = BIGOPS[name]
            return ("big", g, None, None, over), i + 1
        if name in GREEK:
            return ("sym", GREEK[name], "var"), i + 1
        if name in OPS:
            return ("sym", OPS[name], "rel"), i + 1
        if name in FUNCS:
            return ("fnc", name, None), i + 1
        if name == "lim":
            return ("fnc", "lim", None), i + 1
        if name in ("left", "right", "big", "Big", "bigg", "Bigg",
                    "displaystyle", "limits", "nolimits"):
            if name in ("left", "right") and i + 1 < len(tokens):
                nxt = tokens[i + 1]
                if nxt == ".":
                    return None, i + 2
                node, i2 = _parse_atom(tokens, i + 1)
                return node, i2
            return None, i + 1
        if name in ("mathrm", "operatorname"):
            node, i = _cmd_arg(tokens, i + 1)
            return ("style", node, "text"), i
        if name == "mathbf":
            node, i = _cmd_arg(tokens, i + 1)
            return ("style", node, "bold"), i
        if name in ("text", "textrm", "mbox"):
            node, i = _cmd_arg(tokens, i + 1)
            return ("style", node, "text"), i
        if name in ("overline", "bar"):
            node, i = _cmd_arg(tokens, i + 1)
            return ("over", node), i
        if name in ("hat", "widehat"):          # v3.62: крышка над символом
            node, i = _cmd_arg(tokens, i + 1)
            return ("hat", node), i
        if name == "vec":
            node, i = _cmd_arg(tokens, i + 1)
            return ("vec", node), i
        if name in SPACES:
            return ("sp", SPACES[name]), i + 1
        if name == "backslash":
            return ("sym", "\\", "op"), i + 1
        raise TexError("неизвестная команда \\%s — её нет в движке "
                       "latexlite" % name)
    # обычный символ
    c = t
    if c.isdigit():
        return ("sym", c, "num"), i + 1
    if c.isalpha():
        return ("sym", c, "var"), i + 1
    if c in "+*-×÷±∓=<>()[]|/'\"":
        return ("sym", c, "op"), i + 1
    return ("sym", c, "op"), i + 1


def _inline(tokens):
    out, i = [], 0
    while i < len(tokens):
        node, i = _parse_item(tokens, i)
        if node is not None:
            out.append(node)
    return out


def _parse_script(tokens, i, base):
    """После базы: цепочка ^ и _ (в любом порядке, последняя выигрывает)."""
    sub = sup = None
    while True:
        j = i                                    # v3.62: пробел перед ^/_ — ок
        while j < len(tokens) and tokens[j].isspace():
            j += 1
        if j < len(tokens) and tokens[j] in ("^", "_"):
            i = j
        else:
            break
        kind = tokens[i]
        arg, i = _cmd_arg(tokens, i + 1)
        if kind == "^":
            sup = arg
        else:
            sub = arg
    if sub is None and sup is None:
        return base, i
    return ("scr", base, sub, sup), i


def parse(src):
    """LaTeX-строка -> дерево. TexError с человеческой причиной."""
    s = str(src or "").strip()
    if not s:
        raise TexError("пустая формула")
    s = s.replace("$", " ").replace("\\\\", " \\quad ").replace("&", " & ")
    tokens = _TOKEN_RE.findall(s)
    out, i = [], 0
    while i < len(tokens):
        node, i = _parse_atom(tokens, i)
        if node is None:
            continue
        # скрипты цепляем к базам (сим/функции/большие операторы/группам)
        if node[0] in _SCRIPTABLE:
            node, i = _parse_script(tokens, i, node)
        out.append(node)
    if not out:
        raise TexError("в формуле не нашлось символов")
    return ("seq", out)

# ============================== ВЁРСТКА =====================================
# Каждый узел мерится относительно СВОЕЙ базовой линии: (w, asc, desc),
# asc — вверх, desc — вниз (положительные числа в em×size).
# SVG-текст ставится в (x, y_baseline); fill наследуется от <g>.


def _font(style):
    if style == "var":
        return ' font-style="italic"'
    if style == "bold":
        return ' font-weight="bold"'
    return ""


def _m_sym(ch, style, s):
    return _text_w(ch, s), 0.74 * s, 0.22 * s


def _emit_sym(x, y, ch, style, s):
    cls = {"num": ' class="mnum"', "text": ' class="mtxt"'}.get(style, "")
    return ('<text x="%s" y="%s" font-size="%s"%s%s>%s</text>'
            % (_f(x), _f(y), _f(s), _font(style), cls, esc(ch)))


def _m_seq(nodes, s):
    w = a = d = 0.0
    for n in nodes:
        nw, na, nd = _measure(n, s)
        w += nw
        a = max(a, na)
        d = max(d, nd)
    return w, max(a, 0.1 * s), max(d, 0.05 * s)


def _emit_seq(x, y, nodes, s):
    parts, cx = [], x
    for n in nodes:
        nw, _na, _nd = _measure(n, s)
        parts.append(_emit(cx, y, n, s))
        cx += nw
    return "".join(parts)


def _m_frac(n, d, s):
    ns = 0.88 * s
    ds = 0.88 * s
    nw, na, nd = _measure(n, ns)
    dw, da, dd = _measure(d, ds)
    w = max(nw, dw) + 0.24 * s
    asc = 0.30 * s + nd + 0.14 * s + na
    desc = 0.30 * s + da + 0.14 * s + dd
    return w, asc, desc


def _emit_frac(x, y, n, d, s):
    ns = ds = 0.88 * s
    nw, _a, _ = _measure(n, ns)
    dw, _a, _ = _measure(d, ds)
    w = max(nw, dw) + 0.24 * s
    b_num = 0.30 * s + _measure(n, ns)[2] + 0.14 * s
    b_den = -(0.30 * s + _measure(d, ds)[1] + 0.14 * s)
    parts = [_emit(x + (w - nw) / 2.0, y - b_num, n, ns)]
    parts.append('<line x1="%s" y1="%s" x2="%s" y2="%s" stroke="currentColor" '
                 'stroke-width="%s"/>' % (_f(x + 0.04 * s), _f(y - 0.30 * s),
                                          _f(x + w - 0.04 * s),
                                          _f(y - 0.30 * s), _f(0.045 * s)))
    parts.append(_emit(x + (w - dw) / 2.0, y - b_den, d, ds))
    return "".join(parts), w


def _m_sqrt(body, idx, s):
    bs = 0.95 * s
    bw, ba, bd = _measure(body, bs)
    iw = _measure(idx, 0.6 * s)[0] if idx is not None else 0.0
    w = 0.75 * s + iw + bw + 0.10 * s
    asc = ba + 0.34 * s
    desc = bd + 0.10 * s
    return w, asc, desc


def _emit_sqrt(x, y, body, idx, s):
    bs = 0.95 * s
    bw, ba, bd = _measure(body, bs)
    iw = _measure(idx, 0.6 * s)[0] if idx is not None else 0.0
    top = y - ba - 0.20 * s
    bot = y + bd + 0.04 * s
    rx = x + 0.30 * s + iw
    parts = ['<path d="M %s %s L %s %s L %s %s L %s %s" fill="none" '
             'stroke="currentColor" stroke-width="%s" stroke-linecap="round"/>'
             % (_f(x + 0.04 * s), _f(y - 0.42 * s), _f(x + 0.20 * s),
                _f(bot), _f(rx - 0.10 * s), _f(top), _f(x + 0.62 * s + iw + bw),
                _f(top), _f(0.05 * s))]
    if idx is not None:
        parts.append(_emit(x + 0.02 * s, y + bd * 0.55, idx, 0.6 * s))
    parts.append(_emit(rx, y, body, bs))
    w = 0.75 * s + iw + bw + 0.10 * s
    return "".join(parts), w


def _m_scr(base, sub, sup, s):
    bw, ba, bd = _measure(base, s)
    asc, desc = ba, bd
    if sup is not None:
        sw, sa, sd = _measure(sup, 0.66 * s)
        asc = max(asc, 0.46 * s + sa)
    if sub is not None:
        uw, ua, ud = _measure(sub, 0.66 * s)
        desc = max(desc, 0.20 * s + ud)
    w = bw + max(_measure(sup, 0.66 * s)[0] if sup is not None else 0,
                 _measure(sub, 0.66 * s)[0] if sub is not None else 0) + \
        0.04 * s
    return w, asc, desc


def _emit_scr(x, y, base, sub, sup, s):
    parts = []
    bw, _a, _d = _measure(base, s)
    parts.append(_emit(x, y, base, s))
    cx = x + bw + 0.03 * s
    if sup is not None:
        parts.append(_emit(cx, y - 0.46 * s, sup, 0.66 * s))
    if sub is not None:
        parts.append(_emit(cx, y + 0.20 * s, sub, 0.66 * s))
    w = bw + max(_measure(sup, 0.66 * s)[0] if sup is not None else 0,
                 _measure(sub, 0.66 * s)[0] if sub is not None else 0) + \
        0.04 * s
    return "".join(parts), w


def _m_big(g, sub, sup, over, s):
    gs = 1.05 * s
    gw, ga, gd = _m_sym(g, "op", gs)
    w = gw
    if over:                                   # пределы над/под (∑, ∏)
        if sup is not None:
            w = max(w, _measure(sup, 0.6 * s)[0])
            ga += 0.14 * s + _measure(sup, 0.6 * s)[2] + \
                _measure(sup, 0.6 * s)[1]
        if sub is not None:
            w = max(w, _measure(sub, 0.6 * s)[0])
            gd += 0.14 * s + _measure(sub, 0.6 * s)[1] + \
                _measure(sub, 0.6 * s)[2]
    else:                                      # сбоку (∫)
        mw = 0.0
        if sup is not None:
            mw = max(mw, _measure(sup, 0.6 * s)[0])
        if sub is not None:
            mw = max(mw, _measure(sub, 0.6 * s)[0])
        w = gw + mw + 0.06 * s
        if sup is not None:
            ga = max(ga, 0.52 * s + _measure(sup, 0.6 * s)[1])
        if sub is not None:
            gd = max(gd, 0.30 * s + _measure(sub, 0.6 * s)[2])
    return w, ga, gd


def _emit_big(x, y, g, sub, sup, over, s):
    gs = 1.05 * s
    gw = _m_sym(g, "op", gs)[0]
    parts = ['<text x="%s" y="%s" font-size="%s" class="mbig">%s</text>'
             % (_f(x), _f(y), _f(gs), esc(g))]
    w = gw
    if over:
        if sup is not None:
            sw = _measure(sup, 0.6 * s)[0]
            parts.append(_emit(x + (w - sw) / 2.0, y - 1.20 * s, sup, 0.6 * s))
        if sub is not None:
            uw = _measure(sub, 0.6 * s)[0]
            parts.append(_emit(x + (w - uw) / 2.0, y + 0.98 * s, sub, 0.6 * s))
    else:
        cx = x + gw + 0.04 * s
        if sup is not None:
            parts.append(_emit(cx, y - 0.52 * s, sup, 0.6 * s))
        if sub is not None:
            parts.append(_emit(cx, y + 0.30 * s, sub, 0.6 * s))
        w += max(_measure(sup, 0.6 * s)[0] if sup is not None else 0,
                 _measure(sub, 0.6 * s)[0] if sub is not None else 0) + \
            0.06 * s
    return "".join(parts), w


def _m_fnc(name, sub, s):
    w = _text_w(name, s)
    asc = 0.74 * s
    desc = 0.22 * s
    if sub is not None:
        uw, ua, ud = _measure(sub, 0.6 * s)
        desc = 0.44 * s + ud
        w = max(w, uw)
    return w + 0.10 * s, asc, desc


def _emit_fnc(x, y, name, sub, s):
    parts = ['<text x="%s" y="%s" font-size="%s" class="mfnc">%s</text>'
             % (_f(x), _f(y), _f(s), esc(name))]
    w = _text_w(name, s) + 0.10 * s
    if sub is not None:
        uw = _measure(sub, 0.6 * s)[0]
        parts.append(_emit(x + (w - uw) / 2.0, y + 0.44 * s, sub, 0.6 * s))
    return "".join(parts)


def _measure(node, s):
    k = node[0]
    if k == "sym":
        return _m_sym(node[1], node[2], s)
    if k == "seq":
        return _m_seq(node[1], s)
    if k == "frac":
        return _m_frac(node[1], node[2], s)
    if k == "sqrt":
        return _m_sqrt(node[1], node[2], s)
    if k == "scr":
        return _m_scr(node[1], node[2], node[3], s)
    if k == "big":
        return _m_big(node[1], node[2], node[3], node[4], s)
    if k == "fnc":
        return _m_fnc(node[1], node[2], s)
    if k == "txt":
        return _text_w(node[1], s), 0.74 * s, 0.22 * s
    if k == "style":
        return _measure(node[1], s)
    if k in ("over", "hat"):
        w, a, d = _measure(node[1], s)
        return w, a + 0.16 * s, d
    if k == "vec":
        w, a, d = _measure(node[1], s)
        return w, a + 0.18 * s, d
    if k == "sp":
        return node[1] * s, 0.0, 0.0
    return _text_w("?", s), 0.74 * s, 0.22 * s


def _emit(x, y, node, s):
    k = node[0]
    if k == "sym":
        return _emit_sym(x, y, node[1], node[2], s)
    if k == "seq":
        return _emit_seq(x, y, node[1], s)
    if k == "frac":
        frag, _w = _emit_frac(x, y, node[1], node[2], s)
        return frag
    if k == "sqrt":
        frag, _w = _emit_sqrt(x, y, node[1], node[2], s)
        return frag
    if k == "scr":
        frag, _w = _emit_scr(x, y, node[1], node[2], node[3], s)
        return frag
    if k == "big":
        frag, _w = _emit_big(x, y, node[1], node[2], node[3], node[4], s)
        return frag
    if k == "fnc":
        return _emit_fnc(x, y, node[1], node[2], s)
    if k == "txt":
        return _emit_sym(x, y, node[1], "text", s)
    if k == "style":
        inner = _emit(x, y, node[1], s)
        if node[2] == "text":
            return inner.replace('font-style="italic"', "")
        if node[2] == "bold":
            return ('<g font-weight="bold">%s</g>' % inner)
        return inner
    if k == "hat":
        w, _a, _d = _measure(node[1], s)
        return (_emit(x, y, node[1], s) +
                '<path d="M %s %s L %s %s L %s %s" fill="none" '
                'stroke="currentColor" stroke-width="%s"/>' % (
                    _f(x + w * 0.22), _f(y - 0.74 * s), _f(x + w * 0.5),
                    _f(y - 0.90 * s), _f(x + w * 0.78), _f(y - 0.74 * s),
                    _f(0.05 * s)))
    if k == "over":
        w, _a, _d = _measure(node[1], s)
        return (_emit(x, y, node[1], s) +
                '<line x1="%s" y1="%s" x2="%s" y2="%s" stroke="currentColor" '
                'stroke-width="%s"/>' % (_f(x + 0.03 * s), _f(y - 0.78 * s),
                                         _f(x + w - 0.03 * s),
                                         _f(y - 0.78 * s), _f(0.05 * s)))
    if k == "vec":
        w, _a, _d = _measure(node[1], s)
        return (_emit(x, y, node[1], s) +
                '<path d="M %s %s L %s %s M %s %s L %s %s" fill="none" '
                'stroke="currentColor" stroke-width="%s"/>' % (
                    _f(x), _f(y - 0.80 * s), _f(x + w - 0.06 * s),
                    _f(y - 0.80 * s), _f(x + w - 0.22 * s),
                    _f(y - 0.86 * s), _f(x + w - 0.06 * s),
                    _f(y - 0.80 * s), _f(0.05 * s)))
    if k == "sp":
        return ""
    return _emit_sym(x, y, "?", "text", s)


def _f(v):
    """Число для SVG-атрибутов: до 2 знаков, без хвостовых нулей."""
    s = "%.2f" % (float(v) + 0.0)
    s = s.rstrip("0").rstrip(".")
    return s if s not in ("-0", "") else "0"


def esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))

# ============================ ПУБЛИЧНОЕ API =================================


def render(latex, scale=1.0, pad=6):
    """LaTeX -> {ok, svg, error, w, h}. SVG однострочный, глифы экранированы,
    цвет наследуется (fill=currentColor) — работает в светлой и тёмной теме."""
    try:
        tree = parse(latex)
        w, asc, desc = _measure(tree, 1.0 * scale)
        W = w * 16.0 + pad * 2
        H = (asc + desc) * 16.0 + pad * 2
        base_y = pad + asc * 16.0
        body = _emit(pad, base_y, tree, 16.0 * scale)
        svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %s %s" '
               'width="%s" height="%s" class="ll-svg"><g fill="currentColor">'
               '%s</g></svg>'
               % (_f(W), _f(H), _f(min(W, 560)), _f(H), body))
        return {"ok": True, "svg": svg, "error": "", "w": int(W), "h": int(H)}
    except TexError as e:
        return {"ok": False, "svg": "", "error": str(e), "w": 0, "h": 0}
    except Exception as e:
        return {"ok": False, "svg": "",
                "error": "движок не справился: %s" % e, "w": 0, "h": 0}


def render_or_svg(latex, fallback_text=None):
    """SVG-карточка формулы; если движок честно не смог — серый блок с
    текстом формулы (не молчим, не выдумываем)."""
    r = render(latex)
    if r["ok"]:
        return r["svg"]
    txt = fallback_text if fallback_text is not None else str(latex)
    return ('<svg xmlns="http://www.w3.org/2000/svg" width="320" height="34">'
            '<text x="8" y="22" font-size="14" fill="#888">%s</text></svg>'
            % esc(txt[:60]))


# ----------------- from_plain: «обычная» формула -> LaTeX -------------------
_PLAIN_SUBS = [(ch, "{%s}" % v) for ch, v in
               sorted(list(SUPERS.items()) + list(SUBS.items()),
                      key=lambda kv: -len(kv[0]))]
_WORD_RE = re.compile(r"[A-Za-zА-Яа-яЁё]+")


def from_plain(text):
    """Автопревращение простой формулы в LaTeX: E=mc^2 -> E = mc^{2},
    S = π·r² -> S = \\pi \\cdot r^{2}, sqrt(x) -> \\sqrt{x}. Если в строке
    уже есть LaTeX-команды (\\frac, \\pi...) — вернём как есть."""
    s = str(text or "").strip()
    if "\\" in s:
        return s
    # 1) известные имена (pi, sqrt...) — СНАЧАЛА и только целыми словами,
    #    пока в строке ещё нет вставленных LaTeX-команд (иначе \pi
    #    превращалось бы в \π)
    def _name_repl(m):
        w = m.group(0)
        if w in NAME_MAP and len(w) > 1:
            return "\\" + w + " "
        return w
    s = _WORD_RE.sub(_name_repl, s)
    # 2) sqrt(...), корень(...)
    s = re.sub(r"\\?sqrt\s*\(([^()]+)\)", r"\\sqrt{\1}", s)
    s = re.sub(r"корень\s*\(([^()]+)\)", r"\\sqrt{\1}", s)
    # 3) степени ** и ^
    s = s.replace("**", "^")
    # 3а) * -> \cdot (до вставки спец-символов; в классах есть греческие,
    #     } от уже вставленных степеней и \\ от уже вставленных команд)
    s = re.sub(r"(?<=[\dA-Za-zА-Яа-яЁёΑ-Ωα-ω\)\]}])\s*\*\s*"
               r"(?=[\dA-Za-zА-Яа-яЁёΑ-Ωα-ω\(\\])",
               lambda _m: " \\cdot ", s)
    # 4) спец-символы
    for ch, rep in [("π", "\\pi "), ("Δ", "\\Delta "), ("δ", "\\delta "),
                    ("α", "\\alpha "), ("β", "\\beta "), ("λ", "\\lambda "),
                    ("μ", "\\mu "), ("φ", "\\varphi "), ("θ", "\\theta "),
                    ("ω", "\\omega "), ("Ω", "\\Omega "), ("Σ", "\\Sigma "),
                    ("σ", "\\sigma "), ("τ", "\\tau "), ("∞", "\\infty "),
                    ("≈", "\\approx "), ("≤", "\\leq "), ("≥", "\\geq "),
                    ("≠", "\\neq "), ("±", "\\pm "), ("·", " \\cdot "),
                    ("×", " \\times "), ("÷", " \\div "), ("∂", "\\partial "),
                    ("∫", "\\int "), ("∑", "\\sum "), ("°", "^{\\circ} "),
                    ("→", " \\to ")]:
        s = s.replace(ch, rep)
    s = s.replace("<=", " \\leq ").replace(">=", " \\geq ")
    s = s.replace("!=", " \\neq ")
    # юникод-надстрочные/подстрочные -> ^{} / _{}
    def _sup_repl(m):
        return "".join(SUPERS.get(c, c) for c in m.group(0))
    s = re.sub(r"[⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻ⁿⁱ]+", lambda m: "^{" + _sup_repl(m) + "}", s)

    def _sub_repl(m):
        return "".join(SUBS.get(c, c) for c in m.group(0))
    s = re.sub(r"[₀₁₂₃₄₅₆₇₈₉₊₋ₙᵢₖₘ]+", lambda m: "_{" + _sub_repl(m) + "}", s)
    # ^2 -> ^{2}, _i -> _{i} (одиночные символы)
    s = re.sub(r"\^\s*([0-9A-Za-zА-Яа-яЁё])", r"^{\1}", s)
    s = re.sub(r"_\s*([0-9A-Za-zА-Яа-яЁё])", r"_{\1}", s)
    s = re.sub(r"\s{2,}", " ", s).strip()
    return s


def block(latex, fallback=None):
    """Готовый блок для чата: [ФОРМУЛА]<svg>[/ФОРМУЛА] (односторонне).
    v3.108: внутри svg — <metadata data-tex data-plain>: веб-чат по ним
    копирует формулу (кнопка «копировать» и обычное выделение + Ctrl+C) —
    картинку SVG иначе скопировать текстом нельзя."""
    svg = render_or_svg(latex, fallback)
    try:
        import html as _h
        plain = _tex_to_plain(str(latex or ""))
        meta = '<metadata data-tex="%s" data-plain="%s"></metadata>' % (
            _h.escape(str(latex or ""), quote=True),
            _h.escape(str(plain or latex or ""), quote=True))
        i = svg.find(">")
        if svg.startswith("<svg") and i > 0:
            svg = svg[:i + 1] + meta + svg[i + 1:]
    except Exception:
        pass
    return "[ФОРМУЛА]%s[/ФОРМУЛА]" % svg


# ================= ОЧИСТКА ТЕКСТОВ (v3.18): {\displaystyle ...} =============
# Страницы Википедии (и не только) несут формулы сырым TeX-кодом:
# «{\displaystyle {\sqrt {3}}.}». В чате это выглядит как поломанный
# латекс — заявка владельца «латекс движок не работает» была в том числе
# об этом. plainify() превращает такие вставки в читаемый текст:
# √(3), (a)/(b), x^2, π и т.д. — ничего не удаляя молча.

_SUP_PLAIN = {"0": "⁰", "1": "¹", "2": "²", "3": "³", "4": "⁴", "5": "⁵",
              "6": "⁶", "7": "⁷", "8": "⁸", "9": "⁹", "+": "⁺", "-": "⁻",
              "−": "⁻", "=": "⁼", "(": "⁽", ")": "⁾", "n": "ⁿ", "i": "ⁱ"}
_SUB_PLAIN = {"0": "₀", "1": "₁", "2": "₂", "3": "₃", "4": "₄", "5": "₅",
              "6": "₆", "7": "₇", "8": "₈", "9": "₉", "+": "₊", "-": "₋",
              "−": "₋", "n": "ₙ", "i": "ᵢ", "k": "ₖ", "m": "ₘ", "a": "ₐ",
              "e": "ₑ", "o": "ₒ", "x": "ₓ"}
_CMD_PLAIN = {
    "cdot": "·", "times": "×", "div": "÷", "pm": "±", "mp": "∓",
    "leq": "≤", "le": "≤", "geq": "≥", "ge": "≥", "neq": "≠", "ne": "≠",
    "approx": "≈", "equiv": "≡", "to": "→", "rightarrow": "→",
    "leftarrow": "←", "Rightarrow": "⇒", "infty": "∞", "partial": "∂",
    "nabla": "∇", "circ": "°", "ell": "ℓ", "ldots": "…", "dots": "…",
    "cdots": "⋯", "prime": "′", "angle": "∠", "perp": "⊥",
    "parallel": "∥", "propto": "∝", "because": "∵", "therefore": "∴",
}
_TXT_CMDS = ("text", "mathrm", "mathbf", "mathit", "mathsf", "mathbb",
             "mathcal", "operatorname", "left", "right", "big", "Big",
             "bigg", "Bigg", "displaystyle", "limits", "nolimits")


def _braces(t, i):
    """t[i] == '{' -> (содержимое, индекс за закрывающей '}')."""
    depth = 0
    for j in range(i, len(t)):
        if t[j] == "{":
            depth += 1
        elif t[j] == "}":
            depth -= 1
            if depth == 0:
                return t[i + 1:j], j + 1
    return None, len(t)


def _sup_or_pow(inner):
    s = "".join(_SUP_PLAIN.get(c, c) for c in inner)
    return s if len(s) <= 3 else "^(" + inner + ")"


def _sub_or_idx(inner):
    s = "".join(_SUB_PLAIN.get(c, c) for c in inner)
    return s if len(s) <= 3 else "_(" + inner + ")"


def _tex_to_plain(t):
    """TeX-фрагмент -> читаемый текст (без «\» и сырых команд)."""
    t = str(t or "")
    # v3.124: \bar{ν} / \overline{x} — черта над знаком (антинейтрино ν̄)
    t = re.sub(r"\\(?:bar|overline)\s*\{\s*(\\?[A-Za-z]+|.)\s*\}",
               lambda m: _tex_to_plain(m.group(1)) + "\u0304", t)
    # \lim_{x \to 0} — предел: lim(x→0)
    t = re.sub(r"\\lim\s*_\s*\{([^{}]*)\}",
               lambda m: "lim(" + _tex_to_plain(m.group(1)) + ")", t)
    # \sqrt[n]{x} и \sqrt{x}
    while True:
        m = re.search(r"\\sqrt\s*\[\s*(\d+)\s*\]\s*\{", t)
        if m:
            idx = m.group(1)
            inner, k = _braces(t, m.end() - 1)
            if inner is None:
                break
            t = t[:m.start()] + "(%s)^(1/%s)" % (inner, idx) + t[k:]
            continue
        m = re.search(r"\\sqrt\s*\[\s*\{([^{}]*)\}\s*\]\s*\{", t)
        if m:
            idx = m.group(1)
            inner, k = _braces(t, m.end() - 1)
            if inner is None:
                break
            t = t[:m.start()] + "(%s)^(1/%s)" % (inner, idx) + t[k:]
            continue
        m = re.search(r"\\sqrt\s*\{", t)
        if m:
            inner, k = _braces(t, m.end() - 1)
            if inner is None:
                break
            t = t[:m.start()] + "√(" + inner + ")" + t[k:]
            continue
        break
    # \frac{a}{b}, \dfrac, \tfrac
    while True:
        m = re.search(r"\\[dt]?frac\s*\{", t)
        if not m:
            break
        a, k = _braces(t, m.end() - 1)
        if a is None:
            break
        m2 = re.match(r"\s*\{", t[k:])
        if not m2:
            t = t[:m.start()] + "(" + a + ")" + t[k:]
            break
        b, k2 = _braces(t, k + m2.end() - 1)
        if b is None:
            t = t[:m.start()] + "(" + a + ")" + t[k:]
            break
        t = t[:m.start()] + "(" + a + ")/(" + b + ")" + t[k2:]
    # ^{...} и _{...} (перед одиночными символами)
    t = re.sub(r"\^\s*\{([^{}]*)\}",
               lambda m: _sup_or_pow(m.group(1)), t)
    t = re.sub(r"_\s*\{([^{}]*)\}",
               lambda m: _sub_or_idx(m.group(1)), t)
    t = re.sub(r"\^\s*([0-9A-Za-z])", lambda m: _sup_or_pow(m.group(1)), t)
    t = re.sub(r"_\s*([0-9A-Za-z])", lambda m: _sub_or_idx(m.group(1)), t)
    # \text{...}, \mathrm{...}, \left, \right ...
    for cmd in _TXT_CMDS:
        t = re.sub(r"\\" + cmd + r"\s*\{([^{}]*)\}", r"\1", t)
        t = re.sub(r"\\" + cmd + r"\b", " ", t)
    # греческие и символы (длинные имена раньше коротких)
    for name in sorted(list(GREEK) + list(_CMD_PLAIN), key=len,
                       reverse=True):
        t = re.sub(r"\\" + name + r"(?![a-zA-Z])",
                   GREEK.get(name) or _CMD_PLAIN[name], t)
    # \sin/\cos/... — просто имя функции
    for fname in FUNCS:
        t = re.sub(r"\\" + fname + r"(?![a-zA-Z])", fname, t)
    t = re.sub(r"\blim\b", "lim", t)
    # интервалы-пробелы, прочие команды, остаточные скобки-группы
    t = re.sub(r"\\[,;:!]", " ", t)
    t = t.replace("\\quad", " ").replace("\\qquad", "  ")
    t = re.sub(r"\\[a-zA-Zа-яёА-ЯЁ]+", " ", t)     # неизвестное — убрать
    t = t.replace("\\\\", " ").replace("\\ ", " ")
    t = t.replace("{", "").replace("}", "")
    return t


_TEX_FAST = re.compile(r"displaystyle|\\frac|\\sqrt|\\pi|\\cdot|\\left|"
                       r"\^\{|_\{|\\sin|\\cos|\\log|\\lim")


def plainify(text):
    r"""Убрать из текста сырой TeX и {\displaystyle ...} вставки.
    Чистый текст возвращается как есть (быстрый путь) — функция безопасна
    для любого текста страниц."""
    s = str(text or "")
    if not _TEX_FAST.search(s):
        return s
    out, i = [], 0
    while i < len(s):
        j = s.find("{\\", i)          # {\displaystyle ...} и прочие {\...}
        if j < 0:
            out.append(_tex_to_plain(s[i:]))
            break
        out.append(_tex_to_plain(s[i:j]))
        inner, k = _braces(s, j)
        out.append(_tex_to_plain(inner) if inner is not None else "")
        i = k
    r = "".join(out)
    r = re.sub(r"[ \t]{2,}", " ", r)
    r = re.sub(r" ([.,;:!?])", r"\1", r)
    return r


# ============ v3.107: ФОРМУЛЫ ИЗ ТЕКСТОВ — В ОКОШКО (веб) ИЛИ ЧИТАЕМО (боты) ===
_DOLLAR = re.compile(r"\$\$(.+?)\$\$|(?<![\\$\w])\$([^$\n]{2,300}?)\$(?![\w$])",
                     re.S)


def render_dollars(text, mode="svg"):
    """«$TeX$» в ответе -> окошко формулы [ФОРМУЛА]<svg>[/ФОРМУЛА] (mode
    "svg", веб-чат) или читаемый текст (mode "plain": МАКС, Telegram, API).
    Формула, которую движок не нарисовал, остаётся читаемым текстом."""
    s = str(text or "")
    # остатки разметки формул Википедии в старых статьях базы
    s = re.sub(r"</?math\b[^>\n]{0,200}>", " ", s, flags=re.I)
    s = re.sub(r"\s*\"?\s*,\s*\"(?:ref|name|attrs|body|extsrc)\"\s*:\s*\""
               r"[^\"\n]{0,80}\"?\}*", " ", s)
    s = drop_broken(s)
    if "$" not in s:
        return s

    def _one(m):
        tex = (m.group(1) or m.group(2) or "").strip()
        if not tex or not re.search(r"[\\^_=+\-*/<>]|\\[a-z]", tex):
            return m.group(0)                 # «$5» и т.п. — не формула
        # v3.110: одиночный символ или короткое обозначение (γ, T^{4}, c_p)
        # — в строку текстом, а не окошком посреди предложения
        short = len(tex) <= 14 and not re.search(r"[=<>]|\\(?:frac|sum|int|sqrt)", tex)
        if mode == "svg" and not short:
            try:
                r = render(tex)
                if r.get("ok"):
                    return "\n" + block(tex) + "\n"
            except Exception:
                pass
        try:
            return _tex_to_plain(tex)
        except Exception:
            return tex
    return _DOLLAR.sub(_one, s)


_BROKEN = re.compile(r"(?:(?<=\s)|^)(?:[\w⁰¹²³⁴⁵⁶⁷⁸⁹⁄/^{}·]{1,20}\s*=\s*[,;]\s*)*"
                     r"[\w⁰¹²³⁴⁵⁶⁷⁸⁹⁄/^{}·]{1,20}\s*=\s*(?=[.;,]|$)")


def drop_broken(s):
    """«S=, VT³=, PV⁴/³=.» — формулы, у которых при чтении страницы потерялись
    правые части: это не текст, выбрасываем (с хвостом «: .» -> «.»)."""
    # v3.129: блоки кода ``` не трогаем — там отступы значимы (Python!)
    parts = re.split(r"(```[\s\S]*?```)", str(s or ""))
    for i in range(0, len(parts), 2):
        t = _BROKEN.sub("", parts[i])
        t = re.sub(r"[:,]\s*\.", ".", t)
        parts[i] = re.sub(r"[ \t]{2,}", " ", t)
    return "".join(parts)


def code_windows(text):
    """v3.107: строки-команды (sudo apt …, pip install …, код) в ответе
    веб-чата — в окошко кода (```), подряд идущие — одним окошком.
    Уже оформленные блоки ``` не трогаются."""
    s = str(text or "")
    if "```" in s:
        return s
    try:
        import kb
        is_cmd = kb._is_cmd
    except Exception:
        return s
    out, buf = [], []
    for ln in s.split("\n"):
        st = ln.strip()
        if st and not st.startswith(("_", "*", "[ФОРМУЛА]", "#", "LaTeX",
                                     "[")) and "`" not in st and \
                "http" not in st and is_cmd(st):
            buf.append(st)
            continue
        if buf:
            out.append("```\n" + "\n".join(buf) + "\n```")
            buf = []
        out.append(ln)
    if buf:
        out.append("```\n" + "\n".join(buf) + "\n```")
    return "\n".join(out)


_BLOCK = re.compile(r"\[(ФОРМУЛА|КАРТИНКА)\]([\s\S]*?)\[/\1\]")


def blocks_to_text(text):
    """v3.108: для мессенджеров: окошко формулы -> её читаемый текст
    (data-plain), картинка -> убрать (раньше формула пропадала целиком)."""
    import html as _h

    def _one(m):
        if m.group(1) != "ФОРМУЛА":
            return " "
        mp = re.search(r'data-plain="([^"]*)"', m.group(2))
        return " %s " % _h.unescape(mp.group(1)) if mp else " "
    return _BLOCK.sub(_one, str(text or ""))
