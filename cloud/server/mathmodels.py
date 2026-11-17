# -*- coding: utf-8 -*-
# ===========================================================================
# mathmodels.py — ФОРМУЛЫ И МАТЕМАТИЧЕСКИЕ МОДЕЛИ (v3.8)
# ---------------------------------------------------------------------------
# Мозг облака и платы сами ПИШУТ ФОРМУЛЫ и СТРОЯТ МОДЕЛИ — без LLM:
#   1) БАЗА ФОРМУЛ (FORMULAS): каждая формула — id, имя, ключи-темы,
#      переменные (с подсказками-ключами для распознавания чисел в
#      вопросе) и ВЫРАЖЕНИЕ. Пополнение из облака: GET /api/math/formulas
#      (плата сливает это в dicts/math_formulas.json, как рецепты).
#   2) ВЫЧИСЛИТЕЛЬ (safe_eval): AST-обход с белым списком узлов и
#      функций — никакой произвольной executes. Честные ошибки вместо
#      падений: неизвестная переменная, деление на ноль, скобки.
#   3) МАТЕМАТИЧЕСКИЕ МОДЕЛИ: по парам чисел (x,y) мозг строит модель —
#      линейную (МНК), квадратичную (нормальные уравнения, Крамер) или
#      экспоненциальную — выбирает по R², показывает формулу с числами
#      и делает ПРОГНОЗ. Слабая модель честно названа слабой.
# ===========================================================================
import ast
import math
import re

# ========================= БЕЗОПАСНЫЙ ВЫЧИСЛИТЕЛЬ ===========================
_FUNCS1 = {
    "sqrt": math.sqrt, "abs": abs, "sin": math.sin, "cos": math.cos,
    "tan": math.tan, "log": math.log, "ln": math.log, "log10": math.log10,
    "exp": math.exp, "round": round,
}
_FUNCS2 = {"min": min, "max": max, "pow": pow}
_CONSTS = {"pi": math.pi, "π": math.pi, "e": math.e}
_BINOPS = {ast.Add: lambda a, b: a + b, ast.Sub: lambda a, b: a - b,
           ast.Mult: lambda a, b: a * b, ast.Div: lambda a, b: a / b,
           ast.FloorDiv: lambda a, b: a // b, ast.Mod: lambda a, b: a % b,
           ast.Pow: lambda a, b: a ** b}


class FormulaError(ValueError):
    """Честная ошибка вычисления формулы (текст — для человека)."""


def safe_eval(expr, env=None):
    """Посчитать выражение формулы. Разрешены: числа, переменные из env,
    + - * / // % **, скобки, функции из белого списка. Всё прочее —
    FormulaError с понятным текстом (код НЕ исполняется)."""
    s = str(expr or "").strip()
    s = s.replace("х", "x").replace("×", "*").replace("·", "*") \
         .replace("÷", "/").replace("^", "**").replace(",", ".")
    if not s:
        raise FormulaError("пустое выражение")
    env = dict(_CONSTS if env is None else {**_CONSTS, **env})
    try:
        tree = ast.parse(s, mode="eval")
    except SyntaxError as e:
        raise FormulaError("выражение не разобрать: %s" % str(e)[:60])

    def ev(n):
        if isinstance(n, ast.Expression):
            return ev(n.body)
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)):
            return n.value
        if isinstance(n, ast.BinOp) and type(n.op) in _BINOPS:
            a, b = ev(n.left), ev(n.right)
            try:
                r = _BINOPS[type(n.op)](a, b)
            except ZeroDivisionError:
                raise FormulaError("деление на ноль")
            if isinstance(n.op, ast.Pow) and abs(b) > 200:
                raise FormulaError("слишком большая степень")
            if isinstance(r, complex):
                raise FormulaError("корень из отрицательного числа")
            return r
        if isinstance(n, ast.UnaryOp) and isinstance(n.op, (ast.UAdd,
                                                            ast.USub)):
            v = ev(n.operand)
            return -v if isinstance(n.op, ast.USub) else v
        if isinstance(n, ast.Name):
            if n.id in env:
                return env[n.id]
            raise FormulaError("неизвестная переменная «%s»" % n.id)
        if isinstance(n, ast.Call):
            if not isinstance(n.func, ast.Name) or n.keywords:
                raise FormulaError("такие вызовы не поддерживаются")
            fname = n.func.id
            if fname not in _FUNCS1 and fname not in _FUNCS2:
                raise FormulaError("нет функции «%s»" % fname)
            args = [ev(a) for a in n.args]
            if fname in _FUNCS1 and len(args) == 1:
                try:
                    return _FUNCS1[fname](args[0])
                except ValueError:
                    raise FormulaError(
                        "функция %s не определена для этого числа" % fname)
            if fname in _FUNCS2 and len(args) == 2:
                return _FUNCS2[fname](*args)
            raise FormulaError("у функции «%s» другой список аргументов"
                               % fname)
        raise FormulaError("в формуле есть лишнее (%s)" % type(n).__name__)

    r = ev(tree)
    if isinstance(r, complex):
        raise FormulaError("результат — комплексное число")
    return r


def _num(v):
    """Красивое число: 78.5398 -> 78.54, 12.0 -> 12."""
    if isinstance(v, int):
        return str(v)
    if abs(v) >= 1e10 or (v != 0 and abs(v) < 1e-6):
        return ("%.3e" % v).replace("e", "·10^").replace(".000·", "·")
    s = ("%.4f" % v).rstrip("0").rstrip(".")
    return s if s else "0"


# ============================ БАЗА ФОРМУЛ ===================================
# vars: name — имя в выражении; label — по-человечески; vkeys — по каким
# стемам искать число в вопросе («радиус 5», «радиусом 5»). Если по ключам
# не нашлось — числа назначаются ПО ПОРЯДКУ появления в вопросе.
def _f(fid, name, keys, expr, text, vars_, doc="", inv=None):
    return {"id": fid, "name": name, "keys": keys, "expr": expr,
            "text": text, "vars": vars_, "doc": doc, "inv": inv or ""}


FORMULAS = [
    _f("circle_area", "Площадь круга",
       ("площадь круг", "круг радиус", "круга радиус"),
       "pi * r ** 2", "S = π·r²",
       [{"name": "r", "label": "радиус", "vkeys": ("радиус", "радиусом",
                                                   "радиуса")}],
       "площадь круга через радиус"),
    _f("circle_len", "Длина окружности",
       ("длина окружност", "окружност", "обхват"),
       "2 * pi * r", "C = 2·π·r",
       [{"name": "r", "label": "радиус", "vkeys": ("радиус", "радиусом",
                                                   "радиуса")}],
       "длина окружности через радиус"),
    _f("rect_area", "Площадь прямоугольника",
       ("площадь прямоугольник", "площадь стены", "площадь пола",
        "площадь комнат"),
       "a * b", "S = a·b",
       [{"name": "a", "label": "ширина", "vkeys": ("ширин", "длин") },
        {"name": "b", "label": "длина", "vkeys": ("длин", "высот")}],
       "сторона на сторону"),
    _f("triangle_area", "Площадь треугольника",
       ("площадь треугольник", "треугольник основан"),
       "0.5 * a * h", "S = ½·a·h",
       [{"name": "a", "label": "основание", "vkeys": ("основан",)},
        {"name": "h", "label": "высота", "vkeys": ("высот", "высотой")}],
       "половина основания на высоту"),
    _f("kinetic", "Кинетическая энергия",
       ("кинетическ", "энергия движения", "энергия тела"),
       "m * v ** 2 / 2", "E = m·v²/2",
       [{"name": "m", "label": "масса", "vkeys": ("масс",)},
        {"name": "v", "label": "скорость", "vkeys": ("скорост",)}],
       "энергия движущегося тела"),
    _f("speed_dist", "Путь, скорость и время",
       ("скорост", "путь", "расстояни", "проеха", "пройде"),
       "v * t", "s = v·t",
       [{"name": "v", "label": "скорость", "vkeys": ("скорост", "км/ч")},
        {"name": "t", "label": "время", "vkeys": ("времен", "час", "часа",
                                                  "часов", "мин")}],
       "расстояние = скорость · время"),
    # v3.31: законы физики словами («сформулируй закон …»)
    _f("gravity_law", "Закон всемирного тяготения",
       ("всемирн тяготен", "закон тяготен", "гравитац", "притяжен тел"),
       "6.674e-11 * m1 * m2 / r ** 2", "F = G·m₁·m₂/r²",
       [{"name": "m1", "label": "масса первого тела, кг", "vkeys": ("масс",)},
        {"name": "m2", "label": "масса второго тела, кг", "vkeys": ()},
        {"name": "r", "label": "расстояние между телами, м",
         "vkeys": ("расстоян",)}],
       "два тела притягиваются с силой, прямо пропорциональной произведению "
       "их масс и обратно пропорциональной квадрату расстояния между ними; "
       "G = 6,674·10⁻¹¹ Н·м²/кг²"),
    _f("newton2", "Второй закон Ньютона",
       ("второй закон ньютон", "закон ньютон", "сила масса ускорен"),
       "m * a", "F = m·a",
       [{"name": "m", "label": "масса, кг", "vkeys": ("масс",)},
        {"name": "a", "label": "ускорение, м/с²", "vkeys": ("ускорен",)}],
       "сила, действующая на тело, равна произведению его массы на ускорение"),
    _f("hooke", "Закон Гука",
       ("закон гук", "гука", "упруг", "пружин"),
       "k * x", "F = k·x",
       [{"name": "k", "label": "жёсткость, Н/м", "vkeys": ("жёстк", "жестк")},
        {"name": "x", "label": "удлинение, м", "vkeys": ("удлинен", "растяж")}],
       "сила упругости пропорциональна удлинению пружины"),
    _f("archimedes", "Закон Архимеда",
       ("закон архимед", "архимед", "выталкивающ"),
       "rho * 9.81 * v", "F = ρ·g·V",
       [{"name": "rho", "label": "плотность жидкости, кг/м³",
         "vkeys": ("плотност",)},
        {"name": "v", "label": "объём погружённой части, м³",
         "vkeys": ("объём", "объем")}],
       "на тело в жидкости действует выталкивающая сила, равная весу "
       "вытесненной жидкости"),
    _f("joule_lenz", "Закон Джоуля — Ленца",
       ("джоул", "ленц", "тепло ток"),
       "i ** 2 * r * t", "Q = I²·R·t",
       [{"name": "i", "label": "сила тока, А", "vkeys": ("ток",)},
        {"name": "r", "label": "сопротивление, Ом", "vkeys": ("сопротивлен",)},
        {"name": "t", "label": "время, с", "vkeys": ("времен", "сек")}],
       "количество теплоты в проводнике пропорционально квадрату тока, "
       "сопротивлению и времени"),
    _f("ohm", "Закон Ома",
       ("ома", "закон ом", "сопротивлен", "напряжен", "ток цеп"),
       "u / r", "I = U/R",
       [{"name": "u", "label": "напряжение", "vkeys": ("напряжен", "вольт")},
        {"name": "r", "label": "сопротивление", "vkeys": ("сопротивлен",
                                                          "ом")}],
       "ток = напряжение / сопротивление"),
    _f("percent_of", "Процент от числа",
       ("процент от", "процентов от", "% от", "скидк", "налог"),
       "x * p / 100", "Y = X·p/100",
       [{"name": "p", "label": "сколько процентов", "vkeys": ("процент", "%")},
        {"name": "x", "label": "число", "vkeys": ("от", "число", "сумм")}],
       "p процентов от X"),
    _f("average", "Среднее арифметическое",
       ("среднее", "средн чисел", "средн значен"),
       "s / n", "M = (a₁+…+aₙ)/n",
       [{"name": "s", "label": "сумма чисел", "vkeys": ()},
        {"name": "n", "label": "количество", "vkeys": ()}],
       "сумма всех чисел, делённая на их количество"),
    _f("pythagoras", "Теорема Пифагора",
       ("пифагор", "гипотенуз", "катет"),
       "sqrt(a ** 2 + b ** 2)", "c = √(a² + b²)",
       [{"name": "a", "label": "катет a", "vkeys": ("катет a", "катет ")},
        {"name": "b", "label": "катет b", "vkeys": ("катет b",)}],
       "гипотенуза по двум катетам"),
    _f("quadratic_roots", "Корни квадратного уравнения",
       ("квадратн", "дискриминан", "корни уравнен", "ax2 + bx"),
       "(-b + sqrt(b ** 2 - 4 * a * c)) / (2 * a)", "x = (-b±√D)/(2a)",
       [{"name": "a", "label": "коэффициент a", "vkeys": ("a=", "коэф a")},
        {"name": "b", "label": "коэффициент b", "vkeys": ("b=", "коэф b")},
        {"name": "c", "label": "коэффициент c", "vkeys": ("c=", "коэф c")}],
       "старший корень через дискриминант D = b²−4ac",
       inv="второй корень: (-b−√D)/(2a)"),
    _f("exp_growth", "Рост на проценты (вклад, размножение)",
       ("вклад", "сложн процент", "вырастет через", "размнож",
        "рост на процент", "процентов на", "под "),
       "x0 * (1 + p / 100) ** n", "X = X₀·(1+p/100)ⁿ",
       [{"name": "x0", "label": "начальная сумма", "vkeys": ("вклад",
                                                             "начальн", "было")},
        {"name": "p", "label": "процентов в шаг", "vkeys": ("под",)},
        {"name": "n", "label": "число шагов", "vkeys": ("лет", "год",
                                                        "месяц", "шаг",
                                                        "дней")}],
       "экспоненциальный рост на p процентов за n шагов"),
    _f("celsius_fahr", "Цельсий в Фаренгейт",
       ("фаренгейт", "цельси"),
       "c * 9 / 5 + 32", "F = C·9/5 + 32",
       [{"name": "c", "label": "градусы Цельсия", "vkeys": ("цельси",
                                                            "градус")}],
       "перевод температуры"),
    _f("perimeter_rect", "Периметр прямоугольника",
       ("периметр прямоугольник", "периметр стен", "периметр участк"),
       "2 * (a + b)", "P = 2·(a+b)",
       [{"name": "a", "label": "ширина", "vkeys": ("ширин",)},
        {"name": "b", "label": "длина", "vkeys": ("длин", "высот")}],
       "две стороны туда, две обратно",
       inv="сторона по периметру: a = P/2 − b"),
    _f("square_area", "Площадь квадрата",
       ("площадь квадрат", "площадь квадрата"),
       "a * a", "S = a²",
       [{"name": "a", "label": "сторона", "vkeys": ("сторон",)}],
       "сторона на саму себя",
       inv="сторона по площади: a = √S"),
    _f("cube_volume", "Объём куба",
       ("объем куб", "объём куб"),
       "a ** 3", "V = a³",
       [{"name": "a", "label": "ребро куба", "vkeys": ("ребро", "ребро",)}],
       "ребро в кубе",
       inv="ребро по объёму: a = ∛V"),
    _f("box_volume", "Объём параллелепипеда (коробки)",
       ("объем параллелепипед", "объём параллелепипед", "объем коробк",
        "объём коробк", "объем ящик", "объём ящик", "объем комнат",
        "объём комнат"),
       "a * b * c", "V = a·b·c",
       [{"name": "a", "label": "длина", "vkeys": ("длин",)},
        {"name": "b", "label": "ширина", "vkeys": ("ширин",)},
        {"name": "c", "label": "высота", "vkeys": ("высот",)}],
       "длина · ширина · высота"),
    _f("density_mass", "Масса через плотность",
       ("плотност", "масса через плотност"),
       "ro * v", "m = ρ·V",
       [{"name": "ro", "label": "плотность", "vkeys": ("плотност",)},
        {"name": "v", "label": "объём", "vkeys": ("объем", "объём")}],
       "масса = плотность · объём",
       inv="ρ = m/V, V = m/ρ"),
    _f("pressure", "Давление",
       ("давлен",),
       "f / s", "p = F/S",
       [{"name": "f", "label": "сила", "vkeys": ("сил",)},
        {"name": "s", "label": "площадь опоры", "vkeys": ("площад",)}],
       "давление = сила / площадь опоры",
       inv="F = p·S"),
    _f("gravity", "Сила тяжести",
       ("сил тяжест", "тяжест", "вес тела"),
       "m * g", "F = m·g",
       [{"name": "m", "label": "масса", "vkeys": ("масс",)},
        {"name": "g", "label": "g (на Земле 9.8)", "vkeys": ("g", "ж")}],
       "сила тяжести = масса · ускорение свободного падения"),
    # ================== v3.18: ДОБАВКА (латекс «не работал» — базе
    # не хватало половины школьных и физических тем) =================
    _f("emc2", "Энергия покоя (Эйнштейн)",
       ("энергия покоя", "эйнштейн", "формула энергии",
        "энергии поко", "формула эйнштейна"),
       "m * c ** 2", "E = m·c²",
       [{"name": "m", "label": "масса", "vkeys": ("масс",)},
        {"name": "c", "label": "скорость света (299792458)",
         "vkeys": ("скорость света", "света")}],
       "энергия покоя тела: масса на квадрат скорости света"),
    _f("potential_energy", "Потенциальная энергия",
       ("потенциальн", "энергия высот", "энергия подъ"),
       "m * g * h", "E = m·g·h",
       [{"name": "m", "label": "масса", "vkeys": ("масс",)},
        {"name": "h", "label": "высота", "vkeys": ("высот",)},
        {"name": "g", "label": "g (на Земле 9.8)", "vkeys": ("g", "ж")}],
       "энергия поднятого тела"),
    _f("sphere_volume", "Объём шара",
       ("объем шар", "объём шар", "объем сферы", "объём сферы"),
       "4 / 3 * pi * r ** 3", "V = 4/3·π·r³",
       [{"name": "r", "label": "радиус", "vkeys": ("радиус", "радиусом",
                                                   "радиуса")}],
       "объём шара через радиус"),
    _f("cylinder_volume", "Объём цилиндра",
       ("объем цилиндр", "объём цилиндр"),
       "pi * r ** 2 * h", "V = π·r²·h",
       [{"name": "r", "label": "радиус", "vkeys": ("радиус", "радиусом",
                                                   "радиуса")},
        {"name": "h", "label": "высота", "vkeys": ("высот",)}],
       "площадь основания на высоту"),
    _f("cone_volume", "Объём конуса",
       ("объем конус", "объём конус"),
       "pi * r ** 2 * h / 3", "V = π·r²·h/3",
       [{"name": "r", "label": "радиус", "vkeys": ("радиус", "радиусом",
                                                   "радиуса")},
        {"name": "h", "label": "высота", "vkeys": ("высот",)}],
       "треть объёма цилиндра с тем же основанием"),
    _f("circle_by_d", "Площадь круга через диаметр",
       ("площадь круг диаметр", "круг через диаметр", "диаметр площадь"),
       "pi * d ** 2 / 4", "S = π·d²/4",
       [{"name": "d", "label": "диаметр", "vkeys": ("диаметр", "диаметром")}],
       "радиус = половина диаметра"),
    _f("triangle_perimeter", "Периметр треугольника",
       ("периметр треугольник",),
       "a + b + c", "P = a+b+c",
       [{"name": "a", "label": "сторона a", "vkeys": ()},
        {"name": "b", "label": "сторона b", "vkeys": ()},
        {"name": "c", "label": "сторона c", "vkeys": ()}],
       "сумма трёх сторон"),
    _f("acceleration", "Ускорение",
       ("ускорен",),
       "(v1 - v0) / t", "a = (v₁−v₀)/t",
       [{"name": "v1", "label": "конечная скорость", "vkeys": ("скорост",)},
        {"name": "v0", "label": "начальная скорость", "vkeys": ("начальн",)},
        {"name": "t", "label": "время", "vkeys": ("времен", "сек", "час",
                                                  "за ")}],
       "насколько меняется скорость за секунду"),
    _f("work_force", "Механическая работа",
       ("работа силы", "механическ работ", "соверш работ"),
       "f * s", "A = F·s",
       [{"name": "f", "label": "сила", "vkeys": ("сил",)},
        {"name": "s", "label": "путь", "vkeys": ("пут", "расстояни",
                                                 "метр")}],
       "сила на путь"),
    _f("power_time", "Мощность",
       ("мощност",),
       "a / t", "P = A/t",
       [{"name": "a", "label": "работа", "vkeys": ("работ", "джоул")},
        {"name": "t", "label": "время", "vkeys": ("времен", "сек", "час")}],
       "работа за единицу времени"),
    _f("efficiency", "КПД",
       ("кпд", "коэффициент полезного"),
       "useful * 100 / spent", "η = Aпол/Aзатр·100%",
       [{"name": "useful", "label": "полезная работа", "vkeys": ("полезн",
                                                                 "работа")},
        {"name": "spent", "label": "затраченная работа",
         "vkeys": ("затрач", "полная", "получен")}],
       "сколько процентов работы идёт делу, а не в тепло"),
    _f("coulomb", "Закон Кулона",
       ("кулон", "заряд взаимодейств"),
       "8.99e9 * q1 * q2 / r ** 2", "F = k·q₁·q₂/r²",
       [{"name": "q1", "label": "заряд 1", "vkeys": ("заряд",)},
        {"name": "q2", "label": "заряд 2", "vkeys": ("втор", "друг")},
        {"name": "r", "label": "расстояние", "vkeys": ("расстояни", "метр")}],
       "сила между зарядами (k ≈ 9·10⁹)"),
    _f("photon", "Энергия фотона",
       ("фотон", "энергия света", "энергия квант"),
       "6.626e-34 * nu", "E = h·ν",
       [{"name": "nu", "label": "частота (Гц)", "vkeys": ("частот", "гц",
                                                          "герц")}],
       "энергия света через частоту (h — Планка)"),
    _f("wave_speed", "Скорость волны",
       ("длина волны", "скорость волны"),
       "lam * nu", "v = λ·ν",
       [{"name": "lam", "label": "длина волны", "vkeys": ("длин волны",
                                                          "длину волны",
                                                          "лямбда")},
        {"name": "nu", "label": "частота", "vkeys": ("частот", "гц")}],
       "длина волны на частоту"),
    _f("bmi", "Индекс массы тела",
       ("индекс массы тела", "имт", "индекс массы"),
       "m / (h ** 2)", "ИМТ = m/h²",
       [{"name": "m", "label": "масса (кг)", "vkeys": ("масс", "кг", "вес")},
        {"name": "h", "label": "рост (м)", "vkeys": ("рост", "метр")}],
       "норма примерно 18.5–25"),
    _f("heat_energy", "Количество теплоты",
       ("количеств теплот", "нагре", "теплота"),
       "4200 * m * dt", "Q = c·m·Δt",
       [{"name": "m", "label": "масса воды (кг)", "vkeys": ("масс", "кг",
                                                            "литр")},
        {"name": "dt", "label": "на сколько градусов", "vkeys": ("градус",
                                                                 "на ")}],
       "нагрев воды: c воды = 4200 Дж/(кг·°C)"),
    _f("power_current", "Мощность тока",
       ("мощность тока", "мощност цепи"),
       "u * u / r", "P = U²/R",
       [{"name": "u", "label": "напряжение", "vkeys": ("напряжен", "вольт")},
        {"name": "r", "label": "сопротивление", "vkeys": ("сопротивлен",
                                                          "ом")}],
       "закон Джоуля-Ленца через сопротивление"),
    _f("current_charge", "Сила тока",
       ("сила тока", "ток через заряд"),
       "q / t", "I = q/t",
       [{"name": "q", "label": "заряд (Кл)", "vkeys": ("заряд", "кулон")},
        {"name": "t", "label": "время (с)", "vkeys": ("времен", "сек")}],
       "заряд, прошедший за секунду"),
    _f("simple_percent", "Простые проценты",
       ("простые процент", "процент годовых", "начислит процент"),
       "x0 * (1 + p * n / 100)", "S = X·(1+p·n/100)",
       [{"name": "x0", "label": "начальная сумма", "vkeys": ("вклад",
                                                             "сумм", "было")},
        {"name": "p", "label": "процентов за шаг", "vkeys": ("под",
                                                             "процент")},
        {"name": "n", "label": "число шагов", "vkeys": ("лет", "год",
                                                        "месяц", "шаг")}],
       "без капитализации: проценты не растут"),
]

_FORMULA_BY_ID = {f["id"]: f for f in FORMULAS}


def formulas_list(full=False):
    """Список формул (для панели и синхронизации платы). full=1 — с
    выражениями и ключами переменных (плата считает сама)."""
    out = []
    for f in FORMULAS:
        row = {"id": f["id"], "name": f["name"], "text": f["text"],
               "doc": f["doc"], "vars": [v["label"] for v in f["vars"]]}
        if full:
            row["expr"] = f["expr"]
            row["keys"] = list(f["keys"])
            row["vars"] = f["vars"]
            row["inv"] = f.get("inv", "")
        out.append(row)
    return out


_NUM_RE = re.compile(r"-?\d+(?:[.,]\d+)?")


_ENDINGS = re.compile(
    r"(?:иями|ями|ами|ов|ев|ей|ах|ях|ам|ям|ой|ый|ий|ая|яя|ое|ее|ые|ие|"
    r"ью|ом|ем|у|ю|а|я|о|е|ы|и|ь)$")


def _stem(w):
    """Лёгкий стеммер: срезать до двух русских окончаний (мин 4 знака).
    «прямоугольника»->«прямоугольник», «площади»->«площад»,
    «радиусом»->«радиус», «формулу»->«формул». Ключи и вопрос режутся
    ОДИНАКОВО, поэтому падежи и числа больше не ломают выбор формулы."""
    w = str(w).lower().replace("ё", "е")   # е/ё не должны различаться
    for _ in range(2):
        if len(w) < 4:
            break
        w2 = _ENDINGS.sub("", w)
        if w2 == w or len(w2) < 3:
            break
        w = w2
    return w


def _bag(low):
    return set(_stem(w) for w in re.findall(r"[а-яёa-z0-9]+", low))


def formula_pick(text):
    """Выбрать формулу под вопрос: число совпадений ключей-тем.
    v3.10: ключи и вопрос сравниваются по ОСНОВАМ слов — «площади круга»
    теперь находит формулу «площадь круг» (падежи не мешают).
    Возврат (formula|None, score)."""
    # v3.31: БАГФИКС — вопрос не приводился к нижнему регистру: «Ома»
    # токенизировалось как «ма», и «закон Ома» формулу не находил
    bag = _bag(str(text or "").lower())
    best, hits = None, 0
    for f in FORMULAS:
        n = 0
        for k in f["keys"]:
            kw = [_stem(x) for x in re.findall(r"[а-яёa-z0-9]+", k)]
            if kw and all(x in bag for x in kw):
                n += 1
        if n > hits:
            best, hits = f, n
    return best, hits


def formula_bind(f, text):
    """Распознать значения переменных в вопросе. Приоритет: «ключ число»
    (vkeys) и «имя=число», затем числа ПО ПОРЯДКУ. Возврат (values,
    missing) — missing: имена ненайденных переменных."""
    low = (text or "").lower()
    nums = [float(x.replace(",", ".")) for x in _NUM_RE.findall(low)]
    values, used = {}, set()
    # 1) явные «имя=число» и «ключ число»
    for v in f["vars"]:
        m = re.search(r"\b%s\s*[=:]\s*(-?\d+(?:[.,]\d+)?)" % v["name"], low)
        if m:
            values[v["name"]] = float(m.group(1).replace(",", "."))
        for k in v.get("vkeys") or ():
            mm = re.search(re.escape(k) + r"[а-яё]{0,4}\s*(-?\d+(?:[.,]\d+)?)",
                           low)
            if mm and v["name"] not in values:
                values[v["name"]] = float(mm.group(1).replace(",", "."))
                used.add(len(_NUM_RE.findall(low[:mm.start(1)])))
    # 2) остальным переменным — числа по порядку (не занятые ключами)
    queue = [i for i, x in enumerate(nums) if i not in used]
    for v in f["vars"]:
        if v["name"] in values:
            continue
        if queue:
            values[v["name"]] = nums[queue.pop(0)]
        else:
            values[v["name"]] = None
    missing = [v["name"] for v in f["vars"] if values.get(v["name"]) is None]
    return values, missing


def formula_solve(text):
    """Полный цикл: вопрос -> формула -> значения -> вычисление -> ТЕКСТ
    ответа (честный, с формулой и подстановкой)."""
    f, hits = formula_pick(text)
    if not f or not hits:
        themes = "; ".join(x["name"] for x in FORMULAS[:8])
        return ("В базе формул (%d штук) под это темы нет. Знакомое: %s… "
                "Скажи, например: «площадь круга радиусом 5»."
                % (len(FORMULAS), themes)), False
    values, missing = formula_bind(f, text)
    # v3.18: честные константы по умолчанию (названы в ответе)
    defnote = ""
    if f["id"] == "potential_energy" and "g" in missing and \
            values.get("m") is not None and values.get("h") is not None:
        values["g"] = 9.8
        missing.remove("g")
        defnote = " (взяла g = 9.8 — Земля)"
    if missing:
        labels = ", ".join(v["label"] for v in f["vars"]
                           if v["name"] in missing)
        # v3.18: карточка формулы появляется СРАЗУ, даже без чисел —
        # раньше «формула энергии E=mc^2» уходила без карточки
        return ("Формула нашлась: %s (%s)%s. Нужны значения: %s — назови их "
                "числами в вопросе (например: «%s 5» или «%s 5»), и я "
                "посчитаю.%s" % (f["name"], f["text"], defnote, labels,
                                 f["vars"][0]["label"],
                                 f["vars"][0]["name"],
                                 _tex_block(f["text"], f["text"]))), False
    # среднее арифметическое — особый случай: сумма и количество из чисел
    if f["id"] == "average":
        nums = [float(x.replace(",", ".")) for x in _NUM_RE.findall(
            (text or "").lower())]
        if len(nums) < 2:
            return ("Для среднего нужно хотя бы два числа — перечисли их "
                    "через запятую."), False
        values = {"s": sum(nums), "n": len(nums)}
        detail = " + ".join(_num(x) for x in nums)
        val = values["s"] / values["n"]
        return ("Среднее арифметическое: M = (%s)/%d = %s" %
                (detail, len(nums), _num(val))), True
    try:
        val = safe_eval(f["expr"], values)
    except FormulaError as e:
        return ("Формула %s не посчиталась: %s" % (f["text"], e)), False
    subs = ", ".join("%s=%s" % (v["name"], _num(values[v["name"]]))
                     for v in f["vars"])
    out = ("%s: %s. Подставила %s -> %s = %s" %
           (f["name"], f["doc"], subs, f["text"], _num(val)))
    if f.get("inv"):
        out += ". %s" % f["inv"]
    out += _tex_block(f["text"], f["text"])
    return out, True


# ======================== МАТЕМАТИЧЕСКИЕ МОДЕЛИ =============================
_PAIR2_RE = re.compile(r"^\s*(-?\d+)\s*,\s*(-?\d+)\s*$")


def parse_pairs(text):
    """Разобрать данные в пары (x,y): «1,2; 2,4; 3,5» (запятая — РАЗДЕЛИТЕЛЬ
    пары), «1 2; 2 4» или плоский ряд «1 2 2 4 3 5». Возврат (pairs, error)."""
    s = str(text or "").strip()
    if not s:
        return [], "данных нет — нужны пары x,y через точку с запятой"
    chunks = [c for c in re.split(r"[;\n]", s) if c.strip()]
    pairs = []
    for c in chunks:
        m2 = _PAIR2_RE.match(c)             # «1,2» — ровно два целых через запятую: ПАРА
        if m2:
            pairs.append((float(m2.group(1)), float(m2.group(2))))
            continue
        ns = [float(x.replace(",", ".")) for x in _NUM_RE.findall(c)]
        if len(ns) == 2:
            pairs.append((ns[0], ns[1]))
        elif len(ns) > 2:
            for i in range(0, len(ns) - 1, 2):
                pairs.append((ns[i], ns[i + 1]))
            if len(ns) % 2 == 1:
                return [], ("лишнее число в «%s» — пары неполные "
                            "(каждая пара: x и y)" % c.strip()[:30])
        elif ns:
            return [], ("в куске «%s» одно число — нужны два: x и y "
                        "(либо пара «1,2», либо «1 2»)" % c.strip()[:30])
    if len(pairs) < 2:
        return [], ("мало данных: минимум 2 пары x,y (например: "
                    "«1,2; 2,4; 3,5»)")
    return pairs, ""


def _mean(xs):
    return sum(xs) / len(xs)


def _r2(ys, pred):
    """Коэффициент детерминации R²: доля объяснённой дисперсии."""
    m = _mean(ys)
    ss_tot = sum((y - m) ** 2 for y in ys)
    ss_res = sum((y - p) ** 2 for y, p in zip(ys, pred))
    if ss_tot == 0:
        return 1.0 if ss_res == 0 else 0.0
    return max(0.0, 1.0 - ss_res / ss_tot)


def _lin_fit(xs, ys):
    n = len(xs)
    mx, my = _mean(xs), _mean(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    if sxx == 0:
        return None
    a = sxy / sxx
    b = my - a * mx
    pred = [a * x + b for x in xs]
    return {"kind": "lin", "a": a, "b": b, "r2": _r2(ys, pred),
            "pred": lambda x, a=a, b=b: a * x + b}


def _quad_fit(xs, ys):
    """МНК для y = a·x² + b·x + c: нормальные уравнения 3x3, Крамер."""
    if len(set(xs)) < 3:
        return None
    n = float(len(xs))
    sx = sum(xs); sx2 = sum(x * x for x in xs)
    sx3 = sum(x ** 3 for x in xs); sx4 = sum(x ** 4 for x in xs)
    sy = sum(ys); sxy = sum(x * y for x, y in zip(xs, ys))
    sx2y = sum(x * x * y for x, y in zip(xs, ys))
    # матрица A·[a,b,c] = B
    A = [[sx4, sx3, sx2], [sx3, sx2, sx], [sx2, sx, n]]
    B = [sx2y, sxy, sy]

    def det3(m):
        return (m[0][0] * (m[1][1] * m[2][2] - m[1][2] * m[2][1])
                - m[0][1] * (m[1][0] * m[2][2] - m[1][2] * m[2][0])
                + m[0][2] * (m[1][0] * m[2][1] - m[1][1] * m[2][0]))

    D = det3(A)
    if abs(D) < 1e-12:
        return None
    Aa = [[B[0], A[0][1], A[0][2]], [B[1], A[1][1], A[1][2]],
          [B[2], A[2][1], A[2][2]]]
    Ab = [[A[0][0], B[0], A[0][2]], [A[1][0], B[1], A[1][2]],
          [A[2][0], B[2], A[2][2]]]
    Ac = [[A[0][0], A[0][1], B[0]], [A[1][0], A[1][1], B[1]],
          [A[2][0], A[2][1], B[2]]]
    a = det3(Aa) / D; b = det3(Ab) / D; c = det3(Ac) / D
    pred = [a * x * x + b * x + c for x in xs]
    return {"kind": "quad", "a": a, "b": b, "c": c, "r2": _r2(ys, pred),
            "pred": lambda x, a=a, b=b, c=c: a * x * x + b * x + c}


def _exp_fit(xs, ys):
    """y = a·e^(b·x): линеаризация ln y = ln a + b·x (все y > 0)."""
    if len(set(xs)) < 2 or any(y <= 0 for y in ys):
        return None
    lny = [math.log(y) for y in ys]
    fit = _lin_fit(xs, lny)
    if not fit:
        return None
    b = fit["a"]; lna = fit["b"]; a = math.exp(lna)
    pred = [a * math.exp(b * x) for x in xs]
    return {"kind": "exp", "a": a, "b": b, "r2": _r2(ys, pred),
            "pred": lambda x, a=a, b=b: a * math.exp(b * x)}


KIND_NAMES = {"lin": "линейная", "quad": "квадратичная",
              "exp": "экспоненциальная"}


def fit_model(pairs, forecast=None):
    """Построить модель по парам: перебрать линейную/квадратичную/
    экспоненциальную, взять лучшую по R². Возврат (dict, error)."""
    if len(pairs) < 2:
        return None, "модели нужно хотя бы 2 пары"
    xs = [p[0] for p in pairs]
    ys = [p[1] for p in pairs]
    cands = []
    lin = _lin_fit(xs, ys)
    if lin:
        cands.append(lin)
    # честность против переобучения: квадратичная (3 параметра) на 3
    # точках ВСЕГДА точна, экспонента на 2 точках — тоже; такие «идеальные»
    # подгонки не участвуют в выборе
    if len(pairs) >= 4:
        q = _quad_fit(xs, ys)
        if q:
            cands.append(q)
    if len(pairs) >= 3:
        e = _exp_fit(xs, ys)
        if e:
            cands.append(e)
    if not cands:
        return None, ("модель не строится: точки совпадают по x или "
                      "значения не годятся")
    best = max(cands, key=lambda c: (c["r2"], c["kind"] == "lin"))

    def ftext(c):
        if c["kind"] == "lin":
            return "y = %s·x %s %s" % (_num(c["a"]),
                                       "+" if c["b"] >= 0 else "−",
                                       _num(abs(c["b"])))
        if c["kind"] == "quad":
            return "y = %s·x² %s %s·x %s %s" % (
                _num(c["a"]), "+" if c["b"] >= 0 else "−", _num(abs(c["b"])),
                "+" if c["c"] >= 0 else "−", _num(abs(c["c"])))
        return "y = %s·e^(%s·x)" % (_num(c["a"]), _num(c["b"]))

    out = {"kind": best["kind"], "name": KIND_NAMES[best["kind"]],
           "formula": ftext(best), "r2": round(best["r2"], 4),
           "points": len(pairs)}
    # честность: слабая и «тонкая» модели названы таковыми
    if len(pairs) == 2:
        out["note"] = ("точек всего 2 — любая модель проходит через обе "
                       "идеально; добавь данных, чтобы доверять прогнозу")
    elif best["r2"] < 0.5:
        out["note"] = ("данные ложатся плохо (R² < 0.5) — модель описывает "
                       "меньше половины разброса, доверять прогнозу нельзя")
    elif best["r2"] < 0.8:
        out["note"] = "R² средний — модель ловит тренд, но с шумом"
    if forecast is not None:
        try:
            out["forecast"] = round(best["pred"](float(forecast)), 4)
            out["forecast_x"] = forecast
        except (OverflowError, ValueError):
            out["forecast"] = None
            out["note"] = "прогноз не считается для x=%s" % forecast
    return out, ""


def model_reply(text):
    """Полный цикл: вопрос с данными -> пары -> модель -> прогноз -> ТЕКСТ
    (честный). Прогноз: число после слов «прогноз», «при x» — вырезается
    из данных ДО разбора, чтобы прогноз не попал в пары."""
    low = (text or "").lower()
    # 1) прогноз вырезаем первым: «— прогноз 5», «прогноз для 5», «при x=5»
    fc = None
    mfc = re.search(r"(?:—|-|–)?\s*(?:прогноз|предсказ|предскажи)\s*"
                    r"(?:для|при)?\s*(?:x\s*[=:—-]?\s*)?"
                    r"(-?\d+(?:[.,]\d+)?)", low)
    if mfc:
        fc = mfc.group(1).replace(",", ".")
        low = low[:mfc.start()] + low[mfc.end():]
    mfc2 = re.search(r"при\s*x\s*[=:]\s*(-?\d+(?:[.,]\d+)?)", low)
    if mfc2 and fc is None:
        fc = mfc2.group(1).replace(",", ".")
        low = low[:mfc2.start()] + low[mfc2.end():]
    # 2) данные: после слов «данные|по данным|точкам» либо весь остаток
    m = re.search(r"(?:по\s+данным|данные|точк[аам])\s*(.*)$", low, re.S)
    data_src = m.group(1) if m else low
    pairs, err = parse_pairs(data_src)
    if err:
        return ("Модель не построилась: %s. Формат: «модель по данным "
                "1,2; 2,4; 3,5 — прогноз 4»." % err), False
    mdl, err = fit_model(pairs, fc)
    if err:
        return "Модель не построилась: %s." % err, False
    out = ("Модель %s: %s (R² = %s, точек: %d)" %
           (mdl["name"], mdl["formula"], _num(mdl["r2"]), mdl["points"]))
    if mdl.get("forecast") is not None:
        out += ". Прогноз при x=%s: %s" % (_num(float(mdl["forecast_x"])),
                                           _num(mdl["forecast"]))
    if mdl.get("note"):
        out += ". %s" % mdl["note"]
    out += _tex_block(mdl["formula"], "y = …")
    return out, True


# ====================== СОСТАВЛЕНИЕ ФОРМУЛ (v3.10) ==========================
# «математические формулы не составляет» — теперь составляет: по теме из
# базы мозг собирает КАРТОЧКУ ФОРМУЛЫ: вид, смысл, переменные, обратная
# формула; если в вопросе есть числа — сразу подставляет и считает.
_COMPOSE_VERB = re.compile(r"состав|напиши|выведи|запиши|покажи|какая|какую|"
                           r"сформулир|формулир|объясни закон|закон|теорем|"
                           r"правило", re.I)


def _tex_block(expr, fallback=None):
    """v3.16: LaTeX-карточка формулы для чата (движок latexlite).
    Движок без внешних зависимостей; ошибка рендера не ломает ответ —
    вернётся пустая строка, текст формулы остаётся в ответе как был."""
    try:
        import latexlite
        return "\n" + latexlite.block(latexlite.from_plain(expr), fallback)
    except Exception:
        return ""


def compose_reply(text):
    """«Составь формулу …» -> карточка формулы из базы (без LLM):
    имя — вид — смысл — переменные — обратная формула (+ расчёт, если в
    вопросе уже есть все числа)."""
    f, hits = formula_pick(text)
    if not f:
        themes = "; ".join(x["name"] for x in FORMULAS)
        return ("Формул по этой теме в моей базе нет — я составляю формулы "
                "из своих %d тем: %s. Назови тему — соберу карточку: вид, "
                "переменные и обратную формулу." % (len(FORMULAS), themes)), \
            False
    lines = ["Формула «%s»: %s." % (f["name"], f["text"])]
    if f.get("doc"):
        lines.append("Смысл: %s." % f["doc"])
    if f["vars"]:
        lines.append("Переменные: " + ", ".join(
            "%s — %s" % (v["name"], v["label"]) for v in f["vars"]) + ".")
    if f.get("inv"):
        lines.append("Обратная формула: %s." % f["inv"])
    # числа в вопросе? — подставим и посчитаем сразу
    try:
        values, missing = formula_bind(f, text)
        if not missing and f["id"] != "average":
            val = safe_eval(f["expr"], values)
            subs = ", ".join("%s=%s" % (v["name"], _num(values[v["name"]]))
                             for v in f["vars"])
            lines.append("Подставила твои числа (%s) -> %s = %s." %
                         (subs, f["text"], _num(val)))
        elif missing and _NUM_RE.search(text or ""):
            labels = ", ".join(v["label"] for v in f["vars"]
                               if v["name"] in missing)
            lines.append("Числа не все названы: нужны %s — добавь, и "
                         "посчитаю." % labels)
    except FormulaError:
        pass
    if f["vars"]:
        lines.append("Скажи «%s 5» — посчитаю по формуле."
                     % f["vars"][0]["label"])
    lines.append(_tex_block(f["text"], f["text"]).strip())
    return "\n".join(x for x in lines if x), True


def formula_reply(text):
    """Маршрут зоны «Математика» для формул: «составь/напиши/выведи
    формулу …» — СОСТАВЛЕНИЕ (карточка); иначе — вычисление по числам;
    v3.18: иначе — ПРОСТОЕ ВЫРАЖЕНИЕ («корень из 144», «5 в степени 3»);
    иначе — если в вопросе есть сама формула (E = mc^2) — честная
    карточка формулы без вычислений."""
    low = str(text or "").lower()
    if _COMPOSE_VERB.search(low) and not _EXPR_W.search(low):
        out, ok = compose_reply(text)
        if ok:
            return out, ok
    out, ok = formula_solve(text)
    if ok:
        return out, ok
    if out.startswith("Формула нашлась"):
        # v3.18: формула из базы найдена (не хватает чисел) — это финал,
        # а не повод уходить в общую карточку/выражения
        return out, ok
    out2, ok2 = expr_reply(text)
    if ok2:
        return out2, ok2
    out3, ok3 = card_reply(text)
    if ok3:
        return out3, ok3
    return out, ok


# ============= ВЫЧИСЛЕНИЕ ПРОСТЫХ ВЫРАЖЕНИЙ (v3.18) =========================
# «латекс движок не работает» часто значил: «спросила корень из 144 —
# а мозг ушёл в интернет и вернул сырую выжимку». Теперь такие вопросы
# СЧИТАЕТ зона «Математика», и формула рисуется LaTeX-движком.
_EXPR_W = re.compile(
    r"корень\s+из|квадратн\w*\s+корень|в\s+квадрате|в\s+кубе|в\s+степени|"
    r"факториал|модуль\s+(?:из\s+)?-?\d|\babs\s*\(" , re.I)
_CARD_W = re.compile(r"[a-zа-яё0-9)\]]\s*=\s*[a-zа-яё0-9(\[\.]", re.I)


def expr_reply(text):
    """«корень из 144», «5 в квадрате», «2 в степени 10», «факториал 6»,
    «модуль -7» — считается safe_eval'ом и рисуется карточкой."""
    low = str(text or "").lower().replace("ё", "е").replace(",", ".")
    num = r"(-?\d+(?:\.\d+)?)"
    m = re.search(r"(?:квадратн\w*\s+)?корень\s+из\s*" + num, low)
    if m:
        x = m.group(1)
        try:
            val = safe_eval("sqrt(%s)" % x)
            if isinstance(val, complex):
                raise FormulaError("корень из отрицательного")
            return ("Корень из %s = %s%s" % (
                _num(float(x)), _num(val), _tex_block("\\sqrt{%s}" % x,
                                                       "√%s" % x))), True
        except FormulaError as e:
            return "Не посчитала корень: %s." % e, False
    m = re.search(num + r"\s*(?:в\s+квадрате|\^\s*2|\*\*\s*2)", low)
    if m:
        x = m.group(1)
        val = safe_eval("%s ** 2" % x)
        return ("%s в квадрате = %s%s" % (
            _num(float(x)), _num(val), _tex_block("%s^{2}" % x))), True
    m = re.search(num + r"\s*(?:в\s+кубе|\^\s*3|\*\*\s*3)", low)
    if m:
        x = m.group(1)
        val = safe_eval("%s ** 3" % x)
        return ("%s в кубе = %s%s" % (
            _num(float(x)), _num(val), _tex_block("%s^{3}" % x))), True
    m = re.search(num + r"\s*в\s+степени\s*" + num, low)
    if m:
        x, k = m.group(1), m.group(2)
        try:
            val = safe_eval("%s ** %s" % (x, k))
        except FormulaError as e:
            return "Не посчитала степень: %s." % e, False
        return ("%s в степени %s = %s%s" % (
            _num(float(x)), _num(float(k)), _num(val),
            _tex_block("%s^{%s}" % (x, k)))), True
    m = re.search(r"факториал\s*" + r"(\d+)", low)
    if m:
        n = int(m.group(1))
        if n > 500:
            return "Факториал %d не считаю: слишком большое число." % n, \
                False
        val = 1
        for i in range(2, n + 1):
            val *= i
        sval = str(val)
        if len(sval) > 40:
            sval = sval[:37] + "…(%d знаков)" % len(str(val))
        return ("%d! = %s%s" % (
            n, sval, _tex_block("%s!" % n))), True
    m = re.search(r"модуль\s+(?:из\s+)?" + num, low)
    if m:
        x = float(m.group(1))
        val = safe_eval("abs(%s)" % x)
        return ("Модуль %s = %s%s" % (
            _num(x), _num(val), _tex_block("\\left|%s\\right|" % x))), True
    return "", False


def card_reply(text):
    r"""«Вот твоя формула, нарисованная движком» — для вопросов, где сама
    формула уже написана (E = mc^2, y = 2*x + 1, S = π·r²), но тема не из
    базы. ЧЕСТНО: мозг не утверждает, что знает физику — он визуализирует
    то, что написано, и не вычисляет без чисел."""
    t = str(text or "").strip().strip("?!. ")
    if not _CARD_W.search(t) or \
            not re.search(r"\^|√|sqrt|π|pi|\*|/|:", t):
        return "", False
    if len(t) > 80 or "\n" in t:
        return "", False
    # правая часть после знака равенства (или вся строка, если «=» нет)
    expr = re.sub(r"^[^=]*=\s*", "", t) if "=" in t else t
    expr = re.sub(r"^(что|какая|как|такая)\s+", "", expr, flags=re.I)
    expr = expr.strip(" ?!")
    if not expr or not re.search(r"[0-9a-zа-яё]", expr, re.I):
        return "", False
    card = _tex_block(expr, expr[:40])
    if not card:
        return "", False
    return ("Формула «%s» — вот она, нарисовала своим LaTeX-движком. "
            "Назови числа (например: «%s при x=2»), и посчитаю по ней."
            % (t[:60], "x" if "x" in expr.lower() else "значения")) + card, \
        True


# ============================ РАСПОЗНАНИЕ ===================================
_MODEL_W = ("модель", "прогноз", "тренд", "регресс", "аппроксим")
_FORMULA_W = ("формул", "вычисли по", "посчитай по", "площад", "перимет",
              "гипотенуз", "процент", "скидк", "скорост", "среднее",
              "дискриминан", "корн уравнен", "закон ома", "ом",
              "фаренгейт", "цельси", "вклад", "окружност", "объем",
              # v3.18: новые темы базы формул
              "кпд", "энергия", "ускорен", "мощност", "теплот", "кулон",
              "фотон", "волны", "шар", "цилиндр", "конус", "диаметр",
              "индекс массы", "имт")


def detect(text):
    """Классификация вопроса: 'model' | 'formula' | None. Строгая, чтобы
    не отхватывать чужие вопросы: модели нужны данные (числа или «по
    данным»), формулам — тема и числа ЛИБО явное «составь/напиши
    формулу …» (v3.10: составление работает и без чисел); v3.18: простые
    выражения («корень из 144», «5 в квадрате») — тоже «formula»."""
    low = (text or "").lower()
    nums = _NUM_RE.findall(low)
    if any(w in low for w in _MODEL_W):
        # «что такое модель» без данных — не наша зона
        if len(nums) >= 3 or re.search(r"по\s+данным|данные\s*[:\s]*\d", low):
            return "model"
        return None
    # v3.18: «корень из 144», «7 в квадрате», «факториал 6» — наша зона
    # (раньше уходили в интернет с сырыми {\displaystyle} выжимками)
    if _EXPR_W.search(low):
        return "formula"
    # v3.10: явная речь о формуле — «составь формулу площади круга»,
    # «какая формула у скорости» — наша зона даже БЕЗ чисел
    if re.search(r"формул|уравнен", low):
        if re.match(r"^\s*что\s+так(ое|ая)\s+формул", low):
            return None          # «что такое формула» — вопрос о понятии
        if _COMPOSE_VERB.search(low) or formula_pick(low)[1] >= 1:
            return "formula"
    if any(w in low for w in _FORMULA_W) and nums:
        # «посчитай 2+2» — арифметика, не формула: отдаём навыку
        if re.search(r"посчитай\s+[\d\s\-+*/().,^]+$", low):
            return None
        return "formula"
    # v3.18: в вопросе уже написана формула (E = mc^2) — визуализируем
    if _CARD_W.search(low) and re.search(r"\^|√|sqrt|π", low) and \
            len(low) < 80:
        return "formula"
    return None
