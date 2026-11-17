# -*- coding: utf-8 -*-
# ===========================================================================
# autocode.py — МОЗГ САМ ПИШЕТ СЕБЕ ФУНКЦИИ (v3.18, только stdlib)
# ---------------------------------------------------------------------------
# Заявка владельца: «добавь нейросети возможность самой для себя писать
# функции в апп лаб».
#
# Как это работает (без LLM, честно):
#   1) ЗАГОТОВКИ (TEMPLATES): библиотека шаблонов функций run(text, ctx) —
#      конвертеры, серии, текстовые утилиты, даты, статистика. Шаблон —
#      НЕ автоответ: это настоящий код, который мозг пишет в реестр.
#   2) write_function(тема): мозг подбирает заготовку по теме вопроса,
#      проверяет код статическим аудитом песочницы, прогоняет ТЕСТЫ
#      (вход -> ожидаемый кусок ответа) в отдельном процессе и только
#      если все прошли — публикует функцию в реестр (origin «мозг»).
#      После публикации функция живёт в чате (зона «Код»), в реестре
#      «Функции мозга» и вызывается из App Lab кирпичиком brain.func.
#   3) ЧЕСТНОСТЬ: тест не прошёл / аудит не прошёл — функция НЕ
#      публикуется, мозг честно говорит о провале. Перезапись чужих
#      функций запрещена: своё — только под идами auto_*.
# ===========================================================================
import re

import sandbox
import skills

# ============================ ЗАГОТОВКИ ФУНКЦИЙ ==============================
# Каждый шаблон: id, name (для человека), keys (темы), desc, code (исполнимый
# в песочнице: def run(text, ctx=None) -> str), tests [(вход, кусок ответа)].
_T = []


def _t(tid, name, keys, desc, code, tests):
    _T.append({"id": "auto_" + tid, "name": name, "keys": keys, "desc": desc,
               "code": code, "tests": tests})


_t("km_miles", "Конвертер: километры и мили",
   ("км", "миль", "мили", "миля", "километр"),
   "переводит километры в мили и обратно",
   ("def run(text, ctx=None):\n"
    "    import re\n"
    "    s = str(text or '').lower().replace(',', '.')\n"
    "    m = re.search(r'(-?\\d+(?:\\.\\d+)?)', s)\n"
    "    if not m:\n"
    "        return 'Назови число: «переведи 5 км в мили».'\n"
    "    x = float(m.group(1))\n"
    "    t = re.search(r'в\\s+(милях|мили|миль|милю|км|километр\\w*)', s)\n"
    "    target = t.group(1) if t else ''\n"
    "    if target.startswith('мил'):\n"
    "        return '%g км = %.3f мили' % (x, x * 0.621371)\n"
    "    if target.startswith('км') or target.startswith('километр'):\n"
    "        return '%g миль = %.3f км' % (x, x / 0.621371)\n"
    "    if 'мил' in s and 'км' not in s:\n"
    "        return '%g миль = %.3f км' % (x, x / 0.621371)\n"
    "    return '%g км = %.3f мили' % (x, x * 0.621371)\n"),
   [("переведи 5 км в мили", "3.1"), ("10 км в мили", "6.2"),
    ("3 мили в км", "4.8"), ("3 мили", "4.8")])

_t("kg_lb", "Конвертер: килограммы и фунты",
   ("кг", "фунт", "килограмм"),
   "переводит килограммы в фунты и обратно",
   ("def run(text, ctx=None):\n"
    "    import re\n"
    "    s = str(text or '').lower().replace(',', '.')\n"
    "    m = re.search(r'(-?\\d+(?:\\.\\d+)?)', s)\n"
    "    if not m:\n"
    "        return 'Назови число: «переведи 5 кг в фунты».'\n"
    "    x = float(m.group(1))\n"
    "    t = re.search(r'в\\s+(фунт\\w*|кг|килограмм\\w*)', s)\n"
    "    target = t.group(1) if t else ''\n"
    "    if target.startswith('фунт'):\n"
    "        return '%g кг = %.3f фунта' % (x, x * 2.20462)\n"
    "    if target.startswith('кг') or target.startswith('килограмм'):\n"
    "        return '%g фунтов = %.3f кг' % (x, x / 2.20462)\n"
    "    if 'фунт' in s and 'кг' not in s:\n"
    "        return '%g фунтов = %.3f кг' % (x, x / 2.20462)\n"
    "    return '%g кг = %.3f фунта' % (x, x * 2.20462)\n"),
   [("переведи 5 кг в фунты", "11.0"), ("10 фунтов в кг", "4.5"),
    ("5 кг", "11.0")])

_t("celsius_kelvin", "Конвертер: Цельсий и Кельвин",
   ("кельвин", "кельвина", "кельвинов"),
   "переводит градусы Цельсия в Кельвины",
   ("def run(text, ctx=None):\n"
    "    import re\n"
    "    s = str(text or '').lower().replace(',', '.')\n"
    "    m = re.search(r'(-?\\d+(?:\\.\\d+)?)\\s*(кельвин\\w*)?', s)\n"
    "    if not m:\n"
    "        return 'Назови градусы: «25 градусов в Кельвины».'\n"
    "    x = float(m.group(1))\n"
    "    src_kelvin = bool(m.group(2))\n"
    "    to_kelvin = bool(re.search(r'в\\s+кельвин', s))\n"
    "    if to_kelvin and not src_kelvin:\n"
    "        return '%g °C = %.2f K' % (x, x + 273.15)\n"
    "    if src_kelvin:\n"
    "        return '%g K = %.2f °C' % (x, x - 273.15)\n"
    "    return '%g °C = %.2f K' % (x, x + 273.15)\n"),
   [("25 градусов в кельвины", "298.15"), ("300 кельвинов в цельсий", "26.8"),
    ("300 кельвинов", "26.8")])

_t("fibonacci", "Числа Фибоначчи",
   ("фибоначчи", "фибонач", "золотое сечение"),
   "первые N чисел Фибоначчи",
   ("def run(text, ctx=None):\n"
    "    import re\n"
    "    m = re.search(r'(\\d+)', str(text or ''))\n"
    "    n = int(m.group(1)) if m else 10\n"
    "    if n < 1 or n > 60:\n"
    "        return 'Дай N от 1 до 60.'\n"
    "    a, b, out = 0, 1, []\n"
    "    for _ in range(n):\n"
    "        out.append(str(a))\n"
    "        a, b = b, a + b\n"
    "    return 'Первые %d чисел Фибоначчи: %s.' % (n, ', '.join(out))\n"),
   [("фибоначчи 7", "0, 1, 1, 2, 3, 5, 8"), ("фибоначчи 10", "34")])

_t("primes", "Простые числа",
   ("простые числа", "простое ли", "простое число"),
   "список простых чисел до N; проверка числа",
   ("def run(text, ctx=None):\n"
    "    import re\n"
    "    s = str(text or '').lower()\n"
    "    ns = [int(x) for x in re.findall(r'\\d+', s)]\n"
    "    if not ns:\n"
    "        return 'Назови число: «простые числа до 30» или «простое ли 17».'\n"
    "    def isp(k):\n"
    "        if k < 2:\n"
    "            return False\n"
    "        i = 2\n"
    "        while i * i <= k:\n"
    "            if k % i == 0:\n"
    "                return False\n"
    "            i += 1\n"
    "        return True\n"
    "    if 'до' in s or len(ns) > 1:\n"
    "        n = min(ns[-1], 500)\n"
    "        out = [str(x) for x in range(2, n + 1) if isp(x)]\n"
    "        return 'Простые числа до %d: %s.' % (n, ' '.join(out))\n"
    "    k = ns[0]\n"
    "    return '%d — %sчисло' % (k, 'простое ' if isp(k) else 'НЕ простое ')\n"),
   [("простые числа до 30", "2 3 5 7 11"), ("простое ли 17", "простое"),
    ("простое ли 18", "НЕ простое")])

_t("binary", "Двоичная система",
   ("двоичн", "бин", "binary"),
   "число в двоичную и обратно",
   ("def run(text, ctx=None):\n"
    "    import re\n"
    "    s = str(text or '').lower()\n"
    "    if 'из двоич' in s or re.search(r'\\b[01]{2,}\\b', s):\n"
    "        m = re.search(r'\\b[01]{2,}\\b', s)\n"
    "        if m and 'из' in s or (m and 'двоич' in s and 'в' not in s):\n"
    "            v = int(m.group(0), 2)\n"
    "            return '%s (2) = %d (10)' % (m.group(0), v)\n"
    "    m = re.search(r'(\\d+)', s)\n"
    "    if not m:\n"
    "        return 'Назови число: «255 в двоичную» или «1011 из двоичной».'\n"
    "    n = int(m.group(1))\n"
    "    if n < 0 or n > 2 ** 32:\n"
    "        return 'Дай число от 0 до 2^32.'\n"
    "    return '%d (10) = %s (2)' % (n, format(n, 'b'))\n"),
   [("255 в двоичную", "11111111"), ("1011 из двоичной", "= 11 (10)")])

_t("weekday", "День недели по дате",
   ("день недели", "какой день"),
   "день недели для даты ГГГГ-ММ-ДД",
   ("def run(text, ctx=None):\n"
    "    import re, calendar\n"
    "    s = str(text or '')\n"
    "    m = re.search(r'(\\d{4})-(\\d{1,2})-(\\d{1,2})', s)\n"
    "    if not m:\n"
    "        return 'Дай дату ГГГГ-ММ-ДД: «день недели 2026-01-01».'\n"
    "    y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))\n"
    "    names = ['понедельник', 'вторник', 'среда', 'четверг', 'пятница',\n"
    "             'суббота', 'воскресенье']\n"
    "    try:\n"
    "        w = calendar.weekday(y, mo, d)\n"
    "    except Exception:\n"
    "        return 'Такой даты не существует — проверь месяц и день.'\n"
    "    return '%04d-%02d-%02d — %s.' % (y, mo, d, names[w])\n"),
   [("день недели 2026-01-01", "четверг"), ("день недели 2024-02-29", "четверг")])

_t("age_year", "Возраст по году рождения",
   ("возраст", "сколько лет", "год рождения"),
   "сколько лет человеку, рождённому в год N",
   ("def run(text, ctx=None):\n"
    "    import re, time\n"
    "    ns = re.findall(r'\\d{4}', str(text or ''))\n"
    "    if not ns:\n"
    "        return 'Назови год рождения: «возраст 1990».'\n"
    "    y = int(ns[0])\n"
    "    now = time.localtime().tm_year\n"
    "    if y < 1900 or y > now:\n"
    "        return 'Год %d не годится: нужно от 1900 до %d.' % (y, now)\n"
    "    return ('Если человек родился в %d году, ему примерно %d лет '\n"
    "            '(полный возраст зависит от дня рождения).') % (y, now - y)\n"),
   [("возраст 1990", "лет")])

_t("dice", "Кубик и случайные числа",
   ("кубик", "случайное число", "рандом", "подбрось"),
   "случайное число в диапазоне",
   ("def run(text, ctx=None):\n"
    "    import re, random\n"
    "    s = str(text or '').lower()\n"
    "    ns = [int(x) for x in re.findall(r'\\d+', s)]\n"
    "    if 'кубик' in s:\n"
    "        return 'Бросила кубик: выпало %d.' % random.randint(1, 6)\n"
    "    if len(ns) >= 2:\n"
    "        a, b = min(ns[0], ns[1]), max(ns[0], ns[1])\n"
    "        return 'Случайное число от %d до %d: %d.' % (\n"
    "            a, b, random.randint(a, b))\n"
    "    return 'Случайное число от 1 до 100: %d.' % random.randint(1, 100)\n"),
   [("кубик", "кубик"), ("случайное от 1 до 10", "от 1 до 10")])

_t("stats_nums", "Статистика списка чисел",
   ("статистик", "среднее медиан", "мин макс"),
   "среднее, медиана, мин и макс чисел",
   ("def run(text, ctx=None):\n"
    "    import re, statistics\n"
    "    ns = [float(x.replace(',', '.')) for x in\n"
    "          re.findall(r'-?\\d+(?:[.,]\\d+)?', str(text or ''))]\n"
    "    if len(ns) < 2:\n"
    "        return 'Перечисли хотя бы два числа через запятую.'\n"
    "    out = ['среднее %.4g' % statistics.mean(ns),\n"
    "           'медиана %.4g' % statistics.median(ns),\n"
    "           'мин %g' % min(ns), 'макс %g' % max(ns)]\n"
    "    return 'Статистика %d чисел: %s.' % (len(ns), ', '.join(out))\n"),
   [("статистика 2, 4, 6, 8", "среднее 5"), ("статистика 1 3 5", "медиана 3")])

_t("word_count", "Счётчик слов и знаков",
   ("сколько слов", "счёт слов", "сколько знаков", "сколько символов"),
   "считает слова и знаки в тексте",
   ("def run(text, ctx=None):\n"
    "    import re\n"
    "    s = re.sub(r'^(сколько слов|сколько знаков|сколько символов)\\s*'\n"
    "               r'(в|во)?\\s*', '', str(text or ''), flags=re.I)\n"
    "    words = re.findall(r'[а-яёa-z0-9]+', s, re.I)\n"
    "    return 'Слов: %d, знаков: %d (без пробелов: %d).' % (\n"
    "        len(words), len(s.strip()), len(''.join(s.split())))\n"),
   [("сколько слов в привет мир", "Слов: 2")])

_t("palindrome", "Проверка палиндрома",
   ("палиндром", "перевёртыш"),
   "читается ли слово/фраза одинаково в обе стороны",
   ("def run(text, ctx=None):\n"
    "    import re\n"
    "    s = re.sub(r'[^а-яёa-z0-9]', '', str(text or '').lower())\n"
    "    if not s:\n"
    "        return 'Дай слово: «палиндром шалаш».'\n"
    "    ok = s == s[::-1]\n"
    "    return '«%s» %s палиндром.' % (\n"
    "        s, '—' if ok else '— НЕ')\n"),
   [("палиндром шалаш", "палиндром"), ("палиндром дом", "НЕ")])

_t("gcd_lcm", "НОД и НОК",
   ("нод", "нок", "наибольший общий"),
   "наибольший общий делитель и наименьшее общее кратное",
   ("def run(text, ctx=None):\n"
    "    import re\n"
    "    ns = [int(x) for x in re.findall(r'\\d+', str(text or ''))]\n"
    "    if len(ns) < 2:\n"
    "        return 'Назови два числа: «нод 12 18» или «нок 4 6».'\n"
    "    a, b = ns[0], ns[1]\n"
    "    x, y = a, b\n"
    "    while y:\n"
    "        x, y = y, x % y\n"
    "    g = x\n"
    "    l = a * b // g if g else 0\n"
    "    if 'нок' in str(text or '').lower() and 'нод' not in str(text or '').lower():\n"
    "        return 'НОК(%d, %d) = %d.' % (a, b, l)\n"
    "    return 'НОД(%d, %d) = %d; НОК(%d, %d) = %d.' % (a, b, g, a, b, l)\n"),
   [("нод 12 18", "НОД(12, 18) = 6"), ("нок 4 6", "= 12")])

TEMPLATES = _T

_STEM_END = re.compile(
    r"(?:иями|ями|ами|ов|ев|ей|ах|ях|ам|ям|ой|ый|ий|ая|яя|ое|ее|ые|ие|"
    r"ью|ом|ем|у|ю|а|я|о|е|ы|и|ь)$")


def _stem(w):
    w = str(w or "").lower().replace("ё", "е")
    for _ in range(2):
        if len(w) < 4:
            break
        w2 = _STEM_END.sub("", w)
        if w2 == w or len(w2) < 3:
            break
        w = w2
    return w


def templates_list():
    """Каталог заготовок (для панели/чата — что мозг умеет писать сам)."""
    return [{"id": t["id"], "name": t["name"], "desc": t["desc"]}
            for t in TEMPLATES]


def template_pick(topic):
    """Выбрать заготовку под тему (по основам слов ключей)."""
    bag = set(_stem(w) for w in re.findall(r"[а-яёa-z0-9]+",
                                           str(topic or "").lower()))
    best, hits = None, 0
    for t in TEMPLATES:
        n = 0
        for k in t["keys"]:
            if _stem(k) in bag:
                n += 1
        if n > hits:
            best, hits = t, n
    return best if hits else None


def write_function(topic):
    """НАПИСАТЬ СЕБЕ ФУНКЦИЮ по теме: подбор заготовки -> аудит -> ТЕСТЫ
    в песочнице -> публикация в реестр (origin «мозг»).
    Возврат: (текст-отчёт, ok|False)."""
    topic = re.sub(r"\s+", " ", str(topic or "")).strip()[:160]
    t = template_pick(topic)
    if not t:
        names = "; ".join(x["name"] for x in TEMPLATES)
        return ("Пока не научилась писать такую функцию. Заготовки, которые "
                "я могу написать себе сама: %s. Скажи, например: «напиши "
                "себе функцию конвертер км в мили»." % names), False
    # 1) аудит песочницы
    err = sandbox.audit(t["code"])
    if err:
        return ("Написала функцию «%s», но аудит песочницы честно ругнулся: "
                "%s. Публиковать не стану." % (t["name"], err)), False
    # 2) тесты (в отдельных процессах)
    passed, total = 0, len(t["tests"])
    fails = []
    for inp, want in t["tests"]:
        ok, out = sandbox.run_code(t["code"], inp, timeout=10)
        if ok and str(want).lower() in str(out).lower():
            passed += 1
        else:
            fails.append("«%s» -> %s" % (inp, (out or "тишина")[:60]))
    if passed < total:
        return ("Функция «%s» написана, но тесты её не приняли (%d из %d): "
                "%s. Публиковать неработающее не стану."
                % (t["name"], passed, total, "; ".join(fails[:2]))), False
    # 3) публикация (перезаписывать чужое нельзя)
    r = skills.upsert({"id": t["id"], "name": t["name"], "kind": "code",
                       "code": t["code"],
                       "keywords": [k[:24] for k in t["keys"]],
                       "desc": t["desc"], "origin": "мозг"})
    if not r.get("ok"):
        return ("Реестр не принял функцию «%s»: %s" %
                (t["name"], r.get("error") or "?")), False
    return ("Написала себе приложение App Lab «%s» (%s). Проверила в "
            "песочнице: тестов %d — все прошли. Сохранено в App Lab на Python "
            "(id %s, автор — мозг): теперь оно отвечает в чате само, его "
            "видно в App Lab — там можно открыть и поправить его main.py."
            % (t["name"], t["desc"], total, t["id"])), True


def stats():
    """Для панели: сколько заготовок умеет писать, сколько написано."""
    reg = skills._load()
    mine = [sid for sid, sk in reg.items()
            if str(sk.get("origin") or "") == "мозг"
            or sid.startswith("auto_")]
    return {"templates": len(TEMPLATES), "written": len(mine),
            "ids": mine[:20]}
