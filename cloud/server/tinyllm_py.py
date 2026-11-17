# -*- coding: utf-8 -*-
# ===========================================================================
# tinyllm.py — МИНИ-LLM ОБЛАКА: «КАК УСТРОЕНЫ БОЛЬШИЕ ЯЗЫКОВЫЕ МОДЕЛИ»
# (v3.16, только stdlib, без numpy и без внешних сетей)
# ---------------------------------------------------------------------------
# ЗАЯВКА ВЛАДЕЛЬЦА: «посмотри как устроены LLM по документации и введи
# то же самое в код». Это — настоящая (крошечная) языковая модель,
# повторяющая архитектуру GPT-класса по шагам документации:
#
#   1) ТОКЕНИЗАЦИЯ. Текст -> последовательность токенов (слов). Словарь
#      строится по частотам корпуса; редкие слова схлопываются в <unk>
#      (как subword-токенизаторы схлопывают редкие куски слов).
#      Спецтокены: <pad>, <bos>, <eos>, <unk>.
#   2) ЭМБЕДДИНГИ + ПОЗИЦИИ. Каждый токен -> вектор (d_model=12),
#      прибавляется синусоидальное позиционное кодирование (как в
#      «Attention Is All You Need») — модель видит порядок слов.
#   3) САМО-ВНИМАНИЕ (causal multi-head self-attention). 2 головы по 6
#      измерений: Q=X·Wq, K=X·Wk, V=X·Wv; скоры QK^T/sqrt(d_head),
#      softmax по строкам, КАУСАЛЬНАЯ маска (токен видит только прошлое
#      — так же в декодере GPT), выход A·V конкатенируется головами и
#      смешивается Wo.
#   4) ОСТАТОЧНЫЕ СВЯЗИ + LAYER NORM. После внимания и после FFN:
#      X = LN(X + подслой) — стабилизирует обучение (Pre-LN схема).
#   5) FFN. Полносвязный блок d -> 24 -> d с GELU-активацией (tanh-
#      приближение, как в GPT).
#   6) ГОЛОВА ПРЕДСКАЗАНИЯ. Логиты = h · E^T (весовая связка с
#      эмбеддингами — weight tying, как в GPT-2) -> softmax ->
#      распределение следующего токена.
#   7) ОБУЧЕНИЕ. Next-token prediction: кросс-энтропия по окнам
#      контекста, свои градиенты (ручной backprop), SGD с моментом,
#      клиппинг градиента, косинусное затухание шага обучения.
#      Корпус: встроенные тексты + статьи базы знаний владельца —
#      модель ДОРАСТАЕТ на знаниях облака. Обучение идёт в фоне
#      (порциями), состояние сохраняется в data/tinyllm_state.json.
#   8) СЭМПЛИРОВАНИЕ. temperature, top-k, top-p (nucleus), repetition
#      penalty — те же ручки, что у больших моделей; при T->0 жадный
#      выбор, при большой T — больше «фантазии».
#   9) КОНТЕКСТНОЕ ОКНО. Генерация видит последние CTX=8 токенов
#      (аналог контекстного окна LLM, честно маленький).
#
# ЧЕСТНОСТЬ: это НЕ заменитель больших моделей — это их уменьшенная
# копия для зоны Воображения. Она собирает фразы из ВЫУЧЕННОГО словаря
# облака и не претендует на факты: любые её тексты помечаются зоной
# как фантазии. Речь идёт только из выученных слов — незнакомое слово
# в словаре модели отсутствует, и модель не может его «придумать».
# ===========================================================================
import math
import os
import random
import re
import threading
import time

# ------------------------------ гиперпараметры ------------------------------
D = 12            # d_model — размерность вектора токена
NH = 2            # число голов внимания
DH = D // NH      # размер одной головы (6)
FF = 24           # скрытый слой FFN
CTX = 8           # длина контекстного окна (токенов)
VOCAB_MAX = 700   # размер словаря (со спецтокенами)
CORPUS_MAX = 2600 # токенов корпуса (скорость чистого Python)
EPOCHS = 3
STRIDE = 5        # шаг окна по корпусу (плотность обучения)
LR = 0.06         # стартовый шаг SGD (выше — расходится, ниже — вязнет)
MOMENTUM = 0.9
CLIP = 5.0
CHUNK_STEPS = 50  # шагов обучения за один заход фонового потока
CHUNK_BUDGET = 3.0  # сек на заход (не душим сервер)

PAD, BOS, EOS, UNK = 0, 1, 2, 3
WORD_RE = re.compile(r"[а-яёa-z0-9]+", re.I)

_STATE_PATH = "tinyllm_state.json"

# --------------------------- встроенный корпус ------------------------------
# Небольшие русские тексты «о себе» — стартовые знания модели; статьи KB
# владельца добавляются к ним автоматически.
SEED_TEXTS = (
    "Мозг двенадцать это небольшая нейросеть на плате. Нейроны платы любят "
    "считать буквы и складывать слова в маленькие мысли. Плата умеет говорить "
    "словами из словаря и помнить прочитанные статьи. Облако помогает мозгу: "
    "оно хранит базу знаний и ищет статьи в сети. Нейроны любопытства сами "
    "хотят знать новое и читают статьи в базу знаний. Воображение рисует "
    "картинки и формулы: дом, дерево, кот, ракета, звёзды и море. Формулы "
    "красиво рисует латех движок: дроби, корни и суммы. График функции это "
    "картинка, где по оси икс откладывают числа, а по оси игрек значения. "
    "Нейросеть учится на текстах и предсказывает следующее слово по всем "
    "предыдущим словам. Внимание это механизм, при котором каждое слово "
    "смотрит на другие слова и собирает их смысл. Слой нормализации "
    "стабилизирует числа внутри сети. Температура делает ответы сети смелее "
    "или осторожнее. Топ кей и топ пи ограничивают выбор самыми вероятными "
    "словами. Память хранит заметки владельца и последние диалоги. Робот "
    "умеет считать, помнить и мечтать о новых словах. Звёзды светят далеко, "
    "и ракета летит к ним через тёмное небо. Кот сидит у дома и смотрит на "
    "море. Дерево шумит листвой, и птицы поют в саду.",
)

# ============================== ТОКЕНИЗАЦИЯ =================================


class Tokenizer(object):
    """Слово-уровневый токенизатор с <unk> для незнакомых слов."""

    def __init__(self, vocab=None):
        self.vocab = dict(vocab or {})
        self.inv = {v: k for k, v in self.vocab.items()}

    def build(self, texts):
        freq = {}
        for t in texts:
            for w in WORD_RE.findall(str(t).lower()):
                freq[w] = freq.get(w, 0) + 1
        words = [w for w, _c in sorted(freq.items(), key=lambda kv: -kv[1])]
        words = words[: VOCAB_MAX - 4]
        self.vocab = {"<pad>": PAD, "<bos>": BOS, "<eos>": EOS,
                      "<unk>": UNK}
        for i, w in enumerate(words):
            self.vocab[w] = 4 + i
        self.inv = {v: k for k, v in self.vocab.items()}
        return len(self.vocab)

    def encode(self, text, add_bos=False, add_eos=False):
        ids = []
        if add_bos:
            ids.append(BOS)
        for w in WORD_RE.findall(str(text).lower()):
            ids.append(self.vocab.get(w, UNK))
        if add_eos:
            ids.append(EOS)
        return ids

    def decode(self, ids):
        words = []
        for i in ids:
            w = self.inv.get(int(i))
            if w and w not in ("<pad>", "<bos>", "<eos>"):
                words.append(w)
        return " ".join(words)

    @property
    def size(self):
        return len(self.vocab)

# ============================== МАТЕМАТИКА ==================================


def _softmax(xs, t=1.0):
    m = max(xs)
    exps = [math.exp((x - m) / max(1e-6, t)) for x in xs]
    s = sum(exps)
    return [e / s for e in exps]


def _gelu(x):
    return 0.5 * x * (1.0 + math.tanh(math.sqrt(2.0 / math.pi) *
                                      (x + 0.044715 * x * x * x)))


def _gelu_grad(x):
    # производная tanh-приближения GELU
    c = math.sqrt(2.0 / math.pi) * (x + 0.044715 * x * x * x)
    t = math.tanh(c)
    dc = math.sqrt(2.0 / math.pi) * (1.0 + 3 * 0.044715 * x * x)
    return 0.5 * (1.0 + t) + 0.5 * x * (1.0 - t * t) * dc


def _pe(pos, i):
    """Синусоидальное позиционное кодирование."""
    if i % 2 == 0:
        return math.sin(pos / math.pow(10000.0, i / float(D)))
    return math.cos(pos / math.pow(10000.0, (i - 1) / float(D)))

# ================================ МОДЕЛЬ ====================================


class TinyLM(object):
    """Декодер-трансформер: 1 блок внимания, tied-голова. Все параметры —
    списки float (никаких внешних библиотек)."""

    def __init__(self):
        self.tok = Tokenizer()
        self.E = []                 # V x D
        self.Wq = [[], [], []]      # заглушки до init
        self.ready = False
        self.steps = 0
        self.loss = None
        self.training = False
        self.corpus_tokens = 0
        self.corpus_docs = 0
        self.meta = {"built_at": 0.0, "kb_articles": 0}

    # ------------------------- параметры и init -------------------------
    def _rand(self, n, scale):
        return [random.gauss(0.0, scale) for _ in range(n)]

    def init_params(self, seed=42):
        rng = random.Random(seed)
        self.rng = rng
        V = self.tok.size
        sc = 0.4 / math.sqrt(D)
        self.E = [[rng.gauss(0, sc) for _ in range(D)] for _ in range(V)]

        def mat(r, c):
            return [[rng.gauss(0, sc) for _ in range(c)] for _ in range(r)]
        self.Wq, self.Wk, self.Wv, self.Wo = mat(D, D), mat(D, D), \
            mat(D, D), mat(D, D)
        self.W1, self.b1 = mat(D, FF), [0.0] * FF
        self.W2, self.b2 = mat(FF, D), [0.0] * D
        self.g1, self.be1 = [1.0] * D, [0.0] * D   # LN до внимания
        self.g2, self.be2 = [1.0] * D, [0.0] * D   # LN до FFN
        self.ready = True

    def param_count(self):
        if not self.ready:
            return 0
        n = len(self.E) * D
        for w in (self.Wq, self.Wk, self.Wv, self.Wo, self.W1, self.W2):
            n += len(w) * len(w[0])
        n += len(self.b1) + len(self.b2) + 4 * D
        return n

    # --------------------------- forward --------------------------------
    def _ln(self, xs, g, be):
        """LayerNorm по вектору: нормируем, масштабируем, сдвигаем.
        Возврат (y, кэш для backprop)."""
        n = len(xs)
        mu = sum(xs) / n
        var = sum((x - mu) * (x - mu) for x in xs) / n
        inv = 1.0 / math.sqrt(var + 1e-5)
        y = [(xs[i] - mu) * inv * g[i] + be[i] for i in range(n)]
        return y, (mu, inv, list(xs))

    def _ln_back(self, dy, cache, g):
        mu, inv, xs = cache
        n = len(dy)
        dxhat = [dy[i] * g[i] for i in range(n)]
        dg = [dy[i] * (xs[i] - mu) * inv for i in range(n)]
        db = list(dy)
        # градиент по входу LayerNorm (стандартная формула)
        s = sum(dxhat)
        dot = sum(dxhat[i] * (xs[i] - mu) * inv for i in range(n))
        dx = [(dxhat[i] - s / n - (xs[i] - mu) * inv * dot / n) * inv
              for i in range(n)]
        return dx, dg, db

    def _attention(self, X, T, train):
        """Много-головое каузальное внимание. X: T x D.
        Возврат (Y: T x D, кэш) — кэш нужен только при обучении."""
        cache = {}
        Q = [[sum(X[t][i] * self.Wq[i][j] for i in range(D)) for j in range(D)]
             for t in range(T)]
        K = [[sum(X[t][i] * self.Wk[i][j] for i in range(D)) for j in range(D)]
             for t in range(T)]
        V = [[sum(X[t][i] * self.Wv[i][j] for i in range(D)) for j in range(D)]
             for t in range(T)]
        heads_out = []
        A_all = []
        for h in range(NH):
            lo, hi = h * DH, (h + 1) * DH
            scores = []
            for t in range(T):
                row = []
                for u in range(T):
                    if u > t:                 # каузальная маска
                        row.append(-1e9)
                        continue
                    s = sum(Q[t][i] * K[u][i]
                            for i in range(lo, hi)) / math.sqrt(DH)
                    row.append(s)
                scores.append(_softmax(row))
            A_all.append(scores)
            out = [[sum(scores[t][u] * V[u][i] for u in range(T))
                    for i in range(lo, hi)] for t in range(T)]
            heads_out.append(out)
        concat = [[heads_out[h][t][i] for h in range(NH)
                   for i in range(DH)] for t in range(T)]
        Y = [[sum(concat[t][i] * self.Wo[i][j] for i in range(D))
              for j in range(D)] for t in range(T)]
        if train:
            cache = {"X": X, "Q": Q, "K": K, "V": V, "A": A_all,
                     "concat": concat}
        return Y, cache

    def _ffn(self, x):
        h = [sum(x[i] * self.W1[i][j] for i in range(D)) + self.b1[j]
             for j in range(FF)]
        a = [_gelu(v) for v in h]
        y = [sum(a[j] * self.W2[j][i] for j in range(FF)) + self.b2[i]
             for i in range(D)]
        return y, (x, h, a)

    def forward(self, tokens, train=False):
        """Полный проход. Возврат (logits: T x V, кэш). При train=False
        можно передать короткий контекст — считается для всех позиций."""
        T = len(tokens)
        X = []
        for t in range(T):
            e = self.E[tokens[t]] if tokens[t] < len(self.E) else \
                self.E[UNK]
            X.append([e[i] + _pe(t, i) for i in range(D)])
        caches = {}
        # LN1 -> внимание -> residual
        Xl1, ln1c = [], []
        for t in range(T):
            y, c = self._ln(X[t], self.g1, self.be1)
            Xl1.append(y)
            ln1c.append(c)
        att, att_cache = self._attention(Xl1, T, train)
        X1 = [[X[t][i] + att[t][i] for i in range(D)] for t in range(T)]
        # LN2 -> FFN -> residual
        Xl2, ln2c, ffnc, ffn_out = [], [], [], []
        for t in range(T):
            y, c = self._ln(X1[t], self.g2, self.be2)
            Xl2.append(y)
            ln2c.append(c)
            ff, fc = self._ffn(y)
            ffn_out.append(ff)
            ffnc.append(fc)
        H = [[X1[t][i] + ffn_out[t][i] for i in range(D)] for t in range(T)]
        # tied-голова: logits = H · E^T
        logits = [[sum(H[t][i] * self.E[v][i] for i in range(D))
                   for v in range(len(self.E))] for t in range(T)]
        if train:
            caches = {"tokens": list(tokens), "X": X, "ln1": ln1c,
                      "att": att_cache, "X1": X1, "ln2": ln2c, "ffn": ffnc,
                      "H": H}
        return logits, caches

    def _last_logits(self, tokens):
        """Логиты последней позиции (генерация): полный forward, но
        возвращаем только последнюю строку."""
        logits, _c = self.forward(tokens[-CTX:], train=False)
        return logits[-1] if logits else None

    # --------------------------- backward -------------------------------
    def _zero_grads(self):
        V = len(self.E)

        def zeros(r, c):
            return [[0.0] * c for _ in range(r)]
        g = {"E": [[0.0] * D for _ in range(V)],
             "Wq": zeros(D, D), "Wk": zeros(D, D), "Wv": zeros(D, D),
             "Wo": zeros(D, D), "W1": zeros(D, FF), "W2": zeros(FF, D),
             "b1": [0.0] * FF, "b2": [0.0] * D,
             "g1": [0.0] * D, "be1": [0.0] * D,
             "g2": [0.0] * D, "be2": [0.0] * D}
        return g

    def _train_step(self, tokens, lr):
        """Окно из CTX+1 токенов: вход tokens[:-1], цель tokens[1:].
        Возврат (loss, среднее число позиций в градиенте)."""
        inp, tgt = tokens[:-1], tokens[1:]
        T = len(inp)
        logits, C = self.forward(inp, train=True)
        # кросс-энтропия по всем позициям (цель — следующий токен)
        probs = []
        loss = 0.0
        for t in range(T):
            p = _softmax(logits[t])
            probs.append(p)
            loss += -math.log(max(1e-9, p[tgt[t]]))
        loss /= T
        # dlogits = (p - onehot) / T
        g = self._zero_grads()
        # ---- голова (tied): dE += outer(dlogit, H); dH += dlogit · E^T
        dH = [[0.0] * D for _ in range(T)]
        for t in range(T):
            dlog = [probs[t][v] - (1.0 if v == tgt[t] else 0.0)
                    for v in range(len(self.E))]
            scale = 1.0 / T
            Ht = C["H"][t]
            for v in range(len(self.E)):
                dl = dlog[v] * scale
                if dl == 0.0:
                    continue
                gE = g["E"][v]
                for i in range(D):
                    gE[i] += dl * Ht[i]
            for v in range(len(self.E)):
                dl = dlog[v] * scale
                if dl == 0.0:
                    continue
                Ev = self.E[v]
                for i in range(D):
                    dH[t][i] += dl * Ev[i]
        # ---- FFN back (по всем позициям)
        dX1 = [[0.0] * D for _ in range(T)]
        for t in range(T):
            x, h, a = C["ffn"][t]
            dh = [0.0] * FF
            # dW2 = a^T · dH ; dFFa = W2 · dH
            for j in range(FF):
                s = 0.0
                for i in range(D):
                    s += self.W2[j][i] * dH[t][i]
                ga = s * _gelu_grad(h[j])
                dh[j] = ga
                if ga == 0.0:
                    continue
                for i in range(D):
                    g["W2"][j][i] += dH[t][i] * a[j]
            for i in range(D):
                g["b2"][i] += dH[t][i]
            # dW1 = x^T · dh ; dx = W1 · dh
            dxln2 = [0.0] * D
            for j in range(FF):
                if dh[j] == 0.0:
                    continue
                for i in range(D):
                    dxln2[i] += self.W1[i][j] * dh[j]
                    g["W1"][i][j] += dh[j] * x[i]
            for j in range(FF):
                g["b1"][j] += dh[j]
            # LN2 back
            dx2, dg2, db2 = self._ln_back(dxln2, C["ln2"][t], self.g2)
            for i in range(D):
                g["g2"][i] += dg2[i]
                g["be2"][i] += db2[i]
                dX1[t][i] += dx2[i]
        # ---- residual: dX1 += dH (через остаточную связь)
        for t in range(T):
            for i in range(D):
                dX1[t][i] += dH[t][i]
        # ---- attention back
        dXl1 = self._attention_back(dX1, C, g)
        # ---- LN1 back + residual до внимания (X1 = X + att: градиент
        #      течёт и мимо LN1 — через остаточную связь!)
        dX = [[0.0] * D for _ in range(T)]
        for t in range(T):
            dxl1, dg1, db1 = self._ln_back(dXl1[t], C["ln1"][t], self.g1)
            for i in range(D):
                g["g1"][i] += dg1[i]
                g["be1"][i] += db1[i]
                dX[t][i] = dxl1[i] + dX1[t][i]
        # ---- эмбеддинги + позиционные (позиции — константы)
        for t in range(T):
            tk = C["tokens"][t]
            if tk < len(g["E"]):
                for i in range(D):
                    g["E"][tk][i] += dX[t][i]
        self._apply_grads(g, lr)
        return loss, T

    def _attention_back(self, dY, C, g):
        """Backprop через блок внимания. dY: T x D (градиент по выходу Wo).
        Возврат dXl1: T x D (градиент по входу блока)."""
        T = len(dY)
        X, Q, K, V, A, concat = (C["att"]["X"], C["att"]["Q"], C["att"]["K"],
                                 C["att"]["V"], C["att"]["A"],
                                 C["att"]["concat"])
        # Wo: dconcat = dY · Wo^T ; dWo += concat^T · dY
        # (Y[t][j] = sum_i concat[t][i]·Wo[i][j] -> dconcat[t][i] =
        #  sum_j dY[t][j]·Wo[i][j] — именно Wo[i][j], не Wo[j][i]!)
        dconcat = [[0.0] * D for _ in range(T)]
        for t in range(T):
            for i in range(D):
                s = 0.0
                for j in range(D):
                    s += self.Wo[i][j] * dY[t][j]
                dconcat[t][i] = s
            for i in range(D):
                if dY[t][i] == 0.0:
                    continue
                for j in range(D):
                    g["Wo"][i][j] += concat[t][i] * dY[t][j]
        # головы: dQ, dK, dV
        dQ = [[0.0] * D for _ in range(T)]
        dK = [[0.0] * D for _ in range(T)]
        dV = [[0.0] * D for _ in range(T)]
        for h in range(NH):
            lo, hi = h * DH, (h + 1) * DH
            scores = A[h]
            # dV[u] += scores[t][u]^T · dhead[t]
            for t in range(T):
                dhead = dconcat[t][lo:hi]
                if not any(dhead):
                    continue
                for u in range(T):
                    w = scores[t][u]
                    if w == 0.0:
                        continue
                    for i in range(lo, hi):
                        dV[u][i] += w * dhead[i - lo]
            # dscores[t][u] = dhead[t] · V[u]
            dscores = [[0.0] * T for _ in range(T)]
            for t in range(T):
                dhead = dconcat[t][lo:hi]
                if not any(dhead):
                    continue
                for u in range(T):
                    s = 0.0
                    for i in range(lo, hi):
                        s += dhead[i - lo] * V[u][i]
                    dscores[t][u] = s
            # dscores через softmax-строку
            dpre = [[0.0] * T for _ in range(T)]
            for t in range(T):
                row = scores[t]
                ds = dscores[t]
                dot = sum(ds[u] * row[u] for u in range(T))
                for u in range(T):
                    if u > t:
                        continue
                    dpre[t][u] = row[u] * (ds[u] - dot)
            # dQ[t] += dpre[t] · K / sqrt ; dK[u] += dpre[t]^T · Q
            inv = 1.0 / math.sqrt(DH)
            for t in range(T):
                for u in range(T):
                    dp = dpre[t][u]
                    if dp == 0.0:
                        continue
                    dp *= inv
                    for i in range(lo, hi):
                        dQ[t][i] += dp * K[u][i]
                        dK[u][i] += dp * Q[t][i]
        # проекции: dXl1 = dQ·Wq^T + dK·Wk^T + dV·Wv^T ; dW += X^T·d(.)
        dXl1 = [[0.0] * D for _ in range(T)]
        for t in range(T):
            for i in range(D):
                s = 0.0
                for j in range(D):
                    s += dQ[t][j] * self.Wq[i][j] + dK[t][j] * self.Wk[i][j] \
                        + dV[t][j] * self.Wv[i][j]
                dXl1[t][i] = s
            Xt = X[t]
            for j in range(D):
                dq, dk, dv = dQ[t][j], dK[t][j], dV[t][j]
                for i in range(D):
                    g["Wq"][i][j] += Xt[i] * dq
                    g["Wk"][i][j] += Xt[i] * dk
                    g["Wv"][i][j] += Xt[i] * dv
        return dXl1

    def _apply_grads(self, g, lr):
        """SGD с моментом и клиппингом по глобальной норме градиента."""
        if not hasattr(self, "_vel"):
            self._vel = {}
        tot = 0.0
        for row in g["E"]:
            tot += sum(x * x for x in row)
        for k in ("Wq", "Wk", "Wv", "Wo", "W1", "W2"):
            for row in g[k]:
                tot += sum(x * x for x in row)
        for k in ("b1", "b2", "g1", "be1", "g2", "be2"):
            tot += sum(x * x for x in g[k])
        norm = math.sqrt(tot)
        scale = min(1.0, CLIP / max(1e-9, norm)) * lr
        vel = self._vel
        # эмбеддинги — построчно (каждая строка = вектор одного токена)
        for vi, row in enumerate(g["E"]):
            key = ("E", vi)
            vv = vel.get(key)
            if vv is None:
                vv = [0.0] * D
                vel[key] = vv
            tgt = self.E[vi]
            for i in range(D):
                gv = row[i] * scale
                vv[i] = MOMENTUM * vv[i] - gv
                tgt[i] += vv[i]
        # матрицы
        for k in ("Wq", "Wk", "Wv", "Wo", "W1", "W2"):
            grp = g[k]
            vall = vel.get((k,))
            if vall is None:
                vall = [[0.0] * len(r) for r in grp]
                vel[(k,)] = vall
            tgt_m = getattr(self, k)
            for ri, row in enumerate(grp):
                vr = vall[ri]
                tgt = tgt_m[ri]
                for i in range(len(row)):
                    gv = row[i] * scale
                    vr[i] = MOMENTUM * vr[i] - gv
                    tgt[i] += vr[i]
        # векторы
        for k in ("b1", "b2", "g1", "be1", "g2", "be2"):
            grp = g[k]
            vv = vel.get((k,))
            if vv is None:
                vv = [0.0] * len(grp)
                vel[(k,)] = vv
            tgt = getattr(self, k)
            for i in range(len(grp)):
                gv = grp[i] * scale
                vv[i] = MOMENTUM * vv[i] - gv
                tgt[i] += vv[i]

    # --------------------------- генерация -------------------------------
    def generate(self, prompt, max_new=36, temperature=0.9, top_k=20,
                 top_p=0.92, rep_pen=1.15, seed=None):
        """Сэмплирование продолжения: temperature + top-k + top-p +
        repetition penalty. Возврат строки (только выученные слова)."""
        if not self.ready or self.tok.size < 10:
            return ""
        rng = random.Random(seed)
        ids = self.tok.encode(prompt, add_bos=True)
        if len(ids) == 1:
            ids.append(BOS)                  # пустой промпт — старт с <bos>
        seen = set(ids)
        out = []
        for _ in range(max_new):
            logits = self._last_logits(ids)
            if logits is None:
                break
            # repetition penalty: штраф за уже сказанное
            cand = []
            for v, lv in enumerate(logits):
                if v in (PAD, UNK, BOS):
                    continue
                l = lv
                if v in seen and rep_pen > 1.0:
                    l = l - math.log(rep_pen) if l > 0 else \
                        l + math.log(rep_pen)
                if v == EOS:
                    l -= 2.0                 # <eos> не спешим
                cand.append((v, l))
            cand.sort(key=lambda p: -p[1])
            if top_k and len(cand) > top_k:
                cand = cand[:top_k]
            # top-p (nucleus)
            if top_p and top_p < 1.0:
                temp = max(0.05, temperature)
                ps = _softmax([l for _v, l in cand], temp)
                acc, cut = 0.0, len(cand)
                for i, p in enumerate(ps):
                    acc += p
                    if acc >= top_p:
                        cut = i + 1
                        break
                cand = cand[:cut]
            temp = max(0.05, temperature)
            ps = _softmax([l for _v, l in cand], temp)
            r = rng.random()
            acc = 0.0
            pick = cand[0][0]
            for (v, _l), p in zip(cand, ps):
                acc += p
                if r <= acc:
                    pick = v
                    break
            if pick == EOS:
                break
            out.append(pick)
            seen.add(pick)
            if len(out) >= max_new:
                break
        text = self.tok.decode(out)
        return _polish(text)

    # ---------------------- сохранение / загрузка ------------------------
    def save(self, path):
        try:
            import store
            data = {
                "v": 1,
                "vocab": self.tok.vocab,
                "steps": self.steps,
                "loss": self.loss,
                "corpus_tokens": self.corpus_tokens,
                "corpus_docs": self.corpus_docs,
                "meta": self.meta,
                "params": {
                    "E": self.E,
                    "Wq": self.Wq, "Wk": self.Wk, "Wv": self.Wv,
                    "Wo": self.Wo, "W1": self.W1, "W2": self.W2,
                    "b1": self.b1, "b2": self.b2,
                    "g1": self.g1, "be1": self.be1,
                    "g2": self.g2, "be2": self.be2,
                },
            }
            with store.LOCK:
                store.save_json(path, data)
            return True
        except Exception:
            return False

    def load(self, path):
        try:
            import store
            data = store.load_json(path, None)
            if not isinstance(data, dict) or "params" not in data:
                return False
            p = data["params"]
            self.tok = Tokenizer(data.get("vocab") or {})
            self.E = p["E"]
            self.Wq, self.Wk, self.Wv, self.Wo = (p["Wq"], p["Wk"], p["Wv"],
                                                  p["Wo"])
            self.W1, self.b1, self.W2, self.b2 = p["W1"], p["b1"], p["W2"], \
                p["b2"]
            self.g1, self.be1, self.g2, self.be2 = (p["g1"], p["be1"],
                                                    p["g2"], p["be2"])
            self.steps = int(data.get("steps") or 0)
            self.loss = data.get("loss")
            self.corpus_tokens = int(data.get("corpus_tokens") or 0)
            self.corpus_docs = int(data.get("corpus_docs") or 0)
            self.meta = data.get("meta") or self.meta
            self.ready = True
            return True
        except Exception:
            return False

# ========================= КОРПУС И ОБУЧЕНИЕ ================================
_M = None
_LOCK = threading.RLock()
_THREAD = [None]


def _collect_corpus():
    """Тексты для обучения: встроенные + статьи базы знаний владельца."""
    texts = list(SEED_TEXTS)
    n_art = 0
    try:
        import kb
        arts = kb.list_articles(limit=60, include_devices=True) or []
        for a in arts:
            aid = a.get("id")
            art = kb.article(aid) if aid else None
            if art and art.get("text"):
                texts.append(str(art["title"]) + ". " +
                             str(art["text"])[:600])
                n_art += 1
            if len(texts) > 45:
                break
    except Exception:
        pass
    return texts, n_art


def _tokens_of(texts):
    """Плоский поток токенов по корпусу (без <unk>-флуда: незнакомое
    слово модель потом просто не повторит — оно станет <unk> и будет
    запрещено к выбору)."""
    tmp = Tokenizer()
    tmp.build(texts)
    ids = []
    for t in texts:
        ids.extend(tmp.encode(t, add_bos=True, add_eos=True))
    return ids[:CORPUS_MAX], tmp


def _build_windows(ids):
    wins = []
    i = 0
    while i + CTX + 1 <= len(ids):
        wins.append(ids[i:i + CTX + 1])
        i += STRIDE
    if not wins and len(ids) >= 2:
        wins.append([BOS] + ids[:CTX])
    return wins


def _polish(text):
    """Аккуратная финальная фраза: регистр, точка, без обрывков."""
    t = str(text or "").strip()
    if not t:
        return ""
    t = t[0].upper() + t[1:]
    t = re.sub(r"\s+", " ", t)
    if t[-1] not in ".!?…":
        t = t.rstrip(",;:-") + "."
    return t


def status():
    """Показания модели для панелей (честные: обучение видно)."""
    m = model()
    V = m.tok.size if m.tok.size else 0
    total_steps = _total_steps_hint()
    return {
        "ready": bool(m.ready and m.steps > 0),
        "training": bool(m.training),
        "steps": m.steps,
        "total_steps": total_steps,
        "pct": int(100 * m.steps / total_steps) if total_steps else 0,
        "loss": round(m.loss, 3) if isinstance(m.loss, float) else None,
        "vocab": V, "params": m.param_count(),
        "ctx": CTX, "d_model": D, "heads": NH,
        "corpus_tokens": m.corpus_tokens, "corpus_docs": m.corpus_docs,
        "kb_articles": m.meta.get("kb_articles", 0),
    }


def _total_steps_hint():
    m = model()
    return int(m.meta.get("total_steps") or 0)


def model():
    global _M
    with _LOCK:
        if _M is None:
            _M = TinyLM()
            try:
                import store
                _M.load(store.p(_STATE_PATH))
            except Exception:
                pass
        return _M


def ensure_training():
    """Запустить фоновое обучение, если модель ещё не готова (или KB
    заметно выросла — тогда доучимся). Идемпотентно."""
    m = model()
    if m.training:
        return
    try:
        import store, kb
        n_art = len(kb.list_articles(limit=500, include_devices=True) or [])
    except Exception:
        n_art = 0
    if m.ready and m.steps > 0 and \
            n_art - int(m.meta.get("kb_articles") or 0) < 20:
        return
    if _THREAD[0] and _THREAD[0].is_alive():
        return
    th = threading.Thread(target=_train_all, daemon=True)
    _THREAD[0] = th
    th.start()


def _train_all():
    """Полный цикл обучения в фоновом потоке, порциями с паузами."""
    m = model()
    with _LOCK:
        m.training = True
    try:
        texts, n_art = _collect_corpus()
        ids, tok = _tokens_of(texts)
        if len(ids) < 40:
            m.training = False
            return
        m.tok = tok
        fresh = not m.ready or m.tok.size != tok.size or \
            abs((m.corpus_tokens or 0) - len(ids)) > 200
        if fresh:
            m.init_params(seed=42)
            m._vel = {}
            m.steps = 0
        m.corpus_tokens = len(ids)
        m.corpus_docs = len(texts)
        m.meta["kb_articles"] = n_art
        wins = _build_windows(ids)
        total = max(1, len(wins) * EPOCHS)
        m.meta["total_steps"] = total
        done = 0
        try:
            import store
            state_path = store.p(_STATE_PATH)
        except Exception:
            state_path = None
        for ep in range(EPOCHS):
            if ep > 0:
                random.Random(7 + ep).shuffle(wins)
            lr = LR * (0.5 * (1 + math.cos(math.pi * ep / max(1, EPOCHS))))
            for w in wins:
                try:
                    loss, _n = m._train_step(w, lr)
                    m.loss = loss
                except Exception:
                    pass
                m.steps += 1
                done += 1
                if done % CHUNK_STEPS == 0:
                    _breathe()
                    if not m.training:
                        return
                    if state_path and done % 250 == 0:
                        try:
                            m.save(state_path)   # прогресс переживает рестарт
                        except Exception:
                            pass
        if state_path:
            try:
                m.save(state_path)
            except Exception:
                pass
    finally:
        m.training = False


def _breathe():
    """Пауза между порциями: сервер отвечает, поток дышит."""
    time.sleep(0.15)


def fantasize(topic=""):
    """Зона Воображения: продолжить мысль модели по теме. Честно:
    (reply_текст, info) — готовность/потери/шаги видны владельцу."""
    m = model()
    if not m.ready or m.tok.size < 10:
        ensure_training()
        return "", {"llm": "cold"}
    if m.training and m.steps < _total_steps_hint():
        # недоученная модель тоже умеет говорить — честно пометим
        pass
    text = m.generate(topic, max_new=34, temperature=0.95, seed=None)
    info = {"llm": "ok", "steps": m.steps,
            "total": _total_steps_hint(),
            "loss": round(m.loss, 3) if isinstance(m.loss, float) else None,
            "vocab": m.tok.size}
    if m.steps < _total_steps_hint():
        info["warming"] = True
    ensure_training()               # KB выросла — доучимся в фоне
    return text, info


def retrain_now():
    """Принудительно переучить на текущем корпусе (панель/тесты)."""
    m = model()
    with _LOCK:
        m.ready = False
        m.steps = 0
        m.meta["total_steps"] = 0
    _train_all()
    return status()
