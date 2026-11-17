# -*- coding: utf-8 -*-
# Выполняется НА ПЛАТЕ (Python 3) лаунчером neuro12-start.exe перед стартом:
#   python3 n12_setcfg.py <cloud_url> <token> <app_dir>
# Прописывает адрес облака и мозг-токен во ВСЕ конфиги приложения
# (neuro12_config.json в корне и в python/), прочие настройки не трогает;
# конфига нет — создаёт в корне приложения (это /app внутри App Lab).
import json
import os
import sys

url, token, app = sys.argv[1], sys.argv[2], os.path.expanduser(sys.argv[3])
paths = [os.path.join(app, "neuro12_config.json"),
         os.path.join(app, "python", "neuro12_config.json")]
found = [p for p in paths if os.path.isfile(p)] or paths[:1]
for p in found:
    cfg = {}
    try:
        with open(p, "r", encoding="utf-8") as f:
            cfg = json.load(f) or {}
    except Exception:
        cfg = {}
    cfg["cloud_url"] = url
    if token:
        cfg["token"] = token
    cfg.setdefault("brain_on", True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=1)
    print("настроено:", p, "->", url)
