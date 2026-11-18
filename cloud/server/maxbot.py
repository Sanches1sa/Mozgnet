# -*- coding: utf-8 -*-
# ===========================================================================
# maxbot.py — БОТ В МЕССЕНДЖЕРЕ МАКС, подключённый к мозгу облака (v3.24)
# ---------------------------------------------------------------------------
# Bot API МАКС (dev.max.ru/docs-api, сверено с OpenAPI-схемой
# github.com/max-messenger/api-schema, сентябрь 2026):
#   база     https://platform-api2.max.ru  (с 19.07.2026; старый
#            platform-api.max.ru НЕ работает; сертификат ТОЛЬКО Минцифры —
#            _ssl_context() добавляет Russian Trusted Root CA везде)
#   токен    заголовок Authorization: <access_token>
#            (через query-параметр токен больше не поддерживается)
#   GET /me                — кто я (имя, username, user_id)
#   GET /chats             — УДАЛЁН из API (июнь 2026) — чаты собираются
#                            ПО СОБЫТИЯМ (_see_chat / _forget_chat)
#   GET /updates?timeout=&marker=&types=   — long polling событий
#   POST /messages?chat_id=  {"text": "...", "attachments": []}
#   GET /messages?chat_id=&count=&before=  — ИСТОРИЯ чата (v3.24): новейшие
#                            первыми, следующая страница — before=<ts>
#                            (официальная OpenAPI-схема max-messenger:
#                             «Messages traversed in reverse direction»)
#   GET /videos/{token}    — прямые ссылки на видео (после удаления
#                            GET /files в сентябре 2026; у файлов, если у
#                            вложения нет payload.url, честная ошибка)
#   лимит: не более 2 сообщений в секунду в один чат
#   ВАЖНО: чтобы получать события из групповых чатов и каналов, бот должен
#   быть АДМИНИСТРАТОРОМ чата (право read_all_messages).
#
# Что бот умеет (только stdlib, честно, без внешнего LLM):
#   1. Отвечает на вопросы МОЗГОМ ОБЛАКА (brain.think — все зоны: база
#      знаний, материалы, интернет с источниками, математика с формулами,
#      воображение, гипотезы...).
#   2. УЧИТСЯ ПО СТАТЬЯМ ИЗ ГРУППЫ: сообщения длиннее минимума (настройка
#      maxbot_min_learn, по умолчанию 10 знаков — заявка владельца v3.23)
#      и Википедия-ссылки бот складывает в базу знаний облака
#      (kb.add_article) — мозг растёт прямо из общения в группе.
#      Голые ссылки, команды и обращения к боту — не статьи.
#   3. ДОКУМЕНТЫ (v3.23): вложения-файлы (docx, doc, rtf, pdf, txt, csv,
#      xlsx, pptx, html...) бот скачивает через GET /files?token=, сохраняет
#      к себе в data/files/МАКС/, вытаскивает текст (textutils.extract_text,
#      только stdlib) и складывает статью в базу знаний. В личке подтвер
#      ждает, в группах — тихо (журнал + счётчики), как обычная учёба.
#   4. СКАН ИСТОРИИ ЧАТА (v3.24, кнопка панели): читает историю чата через
#      GET /messages и загружает её в память нейросети — тексты сообщений
#      статьями в базу знаний, документы (docx, doc, rtf, pdf...) файлами
#      в data/files/МАКС + статьями. Уже выученное не заучивается повторно
#      (md5-дедуп + водяной знак scan_done), скан идёт в фоне с прогрессом.
#   5. Команды: /help /status /выучи <текст>; в личке отвечает на всё.
#   6. В группах отвечает только когда к нему обращаются (@username /
#      «бот ...» / «мозг ...» / ответ на его сообщение) — без спама.
#   7. SVG-блоки ([КАРТИНКА]/[ФОРМУЛА]) из ответов честно убираются —
#      мессенджер текстовый; красивые формулы и картинки остаются в панели.
#   8. ПАМЯТЬ ДИАЛОГА (v3.26, заявка владельца «чтобы бот ориентировался
#      на контекст»): бот помнит последние 12 реплик каждого чата
#      (data/maxbot.json, переживает перезапуск) и передаёт их мозгу:
#      продолжения («а почему?», «а когда он основан?», «расскажи ещё»)
#      ищутся по теме диалога, а «о чём мы говорили?» — честный пересказ
#      последних реплик. Команда «забудь» стирает память диалога чата;
#      выключатель — настройка maxbot_memory (панель: Макс-бот).
#   9. ПАМЯТЬ ИНСТРУКЦИЙ (v3.142, заявка владельца «инструкции из Макс
#      чата после скана собирались в JSON и бот к нему обращался как
#      к памяти»): всё командное из чатов (sudo/dnf/systemctl…, пути,
#      пошаговые рецепты) складывается в data/maxbot_instructions.json
#      (chatmem.py: живой сбор + проход по базе ПОСЛЕ скана) и на каждый
#      вопрос бот ищет там по теме и дописывает к ответу раздел
#      «📋 Из инструкций чата» — раньше в эту память он не заглядывал.
#      АНГЛИЙСКИЙ В ПОСТЕ — либо ссылка, либо команда: URL остаются
#      ссылками, командные строки/пути/ключи выделяются моноширинным
#      `кодом` (разметка МАКС), остальной текст не трогается.
#      ПОСЛЕДНЯЯ БУКВА НЕ ОБРЕЗАЕТСЯ: перед отправкой сообщение получает
#      невидимый концевой знак (NBSP) — его съедает рендер МАКС, если тот
#      отрезает лишний символ, а не букву ответа.
#
# Токен хранится в data/settings.json (maxbot_token), включение — настройка
# maxbot_on (панель: Настройки -> «Макс-бот»). Никогда не пишется в журнал
# целиком — только первые 4 знака.
# ===========================================================================
import hashlib
import json
import os
import re
import tempfile
import threading
import time
import urllib.parse
import urllib.request

import kb
import store
import textutils

API_BASE = "https://platform-api2.max.ru"       # устаревшая ссылка (см. DEFAULT_BASE)
API_FALLBACK = "https://botapi.max.ru"          # если основной хост молчит
DEFAULT_BASE = "https://platform-api2.max.ru"   # база Bot API (docs)
_OVERRIDE = [None]                               # запасной хост / тесты
POLL_TIMEOUT = 25                                # сек long-polling (0..90)
HTTP_TIMEOUT = POLL_TIMEOUT + 15
SEND_GAP = 0.65                                  # лимит МАКС: 2 сообщения/сек
LEARN_MIN = 10                                   # знаков — минимум статьи
                                                 # (настройка maxbot_min_learn,
                                                 #  заявка владельца v3.23)
LEARN_GAP = 20.0                                 # сек между статьями из чата
                                                 # (настройка maxbot_learn_gap)
MEM_TURNS = 12                                   # v3.26: реплик диалога в
                                                 # памяти на каждый чат
MEM_CHATS = 200                                  # v3.26: максимум чатов
                                                 # в памяти (старые — вылетают)
MEM_CHARS = 600                                  # v3.26: максимум знаков
                                                 # реплики в памяти
MAX_REPLY = 3800                                 # запас до лимита текста МАКС
UA = "neuro12-cloud-maxbot/3.26"

# v3.142: защита последней буквы + оформление английского
_EN_MARK = re.compile(r"(https?://[^\s)\]]+|`[^`\n]*`|\[[^\]]*\]\([^)]*\)|\*\*[^*\n]+\*\*)", re.IGNORECASE)
_CMDTOK = re.compile(
    r"(?<![\w`/@.~-])(?:"
    r"(?:sudo|dnf|yum|apt-get|apt|rpm|systemctl|journalctl|nmcli|ping|ssh|"
    r"scp|curl|wget|pip3?|python3?|docker|kubectl|git|chmod|chown|reboot|"
    r"arduino-app-cli|tar|unzip|nm|netsh|ipconfig|systemd|crontab|"
    r"rm|cp|mv|mkdir|lsblk|fdisk|mount|umount|dmesg|ls|cat|df|du|ps|kill|"
    r"sed|awk|grep|find|head|tail|nano|hostnamectl|timedatectl|ufw|"
    r"firewall-cmd|iptables|useradd|passwd|ifconfig)\b"
    r"(?:\s+[A-Za-z0-9_./=@:+~-]{1,60}){0,8}"
    r"|/[A-Za-z0-9_][\w./-]{1,80}"
    r"|~/[\w./-]{1,80}"
    r"|--?[A-Za-z][\w-]{1,24}(?:[ =][\w./=-]{1,40})?"
    r"|[A-Za-z0-9_][\w.-]{0,60}\.(?:service|conf|cfg|yaml|yml|sh|py|json|log|txt|md|deb|rpm)\b"
    r")(?![\w`])")

# v3.24: скан истории чата (GET /messages)
SCAN_PAGE = 50                                   # сообщений за запрос (МАКС
                                                 # разрешает до 100)
SCAN_PAGES_MAX = 4000                            # страниц с НОВЫМИ сообщениями
                                                 # на один чат за один скан
SCAN_WALK_MULT = 3                               # страницы-прогулки по уже
                                                 # выученному (предел 3*N+40)
SCAN_TIME_MAX = 4 * 60 * 60                      # v3.136: 4 ч (4000 страниц)
SCAN_STEP = 0.35                                 # пауза между страницами

_SCAN_TH = [None]                                # фоновый поток скана
_SCAN_STOP = threading.Event()                   # кнопка «Остановить»
_SCAN_START_LOCK = threading.Lock()              # без гонки двух кнопок
AUTO_WALK_PAGES = 4                              # v3.136: автоскан — только
                                                 # свежие страницы, не вся история

# v3.23: документы из чатов (вложения type=file)
DOC_EXTS = (".docx", ".doc", ".rtf", ".pdf", ".txt", ".md", ".csv", ".tsv",
            ".xlsx", ".pptx", ".html", ".htm", ".xml", ".json", ".log")
MAX_DOC_BYTES = 30 * 1024 * 1024                 # честный потолок 30 МБ

_STATE_LOCK = threading.RLock()                  # v3.23: RLock — _load()
                                                 # вызывается и под замком
_STATE = None                                    # data/maxbot.json
_TH = [None]                                     # поток long polling
_WAKE = threading.Event()                        # разбудить после настройки

_SVG = re.compile(r"\[(ФОРМУЛА|КАРТИНКА)\][\s\S]*?\[/\1\]")
_RAWLINK = re.compile(r"^\s*(https?://\S+|www\.\S+)\s*$", re.IGNORECASE)
_ADDR = ("бот", "мозг")                          # обращения без @username


# ------------- v3.33: ОДИН ОПРОСЧИК НА ТОКЕН (несколько копий сервера) -------
# Жалоба «ответов три»: на Windows одновременно работали несколько копий
# облака (автозапуск + запуск вручную + exe) — каждая опрашивала МАКС одним
# токеном и каждая отвечала (у одних приветствие есть, у других нет — у
# каждой своя память «кого уже поздоровали»). Теперь:
#   * опрос ведёт ОДИН процесс на компьютере — тот, кто взял замок-файл
#     (msvcrt/fcntl в каталоге временных файлов; замок снимается сам, если
#     процесс упал); остальные копии ждут в режиме «резерв» и подхватывают
#     опрос, если первая остановлена;
#   * каждое сообщение МАКС и каждый исходящий текст «занимаются» атомарным
#     файлом-меткой — даже если замок не сработал (другая учётная запись,
#     другой каталог данных), второй процесс увидит метку и промолчит.
_LEAD = {"hash": "", "fh": None, "note": "", "pid": 0}


def _tok_hash():
    return hashlib.md5(token().encode("utf-8", "replace")).hexdigest()[:10]


def _claim_dirs(h=None):
    h = h or _tok_hash()
    dirs = [os.path.join(tempfile.gettempdir(), "neuro12-max-" + h)]
    try:
        dirs.append(os.path.join(store.DATA_DIR, "maxclaims"))
    except Exception:
        pass
    return dirs


def _lock_path(h):
    return os.path.join(tempfile.gettempdir(), "neuro12-maxbot-%s.lock" % h)


def _release_lead():
    fh = _LEAD.get("fh")
    _LEAD.update(hash="", fh=None, pid=0)
    if fh:
        try:
            fh.close()
        except Exception:
            pass


def _holder_pid(path):
    try:
        with open(path, "rb") as f:
            f.seek(8)
            return int((f.read(16) or b"0").split()[0])
    except Exception:
        return 0


def _is_leader():
    """True — этот процесс ведёт опрос МАКС с текущим токеном."""
    h = _tok_hash()
    if _LEAD["fh"] is not None and _LEAD["hash"] == h:
        return True
    if _LEAD["fh"] is not None:                    # токен сменили
        _release_lead()
    path = _lock_path(h)
    fh = None
    try:
        fh = open(path, "a+b")
        fh.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        fh.seek(8)
        fh.write(("%d" % os.getpid()).encode("ascii").ljust(16))
        fh.flush()
        _LEAD.update(hash=h, fh=fh, pid=os.getpid(),
                     note="опрос МАКС веду я (PID %d)" % os.getpid())
        return True
    except Exception:
        try:
            if fh:
                fh.close()
        except Exception:
            pass
        other = _holder_pid(path)
        _LEAD["note"] = ("опрос МАКС уже ведёт другая копия облака (PID %s) — "
                         "эта копия в резерве" % (other or "?"))
        return False


def _claim(kind, key, window=0.0):
    """Атомарно «занять» событие. True — занял я (обрабатываю), False — уже
    занято другим процессом. window>0: метка действует window секунд
    (для исходящих текстов), иначе — вечно (для mid входящих; чистим
    метки старше 3 суток)."""
    name = "%s-%s" % (kind, hashlib.md5(
        str(key).encode("utf-8", "replace")).hexdigest())
    ok = True
    for d in _claim_dirs():
        try:
            os.makedirs(d, exist_ok=True)
            path = os.path.join(d, name)
            if window > 0 and os.path.exists(path):
                if time.time() - os.path.getmtime(path) < window:
                    ok = False
                    continue
                os.utime(path, None)               # окно истекло — обновим
                continue
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(fd)
        except FileExistsError:
            ok = False
        except Exception:
            continue                               # нет прав — не мешаем
    if ok and (hash(name) % 100 == 0):             # изредка чистим старьё
        _claims_gc()
    return ok


def _claims_gc(max_age=3 * 86400):
    now = time.time()
    for d in _claim_dirs():
        try:
            for fn in os.listdir(d):
                fp = os.path.join(d, fn)
                if now - os.path.getmtime(fp) > max_age:
                    os.remove(fp)
        except Exception:
            pass


# ------------------------------- состояние ----------------------------------
def _defaults():
    return {"marker": None, "bot": {}, "chats": {},
            "stats": {"recv": 0, "ans": 0, "learned": 0, "skipped": 0,
                      "sent": 0, "errors": 0, "docs": 0},
            "docs_recent": [],
            "last_error": "", "last_ok": 0.0, "started": 0.0,
            "seen": [], "last_learn": {}, "last_learn_err": {},
            "scan": {},                           # v3.24: прогресс скана
            "scan_done": {},                      # v3.24: что уже выучено
            "mem": {}}                            # v3.26: память диалогов


def _load():
    global _STATE
    if _STATE is None:
        with _STATE_LOCK:
            if _STATE is None:
                st = store.load_json("maxbot.json", None)
                if not isinstance(st, dict):
                    st = _defaults()
                for k, v in _defaults().items():
                    st.setdefault(k, v)
                # v3.23: новый счётчик документов — старые maxbot.json
                # дополняются без потери статистики
                if not isinstance(st.get("stats"), dict):
                    st["stats"] = {}
                for k, v in _defaults()["stats"].items():
                    st["stats"].setdefault(k, v)
                if not isinstance(st.get("docs_recent"), list):
                    st["docs_recent"] = []
                if not isinstance(st.get("mem"), dict):   # v3.26: память
                    st["mem"] = {}                        # диалогов
                _STATE = st
    return _STATE


def _save():
    with _STATE_LOCK:
        st = _STATE or _defaults()
        st["seen"] = (st.get("seen") or [])[-800:]
        try:
            store.save_json("maxbot.json", st)
        except Exception:
            pass


def enabled():
    return bool(store.settings().get("maxbot_on", False))


def token():
    return str(store.settings().get("maxbot_token") or "").strip()


def _min_learn():
    """v3.23: минимальная длина статьи из групп (знаков). Настройка панели
    maxbot_min_learn, по умолчанию 10 — заявка владельца «собирать статьи
    от 10 знаков». Честные границы: 1..100000."""
    try:
        v = int(store.settings().get("maxbot_min_learn"))
    except (TypeError, ValueError):
        v = LEARN_MIN
    return max(1, min(100000, v))


def _learn_gap_s():
    """v3.23: пауза между статьями из одного чата (сек), настройка
    maxbot_learn_gap (0..3600)."""
    try:
        v = float(store.settings().get("maxbot_learn_gap"))
    except (TypeError, ValueError):
        v = LEARN_GAP
    return max(0.0, min(300.0, v))  # v3.173: не дольше 5 мин даже при ошибке настройки


# --------------------- память диалога (v3.26) --------------------------------
def _memory_on():
    """v3.26: память диалога вкл/выкл — настройка maxbot_memory (по умолчанию
    вкл). Память хранится в data/maxbot.json и переживает перезапуск
    сервера; «забудь» в чате стирает её для этого чата."""
    return bool(store.settings().get("maxbot_memory", True))


def _mem_get(chat_id):
    """Реплики памяти диалога чата (свежая копия, старые -> новые)."""
    if not _memory_on() or not chat_id:
        return []
    with _STATE_LOCK:
        mem = _load().get("mem") or {}
        return [dict(m) for m in (mem.get(str(chat_id)) or [])]


def _mem_add(chat_id, role, text):
    """Добавить реплику в память диалога чата (храню последние MEM_TURNS,
    не более MEM_CHARS знаков каждая; подряд одинаковые не дублируются —
    повторная доставка события МАКС не раздувает память). Чатов не больше
    MEM_CHATS — самые неактивные забываются."""
    t = re.sub(r"\s+", " ", str(text or "")).strip()
    if not t or not chat_id or not _memory_on():
        return
    with _STATE_LOCK:
        st = _load()
        mem = st.setdefault("mem", {})
        lst = mem.setdefault(str(chat_id), [])
        if lst and lst[-1].get("role") == str(role) and \
                lst[-1].get("text") == t:
            return
        lst.append({"role": str(role), "text": t[:MEM_CHARS],
                    "ts": time.time()})
        mem[str(chat_id)] = lst[-MEM_TURNS:]
        if len(mem) > MEM_CHATS:             # самые старые чаты — забыть
            for k in sorted(mem, key=lambda k: (mem[k] or [{}])[-1]
                            .get("ts", 0))[:-MEM_CHATS]:
                mem.pop(k, None)
    _save()


def _mem_ctx(chat_id):
    """ctx для brain.think: последние реплики диалога (как в чате облака —
    не более 6). Порядок: старые -> новые."""
    return [{"role": m.get("role"), "text": m.get("text")}
            for m in _mem_get(chat_id)[-6:]]


def _mem_forget(chat_id):
    """Стереть память диалога чата (команда «забудь»). Возврат: сколько
    реплик забыто."""
    n = 0
    with _STATE_LOCK:
        mem = _load().setdefault("mem", {})
        n = len(mem.pop(str(chat_id), None) or [])
    _save()
    return n


# ------------------------------- HTTP-клиент --------------------------------
class MaxErr(Exception):
    """Честная ошибка Bot API МАКС."""


def _base_url():
    """Хост API. Для тестов env NEURO_MAX_API=http://127.0.0.1:... —
    разрешён ТОЛЬКО локальный хост (иначе только https, по документации)."""
    env = (os.environ.get("NEURO_MAX_API") or "").rstrip("/")
    if env:
        u = urllib.parse.urlparse(env)
        if u.hostname in ("127.0.0.1", "localhost", "::1"):
            return env
    if _OVERRIDE[0]:
        return _OVERRIDE[0]
    return DEFAULT_BASE


def _ssl_context():
    """v3.23.2: SSL-контекст для запросов к МАКС: системные CA + сертификат
    Минцифры (Russian Trusted Root CA). С 19 июля 2026 platform-api2.max.ru
    отдаёт ТОЛЬКО сертификат Минцифры (проверено живым тестом: без него
    0/10 соединений, с ним 10/10) — без этого контекста urllib падает с
    CERTIFICATE_VERIFY_FAILED и бот мёртв. Контекст кешируется."""
    global _SSL_CTX
    if _SSL_CTX is None:
        import ssl
        ctx = ssl.create_default_context()
        try:
            import ruca
            ca = getattr(ruca, "CADATA", "")
            if ca:
                ctx.load_verify_locations(cadata=ca)
        except Exception:
            pass
        _SSL_CTX = ctx
    return _SSL_CTX


_SSL_CTX = None


def _call(method, path, obj=None, timeout=HTTP_TIMEOUT):
    """Запрос к Bot API. Токен — ТОЛЬКО в заголовке Authorization.
    Возврат: dict из JSON. Ошибки — MaxErr с честной причиной.
    Хост API может быть отравлен DNS-фильтром (как Википедия в v3.12) —
    поэтому СНАЧАЛА прямой https на настоящий IP через DoH (механика
    webfetch), и только если DoH недоступен — обычный urllib."""
    tok = token()
    if not tok:
        raise MaxErr("токен бота не задан (Настройки -> Макс-бот)")
    url = _base_url() + path
    try:                        # v3.24.4: кириллица в адресе -> ascii,
        import webfetch         # иначе http.client/urllib падают с
        url = webfetch.safe_url(url)   # «'ascii' codec can't encode…»
    except Exception:
        pass
    raw_body = json.dumps(obj).encode("utf-8") if obj is not None else None
    u = urllib.parse.urlparse(url)
    host = u.hostname or ""
    path_qs = (u.path or "/") + (("?" + u.query) if u.query else "")
    last = ""
    if host not in ("127.0.0.1", "localhost", "::1"):
        try:
            import webfetch
            import ruca
            extra_ca = ruca.CADATA        # *.max.ru подписан Минцифры
            ips = webfetch._doh_resolve(host)
        except Exception as e:
            extra_ca, ips, last = None, [], "%s: %s" % (type(e).__name__, e)
        for ip in (ips or [])[:3]:
            try:
                status, _h, data = webfetch._https_call(
                    host, ip, path_qs, timeout=timeout, method=method,
                    raw_body=raw_body,
                    content_type="application/json"
                    if raw_body is not None else None,
                    extra_headers={"Authorization": tok, "User-Agent": UA},
                    extra_ca=extra_ca)
                if status == 401:
                    raise MaxErr("МАКС: токен недействителен (401) — "
                                 "проверь токен бота в настройках")
                if status >= 400:
                    raise MaxErr("МАКС: HTTP %s: %s"
                                 % (status, data[:300].decode(
                                     "utf-8", "replace")))
                return json.loads(data.decode("utf-8", "replace") or "{}")
            except MaxErr:
                raise
            except Exception as e:
                last = "%s: %s" % (type(e).__name__, e)
    req = urllib.request.Request(
        url, data=raw_body, method=method,
        headers={"Authorization": tok, "User-Agent": UA,
                 "Content-Type": "application/json"})
    try:
        # v3.23.2: urllib-фолбэк тоже с сертификатом Минцифры — раньше тут
        # был голый контекст и бот умирал на SSL, когда DoH недоступен
        with urllib.request.urlopen(req, timeout=timeout,
                                    context=_ssl_context()) as r:
            return json.loads(r.read().decode("utf-8", "replace") or "{}")
    except urllib.error.HTTPError as e:
        try:
            body = e.read().decode("utf-8", "replace")[:300]
        except Exception:
            body = ""
        if e.code == 401:
            raise MaxErr("МАКС: токен недействителен (401) — проверь токен "
                         "бота в настройках")
        raise MaxErr("МАКС: HTTP %s: %s" % (e.code, body or e.reason))
    except MaxErr:
        raise
    except Exception as e:
        raise MaxErr("МАКС: %s: %s"
                     % (type(e).__name__, last or e))


def _poll(marker):
    """Одно long-polling чтение: список обновлений + новый marker.
    Без marker МАКС отдаёт только ПОСЛЕДНЕЕ обновление — так бот при
    первом запуске не захлёбывается старой историей группы."""
    q = "/updates?timeout=%d&types=message_created,bot_added,bot_started" \
        % POLL_TIMEOUT
    if marker:
        q += "&marker=%s" % urllib.parse.quote(str(marker))
    d = _call("GET", q)
    ups = d.get("updates") if isinstance(d, dict) else None
    if not isinstance(ups, list):
        ups = []
    return ups, d.get("marker")


def me(refresh=False):
    """Инфо о боте (GET /me) — кешируется в состоянии."""
    st = _load()
    if st.get("bot") and not refresh:
        return st["bot"]
    d = _call("GET", "/me")
    bot = {"user_id": d.get("user_id"), "name": d.get("name"),
           "username": d.get("username"), "active": bool(d.get("active")),
           "ts": time.time()}
    with _STATE_LOCK:
        st["bot"] = bot
    _save()
    return bot


def refresh_chats():
    """Список групп/каналов. ВНИМАНИЕ (v3.23.2, по docs dev.max.ru): метод
    GET /chats УДАЛЁН из API с июня 2026 (вместо него МАКС предлагает
    webhook-подписки или сбор chat_id по событиям). Мы работаем по long
    polling — чаты собираются ПО СОБЫТИЯМ (_see_chat: bot_added,
    message_created; bot_removed — удаляет). Никаких запросов к мёртвому
    методу не делаем — раньше это был гарантированный -1 ошибка при
    каждом старте."""
    with _STATE_LOCK:
        cs = dict(_load().get("chats") or {})
        _load()["chats_note"] = ("GET /chats удалён из МАКС (июнь 2026) — "
                                 "чаты собираю по событиям, живых: %d"
                                 % len(cs))
    _save()
    return cs


def _see_chat(chat_id, chat_type=""):
    """Запомнить чат по событию (замена удалённому GET /chats)."""
    if not chat_id:
        return
    with _STATE_LOCK:
        cs = _load().setdefault("chats", {})
        cur = cs.get(str(chat_id)) or {}
        cur.update({"chat_id": chat_id, "type": chat_type or cur.get("type")
                    or "", "seen": time.time()})
        cs[str(chat_id)] = cur
        need_title = not cur.get("title") and \
            time.time() - float(cur.get("title_try") or 0) > 600
        if need_title:
            cur["title_try"] = time.time()
        if len(cs) > 100:                # помечаем старые, но не растём
            for k in sorted(cs, key=lambda k: cs[k].get("seen") or 0)[:-100]:
                cs.pop(k, None)
    _save()
    if need_title:
        threading.Thread(target=_fetch_title, args=(chat_id,), daemon=True,
                         name="maxbot-title").start()


def _fetch_title(chat_id):
    """v3.72: название чата (GET /chats/{id}) — раньше оно нигде не бралось,
    и чат «База знаний» в статьях/панели назывался «чат 123»."""
    try:
        r = _call("GET", "/chats/%s" % chat_id, timeout=10)
        t = str((r or {}).get("title") or "").strip()[:120]
        if t:
            with _STATE_LOCK:
                c = (_load().get("chats") or {}).get(str(chat_id))
                if c is not None:
                    c["title"] = t
            _save()
    except Exception:
        pass


def _forget_chat(chat_id):
    """v3.23.2: bot_removed — убрать чат из кеша (рекомендация docs:
    «удалите chat_id при возникновении соответствующих событий»)."""
    if not chat_id:
        return
    with _STATE_LOCK:
        cs = _load().get("chats") or {}
        cs.pop(str(chat_id), None)
    _save()


def chat_title(chat_id):
    """Название чата из кеша /chats (для источника статей)."""
    c = (_load().get("chats") or {}).get(str(chat_id)) or {}
    return c.get("title") or ("чат %s" % chat_id)


# ------------------------------- отправка -----------------------------------
_last_send = {}                                  # chat_id -> ts (2 сообщения/с)
_sent_recent = {}                                # v3.32: chat_id -> {md5: ts}
DUP_SEND_S = 180.0                               # v3.32: окно защиты от дублей


def _en_fix(t):
    """v3.142: АНГЛИЙСКИЙ В ПОСТЕ — либо ссылка, либо команда (заявка
    владельца). URL и [текст](адрес) остаются ссылками; командные строки
    (sudo/dnf/systemctl…), пути (/etc/…, ~/…), ключи (--flag) и файлы
    (*.service, *.sh…) выделяются моноширинным `кодом` — разметка МАКС.
    Строка целиком — как команда, только если она почти вся латиница
    (русская фраза с командой внутри получает `код` лишь на команде)."""
    def _lat_ratio(s):
        letters = [ch for ch in s if ch.isalpha()]
        if not letters:
            return 0.0
        return sum(1 for ch in letters if ch.isascii()) / float(len(letters))

    parts = _EN_MARK.split(str(t or ""))
    for i in range(0, len(parts), 2):          # чётные — чистый текст
        p = parts[i]
        if not p or "`" in p:
            continue
        out = []
        for ln in p.split("\n"):
            s = ln.strip()
            if s and "http" not in s[:9] and len(s) < 300 and \
                    _lat_ratio(s) >= 0.9 and \
                    (_CMD_LINE.match(s) or _looks_cmd(s)):
                out.append(ln.replace(s, "`" + s + "`", 1))
            else:
                out.append(_CMDTOK.sub(lambda m: "`%s`" % m.group(0), ln))
        parts[i] = "\n".join(out)
    return "".join(parts)


def _last_letter_safe(t):
    """v3.142: ПОСЛЕДНЯЯ БУКВА НЕ ОБРЕЗАЕТСЯ. Рендер МАКС отрезает последний
    знак сообщения (наблюдалось на живых постах) — снабжаем текст невидимым
    концевым NBSP: его съест рендер, а не буква ответа. Заодно убираем
    НЕЗАКРЫТЫЙ маркер разметки в конце (нечётное число `*` или `_` заставляет
    парсер глотать хвост); парный закрывающий ` и ** не трогаем."""
    t = str(t or "").rstrip()
    if t:
        for mk in ("**", "~~", "++", "`", "*", "_", "~"):
            if t.endswith(mk) and t.count(mk) % 2 == 1:
                while t.endswith(mk):
                    t = t[:-len(mk)]
                t = t.rstrip()
                break
    if not t:
        return t
    return t + "\u00a0"


_in_split = threading.local()             # v3.176.2: идёт отправка частями


def send(chat_id, text):
    """Отправить текст в чат. Длинный ответ честно режется с пометкой.
    v3.142: английский оформляется ссылкой/командой, концевой NBSP
    защищает последнюю букву."""
    t = clean_text(__import__("latexlite").render_dollars(str(text or "").replace("\u2063", ""), "plain"), 10 ** 9)
    t = _en_fix(t)
    if not t:
        return False
    if len(t) > MAX_REPLY and not getattr(_in_split, "on", False):
        # v3.176.2: длиннее лимита МАКС — не обрезаем, а шлём частями
        import replytpl
        _in_split.on = True
        try:
            ok = False
            for p_ in replytpl.split(t, limit=MAX_REPLY - 200, parts=12):
                ok = send(chat_id, p_) or ok
            return ok
        finally:
            _in_split.on = False
    if len(t) > MAX_REPLY:                   # часть всё ещё длинная (редко)
        t = textutils.cut_at_sentence(t, MAX_REPLY - 60) + "\n\n[…]"
    now = time.time()
    # v3.32: один и тот же текст в тот же чат не чаще раза в DUP_SEND_S секунд
    # (повторная доставка события МАКС / два пути ответа раньше давали три
    # одинаковых сообщения подряд)
    key = hashlib.md5(t.encode("utf-8", "replace")).hexdigest()
    with _STATE_LOCK:
        prev = _sent_recent.get(str(chat_id)) or {}
        if now - prev.get(key, 0.0) < DUP_SEND_S:
            _load()["stats"]["skipped"] += 1
            return False
        prev = {k: v for k, v in prev.items() if now - v < DUP_SEND_S}
        prev[key] = now
        _sent_recent[str(chat_id)] = prev
    # v3.33: и между процессами — тот же текст в тот же чат другой копией
    if not _claim("out", "%s|%s" % (chat_id, key), window=DUP_SEND_S):
        with _STATE_LOCK:
            _load()["stats"]["skipped"] += 1
        return False
    gap = now - _last_send.get(str(chat_id), 0.0)
    if gap < SEND_GAP:
        time.sleep(SEND_GAP - gap)
    _url = "/messages?chat_id=%s" % urllib.parse.quote(str(chat_id))
    if textutils.has_bold(t):                # v3.45: жирные разделы ответа
        try:
            _call("POST", _url, {"text": _last_letter_safe(t),
                                 "format": "markdown",
                                 "attachments": []}, timeout=15)
        except Exception:
            _call("POST", _url, {"text": _last_letter_safe(
                textutils.strip_bold(t)), "attachments": []}, timeout=15)
    else:
        _call("POST", _url, {"text": _last_letter_safe(t),
                             "attachments": []}, timeout=15)
    _last_send[str(chat_id)] = time.time()
    with _STATE_LOCK:
        _load()["stats"]["sent"] += 1
    return True


def _send_illustrations(chat_id, texts):
    """v3.124: иллюстрации статьи из ответа — фото в чат МАКС."""
    if not store.settings().get("maxbot_images", True):
        return 0
    n = 0
    try:
        import kbimages
        for t in texts:
            for src, url, alt in kbimages.items(t)[:2]:
                data, ct = kbimages.read(src) if src.startswith("kbimg:") \
                    else (None, "")
                if not data and src.startswith("http"):
                    try:
                        import webfetch
                        ok, data, _fn, ct = webfetch.fetch_file(
                            src, timeout=15, max_bytes=kbimages.MAX_BYTES)
                        data = data if ok else None
                    except Exception:
                        data = None
                if data and "svg" not in str(ct) and \
                        send_image(chat_id, data, alt[:200], ctype=ct):
                    n += 1
    except Exception as e:
        store.log("МАКС", "иллюстрации: %s" % e)
    return n


def send_image(chat_id, png, caption="", ctype="image/png"):
    """v3.30: ФОТО В ЧАТ МАКС (формулы, картинки): 1) POST /uploads?type=
    image -> адрес загрузки; 2) multipart-загрузка файла (поле data) ->
    токен; 3) сообщение с вложением image. Вложение может быть «ещё не
    готово» — несколько повторов. Возврат True/False (ошибка — в журнал)."""
    try:
        up = _call("POST", "/uploads?type=image", None)
        url = str(up.get("url") or "")
        if not url:
            raise MaxErr("МАКС не дал адрес загрузки")
        bnd = "n12" + os.urandom(8).hex()
        ct_ = str(ctype or "image/png").split(";")[0].strip() or "image/png"
        ext_ = {"image/jpeg": "jpg", "image/gif": "gif",
                "image/webp": "webp"}.get(ct_, "png")
        body = (("--%s\r\nContent-Disposition: form-data; name=\"data\"; "
                 "filename=\"image.%s\"\r\nContent-Type: %s\r\n\r\n"
                 % (bnd, ext_, ct_)).encode("utf-8") + png +
                ("\r\n--%s--\r\n" % bnd).encode("utf-8"))
        req = urllib.request.Request(url, data=body, method="POST", headers={
            "Content-Type": "multipart/form-data; boundary=%s" % bnd,
            "User-Agent": UA})
        with urllib.request.urlopen(req, timeout=30,
                                    context=_ssl_context()) as r:
            res = json.loads(r.read().decode("utf-8", "replace") or "{}")
        payload = None
        if res.get("token"):
            payload = {"token": res["token"]}
        elif isinstance(res.get("photos"), dict) and res["photos"]:
            first = list(res["photos"].values())[0]
            payload = {"token": first.get("token")} if \
                isinstance(first, dict) and first.get("token") else \
                {"photos": res["photos"]}
        if not payload:
            raise MaxErr("загрузка не вернула токен: %s" % str(res)[:120])
        msg = {"text": clean_text(caption, 900) if caption else "",
               "attachments": [{"type": "image", "payload": payload}]}
        for attempt in range(4):
            try:
                _call("POST", "/messages?chat_id=%s"
                      % urllib.parse.quote(str(chat_id)), msg, timeout=15)
                return True
            except MaxErr as e:
                if "not.ready" in str(e) or "not ready" in str(e):
                    time.sleep(1.5 * (attempt + 1))
                    continue
                raise
    except Exception as e:
        store.log("МАКС", "фото не отправилось: %s" % e)
    return False


def _send_formulas(chat_id, texts):
    """Формулы ответа (SVG latexlite) — картинками PNG вслед за текстом."""
    if not store.settings().get("maxbot_images", True):
        return 0
    n = 0
    try:
        import svgraster
        for t in texts:
            for svg in svgraster.formula_blocks(t)[:3]:
                png = svgraster.rasterize(svg)
                if png and send_image(chat_id, png, ""):
                    n += 1
    except Exception as e:
        store.log("МАКС", "формулы картинкой: %s" % e)
    return n


def send_reply(chat_id, text, name=""):
    """v3.28: ШАБЛОННЫЙ ОТВЕТ — приветствие + ответ; не влез в одно
    сообщение — два (по абзацам, не посреди фразы). Выключено (tpl_bot) —
    как раньше: одно сообщение с честным сокращением."""
    try:
        import replytpl
        if not replytpl.cfg("tpl_bot"):
            return send(chat_id, text)
        t = clean_text(text, limit=10 ** 9)      # без сокращения: делим сами
        # v3.176.2: ответ НЕ обрывается. «Сообщений на ответ» — сколько
        # частей предпочтительно; длинному ответу — столько, сколько нужно
        # (до 12), без «[сокращено]». Запас 400 знаков на оформление
        # (ссылки, `команды`), которое send() добавляет позже.
        try:
            want = max(1, int(replytpl.cfg("tpl_parts") or 2))
        except (TypeError, ValueError):
            want = 2
        need = -(-len(t) // (MAX_REPLY - 400)) + 1
        parts = replytpl.build(t, name=name, chat_key="max:%s" % chat_id,
                               limit=MAX_REPLY - 400,
                               parts=max(want, min(12, need)))
    except Exception:
        return send(chat_id, text)
    ok = False
    for p in parts:
        ok = send(chat_id, p) or ok
    return ok


def clean_text(text, limit=MAX_REPLY):
    """Ответ мозга -> текст для мессенджера: без SVG-блоков, с честной
    пометкой при сокращении."""
    try:                      # v3.108: формула — читаемым текстом, не пропадает
        import latexlite
        text = latexlite.blocks_to_text(text)
    except Exception:
        pass
    try:                      # v3.124: иллюстрация — подписью (фото — следом)
        import kbimages
        text = kbimages.MARK_RE.sub(
            lambda m: "🖼 " + (m.group(3).strip() or "иллюстрация"), str(text or ""))
    except Exception:
        pass
    t = _SVG.sub(" ", str(text or ""))
    # v3.27: ссылка на документ облака в МАКС не откроется — подсказка
    t = re.sub(r"\[⬇?\s*Скачать ([^\]]+)\]\(/api/files/get/[^)]+\)",
               r"Файл «\1» — скачать в браузерной версии облака "
               r"(Файлы → документы).", t)
    t = re.sub(r"\n{3,}", "\n\n", t).strip()
    if len(t) > limit:
        # v3.27: только по концу предложения — без обрыва на полуфразе
        t = textutils.cut_at_sentence(t, limit - 60)
        t += "\n\n[сокращено — полный ответ в панели облака]"
    return t.strip()


# ------------------------------- интеллект ----------------------------------
def _think(text, sender, chat_id=None):
    """Вопрос -> ответ мозга (все зоны облака). v3.26: у бота ЕСТЬ память
    диалога — в brain.think уходит ctx (последние реплики этого чата):
    продолжения «а почему?», «а когда он основан?» ищутся по теме диалога,
    «о чём мы говорили?» — пересказ недавнего. chat_id — чат, чью память
    брать. v3.142: к ответу добавляется ПАМЯТЬ ИНСТРУКЦИЙ (chatmem).
    Отдельная функция — чтобы тест мог подменить."""
    import brain
    # v3.29: ЕДИНЫЙ МОЗГ — слабый ответ облака дополняет плата: успела за
    # unify_wait — в этот же ответ, позже — отдельным сообщением (только
    # знание с опорой; заготовки зон в мессенджер не идут)
    reply, zone, info = brain.think(
        text, sender=sender, ctx=_mem_ctx(chat_id),
        deliver=({"kind": "max", "chat_id": chat_id} if chat_id else None))
    return _with_instructions(reply, text), zone


def _with_instructions(reply, question):
    """v3.142: ПАМЯТЬ ИНСТРУКЦИЙ в ответе. Если в JSON-памяти (chatmem)
    есть инструкция по теме вопроса и мозг сам её не привёл — честно
    дописываем раздел «📋 Из инструкций чата». Ничего не выдумываем —
    только текст из памяти чата."""
    reply = str(reply or "")
    try:
        import chatmem
        line = chatmem.memory_line(question)
        if line and "Из инструкций чата" not in reply:
            return reply.rstrip() + "\n\n" + line
    except Exception:
        pass
    return reply


def _help_text():
    return ("Я — бот облака «мозг-12» (без внешнего LLM, только честные "
            "зоны: база знаний, материалы, интернет с источниками, "
            "математика, воображение).\n\n"
            "Я ПОМНЮ наш недавний диалог: спрашивай продолжения — «а почему?», "
            "«а когда он основан?», «расскажи ещё», «о чём мы говорили?». "
            "«забудь» — сотру память диалога.\n\n"
            "В личке: просто спроси — отвечу всем мозгом облака. Пришли "
            "документ (docx, doc, rtf, pdf, txt, csv…) — скачаю его к себе, "
            "прочитаю и запомню.\n"
            "В группе: обратись ко мне («бот ...», «мозг ...», @username "
            "или ответь на моё сообщение) — отвечу; статьи от %d знаков, "
            "ссылки на Википедию и документы я читаю и учусь по ним.\n\n"
            "«Что ты видишь?» (или /камера) — пришлю кадр с камеры мозга и "
            "скажу, что на нём; «что ты слышишь?» (или /слышу) — что мозг "
            "услышал в свой микрофон.\n\n"
            "Команды: /help — это справка, /status — что я знаю, "
            "«выучи: <текст>» — добавить статью в базу знаний, "
            "«забудь» — стереть память нашего диалога."
            % _min_learn())


def _status_text():
    st = _load()["stats"]
    arts = len(kb.list_articles(limit=1000))
    bot = _load().get("bot") or {}
    mem_n = len(_load().get("mem") or {})
    try:                      # v3.142: память инструкций из чатов
        import chatmem
        instr_n = chatmem.count()
    except Exception:
        instr_n = 0
    return ("Статус мозга облака:\n— статей в базе знаний: %d\n"
            "— сообщений получено: %d, ответов: %d\n"
            "— выучено статей из МАКС: %d (пропущено повторов: %d)\n"
            "— документов прочитано: %d (docx/doc/rtf/pdf...)\n"
            "— память диалога: %s (чатов в памяти: %d)\n"
            "— память инструкций из чата: %d (JSON, bot отвечает по ней)\n"
            "— минимум статьи из групп: %d знаков (пауза %s с)\n"
            "— ошибок API: %d\n— я в МАКС: %s%s\n— сервер: neuro12-cloud "
            "v%s" % (arts, st.get("recv", 0), st.get("ans", 0),
                     st.get("learned", 0), st.get("skipped", 0),
                     st.get("docs", 0),
                     ("вкл" if _memory_on() else "выкл"), mem_n,
                     instr_n,
                     _min_learn(),
                     _learn_gap_s(), st.get("errors", 0),
                     bot.get("name") or "?",
                     (" (@" + bot["username"] + ")") if bot.get("username")
                     else "", store.version()))


def _cmd(text, chat_id=None):
    """Команды бота. Возврат: текст ответа или None (не команда).
    v3.26: chat_id нужен команде «забудь» (стирает память диалога чата)."""
    t = str(text or "").strip()
    low = t.lower()
    if low in ("/forget", "/забыть", "забудь", "забудь всё", "забудь диалог",
               "забудь наш диалог", "забыть всё", "забыть диалог"):
        n = _mem_forget(chat_id) if chat_id else 0
        if n:
            return ("Забыла наш недавний диалог (%d %s) — начинаю с чистого "
                    "листа. Статьи в базе знаний остались на месте."
                    % (n, _ru_plural(n)))
        return ("Память нашего диалога и так пуста — забывать нечего. "
                "(Статьи из базы знаний это не трогает.)")
    if low in ("/start", "/help", "помощь"):
        return _help_text()
    if low == "/status" or low.startswith("/status "):
        return _status_text()
    try:                                  # v3.125: «/почему» — путь ответа
        import dialog
        if dialog.is_why(t):
            return dialog.explain("max:%s" % chat_id)
    except Exception:
        pass
    m = re.match(r"^(/?(?:выучи|learn)[\s:]+)([\s\S]+)$", low)
    if m:
        body = t[len(m.group(1)):].strip()
        res = learn_text(body, source="команда «выучи» в МАКС")
        if res.get("ok"):
            return "Выучила статью «%s» (%d знаков) — теперь знаю больше." \
                   % (res.get("title"), res.get("chars", 0))
        if res.get("dup"):
            return "Это я уже знаю — статью «%s» читала раньше." \
                   % res.get("title", "?")
        return "Не смогла выучить: %s" % res.get("error")
    return None


def _ru_plural(n):
    """«1 реплика / 2 реплики / 5 реплик» — для честных ответов о памяти."""
    n = abs(int(n or 0)) % 100
    if 11 <= n <= 14:
        return "реплик"
    d = n % 10
    if d == 1:
        return "реплика"
    if 2 <= d <= 4:
        return "реплики"
    return "реплик"


def learn_text(text, source="", min_chars=None):
    """Статья в базу знаний облака. Дедуп по md5 (честно «уже знаю»).
    v3.23: минимум знаков — настройка maxbot_min_learn (по умолчанию 10;
    был жёсткий порог 80). Возврат {ok, title, chars} | {ok:False, error}
    | {dup:True, title}."""
    body = re.sub(r"\s+", " ", str(text or "")).strip()
    if min_chars is None:
        min_chars = _min_learn()
    if len(body) < max(2, int(min_chars)):
        return {"ok": False, "error": ("текст слишком короткий для статьи "
                                       "(нужно хотя бы %s знаков)"
                                       % int(min_chars))}
    h = hashlib.md5(body.encode("utf-8", "replace")).hexdigest()
    st = _load()
    if h in (st.get("seen") or []):
        with _STATE_LOCK:
            st["stats"]["skipped"] += 1
        # v3.27: id уже выученной статьи — чтобы продолжение (команда
        # следующим сообщением) дописалось в неё
        return {"dup": True, "title": body[:60] + "…",
                "id": (st.get("seen_aid") or {}).get(h)}
    title = body[:80].strip()
    sp = body.find(". ")
    if 0 < sp < 80:
        title = body[:sp + 1]
    res = kb.add_article(title, body, source=source or "МАКС")
    if not res.get("ok"):
        return {"ok": False, "error": res.get("error") or "не сохранилась"}
    with _STATE_LOCK:
        st.setdefault("seen", []).append(h)
        sa = st.setdefault("seen_aid", {})        # v3.27: хеш -> статья
        sa[h] = res.get("id")
        if len(sa) > 800:
            for k in list(sa)[:len(sa) - 800]:
                sa.pop(k, None)
        st["stats"]["learned"] += 1
    _save()
    return {"ok": True, "title": title, "chars": res.get("chars", 0),
            "id": res.get("id")}


# v3.27: ПРОДОЛЖЕНИЕ СТАТЬИ. В группах часто пишут: «При зависании РЕД ОС
# Гравитон» — и СЛЕДУЮЩИМ сообщением саму команду («sudo …»). Раньше
# второе сообщение в течение паузы maxbot_learn_gap молча выбрасывалось, а
# позже становилось отдельной «статьёй» из одной латиницы, которую на
# русский вопрос не найти: бот отвечал описанием без команды. Теперь
# сообщения того же автора подряд (до _CONT_S секунд) дописываются в ОДНУ
# статью — описание и команда остаются вместе.
_CONT_S = 180.0


def _append_to_article(aid, text):
    """Дописать продолжение в статью KB. Возврат: True/False."""
    try:
        art = kb.article(aid)
        if not art:
            return False
        add = str(text or "").strip()
        old = str(art.get("text") or "").rstrip()
        if not add or add in old:
            return bool(add)
        res = kb.add_article(art.get("title") or add[:80], old + "\n" + add,
                             source=art.get("source") or "МАКС",
                             device=art.get("device") or None, did=aid)
        if res.get("ok"):
            h = hashlib.md5(re.sub(r"\s+", " ", add).encode(
                "utf-8", "replace")).hexdigest()
            with _STATE_LOCK:
                _load().setdefault("seen", []).append(h)
            return True
    except Exception:
        pass
    return False


def _learn_from_group(msg, chat_id, addressed=False):
    """Учёба по статье из группы: текст длиннее минимума (настройка,
    по умолчанию 10 знаков — v3.23) или Википедия-ссылка.
    Тихо (без спама в чат): счётчики + журнал. Пауза между статьями —
    настройка maxbot_learn_gap. Обращения к боту — это разговор,
    а не статья; голые ссылки и смайлы тоже не статьи."""
    body = ((msg.get("body") or {}).get("text") or "").strip()
    if not body or addressed:
        return
    now = time.time()
    uid = str((msg.get("sender") or {}).get("user_id") or "")
    # v3.27: продолжение того же автора (команда следом за описанием) —
    # в ТУ ЖЕ статью, без паузы и без минимума длины («reboot» — тоже ответ)
    la = (_load().get("last_art") or {}).get(str(chat_id)) or {}
    age = now - float(la.get("ts") or 0)
    link = msg.get("link") or {}
    cont = bool(la.get("aid")) and sum(ch.isalnum() for ch in body) >= 2 and (
        (uid and la.get("uid") == uid and age < _ctx_gap()) or     # v3.31
        (age < 300 and (_looks_cmd(body) or
                        str(la.get("tail") or "").endswith(":"))) or
        (isinstance(link, dict) and str(link.get("type") or "") == "reply"
         and age < 2 * _ctx_gap()))
    if cont:
        if _append_to_article(la["aid"], body):
            with _STATE_LOCK:
                la["ts"] = now
                la["tail"] = body[-40:]
            _save()
            try:                 # v3.142: продолжение — тоже в память инструкций
                import chatmem
                chatmem.remember(body, source="группа МАКС: " +
                                 chat_title(chat_id), chat_id=chat_id)
            except Exception:
                pass
            store.log("МАКС", "дописала продолжение в статью «%s» (чат %s)"
                      % (str(la.get("title") or "?")[:60], chat_id))
            return
    last = _load().get("last_learn") or {}
    if now - float(last.get(str(chat_id), 0)) < _learn_gap_s():
        return
    # 1) ссылки на Википедию — читаем страницу целиком (SSRF-защита
    #    webfetch: только https + белый список хостов)
    ok_learn = None
    for url in re.findall(r"https?://[^\s)\]]+", body)[:2]:
        page = urllib.parse.urlparse(url)
        host = (page.hostname or "").lower()
        if not host.endswith("wikipedia.org"):
            continue
        import webfetch
        got, page_or_why = webfetch.fetch_page(url)
        if got:
            p = page_or_why
            res = learn_text(p.get("text") or "", source="ссылка из МАКС: "
                             + url[:100])
            if res.get("ok"):
                ok_learn = "«%s» по ссылке" % (p.get("title") or "статья")
        else:
            with _STATE_LOCK:
                _load()["last_learn_err"][str(chat_id)] = str(page_or_why)[:160]
        break
    if ok_learn:
        with _STATE_LOCK:
            _load()["last_learn"][str(chat_id)] = now
        _save()
        store.log("МАКС", "выучила %s (чат %s)" % (ok_learn, chat_id))
        return
    # 2) текст длиннее минимума = статья (что публикуют в группе — то и учим)
    min_len = _min_learn()
    if len(body) < min_len:
        return
    if not re.sub(r"https?://\S+", "", body).strip():
        return                    # голая ссылка без текста — не статья
    if sum(ch.isalpha() for ch in body) < 3:
        return                    # «((((((((» и смайлы — шум, не статья
    res = learn_text(body, source="группа МАКС: " + chat_title(chat_id))
    if res.get("ok"):
        with _STATE_LOCK:
            _load()["last_learn"][str(chat_id)] = now
            if uid and res.get("id"):          # куда дописывать продолжение
                _load().setdefault("last_art", {})[str(chat_id)] = {
                    "uid": uid, "aid": res["id"], "ts": now,
                    "title": res.get("title") or "", "tail": body[-40:]}
        _save()
        try:                     # v3.142: инструкция — и в JSON-память (chatmem)
            import chatmem
            chatmem.remember(body, source="группа МАКС: " +
                             chat_title(chat_id), chat_id=chat_id)
        except Exception:
            pass
        store.log("МАКС", "выучила статью «%s» (%d знаков) из чата %s"
                  % (res.get("title", "?"), res.get("chars", 0), chat_id))
    elif res.get("dup"):
        with _STATE_LOCK:
            _load()["last_learn"][str(chat_id)] = now
            _load()["last_learn_err"][str(chat_id)] = "уже знаю"
            if uid and res.get("id") and kb.article(res["id"]):
                _load().setdefault("last_art", {})[str(chat_id)] = {
                    "uid": uid, "aid": res["id"], "ts": now,
                    "title": res.get("title") or "", "tail": body[-40:]}
        _save()


def wipe_learned(what="all"):
    """v3.31: стереть из базы знаний всё, что бот выучил из чатов МАКС.
    what: all | groups (статьи из групп и истории) | docs (документы).
    Файлы в data/files/МАКС не трогаем. Сбрасываем дедуп (seen), водяные
    знаки сканов и «последнюю статью» — чтобы перечитать с контекстом.
    v3.142: вместе с выученным стирается память инструкций (chatmem)."""
    pref = {"groups": ("группа макс", "история чата", "макс"),
            "docs": ("документ макс",)}
    want = pref["groups"] + pref["docs"] if what == "all" else \
        pref.get(what, pref["groups"])
    removed = 0
    for a in kb.list_articles(limit=100000):
        src = str(a.get("source") or "").lower()
        if any(src == w or src.startswith(w + " ") or src.startswith(w + ":")
               for w in want):
            if kb.delete_article(a.get("id"), a.get("device") or None):
                removed += 1
    with _STATE_LOCK:
        st = _load()
        st["seen"] = []
        st["seen_aid"] = {}
        st["scan_done"] = {}
        st["last_art"] = {}
        st["stats"]["learned"] = 0
        if what in ("all", "docs"):
            st["stats"]["docs"] = 0
            st["docs_recent"] = []
    _save()
    try:                             # v3.142: память инструкций — тоже стереть
        import chatmem
        chatmem.wipe()
    except Exception:
        pass
    store.log("МАКС", "стёрто выученное из чатов: статей %d (%s)" % (removed, what))
    return {"ok": True, "removed": removed}


# ------------------------- документы из чатов (v3.23) ------------------------
def _video_url(token):
    """v3.23.2: GET /videos/{token} -> прямая ссылка (VideoUrls: mp4_1080,
    mp4_720, ...). Для видео (и на практике для аудио) это единственный
    живой способ достать медиа после удаления /files."""
    try:
        d = _call("GET", "/videos/%s" % urllib.parse.quote(str(token)))
    except MaxErr:
        return ""
    d = d if isinstance(d, dict) else {}
    urls = d.get("urls") if isinstance(d.get("urls"), dict) else {}
    for k in ("mp4_1080", "mp4_720", "mp4_480", "mp4_360", "hls", "dash"):
        u = str(urls.get(k) or "").strip()
        if u:
            return u
    for k in ("url", "video_url", "playback_url"):
        u = str(d.get(k) or "").strip()
        if u:
            return u
    return ""


def _fetch_file_url(payload):
    """payload вложения МАКС -> (url, причина). Цепочка v3.23.2 (метод
    GET /files удалён из API — проверено: 404 method.not.found):
    1) payload.url — прямой URL (картинки, часто и файлы);
    2) GET /videos/{token} — официальный способ для видео/аудио;
    3) старый GET /files?token= — на случай возврата метода;
    4) честная ошибка с советом."""
    p = payload if isinstance(payload, dict) else {}
    url = str(p.get("url") or "").strip()
    if url:
        return url, ""
    tok = str(p.get("token") or "").strip()
    if not tok:
        return "", "у вложения нет ни URL, ни токена"
    url = _video_url(tok)
    if url:
        return url, ""
    try:
        d = _call("GET", "/files?token=%s" % urllib.parse.quote(tok))
        url = str((d or {}).get("url") or "").strip()
        if url:
            return url, ""
    except MaxErr:
        pass
    return "", ("МАКС больше не отдаёт этот файл через API (метод /files "
                "удалён, /videos не знает такого токена) — пришли файл "
                "ещё раз или пришли его картинкой")


def _fname_from(headers, url):
    """Имя файла: Content-Disposition -> путь URL. Честное «document.bin»,
    если ничего нет."""
    try:
        for k, v in (headers or {}).items():
            if str(k).lower() == "content-disposition":
                m = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)"?',
                              str(v), re.IGNORECASE)
                if m:
                    return urllib.parse.unquote(m.group(1)).strip()[:120]
    except Exception:
        pass
    name = urllib.parse.urlparse(url).path.rsplit("/", 1)[-1]
    return urllib.parse.unquote(name).strip()[:120]


def _download(url, max_bytes=MAX_DOC_BYTES, hops=3):
    """Скачать файл вложения. Хост *.max.ru — через настоящий IP по DoH с
    Минцифры-CA (как весь Bot API, v3.20); остальные — обычный urllib
    (редиректы он умеет сам). Возврат (ok, data|причина, имя_файла)."""
    if hops <= 0:
        return False, "слишком много редиректов при скачивании", ""
    try:                        # v3.24.4: кириллица в адресе -> ascii
        import webfetch         # (рекурсия по редиректам тоже проходит
        url = webfetch.safe_url(url)   # через эту строку)
    except Exception:
        pass
    u = urllib.parse.urlparse(url)
    host = (u.hostname or "").lower()
    path_qs = (u.path or "/") + (("?" + u.query) if u.query else "")
    if host and host not in ("127.0.0.1", "localhost", "::1"):
        try:
            import webfetch
            import ruca
            ips = webfetch._doh_resolve(host)
        except Exception:
            ips = []
        for ip in (ips or [])[:2]:
            try:
                status, headers, data = webfetch._https_call(
                    host, ip, path_qs, timeout=25, max_bytes=max_bytes,
                    extra_headers={"User-Agent": UA},
                    extra_ca=getattr(ruca, "CADATA", None))
                if status in (301, 302, 303, 307, 308):
                    loc = ""
                    for k, v in (headers or {}).items():
                        if str(k).lower() == "location":
                            loc = str(v)
                    if loc:
                        return _download(urllib.parse.urljoin(url, loc),
                                         max_bytes=max_bytes, hops=hops - 1)
                    continue
                if status != 200:
                    return False, "сервер файлов ответил HTTP %s" % status, \
                           _fname_from(headers, url)
                return True, data, _fname_from(headers, url)
            except Exception:
                continue
    # запасной путь: обычный urllib (следит за редиректами сам).
    # v3.23.2: тоже с Минцифры-CA — платформа файлов *.max.ru подписана им
    try:
        hdrs = {"User-Agent": UA}
        if host and host.endswith("max.ru"):
            hdrs["Authorization"] = token()   # токен — только своим хостам
        req = urllib.request.Request(url, headers=hdrs)
        with urllib.request.urlopen(req, timeout=30,
                                    context=_ssl_context()) as r:
            data = r.read(max_bytes + 1)
            if len(data) > max_bytes:
                return False, "файл больше 30 МБ — не потяну", ""
            return True, data, _fname_from(dict(r.headers), url)
    except Exception as e:
        return False, "%s: %s" % (type(e).__name__, e), ""


def _save_document(name, raw, chat_id):
    """Документ -> своя база: сам файл в data/files/МАКС/, текст (docx, doc,
    rtf, pdf, txt...) — статьёй в базу знаний. Дедуп по md5 содержимого.
    Возврат (ответ-текст, ok)."""
    raw = raw or b""
    safe = "".join(c for c in str(name or "document.bin")
                   if c.isalnum() or c in "._- ")[:80] or "document.bin"
    stem = os.path.splitext(safe)[0]
    try:
        os.makedirs(store.p("files", "МАКС"), exist_ok=True)
        with open(store.p("files", "МАКС", safe), "wb") as f:
            f.write(raw)
    except Exception as e:
        store.log("МАКС", "файл «%s» не сохранился: %s" % (safe, e))
    h = "f:" + hashlib.md5(raw).hexdigest()
    st = _load()
    with _STATE_LOCK:
        dup = h in (st.get("seen") or [])
        if not dup:
            st.setdefault("seen", []).append(h)
            st["stats"]["docs"] = st["stats"].get("docs", 0) + 1
            st.setdefault("docs_recent", []).append(
                {"name": safe, "chars": 0, "kind": "?", "ts": time.time(),
                 "chat": str(chat_id)})
            st["docs_recent"] = st["docs_recent"][-10:]
    _save()
    if dup:
        return "Файл «%s» я уже читала раньше — ничего нового." % safe, True
    import textutils
    text, kind = textutils.extract_text(safe, raw)
    chars = len(text)
    with _STATE_LOCK:
        if st.get("docs_recent"):
            st["docs_recent"][-1].update({"chars": chars, "kind": kind})
    _save()
    if chars >= 40:
        kb.add_article((stem or "Документ")[:80], text,
                       source="документ МАКС из чата %s"
                              % chat_title(chat_id))
        store.log("МАКС", "документ «%s» (%s, %d знаков): файл в "
                  "data/files/МАКС, статья в базе знаний (чат %s)"
                  % (safe, kind, chars, chat_id))
        return ("Сохранила документ «%s»: сам файл — к себе, текст "
                "(%s, %d знаков) — в базу знаний. Теперь знаю больше."
                % (safe, kind, chars)), True
    store.log("МАКС", "документ «%s» сохранён в data/files/МАКС, но текста "
              "не нашла (вид: %s)" % (safe, kind))
    return ("Файл «%s» сохранила к себе, но текст вытащить не смогла "
            "(вид: %s — возможно, это скан-картинки или редкий формат)."
            % (safe, kind)), False


def _handle_files(msg, chat_id):
    """Вложения-документы (type=file) из сообщения: скачать -> сохранить ->
    прочитать. Возврат текст-подтверждение (для лички и обращений в группе)
    или None (в группах без обращения всё тихо, только журнал)."""
    body = msg.get("body") or {}
    out = []
    n = 0
    for a in (body.get("attachments") or [])[:3]:
        if not isinstance(a, dict):
            continue
        atype = str(a.get("type") or "").lower()
        if atype in ("image", "photo"):
            # v3.30: КАРТИНКА — скачать и разобрать (imgsense), описание —
            # в ответ и в базу знаний (облако помнит, что видело)
            n += 1
            pl = a.get("payload") or {}
            iurl = str(pl.get("url") or "") if isinstance(pl, dict) else ""
            if not iurl:
                out.append("Картинку получила, но ссылки на неё МАКС не дал.")
                continue
            ok, data, _fn = _download(iurl)
            if not ok:
                out.append("Не смогла скачать картинку: %s" % data)
                continue
            try:
                import imgsense
                out.append(imgsense.describe(
                    data, name="из чата %s" % chat_title(chat_id)))
            except Exception as e:
                out.append("Картинку не разобрала: %s" % e)
            continue
        if atype in ("video", "audio", "voice"):
            # v3.40: СЛУХ — звук видео/голосового: свойства, речь, база знаний
            n += 1
            url, why = _fetch_file_url(a.get("payload"))
            if not url:
                out.append("Не смогла получить звук: %s" % why)
                continue
            ok, data, fname = _download(url)
            if not ok:
                out.append("Не смогла скачать звук: %s" % data)
                continue
            try:
                import hearing
                rep, _ok = hearing.report(
                    data, fname or "%s из чата" % atype,
                    source="МАКС %s" % chat_title(chat_id))
            except Exception as e:
                rep = "Звук не разобрала: %s" % e
            out.append(rep)
            continue
        if atype not in ("file", "document"):
            continue
        n += 1
        url, why = _fetch_file_url(a.get("payload"))
        if not url:
            _err("документ из чата %s: %s" % (chat_id, why))
            out.append("Не смогла скачать документ: %s" % why)
            continue
        ok, data, fname = _download(url)
        if not ok:
            _err("документ из чата %s: %s" % (chat_id, data))
            out.append("Не смогла скачать документ: %s" % data)
            continue
        try:
            import hearing
            if hearing.is_media_name(fname):   # файл-видео/аудио -> слух
                rep, _ok = hearing.report(data, fname,
                                          source="МАКС %s" % chat_title(chat_id))
                out.append(rep)
                continue
        except Exception:
            pass
        if str(fname or "").lower().endswith(".zip"):     # v3.44: ZIP с кодом
            try:
                import ghlearn
                r = ghlearn.ingest_zip(data, fname)
                if r.get("ok"):
                    out.append("Приняла архив «%s»: файлов с кодом %d — "
                               "читаю по одному, учусь." % (fname, r["files"]))
                    continue
            except Exception:
                pass
        reply, _ok = _save_document(fname or "document.bin", data, chat_id)
        out.append(reply)
    if not n:
        return None
    return "\n\n".join(o for o in out if o) or None


# --------------------- СКАН ИСТОРИИ ЧАТА (v3.24) -----------------------------
def _norm_ts(ts):
    """timestamp сообщения МАКС -> секунды. В API приходит int64 (схема:
    «Unix-time when message was created») — встречаются и миллисекунды, и
    секунды, честно распознаём по величине (2026 год = 1.78e9 сек /
    1.78e12 мс). Мусор и нули -> 0."""
    try:
        v = float(ts or 0)
    except (TypeError, ValueError):
        return 0.0
    if v > 1e11:                                   # миллисекунды
        v /= 1000.0
    return v if 0 < v < 4e10 else 0.0


def scan_state():
    """v3.24: состояние скана истории для панели (честная копия)."""
    with _STATE_LOCK:
        st = _load()
        sc = dict(st.get("scan") or {})
        sd = {k: dict(v) if isinstance(v, dict) else v
              for k, v in (st.get("scan_done") or {}).items()}
    sc.setdefault("running", False)
    sc.setdefault("finished", False)
    if sc.get("running") and not _scan_alive() and \
            time.time() - float(sc.get("started") or 0) > 10:
        # v3.136: «скан идёт» после перезапуска облака — потока уже нет, а
        # флаг остался в файле: кнопка была недоступна навсегда
        _scan_finalize("скан прерван перезапуском облака — выученное "
                       "сохранено, можно запускать заново")
        return scan_state()
    if sc.get("running") and sc.get("started"):
        sc["elapsed"] = round(time.time() - sc["started"], 1)  # живое время
    sc["done_chats"] = sd
    return sc


def _scan_alive():
    th = _SCAN_TH[0]
    return bool(th is not None and th.is_alive())


def _scan_finalize(note):
    with _STATE_LOCK:
        sc = _load().setdefault("scan", {})
        sc["running"] = False
        sc["finished"] = True
        sc["stopped"] = True
        sc["note"] = (str(sc.get("note") or "") + " " + note).strip()[:300]
        if sc.get("started"):
            sc["elapsed"] = round(time.time() - sc["started"], 1)
    _save()


def _scan_bump(**kw):
    """Счётчики прогресса скана (+1 и т.п.) под замком."""
    with _STATE_LOCK:
        sc = _load().setdefault("scan", {})
        for k, v in kw.items():
            cur = sc.get(k)
            if isinstance(cur, (int, float)) and isinstance(v, (int, float)):
                sc[k] = cur + v
            else:
                sc[k] = v
    return sc


def _scan_fail(msg):
    """Ошибка во время скана: счётчик, last_error, журнал (с дросселем
    смысла нет — ошибки скана редки и каждая важна)."""
    _scan_bump(errors=1)
    with _STATE_LOCK:
        _load().setdefault("scan", {})["last_error"] = str(msg)[:300]
    _save()
    store.log("МАКС", "скан истории:", str(msg)[:200])


def scan_history(chat_id="", pages=None, auto=False):
    """v3.24: СКАН ИСТОРИИ ЧАТА — заявка владельца «загрузить все документы
    и историю чата в память нейросети». Читаем историю через официальный
    GET /messages (новейшие первыми, следующая страница — before=<ts>) и
    складываем в память: тексты от минимума знаков — статьями в базу знаний
    (learn_text), вложения-файлы — через _fetch_file_url -> _download ->
    _save_document (как в живых чатах). Свои сообщения бота пропускаем,
    голые ссылки и смайлы — не статьи, повторы не заучиваем (md5 +
    водяной знак scan_done). Скан идёт В ФОНОВОМ ПОТОКЕ, прогресс —
    scan_state(). chat_id="" — все известные чаты (по событиям).
    Возврат {ok:True, scan} | {ok:False, error, scan?}."""
    cur = scan_state()
    if cur.get("running") and cur.get("auto") and not auto:
        # v3.136: ручной скан важнее фонового автоскана — автоскан уступает
        _SCAN_STOP.set()
        th = _SCAN_TH[0]
        if th is not None:
            th.join(20.0)
        cur = scan_state()
    if cur.get("running"):
        return {"ok": False, "error": "скан уже идёт — останови его кнопкой "
                                      "«Остановить скан»", "scan": cur}
    if not token():
        return {"ok": False, "error": "токен бота не задан (Настройки -> "
                                      "Макс-бот)"}
    if not enabled():
        return {"ok": False, "error": "бот выключен — включи его и сохрани "
                                      "настройки"}
    chats = _load().get("chats") or {}
    if str(chat_id).strip():
        targets = [str(chat_id).strip()]
        _see_chat(targets[0], "chat")          # v3.31: чат по ссылке — в список
    else:
        targets = sorted(chats, key=lambda k: -(chats[k].get("seen") or 0))
        if not targets:
            return {"ok": False, "error": "известных чатов нет — чаты "
                    "собираются по событиям: напиши боту в личку или добавь "
                    "его в группу, тогда скан станет возможен (или передай "
                    "chat_id вручную)"}
    try:
        pages_n = int(pages) if pages else int(
            store.settings().get("maxbot_scan_pages", SCAN_PAGES_MAX) or SCAN_PAGES_MAX)
    except (TypeError, ValueError):
        pages_n = SCAN_PAGES_MAX
    pages_n = max(1, min(SCAN_PAGES_MAX, pages_n))
    with _SCAN_START_LOCK:                       # честно без гонки кнопок
        cur = scan_state()
        if cur.get("running"):
            return {"ok": False, "error": "скан уже идёт — останови его "
                                          "кнопкой «Остановить скан»",
                    "scan": cur}
        _SCAN_STOP.clear()
        with _STATE_LOCK:
            st = _load()
            st["scan"] = {"running": True, "finished": False,
                          "stopped": False, "stop_req": False,
                          "chat_id": "", "chat_title": "",
                          "chats_total": len(targets), "chats_done": 0,
                          "page": 0, "walk": 0, "pages_budget": pages_n,
                          "msgs": 0, "old": 0, "learned": 0, "dups": 0,
                          "docs": 0, "doc_errs": 0, "errors": 0,
                          "note": "", "last_error": "", "auto": bool(auto),
                          "started": time.time(), "elapsed": 0}
        _save()
        _SCAN_TH[0] = threading.Thread(target=_scan_worker,
                                       args=(targets, pages_n, bool(auto)),
                                       daemon=True, name="maxbot-scan")
        _SCAN_TH[0].start()
    store.log("МАКС", "запущен %s истории: чатов %d, бюджет %d страниц "
              "на чат" % ("автоскан" if auto else "скан", len(targets), pages_n))
    return {"ok": True, "started": True, "scan": scan_state()}


def scan_stop():
    """v3.24: кнопка «Остановить скан» — мягко, между страницами."""
    cur = scan_state()
    if not cur.get("running"):
        return {"ok": False, "error": "скан сейчас не идёт", "scan": cur}
    _SCAN_STOP.set()
    if not _scan_alive():                        # v3.136: потока нет — сразу
        _scan_finalize("остановлено кнопкой")
        return {"ok": True, "scan": scan_state()}
    with _STATE_LOCK:
        sc = _load().setdefault("scan", {})
        sc["stop_req"] = True
        sc["note"] = (sc.get("note") or "") + " останавливаю по кнопке…"
    _save()
    return {"ok": True, "scan": scan_state()}


def _scan_note(note):
    with _STATE_LOCK:
        sc = _load().setdefault("scan", {})
        sc["note"] = (str(sc.get("note") or "") + " " + str(note)).strip()[:300]
    _save()


def _scan_worker(targets, pages_n, auto=False):
    """Фоновый поток скана: по чату на каждый target, потом честный финиш."""
    started = time.time()
    try:
        for i, cid in enumerate(targets):
            if _SCAN_STOP.is_set():
                break
            if time.time() - started > SCAN_TIME_MAX:
                _scan_note("вышло время скана (%d мин) — выученное уже "
                           "сохранено; запусти скан ещё раз, продолжу"
                           % (SCAN_TIME_MAX // 60))
                break
            try:
                _scan_one_chat(cid, pages_n, started, auto)
            except Exception as e:                 # чат не должен валить всё
                _scan_fail("чат %s: %s: %s" % (cid, type(e).__name__, e))
            finally:
                _scan_bump(chats_done=1)
    finally:
        _SCAN_STOP.clear()
        with _STATE_LOCK:
            sc = _load().setdefault("scan", {})
            stopped = bool(sc.get("stop_req"))
            sc["running"] = False
            sc["finished"] = True
            sc["stopped"] = stopped
            sc["elapsed"] = round(time.time() - sc.get("started",
                                                       started), 1)
            if stopped and not sc.get("note"):
                sc["note"] = "остановлено кнопкой"
        _save()
        try:                      # v3.60: после скана — чтение/обучение сразу
            sc3 = _load().get("scan") or {}
            if int(sc3.get("learned") or 0) or int(sc3.get("docs") or 0):
                import syncpoint
                syncpoint.mark("train", "скан чатов завершён: выучено %s"
                               % sc3.get("learned", 0), llm=True, now=True)
                store.log("МАКС", "после скана запущено чтение: дообучение "
                          "своей LLM, плата — тянет базу и учит сеть")
        except Exception:
            pass
        try:                      # v3.142: ПОСЛЕ СКАНА — сбор инструкций в JSON
            import chatmem
            chatmem.harvest()
        except Exception:
            pass
        try:
            sc2 = _load().get("scan") or {}
            store.log("МАКС", "скан истории завершён%s: страниц %s, "
                      "сообщений %s, выучено %s, документов %s, ошибок %s "
                      "(%.1f мин)"
                      % (" (остановлен)" if stopped else "",
                         sc2.get("page", 0), sc2.get("msgs", 0),
                         sc2.get("learned", 0), sc2.get("docs", 0),
                         sc2.get("errors", 0),
                         sc2.get("elapsed", 0) / 60.0))
        except Exception:
            pass


def _scan_eat(msg, chat_id, bot, min_len, local_seen):
    """Одно сообщение истории -> память нейросети. Документы: файл в
    data/files/МАКС + статья (как в живых чатах); текст от минимума знаков —
    статьёй. Свои сообщения бота — мимо. Голая ссылка/менее 3 букв — шум.
    Возврат: 'own' | 'learned' | 'dup' | 'short' | 'doc+' (уже выученные
    сообщения отсекаются раньше, в _scan_one_chat)."""
    if bot.get("user_id") and ((msg.get("sender") or {}).get("user_id")
                               == bot.get("user_id")):
        return "own"
    body = msg.get("body") or {}
    had_doc = False
    for a in (body.get("attachments") or [])[:5]:
        if not isinstance(a, dict) or \
                str(a.get("type") or "").lower() not in ("file", "document"):
            continue
        url, why = _fetch_file_url(a.get("payload"))
        if not url:
            _scan_fail("документ из истории чата %s: %s" % (chat_id, why))
            continue
        okd, data, fname = _download(url)
        if not okd:
            _scan_fail("документ из истории чата %s: %s" % (chat_id, data))
            continue
        _save_document(fname or "document.bin", data, chat_id)
        _scan_bump(docs=1)
        had_doc = True
    text = (body.get("text") or "").strip()
    if not text:
        return "doc+" if had_doc else "short"
    clean = re.sub(r"\s+", " ", text).strip()
    if len(clean) < max(2, min_len):
        return "short"
    if _RAWLINK.match(clean):
        return "short"                             # голая ссылка — не статья
    if sum(ch.isalpha() for ch in clean) < 3:
        return "short"                             # «((((((» и смайлы — шум
    h = hashlib.md5(clean.encode("utf-8", "replace")).hexdigest()
    if h in local_seen:
        _scan_bump(dups=1)
        return "dup"
    res = learn_text(clean, source="история чата «%s»" % chat_title(chat_id))
    if res.get("ok"):
        local_seen.add(h)
        _scan_bump(learned=1)
        try:                     # v3.142: инструкция — и в JSON-память (chatmem)
            import chatmem
            chatmem.remember(clean, source="история чата «%s»"
                             % chat_title(chat_id), chat_id=chat_id)
        except Exception:
            pass
        return "learned"
    if res.get("dup"):
        _scan_bump(dups=1)
        return "dup"
    _scan_fail("статья из истории не сохранилась: %s" % res.get("error"))
    return "short"


def _scan_watermark(chat_id, oldest_new):
    """Запомнить, ДО чего чат уже разобран: timestamp самого старого
    ОБРАБОТАННОГО сообщения (включая пропущенные короткие/шумные — они
    осознанно не статьи). Вызывается при ЛЮБОМ выходе из скана чата —
    иначе остановка/ошибка посередине приведёт к повторному заучиванию."""
    with _STATE_LOCK:
        sd = _load().setdefault("scan_done", {})
        prev = sd.get(str(chat_id)) or {}
        keep = float(prev.get("until") or 0)
        if oldest_new and (not keep or oldest_new < keep):
            keep = oldest_new
        sd[str(chat_id)] = {"until": keep, "ts": time.time(),
                            "title": chat_title(chat_id)[:80]}
    _save()


# v3.31: КОНТЕКСТ ЧТЕНИЯ ЧАТА (заявка: «инструкция разбита на 2–3 сообщения,
# а бот берёт по одному и пропускает команды»). Сообщения склеиваются в одну
# статью, если: тот же автор в пределах maxbot_ctx_gap (10 мин, было 3);
# ИЛИ сообщение — команда/код (латиница, sudo/dnf/apt…, обратные кавычки)
# и идёт следом (до 5 мин) за любым сообщением — автор может быть другой;
# ИЛИ предыдущее заканчивается двоеточием; ИЛИ это ответ (reply) на
# предыдущее. Склейка переживает границы страниц истории (_scan_one_chat).
_CMD_LINE = re.compile(
    r"^\s*(?:\$|#|>)?\s*(?:sudo|apt|apt-get|dnf|yum|rpm|systemctl|service|"
    r"chmod|chown|mount|umount|ip|ifconfig|nmcli|ping|ssh|scp|cat|grep|"
    r"sed|awk|ls|cd|cp|mv|rm|mkdir|tar|zip|unzip|7z|wget|curl|pip|python3?|"
    r"docker|kubectl|git|journalctl|dmesg|lsblk|fdisk|df|du|ps|kill|"
    r"reboot|shutdown|echo|export|nano|vi|vim|setenforce|firewall-cmd|"
    r"ufw|iptables|useradd|usermod|passwd|hostnamectl|timedatectl|"
    r"localectl|update-grub|grub2-mkconfig|dracut|modprobe|lsmod|"
    r"net|netsh|reg|sfc|dism|powershell|wmic|sc|gpupdate|ipconfig|"
    r"chkdsk|diskpart|regedit|msiexec)\b", re.I)


def _looks_cmd(text):
    t = str(text or "").strip()
    if not t or len(t) > 1200:
        return False
    if "`" in t or _CMD_LINE.match(t):
        return True
    letters = [ch for ch in t if ch.isalpha()]
    if len(letters) >= 4:
        lat = sum(1 for ch in letters if ch.isascii())
        if lat / float(len(letters)) >= 0.75 and ("/" in t or "-" in t
                                                  or "." in t or "=" in t):
            return True
    return False


def _ctx_gap():
    try:
        return max(30.0, min(3600.0, float(
            store.settings().get("maxbot_ctx_gap") or 600)))
    except (TypeError, ValueError):
        return 600.0


def _glue(prev, m, ts):
    """Приклеить сообщение m к статье-записи prev."""
    pb = dict(prev.get("body") or {})
    t1 = str(pb.get("text") or "").strip()
    t2 = str((m.get("body") or {}).get("text") or "").strip()
    pb["text"] = (t1 + "\n" + t2).strip() if t2 else t1
    pb["attachments"] = list(pb.get("attachments") or []) + \
        list((m.get("body") or {}).get("attachments") or [])
    prev["body"] = pb
    prev["_last_ts"] = ts
    prev["_n"] = int(prev.get("_n") or 1) + 1


def _continues(prev, m, ts):
    """Сообщение m — продолжение записи prev?"""
    gap = ts - float(prev.get("_last_ts") or 0)
    if gap < 0 or gap > 2 * _ctx_gap():
        return False
    uid = str((m.get("sender") or {}).get("user_id") or "")
    puid = str((prev.get("sender") or {}).get("user_id") or "")
    ptext = str((prev.get("body") or {}).get("text") or "").rstrip()
    text = str((m.get("body") or {}).get("text") or "")
    link = m.get("link") or {}
    if uid and uid == puid and gap < _ctx_gap():
        return True
    if gap < 300 and (ptext.endswith(":") or _looks_cmd(text)):
        return True
    if isinstance(link, dict) and str(link.get("type") or "") == "reply":
        return gap < 2 * _ctx_gap()
    return False


def _group_runs(msgs, pending=None):
    """Сообщения истории -> «статьи» (порядок — от старых к новым).
    pending — незавершённая запись с ПРЕДЫДУЩЕЙ (более новой) страницы:
    если самая новая запись этой страницы — её начало, они сливаются."""
    def _ts(m):
        return _norm_ts(m.get("timestamp")) or 0.0

    out = []
    for m in sorted([m for m in msgs if isinstance(m, dict)], key=_ts):
        if out and _continues(out[-1], m, _ts(m)):
            _glue(out[-1], m, _ts(m))
            continue
        mm = dict(m)
        mm["_last_ts"] = _ts(m)
        mm["_n"] = 1
        out.append(mm)
    if pending is not None:
        pts = _norm_ts(pending.get("timestamp")) or 0.0
        if out and _continues(out[-1], pending, pts):
            # pending — продолжение самой новой записи страницы: слить
            pb = dict(out[-1].get("body") or {})
            t1 = str(pb.get("text") or "").strip()
            t2 = str((pending.get("body") or {}).get("text") or "").strip()
            pb["text"] = (t1 + "\n" + t2).strip()
            pb["attachments"] = list(pb.get("attachments") or []) + \
                list((pending.get("body") or {}).get("attachments") or [])
            out[-1]["body"] = pb
            out[-1]["_last_ts"] = pending.get("_last_ts") or pts
        else:
            out.append(pending)
    return out


def _scan_one_chat(chat_id, pages_n, started, auto=False):
    """История одного чата. GET /messages отдаёт НОВЕЙШИЕ ПЕРВЫМИ, следующая
    страница — before=<самый старый timestamp текущей страницы> (официальная
    схема). Остановы: история кончилась (пустая страница / нет timestamp),
    кнопка «Остановить», время скана, бюджет страниц С НОВЫМИ сообщениями
    (страницы-прогулки по уже выученному считаются отдельно — поэтому
    повторный запуск уходит ГЛУБЖЕ). Водяной знак scan_done[chat_id].until
    (timestamp самого старого обработанного) не даёт заучивать повторно;
    пишется при любом выходе — и на успехе, и на остановке/ошибке."""
    with _STATE_LOCK:
        st = _load()
        bot = dict(st.get("bot") or {})
        until = float((st.get("scan_done") or {}).get(str(chat_id), {})
                      .get("until") or 0)
    if not bot:
        try:                                       # кто я — чтобы честно
            bot = me(refresh=True)                 # пропустить свои слова
        except MaxErr:
            bot = {}
    min_len = _min_learn()
    with _STATE_LOCK:
        sc = _load().setdefault("scan", {})
        sc["chat_id"] = str(chat_id)
        sc["chat_title"] = chat_title(chat_id)
    title = chat_title(chat_id)
    if until:
        store.log("МАКС", "скан чата «%s»: уже выучено до %s — иду глубже"
                  % (title, time.strftime(
                      "%d.%m.%Y %H:%M", time.localtime(until))))
    before = None
    # v3.176.2: у GET /messages НЕТ параметра before (есть from/to — время).
    # МАКС его молча пропускал и каждый раз отдавал ту же страницу новейших
    # сообщений — скан «насчитывал» 2000 сообщений там, где их 40. Теперь:
    # листаем через to/from (какой сработает), каждое сообщение считается
    # ОДИН раз (по mid), страница без новых сообщений — конец истории.
    page_key = "to"
    seen_mids = set()
    total_hint = _chat_msg_count(chat_id)
    oldest_new = 0.0                               # водяной знак на этот раз
    local_seen = set()
    pages_new = 0                                  # страницы с новым
    pages_walk = 0                                 # все запросы (прогулки)
    walk_cap = pages_n * SCAN_WALK_MULT + 40
    if auto:                                       # v3.136: автоскан не гуляет
        walk_cap = AUTO_WALK_PAGES                 # по всей выученной истории
    pending = None                                 # v3.31: запись между страницами

    def _flush():
        if pending is not None:
            _scan_eat(pending, chat_id, bot, min_len, local_seen)

    while True:
        if _SCAN_STOP.is_set() or time.time() - started > SCAN_TIME_MAX:
            _flush()
            _scan_watermark(chat_id, oldest_new)   # прогресс не теряем
            return
        q = "/messages?chat_id=%s&count=%d" % (
            urllib.parse.quote(str(chat_id)), SCAN_PAGE)
        if before is not None:
            q += "&%s=%d" % (page_key, before)
        try:
            d = _call("GET", q)
        except MaxErr as e:
            txt = str(e)
            if "403" in txt:
                txt += " — у бота, похоже, нет права читать этот чат " \
                       "(в группах бот должен быть администратором)"
            _scan_fail(txt)
            _flush()
            _scan_watermark(chat_id, oldest_new)
            return
        except Exception as e:
            _scan_fail("%s: %s" % (type(e).__name__, e))
            _flush()
            _scan_watermark(chat_id, oldest_new)
            return
        _scan_bump(walk=1)
        msgs = d.get("messages") if isinstance(d, dict) else None
        if not isinstance(msgs, list) or not msgs:
            break                                  # история кончилась
        pages_walk += 1
        new_here = 0
        last_raw = 0.0
        uniq_here = 0
        fresh = []                                 # v3.27: склеим подряд идущие
        for msg in msgs:
            if not isinstance(msg, dict):
                continue
            mid = str((msg.get("body") or {}).get("mid") or msg.get("mid") or "") or \
                "%s|%s" % (msg.get("timestamp"), hashlib.md5(str(
                    (msg.get("body") or {}).get("text") or "").encode("utf-8", "replace")).hexdigest()[:8])
            if mid in seen_mids:
                continue                           # эта страница уже была
            seen_mids.add(mid)
            uniq_here += 1
            raw_ts = msg.get("timestamp")
            if isinstance(raw_ts, (int, float)) and (not last_raw or raw_ts < last_raw):
                last_raw = float(raw_ts)           # самое старое на странице
            ts_n = _norm_ts(raw_ts)
            if until and ts_n and ts_n >= until:
                _scan_bump(old=1)                  # уже выучено раньше
                continue
            _scan_bump(msgs=1)
            new_here += 1
            if ts_n and (not oldest_new or ts_n < oldest_new):
                oldest_new = ts_n                  # новее-старее: листаем
            fresh.append(msg)
        # v3.27/v3.31: описание и команда (даже другим автором) — одна
        # статья; самая СТАРАЯ запись страницы ждёт следующую (более
        # старую) страницу — вдруг там её начало
        runs = _group_runs(fresh, pending)
        pending = runs[0] if runs and new_here else None
        for msg in (runs[1:] if pending is not None else runs):
            _scan_eat(msg, chat_id, bot, min_len, local_seen)
            if _SCAN_STOP.is_set():
                break
        if new_here:
            pages_new += 1
            with _STATE_LOCK:
                sc = _load().setdefault("scan", {})
                sc["page"] = sc.get("page", 0) + 1
        if not uniq_here:
            # та же страница — параметр листания не сработал: пробуем другой
            if page_key == "to" and before is not None:
                page_key = "from"
                continue
            break                                  # дальше история не листается
        if total_hint and len(seen_mids) >= total_hint:
            break                                  # прочитаны все сообщения чата
        if len(msgs) < SCAN_PAGE and before is not None:
            break                                  # последняя (неполная) страница
        if pages_new >= pages_n:
            _scan_note("лимит страниц скана (%d) — выученное сохранено; "
                       "запусти ещё раз, пойду глубже" % pages_n)
            break
        if pages_walk >= walk_cap:
            _scan_note("прошла всю доступную историю чата (лимит прогулки "
                       "%d страниц)" % walk_cap)
            break
        if not last_raw:
            _scan_note("МАКС не дал timestamp последнего сообщения — "
                       "дальше историю не листается")
            break
        before = int(last_raw) - 1                 # следующая (старее) страница
        _save()                                    # прогресс на диск
        time.sleep(SCAN_STEP)                      # бережём лимиты API
    _flush()                                       # v3.31: хвостовая запись
    # ---- водяной знак: самый старый ОБРАБОТАННЫЙ timestamp этого чата ----
    _scan_watermark(chat_id, oldest_new)
    with _STATE_LOCK:
        sc = _load().setdefault("scan", {})
        sc["chat_total"] = len(seen_mids)
        if total_hint:
            sc["chat_hint"] = total_hint
    store.log("МАКС", "скан чата «%s» готов: запросов %d, сообщений в чате %d%s, "
              "новых страниц %d" % (title, pages_walk, len(seen_mids),
                                    (" (МАКС говорит: %d)" % total_hint) if total_hint else "",
                                    pages_new))


def _chat_msg_count(chat_id):
    """Сколько сообщений в чате, по словам МАКС (GET /chats/{id}:
    messages_count). 0 — не знаем."""
    try:
        d = _call("GET", "/chats/%s" % urllib.parse.quote(str(chat_id)))
        return int((d or {}).get("messages_count") or 0)
    except Exception:
        return 0


def _addressed(text, msg, bot):
    """Обращаются ли к боту в группе: @username, «бот ...», «бот, ...»,
    «мозг ...», имя бота в начале или ответ на сообщение бота."""
    t = str(text or "").lstrip()
    un = (bot.get("username") or "").lower()
    m = re.match(r"^(?:@(%s)|(?:бот|мозг)[,:\s]+)\s*(.*)$" % re.escape(un),
                 t, re.IGNORECASE | re.DOTALL) if un else \
        re.match(r"^(?:бот|мозг)[,:\s]+\s*(.*)$", t,
                 re.IGNORECASE | re.DOTALL)
    if m:
        # v3.26: число групп зависит от ветки (@username -> 2 группы,
        # «бот …» без username -> 1) — раньше m.group(2) падал с IndexError,
        # если событие приходило до загрузки /me (bot={})
        ng = len(m.groups())
        rest = (m.group(2) if ng >= 2 else m.group(1) if ng == 1 else "") \
            if ng else ""
        rest = rest or ""
        if un and t.lower().startswith("@" + un) and not rest:
            rest = t[len("@" + un):].strip()
        return rest.strip() or t
    nm = _bot_name(bot).lower()
    if nm and t.lower().startswith(nm + " "):
        return t[len(nm) + 1:].strip() or t
    # ответ (reply) на сообщение бота
    link = msg.get("link") or {}
    lm = link.get("message") or {}
    if lm and (lm.get("sender") or {}).get("user_id") == bot.get("user_id"):
        return t
    return None


def _bot_name(bot):
    return str(bot.get("name") or "").strip()


def _on_message(msg):
    """message_created -> ответить / научиться / прочитать документы.
    Честно и без спама."""
    if not isinstance(msg, dict):
        return
    bot = _load().get("bot") or {}
    sender = msg.get("sender") or {}
    if bot.get("user_id") and sender.get("user_id") == bot.get("user_id"):
        return                                   # свои сообщения не читаем
    rec = msg.get("recipient") or {}
    chat_id = rec.get("chat_id")
    ctype = rec.get("chat_type") or "dialog"
    _see_chat(chat_id, ctype)
    body = msg.get("body") or {}
    # v3.32: то же сообщение (mid) не обрабатываем дважды — МАКС может
    # прислать событие повторно (после сбоя опроса / перезапуска)
    mid = str(body.get("mid") or msg.get("mid") or msg.get("message_id") or "")
    if mid:
        with _STATE_LOCK:
            mids = _load().setdefault("mids", [])
            if mid in mids:
                _load()["stats"]["skipped"] += 1
                return
            mids.append(mid)
            del mids[:-400]
        if not _claim("in", mid):                  # v3.33: другая копия
            with _STATE_LOCK:                      # уже взяла это сообщение
                _load()["stats"]["skipped"] += 1
            return
    text = (body.get("text") or "").strip()
    atts = body.get("attachments") or []         # v3.23: документы
    with _STATE_LOCK:
        _load()["stats"]["recv"] += 1
    if not chat_id or (not text and not atts):
        return                                   # нечего и читать
    # v3.67: имя собеседника — настоящее (first_name/name/username), а не
    # название мессенджера; раньше при пустом name приветствие шло «МАКС»
    name = (sender.get("first_name") or sender.get("name")
            or sender.get("username") or "друг")
    if ctype == "dialog":
        parts = []
        if atts:                                 # v3.23: документ(ы) -> база
            doc_ans = _handle_files(msg, chat_id)
            if doc_ans:
                parts.append(doc_ans)
        if text:
            ans = _cmd(text, chat_id=chat_id)
            if not ans:
                # v3.30: общий движок диалога (мысли мозга + ответ облака,
                # приветствие только в начале диалога, хвост — следующим
                # сообщением)
                _deliver_answer(chat_id, text, name, pre=parts)
                return
            parts.append(ans)
        if parts:
            try:
                send_reply(chat_id, "\n".join(parts), name)
                with _STATE_LOCK:
                    _load()["stats"]["ans"] += 1
            except MaxErr as e:
                _err(str(e))
        return
    # ---- группа / канал ----
    asked = _addressed(text, msg, bot) if text else None
    _learn_from_group(msg, chat_id, addressed=bool(asked))
    doc_ans = _handle_files(msg, chat_id) if atts else None
    if ctype == "channel":
        return                                   # в каналах боты не пишут
    cmd_txt = text if text.startswith("/") else None
    try:                                  # v3.128: /камера, /слышу — ответ мозга
        import senses
        if cmd_txt and senses.ask_kind(cmd_txt):
            asked, cmd_txt = cmd_txt, None
    except Exception:
        pass
    if cmd_txt:
        ans = _cmd(cmd_txt)
        if ans:
            try:
                send(chat_id, ans)
                with _STATE_LOCK:
                    _load()["stats"]["ans"] += 1
            except MaxErr as e:
                _err(str(e))
        return
    if asked:
        # подтверждение о документе — только вместе с обращением
        # (в группах бот пишет лишь когда к нему обратились — без спама)
        parts = [doc_ans] if doc_ans else []
        ans = _cmd(asked, chat_id=chat_id)
        if not ans:
            _deliver_answer(chat_id, asked, name, pre=parts)   # v3.30
            return
        parts.append(ans)
        try:
            send_reply(chat_id, "\n".join(p for p in parts if p), name)
            with _STATE_LOCK:
                _load()["stats"]["ans"] += 1
        except MaxErr as e:
            _err(str(e))


def _status_on():
    try:
        return bool(store.settings().get("bot_status_msg", True))
    except Exception:
        return True


def _status_begin(chat_id):
    """v3.50: «🔍 Смотрю базу данных…» — пока готовится ответ. -> mid | None."""
    if not _status_on():
        return None
    try:
        r = _call("POST", "/messages?chat_id=%s"
                  % urllib.parse.quote(str(chat_id)),
                  {"text": "🔍 Смотрю базу данных…", "attachments": []},
                  timeout=10)
        m = (r or {}).get("message") or {}
        mid = (m.get("body") or {}).get("mid") or m.get("mid")
        return str(mid) if mid else None
    except Exception:
        return None


def _status_end(mid):
    """Убрать статус-сообщение, когда ответ готов."""
    if not mid:
        return
    try:
        _call("DELETE", "/messages?message_id=%s" % urllib.parse.quote(mid),
              None, timeout=10)
    except Exception as e:
        store.log("МАКС", "статус-сообщение не убралось: %s" % str(e)[:80])


def _deliver_answer(chat_id, text, name, pre=None):
    """v3.30: ответ на реплику через общий движок (dialog.respond):
    [документы] -> [💭 мысли мозга] -> [ответ облака, 1–2 сообщения].
    Контекст диалога — память чата (_mem_ctx); приветствие — только в
    начале диалога; всё отправленное попадает в память диалога."""
    import dialog
    _st_mid = _status_begin(chat_id)
    try:
        msgs, zone, d_info = dialog.respond(
            text, ctx=_mem_ctx(chat_id), chat_key="max:%s" % chat_id,
            name=name, sender="МАКС: " + name,
            deliver={"kind": "max", "chat_id": chat_id},
            limit=MAX_REPLY, template=bool(_tpl_on()))
    except Exception as e:
        store.log("МАКС", "движок диалога: %s" % e)
        reply, zone = _think(text, "МАКС: " + name, chat_id=chat_id)
        msgs = [reply or "Пока не знаю, что ответить."]
        d_info = {}
    # v3.142: ПАМЯТЬ ИНСТРУКЦИЙ — если в JSON-памяти (chatmem) есть
    # инструкция по теме вопроса, а мозг сам её не привёл — дописываем
    # раздел «📋 Из инструкций чата» к ответу (в память диалога он тоже
    # попадёт — это часть ответа бота). В fallback-пути _think уже вставил.
    try:
        if not any("Из инструкций чата" in str(m) for m in msgs):
            import chatmem
            line = chatmem.memory_line(text)
            if line and msgs and line[:60] not in msgs[-1]:
                msgs[-1] = msgs[-1].rstrip() + "\n\n" + line
    except Exception:
        pass
    _status_end(_st_mid)                      # ответ готов — статус долой
    try:
        _zone_log(zone)
    except Exception:
        pass
    out = [p for p in (pre or []) if p] + [m for m in msgs if m]
    if _memory_on():                          # v3.26: память диалога
        _mem_add(chat_id, "user", text)
        # мысли мозга — не реплика диалога (в память идёт только ответ)
        ans = "\n\n".join(msgs[int(d_info.get("n_thought") or 0):])
        if ans:
            _mem_add(chat_id, "bot", ans)
    try:
        for m in out:
            send(chat_id, m)
        _send_formulas(chat_id, msgs)         # v3.30: формулы — картинками
        _send_illustrations(chat_id, msgs)    # v3.124: иллюстрации статьи
        with _STATE_LOCK:
            _load()["stats"]["ans"] += 1
    except MaxErr as e:
        _err(str(e))


def _tpl_on():
    try:
        import replytpl
        return replytpl.cfg("tpl_bot")
    except Exception:
        return True


def _zone_log(zone):
    if zone:
        store.log("МАКС", "ответила зоной «%s»" % zone)


def _err(msg):
    with _STATE_LOCK:
        st = _load()
        st["stats"]["errors"] += 1
        st["last_error"] = str(msg)[:300]
    _save()
    store.log("МАКС", "ошибка:", str(msg)[:200])


def _extract_message(u):
    m = u.get("message")
    if isinstance(m, dict):
        return m
    p = u.get("payload")
    if isinstance(p, dict):
        if isinstance(p.get("message"), dict):
            return p["message"]
        if isinstance(p.get("body"), dict) and "recipient" in p:
            return p
    return None


def handle_update(u):
    """Одно событие /updates. Возврат True, если обработано."""
    if not isinstance(u, dict):
        return False
    ut = u.get("update_type")
    if ut == "message_created":
        msg = _extract_message(u)
        if msg:
            _on_message(msg)
            return True
    elif ut == "bot_added":
        st = _load()
        _see_chat(u.get("chat_id"), "channel" if u.get("is_channel")
                  else "group")
        store.log("МАКС", "меня добавили в чат %s — чтобы я читал статьи, "
                  "назначь меня администратором чата"
                  % (u.get("chat_id") or "?"))
        return True
    elif ut == "bot_removed":
        _forget_chat(u.get("chat_id"))
        store.log("МАКС", "меня убрали из чата %s — забываю его"
                  % (u.get("chat_id") or "?"))
        return True
    elif ut == "bot_started":
        try:                                    # поприветствовать в личке
            uid = (u.get("user") or {}).get("user_id")
            if uid:
                send(uid, _help_text())
                with _STATE_LOCK:
                    _load()["stats"]["ans"] += 1
        except MaxErr as e:
            _err(str(e))
        return True
    return False


# ------------------------- v3.27: обработка в пуле --------------------------
# Раньше события обрабатывались прямо в потоке опроса: пока мозг думал над
# одним вопросом (интернет, большая база), бот не получал НИЧЕГО — ни
# других чатов, ни следующих сообщений; выглядело как «бот завис». Теперь
# сообщения уходят в небольшой пул; внутри одного чата порядок сохраняется
# (свой замок на чат), разные чаты отвечают параллельно.
_POOL = [None]
_CHAT_LOCKS = {}
_CHAT_LOCKS_GUARD = threading.Lock()
_POOL_WORKERS = 4


def _chat_lock(chat_id):
    with _CHAT_LOCKS_GUARD:
        lk = _CHAT_LOCKS.get(chat_id)
        if lk is None:
            if len(_CHAT_LOCKS) > 500:            # старые чаты — забыть
                for k in list(_CHAT_LOCKS)[:250]:
                    if not _CHAT_LOCKS[k].locked():
                        _CHAT_LOCKS.pop(k, None)
            lk = _CHAT_LOCKS[chat_id] = threading.Lock()
        return lk


_BUSY = {}                       # v3.184: id задачи -> (начало, чат) — для сторожа
_GEN = [0]                       # v3.184: поколение опроса (перезапуск = +1)


def _run_update(u, chat_id):
    key = id(u)
    _BUSY[key] = (time.time(), chat_id)
    try:
        with _chat_lock(chat_id):
            handle_update(u)
    except Exception as e:
        _err("обработка события: %s: %s" % (type(e).__name__, e))
    finally:
        _BUSY.pop(key, None)


def _dispatch(u):
    """Событие -> пул (сообщения) или сразу (служебные события)."""
    try:
        if isinstance(u, dict) and u.get("update_type") == "message_created":
            msg = _extract_message(u) or {}
            chat_id = (msg.get("recipient") or {}).get("chat_id")
            if _POOL[0] is None:
                from concurrent.futures import ThreadPoolExecutor
                _POOL[0] = ThreadPoolExecutor(max_workers=_POOL_WORKERS,
                                              thread_name_prefix="maxbot")
            _POOL[0].submit(_run_update, u, chat_id)
            return
        handle_update(u)
    except Exception as e:
        _err("обработка события: %s: %s" % (type(e).__name__, e))


# ------------------------------- фоновый поток ------------------------------
def _loop(gen=0):
    st = _load()
    st.setdefault("started", time.time())
    marker = st.get("marker")
    fail = 0
    while gen == _GEN[0]:              # v3.184: перезапуск — старый поток уходит
        if not enabled() or not token():
            _release_lead()
            _WAKE.wait(5.0)
            _WAKE.clear()
            continue
        if not _is_leader():                       # v3.33: ведёт другая копия
            if not _LEAD.get("logged"):
                _LEAD["logged"] = True
                store.log("МАКС", _LEAD["note"])
            _WAKE.wait(15.0)
            _WAKE.clear()
            continue
        _LEAD["logged"] = False
        try:
            if not _load().get("bot"):
                me(refresh=True)                 # честная проверка токена
                try:
                    refresh_chats()
                except MaxErr:
                    pass
                _save()
            _autoscan_tick()
            ups, marker = _poll(_load().get("marker") or marker)
            if gen != _GEN[0]:            # v3.184: пока ждали — бота перезагрузили
                break
            with _STATE_LOCK:
                _load()["last_ok"] = time.time()
                _load()["last_error"] = ""
            fail = 0
            for u in ups:
                _dispatch(u)                      # v3.27: не ждём ответа
            if marker:
                with _STATE_LOCK:
                    _load()["marker"] = marker
                _save()
        except MaxErr as e:
            fail += 1
            _err(str(e))
            if fail == 1:
                _try_fallback_host()             # честная смена хоста
            time.sleep(min(2.0 * fail, 12.0))  # v3.173: быстрее восстановление после ошибок
        except Exception as e:
            fail += 1
            _err("%s: %s" % (type(e).__name__, e))
            time.sleep(min(2.0 * fail, 12.0))  # v3.173: быстрее восстановление после ошибок


_AUTO = {"ts": 0.0}


def _autoscan_tick():
    """v3.72: чат «База знаний» читается САМ: раз в maxbot_autoscan_min минут
    (15; 0 — выкл.) идёт короткий скан истории всех известных чатов. Водяной
    знак scan_done не даёт заучивать повторно, так что это дёшево, а
    сообщения, пропущенные при простое бота или до права администратора,
    подбираются."""
    try:
        mins = float(store.settings().get("maxbot_autoscan_min", 15) or 0)
    except (TypeError, ValueError):
        mins = 15.0
    if mins <= 0 or time.time() - _AUTO["ts"] < mins * 60.0:
        return
    _AUTO["ts"] = time.time()
    if not (_load().get("chats") or {}) or scan_state().get("running"):
        return
    try:
        r = scan_history(pages=3, auto=True)
        if r.get("ok"):
            store.log("МАКС", "автоскан истории чатов запущен")
    except Exception:
        pass


def _try_fallback_host():
    """Основной хост молчит/ошибается — один раз пробуем запасной
    (botapi.max.ru). При тестовом env-переопределении не срабатывает."""
    if os.environ.get("NEURO_MAX_API"):
        return
    if _OVERRIDE[0] is None:
        store.log("МАКС", "основной хост API не отвечает, пробую запасной "
                  "botapi.max.ru")
        _OVERRIDE[0] = API_FALLBACK


def start():
    """Запуск фонового long-polling потока (из app.py). Работает всегда:
    сам включается, когда появляются maxbot_on + maxbot_token."""
    if _TH[0] and _TH[0].is_alive():
        return
    _TH[0] = threading.Thread(target=_loop, args=(_GEN[0],), daemon=True,
                              name="maxbot-poll")
    _TH[0].start()
    _WAKE.set()
    if not _WD[0]:
        _WD[0] = threading.Thread(target=_watchdog, daemon=True, name="maxbot-watchdog")
        _WD[0].start()


_WD = [None]
_RESTARTS = {"n": 0, "last": 0.0, "why": ""}


def restart(why="кнопка владельца"):
    """v3.184: ПЕРЕЗАГРУЗКА БОТА МАКС без перезапуска облака: новый поток
    опроса, новый пул обработчиков (зависшие брошены), свежие замки чатов,
    основной хост API, проверка токена заново. Маркер событий сохраняется —
    сообщения не теряются и не дублируются."""
    global _CHAT_LOCKS
    _GEN[0] += 1
    old = _POOL[0]
    _POOL[0] = None
    if old is not None:
        try:
            old.shutdown(wait=False, cancel_futures=True)
        except TypeError:
            old.shutdown(wait=False)
        except Exception:
            pass
    with _CHAT_LOCKS_GUARD:
        _CHAT_LOCKS = {}
    _BUSY.clear()
    _OVERRIDE[0] = None
    try:
        _SCAN_STOP.set()
    except Exception:
        pass
    with _STATE_LOCK:
        _load().pop("bot", None)                 # заново GET /me
        _load()["last_error"] = ""
    _RESTARTS.update(n=_RESTARTS["n"] + 1, last=time.time(), why=str(why)[:200])
    store.log("МАКС", "перезагрузка бота: %s" % why)
    _WAKE.set()                                  # старый поток проснётся и выйдет
    _TH[0] = threading.Thread(target=_loop, args=(_GEN[0],), daemon=True,
                              name="maxbot-poll-%d" % _GEN[0])
    _TH[0].start()
    _WAKE.set()
    return {"ok": True, "restarts": _RESTARTS["n"]}


def _watchdog():
    """v3.184: сторож бота: опрос молчит > 4 мин или ответ висит > 6 мин —
    перезагрузить бота сам (не чаще раза в 5 минут)."""
    time.sleep(120)
    while True:
        time.sleep(30)
        try:
            if not enabled() or not token() or not _LEAD.get("fh"):
                continue
            now = time.time()
            if now - _RESTARTS["last"] < 300:
                continue
            last_ok = float(_load().get("last_ok") or 0)
            stuck = [t for t, _c in list(_BUSY.values()) if now - t > 360]
            if not (_TH[0] and _TH[0].is_alive()):
                restart("поток опроса умер")
            elif last_ok and now - last_ok > 240:
                restart("опрос МАКС молчит %d с" % (now - last_ok))
            elif stuck:
                restart("ответ висит %d мин (%d шт.)" % ((now - min(stuck)) // 60, len(stuck)))
        except Exception:
            pass


def wake():
    """Настройки изменились — не ждать конца таймаута polling."""
    _WAKE.set()


def status():
    """Для панели: честное состояние бота."""
    st = _load()
    bot = dict(st.get("bot") or {})
    th_alive = bool(_TH[0] and _TH[0].is_alive())
    chats = sorted((st.get("chats") or {}).values(),
                   key=lambda c: (str(c.get("title") or ""),
                                  str(c.get("chat_id") or "")))
    return {
        "ok": True,
        "enabled": enabled(),
        "token_set": bool(token()),
        "base": _base_url(),
        "thread": th_alive,
        "bot": {k: bot.get(k) for k in ("user_id", "name", "username")}
               if bot else {},
        "chats": chats,
        "chats_note": st.get("chats_note") or "",
        "stats": dict(st.get("stats") or {}),
        "docs_recent": list((st.get("docs_recent") or [])[-10:]),
        "min_learn": _min_learn(),
        "learn_gap": _learn_gap_s(),
        "memory": _memory_on(),               # v3.26: память диалога
        "mem_chats": len(st.get("mem") or {}),
        "instructions": __import__("chatmem").stats(),   # v3.142
        "scan": {k: v for k, v in (st.get("scan") or {}).items()
                 if k != "started"},
        "scan_done": (st.get("scan_done") or {}),
        "leader": bool(_LEAD.get("fh")),            # v3.33
        "restarts": dict(_RESTARTS),                 # v3.184
        "busy": len(_BUSY),
        "last_ok_age": int(time.time() - float(st.get("last_ok") or 0)) if st.get("last_ok") else None,
        "pid": os.getpid(),
        "leader_note": _LEAD.get("note") or "",
        "marker_set": bool(st.get("marker")),
        "last_ok": st.get("last_ok") or 0.0,
        "last_error": st.get("last_error") or "",
    }
