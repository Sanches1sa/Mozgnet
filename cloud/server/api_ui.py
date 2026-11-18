# -*- coding: utf-8 -*-
# ===========================================================================
# api_ui.py — API ВЕБ-ИНТЕРФЕЙСА (ChatGPT-стиль) и панели управления
# ---------------------------------------------------------------------------
#   POST /api/chat/new|rename|delete|send|gen   GET /api/chat/list|get|poll
#   GET  /api/status     — сводка для шапки и панели
#   GET/POST /api/settings — настройки платформы (учитель LLM, маршрут...)
#   GET  /api/chronicle  — лента хроники плат
#   POST /api/auth/setup|login|logout|password   GET /api/auth/check
# Всё это использует движок brain.think() — БЕЗ LLM.
# Маршруты здесь открыты только ПОСЛЕ ВХОДА по паролю панели (режим
# "panel"; см. auth.py). API плат — отдельно, по токену (api_board.py).
# ===========================================================================
import re
import activity
import threading
import time

import api_board
import applab
import auth
import brain
import kb
import maxbot
import tgbot
import skills
import store
import study
import updater
import webfetch


# -------------------------------- чаты --------------------------------------
def h_chat_new(req):
    b = req.body or {}
    # v3.24.2: новый чат принадлежит адресу (IP), который его создал
    chat = store.chat_new(title=str(b.get("title") or "Новый чат")[:80],
                          owner=getattr(req, "client_ip", ""))
    return 200, {"ok": True, "id": chat["id"], "title": chat["title"]}


def h_chat_list(req):
    # v3.24.2: у разных адресов (IP) — разные чаты (настройка chats_by_ip);
    # старые чаты без владельца и лента «Мысли плат» — общие.
    ip = getattr(req, "client_ip", "")
    owner = ip if store.settings().get("chats_by_ip", True) else None
    return 200, {"ok": True, "chats": store.chat_list(owner)}


def h_chat_get(req):
    cid = req.query.get("id", "")[:40]
    chat = store.chat_load(cid)
    if not chat and cid == "thoughts":     # v3.172: «Мысли плат» ещё пусты —
        chat = store.chat_new("thoughts", title="Мысли плат", kind="thoughts")  # не «чат не найден»
    if not chat:
        return 404, {"ok": False, "error": "чат не найден"}
    if not store.chat_allowed(chat, getattr(req, "client_ip", "")):
        return 403, {"ok": False,
                     "error": "это чат другого адреса (IP)"}
    return 200, {"ok": True, "chat": chat}


def h_chat_poll(req):
    """Новые сообщения чата после ts (ожидание ответа платы без перезагрузки)."""
    cid = req.query.get("id", "")[:40]
    after = float(req.query.get("after", "0") or 0)
    chat = store.chat_load(cid)
    if not chat:
        return 404, {"ok": False, "error": "чат не найден"}
    if not store.chat_allowed(chat, getattr(req, "client_ip", "")):
        return 403, {"ok": False,
                     "error": "это чат другого адреса (IP)"}
    msgs = [m for m in (chat.get("messages") or [])
            if float(m.get("ts") or 0) > after]
    return 200, {"ok": True, "messages": msgs[-20:], "ts": chat.get("ts")}


def h_chat_rename(req):
    b = req.body or {}
    cid = str(b.get("id") or "")[:40]
    chat = store.chat_load(cid)
    if not chat:
        return 404, {"ok": False, "error": "чат не найден"}
    if not store.chat_allowed(chat, getattr(req, "client_ip", "")):
        return 403, {"ok": False,
                     "error": "это чат другого адреса (IP)"}
    chat["title"] = str(b.get("title") or chat["title"])[:80]
    store.chat_save(cid, chat)
    return 200, {"ok": True, "title": chat["title"]}


def h_chat_delete(req):
    cid = str((req.body or {}).get("id") or "")[:40]
    if cid == "thoughts":
        return 200, {"ok": False, "error": "сессию «Мысли плат» удалить нельзя"}
    chat = store.chat_load(cid)
    if chat and not store.chat_allowed(chat, getattr(req, "client_ip", "")):
        return 403, {"ok": False,
                     "error": "это чат другого адреса (IP)"}
    return 200, {"ok": store.chat_delete(cid)}


def h_chat_send(req):
    """Сообщение в чат. Отвечает МОЗГ ОБЛАКА (без LLM). Маршруты:
    auto (по умолчанию) — облако, а при пустом ответе — живая плата;
    cloud — только облако; board — письмо живой плате."""
    b = req.body or {}
    route = str(b.get("route") or "").strip() or \
        store.settings().get("route") or "auto"
    # v3.22: в режиме «Код» можно вставить длинный код на разбор
    text = str(b.get("text") or "").strip()[:20000 if route == "code" else 2000]
    if not text:
        return 400, {"ok": False, "error": "пустое сообщение"}
    cid = str(b.get("id") or "")[:40]
    long_mode = bool(b.get("long", store.settings().get("long_default")))
    with store.LOCK:
        chat = store.chat_load(cid) if cid else None
        if chat and not store.chat_allowed(chat,
                                           getattr(req, "client_ip", "")):
            return 403, {"ok": False,
                         "error": "это чат другого адреса (IP)"}
        if not chat or chat.get("kind") not in ("chat", "thoughts", None):
            # v3.24.2: новый чат получает владельца — адрес отправителя
            chat = store.chat_new(title=text[:60],
                                  owner=getattr(req, "client_ip", ""))
        cid = chat["id"]
        if chat.get("kind") == "thoughts":
            route = "cloud"          # в ленте мыслей отвечает только облако
        chat["messages"].append({"role": "user", "text": text, "ts": time.time()})
        # авто-заголовок чата по первому сообщению
        if chat["title"] in ("Новый чат", "чат"):
            chat["title"] = text[:60]
        store.chat_save(cid, chat)
    t0 = time.time()
    # v3.149.7: вся обработка ответа в try — исключение не роняет
    # соединение и не «отключает» облако после сообщения
    activity.begin(str(b.get("request_id") or ""), getattr(req, "client_ip", ""))
    try:
        return _chat_process(req, chat, cid, text, route, long_mode, t0)  # v3.150: ответ терялся
    except Exception as e:
        import traceback
        traceback.print_exc()
        try:
            store.log("ЧАТ", "ошибка ответа: %s" % e)
        except Exception:
            pass
        return 200, {"ok": False, "error": "облако споткнулось: %s. "
                     "Попробуй ещё раз." % type(e).__name__,
                     "id": cid, "title": chat.get("title")}

    finally:
        activity.finish()


def h_chat_activity(req):
    status = activity.get(req.query.get("id", ""), getattr(req, "client_ip", ""))
    if status is None:
        return 404, {"ok": False, "error": "нет статуса запроса"}
    return 200, dict(ok=True, **status)


def _chat_process(req, chat, cid, text, route, long_mode, t0):
    """Внутренняя обработка сообщения (вынесена, чтобы внешний try
    ловил любые падения и не обрывал соединение)."""
    b = req.body or {}
    # v3.9: контекст диалога для продолжения темы (короткие реплики:
    # «а почему?» после темы держат тему) — последние 6 сообщений.
    ctx = [{"role": m.get("role"), "text": m.get("text")}
           for m in (chat.get("messages") or [])[-6:]]
    # v3.150: команды компьютера (терминал, рабочий стол) — владелец в панели,
    # в любом режиме чата (раньше «Авто»/«Разговор» их не пропускали)
    talk_parts = None
    try:
        import desktop
        pcr = desktop.panel_command(text)
    except Exception as e:
        pcr = "Рабочий стол: %s" % e
    if pcr is not None:
        reply, zone, info = pcr, brain.Z["sys"], {"terminal": True}
    elif route == "board":
        activity.update("Передаю вопрос плате и ожидаю ответ…")
        r = brain.ask_board(text, chat_id=cid, frm="чат облака",
                            long_mode=long_mode)
        if r.get("ok"):
            reply = "Вопрос ушёл живой плате «%s». Ответ придёт сюда " \
                    "отдельным сообщением — плата думает зонной сетью, это " \
                    "может занять до минуты." % r["brain"]
            info = {"pending_mid": r["id"], "brain": r["brain"]}
            zone = brain.Z["board"]
        else:
            # плат по почте нет — пробуем МОСТ: облако само стучится к плате
            br = __import__("bridge").send(text, user="чат облака",
                                           wait=float(store.settings().
                                                      get("board_timeout")
                                                      or 45))
            if br.get("ok") and br.get("answer"):
                reply = br["answer"]
                zone = brain.Z["board"]
                info = {"brain": "плата (мост %s)" % br.get("url", ""),
                        "ms_bridge": br.get("ms")}
                try:
                    brain.bump_zone(zone)
                except Exception:
                    pass
            elif br.get("ok"):
                reply = ("Плата получила вопрос и думает (мост %s), но пока "
                         "молчит — спроси ещё раз через минуту."
                         % br.get("url", ""))
                zone = brain.Z["board"]
                info = {"brain": "плата (мост)"}
            else:
                reply, zone, info = brain.think(text, long_mode,
                                                route="auto", chat_id=cid,
                                                ctx=ctx)
    elif route == "talk":
        # v3.132: «Разговор» — просто поговорить: пишет сам мозг (плата, иначе
        # мозг облака), своя LLM дополняет; без базы знаний и фактов,
        # сплошным текстом без смайликов и кавычек (talkmind.py)
        activity.update("Готовлю ответ в режиме Разговор…")
        import talkmind
        talk_parts, info = talkmind.respond_parts(text, ctx)
        reply = "\n\n".join(talk_parts)
        zone = brain.Z["talk"]
    elif route == "code":
        # v3.22: режим «Код» — мозг сам пишет код из своих заготовок
        # (без LLM), проверяет и честно отчитывается; не про код — обычный путь
        # v3.27: по инструкциям ИИ-агента (задача -> план -> код ->
        # проверка -> запуск -> дальше), с памятью прошлой задачи
        activity.update("Обрабатываю задачу кодинг-агентом…")
        import agent
        try:
            reply, matched, cinfo = agent.code_agent(
                text, lang=str(b.get("lang") or ""), ctx=ctx)
        except Exception as e:
            reply, matched, cinfo = ("Режим «Код» споткнулся: %s. Попробуй "
                                     "переформулировать просьбу." % e), True, {}
        if matched:
            zone = brain.Z["code"]
            info = {k: v for k, v in cinfo.items() if k != "code"}
            try:
                brain.bump_zone(zone)
            except Exception:
                pass
        else:
            reply, zone, info = brain.think(text, long_mode, route="cloud",
                                            chat_id=cid, ctx=ctx)
    else:
        # v3.132: «Авто» и «Облако» — ТОТ ЖЕ ИИ, что в боте МАКС (dialog.py):
        # те же зоны, тот же шаблон ответа, тот же честный режим
        activity.update("Обрабатываю вопрос движком облака…")
        import dialog
        ctx12 = [{"role": m.get("role"), "text": m.get("text")}
                 for m in (chat.get("messages") or [])[-12:]
                 if "💭" not in str(m.get("text") or "")[:60]]
        talk_parts, zone, info = dialog.respond(
            text, ctx=ctx12, chat_key="chat:%s" % cid, name="",
            sender="владелец", deliver=None, long_mode=long_mode,
            limit=int(store.settings().get("tpl_chat_limit") or 4000),
            template=True)
        reply = "\n\n".join(talk_parts)
        route = "talk_parts"           # части ответа — как в МАКС
    # v3.28: шаблон «приветствие + ответ» в чате облака (tpl_chat, выкл. по
    # умолчанию): длинный ответ — двумя сообщениями
    parts = [reply]
    try:
        import replytpl
        if route in ("talk", "talk_parts"):
            parts = talk_parts or [reply]         # v3.30: уже по сообщениям
        elif replytpl.cfg("tpl_chat") and not info.get("pending_mid"):
            parts = replytpl.build(
                reply, name="", chat_key="chat:%s" % cid,
                limit=int(store.settings().get("tpl_chat_limit") or 4000)) \
                or [reply]
    except Exception:
        parts = [reply]
    parts = [str(x).replace("\u2063", "") for x in parts]   # v3.99.1: метка
    try:                       # v3.107: формулы $…$ — в окошко формулы
        import latexlite
        parts = [latexlite.code_windows(latexlite.render_dollars(x, "svg"))
                 for x in parts]
    except Exception:
        pass
    activity.update("Сохраняю ответ…")
    with store.LOCK:
        chat = store.chat_load(cid) or chat
        now = time.time()
        gid = ("g%d" % int(now * 1000)) if len(parts) > 1 else None
        sent = []
        for i, ptxt in enumerate(parts):
            msg = {"role": "bot", "text": ptxt, "zone": zone,
                   "ts": now + i * 0.01, "ms": int((now - t0) * 1000)}
            msg.update({k: v for k, v in info.items() if k != "pending_mid"})
            if gid:
                msg["part_of"] = gid
            if info.get("pending_mid") and i == len(parts) - 1:
                msg["pending"] = True
                msg["mid"] = info["pending_mid"]
            chat["messages"].append(msg)
            sent.append({"text": ptxt, "ts": msg["ts"]})
        chat["ts"] = time.time()
        store.chat_save(cid, chat)
        out = {"ok": True, "id": cid, "title": chat["title"],
               "reply": "\n\n".join(parts), "zone": zone, "ms": msg["ms"],
               "ts": msg["ts"]}
        if len(parts) > 1:
            out["parts"] = sent
        if info.get("net"):
            out["net"] = info["net"]          # v3.9: рост сети за ответ
        if info.get("pending_mid"):
            out["pending"] = True
            out["mid"] = info["pending_mid"]
            out["brain"] = info.get("brain")
        return 200, out


def h_chat_rate(req):
    """v3.28: 👍/👎 под ответом — обучение на ответах (qalearn).
    POST {id: чат, ts: время ответа, v: 1|-1}."""
    import qalearn
    b = req.body or {}
    cid = str(b.get("id") or "")[:40]
    try:
        ts = float(b.get("ts") or 0)
        v = 1 if float(b.get("v") or 0) > 0 else -1
    except (TypeError, ValueError):
        return 400, {"ok": False, "error": "нужны ts и v"}
    with store.LOCK:
        chat = store.chat_load(cid)
        if not chat:
            return 404, {"ok": False, "error": "чат не найден"}
        if not store.chat_allowed(chat, getattr(req, "client_ip", "")):
            return 403, {"ok": False, "error": "это чат другого адреса (IP)"}
        msgs = chat.get("messages") or []
        i = next((j for j, m in enumerate(msgs) if m.get("role") == "bot" and
                  abs(float(m.get("ts") or 0) - ts) < 0.002), -1)
        if i < 0:
            return 404, {"ok": False, "error": "ответ не найден"}
        bot = msgs[i]
        q = next((str(m.get("text") or "") for m in reversed(msgs[:i])
                  if m.get("role") == "user"), "")
        # части одного ответа (шаблон «приветствие + ответ») — вместе
        a_parts = [str(bot.get("text") or "")]
        gid = bot.get("part_of")
        if gid:
            a_parts = [str(m.get("text") or "") for m in msgs
                       if m.get("part_of") == gid and  # мысли — не ответ
                       not str(m.get("text") or "").startswith("💭")]
        for m in msgs:
            if m is bot or (gid and m.get("part_of") == gid):
                m["rated"] = v
        store.chat_save(cid, chat)
    a = "\n\n".join(a_parts)
    try:
        import replytpl
        gl = replytpl._ALREADY.match(a)
        if gl and "\n\n" in a[:120]:
            a = a.split("\n\n", 1)[1]          # приветствие — не часть знания
    except Exception:
        pass
    if not q:
        return 200, {"ok": False, "error": "не нашла вопрос к этому ответу"}
    r = qalearn.rate(q, a, v, zone=str(bot.get("src_zone") or
                                       bot.get("zone") or ""))
    try:      # v3.30: опыт обучения — оценка и сайтам, из которых ответ собран
        r["sites"] = qalearn.rep_update(
            [s.get("url") for s in (bot.get("sources") or [])
             if isinstance(s, dict)], v)
    except Exception:
        pass
    return 200, dict(r, v=v)


def h_qa_stats(req):
    import qalearn
    return 200, {"ok": True, "qa": qalearn.stats()}


def h_qa_suggest(req):
    """Владелец принимает/отклоняет исправление собеседника (МАКС/API)."""
    import qalearn
    b = req.body or {}
    return 200, qalearn.suggest_apply(str(b.get("k") or ""),
                                      bool(b.get("ok")))


def h_chat_gen(req):
    """Длинный текст по теме (генератор облака) или по заданию от плат."""
    b = req.body or {}
    topic = str(b.get("topic") or "").strip()[:200]
    if not topic:
        task = store.gen_task_take()
        if not task:
            rd = brain.mn_readings()
            hot = rd.get("hot") or rd.get("top") or []
            if not hot:
                return 200, {"ok": False, "error": "нет темы: сеть ещё пуста. "
                             "Задай тему: «статья <тема>» или загрузи материалы."}
            topic = hot[0]
        else:
            topic = task.get("text") or topic
    reply = brain.write_article(topic)
    try:
        brain.bump_zone(brain.Z["gen"])
    except Exception:
        pass
    cid = str(b.get("id") or "")[:40]
    if cid:
        with store.LOCK:
            chat = store.chat_load(cid)
            if chat and not store.chat_allowed(chat,
                                               getattr(req, "client_ip", "")):
                return 403, {"ok": False,
                             "error": "это чат другого адреса (IP)"}
            if chat:
                chat["messages"].append({"role": "bot", "text": reply,
                                         "zone": brain.Z["gen"],
                                         "ts": time.time()})
                store.chat_save(cid, chat)
    return 200, {"ok": True, "reply": reply, "topic": topic}


# ------------------------------- панель -------------------------------------
_STATS_CACHE = {"ts": 0.0, "v": None}


_STATUS = {"v": None, "ts": 0.0, "busy": False}


def _status_build():
    """v3.155: сводка для панели. Каждая часть замеряется; часть дольше 1 с
    пишется в журнал «МЕДЛЕННО» — видно, что тормозит именно у вас."""
    slow = []

    def part(name, fn, default):
        t0 = time.time()
        try:
            v = fn()
        except Exception:
            v = default
        dt = time.time() - t0
        if dt > 1.0:
            slow.append("%s %.1f с" % (name, dt))
        return v

    now = time.time()
    if _STATS_CACHE["v"] is None or now - _STATS_CACHE["ts"] > 20:
        _STATS_CACHE["v"] = part("сводка данных", store.stats, {})
        _STATS_CACHE["ts"] = now
    st = _STATS_CACHE["v"] or {}
    rd = part("сеть материалов", brain.mn_readings, {})
    code_st = part("код", lambda: __import__("codemind").stats(),
                   {"code_files": 0, "code_funcs": 0})
    curiosity_st = part("любопытство", lambda: __import__("curiosity").status(),
                        {"on": False, "read": 0, "failed": 0, "queue": []})
    hypo_st = part("гипотезы", lambda: __import__("hypo").status(), {"on": False, "made": 0})
    speech_st = part("речь", lambda: __import__("wordmind").status(),
                     {"words": 0, "links": 0, "ready": False})

    def _boards():
        out, t = [], time.time()
        rall = store.readings_all()
        for b in store.boards().values():
            out.append({"id": b.get("id"), "name": b.get("name"),
                        "url": b.get("url"), "kind": b.get("kind"),
                        "online": (t - float(b.get("last_seen") or 0)) < 75,
                        "last_seen": b.get("last_seen"),
                        "readings": (rall.get(b.get("id")) or {}).get("readings")})
        return out
    boards = part("платы", _boards, [])
    load_st = part("нагрузка", lambda: __import__("sysload").snapshot(), {})
    out = {
        "ok": True, "version": store.version(),
        "uptime": round(time.time() - api_board.STARTED[0]),
        "load": load_st,
        "stats": st, "cloud_readings": rd, "boards": boards,
        "bridge": part("мост", lambda: __import__("bridge").status(), {}),
        "teacher": part("учитель", lambda: __import__("teacher").state(), {}),
        "tour": part("экскурсия", lambda: __import__("teacher").tour_status(), {}),
        "gen_queue": st.get("gen_queue", 0),
        "thoughts": part("мысли", lambda: len(store.thoughts_list(200)), 0),
        "code": code_st,
        "curiosity": curiosity_st,
        "hypo": hypo_st,
        "speech": speech_st,
    }
    if slow:
        store.log("МЕДЛЕННО", "сводка панели: " + ", ".join(slow))
    return out


def _status_refresh():
    try:
        v = _status_build()
        _STATUS["v"], _STATUS["ts"] = v, time.time()
    finally:
        _STATUS["busy"] = False


def h_status(req):
    """v3.155: сводка отдаётся СРАЗУ из памяти; свежая собирается фоном
    (раньше каждый опрос панели собирал её заново — и ждал занятые замки)."""
    now = time.time()
    if _STATUS["v"] is None:
        _status_refresh()                     # самый первый раз — честно собрать
    elif now - _STATUS["ts"] > 4 and not _STATUS["busy"]:
        _STATUS["busy"] = True
        threading.Thread(target=_status_refresh, daemon=True,
                         name="status-refresh").start()
    v = dict(_STATUS["v"])
    v["uptime"] = round(time.time() - api_board.STARTED[0])
    v["age"] = round(time.time() - _STATUS["ts"], 1)
    return 200, v


# --------------------- v3.12: речь из словаря --------------------------------
def h_speech_get(req):
    """Состояние сети речи (панель «Речь из словаря»)."""
    import wordmind
    return 200, {"ok": True, "speech": wordmind.status()}


def h_speech_post(req):
    """СОБРАТЬ фразу из слов словаря по теме (без заготовок, честно).
    Действия: compose (по умолчанию) | rebuild (пересобрать сеть речи)."""
    import wordmind
    b = req.body or {}
    action = str(b.get("action") or "compose").strip().lower()
    if action == "rebuild":
        links = wordmind._rebuild()
        return 200, {"ok": True, "rebuilt": True, "links": links,
                     "speech": wordmind.status()}
    topic = str(b.get("text") or b.get("topic") or "").strip()[:200]
    if not topic:
        return 400, {"ok": False, "error": "пустая тема"}
    phrase, info = wordmind.compose(topic)
    if not phrase:
        return 200, {"ok": False, "error": info.get("why") or "не собралось",
                     "speech": wordmind.status()}
    return 200, {"ok": True, "phrase": phrase, "info": info,
                 "speech": wordmind.status()}


_SECRET_KEYS = ("google_api_key", "yandex_api_key", "conf_password", "conf_token")


def _mask_secrets(s):
    """v3.155: ключи поисковиков в панель уходят маской «…1234»."""
    for k in _SECRET_KEYS:
        v = str(s.get(k) or "")
        if v:                        # пароль — без единого символа
            s[k] = "…••••" if k == "conf_password" else "…" + v[-4:]
    return s


def h_settings_get(req):
    s = _mask_secrets(store.settings())
    return 200, {"ok": True, "settings": s, "token": store.token(),
                 "data_dir": store.DATA_DIR}


def h_settings_post(req):
    patch = req.body or {}
    # маскированный ключ не перезаписываем
    t = patch.get("teacher") or {}
    if isinstance(t, dict) and "…" in str(t.get("llm_key") or ""):
        t["llm_key"] = None
        patch["teacher"] = t
    for k in _SECRET_KEYS:                 # маска — значит «не менять»
        if "…" in str(patch.get(k) or ""):
            patch.pop(k, None)
        elif k in patch:
            patch[k] = str(patch[k] or "").strip()[:200]
    if "brain_strict_min" in patch:          # v3.166
        try:
            patch["brain_strict_min"] = max(0.0, min(0.9, float(patch["brain_strict_min"])))
        except (TypeError, ValueError):
            patch.pop("brain_strict_min")
    if "brain_strict_nobrain" in patch and patch["brain_strict_nobrain"] not in ("pause", "neutral"):
        patch.pop("brain_strict_nobrain")
    for k in ("conf_url", "conf_space", "conf_user"):     # v3.168
        if k in patch:
            patch[k] = str(patch[k] or "").strip()[:300]
    for k in ("google_cx", "yandex_folder"):
        if k in patch:
            patch[k] = str(patch[k] or "").strip()[:120]
    s = _mask_secrets(store.update_settings(patch))
    return 200, {"ok": True, "settings": s}


def h_teacher_tour(req):
    """Управление экскурсией учителя: start|stop|reset|status."""
    b = req.body or {}
    action = str(b.get("action") or "status").strip().lower()
    if action == "start":
        st = __import__("teacher").tour_start(
            board=str(b.get("board") or ""),
            interval=int(b.get("interval") or 0),
            force=bool(b.get("force")))
    elif action == "stop":
        st = __import__("teacher").tour_stop()
    elif action == "reset":
        st = __import__("teacher").tour_reset()
    else:
        st = __import__("teacher").tour_status()
    return 200, {"ok": True, "tour": st}


def h_teacher_thoughts(req):
    """v3.35: лента «Мысли учителя» (своя LLM + платы + облако)."""
    import teacher
    return 200, {"ok": True, "items": teacher.thoughts(60)}


def h_chronicle(req):
    return 200, {"ok": True, "items": store.chronicle_list(100)}


# ---------------- v3.24.2: ссылки-инструкции (панель -> база знаний) ---------
def h_study_links(req):
    """v3.30: обёртка — панель ссылок не «недоступна» ни при какой ошибке:
    список ссылок возвращается всегда, причина сбоя — в result.err."""
    try:
        return _study_links_impl(req)
    except Exception as e:
        store.log("ИНСТРУКЦИИ", "панель ссылок: %s" % e)
        try:
            ls = study.links()
        except Exception:
            ls = []
        return 200, {"ok": True, "links": ls,
                     "refresh_hours": store.settings().get("study_refresh")
                     or 0, "result": {"ok": False,
                                      "err": "сбой: %s" % e}}


def _study_links_impl(req):
    """ССЫЛКИ-ИНСТРУКЦИИ: владелец добавляет ссылку — текст со страницы
    качается в базу знаний (статья «Инструкции: …» + сеть материалов).
    v3.24.3: прямая ссылка на файл (pdf/doc/docx/txt…) читается как файл;
    со страниц дополнительно качаются файлы ТОГО ЖЕ домена (каждый —
    отдельной статьёй, лимиты study_files/study_files_max).
    GET — список ссылок и часы авто-перечитки.
    POST — {url} добавить и прочитать сейчас; {url, read: true} перечитать;
    {url, remove: true} убрать ссылку (и её статью из базы);
    {refresh: <часы>} — сменить авто-перечитку (0 = выкл);
    {study_files: 0|1} — качать ли файлы со страниц;
    {study_files_max: <N>} — сколько файлов со страницы (0..50);
    {study_pages_max: <N>} — предел страниц Confluence-пространства (1..5000);
    {study_attach: 0|1} — качать ли ВЛОЖЕНИЯ страниц Confluence (v3.25.0);
    {study_attach_max: <N>} — новых вложений за круг (0..20000).
    Оба метода зарегистрированы — урок «нет маршрута»."""
    b = req.body or {}
    result = None
    if str(getattr(req, "method", "") or "").upper() == "POST":
        if "refresh" in b:
            try:
                hours = max(0, min(168, float(b.get("refresh") or 0)))
            except (TypeError, ValueError):
                hours = 6.0
            store.update_settings({"study_refresh": hours})
            result = {"ok": True, "refresh": hours}
        elif "study_files" in b or "study_files_max" in b \
                or "study_crawl" in b or "study_crawl_max" in b \
                or "study_pages_max" in b or "study_attach" in b \
                or "study_attach_max" in b:
            patch = {}
            if "study_files" in b:
                patch["study_files"] = bool(b.get("study_files"))
            if "study_files_max" in b:
                try:
                    patch["study_files_max"] = max(
                        0, min(50, int(b.get("study_files_max") or 0)))
                except (TypeError, ValueError):
                    patch["study_files_max"] = 20
            if "study_crawl" in b:       # v3.61: обход раздела сайта
                patch["study_crawl"] = bool(b.get("study_crawl"))
            if "study_crawl_max" in b:
                try:
                    patch["study_crawl_max"] = max(
                        0, min(5000, int(b.get("study_crawl_max") or 0)))
                except (TypeError, ValueError):
                    patch["study_crawl_max"] = 300
            if "study_pages_max" in b:
                # v3.24.5: предел страниц Confluence-пространства на ссылку
                try:
                    patch["study_pages_max"] = max(
                        1, min(5000, int(b.get("study_pages_max") or 0)))
                except (TypeError, ValueError):
                    patch["study_pages_max"] = 1000
            if "study_attach" in b:      # v3.25.0: вложения Confluence
                patch["study_attach"] = bool(b.get("study_attach"))
            if "study_attach_max" in b:
                try:
                    patch["study_attach_max"] = max(
                        0, min(20000, int(b.get("study_attach_max") or 0)))
                except (TypeError, ValueError):
                    patch["study_attach_max"] = 300
            store.update_settings(patch)
            result = {"ok": True, "study_files":
                      store.settings().get("study_files"),
                      "study_files_max":
                      store.settings().get("study_files_max"),
                      "study_pages_max":
                      store.settings().get("study_pages_max"),
                      "study_attach":
                      store.settings().get("study_attach"),
                      "study_attach_max":
                      store.settings().get("study_attach_max")}
        else:
            url = str(b.get("url") or "").strip()
            if not url:
                return 400, {"ok": False, "error": "пустая ссылка"}
            if b.get("remove"):
                result = study.remove(url)
            elif b.get("read"):
                result = study.add(url)          # v3.30: перечитка в фоне
            else:
                result = study.add(url)
    try:
        hours = float(store.settings().get("study_refresh") or 0)
    except Exception:
        hours = 6.0
    try:
        _raw_max = store.settings().get("study_files_max")
        fmax = int(_raw_max) if _raw_max is not None else 20
    except Exception:
        fmax = 20
    try:
        _raw_pg = store.settings().get("study_pages_max")
        pmax = int(_raw_pg) if _raw_pg is not None else 1000
    except Exception:
        pmax = 1000
    try:
        _raw_am = store.settings().get("study_attach_max")
        amax = int(_raw_am) if _raw_am is not None else 300
    except Exception:
        amax = 300
    out = {"ok": True, "links": study.links(), "refresh_hours": hours,
           "study_files": bool(store.settings().get("study_files", True)),
           "study_files_max": fmax,
           "study_crawl": bool(store.settings().get("study_crawl", True)),
           "study_crawl_max": int(store.settings().get("study_crawl_max")
                                  if store.settings().get("study_crawl_max")
                                  is not None else 300),
           "study_pages_max": max(1, min(5000, pmax)),
           "study_attach": bool(store.settings().get("study_attach", True)),
           "study_attach_max": max(0, min(20000, amax))}
    if result is not None:
        out["result"] = result
    return 200, out


def h_study_check(req):
    """POST /api/study/check {urls:[…]} (v3.61): какие ссылки ЗАГРУЗЯТСЯ из
    этого облака (страница читается, не экран защиты; число ссылок раздела)."""
    import study
    urls = [str(u) for u in ((req.body or {}).get("urls") or [])][:12]
    return 200, {"ok": True, "results": [study.check_url(u) for u in urls]}


# ----------------------------- зоны мысли ------------------------------------
def h_zones(req):
    """Статус ВСЕХ зон мысли мозга облака: описание, как вызвать,
    состояние и статистика ответов (v3.3 — зоны подключены все)."""
    stats = brain.zone_stats()
    dstats = brain.dict_stats()
    notes_n = len(store.notes())
    web_on = bool(store.settings().get("web_on"))
    boards = store.free_boards()
    br = __import__("bridge").status()
    mn_rd = brain.mn_readings()
    zones = []
    for key, desc, howto in brain.ZONES:
        name = brain.Z[key]
        st = stats.get(name) or {}
        item = {"key": key, "name": name, "desc": desc, "howto": howto,
                "answers": int(st.get("n") or 0),
                "last": float(st.get("last") or 0)}
        if key == "wiki":
            item["state"] = ("включена (интернет разрешён)" if web_on
                             else "выключена — включи «web_on» в Общих")
        elif key == "board":
            if boards:
                item["state"] = "плат онлайн: %d" % len(boards)
            elif br.get("online"):
                item["state"] = ("плата на мосту: %s (напиши ей — ответит "
                                 "сама)" % br.get("url"))
            else:
                item["state"] = ("плат онлайн нет — мост ищет плату на %s"
                                 % br.get("url", ""))
        elif key == "mem":
            item["state"] = "заметок в памяти: %d" % notes_n
        elif key == "dict":
            item["state"] = "слов в словарях: %d" % dstats["words"]
        elif key == "mn":
            item["state"] = "узлов: %s, материалов: %s" % (
                mn_rd.get("nodes", "?"), mn_rd.get("docs", "?"))
        elif key == "hypo":
            import hypo
            hs = hypo.status()
            item["state"] = (
                ("включена" if hs.get("on") else "выключена (hypo_on)") +
                ": предположений %d, подтверждено %d, опровергнуто %d"
                % (hs.get("made", 0), hs.get("confirmed", 0),
                   hs.get("denied", 0)))
        elif key == "imagine":
            # v3.16: состояние зоны Воображения: мини-LLM + холст
            try:
                import tinyllm
                ls = tinyllm.status()
                llm = ("мини-LLM: %s, шагов %s/%s, словарь %s" % (
                    "учится" if ls.get("training") else (
                        "готова" if ls.get("ready") else "ждёт запуска"),
                    ls.get("steps"), ls.get("total_steps"),
                    ls.get("vocab")))
            except Exception:
                llm = "мини-LLM: недоступна"
            try:
                import imagine
                cv = imagine.canvas_info()
                canvas = ("на холсте: %s" % ", ".join(cv["objects"])) \
                    if cv.get("has") else "холст пуст"
            except Exception:
                canvas = "холст: недоступен"
            item["state"] = "%s; %s" % (llm, canvas)
        elif key == "code":
            # v3.18: самокод — сколько функций мозг написал себе сам
            try:
                import autocode
                ast_ = autocode.stats()
                item["state"] = ("самокод: написано себе функций %d, "
                                 "заготовок %s" % (ast_.get("written") or 0,
                                                   ast_.get("templates") or 0))
            except Exception:
                item["state"] = "самокод недоступен"
        elif key == "curiosity":
            # v3.18: честное состояние любопытства (включено/сам ищет)
            try:
                import curiosity
                cs = curiosity.status()
                item["state"] = (
                    ("включена" if cs.get("on") else "выключена") +
                    ", сама ищет темы: %s, прочитано %d, в очереди %d" %
                    ("да" if cs.get("self", True) else "нет",
                     cs.get("read") or 0, len(cs.get("queue") or [])))
            except Exception:
                item["state"] = "недоступна"
        else:
            item["state"] = "подключена"
        zones.append(item)
    return 200, {"ok": True, "zones": zones, "version": store.version(),
                 "bridge": br}


def h_zones_check(req):
    """Щупы зон: каждая зона получает пробное задание. Щупы вызывают
    механизмы НАПРЯМУЮ — чат, KB и сеть материалов не засоряются
    (щупы не проходят через mn_learn)."""
    res = []

    def _add(key, ok, note, ms):
        res.append({"key": key, "name": brain.Z[key],
                    "ok": ok if ok is None else bool(ok),
                    "note": str(note)[:200], "ms": int(ms)})

    # Навык: подбор + запуск калькулятора
    t = time.time()
    try:
        hit = skills.match("посчитай сколько будет 2 плюс 2")
        r = skills.run_skill(hit[0], "посчитай сколько будет 2 плюс 2") \
            if hit else None
        _add("skill", bool(r and r.get("ok")),
             (str(r.get("result") or r.get("error")) if r
              else "навык не подобрался"), (time.time() - t) * 1000)
    except Exception as e:
        _add("skill", False, "ошибка: %s" % e, (time.time() - t) * 1000)
    # Словарь: слово из встроенного словаря должно найтись всегда
    t = time.time()
    try:
        r = brain.dict_lookup("мозг")
        _add("dict", r["found"], "слово «%s»: %s (слов в словарях: %d)" % (
            r["word"], "найдено" if r["found"] else "НЕ найдено",
            r["total"]), (time.time() - t) * 1000)
    except Exception as e:
        _add("dict", False, "ошибка: %s" % e, (time.time() - t) * 1000)
    # База знаний: ищем статью самоучителя (на молодой базе tf-idf ниже
    # порога чата, поэтому щупу достаточно найти правильную статью)
    t = time.time()
    try:
        r = kb.search("экскурсия для платы 12 уроков", limit=1)
        hits = [h for h in (r.get("results") or []) if not h.get("wiki")]
        top = hits[0] if hits else {}
        _add("kb", bool(top),
             ("статья «%s» (релевантность %s; в чате порог выше — база "
              "растёт и точность растёт)" % (
                  top.get("title"), top.get("score"))) if hits
             else "база пуста — загрузи статьи или файлы",
             (time.time() - t) * 1000)
    except Exception as e:
        _add("kb", False, "ошибка: %s" % e, (time.time() - t) * 1000)
    # Сеть материалов: короткая сборка текста по горячим узлам
    t = time.time()
    try:
        out = brain.mn().write("мозг память нейроны слова", words=40)
        alive = bool(out) and "Материалов по теме пока нет" not in out \
            and "не нашлось ничего" not in out
        _add("mn", alive, (out or "").strip().replace("\n", " ")[:120]
             or "сеть молчит — загрузи материалы",
             (time.time() - t) * 1000)
    except Exception as e:
        _add("mn", False, "ошибка: %s" % e, (time.time() - t) * 1000)
    # Память: путь живой, состояние — сколько заметок
    t = time.time()
    try:
        n = len(store.notes())
        _add("mem", True, "механизм готов, заметок: %d%s" % (
            n, "" if n else " — скажи в чате «запомни: …»"),
            (time.time() - t) * 1000)
    except Exception as e:
        _add("mem", False, "ошибка: %s" % e, (time.time() - t) * 1000)
    # Википедия: зависит от web_on (щуп в сеть — только если включена)
    t = time.time()
    try:
        if store.settings().get("web_on"):
            r2 = webfetch.wiki_search("мозг", limit=1)
            if r2:
                _add("wiki", True, "отвечает: %s" % r2[0]["title"],
                     (time.time() - t) * 1000)
            else:                     # v3.12.1: честная причина простоя
                tail = "; ".join(webfetch.diag_tail()[:2])
                _add("wiki", False,
                     "шлюз молчит: %s" % (tail or "сеть недоступна?"),
                     (time.time() - t) * 1000)
        else:
            _add("wiki", None, "выключена — включи «web_on» в Общих", 0)
    except Exception as e:
        _add("wiki", False, "ошибка: %s" % e, (time.time() - t) * 1000)
    # Плата: письма живым платам ИЛИ мост к плате 127.0.0.1:7000
    t = time.time()
    try:
        free = store.free_boards()
        brst = __import__("bridge").status()
        if free:
            _add("board", True,
                 ("плат онлайн: %d — вопрос маршрутом «плата» уйдёт письмом"
                  % len(free)), (time.time() - t) * 1000)
        elif brst.get("online"):
            _add("board", True,
                 ("мост: плата отвечает на %s (v%s) — напиши ей в чате с "
                  "маршрутом «плата»" % (brst.get("url"),
                                         (brst.get("state") or {})
                                         .get("version") or "?")),
                 (time.time() - t) * 1000)
        else:
            _add("board", None,
                 ("плат онлайн нет, мост стучится на %s — запусти мозг "
                  "(main.py), зона оживёт" % brst.get("url", "")), 0)
    except Exception as e:
        _add("board", False, "ошибка: %s" % e, (time.time() - t) * 1000)
    # Генератор: тяжёлую генерацию не запускаем — путь и сеть проверены выше
    _add("gen", True, "готов: пишет «статья <тема>» по сети материалов "
         "(щуп-генерацию не запускаем, чтобы не засорять KB)", 0)
    # Облако: служебная зона всегда при делах
    _add("sys", True, "служебная зона: статус, экскурс, честное «не знаю»", 0)
    # Воображение (v3.16): LaTeX-рендер + пробная сцена (без записи холста)
    t = time.time()
    try:
        import latexlite
        r = latexlite.render(r"\frac{a}{b}+\sqrt{x^2+1}")
        ok_tex = bool(r.get("ok")) and "<svg" in r.get("svg", "")
        import imagine
        sc, ok_sc, _msg = imagine.build_scene("дом и дерево", seed=12345)
        ok_im = bool(sc) and "<svg" in imagine.render_scene(sc)
        _add("imagine", ok_tex and ok_im,
             ("latex: %s, рисование: ок" % ("ок" if ok_tex else "сбой"))
             if (ok_tex and ok_im) else "движок формул или рисование дают сбой",
             (time.time() - t) * 1000)
    except Exception as e:
        _add("imagine", False, "ошибка: %s" % e, (time.time() - t) * 1000)
    # Самокод (v3.18): заготовки + пробный тест одной функции в песочнице
    # (БЕЗ публикации — щуп не засоряет реестр)
    t = time.time()
    try:
        import autocode
        import sandbox as _sb
        tpl = autocode.TEMPLATES[0] if autocode.TEMPLATES else None
        ok_ac = False
        note_ac = "заготовок нет"
        if tpl:
            err = _sb.audit(tpl["code"])
            ok_run, out = _sb.run_code(tpl["code"], tpl["tests"][0][0],
                                       timeout=10)
            ok_ac = not err and ok_run and \
                tpl["tests"][0][1].lower() in str(out).lower()
            note_ac = ("заготовок %d, тест «%s» прошёл" %
                       (len(autocode.TEMPLATES), tpl["name"])) if ok_ac \
                else ("тест заготовки не прошёл: %s %s" % (err, out[:60]))
        _add("code", ok_ac, note_ac, (time.time() - t) * 1000)
    except Exception as e:
        _add("code", False, "ошибка: %s" % e, (time.time() - t) * 1000)
    return 200, {"ok": True, "results": res}


def h_seed_kb(req):
    """Первичные статьи KB (только если база пуста)."""
    if kb.list_articles(1):
        return 200, {"ok": True, "added": 0}
    import textutils as _t
    arts = [
        ("Что такое нейросеть «мозг-12»",
         "«Мозг-12» — небольшая нейросеть на плате Arduino UNO Q и двенадцати "
         "платах-нейронах. Она умеет думать без больших языковых моделей: "
         "зонная сеть выбирает тему ответа, буквогенератор собирает буквы в "
         "слова, словарь частотных слов подсказывает лексику, а нейросеть "
         "материалов (узлы — основы слов, связи — ассоциации) пишет длинные "
         "тексты по прочитанным материалам. LLM в системе только учитель: он "
         "ставит вердикты фразам и даёт советы, но не пишет тексты."),
        ("Как устроено облако neuro12-cloud",
         "Облачная платформа neuro12-cloud объединяет платы-мозги в единый "
         "мозг. Она хранит облачную память каждой платы, синхронизирует "
         "словари русского языка, ведёт базу знаний, реестр функций, принимает "
         "мысли плат и показания нейросетей. Отвечает в чате ассоциативной "
         "сетью материалов без LLM. Связь с платами — мозг-почта: письмо "
         "кладётся в ящик платы, плата отвечает в свой чат."),
        ("Нейросеть материалов (mozgnet)",
         "Нейросеть материалов учится на загруженных файлах, статьях базы "
         "знаний, диалогах и мыслях плат. Узлы сети — основы слов, связи — "
         "выученные ассоциации совместной встречаемости, документы — векторы "
         "tf-idf. Запрос зажигает узлы (разрядка активации по связям), "
         "активные нейроны выбирают материалы и предложения, из которых "
         "собирается связный длинный текст."),
        ("Формулы и математические модели — как мозг-12 считает",
         "Мозг-12 сам пишет формулы и строит математические модели — без "
         "LLM (v3.8). База формул: GET /api/math/formulas (полная — "
         "?full=1: id, имя, ключи-темы, переменные с vkeys и ВЫРАЖЕНИЕ). "
         "Вопрос -> формула -> числа из вопроса -> расчёт с формулой и "
         "подстановкой: «площадь круга радиусом 5» -> S = π·r² = 78.54; "
         "«вклад 1000 под 10 процентов на 3 года» -> X = X₀·(1+p/100)ⁿ = "
         "1331. Вычислитель — безопасный AST-обход с белым списком "
         "функций: произвольный код не исполняется, ошибки честные "
         "(«деление на ноль», «неизвестная переменная»). Модели: по парам "
         "x,y строится линейная (МНК), квадратичная (нормальные уравнения, "
         "Крамер) или экспоненциальная модель, выбирается лучшая по R²; "
         "против переобучения квадратичной нужно не меньше 4 точек, "
         "экспоненте — 3; слабая модель (R² < 0.5) честно названа слабой. "
         "Прогноз: «модель по данным 1,2; 2,4; 3,5 — прогноз 4». В App Lab "
         "кирпичики «Формула: посчитать» и «Модель по данным» (категория "
         "«Математика»), рецепты «Расчёт по формуле» и «Модель и прогноз». "
         "Плата (neuro_12 v2.32) умеет то же в зоне «Математика» и "
         "синхронизирует базу формул из облака."),
        ("App Lab: рецепты — примеры кода из кирпичиков",
         "Приложение App Lab — стопка кирпичиков, и собирать её можно по "
         "рецептам из базы примеров кода (GET /api/applab/recipes). Каждый "
         "рецепт — готовая последовательность операторов: «Отчёт по плате» "
         "= Состояние платы -> Последняя мысль -> Собрать текст -> Результат; "
         "«Спросить плату и запомнить» = Спросить плату -> Запомнить -> "
         "Результат; «Ответ из базы знаний» = Искать в базе знаний -> "
         "Результат; «Справка из Википедии» = Википедия: выжимка -> "
         "Результат; «Расчёт по формуле» = Формула: посчитать -> Результат; "
         "«Модель и прогноз» = Модель по данным -> Результат; «Утренний "
         "дайджест» = Состояние платы -> Подумать -> "
         "Собрать текст -> Результат; «Подумать и записать» = Свободная "
         "мысль -> Запомнить -> Мысль в ленту плат -> Результат; «Что помню "
         "про…» = Что помню про… -> Собрать текст -> Результат. Параметр "
         "{question} заменяется на тему вопроса. Мозг выбирает рецепт по "
         "ключевым словам и сохраняет приложение сам — кирпичик «Собрать "
         "приложение» (brain.compose) или зона «Кирпичики» платы; человек "
         "может собрать то же самое в редакторе App Lab."),
    ]
    added = 0
    for title, text in arts:
        kb.add_article(title, text, source="seed")
        added += 1
    return 200, {"ok": True, "added": added}


# --------------------- самоучитель платформы (KB + сеть) ---------------------
GUIDE = [
    ("Как пользоваться облачной памятью",
     "Облачная память хранит состояние каждой платы на сервере: чаты, словари, "
     "заметки, настройки, мысли. Плата сохраняет бандл сама: POST /api/memory/"
     "save с заголовком X-Mozg-Token, а читает обратно GET /api/memory/load — "
     "данные переживают выключение платы и перезапуск App Lab. Владелец может "
     "положить заметку прямо в чате облака: «запомни: …» — и спросить «что ты "
     "помнишь про …». Память общая: если плата переустановлена, достаточно "
     "указать тот же токен — и весь её опыт вернётся из облака."),
    ("Мозг-почта: как устройства пишут друг другу",
     "Мозг-почта соединяет плату и облако в единый мозг. Письмо кладётся в "
     "ящик платы (POST /api/brain/msg), плата забирает его опросом GET /api/"
     "brain/inbox раз в 30 секунд, думает сама — зонной сетью, без LLM — и "
     "отвечает POST /api/brain/answer. Флаг long разворачивает длинный ответ "
     "с реальными знаниями: память по теме, статьи, файлы, совет наставника. "
     "Опрос inbox держит плату «в сети» в реестре, поэтому письма — ещё и "
     "heartbeat. Просроченные письма убирает дворник облака."),
    ("База знаний (KB): статьи, поиск, файлы",
     "База знаний — общая память знаний облака и плат. Поиск: GET /api/kb/"
     "search?q=… (tf-idf с порогом релевантности), статья целиком: GET /api/"
     "kb/article?id=…, пополнение: POST /api/kb/import (плата учит облако "
     "своими статьями). Файлы владельца (txt, pdf, docx, xlsx, pptx) после "
     "загрузки через панель превращаются в текст и тоже попадают в KB — "
     "потом облако отвечает по ним с бейджем «База знаний». Чем полнее KB, "
     "тем умнее ответы чата."),
    ("Реестр функций: навыки мозга и облака",
     "Реестр функций — это навыки, которые мозг публикует в облако: "
     "математика, время, память, поиск, код-функции. Список: GET /api/skills, "
     "запуск: POST /api/skills/<id>/run. Плата может НАПИСАТЬ функцию сама "
     "(тихий самокод: собирает её из своих заготовок), облако проверяет код "
     "в песочнице и публикует в реестр — после этого навыком пользуются все "
     "устройства. В чате навык вызывается естественной фразой: «посчитай "
     "сколько будет 12 умножить на 7», «сколько времени»."),
    ("Словари русского языка: склейка словарей",
     "Облако хранит частотные словари (ru_top, ru_pos, extra) и СКЛЕИВАЕТ их "
     "со словарями плат: POST /api/dict/put добавляет слова платы, GET /api/"
     "dict/get отдаёт объединённый словарь. Так буквогенератор платы и сеть "
     "материалов облака говорят на одном языке: словарь растёт после каждого "
     "нового устройства. Словарь — топливо учителя: по нему ставятся вердикты "
     "фразам без всякого LLM."),
    ("Нейросеть материалов: как облако пишет тексты",
     "Сеть материалов (mozgnet) — это память облака: узлы — основы слов, "
     "связи — выученные ассоциации, документы — векторы tf-idf. Она учится "
     "на статьях, файлах, диалогах и мыслях плат. Запрос зажигает узлы, "
     "активные нейроны выбирают материалы, из которых собирается связный "
     "текст. Команда «статья <тема>» пишет длинный текст, «мысль <тема>» — "
     "свободное рассуждение. Сеть растёт после каждого сообщения — облако "
     "умнеет на ходу."),
    ("Википедия и веб-статьи без LLM",
     "Если своего ответа в KB и сети не нашлось, облако ищет тему в "
     "Википедии: GET /api/web/fetch — и возвращает готовую выжимку со "
     "ссылкой, бейдж «Википедия». Найденная статья автоматически сохраняется "
     "в базу знаний — в следующий раз ответ придёт уже из KB, быстрее. Это "
     "не LLM: честная выжимка открытой энциклопедии. Управляется флагом "
     "web_on в настройках."),
    ("Генератор текстов и задания от плат",
     "Плата может ЗАКАЗАТЬ облаку текст: POST /api/gen/task кладёт задание в "
     "очередь, фон облака пишет статью по своим материалам и сохраняет её в "
     "KB — плата потом прочитает её оттуда. Владелец делает то же командой "
     "«статья <тема>» в чате или кнопкой генерации. Так большой текст пишет "
     "облако с его сетью материалов, а маленькая плата экономит силы."),
    ("Зрение: камеры плат",
     "Зрение облака собирает кадры со всех камер плат: POST /api/vision/frame "
     "(плата присылает JPEG), MJPEG-стрим в панели, буфер детекций GET /api/"
     "vision/buffer — платы читают чужие детекции и «видят» вместе. Камера "
     "живёт на плате: она снимает и шлёт кадры в облако по сети. У облака "
     "своих камер и COM-портов нет — провода и порты целиком на стороне "
     "платы."),
    ("Голос: речь без языковых моделей",
     "POST /api/tts превращает текст в WAV и работает всегда: если в системе "
     "есть espeak — живая синтезированная речь, иначе встроенный «нейроголос» "
     "с формантами. Плата проигрывает звук на динамик (D8), браузер — кнопкой "
     "«озвучить» под ответом чата. Голос настраивается во вкладке «Голос»: "
     "нейроголос, низкий, высокий, робот."),
    ("Учитель: LLM только для обучения",
     "LLM в платформе никогда не отвечает в чате: /api/llm/ask закрыт (410). "
     "Разрешён только учительский канал /api/llm/teach с тремя целями: "
     "verdict — вердикт «1/0» фразе буквогенератора, word_goal — слово-цель "
     "диктанта, advice — совет, КАК отвечать (не сам ответ). Без внешнего "
     "LLM работает встроенный учитель по словарям. Вкладка «Учитель» в "
     "панели: подключение OpenAI-совместимого ядра, проверка связи и "
     "экскурсия для плат."),
    ("Экскурсия для платы: 12 уроков",
     "Учитель может сам обучить новую плату всем функциям платформы. Скажи "
     "в чате «проведи экскурс» или нажми «Начать экскурс» во вкладке "
     "«Учитель» — и учитель пошлёт плате уроки через мозг-почту (по "
     "одному каждые 90 секунд): память, мозг-почта, KB, функции, словари, "
     "сеть материалов, Википедия, генератор, зрение, голос, учитель и App "
     "Lab. Уроки платформа также кладёт в базу знаний, а ответы платы "
     "оцениваются вердиктом. Экскурс работает и без LLM — встроенным "
     "учителем."),
    ("App Lab: приложения из кирпичиков",
     "App Lab — лаборатория приложений в стиле Arduino App Lab. Приложение "
     "— это стопка КИРПИЧИКОВ, и каждый кирпичик — настоящая функция "
     "платформы: «Спросить плату» (мост 127.0.0.1:7000, плата думает без "
     "LLM), «Подумать над вопросом» (зонная сеть облака), «Искать в базе "
     "знаний», «Запомнить заметку», «Википедия», «Собрать текст», «Если… "
     "то… иначе». Кирпичик кладёт результат в переменную, следующий "
     "кирпичик подставляет её через {имя} или {last} — выход предыдущего. "
     "Приложения собираются в панели (кнопка «App Lab»), запускаются по "
     "шагам с журналом, есть готовые примеры. Так из функций платы и чата "
     "клеятся свои сценарии — без программирования и без LLM. "
     "В редакторе есть РЕЦЕПТЫ — примеры кода в базе данных: нажми «Собрать "
     "из рецепта», и приложение сложится само (рецепты: «Отчёт по плате», "
     "«Спросить плату и запомнить», «Ответ из базы знаний», «Справка из "
     "Википедии», «Утренний дайджест», «Подумать и записать», «Что помню "
     "про…»). А кирпичик «Собрать приложение» — это мозг-программист: он "
     "сам выбирает рецепт под вопрос и сохраняет приложение; то же умеет "
     "зона «Кирпичики» платы (neuro_12 v2.31) — скажи ей «собери приложение "
     "отчёт по плате» в чате."),
    ("Формулы и модели: мозг считает сам",
     "В v3.8 мозг облака и платы сами ПИШУТ ФОРМУЛЫ и СТРОЯТ МОДЕЛИ — без "
     "LLM. Как это работает: (1) база формул — у каждой формулы есть "
     "ключи-темы, переменные и выражение; список: GET /api/math/formulas. "
     "Спроси в чате «площадь круга радиусом 5» или «вклад 1000 под 10 "
     "процентов на 3 года» — мозг выберет формулу, подставит числа и "
     "покажет расчёт. (2) Модели — по парам чисел x,y: «модель по данным "
     "1,2; 2,4; 3,5; 4,9 — прогноз 5»; мозг строит линейную, квадратичную "
     "и экспоненциальную модель, выбирает лучшую по R² и честно "
     "предупреждает, если данные слабые (R² < 0.5). (3) В App Lab — "
     "категория «Математика»: кирпичики «Формула: посчитать» и «Модель по "
     "данным», рецепты «Расчёт по формуле» и «Модель и прогноз». (4) "
     "Вычислитель безопасный: AST с белым списком, произвольный код не "
     "исполняется — кирпичик не станет лазейкой. Плата считает то же "
     "самое в зоне «Математика» и сама скачивает новые формулы из облака."),
]


def seed_platform_guide():
    """Самоучитель платформы: статьи в KB + обучение сети материалов.
    Однократно (маркер data/seed_guide.json) — работает и на уже
    установленных облаках, где база не пуста. Если тексты обновились
    (версия маркера меньше текущей) — старые статьи самоучителя
    заменяются свежими."""
    GUIDE_VER = "3.8.0"
    mark = store.load_json("seed_guide.json", {})
    if mark.get("done") and mark.get("ver") == GUIDE_VER:
        return 0
    import brain
    if mark.get("done"):
        # обновление текстов: убираем прежние статьи самоучителя
        try:
            for a in kb.list_articles(limit=500):
                if a.get("source") == "самоучитель":
                    kb.delete_article(a.get("id"))
        except Exception:
            pass
    added = 0
    for title, text in GUIDE:
        try:
            kb.add_article(title, text, source="самоучитель")
            brain.mn_learn("%s. %s" % (title, text), title=title,
                           src="самоучитель")
            added += 1
        except Exception:
            pass
    store.save_json("seed_guide.json", {"done": True, "ver": GUIDE_VER,
                                        "ts": time.time(), "added": added})
    store.log("СТАРТ", "самоучитель платформы: %d статей в KB и сети"
              % added)
    return added


# ------------------------- авторизация панели -------------------------------
def h_auth_check(req):
    """Публично: есть ли пароль и авторизован ли этот браузер."""
    return 200, {"ok": True,
                 "has_password": auth.has_password(),
                 "authed": auth.session_ok(req.params.get("_sid", ""))}


def h_auth_setup(req):
    """Первичная настройка: создаём пароль панели (если его ещё нет)."""
    if auth.has_password():
        return 403, {"ok": False, "error": "пароль уже задан — войдите"}
    pw = str((req.body or {}).get("password") or "")
    if len(pw) < 4:
        return 400, {"ok": False,
                     "error": "пароль слишком короткий (минимум 4 знака)"}
    auth.set_password(pw)
    sid = auth.create_session(ip=req.client_ip,
                              ua=req.headers.get("User-Agent") or "")
    store.log("АВТОРИЗАЦИЯ", "пароль создан с адреса %s" % req.client_ip)
    return 200, {"ok": True}, {"Set-Cookie": auth.cookie_header(sid)}


def h_auth_login(req):
    if auth.too_many_fails(req.client_ip):
        return 429, {"ok": False,
                     "error": "слишком много попыток — подожди 15 минут"}
    if not auth.has_password():
        return 403, {"ok": False,
                     "error": "сначала создай пароль (форма настройки)"}
    pw = str((req.body or {}).get("password") or "")
    if not auth.verify(pw):
        auth.register_fail(req.client_ip)
        time.sleep(0.7)                  # слегка замедлить подбор
        store.log("АВТОРИЗАЦИЯ", "НЕВЕРНЫЙ пароль с %s" % req.client_ip)
        return 401, {"ok": False, "error": "неверный пароль"}
    auth.clear_fails(req.client_ip)
    sid = auth.create_session(ip=req.client_ip,
                              ua=req.headers.get("User-Agent") or "")
    store.log("АВТОРИЗАЦИЯ", "вход владельца с %s" % req.client_ip)
    return 200, {"ok": True}, {"Set-Cookie": auth.cookie_header(sid)}


def h_auth_logout(req):
    auth.drop_session(req.params.get("_sid", ""))
    return 200, {"ok": True}, {"Set-Cookie": auth.clear_cookie_header()}


def h_auth_logout_all(req):
    auth.drop_all_sessions()
    store.log("АВТОРИЗАЦИЯ", "выход на всех устройствах (%s)" % req.client_ip)
    return 200, {"ok": True}, {"Set-Cookie": auth.clear_cookie_header()}


def h_auth_password(req):
    """Смена пароля панели: {old, new}."""
    b = req.body or {}
    if not auth.verify(str(b.get("old") or "")):
        return 401, {"ok": False, "error": "старый пароль неверен"}
    new = str(b.get("new") or "")
    if len(new) < 4:
        return 400, {"ok": False,
                     "error": "новый пароль слишком короткий (мин. 4 знака)"}
    auth.set_password(new)
    return 200, {"ok": True}


# ----------------------------- мост к плате ----------------------------------
def h_bridge_status(req):
    """Живой статус моста к плате (127.0.0.1:7000 по умолчанию)."""
    st = __import__("bridge").status()
    try:                        # v3.172: плата на связи по каналу /api/link
        import boardlink
        st["link_boards"] = [{"id": b["id"], "age": b.get("age")}
                             for b in boardlink.status().get("boards") or []
                             if b.get("age") is not None and b["age"] < 60]
    except Exception:
        st["link_boards"] = []
    return 200, {"ok": True, "bridge": st}


def h_bridge_config(req):
    """Настройка моста: {url, on}. Пустой url = 127.0.0.1:7000."""
    b = req.body or {}
    patch = {}
    if "url" in b:
        u = str(b.get("url") or "").strip().rstrip("/")
        if u and not u.startswith(("http://", "https://")):
            u = "http://" + u
        if u and len(u) > 120:
            return 400, {"ok": False, "error": "слишком длинный адрес"}
        patch["bridge_url"] = u
    if "on" in b:
        patch["bridge_on"] = bool(b.get("on"))
    if patch:
        store.update_settings(patch)
        store.log("МОСТ", "настройки: %s" % patch)
    __import__("bridge").probe(save=False)      # щуп сразу, не ждать цикла
    return 200, {"ok": True, "bridge": __import__("bridge").status()}


def h_bridge_send(req):
    """Написать плате напрямую через мост (плата думает сама, без LLM)."""
    text = str((req.body or {}).get("text") or "").strip()[:2000]
    if not text:
        return 400, {"ok": False, "error": "пустое сообщение"}
    r = __import__("bridge").send(text, user="панель")
    if r.get("ok") and r.get("answer"):
        try:
            brain.bump_zone(brain.Z["board"])
        except Exception:
            pass
    return 200, r


def h_bridge_probe(req):
    """v3.23.1: проверить связь с платой СЕЙЧАС — не ждать цикла моста
    (15 с). Возврат: статус моста с диагнозом {cause, why, fix}, если
    плата недоступна. Маршрут живёт и на GET, и на POST — «нет маршрута»
    невозможно, каким бы методом панель ни обратилась."""
    br = __import__("bridge")
    br.probe(save=False)
    return 200, {"ok": True, "bridge": br.status()}


def h_bridge_find(req):
    """v3.23.1: найти плату в сети — когда «облако не подключается к плате»,
    а адрес платы неизвестен. Сканируем 127.0.0.1, свои IP и /24-подсети
    (порт из текущего адреса моста + 7000). {apply: true} — первый
    найденный адрес сразу становится адресом моста. Тоже GET+POST."""
    br = __import__("bridge")
    b = req.body or {}
    try:
        ports = [int(p) for p in (b.get("ports") or []) if p][:8]
        res = br.find_board(ports=ports or None)
    except Exception as e:
        return 200, {"ok": False, "error": "поиск не удался: %s" % e,
                     "found": [], "bridge": br.status()}
    applied = ""
    if b.get("apply") and res["found"]:
        u = res["found"][0]["url"]
        store.update_settings({"bridge_url": u})
        br.probe(save=False)
        applied = u
        store.log("МОСТ", "плата найдена в сети: %s — адрес моста "
                           "обновлён" % u)
    res["applied"] = applied
    res["bridge"] = br.status()
    return 200, dict(ok=True, **res)


# ------------------------------- App Lab -------------------------------------
def h_applab_bricks(req):
    """Каталог кирпичиков для палитры (категории + параметры)."""
    return 200, {"ok": True, "cats": applab.bricks_catalog(),
                 "examples": applab.EXAMPLES}


def h_applab_apps(req):
    """v3.170: App Lab на Python — список приложений."""
    import pyapps
    return 200, {"ok": True, "apps": pyapps.apps(), "python": True}


def h_applab_get(req):
    import pyapps
    m = pyapps.get(str(req.query.get("id") or "")[:40], with_code=True)
    if not m:
        return 404, {"ok": False, "error": "нет такого приложения"}
    return 200, {"ok": True, "app": m}


def h_applab_save(req):
    import pyapps
    r = pyapps.save(req.body or {}, origin="владелец")
    return (200 if r.get("ok") else 400), r


def h_applab_delete(req):
    import pyapps
    return 200, pyapps.delete(str((req.body or {}).get("id") or "")[:40])


def h_applab_run(req):
    """Запуск: сохранённого (id) или черновика из редактора (code)."""
    import pyapps
    b = req.body or {}
    text = str(b.get("text") or "")[:2000]
    if b.get("code"):
        err = pyapps.check_code(str(b["code"]))
        if err:
            return 200, {"ok": False, "error": err}
        r = pyapps.run_code(str(b.get("id") or "draft")[:40] or "draft",
                            str(b["code"]), text, board="владелец")
    else:
        r = pyapps.run(str(b.get("id") or "")[:40], text, board="владелец")
    return 200, r


def h_applab_flags(req):
    import pyapps
    b = req.body or {}
    kw = {k: b[k] for k in ("enabled", "chat") if k in b}
    return 200, pyapps.set_flags(str(b.get("id") or "")[:40], **kw)


def h_applab_ai(req):
    """ИИ пишет приложение App Lab по описанию (pyapps.ai_write)."""
    import pyapps
    return 200, pyapps.ai_write(str((req.body or {}).get("desc") or ""))


def h_applab_guide(req):
    import pyapps
    import instructions
    try:
        import codewrite
        llm_ready = codewrite.own_ready()
    except Exception:
        llm_ready = False
    return 200, {"ok": True, "guide": pyapps.APP_GUIDE, "api": pyapps.API_DOC,
                 "llm_guide": pyapps.LLM_GUIDE, "llm_ready": llm_ready,
                 "bricks": [{"id": b["id"], "name": b["name"], "desc": b["desc"],
                             "params": [p_["name"] for p_ in b.get("params") or []]}
                            for b in applab.BRICKS],
                 "instructions": instructions.text()}


def h_applab_chat(req):
    """Приложения для окна чата (вместо типичных вопросов)."""
    import pyapps
    return 200, {"ok": True, "apps": [
        {"id": m["id"], "name": m.get("name"), "icon": m.get("icon"),
         "desc": m.get("desc"), "examples": m.get("examples") or []}
        for m in pyapps.apps(chat_only=True)][:12]}


def h_applab_example(req):
    app, err = applab.example(str((req.body or {}).get("id") or "")[:40])
    if err:
        return 400, {"ok": False, "error": err}
    return 200, {"ok": True, "app": app}


def h_applab_recipes(req):
    """Рецепты — примеры кода в базе данных (для панели и платы).
    full=1 — вернуть полные рецепты с кирпичиками (плата собирает их сама)."""
    recs = applab.recipes_list()
    if (req.query.get("full") or "").strip() in ("1", "да", "true"):
        recs = [{"id": r["id"], "name": r["name"],
                 "bricks": (applab.app_from_recipe(r["id"]) or {}).get(
                     "bricks") or []}
                for r in recs]
    return 200, {"ok": True, "recipes": recs}


def h_math_formulas(req):
    """База формул (v3.8) — для панели и синхронизации платы.
    full=1 — с выражениями и ключами переменных (плата считает сама)."""
    import mathmodels
    full = (req.query.get("full") or "").strip() in ("1", "да", "true")
    return 200, {"ok": True, "formulas": mathmodels.formulas_list(full)}


def h_applab_from_recipe(req):
    """Собрать приложение из рецепта (без сохранения): {id, question?}."""
    b = req.body or {}
    app = applab.app_from_recipe(str(b.get("id") or "")[:40],
                                 str(b.get("question") or ""))
    if not app:
        return 404, {"ok": False, "error": "рецепт не найден"}
    return 200, {"ok": True, "app": app}


def h_bridge_messages(req):
    after = int(req.query.get("after") or 0)
    return 200, __import__("bridge").messages(after)


# ------------------------------ код (v3.9) -----------------------------------
def h_code_read(req):
    """Изучить программный код: POST {name, text} — разбор на функции/
    классы/defines, индекс + KB + сеть материалов. Вопросы потом в чате:
    «что делает функция tts_say?»."""
    b = req.body or {}
    name = str(b.get("name") or "код.py").strip()[:80]
    text = str(b.get("text") or "")[:400000]
    if not text.strip():
        return 200, {"ok": False, "error": "пустой код"}
    import codemind
    r = codemind.ingest(name, text)
    if r.get("ok"):
        try:
            import brain
            brain.mn_learn("Файл кода %s: язык %s, функций %s." % (
                name, r["lang"], r["functions"]),
                title="Код: %s" % name[:50], src="код")
        except Exception:
            pass
        store.log("КОД", "изучен файл %s: функций %s" % (
            name, r.get("functions")))
    return 200, r


def h_code_list(req):
    """Сводка изученного кода (индекс файлов/функций)."""
    import codemind
    return 200, codemind.outline()


# ------------------- v3.10: любопытство нейронов ----------------------------
def h_curiosity_get(req):
    """Статус любопытства: очередь «хочу знать», журнал чтений, статистика."""
    import curiosity
    return 200, {"ok": True, "curiosity": curiosity.status()}


def h_curiosity_post(req):
    """Управление любопытством: POST {action: 'on'|'off'|'now', q: 'тема'}.
    action=now — немедленно прочитать тему q (кнопка панели)."""
    import curiosity
    b = req.body or {}
    action = str(b.get("action") or "").strip()[:12]
    if action == "on" or action == "off":
        return 200, {"ok": True,
                     "curiosity": curiosity.set_enabled(action == "on")}
    if action == "now":
        r = curiosity.learn_now(str(b.get("q") or ""))
        return 200, {"ok": bool(r.get("ok")),
                     "title": r.get("title"), "chars": r.get("chars"),
                     "note": r.get("note"),
                     "curiosity": curiosity.status()}
    return 200, {"ok": False,
                 "error": "action: on | off | now (с темой q)"}


def h_teacher_instruction(req):
    """POST /api/teacher/instruction {text} (v3.59): инструкция учителя.
    Мозг (сеть материалов), своя LLM и плата обращаются к ней как к совету."""
    import teacher
    txt = str((req.body or {}).get("text") or "").strip()[:4000]
    store.update_settings({"teacher_instruction": txt})
    if len(txt) >= 20:
        try:
            import brain
            brain.mn_learn(txt, "Инструкция учителя", "инструкция")
        except Exception:
            pass
    try:
        import syncpoint
        syncpoint.mark("kb", "инструкция учителя", llm=False)
    except Exception:
        pass
    return 200, {"ok": True, "len": len(txt), "now": teacher.instruction()[:80]}


# ------------------- v3.11: смелость предположений --------------------------
def h_hypo_get(req):
    """Статус предположений: счётчики + последние гипотезы."""
    import hypo
    return 200, {"ok": True, "hypo": hypo.status()}


def h_hypo_post(req):
    """Управление предположениями: POST {action: 'verdict'|'clear',
    ok: true|false, id: 'h…'}. verdict — вердикт по последней ждущей
    гипотезе (или по id); clear — забыть всю историю гипотез."""
    import hypo
    b = req.body or {}
    action = str(b.get("action") or "").strip()[:12]
    if action == "verdict":
        r = hypo.verdict_latest(bool(b.get("ok")))
        return 200, {"ok": True, "applied": bool(r),
                     "reply": r or "", "hypo": hypo.status()}
    if action == "clear":
        with hypo._LOCK:
            hypo._save({"list": [], "made": 0, "confirmed": 0,
                        "denied": 0})
        store.log("ГИПОТЕЗА", "история предположений очищена владельцем")
        return 200, {"ok": True, "hypo": hypo.status()}
    return 200, {"ok": False,
                 "error": "action: verdict (ok: true|false) | clear"}


# ------------------- v3.27: реестр навыков (панель владельца) ---------------
def _skill_tests(code, tests):
    """Прогнать тесты навыка: [[вход, ожидаемый кусок ответа]]."""
    import sandbox
    out, passed = [], 0
    for t in (tests or [])[:10]:
        if not isinstance(t, (list, tuple)) or not t:
            continue
        inp = str(t[0])[:500]
        want = str(t[1] if len(t) > 1 else "")[:200]
        ok, res = sandbox.run_code(code, inp, timeout=8)
        good = bool(ok) and (not want or want.lower() in str(res).lower())
        passed += good
        out.append({"in": inp, "want": want, "got": str(res)[:300],
                    "ok": good})
    return passed, out


def h_skill_save(req):
    """Создать/изменить навык из панели (сессия владельца). Для кода —
    аудит песочницы и тесты: не прошли — навык НЕ сохраняется."""
    import skills
    b = dict(req.body or {})
    b["origin"] = "владелец"
    if isinstance(b.get("keywords"), str):
        b["keywords"] = [k.strip() for k in re.split(r"[,;\n]", b["keywords"])
                         if k.strip()]
    tests = b.pop("tests", None) or []
    if str(b.get("kind")) == "code":
        import sandbox
        err = sandbox.audit(str(b.get("code") or ""))
        if err:
            return 200, {"ok": False, "error": "аудит песочницы: " + err}
        if tests:
            passed, res = _skill_tests(str(b.get("code") or ""), tests)
            if passed < len(res):
                return 200, {"ok": False, "tests": res,
                             "error": "тесты: прошло %d из %d — навык не "
                                      "сохранён" % (passed, len(res))}
    r = skills.upsert(b)
    if r.get("ok"):
        store.log("НАВЫКИ", "владелец сохранил навык «%s»" % r.get("id"))
    return 200, r


def h_skill_remove(req):
    import skills
    return 200, skills.delete_skill(str((req.body or {}).get("id") or "")[:40])


def h_skill_try(req):
    """Проверить навык: сохранённый (id) или ещё не сохранённый код."""
    import skills
    import sandbox
    b = req.body or {}
    text = str(b.get("text") or "")[:2000]
    if b.get("code"):
        err = sandbox.audit(str(b["code"]))
        if err:
            return 200, {"ok": False, "error": "аудит песочницы: " + err}
        if b.get("tests"):
            passed, res = _skill_tests(str(b["code"]), b["tests"])
            return 200, {"ok": passed == len(res), "tests": res,
                         "result": "тестов прошло %d из %d" % (passed,
                                                              len(res))}
        ok, out = sandbox.run_code(str(b["code"]), text, timeout=8)
        return 200, {"ok": bool(ok), "result": out, "error": None if ok
                     else out}
    return 200, skills.run_skill(str(b.get("id") or "")[:40], text,
                                 board="панель")


def h_skill_autocode(req):
    """«Мозг, напиши навык сам»: самокод по описанию (заготовки + тесты)."""
    import autocode
    desc = str((req.body or {}).get("desc") or "").strip()[:200]
    if not desc:
        return 200, {"ok": False, "error": "опиши, что должна делать функция"}
    rep, ok = autocode.write_function(desc)
    return 200, {"ok": bool(ok), "result": rep}


def h_agent_rules(req):
    import agent
    return 200, {"ok": True, "rules": agent.rules()}


# ------------------- v3.28: API ИИ для других приложений ---------------------
def _ai_key(req):
    return req.params.get("_ai_key") or {"id": "?", "name": "?"}


def h_v1_info(req):
    import aiapi
    return 200, aiapi.info()


def h_v1_models(req):
    import aiapi
    return 200, {"object": "list", "data": [
        {"id": aiapi.MODEL, "object": "model", "created": 0,
         "owned_by": "neuro12-cloud"}]}


def h_v1_ask(req):
    import aiapi
    b = req.body or {}
    q = b.get("question") or b.get("text") or b.get("message") or ""
    mode = str(b.get("mode") or "auto")
    if mode not in ("auto", "talk", "code"):
        mode = "auto"
    r = aiapi.answer(q, _ai_key(req), session=str(b.get("session") or ""),
                     mode=mode, long_mode=bool(b.get("long")),
                     template=bool(b.get("template")),
                     name=str(b.get("name") or "")[:60])
    return (200 if r.get("ok") else 400), r


def h_v1_chat(req):
    import aiapi
    return aiapi.chat_completions(req.body or {}, _ai_key(req))


def h_v1_kb_search(req):
    b = req.body or {}
    q = str(b.get("q") or req.query.get("q") or "")[:200]
    try:
        lim = max(1, min(10, int(b.get("limit") or 3)))
    except (TypeError, ValueError):
        lim = 3
    return 200, kb.search(q, limit=lim)


def h_v1_rate(req):
    import aiapi
    b = req.body or {}
    try:
        v = float(b.get("v") or 0)
    except (TypeError, ValueError):
        v = 0
    if not v:
        return 400, {"ok": False, "error": "v: 1 (верно) или -1 (неверно)"}
    return 200, aiapi.rate(_ai_key(req), str(b.get("session") or ""), v)


def h_v1_result(req):
    """v3.29: поздний ответ платы (единый мозг) на вопрос API:
    POST {id} — id из поля "pending" ответа /api/v1/ask."""
    import unify
    mid = str((req.body or {}).get("id") or req.query.get("id") or "")[:40]
    if not mid:
        return 400, {"ok": False, "error": "нужен id (поле pending)"}
    r = brain.mail_result(mid)
    if r.get("answer"):
        r["text"] = unify.supplement_text(r, late=True)
    return 200, r


def h_export(req):
    """v3.30: «Скачать облако целиком» (код + данные + обученная сеть).
    GET — {default: пароль ещё заводской}; POST {password} — собрать архив
    и выдать одноразовую ссылку; POST {password, new_password} — сменить."""
    import export
    b = req.body or {}
    if str(getattr(req, "method", "")).upper() != "POST":
        return 200, {"ok": True, "default": export.is_default()}
    if b.get("new_password"):
        return 200, export.set_password(b.get("password"),
                                        b.get("new_password"))
    ok, why = export.check(b.get("password"))
    if not ok:
        return 200, {"ok": False, "error": why}
    r = export.build()
    r["url"] = "/api/export/download?t=" + r.pop("token")
    return 200, r


def h_export_download(req):
    import export
    import os as _os
    path = export.take(req.query.get("t", ""))
    if not path:
        return 404, {"ok": False, "error": "ссылка устарела — собери архив "
                     "заново (она одноразовая, 10 минут)"}
    return 200, "file", (path, "application/zip", _os.path.basename(path))


def h_brain_train(req):
    """v3.71: GET — ход обучения мозга (сети материалов) на базе знаний;
    POST {start: true} — запустить заново; {auto: bool} — автозапуск."""
    import brain
    b = req.body or {}
    if str(getattr(req, "method", "")).upper() == "POST":
        if "auto" in b:
            store.update_settings({"brain_train_auto": bool(b["auto"])})
        if b.get("start"):
            brain.brain_train_start(force=True)
            try:                  # v3.82: платы видят отметку и сами тянут базу
                import syncpoint
                syncpoint.mark("train", "кнопка «Обучить мозг»", llm=False)
            except Exception:
                pass
    return 200, {"ok": True, "train": brain.brain_train_status()}


def h_tinyllm(req):
    """v3.31/33: своя LLM облака: GET — статус, настройки, оценка нагрузки,
    нагрузка компьютера, состояние связи с мозгом; POST {llm_d, llm_layers,
    ..., llm_force, llm_link_*, retrain: true} — сохранить настройки.
    Смена размеров/обучения = обучение заново; галочки связи и параметры
    генерации обучение НЕ перезапускают. Работает и без numpy: настройки и
    оценка нагрузки не зависят от него (llmcfg)."""
    import tinyllm
    import llmcfg
    b = req.body or {}
    if str(getattr(req, "method", "")).upper() == "POST":
        patch = {}
        for k in ("llm_d", "llm_layers", "llm_heads", "llm_ctx", "llm_vocab",
                  "llm_epochs", "llm_batch", "llm_corpus_tokens",
                  "llm_inc_max_steps", "llm_inc_max_min", "llm_plan_steps", "llm_micro_batch", "llm_disk_mb", "llm_answer_sents", "llm_topk", "llm_max_new", "llm_link_every"):
            if k in b:
                try:
                    patch[k] = int(float(b[k]))
                except (TypeError, ValueError):
                    pass
        for k in ("llm_cpu", "llm_lr", "llm_time_h", "llm_step_s", "llm_temp", "llm_topp", "llm_rep",
                  "llm_link_minq", "llm_link_gen_minq"):
            if k in b:
                try:
                    patch[k] = float(b[k])
                except (TypeError, ValueError):
                    pass
        for k in ("llm_force", "llm_wordonly", "llm_use_swap", "llm_disk_state", "llm_answer_long", "llm_link_on", "llm_link_reply",
                  "llm_link_boards", "llm_link_net", "llm_link_gen", "llm_link_ext", "llm_link_read", "llm_link_learn"):
            if k in b:
                patch[k] = bool(b[k])
        shape = ("llm_d", "llm_layers", "llm_heads", "llm_ctx", "llm_vocab",
                 "llm_epochs", "llm_batch", "llm_corpus_tokens", "llm_lr")
        before = store.settings()
        if patch:
            store.update_settings(patch)
        changed = any(k in patch and patch[k] != before.get(k)
                      for k in shape)
        # «обучать всё равно» включили, а модель стояла из-за памяти — пуск
        unblock = bool(patch.get("llm_force")) and not before.get("llm_force")
        err = ""
        try:
            err = str(tinyllm.status().get("error") or "")
        except Exception:
            pass
        if b.get("delete"):                       # v3.116: удалить нейросеть
            try:
                r = tinyllm.delete_model(restart=bool(b.get("restart")))
            except Exception as e:
                return 200, {"ok": False, "error": str(e)}
            out = {"ok": True, "deleted": r}
            try:
                out["llm"] = tinyllm.status()
            except Exception as e:
                out["llm"] = {"error": "%s: %s" % (type(e).__name__, e)}
            return 200, out
        if b.get("learn_now"):
            try:
                tinyllm.learn_new()
            except Exception:
                pass
            try:                                  # v3.57: плате — тоже учиться
                import syncpoint
                syncpoint.mark("train", "кнопка «Обучить»", llm=False)
            except Exception:
                pass
        if b.get("retrain") or changed or (unblock and err):
            try:
                tinyllm.retrain_now()
            except Exception as e:
                return 200, {"ok": False, "error": str(e)}
            try:
                import syncpoint
                syncpoint.mark("train", "переобучение LLM", llm=False)
            except Exception:
                pass
    out = {"ok": True}
    try:
        out["llm"] = tinyllm.status()
    except Exception as e:
        out["llm"] = {"error": "%s: %s" % (type(e).__name__, e)}
    try:
        out["config"] = llmcfg.cfg()
        out["estimate"] = llmcfg.estimate(out["config"])
        try:
            out["recommend"] = llmcfg.recommend()
            import brain
            out["brain_train"] = brain.brain_train_status()
        except Exception:
            pass
    except Exception as e:
        out["config_error"] = "%s: %s" % (type(e).__name__, e)
    try:
        import llmlink
        out["link"] = llmlink.status()
    except Exception:
        pass
    try:
        import onenet
        out["onenet"] = onenet.status()
    except Exception:
        pass
    try:
        import genmod
        out["genmod"] = genmod.status()
    except Exception:
        pass
    try:
        import negfreq
        out["negfreq"] = negfreq.status()
    except Exception:
        pass
    try:
        import sysload
        out["sys"] = sysload.snapshot()
        out["sys"]["gflops"] = llmcfg.bench_gflops()
    except Exception:
        pass
    out["numpy"] = llmcfg.numpy_status()
    # оценка для предложенных значений (без применения)
    if "probe_d" in (req.query or {}) or "probe_d" in b:
        pc = dict(out.get("config") or llmcfg.cfg())
        pairs = (("llm_d", "probe_d", int), ("llm_layers", "probe_layers", int),
                 ("llm_heads", "probe_heads", int),
                 ("llm_ctx", "probe_ctx", int),
                 ("llm_vocab", "probe_vocab", int),
                 ("llm_batch", "probe_batch", int),
                 ("llm_epochs", "probe_epochs", int),
                 ("llm_corpus_tokens", "probe_corpus", int),
                 ("llm_cpu", "probe_cpu", float))
        for key, qk, typ in pairs:
            v = (req.query or {}).get(qk)
            v = b.get(qk, v)
            if v is not None:
                try:
                    pc[key] = typ(float(v))
                except (TypeError, ValueError):
                    pass
        try:
            out["probe"] = llmcfg.estimate(pc)
        except Exception as e:
            out["probe_error"] = "%s: %s" % (type(e).__name__, e)
    return 200, out


def h_llm_think(req):
    """v3.33: «подумать сейчас» — LLM сочиняет мысль и отправляет её в
    модуль мыслей, мозг и платы. POST {topic?}."""
    b = req.body or {}
    import llmlink
    try:
        return 200, llmlink.think_now(str(b.get("topic") or ""))
    except Exception as e:
        return 200, {"ok": False, "reason": "%s: %s" % (type(e).__name__, e)}


def h_llm_numpy(req):
    """v3.33: поставить numpy в тот же Python (кнопка панели)."""
    import llmcfg
    return 200, {"ok": True, "numpy": llmcfg.install_numpy()}


def h_hearing(req):
    """v3.39: слух облака — статус; POST {install: true} ставит Vosk+модель."""
    import hearing
    b = req.body or {}
    if b.get("install"):
        return 200, {"ok": True, "hearing": hearing.install_vosk()}
    if "media_hear" in b:
        store.update_settings({"media_hear": bool(b["media_hear"])})
    return 200, {"ok": True, "hearing": hearing.status(),
                 "on": bool(store.settings().get("media_hear", True))}


def h_llm_chat(req):
    """v3.32: своя LLM + весь мозг: POST {text, ctx?, long?} -> {reply, zone,
    llm}. Все функции платформы доступны через этот вход."""
    b = req.body or {}
    text = str(b.get("text") or b.get("q") or "").strip()
    if not text:
        return 400, {"ok": False, "error": "пустой запрос"}
    import llmlink
    ctx = b.get("ctx") if isinstance(b.get("ctx"), list) else None
    return 200, llmlink.chat(text[:4000], ctx=ctx,
                             sender=str(b.get("sender") or "LLM-чат")[:40],
                             long_mode=bool(b.get("long")))


def h_llm(req):
    """v3.30: локальная LLM и модель со зрением (подключает владелец).
    GET — настройки; POST {llm_answer_on, llm_answer_url, llm_answer_model,
    vision_llm_on, test: true} — сохранить / проверить связь."""
    b = req.body or {}
    out = {"ok": True}
    if str(getattr(req, "method", "")).upper() == "POST":
        patch = {k: b[k] for k in ("llm_answer_on", "llm_answer_url",
                                   "llm_answer_model", "vision_llm_on",
                                   "curiosity_video", "web_learn_kb")
                 if k in b}
        if patch:
            store.update_settings(patch)
        if b.get("test"):
            import llmzone
            try:
                out["test"] = llmzone._call([{"role": "user", "content":
                                              "Ответь одним словом: привет"}],
                                            timeout=60)[:200]
            except Exception as e:
                out["ok"] = False
                out["error"] = "модель не ответила: %s" % e
    s = store.settings()
    out["llm"] = {k: s.get(k) for k in ("llm_answer_on", "llm_answer_url",
                                        "llm_answer_model", "vision_llm_on",
                                        "curiosity_video", "web_learn_kb")}
    return 200, out


def h_link_status(req):
    """v3.102: панель «Связь мозгов: частота и мысли»."""
    import boardlink
    import coherence
    b = req.body or {}
    if str(getattr(req, "method", "")).upper() == "POST" and (
            "sync_k" in b or "sync_on" in b):           # v3.125: синхронизация
        p_ = {}
        if "sync_on" in b:
            p_["sync_on"] = bool(b.get("sync_on"))
        if "sync_k" in b:
            try:
                p_["sync_k"] = max(0, min(60, int(float(b.get("sync_k")))))
            except (TypeError, ValueError):
                pass
        store.update_settings(p_)
    if str(getattr(req, "method", "")).upper() == "POST" and "brain_coh_min" in b:
        try:                    # v3.121: порог связности мыслей мозга
            store.update_settings({"brain_coh_min": max(0.0, min(1.0, float(
                b.get("brain_coh_min"))))})
        except (TypeError, ValueError):
            pass
    try:
        import breath
        _bst = breath.status()
    except Exception:
        _bst = None
    _tm = None
    try:                    # v3.146: тензор модуляции сетей (платы + дыхание)
        import tensormod
        if str(getattr(req, "method", "")).upper() == "POST" and isinstance(b.get("tmod_k"), dict):
            tensormod.set_k(b.get("tmod_k"))
        _tm = tensormod.status()
    except Exception as e:
        _tm = {"ok": False, "error": str(e)[:200]}
    return 200, {"ok": True, "link": boardlink.status(),
                 "coherence": coherence.status(), "breath": _bst, "tmod": _tm}


def h_unify(req):
    """v3.29: панель «Единый мозг»: состояние, счётчики, настройки."""
    import unify
    b = req.body or {}
    if str(getattr(req, "method", "")).upper() == "POST":
        patch = {}
        if "unify_on" in b:
            patch["unify_on"] = bool(b.get("unify_on"))
        if "unify_wait" in b:
            try:
                patch["unify_wait"] = max(0, min(60, float(b["unify_wait"])))
            except (TypeError, ValueError):
                pass
        if patch:
            store.update_settings(patch)
    return 200, {"ok": True, "unify": unify.status()}


def h_ai_keys(req):
    """Панель: ключи API. GET — список; POST {action: create, name} —
    новый ключ (показывается ОДИН раз), {action: off|on|revoke, id}."""
    import aiapi
    b = req.body or {}
    act = str(b.get("action") or "")
    out = {"ok": True}
    if str(getattr(req, "method", "")).upper() == "POST":
        if act == "create":
            key, rec = aiapi.key_create(b.get("name"))
            out.update({"key": key, "created": rec})
        elif act in ("on", "off", "revoke"):
            ok = aiapi.key_set(str(b.get("id") or ""), on=(act == "on"),
                               remove=(act == "revoke"))
            out["ok"] = ok
        elif "ai_api_on" in b or "ai_rate_per_min" in b:
            patch = {}
            if "ai_api_on" in b:
                patch["ai_api_on"] = bool(b.get("ai_api_on"))
            if "ai_rate_per_min" in b:
                try:
                    patch["ai_rate_per_min"] = max(
                        1, min(1000, int(b.get("ai_rate_per_min") or 60)))
                except (TypeError, ValueError):
                    pass
            store.update_settings(patch)
    s = store.settings()
    out.update({"keys": aiapi.keys_list(),
                "ai_api_on": s.get("ai_api_on", True) is not False,
                "ai_rate_per_min": int(s.get("ai_rate_per_min") or 60)})
    return 200, out


def h_kb_stats(req):
    """v3.28: панель «Загрузка в базу»: прочитано страниц по ссылкам,
    документов и знаков в каждом случае + прогресс ссылок, что читаются
    сейчас (Confluence в фоне)."""
    st = kb.stats()
    reading = []
    try:
        for x in study.links():
            if not x.get("reading"):
                continue
            if x.get("attach_pages"):
                done, total = x.get("attach_page") or 0, x.get("attach_pages")
                what = "вложения"
            else:
                done, total = x.get("pages_read") or 0, x.get("pages_total") or 0
                what = "страницы"
            reading.append({"title": str(x.get("title") or x.get("url"))[:80],
                            "url": x.get("url"), "what": what,
                            "done": int(done or 0), "total": int(total or 0),
                            "chars": int(x.get("chars") or 0) +
                            int(x.get("attach_chars") or 0)})
    except Exception:
        pass
    st["reading"] = reading
    return 200, dict(ok=True, **st)


def h_kbimg(req):
    """v3.124: иллюстрация статьи базы знаний (kbimg/<файл>)."""
    import kbimages
    data, ct = kbimages.read(req.query.get("f", ""))
    if not data:
        return 404, {"ok": False, "error": "нет такой иллюстрации"}
    return 200, "raw", (data, ct)


# --------------------------- функции мозга ----------------------------------
ROUTES_UI = {}


def _r(method, pattern, fn, mode="panel"):
    ROUTES_UI[(method, pattern)] = (fn, mode)


_r("POST", "api/chat/new", h_chat_new)
_r("GET", "api/chat/list", h_chat_list)
_r("GET", "api/chat/get", h_chat_get)
_r("GET", "api/chat/poll", h_chat_poll)
_r("POST", "api/chat/rename", h_chat_rename)
_r("POST", "api/chat/delete", h_chat_delete)
_r("POST", "api/chat/send", h_chat_send)
_r("GET", "api/chat/activity", h_chat_activity)
_r("POST", "api/chat/gen", h_chat_gen)
_r("POST", "api/chat/rate", h_chat_rate)       # v3.28: 👍/👎 — обучение
_r("GET", "api/qa/stats", h_qa_stats)
_r("POST", "api/qa/suggest", h_qa_suggest)
_r("GET", "api/status", h_status)
_r("POST", "api/teacher/tour", h_teacher_tour)
_r("GET", "api/teacher/thoughts", h_teacher_thoughts)
def h_web_engines(req):
    """v3.167: проверка поисковиков (кнопка в настройках): Поиск Mail.ru,
    Mojeek, Google/Яндекс по ключу, Википедия и Рувики."""
    import webfetch
    q = str((req.body or {}).get("q") or "фотосинтез")[:80]
    tests = [("Поиск Mail.ru", webfetch.mailru_search, "mailru", ""),
             ("Mojeek", webfetch.mojeek_search, "mojeek", ""),
             ("Google (ключ API)", webfetch.google_api, "google-api", "google_api_key"),
             ("Яндекс (ключ API)", webfetch.yandex_api, "yandex-api", "yandex_api_key"),
             ("Википедия", lambda q_, n: webfetch._wiki_lang("ru", q_, n, []), "", ""),
             ("Рувики", webfetch.ruwiki_search, "", "")]
    try:                                    # v3.168: документация Confluence
        import sources                     # v3.174: все источники по ссылкам
        if sources.enabled():
            tests.append(("Источники по ссылкам",
                          lambda q_, n: sources.search(q_, n)[0], "", ""))
    except Exception:
        pass
    out = {}
    for name, fn, st_key, need in tests:
        if need and not store.settings().get(need):
            out[name] = {"found": 0, "via": "", "err": "ключ не задан — пропущен"}
            continue
        try:
            res = fn(q, 5) or []
        except Exception as e:
            res, err = [], str(e)[:100]
        else:
            err = ""
        if not res and name.startswith("Источники"):
            import sources
            err = "; ".join("%s: %s" % (x.get("name") or x["url"], x.get("state_err"))
                            for x in sources.sources() if x.get("state_err")) or err
        if not res and st_key:
            err = (webfetch.engines_state().get(st_key) or {}).get("err") or err
        if not res and not err:
            try:
                err = "пусто: " + "; ".join(str(x) for x in (webfetch.diag_tail() or [])[-2:])
            except Exception:
                err = "пусто"
        out[name] = {"found": len(res), "via": "", "err": "" if res else err}
    store.log("ПОИСК", "проверка поисковиков: " + ", ".join(
        "%s=%s" % (k, v["found"]) for k, v in out.items()))
    return 200, {"ok": True, "engines": out}


_r("POST", "api/web/engines", h_web_engines)


def h_brainctl(req):
    """v3.166: строгая связь с мозгом — ворота процессов, ведущая мысль."""
    import brainctl
    return 200, dict(brainctl.status(), ok=True)


_r("GET", "api/brainctl", h_brainctl)


def h_sources(req):
    """v3.174: источники для поиска по ссылкам (Confluence, вики, сайты, документы)."""
    import sources
    return 200, {"ok": True, "sources": sources.sources(), "on": store.settings().get("sources_on", True)}


def h_sources_act(req):
    import sources
    b = req.body or {}
    act = str(b.get("act") or "")
    sid = str(b.get("id") or "")[:20]
    if act == "add":
        return 200, sources.add(b.get("url"), b.get("name"), b.get("user"), b.get("password"),
                                b.get("token"), b.get("space"), b.get("max_pages"))
    if act == "remove":
        return 200, sources.remove(sid)
    if act == "on":
        return 200, sources.set_on(sid, b.get("on"))
    if act == "probe":
        return 200, sources.probe(sid)
    if act == "reindex":
        import threading as _t
        _t.Thread(target=sources.reindex, args=(sid,), daemon=True).start()
        return 200, {"ok": True}
    if act == "search":
        q = str(b.get("q") or "")[:200]
        res, errs = sources.search(q, 8)
        ans = sources.answer(q)
        return 200, {"ok": True, "results": [{k: r.get(k) for k in ("title", "url", "src", "score")}
                                             for r in res], "errors": errs, "answer": ans}
    return 400, {"ok": False, "error": "неизвестное действие"}


_r("GET", "api/sources", h_sources)
_r("POST", "api/sources", h_sources_act)


def h_llm_web(req):
    """v3.169: что своя LLM прочитала в интернете сама; POST — прочитать сейчас."""
    import llmweb
    if req.method == "POST":
        t = str((req.body or {}).get("topic") or "").strip()[:120]
        r = llmweb.read_once(t or None, why="по кнопке владельца")
        return 200, dict(llmweb.status(), ok=True, result=r)
    return 200, dict(llmweb.status(), ok=True)


_r("GET", "api/llm/web", h_llm_web)
_r("POST", "api/llm/web", h_llm_web)
_r("GET", "api/settings", h_settings_get)
_r("POST", "api/settings", h_settings_post)
_r("POST", "api/teacher/instruction", h_teacher_instruction)
_r("GET", "api/chronicle", h_chronicle)
_r("GET", "api/zones", h_zones)
_r("POST", "api/zones/check", h_zones_check)
_r("POST", "api/seed/kb", h_seed_kb)
# v3.27: реестр навыков из панели и правила ИИ-агента
_r("POST", "api/skills/save", h_skill_save)
_r("POST", "api/skills/remove", h_skill_remove)
_r("POST", "api/skills/try", h_skill_try)
_r("POST", "api/skills/autocode", h_skill_autocode)
_r("GET", "api/agent/rules", h_agent_rules)
_r("GET", "api/kb/stats", h_kb_stats)          # v3.28: панель загрузки
# v3.28: API ИИ для других приложений (ключ Bearer, режим "ai" в app.py)
_r("GET", "api/v1/info", h_v1_info, mode="ai")
_r("GET", "api/v1/models", h_v1_models, mode="ai")
_r("POST", "api/v1/ask", h_v1_ask, mode="ai")
_r("POST", "api/v1/chat/completions", h_v1_chat, mode="ai")
_r("POST", "api/v1/kb/search", h_v1_kb_search, mode="ai")
_r("POST", "api/v1/rate", h_v1_rate, mode="ai")
_r("POST", "api/v1/result", h_v1_result, mode="ai")     # v3.29
_r("POST", "api/llm/chat", h_llm_chat)               # v3.32: LLM + мозг
_r("POST", "api/llm/think", h_llm_think)             # v3.33: мысль LLM сейчас
_r("POST", "api/tinyllm/numpy", h_llm_numpy)
def h_doom_state(req):
    """v3.127: мир игры «РОК-12» для страницы /doom."""
    import doomgame
    return 200, doomgame.state(req.query.get("mapver", "0"))


def h_doom_input(req):
    import doomgame
    return 200, doomgame.human_input(req.params.get("_sid", ""), req.body or {})


def h_doom_control(req):
    import doomgame
    r = doomgame.control(req.body or {})
    return (200 if r.get("ok") else 400), r


def h_gubble_state(req):
    """v3.181: мир игры «Gubble 2» для страницы /gubble."""
    import gubble
    return 200, gubble.state(req.query.get("mapver", "0"))


def h_gubble_input(req):
    import gubble
    return 200, gubble.human_input(req.params.get("_sid", ""), req.body or {})


def h_gubble_control(req):
    import gubble
    r = gubble.control(req.body or {})
    return (200 if r.get("ok") else 400), r


def h_gamechat(req):
    """v3.181: общий чат плата↔облако↔боты↔LLM (страницы игр и панель)."""
    import gamechat
    if req.method == "POST":
        b = req.body or {}
        text = str(b.get("text") or "").strip()[:400]
        if not text:
            return 400, {"ok": False, "error": "пустое сообщение"}
        who = str(b.get("who") or "владелец").strip()[:20] or "владелец"
        m = gamechat.post(who, text, kind="chat", game=str(b.get("game") or ""),
                          src="human:" + str(req.params.get("_sid", ""))[:8])
        return 200, {"ok": True, "msg": m}
    msgs, seq = gamechat.since(req.query.get("since", "0"), limit=60)
    try:
        import games
        gst = games.status()
    except Exception:
        gst = {}
    return 200, {"ok": True, "seq": seq, "msgs": msgs, "games": gst}


def h_games(req):
    """v3.181: арбитр игр — где своя LLM, доступность игр."""
    import games
    return 200, dict(ok=True, **games.status())


def h_senses(req):
    """v3.128: глаза и уши: состояние, «что видишь», «что слышишь»."""
    import senses
    what = (req.query.get("what") or (req.body or {}).get("what") or "status")
    if what == "see":
        t, _i = senses.see_answer(str((req.body or {}).get("q") or req.query.get("q") or ""))
        return 200, {"ok": True, "text": t}
    if what == "hear":
        return 200, {"ok": True, "heard": senses.heard(30)}
    return 200, senses.status()


def h_worddict(req):
    """v3.133: общий словарь мозга (плата + облако) — для панели."""
    import worddict
    return 200, dict(ok=True, **worddict.status(int(req.query.get("top", "40") or 40)))


_r("GET", "api/worddict", h_worddict)                     # v3.133: общий словарь
_r("GET", "api/senses", h_senses)                         # v3.128: глаза и уши
_r("POST", "api/senses", h_senses)
_r("GET", "api/doom/state", h_doom_state)                 # v3.127: игра
_r("POST", "api/doom/input", h_doom_input)
_r("POST", "api/doom/control", h_doom_control)
_r("GET", "api/gubble/state", h_gubble_state)             # v3.181: Gubble 2
_r("POST", "api/gubble/input", h_gubble_input)
_r("POST", "api/gubble/control", h_gubble_control)
_r("GET", "api/gamechat", h_gamechat)                     # v3.181: общий чат
_r("POST", "api/gamechat", h_gamechat)
_r("GET", "api/games", h_games)
_r("POST", "api/hearing", h_hearing)
_r("GET", "api/hearing", h_hearing)         # v3.33: поставить numpy
_r("GET", "api/brain/train", h_brain_train)
_r("POST", "api/brain/train", h_brain_train)
_r("GET", "api/tinyllm", h_tinyllm)                      # v3.31: своя LLM
_r("POST", "api/tinyllm", h_tinyllm)
_r("GET", "api/llm", h_llm)                              # v3.30: локальная LLM
_r("POST", "api/llm", h_llm)
_r("GET", "api/export", h_export)                        # v3.30: выгрузка
_r("POST", "api/export", h_export)
_r("GET", "api/export/download", h_export_download)
_r("GET", "api/unify", h_unify)                          # v3.29: единый мозг
_r("GET", "api/link/status", h_link_status)               # v3.102: связь мозгов
_r("GET", "api/kbimg", h_kbimg)                           # v3.124: иллюстрации базы
_r("POST", "api/link/status", h_link_status)              # v3.121: порог связности
_r("POST", "api/unify", h_unify)


# --------------------- v3.143: обучение всех сетей на материалах ------------
def h_learn_all(req):
    """POST — запустить обучение всех нейросетей на материалах;
    GET — статус текущего/последнего прогона."""
    import learnall
    if (req.method or "").upper() == "GET":
        return 200, {"ok": True, "status": learnall.status()}
    b = req.body or {}
    return 200, learnall.start({"full": bool(b.get("full"))})


def h_materials_delete_all(req):
    """POST {confirm: true} — удалить все материалы (KB + сеть материалов).
    Веса LLM и настройки не трогает."""
    import learnall
    b = req.body or {}
    return 200, learnall.delete_materials(confirm=bool(b.get("confirm")))


def h_curriculum(req):
    """POST — загрузить программу обучения и запустить учёбу."""
    import curriculum
    return 200, curriculum.start_training()


def h_teacher_push(req):
    """POST {articles: [{title, text}, ...], train: true}
    Внешний учитель (Grok или другой ИИ) присылает статьи.
    Они попадают в базу знаний, затем запускается обучение."""
    b = req.body or {}
    arts = b.get("articles") or []
    if not isinstance(arts, list) or not arts:
        return 400, {"ok": False, "error": "нужен список articles"}
    import kb
    n = 0
    for a in arts[:50]:
        if not isinstance(a, dict):
            continue
        title = str(a.get("title") or "").strip()[:200]
        text = str(a.get("text") or "").strip()[:20000]
        if len(title) < 2 or len(text) < 10:
            continue
        try:
            r = kb.add_article(title, text, source="внешний учитель")
            if r and r.get("ok"):
                n += 1
        except Exception:
            pass
    train = bool(b.get("train", True))
    st = None
    if train and n:
        try:
            import learnall
            st = learnall.start({"full": False, "why": "внешний учитель"})
        except Exception as e:
            st = {"ok": False, "error": str(e)[:200]}
    return 200, {"ok": True, "loaded": n, "train": st}


def _owner(req):
    """v3.150: только владелец с сессией панели (токен платы не годится)."""
    return bool(req.params.get("_sid"))


def h_desktop(req):
    """v3.150: РАБОЧИЙ СТОЛ компьютера облака.
    GET ?shot=1[&w=1600] — снимок экрана (jpeg); GET ?shot=<файл> — снимок
    из чата; GET — состояние (вкл., размер экрана, окна).
    POST {act: click|move|scroll|type|key|open|focus|windows, …}."""
    import desktop
    if not _owner(req):
        return 403, {"ok": False, "error": "рабочий стол — только владельцу в панели"}
    if not desktop.enabled():
        return 200, {"ok": False, "on": False, "error": desktop._OFF}
    q = req.query or {}
    if req.method == "GET":
        shot = str(q.get("shot") or "")
        if shot.startswith("shot_"):
            data, ct = desktop.read_shot(shot)
            return (200, "raw", (data, ct)) if data else (404, {"ok": False, "error": "нет снимка"})
        if shot:
            try:
                w = max(320, min(3840, int(q.get("w") or 1600)))
            except Exception:
                w = 1600
            data, ct = desktop.grab(w)
            if not data:
                return 500, {"ok": False, "error": ct}
            return 200, "raw", (data, ct)
        return 200, {"ok": True, "on": True, "screen": desktop.screen(),
                     "windows": desktop.windows(), "win": desktop.WIN}
    return 200, desktop.act(req.body or {})


def h_terminal(req):
    """POST {cmd} — выполнить команду. GET — история.
    POST {rotate_mac: true, iface, key_path, host} — сменить MAC."""
    import terminal
    if not _owner(req):
        return 403, {"ok": False, "error": "терминал — только владельцу в панели"}
    if req.method == "GET":
        return 200, {"ok": True, "on": terminal.enabled(),
                     "history": terminal.history()}
    b = req.body or {}
    if b.get("screenshot"):
        return 200, terminal.screenshot()
    if b.get("rotate_mac"):
        return 200, terminal.rotate_mac(
            b.get("iface") or "en0",
            b.get("key_path"),
            b.get("host") or "localhost")
    return 200, terminal.run(b.get("cmd") or "",
                             timeout=int(b.get("timeout") or 60))


def h_board_src(req):
    """GET — список файлов. POST {op, name, text} — read/write/restore."""
    import board_src
    if req.method == "GET":
        return 200, {"ok": True, "files": board_src.list_files()}
    b = req.body or {}
    op = str(b.get("op") or "read")
    name = b.get("name") or ""
    if op == "read":
        return 200, board_src.read(name)
    if op == "write":
        return 200, board_src.write(name, b.get("text") or "")
    if op == "restore":
        return 200, board_src.restore(name or None)
    if op == "push":
        return 200, board_src.push_to_board(name)
    return 400, {"ok": False, "error": "op: read|write|restore|push"}


_r("GET", "api/board/src", h_board_src)
_r("POST", "api/board/src", h_board_src)
_r("GET", "api/desktop", h_desktop)                       # v3.150: рабочий стол
_r("POST", "api/desktop", h_desktop)
_r("GET", "api/terminal", h_terminal)
_r("POST", "api/terminal", h_terminal)
_r("POST", "api/learn/teacher", h_teacher_push)
_r("POST", "api/learn/curriculum", h_curriculum)
_r("GET", "api/learn/all", h_learn_all)
_r("POST", "api/learn/all", h_learn_all)
_r("POST", "api/materials/delete_all", h_materials_delete_all)
_r("GET", "api/ai/keys", h_ai_keys)
_r("POST", "api/ai/keys", h_ai_keys)
_r("POST", "api/boards/ping", api_board.h_boards_ping)

# --- v3.24.2: ссылки-инструкции (панель -> база знаний); GET и POST —
# урок «нет маршрута»: страница может обратиться любым методом
_r("GET", "api/study/links", h_study_links)
_r("POST", "api/study/links", h_study_links)
_r("POST", "api/study/check", h_study_check)

# --- авторизация (setup/login/check — публичные, см. app.py) ---
_r("GET", "api/auth/check", h_auth_check, mode="open")
_r("POST", "api/auth/setup", h_auth_setup, mode="setup")
_r("POST", "api/auth/login", h_auth_login, mode="login")
_r("POST", "api/auth/logout", h_auth_logout)
_r("POST", "api/auth/logout-all", h_auth_logout_all)
_r("POST", "api/auth/password", h_auth_password)

# --- мост к плате (127.0.0.1:7000) ---
_r("GET", "api/bridge/status", h_bridge_status)
_r("POST", "api/bridge/config", h_bridge_config)
_r("POST", "api/bridge/send", h_bridge_send)
_r("GET", "api/bridge/messages", h_bridge_messages)
# v3.23.1: проверка связи и поиск платы — оба метода, «нет маршрута» нет
_r("GET", "api/bridge/probe", h_bridge_probe)
_r("POST", "api/bridge/probe", h_bridge_probe)
_r("GET", "api/bridge/find", h_bridge_find)
_r("POST", "api/bridge/find", h_bridge_find)

# --- v3.9: чтение программного кода (codemind) ---
def h_codegen(req):
    """v3.22: мозг пишет код по просьбе. POST {text, lang?} -> {reply, ...}."""
    import codegen
    b = req.body or {}
    text = str(b.get("text") or "").strip()[:20000]
    if not text:
        return 400, {"ok": False, "error": "пустая просьба"}
    reply, matched, info = codegen.answer(text, lang=str(b.get("lang") or ""))
    if not matched:
        return 200, {"ok": False, "error": "это не просьба про код",
                     "hint": "Опиши задачу: «напиши на python факториал 10»"}
    return 200, {"ok": True, "reply": reply, "lang": info.get("code_lang"),
                 "task": info.get("code_task")}


def h_codegen_catalog(req):
    import codegen
    return 200, dict(ok=True, **codegen.catalog())


def h_codegen_run(req):
    """v3.22: запуск Python-блока из чата в песочнице облака (только Python)."""
    import codegen
    b = req.body or {}
    code = str(b.get("code") or "")[:30000]
    if not code.strip():
        return 400, {"ok": False, "error": "пустой код"}
    ok, out = codegen.run_python(code, str(b.get("text") or "")[:2000])
    store.log("КОД", "запуск из чата: %s (%s знаков)" % ("OK" if ok else "FAIL", len(code)))
    return 200, ({"ok": True, "output": out} if ok else {"ok": False, "error": out})


_r("POST", "api/code/gen", h_codegen)
_r("GET", "api/code/catalog", h_codegen_catalog)
_r("POST", "api/code/run", h_codegen_run)
_r("POST", "api/code/read", h_code_read)
_r("GET", "api/code/list", h_code_list)

# --- v3.10: любопытство нейронов (чтение Википедии по своей воле) ---
_r("GET", "api/curiosity", h_curiosity_get)
_r("POST", "api/curiosity", h_curiosity_post)
_r("GET", "api/hypo", h_hypo_get)
_r("POST", "api/hypo", h_hypo_post)
# v3.12: речь из словаря — состояние и сборка фразы по теме
_r("GET", "api/speech", h_speech_get)
_r("POST", "api/speech", h_speech_post)

# --- App Lab: приложения из кирпичиков ---
_r("GET", "api/applab/bricks", h_applab_bricks)
_r("GET", "api/applab/apps", h_applab_apps)
_r("GET", "api/applab/get", h_applab_get)
_r("POST", "api/applab/save", h_applab_save)
_r("POST", "api/applab/delete", h_applab_delete)
_r("POST", "api/applab/run", h_applab_run)
_r("POST", "api/applab/flags", h_applab_flags)
_r("POST", "api/applab/ai", h_applab_ai)
_r("GET", "api/applab/guide", h_applab_guide)
_r("GET", "api/applab/chat", h_applab_chat)
_r("POST", "api/applab/example", h_applab_example)
_r("GET", "api/applab/recipes", h_applab_recipes)
_r("POST", "api/applab/from_recipe", h_applab_from_recipe)
_r("GET", "api/math/formulas", h_math_formulas)


def h_settings_token(req):
    """Сохранить токен платы из панели (FormData token=...)."""
    tok = str((req.files_data.get("fields") or {}).get("token") or
              (req.body or {}).get("token") or "").strip()
    if not tok:
        return 200, {"ok": False, "error": "пустой токен"}
    store.set_token(tok)
    store.log("НАСТРОЙКИ", "токен платы обновлён через панель")
    return 200, {"ok": True}


ROUTES_UI[("POST", "api/settings/token")] = (h_settings_token, "panel")


# ------------------- v3.19: обновление облака из zip -------------------------
def h_update_status(req):
    """Состояние обновления: версия, последний пакет, резервные копии."""
    return 200, {"ok": True, "update": updater.status()}


def h_update_upload(req):
    """Загрузить zip-пакет новой версии (поле «zip» в multipart) и обновить
    облако. Данные (data/) не трогаются, перед заменой делается копия,
    сервер перезапускается сам. mode=panel — только владелец."""
    f = req.files_data.get("file") or b""
    if not f:
        f = req.raw or b""
    if not f:
        return 200, {"ok": False,
                     "error": "файл не передан — выбери zip-пакет"}
    try:
        r = updater.apply_zip(f, restart=True, actor="панель")
    except updater.UpdErr as e:
        store.log("ОБНОВЛЕНИЕ", "пакет отклонён: %s" % e)
        return 200, {"ok": False, "error": str(e)}
    except Exception as e:
        store.log("ОБНОВЛЕНИЕ", "ошибка применения пакета: %r" % e)
        return 200, {"ok": False,
                     "error": "не смог применить пакет: %s: %s"
                              % (type(e).__name__, e)}
    return 200, {"ok": True, "update": r}


def h_update_rollback(req):
    """Откат к резервной копии: POST {backup: 'pre-3.18.0-…'}."""
    name = str((req.body or {}).get("backup") or "").strip()
    if not name:
        return 200, {"ok": False, "error": "не указана копия (backup)"}
    try:
        r = updater.rollback(name, restart=True)
    except updater.UpdErr as e:
        return 200, {"ok": False, "error": str(e)}
    except Exception as e:
        return 200, {"ok": False, "error": "откат не удался: %s: %s"
                                              % (type(e).__name__, e)}
    return 200, {"ok": True, "update": r}


def h_update_restart(req):
    """Перезапустить облако вручную (кнопка панели)."""
    r = updater.schedule_restart("кнопка панели")
    return 200, {"ok": r.get("mode") == "auto", "restart": r}


_r("GET", "api/update/status", h_update_status)
_r("POST", "api/update/upload", h_update_upload)
_r("POST", "api/update/rollback", h_update_rollback)
_r("POST", "api/update/restart", h_update_restart)


# --------------------- v3.20: бот в мессенджере МАКС -------------------------
def h_maxbot_status(req):
    """Состояние бота МАКС: включён, токен, кто я, чаты, статистика."""
    return 200, {"ok": True, "maxbot": maxbot.status()}


def h_maxbot_config(req):
    """Сохранить токен и/или вкл-выкл бота. Токен сразу проверяется на
    реальном API (GET /me) — честный результат в ответе.
    v3.23: ещё минимальная длина статьи из групп (maxbot_min_learn) и
    пауза между статьями (maxbot_learn_gap, сек).
    v3.26: память диалога (maxbot_memory) — бот в МАКС помнит последние
    реплики чата и понимает продолжения."""
    b = req.body or {}
    patch = {}
    if "maxbot_token" in b:
        patch["maxbot_token"] = str(b.get("maxbot_token") or "").strip()
    if "maxbot_on" in b:
        patch["maxbot_on"] = bool(b.get("maxbot_on"))
    if "maxbot_memory" in b:
        patch["maxbot_memory"] = bool(b.get("maxbot_memory"))
    for k in ("max_cloud_reply", "max_brain_reply"):      # v3.30: галочки
        if k in b:
            patch[k] = bool(b.get(k))
    if "maxbot_min_learn" in b:
        try:
            v = int(b.get("maxbot_min_learn"))
        except (TypeError, ValueError):
            v = 10
        patch["maxbot_min_learn"] = max(1, min(100000, v))
    if "maxbot_learn_gap" in b:
        try:
            v = int(b.get("maxbot_learn_gap"))
        except (TypeError, ValueError):
            v = 20
        patch["maxbot_learn_gap"] = max(0, min(3600, v))
    if not patch:
        return 200, {"ok": False, "error": "пустая настройка"}
    s = store.update_settings(patch)
    probe = {"ok": None, "error": "", "bot": {}}
    if s.get("maxbot_on") and (s.get("maxbot_token") or "").strip():
        try:
            probe["bot"] = maxbot.me(refresh=True)
            probe["ok"] = True
            try:
                probe["chats"] = maxbot.refresh_chats()
            except maxbot.MaxErr as e:
                probe["chats_err"] = str(e)
        except maxbot.MaxErr as e:
            probe["ok"] = False
            probe["error"] = str(e)
        except Exception as e:
            probe["ok"] = False
            probe["error"] = "%s: %s" % (type(e).__name__, e)
    maxbot.wake()                          # polling не ждёт таймаута
    return 200, {"ok": True, "settings": s, "probe": probe}


def h_maxbot_probe(req):
    """Обновить вручную: кто я + список чатов (кнопка панели).
    v3.23: работает и на GET, и на POST (см. регистрацию ниже)."""
    try:
        bot = maxbot.me(refresh=True)
        chats = maxbot.refresh_chats()
        return 200, {"ok": True, "bot": bot, "chats": chats}
    except maxbot.MaxErr as e:
        return 200, {"ok": False, "error": str(e)}
    except Exception as e:
        return 200, {"ok": False, "error": "%s: %s"
                                              % (type(e).__name__, e)}


# v3.23: и GET, и POST — раньше был только POST, и при запросе GET (или
# после частичного обновления со старым app.js) панель получала честное
# «нет маршрута /api/maxbot/probe». Теперь маршрут один и тот же для обоих
# методов — ошибка маршрута исключена в принципе.
_r("GET", "api/maxbot/status", h_maxbot_status)
_r("POST", "api/maxbot/status", h_maxbot_status)
_r("GET", "api/maxbot/config", h_maxbot_config)
_r("POST", "api/maxbot/config", h_maxbot_config)
_r("GET", "api/maxbot/probe", h_maxbot_probe)
_r("POST", "api/maxbot/probe", h_maxbot_probe)


def h_maxbot_wipe(req):
    """v3.31: стереть из базы знаний выученное ботом из чатов МАКС
    (статьи из групп, история чатов, документы) и сбросить дедуп/водяные
    знаки сканов — чтобы перечитать с новым контекстом."""
    b = req.body or {}
    return 200, maxbot.wipe_learned(str(b.get("what") or "all"))


_r("POST", "api/maxbot/wipe", h_maxbot_wipe)


def h_maxbot_scan(req):
    """v3.24: СКАН ИСТОРИИ ЧАТА — кнопка панели «Скан истории чата»:
    загрузить все документы и сообщения чата в память нейросети
    (база знаний + data/files/МАКС).
    POST {"chat_id": "<id>"} — скан одного чата; {"chat_id": ""} — всех
    известных чатов; {"pages": 1..1000} — бюджет страниц с новыми
    сообщениями; {"stop": true} — мягкая остановка.
    GET — только состояние. ОБА метода всегда отвечают состоянием скана
    (урок «нет маршрута»: маршрут зарегистрирован и на GET, и на POST)."""
    b = req.body or {}
    answer = {"ok": True, "started": False, "stopped": False}
    if str(getattr(req, "method", "") or "").upper() == "POST":
        if b.get("stop"):
            res = maxbot.scan_stop()
            answer["stopped"] = bool(res.get("ok"))
            if not res.get("ok"):
                answer["ok"] = False
                answer["error"] = res.get("error") or "не остановить"
        elif ("chat_id" in b) or b.get("start"):
            res = maxbot.scan_history(chat_id=str(b.get("chat_id") or ""),
                                      pages=b.get("pages"))
            answer["started"] = bool(res.get("ok"))
            if not res.get("ok"):
                answer["ok"] = False
                answer["error"] = res.get("error") or "скан не запустился"
        else:
            answer["ok"] = False
            answer["error"] = ("передай {chat_id: \"\"} или "
                               "{chat_id: \"<id>\", чтобы запустить скан, "
                               "или {stop: true}, чтобы остановить")
    answer["scan"] = maxbot.scan_state()
    return 200, answer


_r("GET", "api/maxbot/scan", h_maxbot_scan)
_r("POST", "api/maxbot/scan", h_maxbot_scan)


# --------------------- v3.40: бот в Telegram + слух из файла -----------------
def h_tg_status(req):
    return 200, {"ok": True, "tgbot": tgbot.status()}


def h_tg_config(req):
    b = req.body or {}
    patch = {}
    for k in ("tg_token", "tg_proxy", "tg_api_base", "tg_allowed"):
        if k in b:
            patch[k] = str(b.get(k) or "").strip()
    for k in ("tg_on", "tg_memory"):
        if k in b:
            patch[k] = bool(b.get(k))
    if not patch:
        return 200, {"ok": False, "error": "пустая настройка"}
    s = store.update_settings(patch)
    probe = {"ok": None, "error": "", "bot": {}}
    if s.get("tg_on") and (s.get("tg_token") or "").strip():
        try:
            probe["bot"] = tgbot.me(refresh=True)
            probe["ok"] = True
        except tgbot.TgErr as e:
            probe["ok"] = False
            probe["error"] = str(e)
        except Exception as e:
            probe["ok"] = False
            probe["error"] = "%s: %s" % (type(e).__name__, e)
    tgbot.wake()
    return 200, {"ok": True, "probe": probe, "tgbot": tgbot.status()}


def h_tg_probe(req):
    try:
        return 200, {"ok": True, "bot": tgbot.me(refresh=True)}
    except tgbot.TgErr as e:
        return 200, {"ok": False, "error": str(e)}
    except Exception as e:
        return 200, {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}


for _m in ("GET", "POST"):
    _r(_m, "api/tgbot/status", h_tg_status)
    _r(_m, "api/tgbot/config", h_tg_config)
    _r(_m, "api/tgbot/probe", h_tg_probe)


def h_hear_upload(req):
    """Загрузить звуковой/видео файл — мозг его «послушает» (hearing.report)."""
    import hearing
    data = req.files_data.get("file") or b""
    name = req.files_data.get("filename") or "файл"
    if not data:
        data = req.raw or b""
    if not data:
        return 200, {"ok": False, "error": "файл не передан"}
    try:
        text, ok = hearing.report(data, name, source="панель")
    except Exception as e:
        return 200, {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}
    return 200, {"ok": bool(ok), "reply": text, "name": name}


_r("POST", "api/hear", h_hear_upload)


# --------------------- v3.41: чтение GitHub и код своей LLM ------------------
def h_github(req):
    """GET — состояние; POST {url} — принять ссылку GitHub; POST {gh_on,
    gh_token, gh_files_max, gh_every} — настройки."""
    import ghlearn
    b = req.body or {}
    out = {}
    patch = {}
    if "gh_on" in b:
        patch["gh_on"] = bool(b.get("gh_on"))
    if "gh_token" in b:
        patch["gh_token"] = str(b.get("gh_token") or "").strip()
    for k, lo, hi in (("gh_files_max", 1, 5000), ("gh_every", 0, 3600),
                      ("gh_repos_max", 1, 50)):
        if k in b:
            try:
                patch[k] = max(lo, min(hi, int(float(b.get(k)))))
            except (TypeError, ValueError):
                pass
    if patch:
        store.update_settings(patch)
        ghlearn.WAKE.set()
    if str(b.get("url") or "").strip():
        import study
        out["add"] = study.add(str(b["url"]).strip())
    st = ghlearn.status()
    try:
        import codewrite
        st["own_code"] = codewrite.own_ready()
    except Exception:
        st["own_code"] = False
    st["settings"] = {k: store.settings().get(k) for k in (
        "gh_on", "gh_every", "gh_files_max", "gh_repos_max")}
    try:                                   # v3.176: второй поток — код
        import codestream
        import llmstreams
        cp = {}
        for k_ in ("llm_code_stream", "llm_stream_code", "llm_stream_max", "llm_stream_talk", "llm_stream_admin"):
            if k_ in b:
                cp[k_] = bool(b.get(k_))
                if cp[k_]:              # включили вручную — снять самовыключение
                    _ls = llmstreams._state()
                    if k_ == "llm_code_stream":
                        _ls["off"] = {}
                    else:
                        _ls["off"].pop(k_.replace("llm_stream_", ""), None)
                    llmstreams._save(_ls)
        for k, lo, hi in (("llm_code_mix", 1, 100), ("llm_code_every_min", 1, 1440)):
            if k in b:
                try:
                    cp[k] = max(lo, min(hi, int(float(b.get(k)))))
                except (TypeError, ValueError):
                    pass
        if cp:
            store.update_settings(cp)
            codestream.wake()
        if b.get("restore_weights"):
            out["restore"] = codestream.restore_good()
        if b.get("llm_code_stream") is True:      # включили снова — сбросить отметку
            _cst = codestream._state(); _cst.pop("diverged", None); codestream._save(_cst)
        if cp:
            llmstreams.wake()
        st["streams"] = llmstreams.status()
        cs = codestream.status()
        cs["mix"] = store.settings().get("llm_code_mix")
        cs["every"] = store.settings().get("llm_code_every_min")
        st["stream"] = cs
    except Exception as e:
        st["stream"] = {"on": False, "mode": "ошибка: %s" % e}
    if str(b.get("forget") or "").strip():  # убрать ссылку на репозиторий
        import study
        out["forget"] = study.remove(str(b["forget"]).strip())
        st["links"] = [x for x in st.get("links") or [] if x.get("url") != b["forget"]]
    out.update(ok=True, github=st)
    return 200, out


_r("GET", "api/github", h_github)
_r("POST", "api/github", h_github)


def h_code_try(req):
    """Пробная просьба к режиму «Код»: POST {task}."""
    import codegen
    task = str((req.body or {}).get("task") or "").strip()
    if not task:
        return 200, {"ok": False, "error": "пустая просьба"}
    reply, matched, info = codegen.answer(task)
    return 200, {"ok": bool(matched), "reply": reply or "", "info": info}


_r("POST", "api/code/try", h_code_try)


def h_github_zip(req):
    """Загрузить ZIP репозитория вручную (скачан в браузере с любой площадки)."""
    import ghlearn
    data = req.files_data.get("file") or req.raw or b""
    name = req.files_data.get("filename") or "архив.zip"
    if not data:
        return 200, {"ok": False, "err": "файл не передан"}
    return 200, ghlearn.ingest_zip(data, name)


_r("POST", "api/github/zip", h_github_zip)

# --- Project agent: isolated archives, background jobs, real action log. ---
def _project_error(fn):
    try:
        return fn()
    except PermissionError as e:
        return 403, {"ok": False, "error": str(e)}
    except (ValueError, FileNotFoundError, KeyError, __import__('zipfile').BadZipFile) as e:
        return 400, {"ok": False, "error": str(e)}


def h_project_upload(req):
    import project_agent
    return _project_error(lambda: (200, dict(ok=True, project=project_agent.upload(
        req.files_data.get('file') or b'', req.files_data.get('filename') or '',
        getattr(req, 'client_ip', '')))))


def h_project_run(req):
    import project_agent
    b = req.body or {}
    return _project_error(lambda: (200, dict(ok=True, id=project_agent.start(
        str(b.get('project_id') or ''), str(b.get('task') or ''),
        getattr(req, 'client_ip', ''), internet=b.get('internet', True) is True,
        model=str(b.get('model') or 'auto'), base_job=str(b.get('base_job') or '')))))


def h_project_status(req):
    import project_agent
    return _project_error(lambda: (200, dict(ok=True, job=project_agent.status(
        req.query.get('id', ''), getattr(req, 'client_ip', '')))))


def h_project_download(req):
    import project_agent
    return _project_error(lambda: (200, 'file', (
        str(project_agent.download(req.query.get('id', ''), getattr(req, 'client_ip', ''))),
        'application/zip', 'project-result.zip')))


_r('POST', 'api/code/project/upload', h_project_upload)
_r('POST', 'api/code/project/run', h_project_run)
_r('GET', 'api/code/project/status', h_project_status)
_r('GET', 'api/code/project/download', h_project_download)


def h_project_new(req):
    import project_agent
    return _project_error(lambda: (200, dict(ok=True, project=project_agent.create(getattr(req, 'client_ip', '')))))


_r('POST', 'api/code/project/new', h_project_new)


# A code-only short course never calls retrain_now() or resets user weights.
def h_code_boot(req):
    import codeboot
    def handle():
        b = req.body or {}
        if getattr(req, 'method', 'GET') == 'POST':
            result = codeboot.stop() if b.get('stop') else codeboot.start(b.get('steps', 200), b.get('seconds', 120), mode=str(b.get('mode') or 'dual'))
        else:
            result = codeboot.status()
        return 200, dict(ok=True, course=result)
    return _project_error(handle)


_r('GET', 'api/code/course', h_code_boot)
_r('POST', 'api/code/course', h_code_boot)


def h_code_will(req):
    import brainwill
    return 200, dict(ok=True, will=brainwill.policy())


_r('GET', 'api/code/will', h_code_will)


# ===== SSH серверы и агент администрирования =====
def h_ssh_servers(req):
    import ssh_servers
    if req.method == "GET":
        return 200, {"ok": True, "servers": ssh_servers.list_servers(),
                     "status": ssh_servers.status()}
    b = req.body or {}
    action = b.get("action", "add")
    if action == "add":
        r = ssh_servers.add_server(
            name=b.get("name"), host=b.get("host"),
            user=b.get("user", "root"), port=int(b.get("port") or 22),
            key_path=b.get("key_path", ""), password=b.get("password", ""),
            note=b.get("note", ""))
        return 200, r
    if action == "remove":
        return 200, ssh_servers.remove_server(b.get("id"))
    return 400, {"ok": False, "error": "неизвестное действие"}


def h_ssh_run(req):
    import ssh_servers
    b = req.body or {}
    r = ssh_servers.run(b.get("server"), b.get("command", ""),
                        timeout=int(b.get("timeout") or 60))
    return 200, r


def h_ssh_agent(req):
    import ssh_agent
    if req.method == "GET":
        jid = req.query.get("id", "")
        if jid:
            job = ssh_agent.get_job(jid)
            return 200, {"ok": True, "job": job}
        return 200, {"ok": True, "jobs": ssh_agent.list_jobs()}
    b = req.body or {}
    action = b.get("action", "start")
    if action == "start":
        r = ssh_agent.start_job(b.get("task", ""), owner=getattr(req, "client_ip", ""),
                                use_own_llm=bool(b.get("own_llm", True)),
                                auto_ok=b.get("auto_ok"), target=b.get("target") or "")
        return 200, r
    if action == "approve":                 # v3.178: «Разрешить» / «Отклонить»
        return 200, ssh_agent.approve(b.get("id"), bool(b.get("yes")))
    if action == "stop":
        return 200, ssh_agent.stop(b.get("id"))
    if action == "seed":
        return 200, ssh_agent.seed_admin_knowledge()
    return 400, {"ok": False, "error": "неизвестное действие"}


def h_servers(req):
    """v3.178: окно «Серверы» (только владелец). GET — цели, ключ облака,
    задания плат, журнал. POST {action: check|wake|mac|exec|write|key|
    add_board|classify|auto_ok, ...}."""
    import servers
    import ssh_servers
    if req.method == "GET":
        import boardcode
        return 200, {"ok": True, "targets": servers.targets(), "key": servers.cloud_key(),
                     "history": servers.history(40), "board_jobs": boardcode.jobs(15),
                     "auto_ok": bool(store.settings().get("srv_auto_ok"))}
    b = req.body or {}
    act = b.get("action")
    tg = str(b.get("target") or "")
    if act == "check":
        return 200, servers.check(tg)
    if act == "wake":
        return 200, servers.wake(tg, wait=int(b.get("wait") or 0))
    if act == "mac":
        return 200, ssh_servers.set_field(tg, mac=str(b.get("mac") or "").strip()[:40])
    if act == "classify":
        return 200, {"ok": True, "kind": servers.classify(b.get("command"))}
    if act == "exec":
        cmd = str(b.get("command") or "").strip()
        if not cmd:
            return 400, {"ok": False, "error": "пустая команда"}
        kind = servers.classify(cmd)
        if kind != "read" and not b.get("confirmed"):
            return 200, {"ok": False, "need_confirm": True, "kind": kind}
        store.log("СЕРВЕРЫ", "консоль %s: %s" % (tg, cmd[:200]))
        return 200, dict(servers.run(tg, cmd, timeout=int(b.get("timeout") or 90)), kind=kind)
    if act == "write":
        r = servers.write_file(tg, b.get("path"), b.get("content") or "")
        if r.get("ok") and b.get("run"):
            path = str(b.get("path"))
            runner = "bash" if path.endswith(".sh") else "python3"
            r2 = servers.run(tg, "timeout 60 %s \"%s\"" % (runner, path.replace("~/", "$HOME/", 1)), timeout=90)
            r = dict(r2, written=r.get("out"))
        return 200, r
    if act == "key":
        return 200, servers.cloud_key(create=True)
    if act == "add_board":                  # Linux платы — как SSH-сервер (arduino@ip)
        return 200, ssh_servers.add_server(name=str(b.get("name") or "плата")[:60], host=b.get("host"),
                                           user=b.get("user") or "arduino", port=22,
                                           note="плата Arduino UNO Q (добавлена из окна «Серверы»)")
    if act == "auto_ok":
        store.update_settings({"srv_auto_ok": bool(b.get("on"))})
        return 200, {"ok": True}
    return 400, {"ok": False, "error": "неизвестное действие"}


_r("GET", "api/ssh/servers", h_ssh_servers)
_r("POST", "api/ssh/servers", h_ssh_servers)
_r("POST", "api/ssh/run", h_ssh_run)
_r("GET", "api/ssh/agent", h_ssh_agent)
_r("POST", "api/ssh/agent", h_ssh_agent)
def h_code_write(req):
    """v3.178: окно «Серверы» — код от раздела «Код» (своя LLM, модель, примеры)."""
    import codewrite
    b = req.body or {}
    task = str(b.get("task") or "").strip()[:2000]
    if not task:
        return 400, {"ok": False, "error": "пустая задача"}
    lang = b.get("lang") if b.get("lang") in ("python", "bash") else "python"
    rep = codewrite.compose(task, lang)
    return 200, {"ok": bool(rep), "reply": rep or "не получилось написать код"}


_r("POST", "api/code/write", h_code_write)
_r("GET", "api/servers", h_servers)                        # v3.178: окно «Серверы»
_r("POST", "api/servers", h_servers)


# ===== Память облака и LLM (JSON) =====
def h_memory(req):
    import memory_save
    if req.method == "GET":
        action = req.query.get("action", "status")
        if action == "status":
            return 200, {"ok": True, "status": memory_save.status()}
        if action == "cloud":
            return 200, {"ok": True, "memory": memory_save.load_cloud()}
        if action == "llm":
            return 200, {"ok": True, "memory": memory_save.load_llm()}
        return 200, {"ok": True, "status": memory_save.status()}
    b = req.body or {}
    action = b.get("action", "save")
    if action == "save":
        return 200, memory_save.save_all()
    if action == "cloud":
        return 200, memory_save.save_cloud()
    if action == "llm":
        return 200, memory_save.save_llm()
    return 400, {"ok": False, "error": "неизвестное действие"}


_r("GET", "api/memory", h_memory)
_r("POST", "api/memory", h_memory)


# ===== Агент в стиле z.ai =====
def h_zai(req):
    import zai_agent
    if req.method == "GET":
        action = req.query.get("action", "rules")
        if action == "rules":
            return 200, {"ok": True, "rules": zai_agent.rules(), "system": zai_agent.system_prompt()}
        return 200, {"ok": True, "rules": zai_agent.rules()}
    b = req.body or {}
    action = b.get("action", "generate")
    if action == "generate":
        r = zai_agent.generate(b.get("text", ""), b.get("history"), int(b.get("max_new") or 512))
        return 200, r
    if action == "seed":
        return 200, zai_agent.seed_kb()
    if action == "prompt":
        return 200, {"ok": True, "prompt": zai_agent.apply_to_context(b.get("text", ""), b.get("history"))}
    return 400, {"ok": False, "error": "неизвестное действие"}


_r("GET", "api/zai", h_zai)
_r("POST", "api/zai", h_zai)


# ===== Звёздные врата — просмотр сериала =====
def h_stargate(req):
    import stargate
    if req.method == "GET":
        action = req.query.get("action", "status")
        if action == "status":
            return 200, {"ok": True, "status": stargate.status()}
        if action == "search":
            q = req.query.get("q", "")
            season = req.query.get("season")
            episode = req.query.get("episode")
            vids = stargate.search_episodes(q, season, episode)
            return 200, {"ok": True, "videos": vids}
        return 200, {"ok": True, "status": stargate.status()}
    b = req.body or {}
    action = b.get("action", "watch")
    if action == "seed":
        return 200, stargate.seed_knowledge()
    if action == "watch":
        r = stargate.watch_episode(
            query=b.get("query", ""),
            season=b.get("season"),
            episode=b.get("episode"),
            lang=b.get("lang", "ru"))
        return 200, r
    if action == "sample":
        return 200, stargate.watch_series_sample(int(b.get("n") or 2))
    return 400, {"ok": False, "error": "неизвестное действие"}


_r("GET", "api/stargate", h_stargate)
_r("POST", "api/stargate", h_stargate)


# ===== Зрение ИИ =====
def h_vision(req):
    import vision_agent
    if req.method == "GET":
        return 200, {"ok": True, "status": vision_agent.status()}
    b = req.body or {}
    action = b.get("action", "see")
    if action == "seed":
        return 200, vision_agent.seed_instructions()
    if action == "see":
        if b.get("url"):
            r = vision_agent.see_url(b["url"], b.get("why", "зрение"))
        elif b.get("b64") or b.get("image"):
            r = vision_agent.see_b64(b.get("b64") or b.get("image"),
                                     b.get("filename", "image.jpg"),
                                     b.get("why", "зрение"))
        else:
            return 400, {"ok": False, "error": "нужен url или b64"}
        return 200, r
    return 400, {"ok": False, "error": "неизвестное действие"}


_r("GET", "api/vision", h_vision)
_r("POST", "api/vision", h_vision)


# ===== Установка Pillow =====
def h_install_pillow(req):
    import subprocess
    import sys
    try:
        # check if already installed
        import PIL
        return 200, {"ok": True, "status": "already", "message": "Pillow уже установлен"}
    except ImportError:
        pass
    try:
        # run pip install
        r = subprocess.run(
            [sys.executable, "-m", "pip", "install", "pillow"],
            capture_output=True, text=True, timeout=180
        )
        if r.returncode == 0:
            return 200, {"ok": True, "status": "installed",
                         "message": "Pillow успешно установлен",
                         "output": (r.stdout or "")[-500:]}
        else:
            return 200, {"ok": False, "status": "error",
                         "message": "Ошибка установки",
                         "output": (r.stderr or r.stdout or "")[-800:]}
    except subprocess.TimeoutExpired:
        return 200, {"ok": False, "error": "таймаут установки (3 мин)"}
    except Exception as e:
        return 200, {"ok": False, "error": str(e)[:300]}


_r("POST", "api/install/pillow", h_install_pillow)
