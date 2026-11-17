# -*- coding: utf-8 -*-
# ===========================================================================
# replytpl.py — ШАБЛОННЫЙ ОТВЕТ: ПРИВЕТСТВИЕ + ОТВЕТ (v3.28, только stdlib)
# ---------------------------------------------------------------------------
# Заявка владельца: «шаблонный ответ: приветствие + ответ (если не
# укладывается в одно сообщение, пусть два будут)».
#   * приветствие по времени суток и имени собеседника: «Добрый день,
#     Иван!» (шаблон tpl_greet: {приветствие}, {имя});
#   * ответ, который не влезает в одно сообщение мессенджера, делится на
#     части ПО АБЗАЦАМ/ПРЕДЛОЖЕНИЯМ (не посреди фразы и не посреди блока
#     кода) — до tpl_parts сообщений (по умолчанию 2); только если и двух
#     мало — хвост сокращается с честной пометкой;
#   * если мозг сам уже поздоровался («Привет! …») — второй раз не здороваемся;
#   * v3.30: здороваемся ТОЛЬКО В НАЧАЛЕ ДИАЛОГА (заявка «постоянно
#     здоровается, как будто нет контекста»): приветствие — если в этом
#     чате не было переписки tpl_greet_gap_h часов (по умолчанию 4).
#     Последняя активность чатов хранится на диске (tpl_state.json) —
#     перезапуск облака не делает диалог «новым».
# Где действует: бот МАКС (tpl_bot, вкл. по умолчанию), чат облака
# (tpl_chat, выкл.), API для приложений (поле "template": true).
# ===========================================================================
import re
import time

import store
import textutils

DEFAULTS = {"tpl_bot": True, "tpl_chat": True,
            "tpl_greet": "{приветствие}, {имя}!",
            "tpl_parts": 2, "tpl_greet_hours": 0, "tpl_greet_gap_h": 4}

_GREETED = {}                     # ключ чата -> ts последней активности
_STATE = {"loaded": False, "saved": 0.0}


def _seen_load():
    if not _STATE["loaded"]:
        d = store.load_json("tpl_state.json", None) or {}
        _GREETED.update({str(k): float(v) for k, v in
                         (d.get("seen") or {}).items()})
        _STATE["loaded"] = True


def _seen_save(force=False):
    if force or time.time() - _STATE["saved"] > 20.0:
        _STATE["saved"] = time.time()
        store.save_json("tpl_state.json", {"seen": dict(_GREETED)})


def is_new_dialog(chat_key, now=None):
    """Новый диалог: в чате не было переписки tpl_greet_gap_h часов."""
    _seen_load()
    try:
        gap = float(cfg("tpl_greet_gap_h") or 0) * 3600.0
    except (TypeError, ValueError):
        gap = 4 * 3600.0
    last = _GREETED.get(chat_key, 0.0) if chat_key else 0.0
    return not chat_key or not gap or (now or time.time()) - last >= gap


def touch(chat_key, now=None):
    """Отметить активность чата (и вопрос, и ответ продлевают диалог)."""
    if not chat_key:
        return
    _seen_load()
    _GREETED[chat_key] = now or time.time()
    if len(_GREETED) > 5000:
        for k in sorted(_GREETED, key=_GREETED.get)[:1000]:
            _GREETED.pop(k, None)
    _seen_save()
_ALREADY = re.compile(r"^\s*(?:привет|здравствуй|здравствуйте|добр(?:ое|ый|"
                      r"ой)\s+(?:утро|день|вечер|ночи)|доброго|салют|хай)\b",
                      re.I)
_NOT_NAME = frozenset(["макс", "max", "telegram", "телеграм", "друг", "аноним", "гость", "пользователь", "user",
                       "владелец", "unknown", "?"])


def cfg(key):
    v = store.settings().get(key)
    return DEFAULTS.get(key) if v is None else v


def greeting_word(now=None):
    """Приветствие по времени суток (время сервера облака)."""
    h = time.localtime(now or time.time()).tm_hour
    if 5 <= h < 12:
        return "Доброе утро"
    if 12 <= h < 18:
        return "Добрый день"
    if 18 <= h < 23:
        return "Добрый вечер"
    return "Здравствуйте"


def first_name(name):
    """«Иван Петров» -> «Иван»; служебные имена — без имени."""
    raw = str(name or "")
    if ":" in raw:                # «МАКС: Иван» / «Telegram: Иван» -> «Иван»
        raw = raw.rsplit(":", 1)[-1]
    n = re.sub(r"[^\w\s\-]", " ", raw).strip()
    if not n or n.lower() in _NOT_NAME:
        return ""
    first = n.split()[0]
    if first.lower() in _NOT_NAME or len(first) > 30:
        return ""
    return first[:1].upper() + first[1:]


def greet_line(name="", now=None):
    tpl = str(cfg("tpl_greet") or DEFAULTS["tpl_greet"])[:200]
    nm = first_name(name)
    line = tpl.replace("{приветствие}", greeting_word(now)) \
              .replace("{greeting}", greeting_word(now))
    if nm:
        line = line.replace("{имя}", nm).replace("{name}", nm)
    else:                         # без имени: «Добрый день, {имя}!» -> «Добрый день!»
        line = re.sub(r"[,\s]*\{(?:имя|name)\}", "", line)
    return re.sub(r"\s+([!.,?])", r"\1", line).strip()


def _cut(text, limit):
    """Голова текста не длиннее limit: по абзацу, иначе по предложению."""
    if len(text) <= limit:
        return text, ""
    head = text[:limit]
    para = head.rfind("\n\n")
    if para >= limit * 0.5:
        cut = para
    else:
        sent = textutils.cut_at_sentence(text, limit)
        cut = len(sent) if 0 < len(sent) <= limit and \
            len(sent) >= limit * 0.4 and text.startswith(sent) else -1
        if cut < 0:
            nl = head.rfind("\n")
            sp = head.rfind(" ")
            cut = nl if nl >= limit * 0.5 else (sp if sp > 0 else limit)
    return text[:cut].rstrip(), text[cut:].lstrip()


def split(text, limit=3800, parts=2):
    """Текст -> список сообщений (до parts). Блок кода ``` не рвётся без
    закрытия: в первой части он закрывается, во второй — открывается."""
    text = str(text or "").strip()
    parts = max(1, int(parts or 1))
    out = []
    rest = text
    while rest and len(out) < parts:
        if len(out) == parts - 1 and len(rest) > limit:
            # последняя разрешённая часть: честное сокращение
            note = "\n\n[сокращено — полный ответ в панели облака]"
            head, _tail = _cut(rest, limit - len(note) - 8)
            if head.count("```") % 2:
                head += "\n```"
            out.append(head + note)
            break
        head, rest = _cut(rest, limit - 8)
        if head.count("```") % 2:     # разрезали внутри блока кода
            lang = ""
            m = re.findall(r"```([\w+#.-]*)", head)
            if m:
                lang = m[-1]
            head += "\n```"
            if rest:
                rest = "```" + lang + "\n" + rest
        out.append(head)
    return [p for p in out if p.strip()]


# v3.99.1: невидимая метка «без шаблонного приветствия» — ответ на реплику
# («привет», «спасибо») пишет сам мозг; шаблонное «Добрый день!» к нему не
# приклеивается (раньше на «привет» человек получал именно его)
NO_GREET = "\u2063"


def build(answer, name="", chat_key="", limit=3800, parts=None,
          greet=True):
    """Ответ -> сообщения по шаблону «приветствие + ответ»."""
    text = str(answer or "").strip()
    if text.startswith(NO_GREET):
        greet = False
        text = text.replace(NO_GREET, "").strip()
    if not text:
        return []
    if parts is None:
        try:
            parts = max(1, min(5, int(cfg("tpl_parts") or 2)))
        except (TypeError, ValueError):
            parts = 2
    now = time.time()
    # v3.30: приветствие — только в начале диалога (контекст сохраняется)
    if greet and not _ALREADY.match(text) and "**Приветствие**" not in text[:40] \
            and is_new_dialog(chat_key, now):
        text = greet_line(name, now) + "\n\n" + text
    touch(chat_key, now)
    return split(text, limit=limit, parts=parts)
