# -*- coding: utf-8 -*-
"""board_src.py — исходники платы: хранение, обновление, откат.

Папка data/board_src/ — рабочие файлы.
Папка data/board_src_bak/ — автоматические копии перед записью.
ИИ может читать, писать и восстанавливать через API и мысли учителя.
"""
import os
import shutil
import time
import store

_DIR = "board_src"
_BAK = "board_src_bak"


def _root():
    p = store.p(_DIR)
    os.makedirs(p, exist_ok=True)
    return p


def _bak():
    p = store.p(_BAK)
    os.makedirs(p, exist_ok=True)
    return p


def list_files():
    """Список файлов в board_src."""
    out = []
    root = _root()
    for fn in sorted(os.listdir(root)):
        fp = os.path.join(root, fn)
        if os.path.isfile(fp):
            out.append({"name": fn, "size": os.path.getsize(fp),
                        "ts": os.path.getmtime(fp)})
    return out


def read(name):
    """Прочитать файл. Возврат: {ok, text} или ошибка."""
    name = os.path.basename(str(name or ""))
    fp = os.path.join(_root(), name)
    if not os.path.isfile(fp):
        return {"ok": False, "error": "нет файла %s" % name}
    try:
        with open(fp, "r", encoding="utf-8", errors="replace") as f:
            return {"ok": True, "name": name, "text": f.read()}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


def write(name, text, backup=True):
    """Записать файл. Перед записью — копия в board_src_bak."""
    name = os.path.basename(str(name or ""))
    if not name or ".." in name:
        return {"ok": False, "error": "недопустимое имя"}
    root = _root()
    fp = os.path.join(root, name)
    if backup and os.path.isfile(fp):
        bak_name = "%s.%d.bak" % (name, int(time.time()))
        try:
            shutil.copy2(fp, os.path.join(_bak(), bak_name))
        except Exception:
            pass
    try:
        with open(fp, "w", encoding="utf-8") as f:
            f.write(str(text or ""))
        return {"ok": True, "name": name, "size": len(text or "")}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


def restore(name=None):
    """Восстановить из последней копии. name=None — все файлы."""
    bak = _bak()
    root = _root()
    restored = []
    files = [name] if name else [f for f in os.listdir(bak)
                                 if f.endswith(".bak")]
    # для каждого имени берём самую свежую копию
    latest = {}
    for fn in os.listdir(bak):
        if not fn.endswith(".bak"):
            continue
        base = fn.rsplit(".", 2)[0]
        if name and base != name:
            continue
        ts = os.path.getmtime(os.path.join(bak, fn))
        if base not in latest or ts > latest[base][0]:
            latest[base] = (ts, fn)
    for base, (_, fn) in latest.items():
        try:
            shutil.copy2(os.path.join(bak, fn), os.path.join(root, base))
            restored.append(base)
        except Exception:
            pass
    return {"ok": True, "restored": restored}


def push_to_board(name):
    """Отправить файл на плату через word_push / cloud_buf (если мост жив).
    Возврат: {ok, sent} или ошибка."""
    r = read(name)
    if not r.get("ok"):
        return r
    # пока просто сохраняем — реальная отправка через bridge
    # будет добавлена, когда мост подтвердит приём
    return {"ok": True, "name": name, "note": "файл сохранён в board_src, "
            "отправка на плату — через мост (word_push/cloud_buf)"}
