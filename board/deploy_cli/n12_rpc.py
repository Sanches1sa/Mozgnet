#!/usr/bin/env python3
# n12_rpc.py — статистика вызовов моста Linux -> скетч (что доходит, что нет).
import json, urllib.request
d = json.load(urllib.request.urlopen("http://127.0.0.1:7000/api/state", timeout=15))
bs = d.get("bridge_stats") or {}
print("всего ok=%s err=%s сброшено=%s" % (bs.get("ok"), bs.get("err"), bs.get("dropped")))
ms = bs.get("methods") or {}
for m, v in sorted(ms.items(), key=lambda kv: -(kv[1].get("ok", 0) + kv[1].get("err", 0)))[:14]:
    print("  %-14s ok=%-5s err=%-5s" % (m, v.get("ok"), v.get("err")))
print("проблемные:", [(b["method"], b["last"]) for b in (bs.get("bad") or [])][:6])
s = d.get("sketch") or {}
print("скетч sver:", s.get("sver"), "| diag:", s.get("diag"), s.get("diag_err"))
