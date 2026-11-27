# -*- coding: utf-8 -*-
# ===========================================================================
# aiapi.py — API ИИ ДЛЯ ДРУГИХ ПРИЛОЖЕНИЙ (v3.28, только stdlib)
# ---------------------------------------------------------------------------
# Заявка владельца: «создать API для ИИ, чтобы к нему могли подключаться
# другие приложения». Ключи выдаёт владелец в панели («Настройки → API для
# приложений»); на сервере хранится только SHA-256 ключа, сам ключ
# показывается один раз. Запросы — с заголовком
#     Authorization: Bearer n12-…      (или X-Api-Key: n12-…)
# Маршруты (все JSON, CORS разрешён — ключ, а не cookie):
#   GET  /api/v1/info              — кто я, версия, зоны, маршруты
#   POST /api/v1/ask               — {question, session?, mode?, long?,
#                                     template?, name?} -> {answer, zone…}
#   POST /api/v1/chat/completions  — совместимо с форматом OpenAI Chat
#                                     Completions (messages -> choices);
#                                     stream не поддерживается (честно 400)
#   GET  /api/v1/models            — список «моделей» (одна: mozg-12)
#   POST /api/v1/kb/search         — {q, limit} — поиск по базе знаний
#   POST /api/v1/rate              — {session, v: 1|-1} — оценка последнего
#                                     ответа сессии (обучение на ответах)
# Отвечает ВЕСЬ мозг облака (brain.think: документы, база знаний, интернет,
# калькулятор, навыки, сеть материалов, память ответов), режимы "talk"
# (разговор + мысли облака) и "code" (ИИ-агент кода). Отправитель в мозге —
# «API: <имя ключа>»: исправления из API — только предложения, не знание.
# Лимит запросов — ai_rate_per_min на ключ (по умолчанию 60/мин).
# ===========================================================================
import hashlib
import hmac
import os
import re
import threading
import time

import store

_FILE = "ai_keys.json"
_LOCK = threading.RLock()
_SESS = {}                    # (key_id, session) -> {"ctx": [...], "ts", "last"}
_SESS_MAX = 600
_CTX_MAX = 12
_HITS = {}                    # key_id -> [ts запросов за минуту]
MODEL = "mozg-12"


# --------------------------------- ключи ------------------------------------
def _h(key):
    return hashlib.sha256(str(key or "").encode("utf-8")).hexdigest()


def _db():
    d = store.load_json(_FILE, None) or {}
    d.setdefault("keys", [])
    return d


def keys_list():
    with _LOCK:
        return [{k: v for k, v in x.items() if k != "hash"}
                for x in _db()["keys"]]


def key_create(name):
    name = re.sub(r"[^\w .\-]", "", str(name or "")).strip()[:40] or \
        "приложение"
    key = "n12-" + os.urandom(20).hex()
    rec = {"id": "k" + os.urandom(4).hex(), "name": name, "hash": _h(key),
           "prefix": key[:10] + "…", "created": time.time(),
           "last_used": 0.0, "calls": 0, "on": True}
    with _LOCK:
        d = _db()
        d["keys"].append(rec)
        store.save_json(_FILE, d)
    store.log("API", "выдан ключ «%s» (%s)" % (name, rec["prefix"]))
    return key, {k: v for k, v in rec.items() if k != "hash"}


def key_set(kid, on=None, remove=False):
    with _LOCK:
        d = _db()
        for i, x in enumerate(d["keys"]):
            if x.get("id") == kid:
                if remove:
                    d["keys"].pop(i)
                    store.log("API", "ключ «%s» отозван" % x.get("name"))
                elif on is not None:
                    x["on"] = bool(on)
                store.save_json(_FILE, d)
                return True
    return False


def check(key):
    """Ключ из запроса -> запись ключа | None. Сравнение хешей — за
    постоянное время."""
    key = str(key or "").strip()
    if not key.startswith("n12-") or len(key) < 20:
        return None
    hk = _h(key)
    with _LOCK:
        d = _db()
        for x in d["keys"]:
            if x.get("on", True) and hmac.compare_digest(x.get("hash", ""),
                                                         hk):
                x["last_used"] = time.time()
                x["calls"] = int(x.get("calls") or 0) + 1
                # счётчик пишем не на каждый запрос — раз в 20
                if x["calls"] % 20 == 1:
                    store.save_json(_FILE, d)
                return {k: v for k, v in x.items() if k != "hash"}
    return None


def key_from_headers(headers, query=None):
    a = str(headers.get("Authorization") or "")
    m = re.match(r"\s*Bearer\s+(\S+)", a, re.I)
    if m:
        return m.group(1)
    return str(headers.get("X-Api-Key") or (query or {}).get("api_key") or "")


def rate_ok(kid):
    try:
        lim = int(store.settings().get("ai_rate_per_min") or 60)
    except (TypeError, ValueError):
        lim = 60
    now = time.time()
    with _LOCK:
        h = [t for t in _HITS.get(kid, []) if now - t < 60.0]
        if len(h) >= lim:
            _HITS[kid] = h
            return False
        h.append(now)
        _HITS[kid] = h
    return True


# --------------------------------- сессии -----------------------------------
def _sess(kid, sid):
    k = (kid, str(sid or "default")[:64])
    with _LOCK:
        s = _SESS.get(k)
        if s is None:
            if len(_SESS) >= _SESS_MAX:
                old = sorted(_SESS, key=lambda x: _SESS[x]["ts"])[:100]
                for o in old:
                    _SESS.pop(o, None)
            s = _SESS[k] = {"ctx": [], "ts": time.time(), "last": None}
        s["ts"] = time.time()
        return s


# --------------------------------- ответ ------------------------------------
def cloud_reply(question, *, ctx=None, chat_key='', name='', sender='API', long_mode=False, template=True, limit=None):
    """One application service for panel, builtin MAX and HTTP API.
    No loopback HTTP, service token or messenger-specific answer alterations.
    """
    import dialog
    if limit is None:
        limit=int(store.settings().get('tpl_chat_limit') or 4000)
    return dialog.respond(question,ctx=ctx,chat_key=chat_key,name=name,sender=sender,
                          deliver=None,long_mode=long_mode,limit=limit,template=template)


def answer(question, key, session="", mode="auto", long_mode=False,
           ctx=None, template=False, name="", chat_key=None, sender=None):
    """Общий вход: вопрос -> dict ответа. ctx — история (если приложение
    шлёт её само, как в Chat Completions), иначе — память сессии."""
    import brain
    t0 = time.time()
    q = str(question or "").strip()[:20000 if mode == "code" else 4000]
    if not q:
        return {"ok": False, "error": "пустой вопрос"}
    who = sender or "API: %s" % (key or {}).get("name", "?")
    s = _sess((key or {}).get("id", "?"), session)
    incoming=list(ctx) if isinstance(ctx,list) else list(s['ctx'])
    hist=[]
    for item in incoming[-_CTX_MAX:]:
        if not isinstance(item,dict) or item.get('role') not in ('user','bot','assistant'):continue
        hist.append({'role':'bot' if item['role']=='assistant' else item['role'],
                     'text':str(item.get('text') or item.get('content') or '')[:4000]})
    if not hist or hist[-1].get('role')!='user' or hist[-1].get('text')!=q:
        hist.append({'role':'user','text':q})
    info = {}
    response_parts=None
    if mode == 'talk':
        import talkmind
        response_parts,info=talkmind.respond_parts(q,hist)
        reply='\n\n'.join(response_parts)
        zone=brain.Z['talk']
    elif mode == "code":
        import agent
        reply, matched, cinfo = agent.code_agent(q, ctx=hist)
        if matched:
            zone = brain.Z["code"]
            info = {k: v for k, v in (cinfo or {}).items() if k != "code"}
        else:
            response_parts,zone,info=cloud_reply(q,ctx=hist,chat_key=chat_key or 'api:%s:%s'%((key or {}).get('id','?'),session),name=name,sender=who,long_mode=long_mode,template=template)
            reply='\n\n'.join(response_parts)
    else:
        response_parts,zone,info=cloud_reply(q,ctx=hist,
            chat_key=chat_key or 'api:%s:%s'%((key or {}).get('id','?'),str(session or 'default')[:64]),
            name=name,sender=who,long_mode=long_mode,template=template)
        reply='\n\n'.join(response_parts)
    reply = str(reply or "")
    _raw_reply = reply                     # v3.99.1: с меткой — для шаблона
    reply = reply.replace("\u2063", "")
    try:                                   # v3.107: формулы — читаемым текстом
        import latexlite
        reply = latexlite.render_dollars(reply, "plain")
    except Exception:
        pass
    out = {"ok": True, "answer": reply, "zone": zone,
           "ms": int((time.time() - t0) * 1000), "model": MODEL,
           "session": str(session or "default")[:64]}
    if isinstance(info, dict):
        for k in ("learned", "thought", "qa", "self", "math", "unify",
                  "pending"):
            if info.get(k):
                out[k] = info[k]
    if response_parts is not None:
        out['parts']=[str(part).replace('\u2063','') for part in response_parts]
        out['engine']='talkmind' if mode=='talk' else 'cloud-dialog'
    if isinstance(info,dict):
        out['sources']=info.get('sources') or []
        out['focus_rejected']=bool(info.get('focus_rejected'))
        out['clarification']=bool(info.get('clarification'))
        if info.get('missing_fields'):out['missing_fields']=info['missing_fields']
        out['unanswered']=bool(info.get('focus_no_answer') or (not info.get('sources') and re.search(r'^Не знаю|нет.{0,50}подтвержд[её]нной памяти',reply,re.I)))
    if template and response_parts is None:
        try:
            import replytpl
            out["parts"] = replytpl.build(_raw_reply, name=name,
                                          chat_key="api:%s:%s" % (
                                              (key or {}).get("id", "?"),
                                              session),
                                          limit=3800)
        except Exception:
            pass
    with _LOCK:
        s["ctx"] = (hist + [{"role": "bot", "text": reply}])[-_CTX_MAX:]
        s["last"] = {"q": q, "a": reply, "zone": zone}
    return out


def rate(key, session, v):
    import qalearn
    s = _sess((key or {}).get("id", "?"), session)
    last = s.get("last")
    if not last:
        return {"ok": False, "error": "в этой сессии ещё не было ответа"}
    # оценка из приложения — не владелец: 👍 подтверждает, как и в чате
    # (ключ выдал владелец — он доверяет приложению оценивать)
    return dict(qalearn.rate(last["q"], last["a"], 1 if v > 0 else -1,
                             zone=last.get("zone") or "",
                             who="API: %s" % (key or {}).get("name", "?")),
                ok=True)


def _approx_tokens(text):
    """Приблизительно: «токен» ~ слово/знак (у мозга нет токенизатора LLM)."""
    return len(re.findall(r"\w+|[^\w\s]", str(text or "")))


def chat_completions(body, key):
    """Совместимость с OpenAI Chat Completions (без stream)."""
    msgs = body.get("messages") or []
    if not isinstance(msgs, list) or not msgs:
        return 400, {"error": {"message": "messages: нужен непустой список",
                               "type": "invalid_request_error"}}
    if body.get("stream"):
        return 400, {"error": {"message": "stream не поддерживается: мозг "
                               "отвечает целиком (stream=false)",
                               "type": "invalid_request_error"}}
    ctx, last = [], ""
    for m in msgs:
        if not isinstance(m, dict):
            continue
        c = m.get("content")
        if isinstance(c, list):            # [{type:text, text:…}]
            c = " ".join(str(p.get("text") or "") for p in c
                         if isinstance(p, dict))
        c = str(c or "")
        role = m.get("role")
        if role == "user":
            ctx.append({"role": "user", "text": c})
            last = c
        elif role == "assistant":
            ctx.append({"role": "bot", "text": c})
    if not last:
        return 400, {"error": {"message": "нет сообщения role=user",
                               "type": "invalid_request_error"}}
    if ctx and ctx[-1].get("role") == "user":
        ctx = ctx[:-1]
    mode = str(body.get("mode") or "auto")
    r = answer(last, key, session=str(body.get("user") or "chat")[:64],
               mode=mode if mode in ("auto", "cloud", "talk", "code") else "auto",
               long_mode=bool(body.get("long")), ctx=ctx[-_CTX_MAX:])
    if not r.get("ok"):
        return 400, {"error": {"message": r.get("error"),
                               "type": "invalid_request_error"}}
    pt = sum(_approx_tokens(m.get("text")) for m in ctx) + \
        _approx_tokens(last)
    ct = _approx_tokens(r["answer"])
    return 200, {
        "id": "chatcmpl-" + os.urandom(8).hex(),
        "object": "chat.completion", "created": int(time.time()),
        "model": MODEL,
        "choices": [{"index": 0, "finish_reason": "stop",
                     "message": {"role": "assistant",
                                 "content": r["answer"]}}],
        "usage": {"prompt_tokens": pt, "completion_tokens": ct,
                  "total_tokens": pt + ct, "approximate": True},
        "mozg": {"zone": r.get("zone"), "ms": r.get("ms")}}


def info():
    import brain
    return {"ok": True, "name": "Star Gate (neuro12-cloud)",
            "version": store.version(), "model": MODEL,
            "llm": False,
            "zones": [brain.Z.get(z, z) for z, _d, _e in brain.ZONES],
            "endpoints": {
                "POST /api/v1/ask": "{question, session?, mode: auto|talk|"
                                    "code, long?, template?, name?}",
                "POST /api/v1/chat/completions": "формат OpenAI (без stream)",
                "GET /api/v1/models": "список моделей",
                "POST /api/v1/kb/search": "{q, limit}",
                "POST /api/v1/rate": "{session, v: 1|-1}",
                "POST /api/v1/result": "{id} — поздний ответ платы, если в "
                                       "ответе ask было поле pending",
                "GET /api/v1/info": "это описание"},
            "auth": "Authorization: Bearer <ключ из панели>"}
