# -*- coding: utf-8 -*-
# ===========================================================================
# tgbot.py — БОТ ОБЛАКА В TELEGRAM (v3.40, только stdlib)
# ---------------------------------------------------------------------------
# Как MAX-бот: отвечает мозгом облака (dialog.respond: мысли мозга + ответ
# зон/базы знаний/интернета, память диалога), читает присланные документы
# (в базу знаний), описывает картинки, СЛУШАЕТ голосовые/аудио/видео
# (hearing.report: свойства звука + речь в текст, если стоит Vosk).
# Работа: long polling getUpdates (исходящие https-запросы; белый IP не
# нужен). Токен — у @BotFather. Настройки (панель «Телеграм-бот»):
#   tg_on, tg_token, tg_proxy (http://host:port или socks нельзя — только
#   HTTP(S)-прокси), tg_api_base (зеркало API, по умолчанию
#   https://api.telegram.org), tg_allowed (id/@username через запятую;
#   пусто = все), tg_memory.
# Личка — отвечает на всё. Группа — только на обращение («бот …», «мозг …»,
# @username, ответ на сообщение бота, /команда). Копии сервера: опрос ведёт
# ОДИН процесс (замок-файл), входящие/исходящие дедуплицируются метками.
# ===========================================================================
import hashlib
import json
import os
import re
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

import store
import textutils

POLL_TIMEOUT = 25
HTTP_TIMEOUT = POLL_TIMEOUT + 15
MAX_REPLY = 3900                                  # лимит Telegram 4096
MAX_FILE = 20 * 1024 * 1024                       # потолок getFile для ботов
MEM_TURNS, MEM_CHARS, MEM_CHATS = 12, 600, 200
UA = "neuro12-cloud-tgbot/3.40"
_ADDR = ("бот", "мозг")

_STATE_LOCK = threading.RLock()
_STATE = None
_TH = [None]
_WAKE = threading.Event()
_LEAD = {"hash": "", "fh": None, "note": "", "pid": 0, "logged": False}
_POOL = [None]
_CHAT_LOCKS = {}
_CHAT_LOCKS_GUARD = threading.Lock()


class TgErr(Exception):
    """Честная ошибка Bot API Telegram."""


# ------------------------------- настройки ----------------------------------
def _s():
    return store.settings()


def enabled():
    return bool(_s().get("tg_on"))


def token():
    return str(_s().get("tg_token") or "").strip()


def _proxy():
    return str(_s().get("tg_proxy") or "").strip()


def _api_base():
    b = str(_s().get("tg_api_base") or "").strip().rstrip("/")
    return b or "https://api.telegram.org"


def _memory_on():
    return bool(_s().get("tg_memory", True))


def _allowed(user):
    """Белый список: пусто — всем. Элементы: id или @username (без @)."""
    raw = str(_s().get("tg_allowed") or "").strip()
    if not raw:
        return True
    ok = {x.strip().lstrip("@").lower() for x in re.split(r"[,;\s]+", raw)
          if x.strip()}
    uid = str((user or {}).get("id") or "")
    un = str((user or {}).get("username") or "").lower()
    return uid in ok or (un and un in ok)


# ------------------------------- состояние ----------------------------------
def _defaults():
    return {"offset": 0, "bot": {}, "chats": {}, "mem": {}, "seen": [],
            "stats": {"recv": 0, "ans": 0, "docs": 0, "heard": 0,
                      "skipped": 0, "denied": 0, "errors": 0},
            "last_ok": 0.0, "last_error": ""}


def _load():
    global _STATE
    with _STATE_LOCK:
        if _STATE is None:
            d = store.load_json("tgbot.json", None)
            base = _defaults()
            if isinstance(d, dict):
                for k, v in base.items():
                    d.setdefault(k, v)
                for k, v in base["stats"].items():
                    d["stats"].setdefault(k, v)
                _STATE = d
            else:
                _STATE = base
        return _STATE


def _save():
    with _STATE_LOCK:
        try:
            store.save_json("tgbot.json", _load())
        except Exception:
            pass


def _err(msg):
    with _STATE_LOCK:
        st = _load()
        st["last_error"] = str(msg)[:300]
        st["stats"]["errors"] += 1
    store.log("TELEGRAM", str(msg)[:200])


# ----------------- один опросчик на токен (несколько копий) ------------------
def _tok_hash():
    return hashlib.md5(token().encode("utf-8", "replace")).hexdigest()[:10]


def _claim_dirs():
    h = _tok_hash()
    d = [os.path.join(tempfile.gettempdir(), "neuro12-tg-" + h)]
    try:
        d.append(os.path.join(store.DATA_DIR, "tgclaims"))
    except Exception:
        pass
    return d


def _release_lead():
    fh = _LEAD.get("fh")
    _LEAD.update(hash="", fh=None, pid=0)
    if fh:
        try:
            fh.close()
        except Exception:
            pass


def _is_leader():
    h = _tok_hash()
    if _LEAD["fh"] is not None and _LEAD["hash"] == h:
        return True
    if _LEAD["fh"] is not None:
        _release_lead()
    path = os.path.join(tempfile.gettempdir(), "neuro12-tgbot-%s.lock" % h)
    fh = None
    try:
        fh = open(path, "a+b")
        fh.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        fh.seek(8)
        fh.write(("%d" % os.getpid()).encode("ascii").ljust(16))
        fh.flush()
        _LEAD.update(hash=h, fh=fh, pid=os.getpid(),
                     note="опрос Telegram веду я (PID %d)" % os.getpid())
        return True
    except Exception:
        try:
            if fh:
                fh.close()
        except Exception:
            pass
        _LEAD["note"] = ("опрос Telegram уже ведёт другая копия облака — "
                         "эта копия в резерве")
        return False


def _claim(kind, key, window=0.0):
    name = "%s-%s" % (kind, hashlib.md5(
        str(key).encode("utf-8", "replace")).hexdigest())
    ok = True
    for d in _claim_dirs():
        try:
            os.makedirs(d, exist_ok=True)
            path = os.path.join(d, name)
            if window > 0 and os.path.exists(path):
                if time.time() - os.path.getmtime(path) < window:
                    ok = False
                    continue
                os.utime(path, None)
                continue
            os.close(os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
        except FileExistsError:
            ok = False
        except Exception:
            continue
    return ok


# ------------------------------- HTTP-клиент --------------------------------
def _opener():
    px = _proxy()
    if px:
        return urllib.request.build_opener(
            urllib.request.ProxyHandler({"https": px, "http": px}))
    return urllib.request.build_opener()


def _call(method, params=None, timeout=HTTP_TIMEOUT):
    tok = token()
    if not tok:
        raise TgErr("токен Telegram-бота не задан")
    url = "%s/bot%s/%s" % (_api_base(), tok, method)
    data = json.dumps(params or {}).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={
        "Content-Type": "application/json", "User-Agent": UA})
    try:
        with _opener().open(req, timeout=timeout) as r:
            body = r.read()
    except urllib.error.HTTPError as e:
        try:
            d = json.loads(e.read().decode("utf-8", "replace"))
            raise TgErr("Telegram: %s (HTTP %s)" % (
                d.get("description") or "ошибка", e.code))
        except TgErr:
            raise
        except Exception:
            raise TgErr("Telegram ответил HTTP %s" % e.code)
    except Exception as e:
        raise TgErr("нет связи с Telegram (%s: %s). Если Telegram у вас "
                    "блокируется — задайте HTTP(S)-прокси или зеркало API "
                    "в настройках" % (type(e).__name__, str(e)[:100]))
    try:
        d = json.loads(body.decode("utf-8", "replace"))
    except Exception:
        raise TgErr("Telegram прислал не JSON")
    if not d.get("ok"):
        raise TgErr("Telegram: %s" % (d.get("description") or "отказ"))
    return d.get("result")


def me(refresh=False):
    st = _load()
    if st.get("bot") and not refresh:
        return st["bot"]
    r = _call("getMe", timeout=20) or {}
    with _STATE_LOCK:
        st["bot"] = {"id": r.get("id"), "name": r.get("first_name"),
                     "username": r.get("username")}
    _save()
    return st["bot"]


def _download(file_id, max_bytes=MAX_FILE):
    """file_id -> (ok, bytes|причина, имя)."""
    try:
        f = _call("getFile", {"file_id": file_id}, timeout=20) or {}
    except TgErr as e:
        return False, str(e), ""
    path = f.get("file_path") or ""
    if not path:
        return False, "Telegram не дал путь файла (файл больше 20 МБ?)", ""
    url = "%s/file/bot%s/%s" % (_api_base(), token(), path)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with _opener().open(req, timeout=60) as r:
            data = r.read(max_bytes + 1)
        if len(data) > max_bytes:
            return False, "файл больше 20 МБ — Telegram не отдаёт ботам", ""
        return True, data, os.path.basename(path)
    except Exception as e:
        return False, "%s: %s" % (type(e).__name__, str(e)[:120]), ""


# ----------------------------------- отправка -------------------------------
def _clean(text, limit=MAX_REPLY):
    try:                      # v3.108: формула — читаемым текстом, не пропадает
        import latexlite
        text = latexlite.blocks_to_text(text)
        import kbimages                 # v3.124: иллюстрация — подпись и ссылка
        text = kbimages.plain(text)
    except Exception:
        pass
    t = re.sub(r"\[(ФОРМУЛА|КАРТИНКА)\][\s\S]*?\[/\1\]", "", str(text or ""))
    t = t.strip()
    return t[:limit]


def send(chat_id, text, reply_to=None):
    """Текст в чат; длинный режется по абзацам. Исходящий дубль (другая
    копия) глушится меткой на 20 с."""
    t = _clean(__import__("latexlite").render_dollars(str(text or "").replace("\u2063", ""), "plain"), 20000)
    if not t:
        return
    if not _claim("out", "%s|%s" % (chat_id, t), window=20.0):
        return
    parts, cur = [], ""
    for para in t.split("\n"):
        if len(cur) + len(para) + 1 > MAX_REPLY:
            if cur:
                parts.append(cur)
            cur = para[:MAX_REPLY]
        else:
            cur = (cur + "\n" + para) if cur else para
    if cur:
        parts.append(cur)
    for i, part in enumerate(parts[:6]):
        prm = {"chat_id": chat_id, "text": part,
               "disable_web_page_preview": False}
        if reply_to and i == 0:
            prm["reply_to_message_id"] = reply_to
        if textutils.has_bold(part):         # v3.45: жирные разделы ответа
            try:
                _call("sendMessage", dict(prm, text=textutils.bold_html(part),
                                          parse_mode="HTML"), timeout=30)
                continue
            except TgErr:
                prm["text"] = textutils.strip_bold(part)
        _call("sendMessage", prm, timeout=30)


def send_photo(chat_id, data, caption="", ctype="image/jpeg"):
    """v3.128: фото в чат (multipart sendPhoto) — кадр камеры, иллюстрации."""
    tok = token()
    if not tok or not data:
        return False
    bnd = "n12" + os.urandom(8).hex()
    ext = "png" if "png" in str(ctype) else "jpg"
    body = b""
    for k, v in (("chat_id", str(chat_id)), ("caption", str(caption or "")[:900])):
        body += ("--%s\r\nContent-Disposition: form-data; name=\"%s\"\r\n\r\n%s\r\n"
                 % (bnd, k, v)).encode("utf-8")
    body += ("--%s\r\nContent-Disposition: form-data; name=\"photo\"; "
             "filename=\"frame.%s\"\r\nContent-Type: %s\r\n\r\n" % (bnd, ext, ctype)).encode()
    body += data + ("\r\n--%s--\r\n" % bnd).encode()
    req = urllib.request.Request(
        "%s/bot%s/sendPhoto" % (_api_base(), tok), data=body,
        headers={"Content-Type": "multipart/form-data; boundary=%s" % bnd,
                 "User-Agent": UA})
    try:
        with _opener().open(req, timeout=60) as r:
            d = json.loads(r.read().decode("utf-8", "replace"))
        return bool(d.get("ok"))
    except Exception as e:
        store.log("TELEGRAM", "фото не ушло: %s" % e)
        return False


def _send_illustrations(chat_id, texts):
    """v3.128: картинки из ответа (кадр камеры, иллюстрации статьи) — фото."""
    n = 0
    try:
        import kbimages
        for t in texts or ():
            for src, _url, alt in kbimages.items(t)[:2]:
                if not src.startswith("kbimg:"):
                    continue
                data, ct = kbimages.read(src)
                if data and "svg" not in str(ct) and send_photo(chat_id, data, alt, ct):
                    n += 1
    except Exception as e:
        store.log("TELEGRAM", "картинки: %s" % e)
    return n


def _status_begin(chat_id):
    """v3.50: «🔍 Смотрю базу данных…» пока готовится ответ -> message_id."""
    try:
        if not store.settings().get("bot_status_msg", True):
            return None
        r = _call("sendMessage", {"chat_id": chat_id,
                                  "text": "🔍 Смотрю базу данных…"},
                  timeout=10) or {}
        return r.get("message_id")
    except Exception:
        return None


def _status_end(chat_id, mid):
    if not mid:
        return
    try:
        _call("deleteMessage", {"chat_id": chat_id, "message_id": mid},
              timeout=10)
    except Exception as e:
        store.log("TELEGRAM", "статус-сообщение не убралось: %s" % str(e)[:80])


def _typing(chat_id):
    try:
        _call("sendChatAction", {"chat_id": chat_id, "action": "typing"},
              timeout=10)
    except Exception:
        pass


# -------------------------------- память диалога ----------------------------
def _mem_ctx(chat_id):
    if not _memory_on():
        return []
    with _STATE_LOCK:
        return [{"role": m["role"], "text": m["text"]}
                for m in (_load().get("mem", {}).get(str(chat_id)) or [])[-6:]]


def _mem_add(chat_id, role, text):
    t = re.sub(r"\s+", " ", str(text or "")).strip()
    if not t or not _memory_on():
        return
    with _STATE_LOCK:
        mem = _load().setdefault("mem", {})
        lst = mem.setdefault(str(chat_id), [])
        if lst and lst[-1]["role"] == role and lst[-1]["text"] == t:
            return
        lst.append({"role": role, "text": t[:MEM_CHARS], "ts": time.time()})
        mem[str(chat_id)] = lst[-MEM_TURNS:]
        if len(mem) > MEM_CHATS:
            for k in sorted(mem, key=lambda k: mem[k][-1]["ts"])[:-MEM_CHATS]:
                mem.pop(k, None)
    _save()


# ---------------------------------- команды ---------------------------------
def _help_text():
    return ("Я — бот облака «Star Gate» (без внешнего LLM). Отвечаю мозгом "
            "облака: зоны, база знаний, интернет, функции. Умею:\n"
            "• обычные вопросы и диалог (помню недавние реплики);\n"
            "• документы (docx, pdf, txt…) — читаю в базу знаний;\n"
            "• картинки — описываю;\n"
            "• голосовые, аудио и видео — СЛУШАЮ: свойства звука и речь "
            "(если на сервере стоит распознавание);\n"
            "• «посмотри видео <ссылка>» — читаю ролик и слушаю его звук;\n"
            "• «что ты видишь?» или /камера — кадр с камеры мозга и что на нём;\n"
            "• «что ты слышишь?» или /слышу — что мозг услышал в свой микрофон.\n"
            "/status — состояние, /forget — забыть наш диалог.")


def _status_text():
    st = _load()["stats"]
    try:
        import hearing
        h = hearing.status()
        ear = ("речь распознаю" if h["asr_ready"] else
               "слышу свойства звука" if h["ffmpeg"] else
               "звук пока не декодирую (нет ffmpeg)")
    except Exception:
        ear = "слух недоступен"
    return ("Telegram-бот облака работает. Сообщений: %d, ответов: %d, "
            "документов: %d, на слух: %d. Слух: %s." % (
                st["recv"], st["ans"], st["docs"], st["heard"], ear))


def _cmd(text, chat_id):
    t = text.strip().split("@")[0].lower() if text.startswith("/") else ""
    if t in ("/start", "/help"):
        return _help_text()
    if t == "/status":
        return _status_text()
    try:                                  # v3.125: «/почему» — путь ответа
        import dialog
        if dialog.is_why(text):
            return dialog.explain("tg:%s" % chat_id)
    except Exception:
        pass
    if t in ("/forget", "/забудь"):
        with _STATE_LOCK:
            n = len(_load().setdefault("mem", {}).pop(str(chat_id), None)
                    or [])
        _save()
        return "Забыла наш диалог (реплик: %d)." % n
    return None


# ------------------------------- вложения ------------------------------------
def _handle_media(msg, chat_id):
    """Вложения -> тексты-ответы (список). Голос/аудио/видео — слух,
    документ — база знаний (или слух, если это медиафайл), фото — imgsense."""
    out = []
    items = []
    for key in ("voice", "audio", "video", "video_note", "animation"):
        if isinstance(msg.get(key), dict):
            items.append((key, msg[key]))
    if isinstance(msg.get("document"), dict):
        items.append(("document", msg["document"]))
    if isinstance(msg.get("photo"), list) and msg["photo"]:
        items.append(("photo", msg["photo"][-1]))
    for kind, o in items[:2]:
        fid = o.get("file_id")
        if not fid:
            continue
        _typing(chat_id)
        ok, data, fname = _download(fid)
        if not ok:
            out.append("Не смогла скачать %s: %s" % (kind, data))
            continue
        name = str(o.get("file_name") or fname or kind)
        try:
            import hearing
            if kind in ("voice", "audio", "video", "video_note", "animation") \
                    or (kind == "document" and hearing.is_media_name(name)):
                rep, _ok = hearing.report(data, name, source="Telegram")
                with _STATE_LOCK:
                    _load()["stats"]["heard"] += 1
                out.append(rep)
                continue
        except Exception as e:
            out.append("Звук не разобрала: %s" % e)
            continue
        if kind == "photo":
            try:
                import imgsense
                out.append(imgsense.describe(data, name="из Telegram"))
            except Exception as e:
                out.append("Картинку не разобрала: %s" % e)
            continue
        if name.lower().endswith(".zip"):          # v3.44: ZIP с кодом
            try:
                import ghlearn
                r = ghlearn.ingest_zip(data, name)
                if r.get("ok"):
                    out.append("Приняла архив «%s»: файлов с кодом %d — "
                               "читаю по одному, учусь." % (name, r["files"]))
                    continue
            except Exception:
                pass
        try:                                   # документ -> база знаний
            import kb
            import textutils
            text, vid = textutils.extract_text(name, data)
            if len(text) >= 40:
                kb.add_article(os.path.splitext(name)[0][:80] or "Документ",
                               text, source="документ Telegram")
                with _STATE_LOCK:
                    _load()["stats"]["docs"] += 1
                out.append("Прочитала документ «%s» (%s, %d знаков) — "
                           "он в базе знаний." % (name, vid, len(text)))
            else:
                out.append("Документ «%s» получила, но текста не нашла "
                           "(вид: %s)." % (name, vid))
        except Exception as e:
            out.append("Документ не разобрала: %s" % e)
    _save()
    return out


# ------------------------------- обработка ----------------------------------
def _addressed(text, msg, bot):
    """Текст без обращения, если оно есть; None — группа не к боту."""
    t = (text or "").strip()
    un = str((bot or {}).get("username") or "")
    rep = msg.get("reply_to_message") or {}
    if (rep.get("from") or {}).get("id") and \
            (rep["from"]["id"] == (bot or {}).get("id")):
        return t
    if un and ("@" + un).lower() in t.lower():
        return re.sub("@" + re.escape(un), "", t, flags=re.I).strip()
    m = re.match(r"^\s*(%s)\b[\s,:!.\-]*(.*)$" % "|".join(_ADDR), t,
                 re.I | re.S)
    if m:
        return m.group(2).strip() or None
    return None


def _on_message(msg):
    if not isinstance(msg, dict):
        return
    chat = msg.get("chat") or {}
    chat_id = chat.get("id")
    user = msg.get("from") or {}
    if chat_id is None or user.get("is_bot"):
        return
    mid = "%s:%s" % (chat_id, msg.get("message_id"))
    with _STATE_LOCK:
        seen = _load().setdefault("seen", [])
        if mid in seen:
            _load()["stats"]["skipped"] += 1
            return
        seen.append(mid)
        del seen[:-400]
    if not _claim("in", mid):
        with _STATE_LOCK:
            _load()["stats"]["skipped"] += 1
        return
    with _STATE_LOCK:
        _load()["stats"]["recv"] += 1
        _load().setdefault("chats", {})[str(chat_id)] = {
            "chat_id": chat_id, "type": chat.get("type"),
            "title": chat.get("title") or chat.get("username") or
            user.get("first_name") or str(chat_id), "seen": time.time()}
    if not _allowed(user):
        with _STATE_LOCK:
            _load()["stats"]["denied"] += 1
        if chat.get("type") == "private":
            try:
                send(chat_id, "Этот бот приватный — доступ только по "
                     "списку владельца облака.")
            except TgErr:
                pass
        return
    private = chat.get("type") == "private"
    text = (msg.get("text") or msg.get("caption") or "").strip()
    bot = _load().get("bot") or {}
    name = user.get("first_name") or user.get("username") or "друг"
    _sense_cmd = False
    try:                                  # v3.128: /камера, /слышу — через мозг
        import senses
        _sense_cmd = bool(senses.ask_kind(text))
    except Exception:
        pass
    if text.startswith("/") and not _sense_cmd:
        ans = _cmd(text, chat_id)
        if ans:
            send(chat_id, ans)
            with _STATE_LOCK:
                _load()["stats"]["ans"] += 1
        return
    asked = text
    if not private:
        asked = _addressed(text, msg, bot)
        if asked is None and not (msg.get("voice") or msg.get("audio")):
            return                                   # группа не к боту
        if asked is None:
            return
    pre = []
    if any(msg.get(k) for k in ("voice", "audio", "video", "video_note",
                                "animation", "document", "photo")):
        pre = _handle_media(msg, chat_id)
    if not (asked or "").strip():
        if pre:
            send(chat_id, "\n\n".join(pre), reply_to=msg.get("message_id"))
            with _STATE_LOCK:
                _load()["stats"]["ans"] += 1
        return
    _typing(chat_id)
    _st_mid = _status_begin(chat_id)
    try:
        import dialog
        msgs, zone, d_info = dialog.respond(
            asked, ctx=_mem_ctx(chat_id), chat_key="tg:%s" % chat_id,
            name=name, sender="Telegram: " + name,
            deliver={"kind": "tg", "chat_id": chat_id, "name": name},
            limit=MAX_REPLY, template=True)
    except Exception as e:
        store.log("TELEGRAM", "движок диалога: %s" % e)
        try:
            import brain
            reply, zone, _i = brain.think(asked, sender="Telegram: " + name,
                                          ctx=_mem_ctx(chat_id))
        except Exception:
            reply = ""
        msgs, d_info = [reply or "Пока не знаю, что ответить."], {}
    _status_end(chat_id, _st_mid)             # ответ готов — статус долой
    _mem_add(chat_id, "user", asked)
    ans = "\n\n".join(msgs[int((d_info or {}).get("n_thought") or 0):])
    if ans:
        _mem_add(chat_id, "bot", ans)
    try:
        for m in [p for p in pre if p] + [m for m in msgs if m]:
            send(chat_id, m, reply_to=None)
        _send_illustrations(chat_id, msgs)    # v3.128: кадр камеры, иллюстрации
        with _STATE_LOCK:
            _load()["stats"]["ans"] += 1
    except TgErr as e:
        _err(str(e))
    _save()


def handle_update(u):
    if not isinstance(u, dict):
        return False
    m = u.get("message")
    if isinstance(m, dict):
        _on_message(m)
        return True
    return False


def _chat_lock(chat_id):
    with _CHAT_LOCKS_GUARD:
        lk = _CHAT_LOCKS.get(chat_id)
        if lk is None:
            if len(_CHAT_LOCKS) > 500:
                _CHAT_LOCKS.clear()
            lk = _CHAT_LOCKS[chat_id] = threading.Lock()
        return lk


def _run(u, chat_id):
    try:
        with _chat_lock(chat_id):
            handle_update(u)
    except Exception as e:
        _err("обработка: %s: %s" % (type(e).__name__, e))


def _dispatch(u):
    try:
        chat_id = ((u.get("message") or {}).get("chat") or {}).get("id")
        if _POOL[0] is None:
            from concurrent.futures import ThreadPoolExecutor
            _POOL[0] = ThreadPoolExecutor(max_workers=4,
                                          thread_name_prefix="tgbot")
        _POOL[0].submit(_run, u, chat_id)
    except Exception as e:
        _err("диспетчер: %s: %s" % (type(e).__name__, e))


# --------------------------------- поток -------------------------------------
def _loop():
    fail = 0
    while True:
        if not enabled() or not token():
            _release_lead()
            _WAKE.wait(5.0)
            _WAKE.clear()
            continue
        if not _is_leader():
            if not _LEAD.get("logged"):
                _LEAD["logged"] = True
                store.log("TELEGRAM", _LEAD["note"])
            _WAKE.wait(15.0)
            _WAKE.clear()
            continue
        _LEAD["logged"] = False
        try:
            if not _load().get("bot"):
                me(refresh=True)
                try:                             # long polling и webhook несовместимы
                    _call("deleteWebhook", {"drop_pending_updates": False},
                          timeout=20)
                except TgErr:
                    pass
            st = _load()
            ups = _call("getUpdates", {"offset": st.get("offset") or 0,
                                       "timeout": POLL_TIMEOUT,
                                       "allowed_updates": ["message"]},
                        timeout=HTTP_TIMEOUT) or []
            with _STATE_LOCK:
                st["last_ok"] = time.time()
                st["last_error"] = ""
            fail = 0
            for u in ups:
                with _STATE_LOCK:
                    st["offset"] = max(int(st.get("offset") or 0),
                                       int(u.get("update_id", 0)) + 1)
                _dispatch(u)
            if ups:
                _save()
        except TgErr as e:
            fail += 1
            _err(str(e))
            time.sleep(min(5.0 * fail, 60.0))
        except Exception as e:
            fail += 1
            _err("%s: %s" % (type(e).__name__, e))
            time.sleep(min(5.0 * fail, 60.0))


def start():
    if _TH[0] and _TH[0].is_alive():
        return
    _TH[0] = threading.Thread(target=_loop, daemon=True, name="tgbot-poll")
    _TH[0].start()
    _WAKE.set()


def wake():
    _WAKE.set()


def status():
    st = _load()
    return {"ok": True, "enabled": enabled(), "token_set": bool(token()),
            "thread": bool(_TH[0] and _TH[0].is_alive()),
            "bot": dict(st.get("bot") or {}),
            "chats": sorted((st.get("chats") or {}).values(),
                            key=lambda c: -float(c.get("seen") or 0))[:30],
            "stats": dict(st.get("stats") or {}),
            "leader": bool(_LEAD.get("fh")), "pid": os.getpid(),
            "leader_note": _LEAD.get("note") or "",
            "proxy": bool(_proxy()), "api_base": _api_base(),
            "allowed": str(_s().get("tg_allowed") or ""),
            "memory": _memory_on(),
            "last_ok": st.get("last_ok") or 0.0,
            "last_error": st.get("last_error") or ""}
