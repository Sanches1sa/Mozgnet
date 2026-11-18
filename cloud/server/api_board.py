# -*- coding: utf-8 -*-
# ===========================================================================
# api_board.py — КОНТРАКТ ОБЛАКА ДЛЯ МОЗГА neuro_12 (все вызовы платы)
# ---------------------------------------------------------------------------
# Здесь ровно те эндпоинты, которые вызывает main.py платы:
#   /api/health  /api/brains/register  /api/brains  /api/brains/ask
#   /api/brain/msg|inbox|ping|answer|progress|result/<mid>|log
#   /api/memory/save|load|delete   /api/buffer  /api/cache
#   /api/think  /api/chat/thought  /api/import  /api/gen/task
#   /api/web/fetch  /api/sandbox/run
#   /api/skills(+/id/run/learned)  /api/dict/list|get|put
#   /api/kb/list|search|article/<id>|import
#   /api/chat/readings  /api/tts  /api/llm/teach (410: /api/llm/ask)
#   /api/vision/frame|raw|buffer  /api/files/upload
# Форматы ответов согласованы с тем, что читает мозг (v2.30).
# ===========================================================================
import json
import re
import time

import brain
import kb
import sandbox
import skills
import store
import teacher
import tts
import vision
import webfetch


def h_health(req):
    return 200, {"ok": True, "service": "neuro12-cloud",
                 "version": store.version(), "ts": time.time(),
                 "uptime": round(time.time() - STARTED[0], 1),
                 "starting": _starting()}


def _starting():
    """v3.175.1: порт уже открыт, а посевы при старте ещё идут."""
    try:
        import __main__
        return bool(getattr(__main__, "STARTING", [False])[0])
    except Exception:
        return False


STARTED = [time.time()]


# ------------------------------- реестр плат --------------------------------
def h_brains_register(req):
    b = req.body or {}
    bid = str(b.get("id") or "").strip()[:40]
    if not bid:
        return 400, {"ok": False, "error": "нет id платы"}
    url = str(b.get("url") or "").strip()
    # пустой адрес или внутренний docker (172.16–31.x) недостижимы снаружи:
    # берём реальный IP, с которого плата пришла (порт — из присланного/7000)
    try:
        import re as _re
        m = _re.match(r"^(https?)://(\d+)\.(\d+)\.\d+\.\d+(?::(\d+))?", url)
        docker = bool(m and m.group(2) == "172" and 16 <= int(m.group(3)) <= 31)
        cip = str(req.client_ip or "")
        if (not url or docker) and cip and not cip.startswith(("127.", "::1")):
            if str(b.get("kind") or "board") == "board":
                url = "http://%s:%s" % (cip, (m.group(4) if m and m.group(4) else "7000"))
    except Exception:
        pass
    store.board_touch(bid, name=b.get("name"), kind=b.get("kind"),
                      url=url or b.get("url"), note=b.get("note"),
                      ip=req.client_ip, via_bridge=False)
    store.log("РЕЕСТР", "плата зарегистрирована:", bid,
              str(b.get("url") or ""))
    return 200, {"ok": True, "id": bid, "cloud": "neuro12-cloud"}


def h_brains_list(req):
    bs = store.boards()
    now = time.time()
    out = []
    for bid, b in bs.items():
        out.append({"id": bid, "name": b.get("name"), "kind": b.get("kind"),
                    "url": b.get("url"), "note": b.get("note"),
                    "first_seen": b.get("first_seen"),
                    "last_seen": b.get("last_seen"),
                    "online": (now - float(b.get("last_seen") or 0)) < 75,
                    "busy": False})
    out.sort(key=lambda x: -float(x.get("last_seen") or 0))
    return 200, {"ok": True, "boards": out}


def h_brains_delete(req):
    bid = str((req.body or {}).get("id") or "")[:40]
    if not bid:
        return 400, {"ok": False, "error": "нет id"}
    store.board_delete(bid)
    return 200, {"ok": True}


def h_boards_ping(req):
    """Проверить связь с платой (достук до её /api/health) — из панели."""
    bid = str((req.body or {}).get("id") or "")[:40]
    b = store.boards().get(bid)
    if not b or not b.get("url"):
        return 200, {"ok": False, "error": "плата без адреса"}
    import urllib.request
    t0 = time.time()
    try:
        with urllib.request.urlopen(b["url"].rstrip("/") + "/api/health",
                                    timeout=6) as r:
            r.read(64)
        return 200, {"ok": True, "ms": int((time.time() - t0) * 1000)}
    except Exception as e:
        return 200, {"ok": False, "error": str(e)[:120],
                     "ms": int((time.time() - t0) * 1000)}


# ------------------------------- мозг-почта ---------------------------------
def h_brain_msg(req):
    """Письмо устройству: от платы ИЛИ из чата облака (to=id платы)."""
    b = req.body or {}
    to = str(b.get("to") or "").strip()[:40]
    frm = str(b.get("from") or "чат облака")[:40]
    text = str(b.get("text") or "")[:4000]
    if not to or not text:
        return 400, {"ok": False, "error": "нужны to и text"}
    boards = store.boards()
    if to not in boards:
        return 200, {"ok": False, "error": "устройство не найдено: %s" % to}
    mid = "m" + brain.os_urandom_hex()
    brain.mail_deliver({"id": mid, "from": frm, "to": to, "text": text,
                        "long": bool(b.get("long")), "ts": time.time()})
    # письмо ждёт ответа (для /api/brain/result/<mid>) — как и ask_board
    with store.LOCK:
        pend = store.load_json("mail_pending.json", {}) or {}
        pend[mid] = {"id": mid, "board": to, "chat_id": b.get("chat_id"),
                     "text": text, "ts": time.time()}
        store.save_json("mail_pending.json", brain._cap(pend, 200))
    store.log("ПОЧТА", "%s -> %s: %s" % (frm, to, text[:50]))
    return 200, {"ok": True, "id": mid}


def h_brain_inbox(req):
    bid = req.query.get("board", "")[:40]
    if not bid:
        return 400, {"ok": False, "error": "нет board"}
    mails = brain.mail_take(bid)
    store.board_seen(bid, ip=req.client_ip)
    return 200, {"ok": True, "msgs": mails, "count": len(mails)}


def h_brain_d6(req):
    """Мысли зон облака для платы (GET /api/brain/d6?board=, v3.17): забрать
    и СНЯТЬ очередь — плата проиграет мысли на пине D6 (RPC d6seq -> PWM).
    Отдельный ящик (d6box.json): не письма-вопросы, ответов не ждём."""
    bid = req.query.get("board", "")[:40]
    if not bid:
        return 400, {"ok": False, "error": "нет board"}
    msgs = store.d6_take(bid)
    store.board_seen(bid, ip=req.client_ip)
    try:
        import syncpoint
        sy = syncpoint.status()
    except Exception:
        sy = {}
    try:
        import teacher
        ins = teacher.instruction()
    except Exception:
        ins = ""
    return 200, {"ok": True, "msgs": msgs, "count": len(msgs), "sync": sy,
                 "instr": (__import__("hashlib").md5(ins.encode("utf-8")
                           ).hexdigest()[:8] if ins else ""),
                 "instr_text": ins[:300],
                 "negfreq": bool(store.settings().get("d6_negfreq")),
                 "negfreq4": bool(store.settings().get("d4_negfreq")),
                 "camframe": bool(store.settings().get("d6_camframe")),
                 "d4cam": bool(store.settings().get("d4_camframe")),
                 "cammode": str(store.settings().get("d6_cammode") or "pure")}


def h_negfreq_decide(req):
    """POST /api/negfreq/decide {text}: знак частоты D6 для СОБСТВЕННОЙ фразы
    платы (v3.51). sign 0 — LLM не решила (плата берёт знак по Δf)."""
    import negfreq
    if not negfreq.enabled():
        return 200, {"ok": True, "on": False, "sign": 0}
    import negfreq
    d = negfreq.decide(str((req.body or {}).get("text") or "")[:300]) or {}
    return 200, {"ok": True, "on": True, "sign": int(d.get("sign") or 0),
                 "by": d.get("by") or "", "margin": d.get("margin") or 0}


def h_genmod_words(req):
    """POST /api/genmod/words {text}: уровень слов -1..1 по таблице «сигнал ↔
    слова» (v3.56) — плата строит по ним профиль частоты кадра на D4."""
    import genmod
    lv, known = genmod.word_levels(str((req.body or {}).get("text") or "")[:600])
    return 200, {"ok": True, "levels": lv, "known": known}


def h_genmod_frame(req):
    """POST /api/genmod/frame {device, text, hz}: фраза + знаковая частота
    дыхания кадра -> обучение смыслу (v3.56)."""
    import genmod
    b = req.body or {}
    ok = genmod.learn_frame(str(b.get("device") or "board")[:40],
                            b.get("text"), b.get("hz"), b.get("tn"))
    return 200, {"ok": True, "accepted": bool(ok)}


def h_brain_ping(req):
    bid = req.query.get("board", "")[:40]
    if bid:
        store.board_seen(bid, ip=req.client_ip)
    return 200, {"ok": True, "cloud": "neuro12-cloud"}


def h_brain_answer(req):
    b = req.body or {}
    mid = str(b.get("id") or b.get("mid") or "")[:40]
    frm = str(b.get("from") or b.get("board") or "")[:40]
    r = brain.mail_answer(mid, str(b.get("text") or ""),
                          frm, str(b.get("zone") or ""),
                          grounded=b.get("grounded"),        # v3.29
                          source=str(b.get("source") or "")[:120])
    if frm:
        store.board_seen(frm, ip=req.client_ip)
    store.log("ПОЧТА", "ответ платы %s (%s): %s" %
              (frm or "?", mid, str(b.get("text") or "")[:40]))
    return 200, r


def h_brain_progress(req):
    b = req.body or {}
    mid = str(b.get("id") or "")[:40]
    if mid and str(b.get("text") or ""):
        with store.LOCK:
            pend = store.load_json("mail_pending.json", {}) or {}
            if mid in pend:
                pend[mid]["progress"] = str(b.get("text"))[:200]
                store.save_json("mail_pending.json", pend)
    if str(b.get("board") or ""):
        store.board_seen(str(b["board"])[:40])
    return 200, {"ok": True}


def h_brain_result(req):
    return 200, brain.mail_result(req.params.get("mid", ""))


def h_brain_log(req):
    """v3.176.2: журнал облака — из ПОЛНОГО файла data/log.txt (а не 60
    последних строк из памяти, которые сбрасывались перезапуском и тонули в
    строках HTTP). ?n=строк (до 3000), ?q=поиск, ?cat=раздел, ?http=1 — и
    строки HTTP."""
    q = req.query or {}
    try:
        n = max(20, min(3000, int(q.get("n") or 400)))
    except ValueError:
        n = 400
    needle = str(q.get("q") or "").strip().lower()
    cat = str(q.get("cat") or "").strip()
    show_http = str(q.get("http") or "") == "1"
    lines = _log_file_tail(1500000)
    cats = {}
    out = []
    for ln in reversed(lines):
        parts = ln.split(" ", 3)
        c = parts[2] if len(parts) > 2 else ""
        cats[c] = cats.get(c, 0) + 1
        if c == "HTTP" and not show_http and cat != "HTTP":
            continue
        if cat and c != cat:
            continue
        if needle and needle not in ln.lower():
            continue
        if len(out) < n:
            out.append(ln)
    out.reverse()
    top = sorted(cats.items(), key=lambda kv: -kv[1])[:24]
    return 200, {"ok": True, "log": out, "cats": [{"name": k, "n": v} for k, v in top if k]}


def _log_file_tail(max_bytes):
    """Хвост журнала: data/log.txt (+ log.txt.1, если текущий короткий)."""
    import os
    path = store.p("log.txt")

    def tail(pth, nb):
        try:
            sz = os.path.getsize(pth)
            with open(pth, "rb") as f:
                off = max(0, sz - nb)
                f.seek(off)
                got = f.read().decode("utf-8", "replace").splitlines()
            if off:
                got = got[1:]                      # первая строка — обрывок
            return [x for x in got if x.strip()]
        except OSError:
            return []
    lines = tail(path, max_bytes)
    have = sum(len(x) + 1 for x in lines)
    if have < max_bytes // 2 and os.path.exists(path + ".1"):
        lines = tail(path + ".1", max_bytes - have) + lines
    return lines or store.log_tail(400)


def h_brain_think(req):
    """v3.28: ПЛАТА СПРАШИВАЕТ ВЕСЬ МОЗГ ОБЛАКА (синхронно): документы,
    база знаний, интернет, калькулятор, навыки, сеть материалов, память
    ответов, ИИ-агент кода. Раньше «Облако» на плате (route=cloud) умело
    только переслать вопрос ДРУГОЙ свободной плате — при одной плате в
    сети ответа не было вовсе. POST {text, from, long?, mode?: auto|talk|
    code, ctx?: [{role, text}]} -> {ok, answer, zone, ms, brain}."""
    b = req.body or {}
    text = str(b.get("text") or "").strip()[:6000]
    frm = str(b.get("from") or "плата")[:40]
    if not text:
        return 400, {"ok": False, "error": "пустой вопрос"}
    ctx = [m for m in (b.get("ctx") or []) if isinstance(m, dict)][-12:]
    # плата кладёт в ctx и сам вопрос (brain_remember до запроса) — не дублируем
    while ctx and ctx[-1].get("role") == "user" and \
            str(ctx[-1].get("text") or "").strip() == text:
        ctx.pop()
    ctx.append({"role": "user", "text": text})
    mode = str(b.get("mode") or "auto")
    t0 = time.time()
    sender = "плата %s" % frm
    if mode == "code":
        import agent
        reply, matched, cinfo = agent.code_agent(text, ctx=ctx)
        zone = brain.Z["code"]
        if not matched:
            reply, zone, _i = brain.think(text, bool(b.get("long")),
                                          route="cloud", sender=sender,
                                          ctx=ctx)
    elif mode == "talk":
        import agent
        reply, zone, _i = agent.talk(text, ctx=ctx,
                                     long_mode=bool(b.get("long")))
    else:
        reply, zone, _i = brain.think(text, bool(b.get("long")),
                                      route="cloud", sender=sender, ctx=ctx)
    store.board_seen(frm, ip=req.client_ip)
    store.log("ПЛАТА→ОБЛАКО", "%s: %s -> зона %s" % (frm, text[:50], zone))
    return 200, {"ok": True, "answer": str(reply or ""), "zone": zone,
                 "ms": int((time.time() - t0) * 1000),
                 "brain": "облако %s" % store.version()}


def h_brain_learn(req):
    """v3.29: ЕДИНЫЙ МОЗГ — плата передаёт подтверждённое у себя:
    POST {board, pairs: [{q, a, src}], code: [{task, text}]}."""
    import unify
    b = req.body or {}
    board = str(b.get("board") or "плата")[:40]
    store.board_seen(board, ip=req.client_ip)
    return 200, unify.learn_from_board(board, b.get("pairs") or [],
                                       b.get("code") or [])


def h_brain_knowledge(req):
    """v3.29: ЕДИНЫЙ МОЗГ — подтверждённое облаком для платы:
    GET ?board=&since=<ts> -> {pairs, ts}."""
    import unify
    board = str(req.query.get("board") or "")[:40]
    try:
        since = float(req.query.get("since") or 0)
    except ValueError:
        since = 0.0
    if board:
        store.board_seen(board, ip=req.client_ip)
    return 200, unify.knowledge_for_board(board, since)


def h_brains_ask(req):
    """Спросить СВОБОДНУЮ плату (от мозга или из чата)."""
    b = req.body or {}
    text = str(b.get("text") or "")[:2000]
    frm = str(b.get("from") or "?")[:40]
    if not text:
        return 400, {"ok": False, "error": "пустой вопрос"}
    r = brain.ask_board(text, chat_id=b.get("chat_id"), frm=frm,
                        long_mode=bool(b.get("long", True)))
    if not r.get("ok"):
        return 200, r
    store.log("СПРОСИТЬ", "плату %s: %s" % (r.get("brain"), text[:50]))
    return 200, r


# ----------------------------- облачная память ------------------------------
def h_memory_save(req):
    """Бандл памяти платы: {app, device, memory[], chat[], state{}}."""
    b = req.body or {}
    dev = str(b.get("device") or "")[:40]
    if not dev:
        return 400, {"ok": False, "error": "нет device"}
    safe = "".join(c for c in dev if c.isalnum() or c in "-_") or "device"
    _old = store.load_json("memory/%s.json" % safe, None) or {}
    prev_t = _old.get("tensor") if isinstance(_old.get("tensor"), dict) \
        else None
    bundle = {"app": str(b.get("app") or "neuro_12")[:20],
              "device": dev, "memory": (b.get("memory") or [])[-120:],
              "chat": (b.get("chat") or [])[-400:],
              "state": b.get("state") or {}, "ts": time.time()}
    # v3.43: память НАБЛЮДЕНИЙ тензорной сети платы (примеры + веса)
    tn = b.get("tensor")
    if isinstance(tn, dict) and tn.get("samples") is not None:
        tn = dict(tn)
        tn["samples"] = [str(x)[:200] for x in (tn.get("samples") or [])[-800:]]
        if len(json.dumps(tn)) > 400000:
            for k in ("W1", "b1", "W2", "b2"):
                tn.pop(k, None)
        bundle["tensor"] = tn
    elif prev_t is not None:
        bundle["tensor"] = prev_t        # старая плата без поля — не терять
    # история: 3 последних бандла (страховка от порчи)
    hist = store.load_json("memory/%s.history.json" % safe, []) or []
    prev = store.load_json("memory/%s.json" % safe, None)
    if prev:
        hist.append({"ts": prev.get("ts"), "chat": len(prev.get("chat") or [])})
        hist = hist[-5:]
        store.save_json("memory/%s.history.json" % safe, hist)
    store.save_json("memory/%s.json" % safe, bundle)
    try:                                  # v3.47: генератор скетча -> LLM
        import genmod
        genmod.note(dev, b.get("gen"))
        genmod.learn_bundle(dev, bundle)
        try:
            import negfreq
            negfreq.learn_bundle(dev, bundle, b.get("gen"))
        except Exception:
            pass
    except Exception:
        pass
    # v3.46: память платы -> ЕДИНАЯ сеть (реплики, диалоги, заметки, слова)
    try:
        import onenet
        onenet.ingest_bundle(dev, bundle)
    except Exception:
        try:
            for m in (bundle["chat"] or [])[-20:]:
                txt = str(m.get("text") or "")[:800]
                if txt:
                    brain.mn_learn(txt, title="диалог %s" % dev, src="плата")
        except Exception:
            pass
    return 200, {"ok": True, "device": dev,
                 "memory": len(bundle["memory"]), "chat": len(bundle["chat"]),
                 "tensor_samples": len((bundle.get("tensor") or {})
                                       .get("samples") or [])}


def h_memory_load(req):
    dev = req.query.get("device", "")[:40]
    if not dev:
        return 400, {"ok": False, "error": "нет device"}
    safe = "".join(c for c in dev if c.isalnum() or c in "-_") or "device"
    bundle = store.load_json("memory/%s.json" % safe, None)
    if not bundle:
        return 200, {"ok": True, "found": False, "device": dev}
    return 200, {"ok": True, "found": True, "app": bundle.get("app"),
                 "device": dev, "memory": bundle.get("memory") or [],
                 "chat": bundle.get("chat") or [], "state": bundle.get("state") or {},
                 "tensor": bundle.get("tensor") or {}}


def h_memory_delete(req):
    b = req.body or {}
    dev = str(b.get("device") or "")[:40]
    safe = "".join(c for c in dev if c.isalnum() or c in "-_") or "device"
    removed = 0
    for rel in ("memory/%s.json" % safe, "memory/%s.history.json" % safe):
        try:
            import os
            os.remove(store.p(rel))
            removed += 1
        except Exception:
            pass
    return 200, {"ok": True, "removed": removed, "device": dev}


def h_memory_list(req):
    import os
    out = []
    try:
        for fn in sorted(os.listdir(store.p("memory"))):
            if not fn.endswith(".json") or fn.endswith(".history.json"):
                continue
            b = store.load_json("memory/" + fn, None) or {}
            out.append({"device": b.get("device") or fn[:-5],
                        "ts": b.get("ts"),
                        "memory": len(b.get("memory") or []),
                        "chat": len(b.get("chat") or []),
                        "tn_samples": len((b.get("tensor") or {})
                                          .get("samples") or []),
                        "tn_trains": (b.get("tensor") or {}).get("trains"),
                        "tn_acc": (b.get("tensor") or {}).get("acc"),
                        "bytes": len(json.dumps(b))})
    except Exception:
        pass
    out.sort(key=lambda x: -(x.get("ts") or 0))
    return 200, {"ok": True, "devices": out}


# --------------------------- буфер для мозга --------------------------------
def h_buffer(req):
    """Сводный буфер облака: мозг качает его раз в 5 мин (GET /api/buffer)."""
    board = req.query.get("board", "")[:40]
    msgs = []
    for cid in [c["id"] for c in store.chat_list()[:6]]:
        chat = store.chat_load(cid)
        if not chat:
            continue
        for m in (chat.get("messages") or [])[-8:]:
            if m.get("role") in ("user", "bot") and m.get("text"):
                msgs.append({"role": m["role"], "text": str(m["text"])[:220],
                             "zone": m.get("zone") or "", "ts": m.get("ts")})
    msgs.sort(key=lambda x: float(x.get("ts") or 0))
    msgs = msgs[-14:]
    thoughts = [t["text"] for t in store.thoughts_list(5)]
    kb_top = [{"title": a.get("title"), "chars": a.get("chars")}
              for a in kb.list_articles(6)]
    rd = brain.mn_readings()
    learned = 0
    reg = store.load_json("skills/registry.json", {}) or {}
    for _sid, sk in reg.items():
        learned += len(sk.get("learned_by") or {})
    out = {"ok": True, "board": board, "messages": msgs,
           "thoughts": thoughts, "kb": kb_top,
           "readings": {k: rd.get(k) for k in
                        ("nodes", "links", "docs", "topic", "hot")},
           "skills_learned": learned,
           "boards_online": store.stats()["boards_online"],
           "ts": time.time()}
    if req.query.get("compact") == "1" or req.path.endswith("/cache"):
        out = {"ok": True, "messages": [m["text"][:80] for m in msgs[-6:]],
               "kb": [a.get("title") for a in kb_top[:3]],
               "nodes": rd.get("nodes", 0)}
    return 200, out


# ------------------------- мысли / хроника / задания ------------------------
def h_think(req):
    b = req.body or {}
    board = str(b.get("board") or "?")[:40]
    text = store._tclean(str(b.get("text") or "")[:400])   # v3.118
    if text:
        store.thoughts_add(board, text, b.get("kind") or "say")
        brain.mn_learn(text, title="мысль %s" % board, src="мысль")
    if board != "?":
        store.board_seen(board)
    return 200, {"ok": True}


def h_link(req):
    """v3.102: СВЯЗЬ МОЗГОВ — плата шлёт частоту генератора и новые мысли,
    получает мысли облака (ответ мозга облака + своей LLM) — boardlink.py."""
    import boardlink
    b = req.body or {}
    r = boardlink.link(b.get("board"), b.get("hz") or 0, b.get("shz"),
                       b.get("ema") or 0, b.get("phrases") or [],
                       answers=b.get("answers") or [], up=b.get("up"),
                       rss=b.get("rss_mb"), crash=b.get("crash"),
                       ver=b.get("ver"), ip=getattr(req, "client_ip", ""),
                       asks_ok=bool(b.get("asks_ok")),
                       bridge=b.get("bridge"), grav=b.get("grav"),
                       net=b.get("net"), cam=b.get("cam"),
                       breath=b.get("breath"), will=b.get("will"),
                       extras_err=b.get("extras_err"), lead=b.get("lead"))
    try:                         # v3.127: игра «РОК-12» — плата решает сама
        import doomgame
        if isinstance(r, dict) and r.get("ok"):
            r["game"] = doomgame.info_for_board(str(b.get("board") or ""))
    except Exception:
        pass
    try:                         # v3.181: общий чат плата↔облако↔боты↔LLM
        import gamechat
        if isinstance(r, dict) and r.get("ok"):
            r["chat"] = gamechat.link(str(b.get("board") or ""), b.get("chat_seq"),
                                      b.get("chat_out"))
    except Exception:
        pass
    try:                         # v3.181: кнопка «поставить Vosk на плату»
        if isinstance(r, dict) and r.get("ok") and vosk_req_take(b.get("board")):
            r["vosk_install"] = True
    except Exception:
        pass
    try:                         # v3.181.4: звук из карточки — на динамик платы
        if isinstance(r, dict) and r.get("ok"):
            pl = play_req_take(b.get("board"))
            if pl:
                r["play"] = pl
    except Exception:
        pass
    try:                         # v3.177.2: кванты на D6 (плата v2.92)
        if isinstance(r, dict) and r.get("ok"):
            r["particles"] = particles_cfg()
            if isinstance(b.get("noise"), dict):
                _NOISE[str(b.get("board") or "?")[:40]] = dict(b["noise"], ts=time.time())
    except Exception:
        pass
    try:                         # v3.178: уши и глаза платы + живой звук по кнопке
        import micstream
        if isinstance(r, dict) and r.get("ok"):
            micstream.note_link(b.get("board"), b.get("mic"), b.get("cam"), b.get("sketch"))
            lv = micstream.want_for(b.get("board"))
            if lv:
                r["mic_live"] = lv
    except Exception:
        pass
    try:                         # v3.178: код и команды для платы (раздел «Код», «Серверы»)
        import boardcode
        if isinstance(r, dict) and r.get("ok"):
            boardcode.note_link(b.get("board"), b.get("ver"), b.get("codejobs"))
            cj = boardcode.take(b.get("board"))
            if cj:
                r["code_jobs"] = cj
    except Exception:
        pass
    return (200 if r.get("ok") else 400), r


_NOISE = {}      # плата -> {cap, d6, scale, cfg, ts}: шум конденсатора и шаг весов


def particles_cfg():
    """Галочки «Кванты на D6» (⚙ → Общие) -> строка для скетча платы:
    grav:1,muon:0,phot:1,gen:0,cpu:0,min:100,max:4.0"""
    s_ = store.settings()

    def f(k, d):
        try:
            return float(s_.get(k, d))
        except (TypeError, ValueError):
            return d
    return "grav:%d,muon:%d,phot:%d,gen:%d,cpu:%d,min:%g,max:%g" % (
        1 if s_.get("part_grav") else 0, 1 if s_.get("part_muon") else 0,
        1 if s_.get("part_phot") else 0, int(max(0, f("part_gen_hz", 0))),
        1 if s_.get("part_cpu_link") else 0, f("part_min_mhz", 100.0), f("part_max_ghz", 4.0))


def h_particles(req):
    """GET: галочки и шум плат; POST: сохранить галочки."""
    keys = ("part_grav", "part_muon", "part_phot", "part_cpu_link")
    if req.method == "POST":
        b = req.body or {}
        cp = {k: bool(b[k]) for k in keys if k in b}
        for k, lo, hi in (("part_gen_hz", 0, 20000), ("part_min_mhz", 0, 100000), ("part_max_ghz", 0, 1000)):
            if k in b:
                try:
                    cp[k] = max(lo, min(hi, float(b[k])))
                except (TypeError, ValueError):
                    pass
        if cp:
            store.update_settings(cp)
            store.log("КВАНТЫ", "настройки: %s" % particles_cfg())
    s_ = store.settings()
    now = time.time()
    boards = [dict(v, board=k, age=int(now - v.get("ts", now))) for k, v in _NOISE.items()
              if now - v.get("ts", 0) < 120]
    return 200, {"ok": True, "cfg": particles_cfg(),
                 "settings": {k: s_.get(k) for k in keys + ("part_gen_hz", "part_min_mhz", "part_max_ghz")},
                 "boards": boards}


def h_hearing_mic(req):
    """v3.128: фраза с микрофона мозга (плата, WAV base64) -> речь в текст;
    обращённая к мозгу — вопрос, ответ плата произносит."""
    import senses
    b = req.body or {}
    r = senses.mic_in(b.get("board"), b.get("wav"), b.get("level"),
                      text_board=b.get("text_board"))
    return (200 if r.get("ok") else 400), r


def h_hearing_stream(req):
    """v3.178: живой звук с платы — сырой PCM 16 кГц кусками."""
    import micstream
    b = req.body or {}
    r = micstream.stream_in(b.get("board"), b.get("pcm"), b.get("sr"), b.get("seq"), b.get("rms"))
    return (200 if r.get("ok") else 400), r


def h_hearing_live(req):
    """v3.178: GET — состояние ушей/глаз плат; POST {board?, on, sec?} —
    попросить плату слать живой звук (кнопка «Слушать»)."""
    import micstream
    if req.method == "POST":
        b = req.body or {}
        micstream.set_want(b.get("board") or "*", bool(b.get("on")), b.get("sec") or 60)
    return 200, micstream.status()


_VOSK_REQ = {}      # v3.181: плата -> ts: владелец нажал «поставить Vosk на плату»


def h_vosk_board(req):
    """v3.181: кнопка в карточке микрофона: POST {board} — плата (v2.96+)
    получит в ответе /api/link флаг vosk_install и поставит Vosk в свою
    постоянную папку pylib + русскую модель (из облака или alphacephei)."""
    b = req.body or {}
    board = str(b.get("board") or "").strip()[:40]
    if not board:
        return 400, {"ok": False, "error": "нужен id платы"}
    _VOSK_REQ[board] = time.time()
    store.log("СЛУХ", "владелец попросил поставить Vosk на плату %s" % board)
    return 200, {"ok": True, "board": board,
                 "note": "плата получит команду при следующей связи (до 5 с); установка — несколько минут"}


def vosk_req_take(board):
    ts = _VOSK_REQ.pop(str(board or "")[:40], None)
    return bool(ts and time.time() - ts < 600)


_PLAY_REQ = {}      # v3.181.4: плата -> {what, sec, ts}: «звук из карточки — на динамик платы»


def h_hearing_to_board(req):
    """v3.181.4: кнопка в карточке микрофона: POST {board, what: phrase|clip, sec?}
    — плата (v2.96.1+) получит в ответе /api/link play{url, sec}, скачает WAV
    и сыграет его динамиком (огибающая на D8 через say_wave, иначе aplay)."""
    b = req.body or {}
    board = str(b.get("board") or "").strip()[:40]
    what = "clip" if str(b.get("what") or "") == "clip" else "phrase"
    try:
        sec = max(1, min(20, int(b.get("sec") or 10)))
    except (TypeError, ValueError):
        sec = 10
    if not board:
        return 400, {"ok": False, "error": "нужен id платы"}
    import micstream
    data = micstream.phrase_wav(board) if what == "phrase" else micstream.live_wav(board, sec)
    if not data:
        return 200, {"ok": False, "error": "у облака нет этого звука от платы %s (%s)" % (
            board, "фраз ещё не было" if what == "phrase" else "живого звука ещё нет")}
    _PLAY_REQ[board] = {"what": what, "sec": sec, "ts": time.time()}
    store.log("СЛУХ", "владелец отправил %s на динамик платы %s" % (
        "последнюю фразу" if what == "phrase" else "%d с живого звука" % sec, board))
    return 200, {"ok": True, "board": board, "what": what, "kb": len(data) // 1024,
                 "note": "плата заберёт звук при следующей связи (до 5 с)"}


def play_req_take(board):
    p = _PLAY_REQ.pop(str(board or "")[:40], None)
    if not p or time.time() - p["ts"] > 300:
        return None
    if p["what"] == "phrase":
        return {"url": "/api/hearing/phrase.wav?board=%s" % board, "what": "phrase"}
    return {"url": "/api/hearing/live.wav?board=%s&sec=%d" % (board, p["sec"]), "what": "clip"}


def h_hearing_voice(req):
    """v3.178: ГОЛОСОВОЙ ВВОД В ЧАТЕ. POST {wav: base64} — звук с микрофона
    устройства (браузер записал WAV), или {board, sec} — записать с микрофона
    платы. Ответ {ok, text} — текст ставится в поле ввода чата."""
    import base64
    import hearing
    import micstream
    b = req.body or {}
    if b.get("board"):
        data, why = micstream.record_board(b.get("board"), b.get("sec") or 5)
        if not data:
            return 200, {"ok": False, "error": why}
    else:
        try:
            data = base64.b64decode(re.sub(r"^data:[^,]*,", "", str(b.get("wav") or "")))
        except Exception:
            return 400, {"ok": False, "error": "звук не в base64"}
    if len(data) < 1000:
        return 200, {"ok": False, "error": "слишком короткая запись"}
    a, why = hearing.decode(data, 60)
    if a is None:
        return 200, {"ok": False, "error": why}
    text, why2 = hearing.transcribe(a)
    text = " ".join(str(text or "").split())
    store.log("СЛУХ", "голосовой ввод%s: %s" % (
        " с платы " + str(b.get("board"))[:40] if b.get("board") else "",
        text[:120] or "(" + (why2 or "слов нет") + ")"))
    if not text:
        return 200, {"ok": False, "error": why2 or "слов не разобрано — скажи громче и ближе",
                     "asr": hearing.status()}
    return 200, {"ok": True, "text": text}


def h_board_codejob(req):
    """v3.178: плата вернула результат задания (код/команда)."""
    import boardcode
    r = boardcode.result_in(req.body or {})
    return (200 if r.get("ok") else 404), r


def h_code_board(req):
    """v3.178: владелец — проверить/запустить код или команду на плате.
    GET — платы и журнал заданий; POST {code, mode, board?, timeout?}."""
    import boardcode
    if req.method == "GET":
        return 200, {"ok": True, "boards": boardcode.boards(), "jobs": boardcode.jobs()}
    b = req.body or {}
    r = boardcode.run(b.get("code"), b.get("mode") or "py_check", b.get("board") or "",
                      b.get("timeout") or 20)
    return 200, r


_VOSK_ZIP = {"data": None, "ts": 0.0}


def h_vosk_model_zip(req):
    """v3.178: русская модель Vosk для платы — из облака, без интернета."""
    import hearing
    import io
    import os
    import zipfile
    if not hearing.have_model():
        return 404, {"ok": False, "error": "в облаке нет модели Vosk"}
    if _VOSK_ZIP["data"] is None:
        d = hearing.model_dir()
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for dp, _dn, fn in os.walk(d):
                for f in fn:
                    full = os.path.join(dp, f)
                    z.write(full, os.path.join("vosk-ru", os.path.relpath(full, d)).replace(os.sep, "/"))
        _VOSK_ZIP["data"] = buf.getvalue()
        store.log("СЛУХ", "модель Vosk упакована для плат: %d МБ" % (len(_VOSK_ZIP["data"]) >> 20))
    return 200, "raw", (_VOSK_ZIP["data"], "application/zip", "vosk-ru.zip")


def h_board_backup(req):
    """v3.180: копия памяти платы (все её JSON сжатым пакетом)."""
    import boardsafe
    b = req.body or {}
    r = boardsafe.backup_in(b.get("board"), b.get("blob"), b.get("n"))
    return (200 if r.get("ok") else 400), r


def h_board_restore(req):
    import boardsafe
    b = req.body or {}
    return 200, boardsafe.restore_for(b.get("board"), bool(b.get("fresh")))


def h_board_pair(req):
    """v3.180: плата без адреса облака просит подключения (без токена)."""
    import boardsafe
    if req.method == "GET":
        q = req.query
        return 200, boardsafe.pair_request(q.get("board"), q.get("code"), req.client_ip, q.get("ver"))
    b = req.body or {}
    return 200, boardsafe.pair_request(b.get("board"), b.get("code"), req.client_ip, b.get("ver"))


def h_board_safe(req):
    """v3.180: владелец — копии плат и запросы подключения."""
    import boardsafe
    if req.method == "GET":
        return 200, {"ok": True, "backups": boardsafe.list_backups(), "pairs": boardsafe.pairs(),
                     "auto": store.settings().get("board_auto_restore", True) is not False}
    b = req.body or {}
    act = b.get("action")
    if act == "pair":
        return 200, boardsafe.pair_decide(b.get("board"), b.get("code"), bool(b.get("approve")))
    if act == "restore_to":
        return 200, boardsafe.force_restore(b.get("to"), b.get("from"))
    if act == "auto":
        store.update_settings({"board_auto_restore": bool(b.get("on"))})
        return 200, {"ok": True}
    return 400, {"ok": False, "error": "неизвестное действие"}


def h_hearing_live_wav(req):
    import micstream
    data = micstream.live_wav(req.query.get("board") or None, req.query.get("sec") or 5)
    if not data:
        return 404, {"ok": False, "error": "живого звука ещё нет"}
    return 200, "wav", data


def h_hearing_phrase_wav(req):
    import micstream
    data = micstream.phrase_wav(req.query.get("board") or None)
    if not data:
        return 404, {"ok": False, "error": "фраз с микрофона ещё не было"}
    return 200, "wav", data


def h_worddict_sync(req):
    """v3.133: общий словарь — плата шлёт прирост, получает изменения."""
    import worddict
    b = req.body or {}
    return 200, worddict.sync(b.get("board"), b.get("since"), b.get("inc"))


def h_doom_bot(req):
    """v3.127: бот платы в игре «РОК-12»: действие -> что видит + награда."""
    import doomgame
    return 200, doomgame.bot_call(req.body or {})


def h_gubble_bot(req):
    """v3.181: бот платы в игре «Gubble 2»: {board, act:{dx,dy}, want} -> obs."""
    import gubble
    return 200, gubble.bot_call(req.body or {})


def h_thoughts(req):
    limit = int(req.query.get("limit", "80"))
    return 200, {"ok": True, "thoughts": store.thoughts_list(limit)}


def h_chat_thought_post(req):
    """Мысли плат -> чат-сессия «Мысли плат» (id=thoughts)."""
    b = req.body or {}
    board = str(b.get("board") or "?")[:40]
    text = store._tclean(str(b.get("text") or "")[:3500])   # v3.118
    if not text:
        return 200, {"ok": True, "skipped": "шум"}
    with store.LOCK:
        chat = store.chat_load("thoughts") or \
            store.chat_new("thoughts", "Мысли плат", kind="thoughts")
        chat["messages"].append({"role": "bot", "text": text,
                                 "zone": "Мысль", "brain": board,
                                 "ts": time.time()})
        chat["ts"] = time.time()
        store.chat_save("thoughts", chat)
    brain.mn_learn(text, title="мысль платы (%s)" % board,
                   src="мысль платы")   # v3.9: единый источник в сети
    return 200, {"ok": True}


def h_chat_thought_get(req):
    chat = store.chat_load("thoughts")
    if not chat:
        return 200, {"ok": True, "messages": []}
    return 200, {"ok": True, "messages": (chat.get("messages") or [])[-50:]}


def h_import(req):
    """Хроника: сообщения плат (лента на главной/в панели)."""
    b = req.body or {}
    board = str(b.get("board") or "?")[:40]
    store.chronicle_add(board, b.get("messages") or [])
    if board != "?":
        store.board_seen(board)
    return 200, {"ok": True}


def h_gen_task_post(req):
    b = req.body or {}
    board = str(b.get("board") or "?")[:40]
    text = str(b.get("text") or "")[:400]
    if text:
        store.gen_task_add(board, text, b.get("src") or "мозг")
        store.log("ЗАДАНИЕ", "от %s: %s" % (board, text[:60]))
    return 200, {"ok": True}


def h_gen_task_get(req):
    task = store.gen_task_take()
    return 200, {"ok": True, "task": task}


def h_readings_post(req):
    b = req.body or {}
    board = str(b.get("board") or "?")[:40]
    store.readings_put(board, b.get("readings") or {})
    return 200, {"ok": True}


def h_readings_get(req):
    rd = store.readings_all()
    out = [{"board": k, "readings": v.get("readings"), "ts": v.get("ts")}
           for k, v in rd.items()]
    out.sort(key=lambda x: -(x.get("ts") or 0))
    return 200, {"ok": True, "readings": out[:20],
                 "cloud": brain.mn_readings()}


# ------------------------------ веб-шлюз ------------------------------------
def h_web_fetch(req):
    q = str((req.body or {}).get("q") or "")[:120]
    ok, res = webfetch.fetch(q, limit=int(req.query.get("limit", "3")))
    if not ok:
        return 200, {"ok": False, "error": res}
    return 200, {"ok": True, "q": q, "results": res}


def h_sandbox_run(req):
    b = req.body or {}
    code = str(b.get("code") or "")[:30000]
    text = str(b.get("text") or "")[:2000]
    board = str(b.get("board") or "?")[:40]
    ok, out = sandbox.run_code(code, text, timeout=12)
    store.log("ПЕСОЧНИЦА", "код от %s: %s (%s байт)" %
              (board, "OK" if ok else "FAIL", len(code)))
    if "Тензорное ядро" in code[:400]:   # v3.27: задача тензора платы v2.39
        try:
            import tensorhelp
            tensorhelp.note_sandbox(ok)
        except Exception:
            pass
    return 200, ({"ok": True, "result": out} if ok
                 else {"ok": False, "error": out})


# ------------------------------ функции мозга -------------------------------
def h_skills_list(req):
    return 200, {"ok": True, "skills": skills.public_list()}


def h_skills_full(req):
    return 200, {"ok": True, "skills": skills.full_list()}


def h_skills_add(req):
    b = dict(req.body or {})
    # v3.172: приложение от платы — с автором «плата» (сохраняется ВЫКЛЮЧЕННЫМ,
    # его код исполняется в облаке — включает владелец в App Lab)
    b["origin"] = ("плата %s" % str(b.get("board") or "")).strip()[:24]
    r = skills.upsert(b)
    if r.get("ok"):
        store.log("НАВЫКИ", "функция обновлена: %s" % r.get("id"))
    return 200, r


def h_skills_get(req):
    sid = req.params.get("sid", "")
    for sk in skills.full_list():
        if sk.get("id") == sid:
            return 200, {"ok": True, "skill": sk}
    return 404, {"ok": False, "error": "нет функции"}


def h_skills_del(req):
    return 200, skills.delete_skill(req.params.get("sid", ""))


def h_skills_run(req):
    sid = req.params.get("sid", "")
    b = req.body or {}
    r = skills.run_skill(sid, str(b.get("text") or ""),
                         board=str(b.get("board") or "?"))
    return 200, r


def h_skills_learned(req):
    b = req.body or {}
    r = skills.learned(str(b.get("board") or "?"), str(b.get("id") or ""),
                       b.get("ver") or 1)
    return 200, r


# ------------------------------ словари -------------------------------------
def _dict_count(data):
    if not isinstance(data, dict):
        return 0
    if isinstance(data.get("words"), list):
        return len(data["words"])
    n = 0
    for k in ("nouns", "verbs", "adjs"):
        if isinstance(data.get(k), list):
            n += len(data[k])
    return n


def _dict_list_entry(name):
    d = store.load_json("dicts/%s.json" % name, {}) or {}
    return {"name": name, "words": _dict_count(d), "ts": time.time()}


def h_dict_list(req):
    dicts = [_dict_entry_meta(n) for n in ("ru_top", "ru_pos", "extra")]
    return 200, {"ok": True, "dicts": dicts}


def _dict_entry_meta(name):
    d = store.load_json("dicts/%s.json" % name, {}) or {}
    return {"name": name, "words": _dict_count(d),
            "ts": d.get("ts") or 0}


def h_dict_get(req):
    name = req.query.get("name", "")[:20]
    if name not in ("ru_top", "ru_pos", "extra"):
        return 200, {"ok": False, "error": "нет такого словаря"}
    d = store.load_json("dicts/%s.json" % name, {}) or {}
    if not d:
        return 200, {"ok": True, "found": False, "name": name}
    return 200, {"ok": True, "found": True, "name": name, "data": d}


def h_dict_put(req):
    """Словарь от платы: облако ДЕРЖИТ ОБЪЕДИНЕНИЕ (склеивает)."""
    b = req.body or {}
    name = str(b.get("name") or "")[:20]
    data = b.get("data")
    if name not in ("ru_top", "ru_pos", "extra") or not isinstance(data, dict):
        return 200, {"ok": False, "error": "нужны name (ru_top|ru_pos|extra) "
                                            "и data"}
    with store.LOCK:
        cur = store.load_json("dicts/%s.json" % name, {}) or {}
        if name == "ru_pos":
            for k in ("nouns", "verbs", "adjs"):
                got = [str(w)[:24] for w in (data.get(k) or []) if w]
                cur_k = set(cur.get(k) or [])
                add = [w for w in got if w not in cur_k][:20000]
                cur[k] = (cur.get(k) or []) + add
        else:
            import llm2                  # v3.118: «англ», «лат» — не слова
            got = [str(w)[:24] for w in (data.get("words") or [])
                   if w and str(w).lower() not in llm2.JUNK_W]
            cur["words"] = [w for w in (cur.get("words") or [])
                            if str(w).lower() not in llm2.JUNK_W]
            cur_w = set(cur.get("words") or [])
            add = [w for w in got if w not in cur_w][:30000]
            cap = 60000 if name == "ru_top" else 12000
            cur["words"] = ((cur.get("words") or []) + add)[-cap:]
        cur["ts"] = time.time()
        store.save_json("dicts/%s.json" % name, cur)
    store.log("СЛОВАРЬ", "принято: %s (+%s слов)" % (name, _dict_count(data)))
    return 200, {"ok": True, "name": name, "words": _dict_count(cur)}


# ------------------------------ база знаний ---------------------------------
def h_kb_list(req):
    arts = kb.list_articles(limit=int(req.query.get("limit", "80")),
                            device=req.query.get("device"))
    return 200, {"ok": True, "articles": arts}


def h_kb_article(req):
    art = kb.article(req.params.get("aid", ""), req.query.get("device"))
    if not art:
        return 404, {"ok": False, "error": "статья не найдена"}
    return 200, art


def h_kb_search(req):
    r = kb.search(req.query.get("q", ""),
                  limit=int(req.query.get("limit", "3")),
                  wiki=int(req.query.get("wiki", "0") or 0),
                  device=req.query.get("device"))
    return 200, r


def h_kb_import(req):
    b = req.body or {}
    r = kb.add_article(b.get("title"), b.get("text"),
                       source=b.get("source") or "мозг-12",
                       device=b.get("device"))
    if r.get("ok"):
        store.log("KB", "статья: %s (%s зн.)" % (b.get("title", "?")[:50],
                                                 r.get("chars")))
    return 200, r


def h_kb_delete(req):
    b = req.body or {}
    ok = kb.delete_article(str(b.get("id") or ""), b.get("device"))
    return 200, {"ok": bool(ok)}


# ------------------------------ TTS / LLM -----------------------------------
def h_tts(req):
    """POST /api/tts {text, voice} -> WAV-байты (мозг играет динамиком).
    Без токена тоже доступен: синтез ограничен 12 с, данных не выдаёт."""
    b = req.body or {}
    text = str(b.get("text") or "")[:600]
    voice = str(b.get("voice") or store.settings().get("tts_voice") or "neuro")
    if not text:
        return 400, {"ok": False, "error": "пустой текст"}
    data = tts.synth(text, voice)
    if data[:4] != b"RIFF":
        data = tts.synth_builtin(text, voice)
    return 200, "wav", data


def h_llm_teach(req):
    """Учительский канал: purpose word_goal|verdict|advice — ТОЛЬКО обучение."""
    teacher.rate_guard()
    b = req.body or {}
    r = teacher.teach(str(b.get("purpose") or ""), b.get("messages") or [],
                      int(b.get("max_tokens") or 500))
    return 200, r


def h_llm_ask(req):
    """Старый ответчик LLM закрыт навсегда (как в мозге v2.14)."""
    return 410, {"ok": False, "error": "/api/llm/ask закрыт: LLM не отвечает "
                 "в чате. Только /api/llm/teach (учитель).",
                 "retired": True}


def h_llm_test(req):
    return 200, teacher.test_llm()


# ------------------------------ зрение --------------------------------------
def h_vision_frame(req):
    b = req.body or {}
    return 200, vision.frame(b.get("img"), b.get("cam") or "cam",
                             float(b.get("conf") or 0.35),
                             board=str(b.get("cam") or ""))


def h_vision_raw(req):
    b = req.body or {}
    return 200, vision.raw(b.get("img"), b.get("cam") or "cam",
                           board=str(b.get("cam") or ""))


def h_vision_raster(req):
    """v3.52: кадр камеры -> растр 16x12 для D6 (только если опция включена)."""
    if not store.settings().get("d6_camframe"):
        return 200, {"ok": False, "error": "d6_camframe выключена"}
    import camraster
    b = req.body or {}
    return 200, camraster.raster(b.get("img"), b.get("w") or 16,
                                 b.get("h") or 12)


def h_vision_buffer(req):
    return 200, vision.buffer(int(req.query.get("limit", "3")),
                              req.query.get("cam", ""))


def h_vision_last(req):
    data = vision.last_jpeg(req.query.get("cam", "cam"))
    if not data:
        return 404, {"ok": False, "error": "кадров нет"}
    return 200, "jpg", data


def h_vision_cams(req):
    return 200, {"ok": True, "cams": vision.cams()}


# ------------------------------ файлы ---------------------------------------
def h_files_upload(req):
    """multipart: device, text, file -> текст в KB устройства + сеть."""
    device = str(req.files_data.get("device") or
                 (req.body or {}).get("device") or "владелец")[:40]
    # v3.27: БЕЗОПАСНОСТЬ — «..» в device писал файл поверх settings.json
    device = "".join(c for c in device if c.isalnum() or c in "-_") \
        or "владелец"
    caption = str(req.files_data.get("text") or
                  (req.body or {}).get("text") or "")[:2000]
    fname = req.files_data.get("filename") or ""
    raw = req.files_data.get("file") or b""
    if not fname and not raw:
        return 400, {"ok": False, "error": "нет файла"}
    safe = "".join(c for c in fname if c.isalnum() or c in "._- ")[:80] \
        or "file.bin"
    fdir = store.p("files", device)
    import os
    os.makedirs(fdir, exist_ok=True)
    with open(store.p("files", device, safe), "wb") as f:
        f.write(raw)
    try:                       # v3.30: картинка — разбор и описание
        import imgsense
        if imgsense.is_image(safe, raw[:16]):
            desc = imgsense.describe(raw, name=safe)
            store.log("ФАЙЛЫ", "%s от %s: картинка разобрана" % (safe, device))
            return 200, {"ok": True, "name": safe, "kind": "image",
                         "chars": len(desc), "kb": True,
                         "description": desc}
    except Exception as e:
        store.log("ФАЙЛЫ", "картинка %s не разобрана: %s" % (safe, e))
    text, kind = _extract_text(safe, raw)
    chars = len(text)
    title = caption or safe
    if text and chars > 40:
        ra = kb.add_article(title, text, source="файл %s" % device,
                            device=device)
        # v3.28: честно — сколько знаков ЛЕГЛО в базу (статья до 200 000)
        kept = int(ra.get("chars") or 0) if ra.get("ok") else 0
        store.log("ФАЙЛЫ", "%s от %s: %s, %s зн. (в базе %s)"
                  % (safe, device, kind, chars, kept))
        return 200, {"ok": True, "name": safe, "kind": kind, "chars": kept,
                     "chars_file": chars, "kb": bool(ra.get("ok")),
                     "id": ra.get("id")}
    store.log("ФАЙЛЫ", "%s от %s: сохранён без текста (%s)" %
              (safe, device, kind))
    return 200, {"ok": True, "name": safe, "kind": kind, "chars": 0,
                 "kb": False}


def _extract_text(name, raw):
    try:
        import textutils
        return textutils.extract_text(name, raw)
    except Exception:
        return "", "bin"


def h_files_list(req):
    import os
    device = req.query.get("device", "")
    out = []
    try:
        root = store.p("files")
        devs = [device] if device else os.listdir(root)
        for d in devs:
            dd = store.p("files", d)
            if not os.path.isdir(dd):
                continue
            for fn in os.listdir(dd):
                fp = store.p("files", d, fn)
                if os.path.isfile(fp):
                    out.append({"device": d, "name": fn,
                                "size": os.path.getsize(fp),
                                "ts": os.path.getmtime(fp)})
    except Exception:
        pass
    out.sort(key=lambda x: -x.get("ts", 0))
    return 200, {"ok": True, "files": out[:200]}


def h_files_get(req):
    import urllib.parse
    rel = urllib.parse.unquote(req.params.get("fn", ""))
    import os
    # v3.27: БЕЗОПАСНОСТЬ — путь обязан остаться ВНУТРИ data/files: раньше
    # «..%2Fsettings.json» отдавал настройки (токен бота МАКС), sessions.json
    base = os.path.abspath(store.p("files"))
    fp = os.path.abspath(os.path.join(base, rel))
    if not fp.startswith(base + os.sep) or not os.path.isfile(fp):
        return 404, {"ok": False, "error": "файл не найден"}
    with open(fp, "rb") as f:
        data = f.read()
    low = rel.lower()
    ct = "application/octet-stream"
    if low.endswith((".txt", ".md", ".log")):
        ct = "text/plain; charset=utf-8"
    elif low.endswith(".csv"):
        ct = "text/csv; charset=utf-8"
    elif low.endswith(".json"):
        ct = "application/json"
    elif low.endswith(".docx"):
        ct = ("application/vnd.openxmlformats-officedocument."
              "wordprocessingml.document")
    elif low.endswith(".pdf"):
        ct = "application/pdf"
    # v3.27: документы ИИ и любые html — только СКАЧИВАНИЕМ (html из
    # файлов не открывается в панели: чужие скрипты не исполняются)
    attach = low.endswith((".html", ".htm", ".docx", ".csv", ".md")) or \
        low.startswith("документы/")
    if low.endswith((".html", ".htm")):
        ct = "application/octet-stream"
    return 200, "raw", (data, ct, os.path.basename(rel) if attach else None)


def h_files_delete(req):
    import os
    b = req.body or {}
    name = str(b.get("name") or "")[:120]
    device = str(b.get("device") or "")[:40]
    # v3.27: БЕЗОПАСНОСТЬ — удалять можно только внутри data/files
    base = os.path.abspath(store.p("files"))
    fp = os.path.abspath(os.path.join(base, device, name))
    if not fp.startswith(base + os.sep):
        return 200, {"ok": False, "error": "файл не найден"}
    try:
        os.remove(fp)
        return 200, {"ok": True}
    except Exception:
        return 200, {"ok": False, "error": "файл не найден"}


ROUTES = {}


def _r(method, pattern, fn, auth=False):
    ROUTES[(method, pattern)] = (fn, auth)


def h_board_codegen(req):
    """v3.22: плата просит облако написать код (режим «Код»).
    POST {text, lang?, board?} -> {ok, reply, lang, task, code}. Без LLM."""
    import codegen
    b = req.body or {}
    text = str(b.get("text") or "").strip()[:2000]
    if not text:
        return 400, {"ok": False, "error": "пустая просьба"}
    reply, matched, info = codegen.answer(text, lang=str(b.get("lang") or ""))
    store.log("КОД", "плата %s просила код: %s" % (
        str(b.get("board") or "?")[:40], "собрано" if matched and info.get("code_task") else "нет заготовки"))
    if not matched or not info.get("code_task"):
        return 200, {"ok": False, "error": "подходящей заготовки нет", "reply": reply or ""}
    m = __import__("re").search(r"```[\w+#-]*\n(.*?)```", reply or "", __import__("re").S)
    return 200, {"ok": True, "reply": reply, "lang": info.get("code_lang"),
                 "task": info.get("code_task"), "code": (m.group(1) if m else "")}


_r("GET", "api/health", h_health)
_r("POST", "api/board/codegen", h_board_codegen, auth=True)
_r("POST", "api/brains/register", h_brains_register, auth=True)
_r("GET", "api/brains", h_brains_list)
_r("POST", "api/brains/delete", h_brains_delete)
_r("POST", "api/brains/ask", h_brains_ask, auth=True)
_r("POST", "api/brain/think", h_brain_think, auth=True)   # v3.28
_r("POST", "api/brain/learn", h_brain_learn, auth=True)   # v3.29: единый мозг
_r("GET", "api/brain/knowledge", h_brain_knowledge, auth=True)
_r("POST", "api/brain/msg", h_brain_msg, auth=True)
_r("GET", "api/brain/inbox", h_brain_inbox, auth=True)
_r("GET", "api/brain/d6", h_brain_d6, auth=True)
_r("POST", "api/negfreq/decide", h_negfreq_decide, auth=True)
_r("POST", "api/genmod/words", h_genmod_words, auth=True)
_r("POST", "api/genmod/frame", h_genmod_frame, auth=True)
_r("GET", "api/brain/ping", h_brain_ping, auth=True)
_r("POST", "api/brain/answer", h_brain_answer, auth=True)
_r("POST", "api/brain/progress", h_brain_progress, auth=True)
_r("GET", "api/brain/result/<mid>", h_brain_result, auth=True)
_r("GET", "api/brain/log", h_brain_log)
_r("POST", "api/memory/save", h_memory_save, auth=True)
_r("GET", "api/memory/load", h_memory_load, auth=True)
_r("POST", "api/memory/delete", h_memory_delete, auth=True)
_r("GET", "api/memory/list", h_memory_list)
_r("GET", "api/buffer", h_buffer, auth=True)
_r("GET", "api/cache", h_buffer, auth=True)
_r("POST", "api/think", h_think, auth=True)
_r("POST", "api/link", h_link, auth=True)
_r("GET", "api/particles", h_particles)          # v3.177.2: кванты на D6
_r("POST", "api/particles", h_particles)


def h_board_read(req):
    """v3.167: МОЗГ ПЛАТЫ НЕ ЗНАЕТ — ЧИТАЕТ САМ. Плата присылает тему,
    облако находит статью (Википедия, иначе Рувики), сохраняет в личную
    базу знаний этой платы и отдаёт текст: плата учит на нём сеть знаний
    генератора речи (связная речь) и нейросеть материалов."""
    import readlike
    b = req.body or {}
    board = str(b.get("board") or "")[:40]
    topic = str(b.get("topic") or "").strip()[:150]
    if not topic:
        return 400, {"ok": False, "error": "нет темы"}
    if not store.settings().get("web_on"):
        return 200, {"ok": False, "error": "в облаке выключен интернет (Настройки → «Интернет»)"}
    art, why = readlike.fetch_article(topic)
    if not art:
        store.log("ЧТЕНИЕ", "плата %s: «%s» — статьи нет (%s)" % (board, topic[:60], why))
        return 200, {"ok": False, "error": why or "статьи не нашлось"}
    try:
        import kb
        kb.add_article(art["title"], art["text"],
                       source="%s (прочитала плата)" % art["src"],
                       device=board or None)
    except Exception:
        pass
    store.log("ЧТЕНИЕ", "плата %s не знала «%s» — прочитала «%s» (%s, %d зн.)" % (
        board, topic[:60], art["title"][:60], art["src"], len(art["text"])))
    return 200, {"ok": True, "title": art["title"], "src": art["src"],
                 "text": art["text"][:12000]}


_r("POST", "api/board/read", h_board_read, auth=True)
_r("POST", "api/doom/bot", h_doom_bot, auth=True)
_r("POST", "api/gubble/bot", h_gubble_bot, auth=True)          # v3.181: Gubble 2
_r("POST", "api/worddict/sync", h_worddict_sync, auth=True)  # v3.133: общий словарь
_r("POST", "api/hearing/stream", h_hearing_stream, auth=True)   # v3.178: живой звук
_r("GET", "api/hearing/live", h_hearing_live, "panel")
_r("POST", "api/hearing/live", h_hearing_live, "panel")
_r("POST", "api/hearing/vosk-board", h_vosk_board, "panel")     # v3.181: Vosk на плату по кнопке
_r("POST", "api/hearing/to-board", h_hearing_to_board, "panel") # v3.181.4: звук -> динамик платы
_r("GET", "api/hearing/live.wav", h_hearing_live_wav, "panel")
_r("POST", "api/hearing/voice", h_hearing_voice)
_r("POST", "api/board/codejob", h_board_codejob, auth=True)
_r("POST", "api/board/backup", h_board_backup, auth=True)      # v3.180: сейф памяти плат
_r("POST", "api/board/restore", h_board_restore, auth=True)
_r("GET", "api/board/pair", h_board_pair, "open")               # плата без токена: подключение
_r("POST", "api/board/pair", h_board_pair, "open")
_r("GET", "api/board/safe", h_board_safe)                       # владелец
_r("POST", "api/board/safe", h_board_safe)      # v3.178: результат задания платы
_r("GET", "api/code/board", h_code_board)                          # v3.178: код на плате (владелец)
_r("POST", "api/code/board", h_code_board)
_r("GET", "api/hearing/vosk-model.zip", h_vosk_model_zip, auth=True)                # v3.178: голосовой ввод (владелец)
_r("GET", "api/hearing/phrase.wav", h_hearing_phrase_wav, "panel")
_r("POST", "api/hearing/mic", h_hearing_mic, auth=True)    # v3.128: уши мозга          # v3.127: игра мозга                  # v3.102: связь мозгов
_r("GET", "api/thoughts", h_thoughts)
_r("POST", "api/chat/thought", h_chat_thought_post, auth=True)
_r("GET", "api/chat/thought", h_chat_thought_get)
_r("POST", "api/import", h_import, auth=True)
_r("POST", "api/gen/task", h_gen_task_post, auth=True)
_r("GET", "api/gen/task", h_gen_task_get, auth=True)
_r("POST", "api/chat/readings", h_readings_post, auth=True)
_r("GET", "api/chat/readings", h_readings_get)
_r("POST", "api/web/fetch", h_web_fetch, auth=True)
_r("POST", "api/sandbox/run", h_sandbox_run, auth=True)
_r("GET", "api/skills", h_skills_list)
_r("GET", "api/skills/full", h_skills_full)
_r("POST", "api/skills", h_skills_add, auth=True)
_r("GET", "api/skills/<sid>", h_skills_get)
# v3.27: удаление навыка — только с токеном платы или сессией владельца
# (раньше любой в сети мог стереть навык одним запросом)
_r("DELETE", "api/skills/<sid>", h_skills_del, auth=True)
_r("POST", "api/skills/<sid>/run", h_skills_run)
_r("POST", "api/skills/learned", h_skills_learned, auth=True)
_r("GET", "api/dict/list", h_dict_list)
_r("GET", "api/dict/get", h_dict_get, auth=True)
_r("POST", "api/dict/put", h_dict_put, auth=True)
_r("GET", "api/kb/list", h_kb_list)
_r("GET", "api/kb/search", h_kb_search)
_r("GET", "api/kb/article/<aid>", h_kb_article)


def h_kb_pack(req):
    """v3.49: база знаний для платы в JSON (плата тянет пачками)."""
    import kbpack
    q = req.query
    dev = str(q.get("device") or "")[:40]
    if dev:
        store.board_seen(dev, ip=req.client_ip)
    try:
        return 200, kbpack.pack(float(q.get("since") or 0),
                                int(q.get("offset") or 0),
                                int(q.get("limit") or 15), dev)
    except (TypeError, ValueError):
        return 400, {"ok": False, "error": "неверные параметры"}


_r("GET", "api/kb/pack", h_kb_pack, auth=True)
_r("POST", "api/kb/import", h_kb_import)
_r("POST", "api/kb/delete", h_kb_delete)
_r("POST", "api/tts", h_tts)
_r("POST", "api/llm/teach", h_llm_teach, auth=True)
_r("POST", "api/llm/ask", h_llm_ask, auth=True)
_r("POST", "api/llm/test", h_llm_test)
_r("POST", "api/vision/frame", h_vision_frame, auth=True)
_r("POST", "api/vision/raw", h_vision_raw, auth=True)
_r("POST", "api/vision/raster", h_vision_raster, auth=True)
_r("GET", "api/vision/buffer", h_vision_buffer)
_r("GET", "api/vision/last.jpg", h_vision_last, "panel")
_r("GET", "api/vision/cams", h_vision_cams, "panel")
_r("POST", "api/files/upload", h_files_upload)
_r("GET", "api/files/list", h_files_list)
_r("GET", "api/files/get/<fn>", h_files_get)
_r("POST", "api/files/delete", h_files_delete)
