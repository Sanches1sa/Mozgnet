#!/usr/bin/env python3
# n12_rpc_probe.py — таймлайн моста после прошивки: ping каждые 3 с + дельты ok/err по
# методам из /api/state (bridge_stats) каждые 10 с. Запуск: ... n12_rpc_probe.py 300
import json
import sys
import time
import urllib.request
try:
    from arduino.app_utils import Bridge
except Exception as e:
    print("нет модуля моста:", e)
    raise SystemExit(1)
dur = int(sys.argv[1]) if len(sys.argv) > 1 else 300
t_start = time.time()
prev = {}
last_stats = 0.0


def stats():
    try:
        d = json.load(urllib.request.urlopen("http://127.0.0.1:7000/api/state", timeout=8))
        return (d.get("bridge_stats") or {}).get("methods") or {}, d.get("bridge_alive")
    except Exception:
        return {}, None


while time.time() - t_start < dur:
    t0 = time.time()
    try:
        Bridge.call("ping", timeout=3)
        print("%5.0f с  ping OK   %4.0f мс" % (t0 - t_start, (time.time() - t0) * 1000), flush=True)
    except Exception as e:
        print("%5.0f с  ping err  %s" % (t0 - t_start, str(e)[:40]), flush=True)
    if time.time() - last_stats >= 10:
        last_stats = time.time()
        m, alive = stats()
        delta = []
        for k, v in m.items():
            po, pe = prev.get(k, (0, 0))
            do, de = v.get("ok", 0) - po, v.get("err", 0) - pe
            if do or de:
                delta.append("%s +%d/-%d" % (k, do, de))
            prev[k] = (v.get("ok", 0), v.get("err", 0))
        print("%5.0f с  [мост жив=%s] %s" % (time.time() - t_start, alive, ", ".join(delta) or "—"), flush=True)
    time.sleep(max(0.0, 3 - (time.time() - t0)))
