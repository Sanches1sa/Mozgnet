# -*- coding: utf-8 -*-
"""memory_save.py — явное сохранение памяти облака и своей LLM в JSON.

Облако: снимок ключевых частей памяти (мысли, заметки, краткие чаты,
агенты, SSH, KB-заголовки) в data/cloud_memory.json.

LLM: мета-состояние, список обученных материалов, последние генерации
в data/llm_memory.json.

Вызывается при старте (восстановление), периодически и по API.
"""
import json
import os
import time
import threading
import store

_CLOUD_PATH = "cloud_memory.json"
_LLM_PATH = "llm_memory.json"
_LOCK = threading.RLock()
_LAST_SAVE = [0.0]


def _safe_load(path, default):
    try:
        return store.load_json(path, default) or default
    except Exception:
        return default


def cloud_snapshot():
    """Собрать текущую память облака."""
    snap = {
        "ts": time.time(),
        "version": store.version(),
        "thoughts": [],
        "notes": [],
        "recent_chats": [],
        "ssh_servers": [],
        "ssh_log_tail": [],
        "kb_titles": [],
        "curiosity": {},
        "settings_flags": {},
    }
    try:
        thoughts = store.load_json("thoughts.json", []) or []
        snap["thoughts"] = thoughts[-40:]  # последние
    except Exception:
        pass
    try:
        notes = store.notes() or []
        snap["notes"] = notes[-30:]
    except Exception:
        pass
    try:
        # краткие названия чатов
        chats = store.chat_list() or []
        snap["recent_chats"] = [{"id": c.get("id"), "title": c.get("title"),
                                 "ts": c.get("ts")} for c in chats[-20:]]
    except Exception:
        pass
    try:
        import ssh_servers
        snap["ssh_servers"] = ssh_servers.list_servers()
        log = store.load_json("ssh_log.json", []) or []
        snap["ssh_log_tail"] = log[-15:]
    except Exception:
        pass
    try:
        import kb
        # только заголовки, чтобы не раздувать
        arts = getattr(kb, "list_titles", lambda: [])() or []
        snap["kb_titles"] = arts[:100]
    except Exception:
        try:
            # fallback
            snap["kb_titles"] = ["(kb titles unavailable)"]
        except Exception:
            pass
    try:
        import curiosity
        snap["curiosity"] = curiosity.status() if hasattr(curiosity, "status") else {}
    except Exception:
        pass
    try:
        s = store.settings()
        snap["settings_flags"] = {
            k: s.get(k) for k in ("curiosity_on", "ssh_on", "web_on", "llm_link_on", "terminal_on")
            if k in s
        }
    except Exception:
        pass
    return snap


def llm_snapshot():
    """Собрать память своей LLM."""
    snap = {
        "ts": time.time(),
        "trained": {},
        "meta": {},
        "recent_generations": [],
        "corpus_info": {},
    }
    try:
        import tinyllm
        # состояние обучения
        st = store.load_json("llmtrained.json", {}) or {}
        snap["trained"] = {k: v for k, v in list(st.items())[:50]}  # ограничить
        # если модель загружена
        try:
            # v3.175.1: НЕ грузить модель ради снимка (это сотни МБ и
            # минуты) — только если она уже загружена
            m = getattr(tinyllm, "_M", None)
            if m:
                snap["meta"] = {
                    "steps": getattr(m, "steps", 0),
                    "loss": getattr(m, "loss", None),
                    "c": getattr(m, "c", {}),
                }
                # последние слова/токены если есть
                if hasattr(m, "tok") and hasattr(m.tok, "words"):
                    snap["meta"]["vocab_sample"] = list(m.tok.words.keys())[:30]
        except Exception:
            pass
    except Exception:
        pass
    try:
        # последние генерации если логируются
        gen_log = store.load_json("llm_gen_log.json", []) or []
        snap["recent_generations"] = gen_log[-20:]
    except Exception:
        pass
    return snap


def save_cloud():
    with _LOCK:
        snap = cloud_snapshot()
        store.save_json(_CLOUD_PATH, snap)
        _LAST_SAVE[0] = time.time()
        return {"ok": True, "path": _CLOUD_PATH, "ts": snap["ts"],
                "thoughts": len(snap["thoughts"]), "notes": len(snap["notes"])}


def save_llm():
    with _LOCK:
        snap = llm_snapshot()
        store.save_json(_LLM_PATH, snap)
        return {"ok": True, "path": _LLM_PATH, "ts": snap["ts"]}


def save_all():
    c = save_cloud()
    l = save_llm()
    return {"ok": True, "cloud": c, "llm": l}


def load_cloud():
    return _safe_load(_CLOUD_PATH, {})


def load_llm():
    return _safe_load(_LLM_PATH, {})


def status():
    return {
        "last_save": _LAST_SAVE[0],
        "cloud_exists": os.path.isfile(store.p(_CLOUD_PATH)),
        "llm_exists": os.path.isfile(store.p(_LLM_PATH)),
        "cloud_size": os.path.getsize(store.p(_CLOUD_PATH)) if os.path.isfile(store.p(_CLOUD_PATH)) else 0,
        "llm_size": os.path.getsize(store.p(_LLM_PATH)) if os.path.isfile(store.p(_LLM_PATH)) else 0,
    }


def background_saver(interval=300):
    """Фоновый поток: сохранять каждые interval секунд."""
    def _loop():
        while True:
            time.sleep(interval)
            try:
                save_all()
            except Exception:
                pass
    t = threading.Thread(target=_loop, daemon=True, name="memory-saver")
    t.start()
    return t
