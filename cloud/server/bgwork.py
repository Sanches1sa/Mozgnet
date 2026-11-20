"""bgwork.py — фоновые очереди и отложенное сохранение (v3.186.2).

Зачем. Плата шлёт /api/link каждые 5 с и /api/memory/save каждые ~30 с.
Раньше облако прямо в запросе учило на них генератор, словарь, сеть и
каждый раз переписывало большие JSON (genassoc.json — 7,5 МБ) под общей
блокировкой. Плата не дожидалась ответа и слала снова, а брошенные запросы
продолжали работать: очередь за блокировкой росла на ~3500 с в час, потоков
становилось тысячи, ответы платам шли по 10–20 часов, тормозило всё облако.

Теперь:
  * Lane — одна фоновая очередь с одним потоком. submit(key, fn, ...) с
    coalesce=True заменяет ещё не начатую задачу с тем же ключом (от платы
    важен только свежий пакет памяти); очередь ограничена maxlen — самые
    старые задачи выбрасываются со счётчиком dropped.
  * debounce(name, now_fn, later_fn, gap) — «сохранить не чаще раза в gap
    секунд»: если с прошлого сохранения прошло мало, сохранение откладывается
    и выполнится фоном (later_fn сама берёт свою блокировку). При выходе из
    процесса отложенное дописывается (atexit).
"""
import atexit
import collections
import threading
import time


class Lane:
    def __init__(self, name, maxlen=200):
        self.name = name
        self.maxlen = maxlen
        self._q = collections.OrderedDict()   # key -> (fn, args, kw, ts)
        self._cv = threading.Condition()
        self._seq = 0
        self._th = None
        self.done = 0
        self.dropped = 0
        self.replaced = 0
        self.errors = 0
        self.last_err = ""
        self.busy_since = 0.0
        self.last_dt = 0.0

    def _start(self):
        if self._th is None or not self._th.is_alive():
            self._th = threading.Thread(target=self._run, daemon=True,
                                        name="bg-" + self.name)
            self._th.start()

    def submit(self, key, fn, *args, coalesce=False, **kw):
        with self._cv:
            if key is None or not coalesce:
                self._seq += 1
                key = ("_", self._seq)
            elif key in self._q:
                self.replaced += 1
                del self._q[key]
            self._q[key] = (fn, args, kw, time.time())
            while len(self._q) > self.maxlen:
                self._q.popitem(last=False)
                self.dropped += 1
            self._start()
            self._cv.notify()
        return True

    def _run(self):
        while True:
            with self._cv:
                while not self._q:
                    self._cv.wait(30.0)
                _key, (fn, args, kw, _ts) = self._q.popitem(last=False)
            self.busy_since = time.time()
            try:
                fn(*args, **kw)
            except Exception as e:
                self.errors += 1
                self.last_err = "%s: %s" % (type(e).__name__, str(e)[:160])
            self.last_dt = time.time() - self.busy_since
            self.busy_since = 0.0
            self.done += 1

    def stats(self):
        with self._cv:
            n = len(self._q)
        return {"queue": n, "done": self.done, "dropped": self.dropped,
                "replaced": self.replaced, "errors": self.errors,
                "last_err": self.last_err, "last_sec": round(self.last_dt, 2),
                "busy_sec": round(time.time() - self.busy_since, 1)
                if self.busy_since else 0.0}


_LANES = {}
_LANES_LOCK = threading.Lock()


def lane(name, maxlen=200):
    with _LANES_LOCK:
        ln = _LANES.get(name)
        if ln is None:
            ln = _LANES[name] = Lane(name, maxlen)
        return ln


def stats():
    with _LANES_LOCK:
        items = list(_LANES.items())
    return {k: v.stats() for k, v in items}


# --------------------------- отложенное сохранение ---------------------------
_DEB = {}            # name -> {"last": ts, "pending": bool, "later": fn, "gap": s}
_DEB_LOCK = threading.Lock()
_FLUSHER = [None]


def debounce(name, now_fn, later_fn, gap=60.0):
    """Сохранить сейчас (now_fn, вызывающий уже держит свою блокировку), если
    прошлое сохранение было ≥ gap с назад; иначе — отложить: фоновый поток
    позовёт later_fn (она сама берёт блокировку)."""
    now = time.time()
    with _DEB_LOCK:
        d = _DEB.setdefault(name, {"last": 0.0, "pending": False})
        d["later"], d["gap"] = later_fn, float(gap)
        due = now - d["last"] >= gap
        if due:
            d["last"], d["pending"] = now, False
        else:
            d["pending"] = True
            _start_flusher()
    if due:
        now_fn()


def _start_flusher():
    if _FLUSHER[0] is None or not _FLUSHER[0].is_alive():
        _FLUSHER[0] = threading.Thread(target=_flush_loop, daemon=True,
                                       name="bg-save")
        _FLUSHER[0].start()


def _flush_loop():
    while True:
        time.sleep(5.0)
        flush(only_due=True)


def flush(only_due=False):
    now = time.time()
    todo = []
    with _DEB_LOCK:
        for name, d in _DEB.items():
            if d.get("pending") and (not only_due or now - d["last"] >= d["gap"]):
                d["pending"], d["last"] = False, now
                todo.append(d["later"])
    for fn in todo:
        try:
            fn()
        except Exception:
            pass
    return len(todo)


atexit.register(flush)
