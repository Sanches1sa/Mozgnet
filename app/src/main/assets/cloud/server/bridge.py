# -*- coding: utf-8 -*-
# ===========================================================================
# bridge.py — МОСТ К ПЛАТЕ: облако САМО подключается к мозгу по HTTP
# ---------------------------------------------------------------------------
# Плата neuro_12 слушает свой веб-хост на 127.0.0.1:7000: там её панель и
# собственный API (/api/state, /api/send, /api/messages). Обычно плата сама
# регистрируется в облаке и работает через мозг-почту. Мост работает и БЕЗ
# регистрации: облако раз в 15 секунд опрашивает плату, показывает её живой
# статус и последнюю мысль в панели, а в чате может задать ей вопрос напрямую
# (POST /api/send) — плата думает САМА, зонной сетью, без LLM, и отвечает.
# Плата недоступна? Мост честно покажет «недоступна» и зачем это чинить.
# ===========================================================================
import json
import threading
import time
import urllib.parse
import urllib.request

import store

DEFAULT_URL = "http://127.0.0.1:7000"
POLL_SEC = 15.0

STATE = {"online": None, "last_ok": 0.0, "last_err": "", "state": {},
         "ts": 0.0, "polls": 0, "fails": 0, "diagnosis": None,
         "tried": []}
_LOCK = threading.Lock()
_SAY_TS = [0.0]                              # последняя мысль, ушедшая в ленту
_REG_TS = [0.0]                              # последняя постановка в реестр
# v3.27: РАБОЧИЙ адрес платы, найденный мостом сам (может отличаться от
# настройки: 127.0.0.1:7000 — это часто проброс adb из App Lab, который
# «висит» после отключения USB, а плата живёт в Wi-Fi на своём IP)
_ACTIVE = [""]


# ------------------------------- настройки ----------------------------------
def url():
    """Адрес из настроек (bridge_url; пусто = 127.0.0.1:7000)."""
    u = str(store.settings().get("bridge_url") or "").strip()
    return (u or DEFAULT_URL).rstrip("/")


def active_url():
    """Адрес, по которому мост сейчас реально говорит с платой."""
    return _ACTIVE[0] or url()


def enabled():
    return bool(store.settings().get("bridge_on", True))


# ------------------------------- HTTP ---------------------------------------
# v3.27: плата — в локальной сети, системный прокси/VPN ей не нужен (через
# прокси запросы к 192.168.x.x и 127.0.0.1 молча умирают) — ходим напрямую
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _get(base, path, timeout=3.0):
    req = urllib.request.Request(
        base + path, headers={"User-Agent": "neuro12-cloud-bridge/3.4"})
    with _OPENER.open(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def _post(base, path, obj, timeout=6.0):
    data = json.dumps(obj).encode("utf-8")
    req = urllib.request.Request(
        base + path, data=data, method="POST",
        headers={"Content-Type": "application/json",
                 "User-Agent": "neuro12-cloud-bridge/3.4"})
    with _OPENER.open(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def _is_loopback(host):
    h = str(host or "").strip().lower()
    return (not h or h.startswith("127.") or h in ("::1", "localhost")
            or h.startswith("::ffff:127."))


def _mail_boards(max_age=900.0):
    """Платы, которые недавно ходили в облако по мозг-почте, с их IP
    (req.client_ip): [(id, ip, age_sec)], свежие первыми. По ним мост
    знает, где плата на самом деле, даже если bridge_url неверный."""
    return [(b, ip, age) for b, ip, age, _u in _mail_boards_full(max_age)]


def _mail_boards_full(max_age=900.0):
    """[(id, ip, age, url)] — url: адрес, который плата САМА сообщила при
    регистрации (POST /api/brains/register, url=http://<её IP в LAN>:7000).
    v3.27: это главный адрес для моста — IP запроса (ip) бывает адресом
    роутера/NAT/Docker, а не платы, и тогда «Connection refused»."""
    out = []
    now = time.time()
    try:
        for bid, b in (store.boards() or {}).items():
            ip = str(b.get("ip") or "").strip()
            age = now - float(b.get("last_seen") or 0)
            u = str(b.get("url") or "").strip().rstrip("/")
            host = ""
            try:
                host = urllib.parse.urlparse(u).hostname or ""
            except Exception:
                u = ""
            if b.get("via_bridge") or _is_loopback(host):
                u = ""                         # это не адрес платы в сети
            if age < max_age and ((ip and not _is_loopback(ip)) or u):
                out.append((bid, ip, age, u))
    except Exception:
        pass
    out.sort(key=lambda x: x[2])
    return out


def _candidates():
    """Куда стучаться: рабочий адрес -> адрес из настроек -> адрес, который
    сообщила САМА плата -> IP запросов мозг-почты (порт из настроек и 7000)."""
    cfg = url()
    port = 7000
    try:
        port = urllib.parse.urlparse(cfg).port or 7000
    except Exception:
        pass
    out = []
    for u in (_ACTIVE[0], cfg):
        if u and u not in out:
            out.append(u)
    for _bid, ip, _age, reg in _mail_boards_full():
        if reg and reg not in out:
            out.append(reg)
        if not ip or _is_loopback(ip):
            continue
        for p in (port, 7000):
            u = "http://%s:%d" % (ip, p)
            if u not in out:
                out.append(u)
    return out[:8]


def _private(ip):
    """Адрес локальной сети (10/8, 172.16/12, 192.168/16, 100.64/10)."""
    try:
        a = [int(x) for x in str(ip).split(".")]
        if len(a) != 4:
            return False
        return a[0] == 10 or (a[0] == 172 and 16 <= a[1] <= 31) or \
            (a[0] == 192 and a[1] == 168) or (a[0] == 100 and 64 <= a[1] <= 127)
    except Exception:
        return False


def topology():
    """v3.27: где плата и где сервер — в одной локальной сети или нет.
    Возврат {board, board_lan, seen_from, server_ips, same_lan, verdict}."""
    mail = _mail_boards_full()
    if not mail:
        return {"verdict": "no-mail"}
    bid, ip, age, reg = mail[0]
    lan = ""
    try:
        lan = urllib.parse.urlparse(reg).hostname or "" if reg else ""
    except Exception:
        lan = ""
    mine = _lan_ips()
    pref = lambda a: ".".join(str(a).split(".")[:3])
    same = bool(lan) and any(pref(m) == pref(lan) for m in mine)
    if ip and not _private(ip) and not _is_loopback(ip):
        verdict = "nat"               # плата пришла с публичного адреса
    elif lan and not same:
        verdict = "other-lan"         # у сервера нет адреса в сети платы
    else:
        verdict = "same-lan"
    return {"board": bid, "board_lan": lan, "seen_from": ip,
            "server_ips": mine, "same_lan": same, "verdict": verdict,
            "age": round(age, 1)}


# --------------------------- диагноз ошибки ---------------------------------
def diagnose(err, base=None):
    """v3.23.1: текст ошибки -> человеческий диагноз {cause, why, fix}.
    Зачем: панель показывает только «недоступна», а владелец не понимает,
    ЧЕГО именно не хватает. Здесь честно раскладываем по причинам.
    v3.27: + обрыв проброса adb (App Lab) и подсказка, где плата видна по
    мозг-почте («плата в сети, а мост не работает»)."""
    d = _diagnose(err, base or url())
    topo = topology()
    if topo.get("verdict") in (None, "no-mail"):
        return d
    d["mail"] = {"board": topo["board"], "ip": topo.get("seen_from"),
                 "lan": topo.get("board_lan"), "age": topo.get("age")}
    d["topology"] = topo
    where = topo.get("board_lan") or topo.get("seen_from")
    head = ("Плата «%s» В СЕТИ — по мозг-почте (%d с назад, её адрес в "
            "локальной сети: %s, запрос пришёл с %s). Ответы, письма, "
            "память, песочница и тензор (через /api/sandbox/run) работают "
            "по мозг-почте — мост для них не нужен."
            % (topo["board"], int(topo.get("age") or 0),
               topo.get("board_lan") or "плата его не сообщила",
               topo.get("seen_from") or "?"))
    if topo["verdict"] == "nat":
        d["cause"] = ("сервер и плата в РАЗНЫХ сетях: плата выходит к серверу "
                      "через интернет (адрес %s — внешний)"
                      % topo.get("seen_from"))
        d["why"] = ("сервер не может сам подключиться к плате за роутером "
                    "(NAT) — связь только в одну сторону: плата → сервер. " +
                    head)
        d["fix"] = ("мост здесь не нужен — всё идёт мозг-почтой. Если нужен "
                    "именно мост: проброс порта 7000 на роутере к %s или VPN "
                    "(WireGuard/Tailscale) между сервером и платой, затем "
                    "впиши адрес выше" % (topo.get("board_lan") or "плате"))
    elif topo["verdict"] == "other-lan":
        d["cause"] = ("у сервера нет адреса в сети платы (%s): адреса "
                      "сервера %s" % (where, ", ".join(topo.get("server_ips")
                                                     or []) or "неизвестны"))
        d["why"] = ("сервер и плата в разных подсетях/сетях — сервер до "
                    "платы не достаёт. " + head)
        d["fix"] = ("мост не обязателен — всё идёт мозг-почтой. Для моста: "
                    "маршрут/VPN до сети платы или проброс порта 7000 к %s"
                    % where)
    else:
        d["fix"] += (". " + head + " Мост пробовал и адрес платы %s — если "
                     "и там «Connection refused», на плате не запущено "
                     "приложение (Run в App Lab) или порт 7000 закрыт "
                     "файрволом" % (topo.get("board_lan") and
                                    "http://%s:7000" % topo["board_lan"] or
                                    "(не сообщён)"))
    return d


def _diagnose(err, base):
    e = str(err or "")
    low = e.lower()
    if ("remotedisconnected" in low or "without response" in low
            or "connectionabortederror" in low or "10053" in low
            or "badstatusline" in low) and _is_loopback(
                urllib.parse.urlparse(base).hostname):
        return {
            "cause": "%s принимает соединение и тут же его рвёт" % base,
            "why": "этот порт держит проброс adb (Arduino App Lab), а связь "
                   "adb с платой оборвалась (USB отключён или плата "
                   "перезагрузилась) — сама плата тут ни при чём",
            "fix": "впиши адрес платы в Wi-Fi (http://<IP платы>:7000) или "
                   "нажми «Найти плату в сети»; для adb — переподключи "
                   "плату в App Lab",
        }
    if ("connectionrefusederror" in low or "connection refused" in low
            or "errno 111" in low or "10061" in low) and _is_loopback(
                urllib.parse.urlparse(base).hostname):
        # v3.104: 127.0.0.1:7000 на компьютере — это проброс порта Arduino
        # App Lab (adb), а не сама плата: он живёт, пока открыт App Lab и
        # жив USB, и пропадает («сколько-то работает, потом отрубается»)
        return {
            "cause": "%s отверг подключение (WinError 10061)" % base,
            "why": "127.0.0.1:7000 — проброс порта Arduino App Lab по USB "
                   "(adb), а не сама плата: он пропадает, когда App Lab "
                   "закрыт, компьютер уснул или USB переподключился",
            "fix": "облако само переходит на адрес платы в Wi-Fi, который "
                   "она сообщает по каналу связи (плата v2.60+); надёжнее "
                   "вписать его в настройку моста: http://<IP платы>:7000. "
                   "Вопросы мозгу при этом идут по каналу связи (плата v2.61)",
        }
    if ("connectionrefusederror" in low or "connection refused" in low
            or "errno 111" in low or "10061" in low):
        return {
            "cause": "по адресу %s никто не слушает" % base,
            "why": "мозг neuro_12 не запущен — или его веб-хост живёт на "
                   "другом порту",
            "fix": "запусти мозг на плате (python main.py) — мост подключится "
                   "сам; если хост на другом порту — впиши адрес выше",
        }
    if ("timeout" in low or "timed out" in low or "10060" in low
            or "errno 110" in low):
        return {
            "cause": "плата по адресу %s не отвечает за 3 секунды" % base,
            "why": "плата выключена или занята, адрес неверный — либо "
                   "файрвол режет порт",
            "fix": "проверь, что плата включена и адрес верный; попробуй "
                   "«Найти плату в сети» в панели",
        }
    if ("errno -2" in low or "name or service not known" in low
            or "getaddrinfo failed" in low
            or "nodename nor servname" in low):
        return {
            "cause": "имя хоста в адресе %s не разрешается" % base,
            "why": "опечатка в адресе — или DNS не знает такое имя",
            "fix": "впиши адрес числами (http://192.168.1.50:7000) или "
                   "нажми «Найти плату в сети»",
        }
    if ("connectionreset" in low or "errno 104" in low
            or "10054" in low):
        return {
            "cause": "плата разорвала соединение (%s)" % base,
            "why": "веб-хост платы перезапускается — или упал",
            "fix": "подожди минуту; если повторяется — перезапусти мозг "
                   "(python main.py)",
        }
    if "http error" in low:
        return {
            "cause": "на %s живёт веб-сервер, но /api/state нет" % base,
            "why": "это не веб-хост платы — или мозг очень старой версии",
            "fix": "впиши верный адрес платы или обнови мозг; помогает "
                   "«Найти плату в сети»",
        }
    if "не api платы" in low or "что-то другое" in low:
        return {
            "cause": "на %s отвечает другой сервис, а не API платы" % base,
            "why": "порт занял другой процесс (например, другой веб-сервер)",
            "fix": "освободи порт — или впиши верный адрес платы",
        }
    return {
        "cause": "плата не отвечает (%s)" % base,
        "why": e[:200],
        "fix": "запусти мозг (python main.py) и проверь адрес; помогает "
               "«Найти плату в сети»",
    }


# ------------------------------- зонд ---------------------------------------
def probe(save=True):
    """Опросить плату GET /api/state. Возврат: (ok, state_или_текст_ошибки).
    Плата отвечает своей живой версией, мыслью, счётчиками нейронов.
    v3.27: адрес из настроек не ответил — мост сам пробует IP, с которого
    плата ходит по мозг-почте (это и есть «плата в сети»), и запоминает
    рабочий адрес (active_url); настройку владельца не переписывает."""
    tried, first_err, first_base = [], "", url()
    for base in _candidates():
        try:
            st = _get(base, "/api/state", timeout=3.0)
            if not isinstance(st, dict) or "version" not in st:
                raise ValueError("отвечает что-то другое (это не API платы)")
        except Exception as e:
            err = "%s: %s" % (type(e).__name__, e)
            tried.append({"url": base, "error": err[:160]})
            if not first_err:
                first_err, first_base = err, base
            continue
        if base != _ACTIVE[0] and base != url():
            try:
                store.log("МОСТ", "плата найдена на %s (адрес в настройках "
                          "%s не отвечает) — мост работает через неё"
                          % (base, url()))
            except Exception:
                pass
        _ACTIVE[0] = "" if base == url() else base
        tried.append({"url": base, "ok": True})
        with _LOCK:
            STATE["online"] = True
            STATE["last_ok"] = time.time()
            STATE["last_err"] = ""
            STATE["state"] = st
            STATE["ts"] = time.time()
            STATE["polls"] = int(STATE.get("polls") or 0) + 1
            STATE["tried"] = tried
        if save:
            _mirror_thought(st)
            register_via_bridge(st)
        return True, st
    _ACTIVE[0] = ""
    with _LOCK:
        STATE["online"] = False
        STATE["last_err"] = first_err or "нет адресов для проверки"
        STATE["ts"] = time.time()
        STATE["fails"] = int(STATE.get("fails") or 0) + 1
        STATE["tried"] = tried
        STATE["diagnosis"] = diagnose(STATE["last_err"], first_base)
    return False, STATE["last_err"]


def _mirror_thought(st):
    """Новая мысль платы (say/last_say) — в общую ленту «Мысли плат».
    v3.9: мысль СРАЗУ становится материалом сети материалов облака —
    так «генератор на том, что думает плата» учится непрерывно, а не
    только по запросу («что думает плата»)."""
    try:
        say = str(st.get("say") or st.get("last_say") or "").strip()
        if not say or len(say) < 2:
            return
        if time.time() - _SAY_TS[0] < 3.0:
            return                            # тот же поток — не дублируем
        _SAY_TS[0] = time.time()
        store.thoughts_add(board="bridge", text=say[:600], kind="мост")
        try:
            import brain
            brain.mn_learn(say[:3000], title="мысль платы (мост)",
                           src="мысль платы")
        except Exception:
            pass
    except Exception:
        pass


def device_id(st=None):
    """Имя платы из её /api/state (mem.device); запасное — «плата-мост»."""
    snap = (st if isinstance(st, dict) else None) or (STATE.get("state") or {})
    mem = (snap.get("mem") or {}) if isinstance(snap.get("mem"), dict) else {}
    return (str(mem.get("device") or "").strip()[:40] or "плата-мост")


def register_via_bridge(st=None):
    """v3.8.2: мост ВИДИТ плату напрямую (127.0.0.1:7000) — пусть она
    появится и в реестре плат («Устройства», экскурс), даже если мозг-почта
    у платы не настроена (cloud_url пуст). Такие записи помечены
    via_bridge=True: учитель возит им уроки ЧЕРЕЗ МОСТ. Пишем не чаще
    раза в 60 с (boards.json — общий файл, не дёргаем каждый опрос).
    Возврат: id записи или ""."""
    try:
        if time.time() - _REG_TS[0] < 60.0:
            return ""
        _REG_TS[0] = time.time()
        dev = device_id(st)
        # v3.27: плата уже живёт в реестре по МОЗГ-ПОЧТЕ — не перекрашиваем
        # её в «через мост» (раньше флаг via_bridge и адрес 127.0.0.1:7000
        # затирали почтовую запись, и учитель возил уроки в мёртвый мост)
        cur = (store.boards() or {}).get(dev) or {}
        if cur and not cur.get("via_bridge"):
            store.board_seen(dev)
            return dev
        store.board_touch(dev, name="Плата %s (через мост)" % dev,
                          kind="board", url=active_url(),
                          note="авто: мост видит плату; мозг-почта не "
                               "настроена (cloud_url в config платы)",
                          via_bridge=True)
        return dev
    except Exception:
        return ""


# --------------------------- статус для панели ------------------------------
def _tensor_help():
    try:
        import tensorhelp
        return tensorhelp.status()
    except Exception:
        return None


def status():
    """Сводка моста: вкл/выкл, адрес, онлайн, снимок состояния платы."""
    with _LOCK:
        st = {k: v for k, v in STATE.items()}
    snap = st.get("state") or {}
    mem = (snap.get("mem") or {}) if isinstance(snap, dict) else {}
    try:
        online = st.get("online")
        if online is True:
            age = round(time.time() - float(st.get("last_ok") or 0), 1)
        else:
            age = None
    except Exception:
        age = None
    return {
        "on": enabled(),
        "url": url(),
        "active_url": active_url(),          # v3.27: где мост реально говорит
        "tried": st.get("tried") or [],      # v3.27: какие адреса пробовал
        "mail_boards": [{"board": b, "ip": ip, "age": round(a, 1)}
                        for b, ip, a in _mail_boards()][:5],
        "default_url": DEFAULT_URL,
        "online": online,
        "last_ok": st.get("last_ok") or 0,
        "age": age,
        "last_err": st.get("last_err") or "",
        "diagnosis": (None if online else st.get("diagnosis")),
        "polls": st.get("polls") or 0,
        "fails": st.get("fails") or 0,
        "state": {
            "version": snap.get("version"),
            "say": snap.get("say") or snap.get("last_say") or "",
            "gen_hz": snap.get("gen_hz"),
            "cyc": snap.get("cyc"),
            "act": snap.get("act"),
            "top": snap.get("top"),
            "mem_count": mem.get("mem_count",
                                 snap.get("mem_count")),
            "chat_count": mem.get("chat_count"),
            "device": mem.get("device"),
            "neurons_online": snap.get("neurons_online"),
            "up_sec": snap.get("up_sec"),
            "brainmail": snap.get("brainmail") or {},
            "cloud": snap.get("cloud") or {},
            "cfg": snap.get("cfg") or {},
            # v2.39 платы: тензорная сеть (H×76) — для чипа в панели
            "tensor": snap.get("tensor") if isinstance(snap.get("tensor"),
                                                       dict) else None,
        },
        "tensor_help": _tensor_help(),
        "ts": st.get("ts") or 0,
    }


# ----------------------- говорить с платой напрямую -------------------------
def send(text, user="облако", wait=45.0):
    """Задать плате вопрос напрямую: POST /api/send, плата думает сама
    (зонная сеть, без LLM) и отвечает в своей ленте — ждём реплики «bot».
    Возврат: {ok, answer, ms, error?}"""
    t0 = time.time()
    if not enabled():
        return {"ok": False, "error": "мост выключен — включи во вкладке "
                                      "«Устройства»"}
    ok, st = probe(save=False)
    base = active_url()                   # v3.27: рабочий адрес после щупа
    if not ok:
        d = STATE.get("diagnosis") or {}
        return {"ok": False,
                "error": "плата не отвечает на %s (%s). %s" % (
                    url(), st, d.get("fix") or "Запусти мозг (main.py) — "
                                               "мост подключится сам")}
    seq0 = 0
    try:
        before = _get(base, "/api/messages", timeout=3.0)
        seq0 = int(before.get("seq") or 0)
    except Exception:
        pass
    try:
        r = _post(base, "/api/send",
                  {"text": str(text)[:2000], "user": str(user)[:40]},
                  timeout=6.0)
        if not isinstance(r, dict) or not r.get("ok"):
            return {"ok": False, "error": "плата отклонила сообщение: %s"
                                          % (r or {})}
    except Exception as e:
        return {"ok": False, "error": "не смог отправить: %s" % e}
    said, last_seq, quiet = [], seq0, 0.0
    deadline = time.time() + float(wait)
    while time.time() < deadline:
        time.sleep(1.0)
        try:
            m = _get(base, "/api/messages?after=%d" % last_seq, timeout=3.0)
            items = m.get("items") or []
        except Exception:
            quiet += 1.0
            continue
        new_bot = False
        for it in items:
            try:
                last_seq = max(last_seq, int(it.get("seq") or 0))
            except Exception:
                pass
            if str(it.get("role") or "") == "bot":
                t = str(it.get("text") or "").strip()
                if t:
                    said.append(t)
                    new_bot = True
        quiet = 0.0 if items else quiet + 1.0
        if said and quiet >= 4.0:            # ответ закончился: 4 с тишины
            break
    answer = "\n".join(said).strip()
    res = {"ok": True, "answer": answer, "ms": int((time.time() - t0) * 1000),
           "url": base}
    if not answer:
        res["answer"] = ""
        res["note"] = ("плата пока молчит (думает зонной сетью) — повтори "
                       "вопрос позже или зайди в панель платы %s" % base)
    return res


def think(text, timeout=25.0, fresh=False):
    """v3.104: прямой вопрос по мосту, а не вышло (мост выключен, порт
    платы закрыт — WinError 10061, таймаут) — тот же вопрос по КАНАЛУ СВЯЗИ,
    который идёт от самой платы (boardlink.ask, плата v2.61+)."""
    r = _think_direct(text, timeout=timeout, fresh=fresh)
    if r.get("ok"):
        return r
    try:
        import boardlink
        lr = boardlink.ask(text, timeout=min(14.0, float(timeout)))
    except Exception:
        lr = None
    if lr and lr.get("ok"):
        return lr
    if lr and lr.get("error"):
        r = dict(r, error="%s; канал связи: %s" % (r.get("error"),
                                                   lr["error"]))
    return r


def _think_direct(text, timeout=25.0, fresh=False):
    """v3.98: ПРЯМОЙ ВОПРОС МОЗГУ — POST /api/think на плату (v2.58+): мозг
    читает текст и сразу отвечает фразой из своей мысли. Без мозг-почты:
    ни очереди писем, ни опроса inbox, ни реестра «свободных мозгов».
    Возврат: {ok, answer, board, url, ms} | {ok: False, error, fix?}."""
    if not enabled():
        return {"ok": False, "error": "мост к плате выключен (Устройства)"}
    # v3.99: плата только что не отвечала (фоновый опрос раз в 15 с) — не
    # ждём новых таймаутов в каждом ответе: мозг облака отвечает сам
    with _LOCK:
        off = STATE.get("online") is False and \
            time.time() - float(STATE.get("ts") or 0) < 20.0
        last_err = STATE.get("last_err") or ""
    if off and not fresh:
        return {"ok": False, "error": "плата не отвечает на %s: %s"
                % (url(), str(last_err)[:160]), "offline": True}
    ok, st = probe(save=False)
    base = active_url()
    if not ok:
        d = STATE.get("diagnosis") or {}
        return {"ok": False, "error": "плата не отвечает на %s: %s"
                % (url(), str(st)[:160]), "fix": d.get("fix") or ""}
    try:
        r = _post(base, "/api/think", {"text": str(text)[:2000]},
                  timeout=float(timeout))
    except Exception as e:
        msg = str(e)
        if "404" in msg:
            return {"ok": False, "error": "плата старая — нет /api/think "
                    "(нужна neuro_12 v2.58)", "old": True}
        return {"ok": False, "error": "плата не ответила: %s" % msg[:160]}
    if not isinstance(r, dict) or not r.get("ok"):
        return {"ok": False, "error": "плата: %s" % (r or {}).get("error")}
    mem = []                     # v3.99 (плата v2.59): опора из памяти платы
    for m in (r.get("memory") or [])[:6]:
        if isinstance(m, dict) and str(m.get("text") or "").strip():
            mem.append({"text": str(m["text"]).strip()[:1500],
                        "src": str(m.get("src") or "память платы")[:80]})
    return {"ok": True, "answer": str(r.get("answer") or "").strip(),
            "thought": str(r.get("thought") or r.get("answer") or "").strip(),
            "memory": mem, "grounded": bool(r.get("grounded")),
            "board": str(r.get("board") or (st or {}).get("device") or
                         "плата")[:40],
            "why": r.get("why") or "", "url": base, "ms": r.get("ms")}


def messages(after=0):
    """Лента сообщений платы (прокси GET /api/messages?after=N)."""
    try:
        m = _get(active_url(), "/api/messages?after=%d" % int(after or 0),
                 timeout=3.0)
        return {"ok": True, "items": m.get("items") or [],
                "seq": m.get("seq") or 0}
    except Exception as e:
        return {"ok": False, "error": str(e), "items": []}


# --------------------------- поиск платы в сети ------------------------------
def _lan_ips():
    """Собственные IP хоста в LAN (без 127.*) — по ним строим /24-подсети."""
    ips = set()
    try:
        import socket as _s
        for info in _s.getaddrinfo(_s.gethostname(), None, _s.AF_INET):
            ips.add(info[4][0])
    except Exception:
        pass
    try:
        import socket as _s
        s = _s.socket(_s.AF_INET, _s.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))     # пакет не летит — но маршрут ясен
            ips.add(s.getsockname()[0])
        finally:
            s.close()
    except Exception:
        pass
    # v3.27: link-local 169.254.* (адаптеры без DHCP) — не сеть платы;
    # домашние 192.168.* — первыми, VPN/корпоративные 10.* — последними
    # (раньше сортировка ставила 10.* и 169.254.* вперёд, и лимит хостов
    # обрезал настоящую подсеть 192.168.1.x — плату «не находило»)
    def _rank(ip):
        if ip.startswith("192.168."):
            return 0
        if ip.startswith("172."):
            return 1
        return 2
    return sorted((ip for ip in ips
                   if ip and not ip.startswith("127.") and ":" not in ip
                   and not ip.startswith("169.254.")),
                  key=lambda ip: (_rank(ip), ip))


def _check_host(ip, port, timeout=0.6):
    """Живая ли тут плата: TCP-проба, затем GET /api/state с ключом version.
    Возврат: {url, version, device, say} или None."""
    try:
        import socket as _s
        c = _s.create_connection((ip, port), timeout=timeout)
        c.close()
    except Exception:
        return None
    try:
        st = _get("http://%s:%d" % (ip, port), "/api/state", timeout=2.0)
        if isinstance(st, dict) and "version" in st:
            mem = (st.get("mem") or {}
                   if isinstance(st.get("mem"), dict) else {})
            return {"url": "http://%s:%d" % (ip, port),
                    "version": st.get("version"),
                    "device": str(mem.get("device") or "")[:40],
                    "say": str(st.get("say") or st.get("last_say")
                               or "")[:120]}
    except Exception:
        return None
    return None


def find_board(ports=None, max_total=600, timeout=0.6):
    """v3.23.1: НАЙТИ ПЛАТУ В СЕТИ — когда «облако не подключается к плате»,
    а владелец не знает её адрес. Проверяем 127.0.0.1, собственные IP хоста
    и /24 каждой LAN-подсети. Порты: порт из текущего адреса моста + 7000.
    Плата опознаётся по GET /api/state с ключом version (как в probe()).
    Возврат: {found: [{url, version, device, say}], scanned: N}."""
    from concurrent.futures import ThreadPoolExecutor
    cur = url()
    ports = set(int(p) for p in (ports or []) if p)
    try:
        pr = urllib.parse.urlparse(cur)
        if pr.port:
            ports.add(int(pr.port))
    except Exception:
        pass
    ports.add(7000)                        # штатный порт веб-хоста платы
    # v3.27: сначала — IP, с которых платы ходят по мозг-почте (точно они)
    hosts = [ip for _b, ip, _a in _mail_boards(max_age=86400.0)]
    hosts.append("127.0.0.1")
    for ip in _lan_ips():
        pref = ".".join(ip.split(".")[:3]) + "."
        hosts.extend(pref + str(n) for n in range(1, 255))
    hosts = list(dict.fromkeys(hosts))[:max_total]
    scanned = 0
    found = []
    with ThreadPoolExecutor(max_workers=64) as ex:
        futs = {}
        for h in hosts:
            for p in sorted(ports):
                futs[ex.submit(_check_host, h, p, timeout)] = p
        for f in futs:
            scanned += 1
            try:
                r = f.result()
            except Exception:
                r = None
            if r:
                found.append(r)
    found.sort(key=lambda x: (x["url"] != cur, x["url"]))
    return {"found": found, "scanned": scanned, "hosts": len(hosts),
            "ports": sorted(ports)}


# ------------------------------- фон ----------------------------------------
def _loop():
    while True:
        try:
            if enabled():
                probe(save=True)
        except Exception:
            pass
        time.sleep(POLL_SEC)


def start():
    threading.Thread(target=_loop, daemon=True, name="bridge").start()
    try:                       # v3.27: облако считает задачи тензора платы
        import tensorhelp
        tensorhelp.start()
    except Exception:
        pass
