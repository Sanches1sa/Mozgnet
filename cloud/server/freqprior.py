# -*- coding: utf-8 -*-
# ===========================================================================
# freqprior.py — ОПОРА НА ЧАСТОТУ для всех нейросетей облака (v3.73, stdlib)
# ---------------------------------------------------------------------------
# Источник — знаковая частота генераторов плат (genmod.signal, z -1..+1) и
# таблица «уровень сигнала ↔ слова» (genmod.word_levels, тоже -1..+1), которую
# плата учит по своим фразам. Здесь из них получается ОДНА общая величина:
#   bonus(text) — насколько текст «созвучен» текущему сигналу, -1..+1;
# её прибавляют к своим оценкам: выбор предложений для ответа (мозг + LLM),
# сеть материалов (mozgnet), поиск по базе знаний. Генерация слов своей LLM
# уже идёт через genmod.word_bias, вес обучения — через genmod.lr_factor.
# Честно: это МЯГКАЯ подсказка (±0.3 к баллу предложения, настройка
# llm_freq_prior, 0 — выкл.), а не понимание; нет свежих данных плат или
# слова текста не встречались в таблице ≥3 раз — прибавка 0.
# ===========================================================================
import store

_N = {"used": 0, "zero": 0}


def _strength_raw():
    try:
        return max(0.0, min(3.0, float(store.settings().get(
            "llm_freq_prior", 1.0))))
    except (TypeError, ValueError):
        return 1.0



def strength():
    """Hz-to-word associations are reserved for feeling encoding."""
    return 0.0


def level():
    """Текущий уровень сигнала плат z (-1..+1) или None."""
    try:
        import genmod
        z, _b, n = genmod.signal()
        return z if n else None
    except Exception:
        return None


def bonus(text, z=None, s=None):
    """Never rank textual answers by oscillator frequency."""
    return 0.0


def status():
    z = level()
    return {"strength": strength(), "z": None if z is None else round(z, 3),
            "used": _N["used"], "no_data": _N["zero"]}
