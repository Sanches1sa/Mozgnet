# micstream.py — v3.178: ЖИВОЙ ЗВУК С МИКРОФОНА ПЛАТЫ И ПРОВЕРКА УШЕЙ/ГЛАЗ.
#
# Плата (v2.93+) в «связи мозгов» (/api/link) присылает состояние микрофона
# (mic) и камеры (cam, при UART — счётчики приёмника). Владелец в облаке
# (⚙ → Общие → «🎤 Микрофон и 📷 камера плат») жмёт «Слушать»: облако в
# ответе link отдаёт mic_live=N секунд, плата шлёт сырой PCM 16 кГц кусками
# в POST /api/hearing/stream, облако копит последние 20 с на плату и отдаёт
# их WAV-ом (GET /api/hearing/live.wav) — браузер играет звук. Последняя
# распознанная фраза тоже хранится WAV-ом (GET /api/hearing/phrase.wav).
#
# Всё в памяти процесса (последние секунды), на диск — только последняя
# фраза каждой платы (data/hearing/<плата>.wav), чтобы её можно было
# переслушать после перезапуска.
import base64
import io
import os
import re
import threading
import time
import wave

import store

KEEP_SEC = 20.0
_LOCK = threading.RLock()
_LIVE = {}       # плата -> {"pcm": bytearray, "sr": 16000, "ts", "chunks", "rms", "bytes"}
_WANT = {}       # плата|"*" -> до какого времени держать живой звук
_MIC = {}        # плата -> состояние микрофона из link + ts
_CAM = {}        # плата -> состояние камеры из link + ts


def _bname(b):
    b = str(b or "плата")[:40]
    return re.sub(r"[^\w.\-]", "_", b) or "board"


def note_link(board, mic=None, cam=None):
    """Из /api/link: запомнить состояние ушей и глаз платы."""
    board = str(board or "?")[:40]
    now = time.time()
    with _LOCK:
        if isinstance(mic, dict):
            _MIC[board] = dict({k: mic.get(k) for k in (
                "state", "err", "phrases", "sent", "heard", "last", "rms", "floor",
                "on", "dev", "queue", "live", "live_sent", "live_err", "reply", "vosk", "vr")}, ts=now)
        if isinstance(cam, dict):
            _CAM[board] = dict({k: cam.get(k) for k in (
                "state", "err", "up_err", "kb", "ok_age", "sent_age", "url", "on",
                "uart")}, ts=now)


def want_for(board):
    """Сколько секунд живого звука просить у платы в ответе link (0 — не надо)."""
    now = time.time()
    with _LOCK:
        until = max(_WANT.get(str(board or "")[:40], 0), _WANT.get("*", 0))
    left = until - now
    return int(min(30, left)) + 1 if left > 0 else 0


def set_want(board, on, sec=60):
    board = str(board or "*")[:40] or "*"
    with _LOCK:
        if on:
            _WANT[board] = time.time() + max(5, min(600, float(sec or 60)))
        else:
            _WANT.pop(board, None)
            if board == "*":
                _WANT.clear()
    store.log("СЛУХ", "живой звук %s: %s" % (board, "вкл" if on else "выкл"))
    return {"ok": True, "board": board, "on": bool(on)}


def stream_in(board, pcm_b64, sr=16000, seq=0, rms=None):
    """Кусок сырого PCM s16le mono с платы."""
    board = str(board or "плата")[:40]
    try:
        raw = base64.b64decode(str(pcm_b64 or ""))
    except Exception:
        return {"ok": False, "error": "PCM не в base64"}
    if not raw:
        return {"ok": False, "error": "пустой кусок"}
    try:
        sr = int(sr or 16000)
    except (TypeError, ValueError):
        sr = 16000
    if sr < 4000 or sr > 48000:
        return {"ok": False, "error": "частота %s не поддерживается" % sr}
    raw = raw[:len(raw) // 2 * 2]
    with _LOCK:
        d = _LIVE.get(board)
        if not d or d.get("sr") != sr:
            d = _LIVE[board] = {"pcm": bytearray(), "sr": sr, "chunks": 0, "bytes": 0,
                                "t0": time.time()}
            store.log("СЛУХ", "живой звук с %s пошёл (%d Гц)" % (board, sr))
        d["pcm"].extend(raw)
        cap = int(KEEP_SEC * sr) * 2
        if len(d["pcm"]) > cap:
            del d["pcm"][:len(d["pcm"]) - cap]
        d["chunks"] += 1
        d["bytes"] += len(raw)
        d["ts"] = time.time()
        d["seq"] = seq
        d["rms"] = rms
        d["peak"] = _peak(raw)
    stop = want_for(board) == 0
    return {"ok": True, "stop": stop}


def _peak(raw):
    import array
    a = array.array("h")
    a.frombytes(raw)
    return max((abs(x) for x in a), default=0)


def _wav(pcm, sr):
    b = io.BytesIO()
    w = wave.open(b, "wb")
    w.setnchannels(1)
    w.setsampwidth(2)
    w.setframerate(sr)
    w.writeframes(bytes(pcm))
    w.close()
    return b.getvalue()


def live_wav(board=None, sec=5):
    """Последние sec секунд живого звука платы WAV-ом (None — звука нет)."""
    with _LOCK:
        if not board:
            if not _LIVE:
                return None
            board = max(_LIVE, key=lambda k: _LIVE[k].get("ts", 0))
        d = _LIVE.get(str(board)[:40])
        if not d or not d["pcm"]:
            return None
        try:
            sec = max(0.5, min(KEEP_SEC, float(sec or 5)))
        except (TypeError, ValueError):
            sec = 5.0
        n = int(sec * d["sr"]) * 2
        return _wav(d["pcm"][-n:], d["sr"])


def save_phrase(board, wav_bytes):
    """Последняя фраза платы (WAV, как пришёл) — на диск, чтобы переслушать."""
    try:
        os.makedirs(store.p("hearing"), exist_ok=True)
        with open(store.p("hearing", _bname(board) + ".wav"), "wb") as f:
            f.write(wav_bytes)
    except Exception:
        pass


def phrase_wav(board=None):
    try:
        d = store.p("hearing")
        if not os.path.isdir(d):
            return None
        names = [n for n in os.listdir(d) if n.endswith(".wav")]
        if board:
            want = _bname(board) + ".wav"
            names = [n for n in names if n == want]
        if not names:
            return None
        names.sort(key=lambda n: os.path.getmtime(os.path.join(d, n)), reverse=True)
        with open(os.path.join(d, names[0]), "rb") as f:
            return f.read()
    except Exception:
        return None


def status():
    """Сводка для карточки в облаке."""
    now = time.time()
    with _LOCK:
        boards = sorted(set(_MIC) | set(_CAM) | set(_LIVE))
        out = []
        for b in boards:
            m = _MIC.get(b) or {}
            c = _CAM.get(b) or {}
            lv = _LIVE.get(b) or {}
            out.append({
                "board": b,
                "mic": dict(m, age=int(now - m["ts"])) if m else None,
                "cam": dict(c, age=int(now - c["ts"])) if c else None,
                "live": {"sec": round(len(lv.get("pcm") or b"") / 2.0 / (lv.get("sr") or 16000), 1),
                         "age": int(now - lv["ts"]) if lv.get("ts") else None,
                         "chunks": lv.get("chunks", 0), "kb": int(lv.get("bytes", 0) / 1024),
                         "rms": lv.get("rms"), "peak": lv.get("peak"),
                         "sr": lv.get("sr")} if lv else None,
                "want": want_for(b) > 0,
            })
        want_all = _WANT.get("*", 0) > now
    try:
        import senses
        heard = list(reversed(senses.heard(8)))
    except Exception:
        heard = []
    return {"ok": True, "boards": out, "want_all": want_all, "heard": heard,
            "phrase": bool(phrase_wav())}


def record_board(board, sec=5.0):
    """v3.178: голосовой ввод с микрофона платы — включить живой звук,
    дождаться sec секунд НОВОГО звука и вернуть WAV (или (None, причина))."""
    board = str(board or "")[:40]
    try:
        sec = max(1.0, min(15.0, float(sec or 5)))
    except (TypeError, ValueError):
        sec = 5.0
    with _LOCK:
        d = _LIVE.get(board) or {}
        start = d.get("bytes", 0)
        _WANT[board] = max(_WANT.get(board, 0), time.time() + sec + 20)
    t_end = time.time() + sec + 15        # плата узнаёт о просьбе за ≤5 с (link)
    while time.time() < t_end:
        time.sleep(0.3)
        with _LOCK:
            d = _LIVE.get(board)
            if not d:
                continue
            got = d["bytes"] - start if d["bytes"] >= start else d["bytes"]
            if got >= int(sec * d["sr"]) * 2:
                n = min(len(d["pcm"]), got)
                return _wav(d["pcm"][-n:], d["sr"]), ""
    return None, ("плата %s не прислала звук: нужна плата v2.93+ с включённым "
                  "микрофоном (mic_on) и arecord" % board)
