# -*- coding: utf-8 -*-
"""
shareddict.py — ОБЩИЙ СЛОВАРЬ МОЗГА (плата neuro_12 v2.75 + облако v3.133).

ОДИН И ТОТ ЖЕ ФАЙЛ лежит и на плате (python/shareddict.py), и в облаке
(server/shareddict.py): подбор слов на обеих сторонах — буквально один код.

Что хранится о каждом слове:
  • n      — сколько раз слово встретилось (в мыслях, репликах, текстах);
  • частота — средняя ЗНАКОВАЯ частота генератора плат (Гц), при которой
    слово звучало (мысли плат приходят с частотой, на которой родились);
  • связи  — какие слова шли СРАЗУ за ним (биграммы, как в сети знаний
    платы: служебные слова — тоже узлы, знаки препинания рвут цепочку).

ПОДБОР СЛОВА — формула генератора платы (_gen_weighted, v2.17–v2.66):
    вес = 1 + √(счёт связи)
          + 2·min(3, активность нейронов по основе слова)     (hot)
          + min(3, |голос зонной сети за зону|)              (zone_vote)
    вес ×= 1 + min(5, тяга гравитации мыслей к слову)        (grav)
  и общее для обеих сторон (v2.75):
    вес ×= 1 + 0.8·exp(−|частота слова − частота сейчас| / 400)  — слова,
           звучавшие на похожей частоте генератора, «резонируют»;
    вес = вес^(1/(1 + 0.3·дыхание)) — на вдохе выбор шире, на выдохе
           увереннее (дыхание — тензорной сети платы, общее для всех).
  Выбор — взвешенная случайность, как на плате.

СИНХРОНИЗАЦИЯ: каждая сторона копит ПРИРОСТ (inc) с прошлой сверки. Плата
шлёт прирост облаку, облако складывает его в общий словарь и отдаёт плате
строки, изменившиеся с её прошлой сверки (уже с вкладом всех плат).
"""
import json
import math
import os
import random
import re
import threading
import time

FUNC_W = frozenset("в во и на с со к ко по о об у из за от до не ни а но "
                   "же ли бы что как это при для или".split())
_TOK = re.compile(r"[а-яёa-z]+|[.,!?;:—()]")
CAP_WORDS = 40000
CAP_SUCC = 48


def norm(w):
    return str(w or "").strip().lower().replace("ё", "е")


class SharedDict:
    def __init__(self, path):
        self.path = path
        self.lock = threading.RLock()
        self.w = {}          # слово -> [n, сумма Гц, число замеров Гц]
        self.b = {}          # слово -> {следующее: n}
        self.rev = 0         # версия (растёт при каждом изменении)
        self.chg = {}        # слово -> версия последнего изменения
        self.inc_w = {}      # прирост с прошлой сверки: слово -> [dn, dhz, dhn]
        self.inc_b = {}      # прирост связей: a -> {b: dn}
        self.cursor = 0      # плата: версия облака на прошлой сверке
        self.dirty = False
        self.last_save = 0.0
        self.load()

    # ------------------------------------------------------------ память
    def load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                d = json.load(f)
            self.w = {k: list(v) for k, v in (d.get("w") or {}).items()}
            self.b = d.get("b") or {}
            self.rev = int(d.get("rev") or 0)
            self.chg = d.get("chg") or {}
            self.cursor = int(d.get("cursor") or 0)
            self.inc_w = {k: list(v) for k, v in (d.get("inc_w") or {}).items()}
            self.inc_b = d.get("inc_b") or {}
        except Exception:
            pass

    def save(self, force=False):
        if not (self.dirty or force) or (not force and time.time() - self.last_save < 30):
            return
        with self.lock:
            d = {"v": 1, "w": self.w, "b": self.b, "rev": self.rev, "chg": self.chg,
                 "cursor": self.cursor, "inc_w": self.inc_w, "inc_b": self.inc_b}
            tmp = self.path + ".tmp"
            try:
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(d, f, ensure_ascii=False)
                os.replace(tmp, self.path)
                self.dirty = False
                self.last_save = time.time()
            except Exception:
                pass

    # ------------------------------------------------------------ учёба
    def _touch(self, word):
        self.rev += 1
        self.chg[word] = self.rev

    def learn(self, text, hz=None, accept=None, track=True):
        """Текст (+ знаковая частота генератора, при которой он родился) ->
        словарь. accept(слово) — фильтр словаря стороны (плата — её
        русский словарь), служебные слова проходят всегда."""
        ws = []
        for tok in _TOK.findall(norm(text)[:6000]):
            if not tok[0].isalpha():
                ws.append("")
                continue
            if tok in FUNC_W or (len(tok) >= 3 and (accept is None or accept(tok))):
                ws.append(tok)
            else:
                ws.append("")
        try:
            hzf = float(hz) if hz is not None else None
        except (TypeError, ValueError):
            hzf = None
        n_new = 0
        with self.lock:
            for i, a in enumerate(ws):
                if not a:
                    continue
                row = self.w.get(a)
                if row is None:
                    if len(self.w) >= CAP_WORDS:
                        continue
                    row = self.w[a] = [0, 0.0, 0]
                    n_new += 1
                row[0] += 1
                if hzf is not None:
                    row[1] += hzf
                    row[2] += 1
                self._touch(a)
                if track:
                    iw = self.inc_w.setdefault(a, [0, 0.0, 0])
                    iw[0] += 1
                    if hzf is not None:
                        iw[1] += hzf
                        iw[2] += 1
                if i + 1 < len(ws) and ws[i + 1] and ws[i + 1] != a:
                    nb = ws[i + 1]
                    succ = self.b.setdefault(a, {})
                    if nb in succ or len(succ) < CAP_SUCC:
                        succ[nb] = succ.get(nb, 0) + 1
                        if track:
                            ib = self.inc_b.setdefault(a, {})
                            ib[nb] = ib.get(nb, 0) + 1
            self.dirty = True
        return n_new

    # ------------------------------------------------------------ знание
    def hz_of(self, word):
        row = self.w.get(norm(word))
        if not row or not row[2]:
            return None
        return row[1] / row[2]

    def successors(self, word, accept=None):
        succ = self.b.get(norm(word)) or {}
        return [(w, c) for w, c in succ.items() if accept is None or w in FUNC_W or accept(w)]

    def pool(self, top=120, accept=None):
        items = sorted(self.w.items(), key=lambda kv: -kv[1][0])
        out = []
        for w, _r in items:
            if w in FUNC_W or (accept is not None and not accept(w)):
                continue
            out.append(w)
            if len(out) >= top:
                break
        return out

    # ------------------------------------------------------------ подбор
    def weight(self, w, c, hot=None, zone_vote=None, grav=None, hz=None, breath=0.0):
        """Вес кандидата — формула генератора платы + частота + дыхание."""
        sc = 1.0 + (float(c) ** 0.5)
        if hot:
            sc += 2.0 * min(3.0, hot.get(w[:5], 0.0))
        if zone_vote is not None:
            try:
                sc += min(3.0, abs(float(zone_vote(w))))
            except Exception:
                pass
        if grav:
            sc *= 1.0 + min(5.0, grav.get(w, 0.0))
        # Hz associations are stored for feeling encoding, never lexical scoring.
        sc = max(0.1, sc)
        try:
            sc = sc ** (1.0 / (1.0 + 0.3 * max(-1.0, min(1.0, float(breath or 0)))))
        except Exception:
            pass
        return sc

    def pick(self, cands, hot=None, zone_vote=None, grav=None, hz=None, breath=0.0):
        """Взвешенный выбор из [(слово, счёт)] — как у генератора платы."""
        items, weights = [], []
        for w, c in cands:
            items.append(w)
            weights.append(self.weight(w, c, hot, zone_vote, grav, hz, breath))
        if not items:
            return ""
        tot = sum(weights)
        r = random.uniform(0, tot)
        acc = 0.0
        for w, wt in zip(items, weights):
            acc += wt
            if r <= acc:
                return w
        return items[-1]

    # ------------------------------------------------------------ сверка
    def take_inc(self, limit=4000):
        """Прирост с прошлой сверки (и очистить)."""
        with self.lock:
            keys = list(self.inc_w.keys())[:limit]
            iw = {k: self.inc_w.pop(k) for k in keys}
            bk = list(self.inc_b.keys())[:limit]
            ib = {k: self.inc_b.pop(k) for k in bk}
        return {"w": iw, "b": ib}

    def give_back(self, inc):
        """Сверка не удалась — прирост вернуть (не потерять)."""
        with self.lock:
            for k, v in (inc.get("w") or {}).items():
                r = self.inc_w.setdefault(k, [0, 0.0, 0])
                r[0] += v[0]
                r[1] += v[1]
                r[2] += v[2]
            for a, nx in (inc.get("b") or {}).items():
                d = self.inc_b.setdefault(a, {})
                for b, n in nx.items():
                    d[b] = d.get(b, 0) + n

    def add_inc(self, inc, accept=None):
        """Облако: сложить прирост платы в общий словарь."""
        n = 0
        with self.lock:
            for k, v in (inc.get("w") or {}).items():
                k = norm(k)
                if not k or (accept is not None and k not in FUNC_W and not accept(k)):
                    continue
                try:
                    dn, dhz, dhn = int(v[0]), float(v[1]), int(v[2])
                except Exception:
                    continue
                row = self.w.get(k)
                if row is None:
                    if len(self.w) >= CAP_WORDS:
                        continue
                    row = self.w[k] = [0, 0.0, 0]
                row[0] += max(0, dn)
                row[1] += dhz
                row[2] += max(0, dhn)
                self._touch(k)
                n += 1
            for a, nx in (inc.get("b") or {}).items():
                a = norm(a)
                if a not in self.w:
                    continue
                succ = self.b.setdefault(a, {})
                for b, dn in (nx or {}).items():
                    b = norm(b)
                    if b in succ or len(succ) < CAP_SUCC:
                        succ[b] = succ.get(b, 0) + max(0, int(dn or 0))
                self._touch(a)
            self.dirty = True
        return n

    def rows_since(self, since, limit=3000):
        """Облако: строки, изменившиеся после версии since (абсолютные)."""
        with self.lock:
            ch = [(r, k) for k, r in self.chg.items() if r > since]
            ch.sort()
            ch = ch[:limit]
            w = {k: self.w[k] for _r, k in ch if k in self.w}
            b = {k: self.b.get(k, {}) for _r, k in ch if k in self.b}
            last = ch[-1][0] if ch else self.rev
            more = len(ch) >= limit
        return {"rev": last, "w": w, "b": b, "more": more}

    def apply_rows(self, rows):
        """Плата: принять строки облака (абсолютные значения)."""
        with self.lock:
            for k, v in (rows.get("w") or {}).items():
                self.w[k] = [int(v[0]), float(v[1]), int(v[2])]
            for k, v in (rows.get("b") or {}).items():
                self.b[k] = {b: int(n) for b, n in (v or {}).items()}
            self.cursor = int(rows.get("rev") or self.cursor)
            self.dirty = True

    def stats(self):
        with self.lock:
            hz_n = sum(1 for r in self.w.values() if r[2])
            return {"words": len(self.w), "with_hz": hz_n,
                    "links": sum(len(v) for v in self.b.values()),
                    "rev": self.rev, "cursor": self.cursor,
                    "pending": len(self.inc_w)}
