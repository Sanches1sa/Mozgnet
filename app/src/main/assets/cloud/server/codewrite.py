# -*- coding: utf-8 -*-
# ===========================================================================
# codewrite.py — «ПИШУ КОД САМА» (v3.41, только stdlib)
# ---------------------------------------------------------------------------
# Раньше режим «Код» умел только заготовки (codegen) и на всё остальное
# отвечал шаблонной фразой «такой код я пока не умею собирать сама».
# Теперь, когда заготовки не подошли, цепочка такая:
#   1) ЛОКАЛЬНАЯ МОДЕЛЬ (OpenAI-совместимый адрес из настроек — Ollama и т.п.,
#      тот же, что у «LLM пишет по базе знаний»): пишет код по просьбе;
#   2) ПРОВЕРКА: синтаксис + lint, для Python — прогон в ПЕСОЧНИЦЕ облака;
#      не прошла — ошибка уходит модели, до двух исправлений;
#   3) ПОХОЖИЙ КОД: в индексе кода (codemind) и реестре функций ищется
#      близкое по смыслу — показывается как есть, с пометкой;
#   4) ничего нет — честное «не смогла», без выдумок.
# Крошечная своя LLM (tinyllm) код писать не может: её токенайзер приводит
# текст к нижнему регистру и теряет отступы и переводы строк.
# ===========================================================================
import json
import re
import urllib.request

import store
import activity

_SYS = ("Ты — опытный программист. Пиши рабочий, короткий и понятный код "
        "под просьбу. Верни ОДИН блок кода в тройных обратных кавычках с "
        "указанием языка и 1–2 строки пояснения по-русски после блока. Для "
        "Python используй только стандартную библиотеку, без чтения файлов и "
        "работы с сетью, если об этом не просили; в конце добавь "
        "`if __name__ == '__main__':` с примером вызова и print(). Не "
        "используй input().")

_FENCE = re.compile(r"```[ \t]*([A-Za-z0-9_+#.-]*)[ \t]*\n([\s\S]*?)```")


def _cfg():
    s = store.settings()
    return (str(s.get("llm_answer_url") or "").strip(),
            str(s.get("llm_answer_model") or "").strip() or "qwen2.5:1.5b")


def available():
    """Есть ли внешняя модель, которой можно поручить код."""
    return bool(_cfg()[0])


def own_ready():
    """Научилась ли СВОЯ LLM коду (в её словаре есть токены кода)."""
    try:
        import tinyllm
        st = tinyllm.status()
        if not st.get("steps") or st.get("numpy") is False:
            return False
        m = tinyllm.model()
        return bool(m.ready) and not getattr(m, "_code_course", False) and any(k.startswith("\u00a2") for k in m.tok.vocab)
    except Exception:
        return False


def _call(messages, timeout=120, max_tokens=1400, temperature=None):
    url, model = _cfg()
    url = url.rstrip("/")
    if not url.endswith("/chat/completions"):
        url += "/chat/completions"
    try:
        import llmzone as _lz
        _t = _lz._br_temp(0.2)
    except Exception:
        _t = 0.2
    body = {"model": model, "messages": messages, "temperature": _t if temperature is None else float(temperature),
            "max_tokens": max_tokens}
    req = urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        d = json.loads(r.read().decode("utf-8", "replace"))
    return str(d["choices"][0]["message"]["content"]).strip()


def _extract(reply, want_lang=""):
    """(язык, код, пояснение) из ответа модели."""
    m = _FENCE.search(reply or "")
    if not m:
        return "", "", ""
    lang = (m.group(1) or want_lang or "").lower()
    code = m.group(2).rstrip()
    note = (reply[m.end():] or "").strip()[:400]
    return lang, code, note


_NORM = {"py": "python", "python3": "python", "js": "javascript",
         "node": "javascript", "ino": "arduino", "cpp": "arduino",
         "c++": "arduino", "sh": "bash", "shell": "bash"}


def _check(lang, code):
    """(ok, текст проверки | причина, критично). Python — песочница."""
    activity.update("Проверяю сгенерированный код…")
    import codegen
    if lang == "python":
        issues = codegen.lint_python(code)[0]
        errs = [m for _l, sev, m in issues if sev == "error"]
        if errs:
            return False, "; ".join(errs[:2]), True
        ok, out = codegen.run_python(code, "", timeout=10)
        if ok:
            return True, ("запустила в песочнице облака — работает; вывод: "
                          "%s" % str(out).strip()[:160].replace("\n", " ⏎ ")), \
                False
        low = str(out)
        if any(k in low for k in ("запрещ", "нечего запускать",
                                  "не вернула результат")):
            return True, ("синтаксис верный (в песочнице не запускала: "
                          "%s)" % low[:90]), False
        return False, low[:200], True
    ok, what = codegen.verify(lang, code, None)
    return ok, what, not ok


def write(task, lang="python"):
    """Написать код по просьбе. Возврат dict(ok, lang, code, check, note,
    how) | None (модель не настроена/не справилась)."""
    if not available():
        return None
    lang = _NORM.get(str(lang or "python").lower(), str(lang or "python").lower())
    msgs = [{"role": "system", "content": _SYS},
            {"role": "user", "content": "Язык: %s.\nЗадача: %s" % (lang, task)}]
    last_err = ""
    for attempt in range(3):
        try:
            reply = _call(msgs)
        except Exception as e:
            return {"ok": False, "error": "модель не ответила: %s: %s"
                    % (type(e).__name__, str(e)[:120])}
        lg, code, note = _extract(reply, lang)
        lg = _NORM.get(lg, lg) or lang
        if not code:
            msgs += [{"role": "assistant", "content": reply},
                     {"role": "user", "content": "Верни код одним блоком в "
                      "тройных обратных кавычках."}]
            last_err = "ответ без блока кода"
            continue
        ok, what, critical = _check(lg, code)
        if ok:
            return {"ok": True, "lang": lg, "code": code, "check": what,
                    "note": note, "tries": attempt + 1}
        last_err = what
        msgs += [{"role": "assistant", "content": reply},
                 {"role": "user", "content": "Код не прошёл проверку: %s. "
                  "Исправь и верни полный исправленный код одним блоком."
                  % what}]
    return {"ok": False, "error": "после трёх попыток проверка не прошла: %s"
            % last_err}


def can_write():
    """Есть чем писать код: внешняя модель, изученные GitHub-функции или
    своя LLM, обученная на коде."""
    try:
        import codecorpus
        if codecorpus.count_snips() > 0:
            return True
    except Exception:
        pass
    return available() or own_ready()


def learned_write(task, lang="python"):
    """Подходящая функция из ИЗУЧЕННОГО кода (GitHub), прошедшая проверку.
    Возврат dict | None."""
    if lang != "python":
        return None
    import codecorpus
    for c in codecorpus.find(task, k=5):
        ok, what, _crit = _check("python", c["code"])
        if ok:
            return dict(c, check=what)
    return None


def _free():
    """v3.175: своя LLM пишет свободно — её код показываем всегда (черновик
    с честной пометкой, если проверку не прошёл)."""
    try:
        return bool(store.settings().get("llm_free", True))
    except Exception:
        return True


def draft_text(o):
    """Текст ответа с черновиком своей LLM (код не прошёл проверку)."""
    head = ("Своя LLM написала код сама" +
            (" (вариант 1 из %d)" % o["of"] if o.get("of") else "") +
            ". Проверку он пока не прошёл: %s — это её черновик, не готовое "
            "решение:" % o.get("why", "?"))
    return "%s\n\n```python\n%s\n```" % (head, o["draft"])


def own_write(task, lang="python", tries=4):
    """СВОЯ LLM пишет код (учится на GitHub-ссылках): кандидаты -> проверка
    (синтаксис, lint, песочница). Возврат dict | None."""
    if lang != "python" or not own_ready():
        return None
    import tinyllm
    activity.update("Своя LLM генерирует кандидаты кода…")
    cands = tinyllm.write_code(task, tries=tries)
    if not cands:
        raw = str(getattr(tinyllm.write_code, "last_raw", "") or "").strip()
        if raw and _free():          # v3.175: показать и незаконченный набросок
            return {"ok": False, "draft": raw, "of": 0,
                    "why": "функция не закончена (набросок оборван)"}
        return {"ok": False, "error": "своя LLM не собрала ни одной "
                                      "законченной функции"}
    first_bad = ""
    for i, code in enumerate(cands):
        ok, what, _crit = _check("python", code)
        if ok:
            store.log("КОД", "своя LLM написала код «%s»: вариант %d из %d прошёл проверку (%s)"
                      % (str(task)[:80], i + 1, len(cands), what))
            return {"ok": True, "lang": "python", "code": code, "check": what,
                    "n": i + 1, "of": len(cands)}
        if not first_bad:
            first_bad = (code, what)
    store.log("КОД", "своя LLM написала черновик «%s» (%d вариантов), проверку не прошёл: %s"
              % (str(task)[:80], len(cands), str(first_bad[1])[:160]))
    return {"ok": False, "draft": first_bad[0], "why": first_bad[1],
            "of": len(cands)}


def similar(task):
    """Похожий код из индекса кода / реестра функций: str | ""."""
    try:
        import codemind
        txt, conf = codemind.ask(task)
        if txt and conf >= 0.5:
            return txt
    except Exception:
        pass
    try:
        import skills
        m = skills.match(task)
        if m and m[1].get("kind") == "code" and m[1].get("code"):
            return "Функция «%s» из реестра:\n```python\n%s\n```" % (
                m[1].get("name") or m[0], m[1]["code"])
    except Exception:
        pass
    return ""


def compose(task, lang="python", try_own=True):
    """Полная цепочка для режима «Код». Возврат текст-ответ | ""."""
    names = {"python": "Python", "javascript": "JavaScript",
             "arduino": "Arduino (C++)", "html": "HTML", "bash": "Bash",
             "sql": "SQL"}
    # Own trained weights get the first attempt; sources are labelled separately.
    o = None
    own_error = ""
    try:
        o = own_write(task, lang) if try_own else None
    except Exception as e:
        own_error = "%s: %s" % (type(e).__name__, str(e)[:120])
    if o and o.get("ok"):
        return ("Код сгенерировала своя LLM (кандидат %d из %d):\n\n"
                "```python\n%s\n```\n\nПроверка: %s. "
                "Соответствие всей задаче нужно проверить отдельно." %
                (o["n"], o["of"], o["code"], o["check"]))
    pre = ""
    if o and o.get("draft") and _free():    # v3.175: её черновик — первым
        pre = draft_text(o) + "\n\n---\n\n**Проверенный вариант:**\n\n"
    activity.update("Проверяю доступность настроенной модели кода…")
    r = write(task, lang)
    if r and r.get("ok"):
        lg = r["lang"]
        return pre + ("Код сгенерировала настроенная OpenAI-совместимая модель "
                "на %s:\n\n```%s\n%s\n```\n\nПроверка: %s.%s" % (
                    names.get(lg, lg), lg, r["code"], r["check"],
                    ("\n\n" + r["note"]) if r.get("note") else ""))
    activity.update("Ищу подходящий пример в изученном коде…")
    try:
        lw = learned_write(task, lang)
    except Exception:
        lw = None
    if lw:
        return pre + ("Пример из изученного кода GitHub %s:\n\n```python\n%s\n```"
                "\n\nПроверка: %s. Это готовый пример, проверь соответствие задаче.%s" % (
                    lw["source"], lw["code"], lw["check"],
                    (" Лицензия: " + lw["lic"]) if lw.get("lic") else ""))
    wc = None
    try:                                  # v3.129: код с настоящих страниц + проверка
        if store.settings().get("web_on"):
            import codeweb
            wc = codeweb.find(task, lang)
    except Exception as e:
        wc = {"ok": False, "why": str(e)[:100]}
    if wc and wc.get("ok"):
        return pre + ("Сама написать не смогла, но нашла готовый код в интернете и "
                "проверила его. Это чужой код — источник: [%s](%s)\n\n"
                "```%s\n%s\n```\n\n✓ Проверка: %s." % (
                    str(wc["title"])[:80], wc["url"], lang, wc["code"], wc["check"]))
    sim = similar(task)
    if sim:
        return pre + ("Точно такой код написать не смогла%s, но у меня есть похожий:"
                "\n\n%s" % (
                    (" (" + r["error"] + ")") if r and r.get("error") else "",
                    sim))
    if pre:                           # v3.175: проверенного нет — её черновик
        return draft_text(o)
    if o and o.get("draft"):
        return ("Своя LLM (учится коду по ссылкам GitHub) набросала черновик, "
                "но проверку он не прошёл: %s. Смотри как набросок, а не как "
                "готовое решение:\n\n```python\n%s\n```" % (
                    o["why"], o["draft"]))
    if r and r.get("error"):
        return ("Попробовала написать, но не вышло: %s. Уточни задачу или "
                "разбей её на части." % r["error"])
    if own_error:
        return "Своя LLM не смогла сгенерировать код: " + own_error
    return ""


def status_text(web_why=""):
    """v3.129: честно — какие нейросети и источники подключены к «Коду»."""
    if not web_why:
        try:
            import codeweb
            web_why = codeweb.LAST.get("why") or ""
        except Exception:
            pass
    lines = ["Что сейчас подключено к разделу «Код»:"]
    url, model = _cfg()
    lines.append("• OpenAI-совместимая модель: " + (
        "настроена — %s, модель %s (доступность проверяется при запросе)" % (url, model) if url else
        "НЕ подключена. Как подключить: поставь Ollama (ollama.com), выполни "
        "`ollama pull qwen2.5-coder:1.5b`, затем в Настройках → API и ответы "
        "укажи адрес `http://localhost:11434/v1` и модель `qwen2.5-coder:1.5b`"))
    lines.append("• Своя LLM облака: " + ("обучена на коде" if own_ready() else
                 "не готова к генерации: нужны NumPy, обученные веса и токены кода"))
    try:
        import codecorpus
        n = codecorpus.count_snips()
    except Exception:
        n = 0
    lines.append("• Изученный код с GitHub: %s" % (
        "%d функций" % n if n else "пусто (дай ссылку на репозиторий — изучу)"))
    lines.append("• Поиск готового кода в интернете с проверкой: " + (
        "включён" if store.settings().get("web_on") else "выключен (интернет выключен)") +
        ((" — " + web_why) if web_why else ""))
    lines.append("• Проверенные заготовки: есть (факториал, сортировка, Arduino и др.)")
    return "\n".join(lines)
