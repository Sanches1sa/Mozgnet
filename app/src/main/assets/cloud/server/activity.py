"""Request-scoped server activity, isolated by request ID and panel owner."""
import re
import threading
import time

_LOCK = threading.Lock()
_LOCAL = threading.local()
_ITEMS = {}
TTL = 600


def begin(request_id, owner):
    _LOCAL.key = None
    if not re.fullmatch(r'[A-Za-z0-9_-]{8,80}', request_id):
        return
    key = (owner, request_id)
    with _LOCK:
        now = time.monotonic()
        for old in list(_ITEMS):
            if now - _ITEMS[old]['updated'] > TTL:
                del _ITEMS[old]
        if len(_ITEMS) >= 2000:
            del _ITEMS[min(_ITEMS, key=lambda k: _ITEMS[k]['updated'])]
        _ITEMS[key] = dict(status='Сервер принял запрос…', done=False, updated=now)
    _LOCAL.key = key


def update(status):
    key = getattr(_LOCAL, 'key', None)
    with _LOCK:
        if key in _ITEMS:
            _ITEMS[key].update(status=status, updated=time.monotonic())


def finish():
    key = getattr(_LOCAL, 'key', None)
    with _LOCK:
        if key in _ITEMS:
            _ITEMS[key].update(status='Обработка запроса завершена', done=True,
                               updated=time.monotonic())
    _LOCAL.key = None


def get(request_id, owner):
    with _LOCK:
        item = _ITEMS.get((owner, request_id))
        if not item or time.monotonic() - item['updated'] > TTL:
            return None
        return {k: item[k] for k in ('status', 'done')}
