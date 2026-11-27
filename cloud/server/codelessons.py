# -*- coding: utf-8 -*-
"""v3.220 — ПРОГРАММНЫЕ УРОКИ: задача -> проверенный шаблон кода.

106 уроков (codelessons.json, собирает tools/build_code_lessons.py). Каждый
шаблон при сборке запущен в чистом подпроцессе вместе со своим assert-тестом.
Это «инструкции для программирования»: мозг находит урок по задаче, берёт
шаблон, проверяет синтаксис и тест — как администрирование берёт инструкции.
Честно: шаблоны покрывают типовые задачи; свободно написать новое по
незнакомой задаче этот модуль не может (это работа обученной LLM).
Уроки идут в базу знаний («Код: …») и в корпус обучения своей LLM."""
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile

import store

_HERE = os.path.dirname(os.path.abspath(__file__))
_CACHE = {"items": None}
MIN_SCORE = 0.34


def load():
    if _CACHE["items"] is None:
        items = []
        try:
            with open(os.path.join(_HERE, "codelessons.json"), encoding="utf-8") as f:
                items = json.load(f)
        except Exception:
            items = []
        ud = os.path.join(store.DATA_DIR, "codelessons")
        if os.path.isdir(ud):
            for fn in sorted(os.listdir(ud)):
                if fn.endswith(".json"):
                    try:
                        extra = json.load(open(os.path.join(ud, fn), encoding="utf-8"))
                        items += [x for x in extra if isinstance(x, dict) and x.get("id") and x.get("code")]
                    except Exception:
                        pass
        _CACHE["items"] = [x for x in items if isinstance(x, dict) and x.get("id") and x.get("code")]
    return _CACHE["items"]


_GEN = {"сдела", "напиш", "созда", "прове", "найди", "найти", "посчит", "нужен", "нужна", "функци", "скрипт", "програ", "код", "почему", "как", "что", "это", "для", "над", "при", "исправ", "ошибк", "помог", "хочу", "надо", "можно", "собер", "запиш", "вычис", "покаж", "дай", "мне", "def", "func", "functi", "python", "питон", "пишет", "напис", "задач", "прим", "пример", "сделай", "напиши", "создай", "проверь", "найди", "посчитай", "собери", "запиши", "покажи", "вычисл", "сделат", "написа", "проверк", "провер", "исправь", "ошибка", "ошибку", "функцию", "функция", "скрипт"}


def _stems(s):
    return {w[:6] for w in re.findall(r"[a-zа-яё0-9]{3,}", str(s).lower())} - _GEN


def match(task, limit=3):
    """[(оценка, урок)] — по пересечению корней слов задачи с формулировками урока."""
    q = _stems(task)
    if not q:
        return []
    out = []
    for it in load():
        best = 0.0
        for ph in list(it.get("ask") or []) + [it.get("title", "")]:
            p = _stems(ph)
            if not p:
                continue
            inter = len(q & p)
            if not inter:
                continue
            s = inter / float(len(p)) * 0.7 + inter / float(len(q)) * 0.3
            best = max(best, s)
        if best >= MIN_SCORE:
            out.append((round(best, 2), it))
    out.sort(key=lambda x: -x[0])
    return out[:limit]


def verify(item, timeout=20):
    """Прогнать шаблон и его тест здесь и сейчас -> (ok, вывод)."""
    src = (item.get("fixcode") or item["code"]) + "\n\n" + str(item.get("test") or "")
    d = tempfile.mkdtemp(prefix="cl_")
    p = os.path.join(d, "t.py")
    with open(p, "w", encoding="utf-8") as f:
        f.write(src)
    exe = sys.executable if not getattr(sys, "frozen", False) else "python"
    try:
        r = subprocess.run([exe, "-I", p], cwd=d, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, timeout=timeout)
        return r.returncode == 0, r.stdout.decode("utf-8", "replace")[-800:]
    except Exception as e:
        return False, "%s: %s" % (type(e).__name__, str(e)[:200])


def lesson_text(it):
    lines = ["Урок программирования: %s." % it["title"],
             "Задача владельца: %s." % "; ".join(it.get("ask") or [])]
    lines.append("Шаблон:\n```python\n%s```" % (it.get("fixcode") or it["code"]))
    if it.get("test"):
        lines.append("Проверка:\n```python\n%s```" % it["test"])
    lines.append("Статус: шаблон и проверка запущены при сборке (%s)." % (it.get("tested_on") or "не запускались")
                 if it.get("tested") else "Статус: не проверено.")
    return "\n".join(lines)


def _did(it):
    return "code_" + hashlib.md5(it["id"].encode("utf-8")).hexdigest()[:12]


def seed_kb():
    import kb
    n = 0
    for it in load():
        r = kb.add_article("Код: " + it["title"], lesson_text(it), source="codelessons", did=_did(it))
        if r.get("ok"):
            n += 1
    return n


def corpus():
    """[(подпись, текст)] для потока обучения своей LLM."""
    out = []
    for it in load():
        t = lesson_text(it)
        out.append(("cl-l-" + hashlib.md5(t.encode("utf-8")).hexdigest()[:14], t))
        for ph in (it.get("ask") or [])[1:3]:
            t2 = "Задача владельца: %s\n```python\n%s```" % (ph, it.get("fixcode") or it["code"])
            out.append(("cl-e-" + hashlib.md5(t2.encode("utf-8")).hexdigest()[:14], t2))
    return out


def compose(task):
    """Ответ раздела «Код» из урока: ```python …``` + пометка, откуда. None — урока нет."""
    m = match(task, 1)
    if not m:
        return None
    s, it = m[0]
    return "Это типовая задача — беру проверенный шаблон «%s» (совпадение %.2f).\n```python\n%s```" % (
        it["title"], s, it.get("fixcode") or it["code"])


def status():
    items = load()
    tags = {}
    for it in items:
        tags[it.get("tags", "")] = tags.get(it.get("tags", ""), 0) + 1
    return {"count": len(items), "tested": sum(1 for x in items if x.get("tested")), "tags": tags}
