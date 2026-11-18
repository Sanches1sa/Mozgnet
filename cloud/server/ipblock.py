# -*- coding: utf-8 -*-
"""v3.182: ЗАПРЕЩЁННЫЕ АДРЕСА. Облако не принимает запросы с этих адресов и
само на них не ходит (ни один сокет Python не соединится). По умолчанию —
157.22.190.54: на него ходила старая чужая платформа neuro_brain_12 платы.
Список: ⚙ → Безопасность (settings.blocked_ips), «a.b.c.d» или «a.b.c.0/24»."""
import ipaddress
import socket
import threading

DEFAULT = ["157.22.190.54"]
_NETS = []
_LOCK = threading.Lock()
_ORIG = {}


def _parse(lst):
    out = []
    for x in lst or []:
        try:
            out.append(ipaddress.ip_network(str(x).strip(), strict=False))
        except ValueError:
            continue
    return out


def reload(lst=None):
    if lst is None:
        try:
            import store
            lst = store.settings().get("blocked_ips")
        except Exception:
            lst = None
        if not isinstance(lst, list):
            lst = list(DEFAULT)
    with _LOCK:
        _NETS[:] = _parse(lst)
    return [str(n) for n in _NETS]


def blocked(ip):
    try:
        a = ipaddress.ip_address(str(ip).split("%")[0])
    except ValueError:
        return False
    if getattr(a, "ipv4_mapped", None):
        a = a.ipv4_mapped
    with _LOCK:
        return any(a in n for n in _NETS)


def _guard(orig):
    def connect(self, address, *a, **kw):
        try:
            host = address[0] if isinstance(address, tuple) else None
        except Exception:
            host = None
        if host and blocked(host):
            raise ConnectionRefusedError("адрес %s запрещён (⚙ → Безопасность)" % host)
        return orig(self, address, *a, **kw)
    return connect


def install():
    """Запрет исходящих: обёртка socket.connect/connect_ex для всего процесса."""
    reload()
    if _ORIG:
        return
    for name in ("connect", "connect_ex"):
        _ORIG[name] = getattr(socket.socket, name)
        setattr(socket.socket, name, _guard(_ORIG[name]))
