# -*- coding: utf-8 -*-
"""mozgnet — облако «Star Gate» внутри телефона.

Java (CloudService) копирует облако из APK в files/cloud и зовёт start().
Облако — тот же код, что на компьютере (server/app.py, web/), с теми же
возможностями: чат, мозг, своя LLM, база знаний, боты, связь с платами.
Данные (память, база, модель) лежат в files/cloud/data и переживают
обновление приложения.
"""
import json
import os
import secrets
import sys
import threading
import time
import traceback
import urllib.request

_ST = {"started": False, "root": "", "port": 8077, "err": "", "t0": 0.0}


def _prep(root, port):
    data = os.path.join(root, "data")
    os.makedirs(data, exist_ok=True)
    os.environ["NEURO_DATA"] = data
    os.environ["NEURO_PORT"] = str(int(port))
    os.environ["NEURO_NO_SUPERVISOR"] = "1"      # без сторожа-подпроцесса: в телефоне нет python.exe
    os.environ["NEURO_MOBILE"] = "1"
    srv = os.path.join(root, "server")
    if srv not in sys.path:
        sys.path.insert(0, srv)
    try:
        os.chdir(root)
    except OSError:
        pass


def start(root, port=8077):
    """Запустить облако фоном. Повторный вызов ничего не делает."""
    if _ST["started"]:
        return "already"
    _ST.update(root=str(root), port=int(port), t0=time.time(), err="")
    _prep(_ST["root"], _ST["port"])

    def run():
        try:
            import app
            try:
                app._crash_diag()
            except Exception:
                pass
            app.main()
        except BaseException as e:            # noqa: B902 — показать причину в приложении
            _ST["err"] = "%s: %s\n%s" % (type(e).__name__, e, traceback.format_exc()[-1500:])
            _ST["started"] = False

    threading.Thread(target=run, daemon=True, name="cloud").start()
    _ST["started"] = True
    return "started"


def ensure_password():
    """Пароль панели: если его нет — придумать и сохранить
    (data/mozgnet_password.txt, видно в приложении)."""
    _prep(_ST["root"], _ST["port"])
    import auth
    pf = os.path.join(_ST["root"], "data", "mozgnet_password.txt")
    if auth.has_password():
        try:
            with open(pf, encoding="utf-8") as f:
                return f.read().strip()
        except OSError:
            return ""
    pw = "mz-" + secrets.token_hex(5)
    auth.set_password(pw)
    with open(pf, "w", encoding="utf-8") as f:
        f.write(pw)
    return pw


def session():
    """Сессия владельца для окна приложения (cookie n12sid) — без ввода пароля."""
    import auth
    return auth.create_session("127.0.0.1", "mozgnet-app")


def token():
    try:
        with open(os.path.join(_ST["root"], "data", "token.txt"), encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def status():
    """JSON: работает ли облако, версия, ошибка запуска."""
    out = {"started": _ST["started"], "port": _ST["port"], "err": _ST["err"],
           "uptime": round(time.time() - _ST["t0"], 1) if _ST["t0"] else 0}
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d/api/health" % _ST["port"], timeout=1.5) as r:
            d = json.loads(r.read().decode("utf-8", "replace") or "{}")
        out.update(ok=True, version=d.get("version"), starting=bool(d.get("starting")))
    except Exception as e:
        out.update(ok=False, why=str(e)[:200])
    try:
        out["numpy"] = __import__("importlib").util.find_spec("numpy") is not None
    except Exception:
        out["numpy"] = False
    return json.dumps(out, ensure_ascii=False)
