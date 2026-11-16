# -*- coding: utf-8 -*-
# ===========================================================================
# ghlearn.py — ОБЛАКО УЧИТСЯ КОДУ ПО ССЫЛКЕ НА GITHUB (v3.41, только stdlib)
# ---------------------------------------------------------------------------
# Заявка владельца: «как базу берут с Confluence или Википедии, пусть я
# просто вставлю общую ссылку на GitHub, а она сама обучится на примерах».
#
# ЧТО ПРИНИМАЕТ (ссылка вставляется в чат или в «Ссылки с инструкциями»):
#   * репозиторий            github.com/владелец/репо
#   * папка / ветка          github.com/владелец/репо/tree/ветка/папка
#   * один файл              github.com/владелец/репо/blob/ветка/файл.py,
#                            raw.githubusercontent.com/…
#   * пользователь/компания  github.com/владелец  (его самые популярные
#                            репозитории, не форки)
#   * общая ссылка           github.com, github.com/topics/python,
#                            github.com/search?q=… — репозитории ищутся
#                            через поиск GitHub (по звёздам)
# КАК ЧИТАЕТ: в фоне, файл за файлом, как Confluence-читатель: план файлов
# (дерево репозитория через REST API), затем каждый файл по raw-ссылке.
# Исходник становится статьёй базы знаний «Исходник: репо/путь» (её читает
# своя LLM — учится писать код; мозг учит функции через codemind), README и
# документация — обычными статьями. Прогресс виден в списке ссылок панели
# («прочитано N из M»). Повторное чтение пропускает неизменившиеся файлы
# (по sha). Всё безопасно: только https к github.com / api.github.com /
# raw.githubusercontent.com, публичные адреса, размер файла ограничен.
# ЧЕСТНО: без токена GitHub API отдаёт 60 запросов в час (хватает на ~25
# репозиториев в час; сами файлы с raw-хоста этим лимитом не считаются).
# Токен (необязательно) — в настройках gh_token, поднимает лимит до 5000.
# ===========================================================================
import hashlib
import json
import re
import threading
import time
import urllib.parse

import store
import webfetch

API_HOST = "api.github.com"
RAW_HOST = "raw.githubusercontent.com"
WEB_HOSTS = ("github.com", "www.github.com")
OK_HOSTS = WEB_HOSTS + (API_HOST, RAW_HOST)
# v3.44: другие площадки (GitHub в России местами закрыт): читаем архивом
FORGES = ("gitlab.com", "codeberg.org", "gitverse.ru", "gitflic.ru",
          "gitee.com", "bitbucket.org", "sourcecraft.dev", "git.sr.ht",
          "invent.kde.org", "salsa.debian.org")
ZIP_MAX = 40 * 1024 * 1024

CODE_EXT = {".py": "python", ".ino": "arduino", ".cpp": "c", ".cc": "c",
            ".c": "c", ".h": "c", ".hpp": "c", ".js": "js", ".sh": "bash",
            ".sql": "sql"}
DOC_EXT = (".md", ".rst", ".txt")
SKIP_PARTS = ("node_modules", "vendor", "dist", "build", "__pycache__",
              "venv", "site-packages", "migrations", "third_party",
              "thirdparty", ".git", "bower_components", "min")
DEFAULT_TOPICS = ("python", "algorithms", "arduino", "micropython",
                  "raspberry-pi", "neural-network", "esp32")
FILE_MAX = 150 * 1024
LOCK = threading.RLock()
WAKE = threading.Event()
_TH = [None]
_PLANNING = set()


class GhErr(Exception):
    pass


# ------------------------------- настройки ----------------------------------
def _s(k, d):
    v = store.settings().get(k)
    return d if v is None else v


def enabled():
    return bool(_s("gh_on", True)) and bool(_s("web_on", True))


def _token():
    return str(_s("gh_token", "") or "").strip()


def _every():
    try:
        return max(0.0, float(_s("gh_every", 4)))
    except (TypeError, ValueError):
        return 4.0


def _files_max():
    try:
        return max(1, int(_s("gh_files_max", 200)))
    except (TypeError, ValueError):
        return 200


def _repos_max():
    try:
        return max(1, int(_s("gh_repos_max", 6)))
    except (TypeError, ValueError):
        return 6


# ------------------------------- состояние ----------------------------------
def _load():
    d = store.load_json("ghlearn.json", None)
    if not isinstance(d, dict):
        d = {}
    d.setdefault("repos", {})          # full -> план и прогресс
    d.setdefault("links", {})          # исходная ссылка -> [full, ...]
    d.setdefault("stats", {"files": 0, "funcs": 0, "bytes": 0, "docs": 0,
                           "errors": 0})
    d.setdefault("backoff_until", 0.0)
    d.setdefault("last_error", "")
    d.setdefault("last_file", "")
    return d


def _save(d):
    store.save_json("ghlearn.json", d)


# --------------------------------- сеть -------------------------------------
def _get(url, timeout=25, max_bytes=3 * 1024 * 1024):
    """(status, headers, body) — только публичные адреса GitHub."""
    u = urllib.parse.urlparse(url)
    host = (u.hostname or "").lower()
    if u.scheme != "https" or host not in OK_HOSTS:
        raise GhErr("адрес не GitHub: %s" % host)
    ips = webfetch._resolve_public(host)
    if not ips:
        raise GhErr("%s не резолвится в публичный адрес (DNS-фильтр?)" % host)
    path_qs = (u.path or "/") + (("?" + u.query) if u.query else "")
    hdr = {"Accept": "application/vnd.github+json"} if host == API_HOST \
        else {}
    tok = _token()
    if tok and host in (API_HOST, RAW_HOST):
        hdr["Authorization"] = "Bearer " + tok
    last = ""
    for ip in ips[:2]:
        try:
            return webfetch._https_call(host, ip, path_qs, timeout=timeout,
                                        max_bytes=max_bytes + 1,
                                        extra_headers=hdr)
        except Exception as e:
            last = webfetch._honest(e)
    raise GhErr(last or "соединение не удалось")


def _api(path):
    """JSON от REST API GitHub. Лимит запросов -> пауза до сброса."""
    status, headers, body = _get("https://%s%s" % (API_HOST, path))
    if status in (403, 429):
        reset = 0
        try:
            for k, v in (headers or {}).items():
                if str(k).lower() == "x-ratelimit-reset":
                    reset = int(v)
        except Exception:
            pass
        wait = max(60.0, (reset - time.time()) if reset else 900.0)
        with LOCK:
            d = _load()
            d["backoff_until"] = time.time() + min(wait, 3700)
            d["last_error"] = ("лимит запросов GitHub API — жду %d мин "
                               "(токен gh_token поднимет лимит)"
                               % int(min(wait, 3700) // 60 + 1))
            _save(d)
        raise GhErr("лимит запросов GitHub API (60/час без токена)")
    if status == 404:
        raise GhErr("не найдено (404): %s" % path.split("?")[0])
    if status != 200:
        raise GhErr("GitHub ответил HTTP %s" % status)
    try:
        return json.loads(bytes(body).decode("utf-8", "replace"))
    except Exception:
        raise GhErr("GitHub прислал не JSON")


# ------------------------------ разбор ссылки -------------------------------
def _extra_hosts():
    raw = str(_s("gh_hosts", "") or "")
    return tuple(h.strip().lower() for h in re.split(r"[,\s;]+", raw)
                 if h.strip())


def _forge_hosts():
    return FORGES + _extra_hosts()


def _host_of(url):
    try:
        return (urllib.parse.urlparse(str(url or "").strip()).hostname
                or "").lower()
    except Exception:
        return ""


def is_github(url):
    """Ссылка на GitHub или другую площадку кода (GitLab, Codeberg,
    GitVerse, GitFlic, Gitee, Bitbucket, SourceCraft…)."""
    h = _host_of(url)
    return h in (WEB_HOSTS + (RAW_HOST,)) or h in _forge_hosts()


def _link_re():
    hosts = ["github\\.com", "raw\\.githubusercontent\\.com"] + [
        re.escape(h) for h in _forge_hosts()]
    return re.compile(r"https?://(?:www\.)?(?:%s)[^\s<>()\"']*"
                      % "|".join(hosts), re.I)


def find_link(text):
    m = _link_re().search(str(text or ""))
    return m.group(0).rstrip(".,;:!?") if m else ""


_RESERVED = {"topics", "search", "explore", "trending", "collections",
             "marketplace", "sponsors", "orgs", "features", "pricing",
             "about", "login", "settings", "notifications", "pulls",
             "issues", "new", "organizations", "enterprise", "readme"}


def _parse_forge(host, parts):
    """Не-GitHub площадки: владелец/репо [+ ветка] из пути."""
    p = list(parts)
    if host == "gitflic.ru" and p and p[0] == "project":
        p = p[1:]
    cut = len(p)
    branch, sub = "", ""
    for i, seg in enumerate(p):
        if seg in ("tree", "blob", "-", "src", "raw", "archive", "commits",
                   "tags", "releases", "issues"):
            cut = i
            rest = p[i + 1:]
            if rest and rest[0] == "-":
                rest = rest[1:]
            if rest and rest[0] in ("tree", "blob", "src", "raw"):
                rest = rest[1:]
            if rest and rest[0] in ("branch", "tag", "commit"):
                rest = rest[1:]
            if rest:
                branch = rest[0]
                sub = "/".join(rest[1:])
            break
    p = p[:cut]
    if len(p) < 2:
        return None
    return {"kind": "repo", "host": host, "owner": "/".join(p[:-1]),
            "repo": p[-1].replace(".git", ""), "branch": branch, "path": sub}


def parse(url):
    """dict(kind: repo|file|user|search, owner, repo, branch, path, q)."""
    u = urllib.parse.urlparse(str(url or "").strip())
    host = (u.hostname or "").lower()
    parts = [urllib.parse.unquote(p) for p in (u.path or "").split("/") if p]
    if host.startswith("www."):
        host = host[4:]
    if host in _forge_hosts():
        return _parse_forge(host, parts)
    if host == RAW_HOST:
        if len(parts) >= 4:
            return {"kind": "file", "owner": parts[0], "repo": parts[1],
                    "branch": parts[2], "path": "/".join(parts[3:])}
        return None
    if host not in WEB_HOSTS:
        return None
    qs = urllib.parse.parse_qs(u.query)
    if not parts:
        return {"kind": "search", "q": "topic:%s" % DEFAULT_TOPICS[0],
                "topics": list(DEFAULT_TOPICS)}
    if parts[0] == "topics" and len(parts) >= 2:
        return {"kind": "search", "q": "topic:%s" % parts[1]}
    if parts[0] == "search":
        q = (qs.get("q") or [""])[0].strip()
        return {"kind": "search", "q": q or "topic:%s" % DEFAULT_TOPICS[0]}
    if parts[0] in _RESERVED:
        return {"kind": "search", "q": "topic:%s" % DEFAULT_TOPICS[0]}
    if len(parts) == 1:
        return {"kind": "user", "owner": parts[0]}
    owner, repo = parts[0], re.sub(r"\.git$", "", parts[1])
    if len(parts) >= 4 and parts[2] == "blob":
        return {"kind": "file", "owner": owner, "repo": repo,
                "branch": parts[3], "path": "/".join(parts[4:])}
    if len(parts) >= 3 and parts[2] == "tree":
        return {"kind": "repo", "owner": owner, "repo": repo,
                "branch": parts[3] if len(parts) > 3 else "",
                "path": "/".join(parts[4:])}
    return {"kind": "repo", "owner": owner, "repo": repo, "branch": "",
            "path": ""}


# ------------------------------- план файлов --------------------------------
def _file_kind(path):
    low = path.lower()
    segs = low.split("/")
    if any(s in SKIP_PARTS for s in segs[:-1]) or ".min." in low:
        return ""
    ext = "." + low.rsplit(".", 1)[-1] if "." in segs[-1] else ""
    if ext in CODE_EXT:
        return "code"
    if ext in DOC_EXT and (segs[-1].startswith("readme") or
                           (len(segs) <= 3 and ext == ".md")):
        return "doc"
    return ""


def _priority(path, size):
    low = path.lower()
    segs = low.split("/")
    p = 0
    if segs[-1].startswith("readme"):
        p -= 100
    if low.endswith(".py"):
        p -= 20
    if any(s in ("test", "tests") or s.startswith("test_") for s in segs):
        p += 30
    p += len(segs) * 3
    p += min(size // 4096, 20)
    return p


def plan_repo(owner, repo, branch="", sub=""):
    """Составить план чтения репозитория -> full | raises GhErr."""
    full = "%s/%s" % (owner, repo)
    meta = _api("/repos/%s/%s" % (urllib.parse.quote(owner),
                                  urllib.parse.quote(repo)))
    branch = branch or meta.get("default_branch") or "main"
    try:
        tree = _api("/repos/%s/%s/git/trees/%s?recursive=1" % (
            urllib.parse.quote(owner), urllib.parse.quote(repo),
            urllib.parse.quote(branch, safe="")))
    except GhErr:
        if branch != meta.get("default_branch") and meta.get("default_branch"):
            sub = (branch + "/" + sub).strip("/")        # ветка с «/» в имени
            branch = meta["default_branch"]
            tree = _api("/repos/%s/%s/git/trees/%s?recursive=1" % (
                urllib.parse.quote(owner), urllib.parse.quote(repo),
                urllib.parse.quote(branch, safe="")))
        else:
            raise
    items = []
    sub = (sub or "").strip("/")
    for t in tree.get("tree") or []:
        if t.get("type") != "blob":
            continue
        p = str(t.get("path") or "")
        sz = int(t.get("size") or 0)
        if sub and not (p == sub or p.startswith(sub + "/")):
            continue
        if sz <= 0 or sz > FILE_MAX or not _file_kind(p):
            continue
        items.append([p, sz, str(t.get("sha") or "")])
    items.sort(key=lambda x: _priority(x[0], x[1]))
    items = items[:_files_max()]
    lic = ((meta.get("license") or {}).get("spdx_id") or "") \
        if isinstance(meta.get("license"), dict) else ""
    with LOCK:
        d = _load()
        old = d["repos"].get(full) or {}
        done = old.get("done") or {}
        d["repos"][full] = {
            "branch": branch, "lic": lic, "stars": meta.get("stargazers_count"),
            "desc": str(meta.get("description") or "")[:160],
            "files": items, "done": done,
            "planned_ts": time.time(), "fails": old.get("fails") or {}}
        _save(d)
    WAKE.set()
    return full


def _pick_repos(spec):
    """Для ссылки-пользователя/поиска — список (owner, repo)."""
    out = []
    if spec["kind"] == "user":
        rs = _api("/users/%s/repos?sort=pushed&per_page=40" %
                  urllib.parse.quote(spec["owner"]))
        rs = [r for r in rs if not r.get("fork") and not r.get("archived")]
        rs.sort(key=lambda r: -int(r.get("stargazers_count") or 0))
        out = [(r["owner"]["login"], r["name"]) for r in rs]
    else:
        topics = spec.get("topics") or [None]
        for tp in topics[:3]:
            q = spec["q"] if tp is None else "topic:%s" % tp
            if "stars:" not in q:
                q += " stars:>200"
            if "language:" not in q and "topic:" in q:
                pass
            r = _api("/search/repositories?q=%s&sort=stars&per_page=10" %
                     urllib.parse.quote(q))
            for it in r.get("items") or []:
                out.append((it["owner"]["login"], it["name"]))
            if len(out) >= _repos_max():
                break
    return out[:_repos_max()]



# ------------------------- чтение АРХИВОМ (ZIP) ------------------------------
# Работает без API и без raw-хоста (у GitHub в России они чаще закрыты, чем
# сам архив) и на любых площадках: скачиваем zip репозитория один раз,
# кладём в data/ghzip/ и читаем файлы из него по одному.
import io
import os
import zipfile


def _zip_dir():
    d = store.p("ghzip")
    os.makedirs(d, exist_ok=True)
    return d


def _archive_urls(host, owner, repo, branch=""):
    br = [b for b in (branch, "HEAD", "main", "master") if b]
    seen, out = set(), []
    o = urllib.parse.quote(owner, safe="/")
    r = urllib.parse.quote(repo)
    for b in br:
        if host in ("github.com", "www.github.com"):
            if b == "HEAD":
                cand = ["https://codeload.github.com/%s/%s/zip/HEAD" % (o, r),
                        "https://github.com/%s/%s/archive/HEAD.zip" % (o, r)]
            else:
                cand = ["https://codeload.github.com/%s/%s/zip/refs/heads/%s"
                        % (o, r, b)]
        elif "gitlab" in host or host in ("invent.kde.org", "salsa.debian.org",
                                          "sourcecraft.dev"):
            cand = ["https://%s/%s/%s/-/archive/%s/%s-%s.zip"
                    % (host, o, r, b, r, b.replace("/", "-"))]
            if host == "sourcecraft.dev":
                cand.append("https://%s/%s/%s/archive/%s.zip" % (host, o, r, b))
        elif host == "bitbucket.org":
            cand = ["https://bitbucket.org/%s/%s/get/%s.zip" % (o, r, b)]
        elif host == "gitee.com":
            cand = ["https://gitee.com/%s/%s/repository/archive/%s.zip"
                    % (o, r, b)]
        elif host == "gitflic.ru":
            cand = ["https://gitflic.ru/project/%s/%s/archive/%s.zip"
                    % (o, r, b),
                    "https://gitflic.ru/project/%s/%s/archive/%s/%s.zip"
                    % (o, r, b, r)]
        else:          # Gitea/Forgejo-образные: codeberg, gitverse и др.
            cand = ["https://%s/%s/%s/archive/%s.zip" % (host, o, r, b)]
            if host == "gitverse.ru":
                cand.append("https://%s/%s/%s/archive/refs/heads/%s.zip"
                            % (host, o, r, b))
        for c in cand:
            if c not in seen:
                seen.add(c)
                out.append(c)
    return out


def _zip_max():
    """v3.176.1: предел архива — настройка gh_zip_mb (по умолчанию 150 МБ;
    было жёстко 40 МБ — большие репозитории «не скачивались»)."""
    try:
        return max(5, min(2000, int(float(_s("gh_zip_mb", 150))))) * 1024 * 1024
    except (TypeError, ValueError):
        return 150 * 1024 * 1024


def _download_zip(host, owner, repo, branch=""):
    """-> (bytes, url) | raises GhErr (с перечнем причин)."""
    errs = []
    urls = _archive_urls(host, owner, repo, branch)[:8]
    if host in ("github.com", "www.github.com"):     # тот же архив через API
        urls.append("https://api.github.com/repos/%s/%s/zipball%s" % (
            urllib.parse.quote(owner), urllib.parse.quote(repo),
            ("/" + urllib.parse.quote(branch, safe="")) if branch else ""))
    for u in urls:
        why = ""
        for _try in range(2):                         # обрыв сети — ещё раз
            ok, data, _fn, _ct = webfetch.fetch_file(u, timeout=90,
                                                     max_bytes=_zip_max())
            if ok and bytes(data[:2]) == b"PK":
                return bytes(data), u
            why = str(data)[:160] if not ok else "пришёл не zip"
            if "HTTP 404" in why or "больше" in why or "не резолвится" in why:
                break
        errs.append("%s: %s" % (u.split("/")[2], why))
        if "больше" in why:                          # дальше будет то же
            break
    seen, uniq = set(), []
    for e in errs:
        if e not in seen:
            seen.add(e)
            uniq.append(e)
    raise GhErr("архив не скачался (%s)" % "; ".join(uniq[:4]))


def _zip_items(zdata, sub=""):
    """[(путь, размер, crc)] из zip (без верхней папки), отфильтровано."""
    zf = zipfile.ZipFile(io.BytesIO(zdata))
    names = [i for i in zf.infolist() if not i.is_dir()]
    tops = {i.filename.split("/", 1)[0] for i in names}
    strip = len(tops) == 1 and all("/" in i.filename for i in names)
    sub = (sub or "").strip("/")
    items = []
    for i in names:
        p = i.filename.split("/", 1)[1] if strip else i.filename
        if sub and not (p == sub or p.startswith(sub + "/")):
            continue
        if i.file_size <= 0 or i.file_size > FILE_MAX or not _file_kind(p):
            continue
        items.append([p, i.file_size, str(i.CRC)])
    items.sort(key=lambda x: _priority(x[0], x[1]))
    return items[:_files_max()], strip


def _register_zip(full, zdata, items, strip, meta):
    zpath = os.path.join(_zip_dir(), hashlib.md5(full.encode()).hexdigest()
                         + ".zip")
    with open(zpath, "wb") as f:
        f.write(zdata)
    with LOCK:
        d = _load()
        old = d["repos"].get(full) or {}
        d["repos"][full] = {
            "branch": meta.get("branch") or "", "lic": "", "stars": None,
            "desc": meta.get("desc") or "", "files": items,
            "done": old.get("done") or {}, "fails": old.get("fails") or {},
            "planned_ts": time.time(), "zip": zpath, "strip": bool(strip),
            "host": meta.get("host") or ""}
        _save(d)
    WAKE.set()
    return full


def plan_repo_zip(host, owner, repo, branch="", sub=""):
    """Скачать архив репозитория и составить план файлов -> full."""
    zdata, url = _download_zip(host, owner, repo, branch)
    items, strip = _zip_items(zdata, sub)
    if not items:
        raise GhErr("в архиве нет файлов кода (py, ino, c, cpp, h, js, sh, "
                    "sql) или README")
    full = "%s/%s" % (owner, repo) if host in ("github.com", "www.github.com") \
        else "%s/%s/%s" % (host, owner, repo)
    store.log("GITHUB", "архив %s: %d КБ, файлов в плане %d"
              % (url, len(zdata) // 1024, len(items)))
    return _register_zip(full, zdata, items, strip,
                         {"branch": branch, "host": host})


def ingest_zip(data, label="архив"):
    """ZIP репозитория, присланный вручную (панель/бот): читается так же, как
    ссылка. Работает всегда, даже если площадка с сервера недоступна."""
    try:
        items, strip = _zip_items(bytes(data))
    except zipfile.BadZipFile:
        return {"ok": False, "err": "это не zip-архив"}
    if not items:
        return {"ok": False, "err": "в архиве нет файлов кода или README"}
    label = re.sub(r"\.zip$", "", str(label or "архив"), flags=re.I)[:60]
    full = "zip/" + (re.sub(r"[^\w.\-]+", "_", label) or "archive")
    _register_zip(full, bytes(data), items, strip, {"host": "zip"})
    with LOCK:
        d = _load()
        d["links"]["zip:" + full] = [full]
        _save(d)
    start()
    store.log("GITHUB", "загружен архив %s: файлов в плане %d"
              % (label, len(items)))
    return {"ok": True, "repo": full, "files": len(items)}


# --------------------------- добавление ссылки ------------------------------
def _link_entry(url, **patch):
    """Обновить запись ссылки в списке «Ссылки с инструкциями» (панель)."""
    try:
        import study
        with study._LOCK:
            ls = study._links_raw()
            i = study._find(ls, url)
            e = dict(ls[i]) if i >= 0 else {
                "url": url, "kind": "github", "title": url, "chars": 0,
                "ok": False, "err": "", "aid": ""}
            e.update(patch)
            e["ts"] = time.time()
            if i >= 0:
                ls[i] = e
            else:
                ls.append(e)
            store.update_settings({"study_links": study._cap(ls)})
    except Exception:
        pass


def _progress(url):
    """(прочитано, всего, репозиториев) по ссылке."""
    d = _load()
    fulls = d["links"].get(url) or []
    done = total = 0
    for f in fulls:
        r = d["repos"].get(f) or {}
        total += len(r.get("files") or [])
        done += len([p for p, _s, _h in (r.get("files") or [])
                     if p in (r.get("done") or {}) or
                     p in (r.get("fails") or {})])
    return done, total, len(fulls)


def _update_entry(url):
    done, total, n = _progress(url)
    fin = total > 0 and done >= total
    d = _load()
    names = ", ".join((d["links"].get(url) or [])[:3])
    _link_entry(url, kind="github", title="GitHub: " + (names or url)[:90],
                reading=not fin, ok=fin or done > 0, err="",
                gh_done=done, chars=int(d["stats"].get("bytes") or 0),
                note=("прочитано %d из %d файлов, репозиториев: %d"
                      % (done, total, n)) if total else
                "составляю план файлов…")


def _plan_link(url):
    """Фон: ссылка -> репозитории -> планы файлов."""
    try:
        spec = parse(url)
        if not spec:
            raise GhErr("ссылка не похожа на GitHub")
        fulls = []
        host = spec.get("host") or "github.com"
        if spec["kind"] in ("repo", "file"):
            br = spec.get("branch") or ""
            sub = spec.get("path") or ""
            try:                          # 1) архивом — без API и raw-хоста
                full = plan_repo_zip(host, spec["owner"], spec["repo"], br,
                                     sub)
            except GhErr as ez:
                if host != "github.com":
                    raise GhErr("%s. Скачай ZIP репозитория в браузере и "
                                "загрузи его кнопкой «ZIP» (или пришли "
                                "файлом боту)" % ez)
                store.log("GITHUB", "архив не вышел (%s) — пробую API" % ez)
                full = plan_repo(spec["owner"], spec["repo"], br, sub)
            fulls.append(full)
        elif host != "github.com":
            raise GhErr("для этой площадки читаю ссылки на репозиторий "
                        "(владелец/репо)")
        else:
            for o, r in _pick_repos(spec):
                try:
                    try:
                        fulls.append(plan_repo_zip("github.com", o, r))
                    except GhErr:
                        fulls.append(plan_repo(o, r))
                except GhErr as e:
                    store.log("GITHUB", "репозиторий %s/%s пропущен: %s"
                              % (o, r, e))
        with LOCK:
            d = _load()
            d["links"][url] = fulls
            _save(d)
        if not fulls:
            raise GhErr("не нашла репозиториев по этой ссылке")
        _RETRY.pop(url, None)
        store.log("GITHUB", "ссылка %s: репозиториев %d, файлов в плане %d"
                  % (url, len(fulls), _progress(url)[1]))
        _update_entry(url)
        start()
        WAKE.set()
    except GhErr as e:
        with LOCK:
            d = _load()
            d["last_error"] = str(e)
            _save(d)
        _link_entry(url, kind="github", reading=False, ok=False,
                    err=str(e), note="")
        store.log("GITHUB", "ссылка %s: %s" % (url, e))
    except Exception as e:
        _link_entry(url, kind="github", reading=False, ok=False,
                    err="%s: %s" % (type(e).__name__, e), note="")
    finally:
        _PLANNING.discard(url)


def add_link(url, wait=False):
    """Принять ссылку GitHub: поставить в список и читать в фоне."""
    url = str(url or "").strip()
    spec = parse(url)
    if not spec:
        return {"ok": False, "url": url, "err": "это не ссылка на GitHub"}
    _link_entry(url, kind="github", title="GitHub: " + url[:80],
                reading=True, ok=False, err="", note="составляю план файлов…")
    if url in _PLANNING:
        return {"ok": True, "url": url, "kind": "github", "reading": True}
    _PLANNING.add(url)
    if wait:
        _plan_link(url)
    else:
        threading.Thread(target=_plan_link, args=(url,), daemon=True,
                         name="gh-plan").start()
    start()
    return {"ok": True, "url": url, "kind": "github", "reading": True,
            "title": "GitHub: " + url[:80], "chars": 0}


def forget(url, drop_articles=True):
    """Ссылку сняли: убрать план и (по умолчанию) её статьи из базы знаний."""
    url = str(url or "").strip()
    n = 0
    with LOCK:
        d = _load()
        fulls = d["links"].pop(url, None) or []
        shared = {f for u, fs in d["links"].items() for f in fs}
        for full in fulls:
            if full in shared:
                continue
            r = d["repos"].pop(full, None) or {}
            if r.get("zip"):
                try:
                    os.remove(r["zip"])
                except OSError:
                    pass
            if drop_articles:
                import kb
                for p in (r.get("done") or {}):
                    aid = "gh_" + hashlib.md5(
                        ("%s:%s" % (full, p)).encode()).hexdigest()[:16]
                    try:
                        kb.delete_article(aid)
                        n += 1
                    except Exception:
                        pass
        _save(d)
    return n


def chat_reply(text):
    """Ссылка GitHub в сообщении -> принять -> текст ответа."""
    url = find_link(text)
    if not url:
        return ""
    r = add_link(url)
    if not r.get("ok"):
        return "Ссылка на GitHub не принята: %s." % r.get("err")
    sp = parse(url) or {}
    what = {"repo": "репозиторий", "file": "файл", "user": "аккаунт",
            "search": "общую ссылку (подберу репозитории по звёздам)"
            }.get(sp.get("kind"), "ссылку")
    return ("Приняла %s GitHub и читаю его в фоне, файл за файлом: "
            "исходники идут в базу знаний, функции — в мозг, а своя LLM "
            "учится на них писать код. Ход чтения — в списке ссылок "
            "(вкладка «База знаний»)." % what)


# ----------------------------- чтение файла ---------------------------------
def _looks_ok(text, kind):
    if "\x00" in text:
        return False
    lines = text.split("\n")
    if lines and len(text) / max(1, len(lines)) > 300:        # минифицировано
        return False
    return True


def _ingest(full, branch, lic, path, text, kind):
    """Файл -> база знаний (+ codemind для функций). Возврат: число функций."""
    aid = "gh_" + hashlib.md5(("%s:%s" % (full, path)).encode()).hexdigest()[:16]
    src = "GitHub %s%s" % (full, (" (%s)" % lic) if lic and lic != "NOASSERTION"
                           else "")
    import kb
    if kind == "doc":
        kb.add_article("GitHub: %s — %s" % (full, path.rsplit("/", 1)[-1]),
                       text[:60000], source=src, did=aid)
        return 0
    kb.add_article("Исходник: %s/%s" % (full, path), text[:200000],
                   source=src, did=aid)
    nf = 0
    try:
        import codemind
        r = codemind.ingest("%s/%s" % (full.split("/")[-1], path), text)
        nf = int((r or {}).get("functions") or 0)
    except Exception:
        pass
    if path.lower().endswith(".py"):
        try:                                  # функции — для поиска образцов
            import codecorpus
            codecorpus.add_snips("%s/%s" % (full, path), text, lic or "")
        except Exception:
            pass
    return nf


def _note(full, path, nf, kind):
    """Мысль/журнал: что прочитала."""
    msg = "💻 Прочитала %s %s/%s%s" % (
        "файл" if kind == "code" else "документ", full, path,
        (" — функций: %d" % nf) if nf else "")
    store.log("GITHUB", msg)
    try:                       # в мысли — не каждый файл, чтобы не вытеснить
        n = int(_load()["stats"].get("files") or 0)      # настоящие мысли
        if kind == "doc" or n % 10 == 1:
            store.thoughts_add("своя LLM", msg, kind="read")
    except Exception:
        pass


def read_one():
    """Прочитать следующий файл из плана. Возврат: True (что-то прочитала),
    False (нечего читать / пауза)."""
    d = _load()
    # v3.176.1: пауза из-за лимита GitHub API касается только чтения через
    # сеть. Раньше она останавливала ВСЁ: архив не скачался -> план через
    # API -> лимит -> пауза -> файлы из уже скачанных архивов тоже не читались
    paused = time.time() < float(d.get("backoff_until") or 0)
    pick = None
    for full, r in d["repos"].items():
        if paused and not r.get("zip"):
            continue
        if r.get("zip") and not os.path.exists(r["zip"]):
            continue                      # архив пропал — его план не читаем
        done, fails = r.get("done") or {}, r.get("fails") or {}
        for p, sz, sha in r.get("files") or []:
            if (done.get(p) == (sha or sz)) or int(fails.get(p) or 0) >= 2:
                continue
            pick = (full, r, p, sz, sha)
            break
        if pick:
            break
    if not pick:
        return False
    full, r, path, sz, sha = pick
    ok, text, err = False, "", ""
    if r.get("zip"):                       # файл из локального архива
        try:
            with zipfile.ZipFile(r["zip"]) as zf:
                names = zf.namelist()
                top = names[0].split("/", 1)[0] + "/" if r.get("strip") \
                    and names else ""
                text = zf.read(top + path).decode("utf-8", "replace")
                ok = True
        except Exception as e:
            err = "архив: %s: %s" % (type(e).__name__, str(e)[:60])
    else:
        url = "https://%s/%s/%s/%s" % (RAW_HOST, full,
                                       urllib.parse.quote(r["branch"],
                                                          safe=""),
                                       urllib.parse.quote(path))
        try:
            status, _h, body = _get(url, max_bytes=FILE_MAX)
            if status == 200:
                text = bytes(body).decode("utf-8", "replace")
                ok = True
            else:
                err = "HTTP %s" % status
        except GhErr as e:
            err = str(e)
        if not ok and "HTTP 404" not in err:
            # v3.176.1: raw.githubusercontent.com часто недоступен — тот же
            # файл через API (contents, base64)
            try:
                j = _api("/repos/%s/contents/%s?ref=%s" % (
                    full, urllib.parse.quote(path),
                    urllib.parse.quote(r["branch"], safe="")))
                if isinstance(j, dict) and j.get("content"):
                    import base64
                    text = base64.b64decode(j["content"]).decode("utf-8", "replace")
                    ok, err = True, ""
            except GhErr as e2:
                err = "%s; через API: %s" % (err, e2)
    kind = _file_kind(path)
    nf = 0
    if ok and (len(text.strip()) < 30 or not _looks_ok(text, kind)):
        ok, err = False, "пустой/двоичный/минифицированный файл"
    if ok:
        try:
            nf = _ingest(full, r["branch"], r.get("lic") or "", path, text,
                         kind)
        except Exception as e:
            ok, err = False, "%s: %s" % (type(e).__name__, e)
    with LOCK:
        d = _load()
        rr = d["repos"].get(full)
        if rr is not None:
            if ok:
                rr.setdefault("done", {})[path] = sha or sz
                st = d["stats"]
                st["files" if kind == "code" else "docs"] += 1
                st["funcs"] += nf
                st["bytes"] += len(text)
                d["last_file"] = "%s/%s" % (full, path)
            else:
                f = rr.setdefault("fails", {})
                f[path] = int(f.get(path) or 0) + 1
                d["stats"]["errors"] += 1
                d["last_error"] = "%s/%s: %s" % (full, path, err)
        _save(d)
    if ok:
        _note(full, path, nf, kind)
    for url_, fulls in list(_load()["links"].items()):
        if full in fulls:
            _update_entry(url_)
    return True


# ------------------------------- фон ----------------------------------------
_RETRY = {}                               # url -> (попыток, время следующей)


def retry_failed(now=None):
    """v3.176.1: ссылка не спланировалась (архив не скачался, сеть, лимит) —
    раньше на этом всё и вставало. Теперь повтор сам: через 15 мин, 30, 1 ч…
    до 6 ч между попытками."""
    now = now or time.time()
    d = _load()
    n = 0
    for e in _gh_links():
        url = e.get("url") or ""
        if not url or url in _PLANNING or d["links"].get(url) or not e.get("err"):
            continue
        k, due = _RETRY.get(url, (0, 0.0))
        if now < due:
            continue
        _RETRY[url] = (k + 1, now + min(6 * 3600, 900 * (2 ** k)))
        if k == 0 and due == 0.0:
            _RETRY[url] = (1, now + 900)          # первый повтор — через 15 мин
            continue
        _PLANNING.add(url)
        _link_entry(url, note="повтор попытки %d…" % (k + 1))
        threading.Thread(target=_plan_link, args=(url,), daemon=True,
                         name="gh-plan").start()
        n += 1
    return n


def _loop():
    last_retry = 0.0
    while True:
        if time.time() - last_retry > 60:
            last_retry = time.time()
            try:
                retry_failed()
            except Exception:
                pass
        try:
            if not enabled():
                WAKE.wait(30)
                WAKE.clear()
                continue
            try:                       # v3.132: читает репозитории на вдохе
                import breath as _brgh
                if not _brgh.inhale("Учёба коду (GitHub)"):
                    time.sleep(3)
                    continue
            except Exception:
                pass
            worked = read_one()
        except Exception as e:
            worked = False
            store.log("GITHUB", "ошибка чтения: %s: %s" % (type(e).__name__, e))
        if worked:
            time.sleep(_every())
        else:
            WAKE.wait(60)
            WAKE.clear()


_RESUMED = [False]


def _gh_links():
    """Ссылки на репозитории из списка ссылок (они живут в settings.json)."""
    try:
        import study
        return [x for x in study.links()
                if x.get("kind") == "github" or is_github(x.get("url") or "")]
    except Exception:
        return []


def resume():
    """v3.176: после перезапуска облака — продолжить. Раньше план файлов
    сохранялся только когда был составлен целиком: облако перезапустилось
    посреди плана (обновление, падение) — ссылка висела «составляю план…»,
    а чтение не начиналось. Теперь недопланированные ссылки планируются
    заново, а прочитанные файлы (done) не читаются второй раз."""
    d = _load()
    n = 0
    for e in _gh_links():
        url = e.get("url") or ""
        if not url or url in _PLANNING:
            continue
        if d["links"].get(url):
            continue                      # план есть — читатель продолжит
        _PLANNING.add(url)
        threading.Thread(target=_plan_link, args=(url,), daemon=True,
                         name="gh-plan").start()
        n += 1
    if n:
        store.log("GITHUB", "после перезапуска продолжаю ссылок: %d" % n)
    return n


def start():
    if not _RESUMED[0]:
        _RESUMED[0] = True
        try:
            resume()
        except Exception as e:
            store.log("GITHUB", "продолжение ссылок не вышло: %s" % e)
    if _TH[0] and _TH[0].is_alive():
        return
    _TH[0] = threading.Thread(target=_loop, daemon=True, name="gh-reader")
    _TH[0].start()


def refresh_all():
    """Перечитка: перепланировать все известные ссылки (по sha — только
    изменившиеся файлы)."""
    for url in list((_load()["links"]).keys()):
        if url not in _PLANNING:
            _PLANNING.add(url)
            threading.Thread(target=_plan_link, args=(url,), daemon=True,
                             name="gh-plan").start()


def status():
    d = _load()
    repos = []
    for full, r in d["repos"].items():
        fl = r.get("files") or []
        repos.append({"repo": full, "lic": r.get("lic") or "",
                      "stars": r.get("stars"), "files": len(fl),
                      "done": len(r.get("done") or {}),
                      "desc": r.get("desc") or ""})
    links = []
    for e in _gh_links():
        u = e.get("url") or ""
        done, total, n = _progress(u)
        links.append({"url": u, "title": e.get("title") or u,
                      "note": e.get("note") or "", "err": e.get("err") or "",
                      "reading": bool(e.get("reading")) or u in _PLANNING,
                      "done": done, "total": total, "repos": n,
                      "retry_s": max(0, int(_RETRY.get(u, (0, 0))[1] - time.time()))
                      if e.get("err") else 0})
    return {"ok": True, "enabled": enabled(), "token_set": bool(_token()),
            "links": links,
            "thread": bool(_TH[0] and _TH[0].is_alive()),
            "stats": d["stats"], "repos": repos[-30:],
            "last_error": d.get("last_error") or "",
            "last_file": d.get("last_file") or "",
            "backoff_s": max(0, int(float(d.get("backoff_until") or 0)
                                    - time.time()))}
