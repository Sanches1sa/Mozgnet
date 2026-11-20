# -*- coding: utf-8 -*-
# ===========================================================================
# hearing.py — «СЛУХ» ОБЛАКА: мозг слышит ЗВУК ВИДЕО (v3.39)
# ---------------------------------------------------------------------------
# Что умеет (честно, по уровням):
#   1) СВОЙСТВА ЗВУКА (всегда, только stdlib): длительность, громкость,
#      доля тишины, похоже ли на речь / музыку / шум, темп. Нужна дорожка
#      в виде WAV (её делает ffmpeg, если он есть; чистый WAV читается сам).
#   2) РЕЧЬ В ТЕКСТ (если установлен offline-распознаватель Vosk и русская
#      модель): мозг получает, что СКАЗАНО в видео, даже без субтитров.
#   3) ЗВУК ИЗ РОЛИКА: прямая ссылка на аудиодорожку берётся со страницы
#      ролика (если YouTube отдаёт её без шифра), качается кусок первых
#      минут (hear_max_s) — через webfetch (https, публичные адреса, лимит).
# Без ffmpeg/Vosk уровни 2–3 честно сообщают, чего не хватает; мозг при
# этом читает субтитры, как раньше (media.py).
# Установка: панель LLM/«Слух» -> кнопки; либо `pip install vosk`, модель
# в data/models/vosk-ru, ffmpeg в PATH (winget install ffmpeg).
# ===========================================================================
import array
import io
import math
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import wave

import store

SR = 16000
_VOSK_URL = "https://alphacephei.com/vosk/models/vosk-model-small-ru-0.22.zip"
_INST = {"state": "idle", "log": "", "ts": 0.0}
_MODEL = [None]


# ------------------------------- инструменты --------------------------------
def ffmpeg_path():
    exe = "ffmpeg.exe" if os.name == "nt" else "ffmpeg"
    for cand in (os.path.join(store.p("bin"), exe), shutil.which("ffmpeg")):
        if cand and os.path.isfile(cand):
            return cand
    return ""


def model_dir():
    d = store.p("models", "vosk-ru")
    if os.path.isdir(d):
        return d
    # v3.178: пакет Windows несёт модель с собой (runtime/vosk-ru) — без скачивания
    rt = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "runtime", "vosk-ru")
    return rt if os.path.isdir(rt) else d


def have_vosk():
    try:
        import vosk  # noqa: F401
        return True
    except Exception:
        return False


def have_model():
    d = model_dir()
    return os.path.isdir(d) and bool(os.listdir(d))


def status():
    return {"ffmpeg": bool(ffmpeg_path()), "vosk": have_vosk(),
            "model": have_model(), "install": dict(_INST),
            "asr_ready": have_vosk() and have_model() and bool(ffmpeg_path())}


# ------------------------------ декодирование -------------------------------
def _wav_to_samples(data):
    """RIFF WAV -> (samples моно int16, sr). Любая частота/каналы 8/16 бит."""
    with wave.open(io.BytesIO(data), "rb") as w:
        n, ch, sw, sr = w.getnframes(), w.getnchannels(), w.getsampwidth(), \
            w.getframerate()
        raw = w.readframes(n)
    if sw == 1:
        a = array.array("h", [(b - 128) << 8 for b in raw])
    elif sw == 2:
        a = array.array("h")
        a.frombytes(raw[:len(raw) // 2 * 2])
        if sys.byteorder == "big":
            a.byteswap()
    else:
        raise ValueError("WAV %d бит не поддерживается" % (8 * sw))
    if ch > 1:
        a = array.array("h", [int(sum(a[i:i + ch]) / ch)
                              for i in range(0, len(a) - ch + 1, ch)])
    return a, sr


def _resample(a, sr, to=SR):
    if sr == to or not a:
        return a
    step = sr / float(to)
    out = array.array("h")
    i = 0.0
    while int(i) < len(a):
        out.append(a[int(i)])
        i += step
    return out


def decode(data, max_s=240):
    """Байты аудио/видео -> (samples int16 16 кГц моно, причина_неудачи).
    WAV читаем сами; всё остальное (mp4/m4a/webm/mp3…) — через ffmpeg."""
    if not data:
        return None, "нет данных"
    if data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        try:
            a, sr = _wav_to_samples(data)
            return _resample(a, sr)[:SR * max_s], ""
        except Exception as e:
            return None, "WAV не прочитался: %s" % e
    ff = ffmpeg_path()
    if not ff:
        return None, ("нет ffmpeg — не из чего получить звук (Windows: "
                      "winget install ffmpeg, затем перезапуск сервера)")
    fd, src = tempfile.mkstemp(suffix=".bin")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        flags = {}
        if os.name == "nt":
            flags["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        r = subprocess.run([ff, "-v", "error", "-i", src, "-t", str(max_s),
                            "-vn", "-ac", "1", "-ar", str(SR), "-f", "s16le",
                            "pipe:1"], stdin=subprocess.DEVNULL,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           timeout=120, **flags)
        if r.returncode != 0 or not r.stdout:
            return None, "ffmpeg не смог разобрать звук: %s" % \
                r.stderr.decode("utf-8", "replace")[-160:]
        a = array.array("h")
        a.frombytes(r.stdout[:len(r.stdout) // 2 * 2])
        return a, ""
    except Exception as e:
        return None, "ffmpeg: %s: %s" % (type(e).__name__, e)
    finally:
        try:
            os.remove(src)
        except Exception:
            pass


# --------------------------------- анализ -----------------------------------
def _acf_peak(env, lo, hi):
    """Лучший лаг автокорреляции огибающей в диапазоне [lo, hi] кадров и
    сила пика (0..1)."""
    n = len(env)
    if n < hi + 5:
        return 0, 0.0
    m = sum(env) / n
    e = [x - m for x in env]
    v0 = sum(x * x for x in e) or 1e-9
    best, bl = 0.0, 0
    for lag in range(lo, hi + 1):
        c = sum(e[i] * e[i + lag] for i in range(n - lag)) / v0
        if c > best:
            best, bl = c, lag
    return bl, max(0.0, best)


def hum_check(a, sr=SR):
    """v3.186.1: сетевой гул 50 Гц. Возврат (доля энергии ниже 100 Гц 0..1,
    громкость речевой полосы 300–3400 Гц). Без numpy — оценка по разности
    соседних отсчётов (фильтр верхних частот)."""
    n = len(a) if a else 0
    if n < sr // 4:
        return 0.0, 0.0
    try:
        import numpy as np
        x = np.asarray(a, dtype=np.float64)
        X = np.abs(np.fft.rfft(x * np.hanning(n))) ** 2
        f = np.fft.rfftfreq(n, 1.0 / sr)
        tot = float(X.sum()) or 1.0
        low = float(X[f < 100].sum()) / tot
        voice = float(np.sqrt(X[(f >= 300) & (f <= 3400)].sum() / tot) * x.std())
        return low, voice
    except Exception:
        pass
    s2 = sum(v * v for v in a) / float(n)
    d2 = sum((a[i] - a[i - 1]) ** 2 for i in range(1, n)) / float(n - 1)
    rms, hp = math.sqrt(s2), 2.5 * math.sqrt(d2)
    return (max(0.0, 1.0 - hp / rms) if rms > 1 else 0.0), hp


def dehum(a, sr=SR):
    """v3.186.1: вырезать сетевой гул: всё ниже 80 Гц и узкие полосы ±3 Гц
    вокруг 100/150/…/450 Гц (гармоники 50 Гц). Речь почти не задевается.
    Нужен numpy; без него звук возвращается как есть."""
    try:
        import numpy as np
    except Exception:
        return a
    n = len(a)
    if n < sr // 4:
        return a
    x = np.asarray(a, dtype=np.float64)
    X = np.fft.rfft(x)
    f = np.fft.rfftfreq(n, 1.0 / sr)
    X[f < 80] = 0
    for h in range(100, 500, 50):
        X[np.abs(f - h) <= 3] = 0
    y = np.clip(np.fft.irfft(X, n), -32768, 32767).astype(np.int16)
    out = array.array("h")
    out.frombytes(y.tobytes())
    return out


def analyze(a, sr=SR):
    """Свойства звука: {sec, loud_db, silence, zcr, speech, beat_bpm, kind,
    text}. Чистый stdlib; на минуту звука — доли секунды."""
    if not a or len(a) < sr // 2:
        return {"ok": False, "text": "звука почти нет"}
    fl = int(sr * 0.03)                       # кадр 30 мс
    rms, zc = [], []
    for i in range(0, len(a) - fl, fl):
        fr = a[i:i + fl]
        s = sum(x * x for x in fr) / fl
        rms.append(math.sqrt(s))
        z = sum(1 for j in range(1, fl) if (fr[j - 1] < 0) != (fr[j] < 0))
        zc.append(z / fl)
    peak = max(rms) or 1.0
    db = [20 * math.log10(max(r, 1.0) / 32768.0) for r in rms]
    thr = max(150.0, 0.05 * peak)
    silence = sum(1 for r in rms if r < thr) / len(rms)
    live = [d for d, r in zip(db, rms) if r >= thr] or db
    loud = sum(live) / len(live)             # громкость по звучащим кадрам
    zcr = sum(zc) / len(zc)
    # огибающая ~33 кадра/с: слоговый ритм речи 2–8 Гц -> лаг 4–16 кадров
    env = [math.sqrt(sum(rms[i:i + 2]) / 2) for i in range(0, len(rms) - 1)]
    _l, syl = _acf_peak(env[:900], 4, 16)
    # ритм музыки 0.35–1.0 с -> лаг 10–33 кадров
    bl, beat = _acf_peak(env[:900], 10, 33)
    bpm = int(60.0 / (bl * 0.03)) if bl and beat > 0.25 else 0
    # изменчивость громкости (речь «дышит», музыка ровнее)
    mean_r = sum(rms) / len(rms)
    cv = (sum((r - mean_r) ** 2 for r in rms) / len(rms)) ** 0.5 / \
        (mean_r or 1.0)
    if silence > 0.9:
        kind = "тишина"
    elif zcr > 0.35 and syl < 0.3:
        kind = "шум"
    elif beat > 0.45 and cv < 0.9:
        kind = "музыка"
    elif syl > 0.2 or (0.1 < silence < 0.6 and cv > 0.6):
        kind = "речь"
    else:
        kind = "музыка" if beat > 0.3 else "шум"
    sec = len(a) / float(sr)
    out = {"ok": True, "sec": round(sec, 1), "loud_db": round(loud, 1),
           "silence": round(silence, 2), "zcr": round(zcr, 3),
           "speech": round(syl, 2), "beat": round(beat, 2),
           "beat_bpm": bpm, "kind": kind}
    desc = {"тишина": "почти тишина", "шум": "шум без речи и ритма",
            "музыка": "музыка (ровный ритм)", "речь": "речь"}[kind]
    out["text"] = "%s, %d с звука, громкость %d дБ, пауз %d%%%s" % (
        desc, int(sec), int(loud), int(100 * silence),
        (", темп ≈ %d уд/мин" % bpm) if kind == "музыка" and bpm else "")
    return out


# ------------------------------- речь в текст -------------------------------
def transcribe(a):
    """Vosk: samples -> текст | ('' , причина)."""
    if not have_vosk():
        return "", "не установлен распознаватель речи Vosk"
    if not have_model():
        return "", "нет русской модели Vosk (data/models/vosk-ru)"
    try:
        import json
        import vosk
        vosk.SetLogLevel(-1)
        if _MODEL[0] is None:
            _MODEL[0] = vosk.Model(model_dir())
        rec = vosk.KaldiRecognizer(_MODEL[0], SR)
        raw = a.tobytes() if sys.byteorder == "little" else \
            bytes(bytearray(a))
        parts = []
        for i in range(0, len(raw), 8000):
            if rec.AcceptWaveform(raw[i:i + 8000]):
                t = json.loads(rec.Result()).get("text") or ""
                if t:
                    parts.append(t)
        t = json.loads(rec.FinalResult()).get("text") or ""
        if t:
            parts.append(t)
        return " ".join(parts), ""
    except Exception as e:
        return "", "Vosk: %s: %s" % (type(e).__name__, e)


# ------------------------------ звук из ролика ------------------------------
def audio_url(formats):
    """Из adaptiveFormats плеера YouTube — самая лёгкая аудиодорожка с
    ПРЯМОЙ ссылкой (без signatureCipher). '' если такой нет."""
    best, bb = "", 10 ** 9
    for f in formats or []:
        if not isinstance(f, dict):
            continue
        if not str(f.get("mimeType") or "").startswith("audio/"):
            continue
        u = f.get("url")
        if not u:
            continue                       # шифр: без внешних расшифровщиков
        br = int(f.get("bitrate") or 0) or 10 ** 6
        if br < bb:
            best, bb = u, br
    return best


def fetch_audio(url, max_s=240):
    """Первые ~max_s секунд дорожки (по размеру ~16 КБ/с с запасом)."""
    import webfetch
    cap = min(14 * 1024 * 1024, max(400000, int(max_s * 20000)))
    u = url + ("&" if "?" in url else "?") + "range=0-%d" % cap
    ok, data, _n, _ct = webfetch.fetch_file(u, timeout=40, max_bytes=cap + 65536)
    if not ok:
        return None, str(data)[:140]
    return data, ""


def hear_video(v):
    """Видео из media.watch -> {ok, props, asr, text, how, err}. Не бросает."""
    s = store.settings()
    if not s.get("media_hear", True):
        return {"ok": False, "err": "слух выключен"}
    max_s = int(s.get("hear_max_s") or 240)
    out = {"ok": False, "props": None, "asr": "", "text": "", "how": "",
           "err": ""}
    try:
        url = v.get("audio_url") or ""
        if not url:
            out["err"] = ("площадка (%s) не отдала прямую ссылку на звук "
                          "ролика — слышать нечего" % (v.get("platform") or
                                                       "YouTube"))
            return out
        data, why = fetch_audio(url, max_s)
        if not data:
            out["err"] = "звук не скачался: %s" % why
            return out
        a, why = decode(data, max_s)
        if a is None:
            out["err"] = why
            return out
        out["props"] = analyze(a)
        out["text"] = out["props"].get("text", "")
        if out["props"].get("kind") in ("речь", "музыка"):
            t, why2 = transcribe(a)
            out["asr"] = t[:60000]
            if not t and why2:
                out["how"] = why2
        out["ok"] = True
    except Exception as e:
        out["err"] = "%s: %s" % (type(e).__name__, e)
    return out


def hear_file(data, name=""):
    """Звук/видео-файл (панель, MAX) -> то же, что hear_video."""
    s = store.settings()
    a, why = decode(data, int(s.get("hear_max_s") or 240))
    if a is None:
        return {"ok": False, "err": why}
    out = {"ok": True, "props": analyze(a), "asr": "", "how": ""}
    out["text"] = out["props"].get("text", "")
    t, why2 = transcribe(a)
    out["asr"] = t[:60000]
    if not t:
        out["how"] = why2
    return out


# ------------------------- отчёт «послушала файл» ---------------------------
MEDIA_EXT = (".mp4", ".m4a", ".mp3", ".wav", ".ogg", ".oga", ".opus", ".webm",
             ".mov", ".avi", ".mkv", ".flac", ".aac", ".amr", ".3gp", ".wma")


def is_media_name(name):
    return str(name or "").lower().endswith(MEDIA_EXT)


def report(data, name="файл", source="файл"):
    """Звуковой/видео файл -> (ответ человеку, ok). Слух мозга: свойства
    звука; речь в текст (если есть Vosk) — текст идёт в базу знаний
    («Звук: имя»), в сеть материалов и в мысль «🎧». Повтор того же файла
    (по md5) заново не учится. Не бросает."""
    import hashlib
    name = str(name or "файл")[:100]
    try:
        r = hear_file(data, name)
    except Exception as e:
        return "Не смогла послушать «%s»: %s" % (name, e), False
    if not r.get("ok"):
        return "Не смогла послушать «%s»: %s." % (name, r.get("err") or "?"), \
            False
    out = ["Послушала «%s»: %s." % (name, r.get("text") or "звук есть")]
    asr = (r.get("asr") or "").strip()
    learned = False
    if asr:
        out.append("Что сказано: %s" % (asr[:700] + ("…" if len(asr) > 700
                                                     else "")))
        h = hashlib.md5(data[:2000000]).hexdigest()
        seen = store.load_json("llmheard.json", []) or []
        if h in seen:
            out.append("(этот файл я уже слушала раньше — в базе он есть)")
        elif len(asr) >= 60:
            try:
                import kb
                kb.add_article("Звук: %s" % name[:80],
                               "Услышано в файле «%s» (%s).\n\n%s" % (
                                   name, r.get("text", ""), asr),
                               source=("%s: звук" % source)[:120])
                seen.append(h)
                store.save_json("llmheard.json", seen[-300:])
                learned = True
                out.append("Запомнила в базе знаний — спрашивай по содержанию.")
            except Exception:
                pass
    elif r.get("how"):
        out.append("Слов не разобрала: %s." % r["how"])
    try:
        store.thoughts_add("облако", "🎧 Услышала «%s»: %s%s" % (
            name[:60], r.get("text", ""), " Речь распознана." if asr else ""),
            kind="hear")
    except Exception:
        pass
    return "\n".join(out), True


# -------------------------------- установка ---------------------------------
def install_vosk():
    """Фон: pip install vosk + русская small-модель (~45 МБ) в data/models."""
    if _INST["state"] == "running":
        return status()
    _INST.update(state="running", log="ставлю vosk…", ts=time.time())

    def job():
        flags = {}
        if os.name == "nt":
            flags["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            if not have_vosk():
                ok = False
                for extra in ([], ["--user"]):
                    r = subprocess.run(
                        [sys.executable, "-m", "pip", "install",
                         "--disable-pip-version-check", "vosk"] + extra,
                        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT, timeout=900, **flags)
                    _INST["log"] = r.stdout.decode("utf-8", "replace")[-800:]
                    if r.returncode == 0:
                        ok = True
                        break
                if not ok:
                    _INST["state"] = "error"
                    return
                import importlib
                importlib.invalidate_caches()
            if not have_model():
                _INST["log"] = "качаю русскую модель (~45 МБ)…"
                import webfetch
                import zipfile
                ok, data, _n, _ct = webfetch.fetch_file(
                    _VOSK_URL, timeout=300, max_bytes=120 * 1024 * 1024)
                if not ok:
                    _INST.update(state="error", log="модель не скачалась: "
                                 + str(data)[:200])
                    return
                tmp = store.p("models", "_tmp")
                shutil.rmtree(tmp, ignore_errors=True)
                os.makedirs(tmp, exist_ok=True)
                with zipfile.ZipFile(io.BytesIO(data)) as z:
                    z.extractall(tmp)
                inner = [d for d in os.listdir(tmp)
                         if os.path.isdir(os.path.join(tmp, d))]
                src = os.path.join(tmp, inner[0]) if inner else tmp
                shutil.rmtree(model_dir(), ignore_errors=True)
                shutil.move(src, model_dir())
                shutil.rmtree(tmp, ignore_errors=True)
            _MODEL[0] = None
            _INST.update(state="done", log="Vosk и русская модель готовы")
        except Exception as e:
            _INST.update(state="error", log="%s: %s" % (type(e).__name__, e))

    threading.Thread(target=job, daemon=True, name="vosk-install").start()
    return status()
