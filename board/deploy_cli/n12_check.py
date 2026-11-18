#!/usr/bin/env python3
# n12_check.py — быстрая проверка платы с её же Linux: версия скетча, камера, уши, Vosk.
#   docker exec neuro_12-main-1 python3 /tmp/n12_check.py   (скопировав файл в контейнер)
import json
import os
import sys
import urllib.request

try:
    d = json.load(urllib.request.urlopen("http://127.0.0.1:7000/api/state", timeout=15))
except Exception as e:
    print("приложение платы не отвечает на 7000:", e)
    raise SystemExit(1)
print("приложение:", d.get("version"), "| мост жив:", d.get("bridge_alive"), "| сброшено:", d.get("bridge_dropped"))
s = d.get("sketch") or {}
print("скетч: запуск шаг", s.get("boot"), s.get("boot_name"), "| diag:", json.dumps(s.get("diag"), ensure_ascii=False)[:160])
cam = d.get("d6_cam") or {}
print("камера d6:", json.dumps({k: cam.get(k) for k in ("on", "on4", "frames", "d4_sent", "err")}, ensure_ascii=False))
g = d.get("gen") or {}
m = g.get("mic") or {}
c = g.get("cam") or {}
if m:
    print("уши:", m.get("state"), "|", m.get("err") or "", "| vosk:", json.dumps(m.get("vosk"), ensure_ascii=False))
if c:
    print("камера:", c.get("state"), "|", c.get("err") or "", "| КБ:", c.get("kb"), "| ok", int(__import__("time").time() - (c.get("ok_ts") or 0)) if c.get("ok_ts") else "—", "с назад")
# Vosk проверяем ТЕМ ЖЕ интерпретатором, что и приложение (venv App Lab /app/.cache/.venv)
import subprocess
py = "/app/.cache/.venv/bin/python" if os.path.exists("/app/.cache/.venv/bin/python") else sys.executable
r = subprocess.run([py, "-c", "import vosk,os;print(os.path.dirname(vosk.__file__))"], capture_output=True, text=True)
print("vosk (%s):" % py, ("установлен: " + r.stdout.strip()) if r.returncode == 0 else "НЕ установлен")
print("модель vosk-ru:", "есть" if os.path.isfile("/app/vosk-ru/am/final.mdl") else "нет")
