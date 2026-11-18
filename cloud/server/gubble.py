# -*- coding: utf-8 -*-
"""
gubble.py — v3.181: «GUBBLE 2» — головоломка-лабиринт внутри облака
(в духе классической Gubble: вид сверху, инопланетянин Габбл снимает
крепёж подходящим инструментом, пока за ним гоняются роботы).

Правила:
  • На поле лежит КРЕПЁЖ четырёх видов: винты, гвозди, болты, заклёпки.
    Каждому нужен свой ИНСТРУМЕНТ: отвёртка, молоток, ключ, дрель. Габбл
    держит один инструмент; наступил на другой — поменял (старый остаётся
    лежать). Стоишь на крепеже с нужным инструментом — через 0.6 с снят.
  • Сняли ВЕСЬ крепёж — открывается ВЫХОД (портал). Дошёл любой игрок —
    уровень пройден для всех (игра совместная).
  • ВРАГИ: краб (патруль туда-сюда), призрак (медленно идёт по пути к
    игроку), искра (стоит, раз в 3 с бьёт током вокруг — видно по свечению),
    каток (едет прямо, у стены поворачивает, быстрый). Касание — минус жизнь,
    возрождение на старте с 2 с неуязвимости. Жизни кончились — уровень
    заново (жизни восстанавливаются).
  • БОНУСЫ: часы (+20 с), сердце (+жизнь), звезда (враги замирают на 5 с),
    кристалл (очки).
  • ТАЙМЕР на уровень: вышло время — минус жизнь, таймер на половину.
  • 60 уровней в 6 мирах по 10 (имя и строка сюжета у каждого), после
    60-го — новый круг (враги быстрее на 10 % за круг).

Кто играет: человек (/gubble — стрелки/WASD, несколько человек сразу),
своя LLM облака (тактика раз в 1.2 с: работа/инструмент/бонус/бегство/
выход/ожидание — выбирает tinyllm по описанию ситуации + опыт), мозг платы
через POST api/gubble/bot (как в РОК-12: {board, act:{dx,dy}, want} ->
{obs, reward, events}).

Все картинки рисуются кодом на странице (общий стиль с РОК-12).
"""
import math
import random
import threading
import time
from collections import deque

import store

TICK = 0.05
W = H = 24
P_SPEED = 4.2
P_R = 0.32
WORK_S = 0.6
BOT_TIMEOUT = 2.5
BOT_GONE = 30.0
HUMAN_GONE = 20.0

FASTENERS = {"screw": "винт", "nail": "гвоздь", "bolt": "болт", "rivet": "заклёпка"}
TOOLS = {"driver": "отвёртка", "hammer": "молоток", "wrench": "ключ", "drill": "дрель"}
TOOL_FOR = {"screw": "driver", "nail": "hammer", "bolt": "wrench", "rivet": "drill"}
BONUS = {"clock": "часы (+20 с)", "heart": "сердце (+жизнь)",
         "star": "звезда (враги замерли)", "gem": "кристалл"}
ENEMIES = {
    "crab": {"name": "краб", "speed": 2.0},
    "ghost": {"name": "призрак", "speed": 1.35},
    "zap": {"name": "искра", "speed": 0.0, "period": 3.0, "radius": 1.6},
    "roller": {"name": "каток", "speed": 3.4},
}

WORLDS = [
    ("Сад Габбла", "garden", ("#13261a", "#1f3a26"), ("#2d5a34", "#3b6f41"), "#7bd389"),
    ("Завод", "factory", ("#1c1c20", "#2a2a30"), ("#4a4a52", "#5c5c66"), "#e2a13a"),
    ("Подводная база", "sea", ("#071c2e", "#0c2a44"), ("#164a6e", "#1e5c86"), "#5fd0ff"),
    ("Ледяные пещеры", "ice", ("#10243a", "#183450"), ("#6c9cc4", "#8ab4d8"), "#d8f2ff"),
    ("Небесный город", "sky", ("#2a1e3e", "#3a2a58"), ("#8a7ab0", "#a494c8"), "#ffd27d"),
    ("Тёмная лаборатория", "lab", ("#0e0e12", "#16161c"), ("#2e3a2e", "#3a4a3a"), "#9bff6b"),
]
_NAMES = [
    ["Первый винт", "Грядки", "Теплица", "Пруд", "Крабы на дорожке", "Старый сарай", "Лабиринт из живой изгороди", "Яблони", "Ночной сад", "Хранитель сада"],
    ["Проходная", "Конвейер", "Прессовый цех", "Склад болтов", "Катки", "Сборочная линия", "Электрощитовая", "Крыша завода", "Аварийная смена", "Мастер цеха"],
    ["Шлюз", "Коридоры базы", "Аквариумы", "Насосная", "Призраки глубин", "Затопленный отсек", "Батискаф", "Коралловый риф", "Чёрная вода", "Хозяин базы"],
    ["Вход в пещеру", "Сосульки", "Замёрзшее озеро", "Ледяные катки", "Эхо", "Кристальный зал", "Трещины", "Лавина", "Сердце ледника", "Страж льда"],
    ["Причал дирижаблей", "Мостики", "Ветряные башни", "Облачная площадь", "Молнии", "Висячие сады", "Часовая башня", "Гроза", "Падающий город", "Хранитель неба"],
    ["Вестибюль", "Пробирки", "Клетки", "Реактор", "Все сразу", "Тёмный коридор", "Архив", "Побег образцов", "Последняя дверь", "Профессор"],
]
_STORY = [
    "Габбл вернулся домой: сад заржавел, всё прикручено и прибито. Начни с простого — сними винты отвёрткой.",
    "Роботы-крабы завода прибили грядки гвоздями. Молоток — где-то у сарая.",
    "В теплице болты. Ключ лежит в дальнем углу, а крабы ходят по дорожкам.",
    "Пруд огорожен заклёпками. Дрель под яблоней — но смотри под ноги.",
    "Крабов стало больше. Жди, когда краб уйдёт, и проскакивай.",
    "Сарай полон крепежа всех видов. Инструменты придётся менять на ходу.",
    "Живая изгородь — настоящий лабиринт. Часы помогут успеть.",
    "Призрак завода бродит между яблонь. Он медленный, но не отстаёт.",
    "Темно. Бонус-звезда замораживает врагов — используй в узких местах.",
    "Хранитель сада — большой каток. Сними всё, пока он разгоняется.",
    "Завод, откуда пришли роботы. Болты на каждом шагу, ключей мало.",
    "По конвейеру едут катки. Переходи, когда лента пуста.",
    "Пресс бьёт током — искры по углам. Обходи свечение.",
    "Склад: сотни болтов, но ключ один. Береги время.",
    "Катки везде. Они не сворачивают — сворачивай ты.",
    "Сборочная линия: винты и заклёпки вперемешку, инструменты — на концах.",
    "Электрощитовая. Искры и призраки. Жизни тут тают быстро.",
    "На крыше ветер, крабы бегут вдвое быстрее.",
    "Аварийная смена: времени в обрез, кристаллы — лишние очки.",
    "Мастер цеха сторожит рубильник. Выключи завод — сними всё.",
    "Подводная база. Шлюз закрыт заклёпками.",
    "Коридоры узкие, призраки тянутся за тобой из каждого ответвления.",
    "Аквариумы треснули. Крабы вышли из воды.",
    "Насосная: искры в воде бьют дальше. Держись стен.",
    "Призраков много — ищи звезду.",
    "Отсек затоплен наполовину — проходов мало, враги ходят теми же.",
    "Батискаф прикручен к причалу. Сними крепёж — уплывёшь.",
    "Риф — лабиринт без тупиков. Катки носятся кругами.",
    "Ничего не видно. Слушай призраков по свечению.",
    "Хозяин базы собрал всех. Последний крепёж — в самом центре.",
    "Пещеры. На льду Габбл скользит чуть дальше — осторожно у искр.",
    "Сосульки — стены. Инструменты раскиданы далеко друг от друга.",
    "Замёрзшее озеро — огромная комната, спрятаться негде.",
    "Катки на льду разгоняются до предела.",
    "Эхо: призраки идут на звук — на шаги.",
    "Кристальный зал: кристаллы — очки, но за каждым ждёт краб.",
    "Трещины делят зал на островки — путь один, и его знают враги.",
    "Лавина сходит через 90 секунд. Это всё время, что есть.",
    "Сердце ледника — заклёпки повсюду, дрель только одна.",
    "Страж льда замирает только от звезды. Найди её первой.",
    "Небесный город держится на болтах. Сними их — но не все сразу!",
    "Мостики узкие: встретил краба — отступай к развилке.",
    "Ветряные башни: катки летят по ветру.",
    "Площадь полна народа — то есть крабов.",
    "Молнии (искры) бьют каждые две секунды.",
    "Висячие сады: снова лабиринт из изгороди, но над пропастью.",
    "Часовая башня: много часов-бонусов, но и времени мало.",
    "Гроза — искры и катки одновременно.",
    "Город падает: таймер идёт вдвое быстрее. Беги.",
    "Хранитель неба — быстрее всех. Сними крепёж по кругу.",
    "Лаборатория, где сделали роботов. Пробирки прикручены.",
    "Клетки с образцами. Призраки — это сбежавшие образцы.",
    "Реактор гудит: искры везде.",
    "Все виды врагов, все виды крепежа. Экзамен.",
    "Тёмный коридор длинный, враги в нём ходят цепочкой.",
    "Архив: крепёж в тупиках, тупики сторожат крабы.",
    "Образцы бегут: призраков больше, чем крепежа.",
    "Последняя дверь на заклёпках — дрель у самого входа, иди назад.",
    "Профессор-каток носится по залу. Финал.",
    "Габбл снял последний винт. Роботы остановились. Сад можно чинить.",
]


def level_def(n):
    i = (int(n) - 1) % 60
    w = i // 10
    k = i % 10
    name, theme, bg, floor, acc = WORLDS[w]
    base = 6 + k + w * 2                              # крепежа
    ekinds = ["crab"]
    if i >= 7:
        ekinds.append("ghost")
    if i >= 12:
        ekinds.append("zap")
    if i >= 4:
        ekinds.append("roller")
    special = {4: "crab", 14: "roller", 12: "zap", 24: "ghost", 23: "zap", 33: "roller",
               34: "ghost", 44: "zap", 46: "clock", 47: "zap", 48: "fast", 52: "zap",
               56: "ghost"}.get(i, "")
    if k == 9:
        special = "boss"
    ntools = min(4, 1 + (k + w) // 3 + (1 if i >= 5 else 0))
    return {"n": int(n), "idx": i + 1, "world": name, "world_n": w + 1, "theme": theme,
            "bg": bg, "floor": floor, "acc": acc, "name": _NAMES[w][k],
            "story": _STORY[min(i, len(_STORY) - 2)] if i < 59 else _STORY[58],
            "fasteners": base, "kinds": list(FASTENERS)[:ntools],
            "enemies": 2 + k // 2 + w, "ekinds": ekinds, "special": special,
            "time": max(60, 150 - w * 8 - k * 3) if special != "fast" else 70,
            "loop": (int(n) - 1) // 60}


_LOCK = threading.RLock()
_G = {"started": False, "paused": False, "level": 0, "map": [], "mapver": 0,
      "def": None, "start": (1.5, 1.5), "exit": (W - 2, H - 2), "exit_open": False,
      "fast": [], "tools": [], "bonus": [], "enemies": [], "players": {},
      "log": deque(maxlen=40), "tick": 0, "next_level_at": 0.0, "time_left": 0.0,
      "freeze_until": 0.0, "eid": 0, "bots": {}, "invite": {}, "invite_from": {},
      "flow": {}, "flow_ts": 0.0, "finished": 0, "loop_ts": 0.0, "chat": deque(maxlen=30),
      "dist_exit": []}

_CHAT_KINDS = ("join", "leave", "chat", "thought", "level")


def _eid():
    _G["eid"] += 1
    return _G["eid"]


def _event(p, text, kind="info"):
    _G["log"].append({"ts": time.time(), "who": (p or {}).get("name", ""), "text": text, "kind": kind})
    if p is not None:
        p["events"].append(text)
        del p["events"][:-12]
    if kind in _CHAT_KINDS:
        try:
            import gamechat
            src = "cloud"
            if p is not None:
                src = ("board:" + p["id"][4:]) if p["kind"] == "bot" else \
                    ("llm" if p["kind"] == "llm" else "human:" + p["id"])
            gamechat.post((p or {}).get("name") or "Gubble 2", text.replace("💬 ", "", 1),
                          kind=("event" if kind in ("join", "leave", "level") else kind),
                          game="Gubble 2", src=src)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# карта: лабиринт из комнат и коридоров (вид сверху)
# ---------------------------------------------------------------------------
def _solid(x, y):
    xi, yi = int(math.floor(x)), int(math.floor(y))
    if xi < 0 or yi < 0 or xi >= W or yi >= H:
        return True
    return _G["map"][yi][xi] != 0


def _bfs(src, limit=None):
    m = _G["map"]
    dist = [[-1] * W for _ in range(H)]
    q = deque()
    for (cx, cy) in src:
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
    d = level_def(n)
    rnd = random.Random(5000 + n * 104729 + int(time.time()) % 991)
    # лабиринт на сетке нечётных клеток + «комнаты» (вырубленные квадраты)
    m = [[1] * W for _ in range(H)]
    cw, ch = (W - 1) // 2, (H - 1) // 2
    seen = [[False] * cw for _ in range(ch)]
    stack = [(0, 0)]
    seen[0][0] = True
    m[1][1] = 0
    while stack:
        cx, cy = stack[-1]
        nb = [(cx + dx, cy + dy, dx, dy) for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1))
              if 0 <= cx + dx < cw and 0 <= cy + dy < ch and not seen[cy + dy][cx + dx]]
        if not nb:
            stack.pop()
            continue
        nx, ny, dx, dy = rnd.choice(nb)
        seen[ny][nx] = True
        m[2 * cy + 1 + dy][2 * cx + 1 + dx] = 0
        m[2 * ny + 1][2 * nx + 1] = 0
        stack.append((nx, ny))
    # петли и комнаты — чтобы было где разминуться с врагом
    for _ in range(10 + d["idx"] // 3):
        x, y = rnd.randint(1, W - 2), rnd.randint(1, H - 2)
        if m[y][x] and ((m[y][x - 1] == 0 and m[y][x + 1] == 0) or (m[y - 1][x] == 0 and m[y + 1][x] == 0)):
            m[y][x] = 0
    for _ in range(2 + d["world_n"] // 2):
        rx, ry = rnd.randint(1, W - 5), rnd.randint(1, H - 5)
        for y in range(ry, ry + 3):
            for x in range(rx, rx + 3):
                m[y][x] = 0
    _G["map"] = m
    free = [(x, y) for y in range(H) for x in range(W) if m[y][x] == 0]
    rnd.shuffle(free)
    sx, sy = 1, 1
    _G["start"] = (sx + 0.5, sy + 0.5)
    d0 = _bfs([(sx, sy)])
    far = max(free, key=lambda c: d0[c[1]][c[0]])
    _G["exit"] = far
    _G["dist_exit"] = _bfs([far])
    taken = {(sx, sy), far}

    def take(near_start=False):
        for c in free:
            if c in taken or d0[c[1]][c[0]] < 2:
                continue
            if near_start and d0[c[1]][c[0]] > 10:
                continue
            taken.add(c)
            return c
        return None
    fast, tools, bonus, enemies = [], [], [], []
    kinds = d["kinds"]
    for i in range(d["fasteners"]):
        c = take()
        if c:
            fast.append({"id": _eid(), "kind": kinds[i % len(kinds)], "x": c[0] + .5, "y": c[1] + .5, "work": 0.0})
    for k in kinds:                                   # по инструменту на вид, первый — близко
        c = take(near_start=(k == kinds[0]))
        if c:
            tools.append({"id": _eid(), "kind": TOOL_FOR[k], "x": c[0] + .5, "y": c[1] + .5})
    if len(kinds) >= 3:                               # запасной инструмент
        c = take()
        if c:
            tools.append({"id": _eid(), "kind": TOOL_FOR[rnd.choice(kinds)], "x": c[0] + .5, "y": c[1] + .5})
    bpool = ["gem", "gem", "clock", "heart", "star"] if d["special"] != "clock" else ["clock"] * 4 + ["gem"]
    for _ in range(2 + d["idx"] // 10):
        c = take()
        if c:
            bonus.append({"id": _eid(), "kind": rnd.choice(bpool), "x": c[0] + .5, "y": c[1] + .5})
    sp_mul = 1.0 + 0.1 * d["loop"]
    ek = list(d["ekinds"])
    if d["special"] in ENEMIES:
        ek += [d["special"]] * 3
    for i in range(d["enemies"]):
        c = take()
        if not c:
            break
        k = rnd.choice(ek)
        e = {"id": _eid(), "kind": k, "x": c[0] + .5, "y": c[1] + .5, "dir": rnd.choice([(1, 0), (-1, 0), (0, 1), (0, -1)]),
             "speed": ENEMIES[k]["speed"] * sp_mul, "t": rnd.random() * 3, "boss": False}
        enemies.append(e)
    if d["special"] == "boss":
        c = take()
        if c:
            enemies.append({"id": _eid(), "kind": "roller", "x": c[0] + .5, "y": c[1] + .5, "dir": (1, 0),
                            "speed": 4.2 * sp_mul, "t": 0.0, "boss": True})
    _G.update(fast=fast, tools=tools, bonus=bonus, enemies=enemies, level=n)
    _G["def"] = d
    _G["exit_open"] = False
    _G["time_left"] = float(d["time"])
    _G["freeze_until"] = 0.0
    _G["mapver"] += 1
    _G["flow"], _G["flow_ts"] = {}, 0.0
    _event(None, "Уровень %d «%s» (%s): крепежа %d, врагов %d, время %d с" % (
        n, d["name"], d["world"], len(fast), len(enemies), d["time"]), "level")


# ---------------------------------------------------------------------------
# игроки
# ---------------------------------------------------------------------------
def _new_player(pid, name, kind):
    sx, sy = _G["start"]
    return {"id": pid, "name": name, "kind": kind, "x": sx, "y": sy, "tool": "",
            "lives": 3, "score": 0, "removed": 0, "deaths": 0, "levels": 0,
            "dead_until": 0.0, "safe_until": time.time() + 2.0, "input": {}, "in_ts": 0.0,
            "seen_ts": time.time(), "events": [], "rw": 0.0, "best_d": None, "joined": time.time(),
            "face": (1, 0), "working": 0.0, "stuck_ref": (sx, sy, time.time()), "stuck": False}


def _spawn(p):
    sx, sy = _G["start"]
    p.update(x=sx, y=sy, dead_until=0.0, safe_until=time.time() + 2.0, best_d=None,
             stuck_ref=(sx, sy, time.time()))


def _move(o, dx, dy, r=P_R):
    nx = o["x"] + dx
    sx = r if dx > 0 else -r
    if dx and not _solid(nx + sx, o["y"] - r * .7) and not _solid(nx + sx, o["y"] + r * .7):
        o["x"] = nx
    ny = o["y"] + dy
    sy = r if dy > 0 else -r
    if dy and not _solid(o["x"] - r * .7, ny + sy) and not _solid(o["x"] + r * .7, ny + sy):
        o["y"] = ny


def _lose_life(p, why):
    if p["dead_until"] or time.time() < p["safe_until"]:
        return
    p["lives"] -= 1
    p["deaths"] += 1
    p["rw"] -= 2.0
    p["dead_until"] = time.time() + 1.5
    _event(p, "потерял жизнь (%s), осталось %d" % (why, max(0, p["lives"])), "death")
    if p["lives"] <= 0:
        p["lives"] = 3
        p["rw"] -= 3.0
        _event(p, "жизни кончились — уровень заново", "level")
        _gen_level(_G["level"])
        for q in _G["players"].values():
            _spawn(q)
            q["tool"] = ""


def _step_player(p, dt, now):
    if p["dead_until"]:
        if now >= p["dead_until"]:
            _spawn(p)
        return
    inp = p["input"] if now - p["in_ts"] < BOT_TIMEOUT else {}
    if p["kind"] == "human" and "x" in inp:
        nx, ny = float(inp["x"]), float(inp["y"])
        if math.hypot(nx - p["x"], ny - p["y"]) < P_SPEED * 0.6 and not _solid(nx, ny):
            if abs(nx - p["x"]) > 1e-3 or abs(ny - p["y"]) > 1e-3:
                p["face"] = (1 if nx > p["x"] else -1, 0) if abs(nx - p["x"]) >= abs(ny - p["y"]) \
                    else (0, 1 if ny > p["y"] else -1)
            p["x"], p["y"] = nx, ny
    else:
        dx = max(-1.0, min(1.0, float(inp.get("dx", 0) or 0)))
        dy = max(-1.0, min(1.0, float(inp.get("dy", 0) or 0)))
        ln = math.hypot(dx, dy)
        if ln > 1e-3:
            dx, dy = dx / ln, dy / ln
            p["face"] = (1 if dx > 0 else -1, 0) if abs(dx) >= abs(dy) else (0, 1 if dy > 0 else -1)
            _move(p, dx * P_SPEED * dt, dy * P_SPEED * dt)
    # инструменты (обмен)
    for t in _G["tools"]:
        if math.hypot(t["x"] - p["x"], t["y"] - p["y"]) < 0.5:
            old = p["tool"]
            if old == t["kind"]:
                continue
            p["tool"] = t["kind"]
            if old:
                t["kind"] = old
            else:
                _G["tools"].remove(t)
            p["rw"] += 0.3
            _event(p, "взял: %s" % TOOLS[p["tool"]], "item")
            break
    # крепёж
    p["working"] = 0.0
    for f in _G["fast"]:
        if math.hypot(f["x"] - p["x"], f["y"] - p["y"]) < 0.45:
            if TOOL_FOR[f["kind"]] == p["tool"]:
                f["work"] += dt
                p["working"] = f["work"] / WORK_S
                if f["work"] >= WORK_S:
                    _G["fast"].remove(f)
                    p["removed"] += 1
                    p["score"] += 50
                    p["rw"] += 1.0
                    _event(p, "снял: %s (осталось %d)" % (FASTENERS[f["kind"]], len(_G["fast"])), "item")
                    if not _G["fast"] and not _G["exit_open"]:
                        _G["exit_open"] = True
                        _event(None, "✨ Весь крепёж снят — выход открыт!", "level")
            elif now - p.get("hint_ts", 0) > 5:
                p["hint_ts"] = now
                p["events"].append("нужен инструмент: %s" % TOOLS[TOOL_FOR[f["kind"]]])
            break
    # бонусы
    for b in list(_G["bonus"]):
        if math.hypot(b["x"] - p["x"], b["y"] - p["y"]) < 0.5:
            _G["bonus"].remove(b)
            k = b["kind"]
            if k == "clock":
                _G["time_left"] += 20
            elif k == "heart":
                p["lives"] = min(6, p["lives"] + 1)
            elif k == "star":
                _G["freeze_until"] = now + 5.0
            p["score"] += 25 if k != "gem" else 100
            p["rw"] += 0.4
            _event(p, "бонус: %s" % BONUS[k], "item")
    # продвижение к цели (награда боту)
    tgt = _objective(p)
    if tgt:
        dd = math.hypot(tgt[0] - p["x"], tgt[1] - p["y"])
        if p["best_d"] is None or dd < p["best_d"] - 0.5:
            if p["best_d"] is not None:
                p["rw"] += 0.03
            p["best_d"] = dd
    rx, ry, rt = p["stuck_ref"]
    if now - rt > 1.5:
        p["stuck"] = math.hypot(p["x"] - rx, p["y"] - ry) < 0.3 and p["kind"] in ("bot", "llm")
        if p["stuck"]:
            p["rw"] -= 0.05
        p["stuck_ref"] = (p["x"], p["y"], now)
    # враги
    if now >= p["safe_until"]:
        for e in _G["enemies"]:
            if e["kind"] == "zap":
                continue
            if math.hypot(e["x"] - p["x"], e["y"] - p["y"]) < 0.55:
                _lose_life(p, ENEMIES[e["kind"]]["name"])
                return
    # выход
    ex, ey = _G["exit"]
    if _G["exit_open"] and (int(p["x"]), int(p["y"])) == (ex, ey) and not _G["next_level_at"]:
        _G["next_level_at"] = now + 2.5
        bonus = int(_G["time_left"]) * 5
        for q in _G["players"].values():
            q["rw"] += 5.0 if q is p else 2.5
            q["levels"] += 1
            q["score"] += bonus
        d = _G["def"] or {}
        _event(p, "дошёл до выхода — уровень %d «%s» пройден (+%d за время)" % (_G["level"], d.get("name", ""), bonus), "level")
        if d.get("idx") == 60:
            _G["finished"] += 1
            _event(None, "🏆 GUBBLE 2 ПРОЙДЕНА (раз %d)! %s" % (_G["finished"], _STORY[-1]), "level")


def _objective(p):
    """Куда идти сейчас: крепёж под мой инструмент -> нужный инструмент -> выход."""
    fast = _G["fast"]
    if not fast:
        ex, ey = _G["exit"]
        return (ex + .5, ey + .5, "exit", "")
    mine = [f for f in fast if TOOL_FOR[f["kind"]] == p["tool"]]
    if mine:
        f = min(mine, key=lambda f: math.hypot(f["x"] - p["x"], f["y"] - p["y"]))
        return (f["x"], f["y"], "fast", f["kind"])
    need = {TOOL_FOR[f["kind"]] for f in fast}
    tools = [t for t in _G["tools"] if t["kind"] in need]
    if tools:
        t = min(tools, key=lambda t: math.hypot(t["x"] - p["x"], t["y"] - p["y"]))
        return (t["x"], t["y"], "tool", t["kind"])
    return None


# ---------------------------------------------------------------------------
# враги
# ---------------------------------------------------------------------------
def _free_dirs(x, y):
    out = []
    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        if not _solid(x + dx * 0.75, y + dy * 0.75):
            out.append((dx, dy))
    return out


def _step_enemies(dt, now):
    frozen = now < _G["freeze_until"]
    alive = [p for p in _G["players"].values() if not p["dead_until"]]
    if now - _G["flow_ts"] > 0.5 and alive:
        _G["flow_ts"] = now
        _G["flow"] = {p["id"]: _bfs([(int(p["x"]), int(p["y"]))], limit=40) for p in alive}
    for e in _G["enemies"]:
        k = e["kind"]
        if k == "zap":
            e["t"] += dt
            per = ENEMIES["zap"]["period"]
            if e["t"] >= per:
                e["t"] = 0.0
                e["flash"] = now
                for p in alive:
                    if math.hypot(p["x"] - e["x"], p["y"] - e["y"]) < ENEMIES["zap"]["radius"]:
                        _lose_life(p, "искра")
            continue
        if frozen:
            continue
        sp = e["speed"] * dt
        dx, dy = e["dir"]
        if k == "ghost" and alive:
            tgt = min(alive, key=lambda p: math.hypot(p["x"] - e["x"], p["y"] - e["y"]))
            fl = _G["flow"].get(tgt["id"])
            cx, cy = int(e["x"]), int(e["y"])
            best = None
            if fl and fl[cy][cx] > 0:
                for ddx, ddy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    nx, ny = cx + ddx, cy + ddy
                    if 0 <= nx < W and 0 <= ny < H and 0 <= fl[ny][nx] < fl[cy][cx]:
                        best = (nx + .5, ny + .5)
                        break
            if best:
                a = math.atan2(best[1] - e["y"], best[0] - e["x"])
                e["x"] += math.cos(a) * sp
                e["y"] += math.sin(a) * sp
                e["dir"] = (1 if math.cos(a) > 0.3 else -1 if math.cos(a) < -0.3 else 0,
                            1 if math.sin(a) > 0.3 else -1 if math.sin(a) < -0.3 else 0)
            continue
        # краб / каток: едут прямо, у стены — поворот
        ox, oy = e["x"], e["y"]
        _move(e, dx * sp, dy * sp, 0.3)
        moved = math.hypot(e["x"] - ox, e["y"] - oy) > sp * 0.3
        at_center = abs((e["x"] % 1) - 0.5) < 0.08 and abs((e["y"] % 1) - 0.5) < 0.08
        if not moved or (k == "crab" and at_center and random.random() < 0.08 * dt * 20):
            dirs = _free_dirs(e["x"], e["y"])
            if k == "crab":
                back = (-dx, -dy)
                dirs = [d for d in dirs if d != (dx, dy)] or dirs
                if not moved and back in dirs and len(dirs) > 1:
                    dirs.remove(back)
                    dirs.append(back)                      # назад — в последнюю очередь
                e["dir"] = dirs[0] if not moved and len(dirs) == 1 else random.choice(dirs) if dirs else (-dx, -dy)
            else:
                dirs = [d for d in dirs if d != (dx, dy)]
                e["dir"] = random.choice(dirs) if dirs else (-dx, -dy)
            e["x"], e["y"] = int(e["x"]) + .5, int(e["y"]) + .5


def _step(dt):
    now = time.time()
    for pid, p in list(_G["players"].items()):
        gone = BOT_GONE if p["kind"] == "bot" else HUMAN_GONE
        if now - p["seen_ts"] > gone:
            _event(p, "вышел из игры (нет связи)", "leave")
            del _G["players"][pid]
    if not _G["players"] or _G["paused"]:
        return
    _G["tick"] += 1
    d = _G["def"] or {}
    _G["time_left"] -= dt * (2.0 if d.get("special") == "fast" else 1.0)
    if _G["time_left"] <= 0:
        _G["time_left"] = max(30.0, float(d.get("time", 100)) / 2)
        for p in _G["players"].values():
            if not p["dead_until"]:
                p["safe_until"] = 0.0
                _lose_life(p, "вышло время")
                break
    for p in list(_G["players"].values()):
        _step_player(p, dt, now)
    _step_enemies(dt, now)
    if _G["next_level_at"] and now >= _G["next_level_at"]:
        _G["next_level_at"] = 0.0
        _gen_level(_G["level"] + 1)
        for p in _G["players"].values():
            _spawn(p)
            p["tool"] = ""


def _loop():
    last = time.time()
    while True:
        time.sleep(TICK)
        now = time.time()
        dt = min(0.1, now - last)
        last = now
        try:
            with _LOCK:
                _G["loop_ts"] = now
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
    threading.Thread(target=_loop, daemon=True, name="gubble").start()


def available():
    """Арбитр игр: Gubble 2 готова принять LLM."""
    return (not _G["paused"]) and (not _G["started"] or time.time() - _G["loop_ts"] < 10)


# ---------------------------------------------------------------------------
# наблюдение для ботов (плата, LLM)
# ---------------------------------------------------------------------------
def _path_dir(p, tx, ty):
    """Направление первого шага по кратчайшему пути к (tx,ty). -> (dx,dy,steps)"""
    sx, sy = int(p["x"]), int(p["y"])
    gx, gy = int(tx), int(ty)
    if (sx, sy) == (gx, gy):
        return (tx - p["x"], ty - p["y"], 0)
    key = (gx, gy)
    cache = _G.setdefault("pathcache", {})
    ent = cache.get(key)
    if not ent or time.time() - ent[0] > 2.0 or ent[2] != _G["mapver"]:
        ent = (time.time(), _bfs([(gx, gy)]), _G["mapver"])
        cache[key] = ent
        if len(cache) > 40:
            cache.pop(next(iter(cache)))
    dist = ent[1]
    if dist[sy][sx] < 0:
        return (0, 0, -1)
    best = None
    for ddx, ddy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        nx, ny = sx + ddx, sy + ddy
        if 0 <= nx < W and 0 <= ny < H and 0 <= dist[ny][nx] < dist[sy][sx]:
            best = (nx, ny)
            break
    if not best:
        return (0, 0, dist[sy][sx])
    return (best[0] + .5 - p["x"], best[1] + .5 - p["y"], dist[sy][sx])


def observe(p):
    x, y = p["x"], p["y"]
    walls = [min(4.0, _wall_dist(x, y, dx, dy)) / 4.0 for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1))]
    obj = _objective(p)
    goal = None
    if obj:
        gdx, gdy, steps = _path_dir(p, obj[0], obj[1])
        ln = math.hypot(gdx, gdy) or 1.0
        goal = {"dx": round(gdx / ln, 3), "dy": round(gdy / ln, 3), "steps": steps,
                "what": obj[2], "kind": obj[3]}
    enemy = None
    for e in _G["enemies"]:
        dd = math.hypot(e["x"] - x, e["y"] - y)
        if dd < 6 and (enemy is None or dd < enemy["d"]):
            enemy = {"dx": round((e["x"] - x) / (dd or 1), 3), "dy": round((e["y"] - y) / (dd or 1), 3),
                     "d": round(dd, 2), "kind": e["kind"]}
    bon = None
    for b in _G["bonus"]:
        dd = math.hypot(b["x"] - x, b["y"] - y)
        if dd < 7 and (bon is None or dd < bon["d"]):
            bdx, bdy, st = _path_dir(p, b["x"], b["y"])
            ln = math.hypot(bdx, bdy) or 1.0
            bon = {"dx": round(bdx / ln, 3), "dy": round(bdy / ln, 3), "d": round(dd, 2), "kind": b["kind"], "steps": st}
    return {"walls": [round(w, 3) for w in walls], "goal": goal, "enemy": enemy, "bonus": bon,
            "tool": p["tool"], "lives": p["lives"], "time": int(_G["time_left"]),
            "left": len(_G["fast"]), "exit_open": _G["exit_open"], "frozen": time.time() < _G["freeze_until"],
            "working": round(p["working"], 2), "stuck": bool(p["stuck"]), "dead": bool(p["dead_until"]),
            "level": _G["level"], "score": p["score"]}


def _wall_dist(x, y, dx, dy, maxd=4.0):
    d = 0.0
    while d < maxd:
        d += 0.25
        if _solid(x + dx * d, y + dy * d):
            return d
    return maxd


# ---------------------------------------------------------------------------
# API: плата, человек, панель
# ---------------------------------------------------------------------------
def _bot_pid(board):
    return "bot:" + str(board or "board")[:40]


def bot_call(body):
    ensure()
    board = str(body.get("board") or "board")[:40]
    want = body.get("want") or {}
    act = body.get("act") or {}
    pid = _bot_pid(board)
    now = time.time()
    with _LOCK:
        info = _G["bots"].setdefault(board, {})
        info.update(ts=now, desire=want.get("desire"), why=str(want.get("why") or "")[:200],
                    join=bool(want.get("join")))
        p = _G["players"].get(pid)
        inv = bool(_G["invite"].get(board) or _G["invite"].get("*"))
        if not want.get("join"):
            if p:
                _event(p, "мозг платы вышел из игры: %s" % (info["why"] or "не хочет"), "leave")
                del _G["players"][pid]
            return {"ok": True, "playing": False, "invite": inv}
        if not p:
            p = _new_player(pid, "мозг %s" % board, "bot")
            _G["players"][pid] = p
            _event(p, "мозг платы пришёл играть в Gubble 2: %s" % (info["why"] or "захотел"), "join")
        p["seen_ts"] = now
        if isinstance(act, dict):
            p["input"] = {"dx": act.get("dx", 0), "dy": act.get("dy", 0)}
            p["in_ts"] = now
        obs = observe(p)
        rw, ev = round(p["rw"], 3), p["events"][:]
        p["rw"], p["events"] = 0.0, []
        if body.get("say"):
            saved, p["events"] = p["events"], []
            try:
                _event(p, "💭 " + str(body.get("say"))[:160], "thought")
            finally:
                p["events"] = saved
        return {"ok": True, "playing": True, "obs": obs, "reward": rw, "events": ev,
                "level": _G["level"], "paused": _G["paused"], "invite": inv, "llm": _LLM["on"]}


def human_input(sid, body):
    ensure()
    cid = "".join(ch for ch in str(body.get("cid") or "") if ch.isalnum())[:8]
    pid = "me:" + (str(sid or "owner")[:8]) + (":" + cid if cid else "")
    now = time.time()
    with _LOCK:
        p = _G["players"].get(pid)
        if body.get("leave"):
            if p:
                _event(p, "человек вышел из игры", "leave")
                del _G["players"][pid]
            return {"ok": True, "playing": False}
        name = str(body.get("name") or "").strip()[:20]
        if not p:
            p = _new_player(pid, name or "игрок %d" % (1 + sum(1 for q in _G["players"].values() if q["kind"] == "human")), "human")
            _G["players"][pid] = p
            _event(p, "человек вошёл в игру", "join")
        elif name and name != p["name"]:
            p["name"] = name
        p["seen_ts"] = now
        say = str(body.get("say") or "").strip()[:200]
        if say:
            _G["chat"].append({"ts": now, "who": p["name"], "text": say})
            _event(p, "💬 " + say, "chat")
        inp = {}
        for k in ("x", "y"):
            if k in body:
                try:
                    inp[k] = float(body[k])
                except (TypeError, ValueError):
                    pass
        if p["dead_until"]:
            inp = {}
        p["input"], p["in_ts"] = inp, now
        return {"ok": True, "playing": True, "id": pid,
                "me": {"x": p["x"], "y": p["y"], "lives": p["lives"], "tool": p["tool"],
                       "dead": bool(p["dead_until"])}}


def control(body):
    ensure()
    act = str(body.get("action") or "")
    who = str(body.get("board") or "*")[:40]
    with _LOCK:
        if act == "pause":
            _G["paused"] = not _G["paused"]
            _event(None, "пауза" if _G["paused"] else "игра продолжается")
        elif act == "restart":
            _gen_level(1)
            for p in _G["players"].values():
                p.update(score=0, removed=0, deaths=0, levels=0, lives=3, tool="")
                _spawn(p)
        elif act == "next":
            _gen_level(_G["level"] + 1)
            for p in _G["players"].values():
                _spawn(p)
                p["tool"] = ""
        elif act == "level":
            try:
                n = max(1, min(180, int(body.get("n") or 1)))
            except (TypeError, ValueError):
                n = 1
            _gen_level(n)
            for p in _G["players"].values():
                _spawn(p)
                p["tool"] = ""
        elif act == "invite":
            _G["invite"][who] = True
            _G["invite_from"][who] = "владелец"
            _event(None, "владелец позвал мозг поиграть в Gubble 2")
        elif act == "uninvite":
            _G["invite"].pop(who, None)
            _G["invite_from"].pop(who, None)
        elif act == "llm_join":
            import games
            return games.llm_play("gubble", why="владелец позвал кнопкой", auto=False)
        elif act == "llm_leave":
            return llm_leave()
        else:
            return {"ok": False, "error": "неизвестное действие"}
        return {"ok": True}


def state(mapver=0):
    ensure()
    now = time.time()
    with _LOCK:
        d = _G["def"] or level_def(1)
        try:
            import games
            gst = games.status()
        except Exception:
            gst = {}
        out = {"ok": True, "level": _G["level"], "mapver": _G["mapver"], "paused": _G["paused"],
               "tick": _G["tick"], "now": now, "exit": _G["exit"], "exit_open": _G["exit_open"],
               "time_left": int(max(0, _G["time_left"])), "frozen": max(0.0, _G["freeze_until"] - now),
               "total": 60, "finished": _G["finished"],
               "def": {k: d[k] for k in ("name", "story", "world", "world_n", "theme", "bg", "floor", "acc", "idx", "loop", "time")},
               "players": [{"id": p["id"], "name": p["name"], "kind": p["kind"], "x": round(p["x"], 3), "y": round(p["y"], 3),
                            "tool": p["tool"], "lives": p["lives"], "score": p["score"], "removed": p["removed"],
                            "deaths": p["deaths"], "levels": p["levels"], "dead": bool(p["dead_until"]),
                            "safe": now < p["safe_until"], "face": p["face"], "working": round(p["working"], 2)}
                           for p in _G["players"].values()],
               "fast": [{"id": f["id"], "kind": f["kind"], "x": f["x"], "y": f["y"], "work": round(f["work"] / WORK_S, 2)} for f in _G["fast"]],
               "tools": [{"id": t["id"], "kind": t["kind"], "x": t["x"], "y": t["y"]} for t in _G["tools"]],
               "bonus": [{"id": b["id"], "kind": b["kind"], "x": b["x"], "y": b["y"]} for b in _G["bonus"]],
               "enemies": [{"id": e["id"], "kind": e["kind"], "x": round(e["x"], 3), "y": round(e["y"], 3),
                            "dir": e["dir"], "boss": e["boss"], "t": round(e["t"], 2),
                            "flash": now - e.get("flash", 0) < 0.3} for e in _G["enemies"]],
               "log": [dict(e) for e in list(_G["log"])[-25:]],
               "chat": [dict(c) for c in list(_G["chat"])[-12:]],
               "bots": {b: {"desire": v.get("desire"), "why": v.get("why"), "join": v.get("join"),
                            "age": int(now - v.get("ts", 0))} for b, v in _G["bots"].items()},
               "invite": sorted(_G["invite"].keys()), "llm": llm_status(), "games": gst}
        if int(mapver or 0) != _G["mapver"]:
            out["map"] = ["".join(str(c) for c in row) for row in _G["map"]]
        return out


# ---------------------------------------------------------------------------
# своя LLM — игрок (тактика по описанию ситуации + опыт; рефлексы ведут)
# ---------------------------------------------------------------------------
LLM_PID = "llm:cloud"
TACTIC_EVERY = 1.2
LLM_TICK = 0.1
TACTICS = {
    "работа": "Иду к крепежу и снимаю его своим инструментом.",
    "инструмент": "Иду за нужным инструментом.",
    "бонус": "Подбираю бонус рядом.",
    "бегство": "Убегаю от врага подальше.",
    "выход": "Иду к открытому выходу.",
    "ожидание": "Стою и жду, пока враг пройдёт мимо.",
}
_LLM = {"on": False, "tactic": "работа", "t_ts": 0.0, "q": {}, "n": {}, "rw_acc": 0.0,
        "src": "", "said": 0.0, "loaded": False, "decisions": 0, "arb_ts": 0.0, "why": ""}


def _q_load():
    if _LLM["loaded"]:
        return
    _LLM["loaded"] = True
    try:
        d = store.load_json("gubble_llm.json", {}) or {}
        _LLM["q"] = {k: float(v) for k, v in (d.get("q") or {}).items() if k in TACTICS}
        _LLM["n"] = {k: int(v) for k, v in (d.get("n") or {}).items() if k in TACTICS}
    except Exception:
        pass


def _q_save():
    try:
        store.save_json("gubble_llm.json", {"q": _LLM["q"], "n": _LLM["n"]})
    except Exception:
        pass


def _situation(o):
    parts = ["Я играю в Gubble 2."]
    g = o.get("goal")
    if g:
        what = {"fast": "крепёж (%s)" % FASTENERS.get(g["kind"], ""), "tool": "инструмент (%s)" % TOOLS.get(g["kind"], ""),
                "exit": "выход"}.get(g["what"], "цель")
        parts.append("Моя цель — %s, до неё %d шагов." % (what, g["steps"]))
    parts.append("У меня в руках %s." % (TOOLS.get(o["tool"], "ничего")))
    e = o.get("enemy")
    if e:
        parts.append("Рядом враг: %s в %.0f клетках." % (ENEMIES[e["kind"]]["name"], e["d"]))
    else:
        parts.append("Врагов рядом нет.")
    parts.append("Жизней %d, времени %d секунд, крепежа осталось %d." % (o["lives"], o["time"], o["left"]))
    if o.get("bonus"):
        parts.append("Недалеко бонус: %s." % BONUS.get(o["bonus"]["kind"], ""))
    if o.get("exit_open"):
        parts.append("Выход открыт.")
    if o.get("stuck"):
        parts.append("Я застрял.")
    return " ".join(parts)


def _allowed(o):
    ok = []
    g = o.get("goal")
    if g and g["what"] == "fast":
        ok.append("работа")
    if g and g["what"] == "tool":
        ok.append("инструмент")
    if o.get("exit_open"):
        ok.append("выход")
    if o.get("bonus"):
        ok.append("бонус")
    if o.get("enemy"):
        ok += ["бегство", "ожидание"]
    return ok or ["ожидание"]


def _prior(t, o):
    e = o.get("enemy")
    near = e and e["d"] < 2.2 and e["kind"] != "zap" and not o.get("frozen")
    return {"работа": 0.6 if not near else -0.3, "инструмент": 0.6 if not near else -0.3,
            "выход": 0.9 if o.get("exit_open") else 0.0,
            "бонус": 0.5 if (o.get("bonus") and (o["bonus"]["kind"] != "gem" or o["time"] > 40)) else 0.1,
            "бегство": 0.9 if near else -0.4, "ожидание": 0.2 if (e and 2.2 <= e["d"] < 3.5) else -0.3}.get(t, 0.0)


def _choose(o):
    _q_load()
    cands = _allowed(o)
    sit = _situation(o)
    ls, why = {}, ""
    try:
        import tinyllm
        lk = getattr(tinyllm, "_LOCK", None)
        if lk is not None and lk.acquire(blocking=False):
            try:
                m = tinyllm.model()
                if m.ready and m.steps:
                    for t in cands:
                        v = m.nll(sit + " ", TACTICS[t])
                        if v is not None:
                            ls[t] = -float(v)
                else:
                    why = "LLM не обучена"
            finally:
                lk.release()
        elif lk is None:
            why = "нет numpy"
        else:
            why = "LLM занята обучением"
    except Exception as e:
        why = str(e)[:60]
    if ls:
        mx, mn = max(ls.values()), min(ls.values())
        span = (mx - mn) or 1.0
        ls = {k: (v - mn) / span for k, v in ls.items()}
    best, bs = cands[0], -1e9
    for t in cands:
        n = _LLM["n"].get(t, 0)
        sc = _LLM["q"].get(t, 0.0) + _prior(t, o) + 0.5 * ls.get(t, 0.0) + 0.25 / math.sqrt(1 + n) + random.random() * 0.05
        if sc > bs:
            best, bs = t, sc
    _LLM["src"] = "LLM + опыт" if ls else "опыт игры (%s)" % why
    return best, sit


def _reflex(t, o):
    g, e, b = o.get("goal"), o.get("enemy"), o.get("bonus")
    dx = dy = 0.0
    if t in ("работа", "инструмент", "выход") and g:
        dx, dy = g["dx"], g["dy"]
    elif t == "бонус" and b:
        dx, dy = b["dx"], b["dy"]
    elif t == "бегство" and e:
        dx, dy = -e["dx"], -e["dy"]
        w = o["walls"]                        # к стене не бежим
        if (dx > 0 and w[0] < 0.2) or (dx < 0 and w[1] < 0.2):
            dx, dy = 0.0, (1.0 if w[2] > w[3] else -1.0)
        elif (dy > 0 and w[2] < 0.2) or (dy < 0 and w[3] < 0.2):
            dx, dy = (1.0 if w[0] > w[1] else -1.0), 0.0
    elif t == "ожидание":
        dx = dy = 0.0
    if o.get("stuck") and t != "ожидание":
        w = o["walls"]
        i = max(range(4), key=lambda j: w[j])
        dx, dy = ((1, 0), (-1, 0), (0, 1), (0, -1))[i]
    return {"dx": dx, "dy": dy}


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
            if now - _LLM["arb_ts"] > 3.0:
                _LLM["arb_ts"] = now
                try:
                    import games
                    games.tick()
                except Exception:
                    pass
                if not _LLM["on"]:
                    break
            if dead:
                continue
            if now - _LLM["t_ts"] > TACTIC_EVERY:
                old = _LLM["tactic"]
                if _LLM["t_ts"]:
                    n = _LLM["n"].get(old, 0) + 1
                    _LLM["n"][old] = n
                    q = _LLM["q"].get(old, 0.0)
                    _LLM["q"][old] = round(q + (_LLM["rw_acc"] - q) / min(n, 50), 4)
                _LLM["rw_acc"] = 0.0
                t, _sit = _choose(o)
                _LLM["tactic"], _LLM["t_ts"] = t, now
                _LLM["decisions"] += 1
                if t != old or now - _LLM["said"] > 15:
                    _LLM["said"] = now
                    with _LOCK:
                        _event(_G["players"].get(LLM_PID), "💭 LLM: %s (%s)" % (TACTICS[t], _LLM["src"]), "thought")
                if _LLM["decisions"] % 20 == 0:
                    _q_save()
            inp = _reflex(_LLM["tactic"], o)
            with _LOCK:
                p = _G["players"].get(LLM_PID)
                if p:
                    p["input"], p["in_ts"] = inp, time.time()
        except Exception:
            import traceback
            traceback.print_exc()
            time.sleep(1.0)
    _q_save()


def llm_join(why="", auto=False):
    ensure()
    try:
        import games
        games.llm_claim("gubble", why=why, auto=auto)
    except Exception:
        pass
    with _LOCK:
        _LLM["why"] = why
        if LLM_PID not in _G["players"]:
            p = _new_player(LLM_PID, "своя LLM", "llm")
            _G["players"][LLM_PID] = p
            _event(p, "своя LLM пришла играть в Gubble 2" + (": " + why if why else ""), "join")
        if not _LLM["on"]:
            _LLM["on"] = True
            _LLM["t_ts"] = 0.0
            threading.Thread(target=_llm_loop, daemon=True, name="gubble-llm").start()
    return {"ok": True, "llm": True}


def llm_leave(by_arbiter=False):
    with _LOCK:
        p = _G["players"].pop(LLM_PID, None)
        _LLM["on"] = False
        if p:
            _event(None, "своя LLM вышла из Gubble 2" + (" — ушла в РОК-12" if by_arbiter else ""), "leave")
    if not by_arbiter:
        try:
            import games
            games.llm_release("gubble")
        except Exception:
            pass
    return {"ok": True, "llm": False}


def llm_status():
    _q_load()
    return {"on": _LLM["on"], "tactic": _LLM["tactic"], "src": _LLM["src"], "why": _LLM["why"],
            "decisions": _LLM["decisions"], "q": {k: round(v, 2) for k, v in _LLM["q"].items()}}


def info_for_board(board):
    with _LOCK:
        return {"on": True, "invite": bool(_G["invite"].get(board) or _G["invite"].get("*")),
                "level": _G["level"] if _G["started"] else 0, "llm": _LLM["on"]}
