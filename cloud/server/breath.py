# -*- coding: utf-8 -*-
"""
breath.py — v3.132: ОБЩЕЕ ДЫХАНИЕ ВСЕХ ИИ-АЛГОРИТМОВ, синхронное с платами.

Дыхание — как у мозга платы (тензорная сеть, v2.51): s(t) = sin(2π·φ),
φ — фаза 0..1. Вдох (s > 0) — фаза ПРИЁМА: учёба, чтение, поиск, перестройка
моделей; выдох (s < 0) — фаза ВЫДАЧИ: алгоритмы сдержаннее, фоновая учёба
ждёт следующего вдоха.

ИСТОЧНИК ОДИН — ТЕНЗОРНАЯ СЕТЬ ПЛАТЫ (v3.133):
  • плата v2.75 присылает дыхание своей тензорной сети: период вдоха-выдоха
    T = 4 + 8·e и глубину d = 1 − 0.65·e (e — неуверенность сети в зоне
    последней мысли), и фазу;
  • общее дыхание = среднее периодов и глубин тензорных сетей плат, фаза —
    круговое среднее их фаз; облако отдаёт его обратно — все платы дышат
    в одной фазе и с одним периодом;
  • сила дыхания s = d·sin(2πφ): уверенная сеть — глубокое дыхание всех
    алгоритмов, неуверенная — мелкое;
  • плат нет — последнее дыхание тензорной сети (до часа), потом покой 8 с.

Каждый алгоритм вызывает mod()/inhale() со своим именем — счётчики в
status() показывают, кто реально дышит, а кто нет (и почему).
"""
import math
import threading
import time

_LOCK = threading.Lock()
_ST = {"t0": time.time(), "period": 8.0, "depth": 0.6,
       "src": "тензорная сеть платы не на связи — покой 8 с", "hz": None,
       "boards": {}, "ts": 0.0, "last_tn": None}

# Реестр ИИ-алгоритмов облака: (имя, что дышит). Порядок — для панели.
ALGOS = [
    ("Агент Контур", "воля зон Кирпичики/Помощь: бюджет действий в пределах лимита задачи", False, "Кирпичики"),
    ("Своя LLM: код", "воля Кирпичиков: кандидаты, длина и температура; веса стабильны", False, "Кирпичики"),
    ("Курс LLM", "воля Обучения: шаг оптимизатора ×0,9…1,1; лимиты и пропорция источников прежние", False, "Обучение"),
    ("Своя LLM", "генерация: температура ±15 %; учёба: шаг обучения ±10 %", False, "Буквы"),
    ("Сеть материалов", "внимание: дальность разрядки активации ±12 %", False, "Знания"),
    ("Речь из словаря", "подбор слова общего словаря: вдох — шире, выдох — увереннее", False, "Буквы"),
    ("Модель «частота ↔ слова»", "учёба: вес новой пары ±15 %", False, "Сигнал"),
    ("Опора на частоту", "сила опоры нейросетей на сигнал плат ±10 %", False, "Сигнал"),
    ("Судья связности", "перестройка модели связности — только на вдохе", False, "Обучение"),
    ("Любопытство", "чтение новых тем — только на вдохе", False, "Любопытство"),
    ("Смелость предположений", "смелость догадки ±10 %", False, "Вопросы"),
    ("Связь LLM с мозгом", "фоновая учёба и пересказ мыслей плат — на вдохе", False, "Облако"),
    ("Своя LLM: интернет", "v3.169: LLM сама читает Википедию/Рувики/Confluence — на вдохе", False, "Любопытство"),
    ("Своя LLM: РОК-12", "v3.169: LLM — второй игрок в РОК-12, тактика по силе зоны", False, "Помощь"),
    ("Понимание вопроса", "перепроверка точности своей LLM — на вдохе", False, "Вопросы"),
    ("Учёба коду (GitHub)", "чтение репозиториев — на вдохе", False, "Кирпичики"),
    ("Мысли плат", "впитывание мыслей плат — на вдохе", False, "Память"),
    ("Синхронизация частот плат", "общий ритм генераторов; фаза дыхания — общая", False, "Сигнал"),
    ("ИИ монстров «РОК-12»", "агрессия: пауза между атаками ±20 %", False, "Юмор"),
    ("Разговор (мозг + LLM)", "длина реплики своей LLM ±25 %", False, "Приветствие"),
    ("Локальная модель (если подключена)", "температура ответа ±0,1", False, "Помощь"),
    ("Обучение на ответах", "подрезка и сохранение памяти пар — на вдохе (сами ответы не качаются)", False, "Обучение"),
    ("Слух (микрофон мозга → Vosk)", "на плате: чуткость микрофона ±15 % (вдох — слышит тише)", True, "Сигнал"),
    ("Зрение (камера → разбор кадра)", "на плате: темп кадров ±30 % (вдох — чаще)", True, "Камера"),
]
NOT_BREATHING = []
_BOARD_USES = {}
# v3.134: ВОЛЯ МОЗГА РОУЗ (мозг платы neuro_12): активность зон его зонной
# сети -> сила каждого алгоритма. Зона активна — её алгоритмы работают
# сильнее (до ×1.5), молчит — приглушены (до ×0.5). Мозга нет — ×1.
_WILL = {}                 # плата -> {"z": {зона: 0..1}, "ts"}
ZONE_OF = {a[0]: a[3] for a in ALGOS}
ZONE_OF["Своя LLM: параметры"] = "Буквы"
_USE = {}


def note_board(board, period, phase, depth=None, uses=None):
    """Плата прислала дыхание своей ТЕНЗОРНОЙ СЕТИ (период, глубина, фаза)."""
    try:
        p, ph = float(period), float(phase) % 1.0
        d = float(depth) if depth is not None else 0.6
    except (TypeError, ValueError):
        return
    if not (2.0 <= p <= 30.0):
        return
    with _LOCK:
        _ST["boards"][str(board)[:40]] = {"period": p, "phase": ph, "ts": time.time(),
                                           "depth": max(0.2, min(1.0, d))}
        if isinstance(uses, dict):
            _BOARD_USES[str(board)[:40]] = {str(k)[:60]: int(v or 0) for k, v in uses.items()}
        _ST["ts"] = 0.0                    # пересчитать общее дыхание сразу


def _why_no_tn(now):
    """v3.135: почему нет дыхания тензорной сети — честно и конкретно."""
    try:
        import boardlink
        on = [(bid, b) for bid, b in boardlink._ST["boards"].items()
              if now - float(b.get("ts") or 0) < 90]
    except Exception:
        on = []
    if not on:
        return "плат на связи нет (канал связи молчит), дыхания тензорной сети нет"
    errs = [(bid, b["extras_err"]["breath"]) for bid, b in on
            if (b.get("extras_err") or {}).get("breath")]
    if errs:
        return "плата %s не смогла собрать дыхание: %s" % errs[0]
    old = [(bid, b.get("ver") or "?") for bid, b in on if not b.get("has_breath")]
    if old:
        return ("плата %s (версия %s) не присылает дыхание тензорной сети — "
                "поставь приложение платы v2.77+" % old[0])
    return "дыхание плат ещё не пришло"


def _recompute(now):
    with _LOCK:
        fresh = {b: v for b, v in _ST["boards"].items() if now - v["ts"] < 60}
        _ST["boards"] = fresh
        if fresh:
            T = sum(v["period"] for v in fresh.values()) / len(fresh)
            D = sum(v["depth"] for v in fresh.values()) / len(fresh)
            sx = sy = 0.0
            for v in fresh.values():
                ph = (v["phase"] + (now - v["ts"]) / v["period"]) % 1.0
                sx += math.cos(2 * math.pi * ph)
                sy += math.sin(2 * math.pi * ph)
            ph_mean = (math.atan2(sy, sx) / (2 * math.pi)) % 1.0
            _ST.update(t0=now - ph_mean * T, period=T, depth=D,
                       src="тензорная сеть %d плат" % len(fresh),
                       last_tn={"T": T, "D": D, "ts": now})
        else:
            lt = _ST.get("last_tn")
            if lt and now - lt["ts"] < 3600:
                T, D = lt["T"], lt["D"]
                src = ("последнее дыхание тензорной сети платы (%d мин назад)"
                       % int((now - lt["ts"]) / 60))
            else:
                T, D = 8.0, 0.6
                src = _why_no_tn(now) + " — покой 8 с"
            ph_old = ((now - _ST["t0"]) / _ST["period"]) % 1.0
            _ST.update(t0=now - ph_old * T, period=T, depth=D, src=src)
        _ST["ts"] = now


def note_will(board, zones):
    """Плата прислала активность зон своей зонной сети (0..1)."""
    if not isinstance(zones, dict):
        return
    z = {}
    for k, v in zones.items():
        try:
            number = float(v)
            if math.isfinite(number):
                z[str(k)[:30]] = max(0.0, min(1.0, number))
        except (TypeError, ValueError):
            pass
    with _LOCK:
        _WILL[str(board)[:40]] = {"z": z, "ts": time.time()}


def will_zones():
    """Средняя активность зон мозга по платам на связи | None."""
    now = time.time()
    with _LOCK:
        fresh = [v["z"] for v in _WILL.values() if 0 <= now - v["ts"] < 90]
    if not fresh:
        return None
    out = {}
    count = {}
    for z in fresh:
        for k, v in z.items():
            if math.isfinite(v):
                out[k] = out.get(k, 0.0) + v
                count[k] = count.get(k, 0) + 1
    return {k: v / count[k] for k, v in out.items()} or None


def strength(name):
    """Сила алгоритма по воле мозга: 0.5 + активность его зоны (0.5..1.5);
    мозг не на связи — 1.0 (работает как обычно)."""
    wz = will_zones()
    if wz is None:
        return 1.0
    zone = ZONE_OF.get(name, "")
    if zone not in wz:
        return 1.0
    try:                       # v3.166: строгий режим — 2×воля (0…2)
        import brainctl
        return brainctl.strength(wz[zone])
    except Exception:
        return 0.5 + max(0.0, min(1.0, wz[zone]))


def phase(now=None):
    now = now or time.time()
    if now - _ST["ts"] > 2.0:
        _recompute(now)
    return ((now - _ST["t0"]) / _ST["period"]) % 1.0


def s(now=None):
    """Дыхание -d..d (вдох > 0); d — глубина дыхания тензорной сети."""
    ph = phase(now)
    return _ST["depth"] * math.sin(2 * math.pi * ph)


def _mark(name):
    u = _USE.setdefault(name, {"n": 0, "ts": 0.0})
    u["n"] += 1
    u["ts"] = time.time()


def mod(name, x, depth=0.1):
    """Значение x, «дышащее» с глубиной depth (доля). v3.134: глубину
    задаёт и воля мозга — сильная зона дышит глубже, слабая мельче."""
    _mark(name)
    try:
        return x * (1.0 + depth * strength(name) * s())
    except Exception:
        return x


def inhale(name):
    """Сейчас вдох? (фоновая учёба/чтение — только на вдохе). v3.134: и мозг
    не против — у приглушённой зоны часть вдохов пропускается."""
    _mark(name)
    try:                       # v3.166: ворота мозга — зона молчит, процесс стоит
        import brainctl
        if not brainctl.allowed(name):
            return False
    except Exception:
        pass
    if s() < 0.0:
        return False
    import random as _r
    return _r.random() < min(1.0, strength(name))


def for_board():
    """Что отдать плате в ответ на канал связи: общий период и фаза."""
    ph = phase()
    return {"period": round(_ST["period"], 2), "phase": round(ph, 4),
            "depth": round(_ST["depth"], 3), "ts": time.time()}


def status():
    now = time.time()
    ph = phase(now)
    sv = _ST["depth"] * math.sin(2 * math.pi * ph)
    bu = {}
    for u in _BOARD_USES.values():
        for k, v in u.items():
            bu[k] = bu.get(k, 0) + v
    algos = []
    wz = will_zones()
    for name, how, on_board, zone in ALGOS:
        u = _USE.get(name) or {}
        n = u.get("n", 0) + bu.get(name, 0)
        algos.append({"name": name, "how": how, "uses": n, "board": on_board,
                      "zone": zone, "strength": round(strength(name), 2),
                      "ago": int(now - u["ts"]) if u.get("ts") else None})
    return {"ok": True, "period": round(_ST["period"], 2), "phase": round(ph, 3),
            "depth": round(_ST["depth"], 2), "s": round(sv, 3),
            "state": "вдох" if sv >= 0 else "выдох",
            "src": _ST["src"], "boards": len(_ST["boards"]),
            "algos": algos, "not_breathing": [],
            "will": {k: round(v, 2) for k, v in (wz or {}).items()},
            "will_src": ("воля мозга Роуз: зонная сеть %d плат" % len(
                [1 for v in _WILL.values() if now - v["ts"] < 90])) if wz is not None
            else "мозг Роуз не на связи — все алгоритмы работают с силой ×1",
            "count": {"cloud_total": len(ALGOS), "cloud_breathing": len(ALGOS),
                      "board_total": 9, "all": len(ALGOS) + 9}}
