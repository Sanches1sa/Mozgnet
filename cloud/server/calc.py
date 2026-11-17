# -*- coding: utf-8 -*-
# ===========================================================================
# calc.py — КАЛЬКУЛЯТОР И РЕШАТЕЛЬ УРАВНЕНИЙ (v3.27, только stdlib)
# ---------------------------------------------------------------------------
# Заявка владельца: «ИИ не считает, математический движок не использует и
# формулы не считает». Прежний навык брал из фразы ПЕРВЫЕ ДВА числа:
# «2+2*3» -> «2+2 = 4», «(12.5+7)*3/2» -> 19.5, «найди корни x^2-5x+6=0»
# -> «2-5 = -3»; проценты, корни и уравнения — «не знаю». Здесь:
#   * всё выражение целиком: порядок действий, скобки, степени (^, **),
#     sqrt/«корень из», sin cos tan ln log exp abs, π/pi, e;
#   * слова: «плюс», «минус», «умножить на», «разделить на», «в квадрате»,
#     «в кубе», «в степени», «корень из», «x»/«х» между числами;
#   * проценты: «15% от 2400», «2400 + 15%», «2400 - 15%», «сколько
#     процентов составляет 30 от 120»;
#   * уравнения с одной переменной: линейные и квадратные — ТОЧНО (с
#     дискриминантом), прочие — численно (сканирование + деление пополам).
# Вычисление — mathmodels.safe_eval (AST, белый список; код не исполняется).
# ===========================================================================
import math
import re

import mathmodels

_FN = r"(?:sqrt|sin|cos|tan|ln|log|exp|abs)"


def _fmt(v):
    """Число для человека: 8, 29.25, 0.333333, 1.5e+12."""
    if isinstance(v, bool):
        return str(v)
    if isinstance(v, int):
        return str(v)
    if v != v or v in (float("inf"), float("-inf")):
        return "не определено"
    if abs(v - round(v)) < 1e-9 and abs(v) < 1e15:
        return str(int(round(v)))
    if abs(v) >= 1e12 or abs(v) < 1e-6:
        return "%.6g" % v
    return ("%.10f" % v).rstrip("0").rstrip(".")


def _words_to_ops(t):
    """Русские слова-операторы -> символы."""
    t = " " + t.lower().replace("ё", "е") + " "
    t = t.replace("×", "*").replace("·", "*").replace("÷", "/") \
         .replace("−", "-").replace("–", "-").replace("√", " sqrt ") \
         .replace("π", " pi ").replace("²", "^2").replace("³", "^3")
    rules = [
        (r"квадратн\w*\s+корн\w*\s+из|корн\w*\s+квадратн\w*\s+из|"
         r"корен\w*\s+из|корн\w*\s+из|корень", " sqrt "),
        (r"\bумнож\w*\s+на\b|\bумножить\b|\bумножь\b|\bпомножить на\b|"
         r"\bразмножить на\b", " * "),
        (r"\bраздел\w*\s+на\b|\bподел\w*\s+на\b|\bделенн\w*\s+на\b|"
         r"\bделить на\b", " / "),
        (r"\bплюс\b|\bприбавить\b|\bприбавь\b", " + "),
        (r"\bминус\b|\bотнять\b|\bвычесть\b", " - "),
        (r"\bв\s+квадрате\b", " ^2 "), (r"\bв\s+кубе\b", " ^3 "),
        (r"\bв\s+степени\b", " ^ "),
        (r"\bсинус\w*\b", " sin "), (r"\bкосинус\w*\b", " cos "),
        (r"\bтангенс\w*\b", " tan "), (r"\bлогарифм\w*\b", " ln "),
        (r"\bпи\b", " pi "),
    ]
    for rx, rep in rules:
        t = re.sub(rx, rep, t)
    # «7 х 8», «7 x 8» — умножение (между числами)
    t = re.sub(r"(?<=[\d)])\s*[xх]\s*(?=[\d(])", " * ", t)
    # десятичная запятая: «12,5» -> «12.5» (а «1, 2, 3» — перечисление)
    t = re.sub(r"(?<=\d),(?=\d)", ".", t)
    return t


_EXPR_RE = re.compile(
    r"(?:[-+]?\s*(?:\d+(?:\.\d+)?|pi|e\b|%s\s*\(?|\()"
    r"(?:\s*(?:[-+*/^%%()]|\*\*|\d+(?:\.\d+)?|pi|e\b|%s|\.)\s*)*)" % (_FN, _FN))


def _clean_expr(e):
    e = e.strip().rstrip("=?.!,")
    # «sqrt 16» -> «sqrt(16)», «sqrt 2^2» -> «sqrt(2)^2»
    e = re.sub(r"%s\s+(-?\d+(?:\.\d+)?)" % _FN,
               lambda m: m.group(0).split()[0] + "(" + m.group(1) + ")", e)
    # неявное умножение: «2(3+4)», «2pi», «)(»
    e = re.sub(r"(?<=[\d)])\s*(?=\(|pi\b|sqrt|sin|cos|tan|ln|log|exp|abs)",
               "*", e)
    e = re.sub(r"\)\s*\(", ")*(", e)
    # незакрытые скобки — закрыть
    bal = e.count("(") - e.count(")")
    if bal > 0:
        e += ")" * bal
    return e.replace("^", "**")


def evaluate(expr):
    """Посчитать выражение (строка с операторами). Возврат: число."""
    return mathmodels.safe_eval(_clean_expr(expr))


# ------------------------------- проценты -----------------------------------
def _percent(t):
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:%|процент\w*)\s*(?:от|из)\s*"
                  r"(\d+(?:\.\d+)?)", t)
    if m:
        p, n = float(m.group(1)), float(m.group(2))
        return "%s%% от %s = %s" % (_fmt(p), _fmt(n), _fmt(p * n / 100.0))
    m = re.search(r"(\d+(?:\.\d+)?)\s*([+-])\s*(\d+(?:\.\d+)?)\s*(?:%|процент)",
                  t)
    if m:
        n, op, p = float(m.group(1)), m.group(2), float(m.group(3))
        v = n * (1 + p / 100.0) if op == "+" else n * (1 - p / 100.0)
        return "%s %s %s%% = %s" % (_fmt(n), op, _fmt(p), _fmt(v))
    m = re.search(r"сколько\s+процент\w*\s+(?:составляет\s+|будет\s+|от\s+)?"
                  r"(\d+(?:\.\d+)?)\s*(?:от|из|к)\s*(\d+(?:\.\d+)?)", t)
    if m:
        a, b = float(m.group(1)), float(m.group(2))
        if b == 0:
            return "Процент от нуля не определён."
        return "%s от %s — это %s%%" % (_fmt(a), _fmt(b), _fmt(a * 100.0 / b))
    return None


# ------------------------------ уравнения -----------------------------------
_VAR_RE = re.compile(r"(?<![a-z])([xyzt])(?![a-z])")


def _prep_side(s, var):
    s = _words_to_ops(s)
    s = re.sub(r"(?<=[\d)])\s*(?=%s\b)" % var, "*", s)   # 2x -> 2*x
    s = re.sub(r"(?<=\b%s)\s*(?=\()" % var, "*", s)      # x(…) -> x*(…)
    return _clean_expr(s)


def solve(eq):
    """Уравнение «левая = правая» с одной переменной. Возврат: текст."""
    eq = eq.replace("х", "x")
    if eq.count("=") != 1:
        return None
    lhs, rhs = eq.split("=")
    found = set(_VAR_RE.findall((lhs + " " + rhs).lower()))
    if len(found) != 1:
        return None
    var = found.pop()
    L, R = _prep_side(lhs, var), _prep_side(rhs, var)

    def f(x):
        return mathmodels.safe_eval("(%s)-(%s)" % (L, R), {var: x})

    try:
        c, f1, fm1, f2, f3 = f(0.0), f(1.0), f(-1.0), f(2.0), f(3.0)
    except mathmodels.FormulaError as e:
        return "Не смогла разобрать уравнение: %s." % e
    except Exception:
        c = None
    # многочлен степени <= 2? a = (f(1)+f(-1))/2 - c, b = (f(1)-f(-1))/2
    if c is not None:
        a = (f1 + fm1) / 2.0 - c
        b = (f1 - fm1) / 2.0
        quad = all(abs(a * x * x + b * x + c - fx) < 1e-7 * (1 + abs(fx))
                   for x, fx in ((2.0, f2), (3.0, f3)))
        if quad:
            if abs(a) < 1e-12:
                if abs(b) < 1e-12:
                    return ("Уравнение не зависит от %s: %s." % (
                        var, "верно при любом %s" % var if abs(c) < 1e-12
                        else "решений нет"))
                return "%s = %s" % (var, _fmt(-c / b))
            d = b * b - 4 * a * c
            head = "Дискриминант D = b² − 4ac = %s. " % _fmt(d)
            if d < -1e-12:
                re_, im = -b / (2 * a), math.sqrt(-d) / (2 * abs(a))
                return head + ("D < 0 — действительных корней нет "
                               "(комплексные: %s = %s ± %si)."
                               % (var, _fmt(re_), _fmt(im)))
            if abs(d) <= 1e-12:
                return head + "Один корень: %s = %s." % (var, _fmt(-b / (2 * a)))
            r1 = (-b - math.sqrt(d)) / (2 * a)
            r2 = (-b + math.sqrt(d)) / (2 * a)
            r1, r2 = sorted((r1, r2))
            return head + "Корни: %s₁ = %s, %s₂ = %s." % (var, _fmt(r1), var,
                                                         _fmt(r2))
    # численно: сканирование знаков + деление пополам
    roots = []
    xs = [i / 10.0 for i in range(-1000, 1001)] + \
        [float(i) for i in range(-1000, 1001, 5)]
    xs = sorted(set(xs))
    prev = None
    for x in xs:
        try:
            y = f(x)
        except Exception:
            prev = None
            continue
        if abs(y) < 1e-12:
            roots.append(x)
        elif prev is not None and prev[1] * y < 0:
            a_, b_ = prev[0], x
            for _ in range(80):
                m_ = (a_ + b_) / 2.0
                try:
                    ym = f(m_)
                except Exception:
                    break
                if f(a_) * ym <= 0:
                    b_ = m_
                else:
                    a_ = m_
            roots.append((a_ + b_) / 2.0)
        prev = (x, y)
    uniq = []
    for r in roots:
        if all(abs(r - u) > 1e-6 for u in uniq):
            uniq.append(r)
    if not uniq:
        return ("Действительных корней на отрезке [−1000; 1000] не нашла "
                "(численный поиск).")
    return "Корни (численно): %s." % ", ".join(
        "%s ≈ %s" % (var, _fmt(round(r, 8))) for r in uniq[:10])


# ------------------------------ главный вход --------------------------------
_TRIGGER = re.compile(
    r"посчитай|подсчитай|вычисли|рассчитай|сколько\s+будет|сколько\s+это|"
    r"реши|решить|найди\s+корн|корни|корень|чему\s+равн|процент|%|"
    r"\d\s*[-+*/^×÷]\s*\(?\s*-?\d|\d\s*\)|\d\s*[xх]\s*\d|sqrt|√|\bsin\b|"
    r"\bcos\b|"
    r"\bв\s+квадрате\b|\bв\s+кубе\b|\bв\s+степени\b|умножить|разделить|"
    r"плюс|минус", re.I)


# v3.28: ЯВНАЯ ПРОСЬБА ПОСЧИТАТЬ. Без неё дефис/тире между числами — это
# ДИАПАЗОН или ОБОЗНАЧЕНИЕ, а не минус: «Astra Linux 1.7 - 1.8 обновление»
# давало «1.7 - 1.8 = -0.1», «ГОСТ 34.602-2020» — «= -1985.398», «в 2020 -
# 2021 годах» — «= -1», телефон 8-800-… — отрицательное число: вопрос
# терял смысл, ответ уходил в калькулятор.
_INTENT = re.compile(
    r"посчитай|подсчитай|вычисли|рассчитай|сосчитай|сколько\s+будет|"
    r"сколько\s+это|сколько\s+получится|чему\s+равн|реши|найди\s+корн|"
    r"\bминус\b|\bплюс\b|умнож|раздели|делить|=\s*\??\s*$", re.I)
# тире и длинный минус как знаки препинания (не «-» с клавиатуры)
_DASHES = "—–‒―"


def _only_minus(expr):
    """В выражении из операторов только минус (дефис) — «1.7 - 1.8»."""
    e = re.sub(r"^\s*-", "", expr)
    return "-" in e and not re.search(r"[+*/^%()]|sqrt|sin|cos|tan|ln|log|"
                                      r"exp|abs", e)


def _pure(low, expr):
    """Сообщение — само выражение («2 - 3», «12-5?»), без слов вокруг."""
    rest = low.replace(expr.strip(), " ", 1) if expr.strip() in low else low
    return not re.search(r"[a-zа-я]{2,}", rest)


def answer(text):
    """Вопрос -> (ответ, вид) | None, если это не математика.
    вид: 'calc' | 'percent' | 'equation'."""
    raw = str(text or "").strip()
    low = raw.lower().replace("ё", "е")
    if not re.search(r"\d|pi|π", low) or not _TRIGGER.search(low):
        return None
    if re.search(r"https?://|\d{1,2}:\d{2}|\d{2}\.\d{2}\.\d{2,4}", low):
        return None                     # ссылки, время, даты — не счёт
    explicit = bool(_INTENT.search(low))
    # v3.28: тире (—, –) — знак препинания; минусом оно бывает только при
    # явной просьбе посчитать («сколько будет 10 — 3»)
    if not explicit and any(ch in low for ch in _DASHES):
        low = re.sub("[%s]" % _DASHES, " , ", low)
    elif explicit:
        low = re.sub(r"(?<=[\d)])\s*[%s]\s*(?=[\d(])" % _DASHES, " - ", low)
    # номера, стандарты, версии: 8-800-555-35-35, 34.602-2020, 2.04.01-85
    if not explicit and re.search(
            r"\d+(?:-\d+){2,}|\d+\.\d+\.\d+|\d+\.\d+-\d{2,}", low):
        return None
    t = _words_to_ops(low)
    p = _percent(t)
    if p:
        return p, "percent"
    # уравнение: есть «=» и переменная (x, y, z, t)
    m = re.search(r"([-+*/^().\d\s\w]*[xyzt][-+*/^().\d\s\w]*=\s*"
                  r"[-+*/^().\d\s\w]+)", t.replace("х", "x"))
    if "=" in t and m and _VAR_RE.search(m.group(1)):
        eq = re.sub(r"^\s*(?:реши(?:ть)?|найди|уравнени\w*|корни|корень|"
                    r"\s)+", "", m.group(1)).strip(" ?.!,")
        eq = re.sub(r"[а-я]+", " ", eq)
        res = solve(eq)
        if res:
            return "Уравнение %s:\n%s" % (eq.replace("**", "^").strip(),
                                         res), "equation"
    # обычное выражение: самый длинный кусок с операторами
    cands = [c.strip() for c in _EXPR_RE.findall(t)]
    cands = [c for c in cands if re.search(r"\d|pi", c) and
             (re.search(r"[-+*/^]|sqrt|sin|cos|tan|ln|log|exp|abs", c))]
    if not cands:
        return None
    expr = max(cands, key=len)
    # v3.28: «Windows 10 - 11 разница», «статья 5 - 2 закона» — дефис между
    # числами внутри обычной фразы = диапазон/обозначение. Минусом он
    # считается, только если просят посчитать или фраза — само выражение.
    if _only_minus(expr) and not explicit and not _pure(t, expr):
        return None
    try:
        val = evaluate(expr)
    except mathmodels.FormulaError as e:
        if not explicit and not _pure(t, expr):
            return None                 # не просили считать — не ошибка счёта
        return "Не смогла посчитать «%s»: %s." % (expr.strip(), e), "calc"
    except Exception as e:
        return None
    shown = _clean_expr(expr).replace("**", "^").replace("*", "×")
    shown = re.sub(r"\s+", " ", shown).strip()
    shown = re.sub(r"\s*([×/^+])\s*", r"\1", shown)
    return "%s = %s" % (shown, _fmt(val)), "calc"
