# -*- coding: utf-8 -*-
# ===========================================================================
# kbpack.py — БАЗА ЗНАНИЙ ОБЛАКА В JSON ДЛЯ ПЛАТЫ, КУСКАМИ С КОНТЕКСТОМ (v3.50)
# ---------------------------------------------------------------------------
# Заявка владельца: «база знаний записывается в JSON на плату и плата к ней
# обращается» (v3.49), затем: «увеличить лимит знаков или делить статьи на
# куски, но чтобы был контекст». Теперь статья делится на КУСКИ ~1200 знаков
# по абзацам и предложениям, с НАХЛЁСТОМ (хвост предыдущего куска повторяется
# в начале следующего, ~150 знаков), у каждого куска — заголовок статьи,
# заголовок раздела, номер «i из n» и ссылка на статью; в нулевом куске —
# «начало статьи» (lead), которое плата подставляет, если нашла кусок из
# середины. Лимит статьи — kb_pack_chars (по умолчанию 24000 знаков).
# GET /api/kb/pack?since=&offset=&limit=&device= (пачка = limit СТАТЕЙ):
#   {ok, v:2, ts, total, changed, offset, next, ids:[id кусков] (offset=0),
#    items:[{id:"<статья>#<i>", a:<статья>, i, n, t, h:<раздел>, l:<lead>,
#            x:<текст куска>, k:[слова], s:<источник>, ts}]}
# Старая плата (v2.45) читает кусок как отдельную статью — тоже работает.
# ===========================================================================
import re
import time

import kb
import store

_CACHE = {}                       # id статьи -> (ts, chars, [куски])


def _cfg():
    s = store.settings()
    if not s.get("kb_pack_mig86"):     # v3.86: прежние умолчания 500/4000 -> 3000/12000
        upd = {"kb_pack_mig86": True}
        if int(s.get("kb_pack_max") or 500) == 500:
            upd["kb_pack_max"] = 3000
        if int(s.get("kb_pack_chunks_max") or 4000) == 4000:
            upd["kb_pack_chunks_max"] = 12000
        store.update_settings(upd)
        s = store.settings()

    def num(k, d, lo):
        try:
            return max(lo, int(s.get(k, d)))
        except (TypeError, ValueError):
            return d
    return {"max": num("kb_pack_max", 3000, 10),
            "art": num("kb_pack_chars", 24000, 200),
            "chunk": num("kb_pack_chunk", 1200, 300),
            "over": num("kb_pack_overlap", 150, 0),
            "chunks_max": num("kb_pack_chunks_max", 12000, 50),
            "code": bool(s.get("kb_pack_code", False))}


def _units(text):
    """[(раздел, предложение)]: абзацы -> предложения; заголовки (#…, короткая
    строка без точки) запоминаются как раздел."""
    out, heading = [], ""
    for p in re.split(r"\n\s*\n|\n(?=#)", str(text)):
        p = p.strip()
        if not p:
            continue
        if p.startswith("#") or (len(p) < 80 and "\n" not in p and
                                 not re.search(r"[.!?:;,]$", p)):
            heading = p.lstrip("# ").strip()[:100]
            continue
        for sn in re.split(r"(?<=[.!?])\s+", p.replace("\n", " ")):
            sn = sn.strip()
            if sn:
                out.append((heading, sn))
    return out


def chunk_text(text, size=1200, overlap=150):
    """-> [{"x": текст, "h": раздел}] с нахлёстом конца предыдущего куска."""
    units = []
    for h, sn in _units(text):
        while len(sn) > size:                     # очень длинное предложение
            cut = sn.rfind(" ", 0, size)
            cut = cut if cut > size // 2 else size
            units.append((h, sn[:cut].strip()))
            sn = sn[cut:].strip()
        if sn:
            units.append((h, sn))
    chunks, cur, curlen, carried = [], [], 0, 0
    for h, sn in units:
        if cur and curlen + len(sn) + 1 > size and len(cur) > carried:
            chunks.append({"x": " ".join(s for _h, s in cur),
                           "h": next((hh for hh, _s in cur[carried:] if hh),
                                     cur[0][0])})
            tail, tl = [], 0
            for u in reversed(cur):               # нахлёст: хвост куска
                if tl + len(u[1]) > overlap:
                    break
                tail.insert(0, u)
                tl += len(u[1]) + 1
            cur, curlen, carried = tail, tl, len(tail)
        cur.append((h, sn))
        curlen += len(sn) + 1
    if cur and len(cur) > carried:
        chunks.append({"x": " ".join(s for _h, s in cur),
                       "h": next((hh for hh, _s in cur[carried:] if hh),
                                 cur[0][0])})
    return chunks


def _article_chunks(meta, c):
    aid = meta.get("id")
    key = (float(meta.get("ts") or 0), c["art"], c["chunk"], c["over"])
    hit = _CACHE.get(aid)
    if hit and hit[0] == key:
        return hit[1]
    art = kb.article(aid, meta.get("device")) or {}
    text = str(art.get("text") or "").strip()[:c["art"]]
    out = []
    if len(text) >= 20:
        parts = chunk_text(text, c["chunk"], c["over"])
        lead = " ".join(sn for _h, sn in _units(text)[:3])[:200].strip()
        n = len(parts)
        for i, p in enumerate(parts):
            it = {"id": "%s#%d" % (aid, i), "a": aid, "i": i, "n": n,
                  "t": str(meta.get("title") or "")[:160], "h": p["h"],
                  "x": p["x"], "k": list(meta.get("kw") or [])[:6],
                  "s": str(meta.get("source") or "")[:40],
                  "ts": float(meta.get("ts") or 0)}
            if i == 0:
                it["l"] = lead
            out.append(it)
    if len(_CACHE) > 20000:
        _CACHE.clear()
    _CACHE[aid] = (key, out)
    return out


def selected(device=""):
    """[(метаданные, куски)] свежие первыми, в пределах kb_pack_max статей и
    kb_pack_chunks_max кусков."""
    c = _cfg()
    out, total = [], 0
    for m in kb.list_articles(limit=100000, include_devices=True):
        dv = m.get("device")
        if dv and str(dv) != str(device):
            continue                                  # личное чужой платы
        title = str(m.get("title") or "")
        if not c["code"] and (title.startswith("Исходник:") or
                              str(m.get("id") or "").startswith("gh_")):
            continue
        ch = _article_chunks(m, c)
        if not ch:
            continue
        if total + len(ch) > c["chunks_max"]:
            break
        out.append((m, ch))
        total += len(ch)
        if len(out) >= c["max"]:
            break
    return out


def pack(since=0.0, offset=0, limit=15, device=""):
    sel = selected(device)
    changed = [x for x in sel if float(x[0].get("ts") or 0) > float(since or 0)]
    limit = max(1, min(60, int(limit or 15)))
    offset = max(0, int(offset or 0))
    page = changed[offset:offset + limit]
    items = [it for _m, ch in page for it in ch]
    nxt = offset + limit if offset + limit < len(changed) else None
    res = {"ok": True, "v": 2, "ts": time.time(), "total": len(sel),
           "changed": len(changed), "offset": offset, "next": nxt,
           "items": items}
    if offset == 0:
        res["ids"] = [it["id"] for _m, ch in sel for it in ch]
    return res
