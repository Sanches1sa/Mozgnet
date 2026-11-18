#!/usr/bin/env python3
# n12_bridge.py — статистика RPC-моста платы и состояние скетча (запуск, loop, журнал).
import json
import urllib.request

d = json.load(urllib.request.urlopen("http://127.0.0.1:7000/api/state", timeout=15))
bs = d.get("bridge_stats") or {}
print("мост жив:", d.get("bridge_alive"), "| возраст:", d.get("bridge_age"), "с | видел скетч:", d.get("bridge_seen"),
      "| сброшено из очереди:", d.get("bridge_dropped"))
print("всего ok:", bs.get("ok"), "err:", bs.get("err"))
for m, s in sorted((bs.get("methods") or {}).items(), key=lambda kv: -(kv[1].get("ok", 0) + kv[1].get("err", 0)))[:8]:
    print("  %-16s ok=%-6d err=%-6d" % (m, s.get("ok", 0), s.get("err", 0)))
sk = d.get("sketch") or {}
print("скетч: запуск шаг", sk.get("boot"), sk.get("boot_name"), "| diag:", json.dumps(sk.get("diag"), ensure_ascii=False)[:200],
      "| diag_err:", sk.get("diag_err"))
for line in sk.get("log") or []:
    print("  [скетч]", line[:160])
print("sketch_build:", str(d.get("sketch_build") or "")[-600:])
