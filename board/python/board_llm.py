# -*- coding: utf-8 -*-
"""board_llm.py — v3.237 / плата 2.112: ЗАПУСК СВОЕЙ LLM МОЗГА-12 ВНЕ ОБЛАКА.

Облако выгружает свою LLM (GPT: эмбеддинги, внимание, FFN, LayerNorm) в пакет:
    model.json   — размеры сети, словарь BPE, слияния, таблица тензоров;
    weights.f32  — все веса подряд, float32 little-endian.
Этот файл читает пакет и генерирует текст. Работает:
  * с numpy — быстро;
  * без numpy — на чистом Python (медленно: секунды на слово при d=128, 2 слоя;
    на плате UNO Q numpy в контейнере App Lab может не быть — тогда так).
Только вывод (генерация): учится LLM в облаке, плата получает готовые веса.
Запуск вручную:  python3 board_llm.py <папка_пакета> "твой текст"
"""
import json
import math
import os
import random
import re
import sys
from array import array

try:
    import numpy as _np
except Exception:          # на плате numpy может не быть
    _np = None

FORMAT = "neuro12-llm-1"
PAD, BOS, EOS, UNK = 0, 1, 2, 3
WORD_RE = re.compile(r"[а-яёa-z0-9]+|[^\sа-яёa-z0-9]", re.I)
_ALNUM_RE = re.compile(r"^[а-яёa-z0-9]+$")


def _bpe_word(w, ranks, cache):
    r = cache.get(w)
    if r is not None:
        return r
    sy = ["▁" + w[0]] + list(w[1:])
    while len(sy) > 1:
        best, bi = None, -1
        for i in range(len(sy) - 1):
            rk = ranks.get((sy[i], sy[i + 1]))
            if rk is not None and (best is None or rk < best):
                best, bi = rk, i
        if best is None:
            break
        sy = sy[:bi] + [sy[bi] + sy[bi + 1]] + sy[bi + 2:]
    if len(cache) > 50000:
        cache.clear()
    cache[w] = sy
    return sy


class BoardLLM(object):
    def __init__(self, folder):
        self.folder = folder
        with open(os.path.join(folder, "model.json"), "r", encoding="utf-8") as f:
            m = json.load(f)
        if m.get("format") != FORMAT:
            raise ValueError("не тот формат пакета: %s" % m.get("format"))
        self.meta = m
        c = m["c"]
        self.D, self.L, self.H, self.T = int(c["llm_d"]), int(c["llm_layers"]), int(c["llm_heads"]), int(c["llm_ctx"])
        self.Dh = self.D // self.H
        self.vocab = dict(m["vocab"])
        self.inv = {int(v): k for k, v in self.vocab.items()}
        self.ranks = {tuple(x): i for i, x in enumerate(m.get("merges") or [])}
        self._cache = {}
        raw = array("f")
        with open(os.path.join(folder, "weights.f32"), "rb") as f:
            raw.frombytes(f.read())
        if sys.byteorder != "little":
            raw.byteswap()
        self.np = _np is not None
        self.p = {}
        for name, shape, off, cnt in m["tensors"]:
            seg = raw[off:off + cnt]
            if self.np:
                self.p[name] = _np.frombuffer(seg.tobytes(), dtype=_np.float32).reshape(shape)
            elif len(shape) == 2 and name != "E" and name != "P":
                r_, c_ = shape                      # матрица (вход, выход): храним СТОЛБЦЫ — строки W^T
                self.p[name] = [array("f", seg[j::c_]) for j in range(c_)]
            elif len(shape) == 2:
                r_, c_ = shape                      # E и P: строки как есть
                self.p[name] = [seg[i * c_:(i + 1) * c_] for i in range(r_)]
            else:
                self.p[name] = seg
        self.V = len(self.p["E"])
        bad = [i for t, i in self.vocab.items() if t.startswith("<") or t.startswith("¢")]
        self._bad = set(int(i) for i in bad if int(i) not in (EOS,))
        del raw

    # ------------------------------ токены ---------------------------------
    def encode(self, text):
        ids = []
        for w in WORD_RE.findall(str(text).lower()):
            t = self.vocab.get("▁" + w)
            if t is not None:
                ids.append(int(t))
                continue
            if len(w) == 1 and not _ALNUM_RE.match(w):
                ids.append(UNK)
                continue
            for pc in _bpe_word(w, self.ranks, self._cache):
                t = self.vocab.get(pc)
                if t is not None:
                    ids.append(int(t))
                    continue
                for i, ch in enumerate(pc.lstrip("▁")):
                    sp = "▁" if (i == 0 and pc.startswith("▁")) else ""
                    ids.append(int(self.vocab.get(sp + ch, UNK)))
        return ids

    def decode(self, ids):
        out = ""
        for i in ids:
            t = self.inv.get(int(i), "")
            if not t or t.startswith("<") or t.startswith("¢"):
                continue
            out += ((" " if out else "") + t[1:]) if t.startswith("▁") else t
        return re.sub(r"\s+([.,!?;:])", r"\1", out)

    # ------------------------------ чистый Python ---------------------------
    @staticmethod
    def _ln(x, g, b):
        n = len(x)
        mu = sum(x) / n
        var = sum((v - mu) * (v - mu) for v in x) / n
        s = 1.0 / math.sqrt(var + 1e-5)
        return [(x[i] - mu) * s * g[i] + b[i] for i in range(n)]

    @staticmethod
    def _mv(x, WT, b):
        return [_dot(x, col) + b[j] for j, col in enumerate(WT)]

    def _step_py(self, tok, pos, kv):
        p, D, H, Dh = self.p, self.D, self.H, self.Dh
        h = [a + b for a, b in zip(p["E"][tok], p["P"][pos])]
        for l in range(self.L):
            a = self._ln(h, p["ln1g%d" % l], p["ln1b%d" % l])
            q = self._mv(a, p["Wq%d" % l], p["bq%d" % l])
            k = self._mv(a, p["Wk%d" % l], p["bk%d" % l])
            v = self._mv(a, p["Wv%d" % l], p["bv%d" % l])
            kv[l][0].append(k)
            kv[l][1].append(v)
            o = [0.0] * D
            sc = 1.0 / math.sqrt(Dh)
            for hh in range(H):
                s0, s1 = hh * Dh, (hh + 1) * Dh
                qh = q[s0:s1]
                ss = [_dot(qh, kk[s0:s1]) * sc for kk in kv[l][0]]
                mx = max(ss)
                ex = [math.exp(s - mx) for s in ss]
                z = sum(ex)
                for t_, e in enumerate(ex):
                    w = e / z
                    vv = kv[l][1][t_]
                    for i in range(s0, s1):
                        o[i] += w * vv[i]
            proj = self._mv(o, p["Wo%d" % l], p["bo%d" % l])
            h2 = [x + y for x, y in zip(h, proj)]
            a2 = self._ln(h2, p["ln2g%d" % l], p["ln2b%d" % l])
            zz = self._mv(a2, p["W1_%d" % l], p["b1_%d" % l])
            g = [0.5 * x * (1.0 + math.tanh(0.7978845608 * (x + 0.044715 * x * x * x))) for x in zz]
            ff = self._mv(g, p["W2_%d" % l], p["b2_%d" % l])
            h = [x + y for x, y in zip(h2, ff)]
        hf = self._ln(h, p["lnf_g"], p["lnf_b"])
        return [_dot(hf, e) for e in p["E"]]

    # ------------------------------ numpy ----------------------------------
    def _logits_np(self, ids):
        np, p = _np, self.p
        T = len(ids)
        H, D, Dh = self.H, self.D, self.Dh
        X = np.array(ids, dtype=np.int64)
        h = p["E"][X] + p["P"][:T]
        mask = np.triu(np.ones((T, T), dtype=bool), 1)

        def ln(x, g, b):
            mu = x.mean(-1, keepdims=True)
            var = x.var(-1, keepdims=True)
            return (x - mu) / np.sqrt(var + 1e-5) * g + b
        for l in range(self.L):
            a = ln(h, p["ln1g%d" % l], p["ln1b%d" % l])
            q = (a @ p["Wq%d" % l] + p["bq%d" % l]).reshape(T, H, Dh).transpose(1, 0, 2)
            k = (a @ p["Wk%d" % l] + p["bk%d" % l]).reshape(T, H, Dh).transpose(1, 0, 2)
            v = (a @ p["Wv%d" % l] + p["bv%d" % l]).reshape(T, H, Dh).transpose(1, 0, 2)
            s = q @ k.transpose(0, 2, 1) / math.sqrt(Dh)
            s = np.where(mask[None], -1e9, s)
            s = np.exp(s - s.max(-1, keepdims=True))
            s /= s.sum(-1, keepdims=True)
            o = (s @ v).transpose(1, 0, 2).reshape(T, D)
            h2 = h + o @ p["Wo%d" % l] + p["bo%d" % l]
            a2 = ln(h2, p["ln2g%d" % l], p["ln2b%d" % l])
            z = a2 @ p["W1_%d" % l] + p["b1_%d" % l]
            g = 0.5 * z * (1.0 + np.tanh(0.7978845608 * (z + 0.044715 * z ** 3)))
            h = h2 + g @ p["W2_%d" % l] + p["b2_%d" % l]
        hf = ln(h[-1:], p["lnf_g"], p["lnf_b"])
        return [float(x) for x in (hf @ p["E"].T)[0]]

    # ------------------------------ генерация -------------------------------
    def generate(self, prompt, max_new=24, temperature=0.8, top_k=30, rep=1.3, seed=None):
        rnd = random.Random(seed)
        ids = [BOS] + self.encode(prompt)[-(self.T - 1 - max_new if self.T > max_new + 8 else self.T // 2):]
        out = []
        kv = [([], []) for _ in range(self.L)]
        lg = None
        if not self.np:
            for pos, t in enumerate(ids):
                lg = self._step_py(t, pos, kv)
        for _ in range(max_new):
            if len(ids) >= self.T:
                break
            if self.np:
                lg = self._logits_np(ids)
            lg = list(lg)
            for b in self._bad:
                if b < len(lg):
                    lg[b] = -1e9
            for t in set(ids[-20:]):
                if t < len(lg) and t != EOS:
                    lg[t] -= math.log(rep) * 3
            temp = max(1e-3, float(temperature))
            top = sorted(range(len(lg)), key=lambda i: -lg[i])[:max(1, int(top_k))]
            mx = lg[top[0]]
            ws = [math.exp((lg[i] - mx) / temp) for i in top]
            r, acc, pick = rnd.random() * sum(ws), 0.0, top[0]
            for i, w in zip(top, ws):
                acc += w
                if acc >= r:
                    pick = i
                    break
            if pick == EOS:
                break
            ids.append(pick)
            out.append(pick)
            if not self.np:
                lg = self._step_py(pick, len(ids) - 1, kv)
        return self.decode(out)

    def info(self):
        return {"d": self.D, "layers": self.L, "heads": self.H, "ctx": self.T, "vocab": self.V,
                "steps": self.meta.get("steps"), "loss": self.meta.get("loss"), "sig": self.meta.get("sig"),
                "numpy": self.np}


def _dot(a, b):
    s = 0.0
    for x, y in zip(a, b):
        s += x * y
    return s


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("python3 board_llm.py <папка_пакета> \"текст\"")
        sys.exit(1)
    m = BoardLLM(sys.argv[1])
    print(m.info())
    print(m.generate(" ".join(sys.argv[2:])))
