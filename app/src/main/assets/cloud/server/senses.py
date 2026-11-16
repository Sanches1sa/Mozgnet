# -*- coding: utf-8 -*-
"""
senses.py — v3.128: ГЛАЗА И УШИ МОЗГА для облака, ботов и LLM.

ГЛАЗА — уже имеющаяся камера (ESP32-CAM через плату neuro_12: кадры идут в
облако POST /api/vision/raw|frame и лежат в data/vision/<cam>/last.jpg).
На вопрос «что ты видишь?», «что на камере?», «покажи камеру» (чат панели,
МАКС, Telegram) мозг берёт ПОСЛЕДНИЙ кадр и отвечает:
  • сам кадр — картинкой (метка [ИЛЛЮСТРАЦИЯ]: панель показывает, МАКС и
    Telegram присылают фото);
  • что на нём — imgsense: яркость, цвета, «что где», текст (Tesseract),
    YOLO-объекты (если подключён), что изменилось с прошлого кадра;
  • описание сцены локальной моделью со зрением — если владелец её
    подключил (Настройки → API и ответы: «модель со зрением»), ей уходит
    сам кадр вместе с вопросом;
  • своя LLM пересказывает увиденное своими словами (по фактам кадра).
Честно: без модели со зрением мозг не узнаёт предметы по форме.

УШИ — микрофон самого мозга (плата neuro_12 v2.70+, USB-микрофон на UNO Q):
плата сама вырезает фразы по громкости и шлёт их WAV-ом в
POST /api/hearing/mic; облако распознаёт речь (Vosk) и запоминает
услышанное. На «что ты слышишь?», «что сказали?» мозг отвечает услышанным.
Фраза, обращённая к мозгу («мозг, …», «робот, …» — слова в настройке
mic_wake), идёт ему вопросом; ответ возвращается плате — она его
произносит. Модули KY-037/038 на D12/D13 дают только «громко/тихо»
(цифровой выход) — слов из них не получить.
"""
import hashlib
import io
import math
import os
import re
import threading
import time

import store

_LOCK = threading.RLock()
_DESC = {}                 # (cam, ts) -> описание
_PREV = {}                 # cam -> (ts, миниатюра 32x24) для «что изменилось»
CAM_FRESH_S = 600          # кадр старше — «камера давно молчит»
HEARD_MAX = 120

_SEE_Q = re.compile(
    r"(что|кого|кто)\s+(ты\s+)?(сейчас\s+)?(видишь|видно|видите|перед\s+(тобой|камерой))|"
    r"(что|кто)\s+(сейчас\s+)?(на|в)\s+камер|покажи\s+(мне\s+)?(камер|что\s+видишь|кадр|"
    r"изображени|картинк\w*\s+с\s+камер)|посмотри\s+(в\s+камер|вокруг|что\s+там)|"
    r"^\s*/?(камера|кам|cam|видишь|глаза)\s*$|(кадр|изображени\w*|картинк\w*)\s+с\s+камер|"
    r"что\s+там\s+(у\s+тебя\s+)?(на\s+)?камер|опиши\s+(что\s+видишь|кадр|камер)",
    re.I)
_HEAR_Q = re.compile(
    r"(что|кого|кто)\s+(ты\s+)?(сейчас\s+)?(слышишь|слышно|слышал\w*|услышал\w*)|"
    r"что\s+(тебе\s+)?(сказали|говорили|говорят)\s*(в\s+микрофон|рядом|вокруг)?\s*\??$|"
    r"(что|кто)\s+(сейчас\s+)?(в|на)\s+микрофон|^\s*/?(слышу|слух|уши|микрофон|mic)\s*$",
    re.I)


def ask_kind(text):
    t = " ".join(str(text or "").lower().replace("ё", "е").split())
    if not t or len(t) > 160:
        return ""
    if _SEE_Q.search(t):
        return "see"
    if _HEAR_Q.search(t):
        return "hear"
    return ""


# ------------------------------------------------------------------ ГЛАЗА
def latest_frame(cam=""):
    """-> (jpeg, cam, ts, names) самого свежего кадра (или кадра камеры cam)."""
    try:
        import vision
        cl = vision.cams()
    except Exception:
        return b"", "", 0.0, []
    if cam:
        cl = [c for c in cl if c.get("cam") == cam] or cl
    for c in cl:
        data = vision.last_jpeg(c["cam"])
        if data:
            return data, c["cam"], float(c.get("ts") or 0), list(c.get("names") or [])
    return b"", "", 0.0, []


def _thumb(data):
    try:
        from PIL import Image
        im = Image.open(io.BytesIO(data)).convert("L").resize((32, 24))
        return list(im.getdata())
    except Exception:
        return None


def _change(cam, ts, data):
    """Насколько кадр отличается от предыдущего (доля, 0..1) или None."""
    th = _thumb(data)
    if th is None:
        return None
    prev = _PREV.get(cam)
    _PREV[cam] = (ts, th)
    if not prev or prev[0] == ts:
        return None
    pt = prev[1]
    diff = sum(1 for a, b in zip(th, pt) if abs(a - b) > 28) / float(len(th))
    return diff


def _snapshot_marker(data, alt):
    """Кадр -> файл kbimg/ (его показывает панель, шлют МАКС и Telegram)."""
    try:
        import kbimages
        name = hashlib.sha1(data).hexdigest()[:20] + ".jpg"
        d = store.p("kbimg")
        os.makedirs(d, exist_ok=True)
        fp = os.path.join(d, name)
        if not os.path.isfile(fp):
            with open(fp, "wb") as f:
                f.write(data)
        # старые кадры камеры не копим: держим 40 последних
        lst = store.load_json(os.path.join("senses", "snaps.json"), []) or []
        if name not in lst:
            lst.append(name)
        for old in lst[:-40]:
            try:
                os.remove(os.path.join(d, old))
            except Exception:
                pass
        store.save_json(os.path.join("senses", "snaps.json"), lst[-40:])
        return kbimages.marker("kbimg:" + name, alt).strip("\n")
    except Exception:
        return ""


def _age_words(sec):
    sec = int(max(0, sec))
    if sec < 60:
        return "%d с назад" % sec
    if sec < 3600:
        return "%d мин назад" % (sec // 60)
    return "%d ч %d мин назад" % (sec // 3600, (sec % 3600) // 60)


def see_facts(question="", cam=""):
    """-> dict(ok, cam, age, desc, names, change, jpeg) | dict(ok=False, why)."""
    data, cam, ts, names = latest_frame(cam)
    if not data:
        return {"ok": False, "why": "камера ещё не прислала ни одного кадра. "
                "Кадры шлёт плата neuro_12: в её настройках задай cam_url "
                "(адрес ESP32-CAM или «uart») и включи камеру (cam_on)"}
    key = (cam, ts, question.strip().lower()[:60])
    with _LOCK:
        desc = _DESC.get(key)
        if desc is None:
            try:
                import imgsense
                desc = imgsense.describe(data, name="кадр камеры %s" % cam,
                                         question=_vision_q(question), learn=False)
            except Exception as e:
                desc = "Кадр разобрать не смогла: %s." % e
            _DESC.clear() if len(_DESC) > 40 else None
            _DESC[key] = desc
        ch = _change(cam, ts, data)
    return {"ok": True, "cam": cam, "ts": ts, "age": time.time() - ts if ts else 0,
            "desc": desc, "names": names, "change": ch, "jpeg": data}


def _vision_q(q):
    q = str(q or "").strip()
    if not q or ask_kind(q) == "see" and len(q) < 40:
        return "Это кадр камеры робота. Опиши по-русски, что на нём видно: " \
               "предметы, люди, обстановка. Коротко и по делу."
    return "Это кадр камеры робота. Вопрос владельца: %s. Ответь по-русски " \
           "по тому, что видно на кадре." % q


def _clean_desc(desc):
    """Убрать из описания imgsense строку-совет (её место — в конце ответа)."""
    out = []
    for ln in str(desc or "").split("\n"):
        if ln.startswith("Чего не умею"):
            continue
        out.append(ln.replace("Картинка ", "Кадр ", 1) if ln.startswith("Картинка ") else ln)
    return "\n".join(out).strip()


def _llm_ok(t, facts):
    """Фраза своей LLM годится, только если это связный пересказ фактов:
    слова целиком из фактов (не обрубки «малокон.») и порядок слов как в
    фактах. Иначе строку не показываем — лучше без неё, чем бред."""
    try:
        import llmreply
        fw = set(re.findall(r"[а-яёa-z0-9]+", str(facts).lower()))
        tw = re.findall(r"[а-яёa-z0-9]+", str(t).lower())
        if len(tw) < 4 or any(len(w) >= 4 and w not in fw for w in tw):
            return False
        return llmreply.order_ok(t, facts, 0.8)
    except Exception:
        return False


def see_answer(question=""):
    f = see_facts(question)
    if not f["ok"]:
        return "Сейчас ничего не вижу: %s." % f["why"], {"see": False}
    lines = ["📷 Камера «%s», кадр %s." % (f["cam"], _age_words(f["age"]) if f["ts"] else "")]
    if f["ts"] and f["age"] > CAM_FRESH_S:
        lines.append("_Это последний кадр — камера давно не присылала новых "
                     "(плата выключена или камера не отвечает)._")
    lines.append(_snapshot_marker(f["jpeg"], "кадр камеры %s, %s" % (
        f["cam"], time.strftime("%H:%M:%S", time.localtime(f["ts"] or time.time())))))
    lines.append(_clean_desc(f["desc"]))
    if f["names"]:
        lines.append("Распознанные объекты (YOLO): %s." % ", ".join(f["names"][:8]))
    if f["change"] is not None:
        ch = f["change"]
        lines.append("По сравнению с прошлым кадром: %s." % (
            "почти ничего не изменилось" if ch < 0.03 else
            "изменилось немного (%d%% кадра)" % round(ch * 100) if ch < 0.2 else
            "изменилось заметно (%d%% кадра) — что-то движется или сменилась сцена"
            % round(ch * 100)))
    scene = "Описание сцены (локальная модель со зрением)" in f["desc"]
    try:                                  # своя LLM — своими словами по фактам кадра
        import llmreply
        facts = " ".join(ln for ln in _clean_desc(f["desc"]).split("\n")
                         if ln and not ln.startswith("["))
        ls, why = llmreply._llm_summary(question or "что видно на камере", facts)
        if ls and _llm_ok(ls[0], facts):
            lines.append("**Своя LLM**\n%s\n_(своими словами по фактам кадра, опора %d%%)_"
                         % (ls[0], ls[1]))
    except Exception:
        pass
    if not scene:
        lines.append("_Предметы и людей по форме я пока не узнаю — вижу цвета, "
                     "яркость, текст и движение. Для настоящего описания сцены "
                     "подключи локальную модель со зрением (Настройки → API и "
                     "ответы, «модель со зрением», например llava через Ollama)._")
    try:
        store.thoughts_add("облако", "👁 Посмотрела в камеру «%s»: %s" % (
            f["cam"], _clean_desc(f["desc"]).split("\n")[0][:160]), kind="see")
    except Exception:
        pass
    return "\n\n".join(x for x in lines if x), {"see": True, "cam": f["cam"]}


# ------------------------------------------------------------------- УШИ
def heard(limit=8, max_age=None):
    lst = store.load_json(os.path.join("senses", "heard.json"), []) or []
    if max_age:
        lst = [h for h in lst if time.time() - float(h.get("ts") or 0) < max_age]
    return lst[-int(limit):]


def _heard_add(item):
    with _LOCK:
        lst = store.load_json(os.path.join("senses", "heard.json"), []) or []
        lst.append(item)
        store.save_json(os.path.join("senses", "heard.json"), lst[-HEARD_MAX:])


def _wake_words():
    s = store.settings().get("mic_wake") or "мозг, робот, нейро"
    return [w.strip().lower() for w in re.split(r"[,;]", str(s)) if w.strip()]


def _addressed(text):
    t = str(text or "").lower().strip()
    for w in _wake_words():
        if t.startswith(w + " ") or t.startswith(w + ",") or t == w:
            return t[len(w):].strip(" ,.!?")
    if store.settings().get("mic_answer_all"):
        return t
    return None


def mic_in(board, wav_b64, level=None):
    """Фраза с микрофона платы -> распознать, запомнить, ответить, если к мозгу."""
    import base64
    import hearing
    try:
        data = base64.b64decode(re.sub(r"^data:[^,]*,", "", str(wav_b64 or "")))
    except Exception:
        return {"ok": False, "error": "WAV не в base64"}
    if len(data) < 1000:
        return {"ok": False, "error": "слишком короткий звук"}
    a, why = hearing.decode(data, 30)
    if a is None:
        return {"ok": False, "error": why}
    props = hearing.analyze(a)
    text, why2 = hearing.transcribe(a)
    text = " ".join(str(text or "").split())
    board = str(board or "плата")[:40]
    item = {"ts": time.time(), "board": board, "text": text[:600],
            "kind": props.get("kind"), "sec": props.get("sec"),
            "loud_db": props.get("loud_db"), "level": level,
            "why": "" if text else (why2 or "слов не разобрано")}
    _heard_add(item)
    out = {"ok": True, "text": text, "kind": props.get("kind"),
           "props": props.get("text"), "asr_why": item["why"]}
    if text:
        q = _addressed(text)
        if q:
            try:
                import brain
                rep, zone, _i = brain.think(q, sender="Микрофон: %s" % board,
                                            chat_id="mic:%s" % board)
                spoken = _speakable(rep)
                out.update(reply=spoken, zone=zone, asked=q)
                item["asked"], item["reply"] = q, spoken[:400]
                _heard_add(dict(item, ts=time.time() + 0.001, text="", reply=spoken[:400],
                                kind="ответ"))
            except Exception as e:
                out["reply_err"] = str(e)[:120]
        try:             # мысль «услышала» — ПОСЛЕ ответа (иначе мозг цитирует её)
            store.thoughts_add(board, "🎤 Услышала в микрофон: «%s»" % text[:200], kind="hear")
        except Exception:
            pass
    store.log("СЛУХ", "микрофон %s: %s" % (board, text[:120] or ("(%s; %s)" % (
        props.get("text"), item["why"]))))
    return out


def _speakable(rep):
    """Ответ -> текст для речи: без меток, формул, служебных строк."""
    t = str(rep or "")
    try:
        import kbimages
        t = kbimages.strip(t)
    except Exception:
        pass
    t = re.sub(r"\[(ФОРМУЛА|КАРТИНКА)\][\s\S]*?\[/\1\]", " ", t)
    try:                                   # формулы — словами-символами, не LaTeX
        import latexlite
        t = latexlite.render_dollars(t, "plain")
    except Exception:
        t = re.sub(r"\$[^$]*\$", " ", t)
    keep = []
    for ln in t.split("\n"):
        s = ln.strip()
        if not s or s.startswith(("_", "**", "Источник", "#", "[", "🖼")):
            continue
        keep.append(s)
    t = " ".join(" ".join(keep).split())
    t = re.sub(r"^(Добрый\s+\w+|Привет\w*|Здравствуй\w*|Доброе\s+\w+|Доброй\s+\w+)"
               r"[^.!?]*[.!?]\s*", "", t)             # приветствие вслух — лишнее
    t = re.sub(r"🎤 Услышала в микрофон:.*$", "", t).strip()
    return t[:400]


def hear_answer(question=""):
    lst = [h for h in heard(30) if h.get("kind") != "ответ"]
    if not lst:
        try:
            import hearing
            st = hearing.status()
            asr = "Распознавание речи готово" if (st["vosk"] and st["model"]) else \
                "Распознавания речи на сервере пока нет (Настройки → Слух: поставить Vosk)"
        except Exception:
            asr = ""
        return ("Пока ничего не слышала: с микрофона мозга не пришло ни одной фразы. "
                "Нужен USB-микрофон на плате UNO Q и приложение neuro_12 v2.70+ "
                "(в его настройках — «микрофон»). %s." % asr), {"hear": False}
    lines = ["🎤 Что я слышала в микрофон мозга (последнее):"]
    for h in lst[-8:]:
        when = time.strftime("%H:%M:%S", time.localtime(h["ts"]))
        if h.get("text"):
            lines.append("• %s, %s: «%s»" % (when, h.get("board"), h["text"]))
        else:
            lines.append("• %s, %s: %s, %s с — слов не разобрала (%s)" % (
                when, h.get("board"), h.get("kind") or "звук", h.get("sec"), h.get("why")))
    last = lst[-1]
    lines.append("_Последний звук — %s._" % _age_words(time.time() - last["ts"]))
    try:
        import llmreply
        facts = " ".join("Услышано: %s." % h["text"] for h in lst[-5:] if h.get("text"))
        if facts:
            ls, _w = llmreply._llm_summary(question or "что ты слышишь", facts)
            if ls and _llm_ok(ls[0], facts):
                lines.append("**Своя LLM**\n%s\n_(своими словами по услышанному, опора %d%%)_"
                             % (ls[0], ls[1]))
    except Exception:
        pass
    return "\n".join(lines), {"hear": True}


def answer(question):
    k = ask_kind(question)
    if k == "see":
        return see_answer(question)
    if k == "hear":
        return hear_answer(question)
    return None, None


def status():
    data, cam, ts, _n = latest_frame()
    h = heard(1)
    try:
        import hearing
        st = hearing.status()
    except Exception:
        st = {}
    return {"ok": True, "cam": cam, "cam_age": int(time.time() - ts) if ts else None,
            "cam_bytes": len(data), "heard_last": h[-1] if h else None,
            "heard_n": len(heard(HEARD_MAX)), "vosk": st.get("vosk"),
            "vosk_model": st.get("model"),
            "vision_llm": bool(store.settings().get("vision_llm_on")),
            "wake": _wake_words()}
