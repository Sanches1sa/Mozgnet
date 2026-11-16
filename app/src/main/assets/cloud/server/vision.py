# -*- coding: utf-8 -*-
# ===========================================================================
# vision.py — ЗРЕНИЕ ОБЛАКА: кадры с плат ESP32-CAM (только stdlib)
# ---------------------------------------------------------------------------
# Мозг neuro_12 шлёт кадры камеры:
#   POST /api/vision/frame {img: b64(jpeg), cam, conf}  — с надеждой на YOLO
#   POST /api/vision/raw   {img: b64(jpeg), cam}        — сырой кадр в стрим
# и ВЫКАЧИВАЕТ буфер детекций: GET /api/vision/buffer?limit=3&cam=X ->
#   {ok, last: {names: [...], ts}, items: [...]}
# YOLO опционален (NEURO_YOLO=1 + совместимый модуль) — без него честно
# возвращаем detections: [] (мозг переключается на raw+буфер). Кадры хранятся
# кольцом на диск (data/vision/<cam>/), страница /cam показывает последние.
# ===========================================================================
import base64
import json
import os
import re
import threading
import time

import store

_LOCK = threading.RLock()
_HIST_MAX = 24
_BUF_MAX = 200


def _cam_dir(cam):
    safe = "".join(c for c in str(cam or "cam") if c.isalnum() or c in "-_")[:40]
    d = store.p("vision", safe)
    os.makedirs(d, exist_ok=True)
    return d, safe


def _decode_b64(img_b64):
    raw = re.sub(r"^data:[^;,]*;base64,", "", str(img_b64 or ""))
    try:
        return base64.b64decode(raw, validate=False)
    except Exception:
        return b""


def _looks_jpeg(data):
    return data[:2] == b"\xff\xd8"


def _yolo(data, conf=0.35):
    """Опциональный YOLO: модуль yolo_core.run(data, conf) -> [(name, conf)]"""
    if os.environ.get("NEURO_YOLO") != "1":
        return []
    try:
        import yolo_core                     # опциональный модуль владельца
        return [(str(n), float(c)) for n, c in
                (yolo_core.run(data, conf) or [])][:8]
    except Exception:
        return []


def _complete(data):
    """v3.130: целый ли JPEG (есть маркер конца FFD9). Оборванный кадр
    браузер не покажет — его не кладём вместо последнего хорошего."""
    return _looks_jpeg(data) and b"\xff\xd9" in data[-2048:]


def _bad(cam, why):
    try:
        d, safe = _cam_dir(cam)
        st = store.load_json(os.path.join("vision", safe, "status.json"), {}) or {}
        st.update(bad=int(st.get("bad") or 0) + 1, bad_ts=time.time(), why=str(why)[:160])
        store.save_json(os.path.join("vision", safe, "status.json"), st)
    except Exception:
        pass


def frame(img_b64, cam="cam", conf=0.35, board=""):
    """Кадр на распознавание. Возврат: {ok, detections:[{name,conf}]}"""
    data = _decode_b64(img_b64)
    if not _looks_jpeg(data):
        _bad(cam, "пришёл не JPEG (%d байт)" % len(data))
        return {"ok": False, "error": "ожидался JPEG (base64)"}
    if not _complete(data):
        _bad(cam, "оборванный JPEG (%d КБ без конца кадра)" % (len(data) // 1024))
        return {"ok": False, "error": "оборванный JPEG — кадр не сохранён"}
    det = _yolo(data, conf)
    names = [n for n, _c in det]
    _save_frame(cam, data, names, src="yolo" if det else "frame",
                board=board)
    return {"ok": True, "detections": [{"name": n, "conf": round(c, 3)}
                                       for n, c in det]}


def raw(img_b64, cam="cam", board=""):
    """Сырой кадр в стрим (без YOLO)."""
    data = _decode_b64(img_b64)
    if not _looks_jpeg(data):
        _bad(cam, "пришёл не JPEG (%d байт)" % len(data))
        return {"ok": False, "error": "ожидался JPEG (base64)"}
    if not _complete(data):
        _bad(cam, "оборванный JPEG (%d КБ без конца кадра)" % (len(data) // 1024))
        return {"ok": False, "error": "оборванный JPEG — кадр не сохранён"}
    _save_frame(cam, data, [], src="raw", board=board)
    return {"ok": True}


def _save_frame(cam, data, names, src="raw", board=""):
    with _LOCK:
        d, safe = _cam_dir(cam)
        with open(os.path.join(d, "last.jpg"), "wb") as f:
            f.write(data)
        item = {"cam": safe, "names": [str(n)[:24] for n in names],
                "src": src, "board": str(board)[:40], "ts": time.time()}
        buf = store.load_json(os.path.join("vision", "buffer.json"), []) or []
        buf.append(item)
        store.save_json(os.path.join("vision", "buffer.json"), buf[-_BUF_MAX:])
        hist = store.load_json(os.path.join("vision", safe, "hist.json"),
                               []) or []
        hist.append(item)
        store.save_json(os.path.join("vision", safe, "hist.json"),
                        hist[-_HIST_MAX:])


def last_jpeg(cam="cam"):
    d, _s = _cam_dir(cam)
    try:
        with open(os.path.join(d, "last.jpg"), "rb") as f:
            return f.read()
    except Exception:
        return b""


def buffer(limit=3, cam=""):
    """Буфер детекций (мозг выкачивает: last.names -> нейронам)."""
    with _LOCK:
        buf = store.load_json(os.path.join("vision", "buffer.json"), []) or []
    if cam:
        buf = [b for b in buf if b.get("cam") == str(cam)[:40]]
    items = buf[-int(limit):][::-1]
    last = items[0] if items else None
    return {"ok": True, "last": last, "items": items}


def cams():
    """Список камер с активностью — для страницы /cam."""
    out = []
    try:
        root = store.p("vision")
        for name in os.listdir(root):
            d = os.path.join(root, name)
            if not os.path.isdir(d) or not os.path.exists(
                    os.path.join(d, "last.jpg")):
                continue
            hist = store.load_json(os.path.join("vision", name, "hist.json"),
                                   []) or []
            st = store.load_json(os.path.join("vision", name, "status.json"), {}) or {}
            out.append({"cam": name, "ts": hist[-1]["ts"] if hist else 0,
                        "names": (hist[-1].get("names") or []) if hist else [],
                        "bytes": os.path.getsize(os.path.join(d, "last.jpg")),
                        "bad": st.get("bad") or 0, "bad_ts": st.get("bad_ts"),
                        "bad_why": st.get("why") or ""})
    except Exception:
        pass
    # v3.130: что говорят платы о своей камере (почему кадров нет)
    try:
        import boardlink
        have = {c["cam"] for c in out}
        for b in boardlink.status().get("boards") or []:
            cs = b.get("cam")
            if not cs:
                continue
            safe = "".join(ch for ch in str(b["id"]) if ch.isalnum() or ch in "-_")[:40]
            row = next((c for c in out if c["cam"] == safe), None)
            if row is None:
                row = {"cam": safe, "ts": 0, "names": [], "nofile": True}
                out.append(row)
            row["board"] = dict(cs, link_age=b.get("age"))
    except Exception:
        pass
    out.sort(key=lambda x: -x.get("ts", 0))
    return out
