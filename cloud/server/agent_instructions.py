# -*- coding: utf-8 -*-
"""instructions.py — v3.219: БИБЛИОТЕКА ИНСТРУКЦИЙ АГЕНТА И ОБУЧЕНИЯ.

Инструкция = «как сделать X»: пошаговый план с инструментами агента, проверками
и откатом. Лежит в JSON (cloud/server/instructions/*.json; свои — в
data/instructions/*.json, они важнее встроенных с тем же id).

Куда идёт каждая инструкция:
  1) в БАЗУ ЗНАНИЙ как урок «Администрирование: Инструкция — …»: оттуда её
     достаёт Star Gate / планировщик агента (brainplan) и выполняет по шагам;
     при записи в KB текст попадает и в сеть материалов мозга (brain.mn_learn);
  2) в ОБУЧЕНИЕ своей LLM (поток «admin»): урок + пример в протоколе агента.
     Так LLM можно учить без мозга-12 (отдельный корпус: corpus_text());
  3) в справочник по API: GET /api/instructions.

Формат (поля, * — обязательные):
  id*, title*, ask* [как просят], target* (board|linux|windows|cloud|any),
  risk (read|change|danger), slots {имя: regex с группой}, steps* [...],
  verify [..], rollback, notes, verified (проверено на железе? по умолчанию false)
  шаг: {"tool": run|check|wake|write|code|wait|note|cloud|restart_cloud|shot|vision|list|desk,
        "command"|"op"|"seconds"|"text", "note", "expect" (regex), "stop_on_fail"}
"""
import hashlib
import json
import os
import re
import threading

import store

_BUNDLED = os.path.join(os.path.dirname(os.path.abspath(__file__)), "instructions")
TOOLS = {"run", "check", "wake", "write", "code", "wait", "note", "cloud", "restart_cloud",
         "shot", "vision", "list", "desk"}
TARGETS = {"board", "linux", "windows", "cloud", "any"}
RISKS = {"read", "change", "danger"}
_LOCK = threading.RLock()
_ST = {"loaded": 0, "invalid": [], "seeded": 0, "ts": 0.0}


def validate(i):
    """-> список ошибок (пусто = инструкция годна)."""
    err = []
    if not isinstance(i, dict):
        return ["не объект"]
    if not re.fullmatch(r"[a-z0-9._-]{3,60}", str(i.get("id") or "")):
        err.append("id: латиница/цифры/._-, 3–60 знаков")
    if not str(i.get("title") or "").strip():
        err.append("нет title")
    if not (isinstance(i.get("ask"), list) and i["ask"] and all(isinstance(x, str) and x.strip() for x in i["ask"])):
        err.append("ask: нужен список фраз")
    if i.get("target") not in TARGETS:
        err.append("target: один из %s" % sorted(TARGETS))
    if i.get("risk", "read") not in RISKS:
        err.append("risk: один из %s" % sorted(RISKS))
    st = i.get("steps")
    if not (isinstance(st, list) and st):
        err.append("нет steps")
    else:
        for n, s in enumerate(st, 1):
            if not isinstance(s, dict) or s.get("tool") not in TOOLS:
                err.append("шаг %d: tool должен быть из %s" % (n, sorted(TOOLS)))
                continue
            if s["tool"] == "run" and not str(s.get("command") or "").strip():
                err.append("шаг %d: пустая command" % n)
            if s.get("expect"):
                try:
                    re.compile(s["expect"])
                except re.error:
                    err.append("шаг %d: expect — неверная регулярка" % n)
    for n, rx in (i.get("slots") or {}).items():
        try:
            re.compile(rx)
        except re.error:
            err.append("слот %s: неверная регулярка" % n)
    used = set()
    for s in (st or []):
        if isinstance(s, dict):
            used |= set(re.findall(r"\{(\w+)\}", str(s.get("command") or "")))
    for n in used - set((i.get("slots") or {}).keys()):
        err.append("слот {%s} не описан в slots" % n)
    return err


def _read_dir(path):
    out = []
    if not os.path.isdir(path):
        return out
    for fn in sorted(os.listdir(path)):
        if not fn.endswith(".json"):
            continue
        try:
            with open(os.path.join(path, fn), "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            _ST["invalid"].append("%s: %s" % (fn, str(e)[:100]))
            continue
        for i in (data if isinstance(data, list) else [data]):
            e = validate(i)
            if e:
                _ST["invalid"].append("%s/%s: %s" % (fn, (i or {}).get("id") if isinstance(i, dict) else "?", "; ".join(e)))
            else:
                out.append(i)
    return out


def user_dir():
    return store.p("instructions")


def load_all():
    """Встроенные + пользовательские (пользовательские перекрывают по id)."""
    with _LOCK:
        _ST["invalid"] = []
        by = {}
        for i in _read_dir(_BUNDLED) + _read_dir(user_dir()):
            by[i["id"]] = i
        _ST["loaded"] = len(by)
        return list(by.values())


def lesson_text(i):
    risk = i.get("risk", "read")
    lines = ["Инструкция: %s." % i["title"],
             "Задача владельца: %s." % "; ".join(i["ask"]),
             "Цель: %s. Риск: %s." % (i["target"], risk)]
    if i.get("slots"):
        lines.append("Слоты: " + json.dumps(i["slots"], ensure_ascii=False))
    for s in i["steps"]:
        lines.append("Шаг: " + json.dumps({k: v for k, v in s.items() if v not in (None, "")}, ensure_ascii=False))
    if i.get("verify"):
        lines.append("Проверка: " + "; ".join(i["verify"]))
    if i.get("rollback"):
        lines.append("Откат: " + str(i["rollback"]))
    if i.get("notes"):
        lines.append("Примечание: " + str(i["notes"]))
    if not i.get("verified"):
        lines.append("Статус: составлено по документации и репозиторию, на железе не проверено.")
    return "\n".join(lines)


def _did(i):
    return "instr_" + hashlib.md5(i["id"].encode("utf-8")).hexdigest()[:12]


def seed_kb(items=None):
    """Записать инструкции в базу знаний (стабильные id: повтор не дублирует)."""
    import kb
    items = items if items is not None else load_all()
    n = 0
    for i in items:
        r = kb.add_article("Администрирование: Инструкция — " + i["title"], lesson_text(i),
                           source="instructions", did=_did(i))
        if r.get("ok"):
            n += 1
    ref = api_reference_text()
    if ref:
        kb.add_article("Администрирование: Справочник API платформы", ref, source="instructions", did="instr_api_ref")
    _ST["seeded"] = n
    import time
    _ST["ts"] = time.time()
    return n


def api_reference_text():
    """Справочник API платформы (из исходников: tools/build_instructions.py) — знание, не действие."""
    try:
        with open(os.path.join(os.path.dirname(_BUNDLED), "platform_api.json"), encoding="utf-8") as f:
            ref = json.load(f)
    except Exception:
        return ""
    rows = ["Справочник API облака Star Gate (только чтение, для понимания платформы):"]
    for r in ref:
        rows.append("%s %s%s — %s" % (r["method"], r["path"], " (нужен вход)" if r.get("auth") else "", r.get("doc") or r["handler"]))
    return "\n".join(rows)


def example_text(i):
    """Пример в протоколе агента для обучения своей LLM (без выдуманных выводов)."""
    lines = ["Задача владельца: %s" % i["ask"][0]]
    for s in i["steps"]:
        lines.append("Шаг: " + json.dumps({k: v for k, v in s.items() if k in ("tool", "command", "op", "seconds", "text", "server")},
                                          ensure_ascii=False))
        if s.get("expect"):
            lines.append("Ожидается в выводе: %s" % s["expect"])
    lines.append("Итог: %s" % (i.get("verify") or ["шаги выполнены"])[0])
    return "\n".join(lines)


def corpus(items=None):
    """[(подпись, текст)] для потока обучения «admin» своей LLM."""
    items = items if items is not None else load_all()
    out = []
    for i in items:
        t = lesson_text(i)
        out.append(("ins-l-" + hashlib.md5(t.encode("utf-8")).hexdigest()[:14], "Урок администрирования: " + t))
        for ph in i["ask"][1:3]:                   # те же шаги под другие формулировки просьбы
            t2 = example_text(dict(i, ask=[ph]))
            out.append(("ins-e-" + hashlib.md5(t2.encode("utf-8")).hexdigest()[:14], t2))
        out.append(("ins-x-" + hashlib.md5(i["id"].encode()).hexdigest()[:14], example_text(i)))
    return out


def corpus_text(items=None):
    return "\n\n".join(t for _s, t in corpus(items))


def status():
    items = load_all()
    by_t = {}
    for i in items:
        by_t[i["target"]] = by_t.get(i["target"], 0) + 1
    return {"count": len(items), "by_target": by_t, "invalid": list(_ST["invalid"])[:30],
            "seeded": _ST["seeded"], "user_dir": user_dir(),
            "verified": sum(1 for i in items if i.get("verified"))}


def import_items(items, replace_ok=True):
    """Принять инструкции из JSON (панель/API): сохранить в data/instructions/ и посеять."""
    good, bad = [], []
    for i in (items if isinstance(items, list) else [items]):
        e = validate(i)
        (bad if e else good).append((i, e))
    if good:
        os.makedirs(user_dir(), exist_ok=True)
        path = os.path.join(user_dir(), "imported.json")
        cur = []
        try:
            with open(path, "r", encoding="utf-8") as f:
                cur = json.load(f)
        except Exception:
            cur = []
        ids = {i["id"] for i, _e in good}
        cur = [c for c in cur if c.get("id") not in ids] + [i for i, _e in good]
        with open(path, "w", encoding="utf-8") as f:
            json.dump(cur, f, ensure_ascii=False, indent=1)
        seed_kb()
    return {"ok": bool(good), "added": len(good),
            "invalid": [{"id": (i or {}).get("id") if isinstance(i, dict) else "?", "errors": e} for i, e in bad]}
