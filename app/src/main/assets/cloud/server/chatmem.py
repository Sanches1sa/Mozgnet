# -*- coding: utf-8 -*-
# ===========================================================================
# chatmem.py — ПАМЯТЬ ИНСТРУКЦИЙ ИЗ ЧАТА МАКС (v3.142, только stdlib)
# ---------------------------------------------------------------------------
# Заявка владельца: «инструкции из Макс чата (после скана) собирались в JSON
# и бот Макс к нему обращался тоже, как к памяти, чтобы отвечать на вопросы
# пользователя, а то он не обращается».
#
# Как это работает:
#   1. СБОР: всё, что бот выучил из чатов МАКС (живые сообщения группы,
#      скан истории, документы) и похоже на ИНСТРУКЦИЮ — команды
#      (sudo/dnf/systemctl/…, пути, ключи), пошаговые рецепты, «как …» —
#      попадает в JSON-файл data/maxbot_instructions.json (dedup по md5).
#      Сбор идёт и в живом режиме, и отдельным ПРОХОДОМ ПО БАЗЕ ПОСЛЕ
#      СКАНА (harvest) — заявка «после скана собирались в JSON».
#   2. ОТВЕТ: на каждый вопрос пользователя бот ищет в этой памяти
#      инструкции по теме (стемы слов вопроса ∩ стемы инструкции,
#      бонус командным инструкциям при просьбе «как/установи/почини…»)
#      и дописывает найденное к ответу отдельным разделом
#      «📋 Из инструкций чата» — РАНЬШЕ бот в эту память не заглядывал
#      и команды из чата не показывал.
#   3. Оформление английского (ссылка/команда) и защита последней буквы —
#      на стороне maxbot.py при отправке; эта память хранит ЧЕСТНЫЙ текст.
#
# Файл переживает перезапуск облака; «Стереть выученное из чатов» (панель)
# чистит и её (maxbot.wipe_learned -> wipe()).
# ===========================================================================
import hashlib
import re
import threading
import time

import store
import textutils

FILE = "maxbot_instructions.json"     # в data/ облака
LIMIT = 600                           # максимум инструкций (старые — вылетают)
TEXT_MAX = 4000                       # максимум знаков текста одной инструкции
SNIPPET = 600                         # максимум знаков инструкции в ответе

_LOCK = threading.RLock()             # RLock: _load() зовётся под замком
_STATE = None

# «как установить», «почини сборку», «какую команду…» — вопрос-просьба
_INTENT = re.compile(
    r"\b(как|каким|команд\w*|установ\w+|настро\w+|почин\w+|исправ\w+|"
    r"запуст\w+|обнов\w+|включ\w+|выключ\w+|перезапусти\w*|перезагруз\w*|"
    r"провер\w+|удал\w+|собер\w+|прошив\w+|скрипт\w*|инструкц\w+|шаг\w*)\b",
    re.I)

# команды/пути/файлы в тексте инструкции (как maxbot._looks_cmd, своя копия —
# без циклического импорта)
_CMD_LINE = re.compile(
    r"^\s*(?:\$|#|>)?\s*(?:sudo|apt|apt-get|dnf|yum|rpm|systemctl|service|"
    r"chmod|chown|mount|umount|ip|ifconfig|nmcli|ping|ssh|scp|cat|grep|"
    r"sed|awk|ls|cd|cp|mv|rm|mkdir|tar|zip|unzip|7z|wget|curl|pip|python3?|"
    r"docker|kubectl|git|journalctl|dmesg|lsblk|fdisk|df|du|ps|kill|"
    r"reboot|shutdown|echo|export|nano|vi|vim|arduino-app-cli|nm|netsh|"
    r"reg|sfc|dism|powershell|ipconfig|setenforce|firewall-cmd|ufw|"
    r"iptables|useradd|hostnamectl|grub2-mkconfig|dracut|modprobe)\b", re.I)

_URL_RE = re.compile(r"https?://\S+", re.I)


def _cmd_like(text):
    """Похоже на инструкцию/команду: есть командная строка, путь, ключ или
    пошаговый рецепт. Голая ссылка и болтовня — нет."""
    t = str(text or "").strip()
    if not t or _URL_RE.fullmatch(t):
        return False
    lines = [ln.strip() for ln in t.split("\n") if ln.strip()]
    if any(_CMD_LINE.match(ln) for ln in lines):
        return True
    if "```" in t or re.search(r"(?<!\w)sudo\b|/usr/|/etc/|~/|\.service\b|"
                               r"\.sh\b|\.py\b|\.conf\b|systemctl|dnf install|"
                               r"apt install|systemctl restart", t, re.I):
        return True
    steps = sum(1 for ln in lines
                if re.match(r"^\d+[.)]\s+\S", ln) or
                re.match(r"^\s*[-*•]\s+\S", ln))
    if steps >= 2:
        return True
    letters = [ch for ch in t if ch.isalpha()]
    if len(letters) >= 4:
        lat = sum(1 for ch in letters if ch.isascii())
        if lat / float(len(letters)) >= 0.75 and (
                "/" in t or "-" in t or "=" in t):
            return True
    return False


def _looks_instr_title(t):
    """Начало текста как заголовок (до точки/перевода строки)."""
    t = str(t or "").strip()
    sp = t.find(". ")
    head = t[:sp + 1] if 0 < sp < 90 else t[:80]
    return head.strip()


# ------------------------------- хранилище -----------------------------------
def _load():
    global _STATE
    if _STATE is None:
        with _LOCK:
            if _STATE is None:
                st = store.load_json(FILE, None)
                if not isinstance(st, dict):
                    st = {"items": [], "updated": 0.0}
                if not isinstance(st.get("items"), list):
                    st["items"] = []
                _STATE = st
    return _STATE


def _save():
    with _LOCK:
        st = _STATE or {"items": [], "updated": 0.0}
        st["items"] = (st.get("items") or [])[-LIMIT:]
        st["updated"] = time.time()
        try:
            store.save_json(FILE, st)
        except Exception:
            pass


def remember(text, source="", chat_id="", title=""):
    """Инструкция в память (dedup по md5 текста). Возврат True — добавлена."""
    t = re.sub(r"[ \t]+", " ", str(text or "")).strip()
    if len(t) < 6:
        return False
    if _URL_RE.fullmatch(t):
        return False
    if not _cmd_like(t):
        return False
    try:                                  # v3.144: вопрос — не инструкция
        if textutils.question_only(t):
            return False
    except Exception:
        pass
    t = t[:TEXT_MAX]
    iid = hashlib.md5(t.encode("utf-8", "replace")).hexdigest()
    with _LOCK:
        st = _load()
        items = st.setdefault("items", [])
        if any(it.get("id") == iid for it in items):
            return False
        items.append({"id": iid, "text": t,
                      "title": str(title or "").strip()[:120] or
                      _looks_instr_title(t),
                      "chat": str(chat_id or "")[-40:],
                      "src": str(source or "")[:160],
                      "ts": time.time()})
        del items[:-LIMIT]
    _save()
    return True


def wipe():
    """Стереть всю память инструкций (вместе со «Стереть выученное»)."""
    global _STATE
    with _LOCK:
        _STATE = {"items": [], "updated": time.time()}
    _save()


def count():
    try:
        with _LOCK:
            return len(_load().get("items") or [])
    except Exception:
        return 0


def stats():
    """Для панели/статуса бота."""
    try:
        with _LOCK:
            st = _load()
            items = st.get("items") or []
            last = max([float(it.get("ts") or 0) for it in items] or [0.0])
            return {"count": len(items), "updated": st.get("updated") or 0.0,
                    "last": last}
    except Exception:
        return {"count": 0, "updated": 0.0, "last": 0.0}


# ------------------------- сбор после скана (harvest) -------------------------
def harvest():
    """Пройти по базе знаний и добавить в память инструкции из всего, что
    бот выучил из МАКС (источники «группа МАКС», «история чата…», МАКС).
    Вызывается после скана истории (maxbot._scan_worker) — заявка владельца
    «после скана собирались в JSON». Возврат: сколько ДОБАВЛЕНО новых."""
    added = 0
    try:
        import kb
        for a in kb.list_articles(limit=100000):
            src = str(a.get("source") or "").lower()
            if not any(w in src for w in ("макс", "история чата", "группа")):
                continue
            art = kb.article(a.get("id"), a.get("device") or None) or {}
            body = str(art.get("text") or "").strip()
            if not body:
                continue
            # статью можно разложить: командные строки — сами, остальное —
            # целиком, если похоже на инструкцию
            if _cmd_like(body):
                if remember(body, source="статья из чата МАКС",
                            title=art.get("title") or ""):
                    added += 1
            else:
                lines = [ln for ln in body.split("\n")
                         if ln.strip() and (_CMD_LINE.match(ln.strip()) or
                                            _cmd_like(ln.strip()))]
                for ln in lines[:8]:
                    if remember(ln, source="команда из чата МАКС",
                                title=art.get("title") or ""):
                        added += 1
    except Exception as e:
        store.log("ПАМЯТЬ", "сбор инструкций после скана: %s: %s"
                  % (type(e).__name__, e))
    if added:
        store.log("ПАМЯТЬ", "инструкций из чатов МАКС в JSON-памяти: %d"
                  % count())
    return added


# ------------------------------ поиск по вопросу ------------------------------
_WORD = re.compile(r"[а-яёa-z0-9_][а-яёa-z0-9_-]{2,}", re.I)
_QSTOP = frozenset("""
какой какая какое какие эта этот это есть было были быть очень чтобы
тогда сейчас здесь если или а но и в во не ни на за по из от до у о об
что как кто где когда почему зачем сколько можно нужно надо мне тебе
нам вас их его её ее мой моя твоей свой тема вопрос ответ скажи скажите
подскажи пожалуйста спасибо привет давай давайте хочу хочу ещё еще
tell what who how why when where the and you your please about
""".split())


def _stems(text):
    out = []
    for w in _WORD.findall(str(text or "").lower()):
        if w in _QSTOP or len(w) < 3:      # «кэш», «мак» — тоже темы
            continue
        s = textutils.stem(w)
        if len(s) >= 3:
            out.append(s)
    return out


# v3.145: слова ПРОСЬБЫ, а не темы: «напиши команду для терминала для
# установки … на линуксе» — тема тут «7zip, архиватор», а не «команда,
# терминал, установка, линукс». Раньше совпадение по одному «команд» давало
# в ответ случайную инструкцию (про SELinux на вопрос про 7zip).
_GENERIC = frozenset(textutils.stem(w) for w in """
команда команды командой команду терминал терминала консоль консоли
установка установки установить установит поставить ставить скачать
линукс линуксе linux системе система системы сделать напиши напишите
нужно нужна нужен надо как помоги помогите подскажи подскажите способ
пакет пакета программа программу файл файла работает работать запустить
включить выключить проверить настроить настройка вопрос инструкция
""".split())


def match(q, limit=2):
    """Инструкции по теме вопроса: [{id, text, title, chat, src, ts, score}].
    Оценка: число общих стемов вопроса и инструкции; бонус +0.5 командным
    инструкциям при вопросе-просьбе («как установить…»). Порог честный:
    минимум 2 общих содержательных слова (или 1 при сильной командности)."""
    qs = _stems(q)
    if not qs:
        return []
    qset = set(qs)
    intent = bool(_INTENT.search(str(q or "")))
    scored = []
    try:
        with _LOCK:
            items = list(_load().get("items") or [])
    except Exception:
        items = []
    for it in items:
        txt = str(it.get("text") or "")
        try:                              # v3.144: старые записи-вопросы
            if textutils.question_only(txt):
                continue
        except Exception:
            pass
        title = str(it.get("title") or "")
        ist = set(_stems(title)) | set(_stems(txt[:1200]))
        ov = qset & ist
        topic = ov - _GENERIC              # v3.145: совпадение именно по теме
        if not topic:
            continue
        sc = float(len(topic)) + 0.25 * len(ov - topic)
        if intent and _cmd_like(txt):
            sc += 0.5
        if title and set(_stems(title)) & qset:
            sc += 0.3                                   # тема в заголовке
        need = 2.0 if len(qset - _GENERIC) >= 3 else 1.0
        if sc >= need or (intent and _cmd_like(txt) and sc >= 1.0):
            r = dict(it)
            r["score"] = round(sc, 2)
            r["words"] = sorted(ov)[:6]
            scored.append(r)
    scored.sort(key=lambda r: (-r["score"], -float(r.get("ts") or 0)))
    return scored[:max(1, int(limit or 2))]


def memory_line(q, limit=2):
    """Раздел ответа «📋 Из инструкций чата» по теме вопроса; "" — памяти
    нет или ничего не совпало. Командные строки бот оформит `кодом` при
    отправке (английский в посте — ссылка или команда)."""
    hits = match(q, limit=limit)
    if not hits:
        return ""
    ln = ["📋 Из инструкций чата:"]
    for h in hits:
        t = str(h.get("text") or "").strip()
        if len(t) > SNIPPET:
            t = t[:SNIPPET].rstrip() + "…"
        rows = t.split("\n")
        ln.append("• " + rows[0])
        for r in rows[1:]:
            ln.append("   " + r)
        src = str(h.get("src") or "").strip()
        if src:
            ln.append("   (%s)" % src[:120])
    return "\n".join(ln)
