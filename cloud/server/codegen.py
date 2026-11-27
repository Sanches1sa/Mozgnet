# -*- coding: utf-8 -*-
# ===========================================================================
# codegen.py — РЕЖИМ «КОД»: МОЗГ ПИШЕТ КОД ПО ЗАПРОСУ (v3.22, только stdlib)
# ---------------------------------------------------------------------------
# Заявка владельца: «добавь режим код, пусть нейросеть пишет код по запросу».
#
# Как это работает (без LLM, честно — так же, как autocode.py и /код в
# приложении neuro_12):
#   1) ЗАДАЧИ (TASKS): библиотека заготовок на 6 языках — Python, JavaScript,
#      Arduino (C++), HTML/CSS/JS, Bash, SQL. Заготовка — настоящий код с
#      параметрами; мозг вынимает параметры из просьбы (число, пин, период в
#      мс/сек, название) и подставляет их.
#   2) ПОДБОР: по ключевым словам просьбы + определение языка («на питоне»,
#      «скетч для ардуино», «html-страница»…). Не нашла задачу — честно
#      говорит об этом и показывает, что умеет (без выдумок).
#   3) ПРОВЕРКА: Python — ast.parse + прогон тестов в песочнице облака
#      (sandbox.run_code); JS/C++/Bash/SQL/HTML — проверка баланса скобок,
#      кавычек и тегов. В отчёте честно сказано, ЧТО именно проверено.
#   4) РАЗБОР ЧУЖОГО КОДА: вставь код в ``` — мозг найдёт синтаксические
#      ошибки, неиспользуемые импорты, голые except, мутируемые аргументы по
#      умолчанию, необъявленные имена, и коротко объяснит структуру.
# Ответ — markdown с блоками ```язык (в интерфейсе у блока есть «Копировать»,
# «Скачать файл» и «Запустить» для Python).
# ===========================================================================
import ast
import builtins
import re

import sandbox

# ------------------------------- язык --------------------------------------
LANGS = {
    "python": {"name": "Python", "fence": "python", "ext": "py"},
    "js": {"name": "JavaScript", "fence": "javascript", "ext": "js"},
    "arduino": {"name": "Arduino (C++)", "fence": "cpp", "ext": "ino"},
    "html": {"name": "HTML", "fence": "html", "ext": "html"},
    "bash": {"name": "Bash", "fence": "bash", "ext": "sh"},
    "sql": {"name": "SQL", "fence": "sql", "ext": "sql"},
}

_LANG_WORDS = [
    ("arduino", r"arduino|ардуино|скетч|\bino\b|esp32|esp8266|светодиод|серво|"
                r"\bpin\b|пин[ауе]?\b|микроконтроллер|neuro_12|плат[аеуы]\b"),
    ("html", r"\bhtml\b|\bcss\b|страниц|верстк|сайт|лендинг|форм[ауы]\b|кнопк"),
    ("sql", r"\bsql\b|sqlite|запрос к базе|select\b|таблиц[ауы]\b|бд\b"),
    ("bash", r"\bbash\b|\bshell\b|\bsh\b|баш\b|шелл|терминал|скрипт для linux"),
    ("js", r"javascript|джаваскрипт|(?<![\w])js(?![\w])|\bnode\b|\bнод\b|"
           r"браузер|frontend"),
    ("python", r"python|питон|пайтон|(?<![\w])py(?![\w])"),
]


def detect_lang(text, forced=""):
    f = str(forced or "").strip().lower()
    if f in LANGS:
        return f, True
    low = str(text or "").lower()
    # явное имя языка важнее контекста
    for key, pat in reversed(_LANG_WORDS):
        if key in ("python", "js", "bash", "sql") and re.search(pat, low):
            return key, True
    for key, pat in _LANG_WORDS:
        if re.search(pat, low):
            return key, True
    return "python", False


# ---------------------------- параметры просьбы -----------------------------
def _ints(text):
    return [int(x) for x in re.findall(r"(?<![\w.])\d{1,9}(?![\w.])", text)]


def extract_params(text):
    low = str(text or "").lower().replace(",", ".")
    p = {}
    nums = _ints(low)
    p["n"] = nums[0] if nums else None
    m = (re.search(r"пин\w*\s*[dа]?\s*(\d{1,2})", low) or
         re.search(r"\bpin\s*[d]?\s*(\d{1,2})", low) or
         re.search(r"\b[dд](\d{1,2})\b", low) or
         re.search(r"\bгп?ио\s*(\d{1,2})", low))
    p["pin"] = int(m.group(1)) if m else None
    m2 = re.search(r"(?:пин\w*|pin)\s*[aа]\s*(\d)", low) or \
        re.search(r"\ba(\d)\b", low)
    p["apin"] = int(m2.group(1)) if m2 else None
    ms = None
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:мс|мсек|миллисек\w*|ms)\b", low)
    if m:
        ms = int(float(m.group(1)))
    else:
        m = re.search(r"(\d+(?:\.\d+)?)\s*(?:с|сек|секунд\w*|s)\b", low)
        if m:
            ms = int(float(m.group(1)) * 1000)
        elif re.search(r"кажд\w+\s+секунд|раз в секунд|секунд[уы]\b", low):
            ms = 1000
    p["ms"] = ms
    m = re.search(r"[«\"']([^»\"']{1,60})[»\"']", str(text or ""))
    p["title"] = m.group(1).strip() if m else None
    return p


def _fill(tpl, **kw):
    out = tpl
    for k, v in kw.items():
        out = out.replace("<<%s>>" % k, str(v))
    return out


# ------------------------------- ЗАДАЧИ -------------------------------------
# tid, name, keys (подстроки, нижний регистр), langs {язык: fn(params)->dict}
# fn возвращает {"code": ..., "demo": ... (необязательно), "test": (выражение,
# ожидаемая строка) для Python}. «demo» дописывается после кода (не тестируется).
TASKS = []


def task(tid, name, keys, **gens):
    TASKS.append({"id": tid, "name": name, "keys": tuple(keys), "gens": gens})


def _n(p, default, lo=1, hi=10**6):
    v = p.get("n")
    if v is None:
        return default
    return max(lo, min(hi, int(v)))


# ---- Python / JS: алгоритмы -------------------------------------------------
task("factorial", "Факториал", ("факториал", "factorial"),
     python=lambda p: {
         "code": ("def factorial(n):\n"
                  "    \"\"\"n! — произведение чисел от 1 до n.\"\"\"\n"
                  "    if n < 0:\n"
                  "        raise ValueError('факториал определён для n >= 0')\n"
                  "    result = 1\n"
                  "    for i in range(2, n + 1):\n"
                  "        result *= i\n"
                  "    return result\n"),
         "demo": "\n\nif __name__ == '__main__':\n    print(factorial(%d))\n" % _n(p, 5, 0, 500),
         "test": ("factorial(5)", "120")},
     js=lambda p: {"code": (
         "function factorial(n) {\n"
         "  if (n < 0) throw new RangeError('n должно быть >= 0');\n"
         "  let result = 1n;\n"
         "  for (let i = 2n; i <= BigInt(n); i++) result *= i;\n"
         "  return result;\n"
         "}\n\nconsole.log(String(factorial(%d)));\n" % _n(p, 5, 0, 500))})

task("fibonacci", "Числа Фибоначчи", ("фибоначчи", "fibonacci", "fib"),
     python=lambda p: {
         "code": ("def fibonacci(n):\n"
                  "    \"\"\"Первые n чисел Фибоначчи списком.\"\"\"\n"
                  "    seq = []\n"
                  "    a, b = 0, 1\n"
                  "    for _ in range(n):\n"
                  "        seq.append(a)\n"
                  "        a, b = b, a + b\n"
                  "    return seq\n"),
         "demo": "\n\nif __name__ == '__main__':\n    print(fibonacci(%d))\n" % _n(p, 10, 1, 200),
         "test": ("fibonacci(8)", "[0, 1, 1, 2, 3, 5, 8, 13]")},
     js=lambda p: {"code": (
         "function fibonacci(n) {\n"
         "  const seq = [];\n"
         "  let a = 0n, b = 1n;\n"
         "  for (let i = 0; i < n; i++) { seq.push(a); [a, b] = [b, a + b]; }\n"
         "  return seq;\n"
         "}\n\nconsole.log(fibonacci(%d).join(', '));\n" % _n(p, 10, 1, 200))})

task("prime", "Простые числа", ("простое", "простые", "простых", "prime"),
     python=lambda p: {
         "code": ("def is_prime(n):\n"
                  "    \"\"\"True, если n — простое число.\"\"\"\n"
                  "    if n < 2:\n"
                  "        return False\n"
                  "    i = 2\n"
                  "    while i * i <= n:\n"
                  "        if n % i == 0:\n"
                  "            return False\n"
                  "        i += 1\n"
                  "    return True\n\n\n"
                  "def primes_up_to(limit):\n"
                  "    \"\"\"Решето Эратосфена: все простые до limit.\"\"\"\n"
                  "    sieve = [True] * (limit + 1)\n"
                  "    for i in range(2, limit + 1):\n"
                  "        if sieve[i]:\n"
                  "            for j in range(i * i, limit + 1, i):\n"
                  "                sieve[j] = False\n"
                  "    return [i for i in range(2, limit + 1) if sieve[i]]\n"),
         "demo": "\n\nif __name__ == '__main__':\n    print(primes_up_to(%d))\n" % _n(p, 50, 2, 100000),
         "test": ("primes_up_to(20)", "[2, 3, 5, 7, 11, 13, 17, 19]")},
     js=lambda p: {"code": (
         "function isPrime(n) {\n"
         "  if (n < 2) return false;\n"
         "  for (let i = 2; i * i <= n; i++) if (n %% i === 0) return false;\n"
         "  return true;\n"
         "}\n\n"
         "const primes = Array.from({ length: %d }, (_, i) => i + 1).filter(isPrime);\n"
         "console.log(primes.join(', '));\n" % _n(p, 50, 2, 100000))})

task("sort", "Сортировка", ("сортиров", "sort", "упорядоч", "быстрая", "пузыр"),
     python=lambda p: {
         "code": ("def quick_sort(items):\n"
                  "    \"\"\"Быстрая сортировка (возвращает новый список).\"\"\"\n"
                  "    if len(items) <= 1:\n"
                  "        return list(items)\n"
                  "    pivot = items[len(items) // 2]\n"
                  "    left = [x for x in items if x < pivot]\n"
                  "    middle = [x for x in items if x == pivot]\n"
                  "    right = [x for x in items if x > pivot]\n"
                  "    return quick_sort(left) + middle + quick_sort(right)\n\n\n"
                  "def bubble_sort(items):\n"
                  "    \"\"\"Пузырьковая сортировка — для обучения, O(n²).\"\"\"\n"
                  "    a = list(items)\n"
                  "    for i in range(len(a)):\n"
                  "        swapped = False\n"
                  "        for j in range(len(a) - 1 - i):\n"
                  "            if a[j] > a[j + 1]:\n"
                  "                a[j], a[j + 1] = a[j + 1], a[j]\n"
                  "                swapped = True\n"
                  "        if not swapped:\n"
                  "            break\n"
                  "    return a\n"),
         "demo": "\n\nif __name__ == '__main__':\n    data = [5, 2, 9, 1, 5, 6]\n    print(quick_sort(data), bubble_sort(data))\n",
         "test": ("quick_sort([5, 2, 9, 1, 5, 6]) == bubble_sort([5, 2, 9, 1, 5, 6]) == [1, 2, 5, 5, 6, 9]", "True")},
     js=lambda p: {"code": (
         "function quickSort(items) {\n"
         "  if (items.length <= 1) return [...items];\n"
         "  const pivot = items[Math.floor(items.length / 2)];\n"
         "  const left = items.filter(x => x < pivot);\n"
         "  const middle = items.filter(x => x === pivot);\n"
         "  const right = items.filter(x => x > pivot);\n"
         "  return [...quickSort(left), ...middle, ...quickSort(right)];\n"
         "}\n\nconsole.log(quickSort([5, 2, 9, 1, 5, 6]));\n")})

task("palindrome", "Палиндром", ("палиндром", "palindrome"),
     python=lambda p: {
         "code": ("def is_palindrome(text):\n"
                  "    \"\"\"Палиндром ли строка (без пробелов, знаков и регистра).\"\"\"\n"
                  "    cleaned = ''.join(ch.lower() for ch in text if ch.isalnum())\n"
                  "    return cleaned == cleaned[::-1]\n"),
         "demo": "\n\nif __name__ == '__main__':\n    print(is_palindrome('А роза упала на лапу Азора'))\n",
         "test": ("is_palindrome('А роза упала на лапу Азора')", "True")},
     js=lambda p: {"code": (
         "function isPalindrome(text) {\n"
         "  const s = [...text.toLowerCase()].filter(ch => /[\\p{L}\\p{N}]/u.test(ch)).join('');\n"
         "  return s === [...s].reverse().join('');\n"
         "}\n\nconsole.log(isPalindrome('А роза упала на лапу Азора'));\n")})

task("wordcount", "Частота слов", ("частот", "подсчит", "слов в тексте", "wordcount", "word count"),
     python=lambda p: {
         "code": ("import re\n"
                  "from collections import Counter\n\n\n"
                  "def word_frequency(text, top=5):\n"
                  "    \"\"\"Самые частые слова текста: [(слово, сколько раз), ...].\"\"\"\n"
                  "    words = re.findall(r'[а-яёa-z0-9]+', text.lower())\n"
                  "    return Counter(words).most_common(top)\n"),
         "demo": "\n\nif __name__ == '__main__':\n    print(word_frequency('мозг думает, мозг учится, плата думает', 3))\n",
         "test": ("word_frequency('a b a c a b', 2)", "[('a', 3), ('b', 2)]")},
     js=lambda p: {"code": (
         "function wordFrequency(text, top = 5) {\n"
         "  const words = text.toLowerCase().match(/[а-яёa-z0-9]+/g) || [];\n"
         "  const counts = new Map();\n"
         "  for (const w of words) counts.set(w, (counts.get(w) || 0) + 1);\n"
         "  return [...counts].sort((a, b) => b[1] - a[1]).slice(0, top);\n"
         "}\n\nconsole.log(wordFrequency('мозг думает, мозг учится, плата думает', 3));\n")})

task("reverse", "Разворот строки", ("разверн", "наоборот", "reverse", "переворач",
                                    "переверн", "задом наперед", "задом наперёд",
                                    "в обратном порядке"),
     python=lambda p: {
         "code": ("def reverse_text(text):\n"
                  "    \"\"\"Строка задом наперёд.\"\"\"\n"
                  "    return text[::-1]\n"),
         "demo": "\n\nif __name__ == '__main__':\n    print(reverse_text('Star Gate'))\n",
         "test": ("reverse_text('abc')", "cba")},
     js=lambda p: {"code": (
         "const reverseText = (text) => [...text].reverse().join('');\n"
         "console.log(reverseText('Star Gate'));\n")})

task("stats", "Сумма, среднее, минимум, максимум",
     ("среднее", "сумма чисел", "максимум", "минимум", "статистик", "average", "mean"),
     python=lambda p: {
         "code": ("def describe(numbers):\n"
                  "    \"\"\"Сводка по списку чисел.\"\"\"\n"
                  "    if not numbers:\n"
                  "        raise ValueError('список пуст')\n"
                  "    total = sum(numbers)\n"
                  "    return {'sum': total, 'mean': total / len(numbers),\n"
                  "            'min': min(numbers), 'max': max(numbers)}\n"),
         "demo": "\n\nif __name__ == '__main__':\n    print(describe([3, 7, 1, 9]))\n",
         "test": ("describe([3, 7, 1, 9])['mean']", "5.0")},
     js=lambda p: {"code": (
         "function describe(numbers) {\n"
         "  if (!numbers.length) throw new Error('список пуст');\n"
         "  const sum = numbers.reduce((a, b) => a + b, 0);\n"
         "  return { sum, mean: sum / numbers.length,\n"
         "           min: Math.min(...numbers), max: Math.max(...numbers) };\n"
         "}\n\nconsole.log(describe([3, 7, 1, 9]));\n")})

task("fizzbuzz", "FizzBuzz", ("fizzbuzz", "fizz buzz", "физбаз", "физзбазз"),
     python=lambda p: {
         "code": ("def fizzbuzz(limit):\n"
                  "    \"\"\"Числа 1..limit: кратные 3 — Fizz, 5 — Buzz, обоим — FizzBuzz.\"\"\"\n"
                  "    out = []\n"
                  "    for i in range(1, limit + 1):\n"
                  "        if i % 15 == 0:\n"
                  "            out.append('FizzBuzz')\n"
                  "        elif i % 3 == 0:\n"
                  "            out.append('Fizz')\n"
                  "        elif i % 5 == 0:\n"
                  "            out.append('Buzz')\n"
                  "        else:\n"
                  "            out.append(str(i))\n"
                  "    return out\n"),
         "demo": "\n\nif __name__ == '__main__':\n    print(' '.join(fizzbuzz(%d)))\n" % _n(p, 15, 1, 1000),
         "test": ("fizzbuzz(15)[-1]", "FizzBuzz")},
     js=lambda p: {"code": (
         "for (let i = 1; i <= %d; i++) {\n"
         "  console.log(i %% 15 === 0 ? 'FizzBuzz' : i %% 3 === 0 ? 'Fizz' : i %% 5 === 0 ? 'Buzz' : i);\n"
         "}\n" % _n(p, 15, 1, 1000))},
     bash=lambda p: {"code": (
         "#!/usr/bin/env bash\n"
         "for i in $(seq 1 %d); do\n"
         "  if   (( i %% 15 == 0 )); then echo FizzBuzz\n"
         "  elif (( i %% 3  == 0 )); then echo Fizz\n"
         "  elif (( i %% 5  == 0 )); then echo Buzz\n"
         "  else echo \"$i\"; fi\n"
         "done\n" % _n(p, 15, 1, 1000))})

task("gcd", "НОД и НОК", ("нод", "нок", "делитель", "gcd", "lcm", "кратное"),
     python=lambda p: {
         "code": ("def gcd(a, b):\n"
                  "    \"\"\"Наибольший общий делитель (алгоритм Евклида).\"\"\"\n"
                  "    while b:\n"
                  "        a, b = b, a % b\n"
                  "    return abs(a)\n\n\n"
                  "def lcm(a, b):\n"
                  "    \"\"\"Наименьшее общее кратное.\"\"\"\n"
                  "    return abs(a * b) // gcd(a, b) if a and b else 0\n"),
         "demo": "\n\nif __name__ == '__main__':\n    print(gcd(48, 18), lcm(4, 6))\n",
         "test": ("(gcd(48, 18), lcm(4, 6))", "(6, 12)")},
     js=lambda p: {"code": (
         "const gcd = (a, b) => (b ? gcd(b, a %% b) : Math.abs(a));\n"
         "const lcm = (a, b) => (a && b ? Math.abs(a * b) / gcd(a, b) : 0);\n"
         "console.log(gcd(48, 18), lcm(4, 6));\n").replace("%%", "%")})

task("bsearch", "Бинарный поиск", ("бинарн", "двоичный поиск", "binary search"),
     python=lambda p: {
         "code": ("def binary_search(sorted_items, target):\n"
                  "    \"\"\"Индекс target в отсортированном списке или -1.\"\"\"\n"
                  "    lo, hi = 0, len(sorted_items) - 1\n"
                  "    while lo <= hi:\n"
                  "        mid = (lo + hi) // 2\n"
                  "        if sorted_items[mid] == target:\n"
                  "            return mid\n"
                  "        if sorted_items[mid] < target:\n"
                  "            lo = mid + 1\n"
                  "        else:\n"
                  "            hi = mid - 1\n"
                  "    return -1\n"),
         "demo": "\n\nif __name__ == '__main__':\n    print(binary_search([1, 3, 5, 7, 9], 7))\n",
         "test": ("(binary_search([1, 3, 5, 7, 9], 7), binary_search([1, 3], 4))", "(3, -1)")},
     js=lambda p: {"code": (
         "function binarySearch(items, target) {\n"
         "  let lo = 0, hi = items.length - 1;\n"
         "  while (lo <= hi) {\n"
         "    const mid = (lo + hi) >> 1;\n"
         "    if (items[mid] === target) return mid;\n"
         "    if (items[mid] < target) lo = mid + 1; else hi = mid - 1;\n"
         "  }\n"
         "  return -1;\n"
         "}\n\nconsole.log(binarySearch([1, 3, 5, 7, 9], 7));\n")})

task("temperature", "Градусы: Цельсий ↔ Фаренгейт", ("цельси", "фаренгейт", "температур", "celsius"),
     python=lambda p: {
         "code": ("def c_to_f(c):\n"
                  "    return c * 9 / 5 + 32\n\n\n"
                  "def f_to_c(f):\n"
                  "    return (f - 32) * 5 / 9\n"),
         "demo": "\n\nif __name__ == '__main__':\n    print(c_to_f(36.6), f_to_c(98.6))\n",
         "test": ("(c_to_f(100), f_to_c(32))", "(212.0, 0.0)")},
     js=lambda p: {"code": (
         "const cToF = (c) => c * 9 / 5 + 32;\n"
         "const fToC = (f) => (f - 32) * 5 / 9;\n"
         "console.log(cToF(36.6).toFixed(1), fToC(98.6).toFixed(1));\n")})

# ---- Python / JS: инфраструктура -------------------------------------------
task("httpserver", "HTTP-сервер", ("http-сервер", "http сервер", "веб-сервер", "вебсервер", "web server", "http server", "сервер на"),
     python=lambda p: {"code": (
         "from http.server import BaseHTTPRequestHandler, HTTPServer\n"
         "import json\n\n\n"
         "class Handler(BaseHTTPRequestHandler):\n"
         "    def do_GET(self):\n"
         "        body = json.dumps({'ok': True, 'path': self.path}).encode('utf-8')\n"
         "        self.send_response(200)\n"
         "        self.send_header('Content-Type', 'application/json; charset=utf-8')\n"
         "        self.send_header('Content-Length', str(len(body)))\n"
         "        self.end_headers()\n"
         "        self.wfile.write(body)\n\n\n"
         "if __name__ == '__main__':\n"
         "    server = HTTPServer(('0.0.0.0', %d), Handler)\n"
         "    print('слушаю http://localhost:%d')\n"
         "    server.serve_forever()\n" % (_n(p, 8000, 1024, 65535), _n(p, 8000, 1024, 65535)))},
     js=lambda p: {"code": (
         "const http = require('http');\n\n"
         "const server = http.createServer((req, res) => {\n"
         "  res.writeHead(200, { 'Content-Type': 'application/json; charset=utf-8' });\n"
         "  res.end(JSON.stringify({ ok: true, path: req.url }));\n"
         "});\n\n"
         "server.listen(%d, () => console.log('слушаю http://localhost:%d'));\n"
         % (_n(p, 3000, 1024, 65535), _n(p, 3000, 1024, 65535)))})

task("httpget", "HTTP-запрос (GET, JSON)", ("запрос к api", "http-запрос", "get-запрос", "получить json", "fetch", "requests", "запрос по http", "api"),
     python=lambda p: {"code": (
         "import json\n"
         "import urllib.request\n\n\n"
         "def get_json(url, timeout=10):\n"
         "    \"\"\"GET-запрос, возвращает разобранный JSON.\"\"\"\n"
         "    req = urllib.request.Request(url, headers={'User-Agent': 'neuro12'})\n"
         "    with urllib.request.urlopen(req, timeout=timeout) as resp:\n"
         "        return json.loads(resp.read().decode('utf-8'))\n\n\n"
         "if __name__ == '__main__':\n"
         "    print(get_json('http://localhost:8077/api/health'))\n")},
     js=lambda p: {"code": (
         "async function getJson(url) {\n"
         "  const res = await fetch(url);\n"
         "  if (!res.ok) throw new Error('HTTP ' + res.status);\n"
         "  return res.json();\n"
         "}\n\n"
         "getJson('http://localhost:8077/api/health').then(console.log).catch(console.error);\n")})

task("cloudsandbox", "Плата → облако: выполнить код в песочнице",
     ("песочниц", "sandbox", "токен платы", "x-mozg-token", "к облаку мозг", "отправить в облако"),
     python=lambda p: {"code": (
         "import json\n"
         "import urllib.request\n\n"
         "CLOUD = 'http://192.168.1.50:8077'   # адрес облака Star Gate\n"
         "TOKEN = 'ТОКЕН_ПЛАТЫ'                  # Настройки → Общие → токен платы\n\n\n"
         "def run_in_cloud(code, text=''):\n"
         "    \"\"\"Выполнить def run(text, ctx=None) в песочнице облака.\"\"\"\n"
         "    data = json.dumps({'code': code, 'text': text, 'board': 'neuro_12'}).encode('utf-8')\n"
         "    req = urllib.request.Request(\n"
         "        CLOUD + '/api/sandbox/run', data=data, method='POST',\n"
         "        headers={'Content-Type': 'application/json', 'X-Mozg-Token': TOKEN})\n"
         "    with urllib.request.urlopen(req, timeout=15) as resp:\n"
         "        return json.loads(resp.read().decode('utf-8'))\n\n\n"
         "if __name__ == '__main__':\n"
         "    src = \"def run(text, ctx=None):\\n    return text.upper()\\n\"\n"
         "    print(run_in_cloud(src, 'привет'))\n")})

task("classpy", "Класс с данными", ("класс", "dataclass", "class "),
     python=lambda p: {"code": (
         "from dataclasses import dataclass, field\n\n\n"
         "@dataclass\n"
         "class Neuron:\n"
         "    \"\"\"Простой нейрон: вес на каждый вход и порог активации.\"\"\"\n"
         "    weights: list = field(default_factory=list)\n"
         "    threshold: float = 0.5\n\n"
         "    def activate(self, inputs):\n"
         "        s = sum(w * x for w, x in zip(self.weights, inputs))\n"
         "        return 1 if s >= self.threshold else 0\n\n\n"
         "if __name__ == '__main__':\n"
         "    n = Neuron(weights=[0.4, 0.4, 0.4], threshold=0.7)\n"
         "    print(n.activate([1, 1, 0]), n.activate([1, 0, 0]))\n")})

task("debounce", "Debounce", ("debounce", "дебаунс", "задержка ввода"),
     js=lambda p: {"code": (
         "function debounce(fn, delay = %d) {\n"
         "  let timer;\n"
         "  return (...args) => {\n"
         "    clearTimeout(timer);\n"
         "    timer = setTimeout(() => fn(...args), delay);\n"
         "  };\n"
         "}\n\n"
         "const onInput = debounce((text) => console.log('искать:', text));\n"
         "onInput('м'); onInput('мо'); onInput('мозг');\n" % (p.get("ms") or 300))})

# ---- Arduino ---------------------------------------------------------------
_ARD_TIP = ("// Плата: Arduino UNO / UNO Q. Загрузка: Arduino IDE → Инструменты → Плата.\n")

task("blink", "Мигание светодиодом", ("мигани", "мигай", "мигает", "blink", "~светодиод"),
     arduino=lambda p: {"code": _fill(
         _ARD_TIP + "const int LED_PIN = <<PIN>>;      // пин светодиода\n"
         "const unsigned long PERIOD_MS = <<MS>>;  // полупериод, мс\n\n"
         "void setup() {\n"
         "  pinMode(LED_PIN, OUTPUT);\n"
         "}\n\n"
         "void loop() {\n"
         "  digitalWrite(LED_PIN, HIGH);\n"
         "  delay(PERIOD_MS);\n"
         "  digitalWrite(LED_PIN, LOW);\n"
         "  delay(PERIOD_MS);\n"
         "}\n", PIN=p.get("pin") if p.get("pin") is not None else 13,
         MS=p.get("ms") or 500)})

task("blink_nodelay", "Мигание без delay (millis)", ("без delay", "millis", "неблокирующ", "не блокируя"),
     arduino=lambda p: {"code": _fill(
         _ARD_TIP + "const int LED_PIN = <<PIN>>;\n"
         "const unsigned long PERIOD_MS = <<MS>>;\n"
         "unsigned long lastToggle = 0;\n"
         "bool ledOn = false;\n\n"
         "void setup() {\n"
         "  pinMode(LED_PIN, OUTPUT);\n"
         "}\n\n"
         "void loop() {\n"
         "  unsigned long now = millis();\n"
         "  if (now - lastToggle >= PERIOD_MS) {   // корректно и при переполнении millis()\n"
         "    lastToggle = now;\n"
         "    ledOn = !ledOn;\n"
         "    digitalWrite(LED_PIN, ledOn ? HIGH : LOW);\n"
         "  }\n"
         "  // здесь можно делать другие дела — loop не блокируется\n"
         "}\n", PIN=p.get("pin") if p.get("pin") is not None else 13,
         MS=p.get("ms") or 500)})

task("button", "Кнопка включает светодиод", ("кнопк", "button", "нажатии"),
     arduino=lambda p: {"code": _fill(
         _ARD_TIP + "const int BUTTON_PIN = 2;   // кнопка между пином и GND\n"
         "const int LED_PIN = <<PIN>>;\n"
         "bool ledOn = false;\n"
         "int lastState = HIGH;\n\n"
         "void setup() {\n"
         "  pinMode(BUTTON_PIN, INPUT_PULLUP);   // внутренняя подтяжка: нажата = LOW\n"
         "  pinMode(LED_PIN, OUTPUT);\n"
         "}\n\n"
         "void loop() {\n"
         "  int state = digitalRead(BUTTON_PIN);\n"
         "  if (lastState == HIGH && state == LOW) {   // фронт нажатия\n"
         "    ledOn = !ledOn;\n"
         "    digitalWrite(LED_PIN, ledOn ? HIGH : LOW);\n"
         "    delay(30);                               // простое подавление дребезга\n"
         "  }\n"
         "  lastState = state;\n"
         "}\n", PIN=p.get("pin") if p.get("pin") is not None else 13)})

task("analog", "Чтение аналогового датчика в Serial", ("аналог", "датчик", "потенциометр", "analogread", "фоторезистор", "serial"),
     arduino=lambda p: {"code": _fill(
         _ARD_TIP + "const int SENSOR_PIN = A<<APIN>>;\n"
         "const unsigned long PERIOD_MS = <<MS>>;\n\n"
         "void setup() {\n"
         "  Serial.begin(115200);\n"
         "}\n\n"
         "void loop() {\n"
         "  int raw = analogRead(SENSOR_PIN);              // 0..1023\n"
         "  float volts = raw * (5.0 / 1023.0);\n"
         "  Serial.print(\"raw=\");\n"
         "  Serial.print(raw);\n"
         "  Serial.print(\"  V=\");\n"
         "  Serial.println(volts, 2);\n"
         "  delay(PERIOD_MS);\n"
         "}\n", APIN=p.get("apin") if p.get("apin") is not None else 0,
         MS=p.get("ms") or 500)})

task("pwm", "Плавное затухание светодиода (PWM)", ("плавн", "затухан", "яркост", "pwm", "шим", "analogwrite"),
     arduino=lambda p: {"code": _fill(
         _ARD_TIP + "const int LED_PIN = <<PIN>>;   // пин с поддержкой ШИМ (~) : 3, 5, 6, 9, 10, 11\n"
         "int brightness = 0;\n"
         "int step = 5;\n\n"
         "void setup() {\n"
         "  pinMode(LED_PIN, OUTPUT);\n"
         "}\n\n"
         "void loop() {\n"
         "  analogWrite(LED_PIN, brightness);\n"
         "  brightness += step;\n"
         "  if (brightness <= 0 || brightness >= 255) step = -step;\n"
         "  delay(20);\n"
         "}\n", PIN=p.get("pin") if p.get("pin") is not None else 9)})

task("servo", "Сервопривод: качание", ("серво", "servo"),
     arduino=lambda p: {"code": _fill(
         _ARD_TIP + "#include <Servo.h>\n\n"
         "Servo servo;\n"
         "const int SERVO_PIN = <<PIN>>;\n\n"
         "void setup() {\n"
         "  servo.attach(SERVO_PIN);\n"
         "}\n\n"
         "void loop() {\n"
         "  for (int angle = 0; angle <= 180; angle += 2) {\n"
         "    servo.write(angle);\n"
         "    delay(15);\n"
         "  }\n"
         "  for (int angle = 180; angle >= 0; angle -= 2) {\n"
         "    servo.write(angle);\n"
         "    delay(15);\n"
         "  }\n"
         "}\n", PIN=p.get("pin") if p.get("pin") is not None else 9)})

task("ultrasonic", "Ультразвуковой дальномер HC-SR04", ("ультразвук", "дальномер", "hc-sr04", "hcsr04", "расстояни"),
     arduino=lambda p: {"code": _fill(
         _ARD_TIP + "const int TRIG_PIN = <<PIN>>;\n"
         "const int ECHO_PIN = <<ECHO>>;\n\n"
         "void setup() {\n"
         "  Serial.begin(115200);\n"
         "  pinMode(TRIG_PIN, OUTPUT);\n"
         "  pinMode(ECHO_PIN, INPUT);\n"
         "}\n\n"
         "float readCm() {\n"
         "  digitalWrite(TRIG_PIN, LOW);\n"
         "  delayMicroseconds(2);\n"
         "  digitalWrite(TRIG_PIN, HIGH);\n"
         "  delayMicroseconds(10);\n"
         "  digitalWrite(TRIG_PIN, LOW);\n"
         "  unsigned long us = pulseIn(ECHO_PIN, HIGH, 30000UL);   // таймаут 30 мс\n"
         "  if (us == 0) return -1;                                 // нет эха\n"
         "  return us * 0.0343 / 2.0;                               // скорость звука, см/мкс\n"
         "}\n\n"
         "void loop() {\n"
         "  float cm = readCm();\n"
         "  if (cm < 0) Serial.println(\"нет эха\");\n"
         "  else { Serial.print(cm, 1); Serial.println(\" см\"); }\n"
         "  delay(200);\n"
         "}\n", PIN=p.get("pin") if p.get("pin") is not None else 9,
         ECHO=(p.get("pin") + 1) if p.get("pin") is not None else 10)})

task("serialcmd", "Команды по Serial (ON/OFF)", ("команд", "serial.read", "управлени", "по serial", "через serial"),
     arduino=lambda p: {"code": _fill(
         _ARD_TIP + "const int LED_PIN = <<PIN>>;\n"
         "String line;\n\n"
         "void setup() {\n"
         "  Serial.begin(115200);\n"
         "  pinMode(LED_PIN, OUTPUT);\n"
         "  Serial.println(\"Команды: ON, OFF, STATUS\");\n"
         "}\n\n"
         "void loop() {\n"
         "  while (Serial.available()) {\n"
         "    char c = Serial.read();\n"
         "    if (c == '\\n') {\n"
         "      line.trim();\n"
         "      line.toUpperCase();\n"
         "      if (line == \"ON\")       { digitalWrite(LED_PIN, HIGH); Serial.println(\"OK: включено\"); }\n"
         "      else if (line == \"OFF\") { digitalWrite(LED_PIN, LOW);  Serial.println(\"OK: выключено\"); }\n"
         "      else if (line == \"STATUS\") Serial.println(digitalRead(LED_PIN) ? \"светодиод: ON\" : \"светодиод: OFF\");\n"
         "      else Serial.println(\"неизвестная команда\");\n"
         "      line = \"\";\n"
         "    } else if (c != '\\r') {\n"
         "      line += c;\n"
         "    }\n"
         "  }\n"
         "}\n", PIN=p.get("pin") if p.get("pin") is not None else 13)})

# ---- HTML ------------------------------------------------------------------
_HTML_HEAD = ("<!DOCTYPE html>\n<html lang=\"ru\">\n<head>\n<meta charset=\"utf-8\">\n"
              "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
              "<title><<TITLE>></title>\n<style>\n"
              "  :root { --bg:#0f1115; --card:#1a1e26; --text:#eceef2; --acc:#2dd4bf; }\n"
              "  * { box-sizing: border-box; }\n"
              "  body { margin:0; min-height:100vh; display:grid; place-items:center;\n"
              "         background:var(--bg); color:var(--text); font-family:system-ui,sans-serif; }\n"
              "  .card { background:var(--card); border-radius:18px; padding:28px; width:min(92vw,380px);\n"
              "          box-shadow:0 20px 60px rgba(0,0,0,.45); text-align:center; }\n"
              "  button { background:var(--acc); color:#04211d; border:0; border-radius:12px;\n"
              "           padding:12px 20px; font-size:16px; font-weight:700; cursor:pointer; }\n"
              "  button:active { transform:scale(.97); }\n")

task("counter", "Кнопка-счётчик", ("счётчик", "счетчик", "counter", "клик"),
     html=lambda p: {"code": _fill(
         _HTML_HEAD + "  .num { font-size:64px; font-weight:800; margin:10px 0 18px; }\n"
         "</style>\n</head>\n<body>\n<div class=\"card\">\n  <h1><<TITLE>></h1>\n"
         "  <div class=\"num\" id=\"num\">0</div>\n"
         "  <button id=\"plus\">+1</button>\n  <button id=\"reset\" style=\"background:#333;color:#eee\">Сброс</button>\n"
         "</div>\n<script>\n  let n = 0;\n  const out = document.getElementById('num');\n"
         "  document.getElementById('plus').onclick = () => { n++; out.textContent = n; };\n"
         "  document.getElementById('reset').onclick = () => { n = 0; out.textContent = n; };\n"
         "</script>\n</body>\n</html>\n", TITLE=p.get("title") or "Счётчик")})

task("todo", "Список дел", ("список дел", "todo", "to-do", "задачи", "заметки"),
     html=lambda p: {"code": _fill(
         _HTML_HEAD + "  ul { list-style:none; padding:0; text-align:left; }\n"
         "  li { padding:9px 4px; border-bottom:1px solid #2a2f3a; cursor:pointer; }\n"
         "  li.done { text-decoration:line-through; opacity:.5; }\n"
         "  input { width:100%; padding:11px; border-radius:10px; border:1px solid #2a2f3a;\n"
         "          background:#12151b; color:var(--text); margin-bottom:10px; }\n"
         "</style>\n</head>\n<body>\n<div class=\"card\">\n  <h1><<TITLE>></h1>\n"
         "  <input id=\"inp\" placeholder=\"Новая задача и Enter\">\n  <ul id=\"list\"></ul>\n</div>\n"
         "<script>\n  const list = document.getElementById('list');\n  const inp = document.getElementById('inp');\n"
         "  inp.addEventListener('keydown', (e) => {\n"
         "    if (e.key !== 'Enter' || !inp.value.trim()) return;\n"
         "    const li = document.createElement('li');\n"
         "    li.textContent = inp.value.trim();          // textContent — без XSS\n"
         "    li.onclick = () => li.classList.toggle('done');\n"
         "    list.appendChild(li);\n    inp.value = '';\n  });\n"
         "</script>\n</body>\n</html>\n", TITLE=p.get("title") or "Мои дела")})

task("htmlpage", "Страница-визитка", ("страниц", "визитк", "лендинг", "сайт", "html"),
     html=lambda p: {"code": _fill(
         _HTML_HEAD + "  h1 { margin:0 0 8px; }\n  p { color:#9aa3b2; line-height:1.5; }\n"
         "</style>\n</head>\n<body>\n<div class=\"card\">\n  <h1><<TITLE>></h1>\n"
         "  <p>Короткое описание: кто вы и чем занимаетесь.</p>\n"
         "  <button onclick=\"alert('Привет!')\">Связаться</button>\n</div>\n</body>\n</html>\n",
         TITLE=p.get("title") or "Моя страница")})

# ---- Bash ------------------------------------------------------------------
task("backup", "Резервная копия папки", ("резервн", "бэкап", "бекап", "backup", "архив"),
     bash=lambda p: {"code": (
         "#!/usr/bin/env bash\n"
         "set -euo pipefail\n\n"
         "SRC=\"${1:?укажи папку: ./backup.sh /путь/к/папке}\"\n"
         "DEST=\"${2:-$HOME/backups}\"\n"
         "STAMP=$(date +%Y-%m-%d_%H-%M-%S)\n\n"
         "mkdir -p \"$DEST\"\n"
         "tar -czf \"$DEST/$(basename \"$SRC\")_$STAMP.tar.gz\" -C \"$(dirname \"$SRC\")\" \"$(basename \"$SRC\")\"\n"
         "echo \"готово: $DEST/$(basename \"$SRC\")_$STAMP.tar.gz\"\n\n"
         "# оставляем 7 последних копий\n"
         "ls -1t \"$DEST\"/*.tar.gz | tail -n +8 | xargs -r rm --\n")})

task("countfiles", "Подсчёт файлов по расширениям", ("файлов", "расширени", "посчитать файлы"),
     bash=lambda p: {"code": (
         "#!/usr/bin/env bash\n"
         "DIR=\"${1:-.}\"\n"
         "find \"$DIR\" -type f | sed -E 's/.*\\.([^./]+)$/\\1/; t; s/.*/(без расширения)/' \\\n"
         "  | sort | uniq -c | sort -rn\n")})

# ---- SQL -------------------------------------------------------------------
task("sqltable", "Таблица и данные", ("создать таблицу", "create table", "таблиц", "insert", "бд", "база данных"),
     sql=lambda p: {"code": (
         "CREATE TABLE IF NOT EXISTS boards (\n"
         "  id        INTEGER PRIMARY KEY,\n"
         "  name      TEXT    NOT NULL,\n"
         "  online    INTEGER NOT NULL DEFAULT 0,\n"
         "  last_seen TEXT\n"
         ");\n\n"
         "INSERT INTO boards (name, online, last_seen) VALUES\n"
         "  ('neuro_1', 1, '2026-09-30 10:00:00'),\n"
         "  ('neuro_2', 0, '2026-09-29 18:30:00');\n\n"
         "SELECT id, name FROM boards WHERE online = 1 ORDER BY name;\n")})

task("sqljoin", "JOIN и группировка", ("join", "объедин", "группиров", "group by", "count", "подсчёт по"),
     sql=lambda p: {"code": (
         "-- сколько сообщений у каждого чата\n"
         "SELECT c.id, c.title, COUNT(m.id) AS messages\n"
         "FROM chats AS c\n"
         "LEFT JOIN messages AS m ON m.chat_id = c.id\n"
         "GROUP BY c.id, c.title\n"
         "HAVING COUNT(m.id) > 0\n"
         "ORDER BY messages DESC\n"
         "LIMIT 10;\n")})


# ------------------------------ подбор задачи -------------------------------
def _score(t, low, lang, lang_explicit):
    s = 0
    for k in t["keys"]:
        weak = k.startswith("~")          # «~слово» — слабый общий признак
        kk = k[1:] if weak else k
        if kk in low:
            s += 1 if weak else 2 + min(len(kk), 12) / 6.0
    if not s:
        return 0
    if lang in t["gens"]:
        s += 3 if lang_explicit else 1
    elif lang_explicit:
        s -= 1.5
    return s


def pick(text, lang, lang_explicit):
    low = str(text or "").lower()
    best, best_s = None, 0
    for t in TASKS:
        s = _score(t, low, lang, lang_explicit)
        if s > best_s:
            best, best_s = t, s
    return best if best_s >= 2 else None


# ------------------------------- проверка ------------------------------------
def _balance(code):
    """Баланс скобок/кавычек для C-подобных языков (без комментариев)."""
    stack, pairs = [], {")": "(", "]": "[", "}": "{"}
    i, n, in_str, in_line, in_blk = 0, len(code), None, False, False
    while i < n:
        ch, nx = code[i], code[i + 1] if i + 1 < n else ""
        if in_line:
            if ch == "\n":
                in_line = False
        elif in_blk:
            if ch == "*" and nx == "/":
                in_blk = False
                i += 1
        elif in_str:
            if ch == "\\":
                i += 1
            elif ch == in_str:
                in_str = None
        elif ch == "/" and nx == "/":
            in_line = True
        elif ch == "/" and nx == "*":
            in_blk = True
            i += 1
        elif ch in "\"'`":
            in_str = ch
        elif ch in "([{":
            stack.append(ch)
        elif ch in ")]}":
            if not stack or stack.pop() != pairs[ch]:
                return "лишняя или не та закрывающая скобка «%s»" % ch
        i += 1
    if in_str:
        return "не закрыта кавычка"
    if stack:
        return "не закрыта скобка «%s»" % stack[-1]
    return ""


def _check_html(code):
    tags = re.findall(r"<(/?)([a-zA-Z][a-zA-Z0-9]*)[^>]*?(/?)>", code)
    void = {"meta", "link", "br", "hr", "img", "input", "source", "wbr", "col"}
    st = []
    for close, name, selfc in tags:
        n = name.lower()
        if n in void or selfc:
            continue
        if not close:
            st.append(n)
        else:
            if not st or st.pop() != n:
                return "тег </%s> закрыт не там" % n
    return ("не закрыт тег <%s>" % st[-1]) if st else ""


def verify(lang, code, test=None):
    """Возврат (ok, честная строка о том, что проверено)."""
    if lang == "python":
        try:
            ast.parse(code)
        except SyntaxError as e:
            return False, "синтаксическая ошибка: %s (строка %s)" % (e.msg, e.lineno)
        if test:
            expr, want = test
            # тесты гоняем только на определениях (без блока __main__)
            body = code.split("\nif __name__")[0]
            harness = body + "\n\ndef run(text, ctx=None):\n    return str(%s)\n" % expr
            ok, out = sandbox.run_code(harness, "", timeout=10)
            if not ok:
                return False, "тест в песочнице упал: %s" % str(out)[:120]
            if str(out).strip() != str(want):
                return False, "тест не сошёлся: %s дал %s, ждали %s" % (expr, str(out)[:60], want)
            return True, ("синтаксис верный; тест в песочнице облака пройден "
                          "(%s → %s)" % (expr[:50], str(out)[:40]))
        return True, "синтаксис Python верный (ast.parse); тестов для этой заготовки нет"
    if lang in ("js", "arduino", "bash", "sql"):
        if lang == "bash":
            if code.count('"') % 2:
                return False, "нечётное число двойных кавычек"
            return True, "кавычки парны, есть shebang (bash не запускался)"
        err = _balance(code)
        if err:
            return False, err
        if lang == "arduino" and not ("void setup" in code and "void loop" in code):
            return False, "нет setup()/loop()"
        return True, "скобки и кавычки сбалансированы%s (компилятор не запускался)" % (
            "; есть setup() и loop()" if lang == "arduino" else "")
    if lang == "html":
        err = _check_html(code)
        return (False, err) if err else (True, "теги парны, структура страницы верна")
    return True, "не проверялось"


# --------------------------------- ответ -------------------------------------
def _fence(lang, code):
    return "```%s\n%s\n```" % (LANGS[lang]["fence"], code.rstrip("\n"))


def catalog():
    """Что умею: список задач и языков (для API и справки)."""
    return {"langs": [{"key": k, "name": v["name"], "ext": v["ext"]} for k, v in LANGS.items()],
            "tasks": [{"id": t["id"], "name": t["name"], "langs": sorted(t["gens"].keys())}
                      for t in TASKS]}


def _menu(limit=14):
    names = []
    for t in TASKS[:limit * 3]:
        names.append("%s (%s)" % (t["name"], ", ".join(LANGS[l]["ext"] for l in sorted(t["gens"]))))
    return "; ".join(names[:limit]) + " и другое"


_CODE_VERBS = re.compile(r"напиш|сделай|создай|сгенер|код|функци|скетч|скрипт|програм|"
                         r"реализуй|покажи пример|write|create|generate", re.I)
_REVIEW_VERBS = re.compile(r"провер|ошибк|баг|объясн|разбер|review|найди|что не так|исправ|отлад|почему не", re.I)


def extract_fenced(text):
    m = re.search(r"```([\w+#-]*)\n?(.*?)```", str(text or ""), re.S)
    if m:
        return m.group(1).strip().lower(), m.group(2).strip("\n")
    return "", ""


def looks_like_code(text):
    t = str(text or "")
    if t.count("\n") < 2:
        return False
    hits = len(re.findall(r"^\s*(def |class |import |from \S+ import|function |const |let |var |"
                          r"#include|void |int |for\s*\(|if\s*\(|SELECT |CREATE |<\w+)", t, re.M))
    return hits >= 2


def answer(text, lang="", ctx=None):
    """Главный вход режима «Код». Возврат: (reply, matched, info)
    matched=False — просьба не про код и мозг может ответить обычным путём."""
    text = str(text or "").strip()
    info = {"code": True}
    flang, fcode = extract_fenced(text)
    if fcode or (looks_like_code(text) and _REVIEW_VERBS.search(text)):
        code = fcode or text
        return review(code, flang or _guess_lang(code), text), True, info
    if not _CODE_VERBS.search(text) and pick(text, "python", False) is None:
        return None, False, info
    lg, explicit = detect_lang(text, lang)
    t = pick(text, lg, explicit)
    if t:
        # v3.41: заготовка совпала слабо (общим словом) и есть модель — пусть
        # лучше напишет модель, чем отдавать чужой шаблон
        try:
            import codewrite
            if codewrite.can_write():
                low = text.lower()
                strong = [k for k in t["keys"]
                          if not k.startswith("~") and k in low]
                if not strong or _score(t, low, lg, explicit) <= 4.0:
                    own = codewrite.compose(text, lg)
                    if own:
                        info.update(code_lang=lg, code_own=True)
                        return own, True, info
        except Exception:
            pass
    if not t:
        try:                      # v3.41: заготовки не подошли — пишу сама
            import codewrite
            own = codewrite.compose(text, lg)
        except Exception:
            own = ""
        if own:
            info.update(code_lang=lg, code_own=True)
            return own, True, info
        return ("Такой код я пока не умею собирать сама. Из своего у меня — только "
                "проверенные заготовки: %s. Языки: Python, JavaScript, Arduino, HTML, Bash, SQL. "
                "Скажи, например: «напиши на python факториал 10» или «скетч мигание светодиодом "
                "на пине 7 каждые 300 мс». Ещё можно вставить свой код в ``` — я его разберу и "
                "найду ошибки." % _menu()), True, info
    use = lg
    note = ""
    if use not in t["gens"]:
        # запрошенного языка у заготовки нет — честно предлагаем то, что есть
        avail = sorted(t["gens"].keys())
        if explicit:
            have = ", ".join(LANGS[a]["name"] for a in avail)
            return ("Заготовку «%s» я умею писать на: %s — на %s пока нет. Напиши, например, "
                    "«… на %s»." % (t["name"], have, LANGS[lg]["name"], LANGS[avail[0]]["name"])), True, info
        use = avail[0]
        note = " (язык не назван — выбрала %s)" % LANGS[use]["name"]
    p = extract_params(text)
    g = t["gens"][use](p)
    code = g["code"] + (g.get("demo") or "")
    ok, what = verify(use, code, g.get("test"))
    if not ok:
        return ("Собрала «%s»%s, но собственная проверка не прошла: %s. Отдавать сломанный код "
                "не стану — переформулируй просьбу." % (t["name"], note, what)), True, info
    params_note = _params_note(use, p, t["id"])
    reply = ("Собрала «%s» на %s%s:\n\n%s\n\n✓ Проверка: %s.%s" % (
        t["name"], LANGS[use]["name"], note, _fence(use, code), what, params_note))
    if use == "arduino":
        reply += ("\n\nКак использовать: открой Arduino IDE, вставь скетч, выбери плату и порт, "
                  "нажми «Загрузить».")
    info.update(code_lang=use, code_task=t["id"], code_ext=LANGS[use]["ext"])
    return reply, True, info


def _params_note(lang, p, tid):
    bits = []
    if lang == "arduino":
        if p.get("pin") is not None:
            bits.append("пин %d" % p["pin"])
        if p.get("ms"):
            bits.append("период %d мс" % p["ms"])
    elif p.get("n") is not None and tid in ("factorial", "fibonacci", "prime", "fizzbuzz"):
        bits.append("n = %d" % p["n"])
    return ("\nВзяла из просьбы: %s." % ", ".join(bits)) if bits else ""


def _guess_lang(code):
    if re.search(r"^\s*(def |import |from \S+ import|class \w+.*:)", code, re.M):
        return "python"
    if "void setup" in code or "#include" in code:
        return "arduino"
    if re.search(r"<html|<div|<body", code, re.I):
        return "html"
    if re.search(r"^\s*(SELECT|INSERT|CREATE|UPDATE)\b", code, re.I | re.M):
        return "sql"
    if code.startswith("#!") or re.search(r"\bfi\b|\bdone\b", code):
        return "bash"
    return "js"


# ------------------------------- разбор кода ----------------------------------
_B = set(dir(builtins)) | {"__file__", "__name__", "__doc__"}


def _declared(tree):
    names = set()
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(n.name)
        elif isinstance(n, ast.Import):
            for a in n.names:
                names.add((a.asname or a.name).split(".")[0])
        elif isinstance(n, ast.ImportFrom):
            for a in n.names:
                names.add(a.asname or a.name)
        elif isinstance(n, ast.Name) and isinstance(n.ctx, (ast.Store, ast.Del)):
            names.add(n.id)
        elif isinstance(n, ast.arg):
            names.add(n.arg)
        elif isinstance(n, ast.ExceptHandler) and n.name:
            names.add(n.name)
        elif isinstance(n, (ast.Global, ast.Nonlocal)):
            names.update(n.names)
    return names


def lint_python(code):
    """Список (строка, серьёзность, сообщение). Серьёзность: error|warn|info."""
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return [(e.lineno or 1, "error", "синтаксическая ошибка: %s" % e.msg)], None
    out = []
    declared = _declared(tree)
    used = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            for a in n.names:
                nm = (a.asname or a.name).split(".")[0]
                if nm not in used:
                    out.append((n.lineno, "warn", "импорт «%s» не используется" % nm))
        elif isinstance(n, ast.ImportFrom):
            for a in n.names:
                nm = a.asname or a.name
                if nm != "*" and nm not in used:
                    out.append((n.lineno, "warn", "импорт «%s» не используется" % nm))
        elif isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load):
            if n.id not in declared and n.id not in _B:
                out.append((n.lineno, "error", "имя «%s» нигде не объявлено" % n.id))
        elif isinstance(n, ast.ExceptHandler):
            if n.type is None:
                out.append((n.lineno, "warn", "голый except: ловит даже KeyboardInterrupt — укажи Exception"))
            if len(n.body) == 1 and isinstance(n.body[0], ast.Pass):
                out.append((n.lineno, "warn", "except с pass молча глотает ошибку — хотя бы залогируй"))
        elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for d in n.args.defaults + [x for x in n.args.kw_defaults if x]:
                if isinstance(d, (ast.List, ast.Dict, ast.Set)):
                    out.append((n.lineno, "warn", "мутируемое значение по умолчанию в «%s» "
                                "— общий объект на все вызовы; используй None" % n.name))
        elif isinstance(n, ast.Compare):
            for op, c in zip(n.ops, n.comparators):
                if isinstance(op, (ast.Eq, ast.NotEq)) and isinstance(c, ast.Constant) and c.value is None:
                    out.append((n.lineno, "info", "сравнение с None лучше писать через «is None»"))
        elif isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in ("eval", "exec"):
            out.append((n.lineno, "warn", "%s() исполняет произвольный код — небезопасно" % n.func.id))
    seen = {}
    for s in tree.body:
        if isinstance(s, (ast.FunctionDef, ast.ClassDef)):
            if s.name in seen:
                out.append((s.lineno, "warn", "«%s» определена повторно (первая — строка %d)" % (s.name, seen[s.name])))
            seen[s.name] = s.lineno
    out = sorted(set(out))
    return out, tree


def _outline(tree):
    items = []
    for s in tree.body:
        if isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef)):
            args = ", ".join(a.arg for a in s.args.args)
            doc = (ast.get_docstring(s) or "").split("\n")[0][:80]
            items.append("`%s(%s)`%s" % (s.name, args, (" — " + doc) if doc else ""))
        elif isinstance(s, ast.ClassDef):
            meths = [m.name for m in s.body if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))]
            items.append("класс `%s` (методы: %s)" % (s.name, ", ".join(meths[:8]) or "нет"))
    return items


def review(code, lang="python", raw=""):
    lang = lang if lang in LANGS else _guess_lang(code)
    lines = code.count("\n") + 1
    if lang == "python":
        issues, tree = lint_python(code)
        if tree is None:
            ln, _sev, msg = issues[0]
            return ("Разобрала код. **Найдена ошибка:** %s — строка %d. Пока она не "
                    "исправлена, программа не запустится." % (msg, ln))
        icon = {"error": "🔴", "warn": "🟡", "info": "🔵"}
        parts = ["Разобрала код на Python (%d строк)." % lines]
        outl = _outline(tree)
        if outl:
            parts.append("**Структура:** " + "; ".join(outl[:8]) + ".")
        if issues:
            parts.append("**Замечания (%d):**\n" % len(issues) + "\n".join(
                "%s строка %d — %s" % (icon[s], l, m) for l, s, m in issues[:14])
                + (("\n…и ещё %d замечаний" % (len(issues) - 14)) if len(issues) > 14 else ""))
        else:
            parts.append("✓ Синтаксис верный, явных проблем (необъявленные имена, лишние импорты, "
                         "голые except, мутируемые аргументы) не нашла.")
        parts.append("Проверка статическая (без запуска). Хочешь — напиши «/код» с задачей, "
                     "и я соберу свой вариант.")
        return "\n\n".join(parts)
    # прочие языки: баланс + базовые признаки
    if lang == "html":
        err = _check_html(code)
    elif lang in ("js", "arduino", "sql"):
        err = _balance(code)
    else:
        err = "" if code.count('"') % 2 == 0 else "нечётное число двойных кавычек"
    notes = []
    if lang == "arduino":
        if "void setup" not in code:
            notes.append("нет `setup()`")
        if "void loop" not in code:
            notes.append("нет `loop()`")
        if "delay(" in code and "millis(" not in code:
            notes.append("`delay()` блокирует программу — для параллельных дел лучше `millis()`")
    if lang == "js" and re.search(r"[^=!]==[^=]", code):
        notes.append("используется `==` — обычно нужен строгий `===`")
    if lang == "js" and "var " in code:
        notes.append("`var` лучше заменить на `let`/`const`")
    if lang == "sql" and re.search(r"select\s+\*", code, re.I):
        notes.append("`SELECT *` тянет лишние колонки — перечисли нужные")
    head = "Разобрала код на %s (%d строк)." % (LANGS[lang]["name"], lines)
    if err:
        return "%s **Найдена ошибка:** %s. Компилятор я не запускала — проверка по структуре." % (head, err)
    body = ("\n".join("🟡 " + n for n in notes)) if notes else "явных проблем по структуре не нашла"
    return "%s Скобки и кавычки сбалансированы.\n\n%s\n\nПроверка статическая: %s не запускался." % (
        head, body, LANGS[lang]["name"])


# ------------------------- запуск Python из чата -----------------------------
class _PrintToBuf(ast.NodeTransformer):
    """print(...) -> _p(...): вывод собираем в строку (в песочнице print — заглушка)."""

    def visit_Call(self, node):
        self.generic_visit(node)
        if isinstance(node.func, ast.Name) and node.func.id == "print":
            node.func = ast.Name(id="_p", ctx=ast.Load())
            node.keywords = []
        return node


def _is_main_guard(s):
    if not isinstance(s, ast.If) or not isinstance(s.test, ast.Compare):
        return False
    t = s.test
    return (isinstance(t.left, ast.Name) and t.left.id == "__name__"
            and len(t.comparators) == 1 and isinstance(t.comparators[0], ast.Constant)
            and t.comparators[0].value == "__main__")


def run_python(code, text="", timeout=8):
    """Запустить Python-блок из чата в песочнице облака. Блок `if __name__ ==
    '__main__':` выполняется, print() собирается в вывод. Возврат (ok, вывод|ошибка)."""
    code = str(code or "")[:30000]
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return False, "синтаксическая ошибка: %s (строка %s)" % (e.msg, e.lineno)
    if any(isinstance(n, ast.FunctionDef) and n.name == "run" for n in tree.body):
        ok, out = sandbox.run_code(code, text, timeout=timeout)
        return ok, out
    defs = [s for s in tree.body if not _is_main_guard(s)]
    mains = [st for s in tree.body if _is_main_guard(s) for st in s.body]
    if not mains:
        return False, ("нечего запускать: в блоке нет `if __name__ == '__main__':` "
                       "и нет функции run(text). Добавь вызов функции с print().")
    buf_def = ast.parse("def _p(*a):\n    _buf.append(' '.join(str(x) for x in a))\n").body[0]
    ret = ast.parse("return '\\n'.join(_buf)").body[0]
    init = ast.parse("_buf = []").body[0]
    body = [init, buf_def] + [_PrintToBuf().visit(m) for m in mains] + [ret]
    fn = ast.FunctionDef(name="run", args=ast.arguments(
        posonlyargs=[], args=[ast.arg(arg="text"), ast.arg(arg="ctx")], vararg=None, kwonlyargs=[],
        kw_defaults=[], kwarg=None, defaults=[ast.Constant(value=None)]),
        body=body, decorator_list=[], returns=None, type_comment=None)
    mod = ast.Module(body=defs + [fn], type_ignores=[])
    ast.fix_missing_locations(mod)
    try:
        src = ast.unparse(mod)
    except Exception as e:
        return False, "не удалось подготовить код к запуску: %s" % e
    ok, out = sandbox.run_code(src, text, timeout=timeout)
    if not ok:
        return False, str(out)
    return True, (str(out).strip() or "(программа ничего не вывела)")
