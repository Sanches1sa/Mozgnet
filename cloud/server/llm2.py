# -*- coding: utf-8 -*-
"""
llm2.py — v3.117: ПЕРЕРАБОТКА СВОЕЙ LLM (словарь, обучение, речь).

Почему прежняя LLM «зацикливалась на англ н н н»:
  1) СЛОВАРЬ ПО СЛОВАМ + <unk>. Всё, чего нет в словаре (в русском — каждое
     третье слово из-за окончаний), превращалось в <unk>. Треть того, чему
     училась сеть, была «предскажи <unk>», а в речи <unk> запрещён — сеть
     говорила остатком: служебными словами и однобуквенными «словами»
     («н» из «н. э.», «т» из «т. е.», «англ» из «(англ. …)»).
  2) ОБРЫВКИ СОКРАЩЕНИЙ в корпусе: «до н. э.», «т. е.», «и т. д.»,
     греческие/латинские пометки — частые «слова», не несущие смысла.
  3) СЛАБАЯ ЗАЩИТА ОТ ПОВТОРОВ в свободной речи (мысли, разговор):
     только мягкий штраф — недоученная сеть легко уходила в петлю.

Что теперь:
  • ТОКЕНАЙЗЕР BPE: слово = 1–3 куска (основа + окончание), <unk> почти не
    бывает, сеть учится на ВСЁМ тексте. Частые слова — целым токеном.
  • НОРМАЛИЗАЦИЯ КОРПУСА: сокращения раскрываются («н. э.» → «нашей эры»,
    «т. е.» → «то есть»), пометки языка и слова чужих алфавитов убираются.
  • РЕЧЬ ТОЛЬКО НАСТОЯЩИМИ СЛОВАМИ: генерация идёт по «словарному автомату»
    (префиксное дерево слов корпуса) — куски складываются только в слова,
    которые реально встречались в базе. Выдумок и обрывков не бывает.
  • ЖЁСТКИЕ ПРАВИЛА ОТ ПЕТЕЛЬ: то же слово подряд — нельзя; слово больше
    2 раз (служебное — 3) — нельзя; пара слов не повторяется; знак
    препинания не в начале и не два подряд; скобки/кавычки парные.
  • ПОДСТРАХОВКА СТАТИСТИКОЙ: пока сеть недоучена, её распределение
    смешивается с n-граммной статистикой той же базы (тройки/пары кусков).
    Доля сети растёт по мере обучения (по её потере) — до 85 %.
  • ПРОВЕРКА РЕЗУЛЬТАТА: вырожденный текст не показывается — ещё попытка
    с другим зерном, не вышло — молчание (ответит мозг).
"""
import math
import re

PAD, BOS, EOS, UNK = 0, 1, 2, 3
TOK_VER = 2
WORD_RE = re.compile(r"[а-яёa-z0-9]+|[^\sа-яёa-z0-9]", re.I)
_ALNUM_RE = re.compile(r"^[а-яёa-z0-9]+$")
_CYR_RE = re.compile(r"^[а-яё]+$")
_LAT_RE = re.compile(r"^[a-z]+$")

# знаки, которыми сеть может пользоваться в речи
SPEECH_PUNCT = frozenset(". , ! ? : ; — ( ) « » -".split())
END_PUNCT = frozenset(". ! ?".split())
OPENERS = {"(": ")", "«": "»"}
CLOSERS = {")": "(", "»": "«"}
ONE_LETTER_OK = frozenset("а в и к о с у я".split())
FUNC_W = frozenset("и в на с по не что как а но о к у из за от до же ли бы "
                   "то это для при или его их она он они так также который "
                   "которые которая было был была были есть".split())
# после них не бывает знака препинания и ими не кончается предложение
NO_PUNCT_AFTER = frozenset("и в во на с со по к ко у из за от до о об обо при для "
                           "а но или что как который которая которое которые "
                           "чтобы если когда где же ли не ни без под над перед "
                           "через между про".split())
# не слова речи: пометки языка, обрывки сокращений
JUNK_W = frozenset("англ лат греч франц фр итал исп нидерл санскр польск "
                   "чеш норв укр яп ивр др см напр тыс млн млрд гг вв тт "
                   "рис табл стр гл э н т г ш л б м".split())
# («нем», «им», «порт», «тур», «кит», «араб», «швед» — настоящие слова,
#  их пометки «(нем. …)» снимает normalize по скобкам)

# ----------------------------------------------------------------------------
# НОРМАЛИЗАЦИЯ КОРПУСА
# ----------------------------------------------------------------------------
_GLOSS_RE = re.compile(r"\(\s*(?:от\s+)?(?:англ|лат|нем|фр|франц|греч|др\.?\s*-?\s*греч|"
                       r"итал|исп|яп|кит|араб|укр|польск|чеш|швед|норв|"
                       r"нидерл|порт|тур|ивр|санскр|церк\.?-?\s*слав)\.?[^()]{0,200}\)",
                       re.I)
_ABBR = [
    (r"\bдо\s+н\.\s*э\.", "до нашей эры"),
    (r"\bн\.\s*э\.", "нашей эры"),
    (r"\bт\.\s*е\.", "то есть"),
    (r"\bи\s+т\.\s*д\.", "и так далее"),
    (r"\bи\s+т\.\s*п\.", "и тому подобное"),
    (r"\bв\s+т\.\s*ч\.", "в том числе"),
    (r"\bт\.\s*н\.", "так называемый"),
    (r"\bт\.\s*к\.", "так как"),
    (r"\bи\s+др\.", "и другие"),
    (r"\bсм\.\s*", ""),
    (r"\bнапр\.", "например"),
    (r"\bок\.\s*(?=\d)", "около "),
    (r"\bим\.\s*", "имени "),
    (r"(?<=\d)\s*(?:гг?|вв?)\.", ""),
    (r"\b([IVXLC]+)\s*в\.", r"\1 век"),
    (r"(?<=\d)\s*тыс\.", " тысяч"),
    (r"(?<=\d)\s*млн\b\.?", " миллионов"),
    (r"(?<=\d)\s*млрд\b\.?", " миллиардов"),
    (r"\b(?:англ|лат|нем|фр|франц|греч|итал|исп)\.\s*(?=[A-Za-z\u0370-\u03ff«\"])", ""),
]
# сокращение в конце предложения: его точка — и точка предложения
_ABBR = ([(re.compile(a + r"(?=\s+[А-ЯЁA-Z]|\s*$)"), b + ".")
          for a, b in _ABBR[:7]] +
         [(re.compile(a, re.I), b) for a, b in _ABBR])
# слова чужих алфавитов (греческий, иероглифы…) — 2+ буквы подряд; одиночные
# буквы (γ, π, ν в формулах) и математические знаки остаются
_FOREIGN_RE = re.compile(r"[^\W\d_а-яёА-ЯЁa-zA-Z]{2,}")


def normalize(text):
    """Текст статьи -> текст для обучения: без пометок языка, с раскрытыми
    сокращениями, без слов чужих алфавитов (греческий и пр.)."""
    t = str(text or "")
    for _ in range(2):                       # вложенные скобки — в 2 прохода
        t = _GLOSS_RE.sub("", t)
    for rx, rep in _ABBR:
        t = rx.sub(rep, t)
    t = _FOREIGN_RE.sub(" ", t)
    t = re.sub(r"\(\s*[;,]?\s*\)", "", t)    # опустевшие скобки
    t = re.sub(r"[ \t]{2,}", " ", t)
    t = re.sub(r"\s+([.,;:!?])", r"\1", t)
    return t


# ----------------------------------------------------------------------------
# BPE
# ----------------------------------------------------------------------------
def word_symbols(w):
    return ["▁" + w[0]] + list(w[1:])


def train_bpe(word_freq, n_merges, max_words=40000):
    """Обучить слияния BPE по частотам слов. -> список пар (a, b).
    Куча с ленивым обновлением: десятки тысяч слов и тысячи слияний —
    секунды, а не минуты."""
    import heapq
    items = sorted(word_freq.items(), key=lambda kv: -kv[1])[:max_words]
    words = [word_symbols(w) for w, _c in items]
    freqs = [c for _w, c in items]
    pairs = {}
    where = {}
    for wi, sy in enumerate(words):
        f = freqs[wi]
        for a, b in zip(sy, sy[1:]):
            pairs[(a, b)] = pairs.get((a, b), 0) + f
            where.setdefault((a, b), set()).add(wi)
    heap = [(-c, k) for k, c in pairs.items()]
    heapq.heapify(heap)
    merges = []
    while len(merges) < max(0, int(n_merges)) and heap:
        negc, k = heapq.heappop(heap)
        if pairs.get(k, 0) != -negc:
            continue                              # устаревшая запись
        if -negc < 2:
            break
        a, b = k
        ab = a + b
        merges.append((a, b))
        touched = set()
        for wi in list(where.get(k, ())):
            sy = words[wi]
            f = freqs[wi]
            for x, y in zip(sy, sy[1:]):
                kk = (x, y)
                pairs[kk] -= f
                touched.add(kk)
                s_ = where.get(kk)
                if s_ is not None:
                    s_.discard(wi)
            ns, i = [], 0
            while i < len(sy):
                if i < len(sy) - 1 and sy[i] == a and sy[i + 1] == b:
                    ns.append(ab)
                    i += 2
                else:
                    ns.append(sy[i])
                    i += 1
            words[wi] = ns
            for x, y in zip(ns, ns[1:]):
                kk = (x, y)
                pairs[kk] = pairs.get(kk, 0) + f
                where.setdefault(kk, set()).add(wi)
                touched.add(kk)
        pairs.pop(k, None)
        where.pop(k, None)
        for kk in touched:
            c = pairs.get(kk, 0)
            if c <= 0:
                pairs.pop(kk, None)
                where.pop(kk, None)
            elif kk != k:
                heapq.heappush(heap, (-c, kk))
    return merges


def bpe_word(w, ranks, cache=None):
    """Слово -> список кусков по слияниям (ranks: (a,b) -> номер)."""
    if cache is not None:
        r = cache.get(w)
        if r is not None:
            return r
    sy = word_symbols(w)
    while len(sy) > 1:
        best, bi = None, -1
        for i in range(len(sy) - 1):
            rk = ranks.get((sy[i], sy[i + 1]))
            if rk is not None and (best is None or rk < best):
                best, bi = rk, i
        if best is None:
            break
        sy = sy[:bi] + [sy[bi] + sy[bi + 1]] + sy[bi + 2:]
    if cache is not None:
        if len(cache) > 200000:
            cache.clear()
        cache[w] = sy
    return sy


# ----------------------------------------------------------------------------
# СЛОВАРНЫЙ АВТОМАТ: какие слова сеть может произносить
# ----------------------------------------------------------------------------
def speech_word_ok(w, freq):
    """Слово годится для речи сети."""
    if not w or w in JUNK_W:
        return False
    if len(w) == 1:
        return w in ONE_LETTER_OK
    if w.isdigit():
        return len(w) <= 4 and freq >= 2
    if _CYR_RE.match(w):
        return True
    if _LAT_RE.match(w):
        return len(w) >= 3 and freq >= 3
    return False                              # смесь букв/цифр


def pick_speech_words(word_freq, cap=60000):
    good = [(w, c) for w, c in word_freq.items() if speech_word_ok(w, c)]
    good.sort(key=lambda kv: -kv[1])
    if len(good) > cap:                       # огромная база — без единичных
        good = [x for x in good if x[1] >= 2][:cap]
    return [w for w, _c in good]


class WordGuide(object):
    """Префиксное дерево слов речи + маски «какой кусок можно взять»."""

    def __init__(self, tok, words):
        self.words = set(words)
        self.pref = set()
        for w in self.words:
            for i in range(1, len(w) + 1):
                self.pref.add(w[:i])
        self.starts, self.conts, self.puncts = [], [], []
        for tk, i in tok.vocab.items():
            if tk.startswith("<") or tk.startswith("¢"):
                continue
            if tk.startswith("▁"):
                body = tk[1:]
                if body in SPEECH_PUNCT:
                    self.puncts.append((i, body))
                elif body and _ALNUM_RE.match(body) and body in self.pref:
                    self.starts.append((i, body))
            elif tk and _ALNUM_RE.match(tk):
                self.conts.append((i, tk))
        self.n = len(tok.vocab)


# ----------------------------------------------------------------------------
# N-ГРАММЫ (подстраховка недоученной сети)
# ----------------------------------------------------------------------------
class NGram(object):
    def __init__(self, ids, V):
        import numpy as np
        self.V = V
        uni = np.zeros(V, dtype=np.float64)
        bi, tri, quad = {}, {}, {}
        p3, p2, p1 = None, None, None
        for t in ids:
            if 0 <= t < V:
                uni[t] += 1
            if p1 is not None:
                d = bi.setdefault(p1, {})
                d[t] = d.get(t, 0) + 1
                if p2 is not None:
                    d = tri.setdefault((p2, p1), {})
                    d[t] = d.get(t, 0) + 1
                    if p3 is not None:
                        d = quad.setdefault((p3, p2, p1), {})
                        d[t] = d.get(t, 0) + 1
            p3, p2, p1 = p2, p1, t
            if t == EOS:
                p3, p2, p1 = None, None, None
        uni[[PAD, UNK]] = 0
        s = uni.sum()
        self.uni = (uni + 0.01) / (s + 0.01 * V)
        self.bi = {k: self._pack(v) for k, v in bi.items()}
        self.tri = {k: self._pack(v) for k, v in tri.items() if sum(v.values()) >= 2}
        self.quad = {k: self._pack(v) for k, v in quad.items() if sum(v.values()) >= 2}
        p = self.uni
        self.h_uni = float(-(p * np.log(p)).sum())       # энтропия униграмм
        self.n_tok = int(s)

    @staticmethod
    def _pack(d):
        import numpy as np
        idx = np.fromiter(d.keys(), dtype=np.int64)
        cnt = np.fromiter(d.values(), dtype=np.float64)
        return idx, cnt / cnt.sum()

    def probs(self, ctx):
        """Распределение следующего куска по трём предыдущим (смесь
        4-/3-/2-/1-грамм; чего нет — вес уходит остальным)."""
        p = self.uni * 0.05
        w = 0.05
        if ctx:
            b2 = self.bi.get(ctx[-1])
            if b2 is not None:
                p[b2[0]] += 0.20 * b2[1]
                w += 0.20
        if len(ctx) >= 2:
            t3 = self.tri.get((ctx[-2], ctx[-1]))
            if t3 is not None:
                p[t3[0]] += 0.35 * t3[1]
                w += 0.35
        if len(ctx) >= 3:
            q4 = self.quad.get((ctx[-3], ctx[-2], ctx[-1]))
            if q4 is not None:
                p[q4[0]] += 0.40 * q4[1]
                w += 0.40
        return p / w


def net_share(loss, ng):
    """Доля сети в смеси (0.15…0.85) по её потере относительно униграмм."""
    try:
        if ng is None:
            return 1.0
        # потеря обучения бывает обманчиво низкой (последний шаг, малое
        # окно) — берём замер сети на окнах корпуса, если он есть
        nl = getattr(ng, "net_nll", None)
        loss = nl if nl is not None else loss
        if loss is None:
            return 0.15
        prog = (ng.h_uni - float(loss)) / max(1e-6, ng.h_uni)
        return 0.15 + 0.70 * max(0.0, min(1.0, prog / 0.55))
    except Exception:
        return 0.5


# ----------------------------------------------------------------------------
# ПРОВЕРКА ТЕКСТА
# ----------------------------------------------------------------------------
def looks_degenerate(text):
    ws = re.findall(r"[а-яёa-z0-9]+", str(text or "").lower())
    if len(ws) < 3:
        return True
    if len(set(ws)) < 0.6 * len(ws):
        return True
    short = sum(1 for w in ws if len(w) <= 2)
    if short > 0.6 * len(ws):
        return True
    if any(w in JUNK_W for w in ws):
        return True
    for i in range(len(ws) - 1):
        if ws[i] == ws[i + 1]:
            return True
    return False


def finish_text(t):
    """Обрезать до последнего законченного предложения, убрать висящие
    скобки/кавычки и знаки в начале; первая буква — заглавная."""
    t = str(t or "").strip()
    # эхо формата диалогов из корпуса («Ответ: …», «Вопрос: …»)
    t = re.sub(r"(?i)\b(?:ответ|вопрос)\s*:\s*", "", t)
    t = re.sub(r"^[\s.,;:!?—\-)»]+", "", t)
    for o, c in (("(", ")"), ("«", "»")):
        while t.count(o) > t.count(c):        # висящая скобка — убрать знак
            k = t.rfind(o)
            t = (t[:k].rstrip() + " " + t[k + 1:].lstrip()).strip()
        while t.count(c) > t.count(o):
            k = t.find(c)
            t = (t[:k].rstrip() + t[k + 1:]).strip()
    m = list(re.finditer(r"[.!?…]", t))
    if m and m[-1].end() >= 0.5 * len(t):
        t = t[:m[-1].end()]
    t = t.rstrip(" ,;:—-")
    if t and t[-1] not in ".!?…":
        t += "."
    if t:
        t = t[0].upper() + t[1:]
    return re.sub(r"(^|[.!?]\s+)([а-яё])", lambda m_: m_.group(1) + m_.group(2).upper(), t)


# ----------------------------------------------------------------------------
# v3.118: ЧИСТКА ЛЮБЫХ МЫСЛЕЙ (платы, мозг облака, LLM) от «англ», «н»…
# ----------------------------------------------------------------------------
# «англ н англ н н» жили не только в LLM: такие фразы уже лежали в журнале
# мыслей, в чате «Мысли плат», в таблице «частота ↔ слова», в ящиках плат —
# и по кругу возвращались: плата слышит мысль облака, повторяет её, облако
# учится на мысли платы (и LLM — тоже). Чистка — на КАЖДОЙ границе.
_JUNK_SINGLE = "бгдежзйлмнпртфхцчшщъыьэю"   # в, с, к, у, о, а, и, я — слова
_JUNK_RE = re.compile(
    r"(?<![а-яёa-z0-9])(?:" + "|".join(sorted(
        (w for w in JUNK_W if len(w) > 1), key=len, reverse=True)) +
    r"|[" + _JUNK_SINGLE + r"])(?![а-яёa-z0-9])\.?", re.I)


def scrub(text, keep_lines=False):
    """Текст мысли -> без пометок языка, обрывков сокращений и одиночных
    «букв-слов» (н, т, э…). Вырожденный остаток -> ''."""
    t = normalize(text)
    t = _JUNK_RE.sub(" ", t)
    t = re.sub(r"(?<![а-яёa-z0-9])[а-яё]{1,2}\d+(?![а-яёa-z0-9])", " ", t, flags=re.I)
    t = re.sub(r"[ \t]{2,}", " ", t)
    t = re.sub(r"\s+([.,;:!?»)])", r"\1", t)
    t = re.sub(r"([,;:])(?:\s*[,;:])+", r"\1", t)
    t = re.sub(r"(?m)^[\s,.;:—-]+", "", t).strip()
    if not keep_lines:
        t = re.sub(r"\s+", " ", t).strip()
    return t


def thought_clean(text):
    """Мысль для журнала/ящиков/обучения: чистка + отказ от шума.
    -> чистый текст | '' (не хранить, не слать, не учиться)."""
    t = scrub(text, keep_lines=True)
    if "\n" in t:                         # сборка мыслей: чистим построчно
        keep = []
        for ln in t.split("\n"):
            core = re.sub(r"^[\s•*\-—·]+", "", ln).strip()
            if not core:
                continue
            if core != ln.strip() or len(keep):   # строка-пункт
                ws_ = re.findall(r"[а-яёa-z0-9]+", core.lower())
                if len(ws_) < 2 or (len(ws_) >= 3 and looks_degenerate(core)):
                    continue
            keep.append(ln)
        t = "\n".join(keep)
        if len(keep) <= 1 and t.rstrip().endswith(":"):
            return ""                       # заголовок без мыслей
    body = re.sub(r"^[^«]*«|»\s*$", "", t) if "«" in t else t
    ws = re.findall(r"[а-яёa-z0-9]+", body.lower())
    if len(ws) < 2:
        return ""
    if len(ws) >= 3 and looks_degenerate(body):
        return ""
    return t
