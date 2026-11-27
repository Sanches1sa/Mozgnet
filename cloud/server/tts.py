# -*- coding: utf-8 -*-
# ===========================================================================
# tts.py — ВСТРОЕННЫЙ «НЕЙРОГОЛОС» облака: текст -> WAV, БЕЗ внешних движков
# ---------------------------------------------------------------------------
# Работает ВСЕГДА (мозг neuro_12 ждёт от POST /api/tts честный RIFF-WAV).
# Синтез рече-подобный: гласные — двухформантные тоны с огибающей,
# согласные — приглушённый шум/свист, паузы по словам и знакам, интонация
# (понижение тона к концу предложения, подъём на вопросе). Детерминированно:
# «голос» один и тот же для одного текста. Если в системе есть espeak —
# используем его (звучит живее), иначе — встроенный синтез. Silero/Piper
# не требуются и не устанавливаются.
# ===========================================================================
import math
import io
import os
import struct
import subprocess
import tempfile
import wave

_RATE = 8000


def _vowel_formants(ch):
    """Две формантные частоты для гласной (Гц) — грубая модель русской речи."""
    f = {
        "а": (730, 1090), "я": (700, 1220), "о": (570, 840),
        "ё": (560, 900), "у": (300, 870), "ю": (320, 1000),
        "и": (300, 2300), "ы": (450, 1900), "э": (530, 1840),
        "е": (480, 2000), "a": (730, 1090), "e": (480, 2000),
        "i": (300, 2300), "o": (570, 840), "u": (300, 870),
        "y": (450, 1900),
    }
    return f.get(ch, (500, 1500))


def _synth_word(samples, word, pitch, vibrato, amp):
    """Синтез одного слова: гласные — форманты, согласные — шум/свист."""
    prev_v = False
    for ch in word.lower():
        if ch in "аеёиоуыэюяaeiouy":
            f1, f2 = _vowel_formants(ch)
            ms = 95 if prev_v else 120
            n = int(_RATE * ms / 1000)
            base = pitch * (1.0 + 0.06 * math.sin(len(samples) / 900.0))
            for i in range(n):
                t = i / n
                env = math.sin(math.pi * min(1.0, t)) ** 0.7
                ph = 2 * math.pi * base * i / _RATE
                vib = 1.0 + vibrato * math.sin(2 * math.pi * 5.5 * i / _RATE)
                s1 = math.sin(2 * math.pi * f1 * i / _RATE)
                s2 = 0.55 * math.sin(2 * math.pi * f2 * vib * i / _RATE)
                s3 = 0.25 * math.sin(ph)
                samples.append(int(amp * env * (s1 + s2 + s3) / 1.8 * 32767))
            prev_v = True
        elif ch.isalpha():
            ms = 55
            n = int(_RATE * ms / 1000)
            hard = ch in "кпстфхцчшщ"
            freq = 2400 if ch in "сзцчщш" else (1200 if hard else 700)
            for i in range(n):
                t = i / n
                env = math.sin(math.pi * min(1.0, t)) ** 0.5
                noise = ((hash((ch, i)) % 2000) / 1000.0 - 1.0) * 0.9
                tone = 0.3 * math.sin(2 * math.pi * freq * i / _RATE)
                samples.append(int(amp * env * (noise * 0.8 + tone) / 1.6
                                   * 32767 * 0.7))
            prev_v = False
        else:
            prev_v = False


def _wav_from_samples(samples, path):
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(_RATE)
        frames = b"".join(
            struct.pack("<h", max(-32767, min(32767, s))) for s in samples)
        w.writeframes(frames)


def synth_builtin(text, voice="neuro"):
    """Встроенный синтез: возврат WAV-байтов (всегда валидный RIFF)."""
    text = " ".join(str(text or "").split())[:600]
    if not text:
        text = "."
    pitch = {"male": 96, "robot": 110, "female": 175}.get(
        str(voice or "").lower(), 136)
    vibrato = 0.05 if str(voice or "").lower() == "robot" else 0.025
    amp = 0.9 if str(voice or "").lower() == "robot" else 0.78
    samples = []
    words = text.replace("ё", "ё").split(" ")
    sent_end = False
    for wi, word in enumerate(words):
        clean = "".join(c for c in word if c.isalpha())
        digits = "".join(c for c in word if c.isdigit())
        if digits:
            for d in digits:
                _synth_word(samples, "оу" if d in "01" else "эиа", pitch, 0, amp)
        if clean:
            q = word.rstrip("").endswith("?")
            p = pitch * (1.12 if q else 1.0) * (
                1.0 - 0.05 * (1 if sent_end else 0))
            _synth_word(samples, clean, p, vibrato, amp)
        sent_end = word.rstrip("").endswith((".", "!", "?", "…"))
        # пауза: межсловная / межфразная
        gap = int(_RATE * (0.28 if sent_end else 0.07))
        samples.extend([0] * gap)
    if len(samples) > _RATE * 12:            # потолок ~12 c
        samples = samples[:_RATE * 12]
    # v3.186.2: WAV собираем в памяти. Раньше общий файл n12_tts_<pid>.wav во
    # временной папке: два одновременных запроса /api/tts удаляли файл друг у
    # друга -> FileNotFoundError и 500 у платы/чата.
    buf = io.BytesIO()
    _wav_from_samples(samples or [0] * 100, buf)
    return buf.getvalue()


def synth_espeak(text, voice="neuro"):
    """Попытка espeak-ng/espeak (если стоит в системе) -> WAV-байты|None."""
    exe = None
    for c in ("espeak-ng", "espeak"):
        try:
            if subprocess.run([c, "--version"], capture_output=True, timeout=5).returncode:
                continue
            exe = c
            break
        except Exception:
            continue
    if not exe:
        return None
    ru_v = {"male": "ru+12", "robot": "ru+whisper", "female": "ru+f3"}.get(
        str(voice or "").lower(), "ru")
    text=' '.join(str(text).split())
    if not text or len(text)>600:return None
    try:
        with tempfile.TemporaryDirectory(prefix='n12-ru-') as folder:
            path=os.path.join(folder,'speech.wav')
            result=subprocess.run([exe,'-v',ru_v,'-s','150','-w',path,'--stdin'],input=text.encode('utf-8'),capture_output=True,timeout=40)
            if result.returncode:return None
            with open(path,'rb') as f:data=f.read()
            if data[:4]==b'RIFF' and len(data)>100:return data
    except Exception:pass
    return None


def synth(text, voice="neuro"):
    """Главный вход: текст -> WAV-байты. Сначала espeak (если есть),
    иначе встроенный нейроголос. WAV есть ВСЕГДА."""
    text = " ".join(str(text or "").split())
    if not text:
        text = "Я вас слушаю."
    if str(voice or "").lower() not in ("robot",):
        data = synth_espeak(text, voice)
        if data:
            return data
    return synth_builtin(text, voice)
