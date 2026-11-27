# -*- coding: utf-8 -*-
"""llm_export.py — v3.237: СКАЧАТЬ СВОЮ LLM И ЗАГРУЗИТЬ ЕЁ НА ПЛАТУ.

Пакет своей LLM (zip):
    model.json   — формат neuro12-llm-1: размеры, словарь BPE, слияния, таблица тензоров;
    weights.f32  — веса float32 подряд;
    board_llm.py — запуск без облака (numpy или чистый Python);
    README.txt   — как запустить.
Собирается из сохранённого на диск состояния своей LLM (tinyllm_np.npz/.json),
то есть из последней сохранённой точки обучения.

«Загрузить LLM на плату»: облако ставит пакет в очередь для плат (llm_push);
плата видит это в ответе /api/brain/d6, скачивает /api/llm/bundle со своим
токеном, распаковывает и запускает board_llm.py у себя. Состояние плата
сообщает в /api/llm/board_state. Плата НЕ учит сеть — только генерирует."""
import hashlib
import io
import json
import os
import threading
import time
import zipfile

import store

FORMAT = "neuro12-llm-1"
_LOCK = threading.Lock()
_ZIP = "llm_bundle.zip"
_BOARDS = "llm_boards.json"


def _state_base():
    import tinyllm
    return tinyllm._state_path()


def available():
    b = _state_base()
    return os.path.isfile(b + ".npz") and os.path.isfile(b + ".json")


def _src_sig():
    b = _state_base()
    try:
        st = os.stat(b + ".npz")
        return hashlib.md5(("%d:%d" % (st.st_size, int(st.st_mtime))).encode()).hexdigest()[:12]
    except OSError:
        return ""


def build(force=False):
    """-> {ok, path, sig, bytes, info} | {ok: False, error}."""
    if not available():
        return {"ok": False, "error": "своя LLM ещё не сохранена на диск — дай ей поучиться (⚙ → «Своя LLM»)"}
    try:
        import numpy as np
    except Exception:
        return {"ok": False, "error": "в облаке нет numpy — своя LLM не собрана"}
    sig = _src_sig()
    out = store.p(_ZIP)
    with _LOCK:
        meta_old = store.load_json("llm_bundle_meta.json", {}) or {}
        if not force and meta_old.get("src") == sig and os.path.isfile(out):
            return dict(meta_old, ok=True, path=out)
        b = _state_base()
        with open(b + ".json", "r", encoding="utf-8") as f:
            st = json.load(f)
        z = np.load(b + ".npz")
        names = sorted(z.files, key=lambda n: (n not in ("E", "P", "lnf_g", "lnf_b"), n))
        buf = io.BytesIO()
        tensors, off = [], 0
        for n in names:
            a = np.ascontiguousarray(z[n], dtype="<f4")
            tensors.append([n, list(a.shape), off, int(a.size)])
            buf.write(a.tobytes())
            off += int(a.size)
        c = st.get("c") or {}
        model = {"format": FORMAT, "c": {k: c.get(k) for k in ("llm_d", "llm_layers", "llm_heads", "llm_ctx")},
                 "vocab": st.get("vocab") or {}, "merges": st.get("merges") or [],
                 "steps": st.get("steps"), "loss": st.get("loss"), "tensors": tensors,
                 "sig": sig, "built": time.time(),
                 "note": "своя LLM облака Star Gate; генерация без обучения"}
        here = os.path.dirname(os.path.abspath(__file__))
        tmp = out + ".tmp"
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("model.json", json.dumps(model, ensure_ascii=False))
            zf.writestr("weights.f32", buf.getvalue())
            zf.write(os.path.join(here, "board_llm.py"), "board_llm.py")
            zf.writestr("README.txt", (
                "Своя LLM Мозга-12 (пакет %s)\n"
                "Размер сети: d=%s, слоёв %s, голов %s, окно %s токенов, словарь %d, шагов обучения %s.\n\n"
                "Запуск на любом компьютере или плате (Python 3):\n"
                "  unzip llm_bundle.zip -d mozg_llm\n"
                "  python3 mozg_llm/board_llm.py mozg_llm \"привет мозг\"\n"
                "С numpy — быстро, без numpy — на чистом Python (медленно).\n"
                "Это только генерация: учится LLM в облаке.\n") % (
                sig, c.get("llm_d"), c.get("llm_layers"), c.get("llm_heads"), c.get("llm_ctx"),
                len(model["vocab"]), st.get("steps")))
        try:
            os.replace(tmp, out)
        except PermissionError:
            time.sleep(0.5)
            os.replace(tmp, out)
        info = {"src": sig, "sig": sig, "bytes": os.path.getsize(out), "params": off,
                "d": c.get("llm_d"), "layers": c.get("llm_layers"), "vocab": len(model["vocab"]),
                "steps": st.get("steps"), "built": time.time()}
        store.save_json("llm_bundle_meta.json", info)
        return dict(info, ok=True, path=out)


def push(boards="*"):
    """Поставить пакет в очередь на платы. boards: '*' или список id."""
    r = build()
    if not r.get("ok"):
        return r
    tg = "*" if boards in ("*", "", None) else [str(b)[:40] for b in (boards if isinstance(boards, list) else [boards])]
    store.update_settings({"llm_push": {"sig": r["sig"], "boards": tg, "ts": time.time(), "bytes": r["bytes"]}})
    n = len(store.boards()) if tg == "*" else len(tg)
    store.log("LLM", "своя LLM (%s, %d КБ) поставлена на загрузку на платы: %s" % (r["sig"], r["bytes"] // 1024,
                                                                                    "все" if tg == "*" else ", ".join(tg)))
    return {"ok": True, "sig": r["sig"], "bytes": r["bytes"], "boards": tg, "count": n,
            "text": "Пакет %d КБ поставлен в очередь: платы заберут его при следующем опросе мозг-почты." % (r["bytes"] // 1024)}


def push_for(board):
    """Что сказать плате в /api/brain/d6: {sig, bytes} | None."""
    pu = store.settings().get("llm_push") or {}
    if not pu.get("sig"):
        return None
    tg = pu.get("boards")
    if tg != "*" and str(board) not in (tg or []):
        return None
    st = (store.load_json(_BOARDS, {}) or {}).get(str(board)) or {}
    if st.get("sig") == pu["sig"] and st.get("ok"):
        return None                          # уже загружена
    return {"sig": pu["sig"], "bytes": pu.get("bytes"), "path": "/api/llm/bundle"}


def board_state(board, data):
    with _LOCK:
        d = store.load_json(_BOARDS, {}) or {}
        d[str(board)[:40]] = {"sig": str(data.get("sig") or "")[:20], "ok": bool(data.get("ok")),
                              "info": data.get("info") if isinstance(data.get("info"), dict) else {},
                              "error": str(data.get("error") or "")[:300],
                              "sample": str(data.get("sample") or "")[:300], "ts": time.time()}
        store.save_json(_BOARDS, d)
    return {"ok": True}


def status():
    meta = store.load_json("llm_bundle_meta.json", {}) or {}
    return {"ok": True, "available": available(), "bundle": meta, "push": store.settings().get("llm_push") or {},
            "boards": store.load_json(_BOARDS, {}) or {}, "download": "/api/llm/download"}
