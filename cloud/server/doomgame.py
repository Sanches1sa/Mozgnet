# -*- coding: utf-8 -*-
"""
doomgame.py — v3.127: «РОК-12», игра в стиле DOOM внутри облака.

Мир живёт НА СЕРВЕРЕ (поток 20 тиков/с), поэтому мозг платы может играть,
даже когда панель закрыта. Веб-страница /doom только показывает мир
(лучевой 3D-вид, как в DOOM) и даёт поиграть человеку.

Кто играет:
  • БОТ ПЛАТЫ neuro_12 — «морпех» под управлением мозга платы. Плата сама
    решает, хочет ли она играть (желание = скука + напряжение сети +
    приглашение − усталость − досада от смертей), и сама выбирает действия:
    её маленькая нейросеть (поверх врождённых рефлексов) получает то, что
    «видит» бот (лучи до стен, враг, предмет, направление к выходу,
    здоровье, патроны), и отвечает поворотом, шагом, стрейфом и выстрелом.
    Награда игры (убил, подобрал, продвинулся к выходу; ранен, погиб,
    застрял) уходит плате — её сеть учится (REINFORCE).
  • ЧЕЛОВЕК — со страницы /doom (WASD/стрелки, пробел — огонь).
Совместная игра: игроки друг друга не ранят, враги — общие.

Все картинки игры рисуются кодом (свои монстры и текстуры): файлы
оригинального DOOM (WAD) не используются.

API:
  POST api/doom/bot      (токен платы)  {board, act, want} -> {obs, reward, events}
  GET  api/doom/state    (панель)       мир для показа
  POST api/doom/input    (панель)       управление человеком
  POST api/doom/control  (панель)       пригласить мозг, новая игра, пауза
  info_for_board(board)  -> поле «game» в ответе /api/link
"""
import math
import random
import threading
import time
from collections import deque

TICK = 0.05                      # 20 тиков в секунду
W = H = 36                       # размер карты в клетках
FOV = math.pi / 3
P_RADIUS = 0.25
P_SPEED = 3.2                    # клеток/с
P_TURN = 3.0                     # рад/с
BOT_TIMEOUT = 2.5                # плата молчит столько — бот стоит
BOT_GONE = 30.0                  # …а столько — бот уходит из мира
HUMAN_GONE = 20.0

MONSTERS = {
    # имя: hp, скорость, урон, дальность атаки, перезарядка, снаряд?, очки
    "mutant": {"name": "мутант", "hp": 30, "speed": 1.5, "dmg": 7,
               "range": 9.0, "cool": 1.6, "proj": True, "pspeed": 7.0},
    "fang": {"name": "клыкач", "hp": 70, "speed": 2.7, "dmg": 14,
             "range": 0.95, "cool": 0.9, "proj": False},
    "eye": {"name": "огнеглаз", "hp": 55, "speed": 1.2, "dmg": 13,
            "range": 12.0, "cool": 2.2, "proj": True, "pspeed": 5.0},
}
ITEMS = {
    "med": "аптечка", "armor": "броня", "ammo": "патроны",
    "shells": "дробь", "shotgun": "дробовик",
}
WEAPONS = {
    1: {"name": "бластер", "ammo": "b", "cool": 0.38, "pellets": 1,
        "spread": 0.015, "dmg": (9, 15)},
    2: {"name": "дробовик", "ammo": "s", "cool": 0.95, "pellets": 7,
        "spread": 0.11, "dmg": (6, 10)},
}

_LOCK = threading.RLock()
_G = {"started": False, "paused": False, "level": 0, "map": [], "mapver": 0,
      "tex": [], "start": (2.5, 2.5), "exit": (3, 3), "dist": [],
      "monsters": [], "items": [], "proj": [], "players": {},
      "log": deque(maxlen=40), "tick": 0, "next_level_at": 0.0,
      "invite": {}, "flow": {}, "flow_ts": 0.0, "eid": 0, "bots": {},
      "fx": deque(maxlen=60)}


# ---------------------------------------------------------------------------
# карта
# ---------------------------------------------------------------------------
def _solid(x, y):
    xi, yi = int(math.floor(x)), int(math.floor(y))
    if xi < 0 or yi < 0 or xi >= W or yi >= H:
        return True
    return _G["map"][yi][xi] != 0


def _bfs(src_cells, limit=None):
    m = _G["map"]
    dist = [[-1] * W for _ in range(H)]
    q = deque()
    for (cx, cy) in src_cells:
        if 0 <= cx < W and 0 <= cy < H and m[cy][cx] == 0:
            dist[cy][cx] = 0
            q.append((cx, cy))
    while q:
        cx, cy = q.popleft()
        d = dist[cy][cx]
        if limit and d >= limit:
            continue
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nx, ny = cx + dx, cy + dy
            if 0 <= nx < W and 0 <= ny < H and m[ny][nx] == 0 and dist[ny][nx] < 0:
                dist[ny][nx] = d + 1
                q.append((nx, ny))
    return dist


def _gen_level(n):
    rnd = random.Random(1000 + n * 7919 + int(time.time()) % 997)
    m = [[1] * W for _ in range(H)]
    rooms = []
    want = min(5 + n, 11)
    for _ in range(400):
        if len(rooms) >= want:
            break
        rw, rh = rnd.randint(4, 8), rnd.randint(4, 8)
        rx, ry = rnd.randint(1, W - rw - 2), rnd.randint(1, H - rh - 2)
        if any(rx - 2 < o[0] + o[2] and rx + rw + 2 > o[0] and
               ry - 2 < o[1] + o[3] and ry + rh + 2 > o[1] for o in rooms):
            continue
        rooms.append((rx, ry, rw, rh, rnd.randint(1, 4)))
    rooms.sort(key=lambda r: (r[0] + r[2] / 2.0) + 0.3 * (r[1] + r[3] / 2.0))
    owner = [[-1] * W for _ in range(H)]
    for i, (rx, ry, rw, rh, _t) in enumerate(rooms):
        for y in range(ry, ry + rh):
            for x in range(rx, rx + rw):
                m[y][x] = 0
                owner[y][x] = i
        # колонны в больших комнатах — укрытия
        if rw >= 6 and rh >= 6 and rnd.random() < 0.6:
            m[ry + rh // 2][rx + rw // 2] = 0
            for (px, py) in ((rx + 1, ry + 1), (rx + rw - 2, ry + rh - 2)):
                if rnd.random() < 0.5:
                    m[py][px] = 1
                    owner[py][px] = i

    def center(r):
        return r[0] + r[2] // 2, r[1] + r[3] // 2

    def carve(x0, y0, x1, y1, i):
        x, y = x0, y0
        while x != x1:
            if m[y][x]:
                m[y][x] = 0
                owner[y][x] = i
            x += 1 if x1 > x else -1
        while y != y1:
            if m[y][x]:
                m[y][x] = 0
                owner[y][x] = i
            y += 1 if y1 > y else -1
        m[y][x] = 0
    for i in range(1, len(rooms)):
        (ax, ay), (bx, by) = center(rooms[i - 1]), center(rooms[i])
        if rnd.random() < 0.5:
            carve(ax, ay, bx, ay, i)
            carve(bx, ay, bx, by, i)
        else:
            carve(ax, ay, ax, by, i)
            carve(ax, by, bx, by, i)
    # лишние проходы — петли, чтобы не было одного коридора
    for _ in range(min(3, len(rooms) // 3)):
        a, b = rnd.sample(range(len(rooms)), 2)
        (ax, ay), (bx, by) = center(rooms[a]), center(rooms[b])
        carve(ax, ay, bx, ay, a)
        carve(bx, ay, bx, by, a)
    # текстуры стен: по соседней комнате
    tex = [[0] * W for _ in range(H)]
    for y in range(H):
        for x in range(W):
            if m[y][x]:
                t = 1
                for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    nx, ny = x + dx, y + dy
                    if 0 <= nx < W and 0 <= ny < H and owner[ny][nx] >= 0:
                        t = rooms[owner[ny][nx]][4]
                        break
                tex[y][x] = t
                m[y][x] = t
    sx, sy = center(rooms[0])
    _G["map"] = m
    d0 = _bfs([(sx, sy)])
    far = max(range(len(rooms)), key=lambda i: d0[center(rooms[i])[1]][center(rooms[i])[0]])
    ex, ey = center(rooms[far])
    _G["start"] = (sx + 0.5, sy + 0.5)
    _G["exit"] = (ex, ey)
    _G["dist"] = _bfs([(ex, ey)])
    _G["tex"] = tex
    mons, items = [], []
    kinds = ["mutant"] * 6 + ["fang"] * (2 + n) + ["eye"] * max(0, n - 1) * 2
    for i, r in enumerate(rooms):
        cells = [(x, y) for y in range(r[1], r[1] + r[3])
                 for x in range(r[0], r[0] + r[2]) if m[y][x] == 0]
        rnd.shuffle(cells)
        if i > 0:
            for _ in range(rnd.randint(1, 1 + min(4, (n + 1) // 2 + 1))):
                if not cells:
                    break
                cx, cy = cells.pop()
                k = rnd.choice(kinds)
                mons.append(_new_mon(k, cx + 0.5, cy + 0.5, rnd))
        for _ in range(rnd.randint(0, 2)):
            if not cells:
                break
            cx, cy = cells.pop()
            items.append(_new_item(rnd.choice(["med", "ammo", "ammo", "shells",
                                               "armor", "med"]), cx + .5, cy + .5))
        if i == min(1, len(rooms) - 1) and cells:
            cx, cy = cells.pop()
            items.append(_new_item("shotgun", cx + .5, cy + .5))
    _G["monsters"], _G["items"], _G["proj"] = mons, items, []
    _G["level"] = n
    _G["mapver"] += 1
    _G["flow"], _G["flow_ts"] = {}, 0.0
    _event(None, "Уровень %d: комнат %d, врагов %d" % (n, len(rooms), len(mons)))


def _eid():
    _G["eid"] += 1
    return _G["eid"]


def _new_mon(kind, x, y, rnd=random):
    d = MONSTERS[kind]
    return {"id": _eid(), "kind": kind, "x": x, "y": y, "a": rnd.random() * 6.28,
            "hp": d["hp"], "state": "idle", "cool": rnd.random(), "target": None,
            "pain": 0.0, "dead_ts": 0.0, "last_seen": None}


def _new_item(kind, x, y):
    return {"id": _eid(), "kind": kind, "x": x, "y": y}


# ---------------------------------------------------------------------------
# лучи, видимость
# ---------------------------------------------------------------------------
def ray_dist(x, y, a, maxd=24.0):
    """Расстояние до стены по лучу (DDA)."""
    dx, dy = math.cos(a), math.sin(a)
    mx, my = int(x), int(y)
    ddx = abs(1.0 / dx) if dx else 1e30
    ddy = abs(1.0 / dy) if dy else 1e30
    if dx < 0:
        stx, sdx = -1, (x - mx) * ddx
    else:
        stx, sdx = 1, (mx + 1.0 - x) * ddx
    if dy < 0:
        sty, sdy = -1, (y - my) * ddy
    else:
        sty, sdy = 1, (my + 1.0 - y) * ddy
    d = 0.0
    for _ in range(80):
        if sdx < sdy:
            d, sdx, mx = sdx, sdx + ddx, mx + stx
        else:
            d, sdy, my = sdy, sdy + ddy, my + sty
        if d > maxd:
            return maxd
        if mx < 0 or my < 0 or mx >= W or my >= H or _G["map"][my][mx]:
            return d
    return maxd


def los(x0, y0, x1, y1):
    d = math.hypot(x1 - x0, y1 - y0)
    if d < 1e-6:
        return True
    return ray_dist(x0, y0, math.atan2(y1 - y0, x1 - x0), d + 0.01) >= d - 0.05


def _wrap(a):
    while a > math.pi:
        a -= 2 * math.pi
    while a < -math.pi:
        a += 2 * math.pi
    return a


def _move(o, dx, dy, r=P_RADIUS):
    nx = o["x"] + dx
    if not _solid(nx + (r if dx > 0 else -r), o["y"] - r * .7) and \
            not _solid(nx + (r if dx > 0 else -r), o["y"] + r * .7):
        o["x"] = nx
    ny = o["y"] + dy
    if not _solid(o["x"] - r * .7, ny + (r if dy > 0 else -r)) and \
            not _solid(o["x"] + r * .7, ny + (r if dy > 0 else -r)):
        o["y"] = ny


def _event(p, text, kind="info"):
    ts = time.time()
    _G["log"].append({"ts": ts, "who": (p or {}).get("name", ""), "text": text,
                      "kind": kind})
    if p is not None:
        p["events"].append(text)
        del p["events"][:-12]


# ---------------------------------------------------------------------------
# игроки
# ---------------------------------------------------------------------------
def _new_player(pid, name, kind):
    sx, sy = _G["start"]
    off = (len(_G["players"]) % 4) * 0.3 - 0.45
    p = {"id": pid, "name": name, "kind": kind, "x": sx + off, "y": sy,
         "a": 0.0, "hp": 100, "armor": 0, "ammo": {"b": 40, "s": 0},
         "weapons": [1], "weapon": 1, "cool": 0.0, "kills": 0, "deaths": 0,
         "dead_until": 0.0, "input": {}, "in_ts": time.time(),
         "seen_ts": time.time(), "events": [], "rw": 0.0,
         "best_d": None, "stuck_ref": (sx, sy, time.time()), "stuck": False,
         "pain": 0.0, "fire_ts": 0.0, "joined": time.time(), "levels": 0,
         "score": 0.0}
    return p


def _spawn(p):
    sx, sy = _G["start"]
    p.update(x=sx, y=sy, a=0.0, hp=100, armor=0, dead_until=0.0,
             best_d=None, stuck_ref=(sx, sy, time.time()))
    p["ammo"]["b"] = max(p["ammo"]["b"], 30)


def _hurt_player(p, dmg, src=""):
    if p["dead_until"]:
        return
    if p["armor"] > 0:
        a = min(p["armor"], dmg // 3)
        p["armor"] -= a
        dmg -= a
    p["hp"] -= dmg
    p["pain"] += dmg
    p["rw"] -= 0.02 * dmg
    if p["hp"] <= 0:
        p["hp"] = 0
        p["deaths"] += 1
        p["dead_until"] = time.time() + 3.0
        p["rw"] -= 2.0
        _event(p, "погиб (%s)" % (src or "враг"), "death")


def _fire(p):
    w = WEAPONS[p["weapon"]]
    if p["ammo"][w["ammo"]] <= 0:
        if p["weapon"] != 1 and p["ammo"]["b"] > 0:
            p["weapon"] = 1
        return
    p["ammo"][w["ammo"]] -= 1
    p["cool"] = w["cool"]
    p["fire_ts"] = time.time()
    _G["fx"].append({"ts": time.time(), "kind": "shot", "x": p["x"], "y": p["y"],
                     "a": p["a"], "pid": p["id"]})
    # шум будит врагов рядом
    for mo in _G["monsters"]:
        if mo["hp"] > 0 and mo["state"] == "idle" and \
                math.hypot(mo["x"] - p["x"], mo["y"] - p["y"]) < 9:
            mo["state"], mo["target"] = "chase", p["id"]
    for _ in range(w["pellets"]):
        a = p["a"] + random.uniform(-w["spread"], w["spread"])
        wd = ray_dist(p["x"], p["y"], a)
        ca, sa = math.cos(a), math.sin(a)
        best, bd = None, wd
        for mo in _G["monsters"]:
            if mo["hp"] <= 0:
                continue
            vx, vy = mo["x"] - p["x"], mo["y"] - p["y"]
            along = vx * ca + vy * sa
            if along <= 0 or along >= bd:
                continue
            perp = abs(-vx * sa + vy * ca)
            if perp < 0.38:
                best, bd = mo, along
        if best is not None:
            dmg = random.randint(*w["dmg"])
            best["hp"] -= dmg
            best["pain"] = 0.25
            best["state"], best["target"] = "chase", p["id"]
            p["rw"] += 0.015 * dmg
            if best["hp"] <= 0:
                best["dead_ts"] = time.time()
                p["kills"] += 1
                p["rw"] += 1.0
                p["score"] += 100
                _event(p, "убил: %s" % MONSTERS[best["kind"]]["name"], "kill")
                if random.random() < 0.35:
                    _G["items"].append(_new_item(random.choice(["ammo", "shells", "med"]),
                                                 best["x"], best["y"]))


def _step_player(p, dt, now):
    if p["dead_until"]:
        if now >= p["dead_until"]:
            _spawn(p)
            _event(p, "снова в игре")
        return
    inp = p["input"] if now - p["in_ts"] < BOT_TIMEOUT else {}
    p["cool"] = max(0.0, p["cool"] - dt)
    p["pain"] *= 0.8
    if p["kind"] == "human" and "x" in inp:
        # человек: позицию считает его браузер (плавно, без задержки сети),
        # облако проверяет стены и скорость
        nx, ny = float(inp["x"]), float(inp["y"])
        if math.hypot(nx - p["x"], ny - p["y"]) < P_SPEED * 0.6 and \
                not _solid(nx, ny):
            p["x"], p["y"] = nx, ny
        p["a"] = float(inp.get("a", p["a"]))
    else:
        turn = max(-1.0, min(1.0, float(inp.get("turn", 0) or 0)))
        mv = max(-1.0, min(1.0, float(inp.get("move", 0) or 0)))
        sf = max(-1.0, min(1.0, float(inp.get("strafe", 0) or 0)))
        p["a"] = _wrap(p["a"] + turn * P_TURN * dt)
        ca, sa = math.cos(p["a"]), math.sin(p["a"])
        _move(p, (ca * mv - sa * sf) * P_SPEED * dt, (sa * mv + ca * sf) * P_SPEED * dt)
    wsel = int(inp.get("weapon", 0) or 0)
    if wsel in p["weapons"] and wsel != p["weapon"]:
        p["weapon"] = wsel
    if inp.get("fire") and p["cool"] <= 0:
        _fire(p)
    # предметы
    for it in list(_G["items"]):
        if math.hypot(it["x"] - p["x"], it["y"] - p["y"]) < 0.55:
            k, took = it["kind"], True
            if k == "med":
                took = p["hp"] < 100
                p["hp"] = min(100, p["hp"] + 25)
            elif k == "armor":
                took = p["armor"] < 100
                p["armor"] = min(100, p["armor"] + 50)
            elif k == "ammo":
                p["ammo"]["b"] = min(200, p["ammo"]["b"] + 15)
            elif k == "shells":
                p["ammo"]["s"] = min(50, p["ammo"]["s"] + 6)
            elif k == "shotgun":
                if 2 not in p["weapons"]:
                    p["weapons"].append(2)
                    p["weapon"] = 2
                p["ammo"]["s"] = min(50, p["ammo"]["s"] + 8)
            if took:
                _G["items"].remove(it)
                p["rw"] += 0.3
                _event(p, "подобрал: %s" % ITEMS[k], "item")
    # продвижение к выходу
    cx, cy = int(p["x"]), int(p["y"])
    d = _G["dist"][cy][cx] if _G["dist"] else -1
    if d >= 0:
        if p["best_d"] is None:
            p["best_d"] = d
        elif d < p["best_d"]:
            p["rw"] += 0.05 * (p["best_d"] - d)
            p["best_d"] = d
    # застрял?
    rx, ry, rt = p["stuck_ref"]
    if now - rt > 1.5:
        p["stuck"] = math.hypot(p["x"] - rx, p["y"] - ry) < 0.3 and \
            p["kind"] in ("bot", "llm")
        if p["stuck"]:
            p["rw"] -= 0.05
        p["stuck_ref"] = (p["x"], p["y"], now)
    ex, ey = _G["exit"]
    if (cx, cy) == (ex, ey) and not _G["next_level_at"]:
        _G["next_level_at"] = now + 2.0
        for q in _G["players"].values():
            q["rw"] += 5.0 if q is p else 2.0
            q["levels"] += 1
        p["score"] += 1000
        _event(p, "дошёл до выхода — уровень %d пройден" % _G["level"], "level")


# ---------------------------------------------------------------------------
# враги
# ---------------------------------------------------------------------------
def _flow_to(pid):
    """Поле путей к игроку (обновляется 2 раза в секунду)."""
    f = _G["flow"].get(pid)
    return f


def _step_monsters(dt, now):
    alive = [p for p in _G["players"].values() if not p["dead_until"]]
    if now - _G["flow_ts"] > 0.5:
        _G["flow_ts"] = now
        _G["flow"] = {p["id"]: _bfs([(int(p["x"]), int(p["y"]))], limit=30)
                      for p in alive}
    for mo in _G["monsters"]:
        if mo["hp"] <= 0:
            continue
        d_ = MONSTERS[mo["kind"]]
        mo["cool"] = max(0.0, mo["cool"] - dt)
        mo["pain"] = max(0.0, mo["pain"] - dt)
        # ближайший видимый игрок
        tgt, td = None, 1e9
        for p in alive:
            dd = math.hypot(p["x"] - mo["x"], p["y"] - mo["y"])
            if dd < 16 and dd < td and los(mo["x"], mo["y"], p["x"], p["y"]):
                tgt, td = p, dd
        if tgt:
            mo["state"], mo["target"] = "chase", tgt["id"]
            mo["last_seen"] = (tgt["x"], tgt["y"])
        elif mo["state"] == "chase" and mo["target"] in _G["players"]:
            tgt = _G["players"][mo["target"]]
            if tgt["dead_until"]:
                mo["state"], tgt = "idle", None
            else:
                td = math.hypot(tgt["x"] - mo["x"], tgt["y"] - mo["y"])
        else:
            mo["state"] = "idle"
        if not tgt or mo["pain"] > 0.15:
            continue
        seen = los(mo["x"], mo["y"], tgt["x"], tgt["y"])
        mo["a"] = math.atan2(tgt["y"] - mo["y"], tgt["x"] - mo["x"])
        if seen and td <= d_["range"] and mo["cool"] <= 0:
            mo["cool"] = d_["cool"] * random.uniform(0.8, 1.3)
            try:                              # v3.132: на вдохе монстры спокойнее
                import breath as _brd
                mo["cool"] = _brd.mod("ИИ монстров «РОК-12»", mo["cool"], 0.20)
            except Exception:
                pass
            mo["attack_ts"] = now
            if d_["proj"]:
                a = mo["a"] + random.uniform(-0.06, 0.06)
                _G["proj"].append({"id": _eid(), "x": mo["x"], "y": mo["y"],
                                   "vx": math.cos(a) * d_["pspeed"],
                                   "vy": math.sin(a) * d_["pspeed"],
                                   "dmg": d_["dmg"], "kind": mo["kind"],
                                   "src": d_["name"], "ts": now})
            else:
                _hurt_player(tgt, random.randint(d_["dmg"] - 4, d_["dmg"] + 4),
                             d_["name"])
            if d_["proj"]:
                continue
        # ход: дальнобойные держат дистанцию, ближние лезут вплотную
        if d_["proj"] and seen and td < d_["range"] * 0.45:
            continue
        if not d_["proj"] and td < 0.8:
            continue
        sp = d_["speed"] * dt
        if seen:
            dx, dy = math.cos(mo["a"]) * sp, math.sin(mo["a"]) * sp
        else:
            fl = _G["flow"].get(tgt["id"])
            cx, cy = int(mo["x"]), int(mo["y"])
            best = None
            if fl and fl[cy][cx] > 0:
                for ddx, ddy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    nx, ny = cx + ddx, cy + ddy
                    if 0 <= nx < W and 0 <= ny < H and 0 <= fl[ny][nx] < fl[cy][cx]:
                        best = (nx + 0.5, ny + 0.5)
                        break
            if not best:
                continue
            a = math.atan2(best[1] - mo["y"], best[0] - mo["x"])
            dx, dy = math.cos(a) * sp, math.sin(a) * sp
        _move(mo, dx, dy, 0.3)


def _step_proj(dt, now):
    keep = []
    for pr in _G["proj"]:
        pr["x"] += pr["vx"] * dt
        pr["y"] += pr["vy"] * dt
        if _solid(pr["x"], pr["y"]) or now - pr["ts"] > 6:
            continue
        hit = False
        for p in _G["players"].values():
            if not p["dead_until"] and math.hypot(p["x"] - pr["x"], p["y"] - pr["y"]) < 0.4:
                _hurt_player(p, pr["dmg"], pr["src"])
                hit = True
                break
        if not hit:
            keep.append(pr)
    _G["proj"] = keep


def _step(dt):
    now = time.time()
    for pid, p in list(_G["players"].items()):
        gone = BOT_GONE if p["kind"] == "bot" else HUMAN_GONE
        if now - p["seen_ts"] > gone:
            _event(p, "вышел из игры (нет связи)")
            del _G["players"][pid]
    if not _G["players"] or _G["paused"]:
        return
    _G["tick"] += 1
    for p in list(_G["players"].values()):
        _step_player(p, dt, now)
    _step_monsters(dt, now)
    _step_proj(dt, now)
    _G["monsters"] = [m for m in _G["monsters"]
                      if m["hp"] > 0 or now - m["dead_ts"] < 25]
    if _G["next_level_at"] and now >= _G["next_level_at"]:
        _G["next_level_at"] = 0.0
        _gen_level(_G["level"] + 1)
        for p in _G["players"].values():
            hp = max(p["hp"], 60)
            _spawn(p)
            p["hp"] = hp


def _loop():
    last = time.time()
    while True:
        time.sleep(TICK)
        now = time.time()
        dt = min(0.1, now - last)
        last = now
        try:
            with _LOCK:
                _step(dt)
        except Exception:
            import traceback
            traceback.print_exc()
            time.sleep(1.0)


def ensure():
    with _LOCK:
        if _G["started"]:
            return
        _G["started"] = True
        _gen_level(1)
    threading.Thread(target=_loop, daemon=True, name="doom").start()


# ---------------------------------------------------------------------------
# что «видит» бот (вход нейросети платы)
# ---------------------------------------------------------------------------
RAYS = (-60, -40, -20, 0, 20, 40, 60)


def observe(p):
    x, y, a = p["x"], p["y"], p["a"]
    rays = [round(min(8.0, ray_dist(x, y, a + math.radians(r), 8.0)) / 8.0, 3)
            for r in RAYS]
    enemy, ecount = None, 0
    for mo in _G["monsters"]:
        if mo["hp"] <= 0:
            continue
        dx, dy = mo["x"] - x, mo["y"] - y
        dd = math.hypot(dx, dy)
        rel = _wrap(math.atan2(dy, dx) - a)
        if dd < 14 and abs(rel) < 1.4 and los(x, y, mo["x"], mo["y"]):
            ecount += 1
            if enemy is None or dd < enemy["d"]:
                enemy = {"rel": round(rel / math.pi, 3), "d": round(dd, 2),
                         "kind": mo["kind"],
                         "hp": round(mo["hp"] / MONSTERS[mo["kind"]]["hp"], 2)}
    item = None
    for it in _G["items"]:
        dx, dy = it["x"] - x, it["y"] - y
        dd = math.hypot(dx, dy)
        rel = _wrap(math.atan2(dy, dx) - a)
        if dd < 10 and abs(rel) < 1.4 and los(x, y, it["x"], it["y"]):
            if item is None or dd < item["d"]:
                item = {"rel": round(rel / math.pi, 3), "d": round(dd, 2),
                        "kind": it["kind"]}
    # «чувство направления»: куда идти к выходу (путь по карте)
    goal = None
    dist = _G["dist"]
    cx, cy = int(x), int(y)
    if dist and dist[cy][cx] >= 0:
        wx, wy = cx, cy
        for _ in range(3):
            nxt = None
            for ddx, ddy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                nx, ny = wx + ddx, wy + ddy
                if 0 <= nx < W and 0 <= ny < H and 0 <= dist[ny][nx] < dist[wy][wx]:
                    nxt = (nx, ny)
                    break
            if not nxt:
                break
            wx, wy = nxt
        rel = _wrap(math.atan2(wy + 0.5 - y, wx + 0.5 - x) - a)
        goal = {"rel": round(rel / math.pi, 3), "steps": dist[cy][cx]}
    w = WEAPONS[p["weapon"]]
    return {"rays": rays, "enemy": enemy, "enemies": ecount, "item": item,
            "goal": goal, "hp": p["hp"], "armor": p["armor"],
            "ammo": p["ammo"][w["ammo"]], "weapon": p["weapon"],
            "weapons": list(p["weapons"]), "pain": round(p["pain"], 1),
            "stuck": bool(p["stuck"]), "dead": bool(p["dead_until"]),
            "level": _G["level"], "kills": p["kills"], "deaths": p["deaths"]}


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------
def _bot_pid(board):
    return "bot:" + str(board or "board")[:40]


def bot_call(body):
    """Плата: действие + желание -> наблюдение, награда, события."""
    ensure()
    board = str(body.get("board") or "board")[:40]
    want = body.get("want") or {}
    act = body.get("act") or {}
    pid = _bot_pid(board)
    now = time.time()
    with _LOCK:
        info = _G["bots"].setdefault(board, {})
        info.update(ts=now, desire=want.get("desire"), why=str(want.get("why") or "")[:200],
                    join=bool(want.get("join")), brain=body.get("brain") or {})
        p = _G["players"].get(pid)
        if not want.get("join"):
            if p:
                _event(p, "мозг платы вышел из игры: %s" % (info["why"] or "не хочет"),
                       "leave")
                del _G["players"][pid]
            return {"ok": True, "playing": False,
                    "invite": bool(_G["invite"].get(board) or _G["invite"].get("*"))}
        if not p:
            p = _new_player(pid, "мозг %s" % board, "bot")
            _G["players"][pid] = p
            _event(p, "мозг платы пришёл играть: %s" % (info["why"] or "захотел"), "join")
        p["seen_ts"] = now
        if isinstance(act, dict):
            p["input"] = {k: act.get(k) for k in ("turn", "move", "strafe", "fire", "weapon")}
            p["in_ts"] = now
        obs = observe(p)
        rw, ev = round(p["rw"], 3), p["events"][:]
        p["rw"], p["events"] = 0.0, []
        thoughts = body.get("say")
        if thoughts:
            _event(p, "💭 " + str(thoughts)[:160], "thought")
        return {"ok": True, "playing": True, "obs": obs, "reward": rw,
                "events": ev, "level": _G["level"], "paused": _G["paused"],
                "invite": bool(_G["invite"].get(board) or _G["invite"].get("*"))}


def info_for_board(board):
    """Поле «game» в ответе /api/link: игра есть, позвали ли поиграть."""
    with _LOCK:
        humans = sum(1 for p in _G["players"].values() if p["kind"] == "human")
        return {"on": True, "ver": 1,
                "invite": bool(_G["invite"].get(board) or _G["invite"].get("*")),
                "humans": humans, "level": _G["level"] if _G["started"] else 0}


def human_input(sid, body):
    ensure()
    pid = "me:" + (str(sid or "owner")[:8])
    now = time.time()
    with _LOCK:
        p = _G["players"].get(pid)
        if body.get("leave"):
            if p:
                _event(p, "человек вышел из игры", "leave")
                del _G["players"][pid]
            return {"ok": True, "playing": False}
        if not p:
            p = _new_player(pid, str(body.get("name") or "игрок")[:20], "human")
            _G["players"][pid] = p
            _event(p, "человек вошёл в игру", "join")
        p["seen_ts"] = now
        inp = {}
        for k in ("x", "y", "a"):
            if k in body:
                try:
                    inp[k] = float(body[k])
                except (TypeError, ValueError):
                    pass
        inp["fire"] = bool(body.get("fire"))
        inp["weapon"] = int(body.get("weapon") or 0)
        if p["dead_until"]:
            inp.pop("x", None)
            inp.pop("y", None)
        p["input"], p["in_ts"] = inp, now
        return {"ok": True, "playing": True, "id": pid,
                "me": {"x": p["x"], "y": p["y"], "a": p["a"], "hp": p["hp"],
                       "dead": bool(p["dead_until"])}}


def control(body):
    ensure()
    act = str(body.get("action") or "")
    who = str(body.get("board") or "*")[:40]
    with _LOCK:
        if act == "invite":
            _G["invite"][who] = True
            _event(None, "владелец позвал мозг поиграть" +
                   ("" if who == "*" else " (%s)" % who))
        elif act == "uninvite":
            _G["invite"].pop(who, None)
            _event(None, "приглашение снято")
        elif act == "llm_join":          # v3.169: своя LLM — второй игрок
            return llm_join()
        elif act == "llm_leave":
            return llm_leave()
        elif act == "pause":
            _G["paused"] = not _G["paused"]
            _event(None, "пауза" if _G["paused"] else "игра продолжается")
        elif act == "restart":
            _gen_level(1)
            for p in _G["players"].values():
                p.update(kills=0, deaths=0, weapons=[1], weapon=1,
                         ammo={"b": 40, "s": 0}, score=0.0, levels=0)
                _spawn(p)
        elif act == "next":
            _gen_level(_G["level"] + 1)
            for p in _G["players"].values():
                _spawn(p)
        else:
            return {"ok": False, "error": "неизвестное действие"}
        return {"ok": True}


def state(mapver=0):
    ensure()
    now = time.time()
    with _LOCK:
        out = {"ok": True, "level": _G["level"], "mapver": _G["mapver"],
               "paused": _G["paused"], "tick": _G["tick"], "now": now,
               "exit": _G["exit"],
               "players": [dict({k: p[k] for k in ("id", "name", "kind", "x", "y", "a",
                                                   "hp", "armor", "weapon", "kills",
                                                   "deaths", "score", "levels")},
                                ammo=p["ammo"][WEAPONS[p["weapon"]]["ammo"]],
                                dead=bool(p["dead_until"]),
                                firing=now - p["fire_ts"] < 0.15,
                                pain=round(p["pain"], 1))
                           for p in _G["players"].values()],
               "monsters": [{"id": m["id"], "kind": m["kind"], "x": round(m["x"], 3),
                             "y": round(m["y"], 3), "dead": m["hp"] <= 0,
                             "pain": m["pain"] > 0,
                             "attack": now - m.get("attack_ts", 0) < 0.3}
                            for m in _G["monsters"]],
               "items": [{"id": i["id"], "kind": i["kind"], "x": i["x"], "y": i["y"]}
                         for i in _G["items"]],
               "proj": [{"id": p["id"], "x": round(p["x"], 3), "y": round(p["y"], 3),
                         "kind": p["kind"]} for p in _G["proj"]],
               "log": [dict(e) for e in list(_G["log"])[-25:]],
               "bots": {b: {"desire": v.get("desire"), "why": v.get("why"),
                            "join": v.get("join"), "age": int(now - v.get("ts", 0)),
                            "brain": v.get("brain")}
                        for b, v in _G["bots"].items()},
               "invite": sorted(_G["invite"].keys()),
               "llm": llm_status()}
        if int(mapver or 0) != _G["mapver"]:
            out["map"] = ["".join(str(c) for c in row) for row in _G["map"]]
        return out


# ---------------------------------------------------------------------------
# v3.169: СВОЯ LLM — ВТОРОЙ ИГРОК. Заявка владельца: «если может, будет
# играть в РОК-12 в качестве второго игрока».
# Честно о том, как она играет: своя LLM — языковая модель, джойстика у неё
# нет. Раз в TACTIC_EVERY она «читает» ситуацию словами («вижу клыкача в
# 3 клетках, здоровье 40…») и выбирает ТАКТИКУ — ту фразу из вариантов,
# которую её сеть считает самой вероятной после описания (nll). К оценке
# LLM добавляется опыт игры: средняя награда каждой тактики (учится на
# убийствах, подборах, пути к выходу; ранениях и смертях). Тактику
# выполняют рефлексы (повернуться к врагу, стрелять, идти к выходу, обойти
# стену). Не обучена LLM или нет numpy — тактику выбирает только опыт,
# это видно в журнале игры.
# ---------------------------------------------------------------------------
LLM_PID = "llm:cloud"
TACTIC_EVERY = 1.5
LLM_TICK = 0.12
TACTICS = {
    "атака": "Атакую: поворачиваюсь к врагу и стреляю.",
    "отход": "Отступаю: здоровья мало, ухожу от врага.",
    "лечение": "Иду к аптечке, чтобы вылечиться.",
    "предмет": "Подбираю предмет рядом.",
    "выход": "Иду к выходу с уровня.",
    "разведка": "Осматриваюсь и ищу проход.",
}
_LLM = {"on": False, "thread": None, "tactic": "разведка", "t_ts": 0.0,
        "q": {}, "n": {}, "rw_acc": 0.0, "src": "", "why": "", "said": 0.0,
        "loaded": False, "decisions": 0}


def _llm_q_load():
    if _LLM["loaded"]:
        return
    _LLM["loaded"] = True
    try:
        import store
        d = store.load_json("doom_llm.json", {}) or {}
        _LLM["q"] = {k: float(v) for k, v in (d.get("q") or {}).items() if k in TACTICS}
        _LLM["n"] = {k: int(v) for k, v in (d.get("n") or {}).items() if k in TACTICS}
    except Exception:
        pass


def _llm_q_save():
    try:
        import store
        store.save_json("doom_llm.json", {"q": _LLM["q"], "n": _LLM["n"]})
    except Exception:
        pass


def _situation(o):
    parts = ["Я играю в РОК-12."]
    e = o.get("enemy")
    if e:
        parts.append("Вижу врага: %s в %.0f клетках%s." % (
            MONSTERS.get(e["kind"], {}).get("name", "враг"), e["d"],
            ", прямо передо мной" if abs(e["rel"]) < 0.08 else ""))
        if o.get("enemies", 0) > 1:
            parts.append("Врагов несколько: %d." % o["enemies"])
    else:
        parts.append("Врагов не видно.")
    parts.append("Здоровье %d, броня %d, патронов %d." % (o["hp"], o["armor"], o["ammo"]))
    it = o.get("item")
    if it:
        parts.append("Рядом лежит %s." % ITEMS.get(it["kind"], "предмет"))
    if o.get("pain", 0) > 1:
        parts.append("Меня ранят.")
    if o.get("stuck"):
        parts.append("Я застрял у стены.")
    if o.get("goal"):
        parts.append("До выхода %d шагов." % o["goal"]["steps"])
    return " ".join(parts)


def _allowed_tactics(o):
    ok = ["разведка"]
    if o.get("goal"):
        ok.append("выход")
    if o.get("enemy") and o.get("ammo", 0) > 0:
        ok.append("атака")
    if o.get("enemy"):
        ok.append("отход")
    it = o.get("item")
    if it:
        ok.append("лечение" if it["kind"] == "med" else "предмет")
    return ok


def _prior(t, o):
    """Здравый смысл как запасной голос (когда LLM молчит)."""
    hp, e = o["hp"], o.get("enemy")
    return {"атака": 0.6 if e and hp > 35 else 0.1,
            "отход": 0.7 if e and hp <= 35 else -0.2,
            "лечение": 0.8 if hp < 60 else 0.0,
            "предмет": 0.3,
            "выход": 0.4 if not e else 0.0,
            "разведка": 0.05 if not o.get("goal") else -0.1}.get(t, 0.0)


def _llm_scores(sit, cands):
    """-nll фраз тактик после описания ситуации. -> ({t: score}, "" | причина)."""
    try:
        import tinyllm
        lk = getattr(tinyllm, "_LOCK", None)
        if lk is None:
            return {}, "нет numpy"
        if not lk.acquire(blocking=False):
            return {}, "LLM занята обучением"
        try:
            m = tinyllm.model()
            if not m.ready or not m.steps:
                return {}, "LLM не обучена"
            out = {}
            for t in cands:
                v = m.nll(sit + " ", TACTICS[t])
                if v is not None:
                    out[t] = -float(v)
            return out, ""
        finally:
            lk.release()
    except Exception as e:
        return {}, str(e)[:60]


def _choose(o):
    _llm_q_load()
    cands = _allowed_tactics(o)
    sit = _situation(o)
    ls, why = _llm_scores(sit, cands)
    if ls:                                      # нормируем голос LLM
        mx, mn = max(ls.values()), min(ls.values())
        span = (mx - mn) or 1.0
        ls = {k: (v - mn) / span for k, v in ls.items()}
    try:
        import breath
        brave = breath.strength("Своя LLM: РОК-12")   # воля мозга: смелость
    except Exception:
        brave = 1.0
    best, bs = cands[0], -1e9
    for t in cands:
        n = _LLM["n"].get(t, 0)
        # опыт + здравый смысл + голос LLM (LLM сдвигает выбор, но не
        # отменяет очевидного: при 10 здоровья под огнём — не «разведка»)
        sc = _LLM["q"].get(t, 0.0) + _prior(t, o) + 0.5 * ls.get(t, 0.0) + \
            0.25 / math.sqrt(1 + n) + random.random() * 0.05
        if t == "атака":
            sc += 0.1 * (brave - 1.0)
        if sc > bs:
            best, bs = t, sc
    _LLM["src"] = "LLM + опыт" if ls else "опыт игры (%s)" % why
    return best, sit


def _reflex(t, o):
    """Тактика -> управление (поворот, шаг, стрейф, огонь, оружие)."""
    rays = o["rays"]
    turn = move = strafe = 0.0
    fire = False
    e, it, g = o.get("enemy"), o.get("item"), o.get("goal")
    if t == "атака" and e:
        turn = max(-1.0, min(1.0, e["rel"] * 6))
        fire = abs(e["rel"]) < 0.05
        move = 0.4 if e["d"] > 5 else (-0.4 if e["d"] < 2 else 0.0)
        strafe = 0.5 if int(time.time() * 1.3) % 2 else -0.5
    elif t == "отход" and e:
        turn = max(-1.0, min(1.0, (g["rel"] if g else -e["rel"]) * 5))
        move = 1.0
        fire = abs(e["rel"]) < 0.05 and o["ammo"] > 0
    elif t in ("лечение", "предмет") and it:
        turn = max(-1.0, min(1.0, it["rel"] * 6))
        move = 1.0 if abs(it["rel"]) < 0.3 else 0.3
    elif t == "выход" and g:
        turn = max(-1.0, min(1.0, g["rel"] * 6))
        move = 1.0 if abs(g["rel"]) < 0.35 else 0.2
    else:
        turn, move = 0.6, 0.5
    if rays[3] < 0.08 or o.get("stuck"):           # стена впереди — обойти
        turn = 1.0 if rays[5] + rays[6] > rays[0] + rays[1] else -1.0
        move, strafe = -0.2, 0.0
    weapon = 2 if (2 in o["weapons"] and e and e["d"] < 4) else 1
    return {"turn": turn, "move": move, "strafe": strafe, "fire": fire, "weapon": weapon}


def _llm_loop():
    while _LLM["on"]:
        time.sleep(LLM_TICK)
        now = time.time()
        try:
            with _LOCK:
                p = _G["players"].get(LLM_PID)
                if not p:
                    _LLM["on"] = False
                    break
                p["seen_ts"] = now
                o = observe(p)
                _LLM["rw_acc"] += p["rw"]
                p["rw"] = 0.0
                dead = bool(p["dead_until"])
            if dead:
                continue
            if now - _LLM["t_ts"] > TACTIC_EVERY:
                old = _LLM["tactic"]
                if _LLM["t_ts"]:                   # опыт: награда прошлой тактики
                    n = _LLM["n"].get(old, 0) + 1
                    _LLM["n"][old] = n
                    q = _LLM["q"].get(old, 0.0)
                    _LLM["q"][old] = round(q + (_LLM["rw_acc"] - q) / min(n, 50), 4)
                _LLM["rw_acc"] = 0.0
                t, _sit = _choose(o)               # LLM думает ВНЕ замка игры
                _LLM["tactic"], _LLM["t_ts"] = t, now
                _LLM["decisions"] += 1
                if t != old or now - _LLM["said"] > 12:
                    _LLM["said"] = now
                    with _LOCK:
                        _event(_G["players"].get(LLM_PID), "💭 LLM: %s (%s)" % (
                            TACTICS[t], _LLM["src"]), "thought")
                if _LLM["decisions"] % 20 == 0:
                    _llm_q_save()
            inp = _reflex(_LLM["tactic"], o)
            with _LOCK:
                p = _G["players"].get(LLM_PID)
                if p:
                    p["input"], p["in_ts"] = inp, time.time()
        except Exception:
            import traceback
            traceback.print_exc()
            time.sleep(1.0)
    _llm_q_save()


def llm_join():
    ensure()
    with _LOCK:
        if LLM_PID not in _G["players"]:
            p = _new_player(LLM_PID, "своя LLM", "llm")
            _G["players"][LLM_PID] = p
            _event(p, "своя LLM вошла в игру вторым игроком", "join")
        if not _LLM["on"]:
            _LLM["on"] = True
            _LLM["t_ts"] = 0.0
            th = threading.Thread(target=_llm_loop, daemon=True, name="doom-llm")
            _LLM["thread"] = th
            th.start()
    return {"ok": True, "llm": True}


def llm_leave():
    with _LOCK:
        p = _G["players"].pop(LLM_PID, None)
        _LLM["on"] = False
        if p:
            _event(None, "своя LLM вышла из игры")
    return {"ok": True, "llm": False}


def llm_status():
    _llm_q_load()
    return {"on": _LLM["on"], "tactic": _LLM["tactic"], "src": _LLM["src"],
            "decisions": _LLM["decisions"],
            "q": {k: round(v, 2) for k, v in _LLM["q"].items()}}
