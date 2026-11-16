# -*- coding: utf-8 -*-
# ===========================================================================
# unify.py — ЕДИНЫЙ МОЗГ: ОБЛАКО И ПЛАТА ДОПОЛНЯЮТ ДРУГ ДРУГА (v3.29)
# ---------------------------------------------------------------------------
# Раньше облако спрашивало плату, только если в веб-чате стоял маршрут
# «авто» и облако сказало «Не знаю»; ответ платы (часто фраза-заготовка
# зоны) уходил только в файл веб-чата, бот МАКС и API плату не спрашивали.
# Теперь, честно и во всех каналах:
#   1) ОБЛАКО -> ПЛАТА. Ответ облака СЛАБЫЙ (догадка, пересказ своей сети,
#      речь из словаря, «не знаю») и плата в сети — вопрос уходит плате с
#      пометкой «единый мозг». Плата v2.41 отвечает СТРУКТУРНО: текст +
#      «есть ли опора» (подтверждённый ответ, её файл, память кода, живое
#      состояние платы) + источник. Ответ платы:
#        * пришёл, пока канал ждёт (unify_wait, МАКС и API) — вливается в
#          тот же ответ («Плата «X» дополняет…»);
#        * пришёл позже — дописывается отдельным сообщением: веб-чат —
#          всегда (с честной пометкой), МАКС — только С ОПОРОЙ (заготовки
#          зон в мессенджер не шлём), API — забирается POST /api/v1/result;
#        * с опорой — облако учится: пара «вопрос/ответ» в qalearn.
#   2) ПЛАТА -> ОБЛАКО. Весь мозг облака плате — POST /api/brain/think
#      (v3.28). Плата шлёт свои подтверждённые ответы и память кода —
#      POST /api/brain/learn; забирает подтверждённое облаком —
#      GET /api/brain/knowledge. Подтверждённое в одном месте знают оба.
#   3) Мысли облака (режим «Разговор») уходят плате: пин D6 + материал её
#      сети (как мысли зон, настройка d6_on).
# С v3.46 материалы облака и память плат живут в ОДНОМ графе (onenet.py);
# веса сетей на устройствах (тензорная сеть платы) остаются на платах.
# ===========================================================================
import re
import threading
import time

import store

_WAIT = {}                 # mid -> {"ev": Event, "res": dict|None}
_LOCK = threading.Lock()
_STATS_FILE = "unify.json"
_ST = {"loaded": False, "c": {}}
_KEYS = ("asked", "answered", "grounded", "merged", "late", "dropped",
         "pairs_in", "pairs_out", "code_in", "thoughts_out")


def _stats():
    if not _ST["loaded"]:
        d = store.load_json(_STATS_FILE, None) or {}
        _ST["c"] = {k: int((d.get("c") or {}).get(k) or 0) for k in _KEYS}
        _ST["last"] = d.get("last") or {}
        _ST["loaded"] = True
    return _ST


def bump(key, n=1, last=None):
    with _LOCK:
        st = _stats()
        st["c"][key] = st["c"].get(key, 0) + n
        if last:
            st["last"] = dict(last, ts=time.time())
        store.save_json(_STATS_FILE, {"c": st["c"], "last": st.get("last")})


def cfg(key, default):
    v = store.settings().get(key)
    return default if v is None else v


def is_weak(reply, zone):
    """Ответ облака без опоры: догадка, пересказ своей сети, речь из
    словаря, честное «не знаю»."""
    import brain
    z = str(zone or "")
    if z in (brain.Z["hypo"], brain.Z["mn"], brain.Z["speech"]):
        return True
    head = str(reply or "").lstrip()[:220]
    return z in (brain.Z["sys"], brain.Z["wiki"]) and bool(_NOANS.search(head))


# «не знаю» облака бывает разными словами
_NOANS = re.compile(r"^Не знаю|ничего не нашлось|не нашла|не нашлось|"
                    r"нет знаний|пока не знаю", re.I)


# ------------------------------- облако -> плата -----------------------------
def ask(text, deliver, wait=0.0, thought=False):
    """Спросить плату как часть единого мозга. deliver — куда дописать
    поздний ответ: {"kind": "chat", "chat_id"} | {"kind": "max", "chat_id",
    "name"} | {"kind": "api", "key", "session"}. wait — сколько секунд ждать
    ответ, чтобы влить его в этот же ответ.
    Возврат: {"answer"...} (успел) | {"pending": mid, "brain"} | None."""
    import brain
    if not cfg("unify_on", True):
        return None
    try:                  # v3.98: прямой вопрос мозгу по мосту — сразу ответ
        import bridge
        dr = bridge.think(text, timeout=min(25.0, max(5.0, float(wait or 15))))
        if dr.get("ok") and dr.get("answer"):
            bump("asked", last={"q": text[:120], "board": dr.get("board")})
            bump("answered")
            return {"answer": dr["answer"], "zone": "Мысль",
                    "brain": dr.get("board"), "grounded": False,
                    "source": "мысль мозга", "thought": True,
                    "q": text, "deliver": deliver or {}}
        if dr.get("ok"):
            # v3.99: плата на связи по мосту и сама сказала, что по теме ей
            # нечего добавить — второе письмо той же плате не шлём
            bump("asked", last={"q": text[:120], "board": dr.get("board")})
            return None
    except Exception:
        pass
    if not store.free_boards():
        return None
    r = brain.ask_board(text, chat_id=(deliver or {}).get("chat_id")
                        if (deliver or {}).get("kind") == "chat" else None,
                        frm="единый мозг", long_mode=True, unify=True,
                        deliver=deliver, thought=thought)
    if not r.get("ok"):
        return None
    bump("asked", last={"q": text[:120], "board": r["brain"]})
    mid = r["id"]
    if wait and wait > 0:
        slot = {"ev": threading.Event(), "res": None}
        with _LOCK:
            _WAIT[mid] = slot
        slot["ev"].wait(float(wait))
        with _LOCK:
            _WAIT.pop(mid, None)
        if slot["res"]:
            bump("merged")
            return slot["res"]
    return {"pending": mid, "brain": r["brain"]}


def on_answer(mid, item, text, frm, zone, grounded, source):
    """Плата ответила на письмо «единого мозга» (из brain.mail_answer).
    Возврат: True — ответ забран ждущим каналом (доставлять не нужно)."""
    if not str(text or "").strip():        # v3.121: мысль отсеяна (ахинея)
        bump("dropped")
        with _LOCK:
            slot = _WAIT.get(mid)
            if slot is not None:
                slot["ev"].set()
        return True
    res = {"answer": str(text or ""), "zone": zone, "brain": frm,
           "grounded": bool(grounded), "source": str(source or ""),
           "thought": bool(item.get("thought")),
           "q": item.get("text") or "", "deliver": item.get("deliver") or {}}
    bump("answered")
    if grounded and text:
        bump("grounded")
        _learn(item.get("text") or "", text, frm, source)
    with _LOCK:
        slot = _WAIT.get(mid)
        if slot is not None:
            slot["res"] = res
            slot["ev"].set()
            return True
    deliver = item.get("deliver") or {}
    kind = deliver.get("kind")
    if kind == "tg":              # v3.97: Telegram — так же, как МАКС
        if not (grounded or res["thought"]):
            bump("dropped")
            return True
        try:
            import tgbot
            tgbot.send(deliver.get("chat_id"), supplement_text(res, late=True))
            bump("late")
        except Exception as e:
            store.log("ЕДИНЫЙ МОЗГ", "Telegram: ответ мозга не ушёл: %s" % e)
        return True
    if kind == "max":
        if not (grounded or res["thought"]):
            bump("dropped")       # заготовку зоны в мессенджер не шлём
            return True
        try:
            import maxbot
            msg = supplement_text(res, late=True)
            maxbot.send(deliver.get("chat_id"), msg)
            try:
                maxbot._mem_add(deliver.get("chat_id"), "bot", msg)
            except Exception:
                pass
            bump("late")
        except Exception as e:
            store.log("ЕДИНЫЙ МОЗГ", "МАКС: дополнение не ушло: %s" % e)
        return True
    if kind == "api":
        bump("late")
        return False              # заберут через /api/v1/result (mail_result)
    if kind == "chat":
        bump("late")
        return False              # brain.mail_answer допишет в веб-чат
    return False


def supplement_text(res, late=False):
    """v3.97: ответ платы — тем же шаблоном, что ответы облака (Ответ +
    подпись), без служебных фраз «плата думает так / дополняет»."""
    import llmreply
    d = res.get("deliver") or {}
    return llmreply.wrap_brain(res.get("q") or "", res.get("answer") or "",
                               board=str(res.get("brain") or "")[:30],
                               sender=str(d.get("name") or ""),
                               chat_id=d.get("chat_id"),
                               grounded=res.get("grounded"),
                               source=res.get("source"),
                               thought=bool(res.get("thought")))


def merge(reply, zone, res):
    """Влить ответ платы в ответ облака."""
    import brain
    if not res or not str(res.get("answer") or "").strip():
        return reply, zone
    if res.get("thought"):
        # v3.99: мысль мозга НЕ заменяет ответ из памяти — дополняет его;
        # заменяет только честное «не знаю» (облаку сказать было нечего)
        head = str(reply or "").lstrip()[:220]
        if not head or _NOANS.search(head):
            return supplement_text(res), brain.Z["board"]
        th = str(res.get("answer") or "").strip()
        if th[:40].lower() in str(reply).lower():
            return reply, zone
        line = "_Мозг neuro-12%s от себя:_ %s" % (
            (" «%s»" % res.get("brain")) if res.get("brain") else "", th)
        lines = str(reply).rstrip().split("\n")
        k = len(lines)
        while k > 0 and lines[k - 1].strip().startswith(("_Составлено",
                                                          "_Источник")):
            k -= 1
        if k == len(lines):
            return str(reply).rstrip() + "\n\n" + line, zone
        return "\n".join(lines[:k]).rstrip() + "\n\n" + line + "\n\n" + \
            "\n".join(lines[k:]), zone
    if not res.get("grounded"):
        bump("dropped")
        return reply, zone        # мнение без опоры слабый ответ не улучшит
    sup = supplement_text(res)
    if _NOANS.search(str(reply or "").lstrip()[:220]):
        return sup, brain.Z["board"]      # облако не знало — отвечает плата
    return str(reply or "").rstrip() + "\n\n" + sup, zone


def _learn(q, a, frm, source):
    try:
        import qalearn
        qalearn.learn_external(q, a, src="плата %s: %s" % (frm, source),
                               verified=str(source or "").startswith(
                                   "подтверждённый"))
    except Exception:
        pass


# ------------------------------ обмен знанием --------------------------------
def learn_from_board(board, pairs, code):
    """POST /api/brain/learn: подтверждённое на плате -> облако."""
    import qalearn
    n_p = n_c = 0
    if str(board) == "облако-материалы":
        pairs = []        # v3.144: материалы — не подтверждённые ответы
    for p in (pairs or [])[:200]:
        if not isinstance(p, dict):
            continue
        q, a = str(p.get("q") or "").strip(), str(p.get("a") or "").strip()
        if not q or not a or str(p.get("src") or "").startswith("облако"):
            continue              # своё же знание обратно не принимаем
        if qalearn.learn_external(q, a, src="плата %s (подтверждено)"
                                  % board, verified=True):
            n_p += 1
    for c in (code or [])[:30]:
        if not isinstance(c, dict):
            continue
        task, text = str(c.get("task") or "").strip(), \
            str(c.get("text") or "").strip()
        if len(task) < 4 or len(text) < 20:
            continue
        try:
            import kb
            kb.add_article("Код платы: %s" % task[:150],
                           "Задача: %s\n\n%s" % (task, text[:12000]),
                           source="код платы %s" % board)
            n_c += 1
        except Exception:
            pass
    if n_p:
        bump("pairs_in", n_p)
    if n_c:
        bump("code_in", n_c)
    store.log("ЕДИНЫЙ МОЗГ", "плата %s передала: ответов %d, кода %d"
              % (board, n_p, n_c))
    return {"ok": True, "pairs": n_p, "code": n_c}


def knowledge_for_board(board, since=0.0):
    """GET /api/brain/knowledge: подтверждённое облаком -> плата."""
    import qalearn
    pairs = [p for p in qalearn.export_verified(since)
             if not str(p.get("src") or "").startswith("плата %s" % board)]
    if pairs:
        bump("pairs_out", len(pairs))
    now = time.time()
    # v3.144: МАТЕРИАЛЫ для сетей платы (сеть материалов + зонная сеть) —
    # отдельно от подтверждённых ответов: это знание для учёбы, а не готовый
    # ответ на вопрос. Порциями по MAT_BATCH, чтобы плату не завалить.
    mats = materials_since(since)
    ts_out = now
    if len(mats) > MAT_BATCH:
        mats = mats[:MAT_BATCH]
        ts_out = float(mats[-1]["ts"])
    return {"ok": True, "pairs": pairs[:200], "materials": mats, "ts": ts_out}


# --------------------- v3.144: материалы для плат ---------------------------
MAT_FILE = "board_materials.json"
MAT_MAX = 400
MAT_BATCH = 30


def materials_put(items):
    """Положить материалы (title, text) в очередь для плат. Возврат: сколько
    новых. Повторы (тот же текст) не дублируются."""
    import hashlib
    d = store.load_json(MAT_FILE, None) or {}
    have = {x.get("id") for x in d.get("items") or []}
    out = list(d.get("items") or [])
    t0 = time.time()
    n = 0
    for i, it in enumerate(items or []):
        text = str(it.get("text") or "").strip()[:3000]
        title = str(it.get("title") or "").strip()[:160]
        if len(text) < 40:
            continue
        mid = hashlib.md5(text.encode("utf-8", "replace")).hexdigest()[:16]
        if mid in have:
            continue
        have.add(mid)
        out.append({"id": mid, "title": title, "text": text, "ts": t0 + i * 1e-3})
        n += 1
    out = out[-MAT_MAX:]
    store.save_json(MAT_FILE, {"items": out})
    return n


def materials_since(since):
    d = store.load_json(MAT_FILE, None) or {}
    return [x for x in d.get("items") or [] if float(x.get("ts") or 0) > float(since or 0)]


def materials_clear():
    store.save_json(MAT_FILE, {"items": []})


def thought_to_boards(text):
    """Мысль облака -> платы (D6 + материал их сети), по настройке d6_on."""
    if not store.settings().get("d6_on"):
        return 0
    n = 0
    for b in store.free_boards():
        try:
            store.d6_add(b["id"], text, "Мысли облака")
            n += 1
        except Exception:
            pass
    if n:
        bump("thoughts_out", n)
    return n


def status():
    with _LOCK:
        st = _stats()
        c, last = dict(st["c"]), dict(st.get("last") or {})
    boards = [{"id": b.get("id"), "age": int(time.time() -
                                            float(b.get("last_seen") or 0))}
              for b in store.free_boards()]
    return {"on": bool(cfg("unify_on", True)),
            "wait": float(cfg("unify_wait", 12)),
            "boards": boards, "counters": c, "last": last}
