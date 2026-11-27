# -*- coding: utf-8 -*-
"""codeteach.py — УЧИТЕЛЬ КОДА для своей LLM (v3.192, вкладка «Учитель (LLM)»).

Внешний учитель (например, YandexGPT от Яндекса) придумывает небольшие
упражнения на Python: задача на русском, решение — ОДНА функция, проверки
(assert). Каждый урок проверяется облаком:

  * разбор AST — ровно одна функция без import/class/lambda с побочками;
  * запуск проверок в песочнице (свой набор встроенных функций, счётчики
    в циклах, лимит 5 секунд) — неверный или «вечный» код отбрасывается.

Принятые уроки копятся в банке (data/code_teach.json) и передаются
существующему курсу кода (codeboot.start(rows=...)) — своя LLM дообучается
на них. Внешняя LLM, как всегда на платформе, в чате не отвечает: она только
учитель. Ключ и каталог Яндекса — те же, что для поиска (⚙ «Интернет и
знания»: yandex_api_key / yandex_folder), отдельного ключа не нужно.

Движки:
  yandex  — YandexGPT (OpenAI-совместимый API Yandex AI Studio);
  teacher — «внешний учитель» из этой же вкладки (OpenAI-совместимый URL).

v3.193: темы уроков расширены группами «информационная безопасность»,
«администрирование серверов» и «сети» — как курсы программы обучения
(curriculum.py); уроков за курс по умолчанию 12 (было 6), максимум 40.
"""
import ast
import builtins
import copy
import json
import re
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

import store

_LOCK = threading.RLock()
_THREAD = None
_CANCEL = threading.Event()
_STATE = None

ENGINE_NAMES = {"yandex": "Яндекс (YandexGPT)", "teacher": "внешний учитель"}
YANDEX_URL = "https://ai.api.cloud.yandex.net/v1/chat/completions"
DEFAULT_MODEL = "yandexgpt/latest"

# встроенные функции, разрешённые в решениях (песочница проверок)
SAFE_BUILTINS = ("len str int float bool list dict set tuple sum min max sorted "
                 "abs range enumerate zip reversed round chr ord any all map "
                 "filter isinstance divmod pow ord bytes bytearray frozenset"
                 ).split()

# узлы AST, запрещённые в решении (сеть, файлы, окружение)
_FORBIDDEN = (ast.Import, ast.ImportFrom, ast.With, ast.AsyncWith,
              ast.Global, ast.Nonlocal, ast.ClassDef, ast.Lambda,
              ast.Yield, ast.YieldFrom, ast.Delete, ast.Starred,
              ast.JoinedStr, ast.Await, ast.Try, ast.AsyncFor,
              ast.AsyncFunctionDef,  # f-строки, try и async — не даём
              )
_MAX_BANK = 500          # уроков в банке (v3.193: было 300)
_CHECK_SECONDS = 5       # лимит времени на проверки урока
_LOOP_LIMIT = 20_000_000  # счётчик итераций циклов в проверяемом коде


def _cfg():
    st = store.load_json('code_teach.json', {}) or {}
    st.setdefault("engine", "yandex")
    st.setdefault("endpoint", "")
    st.setdefault("model", "")
    st.setdefault("lessons", 12)   # v3.193: было 6 — уроков стало больше
    st.setdefault("topics", "")
    st.setdefault("bank", [])
    st.setdefault("stats", {"requested": 0, "accepted": 0, "rejected": 0})
    return st


def _save(st):
    store.save_json('code_teach.json', st)


# ------------------------------------------------------------------ движки
def _yandex_parts(st):
    """(url, key, model) для Яндекса: ключ и каталог — из настроек поиска."""
    s = store.settings()
    key = str(s.get("yandex_api_key") or "").strip()
    folder = str(s.get("yandex_folder") or "").strip()
    url = str(st.get("endpoint") or "").strip() or YANDEX_URL
    model = _base_model(str(st.get("model") or "").strip() or DEFAULT_MODEL)
    if folder:
        model = "gpt://%s/%s" % (folder, model)
    return url, key, model, folder


def _base_model(model):
    """gpt://каталог/yandexgpt/latest -> yandexgpt/latest (имя с версией)."""
    m = str(model or "").strip()
    if m.startswith("gpt://"):
        parts = m[len("gpt://"):].split("/")
        return "/".join(parts[1:]) if len(parts) > 1 else (parts[0] or DEFAULT_MODEL)
    return m


def _engine(st):
    """Готовый вызов учителя: (fn(system,user), название|None, ошибка|'')."""
    engine = str(st.get("engine") or "yandex")
    if engine == "yandex":
        url, key, model, folder = _yandex_parts(st)
        if not key:
            return None, "", ("нужен ключ API Яндекса — ⚙ Настройки → "
                               "«Интернет и знания» → «Яндекс (ключ API)»")
        def call(system, user):
            return _chat(url, key, model, system, user,
                         extra_headers={"x-folder-id": folder} if folder else {})
        return call, "Яндекс (YandexGPT)", ""
    # внешний учитель из вкладки
    t = (store.settings().get("teacher") or {})
    if not (t.get("llm_on") and str(t.get("llm_url") or "").strip()):
        return None, "", ("внешний учитель выключен — включите его выше или "
                           "выберите движок «Яндекс»")
    url, key, model = str(t["llm_url"]).strip(), str(t.get("llm_key") or "").strip(), str(t.get("llm_model") or "").strip()
    def call(system, user):
        import teacher
        return teacher._openai_chat(url, key, model, system,
                                    [{"role": "user", "content": user}], 900)
    return call, "внешний учитель", ""


def _chat(url, key, model, system, user, extra_headers=None):
    """OpenAI-совместимый вызов (Яндекс). Возврат: (текст|None, ошибка|'')."""
    if not url.endswith("/chat/completions"):
        url = url.rstrip("/") + "/chat/completions"
    body = {"model": model or "yandexgpt/latest",
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
            "max_tokens": 1200, "temperature": 0.3}
    headers = {"Content-Type": "application/json",
               "User-Agent": "neuro12-cloud/3.0", "Authorization": "Bearer " + key}
    headers.update(extra_headers or {})
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"),
                                 headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            j = json.loads(r.read().decode("utf-8", "replace"))
        out = ((j.get("choices") or [{}])[0].get("message") or {}).get("content")
        out = (out or "").strip()
        return (out or None), ("" if out else "пустой ответ учителя")
    except urllib.error.HTTPError as e:
        try:
            detail = e.read().decode("utf-8", "replace")[:200]
        except Exception:
            detail = ""
        return None, "HTTP %s: %s" % (e.code, detail)
    except Exception as e:
        return None, str(e)[:160]


# -------------------------------------------------------------- уроки
SYSTEM = '''Ты учитель (программирование, информационная безопасность или администрирование серверов). Придумай ОДНО маленькое упражнение на
Python для ученицы-нейросети. Ответь ТОЛЬКО JSON-объектом на одной строке:
{"task":"задача по-русски, одним предложением",
 "solution":"код Python — ровно одна функция def с решением, без import, без print, без тестов",
 "checks":["assert имя(аргументы) == ответ", "ещё 2–3 разные проверки"]}
Правила: функция должна быть чистой (без ввода-вывода), аргументы — простые
значения (числа, строки, списки), проверки — только assert с литеральными
аргументами и ожидаемым результатом. Решение без рекурсии и без вложенных
определений. Тема: {topic}'''


# v3.193: темы по группам — как курсы «программа обучения» (curriculum.py):
# программирование -> информационная безопасность -> администрирование -> сети.
# Упражнения всегда чистые функции на Python: тема безопасности/администрирования
# — это разбор строк (пароль, лог, вывод df), без импортов и ввода-вывода.
_TOPIC_GROUPS = (
    ("программирование", (
        "числа и арифметика", "строки", "списки", "условия и сравнения",
        "циклы", "словари", "разбор чисел на цифры", "текстовые задачи")),
    ("информационная безопасность", (
        "пароли: проверка стойкости по длине и классам символов",
        "шифр Цезаря: сдвиг букв строки",
        "простое шифрование XOR байтов строки",
        "маскирование данных: скрыть email и телефон",
        "разбор строк лога: найти неудачные входы",
        "считать и проверить контрольную сумму строки",
        "генерация PIN-кода из цифр",
        "проверка списка паролей на повторы")),
    ("администрирование серверов", (
        "разбор вывода df: найти самый полный раздел",
        "разбор вывода free: посчитать занятую память в процентах",
        "разбор строк /etc/passwd: имена пользователей",
        "разбор вывода ps: найти процесс с максимальным CPU",
        "разбор строк journalctl: ошибки и предупреждения по уровням",
        "подсчёт суммарного размера файлов из списка",
        "разбор crontab-строки: минуты и часы запуска")),
    ("сети", (
        "проверка корректности IPv4-адреса",
        "разбор маски подсети в префикс",
        "разбор URL на протокол, хост и порт",
        "порт службы по названию (80, 443, 22)")),
)
_TOPICS = tuple(t for _g, ts in _TOPIC_GROUPS for t in ts)   # плоский список
TOPIC_GROUP = {t: g for g, ts in _TOPIC_GROUPS for t in ts}   # тема -> группа


def _pick_topic(i, custom):
    """(тема, группа) по номеру урока; свои темы — группа «свои темы»."""
    if custom and str(custom).strip():
        parts = [x.strip() for x in str(custom).split(",") if x.strip()]
        t = parts[i % len(parts)]
        return t, TOPIC_GROUP.get(t, "свои темы")
    t = _TOPICS[i % len(_TOPICS)]
    return t, TOPIC_GROUP.get(t, "программирование")


def _parse_lesson(raw):
    """JSON учителя -> (task, code, checks[]). fences ``` терпим."""
    m = re.search(r"\{[\s\S]*\}", raw or "")
    if not m:
        return None, "в ответе нет JSON"
    try:
        j = json.loads(m.group(0))
    except Exception as e:
        return None, "JSON не разобрался: %s" % str(e)[:80]
    task, code = str(j.get("task") or "").strip(), str(j.get("solution") or "").strip()
    checks = j.get("checks") or []
    if isinstance(checks, str):
        checks = [x for x in checks.split("\n") if x.strip()]
    checks = [str(x).strip() for x in checks if str(x).strip()]
    if not task or not code or not checks:
        return None, "в уроке нет задачи, решения или проверок"
    code = re.sub(r"^```[a-zA-Z]*\s*|```\s*$", "", code).strip()
    return (task[:200], code, checks[:6]), ""


# ------------------------------------------------------------ проверка кода
class _LoopGuard(ast.NodeTransformer):
    """Вставляет счётчик в каждый цикл: вечный цикл сам поднимет ошибку."""
    _N = [0]

    def visit_While(self, node):
        return self._guard(node)

    def visit_For(self, node):
        return self._guard(node)

    def _guard(self, node):
        self.generic_visit(node)
        _LoopGuard._N[0] += 1
        name = "__g%d" % _LoopGuard._N[0]
        guard = ast.parse("%s = 0" % name).body[0]
        check = ast.parse(
            "%s += 1\nif %s > %d:\n    raise RuntimeError('слишком долго')"
            % (name, name, _LOOP_LIMIT)).body
        node.body = [check[0], check[1]] + list(node.body)
        return [guard, node]


def _validate(code, checks):
    """Урок в песочнице. Возврат: '' — принят, иначе причина отказа."""
    try:
        tree = ast.parse(code)
    except (SyntaxError, ValueError) as e:
        return "синтаксическая ошибка: %s" % str(e)[:100]
    if len(tree.body) != 1 or not isinstance(tree.body[0], ast.FunctionDef):
        return "нужно ровно одно определение функции def"
    fn = tree.body[0]
    if fn.decorator_list or fn.args.vararg or fn.args.kwarg:
        return "лишние украшения или *args/**kwargs"
    if fn.args.args and fn.args.args[0].arg in ("self", "cls"):
        return "метод без класса"
    for node in ast.walk(tree):
        if isinstance(node, _FORBIDDEN):
            return "в решении %s — запрещено" % type(node).__name__
        if isinstance(node, ast.Name) and node.id.startswith("__"):
            return "служебные имена в решении"
    tree = _LoopGuard().visit(tree)
    ast.fix_missing_locations(tree)
    env = {"__builtins__": {n: getattr(builtins, n) for n in SAFE_BUILTINS
                            if hasattr(builtins, n)}}
    env["__builtins__"]["RuntimeError"] = RuntimeError   # для счётчика циклов
    try:
        exec(compile(tree, "<урок>", "exec"), env)
    except Exception as e:
        return "код не выполнился: %s" % str(e)[:100]
    name = fn.name
    if not re.match(r"[a-zA-Z_][a-zA-Z0-9_]*$", name):
        return "странное имя функции"
    ns = dict(env)

    def run_all():
        for ch in checks:
            env2 = dict(ns)
            try:
                exec(compile(ch, "<проверка>", "exec"), env2)
            except AssertionError:
                return "проверка не сошлась: %s" % ch[:80]
            except Exception as e:
                return "проверка упала: %s (%s)" % (ch[:60], str(e)[:60])
        return ""

    with ThreadPoolExecutor(max_workers=1) as ex:
        fut = ex.submit(run_all)
        try:
            err = fut.result(_CHECK_SECONDS)
        except TimeoutError:
            return "проверки дольше %d с — код отброшен" % _CHECK_SECONDS
        if err:
            return err
    return ""


# -------------------------------------------------------------- курс
def configure(engine=None, endpoint=None, model=None, lessons=None, topics=None):
    with _LOCK:
        st = _cfg()
        if engine in ENGINE_NAMES:
            st["engine"] = engine
        if endpoint is not None:
            st["endpoint"] = str(endpoint).strip()[:200]
        if model is not None:
            st["model"] = str(model).strip()[:80]
        if lessons is not None:
            try:
                st["lessons"] = max(1, min(40, int(lessons)))   # v3.193: до 40
            except (TypeError, ValueError):
                pass
        if topics is not None:
            st["topics"] = str(topics).strip()[:200]
        _save(st)
        return status()


def status():
    with _LOCK:
        st = copy.deepcopy(_STATE or _cfg())
        if st.get("running") and not (_THREAD and _THREAD.is_alive()):
            st.update(running=False, phase="прерван",
                      message="Курс прерван перезапуском сервера")
        bank = st.get("bank") or []
        st["bank_size"] = len(bank)
        st["bank"] = bank[-8:][::-1]                # последние уроки — наверх
        st["engine_name"] = ENGINE_NAMES.get(st.get("engine"), st.get("engine"))
        stats = st.get("stats") or {}
        stats.update({"requested": st.get("requested", stats.get("requested", 0)),
                      "accepted": st.get("accepted", stats.get("accepted", 0)),
                      "rejected": st.get("rejected", stats.get("rejected", 0))})
        st["stats"] = stats
        return st


def stop():
    _CANCEL.set()
    return status()


def start():
    global _THREAD, _STATE
    st0 = _cfg()
    call, name, err = _engine(st0)
    if call is None:
        return {"ok": False, "error": err}
    with _LOCK:
        if _THREAD and _THREAD.is_alive():
            return {"ok": False, "error": "урок уже идёт"}
        _CANCEL.clear()
        st = _cfg()
        st.update(running=True, phase="старт", message="Учитель готов: %s" % name,
                  engine_name=name, requested=0, accepted=0, rejected=0,
                  started=time.time(), updated=time.time(), last_error="",
                  last_lesson=None, boot=None)
        _STATE = st
        _save(st)
        _THREAD = threading.Thread(target=_run, args=(st, call), daemon=True,
                                   name="codeteach")
        _THREAD.start()
        store.log("УЧИТЕЛЬ", "курс кода от %s: %d уроков" % (name, st.get("lessons", 6)))
    return {"ok": True, "teach": status()}


def _update(st, **vals):
    with _LOCK:
        st.update(vals, updated=time.time())
        _save(st)


def _run(st, call):
    n = max(1, min(40, int(st.get("lessons") or 12)))   # v3.193: до 40 уроков
    topics = st.get("topics") or ""
    rows = []
    try:
        for i in range(n):
            if _CANCEL.is_set():
                _update(st, running=False, phase="остановлен",
                        message="Остановлено владельцем на уроке %d из %d" % (i + 1, n))
                return
            topic, group = _pick_topic(i, topics)
            _update(st, phase="урок %d из %d" % (i + 1, n),
                    message="Тема (%s): %s" % (group, topic),
                    requested=st.get("requested", 0))
            raw, err = call(SYSTEM.replace("{topic}", topic),
                            "Придумай упражнение по теме «%s»." % topic)
            if not raw:
                _update(st, last_error="учитель не ответил: %s" % err)
                continue
            lesson, perr = _parse_lesson(raw)
            st["requested"] = st.get("requested", 0) + 1
            if not lesson:
                _update(st, rejected=st.get("rejected", 0) + 1,
                        last_error=perr)
                continue
            task, code, checks = lesson
            verr = _validate(code, checks)
            if verr:
                _update(st, rejected=st.get("rejected", 0) + 1,
                        last_error="урок не принят: %s" % verr)
                continue
            with _LOCK:
                bank = _cfg().get("bank") or []
                bank.append({"t": task, "c": code, "ts": time.time(),
                             "topic": topic, "g": group})
                st["bank"] = bank[-_MAX_BANK:]
                _save(st)
            st["accepted"] = st.get("accepted", 0) + 1
            rows.append({"t": task, "c": code})
            _update(st, accepted=st["accepted"],
                    last_lesson={"task": task, "code": code[:600],
                                 "topic": topic, "group": group},
                    last_error="")
        if not rows:
            _update(st, running=False, phase="без уроков",
                    message="Учитель не дал ни одного верного урока — веса не менялись")
            return
        # дообучение своей LLM на принятых уроках — существующий курс кода
        _update(st, phase="дообучение", message="Своя LLM учит %d урок(ов)" % len(rows))
        try:
            import codeboot
            course = codeboot.start(rows=rows, mode="code", steps=200,
                                    seconds=120, source="учитель кода")
            _update(st, boot={"phase": course.get("phase"),
                              "message": course.get("message")})
        except Exception as e:
            _update(st, boot={"error": str(e)[:200]},
                    message="Уроки приняты в банк, но курс не запустился: %s" % str(e)[:120])
        _update(st, running=False, phase="готово",
                message="Уроков принято %d из %d — банк %d, своя LLM дообучается"
                        % (st.get("accepted", 0), st.get("requested", 0),
                           len(st.get("bank") or [])))
    except Exception as e:
        _update(st, running=False, phase="ошибка",
                message="%s: %s" % (type(e).__name__, str(e)[:200]))


def check_teacher():
    """Кнопка «Проверить учителя кода»: один настоящий запрос, честный ответ."""
    st = _cfg()
    call, name, err = _engine(st)
    if call is None:
        return {"ok": False, "error": err}
    raw, cerr = call(SYSTEM.replace("{topic}", "числа и арифметика"),
                     "Придумай простое упражнение про удвоение числа.")
    if not raw:
        return {"ok": False, "error": cerr or "пустой ответ"}
    lesson, perr = _parse_lesson(raw)
    if not lesson:
        return {"ok": False, "error": perr, "raw": raw[:300]}
    task, code, checks = lesson
    verr = _validate(code, checks)
    return {"ok": True, "engine": name, "task": task, "code": code,
            "checks": checks, "valid": not verr, "why": verr}


if __name__ == "__main__":
    print(json.dumps(status(), ensure_ascii=False, indent=1))
