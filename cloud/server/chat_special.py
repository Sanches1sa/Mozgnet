# -*- coding: utf-8 -*-
"""v3.226 — постоянные чаты «Администрирование» и «Код и App Lab».
История пишется в chats/chat-admin.json и chat-code.json (общие, не по IP).
Код: App Lab (напиши/проверь/запусти приложение и кирпичик, список) -> кодинг-агент ->
мозг. Администрирование: «выполни …» запускает задание агента администрирования
(опасное по-прежнему ждёт подтверждения владельца), вопросы — по инструкциям из базы знаний.
LLM наблюдает: события App Lab идут в журнал для обучения (applab_brain.observe)."""
import re

import store


def _admin(text, ctx, cid, ip):
    import brain
    low = text.lower().strip()
    m = re.match(r"^\s*(?:выполни|запусти задание|сделай|задание)\s*[:\-—]?\s+(.+)$", text, re.I | re.S)
    if m:
        import admin_agent
        r = admin_agent.start_job(m.group(1), owner=ip or "чат")
        if r.get("ok"):
            return ("Задание принято (id %s). Агент администрирования работает по инструкциям; "
                    "всё, что меняет систему, ждёт вашего подтверждения — ход и кнопки «Разрешить» в панели «Администрирование». "
                    "Результат появится в этой истории при следующем «статус %s»." % (r["job_id"], r["job_id"]),
                    brain.Z["sys"], {"admin_job": r["job_id"]})
        return "Не запустила: %s" % r.get("error"), brain.Z["sys"], {}
    m = re.match(r"^\s*статус\s+(\w{6,20})\s*$", low)
    if m:
        import admin_agent
        j = admin_agent.get_job(m.group(1))
        if not j:
            return "Нет такого задания.", brain.Z["sys"], {}
        return "Задание %s: %s. Шагов: %d. Итог: %s" % (j["id"], j.get("status"), len(j.get("steps") or []),
                                                       str(j.get("result") or "—")[:600]), brain.Z["sys"], {"admin_job": j["id"]}
    try:
        import kb
        hits = [h for h in (kb.search(text, limit=5) or {}).get("results") or []
                if str(h.get("title", "")).startswith(("Администрирование", "Инструкция"))]
        if hits:
            h = hits[0]
            art = kb.article(h.get("id")) if hasattr(kb, "article") and h.get("id") else None
            body = (art or {}).get("text") if isinstance(art, dict) else None
            return "По инструкции «%s»:\n%s\n\nЧтобы выполнить — напишите «выполни: <задача>»." % (
                h["title"], str(body or h.get("snippet") or "")[:2500]), brain.Z["sys"], {"kb": h["title"]}
    except Exception:
        pass
    r, z, i = brain.think(text, False, route="cloud", chat_id=cid, ctx=ctx)
    return r, z, i


def _code(text, ctx, cid, ip):
    import brain
    import applab_brain
    ab = applab_brain.handle(text)
    if ab:
        return ab[0], brain.Z["code"], ab[1]
    import agent
    try:
        reply, matched, cinfo = agent.code_agent(text, ctx=ctx)
    except Exception as e:
        return "Код-агент споткнулся: %s" % e, brain.Z["code"], {}
    if matched:
        return reply, brain.Z["code"], {k: v for k, v in cinfo.items() if k != "code"}
    r, z, i = brain.think(text, False, route="cloud", chat_id=cid, ctx=ctx)
    return r, z, i


def reply(kind, text, ctx, cid, ip=""):
    """-> (ответ, зона, info). История сохраняется вызывающим (_chat_process)."""
    return (_admin if kind == "admin" else _code)(text, ctx, cid, ip)
