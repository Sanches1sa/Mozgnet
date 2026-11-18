#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ===========================================================================
# app.py — СЕРВЕР neuro12-cloud (только stdlib: http.server, никаких pip!)
# ---------------------------------------------------------------------------
# Запуск:  python3 server/app.py          (env: NEURO_PORT, NEURO_DATA,
#                                          NEURO_TOKEN)
# Порт по умолчанию 8077 (как у привычной платформы мозг-12).
# Всё в одном процессе: маршруты API (api_board + api_ui), статика web/,
# multipart-разбор файлов, MJPEG-стрим камер, встроенный учитель, mozgnet.
# ===========================================================================
import json
import os
import re
import sys


# v3.183: ВТОРОЙ ЗАПУСК — ВЫХОД ДО ИМПОРТА МОДУЛЕЙ. Страж Windows (каждые
# 5 минут), автозапуск, лаунчер и сторож поднимали ещё одну копию облака.
# Она выходила («второй запуск не нужен»), но лишь ПОСЛЕ импорта всех
# модулей — а часть из них при импорте читает/пишет файлы data и запускает
# потоки. Две копии одновременно писали одни JSON → у работающего облака
# портились файлы, и оно падало. Теперь: замок data/server.lock занят
# (или облако отвечает на свой порт) — выходим сразу, ничего не трогая.
def _n12_busy_early():
    if os.environ.get("NEURO_CHILD"):
        return False                       # ребёнок сторожа — проверит сам
    data = os.environ.get("NEURO_DATA") or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
    lock = os.path.join(data, "server.lock")
    if not os.path.isfile(lock):
        return False
    try:
        fh = open(lock, "a+b")
    except OSError:
        return False
    try:
        fh.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        return False                       # замок свободен — облака нет
    except (OSError, IOError):
        return True                        # замок держит работающее облако
    finally:
        try:
            fh.close()
        except Exception:
            pass


if __name__ == "__main__" and _n12_busy_early():
    try:
        _d = os.environ.get("NEURO_DATA") or os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
        with open(os.path.join(_d, "second_start.log"), "a", encoding="utf-8") as _f:
            import time as _t
            _f.write("%s второй запуск (pid %d) — облако уже работает, выход\n" % (
                _t.strftime("%Y-%m-%d %H:%M:%S"), os.getpid()))
    except Exception:
        pass
    sys.exit(0)

# v3.147: БЫСТРАЯ ЗАПИСЬ JSON. json.dump() в файл кодирует ПО КУСОЧКАМ на чистом
# Python (в ~6 раз медленнее json.dumps на C) и всё это время держит GIL:
# пока сохраняется большая сеть, веб-интерфейс и API ждут — «долго грузятся
# настройки». Подмена на весь процесс: тот же результат, кодирует C-кодер.
def _n12_fast_dump(obj, fp, **kw):
    fp.write(json.dumps(obj, **kw))


json.dump = _n12_fast_dump


# v3.84: обучение своей LLM не должно занимать ВСЕ ядра — иначе веб-панель и
# боты не получают процессор («три точки» вместо данных). На 4+ ядрах один
# остаётся серверу (NEURO_BLAS_THREADS переопределяет). Выставляется ДО импорта
# numpy; уже заданные пользователем OMP/OPENBLAS/MKL не трогаются.
try:
    _nc = os.cpu_count() or 1
    _bt = os.environ.get("NEURO_BLAS_THREADS") or (str(_nc - 1) if _nc >= 4 else "")
    if _bt:
        for _k in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS"):
            os.environ.setdefault(_k, _bt)
except Exception:
    pass
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import store                                  # noqa: E402
import auth                                   # noqa: E402
import api_board                              # noqa: E402
import api_ui                                 # noqa: E402
import brain                                  # noqa: E402
import bridge                                 # noqa: E402
import skills                                 # noqa: E402
import maxbot                                 # noqa: E402
import tgbot                                  # noqa: E402
import ghlearn                                # noqa: E402
import study                                  # noqa: E402  (v3.24.2: ссылки-инструкции)

WEB_DIR = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "web")

MIME = {".html": "text/html; charset=utf-8", ".js": "text/javascript; "
        "charset=utf-8", ".css": "text/css; charset=utf-8",
        ".png": "image/png", ".jpg": "image/jpeg", ".svg": "image/svg+xml",
        ".ico": "image/x-icon", ".woff2": "font/woff2"}

MAX_BODY = 64 * 1024 * 1024                   # 64 МБ (файлы плат)

# v3.23.1: журнал отказов авторизации плат (401) — «плата не подключается»
# становится видимым: в хронике появляется запись с IP и маршрутом.
# Дроссель: не чаще раза в 5 минут на пару (IP, путь) — мозг-почта
# стучится часто, заваливать хронику нельзя.
_AUTH_FAIL_TS = {}


def _log_auth_fail(ip, path, kind):
    key = (str(ip), str(path)[:60])
    now = time.time()
    if now - float(_AUTH_FAIL_TS.get(key) or 0) < 300.0:
        return
    _AUTH_FAIL_TS[key] = now
    if len(_AUTH_FAIL_TS) > 500:               # не растём бесконечно
        _AUTH_FAIL_TS.clear()
    hint = ("проверь ключ приложения (Настройки → API и ответы)"
            if str(path).startswith("/api/v1/") else
            "проверь токен платы (X-Mozg-Token в config платы)")
    store.log("АВТОРИЗАЦИЯ", "%s с %s не прошёл на %s — %s"
              % (kind, ip, path, hint))


class Req:
    """Обёртка запроса для обработчиков."""
    __slots__ = ("method", "path", "query", "body", "raw", "headers",
                 "files_data", "params", "client_ip")


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    # v3.8.2: версия — единый источник store.version() (файл VERSION)
    server_version = "neuro12-cloud/" + store.version()

    # ------------------------------ утилиты ---------------------------------
    def log_message(self, fmt, *args):
        pass                                   # свой журнал в store.log

    def _send(self, code, body=b"", ctype="application/json; charset=utf-8",
              extra=None):
        try:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            if str(self.path or "").startswith("/api/v1/"):
                # v3.28: API для приложений — по ключу, не по cookie:
                # браузерным приложениям других адресов можно
                self.send_header("Access-Control-Allow-Origin", "*")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD" and body:
                self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _send_json(self, code, obj, extra=None):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                   extra=extra)

    def _send_bytes(self, code, data, ctype, stream=False):
        try:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            if not stream:
                self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if stream:
                for chunk in data:
                    try:
                        self.wfile.write(chunk)
                        self.wfile.flush()
                    except (BrokenPipeError, ConnectionResetError):
                        break
            else:
                self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _send_file(self, code, fpath, ctype, fname):
        """v3.30: отдать файл кусками (архив на сотни МБ не в памяти)."""
        import urllib.parse as _up
        try:
            size = os.path.getsize(fpath)
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(size))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Disposition",
                             "attachment; filename*=UTF-8''" +
                             _up.quote(str(fname)))
            self.end_headers()
            with open(fpath, "rb") as f:
                while True:
                    chunk = f.read(1 << 20)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass

    # ------------------------------- вход -----------------------------------
    def _read_body(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = 0
        if n <= 0:
            return b""
        if n > MAX_BODY:
            raise ValueError("тело слишком большое")
        return self.rfile.read(n)

    def _parse_multipart(self, raw):
        """Разбор multipart/form-data (поля + файл) — stdlib, без cgi."""
        out = {"fields": {}, "filename": "", "file": b""}
        ctype = self.headers.get("Content-Type", "")
        m = re.search(r'boundary="?([^";]+)"?', ctype)
        if not m:
            return out
        delim = b"--" + m.group(1).encode()
        for part in raw.split(delim):
            part = part.strip(b"\r\n")
            if not part or part == b"--":
                continue
            if b"\r\n\r\n" not in part:
                continue
            head, _, content = part.partition(b"\r\n\r\n")
            head_l = head.decode("utf-8", "replace").lower()
            nm = re.search(r'name="([^"]*)"', head_l)
            if not nm:
                continue
            name = nm.group(1)
            if 'filename="' in head_l:
                fm = re.search(r'filename="([^"]*)"', head_l)
                out["filename"] = (fm.group(1) if fm else "")[:160]
                out["file"] = content
            else:
                out["fields"][name] = content.decode("utf-8", "replace")[:4000]
        return out

    def handle_one_request(self):
        try:
            super().handle_one_request()
        except (ConnectionResetError, BrokenPipeError, TimeoutError):
            self.close_connection = True

    def _cookie(self, name):
        raw = self.headers.get("Cookie") or ""
        for part in raw.split(";"):
            k, _, v = part.strip().partition("=")
            if k == name:
                return v.strip()
        return ""

    # ----------------------------- диспетчер --------------------------------
    def _dispatch(self):
        t0 = time.time()
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = {k: v[0] for k, v in
                 urllib.parse.parse_qs(parsed.query).items()}
        req = Req()
        req.method = self.command
        req.path = path
        req.query = query
        req.headers = self.headers
        req.body = {}
        req.raw = b""
        req.files_data = {}
        req.params = {}
        req.client_ip = self.client_address[0]
        try:                                       # v3.182: запрещённые адреса
            import ipblock
            if ipblock.blocked(req.client_ip):
                self.close_connection = True
                return self._send_json(403, {"ok": False, "error": "адрес запрещён"})
        except Exception:
            pass

        try:
            if STARTING[0] and path.startswith("/api/") and path not in _START_OK \
                    and not path.startswith("/api/auth"):
                return self._send_json(503, {"ok": False, "starting": True,
                                             "error": "облако запускается — повтори через пару секунд"},
                                       {"Retry-After": "3"})
            # ---------- страница входа: всегда открыта ----------
            if path in ("/login", "/login.html"):
                return self._static("login.html")

            # ---------- статика (веб-панель закрыта паролем) ----------
            if not path.startswith("/api/"):
                if not auth.session_ok(self._cookie(auth.COOKIE)):
                    if path in ("/", "/chat", "/index.html", "/studio",
                                "/cam", "/doom", "/gubble"):
                        return self._send(302, b"", "text/plain",
                                          {"Location": "/login"})
                    return self._send(401, "закрыто паролем".encode("utf-8"),
                                      "text/plain; charset=utf-8")
                if path in ("/", "/chat", "/index.html", "/studio"):
                    return self._static("index.html")
                if path == "/cam":
                    return self._static("cam.html")
                if path == "/doom":                      # v3.127: игра
                    return self._static("doom.html")
                if path == "/gubble":                    # v3.181: Gubble 2
                    return self._static("gubble.html")
                return self._static(path.lstrip("/"))

            # ---------- API ----------
            raw = self._read_body()
            req.raw = raw
            ctype = (self.headers.get("Content-Type") or "").lower()
            if "application/json" in ctype and raw:
                try:
                    req.body = json.loads(raw.decode("utf-8", "replace"))
                except Exception:
                    req.body = {}
            elif "multipart/form-data" in ctype:
                mp = self._parse_multipart(raw)
                req.files_data = mp
                req.body = mp["fields"]

            route, params = self._match(req.method, path.lstrip("/"))
            if not route:
                return self._send_json(404, {"ok": False,
                                             "error": "нет маршрута %s" % path})
            fn, mode = route
            req.params = params

            # ------- АВТОРИЗАЦИЯ -------
            sid = self._cookie(auth.COOKIE)
            sess_ok = auth.session_ok(sid)
            if sess_ok:
                auth.touch_session(sid)
            req.params["_sid"] = sid if sess_ok else ""
            board_tok = (self.headers.get("X-Mozg-Token") or
                         query.get("token") or
                         req.files_data.get("fields", {}).get("token", ""))
            token_ok = store.auth_ok(board_tok)
            token_set = bool(store.token())
            if mode == "ai":
                # v3.28: API ИИ для приложений — ключ «Bearer n12-…» из
                # панели; владелец с сессией панели тоже может (проверка)
                import aiapi
                if store.settings().get("ai_api_on", True) is False:
                    return self._send_json(403, {"ok": False, "error": {
                        "message": "API для приложений выключено в панели",
                        "type": "forbidden"}})
                rec = aiapi.check(aiapi.key_from_headers(self.headers,
                                                         query))
                if not rec and sess_ok:
                    rec = {"id": "owner", "name": "владелец (панель)"}
                if not rec:
                    _log_auth_fail(req.client_ip, path, "приложение без "
                                   "ключа API")
                    return self._send_json(401, {"ok": False, "error": {
                        "message": "нужен ключ: Authorization: Bearer "
                                   "<ключ из Настроек → API для приложений>",
                        "type": "invalid_api_key"}})
                if not aiapi.rate_ok(rec["id"]):
                    return self._send_json(429, {"ok": False, "error": {
                        "message": "слишком часто: лимит ai_rate_per_min "
                                   "запросов в минуту на ключ",
                        "type": "rate_limit"}})
                req.params["_ai_key"] = rec
            elif mode == "open" or mode in ("setup", "login"):
                pass                              # публичные маршруты входа
            elif mode == "panel":
                # веб-панель: сессия по паролю; плате с валидным токеном
                # тоже можно (кадры, файлы — ей это нужно)
                if not (sess_ok or (token_set and token_ok)):
                    _log_auth_fail(req.client_ip, path, "вход без сессии")
                    return self._send_json(401, {
                        "ok": False, "auth": True,
                        "error": "нужен вход: открой /login"})
            elif mode is True:
                # API платы: токен платы (или сессия владельца)
                if not (token_ok or sess_ok):
                    _log_auth_fail(req.client_ip, path,
                                   "плата/запрос с неверным токеном")
                    return self._send_json(401, {"ok": False,
                                                 "error": "неверный токен "
                                                 "(X-Mozg-Token)"})
            else:
                pass                              # открытые API платы

            _tf = time.time()
            result = fn(req)
            _dtf = time.time() - _tf
            if _dtf > 4.0:              # v3.84: журнал медленных запросов
                try:
                    store.log("МЕДЛЕННО", "%s %s — %.1f с" % (req.method, path, _dtf))
                except Exception:
                    pass
            if len(result) == 3:
                _code, second, third = result
                if isinstance(third, dict) and \
                        isinstance(second, (dict, list)):
                    # (code, obj, {headers}) — JSON с доп. заголовками
                    return self._send_json(_code, second, third)
                _code, kind, data = result
                if kind == "wav":
                    return self._send_bytes(_code, data, "audio/wav")
                if kind == "jpg":
                    return self._send_bytes(_code, data, "image/jpeg")
                if kind == "raw":
                    blob, ct = data[0], data[1]
                    if len(data) > 2 and data[2]:
                        # v3.27: скачивание с именем файла (документы ИИ)
                        import urllib.parse as _up
                        return self._send(_code, blob, ct, extra={
                            "Content-Disposition":
                                "attachment; filename*=UTF-8''" +
                                _up.quote(str(data[2]))})
                    return self._send_bytes(_code, blob, ct)
                if kind == "file":
                    # v3.30: большой файл с диска потоком (полная выгрузка)
                    fpath, ct, fname = data
                    return self._send_file(_code, fpath, ct, fname)
                if kind == "mjpg":
                    return self._send_bytes(_code, data,
                                            "multipart/x-mixed-replace; "
                                            "boundary=mjpg", stream=True)
                return self._send_bytes(_code, data, kind)
            code, obj = result
            self._send_json(code, obj)
            dt = (time.time() - t0) * 1000
            if path not in ("/api/health",) and \
                    (req.method != "GET" or "chat" in path):
                store.log("HTTP", req.method, path, code,
                          "%d мс" % int(dt))
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:
            import traceback
            traceback.print_exc()
            try:
                self._send_json(500, {"ok": False, "error": "%s: %s"
                                      % (type(e).__name__, e)})
            except Exception:
                pass

    def _match(self, method, path):
        """Совпадение по шаблону с <param>; сначала точные маршруты."""
        routes = dict(api_board.ROUTES)
        routes.update(api_ui.ROUTES_UI)
        # точное совпадение
        for (m, pat), r in routes.items():
            if m == method and pat == path:
                return r, {}
        # с параметрами
        best, best_params, best_score = None, None, -1
        for (m, pat), r in routes.items():
            if m != method:
                continue
            pp = pat.split("/")
            sp = path.split("/")
            if len(pp) != len(sp):
                continue
            params, ok, score = {}, True, 0
            for a, b in zip(pp, sp):
                if a.startswith("<") and a.endswith(">"):
                    params[a[1:-1]] = b
                    score += 1
                elif a != b:
                    ok = False
                    break
                else:
                    score += 2
            if ok and score > best_score:
                best, best_params, best_score = r, params, score
        return (best, best_params) if best else (None, None)

    def _static(self, rel):
        rel = rel or "index.html"
        full = os.path.normpath(os.path.join(WEB_DIR, rel))
        if not full.startswith(WEB_DIR):
            return self._send_json(403, {"ok": False, "error": "запрещено"})
        if not os.path.isfile(full):
            return self._send_json(404, {"ok": False,
                                         "error": "нет файла %s" % rel})
        ext = os.path.splitext(full)[1]
        with open(full, "rb") as f:
            data = f.read()
        self._send(200, data, MIME.get(ext, "application/octet-stream"))

    # HTTP-методы
    def do_GET(self):
        self._dispatch()

    def do_POST(self):
        self._dispatch()

    def do_DELETE(self):
        self._dispatch()

    def do_HEAD(self):
        self._dispatch()

    def do_OPTIONS(self):
        self._send(204, b"", "text/plain",
                   {"Access-Control-Allow-Methods": "GET,POST,DELETE,OPTIONS",
                    "Access-Control-Allow-Headers": "Content-Type,"
                    "X-Mozg-Token,Authorization,X-Api-Key",
                    "Access-Control-Max-Age": "600"})


def _pick_port(want):
    """Свободный порт: want..want+10. Порт 7000 занят платой? Не страшно:
    облако само сдвинется на свободный (по умолчанию 8077) и запишет
    фактический порт в data/port.txt (его читает neuro12-cloud.exe).
    v3.19: у щупа SO_REUSEADDR — как у самого сервера, иначе сразу после
    перезапуска (обновление) TIME_WAIT-сокеты старых соединений кажутся
    «порт занят», и облако уползает на соседний порт."""
    import socket
    for p in range(want, want + 11):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(("0.0.0.0", p))
            return p
        except OSError:
            continue
        finally:
            try:
                s.close()
            except Exception:
                pass
    return want


def _already_running():
    """Уже запущена? Читаем data/port.txt и щупаем /api/health. Важно для
    сторожевой задачи (она стартует облако каждые 5 минут): второй
    экземпляр тихо выходит, а не плодит дубли на соседних портах.
    v3.20: только если port.txt СУЩЕСТВУЕТ — без него read_port() вернул
    бы дефолт 8077, и второй экземпляр с ДРУГИМИ данными (другой порт)
    ложно отказывался стартовать, увидев чужой живой сервер на 8077."""
    try:
        if not os.path.isfile(store.p("port.txt")):
            return False
        p = int(store.read_port() or 0)
    except Exception:
        return False
    if not p:
        return False
    try:
        import urllib.request
        with urllib.request.urlopen(
                "http://127.0.0.1:%d/api/health" % p, timeout=1.5) as r:
            d = json.loads(r.read().decode("utf-8", "replace"))
            return d.get("service") == "neuro12-cloud"
    except Exception:
        return False


_LOCK_FH = [None]


def _single_instance():
    """v3.33: ОДИН сервер на каталог данных. Замок-файл data/server.lock
    (msvcrt на Windows, fcntl на Linux) держится всё время работы процесса.
    Раньше повторный запуск (автозапуск + «сторож» раз в 5 минут + запуск
    руками), не дождавшись ответа /api/health, поднимал вторую копию на
    соседнем порту — обе опрашивали МАКС одним токеном и отвечали дважды."""
    try:
        os.makedirs(store.DATA_DIR, exist_ok=True)
        fh = open(os.path.join(store.DATA_DIR, "server.lock"), "a+b")
        fh.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        _LOCK_FH[0] = fh
        return True
    except (OSError, IOError):
        return False
    except Exception:
        return True                      # замок недоступен — не мешаем запуску


STARTING = [False]         # v3.175.1: порт открыт, посевы ещё идут (ставит main)
_START_OK = ("/api/health", "/api/login", "/api/logout", "/api/settings",
             "/api/version", "/api/update/status")


def main():
    try:                                           # v3.182: исходящие на запрещённые — нет
        import ipblock
        ipblock.install()
    except Exception:
        pass
    if not _single_instance():
        msg = ("neuro12-cloud уже работает с этим каталогом данных (%s) — "
               "вторая копия не нужна, выхожу." % store.DATA_DIR)
        print(msg)
        try:
            store.log("СТАРТ", msg)
        except Exception:
            pass
        return
    if _already_running():
        p = store.read_port()
        msg = ("neuro12-cloud УЖЕ запущена на порту %s — "
               "http://127.0.0.1:%s/ (второй запуск не нужен)"
               % (p, p))
        print(msg)
        store.log("СТАРТ", msg)
        return
    STARTING[0] = True
    want = int(os.environ.get("NEURO_PORT") or 8077)
    port = _pick_port(want)
    token = store.ensure_dirs()
    # v3.175.1: ПОРТ — СРАЗУ. Раньше сервер открывал порт только после всех
    # посевов, сохранения памяти и загрузки модулей; с большой базой это
    # минуты — лаунчер ждал /api/health 60 с, считал облако мёртвым, а сторож
    # и повторные запуски путались. Теперь порт открыт через доли секунды,
    # /api/health отвечает {"starting": true}, чат и плата получают 503
    # «облако запускается» (повторят сами), пока старт не закончится.
    class _Srv(ThreadingHTTPServer):
        # по умолчанию очередь listen() = 5: при одновременных запросах
        # (лаунчер, плата, веб) лишние соединения сбрасываются (RST)
        request_queue_size = 128
    try:
        httpd = _Srv(("0.0.0.0", port), Handler)
    except OSError as e:
        msg = ("не смог занять порт %d (%s). Укажи другой: "
               "set NEURO_PORT=8080 и запусти снова." % (port, e))
        store.log("СТАРТ", msg)
        print(msg, file=sys.stderr)
        return                       # код 0: сторож не перезапускает впустую
    httpd.daemon_threads = True
    store.write_port(port)
    import threading as _th0
    _th0.Thread(target=httpd.serve_forever, daemon=True, name="http").start()
    store.log("СТАРТ", "порт %d открыт — облако запускается…" % port)
    t_start = time.time()
    skills.seed()                         # v3.170: навыки -> App Lab (Python)
    try:
        import instructions
        instructions.start()               # инструкция ИИ из настоящего состояния
    except Exception:
        pass
    api_ui.h_seed_kb(None)
    api_ui.seed_platform_guide()          # самоучитель платформы в KB+сети
    brain.start_background()
    # v3.27: индекс базы знаний строится фоном сразу — первый вопрос в
    # чате/МАКС не ждёт чтения тысяч статей
    import threading as _th
    import kb as _kb
    _th.Thread(target=_kb.warm_index, daemon=True, name="kb-index").start()
    try:                    # v3.91: интернет включён (один раз; галочка остаётся)
        if not store.settings().get("web_mig91"):
            store.update_settings({"web_on": True, "web_mig91": True})
            store.log("СТАРТ", "интернет для ответов включён (галочка «Интернет» в настройках)")
    except Exception:
        pass
    try:                    # v3.156: любопытство и SSH-агент принудительно включены
        if not store.settings().get("curiosity_on", True):
            store.update_settings({"curiosity_on": True})
            store.log("СТАРТ", "любопытство облака включено")
        if not store.settings().get("ssh_on"):
            store.update_settings({"ssh_on": True})
            store.log("СТАРТ", "SSH-агент администрирования доступен")
        # посев знаний по серверам (один раз)
        if not store.settings().get("ssh_seed_done"):
            import ssh_agent as _sa
            _sa.seed_admin_knowledge()
            store.update_settings({"ssh_seed_done": True})
            store.log("СТАРТ", "базовые знания администрирования серверов добавлены в KB")
    except Exception:
        pass
    try:                    # v3.157: фоновое сохранение памяти облака и LLM в JSON
        import memory_save as _ms
        # v3.175.1: не при старте (это грузило всю свою LLM до открытия
        # порта) — первое сохранение фоном через 5 минут
        _ms.background_saver(interval=300)  # каждые 5 минут
        store.log("СТАРТ", "память облака и LLM сохраняется в JSON")
    except Exception:
        pass
    try:                    # v3.160: посев инструкций агента в стиле z.ai
        import zai_agent as _za
        if not store.settings().get("zai_seed_done"):
            _za.seed_kb()
            store.update_settings({"zai_seed_done": True})
            store.log("СТАРТ", "инструкции агента z.ai добавлены в KB")
    except Exception:
        pass

    try:                    # v3.162: посев знаний о «Звёздных вратах»
        import stargate as _sg
        if not store.settings().get("stargate_seed_done"):
            _sg.seed_knowledge()
            store.update_settings({"stargate_seed_done": True})
            store.log("СТАРТ", "базовые знания о сериале «Звёздные врата» добавлены")
    except Exception:
        pass

    try:                    # v3.163: зрение — посев инструкций
        import vision_agent as _va
        if not store.settings().get("vision_seed_done"):
            _va.seed_instructions()
            store.update_settings({"vision_seed_done": True})
            store.log("СТАРТ", "инструкции зрения добавлены")
    except Exception:
        pass
    try:                    # v3.119: пороги своей LLM 60–70 % -> 50 % (один раз)
        s_ = store.settings()
        if not s_.get("llm_mig119"):
            p_ = {"llm_mig119": True}
            if float(s_.get("llm_link_gen_minq") or 0.5) > 0.5:
                p_["llm_link_gen_minq"] = 0.5
            if float(s_.get("llm_link_minq") or 0.5) > 0.5:
                p_["llm_link_minq"] = 0.5
            store.update_settings(p_)
            store.log("СТАРТ", "пороги своей LLM: опора на факты и качество "
                      "мысли — 50 %")
    except Exception:
        pass
    try:                    # v3.122: проверка интернета (для «Отвечать без сети»)
        import strict as _sx
        _th.Thread(target=_sx.online, daemon=True, name="net-probe").start()
    except Exception:
        pass
    try:                    # v3.121: судья связности мыслей мозга (фоном)
        import coherence as _coh
        _coh.warm()
    except Exception:
        pass
    try:                    # v3.118: разовая чистка «англ н н» из хранилищ
        _th.Thread(target=store.purge_noise, daemon=True,
                   name="purge-noise").start()
    except Exception:
        pass
    try:                    # v3.144: убрать пары «вопрос вместо ответа»
        import qalearn as _qal
        _th.Thread(target=_qal.purge_bad, daemon=True,
                   name="qa-purge").start()
    except Exception:
        pass
    try:                    # v3.83: модель грузится заранее, а не на первом ответе
        import tinyllm as _tl
        _th.Thread(target=lambda: _tl.model(), daemon=True,
                   name="llm-warm").start()
    except Exception:
        pass
    try:                                  # v3.27: инструкции ИИ-агента в KB
        import agent as _agent
        _agent.seed()
    except Exception:
        pass
    bridge.start()                        # мост к плате 127.0.0.1:7000
    maxbot.start()                        # v3.20: бот в МАКС (long polling)
    tgbot.start()                         # v3.40: бот в Telegram
    ghlearn.start()                       # v3.41: чтение GitHub-ссылок
    try:                                  # v3.176: второй поток своей LLM — код
        import codestream
        codestream.start()
    except Exception as e:
        store.log("СТАРТ", "второй поток LLM (код) не запустился: %s" % e)
    try:
        import llmlink                    # v3.33: LLM сама думает -> мозг
        llmlink.start()
    except Exception as e:
        store.log("СТАРТ", "связь LLM не запустилась: %s" % e)
    study.start()                         # v3.24.2: фоновая перечитка ссылок-инструкций
    store.write_port(port)
    if port != want:
        store.log("СТАРТ", "порт %d занят (если это 7000 — там ПЛАТА, у "
                  "неё свой сервер) — облако поднялось на соседнем "
                  "свободном порту %d" % (want, port))
    store.log("СТАРТ", "neuro12-cloud v%s, порт %d, данные: %s"
              % (store.version(), port, store.DATA_DIR))
    store.log("СТАРТ", "App Lab: лаборатория приложений из кирпичиков "
              "(функции платы + чата, без LLM)")
    store.log("СТАРТ", "мост к плате: %s (опрос каждые 15 с; %s)"
              % (bridge.url(),
                 "включён" if bridge.enabled() else "ВЫКЛЮЧЕН"))
    store.log("СТАРТ", "веб-панель: http://127.0.0.1:%d/  (плата при этом "
              "живёт на своём порту, например 7000 — конфликтов нет)"
              % port)
    if token:
        store.log("СТАРТ", "токен платы задан (X-Mozg-Token), "
                  "первые знаки: %s…" % token[:4])
    else:
        store.log("СТАРТ", "токен НЕ задан — API открыт (локальная сеть)")
    if auth.has_password():
        store.log("СТАРТ", "веб-панель закрыта паролем (вход: /login)")
    else:
        store.log("СТАРТ", "пароль панели НЕ задан — при первом открытии "
                  "браузер предложит его создать")
    try:
        import updater                    # v3.19: подтвердить старт после
        updater.mark_confirmed()          # обновления/отката (панель видит)
    except Exception:
        pass
    STARTING[0] = False
    store.log("СТАРТ", "облако готово за %.1f с" % (time.time() - t_start))
    try:                              # v3.27: сеть сохраняется фоном раз в
        import atexit                 # 3 мин — при остановке дописываем её
        atexit.register(lambda: brain.mn_save(force=True))
        atexit.register(lambda: __import__("qalearn").save(force=True))
    except Exception:
        pass
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        store.log("СТОП", "ручная остановка")


# ---------------------------------------------------------------------------
# v3.155: ДИАГНОСТИКА ПАДЕНИЙ И СТОРОЖ. Облако на Windows работает через
# pythonw — без консоли падение было «молчаливым». Теперь:
#   * faulthandler + перехват исключений (и в потоках) -> data/crash.log;
#   * сторож: запускает облако дочерним процессом и поднимает его снова,
#     если оно упало (пауза растёт 3 -> 60 с). Плановый перезапуск
#     обновления (флаг _planned_restart) сторож не трогает.
#   NEURO_NO_SUPERVISOR=1 — запуск без сторожа.
# ---------------------------------------------------------------------------
def _crash_diag():
    import faulthandler
    import threading as _th
    import traceback
    try:
        os.makedirs(store.DATA_DIR, exist_ok=True)
        f = open(store.p("crash.log"), "a", encoding="utf-8", buffering=1)
    except Exception:
        return
    f.write("\n=== старт %s pid %d ===\n" % (time.strftime("%Y-%m-%d %H:%M:%S"),
                                            os.getpid()))
    try:
        faulthandler.enable(file=f, all_threads=True)
    except Exception:
        pass

    def _hook(et, ev, tb):
        f.write("%s НЕПЕРЕХВАЧЕННОЕ ИСКЛЮЧЕНИЕ:\n%s" % (
            time.strftime("%H:%M:%S"), "".join(traceback.format_exception(et, ev, tb))))
        try:
            store.log("ПАДЕНИЕ", "%s: %s (подробно — crash.log)" % (et.__name__, ev))
        except Exception:
            pass

    sys.excepthook = _hook

    def _thook(a):
        if a.exc_type is SystemExit:
            return
        f.write("%s поток %s:\n%s" % (
            time.strftime("%H:%M:%S"), getattr(a.thread, "name", "?"),
            "".join(traceback.format_exception(a.exc_type, a.exc_value,
                                               a.exc_traceback))))
    _th.excepthook = _thook


def _crash_hint(code):
    """v3.183: что значит код выхода процесса облака."""
    try:
        c = int(code) & 0xFFFFFFFF
    except Exception:
        return ""
    known = {
        0xC0000005: "нарушение доступа к памяти в машинном коде (numpy / vosk / Pillow) — "
                    "сбой библиотеки, не Python",
        0xC00000FD: "переполнение стека (слишком глубокая рекурсия)",
        0xC0000409: "аварийная остановка библиотеки (buffer overrun)",
        0xC0000017: "не хватило памяти",
        0xC000013A: "закрыто пользователем / Ctrl+C",
        0x40010004: "процесс убит снаружи (диспетчер задач / другой запуск)",
        1: "необработанное исключение Python — смотри строку причины",
        9: "убит снаружи (kill -9 / нехватка памяти)",
        0xFFFFFFF7: "убит снаружи (SIGKILL — часто нехватка памяти)",
        137: "убит снаружи (нехватка памяти, OOM)",
    }
    return known.get(c, "")


def _supervise():
    import subprocess
    flag = store.p("_planned_restart")
    env = dict(os.environ, NEURO_CHILD="1")
    pause, fails = 3, 0
    while True:
        t0 = time.time()
        try:
            code = subprocess.call([sys.executable, os.path.abspath(__file__)]
                                   + sys.argv[1:], env=env)
        except KeyboardInterrupt:
            return
        if os.path.exists(flag):
            try:
                os.remove(flag)
            except OSError:
                pass
            return                       # плановый перезапуск обновления
        if code == 0:
            return                       # штатная остановка / уже запущено
        fails = fails + 1 if time.time() - t0 < 60 else 1
        pause = min(60, 3 * fails)
        why = ""
        try:                 # v3.175.1: причина падения — прямо в журнал
            with open(store.p("crash.log"), "r", encoding="utf-8",
                      errors="replace") as f_:
                tail = f_.read()[-6000:]
            blk = tail.rsplit("=== старт", 1)[-1]
            lines = [x.strip() for x in blk.splitlines() if x.strip()]
            key = [x for x in lines if ("Error" in x or "Exception" in x or
                   "fatal" in x.lower() or "ИСКЛЮЧЕНИЕ" in x)]
            why = (key[-1] if key else (lines[-1] if len(lines) > 1 else ""))[:300]
        except Exception:
            pass
        try:
            store.log("СТОРОЖ", "облако упало (код %s%s) — поднимаю снова через "
                      "%d с (подробно — data/crash.log)" % (
                          code, (": " + why) if why else "", pause))
        except Exception:
            pass
        try:                 # v3.183: падения — списком, их видно в панели и лаунчере
            lst = store.load_json("crashes.json", []) or []
            lst.append({"ts": time.time(), "code": code, "why": why, "up": round(time.time() - t0, 1),
                        "hint": _crash_hint(code)})
            store.save_json("crashes.json", lst[-100:])
        except Exception:
            pass
        time.sleep(pause)


if __name__ == "__main__":
    if os.environ.get("NEURO_CHILD") or os.environ.get("NEURO_NO_SUPERVISOR"):
        _crash_diag()
        main()
    else:
        _supervise()
