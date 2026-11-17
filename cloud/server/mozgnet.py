# -*- coding: utf-8 -*-
# ===========================================================================
# mozgnet.py — АССОЦИАТИВНАЯ НЕЙРОСЕТЬ «МОЗГ-12» (v1.0, только stdlib)
# ---------------------------------------------------------------------------
# НАЗНАЧЕНИЕ (v2.25 платы / v2.5.0 облака):
#   Нейросеть, которая УЧИТСЯ НА МАТЕРИАЛАХ и ПИШЕТ ДЛИННЫЙ ТЕКСТ по этим
#   материалам. Никакого LLM: узлы сети — основы слов, связи — выученные
#   ассоциации (совместная встречаемость в предложениях материалов),
#   документы материалов представлены tf-idf векторами. Запрос «зажигает»
#   узлы (разрядка активации по связям), активные нейроны выбирают
#   материалы и предложения, из которых собирается связный длинный текст.
#
#   Материалы (что сеть «читает»):
#     - загруженные файлы (PDF/TXT/XLSX/PPTX/DOCX);
#     - статьи баз знаний (KB облака, личные базы устройств);
#     - диалоги и мысли устройства;
#     - ПОКАЗАНИЯ нейросети мозга (hot-темы) — тоже материал и контекст.
#
#   ПОКАЗАНИЯ (readings()): размер сети, число связей/материалов, самые
#   активные узлы последнего запроса — их облако показывает в чате и
#   использует как контекст задания.
#
# ПОТОКОБЕЗОПАСНОСТЬ: не потокобезопасна сама — снаружи держать lock
# (на плате и в облаке обёртки уже делают это). Файл состояния — JSON.
# ===========================================================================
import json
import hashlib
import math
import os
import re
import time

WORD_RE = re.compile(r"[а-яёa-z0-9]{3,}")
SENT_RE = re.compile(r"(?<=[.!?…])\s+")

# служебные слова, которые не становятся узлами сети
_STOP = set("""
и в во не что он на я с со как а то все она так его но да ты к у же вы за бы по
только ее мне было вот от меня еще нет о из ему теперь когда даже ну вдруг ли
если уже или ни быть был него до вас нибудь опять уж вам сказал ведь там потом
себя ничего ей может они тут где есть надо ней для мы тебя их чем была сам чтоб
без будто человек чего раз тоже себе под жизнь будет ж тогда кто этот говорил
того потому этого какой совсем ним здесь этом один почти мой тем чтобы нее
кажется сейчас были куда зачем сказать всех никогда сегодня можно при наконец
два об другой хоть после над больше тот через эти нас про всего них какая много
разве три эту моя впрочем хорошо свою этой перед иногда лучше чуть том нельзя
такой им более всегда конечно всю между это также очень еще это как-то
англ лат греч нем франц итал исп польск нидерл санскр
""".split())

_SUFFIXES = ("иями", "ями", "ами", "иями", "иях", "ях", "ах", "ов", "ев",
             "ий", "ые", "ая", "ое", "ии", "ие", "ой", "ый", "ом", "ем",
             "ых", "их", "ую", "юю", "ья", "ье", "ьи", "ть", "ла", "ло",
             "ли", "ет", "ут", "ют", "ит", "ат", "ят", "ся", "сь", "ны",
             "на", "ко", "ка", "и", "ы", "а", "я", "о", "е", "у", "ю", "ь")


def _stem(w):
    """Грубый стем (согласован со stemmer'ом KB облака): нейронная~нейронной~нейрон."""
    for suf in _SUFFIXES:
        if len(w) > 5 and w.endswith(suf):
            return w[: len(w) - len(suf)]
    return w


# v3.27: вопросные слова — не тема (сеть не ищет материалы про «расскажи»)
_QFILL = frozenset(_stem(w) for w in """знаешь знаете расскажи расскажите
скажи скажите такое такой какой какая какие каков сколько почему зачем
пожалуйста объясни опиши подскажи покажи думаешь считаешь работает
работают делает делают происходит называется означает бывает используется
устроен устроена""".split())
# v3.27: эхо прошлых ответов, выученных обратно в сеть, не цитируем —
# иначе сеть пересказывает саму себя (и свои прежние ошибки)
_ECHO_RE = re.compile(r"^\s*(вопрос|ответ)[^:]{0,20}:|по материалам сети|"
                      r"сеть выделяет|ключевые понятия|источники сети|"
                      r"_источник|^не знаю\. по «", re.I)


def _fragment(t):
    """v3.27: обрывок, а не предложение: «Чёрная дыра» (англ. Чёрная
    дыра».» — разрез на сокращении, непарные скобки/кавычки, мало слов."""
    t = str(t or "")
    words = [w for w in t.split() if any(ch.isalpha() for ch in w)]
    if len(words) < 4 or sum(ch.isalpha() for ch in t) < 20:
        return True
    if t.count("(") != t.count(")") or t.count("«") != t.count("»"):
        return True
    return False


def _sentences(text):
    """Разбить текст на предложения-материалы (12..280 символов;
    v3.9: было 25 — короткие мысли платы тоже материал)."""
    out = []
    for s in SENT_RE.split(re.sub(r"\s+", " ", str(text or "")).strip()):
        s = s.strip(" ;:-—*|")
        if 12 <= len(s) <= 280 and " " in s:
            out.append(s)
    return out


def _freq_z():
    """Текущий знаковый уровень сигнала плат -1..+1 или None (freqprior)."""
    try:
        import freqprior
        if freqprior.strength() <= 0:
            return None
        return freqprior.level()
    except Exception:
        return None



# v3.129: ЧЕСТНЫЙ ОТВЕТ. В сеть материалов попадает и то, что НЕ является
# знанием: мысли плат (генератор слов), мысли и пересказы своей LLM,
# фантазии воображения, речь из словаря, голые вопросы. Для ответов на
# вопросы эти материалы не берутся — только прочитанное (статьи, файлы,
# код, инструкции, интернет-ответы, подтверждённые пары).
UNTRUSTED_SRC = ("мысль", "мысли", "плата", "воображение", "речь из словаря",
                 "чтение llm", "вопрос", "фантаз")
UNTRUSTED_TITLE = ("мысль", "мысли плат", "воображение", "прочитано llm")


def honest_on():
    try:
        import store
        return bool(store.settings().get("answer_honest", True))
    except Exception:
        return True


def trusted(d):
    src = str(d.get("src") or "").lower()
    ttl = str(d.get("title") or "").lower()
    return not (src.startswith(UNTRUSTED_SRC) or ttl.startswith(UNTRUSTED_TITLE))

# ===========================================================================
# v3.146: ТЕНЗОРНЫЙ СЛОЙ СЕТИ МАТЕРИАЛОВ.
# ---------------------------------------------------------------------------
# Каждое слово (основа) — вектор смысла в пространстве D=128:
#     смысл(слово) = свой_вектор(слово) + β · контекст(слово)
#   • свой вектор — постоянный разреженный ±1 (8 ненулевых, зерно — слово);
#   • контекст — ВЫУЧЕННАЯ часть: при каждом обучении к нему прибавляются
#     свои векторы соседних слов (окно ±2) с шагом lr. Слова, которые
#     встречаются в похожем окружении, получают похожие векторы — сеть
#     находит материал не только по тем же словам, но и по близким.
#   • материал — нормированная сумма смыслов его слов с весами tf·idf плюс
#     ЧАСТОТНЫЙ КАНАЛ: уровень сигнала плат, при котором материал выучен.
# Поиск: запрос — такой же вектор; сходство со всеми материалами — одно
# умножение матрицы на вектор. Итог смешивается с точным поиском (BM25):
#     оценка = (1 − α) · BM25 + α · сходство тензора
# Материал, у которого нет ни одного слова вопроса, получает только α-долю —
# точные совпадения остаются выше, факты не путаются.
#
# ЧТО ЗАВИСИТ ОТ ПЛАТ (tensormod, «Сеть материалов», 4 группы):
#   m0 — шаг учёбы контекстов (lr), m1 — доля смыслового поиска α,
#   m2 — вес частотного канала γ, m3 — дальность разрядки активации.
# Все четыре — функции дыхания мозга, частоты плат, их разброса и воли
# мозга (tensormod.vec). Без numpy слой выключен — сеть работает как раньше.
# ===========================================================================
TN_D = 128                    # размерность смыслов
TN_H = 32768                  # корзин контекстов (хеширование основ)
TN_NNZ = 8                    # ненулевых в своём векторе слова


def _tn_np():
    try:
        import numpy as np
        return np
    except Exception:
        return None


def _tmod():
    """Множители [lr, α, γ, разрядка] от плат и дыхания | единицы."""
    try:
        import tensormod
        return [float(v) for v in tensormod.vec("Сеть материалов", 4)]
    except Exception:
        return [1.0, 1.0, 1.0, 1.0]


class TensorLayer(object):
    def __init__(self):
        self.np = _tn_np()
        self.ok = self.np is not None
        self.ctx = None               # H × D float32 — выученные контексты
        self.dm = None                # N × (D+2) — матрица материалов
        self.dm_ids = []
        self.dirty = True
        self.learned = 0
        self._rv = {}
        if self.ok:
            self.ctx = self.np.zeros((TN_H, TN_D), dtype=self.np.float32)

    # ------------------------------------------------------------ векторы
    def _bucket(self, st):
        import zlib
        return zlib.crc32(st.encode("utf-8")) % TN_H

    def rvec(self, st):
        v = self._rv.get(st)
        if v is None:
            np = self.np
            rs = np.random.RandomState(int(hashlib.md5(st.encode("utf-8")).hexdigest()[:7], 16))
            v = np.zeros(TN_D, dtype=np.float32)
            idx = rs.choice(TN_D, TN_NNZ, replace=False)
            v[idx] = rs.choice([-1.0, 1.0], TN_NNZ)
            if len(self._rv) > 200000:
                self._rv.clear()
            self._rv[st] = v
        return v

    def sem(self, st, beta=0.6):
        np = self.np
        c = self.ctx[self._bucket(st)]
        n = float(np.linalg.norm(c))
        r = self.rvec(st)
        return r + (beta * (c / n) * math.sqrt(TN_NNZ) if n > 0 else 0.0)

    # ------------------------------------------------------------ учёба
    def learn_sents(self, stem_lists):
        """Контексты слов по предложениям материала (окно ±2)."""
        if not self.ok:
            return
        lr = 0.1 * _tmod()[0]
        try:                                  # дыхание и воля мозга — в шаге
            import breath
            lr *= breath.mod("Сеть материалов", 1.0, 0.12) * breath.strength("Сеть материалов")
        except Exception:
            pass
        for ss in stem_lists:
            if len(ss) < 2:
                continue
            R = [self.rvec(st) for st in ss]
            for i, st in enumerate(ss):
                acc = None
                for j in range(max(0, i - 2), min(len(ss), i + 3)):
                    if j != i and ss[j] != st:
                        acc = R[j] if acc is None else acc + R[j]
                if acc is not None:
                    self.ctx[self._bucket(st)] += lr * acc
        self.learned += 1

    # ------------------------------------------------------------ материалы
    def _chan(self, z):
        a = max(-1.0, min(1.0, float(z))) * math.pi / 2.0
        return math.sin(a), math.cos(a)

    def doc_vec(self, tf, idf_fn, z=None):
        np = self.np
        v = np.zeros(TN_D + 2, dtype=np.float32)
        for st, c in (tf or {}).items():
            v[:TN_D] += float(c) * idf_fn(st) * self.sem(st)
        n = float(np.linalg.norm(v[:TN_D]))
        if n > 0:
            v[:TN_D] /= n
        if z is not None:
            v[TN_D], v[TN_D + 1] = self._chan(z)
        return v

    def rebuild(self, docs, idf_fn):
        if not self.ok:
            return
        np = self.np
        rows = [self.doc_vec(d.get("tf") or {}, idf_fn, d.get("z")) for d in docs]
        self.dm = np.vstack(rows) if rows else np.zeros((0, TN_D + 2), dtype=np.float32)
        self.dm_ids = [d.get("id") for d in docs]
        self.dirty = False

    def scores(self, q_stems, idf_fn, z=None):
        """Сходство запроса со всеми материалами: {id: 0..1}."""
        if not self.ok or self.dm is None or not len(self.dm_ids):
            return {}
        np = self.np
        q = self.doc_vec({st: 1 for st in q_stems}, idf_fn, None)
        if not float(np.linalg.norm(q[:TN_D])):
            return {}
        gamma = 0.0
        if z is not None:
            try:
                import freqprior
                gamma = 0.15 * min(1.0, freqprior.strength()) * _tmod()[2]
            except Exception:
                gamma = 0.0
            q[TN_D], q[TN_D + 1] = self._chan(z)
            q[TN_D:] *= gamma
        sim = self.dm @ q
        sim = np.clip(sim / (1.0 + gamma), 0.0, 1.0)
        top = np.argsort(-sim)[:64]
        return {self.dm_ids[i]: float(sim[i]) for i in top if sim[i] > 0.05}

    # ------------------------------------------------------------ диск
    def save(self, path, ctx=None):
        if not self.ok:
            return False
        try:
            tmp = path + ".tmp.npy"
            self.np.save(tmp, self.ctx if ctx is None else ctx)
            os.replace(tmp, path)
            return True
        except Exception:
            return False

    def load(self, path):
        if not self.ok or not os.path.isfile(path):
            return False
        try:
            a = self.np.load(path)
            if a.shape == (TN_H, TN_D):
                self.ctx = a.astype(self.np.float32)
                self.dirty = True
                return True
        except Exception:
            pass
        return False


def tensor_path(path):
    return str(path) + ".tensor.npy"


class MozgNet(object):
    """Ассоциативная сеть на материалах + писатель длинных текстов."""

    def __init__(self, docs_max=3000, sents_max=40000, node_max=150000):
        # v3.24.2: прежние пределы (500/3000/20000) резали сеть слишком рано:
        # узлы упирались в 20000 и readings показывали ровно 20000 — владельцу
        # это выглядело как «сеть зависла на 20000 узлов». Рост возвращён.
        self.docs_max = int(docs_max)
        self.sents_max = int(sents_max)
        self.node_max = int(node_max)
        # узлы: {стем: частота} — «нейроны»
        self.nodes = {}
        # связи: {стем: {стем2: вес}} — выученные ассоциации (биграммы)
        self.edges = {}
        # материалы: [{"id","title","src","ts","tf","n","kw"}]
        self.docs = []
        # предложения материалов: [{"d": индекс, "t": текст, "tf", "n"}]
        self.sents = []
        self.stats = {"learns": 0, "writes": 0, "queries": 0}
        self.last = {"topic": "", "hot": {}, "ts": 0.0}   # ПОКАЗАНИЯ
        self._dfc = None                                   # кэш df (v3.27)
        self.tn = TensorLayer()                            # v3.146: тензорный слой
        self._tn_ts = 0.0

    # ------------------------------ обучение ------------------------------
    def learn(self, text, title="", src="", did=None):
        """Учиться на материале. Возврат: {"doc": bool, "sents": n,
        "nodes": n, "links": n} — что нового добавлено.
        v3.9: порог 40 -> 12 знаков — короткие МЫСЛИ ПЛАТЫ («люблю
        кирпичики») тоже должны становиться материалом сети."""
        text = str(text or "")
        try:                         # v3.118: без «(англ. …)», «н. э.» → «нашей эры»
            import llm2
            text = llm2.normalize(text)
        except Exception:
            pass
        if len(text.strip()) < 12:
            return {"doc": False, "sents": 0, "nodes": 0, "links": 0}
        res = {"doc": False, "sents": 0, "nodes": 0, "links": 0}
        sents = _sentences(text)
        if not sents:
            return res
        if did is None:
            raw = ((str(title) or "")[:60] + "|" + text.strip()[:90]).encode(
                "utf-8", "replace")
            did = "h" + hashlib.md5(raw).hexdigest()[:12]
        if did and self._doc_idx(did) >= 0:
            return res                       # материал уже в сети
        tf = {}
        for s in sents:
            for w in WORD_RE.findall(s.lower()):
                if w in _STOP or len(w) > 23:
                    continue
                tf[w] = tf.get(w, 0) + 1
        stems = {}
        for w, c in tf.items():
            stems.setdefault(_stem(w), 0)
            stems[_stem(w)] += c
        if not stems:
            return res
        di = len(self.docs)
        kw = [w for w, _c in sorted(tf.items(), key=lambda kv: -kv[1])[:8]]
        doc = {"id": did, "title": (str(title) or "")[:120],
               "src": (str(src) or "")[:40],
               "ts": int(time.time()), "tf": stems,
               "n": sum(stems.values()), "kw": kw}
        zl = _freq_z()
        if zl is not None:                 # v3.85: частота плат в момент учёбы
            doc["z"] = round(zl, 3)
        self.docs.append(doc)
        res["doc"] = True
        # сеть узлов и связей
        prev = None
        tn_lists = []                      # v3.146: предложения для тензора
        for s in sents:
            ws = [w for w in WORD_RE.findall(s.lower())
                  if w not in _STOP and len(w) <= 23]
            ss = [_stem(w) for w in ws]
            tn_lists.append(ss)
            for st in ss:
                self.nodes[st] = self.nodes.get(st, 0) + 1
                res["nodes"] += 1
            for a, b in zip(ss, ss[1:]):
                if a == b:
                    continue
                nxt = self.edges.setdefault(a, {})
                nxt[b] = nxt.get(b, 0) + 1
                res["links"] += 1
            # предложение — единица выдачи (с вектором)
            stf = {}
            for st in ss:
                stf[st] = stf.get(st, 0) + 1
            if stf:
                # v3.27: id материала в самом предложении — индекс «d»
                # сдвигается при подрезке сети, а id нет
                self.sents.append({"d": di, "did": did,
                                   "t": s, "tf": stf,
                                   "n": sum(stf.values())})
                res["sents"] += 1
        self.stats["learns"] += 1
        self._dfc = None
        try:                               # v3.146: тензор учит контексты
            self.tn.learn_sents(tn_lists)
            if self.tn.ok and self.tn.dm is not None and not self.tn.dirty:
                row = self.tn.doc_vec(doc.get("tf") or {}, self._idf_fn(), doc.get("z"))
                self.tn.dm = self.tn.np.vstack([self.tn.dm, row[None, :]])
                self.tn.dm_ids.append(doc.get("id"))
        except Exception:
            self.tn.dirty = True
        self._trim()
        return res

    # ------------------- v3.145: обучение «как у LLM» -------------------
    def doc_text(self, di, limit=1500):
        """Текст материала, собранный из его предложений."""
        did = self.docs[di].get("id") if 0 <= di < len(self.docs) else None
        out = []
        n = 0
        for s in self.sents:
            if s.get("did") == did:
                out.append(s.get("t") or "")
                n += len(out[-1])
                if n >= limit:
                    break
        return " ".join(out)

    def forget(self, pred):
        """Забыть материалы, для которых pred(doc, text) истинно: убрать сам
        материал и его предложения, затем пересчитать узлы и связи по
        оставшемуся (rebuild_graph). Возврат: сколько забыто."""
        texts = {}
        for s_ in self.sents:              # один проход: id -> текст
            did_ = s_.get("did")
            cur = texts.get(did_, "")
            if len(cur) < 1500:
                texts[did_] = (cur + " " + str(s_.get("t") or "")).strip()
        drop = set()
        for d in self.docs:
            try:
                if pred(d, texts.get(d.get("id"), "")):
                    drop.add(d.get("id"))
            except Exception:
                continue
        if not drop:
            return 0
        self.docs = [d for d in self.docs if d.get("id") not in drop]
        pos = {d.get("id"): i for i, d in enumerate(self.docs)}
        kept = []
        for s in self.sents:
            i = pos.get(s.get("did"))
            if i is not None:
                s["d"] = i
                kept.append(s)
        self.sents = kept
        self.rebuild_graph()
        self._dfc = None
        self.tn.dirty = True
        return len(drop)

    def rebuild_graph(self):
        """Узлы и связи заново — по предложениям, которые остались в сети
        (после «забывания» в графе не остаётся следов шума)."""
        nodes, edges = {}, {}
        for s in self.sents:
            ss = [_stem(w) for w in WORD_RE.findall(str(s.get("t") or "").lower())
                  if w not in _STOP and len(w) <= 23]
            for st in ss:
                nodes[st] = nodes.get(st, 0) + 1
            for a_, b_ in zip(ss, ss[1:]):
                if a_ != b_:
                    nx = edges.setdefault(a_, {})
                    nx[b_] = nx.get(b_, 0) + 1
        self.nodes, self.edges = nodes, edges

    def self_test(self, n=100, k=3, seed=12):
        """ПРОВЕРКА как у LLM (отложенная выборка): для n материалов сети
        запрос = заголовок (или ключевые слова), сеть должна найти этот
        материал в первых k. Возврат: {n, hit, acc} — доля найденных."""
        import random as _r
        idx = [i for i, d in enumerate(self.docs) if trusted(d)]
        if not idx:
            return {"n": 0, "hit": 0, "acc": None}
        rnd = _r.Random(seed)
        rnd.shuffle(idx)
        idx = idx[:int(n)]
        hit = 0
        for i in idx:
            d = self.docs[i]
            q = str(d.get("title") or "").strip()
            if len(WORD_RE.findall(q.lower())) < 2:
                q = " ".join((d.get("kw") or [])[:5])
            got = self._retrieve(q, k=k)
            if any(x.get("id") == d.get("id") for _s, x in got):
                hit += 1
        return {"n": len(idx), "hit": hit, "acc": round(hit / float(len(idx)), 3)}

    def _doc_idx(self, did):
        for i, d in enumerate(self.docs):
            if d.get("id") == did:
                return i
        return -1

    def apply_limits(self, docs_max=None, sents_max=None, node_max=None):
        """v3.24.2: поменять пределы на живой сети (настройки панели:
        net_docs_max / net_sents_max / net_nodes_max). Дёшево: без изменений
        ничего не делает; при уменьшении сразу подрезает лишнее."""
        changed = False
        for name, val in (("docs_max", docs_max), ("sents_max", sents_max),
                          ("node_max", node_max)):
            if val is None:
                continue
            try:
                val = int(val)
            except (TypeError, ValueError):
                continue
            if val >= 1 and val != getattr(self, name):
                setattr(self, name, val)
                changed = True
        if changed:
            self._trim()
        return changed

    def _trim(self):
        """Ограничить рост сети: материалы и предложения — LRU по времени.
        Узлы чистятся по редкости ТОЛЬКО при превышении node_max (v3.24.2:
        потолок поднят с 20000 до 150000, настраивается — net_nodes_max)."""
        if len(self.docs) > self.docs_max:
            # v3.27: БАГФИКС — раньше после сортировки и обрезки индексы
            # «d» у предложений НЕ перевязывались: предложения указывали на
            # чужие материалы, и ответ сети подписывался не теми статьями.
            # Теперь перевязываем по id материала.
            old_ids = [d.get("id") for d in self.docs]
            for s in self.sents:
                if not s.get("did"):
                    di = s.get("d")
                    if isinstance(di, int) and 0 <= di < len(old_ids):
                        s["did"] = old_ids[di]
            self.docs.sort(key=lambda d: d.get("ts", 0))
            del self.docs[:-self.docs_max]
            pos = {d.get("id"): i for i, d in enumerate(self.docs)}
            kept = []
            for s in self.sents:
                i = pos.get(s.get("did"))
                if i is not None:
                    s["d"] = i
                    kept.append(s)
            self.sents = kept
            self._dfc = None
            self.tn.dirty = True
        if len(self.sents) > self.sents_max:
            del self.sents[: len(self.sents) - self.sents_max]
        if len(self.nodes) > self.node_max:
            rare = sorted(self.nodes.items(), key=lambda kv: kv[1])
            for k, _v in rare[: len(self.nodes) - self.node_max]:
                self.nodes.pop(k, None)
                self.edges.pop(k, None)

    # --------------------------- tf-idf и поиск ---------------------------
    def _df(self):
        """v3.27: df всех стемов одним проходом, кэш до следующего learn/
        подрезки (раньше _idf пробегал ВСЕ материалы на каждый стем каждого
        предложения — ответ сети на большой базе думал минутами)."""
        dfc = getattr(self, "_dfc", None)
        if dfc is None or dfc[0] != len(self.docs):
            df = {}
            for d in self.docs:
                for st in d.get("tf") or ():
                    df[st] = df.get(st, 0) + 1
            dfc = self._dfc = (len(self.docs), df)
        return dfc[1]

    def _idf(self, st):
        n_docs = max(1, len(self.docs))
        in_docs = self._df().get(st, 0)
        return math.log(1.0 + n_docs / (1.0 + in_docs)) + 0.35

    def _vec(self, tf, idf=True):
        n = float(sum(tf.values())) or 1.0
        return {st: (c / n) * (self._idf(st) if idf else 1.0)
                for st, c in tf.items()}

    @staticmethod
    def _cos(a, b):
        if not a or not b:
            return 0.0
        if len(a) > len(b):
            a, b = b, a
        dot = 0.0
        for st, v in a.items():
            bv = b.get(st)
            if bv:
                dot += v * bv
        na = math.sqrt(sum(v * v for v in a.values()))
        nb = math.sqrt(sum(v * v for v in b.values()))
        if na <= 0 or nb <= 0:
            return 0.0
        return dot / (na * nb)

    def retrieve(self, query, k=6, extra=None):
        out = self._retrieve(query, k=k, extra=extra)
        if honest_on():             # v3.129: честно — без мыслей плат и фантазий
            out = [(sc, d) for sc, d in out if trusted(d)]
        return out

    def _idf_fn(self):
        df = self._df()
        nd = max(1, len(self.docs))

        def f(st):
            d = df.get(st, 0)
            return math.log(1.0 + (nd - d + 0.5) / (d + 0.5)) if d else 0.0
        return f

    def tensor_ready(self):
        """Матрица материалов тензора актуальна (перестройка не чаще 15 с)."""
        tn = self.tn
        if not tn.ok:
            return False
        if (tn.dirty and (tn.dm is None or time.time() - self._tn_ts > 15.0)) or \
                time.time() - self._tn_ts > 600.0:   # раз в 10 мин — свежие idf и смыслы
            tn.rebuild(self.docs, self._idf_fn())
            self._tn_ts = time.time()
        return tn.dm is not None

    def _retrieve(self, query, k=6, extra=None):
        """v3.146: точный поиск (BM25) + смысловой (тензор), смесь по α."""
        exact = self._retrieve_bm25(query, k=max(k * 3, 12), extra=extra)
        try:
            if not self.tensor_ready():
                return exact[:k]
            m = _tmod()
            alpha = max(0.05, min(0.6, 0.25 * m[1]))
            qs = list(self._query_stems(query, extra).keys())
            sims = self.tn.scores(qs, self._idf_fn(), _freq_z())
            if not sims:
                return exact[:k]
            byid = {d.get("id"): d for d in self.docs}
            comb = {}
            for sc, d in exact:
                comb[d.get("id")] = [(1.0 - alpha) * sc, d]
            for did, sm in sims.items():
                if did in comb:
                    comb[did][0] += alpha * sm
                elif did in byid:
                    comb[did] = [alpha * sm, byid[did]]
            out = sorted(((v[0], v[1]) for v in comb.values()), key=lambda x: -x[0])
            self.stats["tensor_q"] = int(self.stats.get("tensor_q") or 0) + 1
            return [(sc, d) for sc, d in out[:k] if sc > 0.001]
        except Exception:
            return exact[:k]

    def _retrieve_bm25(self, query, k=6, extra=None):
        """Лучшие материалы по запросу: [(score, doc)]. query+extra — тексты.
        v3.27: BM25 только по словам запроса (раньше для КАЖДОГО материала
        пересчитывался весь tf-idf-вектор: на полной сети 6 с под общей
        блокировкой — облако «висло» после вопроса). Шкала 0..1."""
        qs = self._query_stems(query, extra)
        if not qs or not self.docs:
            return []
        df = self._df()
        nd = len(self.docs)
        idf = {st: math.log(1.0 + (nd - df.get(st, 0) + 0.5) /
                            (df.get(st, 0) + 0.5))
               for st in qs if df.get(st, 0)}
        if not idf:
            return []
        avg = (sum(d.get("n") or 0 for d in self.docs) / float(nd)) or 1.0
        top = sum(idf.values()) * 2.2 or 1.0
        out = []
        for d in self.docs:
            tf = d.get("tf") or {}
            sc = 0.0
            norm = None
            for st, w in idf.items():
                c = tf.get(st)
                if c:
                    if norm is None:
                        norm = 1.2 * (0.25 + 0.75 * (d.get("n") or 1) / avg)
                    sc += w * c * 2.2 / (c + norm)
            if sc > 0:
                out.append((sc / top, d))
        out.sort(key=lambda x: -x[0])
        # v3.85: опора на частоту — материал, выученный при похожем сигнале
        # плат (или созвучный ему по таблице «уровень ↔ слова»), чуть выше
        zl = _freq_z()
        if zl is not None and out:
            try:
                import freqprior
                s_ = min(1.0, freqprior.strength())
                top_ = []
                for sc, d in out[:max(3 * k, 12)]:
                    if d.get("z") is not None:
                        b = ((1.0 - abs(float(d["z"]) - zl)) - 0.5) * 2.0
                    else:
                        b = freqprior.bonus((d.get("title") or "") + " " +
                                            " ".join(d.get("kw") or []), z=zl)
                    top_.append((sc * (1.0 + 0.15 * s_ * b), d))
                out = sorted(top_, key=lambda x: -x[0]) + out[max(3 * k, 12):]
            except Exception:
                pass
        return [(s, d) for s, d in out[:k] if s > 0.001]

    def _query_stems(self, *texts):
        qs = {}
        for t in texts:
            if not t:
                continue
            for w in WORD_RE.findall(str(t).lower()):
                if w in _STOP or len(w) > 23:
                    continue
                qs[_stem(w)] = 1
        return qs

    # ------------------------- разрядка активации -------------------------
    def spread(self, seeds, depth=2, decay=0.55):
        """Разрядка активации: запрос зажигает узлы, активация бежит по
        выученным связям. Возврат: {стем: активация} (топ ~40)."""
        act = {s: 1.0 for s in seeds if s in self.nodes or not self.nodes}
        if not self.nodes:
            return act
        try:                       # v3.132: дыхание — дальность разрядки ±12 %
            import breath as _brm
            decay = max(0.3, min(0.8, _brm.mod("Сеть материалов", decay, 0.12)))
        except Exception:
            pass
        try:                       # v3.146: тензор модуляции (платы + дыхание)
            decay = max(0.3, min(0.85, decay * _tmod()[3]))
        except Exception:
            pass
        zl = _freq_z()             # v3.85: сильный сигнал — активация бежит дальше
        if zl is not None:
            decay = max(0.35, min(0.75, decay * (1.0 + 0.2 * zl)))
        frontier = {s: 1.0 for s in seeds if s in self.edges}
        for _depth in range(int(depth)):
            nxt = {}
            for st, a in frontier.items():
                links = self.edges.get(st) or {}
                total = float(sum(links.values())) or 1.0
                top = sorted(links.items(), key=lambda kv: -kv[1])[:12]
                for st2, w in top:
                    add = a * (w / total) * decay * 2.0
                    if act.get(st2, 0.0) < add:
                        nxt[st2] = max(nxt.get(st2, 0.0), add)
            for st, a in nxt.items():
                act[st] = max(act.get(st, 0.0), a)
            frontier = {s: a for s, a in nxt.items() if s in self.edges}
        return act

    def _hot_list(self, act, top=12):
        return [st for st, _a in sorted(act.items(), key=lambda kv: -kv[1])
                if len(st) >= 4][:top]

    # --------------------------- писатель текста --------------------------
    # v3.12: ЗАГОТОВЛЕННЫЕ ФРАЗЫ УБРАНЫ (заявка владельца «убери все
    # автоматические ответы»): никаких выдуманных зачинов, связок и
    # выводов. Текст собирается ТОЛЬКО из предложений материалов + честных
    # пометок (что за тема, какие узлы горят, из каких источников).

    def write(self, topic, words=340, extra=None, seed=0, min_chars=0):
        """ДЛИННЫЙ текст по материалам: заголовок, 3-6 абзацев из
        релевантных предложений материалов, честные пометки сети.
        extra — дополнительный контекст задания (мысли/показания устройства).
        v3.12: заготовленных фраз нет — сборка только из материалов.
        seed оставлен для совместимости вызовов (раньше влиял на выбор
        шаблонных связок — теперь выбор детерминирован материалами).
        v3.15: min_chars — режим «длинно» (заявка владельца: «ответы
        очень короткие, сделай минимум 1500 знаков»): если собранный
        текст короче, сеть ДОБИРАЕТ предложения дальше по списку
        релевантности (те же материалы, без повторов, ничего
        выдуманного); если и материалов мало — честная пометка."""
        self.stats["writes"] += 1
        self.stats["queries"] += 1
        qs = self._query_stems(topic, extra)
        act = self.spread(qs)
        self.last = {"topic": str(topic or "")[:120],
                     "hot": self._hot_list(act),
                     "ts": time.time()}
        if not self.docs or not qs:
            return ("Материалов по теме пока нет: сеть только что создана. "
                    "Загрузите файлы, дайте поискать в облаке или пообщайтесь "
                    "со мной — и после обучения я напишу длинный текст.")
        # поиск материалов и предложений
        ranked = self.retrieve(topic, k=6, extra=extra)
        if not ranked:                       # по активациям — смежное
            ranked = self.retrieve(" ".join(self._hot_list(act, 8) or
                                            list(qs)), k=6)
        if not ranked:
            return ("В моих материалах не нашлось ничего по запросу «%s». "
                    "Узлы сети зажглись, но документы не откликнулись — "
                    "нужно больше материалов по этой теме." % str(topic)[:80])
        # ---------- v3.27: сборка «как у LLM» по НАЙДЕННЫМ материалам -------
        # Раньше предложения набирались по всей сети (материал давал лишь
        # +0.15), и в ответ попадал «салат» из чужих статей + служебные
        # строки «Сеть выделяет узлы…», «Ключевые понятия…». Теперь:
        # 1) кандидаты — предложения лучших материалов (чужие — только при
        #    почти полном совпадении с вопросом);
        # 2) каждое предложение обязано содержать слова вопроса (idf-вес);
        # 3) эхо прошлых ответов («Вопрос: … Ответ: …») не цитируется;
        # 4) абзац = один материал, предложения в порядке ТЕКСТА, а не
        #    вперемешку; подписаны только реально использованные источники;
        # 5) ничего по теме — честное «не нашлось» (мозг идёт дальше).
        df = self._df()
        nd = max(1, len(self.docs))
        q_idf = {st: math.log(1.0 + nd / (1.0 + df.get(st, 0))) + 0.35
                 for st in qs if st not in _QFILL}
        if not q_idf:
            q_idf = {st: 1.0 for st in qs}
        sum_q = sum(q_idf.values()) or 1.0
        top_ids = {}
        for rk, (_sc, d) in enumerate(ranked):
            w = 0.5 if str(d.get("src") or "").startswith(
                ("диалог", "вопрос")) else 1.0
            top_ids[d.get("id")] = (rk, w)
        id_by_idx = [d.get("id") for d in self.docs]
        hot_set = set(self.last["hot"])
        # v3.27: заданные ВОПРОСЫ (мозг сохраняет их «материалом» при «не
        # знаю») ответом не бывают: сеть повторяла человеку его же вопрос
        q_docs = {d.get("id") for d in self.docs
                  if str(d.get("src") or "").startswith("вопрос")}
        cands = []
        for si, s in enumerate(self.sents):
            stf = s.get("tf") or {}
            cov = sum(w for st, w in q_idf.items() if st in stf) / sum_q
            st_t = (s.get("t") or "").rstrip()
            if cov <= 0 or _ECHO_RE.search(st_t) or _fragment(st_t) or \
                    st_t.endswith("?") or s.get("did") in q_docs:
                continue
            did = s.get("did")
            if did:
                rk = top_ids.get(did)
            else:
                # предложение из старой сети (до v3.27): его индекс «d» мог
                # уехать на чужой материал при подрезке — привязке не
                # верим: берём только при почти полном совпадении и не
                # подписываем им источник
                rk, did = None, None
            if rk is None and cov < 0.75:
                continue
            rank, w = rk if rk else (9, 0.6)
            act_sc = sum(min(1.0, act.get(st, 0.0)) for st in stf
                         if st in hot_set) / max(1, len(stf))
            sc = (0.65 * cov + 0.25 / (1.0 + rank) + 0.10 * act_sc) * w
            cands.append((sc, si, did, cov))
        if not cands or max(c[3] for c in cands) < \
                (0.5 if len(q_idf) >= 2 else 0.01):
            return ("В моих материалах не нашлось ничего по запросу «%s». "
                    "Узлы сети зажглись, но предложений именно об этом нет "
                    "— нужно больше материалов по этой теме."
                    % str(topic)[:80])
        # предложение, где из вопроса только «работает», — не ответ про
        # транзистор: держим планку относительно лучшего совпадения
        # (самое редкое слово вопроса обязано быть в предложении, если оно
        # не покрывает вопрос почти целиком)
        best_cov = max(c[3] for c in cands)
        if len(q_idf) >= 2:
            floor = max(0.34, 0.6 * best_cov)
            # ключевое слово — то, что в НАЗВАНИЯХ лучших материалов
            # («транзистор»), иначе самое редкое
            tstems = set()
            for _sc, d in ranked[:3]:
                tstems.update(_stem(w) for w in WORD_RE.findall(
                    str(d.get("title") or "").lower()))
            in_t = [st for st in q_idf if st in tstems]
            key_st = max(in_t or list(q_idf), key=lambda st: q_idf[st])
            cands = [c for c in cands if c[3] >= floor and
                     (c[3] >= 0.75 or key_st in self.sents[c[1]]["tf"])]
            if not cands:
                return ("В моих материалах не нашлось ничего по запросу "
                        "«%s». Узлы сети зажглись, но предложений именно "
                        "об этом нет — нужно больше материалов по этой теме."
                        % str(topic)[:80])
        cands.sort(key=lambda x: -x[0])
        try:                        # v3.73: опора на частоту плат (±0.08)
            import freqprior
            zl = freqprior.level()
            if zl is not None and freqprior.strength() > 0:
                top = cands[:80]
                top = [(sc_ + 0.08 * freqprior.bonus(self.sents[si_]["t"], z=zl),
                        si_, d_, cv_) for sc_, si_, d_, cv_ in top]
                cands = sorted(top, key=lambda x: -x[0]) + cands[80:]
        except Exception:
            pass
        need = max(4, int(words) // 22)
        picked, seen = [], set()
        for sc, si, did, _cov in cands:
            key = self.sents[si]["t"][:48].lower()
            if key in seen:
                continue
            seen.add(key)
            picked.append((sc, si, did))
            if len(picked) >= need:
                break
        if not picked:
            return ("По запросу «%s» материалы нашлись, но годных предложений "
                    "мало — напишите тему точнее или дайте мне материалов."
                    % str(topic)[:80])
        doc_best = {}
        for sc, si, did in picked:
            doc_best[did] = max(doc_best.get(did, 0.0), sc)
        title_of = {d.get("id"): d.get("title") for d in self.docs}
        parts, used_titles, wcount = [], [], 0
        for did in sorted(doc_best, key=lambda x: -doc_best[x]):
            para = [self._sent_clean(self.sents[si]["t"])
                    for si in sorted(si for _sc, si, d2 in picked
                                     if d2 == did)]
            para = [p for p in para if p]
            if not para:
                continue
            parts.append(" ".join(para))
            ttl = (title_of.get(did) or "")[:60]
            if ttl and ttl not in used_titles:
                used_titles.append(ttl)
            wcount += sum(len(p.split()) for p in para)
            if wcount >= int(words) * 1.25:
                break
        per_para = 3

        def _join():
            foot = ("\n\n_Источники сети: %s_" % ", ".join(
                "«%s»" % t for t in used_titles[:4])) if used_titles else ""
            return "\n\n".join(p.strip() for p in parts if p.strip()) + foot

        out = _join()
        # ---------- v3.15: дотяпивание до min_chars (режим «длинно») --------
        # Ничего выдуманного: добавляем только реальные предложения
        # материалов по теме (те же кандидаты), не вошедшие в первую сборку.
        if min_chars and int(min_chars) > len(out):
            target = int(min_chars)
            para2 = []
            for _sc, si, did, _cov in cands:
                if len(out) >= target:
                    break
                s = self.sents[si]
                key = s["t"][:48].lower()
                if key in seen:
                    continue
                seen.add(key)
                clean = self._sent_clean(s["t"])
                if clean:
                    para2.append(clean)
                    ttl = (title_of.get(did) or "")[:60]
                    if ttl and ttl not in used_titles:
                        used_titles.append(ttl)
                if len(para2) >= per_para:
                    parts.append("Из материалов далее: %s" % " ".join(para2))
                    out = _join()
                    para2 = []
            if para2:
                parts.append("Из материалов далее: %s" % " ".join(para2))
                out = _join()
            if int(min_chars) > len(out):
                out += ("\n\nВ материалах сети по теме пока меньше текста, "
                        "чем нужно на полный длинный ответ (%d знаков из "
                        "%d): дай статей или файлов — и продолжу."
                        % (len(out), int(min_chars)))
        return out

    @staticmethod
    def _sent_clean(s):
        """Предложение материала в чистом виде: скобки/мусор, регистр, точка."""
        s = re.sub(r"\[[^\]]{0,60}\]", "", str(s or ""))
        s = re.sub(r"={2,}[^=]{0,120}={2,}", " ", s)       # «== Раздел ==»
        s = re.sub("[\u0301\u0300\u00ad]", "", s)           # ударения
        s = re.sub(r"^[^()]{0,40}\)\s*[,;]?\s*", "", s)     # хвост «(англ.»
        s = re.sub(r"\s+", " ", s).strip(" ;:-—*|«» ")
        if not s:
            return ""
        s = s[0].upper() + s[1:]
        if s[-1] not in ".!?…":
            s += "."
        return s

    # ------------------------------ показания -----------------------------
    def readings(self, top=12):
        """ПОКАЗАНИЯ НЕЙРОСЕТИ: размеры, материал, самые активные узлы.
        Облако и UI показывают это как «что сеть сейчас думает»."""
        act = self.last.get("hot") or []
        import heapq                     # v3.105: без сортировки всех узлов
        top_terms = [st for st, _c in
                     heapq.nlargest(top, self.nodes.items(),
                                    key=lambda kv: kv[1])]
        return {
            "nodes": len(self.nodes),
            "links": sum(len(v) for v in self.edges.values()),
            "docs": len(self.docs),
            "sents": len(self.sents),
            "learns": self.stats.get("learns", 0),
            "writes": self.stats.get("writes", 0),
            "topic": self.last.get("topic", ""),
            "hot": act[:top],
            "top": top_terms[:top],
            "titles": [d.get("title", "")[:60] for d in self.docs[-5:]],
            "nodes_max": self.node_max, "docs_max": self.docs_max,   # v3.105
            "sents_max": self.sents_max,
            "tensor": {"on": bool(self.tn.ok), "dim": TN_D,
                       "docs": 0 if self.tn.dm is None else int(self.tn.dm.shape[0]),
                       "learned": self.tn.learned,
                       "queries": int(self.stats.get("tensor_q") or 0),
                       "mod": [round(v, 3) for v in _tmod()]},
        }

    # --------------------------- сохранение -------------------------------
    def save(self, path):
        try:
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"v": 1,
                           "nodes": self.nodes,
                           "edges": self.edges,
                           "docs": self.docs,
                           "sents": self.sents[-self.sents_max:],
                           "stats": self.stats,
                           "last": self.last}, f, ensure_ascii=False)
            os.replace(tmp, path)
            self.tn.save(tensor_path(path))
            return True
        except Exception:
            return False

    def snapshot(self):
        """v3.27: СНИМОК состояния для сохранения — быстрые копии структур
        (секунда под блокировкой), а долгая запись JSON на диск (десятки
        секунд на полной сети) идёт уже БЕЗ блокировки — облако не стоит."""
        return {"v": 1,
                "nodes": dict(self.nodes),
                "edges": {k: dict(v) for k, v in self.edges.items()},
                "docs": list(self.docs),
                "sents": list(self.sents[-self.sents_max:]),
                "stats": dict(self.stats),
                "last": dict(self.last),
                "_tensor": (self.tn.ctx.copy() if self.tn.ok else None)}

    @staticmethod
    def save_snapshot(path, snap):
        """Записать снимок (вне блокировки): атомарно, через .tmp."""
        ctx = snap.pop("_tensor", None) if isinstance(snap, dict) else None
        if ctx is not None:
            try:
                TensorLayer().save(tensor_path(path), ctx)
            except Exception:
                pass
        try:
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(snap, f, ensure_ascii=False,
                          separators=(",", ":"))
            os.replace(tmp, path)
            return True
        except Exception:
            return False

    def load(self, path):
        try:
            if not os.path.isfile(path):
                return False
            with open(path, encoding="utf-8") as f:
                st = json.load(f)
            self.nodes = st.get("nodes") or {}
            self.edges = st.get("edges") or {}
            self.docs = st.get("docs") or []
            self.sents = st.get("sents") or []
            # v3.118: пометки языка — не узлы сети; предложения — чистые
            for k_ in [k_ for k_ in self.nodes if k_ in _STOP]:
                self.nodes.pop(k_, None)
                self.edges.pop(k_, None)
            for k_, e_ in list(self.edges.items()):
                if isinstance(e_, dict):
                    for j_ in [j_ for j_ in e_ if j_ in _STOP]:
                        e_.pop(j_, None)
            try:
                import llm2
                for s_ in self.sents:
                    if isinstance(s_, dict) and s_.get("t") and \
                            ("англ" in s_["t"] or " н. " in s_["t"] or
                             "лат." in s_["t"] or "греч" in s_["t"]):
                        s_["t"] = llm2.normalize(s_["t"])
            except Exception:
                pass
            self.stats.update(st.get("stats") or {})
            self.last = st.get("last") or self.last
            self._dfc = None
            self.tn.dirty = True
            if not self.tn.load(tensor_path(path)) and self.tn.ok:
                # тензора на диске нет (первый запуск v3.146) — выучить
                # контексты по сохранённым предложениям
                lists = [[_stem(w) for w in WORD_RE.findall(str(s_.get("t") or "").lower())
                          if w not in _STOP and len(w) <= 23] for s_ in self.sents]
                self.tn.learn_sents(lists)
            return True
        except Exception:
            return False
