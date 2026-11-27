"""Wall-clock occupancy of actual local LLM calls; no CPU/GPU utilization claim.
Intervals are unioned across overlapping/nested calls, not summed. No weights
or model configuration are changed by measurement.
"""
from contextlib import contextmanager
from functools import wraps
import threading
import time

WINDOW_SECONDS = 30.0
_LOCK = threading.RLock()
_ACTIVE = {}
_DONE = []
_STARTED = time.monotonic()


def _union(intervals):
    total = 0.0
    end = None
    for start, stop in sorted(intervals):
        if stop <= start:
            continue
        uncovered = start if end is None else max(start, end)
        total += max(0.0, stop - uncovered)
        end = stop if end is None else max(end, stop)
    return total


def snapshot():
    now = time.monotonic()
    start = max(_STARTED, now - WINDOW_SECONDS)
    with _LOCK:
        _DONE[:] = [r for r in _DONE if r['end'] > now - WINDOW_SECONDS]
        active = list(_ACTIVE.values())
        rows = list(_DONE) + [dict(r, end=now) for r in active]
    window = max(1e-9, now - start)
    def occupied(zone=None):
        intervals = [(max(start, r['start']), min(now, r['end'])) for r in rows
                     if zone is None or zone in r['zones']]
        return min(1.0, max(0.0, _union(intervals) / window))
    zones = sorted({z for r in rows for z in r['zones']})
    return dict(busy=bool(active), window_seconds=round(window, 1),
                window_limit_seconds=WINDOW_SECONDS, duty=occupied(),
                operations=[dict(kind=r['kind'], zones=r['zones'],
                                 elapsed_seconds=round(max(0, now-r['start']), 1)) for r in active],
                zones={z:dict(busy=any(z in r['zones'] for r in active), duty=occupied(z)) for z in zones},
                metric='Доля времени в вызовах LLM за последние 30 с; не загрузка CPU/GPU и не качество ответа')


@contextmanager
def operation(kind, zones):
    token = object()
    row = dict(kind=kind, zones=list(dict.fromkeys(zones)), start=time.monotonic())
    with _LOCK:
        _ACTIVE[token] = row
    try:
        yield
    finally:
        with _LOCK:
            finished = _ACTIVE.pop(token, None)
            if finished is not None:
                now = time.monotonic()
                _DONE[:] = [r for r in _DONE if r['end'] > now-WINDOW_SECONDS]
                _DONE.append(dict(finished, end=now))


def tracked(kind, zone=None):
    """Instrument a real model method, including all direct agent/code paths."""
    def decorate(fn):
        @wraps(fn)
        def wrapped(self, *args, **kwargs):
            zones = []
            try:
                import llm_zones
                if llm_zones.enabled():
                    text = str(args[0] if args and isinstance(args[0], str) else
                               kwargs.get('prompt') or kwargs.get('question') or '')
                    zones = [llm_zones.route(text, zone=zone)]
            except Exception:
                pass  # Telemetry must never prevent a model call.
            try:
                with operation(kind, zones):
                    return fn(self, *args, **kwargs)
            finally:
                try:
                    llm_zones.touch(zones)
                except Exception:
                    pass
        return wrapped
    return decorate
