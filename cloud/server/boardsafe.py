# -*- coding: utf-8 -*-
"""v3.180: СЕЙФ ПАМЯТИ ПЛАТ и ПОДКЛЮЧЕНИЕ НОВОЙ ПЛАТЫ ПО КНОПКЕ.

Зачем: App Lab при обновлении приложения заменяет его папку целиком, а
контейнер приложения видит только её (/app) — все JSON платы (память,
словари, веса, настройки, адрес облака и токен) пропадали.

1. КОПИИ. Плата (v2.95+) раз в 10 минут присылает все свои JSON сжатым
   пакетом: POST /api/board/backup (токен платы). Облако держит последние
   5 копий на плату в data/board_backups/<плата>/.
2. ВОССТАНОВЛЕНИЕ. Плата, у которой памяти нет (свежая установка),
   спрашивает POST /api/board/restore {board, fresh}. Облако отдаёт копию
   этой же платы, а если такой нет (у контейнера новое имя) — самую свежую
   копию платы, которая сейчас НЕ на связи (это и есть прежняя она).
3. ПОДКЛЮЧЕНИЕ БЕЗ НАСТРОЕК. Плата без адреса облака ищет облако в своей
   сети и просит подключения: POST /api/board/pair {board, code, ip}
   (без токена). Облако показывает владельцу «Плата … просит подключиться,
   код NNNN»; владелец жмёт «Подключить» — плата забирает токен
   GET /api/board/pair?board&code. Без кнопки владельца токен не выдаётся.
"""
import base64
import gzip
import json
import os
import re
import threading
import time

import store

KEEP = 5
MAX_BLOB = 40 * 1024 * 1024
_LOCK = threading.RLock()
_PAIRS = {}          # (board, code) -> {board, code, ip, ver, ts, state}


def _dir(board=None):
    d = store.p("board_backups")
    if board:
        d = os.path.join(d, re.sub(r"[^\w.\-]", "_", str(board))[:60] or "board")
    return d


# ------------------------------------------------------------------ копии
def backup_in(board, blob_b64, files_n=0):
    board = str(board or "")[:40]
    if not board:
        return {"ok": False, "error": "нет имени платы"}
    try:
        raw = base64.b64decode(str(blob_b64 or ""))
    except Exception:
        return {"ok": False, "error": "пакет не в base64"}
    if not raw or len(raw) > MAX_BLOB:
        return {"ok": False, "error": "пакет пустой или больше 40 МБ"}
    try:
        data = json.loads(gzip.decompress(raw).decode("utf-8"))
        assert isinstance(data, dict)
    except Exception:
        return {"ok": False, "error": "пакет не gzip+JSON"}
    d = _dir(board)
    with _LOCK:
        os.makedirs(d, exist_ok=True)
        name = time.strftime("%Y%m%d-%H%M%S") + ".json.gz"
        with open(os.path.join(d, name), "wb") as f:
            f.write(raw)
        with open(os.path.join(d, "meta.json"), "w", encoding="utf-8") as f:
            json.dump({"board": board, "ts": time.time(), "files": sorted(data)[:200],
                       "kb": len(raw) >> 10}, f, ensure_ascii=False)
        olds = sorted(n for n in os.listdir(d) if n.endswith(".json.gz"))
        for n in olds[:-KEEP]:
            try:
                os.remove(os.path.join(d, n))
            except OSError:
                pass
    return {"ok": True, "kept": min(len(olds), KEEP), "files": len(data)}


def _online_boards(max_age=180):
    try:
        import boardlink
        st = boardlink.status()
        now = time.time()
        return {b.get("id") for b in st.get("boards") or []
                if isinstance(b, dict) and now - float(b.get("ts") or 0) < max_age}
    except Exception:
        return set()


def list_backups():
    out = []
    root = _dir()
    if not os.path.isdir(root):
        return out
    online = _online_boards()
    for b in sorted(os.listdir(root)):
        d = os.path.join(root, b)
        try:
            with open(os.path.join(d, "meta.json"), encoding="utf-8") as f:
                m = json.load(f)
        except Exception:
            continue
        m["copies"] = len([n for n in os.listdir(d) if n.endswith(".json.gz")])
        m["online"] = m.get("board") in online
        out.append(m)
    return sorted(out, key=lambda m: -float(m.get("ts") or 0))


def _latest_blob(board_dirname):
    d = os.path.join(_dir(), board_dirname)
    gz = sorted(n for n in os.listdir(d) if n.endswith(".json.gz")) if os.path.isdir(d) else []
    if not gz:
        return None
    with open(os.path.join(d, gz[-1]), "rb") as f:
        return f.read()


def restore_for(board, fresh=False):
    """Какую копию отдать плате. fresh — у платы нет памяти (новая установка)."""
    board = str(board or "")[:40]
    if store.settings().get("board_auto_restore", True) is False and fresh:
        return {"ok": False, "error": "автовосстановление выключено в облаке"}
    forced = (store.settings().get("board_restore_map") or {}).get(board)
    cands = list_backups()
    pick = None
    if forced:
        pick = next((m for m in cands if m.get("board") == forced), None)
    if pick is None:
        pick = next((m for m in cands if m.get("board") == board), None)
    if pick is None and fresh:
        pick = next((m for m in cands if not m.get("online") and m.get("board") != board), None)
    if pick is None:
        return {"ok": False, "error": "копий для этой платы нет"}
    blob = _latest_blob(re.sub(r"[^\w.\-]", "_", pick["board"])[:60])
    if not blob:
        return {"ok": False, "error": "копия пустая"}
    store.log("СЕЙФ", "плата %s восстанавливает память из копии «%s» (%s)" % (
        board, pick["board"], time.strftime("%d.%m %H:%M", time.localtime(pick.get("ts") or 0))))
    if forced:
        m = dict(store.settings().get("board_restore_map") or {})
        m.pop(board, None)
        store.update_settings({"board_restore_map": m})
    return {"ok": True, "from": pick["board"], "ts": pick.get("ts"),
            "blob": base64.b64encode(blob).decode("ascii")}


def force_restore(to_board, from_board):
    """Владелец: «плата to заберёт копию from» при следующей просьбе."""
    m = dict(store.settings().get("board_restore_map") or {})
    m[str(to_board)[:40]] = str(from_board)[:40]
    store.update_settings({"board_restore_map": m})
    return {"ok": True}


# ------------------------------------------------------------ подключение
def pair_request(board, code, ip, ver):
    board, code = str(board or "")[:40], re.sub(r"\D", "", str(code or ""))[:6]
    if not board or len(code) < 4:
        return {"ok": False, "error": "нужны board и код из 4–6 цифр"}
    now = time.time()
    with _LOCK:
        for k in [k for k, v in _PAIRS.items() if now - v["ts"] > 900]:
            _PAIRS.pop(k, None)
        p = _PAIRS.get((board, code))
        if p is None:
            if len(_PAIRS) >= 20:
                return {"ok": False, "error": "слишком много запросов"}
            p = _PAIRS[(board, code)] = {"board": board, "code": code, "ip": str(ip or "")[:40],
                                         "ver": str(ver or "")[:20], "ts": now, "state": "ждёт"}
            store.log("ПЛАТЫ", "плата %s (%s) просит подключиться, код %s" % (board, ip, code))
        p["ts"] = now
        if p["state"] == "подключена":
            return {"ok": True, "token": store.token(), "approved": True}
        if p["state"] == "отклонена":
            return {"ok": False, "denied": True, "error": "владелец отклонил"}
    return {"ok": True, "wait": True}


def pairs():
    with _LOCK:
        now = time.time()
        return [dict(v, age=int(now - v["ts"])) for v in _PAIRS.values() if v["state"] == "ждёт"]


def pair_decide(board, code, approve):
    with _LOCK:
        p = _PAIRS.get((str(board)[:40], str(code)[:6]))
        if not p:
            return {"ok": False, "error": "запрос устарел"}
        p["state"] = "подключена" if approve else "отклонена"
    store.log("ПЛАТЫ", "плата %s: %s владельцем" % (board, "подключена" if approve else "отклонена"))
    return {"ok": True}
