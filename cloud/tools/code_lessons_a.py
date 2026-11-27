# -*- coding: utf-8 -*-
"""Программные уроки, часть A: каждый = задача -> шаблон -> проверка (assert)."""
A = []


def L(id, title, ask, code, test, tags=""):
    A.append({"id": id, "title": title, "ask": ask, "code": code.strip("\n") + "\n", "test": test.strip("\n") + "\n", "tags": tags})


# ---- строки
L("str.reverse", "Перевернуть строку", ["переверни строку", "строка наоборот", "reverse string"],
  "def reverse_text(s):\n    return s[::-1]", "assert reverse_text('abc') == 'cba'\nassert reverse_text('') == ''", "строки")
L("str.palindrome", "Проверить палиндром", ["проверь палиндром", "является ли слово палиндромом", "палиндром"],
  "def is_palindrome(s):\n    t = ''.join(c.lower() for c in s if c.isalnum())\n    return t == t[::-1]",
  "assert is_palindrome('А роза упала на лапу Азора')\nassert not is_palindrome('мозг')", "строки")
L("str.count_words", "Посчитать слова", ["посчитай слова", "сколько слов в тексте", "количество слов"],
  "def count_words(text):\n    return len(text.split())", "assert count_words('раз два  три') == 3\nassert count_words('') == 0", "строки")
L("str.word_freq", "Частота слов", ["частота слов", "самые частые слова", "посчитай повторы слов"],
  "import re\nfrom collections import Counter\n\n\ndef word_freq(text, top=5):\n    words = re.findall(r'\\w+', text.lower())\n    return Counter(words).most_common(top)",
  "r = word_freq('а б а в а б', 2)\nassert r == [('а', 3), ('б', 2)]", "строки")
L("str.title", "Каждое слово с заглавной", ["сделай заглавные буквы", "capitalize слова", "каждое слово с большой буквы"],
  "def title_words(s):\n    return ' '.join(w[:1].upper() + w[1:] for w in s.split())", "assert title_words('мозг двенадцать') == 'Мозг Двенадцать'", "строки")
L("str.vowels", "Посчитать гласные", ["посчитай гласные", "сколько гласных букв"],
  "def count_vowels(s):\n    return sum(1 for c in s.lower() if c in 'аеёиоуыэюяaeiou')", "assert count_vowels('Мозг') == 1\nassert count_vowels('hello') == 2", "строки")
L("str.slugify", "Сделать слаг из заголовка", ["сделай slug", "преобразуй заголовок в ссылку", "slugify"],
  "import re\n\n_TR = dict(zip('абвгдеёжзийклмнопрстуфхцчшщъыьэюя', ['a','b','v','g','d','e','e','zh','z','i','y','k','l','m','n','o','p','r','s','t','u','f','h','c','ch','sh','sch','','y','','e','yu','ya']))\n\n\ndef slugify(s):\n    s = ''.join(_TR.get(c, c) for c in s.lower())\n    return re.sub(r'[^a-z0-9]+', '-', s).strip('-')",
  "assert slugify('Star Gate: привет!') == 'mozg-12-privet'", "строки")
L("str.truncate", "Обрезать строку с многоточием", ["обрежь текст", "укороти строку", "truncate"],
  "def truncate(s, n=40):\n    return s if len(s) <= n else s[:max(0, n - 1)].rstrip() + '…'", "assert truncate('abc', 5) == 'abc'\nassert truncate('abcdefghij', 5) == 'abcd…'", "строки")
L("str.is_number", "Проверить, что строка — число", ["это число", "проверь число в строке", "is number"],
  "def is_number(s):\n    try:\n        float(str(s).replace(',', '.'))\n        return True\n    except ValueError:\n        return False", "assert is_number('3,14')\nassert not is_number('abc')", "строки")
L("str.caesar", "Шифр Цезаря", ["шифр цезаря", "зашифруй сдвигом", "caesar"],
  "def caesar(s, k):\n    out = []\n    for c in s:\n        if 'a' <= c <= 'z':\n            out.append(chr((ord(c) - 97 + k) % 26 + 97))\n        elif 'A' <= c <= 'Z':\n            out.append(chr((ord(c) - 65 + k) % 26 + 65))\n        else:\n            out.append(c)\n    return ''.join(out)", "assert caesar('abc', 3) == 'def'\nassert caesar(caesar('Hello', 5), -5) == 'Hello'", "строки")
# ---- числа и математика
L("num.is_prime", "Простое ли число", ["простое число", "проверь на простоту", "is prime"],
  "def is_prime(n):\n    if n < 2:\n        return False\n    i = 2\n    while i * i <= n:\n        if n % i == 0:\n            return False\n        i += 1\n    return True", "assert [n for n in range(20) if is_prime(n)] == [2, 3, 5, 7, 11, 13, 17, 19]", "числа")
L("num.primes", "Простые числа до N (решето)", ["все простые до n", "решето эратосфена", "список простых чисел"],
  "def primes_upto(n):\n    if n < 2:\n        return []\n    s = [True] * (n + 1)\n    s[0] = s[1] = False\n    for i in range(2, int(n ** 0.5) + 1):\n        if s[i]:\n            for j in range(i * i, n + 1, i):\n                s[j] = False\n    return [i for i, v in enumerate(s) if v]", "assert primes_upto(10) == [2, 3, 5, 7]", "числа")
L("num.fib", "Числа Фибоначчи", ["фибоначчи", "числа фибоначчи", "fibonacci"],
  "def fib(n):\n    a, b = 0, 1\n    out = []\n    for _ in range(n):\n        out.append(a)\n        a, b = b, a + b\n    return out", "assert fib(8) == [0, 1, 1, 2, 3, 5, 8, 13]", "числа")
L("num.factorial", "Факториал", ["факториал", "вычисли факториал", "factorial"],
  "def factorial(n):\n    if n < 0:\n        raise ValueError('n < 0')\n    r = 1\n    for i in range(2, n + 1):\n        r *= i\n    return r", "assert factorial(5) == 120\nassert factorial(0) == 1", "числа")
L("num.gcd", "НОД и НОК", ["найди нод", "наибольший общий делитель", "нок"],
  "def gcd(a, b):\n    while b:\n        a, b = b, a % b\n    return abs(a)\n\n\ndef lcm(a, b):\n    return abs(a * b) // gcd(a, b) if a and b else 0", "assert gcd(12, 18) == 6\nassert lcm(4, 6) == 12", "числа")
L("num.digits_sum", "Сумма цифр числа", ["сумма цифр", "посчитай сумму цифр"],
  "def digits_sum(n):\n    return sum(int(c) for c in str(abs(n)))", "assert digits_sum(1234) == 10", "числа")
L("num.clamp", "Ограничить число диапазоном", ["ограничь значение", "clamp", "обрежь число по границам"],
  "def clamp(x, lo, hi):\n    return max(lo, min(hi, x))", "assert clamp(5, 0, 3) == 3\nassert clamp(-1, 0, 3) == 0\nassert clamp(2, 0, 3) == 2", "числа")
L("num.map_range", "Перевести значение из одного диапазона в другой (map)", ["перемасштабируй значение", "map диапазон", "аналог arduino map"],
  "def map_range(x, in_lo, in_hi, out_lo, out_hi):\n    if in_hi == in_lo:\n        return out_lo\n    return (x - in_lo) * (out_hi - out_lo) / (in_hi - in_lo) + out_lo", "assert map_range(512, 0, 1023, 0, 255) == 127.75 or abs(map_range(512, 0, 1023, 0, 255) - 127.6) < 0.2", "числа")
L("num.mean_median", "Среднее, медиана, разброс", ["среднее значение", "медиана", "статистика списка"],
  "def stats(xs):\n    xs = sorted(xs)\n    n = len(xs)\n    if not n:\n        return {'mean': 0.0, 'median': 0.0, 'min': 0, 'max': 0}\n    mean = sum(xs) / n\n    med = xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2\n    return {'mean': mean, 'median': med, 'min': xs[0], 'max': xs[-1]}", "r = stats([3, 1, 2, 10])\nassert r['median'] == 2.5 and r['mean'] == 4.0 and r['max'] == 10", "числа")
L("num.roman", "Римские цифры", ["римские цифры", "число в римское", "roman"],
  "def to_roman(n):\n    t = [(1000,'M'),(900,'CM'),(500,'D'),(400,'CD'),(100,'C'),(90,'XC'),(50,'L'),(40,'XL'),(10,'X'),(9,'IX'),(5,'V'),(4,'IV'),(1,'I')]\n    out = ''\n    for v, s in t:\n        while n >= v:\n            out += s\n            n -= v\n    return out", "assert to_roman(1994) == 'MCMXCIV'", "числа")
L("num.base", "Перевод между системами счисления", ["двоичное число", "перевод в двоичную", "системы счисления"],
  "def to_base(n, b):\n    if n == 0:\n        return '0'\n    d = '0123456789abcdefghijklmnopqrstuvwxyz'\n    s = ''\n    m = abs(n)\n    while m:\n        s = d[m % b] + s\n        m //= b\n    return ('-' if n < 0 else '') + s", "assert to_base(10, 2) == '1010'\nassert to_base(255, 16) == 'ff'", "числа")
L("num.temp", "Температура: Цельсий, Кельвин, Фаренгейт", ["перевод температуры", "цельсий в фаренгейт"],
  "def c_to_f(c):\n    return c * 9 / 5 + 32\n\n\ndef c_to_k(c):\n    return c + 273.15", "assert c_to_f(100) == 212\nassert abs(c_to_k(0) - 273.15) < 1e-9", "числа")
# ---- списки
L("list.unique", "Убрать повторы, сохранив порядок", ["убери дубликаты", "уникальные элементы", "unique list"],
  "def unique(xs):\n    seen = set()\n    out = []\n    for x in xs:\n        if x not in seen:\n            seen.add(x)\n            out.append(x)\n    return out", "assert unique([3, 1, 3, 2, 1]) == [3, 1, 2]", "списки")
L("list.flatten", "Разгладить вложенные списки", ["разверни вложенный список", "flatten", "плоский список"],
  "def flatten(xs):\n    out = []\n    for x in xs:\n        if isinstance(x, (list, tuple)):\n            out.extend(flatten(x))\n        else:\n            out.append(x)\n    return out", "assert flatten([1, [2, [3, 4]], 5]) == [1, 2, 3, 4, 5]", "списки")
L("list.chunks", "Разбить список на части", ["разбей список на куски", "chunks", "пачки по n"],
  "def chunks(xs, n):\n    return [xs[i:i + n] for i in range(0, len(xs), n)]", "assert chunks([1, 2, 3, 4, 5], 2) == [[1, 2], [3, 4], [5]]", "списки")
L("list.sort_by", "Сортировка по ключу", ["отсортируй список", "сортировка по полю", "sort by key"],
  "def sort_by(items, key, reverse=False):\n    return sorted(items, key=lambda x: x[key], reverse=reverse)", "r = sort_by([{'a': 2}, {'a': 1}], 'a')\nassert [x['a'] for x in r] == [1, 2]", "списки")
L("list.bubble", "Сортировка пузырьком", ["пузырьковая сортировка", "bubble sort"],
  "def bubble_sort(xs):\n    a = list(xs)\n    for i in range(len(a)):\n        for j in range(len(a) - 1 - i):\n            if a[j] > a[j + 1]:\n                a[j], a[j + 1] = a[j + 1], a[j]\n    return a", "assert bubble_sort([3, 1, 2]) == [1, 2, 3]", "списки")
L("list.quick", "Быстрая сортировка", ["быстрая сортировка", "quicksort"],
  "def quick_sort(xs):\n    if len(xs) <= 1:\n        return list(xs)\n    p = xs[len(xs) // 2]\n    return quick_sort([x for x in xs if x < p]) + [x for x in xs if x == p] + quick_sort([x for x in xs if x > p])", "assert quick_sort([5, 2, 9, 1, 5]) == [1, 2, 5, 5, 9]", "списки")
L("list.bsearch", "Бинарный поиск", ["бинарный поиск", "найди элемент в отсортированном списке"],
  "def binary_search(xs, x):\n    lo, hi = 0, len(xs) - 1\n    while lo <= hi:\n        mid = (lo + hi) // 2\n        if xs[mid] == x:\n            return mid\n        if xs[mid] < x:\n            lo = mid + 1\n        else:\n            hi = mid - 1\n    return -1", "assert binary_search([1, 3, 5, 7], 5) == 2\nassert binary_search([1, 3], 4) == -1", "списки")
L("list.moving_avg", "Скользящее среднее", ["скользящее среднее", "сгладь значения", "moving average"],
  "def moving_average(xs, n=3):\n    out = []\n    for i in range(len(xs)):\n        w = xs[max(0, i - n + 1):i + 1]\n        out.append(sum(w) / len(w))\n    return out", "assert moving_average([1, 2, 3, 4], 2) == [1.0, 1.5, 2.5, 3.5]", "списки")
L("list.ema", "Экспоненциальное сглаживание", ["экспоненциальное сглаживание", "ema фильтр", "фильтр датчика"],
  "def ema(xs, alpha=0.3):\n    out = []\n    s = None\n    for x in xs:\n        s = x if s is None else alpha * x + (1 - alpha) * s\n        out.append(s)\n    return out", "r = ema([10, 20], 0.5)\nassert r == [10, 15.0]", "списки")
L("list.pairs", "Пары соседних элементов", ["пары соседей", "попарно элементы", "pairwise"],
  "def pairwise(xs):\n    return list(zip(xs, xs[1:]))", "assert pairwise([1, 2, 3]) == [(1, 2), (2, 3)]", "списки")
L("list.transpose", "Транспонировать таблицу", ["транспонируй матрицу", "строки в столбцы"],
  "def transpose(m):\n    return [list(r) for r in zip(*m)]", "assert transpose([[1, 2], [3, 4]]) == [[1, 3], [2, 4]]", "списки")
L("list.group", "Сгруппировать по ключу", ["сгруппируй по полю", "group by"],
  "from collections import defaultdict\n\n\ndef group_by(items, key):\n    g = defaultdict(list)\n    for it in items:\n        g[it[key]].append(it)\n    return dict(g)", "r = group_by([{'k': 1}, {'k': 2}, {'k': 1}], 'k')\nassert len(r[1]) == 2", "списки")
L("list.top", "Топ-N по значению", ["самые большие элементы", "топ n", "n наибольших"],
  "import heapq\n\n\ndef top_n(xs, n=3):\n    return heapq.nlargest(n, xs)", "assert top_n([5, 1, 9, 3], 2) == [9, 5]", "списки")
# ---- словари и данные
L("dict.merge", "Слить словари", ["объедини словари", "merge dict"],
  "def merge(a, b):\n    out = dict(a)\n    for k, v in b.items():\n        if isinstance(v, dict) and isinstance(out.get(k), dict):\n            out[k] = merge(out[k], v)\n        else:\n            out[k] = v\n    return out", "assert merge({'a': {'x': 1}}, {'a': {'y': 2}}) == {'a': {'x': 1, 'y': 2}}", "словари")
L("dict.invert", "Поменять ключи и значения", ["инвертируй словарь", "ключи на значения"],
  "def invert(d):\n    return {v: k for k, v in d.items()}", "assert invert({'a': 1}) == {1: 'a'}", "словари")
L("dict.get_path", "Достать вложенное значение по пути", ["достань значение по пути", "вложенный ключ", "get by path"],
  "def get_path(d, path, default=None):\n    cur = d\n    for p in str(path).split('.'):\n        if isinstance(cur, dict) and p in cur:\n            cur = cur[p]\n        else:\n            return default\n    return cur", "assert get_path({'a': {'b': 5}}, 'a.b') == 5\nassert get_path({}, 'a.b', 0) == 0", "словари")
L("dict.counter", "Подсчёт значений", ["посчитай сколько раз встречается", "counter", "гистограмма значений"],
  "from collections import Counter\n\n\ndef histogram(xs):\n    return dict(Counter(xs))", "assert histogram('aab') == {'a': 2, 'b': 1}", "словари")
# ---- файлы, json, csv
L("file.read_lines", "Прочитать файл построчно", ["прочитай файл", "считай строки из файла", "read file"],
  "def read_lines(path):\n    with open(path, encoding='utf-8') as f:\n        return [ln.rstrip('\\n') for ln in f]", "import tempfile, os\np = os.path.join(tempfile.mkdtemp(), 'a.txt')\nopen(p, 'w', encoding='utf-8').write('а\\nб\\n')\nassert read_lines(p) == ['а', 'б']", "файлы")
L("file.write_text", "Записать текст в файл", ["запиши в файл", "сохрани текст в файл", "write file"],
  "import os\n\n\ndef write_text(path, text):\n    d = os.path.dirname(os.path.abspath(path))\n    os.makedirs(d, exist_ok=True)\n    tmp = path + '.tmp'\n    with open(tmp, 'w', encoding='utf-8') as f:\n        f.write(text)\n    os.replace(tmp, path)", "import tempfile, os\np = os.path.join(tempfile.mkdtemp(), 'x', 'a.txt')\nwrite_text(p, 'привет')\nassert open(p, encoding='utf-8').read() == 'привет'", "файлы")
L("file.json", "Прочитать и записать JSON", ["работа с json", "сохрани в json", "прочитай json"],
  "import json\nimport os\n\n\ndef load_json(path, default=None):\n    try:\n        with open(path, encoding='utf-8') as f:\n            return json.load(f)\n    except (OSError, ValueError):\n        return default\n\n\ndef save_json(path, data):\n    tmp = path + '.tmp'\n    with open(tmp, 'w', encoding='utf-8') as f:\n        json.dump(data, f, ensure_ascii=False, indent=1)\n    os.replace(tmp, path)", "import tempfile, os\np = os.path.join(tempfile.mkdtemp(), 'a.json')\nsave_json(p, {'a': 'я'})\nassert load_json(p) == {'a': 'я'}\nassert load_json(p + 'x', 7) == 7", "файлы")
L("file.csv", "Прочитать и записать CSV", ["работа с csv", "прочитай csv", "сохрани таблицу в csv"],
  "import csv\n\n\ndef read_csv(path):\n    with open(path, encoding='utf-8', newline='') as f:\n        return list(csv.DictReader(f))\n\n\ndef write_csv(path, rows, fields):\n    with open(path, 'w', encoding='utf-8', newline='') as f:\n        w = csv.DictWriter(f, fieldnames=fields)\n        w.writeheader()\n        w.writerows(rows)", "import tempfile, os\np = os.path.join(tempfile.mkdtemp(), 'a.csv')\nwrite_csv(p, [{'a': 1, 'b': 'я'}], ['a', 'b'])\nassert read_csv(p) == [{'a': '1', 'b': 'я'}]", "файлы")
L("file.walk", "Найти файлы по маске", ["найди файлы", "список файлов в папке", "поиск файлов"],
  "import fnmatch\nimport os\n\n\ndef find_files(root, pattern='*'):\n    out = []\n    for dp, _dn, fn in os.walk(root):\n        for n in fn:\n            if fnmatch.fnmatch(n, pattern):\n                out.append(os.path.join(dp, n))\n    return sorted(out)", "import tempfile, os\nd = tempfile.mkdtemp()\nopen(os.path.join(d, 'a.py'), 'w').close()\nopen(os.path.join(d, 'b.txt'), 'w').close()\nassert [os.path.basename(x) for x in find_files(d, '*.py')] == ['a.py']", "файлы")
L("file.sizes", "Размеры файлов в папке", ["размер папки", "сколько весят файлы"],
  "import os\n\n\ndef dir_size(root):\n    total = 0\n    for dp, _dn, fn in os.walk(root):\n        for n in fn:\n            try:\n                total += os.path.getsize(os.path.join(dp, n))\n            except OSError:\n                pass\n    return total", "import tempfile, os\nd = tempfile.mkdtemp()\nopen(os.path.join(d, 'a'), 'w').write('12345')\nassert dir_size(d) == 5", "файлы")
L("file.backup", "Резервная копия файла с меткой времени", ["сделай копию файла", "бэкап файла"],
  "import os\nimport shutil\nimport time\n\n\ndef backup_file(path):\n    dst = '%s.%s.bak' % (path, time.strftime('%Y%m%d_%H%M%S'))\n    shutil.copy2(path, dst)\n    return dst", "import tempfile, os\np = os.path.join(tempfile.mkdtemp(), 'a.txt')\nopen(p, 'w').write('x')\nd = backup_file(p)\nassert os.path.exists(d) and d.endswith('.bak')", "файлы")
L("file.tail", "Последние строки файла", ["последние строки файла", "tail", "хвост лога"],
  "from collections import deque\n\n\ndef tail(path, n=20):\n    with open(path, encoding='utf-8', errors='replace') as f:\n        return [ln.rstrip('\\n') for ln in deque(f, maxlen=n)]", "import tempfile, os\np = os.path.join(tempfile.mkdtemp(), 'a')\nopen(p, 'w').write('1\\n2\\n3\\n')\nassert tail(p, 2) == ['2', '3']", "файлы")
# ---- регулярки и время
L("re.emails", "Найти e-mail адреса", ["найди email в тексте", "вытащи почту", "извлеки адреса"],
  "import re\n\n\ndef find_emails(text):\n    return re.findall(r'[\\w.+-]+@[\\w-]+(?:\\.[\\w-]+)+', text)", "assert find_emails('пиши a.b@x.ru или c@y.com.') == ['a.b@x.ru', 'c@y.com']", "регулярки")
L("re.numbers", "Вытащить числа из текста", ["найди числа в тексте", "извлеки числа"],
  "import re\n\n\ndef find_numbers(text):\n    return [float(x.replace(',', '.')) for x in re.findall(r'-?\\d+(?:[.,]\\d+)?', text)]", "assert find_numbers('t=21,5 и -3') == [21.5, -3.0]", "регулярки")
L("re.ip", "Найти IP-адреса", ["найди ip адреса", "извлеки ip из лога"],
  "import re\n\n\ndef find_ips(text):\n    ips = re.findall(r'\\b(?:\\d{1,3}\\.){3}\\d{1,3}\\b', text)\n    return [i for i in ips if all(0 <= int(p) <= 255 for p in i.split('.'))]", "assert find_ips('с 10.0.0.5 и 999.1.1.1') == ['10.0.0.5']", "регулярки")
L("time.format", "Форматирование даты и времени", ["текущая дата", "отформатируй время", "дата и время строкой"],
  "import time\n\n\ndef now_str(ts=None):\n    return time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(ts))", "assert len(now_str(0)) == 19", "время")
L("time.duration", "Длительность человеческим языком", ["сколько прошло времени", "секунды в часы минуты", "длительность"],
  "def human_duration(sec):\n    sec = int(sec)\n    d, r = divmod(sec, 86400)\n    h, r = divmod(r, 3600)\n    m, s = divmod(r, 60)\n    parts = []\n    if d:\n        parts.append('%d д' % d)\n    if h:\n        parts.append('%d ч' % h)\n    if m:\n        parts.append('%d мин' % m)\n    if s or not parts:\n        parts.append('%d с' % s)\n    return ' '.join(parts)", "assert human_duration(3725) == '1 ч 2 мин 5 с'\nassert human_duration(0) == '0 с'", "время")
L("time.timer", "Замер времени выполнения", ["замерь время выполнения", "таймер", "сколько работает функция"],
  "import time\n\n\ndef timed(fn, *a, **kw):\n    t0 = time.perf_counter()\n    r = fn(*a, **kw)\n    return r, time.perf_counter() - t0", "r, dt = timed(sum, [1, 2, 3])\nassert r == 6 and dt >= 0", "время")
L("time.retry", "Повторять при ошибке", ["повтори при ошибке", "retry", "ретрай с задержкой"],
  "import time\n\n\ndef retry(fn, tries=3, delay=0.5):\n    last = None\n    for i in range(tries):\n        try:\n            return fn()\n        except Exception as e:\n            last = e\n            time.sleep(delay * (i + 1))\n    raise last", "n = []\n\n\ndef flaky():\n    n.append(1)\n    if len(n) < 3:\n        raise RuntimeError('x')\n    return 'ok'\n\n\nassert retry(flaky, 3, 0.001) == 'ok'", "время")
