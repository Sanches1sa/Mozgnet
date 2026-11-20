# -*- coding: utf-8 -*-
# ===========================================================================
# store.py — ХРАНИЛИЩЕ ПЛАТФОРМЫ neuro12-cloud (только stdlib, JSON-файлы)
# ---------------------------------------------------------------------------
# Всё состояние облака живёт в каталоге NEURO_DATA (по умолчанию ./data):
#   settings.json   — настройки платформы (учитель, маршрут, голос, флаги)
#   boards.json     — реестр плат-мозгов (авто-регистрация с адресом)
#   chats/          — сессии чата (ChatGPT-стиль, v3.24.2: с владельцем-IP),
#                     thoughts.json — «Мысли плат»
#   d6box.json      — мысли зон облака для пина D6 плат (v3.17)
#   memory/         — облачная память плат (бандлы memory/save от каждой платы)
#   dicts/          — словари русского языка (ru_top / ru_pos / extra), СКЛЕИВАЮТСЯ
#   kb/             — база знаний: kb/common/ (статьи), kb/devices/<id>/ (личные)
#   skills/         — реестр функций мозга (JSON + code/<id>.py)
#   thoughts.json   — лента «Мысли мозга» (Studio-лента)
#   chronicle.json  — хроника всех сообщений плат (/api/import)
#   gen_tasks.json  — очередь заданий генератору (от мыслей плат)
#   readings.json   — показания нейросетей плат (/api/chat/readings)
#   notes.json      — заметки «запомни/вспомни» (навык venv)
#   vision/         — кадры камер + буфер детекций
#   files/          — файлы плат и владельца (+ извлечённый текст в KB)
#   mozgnet_state.json — состояние нейросети материалов облака
#   token.txt       — токен платы (X-Mozg-Token); создаёт install.sh
#   log.txt         — журнал платформы (кольцевой)
# Все записи атомарные (tmp+rename), общий RLock на каждый модуль.
# ===========================================================================
import json
import os
import re
import threading
import time

_VERSION = [""]


def version():
    """v3.8.2: ЕДИНЫЙ источник версии платформы — файл VERSION в корне
    пакета (рядом с server/). Читаем один раз; больше нигде в коде
    версию не хардкодим — релиз меняет только сам файл VERSION."""
    if not _VERSION[0]:
        try:
            vp = os.path.join(os.path.dirname(os.path.dirname(
                os.path.abspath(__file__))), "VERSION")
            with open(vp, "r", encoding="utf-8") as f:
                _VERSION[0] = f.read().strip() or "3"
        except Exception:
            _VERSION[0] = "3"
    return _VERSION[0]

DATA_DIR = os.environ.get("NEURO_DATA") or os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")

LOCK = threading.RLock()
_LOGQ = []
_LOG_LOCK = threading.Lock()
_LOG_MAX = 10 * 1024 * 1024    # v3.27: log.txt больше 10 МБ -> log.txt.1


def p(*parts):
    """Путь внутри NEURO_DATA с защитой от выхода за пределы каталога."""
    base = os.path.abspath(DATA_DIR)
    full = os.path.abspath(os.path.join(base, *[str(x) for x in parts]))
    if not (full == base or full.startswith(base + os.sep)):
        raise ValueError("путь вне каталога данных: %r" % full)
    return full


def ensure_dirs():
    for d in ("chats", "memory", "dicts", os.path.join("kb", "common"),
              os.path.join("kb", "devices"), "skills", os.path.join("skills", "code"),
              "vision", "files"):
        os.makedirs(p(d), exist_ok=True)
    if not os.path.exists(p("settings.json")):
        save_json("settings.json", default_settings())
    if not os.path.exists(p("boards.json")):
        save_json("boards.json", {})
    if not os.path.exists(p("thoughts.json")):
        save_json("thoughts.json", [])
    if not os.path.exists(p("chronicle.json")):
        save_json("chronicle.json", [])
    if not os.path.exists(p("gen_tasks.json")):
        save_json("gen_tasks.json", [])
    if not os.path.exists(p("readings.json")):
        save_json("readings.json", {})
    if not os.path.exists(p("notes.json")):
        save_json("notes.json", [])
    tok = token()
    if not tok:
        tok = "nz-" + os.urandom(12).hex()
        set_token(tok)
    return tok


def token():
    """Токен платы: env NEURO_TOKEN важнее файла data/token.txt.
    Пусто = платформе можно писать без токена (доверенная локальная сеть)."""
    env = (os.environ.get("NEURO_TOKEN") or "").strip()
    if env:
        return env
    try:
        with open(p("token.txt"), "r", encoding="utf-8") as f:
            return f.read().strip()
    except Exception:
        return ""


def set_token(tok):
    with open(p("token.txt"), "w", encoding="utf-8") as f:
        f.write(str(tok).strip() + "\n")


def write_port(port):
    """Фактический порт облака — для neuro12-cloud.exe (читает этот файл)."""
    try:
        with open(p("port.txt"), "w", encoding="utf-8") as f:
            f.write(str(int(port)) + "\n")
    except Exception:
        pass


def read_port(default=8077):
    try:
        with open(p("port.txt"), "r", encoding="utf-8") as f:
            return int(f.read().strip())
    except Exception:
        return default


def auth_ok(req_token):
    """Проверка токена платы (заголовок X-Mozg-Token / ?token=).
    Токен не задан -> открытый режим (локальная сеть)."""
    want = token()
    if not want:
        return True
    return str(req_token or "").strip() == want


# --------------------------- JSON (атомарно) --------------------------------
# v3.30: БАГФИКС WINDOWS («при подстановке ссылки пишет „панель недоступна“
# и не показывает ссылки»). Все потоки писали через ОДИН И ТОТ ЖЕ файл
# «*.tmp», а os.replace на Windows падает PermissionError, если цель в этот
# миг открыта на чтение другим потоком (чтение ссылок в фоне + панель). Итог —
# ошибка 500 в панели; хуже: load_json в этот миг возвращал default, и
# settings() могла подменить настройки (со списком ссылок) значениями по
# умолчанию. Теперь: свой tmp у каждой записи, запись одного файла — по
# очереди, замена и чтение — с повторами при занятом файле.
_FILE_LOCKS = {}
_FILE_LOCKS_GUARD = threading.Lock()


def _file_lock(path):
    with _FILE_LOCKS_GUARD:
        lk = _FILE_LOCKS.get(path)
        if lk is None:
            lk = _FILE_LOCKS[path] = threading.Lock()
        return lk


def load_json(rel, default=None):
    path = p(rel)
    for attempt in range(8):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except PermissionError:
            time.sleep(0.05 * (attempt + 1))   # файл заменяют прямо сейчас
        except Exception:
            return default
    return default


def save_json(rel, data):
    path = p(rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with _file_lock(path):
        tmp = "%s.%d.%d.tmp" % (path, os.getpid(), threading.get_ident())
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=1)
        except BaseException:
            # v3.186.2: сбой записи (данные поменялись прямо во время dump,
            # кончилось место) — не оставлять .tmp мусором в data/
            try:
                os.remove(tmp)
            except Exception:
                pass
            raise
        for attempt in range(10):
            try:
                os.replace(tmp, path)
                return
            except PermissionError:
                time.sleep(0.05 * (attempt + 1))  # цель открыта на чтение
        try:
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except Exception:
                    pass


class _CappedLog:
    """Файл-журнал с потолком размера: после limit байт дописывание
    прекращается (одна строка-предупреждение), процесс не забивает диск."""

    def __init__(self, f, limit):
        self._f, self._limit, self._full = f, int(limit), False

    def write(self, s):
        if self._full:
            return len(s)
        try:
            if self._f.tell() > self._limit:
                self._full = True
                self._f.write("\n[журнал ошибок достиг %d МБ — дальше не пишу до перезапуска]\n"
                              % (self._limit // 1048576))
                return len(s)
            return self._f.write(s)
        except Exception:
            return len(s)

    def flush(self):
        try:
            self._f.flush()
        except Exception:
            pass

    def isatty(self):
        return False

    def fileno(self):
        return self._f.fileno()


def redirect_std(limit=20 * 1048576):
    """v3.186.2: облако без консоли (запуск из лаунчера/перезапуск из панели)
    писало stdout+stderr в data/restart.log без предела — у владельца 85 МБ.
    stdout дублирует log.txt — его не храним; stderr (трассировки) — в
    data/cloud_err.log с потолком limit, прошлый журнал — в cloud_err.log.1.
    При запуске из консоли (start.bat) ничего не меняем."""
    import sys as _sys
    try:
        if _sys.stderr is not None and _sys.stderr.isatty():
            return ""
    except Exception:
        pass
    path = p("cloud_err.log")
    try:
        if os.path.getsize(path) > 2 * 1048576:
            os.replace(path, path + ".1")
    except Exception:
        pass
    try:
        f = open(path, "a", encoding="utf-8", errors="replace", buffering=1)
        f.write("\n=== старт %s pid %d ===\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), os.getpid()))
        _sys.stderr = _CappedLog(f, limit)
        _sys.stdout = open(os.devnull, "w", encoding="utf-8")
        return path
    except Exception:
        return ""


def cleanup_stale_tmp(max_age=3600.0):
    """v3.186.2: удалить «x.json.<pid>.<tid>.tmp» старше часа в data/ — следы
    записей, прерванных падением процесса. Свежие (идёт запись) не трогаем."""
    import re as _re
    n, size = 0, 0
    now = time.time()
    try:
        names = os.listdir(DATA_DIR)
    except Exception:
        return 0, 0.0
    for nm in names:
        if not _re.match(r".+\.\d+\.\d+\.tmp$", nm):
            continue
        fp = os.path.join(DATA_DIR, nm)
        try:
            st = os.stat(fp)
            if now - st.st_mtime < max_age:
                continue
            os.remove(fp)
            n += 1
            size += st.st_size
        except Exception:
            pass
    return n, size / 1048576.0


# ------------------------------ настройки -----------------------------------
def default_settings():
    return {
        "title": "neuro12-cloud",
        "route": "auto",             # auto | cloud | board — кому отвечает чат
        "long_default": False,       # длинные ответы сети материалов по умолчанию
        "gen_on": True,              # генератор длинных текстов (mozgnet.write)
        "web_on": True, "web_mig91": False, "web_any": True, "web_engines": "mailru,mojeek", "sources_on": True, "sources_max_pages": 150, "sources_refresh_h": 24, "conf_on": True, "conf_url": "https://confluence.softrust.ru/collector/pages.action?key=docEMIAS", "conf_space": "", "conf_user": "", "conf_password": "", "conf_token": "", "google_api_key": "", "google_cx": "", "yandex_api_key": "", "yandex_folder": "",              # доступ к Википедии через /api/web/fetch
        "web_first": True,           # v3.13: вопрос-формат ищет в интернете
                                     # первым делом и подписывает источники
                                     # (режим «как у Алисы»)
        "web_pages": 10,             # v3.14: сколько ПЕРВЫХ страниц выдачи
                                     # читать целиком для ответа (1..10)
         "curiosity_on": True,        # принудительно включено по запросу владельца
        "ssh_on": True,              # агент администрирования серверов по SSH        # v3.10: любопытство нейронов — сами
                                     # читают статьи Википедии
        "curiosity_every": 120,      # v3.10: сек между чтениями любопытства
        "curiosity_self": True,      # v3.18: темы выбирает САМА (цепочки из
                                     # прочитанных статей), не только очередь
                                     # «хочу знать» из настроек
        "hypo_on": True,             # v3.11: смелость предположений — мозг
                                     # честно гипотезирует до «не знаю»
        "terminal_on": False,        # v3.150: «Терминал и рабочий стол» — БЕЗ этого
                                     # ключа update_settings отбрасывал галочку
        "imagine_on": True,          # v3.16: зона Воображения — рисование,
                                     # перерисовка, графики, LaTeX-формулы
                                     # и фантазии мини-LLM
        "d6_on": True,               # v3.17: мысли зон облака («Зоны мысли»)
                                     # дублируются на пин D6 платы — плата
                                     # играет их PWM-байтами, как свои фразы
        "maxbot_on": False,          # v3.20: бот в мессенджере МАКС —
                                     # отвечает мозгом облака и учится по
                                     # статьям из групп, куда его добавили
        "maxbot_token": "",          # v3.20: токен бота МАКС (Bot API)
        "maxbot_src_chats": [],      # v3.188: чаты-источники ответов (ID)
        "maxbot_src_scope": "all",   # v3.188: all — везде, same — только в том же чате
        "maxbot_src_on": True,       # v3.188: искать ответ в чатах-источниках
        "maxbot_scan_pages": 4000,   # v3.136: лимит страниц скана истории
        "maxbot_min_learn": 10,      # v3.23: минимальная длина статьи из
                                     # групп (знаков) — заявка владельца:
                                     # «собирать статьи от 10 знаков»
        "maxbot_learn_gap": 20,      # v3.23: пауза между статьями из одного
                                     # чата (сек) — чтобы не захлёбываться
        "maxbot_memory": True,       # v3.26: память диалога у бота в МАКС —
                                     # помнит последние реплики чата и
                                     # понимает продолжения («а почему?»,
                                     # «о чём мы говорили?»)
        "gh_on": True,               # v3.41: читать GitHub-ссылки в фоне
        "gh_zip_mb": 150,            # v3.176.1: предел архива репозитория, МБ
        "gh_token": "",              # токен GitHub (необязательно: лимит API)
        "gh_hosts": "",              # доп. площадки через запятую (свой GitLab/Gitea)
        "llm_link_tpl": True,        # v3.45: ответ по шаблону с жирными разделами
        "teacher_instruction": "",   # v3.59: инструкция учителя (мысли учителя = она)
        "llm_brain_lead": True,      # v3.58: мозг ведёт, своя LLM дополняет
        "llm_gen_influence": 0.5,    # v3.47: влияние генератора скетча на веса LLM (0 — выкл.)
        "llm_freq_prior": 1.0,       # v3.73: опора всех сетей на частоту (0 — выкл.)
        "llm_gen_meaning": 1.0,      # v3.48: привязка сигнала к словам (0 — выкл.)
        "kb_pack_max": 3000, "kb_pack_mig86": False,          # v3.49: сколько статей базы знаний отдавать плате (JSON)
        "kb_pack_chars": 24000,      # макс. знаков одной статьи (делится на куски)
        "kb_pack_chunk": 1200,       # знаков в куске
        "kb_pack_overlap": 150,      # нахлёст кусков (контекст), знаков
        "kb_pack_chunks_max": 12000,  # всего кусков на плате
        "kb_pack_code": False,       # включать код GitHub («Исходник: …»)
        "bot_status_msg": True,      # v3.50: боты пишут «🔍 Смотрю базу данных…» и убирают при ответе
        "d6_negfreq": False,         # v3.50/3.51: знаковая частота на D6; знак решает своя LLM (negfreq.py), иначе Δf
        "d4_negfreq": False,         # v3.52: знак частоты и на D4 (полярность меандра 1/3 или 2/3 периода)
        "d6_cammode": "pure",        # v3.53: как слать кадр на D6: "pure" — чистый кадр (без PWM и байтов), "pwm" — растр уровнями
        "d4_camframe": False,        # v3.54: чистый кадр камеры и на D4 (такт = частота генератора, колеблется вместе с ним)
        "d6_camframe": True,         # v3.52: кадр ESP32-CAM растром 16x12 на D6 (отставание от мысли pi/2; при минусе -pi/2)
        "llm_answer_full": True,     # v3.68: ответ целиком мозг+LLM, без «Мыслей»/«Вывода»
        "llm_words_strict": True,    # v3.66: словарь LLM строгий — без выдуманных слов
        "llm_words": True,           # v3.45: слова LLM только по словарю словоформ
        "gh_every": 4,               # пауза между файлами, сек
        "gh_files_max": 200,         # файлов на репозиторий
        "gh_repos_max": 6,           # репозиториев на ссылку-аккаунт/поиск
        "tg_on": False,              # v3.40: бот в Telegram (long polling)
        "tg_token": "",              # токен от @BotFather
        "tg_proxy": "",              # HTTP(S)-прокси, если Telegram закрыт
        "tg_api_base": "",           # зеркало Bot API (по умолчанию официальный)
        "tg_allowed": "",            # белый список: id/@username; пусто — все
        "tg_memory": True,           # память диалога у бота
        "study_links": [],           # v3.24.2: ссылки-инструкции (панель:
                                     # Настройки -> База знаний) — текст со
                                     # страниц качается в базу знаний
        "study_refresh": 6,          # v3.24.2: часы между перечитыванием
                                     # ссылок (0 = перечитывать только вручную)
        "study_files": True,         # v3.24.3: качать ФАЙЛЫ (pdf, doc, docx,
                                     # rtf, txt…) с сайтов-инструкций в базу
        "study_crawl": True,         # v3.61: обход раздела сайта по ссылке
        "study_sci_max": 500,        # v3.61: научных статей (OpenAlex) за круг на ссылку
        "study_crawl_max": 300,      # v3.61: страниц раздела на ссылку (0 — только страница)
        "study_files_max": 20,       # v3.24.3: сколько файлов со страницы
                                     # (максимум 50; файл до 20 МБ)
        "study_pages_max": 1000,     # v3.24.5: предел страниц Confluence-
                                     # пространства на одну ссылку (1..5000)
        "study_attach": True,        # v3.25.0: качать ВЛОЖЕНИЯ страниц
                                     # Confluence (pdf/doc/docx/txt…) в базу
        "study_attach_max": 300,     # v3.25.0: новых вложений за круг на
                                     # ссылку (0..20000; 0 = не качать)
        "chats_by_ip": True,         # v3.24.2: у разных адресов (IP) — свои
                                     # чаты; выключатель — старое поведение
        "net_nodes_max": 150000,     # v3.24.2: предел узлов сети материалов
                                     # (было 20000 — сеть «зависала» на нём)
        "net_docs_max": 3000,        # v3.24.2: предел материалов сети (было 500)
        "net_sents_max": 40000,      # v3.24.2: предел предложений сети (было 3000)
        "board_timeout": 75,         # сколько ждать ответ платы (сек)
        "bridge_url": "",            # МОСТ: адрес платы (пусто = 127.0.0.1:7000)
        "bridge_on": True,           # мост включён — облако само ищет плату
        "tensor_help": True,         # v3.27: облако считает задачи тензорной
                                     # сети платы (GET /api/tensor/job ->
                                     # POST /api/tensor/result платы)
        "thought_reply": True,       # отвечать на письма в «Мыслях плат»
        # v3.28: ШАБЛОННЫЙ ОТВЕТ «приветствие + ответ» (replytpl.py)
        "tpl_bot": True,             # в боте МАКС
        "tpl_chat": True,            # в чате облака
        "tpl_greet": "{приветствие}, {имя}!",
        "tpl_parts": 2,              # не влезло в сообщение — до N сообщений
        "tpl_greet_hours": 0,        # (до v3.30; больше не используется)
        "tpl_greet_gap_h": 4,        # v3.30: приветствие — только в начале
                                     # диалога (пауза в переписке от N ч)
        "tpl_chat_limit": 4000,      # знаков в сообщении чата облака
        # v3.28: API ИИ для других приложений (aiapi.py, ключи в панели)
        "ai_api_on": True,
        "ai_rate_per_min": 60,
        # v3.29: ЕДИНЫЙ МОЗГ (unify.py): слабый ответ облака дополняет плата
        # v3.30: галочки бота: ответ облака / ответ мозга (первым сообщением)
        "web_learn_kb": True,        # v3.30: ответы из интернета — в базу
        "curiosity_video": True,     # v3.30: любопытство смотрит и видео
        "curiosity_video_every": 2,  # каждую N-ю прочитанную тему
        "llm_answer_on": False,      # v3.30: локальная LLM отвечает по базе
        "llm_answer_url": "",        # OpenAI-совместимый адрес (Ollama и т.п.)
        "llm_answer_model": "",
        "vision_llm_on": False,      # v3.30: локальная LLM с картинками
        # v3.31: своя LLM облака (tinyllm, numpy): размеры и доля ЦП
        "llm_d": 128, "llm_layers": 2, "llm_heads": 4, "llm_ctx": 64,
        "llm_vocab": 4000, "llm_epochs": 3, "llm_batch": 16,
        "llm_corpus_tokens": 120000, "llm_cpu": 1.0, "llm_cpu_mig": False, "llm_lr": 0.002,
        "llm_inc_max_steps": 300, "llm_inc_max_min": 20,   # v3.70: потолок дообучения за запуск
        "llm_micro_batch": 0, "llm_use_swap": True, "llm_time_h": 0.0, "llm_mig81": False, "llm_answer_sents": 10, "llm_answer_budget_s": 6, "llm_step_s": 10, "llm_plan_steps": 3000, "llm_answer_long": True, "llm_disk_state": True, "llm_disk_mb": 0, "brain_train_auto": True, "answer_core": True, "brain_only": False, "brain_only_wait": 40, "maxbot_autoscan_min": 15, "llm_wordonly": True,   # v3.71
        # v3.32: параметры генерации и «обучать всё равно» (потолков нет)
        "llm_temp": 0.9, "llm_topk": 40, "llm_topp": 0.92, "llm_rep": 1.3,
        "llm_max_new": 40, "llm_force": False,
        # v3.32: связь своей LLM с мозгом облака и платами
        "llm_link_on": True, "llm_link_reply": False, "llm_link_boards": True,
        # v3.33: мысли LLM -> сеть мозга; думать сама раз в N сек; порог
        "llm_link_net": True, "llm_link_gen": True, "llm_link_ext": True, "llm_link_read": True, "llm_link_learn": True, "media_hear": True, "hear_max_s": 240, "llm_link_gen_minq": 0.5, "llm_link_every": 600, "llm_link_minq": 0.5, "llm_mig119": False, "brain_coh_min": 0.5, "sync_on": True, "sync_k": 30, "answer_strict": True, "answer_offline": True, "mic_wake": "мозг, робот, нейро", "mic_answer_all": False, "answer_honest": True, "answer_read_first": True, "llm_web_on": True, "llm_web_check": True, "llm_web_check_talk": True, "llm_web_check_s": 12, "brain_strict": True, "brain_strict_min": 0.10, "brain_strict_nobrain": "pause", "brain_llm_thought": True, "llm_free": True, "llm_free_budget_s": 15, "part_grav": False, "part_frz": False, "part_frz_n": 64, "part_muon": False, "part_phot": False, "part_cpu_link": False, "part_gen_hz": 0, "part_min_mhz": 100.0, "part_max_ghz": 4.0, "llm_code_stream": True, "llm_code_mix": 2, "llm_stream_code": True, "llm_stream_max": True, "llm_stream_talk": True, "llm_stream_admin": True, "code_board_check": True, "srv_auto_ok": False, "doom_llm_tired": True, "board_auto_restore": True, "blocked_ips": ["157.22.190.54"], "board_restore_map": {}, "doom_tired_hz": 483.0, "llm_streams_mig177": False, "llm_code_every_min": 10, "llm_code_repeat_min": 60, "llm_code_steps": 120, "llm_code_seconds": 150, "answer_passage": True, "ctx_llm_on": True, "ctx_llm_timeout": 3,
        "maxbot_images": True,       # v3.30: формулы в МАКС — картинками
        "maxbot_ctx_gap": 600,       # v3.31: окно склейки сообщений в статью
        "max_cloud_reply": True,
        "max_brain_reply": True,
        "unify_on": True,
        "unify_wait": 12,            # сек ждать плату в МАКС/API (0 — не ждать)
        "tts_voice": "neuro",        # neuro | male | female | robot
        "teacher": {                 # LLM-НАДСТРОЙКА — ТОЛЬКО ДЛЯ ОБУЧЕНИЯ
            "llm_on": False,
            "llm_url": "",           # OpenAI-совместимый URL (.../chat/completions)
            "llm_key": "",
            "llm_model": "",
        },
    }


# v3.99: ОДИН РЕЖИМ ОТВЕТОВ — «мозг neuro-12 + своя LLM». Прежние галочки
# режимов («Отвечает только мозг», «единым мозгом», «мозг ведёт», «ответ
# целиком», «строгий словарь», «ответ облака/ответ мозга» в МАКС, строка
# «🤖 мысль», связь LLM) больше не переключаются: их значения закреплены здесь,
# старые settings.json приводятся к ним при чтении.
ANSWER_MODE = {
    "brain_only": False,          # мозг больше не отвечает «в одиночку» без памяти
    "answer_core": True,          # ответ — мозг + своя LLM
    "llm_brain_lead": True,       # мозг ведёт, LLM дополняет
    "llm_answer_full": True,      # ответ целиком пишут мозг + LLM (без «Мыслей»)
    "llm_words_strict": True,     # слова LLM — только настоящие слова базы
    "llm_link_on": True,          # LLM связана с мозгом
    "llm_link_tpl": True,         # шаблон Приветствие / Ответ / Вывод
    "llm_link_reply": False,      # без отдельной строки «🤖 мысль (фантазия)»
    "llm_link_gen": True,         # LLM переписывает факты
    "max_cloud_reply": True,      # МАКС/Разговор: весь мозг облака
    "max_brain_reply": False,     # без отдельного сообщения «мысли мозга»
    "long_default": False,
}


# v3.155: НАСТРОЙКИ ИЗ ПАМЯТИ. settings() зовут ~155 мест кода; раньше каждый
# вызов брал общий замок LOCK и заново читал settings.json с диска. LOCK держат
# и модули, сохраняющие большие файлы (индекс кода, ссылки GitHub, «воображение»,
# почта мозга), — пока они пишут, настройки ждали: «долго грузятся настройки».
# Теперь у настроек свой замок и копия в памяти; файл перечитывается только
# если он изменился (по времени изменения), после записи — сразу.
_SET_LOCK = threading.RLock()
_SET_CACHE = {"mt": None, "s": None}


def _settings_mtime():
    try:
        return os.stat(p("settings.json")).st_mtime_ns
    except OSError:
        return -1


def settings():
    mt = _settings_mtime()
    with _SET_LOCK:
        c = _SET_CACHE
        if c["s"] is None or c["mt"] != mt:
            s = load_json("settings.json", None)
            if not isinstance(s, dict):
                s = default_settings()
            base = default_settings()
            for k, v in base.items():
                s.setdefault(k, v)
            if not isinstance(s.get("teacher"), dict):
                s["teacher"] = base["teacher"]
            s.pop("serial_cam", None)      # камера облака удалена (v3.5) —
            s["d6_on"] = True              # v3.35: D6 включён всегда (не отключается)
            s.update(ANSWER_MODE)          # v3.99: один режим ответов — мозг + своя LLM
            c["s"], c["mt"] = s, mt        # старые ключи в settings.json не мешают
        # копия: вызывающие могут менять словарь, кэш от этого не портится
        return json.loads(json.dumps(c["s"], ensure_ascii=False))


def update_settings(patch):
    with _SET_LOCK:
        s = settings()
        for k, v in (patch or {}).items():
            if k == "teacher" and isinstance(v, dict):
                for tk, tv in v.items():
                    if tk in s["teacher"]:
                        s["teacher"][tk] = tv
            elif k == "serial_cam":
                continue                   # камера облака удалена (v3.5)
            elif k == "d6_on":
                s[k] = True                # v3.35: всегда включено
            elif k in ANSWER_MODE:
                s[k] = ANSWER_MODE[k]      # v3.99: режим ответов не переключается
            elif k in s and k != "title":
                s[k] = v
        save_json("settings.json", s)
        _SET_CACHE["s"], _SET_CACHE["mt"] = None, None   # перечитать свежий
        return s


# -------------------------------- платы -------------------------------------
def boards():
    with LOCK:
        return load_json("boards.json", {}) or {}


def board_touch(bid, name="", kind="", url="", note="", ip="",
                via_bridge=None):
    with LOCK:
        bs = boards()
        b = bs.get(bid) or {}
        b.update({
            "id": bid,
            "name": name or b.get("name") or bid,
            "kind": kind or b.get("kind") or "board",
            "url": url or b.get("url") or "",
            "note": note or b.get("note") or "",
            "ip": ip or b.get("ip") or "",
            "last_seen": time.time(),
        })
        if via_bridge is not None:
            b["via_bridge"] = bool(via_bridge)
        b.setdefault("first_seen", time.time())
        bs[bid] = b
        save_json("boards.json", bs)
        return b


def board_seen(bid, ip=""):
    """Плата жива. v3.27: ip — адрес, с которого она пришла по мозг-почте
    (по нему мост находит плату, если bridge_url неверный)."""
    bid = str(bid or "").strip()[:40]
    if not bid or bid == "?":
        return
    with LOCK:
        bs = boards()
        if bid not in bs:
            # v3.172: плата на связи, но ещё не регистрировалась (регистрация
            # раз в 10 мин, облако могло перезапуститься) — раньше её просто
            # не было в списке, и панель горела красным «платы: 0/0»
            now = time.time()
            bs[bid] = {"id": bid, "name": "Плата %s" % bid, "kind": "board",
                       "url": "", "note": "появилась по каналу связи",
                       "ip": "", "first_seen": now, "last_seen": now}
        b = bs[bid]
        now = time.time()
        if now - float(b.get("last_seen") or 0) < 2.0 and \
                (not ip or b.get("ip") == ip):
            return                       # не пишем файл чаще раза в 2 с
        b["last_seen"] = now
        ip = str(ip or "").strip()
        if ip and not ip.startswith("127.") and ip != "::1":
            b["ip"] = ip
        save_json("boards.json", bs)


def board_delete(bid):
    with LOCK:
        bs = boards()
        bs.pop(bid, None)
        save_json("boards.json", bs)


def board_online(b, period=75.0):
    return (time.time() - float(b.get("last_seen") or 0)) < period


def free_boards():
    """Платы, которые в последнее время опрашивали почту (онлайн и не заняты)."""
    now = time.time()
    out = []
    for b in boards().values():
        if now - float(b.get("last_seen") or 0) < 75.0:
            out.append(b)
    out.sort(key=lambda x: -float(x.get("last_seen") or 0))
    return out


# -------------------------------- чаты --------------------------------------
def chat_path(cid):
    safe = "".join(ch for ch in str(cid) if ch.isalnum() or ch in "-_")[:40]
    return os.path.join("chats", safe + ".json")


def chat_load(cid):
    return load_json(chat_path(cid), None)


def chat_save(cid, obj):
    msgs = obj.get("messages") or []
    if len(msgs) > 300:                       # капа на размер файла чата
        obj["messages"] = msgs[-300:]
    save_json(chat_path(cid), obj)


def chat_list(owner=None):
    """Список чатов. v3.24.2: owner=None — все (общий режим chats_by_ip=0);
    owner="<ip>" — только чаты этого адреса + старые общие чаты (без
    владельца), иначе с чужого адреса было бы видно всё."""
    out = []
    try:
        for fn in os.listdir(p("chats")):
            if not fn.endswith(".json"):
                continue
            c = load_json(os.path.join("chats", fn), None)
            if not c:
                continue
            out.append({
                "id": c.get("id"), "title": c.get("title") or "чат",
                "ts": c.get("ts", 0), "count": len(c.get("messages") or []),
                "kind": c.get("kind", "chat"),
                "owner": str(c.get("owner") or "")[:45],
            })
    except Exception:
        pass
    if owner is not None:
        out = [c for c in out
               if not c.get("owner") or c.get("owner") == str(owner)[:45]]
    out.sort(key=lambda x: -float(x.get("ts") or 0))
    return out


def chat_new(cid=None, title="", kind="chat", owner=""):
    """v3.24.2: owner — адрес (IP), создавший чат; пусто = общий чат."""
    cid = cid or ("c" + os.urandom(5).hex())
    obj = {"id": cid, "title": title or "Новый чат", "kind": kind,
           "owner": str(owner or "")[:45],
           "ts": time.time(), "messages": []}
    chat_save(cid, obj)
    return obj


def chat_allowed(chat, ip):
    """v3.24.2: можно ли работать с чатом с адреса ip. Чаты без владельца
    (созданные до v3.24.2) и лента «Мысли плат» — общие. Настройка
    chats_by_ip=False возвращает старое поведение (все видят все чаты)."""
    if not settings().get("chats_by_ip", True):
        return True
    own = str((chat or {}).get("owner") or "")
    return (not own) or own == str(ip or "")[:45]


def chat_delete(cid):
    try:
        os.remove(p(chat_path(cid)))
        return True
    except Exception:
        return False


# --------------------- мысли / хроника / задания ----------------------------
def _tclean(t):
    """v3.118: чистка мысли от «англ», «н», шума (llm2.thought_clean)."""
    try:
        import llm2
        return llm2.thought_clean(t)
    except Exception:
        return str(t or "")


def thoughts_add(board, text, kind="say"):
    text = _tclean(text)
    if not text:
        return False                  # шум («англ н н») — не мысль
    with LOCK:
        t = load_json("thoughts.json", []) or []
        t.append({"board": str(board)[:40], "text": str(text)[:400],
                  "kind": str(kind)[:16], "ts": time.time()})
        save_json("thoughts.json", t[-3000:])   # v3.95: не терять мысли плат
    return True


def thoughts_list(limit=80):
    t = load_json("thoughts.json", []) or []
    return t[-int(limit):][::-1]


# ----------------- мысли зон -> пин D6 платы (v3.17) ------------------------
def d6_add(board, text, zone="", sign=0, by=""):
    """Мысль зон облака в ящик платы (d6box.json): плата заберёт её опросом
    мозг-почты и проиграет на пине D6 (RPC d6seq -> PWM). Ящик короткий
    (8 мыслей): D6 — физический пин, играет мысли по одной, старое
    вытесняется новым; письма-вопросы в mailboxes.json не трогаем."""
    with LOCK:
        b = str(board or "")[:40]
        t = _d6_clean_text(_tclean(text))
        if not b or not t:
            return False
        box = load_json("d6box.json", {}) or {}
        arr = box.setdefault(b, [])
        arr.append({"id": "d6%s" % int(time.time() * 1000),
                    "text": t, "zone": str(zone or "")[:40],
                    "sign": int(sign or 0), "by": str(by or "")[:8],
                    "ts": time.time()})
        box[b] = arr[-8:]
        save_json("d6box.json", box)
        return True


def d6_take(board):
    """Забрать (и снять) мысли для платы — GET /api/brain/d6."""
    with LOCK:
        b = str(board or "")[:40]
        box = load_json("d6box.json", {}) or {}
        arr = box.pop(b, [])
        if arr or b in box:
            box.pop(b, None)
            save_json("d6box.json", box)
        return arr


def _d6_clean_text(text):
    """Одна строка без переносов (в ящике храним компактно); финальную
    подготовку к PWM делает плата (_gen_d6_seq: только буквы RU33)."""
    t = re.sub(r"\s+", " ", str(text or ""))
    return t.strip()[:240]


def chronicle_add(board, messages):
    """Хроника (/api/import): сообщения плат вида {role,text,zone,ts}."""
    with LOCK:
        ch = load_json("chronicle.json", []) or []
        for m in (messages or [])[-30:]:
            ch.append({"board": str(board)[:40],
                       "role": str(m.get("role") or "bot")[:8],
                       "text": str(m.get("text") or "")[:600],
                       "zone": str(m.get("zone") or "")[:24],
                       "ts": float(m.get("ts") or time.time() * 1000) / 1000.0})
        save_json("chronicle.json", ch[-600:])
    return True


def chronicle_list(limit=80):
    ch = load_json("chronicle.json", []) or []
    return ch[-int(limit):][::-1]


def gen_task_add(board, text, src="мозг"):
    with LOCK:
        q = load_json("gen_tasks.json", []) or []
        q.append({"board": str(board)[:40], "text": str(text)[:400],
                  "src": str(src)[:24], "ts": time.time()})
        save_json("gen_tasks.json", q[-20:])
    return True


def gen_task_take():
    with LOCK:
        q = load_json("gen_tasks.json", []) or []
        if not q:
            return None
        task = q.pop(0)
        save_json("gen_tasks.json", q)
        return task


def readings_put(board, readings):
    with LOCK:
        rd = load_json("readings.json", {}) or {}
        rd[str(board)[:40]] = {"readings": readings, "ts": time.time()}
        save_json("readings.json", rd)
    return True


def readings_all():
    return load_json("readings.json", {}) or {}


# ------------------------------ журнал --------------------------------------
def log(*args):
    line = time.strftime("%Y-%m-%d %H:%M:%S") + " " + " ".join(
        str(a) for a in args)
    try:
        print(line, flush=True)
    except Exception:
        pass                       # pythonw: консоли нет — не страшно
    with _LOG_LOCK:
        _LOGQ.append(line[:400])
        if len(_LOGQ) > 400:
            del _LOGQ[:-400]
        try:
            # v3.27: БАГФИКС — раньше КАЖДАЯ запись дописывала последние 20
            # строк: журнал рос в 20 раз быстрее (десятки МБ дублей)
            path = p("log.txt")
            try:
                if os.path.getsize(path) > _LOG_MAX:
                    os.replace(path, path + ".1")     # одна старая копия
            except OSError:
                pass
            with open(path, "a", encoding="utf-8") as f:
                f.write(line[:2000] + "\n")
        except Exception:
            pass


def log_tail(limit=80):
    with _LOG_LOCK:
        return list(_LOGQ[-int(limit):])


def notes():
    with LOCK:
        return load_json("notes.json", []) or []


def notes_add(text, board="владелец"):
    with LOCK:
        n = notes()
        n.append({"text": str(text)[:500], "board": str(board)[:40],
                  "ts": time.time()})
        save_json("notes.json", n[-200:])


def notes_clear_matching(q):
    with LOCK:
        n = notes()
        keep = [x for x in n if str(q or "").lower() not in str(x.get("text", "")).lower()]
        save_json("notes.json", keep)
        return len(n) - len(keep)


def stats():
    """Сводка платформы для панели/статуса."""
    n_msgs = 0
    for c in chat_list():
        n_msgs += int(c.get("count") or 0)
    mem_files = 0
    try:
        mem_files = len([f for f in os.listdir(p("memory")) if f.endswith(".json")])
    except Exception:
        pass
    kb = 0
    for sub in (os.path.join("kb", "common"), os.path.join("kb", "devices")):
        try:
            for root, _d, files in os.walk(p(sub)):
                kb += len([f for f in files if f.endswith(".json")])
        except Exception:
            pass
    try:                                  # v3.170: навыки -> приложения App Lab
        sk = [d for d in os.listdir(p("applab")) if os.path.isfile(p("applab", d, "app.json"))]
    except OSError:
        sk = []
    dwords = 0
    for name in ("ru_top", "ru_pos", "extra"):
        d = load_json(os.path.join("dicts", name + ".json"), {}) or {}
        dwords += len(d.get("words") or d.get("nouns") or [])
    bs = boards()
    return {
        "chats": len(chat_list()), "messages": n_msgs,
        "memory_devices": mem_files, "kb_articles": kb,
        "skills": len(sk), "dict_words": dwords,
        "thoughts": len(load_json("thoughts.json", []) or []),
        "gen_queue": len(load_json("gen_tasks.json", []) or []),
        "boards_total": len(bs),
        "boards_online": sum(1 for b in bs.values() if board_online(b)),
    }


# ---------------- v3.118: РАЗОВАЯ ЧИСТКА «англ н н» ИЗ ВСЕХ ХРАНИЛИЩ ----------
def purge_noise(force=False):
    """«англ н англ н н» прежней LLM разошлись по журналу мыслей, чату
    «Мысли плат», ящикам плат (D6), последней мысли LLM — и по кругу
    возвращались (плата повторяет мысль облака, облако учится на мысли
    платы). Один раз после обновления — вычистить. -> {что: сколько}."""
    flag = p("purge_v3118.flag")
    if os.path.isfile(flag) and not force:
        return {}
    out = {"thoughts": 0, "chat": 0, "d6": 0, "llm_last": 0, "chronicle": 0}
    with LOCK:
        th = load_json("thoughts.json", []) or []
        keep = []
        for t in th:
            tx = _tclean(t.get("text") or "")
            if not tx:
                out["thoughts"] += 1
                continue
            if tx != t.get("text"):
                t = dict(t, text=tx)
            keep.append(t)
        save_json("thoughts.json", keep)
        try:
            ch = chat_load("thoughts")
            if ch:
                ms = []
                for m in ch.get("messages") or []:
                    tx = _tclean(m.get("text") or "")
                    if not tx:
                        out["chat"] += 1
                        continue
                    ms.append(dict(m, text=tx))
                ch["messages"] = ms
                chat_save("thoughts", ch)
        except Exception:
            pass
        box = load_json("d6box.json", {}) or {}
        for b, lst in list(box.items()):
            if isinstance(lst, list):
                nl = []
                for it in lst:
                    tx = _tclean((it or {}).get("text") if isinstance(it, dict) else it)
                    if not tx:
                        out["d6"] += 1
                        continue
                    nl.append(dict(it, text=tx) if isinstance(it, dict) else tx)
                box[b] = nl
        save_json("d6box.json", box)
        ll = load_json("llmlink.json", {}) or {}
        last = ll.get("last") or {}
        if last.get("text") and not _tclean(last["text"]):
            ll["last"] = {}
            out["llm_last"] += 1
        ll["recent"] = [r for r in (ll.get("recent") or [])
                        if _tclean(r if isinstance(r, str) else
                                   (r or {}).get("text", ""))]
        save_json("llmlink.json", ll)
        try:                              # словари плат: «англ» — не слово
            import llm2
            for nm in ("extra", "ru_top"):
                dd = load_json("dicts/%s.json" % nm, None)
                if isinstance(dd, dict) and dd.get("words"):
                    n0 = len(dd["words"])
                    dd["words"] = [w for w in dd["words"]
                                   if str(w).lower() not in llm2.JUNK_W]
                    if len(dd["words"]) != n0:
                        save_json("dicts/%s.json" % nm, dd)
        except Exception:
            pass
        chr_ = load_json("chronicle.json", []) or []
        n0 = len(chr_)
        chr_ = [c for c in chr_ if _tclean(c.get("text") or "")]
        out["chronicle"] = n0 - len(chr_)
        save_json("chronicle.json", chr_)
    try:
        with open(flag, "w", encoding="utf-8") as f:
            f.write(json.dumps(out))
    except Exception:
        pass
    log("ЧИСТКА", "шум «англ н н» убран: мыслей %d, в чате «Мысли плат» %d, "
        "в ящиках плат %d, последняя мысль LLM %d, хроника %d" % (
            out["thoughts"], out["chat"], out["d6"], out["llm_last"],
            out["chronicle"]))
    return out
