# -*- coding: utf-8 -*-
# ===========================================================================
# tinyllm.py — СВОЯ LLM ОБЛАКА: СТАНДАРТНЫЙ ДЕКОДЕР-ТРАНСФОРМЕР (v3.31)
# ---------------------------------------------------------------------------
# Заявка владельца: «переработать tinyLLM до стандартной LLM — больше чем 12,
# например 1648». Было: 1 блок, d_model=12, 2 головы, контекст 8 токенов,
# чистый Python. Стало (на numpy; без numpy — прежняя версия tinyllm_py):
#   * архитектура GPT-класса: эмбеддинги токенов + обучаемые позиции,
#     N блоков [Pre-LN -> causal multi-head self-attention -> остаток ->
#     Pre-LN -> FFN(GELU) -> остаток], финальный LayerNorm, tied-голова;
#   * РАЗМЕРЫ НАСТРАИВАЮТСЯ (панель «API и ответы» -> «Своя LLM»):
#     d_model (llm_d, по умолчанию 128, до 1648), слои (llm_layers, 2–12),
#     головы (llm_heads), контекст (llm_ctx, до 512 токенов), словарь
#     (llm_vocab, до 16000). Все размеры — честные параметры модели,
#     они видны в статусе вместе с числом параметров и памятью;
#   * токенизатор субсловный: частые слова — целыми токенами, редкие —
#     по буквам («▁» — начало слова), поэтому незнакомое слово модель
#     прочитает и произнесёт, а не схлопнет в <unk>;
#   * обучение: next-token, кросс-энтропия, Adam, warmup + косинус,
#     клиппинг; ручной backprop по всем слоям; батчами; в фоне порциями с
#     долей ЦП llm_cpu (0.5 — половина одного ядра), прогресс — в статусе;
#   * корпус: статьи базы знаний (до llm_corpus_tokens токенов) + мысли;
#   * генерация: temperature / top-k / top-p / repetition penalty.
# ЧЕСТНО О НАГРУЗКЕ: d_model=1648 и 4 слоя — это ~110 млн параметров:
# памяти ~1.3 ГБ (веса + Adam), шаг обучения на ЦП — секунды, обучение
# на базе — сутки. Статус показывает параметры, память, скорость
# (токенов/с) и оценку времени до конца — выбирайте размер по компьютеру.
# v3.32: ВЕРХНИХ ПОТОЛКОВ НЕТ — d_model, слои, головы, контекст, словарь и
# остальные параметры можно ставить больше прежних пределов и сохранять;
# честная защита — оценка памяти (не хватит RAM: настройки сохраняются,
# обучение ждёт галочки «обучать всё равно» — llm_force).
# Модель по-прежнему учится ТОЛЬКО на материалах облака и помечается как
# «фантазия/мысль»: фактов она не гарантирует.
# ===========================================================================
import json
import math
import os
import random
import re
import threading
import time

try:
    import numpy as np
    HAVE_NP = True
except Exception:                       # без numpy — прежняя мини-версия
    np = None
    HAVE_NP = False

# v3.33: настройки, оценка нагрузки и проверка numpy — одинаково для обеих
# версий (раньше без numpy cfg()/estimate() не существовали, и панель
# возвращала поля формы к умолчаниям)
from llmcfg import (DEFAULTS, cfg, _fix, estimate, ram_mb,   # noqa: E402,F401
                    ram_free_mb, bench_gflops, numpy_status,
                    text_quality as _text_quality)

if not HAVE_NP:
    from tinyllm_py import *            # noqa: F401,F403  (status/fantasize/…)
    from tinyllm_py import status as _st_py
    from tinyllm_py import fantasize as _fant_py

    def status():
        s = _st_py()
        est = estimate()
        s["backend"] = ("pure-python — numpy НЕ установлен: размеры из "
                        "настроек не применяются (работает мини-модель)")
        s["numpy"] = False
        s.update({"ram_mb": est["ram_mb"], "ram_free_mb": est["ram_free_mb"],
                  "need_mb": est["need_mb"], "act_mb": est["act_mb"],
                  "mem_mb": est["mem_mb"], "verdict": est["verdict"],
                  "note": est["note"], "fits": est["fits"],
                  "train_s_est": est["train_s_est"],
                  "flops_per_token": est["flops_per_token"],
                  "cpu_share": cfg()["llm_cpu"], "force": cfg()["llm_force"],
                  "layers": cfg()["llm_layers"], "ff": 4 * cfg()["llm_d"]})
        s["params_cfg"] = est["params"]
        s["error"] = s.get("error") or ""
        return s

    def quality(text):
        import llmcfg
        return llmcfg.dict_quality(text)

    def sample(topic="", **over):
        t, info = _fant_py(topic)
        t = (t or "").strip()
        if t:
            t = t[0].upper() + t[1:]
            if t[-1] not in ".!?…":
                t = t.rstrip(",;:-") + "."
        return t, info

    def compose(question, facts, max_new=70):    # без numpy — не умеет
        return "", {"llm": "nonumpy"}

    def read_article(title, text, max_sents=40):     # без numpy — не умеет
        return {"ok": False}

    def learn_new(force=False):                       # без numpy — не умеет
        return "cold"

    def write_code(task, **kw):                       # без numpy — не умеет
        return []

    def fantasize(topic=""):          # noqa: F811
        t, info = sample(topic)
        if t:
            try:
                import llmlink
                r = llmlink.publish_thought(t, topic, source="воображение")
                info["published"] = bool(r.get("ok"))
            except Exception:
                pass
        return t, info
else:
    import llm2                     # v3.117: BPE, словарный автомат, n-граммы
    PAD, BOS, EOS, UNK = 0, 1, 2, 3
    WORD_RE = re.compile(r"[а-яёa-z0-9]+|[^\sа-яёa-z0-9]", re.I)
    _STATE = "tinyllm_np"
    _LOCK = threading.RLock()
    _THREAD = [None]
    _M = None

    class _TrainingHandoff(Exception):
        """Discard unfinished gradients before changing optimizer/weights."""
        pass

    # ===================== v3.41: КОД В ТОКЕНАЙЗЕРЕ =========================
    # Код (статьи «Исходник: …» из GitHub) кодируется отдельной схемой:
    # регистр СОХРАНЯЕТСЯ, переводы строк и отступы — токены. Токены кода
    # начинаются с «¢», поэтому не пересекаются с токенами обычного текста
    # и словарь прежней модели остаётся валидным (новые токены дописываются).
    CODE_MARK = "\u27e6код\u27e7"
    _C_NL, _C_IND, _C_SP = "\u00a2\u23ce", "\u00a2\u21e5", "\u00a2\u00b7"
    _CODE_RE = re.compile(r"[A-Za-z_][A-Za-z_0-9]*|\d+|[^\S\n]+|.", re.S)
    CODE_WORDS_MAX = 1800

    def _code_units(text):
        """Текст кода -> [токен-строка | ('W', слово, после_пробела)]."""
        out = []
        lines = str(text).replace("\r", "").replace("\t", "    ").split("\n")
        for li, line in enumerate(lines):
            body = line.lstrip(" ")
            n = len(line) - len(body)
            body = body.rstrip()
            if body:
                out += [_C_IND] * (n // 4) + [_C_SP] * (n % 4)
            pend = 0
            for m in _CODE_RE.finditer(body):
                tk = m.group(0)
                if tk.isspace():
                    pend = len(tk)
                    continue
                if pend > 1:
                    out += [_C_SP] * (pend - 1)
                out.append(("W", tk, pend >= 1))
                pend = 0
            if li < len(lines) - 1:
                out.append(_C_NL)
        return out

    def _code_tok(u):
        return u if isinstance(u, str) else "\u00a2" + ("\u2581" if u[2] else "") + u[1]

    # ============================ ТОКЕНИЗАТОР ===============================
    MIN_INC_STEPS = 24

    def _freq_temp():
        """v3.85: температура генерации от частоты плат: сильный сигнал —
        смелее (до ×1.2), слабый/отрицательный — осторожнее (до ×0.8)."""
        try:
            import freqprior
            z = freqprior.level()
            if z is None:
                return 1.0
            return max(0.8, min(1.2, 1.0 + 0.2 * min(1.0, freqprior.strength()) * z))
        except Exception:
            return 1.0

    def store_log(kind, text):
        try:
            import store
            store.log(kind, text)
        except Exception:
            pass

    def store_get(key):
        try:
            import store
            return store.settings().get(key)
        except Exception:
            return None

    def free_ram_bytes():
        """Свободная память, байт (Linux /proc, Windows ctypes); 0 — не знаем."""
        try:
            with open("/proc/meminfo") as f:
                for ln in f:
                    if ln.startswith("MemAvailable"):
                        return int(ln.split()[1]) * 1024
        except Exception:
            pass
        try:
            import ctypes

            class MS(ctypes.Structure):
                _fields_ = [("l", ctypes.c_ulong), ("ml", ctypes.c_ulong),
                            ("tp", ctypes.c_ulonglong),
                            ("ap", ctypes.c_ulonglong),
                            ("tpf", ctypes.c_ulonglong),
                            ("apf", ctypes.c_ulonglong),
                            ("tv", ctypes.c_ulonglong),
                            ("av", ctypes.c_ulonglong),
                            ("ae", ctypes.c_ulonglong)]
            m = MS()
            m.l = ctypes.sizeof(MS)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
            return int(m.ap)
        except Exception:
            return 0

    class Tokenizer(object):
        """v3.117: BPE-токенайзер (llm2.py). Слово = 1–3 куска «▁основ»+«а»,
        частые слова — целым токеном; буквы — только как запасной путь.
        <unk> почти не бывает: сеть учится на всём тексте. words — слова
        речи (словарный автомат: сеть произносит только их). Код — своя
        схема токенов «¢…» (как раньше)."""

        def __init__(self, vocab=None, merges=None, words=None, ver=None):
            self.vocab = dict(vocab or {})
            self.inv = {v: k for k, v in self.vocab.items()}
            self.merges = [tuple(x) for x in (merges or [])]
            self.ranks = {m_: i for i, m_ in enumerate(self.merges)}
            self.words = list(words or [])
            self.ver = int(ver or (llm2.TOK_VER if vocab is None else 1))
            self._cache = {}
            self._guide = None

        @staticmethod
        def _word_freq(texts):
            freq, cfreq, has_code = {}, {}, False
            for t in texts:
                if str(t).startswith(CODE_MARK):
                    has_code = True
                    for u in _code_units(str(t)[len(CODE_MARK):]):
                        if not isinstance(u, str):
                            cfreq[u[1]] = cfreq.get(u[1], 0) + 1
                    continue
                for w in WORD_RE.findall(llm2.normalize(str(t)).lower()):
                    freq[w] = freq.get(w, 0) + 1
            return freq, cfreq, has_code

        def build(self, texts, size):
            freq, cfreq, has_code = self._word_freq(texts)
            vocab = {"<pad>": PAD, "<bos>": BOS, "<eos>": EOS, "<unk>": UNK}
            chars = set()
            for w in freq:
                if llm2._ALNUM_RE.match(w):
                    chars.update(w)
                elif len(w) == 1:
                    vocab.setdefault("\u2581" + w, len(vocab))   # знак
            for ch in sorted(chars):
                for tok in ("\u2581" + ch, ch):
                    vocab.setdefault(tok, len(vocab))
            cw = 0
            if has_code:
                for tk in (_C_NL, _C_IND, _C_SP):
                    vocab.setdefault(tk, len(vocab))
                for o in range(32, 127):
                    for tk in ("\u00a2" + chr(o), "\u00a2\u2581" + chr(o)):
                        vocab.setdefault(tk, len(vocab))
                cw = min(CODE_WORDS_MAX, max(300, size // 3))
            n_merges = max(50, size - cw - len(vocab))
            wf = {w: c for w, c in freq.items() if llm2._ALNUM_RE.match(w)}
            merges = llm2.train_bpe(wf, n_merges)
            for a_, b_ in merges:
                vocab.setdefault(a_ + b_, len(vocab))
            if has_code:
                n_cw = 0
                for w, _c in sorted(cfreq.items(), key=lambda kv: -kv[1]):
                    if n_cw >= cw or _c < 2:
                        break
                    if len(w) < 2:
                        continue
                    for sp in (True, False):
                        tk = _code_tok(("W", w, sp))
                        if tk not in vocab:
                            vocab[tk] = len(vocab)
                            n_cw += 1
            self.vocab, self.inv = vocab, {v: k for k, v in vocab.items()}
            self.merges = merges
            self.ranks = {m_: i for i, m_ in enumerate(merges)}
            self.words = llm2.pick_speech_words(freq)
            self.ver = llm2.TOK_VER
            self._cache, self._guide = {}, None
            return len(vocab)

        def extend(self, texts, size):
            """Дополнить словарь, не меняя id старых токенов (дообучение):
            новые буквы/знаки, новые частые слова целым токеном, новые
            слова речи."""
            added = 0
            freq, cfreq, _hc = self._word_freq(texts)
            for w in freq:
                if llm2._ALNUM_RE.match(w):
                    for ch in w:
                        for tok in ("\u2581" + ch, ch):
                            if tok not in self.vocab:
                                self.vocab[tok] = len(self.vocab)
                                added += 1
                elif len(w) == 1 and "\u2581" + w not in self.vocab:
                    self.vocab["\u2581" + w] = len(self.vocab)
                    added += 1
            for w, _c in sorted(freq.items(), key=lambda kv: -kv[1]):
                if len(self.vocab) >= size:
                    break
                if _c >= 3 and len(w) > 3 and llm2._ALNUM_RE.match(w) and \
                        "\u2581" + w not in self.vocab:
                    self.vocab["\u2581" + w] = len(self.vocab)
                    added += 1
            if cfreq:
                base = [_C_NL, _C_IND, _C_SP] + [
                    "\u00a2" + chr(o) for o in range(32, 127)] + [
                    "\u00a2\u2581" + chr(o) for o in range(32, 127)]
                for tk in base:
                    if tk not in self.vocab:
                        self.vocab[tk] = len(self.vocab)
                        added += 1
                have = sum(1 for k in self.vocab
                           if k.startswith("\u00a2") and len(k) > 3)
                for w, _c in sorted(cfreq.items(), key=lambda kv: -kv[1]):
                    if have >= CODE_WORDS_MAX or _c < 2:
                        break
                    if len(w) < 2:
                        continue
                    for sp in (True, False):
                        tk = _code_tok(("W", w, sp))
                        if tk not in self.vocab:
                            self.vocab[tk] = len(self.vocab)
                            added += 1
                            have += 1
            old = set(self.words)
            nw = [w for w in llm2.pick_speech_words(freq) if w not in old]
            if nw:
                self.words.extend(nw)
                added += 0
            self.inv = {v: k for k, v in self.vocab.items()}
            self._cache, self._guide = {}, None
            return added

        def guide(self):
            g = self._guide
            if g is None or g.n != len(self.vocab):
                g = self._guide = llm2.WordGuide(self, self.words)
            return g

        def encode_code(self, text):
            ids = []
            for u in _code_units(text):
                tk = _code_tok(u)
                t = self.vocab.get(tk)
                if t is not None:
                    ids.append(t)
                    continue
                if isinstance(u, str):
                    continue
                for i, ch in enumerate(u[1]):
                    sp = "\u2581" if (i == 0 and u[2]) else ""
                    ids.append(self.vocab.get("\u00a2" + sp + ch,
                                              self.vocab.get("\u00a2" + ch,
                                                             UNK)))
            return ids

        def decode_code(self, ids):
            out = []
            for i in ids:
                t = self.inv.get(int(i), "")
                if not t.startswith("\u00a2"):
                    continue
                if t == _C_NL:
                    out.append("\n")
                elif t == _C_IND:
                    out.append("    ")
                elif t == _C_SP:
                    out.append(" ")
                elif t[1:2] == "\u2581":
                    out.append(" " + t[2:])
                else:
                    out.append(t[1:])
            return "".join(out)

        def encode(self, text, add_bos=False, add_eos=False):
            ids = [BOS] if add_bos else []
            if str(text).startswith(CODE_MARK):
                ids.extend(self.encode_code(str(text)[len(CODE_MARK):]))
                if add_eos:
                    ids.append(EOS)
                return ids
            for w in WORD_RE.findall(llm2.normalize(str(text)).lower()):
                t = self.vocab.get("\u2581" + w)
                if t is not None:
                    ids.append(t)
                    continue
                if len(w) == 1 and not llm2._ALNUM_RE.match(w):
                    ids.append(UNK)          # незнакомый знак
                    continue
                for pc in llm2.bpe_word(w, self.ranks, self._cache):
                    t = self.vocab.get(pc)
                    if t is not None:
                        ids.append(t)
                        continue
                    for i, ch in enumerate(pc.lstrip("\u2581")):
                        sp = "\u2581" if (i == 0 and pc.startswith("\u2581")) else ""
                        ids.append(self.vocab.get(sp + ch, UNK))
            if add_eos:
                ids.append(EOS)
            return ids

        def decode(self, ids):
            out = ""
            for i in ids:
                t = self.inv.get(int(i), "")
                if not t or t.startswith("<") or t.startswith("\u00a2"):
                    continue
                if t.startswith("▁"):
                    out += (" " if out else "") + t[1:]
                else:
                    out += t
            return re.sub(r"\s+([.,!?;:])", r"\1", out)

        @property
        def size(self):
            return len(self.vocab)

    # ============================== МОДЕЛЬ ==================================
    def _gelu(x):
        return 0.5 * x * (1.0 + np.tanh(0.7978845608 * (x + 0.044715 * x ** 3)))

    def _gelu_t(x):
        """v3.77: GELU и tanh за один проход, на месте (раньше tanh считался
        дважды — в прямом и обратном ходе — с горой временных массивов)."""
        c = x * x
        c *= 0.044715
        c += 1.0
        c *= x
        c *= 0.7978845608
        t = np.tanh(c, out=c)
        g = t + 1.0
        g *= x
        g *= 0.5
        return g, t

    def _gelu_grad_t(x, t):
        d = x * x
        d *= 3 * 0.044715 * 0.7978845608
        d += 0.7978845608                   # dc
        u = t * t
        np.subtract(1.0, u, out=u)          # 1 - t^2
        d *= u
        d *= x
        d *= 0.5
        d += 0.5
        d += 0.5 * t
        return d

    def _gelu_grad(x):
        c = 0.7978845608 * (x + 0.044715 * x ** 3)
        t = np.tanh(c)
        dc = 0.7978845608 * (1.0 + 3 * 0.044715 * x ** 2)
        return 0.5 * (1.0 + t) + 0.5 * x * (1.0 - t ** 2) * dc

    def _ln_f(x, g, b, eps=1e-5):
        mu = x.mean(-1, keepdims=True)
        var = x.var(-1, keepdims=True)
        xh = (x - mu) / np.sqrt(var + eps)
        return xh * g + b, (xh, var + eps)

    def _ln_b(dy, cache, g):
        xh, v = cache
        dg = (dy * xh).reshape(-1, xh.shape[-1]).sum(0)
        db = dy.reshape(-1, xh.shape[-1]).sum(0)
        dxh = dy * g
        D = xh.shape[-1]
        dx = (dxh - dxh.mean(-1, keepdims=True) -
              xh * (dxh * xh).mean(-1, keepdims=True)) / np.sqrt(v)
        return dx, dg, db

    def _softmax(x):
        m = x.max(-1, keepdims=True)
        e = np.exp(x - m)
        return e / e.sum(-1, keepdims=True)

    # ================= v3.42.1: ЗАЩИТА ОТ «ТАРАБАРЩИНЫ» =====================
    _CONS = set("бвгджзйклмнпрстфхцчшщ")
    _ALLOWED_RE = re.compile(r"^[а-яёa-z0-9\u2581.,!?;:\-\u2014()\"'«»…%+*/=<>]+$", re.I)

    def _word_garbage(w):
        letters = re.sub(r"[^A-Za-zА-Яа-яЁё\u0370-\u03ff]", "", w)
        if not letters:
            return False
        if len(letters) > 22:
            return True
        scripts = set()
        for ch in letters:
            if "а" <= ch.lower() <= "я" or ch.lower() == "ё":
                scripts.add("cyr")
            elif ch.isascii():
                scripts.add("lat")
            else:
                scripts.add("other")
        if len(scripts) > 1:
            return True
        run = 0
        for ch in letters.lower():
            run = run + 1 if ch in _CONS else 0
            if run >= 7:
                return True
        return False

    # v3.115: «(англ. adiabatic process)», «(лат. …)» — пометки языка в
    # статьях Википедии. Их сотни тысяч, и недоученная модель стала «думать»
    # одними «англ н англ н…». Из корпуса и фактов они убираются, а в
    # генерации такие слова-пометки запрещены.
    _GLOSS_RE = re.compile(r"\(\s*(?:англ|лат|нем|фр|франц|греч|др\.?-?\s*греч|"
                           r"итал|исп|яп|кит|араб|укр|польск|чеш|швед|норв|"
                           r"нидерл|порт|тур|ивр|санскр)\.[^()]{0,160}\)", re.I)
    _GLOSS_W = frozenset("англ лат нем фр франц греч итал исп яп кит араб укр "
                         "польск чеш швед норв нидерл порт тур ивр санскр".split())

    def clean_text_corpus(t):
        return llm2.normalize(t)          # v3.117: + сокращения, чужие алфавиты

    def thought_ok(t):
        """Мысль платы годится в обучение: не зацикленный шум, разные слова,
        больше половины слов — настоящие (≥4 букв)."""
        try:
            import llmlink
            if llmlink.degenerate(t):
                return False
        except Exception:
            pass
        ws = re.findall(r"[а-яёa-z]+", str(t or "").lower())
        if len(ws) < 4 or len(set(ws)) < 0.7 * len(ws):
            return False
        if any(w in _GLOSS_W for w in ws):
            return False
        return sum(1 for w in ws if len(w) >= 4) >= 0.5 * len(ws)

    _FUNC_W = frozenset("и в на с по не что как а но о к у из за от до же ли "
                        "бы то это для при".split())

    def has_garbage(text):
        return any(_word_garbage(w) for w in str(text or "").split())

    def clean_text(text):
        """Обрезать текст до первого «слова-тарабарщины» (смесь алфавитов,
        очень длинное, ряд согласных) и до последнего законченного
        предложения. Слишком короткий остаток -> ''."""
        t = str(text or "").strip()
        if not t:
            return ""
        words = t.split()
        for i, w in enumerate(words):
            if _word_garbage(w):
                t = " ".join(words[:i])
                break
        else:
            return t
        m = list(re.finditer(r"[.!?…]", t))
        if m:
            t = t[:m[-1].end()]
        return t if len(t) >= 12 else ""

    class GPT(object):
        def __init__(self, c):
            self.c = dict(c)
            self.tok = Tokenizer()
            self.p = {}
            self.adam = {}
            self.ready = False
            self.training = False
            self.steps = 0
            self.loss = None
            self.meta = {"total_steps": 0, "kb_articles": 0,
                         "corpus_tokens": 0, "corpus_docs": 0,
                         "tok_per_s": 0.0}
            self._stop = False

        # ------------------------------ параметры -------------------------
        def init_params(self, V, seed=42):
            r = np.random.RandomState(seed)
            D, F = self.c["llm_d"], 4 * self.c["llm_d"]
            L, T = self.c["llm_layers"], self.c["llm_ctx"]
            s = 0.02
            p = {"E": r.normal(0, s, (V, D)).astype(np.float32),
                 "P": r.normal(0, s, (T, D)).astype(np.float32),
                 "lnf_g": np.ones(D, np.float32),
                 "lnf_b": np.zeros(D, np.float32)}
            for l in range(L):
                for nm in ("q", "k", "v", "o"):
                    p["W%s%d" % (nm, l)] = r.normal(0, s, (D, D)).astype(
                        np.float32)
                    p["b%s%d" % (nm, l)] = np.zeros(D, np.float32)
                p["ln1g%d" % l] = np.ones(D, np.float32)
                p["ln1b%d" % l] = np.zeros(D, np.float32)
                p["ln2g%d" % l] = np.ones(D, np.float32)
                p["ln2b%d" % l] = np.zeros(D, np.float32)
                p["W1_%d" % l] = r.normal(0, s, (D, F)).astype(np.float32)
                p["b1_%d" % l] = np.zeros(F, np.float32)
                p["W2_%d" % l] = (r.normal(0, s, (F, D)) /
                                  math.sqrt(2 * L)).astype(np.float32)
                p["b2_%d" % l] = np.zeros(D, np.float32)
            self.p = p
            self.adam = None          # v3.83: создаётся лениво, при обучении
            try:                      # новые веса — прежний Adam с диска не годится
                os.remove(os.path.join(self._adam_dir(), "manifest.json"))
            except Exception:
                pass
            self.ready = True

        def _adam_arr(self, kind, key, like):
            """Состояние Adam: в ОЗУ или (llm_disk_state) в файле на диске —
            «виртуальный размер»: модель может быть больше, чем помещается
            вместе с Adam в память (медленнее: идёт обмен с диском)."""
            if store_get("llm_disk_state"):
                try:
                    import store
                    d = store.p("llm_adam")
                    os.makedirs(d, exist_ok=True)
                    self._adam_n = getattr(self, "_adam_n", 0) + 1
                    fn = os.path.join(d, "%s_%s_%d_%d.dat" % (
                        kind, key, int(time.time()), self._adam_n))
                    return np.memmap(fn, dtype=np.float32, mode="w+",
                                     shape=like.shape)
                except Exception:
                    pass
            return np.zeros_like(like)

        def _adam_dir(self):
            import store
            return store.p("llm_adam")

        def _adam_resume(self):
            """v3.89: Adam с диска (файлы подкачки модели) — продолжить
            обучение с тем же состоянием оптимизатора после перезапуска,
            обновления или переноса на другой компьютер (install.exe)."""
            try:
                d = self._adam_dir()
                with open(os.path.join(d, "manifest.json"), encoding="utf-8") as f:
                    man = json.load(f)
                arr = man.get("arr") or {}
                out = {"t": int(man.get("t") or 0), "m": {}, "v": {}}
                for kind in ("m", "v"):
                    for k, v in self.p.items():
                        fn, shape = (arr.get(kind) or {}).get(k) or (None, None)
                        full = os.path.join(d, fn or "")
                        if not fn or list(shape) != list(v.shape) or \
                                not os.path.isfile(full) or \
                                os.path.getsize(full) != v.size * 4:
                            return None
                        out[kind][k] = np.memmap(full, dtype=np.float32,
                                                 mode="r+", shape=v.shape)
                self.meta["adam_resumed"] = out["t"]
                return out
            except Exception:
                return None

        def _adam_save(self):
            """Записать на диск состояние Adam и его манифест."""
            ad = self.adam
            if not ad or not store_get("llm_disk_state"):
                return
            try:
                d = self._adam_dir()
                arr = {"m": {}, "v": {}}
                for kind in ("m", "v"):
                    for k, a in (ad.get(kind) or {}).items():
                        if not isinstance(a, np.memmap) or not a.filename:
                            return            # смешанное состояние — не пишем
                        a.flush()
                        arr[kind][k] = [os.path.basename(a.filename),
                                        list(a.shape)]
                tmp = os.path.join(d, "manifest.json.tmp")
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump({"t": int(ad.get("t") or 0), "steps": self.steps,
                               "arr": arr, "ts": time.time()}, f)
                os.replace(tmp, os.path.join(d, "manifest.json"))
                used = {fn for kd in arr.values() for fn, _s in kd.values()}
                for fn in os.listdir(d):    # осиротевшие файлы (рост словаря)
                    if fn.endswith(".dat") and fn not in used:
                        try:
                            os.remove(os.path.join(d, fn))
                        except Exception:
                            pass
            except Exception:
                pass

        def _adam_new(self):
            if store_get("llm_disk_state"):
                got = self._adam_resume()
                if got is not None:
                    return got
            if store_get("llm_disk_state"):
                try:                          # старые файлы прошлых запусков
                    import store
                    d = store.p("llm_adam")
                    for fn in os.listdir(d):
                        try:
                            os.remove(os.path.join(d, fn))
                        except Exception:
                            pass
                except Exception:
                    pass
            return {"t": 0,
                    "m": {k: self._adam_arr("m", k, v) for k, v in self.p.items()},
                    "v": {k: self._adam_arr("v", k, v) for k, v in self.p.items()}}

        def grow_vocab(self, V):
            """Добавить строки эмбеддинга для новых токенов (веса выхода
            общие с эмбеддингом); состояние Adam дополняется нулями."""
            E = self.p["E"]
            if V <= E.shape[0]:
                return
            r = np.random.RandomState(V)
            add = r.normal(0, 0.02, (V - E.shape[0], E.shape[1])).astype(
                np.float32)
            self.p["E"] = np.concatenate([E, add])
            if not self.adam:
                return
            for k_ in ("m", "v"):
                a = self.adam[k_]["E"]
                new_ = self._adam_arr(k_, "E", self.p["E"])
                new_[:a.shape[0]] = a
                self.adam[k_]["E"] = new_

        def param_count(self):
            return int(sum(v.size for v in self.p.values())) if self.p else 0

        # ------------------------------ прямой ход ------------------------
        def _fp(self):
            """v3.148: плавающие параметры ответа ЭТОГО потока | None
            (учёба идёт в своём потоке без них — полный размер сети)."""
            return getattr(_FP, "p", None)

        def _gains(self):
            """v3.147: ТЕНЗОР МОДУЛЯЦИИ своей LLM — множители весов ветвей
            каждого слоя (внимание, FFN) от частот плат и дыхания мозга
            (tensormod, «Своя LLM»: 2·слоёв групп). Покой — единицы."""
            L = int(self.c["llm_layers"])
            if getattr(_FP, "stable_code", False):
                return [1.0] * (2 * L)
            try:
                import tensormod
                gm = tensormod.vec("Своя LLM", 2 * L)
                return [float(v) for v in gm]
            except Exception:
                return [1.0] * (2 * L)

        def forward(self, X, cache=False):
            p, c = self.p, self.c
            B, T = X.shape
            H, D = c["llm_heads"], c["llm_d"]
            Dh = D // H
            gm = self._gains()          # v3.147: веса ветвей = m · W
            fp = None if cache else self._fp()
            h = p["E"][X] + p["P"][:T][None, :, :]
            mask = np.triu(np.ones((T, T), dtype=bool), 1)
            n_layers, n_heads = c["llm_layers"], H
            if fp:                      # v3.148: плавающая сеть (только ответы)
                if fp["ctx"] < T:       # окно внимания: только последние ctx токенов
                    mask = mask | np.tril(np.ones((T, T), dtype=bool), -int(fp["ctx"]))
                n_layers = max(1, min(n_layers, int(fp["layers"])))
                n_heads = max(1, min(H, int(fp["heads"])))
            caches = []
            for l in range(n_layers):
                a, lnc1 = _ln_f(h, p["ln1g%d" % l], p["ln1b%d" % l])
                q = (a @ p["Wq%d" % l] + p["bq%d" % l]).reshape(B, T, H, Dh) \
                    .transpose(0, 2, 1, 3)
                k = (a @ p["Wk%d" % l] + p["bk%d" % l]).reshape(B, T, H, Dh) \
                    .transpose(0, 2, 1, 3)
                v = (a @ p["Wv%d" % l] + p["bv%d" % l]).reshape(B, T, H, Dh) \
                    .transpose(0, 2, 1, 3)
                s = q @ k.transpose(0, 1, 3, 2) / math.sqrt(Dh)
                s = np.where(mask[None, None], -1e9, s)
                att = _softmax(s)
                ov = att @ v
                if n_heads < H:         # v3.148: работают не все головы
                    ov[:, n_heads:] = 0.0
                    ov = ov * (H / float(n_heads))
                o = ov.transpose(0, 2, 1, 3).reshape(B, T, D)
                proj = o @ p["Wo%d" % l] + p["bo%d" % l]
                if gm[2 * l] != 1.0:
                    proj = proj * gm[2 * l]
                h2 = h + proj
                a2, lnc2 = _ln_f(h2, p["ln2g%d" % l], p["ln2b%d" % l])
                z = a2 @ p["W1_%d" % l] + p["b1_%d" % l]
                g, gt = _gelu_t(z)
                ff = g @ p["W2_%d" % l] + p["b2_%d" % l]
                if gm[2 * l + 1] != 1.0:
                    ff = ff * gm[2 * l + 1]
                h = h2 + ff
                if cache:
                    caches.append((a, lnc1, q, k, v, att, o, h2, a2, lnc2,
                                   z, g, gt))
            hf, lncf = _ln_f(h, p["lnf_g"], p["lnf_b"])
            logits = hf @ p["E"].T
            return logits, (caches, hf, lncf, X, gm) if cache else None

        # ------------------------------ обратный ход ----------------------
        def train_step(self, X, Y, lr):
            def handoff():
                if self._stop and getattr(self, "_code_course", False):
                    raise _TrainingHandoff()
            handoff()
            self._train_phase = "расчёт градиентов"
            genmod = None
            if not getattr(_FP, "stable_code", False):
                try:
                    import genmod
                    lr = lr * genmod.lr_factor()
                except Exception:
                    genmod = None
                try:
                    import breath as _brl
                    lr = _brl.mod("Своя LLM", lr, 0.10) * _brl.strength("Своя LLM")
                except Exception:
                    pass
            B_, T_ = X.shape
            nm = self.micro_size(B_, T_)
            if nm >= B_:
                loss, g = self.grads(X, Y)
            else:                         # v3.71: микро-батчи, память под потолком
                g = {k: np.zeros_like(v) for k, v in self.p.items()}
                loss = 0.0
                tot = float(B_ * T_)
                for i in range(0, B_, nm):
                    handoff()
                    self._train_phase = "градиенты: микробатч %d/%d" % (1+i//nm, -(-B_//nm))
                    l_, _g = self.grads(X[i:i + nm], Y[i:i + nm], g, tot)
                    loss += l_
                    handoff()
            handoff()
            p = self.p
            _t_opt = time.time()          # v3.100: время обновления весов (Adam)
            # клиппинг по норме
            gn = math.sqrt(sum(float((v ** 2).sum()) for v in g.values()))
            if gn > 1.0:
                for k_ in g:
                    g[k_] *= 1.0 / gn
            # Once Adam starts, finish the update as one indivisible step.
            handoff()
            self._train_phase = "обновление Adam"
            if not self.adam:
                self.adam = self._adam_new()
            ad = self.adam
            # v3.64.1: словарь мог вырасти между расчётом градиента и шагом
            # (или состояние Adam осталось от меньшего словаря) — выравниваем
            # формы строк по весам, иначе «operands could not be broadcast»
            def _fit(a_, shape):
                if a_.shape == shape:
                    return a_
                out = np.zeros(shape, a_.dtype)
                n = min(a_.shape[0], shape[0])
                out[:n] = a_[:n]
                return out
            for k_ in p:
                sh = p[k_].shape
                g[k_] = _fit(g[k_], sh)
                ad["m"][k_] = _fit(ad["m"][k_], sh)
                ad["v"][k_] = _fit(ad["v"][k_], sh)
            ad["t"] += 1
            b1, b2 = 0.9, 0.95
            c1 = 1.0 / (1 - b1 ** ad["t"])
            c2 = 1.0 / (1 - b2 ** ad["t"])
            for k_ in p:                  # v3.65: на месте, без копий 73М чисел
                gk, mk, vk = g[k_], ad["m"][k_], ad["v"][k_]
                mk *= b1
                mk += (1 - b1) * gk
                vk *= b2
                gk *= gk
                vk += (1 - b2) * gk
                den = np.sqrt(vk * c2)
                den += 1e-8
                upd = mk * (lr * c1)
                upd /= den
                p[k_] -= upd.astype(np.float32, copy=False)
            self.t_opt = time.time() - _t_opt
            self._train_phase = "шаг завершён"
            if genmod is not None:
                try:
                    genmod.perturb(self, int(ad["t"]))
                except Exception:
                    pass
            return float(loss)

        def micro_size(self, B, T):
            """Сколько примеров батча считать за раз, чтобы активации влезли
            в свободную память (после весов, Adam и градиентов)."""
            try:
                forced = int(store_get("llm_micro_batch") or 0)
            except Exception:
                forced = 0
            if forced > 0:
                return max(1, min(B, forced))
            c = self.c
            D, L, H = c["llm_d"], c["llm_layers"], c["llm_heads"]
            F = int(c.get("llm_ff") or 4 * D)
            V = len(self.tok.vocab)
            per = 4.0 * (L * (12 * T * D + 2 * T * F + 2 * H * T * T)
                         + 4 * T * V) * 1.3
            free = free_ram_bytes()
            if not free:
                return B
            fixed = 4.0 * sum(v.size for v in self.p.values())  # копия g
            room = free * 0.7 - fixed
            return int(max(1, min(B, room // per)))

        def grads(self, X, Y, g=None, norm=None):
            """Потеря и градиенты по всем параметрам (ручной backprop).
            v3.71: g — общий накопитель градиентов (микро-батчи складываются
            на месте), norm — делитель потери (число токенов ВСЕГО батча)."""
            p, c = self.p, self.c
            B, T = X.shape
            H, D = c["llm_heads"], c["llm_d"]
            Dh = D // H
            logits, cache = self.forward(X, cache=True)
            caches, hf, lncf, _x, gm = cache
            probs = _softmax(logits)
            n = float(norm or B * T)
            # v3.117: <unk>/<pad> — не цель обучения (раньше треть шагов
            # сеть училась «предсказывать <unk>», которого нет в речи)
            yv = Y.reshape(-1)
            wv = ((yv != UNK) & (yv != PAD)).astype(probs.dtype)
            loss = -(np.log(np.clip(probs.reshape(-1, probs.shape[-1])[
                np.arange(B * T), yv], 1e-9, None)) * wv).sum() / n
            dlog = probs
            dlog.reshape(-1, probs.shape[-1])[np.arange(B * T), yv] -= 1.0
            dlog.reshape(-1, probs.shape[-1])[...] *= wv[:, None]
            dlog /= n
            if g is None:
                g = {k: np.zeros_like(v) for k, v in p.items()}
            g["E"] += dlog.reshape(-1, dlog.shape[-1]).T @ hf.reshape(-1, D)
            dhf = dlog @ p["E"]
            dh, dg_, db_ = _ln_b(dhf, lncf, p["lnf_g"])
            g["lnf_g"] += dg_
            g["lnf_b"] += db_
            for l in reversed(range(c["llm_layers"])):
                a, lnc1, q, k, v, att, o, h2, a2, lnc2, z, gl, gt = caches[l]
                # FFN (v3.147: ветвь умножена на gm — градиент тоже)
                dhf_ = dh * gm[2 * l + 1] if gm[2 * l + 1] != 1.0 else dh
                g["b2_%d" % l] += dhf_.reshape(-1, D).sum(0)
                g["W2_%d" % l] += gl.reshape(-1, gl.shape[-1]).T @ dhf_.reshape(-1, D)
                dg = dhf_ @ p["W2_%d" % l].T
                dz = dg * _gelu_grad_t(z, gt)
                g["b1_%d" % l] += dz.reshape(-1, dz.shape[-1]).sum(0)
                g["W1_%d" % l] += a2.reshape(-1, D).T @ dz.reshape(-1, dz.shape[-1])
                da2 = dz @ p["W1_%d" % l].T
                dh2, dg_, db_ = _ln_b(da2, lnc2, p["ln2g%d" % l])
                g["ln2g%d" % l] += dg_
                g["ln2b%d" % l] += db_
                dh2 = dh2 + dh                       # остаток
                # внимание
                dha_ = dh2 * gm[2 * l] if gm[2 * l] != 1.0 else dh2
                g["bo%d" % l] += dha_.reshape(-1, D).sum(0)
                g["Wo%d" % l] += o.reshape(-1, D).T @ dha_.reshape(-1, D)
                do = (dha_ @ p["Wo%d" % l].T).reshape(B, T, H, Dh).transpose(0, 2, 1, 3)
                datt = do @ v.transpose(0, 1, 3, 2)
                dv = att.transpose(0, 1, 3, 2) @ do
                ds = att * (datt - (datt * att).sum(-1, keepdims=True))
                ds /= math.sqrt(Dh)
                dq = ds @ k
                dk = ds.transpose(0, 1, 3, 2) @ q
                dq = dq.transpose(0, 2, 1, 3).reshape(B, T, D)
                dk = dk.transpose(0, 2, 1, 3).reshape(B, T, D)
                dv = dv.transpose(0, 2, 1, 3).reshape(B, T, D)
                da = np.zeros_like(a)
                for nm, d_ in (("q", dq), ("k", dk), ("v", dv)):
                    g["b%s%d" % (nm, l)] += d_.reshape(-1, D).sum(0)
                    g["W%s%d" % (nm, l)] += a.reshape(-1, D).T @ d_.reshape(-1, D)
                    da += d_ @ p["W%s%d" % (nm, l)].T
                dh1, dg_, db_ = _ln_b(da, lnc1, p["ln1g%d" % l])
                g["ln1g%d" % l] += dg_
                g["ln1b%d" % l] += db_
                dh = dh1 + dh2                       # остаток
            g["P"][:T] += dh.sum(0)
            # v3.77: np.add.at медленный; сортировка + reduceat даёт то же
            xi = X.reshape(-1)
            order = np.argsort(xi, kind="stable")
            xs_ = xi[order]
            starts = np.flatnonzero(np.r_[True, xs_[1:] != xs_[:-1]])
            g["E"][xs_[starts]] += np.add.reduceat(
                dh.reshape(-1, D)[order], starts, axis=0)
            return float(loss), g

        # ------------------------------- генерация ------------------------
        def sent_scores(self, sentences):
            """v3.37: «опыт чтения» — средний лог-вероятность токенов каждого
            предложения под моделью (выше = модель усвоила лучше)."""
            ctx = self.c["llm_ctx"]
            out = []
            for sn in sentences:
                ids = [BOS] + self.tok.encode(sn)[:ctx - 1]
                if len(ids) < 4:
                    out.append(None)
                    continue
                X = np.array([ids], dtype=np.int64)
                logits, _c = self.forward(X)
                lg = logits[0].astype(np.float64)
                lg = lg - lg.max(axis=1, keepdims=True)
                lp = lg - np.log(np.exp(lg).sum(axis=1, keepdims=True))
                out.append(float(np.mean([lp[i - 1, ids[i]]
                                          for i in range(1, len(ids))])))
            return out

        def generate_grounded(self, question, facts, max_new=70, copy=2.5,
                              temperature=0.6, rep=1.4, min_frac=0.0):
            """v3.34: генерация ПО ФАКТАМ (база знаний/интернет). Берутся 1–2
            предложения фактов, ближайшие к вопросу; слова ответа — только из
            них (жёсткая маска), порядок и связки выбирает модель; продолжение
            пары слов из фактов получает премию copy (как «указатель» в
            pointer-сетях); уже использованная пара слов запрещена (нет
            зацикливания). Возврат: (текст, доля пар слов из фактов)."""
            ctx = self.c["llm_ctx"]
            facts = clean_text_corpus(facts)          # v3.115: без «(англ. …)»
            sents = [x.strip() for x in re.split(r"(?<=[.!?])\s+", str(facts))
                     if len(x.strip()) > 25]
            if not sents:
                return "", 0.0
            qw = [w for w in re.findall(r"[а-яёa-z0-9]{3,}", str(question).lower())
                  if w not in ("что", "такое", "кто", "это", "как", "где",
                               "когда", "почему", "какой", "какая")]

            low = [x.lower() for x in sents]
            wt = {}
            for w in qw:
                df = sum(1 for xl in low if w[:max(3, len(w) - 2)] in xl)
                wt[w] = math.log(1.0 + len(sents) / (1.0 + df)) + 0.1

            def rel(x):
                xl = x.lower()
                return sum(wt[w] for w in qw if w[:max(3, len(w) - 2)] in xl)
            order = sorted(range(len(sents)), key=lambda i: (-rel(sents[i]), i))
            pick = sorted(order[:2])
            if rel(sents[order[0]]) == 0 and qw:
                return "", 0.0
            chosen = [sents[i] for i in pick]
            first_ids = self.tok.encode(sents[order[0]])
            fids = []
            for x in chosen:
                fids += self.tok.encode(x)
            if len(fids) < 5 or not first_ids:
                return "", 0.0
            allowed = set(fids) | {EOS}
            nxt = {}
            for x in chosen:
                e = self.tok.encode(x)
                for p_, q_ in zip(e, e[1:]):
                    nxt.setdefault(p_, set()).add(q_)
            mask = np.full(len(self.tok.vocab) + 8, -1e9)
            for t in allowed:
                if t < len(mask) and self.tok.inv.get(t, "").lstrip(
                        "\u2581").lower() not in _GLOSS_W:
                    mask[t] = 0.0
            ctx_ids = [BOS] + self.tok.encode(str(question)[:80] + " ответ:")
            ids = ctx_ids[-(ctx // 2):]
            out = [first_ids[0]]
            ids.append(out[0])
            used = set()
            ends = 0
            # v3.117: куски BPE складываются только в слова ФАКТОВ
            v2 = getattr(self.tok, "ver", 1) >= 2
            fw, fpref, cur_w = set(), set(), ""
            if v2:
                for x in chosen:
                    for w in WORD_RE.findall(llm2.normalize(x).lower()):
                        if llm2._ALNUM_RE.match(w):
                            fw.add(w)
                            for i_ in range(1, len(w) + 1):
                                fpref.add(w[:i_])
                tk0 = self.tok.inv.get(out[0], "")
                cur_w = tk0[1:] if tk0.startswith("\u2581") and \
                    llm2._ALNUM_RE.match(tk0[1:]) else ""
            al_list = sorted(t for t in allowed if t < len(mask))
            # v3.70: недоученная модель рано выбирала «конец текста» и ответ
            # получался обрубком — пока не набрано min_frac длины факта,
            # конец запрещён
            min_len = int(min_frac * len(first_ids)) if min_frac else 0
            for _ in range(max_new):
                X = np.array([ids[-ctx:]], dtype=np.int64)
                logits, _c = self.forward(X)
                lg = logits[0, -1].astype(np.float64)
                lg = lg + mask[:len(lg)]
                for u in nxt.get(out[-1], ()):
                    lg[u] += copy
                    if (out[-1], u) in used:
                        lg[u] = -1e9
                for t in set(out[-12:]):
                    if v2 and not self.tok.inv.get(t, "").startswith("\u2581"):
                        continue          # окончания слов повторяются законно
                    lg[t] -= math.log(rep) * 2
                # v3.114: без зацикливания «н н н англ н»: подряд то же слово
                # нельзя, каждое повторение штрафуется сильнее, больше 2 раз —
                # запрет (знаки препинания — не в счёт)
                if out and not (v2 and not self.tok.inv.get(
                        out[-1], "").startswith("\u2581")):
                    lg[out[-1]] = -1e9
                cnt_ = {}
                for t in out:
                    cnt_[t] = cnt_.get(t, 0) + 1
                for t, n_ in cnt_.items():
                    if v2 and not self.tok.inv.get(t, "").startswith("\u2581"):
                        continue
                    tx_ = self.tok.inv.get(t, "").lstrip("\u2581")
                    if tx_ in (",", ".", "-", "—", ":", ";", "(", ")"):
                        continue
                    cap_ = 3 if tx_ in _FUNC_W else 2   # «и», «в» — до 3 раз
                    lg[t] = -1e9 if n_ >= cap_ else lg[t] - 1.5 * n_
                lg[UNK] = -1e9
                lg[PAD] = -1e9
                if len(out) < min_len:
                    lg[EOS] = -1e9
                    for tid_ in self._end_ids():     # и точку — пока рано
                        if tid_ < len(lg):
                            lg[tid_] = -1e9
                if v2:
                    done_w = (not cur_w) or (cur_w in fw)
                    for t_ in al_list:
                        if t_ >= len(lg) or lg[t_] <= -1e8:
                            continue
                        tx_ = self.tok.inv.get(t_, "")
                        if t_ == EOS:
                            ok_ = done_w
                        elif tx_.startswith("\u2581"):
                            b_ = tx_[1:]
                            ok_ = done_w and (not llm2._ALNUM_RE.match(b_)
                                              or b_ in fpref)
                        else:
                            ok_ = bool(cur_w) and (cur_w + tx_) in fpref
                        if not ok_:
                            lg[t_] = -1e9
                t = int(np.argmax(lg / max(1e-3, temperature)))
                if lg[t] <= -1e8 or t == EOS:
                    break
                used.add((out[-1], t))
                out.append(t)
                ids.append(t)
                if v2:
                    tkx = self.tok.inv.get(t, "")
                    if tkx.startswith("\u2581"):
                        cur_w = tkx[1:] if llm2._ALNUM_RE.match(tkx[1:]) else ""
                    elif llm2._ALNUM_RE.match(tkx or "-"):
                        cur_w += tkx
                tx = self.tok.inv.get(t, "").lstrip("\u2581")
                if tx in (".", "!", "?") and len(out) >= 3:
                    ends += 1
                    if ends >= 2 or len(out) >= 12 or min_len:
                        break
            if len(out) < 3:
                return "", 0.0
            pairs = list(zip(out, out[1:]))
            fp = set(zip(fids, fids[1:]))
            cov = sum(1 for x in pairs if x in fp) / max(1, len(pairs))
            txt = self.tok.decode(out)
            # вернуть регистр: слова из фактов, как они там написаны
            src = {}
            for w in re.findall(r"[А-Яа-яЁёA-Za-z0-9]+", " ".join(chosen)):
                src.setdefault(w.lower(), w)
            txt = re.sub(r"[а-яёa-z0-9]+",
                         lambda m_: src.get(m_.group(0), m_.group(0)), txt)
            txt = re.sub(r"(^|[.!?]\s+)([а-яё])",
                         lambda m_: m_.group(1) + m_.group(2).upper(), txt)
            return txt, cov

        def _end_ids(self):
            """id токенов конца предложения (. ! ?)."""
            ids = getattr(self, "_endids", None)
            if ids is None or getattr(self, "_endids_n", -1) != len(self.tok.vocab):
                ids = [i for tk, i in self.tok.vocab.items()
                       if tk.lstrip("\u2581") in (".", "!", "?")]
                self._endids, self._endids_n = ids, len(self.tok.vocab)
            return ids

        def _plain_mask(self):
            """Маска для ОБЫЧНОГО текста: без токенов кода, <unk>/<pad>, без
            редких алфавитов (греческий и пр.) — именно они всплывали при
            «разборе по буквам» недоученной моделью."""
            n = self.p["E"].shape[0]
            if getattr(self, "_pmask_n", -1) == (len(self.tok.vocab), n):
                return self._pmask
            mk = np.zeros(n, dtype=np.float64)
            for tk, i in self.tok.vocab.items():
                if i >= n:
                    continue
                if tk.startswith("\u00a2") or (
                        tk.startswith("<") and i not in (EOS,)):
                    mk[i] = -1e9
                elif not tk.startswith("<") and not _ALLOWED_RE.match(tk):
                    mk[i] = -1e9
                elif tk.lstrip("\u2581").lower() in _GLOSS_W:
                    mk[i] = -1e9          # v3.115: «англ», «лат» — не слова речи
            mk[UNK] = mk[PAD] = -1e9
            self._pmask, self._pmask_n = mk, (len(self.tok.vocab), n)
            return mk

        def _is_frag(self, t):
            tk = self.tok.inv.get(int(t), "")
            return (len(tk) == 1 and tk.isalpha()) or (
                len(tk) == 2 and tk[0] == "\u2581" and tk[1].isalpha())

        def _frags(self):
            """Токены-буквы: «▁х» (начало слова) и «х» (продолжение)."""
            n = (len(self.tok.vocab), self.p["E"].shape[0])
            if getattr(self, "_fr_n", None) != n:
                st, ct = [], []
                for tk, i in self.tok.vocab.items():
                    if i >= n[1]:
                        continue
                    if len(tk) == 2 and tk[0] == "\u2581" and tk[1].isalpha():
                        st.append((i, tk[1]))
                    elif len(tk) == 1 and tk.isalpha():
                        ct.append((i, tk))
                self._fr, self._fr_n = (st, ct), n
            return self._fr

        def nll(self, prefix, text, max_tok=48):
            """v3.51: средняя потеря (nats/токен) текста при затравке prefix —
            «насколько модель считает этот текст вероятным после затравки».
            Только прямой ход, веса не трогает."""
            ctx = self.c["llm_ctx"]
            pre = [BOS] + self.tok.encode(prefix)
            tgt = self.tok.encode(text)[:max_tok]
            if not tgt:
                return None
            ids = (pre + tgt)[-ctx:]
            nt = min(len(tgt), len(ids) - 1)
            if nt < 1:
                return None
            X = np.array([ids[:-1]], dtype=np.int64)
            logits, _c = self.forward(X)
            lg = logits[0, -nt:].astype(np.float64)
            lg -= lg.max(axis=1, keepdims=True)
            lp = lg - np.log(np.exp(lg).sum(axis=1, keepdims=True))
            y = np.array(ids[-nt:], dtype=np.int64)
            return float(-lp[np.arange(nt), y].mean())

        # ---------------- v3.117: РЕЧЬ СЛОВАМИ (словарный автомат) ----------
        def generate_words(self, prompt, max_new=40, temperature=0.9,
                           top_k=40, top_p=0.92, seed=None, bias=None,
                           max_sent=2, ngram=None, first_word=""):
            """Свободная речь сети (мысль, разговор). Куски BPE складываются
            ТОЛЬКО в слова корпуса (WordGuide); жёсткие правила против петель
            (llm2); недоученной сети помогает n-граммная статистика базы
            (доля сети — llm2.net_share по её потере)."""
            r = np.random.RandomState(seed)
            g = self.tok.guide()
            n = self.p["E"].shape[0]
            V = n
            ctx_n = self.c["llm_ctx"]
            lam = llm2.net_share(self.loss, ngram)
            start_mask = getattr(self, "_start_mask", None)
            if start_mask is None or start_mask.shape[0] != V or \
                    getattr(self, "_start_mask_g", None) is not g:
                start_mask = np.full(V, -1e9)
                for i, _b in g.starts:
                    if i < V:
                        start_mask[i] = 0.0
                self._start_mask, self._start_mask_g = start_mask, g
            punct_of = {i: b for i, b in g.puncts if i < V}
            ids = [BOS] + self.tok.encode(prompt)[-(ctx_n - 1):]
            out = []                   # выбранные токены
            word_starts = []           # индексы в out, где начиналось слово
            words = []                 # законченные слова
            cur = ""                   # собираемое слово
            prev_punct = None
            stack = []
            sent_words = 0
            sents = 0
            ban_here = set()           # запреты после отката (на эту позицию)
            backtracks = 0
            cnt = {}
            pairs = set()
            fw_ = str(first_word or "").lower()
            if fw_ and fw_ in g.words:
                # v3.117: мысль начинается со слова темы — сеть продолжает его
                pcs = self.tok.encode(fw_)
                if pcs and all(i < V for i in pcs):
                    word_starts.append(0)
                    out.extend(pcs)
                    ids.extend(pcs)
                    cur = fw_

            def banned(w):
                if not w or w not in g.words:
                    return True
                if words and words[-1] == w:
                    return True
                cap = 3 if w in llm2.FUNC_W else 2
                if cnt.get(w, 0) >= cap:
                    return True
                if w not in llm2.FUNC_W and w in words[-6:]:
                    return True               # то же слово через пару слов
                if words and (words[-1], w) in pairs:
                    return True
                return False

            def close_word():
                nonlocal cur, sent_words
                if cur:
                    if words:
                        pairs.add((words[-1], cur))
                    words.append(cur)
                    cnt[cur] = cnt.get(cur, 0) + 1
                    sent_words += 1
                    cur = ""

            steps = 0
            while steps < max_new + 12:
                steps += 1
                complete = (not cur) or (not banned(cur))
                mk = np.full(V, -1e9)
                if cur:
                    for i, tx in g.conts:
                        if i < V and (cur + tx) in g.pref:
                            mk[i] = 0.0
                if complete:
                    can_new_word = len(out) < max_new
                    if can_new_word:
                        mk = np.maximum(mk, start_mask)
                    have_words = bool(words) or bool(cur)
                    last_w = cur or (words[-1] if words and prev_punct is None else "")
                    for i, b in punct_of.items():
                        if not have_words:
                            continue
                        if last_w in llm2.NO_PUNCT_AFTER and b not in llm2.OPENERS:
                            continue          # «на,» «и.» — так не бывает
                        if prev_punct is not None and not cur:
                            if not (prev_punct in llm2.CLOSERS and
                                    b in (".", ",", ";", ":", "!", "?")):
                                continue
                        if b in llm2.CLOSERS:
                            if not stack or stack[-1] != llm2.CLOSERS[b]:
                                continue
                        if b in llm2.OPENERS and (len(stack) >= 1 or
                                                  len(out) > max_new - 6):
                            continue
                        if b in llm2.END_PUNCT and (sent_words + (1 if cur else 0)) < 3:
                            continue
                        if b in llm2.END_PUNCT and stack:
                            continue
                        mk[i] = 0.0
                    if prev_punct in llm2.END_PUNCT and not cur and sents:
                        mk[EOS] = 0.0
                for i in ban_here:
                    if i < V:
                        mk[i] = -1e9
                if mk.max() < -1e8:
                    # тупик: собираемое слово не складывается — откат слова
                    if cur and word_starts and backtracks < 6:
                        k = word_starts.pop()
                        first = out[k]
                        del out[k:]
                        ids = ([BOS] + self.tok.encode(prompt)[-(ctx_n - 1):]
                               + out)
                        cur = ""
                        ban_here = {first}
                        backtracks += 1
                        continue
                    break
                X = np.array([ids[-ctx_n:]], dtype=np.int64)
                logits, _c = self.forward(X)
                lg = logits[0, -1].astype(np.float64)
                p_net = _softmax(lg)
                if ngram is not None and lam < 0.999:
                    p_ng = ngram.probs(ids[-3:])
                    if p_ng.shape[0] < V:
                        p_ng = np.concatenate([p_ng, np.full(V - p_ng.shape[0], 1e-9)])
                    p_mix = lam * p_net + (1.0 - lam) * p_ng[:V]
                else:
                    p_mix = p_net
                lg = np.log(p_mix + 1e-12)
                if bias is not None:
                    lg[:len(bias)] += 0.5 * bias[:V]
                lg = lg + mk
                lg = lg / max(1e-3, temperature * _freq_temp())
                if top_k:
                    thr = np.sort(lg)[-min(top_k, V)]
                    lg = np.where(lg < thr, -1e9, lg)
                pr = _softmax(lg)
                if top_p < 1.0:
                    order = np.argsort(-pr)
                    cum = np.cumsum(pr[order])
                    cut = order[cum > top_p]
                    if len(cut) > 1:
                        pr[cut[1:]] = 0
                        pr /= pr.sum()
                t = int(r.choice(V, p=pr))
                ban_here = set()
                if t == EOS:
                    close_word()
                    break
                tk = self.tok.inv.get(t, "")
                if t in punct_of:
                    close_word()
                    b = punct_of[t]
                    if b in llm2.OPENERS:
                        stack.append(b)
                    elif b in llm2.CLOSERS and stack:
                        stack.pop()
                    prev_punct = b
                    if b in llm2.END_PUNCT:
                        sents += 1
                        sent_words = 0
                    out.append(t)
                    ids.append(t)
                    if b in llm2.END_PUNCT and (sents >= max_sent or
                                                len(out) >= max_new):
                        break
                    continue
                if tk.startswith("\u2581"):
                    close_word()
                    word_starts.append(len(out))
                    cur = tk[1:]
                else:
                    cur += tk
                prev_punct = None
                out.append(t)
                ids.append(t)
            if cur and banned(cur) and word_starts:
                del out[word_starts[-1]:]      # недособранное слово — прочь
            txt = self.tok.decode(out)
            txt = re.sub(r"\s+([)\u00bb])", r"\1", txt)
            txt = re.sub(r"([(\u00ab])\s+", r"\1", txt)
            return txt

        def generate(self, prompt, max_new=40, temperature=0.9, top_k=40,
                     top_p=0.92, rep=1.3, seed=None, lex=None, bias=None,
                     first_word=""):
            """v3.117: словарь BPE -> речь словами (generate_words).
            lex — словарь словоформ (lexicon.Lex): слово, собранное из
            букв, допустимо только если оно в словаре (v3.45); цельные
            слова-токены выбираются как обычно. Обучение не затрагивается."""
            if getattr(self.tok, "ver", 1) >= 2:
                return self.generate_words(
                    prompt, max_new=max_new, temperature=temperature,
                    top_k=top_k, top_p=top_p, seed=seed, bias=bias,
                    ngram=_ngram_get(self), first_word=first_word)
            r = np.random.RandomState(seed)
            pm = self._plain_mask()
            fr_st, fr_ct = self._frags() if lex is not None else ([], [])
            cur = ""                      # буквы собираемого слова
            ids = [BOS] + self.tok.encode(prompt)[-(self.c["llm_ctx"] - 1):]
            out = []
            run = 0                       # подряд идущие «буквы-осколки»
            for _ in range(max_new):
                X = np.array([ids[-self.c["llm_ctx"]:]], dtype=np.int64)
                logits, _c = self.forward(X)
                lg = logits[0, -1].astype(np.float64) + pm
                if bias is not None:             # v3.48: уровень сигнала
                    lg[:len(bias)] += bias[:len(lg)]
                if lex is not None:
                    done = (not cur) or (cur in lex.words)
                    lm = np.full(len(lg), -1e9)
                    if done:                       # слово закончено
                        keep = np.ones(len(lg), dtype=bool)
                        for tid, _c in fr_st + fr_ct:
                            if tid < len(lg):
                                keep[tid] = False
                        lm[keep] = 0.0
                        for tid, ch in fr_st:      # новое слово буквами
                            if tid < len(lg) and (ch in lex.pref or
                                                  ch in lex.words):
                                lm[tid] = 0.0
                    else:
                        lm[EOS] = -1e9
                    if cur:
                        for tid, ch in fr_ct:
                            w2 = cur + ch
                            if tid < len(lg) and (w2 in lex.pref or
                                                  w2 in lex.words):
                                lm[tid] = 0.0
                    lg = lg + lm
                    if lg.max() < -1e8:            # тупик: бросаем слово
                        while out and self._is_frag(out[-1]):
                            out.pop()
                        break
                for t in set(ids[-20:]):
                    lg[t] -= math.log(rep) * 3
                if run >= 4 and lex is None:   # слово рассыпалось на буквы — запрет
                    for tk, i in self.tok.vocab.items():          # осколков
                        if i < len(lg) and len(tk) == 1 and tk.isalpha():
                            lg[i] -= 1e9
                lg = lg / max(1e-3, temperature * _freq_temp())
                if top_k:
                    thr = np.sort(lg)[-min(top_k, len(lg))]
                    lg = np.where(lg < thr, -1e9, lg)
                pr = _softmax(lg)
                if top_p < 1.0:
                    order = np.argsort(-pr)
                    cum = np.cumsum(pr[order])
                    cut = order[cum > top_p]
                    if len(cut) > 1:
                        pr[cut[1:]] = 0
                        pr /= pr.sum()
                t = int(r.choice(len(pr), p=pr))
                if t == EOS:
                    break
                tk = self.tok.inv.get(t, "")
                isfrag = (len(tk) == 1 and tk.isalpha()) or (
                    len(tk) == 2 and tk[0] == "\u2581" and tk[1].isalpha())
                run = run + 1 if (len(tk) == 1 and tk.isalpha()) else 0
                if lex is not None:
                    if isfrag:
                        cur = (cur if len(tk) == 1 else "") + tk[-1]
                    else:
                        cur = ""
                ids.append(t)
                out.append(t)
                if run >= (8 if lex is None else 26):   # не вышло словом — обрываем
                    out = out[:-run]
                    break
            if lex is not None and cur and cur not in lex.words:
                while out and self._is_frag(out[-1]):      # оборванное слово
                    out.pop()
            return self.tok.decode(out)

        def generate_code(self, prompt, max_new=220, temperature=0.7,
                          top_k=20, seed=None, deadline=None):
            """v3.41: продолжить код. Только токены кода; отступы и регистр
            сохраняются. Возврат: сырой текст продолжения."""
            import codecorpus
            r = np.random.RandomState(seed)
            if getattr(self, "_cmask_n", -1) != len(self.tok.vocab):
                mk = np.full(self.p["E"].shape[0], -1e9, dtype=np.float64)
                for tk, i in self.tok.vocab.items():
                    if tk.startswith("\u00a2") and i < len(mk):
                        mk[i] = 0.0
                mk[EOS] = 0.0
                self._cmask, self._cmask_n = mk, len(self.tok.vocab)
            ids = [BOS] + self.tok.encode(CODE_MARK + prompt)[
                -(self.c["llm_ctx"] - 1):]
            out = []
            text = ""
            for i in range(max_new):
                if deadline is not None and time.monotonic() >= deadline:
                    break
                X = np.array([ids[-self.c["llm_ctx"]:]], dtype=np.int64)
                logits, _c = self.forward(X)
                lg = logits[0, -1].astype(np.float64) + self._cmask
                lg = lg / max(1e-3, temperature)
                if top_k:
                    thr = np.sort(lg)[-min(top_k, len(lg))]
                    lg = np.where(lg < thr, -1e9, lg)
                pr = _softmax(lg)
                t = int(r.choice(len(pr), p=pr))
                if t == EOS:
                    break
                ids.append(t)
                out.append(t)
                if i % 6 == 5 or t == self.tok.vocab.get(_C_NL):
                    text = self.tok.decode_code(out)
                    if codecorpus.trim(text) and re.search(
                            r"\n\n(?=\S)|\n# task:", text):
                        break
            return self.tok.decode_code(out)

        # ------------------------------ сохранение ------------------------
        def save(self, path):
            if not self.p or getattr(self, "_dead", False):
                return                        # v3.116: удалённая модель не пишет на диск
            np.savez_compressed(path + ".npz", **self.p)
            self._adam_save()                 # v3.89: Adam — тоже на диск
            with open(path + ".json", "w", encoding="utf-8") as f:
                json.dump({"vocab": self.tok.vocab, "c": self.c,
                           "merges": self.tok.merges, "words": self.tok.words,
                           "tok_ver": self.tok.ver,
                           "steps": self.steps, "loss": self.loss,
                           "meta": self.meta}, f, ensure_ascii=False)

        def load(self, path):
            if not (os.path.isfile(path + ".npz") and
                    os.path.isfile(path + ".json")):
                return False
            with open(path + ".json", "r", encoding="utf-8") as f:
                d = json.load(f)
            if {k: d["c"].get(k) for k in ("llm_d", "llm_layers", "llm_heads",
                                            "llm_ctx")} != \
                    {k: self.c[k] for k in ("llm_d", "llm_layers",
                                            "llm_heads", "llm_ctx")}:
                return False                  # размеры сменили — заново
            if int(d.get("tok_ver") or 1) < llm2.TOK_VER:
                # v3.117: прежняя LLM (словарь по словам + <unk>) несовместима
                # с переработанной — её место займёт новая, обучится заново
                if not getattr(GPT, "_old_logged", False):
                    GPT._old_logged = True
                    store_log("ОБУЧЕНИЕ LLM", "прежняя нейросеть (словарь v1, "
                              "зацикливалась на «англ н н») заменяется "
                              "переработанной v3.117 — обучение с нуля")
                return False
            z = np.load(path + ".npz")
            self.p = {k: z[k] for k in z.files}
            self.adam = None          # v3.83: ответам Adam не нужен
            self.tok = Tokenizer(d.get("vocab") or {}, d.get("merges"),
                                 d.get("words"), d.get("tok_ver"))
            self.steps = int(d.get("steps") or 0)
            self.loss = d.get("loss")
            self.meta.update(d.get("meta") or {})
            self.ready = self.steps > 0
            return True

    # ============================ ОБУЧЕНИЕ В ФОНЕ ===========================
    def _state_path():
        try:
            import store
            return store.p(_STATE)
        except Exception:
            return _STATE

    def model():
        global _M
        with _LOCK:
            c = cfg()
            if _M is None or any(_M.c.get(k) != c[k] for k in
                                 ("llm_d", "llm_layers", "llm_heads",
                                  "llm_ctx")):
                _M = GPT(c)
                try:
                    _M.load(_state_path())
                except Exception:
                    pass
            return _M

    def _article_text(title, text):
        """Статья -> текст обучения: обычная — «заголовок. текст», исходник
        с GitHub — набор функций со сплошным кодом (см. codecorpus)."""
        import codecorpus
        if codecorpus.is_code_title(title):
            return codecorpus.article_text(title, text)
        return str(title or "") + ". " + clean_text_corpus(str(text)[:6500])[:6000]

    _CORPUS_KEYS = []          # v3.75: ключи статей, вошедших в корпус обучения
    _CORPUS_SKIP = {"q": 0, "dup": 0, "short": 0}   # v3.144: что не вошло и почему

    def _collect_corpus(max_tokens):
        texts = []
        n_art = 0
        _CORPUS_KEYS[:] = []
        try:
            import kb
            import hashlib as _hl
            import textutils as _tu
            seen_h = set()
            _CORPUS_SKIP.update(q=0, dup=0, short=0)
            for a in kb.list_articles(limit=1000000, include_devices=True) or []:
                art = kb.article(a.get("id")) if a.get("id") else None
                if art and art.get("text"):
                    # v3.144: КОРПУС БЕЗ ШУМА — меньше токенов, меньше шагов.
                    # Сообщения-вопросы из чатов (учат LLM отвечать вопросом),
                    # повторы одного и того же текста и короткие реплики чата
                    # в обучение не идут.
                    body = str(art["text"])
                    src = str(a.get("source") or art.get("source") or "").lower()
                    chat = any(w in src for w in ("макс", "история чата", "группа", "telegram", "телеграм"))
                    if _tu.question_only(body[:1500]):
                        _CORPUS_SKIP["q"] += 1
                        continue
                    if chat and len(body.strip()) < 60:
                        _CORPUS_SKIP["short"] += 1
                        continue
                    h_ = _hl.md5(" ".join(body.lower().split())[:3000]
                                 .encode("utf-8", "replace")).hexdigest()
                    if h_ in seen_h:
                        _CORPUS_SKIP["dup"] += 1
                        continue
                    seen_h.add(h_)
                    texts.append(_article_text(art["title"], art["text"]))
                    _CORPUS_KEYS.append("kb:" + str(a["id"]))
                    n_art += 1
                if sum(len(t) for t in texts) > max_tokens * 6:
                    break
        except Exception:
            pass
        try:
            import llmlink                    # v3.32: оба мозга -> корпус
            texts.extend(llmlink.corpus_extra())
        except Exception:
            try:
                import store
                for t in store.thoughts_list(200):
                    if t.get("text") and t.get("kind") != "llm":
                        texts.append(str(t["text"]))
            except Exception:
                pass
        if not texts:
            try:
                from tinyllm_py import SEED_TEXTS
                texts = list(SEED_TEXTS)
            except Exception:
                texts = ["Мозг двенадцать — небольшая нейросеть на плате. "
                         "Облако хранит базу знаний и учится на статьях."]
        return texts, n_art

    # ============ v3.100: ШАГ ОБУЧЕНИЯ ЗА ЗАДАННОЕ ВРЕМЯ (llm_step_s) ============
    # Заявка владельца: «на один шаг — 10 секунд». Время шага = обновление
    # весов (Adam, не зависит от батча) + примеры батча (каждый — прямой и
    # обратный проход по окну T). После каждого шага замеряем обе части и
    # подбираем, СКОЛЬКО ПРИМЕРОВ взять в следующий шаг (до llm_batch), чтобы
    # шаг укладывался в цель. Не влезает даже один пример — укорачиваем окно
    # (до 32 токенов). Обновление весов само дольше цели — честно пишем, что
    # быстрее нельзя без уменьшения модели. Скорость обучения при меньшем
    # батче снижается как sqrt(доли батча) — чтобы шаги не «шумели».
    # ============ v3.102: ОБУЧЕНИЕ LLM ВЕДЁТ ПЛАТА (частота + мысли) ============
    def _board_rank(texts_):
        """Баллы статей: созвучие МЫСЛЯМ плат (boardmind — интерес плат) +
        созвучие ТЕКУЩЕЙ ЧАСТОТЕ генератора (freqprior). Порядок обучения —
        по убыванию: сначала то, о чём думают платы. Данных нет — нули."""
        out = [0.0] * len(texts_)
        try:
            import boardmind
            it = boardmind.interest(80)
        except Exception:
            it = []
        try:
            import freqprior
            z = freqprior.level()
        except Exception:
            freqprior, z = None, None
        if not it and z is None:
            return out, False
        for i_, t_ in enumerate(texts_):
            head = str(t_)[:1500]
            sc = 0.0
            if it:
                try:
                    sc += float(boardmind.score(head, it))
                except Exception:
                    pass
            if z is not None and freqprior is not None:
                try:
                    sc += 0.5 * float(freqprior.bonus(head, z=z))
                except Exception:
                    pass
            out[i_] = sc
        return out, True

    _BT_CACHE = {"ts": 0.0, "ids": []}

    def _board_window(m, T_):
        """Окно из СВЕЖИХ мыслей плат (связь мозгов, лента мыслей) для шага
        обучения: модель учится и тому, что платы думают сейчас. -> массив
        T_+1 токенов | None (мыслей мало)."""
        now = time.time()
        if now - _BT_CACHE["ts"] > 30:
            _BT_CACHE["ts"] = now
            _BT_CACHE["uses"] = 0
            txt = []
            try:
                import boardlink
                txt += [t for _b, t, _s in boardlink.recent(1800, 20)]
            except Exception:
                pass
            try:
                import store as _st
                for t in _st.thoughts_list(60):
                    if t.get("kind") in ("llm", "link") or \
                            t.get("board") in ("облако", "учитель"):
                        continue
                    tx = re.sub(r"^\W*🧠\s*«|»\s*$", "",
                                str(t.get("text") or "")).strip()
                    if len(tx) >= 12:
                        txt.append(tx)
            except Exception:
                pass
            ids_ = []
            # v3.115: только годные мысли (не шум и не «англ н англ»), без
            # повторов; мысли плат складываются из их словаря, и раньше
            # одни и те же 30 мыслей учились каждые 3 шага — модель
            # переучивалась на их словах
            seen_ = set()
            for t in txt[-30:]:
                t = clean_text_corpus(t)
                k_ = t.strip().lower()[:80]
                if k_ in seen_ or not thought_ok(t):
                    continue
                seen_.add(k_)
                try:
                    ids_.extend(m.tok.encode(t, add_bos=True, add_eos=True))
                except Exception:
                    pass
            _BT_CACHE["ids"] = ids_
        ids_ = _BT_CACHE["ids"]
        # один набор мыслей — не больше 3 окон за 30 с
        if len(ids_) < T_ + 1 or _BT_CACHE.get("uses", 0) >= 3:
            return None
        _BT_CACHE["uses"] = _BT_CACHE.get("uses", 0) + 1
        o = random.randint(0, len(ids_) - T_ - 1)
        return np.array(ids_[o:o + T_ + 1], dtype=np.int64)

    # v3.111: своя LLM пишет ответ — обучение ждёт между шагами (до 20 с),
    # иначе на большой модели генерация ответа делила процессор с обучением,
    # не укладывалась в лимит времени ответа, и «своя LLM не отвечала»
    _ANSWERING = [0, 0.0]

    def _code_weave(m, lr):
        """v3.176: шаг на коде из второго потока (codestream) между шагами
        обучения текстам. _TrainingHandoff пропускается наверх."""
        t_ = time.time()
        try:
            import llmstreams                 # v3.177: потоки — код, МАКС, разговор
            llmstreams.interleave(m, lr)
        except _TrainingHandoff:
            raise
        except Exception:
            pass
        return time.time() - t_

    def _yield_to_answers():
        t0 = time.time()
        while (_ANSWERING[0] > 0 or time.time() - _ANSWERING[1] < 1.5) and \
                time.time() - t0 < 20.0:
            time.sleep(0.05)

    class _Pace(object):
        MIN_T = 32

        def __init__(self, B_max, T_max):
            self.B_max, self.T_max = max(1, int(B_max)), max(4, int(T_max))
            self.per_tok = None          # с на (пример × токен окна)
            self.fixed = 0.0             # с на обновление весов
            self.B = 1                   # первый шаг — один пример (замер)
            self.T = self.T_max
            self.last = 0.0
            self.note = ""

        @staticmethod
        def target():
            try:
                v = float(store_get("llm_step_s"))
            except (TypeError, ValueError):
                v = 10.0
            return max(0.0, v)

        def off(self):
            return self.target() <= 0

        def lr_k(self):
            return max(0.25, math.sqrt(self.B / float(self.B_max)))

        def update(self, dt, t_opt, B, T):
            self.last = dt
            t_opt = max(0.0, min(dt, float(t_opt or 0.0)))
            pt = max(1e-7, (dt - t_opt) / float(max(1, B * T)))
            a = 0.5 if self.per_tok is not None else 1.0
            self.per_tok = pt if self.per_tok is None else \
                (1 - a) * self.per_tok + a * pt
            self.fixed = t_opt if not self.fixed else 0.5 * self.fixed + 0.5 * t_opt
            tgt = self.target()
            if tgt <= 0:
                self.B, self.T, self.note = self.B_max, self.T_max, ""
                return
            room = tgt * 0.92 - self.fixed
            if room <= self.per_tok * self.MIN_T:
                self.B, self.T = 1, min(self.T_max, self.MIN_T)
                self.note = ("обновление весов модели само занимает ~%.1f с — "
                             "шаг быстрее %.0f с не получится; уменьши модель "
                             "(d, слои, словарь) или выключи «Adam на диске»"
                             % (self.fixed, tgt))
                return
            T = self.T_max
            B = int(room / (self.per_tok * T))
            if B < 1:                    # даже один пример не влезает — окно короче
                T = max(self.MIN_T, min(self.T_max, int(room / self.per_tok)))
                B = 1
            self.B = max(1, min(self.B_max, B))
            self.T = T
            self.note = "" if (self.B == self.B_max and self.T == self.T_max) \
                else ("примеров в шаге %d из %d%s — чтобы шаг шёл ~%.0f с"
                      % (self.B, self.B_max, (", окно %d из %d токенов"
                                              % (self.T, self.T_max))
                         if self.T < self.T_max else "", tgt))

        def meta(self, m):
            m.meta.update({"step_s": round(self.last, 2),
                           "step_target": self.target(),
                           "step_batch": self.B, "step_ctx": self.T,
                           "step_opt_s": round(self.fixed, 2),
                           "step_note": self.note})

    def _train_all():
        m = model()
        c = m.c
        with _LOCK:
            if getattr(m, "_code_course", False) and threading.current_thread() is not _THREAD[0]:
                return
            m.training = True
            m._stop = False
        try:
            est = estimate(c)
            m.meta["error"] = ""
            if not est["fits"] and not c["llm_force"]:
                m.meta["error"] = (
                    "настройки сохранены, но обучение не запущено: модели "
                    "нужно ~%s МБ, в компьютере %s МБ. Включи «обучать "
                    "всё равно» или уменьши размер" % (est["need_mb"],
                                                       est["ram_mb"]))
                return
            texts, n_art = _collect_corpus(c["llm_corpus_tokens"])
            # v3.38: обученную/частично обученную модель НЕ сбрасываем: словарь
            # дополняется (id прежних токенов те же), обучение продолжается
            resume = bool(m.ready and m.steps > 0)
            if resume:
                if m.tok.extend(texts, c["llm_vocab"]):
                    m.grow_vocab(m.tok.size)
                tok = m.tok
            else:
                tok = Tokenizer()
                tok.build(texts, c["llm_vocab"])
            ids = []
            art_bounds = []          # v3.101: (начало, конец, ключ) статей в корпусе
            for i_, t in enumerate(texts):
                b0 = len(ids)
                ids.extend(tok.encode(t, add_bos=True, add_eos=True))
                if i_ < len(_CORPUS_KEYS):
                    art_bounds.append((b0, len(ids), _CORPUS_KEYS[i_]))
                if len(ids) >= c["llm_corpus_tokens"]:
                    break
            ids = ids[:c["llm_corpus_tokens"]]
            if len(ids) < c["llm_ctx"] + 2:
                m.training = False
                return
            fresh = not resume
            if fresh:
                m.tok = tok
                m.init_params(tok.size)
                m.steps = 0
                # v3.116: БАГФИКС переобучения — новые веса учились со СТАРЫМ
                # состоянием Adam с диска (файлы подкачки подходили по форме),
                # и «переобученная» модель вела себя как прежняя
                m.adam = None
                _adam_wipe()
                for k_ in ("plan_open", "plan_keys_done", "plan_step",
                           "plan_misc_done", "seq_pos", "seq_total",
                           "plan_note", "board_steps", "error"):
                    m.meta.pop(k_, None)
            T, B = c["llm_ctx"], c["llm_batch"]
            data = np.array(ids, dtype=np.int64)
            # v3.101: окна встык (шаг T, без нахлёста) — раньше шаг был T//2 и
            # каждый токен учился дважды за эпоху: окон и шагов было вдвое больше
            stride = max(1, T)
            starts = list(range(0, len(data) - T - 1, stride))
            # v3.100: порядок окон на все эпохи заранее (тот же RandomState(7) —
            # продолжение после перезапуска идёт по тем же окнам); шаг берёт
            # столько окон, сколько успевает за llm_step_s
            rs = np.random.RandomState(7)
            order = []
            # v3.101: ПЕРВАЯ эпоха — группами статей: статьи в случайном
            # порядке, по 16 статей в группе, окна внутри группы перемешаны.
            # Если план остановится на потолке шагов, пройденные статьи
            # пройдены ЦЕЛИКОМ (их не надо доучивать), а не по кусочку каждой.
            import bisect as _bs
            a_first = [a0 for a0, _a1, _k in art_bounds]
            groups = {}
            for w in starts:
                ai = _bs.bisect_right(a_first, w) - 1
                if ai < 0 or w >= art_bounds[ai][1]:
                    ai = -1                      # мысли/материалы мозга
                groups.setdefault(ai, []).append(w)
            keys_ = [k_ for k_ in groups if k_ >= 0]
            rs.shuffle(keys_)
            # v3.102: порядок статей ведёт ПЛАТА — по её мыслям и частоте
            try:
                bsc, led = _board_rank([texts[i_] for i_ in range(len(art_bounds))])
            except Exception:
                bsc, led = [], False
            if led:
                keys_.sort(key=lambda k_: -bsc[k_])
                m.meta["board_led"] = {
                    "on": True, "top": [str(texts[k_])[:60] for k_ in keys_[:3]]}
            else:
                m.meta["board_led"] = {"on": False}
            if -1 in groups:
                keys_.insert(0, -1)          # мысли и материалы мозга — первыми
            # v3.103: ПРОДОЛЖЕНИЕ ПЛАНА после перерыва (перезапуск, сбой,
            # обновление) — по СПИСКУ ПРОЙДЕННЫХ СТАТЕЙ, а не по номеру окна:
            # корпус меняется (мысли плат, новые статьи), и раньше план после
            # любого перерыва начинался с нуля. Пройденные целиком статьи
            # в первую эпоху не входят, счёт шагов плана продолжается.
            plan_open = bool(not fresh and m.meta.get("plan_open"))
            done_keys = set(m.meta.get("plan_keys_done") or []) \
                if plan_open else set()
            misc_done = bool(m.meta.get("plan_misc_done")) if plan_open \
                else False
            if plan_open and (done_keys or misc_done):
                keys_ = [k_ for k_ in keys_ if not (
                    (k_ < 0 and misc_done) or
                    (k_ >= 0 and art_bounds[k_][2] in done_keys))]
            win_art = {}
            rem_art = {}
            for k_ in keys_:
                rem_art[k_] = len(groups[k_])
                for w in groups[k_]:
                    win_art[w] = k_
            first = []
            for gi in range(0, len(keys_), 16):
                blk = []
                for k_ in keys_[gi:gi + 16]:
                    blk.extend(groups[k_])
                rs.shuffle(blk)
                first.extend(blk)
            for _ep in range(c["llm_epochs"]):
                if _ep == 0:
                    st_ = first
                else:
                    st_ = list(starts)
                    rs.shuffle(st_)
                order.extend(st_[:len(st_) - len(st_) % B] if len(st_) >= B
                             else st_)
            total_seq = max(1, len(order))
            pace = _Pace(B, T)
            if pace.off():
                pace.B, pace.T = B, T
            pos = 0
            # v3.101: ПОТОЛОК ШАГОВ ПЛАНА (llm_plan_steps, по умолчанию 3000 ≈
            # 8 ч при 10 с на шаг; 0 — без потолка). Окна идут эпоха за эпохой
            # (сначала весь корпус по разу), план кончается на потолке; статьи,
            # что целиком не успели, остаются «новыми» и доучиваются
            # дообучением по частям — ничего не теряется.
            plan_done = int(m.meta.get("plan_step") or 0) if plan_open else 0
            if plan_open:
                try:
                    store_log("ОБУЧЕНИЕ LLM", "продолжаю план: шаг %d, пройдено "
                              "статей %d, осталось окон %d" % (
                                  plan_done, len(done_keys), total_seq))
                except Exception:
                    pass
            m.meta.update({"plan_open": True, "plan_misc_done": misc_done,
                           "plan_keys_done": sorted(done_keys)})

            def _cap():
                try:
                    return max(0, int(float(store_get("llm_plan_steps") or 0)))
                except (TypeError, ValueError):
                    return 0

            def _total_steps():
                left = -(-(total_seq - pos) // max(1, pace.B))
                cp = _cap()
                if cp:
                    left = min(left, max(0, cp - plan_done))
                return int(m.steps + left)
            trained_w = set()            # окна, что учились в этом плане
            m.meta["corpus_skip"] = dict(_CORPUS_SKIP)       # v3.144
            m.meta.update({"total_steps": _total_steps(), "kb_articles": n_art,
                           "corpus_tokens": len(ids), "corpus_docs": len(texts),
                           "seq_total": total_seq, "seq_pos": pos})
            budget = c["llm_cpu"]
            last_save = time.time()
            path = _state_path()
            # v3.79: потолок времени обучения (llm_time_h; 0 — без): окна,
            # что не успеваются, отбрасываются; расписание lr (косинус) идёт
            # по подогнанному числу окон
            try:
                limit_s = float(c.get("llm_time_h") or 0) * 3600.0
            except (TypeError, ValueError):
                limit_s = 0.0
            t_run0, p_run0 = time.time(), pos
            m.meta.pop("time_note", None)
            m.meta.pop("plan_note", None)
            m.meta["plan_step"] = plan_done
            n_run = 0
            while pos < total_seq:
                if m._stop:
                    return
                if _cap() and plan_done >= _cap():
                    m.meta["plan_note"] = (
                        "план остановлен на потолке %d шагов: статей пройдено "
                        "целиком %d; остальное доучит дообучение по частям"
                        % (_cap(), len(done_keys)))
                    break
                _yield_to_answers()
                t0 = time.time()
                Bk = max(1, min(pace.B, total_seq - pos))
                Tk = pace.T
                # v3.102: окно из свежих мыслей плат — в каждый шаг при батче
                # ≥2 (вместо одного окна корпуса), при батче 1 — каждый 3-й
                # шаг целиком; окна корпуса при этом не пропускаются
                bw = None
                if m.steps % 10 == 9:          # v3.115: реже (было: каждый шаг)
                    bw = _board_window(m, Tk)
                n_corp = Bk - 1 if (bw is not None and Bk >= 2) else \
                    (0 if bw is not None else Bk)
                idx = order[pos:pos + n_corp]
                handoff_meta = {k: list(v) if isinstance(v, list) else v for k, v in m.meta.items()}
                trained_w.update(idx)
                for w in idx:                # статья пройдена целиком — в список
                    k_ = win_art.get(w)
                    if k_ is None or k_ not in rem_art:
                        continue
                    rem_art[k_] -= 1
                    if rem_art[k_] <= 0:
                        del rem_art[k_]
                        if k_ < 0:
                            m.meta["plan_misc_done"] = True
                        else:
                            done_keys.add(art_bounds[k_][2])
                            m.meta.setdefault("plan_keys_done", []).append(
                                art_bounds[k_][2])   # в любое сохранение
                off = int(rs.randint(0, T - Tk + 1)) if Tk < T else 0
                xs = [data[s_ + off:s_ + off + Tk] for s_ in idx]
                ys = [data[s_ + off + 1:s_ + off + Tk + 1] for s_ in idx]
                if bw is not None:
                    xs.append(bw[:-1])
                    ys.append(bw[1:])
                    m.meta["board_steps"] = int(m.meta.get(
                        "board_steps") or 0) + 1
                X, Y = np.stack(xs), np.stack(ys)
                Bk = n_corp                 # сколько окон корпуса пройдено
                warm = min(1.0, (m.steps + 1) / 50.0)
                prog = pos / float(total_seq)
                if _cap():                   # ход плана — и по шагам потолка
                    prog = max(prog, min(1.0, plan_done / float(_cap())))
                lr = c["llm_lr"] * warm * 0.5 * (1 + math.cos(math.pi * prog))
                if not pace.off():
                    lr *= pace.lr_k()
                try:
                    m.loss = m.train_step(X, Y, lr)
                except _TrainingHandoff:
                    m.meta = handoff_meta
                    return
                except MemoryError:
                    m.meta["error"] = "не хватило памяти — уменьши размер"
                    return
                m.steps += 1
                t0 += _code_weave(m, lr)      # v3.176: второй поток — код (время шага кода не в замер)
                pos += Bk
                n_run += 1
                plan_done += 1
                m.meta["plan_step"] = plan_done
                if not m.ready and m.steps >= 200:
                    # v3.87: отвечать можно и посреди долгого обучения —
                    # проверки опоры на факт всё равно отсеют слабое
                    m.ready = True
                dt = time.time() - t0
                if not pace.off():
                    pace.update(dt, getattr(m, "t_opt", 0.0), X.shape[0], Tk)
                else:
                    pace.B, pace.T, pace.last, pace.note = B, T, dt, ""
                pace.meta(m)
                m.meta["seq_pos"] = pos
                m.meta["tok_per_s"] = round(X.shape[0] * Tk / max(1e-6, dt), 1)
                if limit_s and n_run >= 8 and n_run % 10 == 0:
                    per_seq = (time.time() - t_run0) / max(1, pos - p_run0)
                    fit = pos + int(max(0.0, limit_s - (time.time() - t_run0))
                                    / max(1e-6, per_seq))
                    if fit < total_seq:
                        m.meta["time_note"] = (
                            "подогнано под потолок %s ч: %d окон вместо %d"
                            % (c.get("llm_time_h"), fit, total_seq))
                        total_seq = max(pos + 1, fit)
                        m.meta["seq_total"] = total_seq
                m.meta["total_steps"] = _total_steps()
                m.meta["eta_s"] = int((m.meta["total_steps"] - m.steps) *
                                      max(dt, 1e-3) /
                                      max(0.05, min(1.0, budget)))
                # v3.103: сохранение — не чаще, чем раз в 10× его длительности
                # (большая модель пишется на диск долго: раньше это шло раз в
                # минуту и съедало обучение); не реже раза в 10 минут
                if m._stop:                  # v3.116: остановили — не писать на диск
                    return
                if time.time() - last_save > max(60.0, min(
                        600.0, 10.0 * float(m.meta.get("save_s") or 0))):
                    m.meta["plan_keys_done"] = sorted(done_keys)
                    t_sv = time.time()
                    try:
                        m.save(path)
                    except Exception as e_:
                        try:
                            store_log("ОБУЧЕНИЕ LLM", "сохранение не удалось: %s"
                                      % e_)
                        except Exception:
                            pass
                    m.meta["save_s"] = round(time.time() - t_sv, 1)
                    last_save = time.time()
                if budget < 1.0:
                    time.sleep(dt * (1.0 / budget - 1.0))   # доля ЦП
                else:
                    time.sleep(0.01)     # v3.84: уступить процессор серверу
            m.ready = True
            m.meta["plan_open"] = False          # v3.103: план завершён
            m.meta["plan_keys_done"] = []
            try:
                store_log("ОБУЧЕНИЕ LLM", "план завершён: шагов %d, статей "
                          "пройдено целиком %d" % (plan_done, len(done_keys)))
            except Exception:
                pass
            try:
                # v3.75: усвоенными отмечаем только статьи, что РЕАЛЬНО вошли в
                # корпус (раньше — все 3000, и остальные не доучивались никогда)
                # v3.101: статья усвоена, только если ВСЕ её окна учились (план
                # мог кончиться на потолке шагов — остальное доучит дообучение)
                got_ = set(done_keys)
                if pos >= total_seq and not m.meta.get("plan_note"):
                    got_ = set(_CORPUS_KEYS)
                _mark_trained([(k_, t_) for k_, t_ in _keyed_corpus()
                               if not k_.startswith("kb:") or k_ in got_])
            except Exception:
                pass
            try:
                m.save(path)
            except Exception:
                pass
        except MemoryError:
            m.meta["error"] = "не хватило памяти — настройки сохранены; " \
                              "уменьши размер и переобучи"
            m.ready = False
        except Exception as e:                  # v3.32: поток не умирает молча
            m.meta["error"] = "обучение остановилось: %s: %s" % (
                type(e).__name__, e)
            try:                                # v3.103: причина — в журнал
                import traceback
                store_log("ОБУЧЕНИЕ LLM", "%s | %s" % (m.meta["error"],
                          traceback.format_exc().strip().splitlines()[-3:]))
            except Exception:
                pass
            try:                                # пройденное не теряем
                m.meta["plan_keys_done"] = sorted(done_keys)
                m.save(_state_path())
            except Exception:
                pass
        finally:
            m.training = False

    # ===================== v3.38: ДООБУЧЕНИЕ БЕЗ ПЕРЕОБУЧЕНИЯ ===================
    import hashlib

    def _keyed_corpus():
        """[(ключ, текст)]: статьи базы (kb:id) + материалы обоих мозгов
        (x:хэш). Ключ+подпись решают, «новое» это или уже усвоено."""
        out = []
        try:
            import kb
            import textutils as _tu
            for a in kb.list_articles(limit=1000000, include_devices=True) or []:
                art = kb.article(a.get("id")) if a.get("id") else None
                if art and art.get("text"):
                    if _tu.question_only(str(art["text"])[:1500]):
                        continue          # v3.144: вопрос — не материал для LLM
                    out.append(("kb:" + str(a["id"]),
                                _article_text(art.get("title"),
                                              art["text"])))
        except Exception:
            pass
        try:
            import llmlink
            for t in llmlink.corpus_extra():
                out.append(("x:" + hashlib.sha1(
                    t.encode("utf-8", "ignore")).hexdigest()[:16], t))
        except Exception:
            pass
        return out

    def _sig(text):
        return "%d:%s" % (len(text), hashlib.sha1(
            text.encode("utf-8", "ignore")).hexdigest()[:10])

    def _trained_state():
        import store
        return store.load_json("llmtrained.json", None)

    def _mark_trained(pairs):
        import store
        st = {k: _sig(t) for k, t in pairs}
        st["_v"] = "375"
        store.save_json("llmtrained.json", st)

    def new_material():
        """Что появилось с последнего обучения: [(ключ, текст)] и состояние.
        Нет файла состояния (модель обучена старой версией) — считаем все
        имеющиеся материалы уже усвоенными (None)."""
        pairs = _keyed_corpus()
        st = _trained_state()
        if st is not None and st.get("_v") != "375":
            # v3.75: прежняя отметка «усвоено» ставилась ВСЕМ 3000 статьям,
            # хотя в корпус входили лишь те, что помещались в бюджет токенов.
            # Пересчитываем: усвоенные — только вошедшие в бюджет (с начала).
            try:
                budget = int(model().c["llm_corpus_tokens"]) * 6
            except Exception:
                budget = 720000
            tot, ok_ = 0, []
            for k_, t_ in pairs:
                if k_.startswith("kb:"):
                    if tot > budget:
                        continue
                    tot += len(t_)
                ok_.append((k_, t_))
            _mark_trained(ok_)
            st = _trained_state()
        if st is None:
            _mark_trained(pairs)
            return [], pairs
        return [(k, t) for k, t in pairs if st.get(k) != _sig(t)], pairs

    def _train_incremental():
        m = model()
        with _LOCK:
            if getattr(m, "_code_course", False) and threading.current_thread() is not _THREAD[0]:
                return
            m.training = True
            m._stop = False
        try:
            new, allp = new_material()
            if not new:
                return
            c = m.c
            # v3.102: дообучение ведёт ПЛАТА — сначала материалы, созвучные
            # её мыслям и частоте; за раз — столько, сколько успевает потолок
            # шагов (остальное останется «новым» до следующего раза)
            try:
                bsc, led = _board_rank([t for _k, t in new])
                if led:
                    order_ = sorted(range(len(new)), key=lambda i_: -bsc[i_])
                    new = [new[i_] for i_ in order_]
                cap_ = int(c.get("llm_inc_max_steps") or 0)
                if cap_ > 0:
                    budget_ = max(1, cap_ * int(c["llm_batch"]) *
                                  int(c["llm_ctx"]) // 2) * 6    # ~6 знаков на токен
                    keep_, tot_ = [], 0
                    for k_, t_ in new:
                        if keep_ and tot_ + len(t_) > budget_:
                            break
                        keep_.append((k_, t_))
                        tot_ += len(t_)
                    new = keep_
                m.meta["inc_board_led"] = bool(led)
            except Exception:
                pass
            texts_new = [t for _k, t in new]
            added = m.tok.extend(texts_new, c["llm_vocab"])
            if added:
                m.grow_vocab(m.tok.size)
                m._bi = None
            ids_new = []
            bounds = []                      # (начало, ключ) каждого нового материала
            for (k_, _t), t in zip(new, texts_new):
                bounds.append((len(ids_new), k_))
                ids_new.extend(m.tok.encode(t, add_bos=True, add_eos=True))
            T, B = c["llm_ctx"], c["llm_batch"]
            if not ids_new:
                return
            base_n = list(ids_new)
            while len(ids_new) < T * 6:     # мало нового — повторим его
                ids_new.extend(base_n)
            # повтор старого (replay) — чтобы не забыть: столько же токенов
            rs = np.random.RandomState(int(time.time()) % 10000)
            old = [t for k, t in allp if k not in {k2 for k2, _ in new}]
            rs.shuffle(old)
            ids_old = []
            for t in old:
                ids_old.extend(m.tok.encode(t, add_bos=True, add_eos=True))
                if len(ids_old) >= len(ids_new):
                    break
            data_new = np.array(ids_new, dtype=np.int64)
            data_old = np.array(ids_old if len(ids_old) > T + 2 else [0] * 0,
                                dtype=np.int64)
            ep = max(1, int(c["llm_epochs"]) // 3)
            # v3.65: окна новых текстов шагают по T//2 (раньше T//4 — каждый
            # токен учился 4 раза), старое для повтора — окна встык (T)
            starts_n = [("n", s_) for s_ in range(0, len(data_new) - T - 1,
                                                  max(1, T // 2))]
            starts_o = [("o", s_) for s_ in range(0, max(0, len(data_old) - T - 1),
                                                  max(1, T))]
            B = max(1, min(B, len(starts_n) + len(starts_o)))
            per_ep = max(1, (len(starts_n) + len(starts_o)) // B)
            # v3.71: материала мало -> раньше выходило ~3 шага (1 батч × ep) и
            # обучение «останавливалось». Минимум MIN_INC_STEPS шагов.
            ep = max(ep, -(-MIN_INC_STEPS // per_ep))
            total = max(1, per_ep * ep)
            cap = int(c.get("llm_inc_max_steps") or 0)
            if cap > 0:
                total = min(total, cap)
            m.meta.update({"inc_total": total, "inc_done": 0,
                           "inc_new_docs": len(new), "inc_added_tokens": added,
                           "inc_active": True})
            done = 0
            budget = c["llm_cpu"]
            t_start = time.time()
            max_s = float(c.get("llm_inc_max_min") or 0) * 60.0
            used_n = set()                  # окна новых материалов, что реально учились
            out_of_time = False
            # v3.100: окна всех эпох по порядку; шаг берёт столько окон,
            # сколько успевает за llm_step_s (потолок — прежний: total × B окон)
            order = []
            for _e in range(ep):
                st_ = starts_n + starts_o
                rs.shuffle(st_)
                order.extend(st_[:len(st_) - len(st_) % B])
            total_seq = max(1, min(len(order), total * B))
            pace = _Pace(B, T)
            if pace.off():
                pace.B = B
            pos = 0
            m.meta["inc_total"] = int(-(-total_seq // max(1, pace.B)))
            while pos < total_seq:
                if m._stop:
                    return
                if max_s and time.time() - t_start > max_s:
                    out_of_time = True      # лимит времени: остаток — в следующий раз
                    break
                _yield_to_answers()
                t0 = time.time()
                Bk = max(1, min(pace.B, total_seq - pos))
                Tk = pace.T
                off = int(rs.randint(0, T - Tk + 1)) if Tk < T else 0
                xs, ys = [], []
                for kind, s_ in order[pos:pos + Bk]:
                    if kind == "n":
                        used_n.add(s_)
                    d = data_new if kind == "n" else data_old
                    xs.append(d[s_ + off:s_ + off + Tk])
                    ys.append(d[s_ + off + 1:s_ + off + Tk + 1])
                lr = c["llm_lr"] * 0.8 * (1.0 - 0.7 * pos / total_seq)   # v3.85: было 0.4
                if not pace.off():
                    lr *= pace.lr_k()
                try:
                    m.loss = m.train_step(np.stack(xs), np.stack(ys), lr)
                except _TrainingHandoff:
                    return
                m.steps += 1
                t0 += _code_weave(m, lr)      # v3.176: второй поток — код (время шага кода не в замер)
                done += 1
                pos += Bk
                dt = time.time() - t0
                if not pace.off():
                    pace.update(dt, getattr(m, "t_opt", 0.0), Bk, Tk)
                else:
                    pace.B, pace.T, pace.last = B, T, dt
                pace.meta(m)
                m.meta["inc_done"] = done
                m.meta["inc_total"] = done + int(-(-(total_seq - pos) //
                                                   max(1, pace.B)))
                m.meta["tok_per_s"] = round(Bk * Tk / max(1e-6, dt), 1)
                if budget < 1.0:
                    time.sleep(dt * (1.0 / budget - 1.0))
                else:
                    time.sleep(0.01)
            m.meta["total_steps"] = m.steps
            m.meta["corpus_docs"] = len(allp)
            m.meta["last_inc"] = time.time()
            # усвоенными отмечаем только материалы, чьи окна реально учились;
            # остальные останутся «новыми» и дообучатся в следующий запуск
            import bisect
            first = [b_[0] for b_ in bounds]
            got = set()
            for s_ in used_n:
                got.add(bounds[max(0, bisect.bisect_right(first, s_) - 1)][1])
            new_keys = {k_ for k_, _t in new}
            partial = [(k_, t_) for k_, t_ in allp
                       if k_ not in new_keys or k_ in got]
            m.meta["inc_left"] = len(new_keys - got)
            m.meta["inc_out_of_time"] = bool(out_of_time)
            _mark_trained(partial)
            try:
                m.save(_state_path())
            except Exception:
                pass
        except MemoryError:
            m.meta["error"] = "не хватило памяти при дообучении"
        except Exception as e:
            m.meta["error"] = "дообучение: %s: %s" % (type(e).__name__, e)
        finally:
            m.meta["inc_active"] = False
            m.training = False

    def learn_new(force=False):
        """Дообучить на новых материалах (без переобучения с нуля). Запуск
        в фоне; возвращает: 'started' | 'busy' | 'idle' | 'cold'."""
        if _deleted_flag():
            return "cold"
        m = model()
        if m.training or (_THREAD[0] and _THREAD[0].is_alive()):
            return "busy"
        if not (m.ready and m.steps > 0):
            return "cold"
        if m.steps < int(m.meta.get("total_steps") or 0):
            return "busy"                   # основное обучение не закончено
        new, _all = new_material()
        if not new:
            return "idle"
        with _LOCK:
            if m.training or (_THREAD[0] and _THREAD[0].is_alive()):
                return "busy"
            m.training = True
            th = threading.Thread(target=_train_incremental, daemon=True,
                                  name="tinyllm-inc")
            _THREAD[0] = th
            th.start()
        return "started"

    def ensure_training():
        if _deleted_flag():               # v3.116: удалили «без обучения» — ждём кнопку
            return
        m = model()
        if m.training or (_THREAD[0] and _THREAD[0].is_alive()):
            return
        try:
            import kb
            n_art = len(kb.list_articles(limit=5000, include_devices=True) or [])
        except Exception:
            n_art = 0
        if m.ready and m.steps > 0:
            # v3.38: новые статьи/материалы — дообучение, а не переобучение
            if m.steps >= int(m.meta.get("total_steps") or 0):
                try:
                    learn_new()
                except Exception:
                    pass
                return
            # обучение прервали (перезапуск, обновление) — ПРОДОЛЖАЕМ с места
            # последнего сохранения, а не начинаем заново (идём ниже)
        elif m.ready and m.steps > 0 and \
                n_art - int(m.meta.get("kb_articles") or 0) < 20:
            return
        with _LOCK:
            if m.training or (_THREAD[0] and _THREAD[0].is_alive()):
                return
            m.training = True
            th = threading.Thread(target=_train_all, daemon=True, name="tinyllm")
            _THREAD[0] = th
            th.start()

    def _deleted_path():
        import store
        return store.p("llm_deleted.flag")

    def _deleted_flag():
        """v3.116: нейросеть удалена и пользователь НЕ просил учить новую —
        фоновое обучение не стартует само, пока не нажмут «Переобучить»."""
        try:
            return os.path.isfile(_deleted_path())
        except Exception:
            return False

    def _set_deleted_flag(on):
        try:
            fn = _deleted_path()
            if on:
                with open(fn, "w", encoding="utf-8") as f:
                    f.write(str(time.time()))
            elif os.path.isfile(fn):
                os.remove(fn)
        except Exception:
            pass

    def _adam_wipe():
        try:
            import store
            d = store.p("llm_adam")
            if os.path.isdir(d):
                for fn in os.listdir(d):
                    try:
                        os.remove(os.path.join(d, fn))
                    except Exception:
                        pass
        except Exception:
            pass

    def _stop_training(wait_s=180.0):
        """Остановить обучение и ДОЖДАТЬСЯ, пока поток выйдет. Раньше ждали
        30 с: шаг большой модели длиннее, старый поток продолжал учить ту
        же модель параллельно с новым — «переобучение не работает»."""
        m = _M
        if m is not None:
            m._stop = True
            m._dead = True                    # v3.116: её поток уже ничего не сохранит
        th = _THREAD[0]
        t0 = time.time()
        while th is not None and th.is_alive() and time.time() - t0 < wait_s:
            th.join(timeout=1.0)
        return not (th is not None and th.is_alive())

    def delete_model(restart=False):
        """v3.116: УДАЛИТЬ НЕЙРОСЕТЬ: остановить обучение, стереть веса,
        словарь модели, состояние Adam (файлы подкачки) и отметки
        «выучено»; модель создаётся заново пустой. restart — сразу начать
        обучение с нуля (иначе начнётся само, фоном, по расписанию).
        База знаний, мозг (сеть материалов), память и настройки не трогаются."""
        global _M
        old = _M
        stopped = _stop_training()
        if old is not None and stopped:       # отпустить файлы Adam (Windows их держит)
            try:
                old.adam = None
                import gc
                gc.collect()
            except Exception:
                pass
        removed = []
        try:
            import store
            base = _state_path()
            for fn in (base + ".npz", base + ".json", base + ".npz.tmp",
                       base + ".json.tmp", store.p("llmtrained.json"),
                       store.p("llmread.json")):
                if os.path.isfile(fn):
                    try:
                        os.remove(fn)
                        removed.append(os.path.basename(fn))
                    except Exception:
                        pass
        except Exception:
            pass
        _adam_wipe()
        with _LOCK:
            _M = None
        _BT_CACHE["ids"], _BT_CACHE["ts"] = [], 0.0
        try:
            import store
            store.log("ОБУЧЕНИЕ LLM", "нейросеть удалена (%s)%s" % (
                ", ".join(removed) or "файлов не было",
                "" if stopped else "; старый поток обучения ещё не вышел — "
                "он остановится после текущего шага и ничего не запишет"))
        except Exception:
            pass
        _set_deleted_flag(not restart)
        if restart:
            m = model()
            m.ready = False
            th = threading.Thread(target=_train_all, daemon=True,
                                  name="tinyllm")
            _THREAD[0] = th
            th.start()
        return {"ok": True, "removed": removed, "stopped": stopped,
                "restart": bool(restart)}

    def retrain_now():
        """Переобучить с нуля = удалить нейросеть и сразу начать обучение."""
        delete_model(restart=True)
        return status()

    def status():
        m = model()
        c = m.c
        est = estimate(c)
        total = int(m.meta.get("total_steps") or 0)
        return {"ready": bool(m.ready and m.steps > 0),
                "training": bool(m.training), "steps": m.steps,
                "total_steps": total,
                "deleted": _deleted_flag(),                     # v3.116
                "tok_ver": int(getattr(m.tok, "ver", 1)),       # v3.117
                "speech_words": len(getattr(m.tok, "words", []) or []),
                "net_share": (round(llm2.net_share(m.loss, _NG["ng"]), 2)
                              if _NG.get("m") is m and _NG.get("ng") else None),
                "net_nll": (round(getattr(_NG["ng"], "net_nll", 0) or 0, 2)
                            if _NG.get("m") is m and _NG.get("ng") else None),
                "pct": int(100 * m.steps / total) if total else 0,
                "inc_active": bool(m.meta.get("inc_active")),   # v3.62
                "inc_done": int(m.meta.get("inc_done") or 0),
                "inc_total": int(m.meta.get("inc_total") or 0),
                "inc_new_docs": int(m.meta.get("inc_new_docs") or 0),
                "loss": round(m.loss, 3) if isinstance(m.loss, float) else None,
                "vocab": m.tok.size, "params": m.param_count() or est["params"],
                "ctx": c["llm_ctx"], "d_model": c["llm_d"],
                "heads": c["llm_heads"], "layers": c["llm_layers"],
                "ff": 4 * c["llm_d"], "backend": "numpy " + np.__version__,
                "mem_mb": est["mem_mb"],
                "flops_per_token": est["flops_per_token"],
                "tok_per_s": m.meta.get("tok_per_s"),
                "step_s": m.meta.get("step_s"),               # v3.100
                "step_target": m.meta.get("step_target"),
                "step_batch": m.meta.get("step_batch"),
                "step_ctx": m.meta.get("step_ctx"),
                "step_opt_s": m.meta.get("step_opt_s"),
                "step_note": m.meta.get("step_note") or "",
                "plan_step": m.meta.get("plan_step"),          # v3.101
                "plan_cap": store_get("llm_plan_steps"),
                "plan_note": m.meta.get("plan_note") or "",
                "board_led": m.meta.get("board_led") or {},    # v3.102
                "board_steps": int(m.meta.get("board_steps") or 0),
                "eta_s": m.meta.get("eta_s"), "cpu_share": c["llm_cpu"],
                "corpus_tokens": m.meta.get("corpus_tokens", 0),
                "corpus_docs": m.meta.get("corpus_docs", 0),
                "corpus_skip": m.meta.get("corpus_skip") or {},     # v3.144
                "kb_articles": m.meta.get("kb_articles", 0),
                "error": m.meta.get("error", ""),
                "inc": {"active": bool(m.meta.get("inc_active")),
                        "done": m.meta.get("inc_done", 0),
                        "total": m.meta.get("inc_total", 0),
                        "docs": m.meta.get("inc_new_docs", 0),
                        "last": m.meta.get("last_inc")},
                "numpy": True,
                "ram_mb": est["ram_mb"], "fits": est["fits"],
                "ram_free_mb": est["ram_free_mb"], "need_mb": est["need_mb"],
                "act_mb": est["act_mb"], "verdict": est["verdict"],
                "note": est["note"], "train_s_est": est["train_s_est"],
                "force": c["llm_force"],
                "gen": {"temp": c["llm_temp"], "top_k": c["llm_topk"],
                        "top_p": c["llm_topp"], "rep": c["llm_rep"],
                        "max_new": c["llm_max_new"]}}

    _NG = {"m": None, "ng": None, "ts": 0.0, "busy": False}

    def _ngram_build(m):
        try:
            c = m.c
            texts, _n = _collect_corpus(c["llm_corpus_tokens"])
            ids = []
            for t in texts:
                ids.extend(m.tok.encode(t, add_bos=True, add_eos=True))
                if len(ids) >= c["llm_corpus_tokens"]:
                    break
            ng = llm2.NGram(ids, m.p["E"].shape[0] if m.p else m.tok.size)
            try:                      # замер сети на 8 окнах корпуса
                T_ = m.c["llm_ctx"]
                L_ = []
                step_ = max(T_ + 1, (len(ids) - T_ - 1) // 8)
                for s0 in range(0, max(0, len(ids) - T_ - 1), step_)[:8]:
                    X_ = np.array([ids[s0:s0 + T_]], dtype=np.int64)
                    Y_ = np.array(ids[s0 + 1:s0 + T_ + 1], dtype=np.int64)
                    lg_, _c = m.forward(X_)
                    lg_ = lg_[0].astype(np.float64)
                    lg_ -= lg_.max(axis=1, keepdims=True)
                    lp_ = lg_ - np.log(np.exp(lg_).sum(axis=1, keepdims=True))
                    w_ = (Y_ != UNK)
                    if w_.any():
                        L_.append(float(-lp_[np.arange(T_), Y_][w_].mean()))
                if L_:
                    ng.net_nll = sum(L_) / len(L_)
            except Exception:
                pass
            _NG.update({"m": m, "ng": ng, "ts": time.time(),
                        "vocab": m.tok.size})
        except Exception as e:
            store_log("ОБУЧЕНИЕ LLM", "n-граммы: %s: %s" % (type(e).__name__, e))
        finally:
            _NG["busy"] = False

    def _ngram_get(m):
        """v3.117: n-граммная статистика корпуса (подстраховка речи
        недоученной сети). Первый раз строится сразу, потом обновляется
        в фоне раз в 30 мин или при смене словаря."""
        ng = _NG["ng"]
        fresh = (_NG["m"] is m and _NG.get("vocab") == m.tok.size and
                 time.time() - _NG["ts"] < 1800)
        if ng is not None and _NG["m"] is m and \
                ng.V == (m.p["E"].shape[0] if m.p else ng.V):
            if not fresh and not _NG["busy"]:
                _NG["busy"] = True
                threading.Thread(target=_ngram_build, args=(m,),
                                 daemon=True).start()
            return ng
        if not _NG["busy"]:
            _NG["busy"] = True
            _ngram_build(m)
        ng = _NG["ng"]
        return ng if (ng is not None and _NG["m"] is m) else None

    def _bigrams(m):
        """Пары соседних токенов корпуса (то, что модель реально видела).
        Строятся лениво по текущей базе и словарю модели, кэш на модели."""
        bi = getattr(m, "_bi", None)
        if bi is not None and getattr(m, "_bi_vocab", -1) == m.tok.size:
            return bi
        try:
            c = m.c
            texts, _n = _collect_corpus(c["llm_corpus_tokens"])
            ids = []
            for t in texts:
                ids.extend(m.tok.encode(t, add_bos=True, add_eos=True))
                if len(ids) >= c["llm_corpus_tokens"]:
                    break
            bi = set(zip(ids[:-1], ids[1:]))
        except Exception:
            bi = set()
        m._bi, m._bi_vocab = bi, m.tok.size
        return bi

    def coherence(text):
        """Доля соседних пар токенов текста, которые встречались в корпусе
        (0..1): «слова из словаря в случайном порядке» дают ~0."""
        m = model()
        bi = _bigrams(m)
        ids = [i for i in m.tok.encode(text) if i not in (PAD, BOS, EOS)]
        pairs = list(zip(ids[:-1], ids[1:]))
        if not pairs or not bi:
            return 0.0
        return sum(1 for p in pairs if p in bi) / float(len(pairs))

    def quality(text):
        """Качество текста модели 0..1 = (доля БУКВ в словах, известных
        модели целыми словами, с поправкой на повторы и «кодовые» символы) *
        (0,3 + 0,7 * связность). Связность — доля пар соседних токенов,
        виденных в корпусе. Выдумка по буквам и «салат из слов» дают ~0."""
        m = model()
        if m.tok.size < 10:
            return 0.0
        if getattr(m.tok, "ver", 1) >= 2:
            ws_ = set(m.tok.words)
            q = _text_quality(text, lambda w: w in ws_ or ("▁" + w) in m.tok.vocab)
        else:
            q = _text_quality(text, lambda w: ("▁" + w) in m.tok.vocab)
        if q <= 0:
            return 0.0
        return round(q * (0.3 + 0.7 * coherence(text)), 2)

    # ===== v3.148: ПЛАВАЮЩИЕ ПАРАМЕТРЫ СВОЕЙ LLM (только в ответах) =====
    # Заявка: «контекст, размер сети и все параметры плавают от частот плат
    # и дыхания мозга, не завися от обучения; чем длиннее вопрос — тем
    # сильнее». Восемь множителей из тензора модуляции («Своя LLM:
    # параметры»), размах растёт с длиной вопроса (0,4 при 1 слове → 1 при
    # 40+ словах). ЧЕСТНО о размере: веса обучены под полный размер, поэтому
    # «размер» плавает только ВНИЗ от обученного — окно контекста (не меньше
    # половины), число работающих голов внимания (не меньше половины) и
    # слоёв (выключается максимум верхний). Больше обученного сеть стать не
    # может: таких весов нет. Учёба идёт в своём потоке на полном размере
    # и от этого не зависит (поток ответа держит параметры в _FP).
    import threading as _thr
    _FP = _thr.local()

    def float_params(m, c, text=""):
        """v3.149: количественные параметры своей LLM ПОВЫШАЮТСЯ и зависят
        от частоты плат, воли мозга и дыхания мозга (tensormod «Своя LLM:
        параметры»: x = дыхание, фаза, z-частота, z·s, разброс, воля зоны).
        Решение тензора (мозг платы + облако) задаёт, насколько раскрыть
        сеть в этом ответе. Архитектура (ctx/heads/layers) — только вниз
        от обученного (весов больше нет); генерация (temp, top-k, …, длина)
        может и повышаться выше базы при сильном сигнале."""
        try:
            import tensormod
            words = len(WORD_RE.findall(str(text or "").lower()))
            qmul = 0.5 + 0.7 * min(1.0, words / 30.0)   # сильнее от длины вопроса
            v = [float(x) for x in tensormod.vec("Своя LLM: параметры", 8, amp_mul=qmul)]
        except Exception:
            return None
        L, H, T = int(m.c["llm_layers"]), int(m.c["llm_heads"]), int(m.c["llm_ctx"])
        # v[i] ≈ 0.5…1.5: при сильной частоте/воле/вдохе → ближе к 1.5
        # (полная сеть + повышенные temp/длина); при покое → ~1; при слабом → ниже.
        def _arch(base, mul, floor_ratio=0.5):
            """Архитектура: повышается к полному размеру при сильном сигнале."""
            sc = max(floor_ratio, min(1.0, float(mul)))
            return int(max(1, min(base, round(base * sc))))
        fp = {
            "ctx": int(max(min(T, 16), _arch(T, v[0], 0.5))),
            "heads": _arch(H, v[1], 0.5),
            "layers": int(max(1, min(L, _arch(L, v[2], max(1.0 / max(L, 1), 0.5))))),
            "q_words": words, "q_mul": round(qmul, 2),
            "v": [round(x, 3) for x in v]}
        # генерация: может повышаться выше базы (сильный сигнал → живее/длиннее)
        c["llm_temp"] = round(max(0.15, min(2.0, float(c["llm_temp"]) * max(0.5, min(1.8, v[3])))), 3)
        c["llm_topk"] = int(max(5, min(250, round(float(c["llm_topk"]) * max(0.5, min(1.8, v[4]))))))
        c["llm_topp"] = round(max(0.4, min(0.995, float(c["llm_topp"]) * max(0.6, min(1.3, v[5])))), 3)
        c["llm_rep"] = round(max(1.0, min(2.2, 1.0 + (float(c["llm_rep"]) - 1.0) * max(0.5, min(1.6, v[6])))), 3)
        c["llm_max_new"] = int(max(8, round(float(c["llm_max_new"]) * max(0.5, min(1.8, v[7])))))
        try:
            import tensormod
            tensormod.LAST["Своя LLM: параметры"] = dict(
                fp, of={"ctx": T, "heads": H, "layers": L},
                temp=c["llm_temp"], top_k=c["llm_topk"], top_p=c["llm_topp"],
                rep=c["llm_rep"], max_new=c["llm_max_new"])
        except Exception:
            pass
        return fp

    def sample(topic="", **over):
        """Один образец текста модели (без побочных эффектов): (текст, info)
        либо ("", {"llm": "cold"}), если модель ещё не училась."""
        m = model()
        if not m.ready or m.tok.size < 10 or not m.steps:
            ensure_training()
            return "", {"llm": "cold"}
        c = dict(cfg())
        for k, v in over.items():
            c["llm_" + k] = v
        try:                              # v3.132: общее дыхание с платами
            import breath as _brt
            c["llm_temp"] = _brt.mod("Своя LLM", float(c["llm_temp"]), 0.15)
        except Exception:
            pass
        _FP.p = float_params(m, c, topic)   # v3.149: плавающие параметры (повышаются от частоты/воли/дыхания)
        try:
            return _sample_body(m, c, topic, over)
        finally:
            _FP.p = None

    def _sample_body(m, c, topic, over):
        v2 = getattr(m.tok, "ver", 1) >= 2
        lx = None
        try:
            import store as _st_
            if not v2 and bool(_st_.settings().get("llm_words", True)):
                import lexicon
                lx = lexicon.get(m)       # v3.45: слова по словарю, не по буквам
        except Exception:
            lx = None
        bias, pre = None, ""
        try:                              # v3.48: сигнал генератора -> смысл
            import genmod
            bias = genmod.word_bias(m)
            pre = genmod.prefix_for_prompt()
        except Exception:
            bias, pre = None, ""
        if v2:
            # v3.117: речь словами + проверка; вырожденное — ещё попытка,
            # три неудачи — молчание (лучше ничего, чем «англ н н»)
            t, tries = "", 0
            seed0 = int.from_bytes(os.urandom(3), "little")
            # слово темы, с которого начнётся мысль (самое длинное знакомое
            # содержательное слово вопроса/темы; в разных попытках — разные)
            ws_ = set(m.tok.words)
            cand = sorted({w for w in WORD_RE.findall(llm2.normalize(topic).lower())
                           if len(w) >= 3 and w in ws_ and w not in llm2.FUNC_W},
                          key=lambda w: -len(w))
            for tries in range(1, 4):
                fw_ = cand[(tries - 1) % len(cand)] if cand and tries < 3 else ""
                raw = m.generate(pre + topic, max_new=c["llm_max_new"],
                                 temperature=c["llm_temp"],
                                 top_k=c["llm_topk"], top_p=c["llm_topp"],
                                 rep=c["llm_rep"], bias=bias,
                                 seed=seed0 + tries, first_word=fw_)
                t = llm2.finish_text(raw)
                bad = llm2.looks_degenerate(t)
                if not bad:
                    try:
                        import llmlink
                        bad = bool(llmlink.degenerate(t))
                    except Exception:
                        pass
                if not bad:
                    break
                t = ""
            info = {"llm": "ok", "steps": m.steps, "tries": tries,
                    "total": int(m.meta.get("total_steps") or 0),
                    "loss": round(m.loss, 3) if isinstance(m.loss, float) else None,
                    "vocab": m.tok.size, "d_model": m.c["llm_d"],
                    "net_share": round(llm2.net_share(m.loss, _NG.get("ng")), 2)}
            if m.steps < int(m.meta.get("total_steps") or 0):
                info["warming"] = True
            return t, info
        text = m.generate(pre + topic, max_new=c["llm_max_new"],
                          temperature=c["llm_temp"], top_k=c["llm_topk"],
                          top_p=c["llm_topp"], rep=c["llm_rep"], lex=lx,
                          bias=bias)
        t = clean_text(text.strip())
        dropped = 0
        if lx is not None and t:
            try:
                import lexicon
                import store as _st2
                if bool(_st2.settings().get("llm_words_strict", True)):
                    # v3.66: слова вне словаря — выдумки: вычищаем; осталось
                    # меньше 3 настоящих слов — молчим (лучше ничего, чем бред)
                    t, dropped, kept = lexicon.purge(t, lx)
                    if kept < 3 or dropped > kept:
                        t = ""
            except Exception:
                pass
        if t:
            t = t[0].upper() + t[1:]
            if t[-1] not in ".!?…":
                t = t.rstrip(",;:-") + "."
        info = {"llm": "ok", "steps": m.steps, "invented_dropped": dropped,
                "total": int(m.meta.get("total_steps") or 0),
                "loss": round(m.loss, 3) if isinstance(m.loss, float) else None,
                "vocab": m.tok.size, "d_model": m.c["llm_d"]}
        if m.steps < int(m.meta.get("total_steps") or 0):
            info["warming"] = True
        return t, info

    def compose(question, facts, max_new=70, min_frac=0.0):
        """v3.34: ответ своей LLM ПО ФАКТАМ. (текст|"", info)."""
        m = model()
        if not m.ready or m.tok.size < 10 or not m.steps:
            ensure_training()
            return "", {"llm": "cold"}
        _ANSWERING[0] += 1                 # v3.111: обучение уступает ответу
        _ANSWERING[1] = time.time()
        _FP.p = float_params(m, dict(cfg()), question)   # v3.149
        try:
            t, cov = m.generate_grounded(question, facts, max_new=max_new,
                                         min_frac=min_frac)
        except Exception as e:
            return "", {"llm": "err", "error": str(e)[:80]}
        finally:
            _FP.p = None
            _ANSWERING[0] = max(0, _ANSWERING[0] - 1)
            _ANSWERING[1] = time.time()
        t = (t or "").strip()
        if t:
            t = t[0].upper() + t[1:]
            if t[-1] not in ".!?…":
                t = t.rstrip(",;:-") + "."
        return t, {"llm": "ok", "grounded": round(cov, 2), "steps": m.steps}

    def write_code(task, tries=4, max_new=180, temperature=0.5):
        """v3.41: своя LLM пишет код по просьбе. Возврат: список кандидатов
        (одна законченная функция каждый), от более вероятных к менее."""
        import codecorpus
        m = model()
        if not m.ready or not m.steps or getattr(m, "_code_course", False):
            return []
        if not any(k.startswith("\u00a2") for k in m.tok.vocab):
            return []                           # на коде ещё не училась
        import brainwill
        will = brainwill.code(tries, max_new, temperature)
        tries, max_new, temperature = will['tries'], will['max_new'], will['temperature']
        m.meta['code_will'] = will
        en = codecorpus.ru_en(task)
        prompts = ["# task: %s\n" % (en or task)]
        if en and en != str(task).lower():
            prompts.append("# task: %s\n" % " ".join(str(task).split())[:120])
        out, seen = [], set()
        write_code.last_raw = ""     # v3.175: самый длинный набросок, даже незаконченный
        for i in range(max(1, int(tries))):
            p = prompts[i % len(prompts)]
            try:
                previous_stable = getattr(_FP, "stable_code", False)
                _FP.stable_code = True
                raw = m.generate_code(
                    p, max_new=max_new,
                    temperature=temperature * (0.85 + 0.1 * (i % 4)),
                    top_k=15 + 5 * (i % 3), seed=1000 + i)
            except Exception:
                continue
            finally:
                _FP.stable_code = previous_stable
            try:
                _r = str(raw or "").strip()
                if len(_r) > len(write_code.last_raw):
                    write_code.last_raw = _r[:4000]
            except Exception:
                pass
            code = codecorpus.trim(raw)
            if code and code not in seen:
                seen.add(code)
                out.append(code)
        return out

    def read_article(title, text, max_sents=40):
        """v3.37: как LLM «прочитала» статью: {ok, learned (0..1), best:[...],
        n}. learned — exp(средний лог-правдоподобие) по предложениям,
        отнесённый к случайному выбору по словарю (1/V)."""
        m = model()
        if not m.ready or not m.steps:
            return {"ok": False}
        sents = [x.strip() for x in re.split(r"(?<=[.!?])\s+",
                 str(text)) if 30 <= len(x.strip()) <= 300][:max_sents]
        if not sents:
            return {"ok": False}
        sc = m.sent_scores(sents)
        pairs = [(v, x) for v, x in zip(sc, sents) if v is not None]
        if not pairs:
            return {"ok": False}
        mean = sum(v for v, _ in pairs) / len(pairs)
        V = max(10, m.tok.size)
        gain = 1.0 - (-mean) / math.log(V)        # 0 = как случайно, 1 = идеально
        best = [x for _v, x in sorted(pairs, key=lambda p_: -p_[0])[:2]]
        return {"ok": True, "learned": round(max(0.0, min(1.0, gain)), 2),
                "best": best, "n": len(pairs), "loss": round(-mean, 2)}

    def fantasize(topic=""):
        """Зона Воображения: продолжить мысль по теме. (текст, info)."""
        t, info = sample(topic)
        ensure_training()
        if t:
            try:                              # v3.32/33: мысль LLM -> мозг/платы
                import llmlink
                r = llmlink.publish_thought(t, topic, source="воображение")
                info["published"] = bool(r.get("ok"))
            except Exception:
                pass
        return t, info
