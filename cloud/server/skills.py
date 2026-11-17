# -*- coding: utf-8 -*-
# ===========================================================================
# skills.py — v3.170: НАВЫКИ УПРАЗДНЕНЫ -> App Lab на Python (pyapps.py).
# Этот модуль — совместимость: те же функции (match, run_skill, upsert,
# public_list…), но всё хранится и исполняется как Python-приложения App Lab.
# Здесь остались только встроенные обработчики (run_builtin), которые
# приложения зовут через ctx.builtin(...).
# Старое описание:
# skills.py — РЕЕСТР ФУНКЦИЙ МОЗГА (расширение возможностей neuro_12)
# ---------------------------------------------------------------------------
# Новые возможности мозга ДОБАВЛЯЮТСЯ НА ОБЛАКЕ — мозг сам скачивает список
# (GET /api/skills), изучает новые функции и начинает их использовать в чате.
# Виды функций (kind):
#   builtin   — исполняет сам сервер (обработчики ниже: math, time, wiki,
#               память «запомни/вспомни», статус облака);
#   knowledge — «знает» мозг сам: ответ из шаблона reply с подстановками
#               {text} и {name}. v3.12: сид-автоответов «личности» больше
#               нет — вид остаётся для ВЫУЧЕННЫХ фраз (от плат/владельца);
#   code      — Python-модуль с run(text, ctx), код хранится в
#               skills/code/<id>.py, исполняется песочницей (sandbox.py).
# Публикация мозгом: мозг сам пишет функцию (самокод из заготовок),
# проверяет в песочнице облака и публикует POST /api/skills — реестр принимает.
# ===========================================================================
import ast
import json
import os
import re
import time

import sandbox
import store
import webfetch

REG = os.path.join("skills", "registry.json")


def _load():
    return store.load_json(REG, {}) or {}


def _save(reg):
    store.save_json(REG, reg)


# --------------------------- builtin-обработчики ----------------------------
_AST_OPS = {ast.Add: "+", ast.Sub: "-", ast.Mult: "*", ast.Div: "/",
            ast.Pow: "**", ast.Mod: "%", ast.FloorDiv: "//",
            ast.USub: "-", ast.UAdd: "+"}


def _safe_math(expr):
    """Безопасный калькулятор через AST (без eval произвольного кода)."""
    expr = expr.replace(",", ".").replace("х", "*").replace("×", "*") \
               .replace("÷", "/").replace("^", "**")

    def ev(node):
        if isinstance(node, ast.Expression):
            return ev(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value,
                                                         (int, float)):
            return node.value
        if isinstance(node, ast.BinOp) and type(node.op) in _AST_OPS:
            l, r = ev(node.left), ev(node.right)
            if isinstance(node.op, ast.Pow) and abs(r) > 1000:
                raise ValueError("слишком большая степень")
            return {"+": lambda: l + r, "-": lambda: l - r,
                    "*": lambda: l * r, "/": lambda: l / r if r else 0 / 0,
                    "**": lambda: l ** r, "%": lambda: l % r,
                    "//": lambda: l // r}[ _AST_OPS[type(node.op)] ]()
        if isinstance(node, ast.UnaryOp) and type(node.op) in _AST_OPS:
            v = ev(node.operand)
            return -v if _AST_OPS[type(node.op)] == "-" else +v
        raise ValueError("не поддерживается")

    tree = ast.parse(expr.strip(), mode="eval")
    val = ev(tree)
    if isinstance(val, float) and val.is_integer():
        val = int(val)
    return val


_NUM_RE = re.compile(r"-?\d+(?:\.\d+)?(?:\s*[-+*/%]|\s*\*\*)\s*-?\d+")


def run_builtin(sid, text, board=""):
    """Встроенные функции. Возврат: {ok, result} | None (не builtin)."""
    text = str(text or "").strip()
    if sid == "math":
        # v3.27: полноценный калькулятор (всё выражение, проценты,
        # уравнения); старый разбор ниже — запасной
        try:
            import calc
            # v3.28: решает САМ calc (раньше к фразе дописывалось
            # «посчитай», и «Windows 10 - 11 разница» считалось как «-1»:
            # тире/дефис между числами — диапазон, а не минус)
            ca = calc.answer(text)
            if ca:
                return {"ok": True, "result": ca[0]}
            if not calc._INTENT.search(text.lower()):
                return {"ok": False, "error": "это не просьба посчитать"}
        except Exception:
            pass
        # словесные операторы: «2 плюс 2», «7 умножить на 6» и т.п.
        t_expr = text.lower().replace("умножить на", "*").replace(
            "разделить на", "/").replace("умножить", "*").replace(
            "разделить", "/").replace("плюс", "+").replace("минус", "-") \
            .replace("х", "*").replace("×", "*").replace("÷", "/")
        m = _NUM_RE.search(t_expr)
        if not m:
            return {"ok": False, "error": "нет выражения для счёта"}
        try:
            val = _safe_math(m.group(0))
            return {"ok": True, "result": "%s = %s"
                    % (m.group(0).replace("**", "^"), val)}
        except Exception as e:
            return {"ok": False, "error": "не смог посчитать: %s" % e}
    if sid == "time":
        now = time.localtime()
        days = ["понедельник", "вторник", "среда", "четверг", "пятница",
                "суббота", "воскресенье"]
        months = ["января", "февраля", "марта", "апреля", "мая", "июня",
                  "июля", "августа", "сентября", "октября", "ноября",
                  "декабря"]
        return {"ok": True, "result": "Сейчас %02d:%02d, %s, %d %s %d года "
                "(время сервера)." % (now[3], now[4], days[now[6]],
                                     now[2], months[now[1] - 1], now[0])}
    if sid == "wiki":
        q = re.sub(r"^(что такое|кто такой|кто такая|найди|поиск|погугли)\s+",
                   "", text, flags=re.I).strip(" ?.!") or text
        # v3.13: навык тоже отвечает «как у Алиса» — несколько источников
        try:
            reply, _meta = webfetch.alice(q)
        except Exception as e:
            return {"ok": False,
                    "error": "интернет-шлюз недоступен: %s"
                             % webfetch.honest(e)}
        if reply:
            return {"ok": True, "result": reply}
        tail = "; ".join(webfetch.diag_tail()[:1])
        return {"ok": False, "error": "Википедия не ответила или пусто"
                + ((" — " + tail) if tail else "")}
    if sid == "memory":
        low = text.lower()
        if low.startswith(("запомни", "запиши")):
            note = re.sub(r"^(запомни|запиши)[,:]?\s*", "", low).strip()
            if not note:
                return {"ok": False, "error": "что запомнить?"}
            store.notes_add(note, board or "владелец")
            return {"ok": True, "result": "Запомнил: «%s»." % note[:120]}
        if low.startswith(("вспомни", "что помнишь")):
            notes = store.notes()[-5:]
            if not notes:
                return {"ok": True, "result": "Заметок пока нет."}
            return {"ok": True, "result": "Помню:\n" + "\n".join(
                "• %s" % n["text"][:120] for n in reversed(notes))}
        if low.startswith(("забудь", "забудь всё")):
            q = re.sub(r"^(забудь)\s*", "", low).strip()
            n = store.notes_clear_matching(q)
            return {"ok": True, "result": "Удалил заметок: %d." % n}
        return {"ok": False, "error": "скажи «запомни …» или «вспомни»"}
    if sid == "llm_extend":
        # v3.36: мозг зовёт свою LLM облака развить мысль по фактам базы
        import llmlink
        low = text.lower()
        for kw in ("развей мысль", "разверни мысль", "продолжи мысль",
                   "расширь мысль", "подумай вместе"):
            low = low.replace(kw, "")
        r = llmlink.extend(low.strip(" :,.-") or "", source="функция мозга")
        if not r.get("ok"):
            return {"ok": False, "error": r.get("reason") or "не вышло"}
        return {"ok": True, "result": "%s\n\n(развитие мысли своей LLM по "
                "фактам базы знаний, опора %d%%)" % (
                    r["text"], int(100 * r["grounded"]))}
    if sid == "status":
        st = store.stats()
        return {"ok": True, "result": "Облако живо: чатов %d, сообщений %d, "
                "статей KB %d, функций %d, слов в словарях %s, плат онлайн "
                "%d из %d." % (st["chats"], st["messages"], st["kb_articles"],
                               st["skills"], st["dict_words"],
                               st["boards_online"], st["boards_total"])}
    return None


# ------------------------------- сид-функции --------------------------------
# v3.12: АВТОМАТИЧЕСКИЕ ОТВЕТЫ УБРАНЫ (заявка владельца). Раньше здесь
# жили seed-заготовки «личности» (привет, кто ты, что умеешь, как дела,
# спасибо, пока, анекдот) — готовые фразы-шаблоны kind=knowledge. Теперь
# чат отвечает ТОЛЬКО честно: нейросеть собирает фразы из слов словаря
# (wordmind) или честно говорит «не знаю». Остаются ЧЕСТНЫЕ вычисления:
# математика, время, поиск, память, статус и демо-код.
# Вид knowledge остаётся доступным платам/владельцу (ВЫУЧЕННАЯ фраза —
# не автоматическая: её кто-то дал мозгу сам).
SEEDS = [
    {"id": "math", "name": "Математика", "kind": "builtin",
     "keywords": ["посчитай", "сколько", "плюс", "минус", "умножить",
                  "разделить", "процент", "корень"],
     "desc": "Считает арифметику: «посчитай 7*6», «сколько 144/12»."},
    {"id": "time", "name": "Время и дата", "kind": "builtin",
     "keywords": ["время", "который час", "дата", "день недели", "сегодня"],
     "desc": "Говорит время и дату (время сервера облака)."},
    {"id": "wiki", "name": "Википедия", "kind": "builtin",
     "keywords": ["википедия", "что такое", "кто такой", "найди", "поиск",
                  "погугли", "расскажи про"],
     "desc": "Поиск в Википедии через шлюз облака (SSRF-защита)."},
    {"id": "memory", "name": "Память владельца", "kind": "builtin",
     "keywords": ["запомни", "вспомни", "забудь", "что помнишь"],
     "desc": "Заметки «запомни/вспомни/забудь» — память владельца в облаке."},
    {"id": "llm_extend", "name": "LLM: развить мысль", "kind": "builtin",
     "keywords": ["развей мысль", "разверни мысль", "продолжи мысль",
                  "расширь мысль", "подумай вместе"],
     "desc": "Своя LLM облака развивает мысль мозга по фактам базы знаний "
             "(«развей мысль <текст>»; без текста — последняя мысль платы)."},
    {"id": "status", "name": "Статус облака", "kind": "builtin",
     "keywords": ["статус", "как дела у облака", "устройства", "диагностика"],
     "desc": "Здоровье платформы: чаты, KB, функции, платы онлайн."},
    {"id": "hex", "name": "Демо-код: HEX", "kind": "code",
     "keywords": ["hex", "шестнадцатеричное", "hex-код"],
     "desc": "Пример code-навыка: переводит число в шестнадцатеричный вид.",
     "code": ("def run(text, ctx=None):\n"
              "    import re\n"
              "    m = re.search(r'-?\\d+', str(text or ''))\n"
              "    if not m:\n"
              "        return 'Напиши число: «hex 255».'\n"
              "    n = int(m.group(0))\n"
              "    return '%d (10) = 0x%X (16)' % (n, n)\n"),
     "origin": "seed"},
]


# ------------------------- совместимость с App Lab -------------------------
import pyapps


def _as_skill(m):
    return {"id": m["id"], "name": m.get("name"), "kind": "code",
            "desc": m.get("desc"), "keywords": m.get("triggers") or [],
            "ver": m.get("ver", 1), "ts": m.get("ts"), "origin": m.get("origin"),
            "enabled": m.get("enabled"), "icon": m.get("icon")}


def _load():
    return {m["id"]: _as_skill(m) for m in pyapps.apps()}


def run_skill(sid, text, board=""):
    r = pyapps.run(sid, text, board=board)
    if r.get("ok"):
        return {"ok": True, "result": r.get("result") or "", "skill": r.get("app")}
    return {"ok": False, "error": r.get("error") or "не вышло", "result": r.get("result")}


def match(text):
    hit = pyapps.match(text)
    if hit:
        return hit[0], _as_skill(hit[1])
    m = pyapps.get("math")                # выражение без слова «посчитай»
    if m and m.get("enabled") and _NUM_RE.search(str(text or "")):
        try:
            import calc
            low = str(text or "").lower()
            if calc.answer(text) and (calc._INTENT.search(low) or
                                      re.fullmatch(r"[\d\s.,+\-*/^()=?]+", low.strip())):
                return "math", _as_skill(m)
        except Exception:
            pass
    return None


def upsert(payload):
    """POST /api/skills (плата/автокод): -> приложение App Lab. Код от платы
    сохраняется ВЫКЛЮЧЕННЫМ — включает владелец в App Lab."""
    origin = str(payload.get("origin") or "владелец")[:24]
    kind = str(payload.get("kind") or "knowledge")
    if kind == "code":
        code = str(payload.get("code") or "")
    elif kind == "knowledge":
        code = ("def run(text, ctx):\n    return %r.replace('{text}', text)\n"
                % str(payload.get("reply") or "")[:600])
    else:
        return {"ok": False, "error": "встроенные функции теперь — приложения App Lab"}
    trusted = origin in ("владелец", "мозг", "ИИ (локальная модель)")
    r = pyapps.save({"id": str(payload.get("id") or "").lower(), "name": payload.get("name"),
                     "desc": payload.get("desc"), "triggers": payload.get("keywords") or [],
                     "code": code, "enabled": trusted, "icon": "🤖" if origin == "мозг" else None},
                    origin=origin)
    if r.get("ok") and not trusted:
        store.log("APP LAB", "плата прислала приложение «%s» — сохранено выключенным, "
                  "включите в App Lab, если код в порядке" % (payload.get("name") or r["id"]))
    return {"ok": bool(r.get("ok")), "id": r.get("id"), "error": r.get("error"),
            "ver": (r.get("app") or {}).get("ver")}


def delete_skill(sid):
    return pyapps.delete(sid)


def learned(board, sid, ver=1):
    store.log("APP LAB", "плата %s изучила приложение «%s» (v%s)" % (board, sid, ver))
    return {"ok": True}


def public_list():
    return [{"id": m["id"], "name": m.get("name"), "keywords": m.get("triggers"),
             "kind": "code", "desc": m.get("desc"), "ver": m.get("ver", 1)}
            for m in pyapps.apps() if m.get("enabled")]


def full_list():
    return [dict(_as_skill(m), code=(pyapps.get(m["id"], True) or {}).get("code", ""))
            for m in pyapps.apps()]


def seed():
    """v3.170: вместо сид-навыков — перенос в App Lab (pyapps.migrate)."""
    if not os.path.exists(store.p("skills", "registry.json")) and not pyapps.apps():
        reg = {}                      # первый старт: старые сиды -> приложения
        for s_ in SEEDS:
            reg[s_["id"]] = dict(s_)
        store.ensure_dirs()
        for s_ in SEEDS:
            if s_.get("code"):
                with open(store.p("skills", "code", s_["id"] + ".py"), "w",
                          encoding="utf-8") as f:
                    f.write(s_["code"])
        store.save_json(REG, reg)
    return len(pyapps.migrate())


