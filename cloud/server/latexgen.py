# -*- coding: utf-8 -*-
# ===========================================================================
# latexgen.py — МОЗГ СОСТАВЛЯЕТ ФОРМУЛЫ В LATEX (v3.62, только stdlib)
# ---------------------------------------------------------------------------
# Заявка владельца: «научи Star Gate составлять формулы в latex».
# Три источника, по убыванию точности:
#   1) РАЗБОР ЗАДАНИЯ по-русски: «интеграл от x в квадрате от 0 до 1»,
#      «сумма 1/i^2 от i=1 до n», «предел sin x / x при x стремится к 0»,
#      «производная x^3 по x», «дробь a над b», «корень из x+1» -> LaTeX;
#   2) БИБЛИОТЕКА известных формул (физика, геометрия, статистика,
#      нейросети: ~50 штук, по ключевым словам: «формула площади круга»,
#      «закон Ома», «softmax», «дисперсия»);
#   3) ВЫУЧЕННЫЕ формулы: из статей базы знаний берутся вставки $…$ / $$…$$ /
#      \(…\) / \[…\], проверяются своим движком (latexlite.render) и
#      запоминаются вместе с фразой вокруг — по ней потом находятся.
# Честно: это не нейросеть-генератор — это разбор + библиотека + выученное;
# незнакомую формулу мозг не выдумывает, а говорит «не знаю такой».
# Движок рисует подмножество LaTeX (см. latexlite): без матриц/окружений.
# ===========================================================================
import hashlib
import re
import threading
import time

import latexlite
import store
import textutils

_LOCK = threading.Lock()
LIB_FILE = "latex_lib.json"
LIB_MAX = 2000

# ---------------------------- встроенные формулы ---------------------------
# (название, ключевые слова через пробел, LaTeX)
BUILTIN = [
    ("Площадь круга", "площад круг", r"S = \pi r^{2}"),
    ("Длина окружности", "длин окружн", r"C = 2 \pi r"),
    ("Площадь треугольника", "площад треугольн", r"S = \frac{1}{2} a h"),
    ("Теорема Пифагора", "пифагор гипотенуз катет", r"a^{2} + b^{2} = c^{2}"),
    ("Корни квадратного уравнения", "корн квадратн уравнен",
     r"x_{1,2} = \frac{-b \pm \sqrt{b^{2} - 4 a c}}{2 a}"),
    ("Дискриминант", "дискриминант", r"D = b^{2} - 4 a c"),
    ("Формула Эйлера", "эйлер", r"e^{i \pi} + 1 = 0"),
    ("Эквивалентность массы и энергии", "эйнштейн масс энерги",
     r"E = m c^{2}"),
    ("Второй закон Ньютона", "втор закон ньютон сил", r"F = m a"),
    ("Закон всемирного тяготения", "тяготен гравитац всемирн",
     r"F = G \frac{m_{1} m_{2}}{r^{2}}"),
    ("Закон Ома", "закон ома напряжен ток сопротивлен", r"I = \frac{U}{R}"),
    ("Мощность тока", "мощност ток", r"P = U I"),
    ("Закон Кулона", "кулон заряд", r"F = k \frac{q_{1} q_{2}}{r^{2}}"),
    ("Кинетическая энергия", "кинетическ энерги", r"E_{k} = \frac{m v^{2}}{2}"),
    ("Потенциальная энергия", "потенциальн энерги", r"E_{p} = m g h"),
    ("Плотность", "плотност", r"\rho = \frac{m}{V}"),
    ("Давление", "давлен", r"p = \frac{F}{S}"),
    ("Скорость", "скорост равномерн", r"v = \frac{s}{t}"),
    ("Уравнение Менделеева — Клапейрона", "менделеев клапейрон газ",
     r"p V = \nu R T"),
    ("Энергия фотона", "план фотон", r"E = h \nu"),
    ("Среднее арифметическое", "средн арифметическ",
     r"\bar{x} = \frac{1}{n} \sum_{i=1}^{n} x_{i}"),
    ("Дисперсия", "дисперси", r"\sigma^{2} = \frac{1}{n} \sum_{i=1}^{n} (x_{i} - \bar{x})^{2}"),
    ("Стандартное отклонение", "стандартн отклонен",
     r"\sigma = \sqrt{\frac{1}{n} \sum_{i=1}^{n} (x_{i} - \bar{x})^{2}}"),
    ("Нормальное распределение", "нормальн распределен гаусс плотност",
     r"f(x) = \frac{1}{\sigma \sqrt{2 \pi}} e^{-\frac{(x - \mu)^{2}}{2 \sigma^{2}}}"),
    ("Формула Байеса", "байес условн вероятност",
     r"P(A|B) = \frac{P(B|A) P(A)}{P(B)}"),
    ("Факториал", "факториал", r"n! = 1 \cdot 2 \cdot 3 \cdot \ldots \cdot n"),
    ("Сумма арифметической прогрессии", "арифметическ прогресс сумм",
     r"S_{n} = \frac{(a_{1} + a_{n}) n}{2}"),
    ("Сумма геометрической прогрессии", "геометрическ прогресс сумм",
     r"S_{n} = \frac{b_{1} (q^{n} - 1)}{q - 1}"),
    ("Определение производной", "определен производн",
     r"f'(x) = \lim_{h \to 0} \frac{f(x + h) - f(x)}{h}"),
    ("Интеграл Гаусса", "интеграл гаусс",
     r"\int_{-\infty}^{\infty} e^{-x^{2}} dx = \sqrt{\pi}"),
    ("Ряд Тейлора", "ряд тейлор",
     r"f(x) = \sum_{n=0}^{\infty} \frac{f^{(n)}(a)}{n!} (x - a)^{n}"),
    ("Основное тригонометрическое тождество", "тригонометрическ тождеств",
     r"\sin^{2} x + \cos^{2} x = 1"),
    ("Синус суммы", "синус сумм", r"\sin(a + b) = \sin a \cos b + \cos a \sin b"),
    ("Косинус суммы", "косинус сумм", r"\cos(a + b) = \cos a \cos b - \sin a \sin b"),
    ("Логарифм произведения", "логарифм произведен",
     r"\log_{a}(x y) = \log_{a} x + \log_{a} y"),
    ("Расстояние между точками", "расстояни меж точк",
     r"d = \sqrt{(x_{2} - x_{1})^{2} + (y_{2} - y_{1})^{2}}"),
    ("Сигмоида", "сигмоид логистическ", r"\sigma(x) = \frac{1}{1 + e^{-x}}"),
    ("ReLU", "relu рел", r"f(x) = \max(0, x)"),
    ("Softmax", "softmax софтмакс", r"\mathrm{softmax}(z_{i}) = \frac{e^{z_{i}}}{\sum_{j=1}^{K} e^{z_{j}}}"),
    ("Нейрон: взвешенная сумма", "нейрон взвешенн сумм персептрон",
     r"y = f\left(\sum_{i=1}^{n} w_{i} x_{i} + b\right)"),
    ("Перекрёстная энтропия", "кросс энтропи перекрёстн",
     r"L = - \sum_{i=1}^{K} y_{i} \log \hat{y}_{i}"),
    ("Среднеквадратичная ошибка", "среднеквадратичн ошибк mse",
     r"\mathrm{MSE} = \frac{1}{n} \sum_{i=1}^{n} (y_{i} - \hat{y}_{i})^{2}"),
    ("Градиентный спуск", "градиентн спуск обновлен вес",
     r"w \leftarrow w - \eta \frac{\partial L}{\partial w}"),
    ("Косинусная близость", "косинусн близост схожест",
     r"\cos \theta = \frac{A \cdot B}{\|A\| \|B\|}"),
    ("Энтропия Шеннона", "энтропи шеннон", r"H = - \sum_{i=1}^{n} p_{i} \log p_{i}"),
    ("Бином Ньютона", "бином ньютон", r"(a + b)^{n} = \sum_{k=0}^{n} C_{n}^{k} a^{n-k} b^{k}"),
    ("Число сочетаний", "сочетан комбинаци", r"C_{n}^{k} = \frac{n!}{k! (n - k)!}"),
    ("Уравнение Шрёдингера", "шрёдингер шредингер",
     r"i \hbar \frac{\partial}{\partial t} \psi = H \psi"),
    ("Площадь шара", "площад сфер шар", r"S = 4 \pi r^{2}"),
    ("Объём шара", "объем объём шар", r"V = \frac{4}{3} \pi r^{3}"),
]

# ---------------------------- русская запись -> TeX -------------------------
_WORDS = [
    (r"\bв квадрате\b", "^2"), (r"\bв кубе\b", "^3"),
    (r"\bв степени\s+(\w+)", r"^{\1}"), (r"\bво?\s+степени\s+(\w+)", r"^{\1}"),
    (r"\bумножить на\b", r" \\cdot "), (r"\bделить на\b", " / "),
    (r"\bплюс\b", " + "), (r"\bминус\b", " - "), (r"\bравно\b", " = "),
    (r"\bсинус\b", r"\\sin "), (r"\bкосинус\b", r"\\cos "),
    (r"\bтангенс\b", r"\\tan "), (r"\bлогарифм\b", r"\\ln "),
    (r"\bэкспонента\b", "e^"), (r"\bбесконечност\w*", r"\\infty"),
    (r"\bальфа\b", r"\\alpha"), (r"\bбета\b", r"\\beta"),
    (r"\bгамма\b", r"\\gamma"), (r"\bдельта\b", r"\\delta"),
    (r"\bтета\b", r"\\theta"), (r"\bлямбда\b", r"\\lambda"),
    (r"\bсигма\b", r"\\sigma"), (r"\bомега\b", r"\\omega"),
    (r"\bпи\b", r"\\pi"), (r"\bмю\b", r"\\mu"),
]


def _expr_plain(s):
    try:
        return re.sub(r"\s+\^", "^", latexlite.from_plain(s))
    except Exception:
        return s


def _expr(s):
    """Кусок формулы по-русски/обычной записью -> LaTeX (подмножество)."""
    s = " " + str(s or "").strip().lower() + " "
    for pat, rep in _WORDS:
        s = re.sub(pat, rep, s)
    s = s.strip()
    if not s:
        return ""
    s = re.sub(r"\^\(([^()]*)\)", r"^{\1}", s)          # e^(-x) -> e^{-x}
    s = re.sub(r"(?<!\\)\b(arcsin|arccos|arctan|sin|cos|tan|ln|log|exp)\b",
               lambda m: "\\" + m.group(1), s)

    def piece(p):
        p = p.strip()
        return re.sub(r"\s+", " ", p) if "\\" in p else _expr_plain(p)

    if s.count("/") == 1 and "(" not in s:
        a_, b_ = s.split("/")
        if a_.strip() and b_.strip():     # простая дробь a/b -> \frac
            return r"\frac{%s}{%s}" % (piece(a_), piece(b_))
    if "\\" in s:
        return re.sub(r"\s+", " ", s)
    return _expr_plain(s)


_RE_INT = re.compile(r"интеграл\s+(?:от\s+)?(?P<f>.+?)(?:\s+по\s+(?P<v>[a-zа-я])\b)?"
                     r"(?:\s+от\s+(?P<a>\S+)\s+до\s+(?P<b>\S+))?\s*$", re.I)
_RE_SUM = re.compile(r"(?P<k>сумма|произведение)\s+(?:от\s+)?(?P<f>.+?)\s+"
                     r"(?:по|при|от)\s+(?P<i>[a-zа-я])\s*=\s*(?P<a>\S+)\s+до\s+(?P<b>\S+)\s*$", re.I)
_RE_LIM = re.compile(r"предел\s+(?:от\s+)?(?P<f>.+?)\s+при\s+(?P<x>[a-zа-я])\s+"
                     r"(?:стремится|стремящемся|→)\s*(?:к\s+)?(?P<a>\S+)\s*$", re.I)
_RE_DER = re.compile(r"(?P<p>частная\s+)?производная\s+(?:от\s+)?(?P<f>.+?)"
                     r"(?:\s+по\s+(?P<v>[a-zа-я])\b)?\s*$", re.I)
_RE_FRAC = re.compile(r"дробь\s+(?P<a>.+?)\s+(?:над|делить на|на)\s+(?P<b>.+?)\s*$", re.I)
_RE_ROOT = re.compile(r"корень\s+(?:(?P<n>\w+)(?:-?[йяе]\s+степени\s+)?\s*)?из\s+(?P<x>.+?)\s*$", re.I)


def _num(s):
    s = str(s).strip(" .,;")
    if s in ("бесконечности", "бесконечность", "∞", "inf"):
        return r"\infty"
    if s in ("-бесконечности", "минус бесконечности"):
        return r"-\infty"
    return _expr(s)


def parse(task):
    """Разбор задания -> (title, tex) | None."""
    t = re.sub(r"\s+", " ", str(task or "").strip(" .!?")).strip()
    low = t.lower()
    m = _RE_INT.match(low)
    if m and "интеграл" in low and not re.search(r"гаусс|эйлер", low):
        f = _expr(m.group("f"))
        v = (m.group("v") or "x")
        lim = ""
        if m.group("a") and m.group("b"):
            lim = "_{%s}^{%s}" % (_num(m.group("a")), _num(m.group("b")))
        return "Интеграл", r"\int%s %s \, d%s" % (lim, f, v)
    m = _RE_SUM.match(low)
    if m:
        g = r"\sum" if m.group("k").startswith("сумм") else r"\prod"
        return ("Сумма" if g == r"\sum" else "Произведение"), \
            r"%s_{%s=%s}^{%s} %s" % (g, m.group("i"), _num(m.group("a")),
                                     _num(m.group("b")), _expr(m.group("f")))
    m = _RE_LIM.match(low)
    if m:
        return "Предел", r"\lim_{%s \to %s} %s" % (
            m.group("x"), _num(m.group("a")), _expr(m.group("f")))
    m = _RE_DER.match(low)
    if m and "производн" in low and not re.search(r"определен", low):
        v = m.group("v") or "x"
        d = r"\partial" if m.group("p") else "d"
        return "Производная", r"\frac{%s}{%s %s} %s" % (
            d, d, v, _expr(m.group("f")))
    m = _RE_FRAC.match(low)
    if m:
        return "Дробь", r"\frac{%s}{%s}" % (_expr(m.group("a")), _expr(m.group("b")))
    m = _RE_ROOT.match(low)
    if m and low.startswith("корень") and "уравнен" not in low:
        n = m.group("n")
        x = _expr(m.group("x"))
        if n and n not in ("квадратный",):
            nn = {"кубический": "3", "третьей": "3", "третий": "3"}.get(n, n)
            return "Корень", r"\sqrt[%s]{%s}" % (nn, x)
        return "Корень", r"\sqrt{%s}" % x
    return None


# ---------------------------- библиотека + выученное ------------------------
def _stems(text):
    return {w[:5] for w in re.findall(r"[а-яёa-z0-9]{3,}", str(text).lower())}


def _learned():
    d = store.load_json(LIB_FILE, None)
    return d if isinstance(d, list) else []


def lookup(task):
    """Ключевые слова задания -> лучшая формула (title, tex, откуда) | None."""
    q = _stems(re.sub(r"формул\w*|latex|латех\w*|латекс\w*|tex\b", " ",
                      str(task).lower()))
    if not q:
        return None
    best, bs = None, 0.0
    for title, keys, tex in BUILTIN:
        ks = _stems(keys + " " + title)
        sc = len(q & ks)
        if sc:
            sc = sc + len(q & ks) / float(len(ks) + 1)
        if sc > bs:
            best, bs = (title, tex, "библиотека"), sc
    for it in _learned():
        ks = set(it.get("k") or [])
        sc = len(q & ks)
        if sc >= 2 and sc > bs:
            best, bs = (it.get("t") or "Формула из базы знаний", it["tex"],
                        "выучено из статьи «%s»" % str(it.get("src") or "")[:50]), sc
    return best if bs >= 1 else None


_MATH_RE = re.compile(r"\$\$(.+?)\$\$|\\\[(.+?)\\\]|\\\((.+?)\\\)|"
                      r"(?<![\\$\d])\$([^$\n]{3,200}?)\$", re.S)


def wiki_math(text):
    """Википедия отдаёт формулы как «{\\displaystyle …}» (вложенные скобки).
    Превращаем в «$…$», чтобы learn_from_text их выучил, а в тексте статьи
    остался читаемый TeX вместо мусора."""
    text = str(text or "")
    key = "{\\displaystyle"
    if key not in text:
        return text
    out, i = [], 0
    while True:
        j = text.find(key, i)
        if j < 0:
            out.append(text[i:])
            break
        out.append(text[i:j])
        depth, k = 0, j
        while k < len(text):
            c = text[k]
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    break
            k += 1
        if depth != 0 or k - j > 400:
            out.append(text[j:j + len(key)])
            i = j + len(key)
            continue
        tex = text[j + len(key):k].strip()
        out.append(" $%s$ " % tex if tex else " ")
        i = k + 1
    return "".join(out)


def learn_from_text(title, text):
    """Из статьи: вставки LaTeX, проверенные движком -> библиотека. Возврат N."""
    text = str(text or "")
    if "\\" not in text:
        return 0
    found = []
    for m in _MATH_RE.finditer(text):
        tex = next((g for g in m.groups() if g), "").strip()
        if len(tex) < 5 or len(tex) > 300 or "\\" not in tex:
            continue
        a = max(0, m.start() - 160)
        ctx = text[a:m.start()] + " " + text[m.end():m.end() + 60]
        found.append((tex, ctx))
    if not found:
        return 0
    added = 0
    with _LOCK:
        lib = _learned()
        have = {it.get("h") for it in lib}
        for tex, ctx in found[:40]:
            h = hashlib.md5(tex.encode("utf-8")).hexdigest()[:12]
            if h in have:
                continue
            try:
                if not latexlite.render(tex).get("ok"):
                    continue
            except Exception:
                continue
            lib.append({"h": h, "tex": tex, "t": str(title)[:80],
                        "k": sorted(_stems(ctx))[:24], "src": str(title)[:60],
                        "ts": int(time.time())})
            have.add(h)
            added += 1
        if added:
            store.save_json(LIB_FILE, lib[-LIB_MAX:])
    return added


# ---------------------------- вход из чата ----------------------------------
_ASK = re.compile(
    r"^\s*(?:пожалуйста[, ]*)?(?:напиши|составь|запиши|выведи|дай|покажи|сделай|"
    r"сформулируй|создай|сгенерируй|нарисуй|как\s+записать|как\s+написать)\s+"
    r"(?:мне\s+)?(?:(?:формул\w*|уравнен\w*|выражен\w*)\s*)?"
    r"(?:(?:в|на)\s+(?:latex|латех\w*|латекс\w*|tex)\s*)?(?P<t>.*)$", re.I)
_HAS = re.compile(r"формул|latex|латех|латекс|\btex\b", re.I)
_DIRECT = re.compile(r"^\s*(?:latex|латех\w*|латекс\w*|tex)\s*[:：]\s*(?P<t>.+)$", re.I)


def compose(task):
    """-> {"ok","title","tex","how"} | {"ok":False,"error"}."""
    t = str(task or "").strip()
    if not t:
        return {"ok": False, "error": "что за формула? Скажи, например: "
                "«напиши формулу интеграл от x в квадрате от 0 до 1»"}
    t = re.sub(r"^(?:формул\w*|в\s+latex|в\s+латех\w*|в\s+латекс\w*)\s*", "",
               t, flags=re.I).strip()
    p = parse(t)
    how = "разобрал задание"
    if not p:
        k = lookup(t)
        if k:
            p, how = (k[0], k[1]), k[2]
    if not p:
        return {"ok": False, "error": "такой формулы не знаю: не нашёл ни в "
                "библиотеке (физика, геометрия, статистика, нейросети), ни в "
                "прочитанных статьях. Могу собрать по описанию: «интеграл от … "
                "от a до b», «сумма … от i=1 до n», «предел … при x стремится "
                "к a», «производная … по x», «дробь a над b», «корень из …»."}
    title, tex = p
    r = latexlite.render(tex)
    if not r.get("ok"):
        return {"ok": False, "error": "собрал LaTeX «%s», но движок его не "
                "нарисовал: %s" % (tex, r.get("error") or "ошибка разбора")}
    return {"ok": True, "title": title, "tex": tex, "how": how}


def route(text, low):
    """Вызов из imagine.route: запрос формулы -> (reply, info) | None."""
    m = _DIRECT.match(text)
    task = None
    if m:
        task = m.group("t")
        tex = task.strip().strip("$")
        if "\\" in tex:                       # дан готовый LaTeX — рисуем
            r = latexlite.render(tex)
            if r.get("ok"):
                return ("Нарисовал:\n" + latexlite.block(tex) + "\nLaTeX: `%s`"
                        % tex), {"latexgen": "render"}
    else:
        m = _ASK.match(text)
        if not m or not _HAS.search(text):
            return None
        task = m.group("t")
    c = compose(task)
    if not c["ok"]:
        # v3.106: формулы нет ни в библиотеке, ни в статьях — НЕ отвечаем
        # заготовкой «такой формулы не знаю…»: вопрос идёт дальше — в базу
        # знаний, интернет и память мозга (мозг + своя LLM)
        return None
    return ("%s (%s):\n%s\nLaTeX: `%s`"
            % (c["title"], c["how"], latexlite.block(c["tex"]), c["tex"])), \
        {"latexgen": "ok", "tex": c["tex"]}


def stats():
    return {"builtin": len(BUILTIN), "learned": len(_learned())}
