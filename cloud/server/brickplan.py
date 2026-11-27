# -*- coding: utf-8 -*-
"""v3.230 — STAR GATE СОБИРАЕТ ПРИЛОЖЕНИЯ ИЗ КИРПИЧИКОВ.

Кирпичик — маленькая проверенная функция b_<имя>(x, ctx) с типом входа и выхода
и этапом конвейера: 1 разбор, 2 извлечение, 3 фильтр, 4 подсчёт, 5 сравнение,
6 оформление, 7 сохранение. У каждого — свой тест.
Планировщик: по словам задачи выбирает кирпичики (лучший на этап), ставит их
по этапам, сводит типы (сам вставляет переходники «текст→строки» и т.п.),
в конце — оформление в отчёт. Код приложения = исходники кирпичиков + run(),
который прогоняет фразу по цепочке. Цепочка проверяется запуском на примере
(образец данных от кирпичика) до сохранения.
Так собирается приложение, которого целиком никогда не было («журнал →
неудачные входы → по IP → больше порога → список блокировок → чек-лист»).
Свои кирпичики: функция b_<имя>(x, ctx) в ВКЛЮЧЁННОМ приложении с docstring
«Кирпичик: название | этап: 3 | вход: lines | выход: lines | слова: a, b» —
мозг берёт её в библиотеку сам.
Честно: это сборка из известных блоков по словам задачи, не понимание смысла."""
import ast
import re

# ----------------------------------------------------------------- кирпичики
# type: text, lines, records, list, counts, report
B = []


def brick(id, title, stage, tin, tout, words, code, test, sample=""):
    B.append({"id": id, "title": title, "stage": stage, "in": tin, "out": tout,
              "words": words, "code": code.strip("\n") + "\n", "test": test.strip("\n") + "\n",
              "sample": sample, "origin": "встроенный"})


AUTH_SAMPLE = """Nov 26 10:01:02 srv sshd[811]: Failed password for root from 203.0.113.7 port 50312 ssh2
Nov 26 10:01:05 srv sshd[811]: Failed password for root from 203.0.113.7 port 50313 ssh2
Nov 26 10:01:09 srv sshd[812]: Failed password for invalid user admin from 203.0.113.7 port 50320 ssh2
Nov 26 10:02:11 srv sshd[815]: Failed password for ivan from 198.51.100.23 port 41022 ssh2
Nov 26 10:03:40 srv sshd[820]: Accepted password for ivan from 192.168.1.15 port 52100 ssh2
Nov 26 10:04:01 srv sshd[822]: Failed password for root from 203.0.113.7 port 50400 ssh2
Nov 26 10:04:30 srv sshd[823]: Failed password for root from 203.0.113.7 port 50401 ssh2
Nov 26 10:05:12 srv sshd[830]: Failed password for test from 198.51.100.23 port 41100 ssh2"""

brick("lines", "Разбить на строки", 1, ["text"], "lines", ["строк", "журнал", "лог", "лога", "логов", "файл"],
      '''
def b_lines(x, ctx):
    """Текст -> непустые строки."""
    if isinstance(x, list):
        return [str(s) for s in x if str(s).strip()]
    return [s.rstrip() for s in str(x).splitlines() if s.strip()]
''', '''
assert b_lines("a\\n\\n b \\n", None) == ["a", " b"]
''')

brick("failed_logins", "Неудачные входы", 2, ["lines"], "records",
      ["неудач", "вход", "логин", "ssh", "брутф", "подбор", "пароль", "failed", "автори", "атак", "инцид"],
      '''
import re as _re
_FAIL = _re.compile(r"(?:Failed password for (?:invalid user )?(?P<u>\\S+) from (?P<ip>[\\d.]+))"
                    r"|(?:authentication failure.*?rhost=(?P<ip2>[\\d.]+)(?:.*?user=(?P<u2>\\S+))?)"
                    r"|(?:Invalid user (?P<u3>\\S+) from (?P<ip3>[\\d.]+))"
                    r"|(?:(?:4625|неудачн\\w* вход\\w*).*?(?P<ip4>\\d{1,3}(?:\\.\\d{1,3}){3}))", _re.I)


def b_failed_logins(x, ctx):
    """Строки журнала -> [{user, ip, line}] неудачных входов (sshd, PAM, Windows 4625)."""
    out = []
    for s in x:
        m = _FAIL.search(s)
        if m:
            ip = m.group("ip") or m.group("ip2") or m.group("ip3") or m.group("ip4")
            user = m.group("u") or m.group("u2") or m.group("u3") or "?"
            out.append({"user": user, "ip": ip, "line": s[:200]})
    return out
''', '''
r = b_failed_logins(["sshd: Failed password for root from 1.2.3.4 port 1", "Accepted password for x from 5.5.5.5"], None)
assert r == [{"user": "root", "ip": "1.2.3.4", "line": "sshd: Failed password for root from 1.2.3.4 port 1"}]
assert b_failed_logins(["pam: authentication failure; rhost=9.9.9.9 user=bob"], None)[0]["ip"] == "9.9.9.9"
''', AUTH_SAMPLE)

brick("ips", "Найти IP-адреса", 2, ["text", "lines"], "list", ["ip", "айпи", "адрес", "адреса", "ipv4"],
      '''
import re as _re
_IP = _re.compile(r"(?<![\\d.])(?:\\d{1,3}\\.){3}\\d{1,3}(?![\\d.])")


def b_ips(x, ctx):
    """Текст или строки -> IPv4-адреса (каждый октет 0..255), по порядку появления."""
    s = "\\n".join(x) if isinstance(x, list) else str(x)
    return [ip for ip in _IP.findall(s) if all(0 <= int(p) <= 255 for p in ip.split("."))]
''', '''
assert b_ips("a 10.0.0.1 b 999.1.1.1 c 8.8.8.8", None) == ["10.0.0.1", "8.8.8.8"]
''', "Подключения: 8.8.8.8, 10.0.0.5, 203.0.113.7, 8.8.8.8")

brick("urls", "Найти ссылки", 2, ["text", "lines"], "list", ["ссылк", "url", "домен", "сайт", "фишин", "письм"],
      '''
import re as _re
_URL = _re.compile(r"(?:https?://|www\\.)[^\\s<>\\"'«»]+", _re.I)


def b_urls(x, ctx):
    """Текст -> ссылки (http/https/www), без хвостовой пунктуации."""
    s = "\\n".join(x) if isinstance(x, list) else str(x)
    return [u.rstrip(".,;:!?)") for u in _URL.findall(s)]
''', '''
assert b_urls("см. https://a.ru/x, и www.b.com.", None) == ["https://a.ru/x", "www.b.com"]
''', "Письмо: срочно подтвердите вход https://secure-login.example-bank.xn--p1ai/verify и http://203.0.113.9/acc")

brick("ioc", "Индикаторы компрометации (IOC)", 2, ["text", "lines"], "records",
      ["ioc", "индикат", "компром", "хеш", "хэш", "md5", "sha256", "sha1"],
      '''
import re as _re


def b_ioc(x, ctx):
    """Текст -> [{type, value}]: хеши MD5/SHA1/SHA256, IPv4, домены, почта."""
    s = "\\n".join(x) if isinstance(x, list) else str(x)
    out, seen = [], set()
    pats = (("sha256", r"\\b[a-fA-F0-9]{64}\\b"), ("sha1", r"\\b[a-fA-F0-9]{40}\\b"), ("md5", r"\\b[a-fA-F0-9]{32}\\b"),
            ("email", r"\\b[\\w.+-]+@[\\w-]+(?:\\.[\\w-]+)+\\b"), ("ip", r"(?<![\\d.])(?:\\d{1,3}\\.){3}\\d{1,3}(?![\\d.])"),
            ("domain", r"\\b(?:[a-z0-9-]+\\.)+(?:ru|com|net|org|io|info|xyz|top|рф|xn--p1ai)\\b"))
    for t, p in pats:
        for v in _re.findall(p, s, _re.I):
            if v.lower() not in seen:
                seen.add(v.lower())
                out.append({"type": t, "value": v})
    return out
''', '''
r = b_ioc("hash d41d8cd98f00b204e9800998ecf8427e from 1.2.3.4 evil.xyz", None)
assert {"type": "md5", "value": "d41d8cd98f00b204e9800998ecf8427e"} in r and {"type": "ip", "value": "1.2.3.4"} in r
''', "Отчёт: файл 44d88612fea8a8f36de82e1278abb02f скачан с update-check.xyz (203.0.113.50), письмо от it-support@mail-secure.top")

brick("emails", "Найти адреса почты", 2, ["text", "lines"], "list", ["почт", "email", "e-mail", "отправит", "ящик"],
      '''
import re as _re


def b_emails(x, ctx):
    """Текст -> адреса электронной почты."""
    s = "\\n".join(x) if isinstance(x, list) else str(x)
    return _re.findall(r"\\b[\\w.+-]+@[\\w-]+(?:\\.[\\w-]+)+\\b", s)
''', '''
assert b_emails("от a.b@c.ru и x@y.com", None) == ["a.b@c.ru", "x@y.com"]
''', "Писали: boss@company.ru, invoice@pay-check.top")

brick("numbers", "Найти числа", 2, ["text", "lines"], "list", ["числ", "чисел", "значен", "показан", "замер"],
      '''
import re as _re


def b_numbers(x, ctx):
    """Текст -> числа (запятая — десятичный разделитель)."""
    s = "\\n".join(x) if isinstance(x, list) else str(x)
    return [float(v.replace(",", ".")) for v in _re.findall(r"-?\\d+(?:[.,]\\d+)?", s)]
''', '''
assert b_numbers("t 21,5 и -3 и 10", None) == [21.5, -3.0, 10.0]
''', "Температура за день: 21,5 22 23,4 19 18,7")

brick("csv", "Разобрать CSV", 1, ["text"], "records", ["csv", "таблиц", "столбц", "колонк"],
      '''
import csv as _csv
import io as _io


def b_csv(x, ctx):
    """CSV-текст (первая строка — заголовки; разделитель , или ;) -> [{колонка: значение}]."""
    s = str(x)
    body = s[s.find("\\n", 0) + 1:] if not ("," in s.splitlines()[0] or ";" in s.splitlines()[0]) else s
    try:
        d = _csv.Sniffer().sniff(body[:2000], delimiters=",;")
    except Exception:
        d = _csv.excel
    return [dict(r) for r in _csv.DictReader(_io.StringIO(body), dialect=d)]
''', '''
r = b_csv("name;n\\na;1\\nb;2", None)
assert r == [{"name": "a", "n": "1"}, {"name": "b", "n": "2"}]
''', "ip,event\n203.0.113.7,fail\n203.0.113.7,fail\n10.0.0.2,ok")

brick("timeline", "Хронология событий", 3, ["lines"], "records", ["хроно", "времен", "таймлайн", "последоват", "когда"],
      '''
import re as _re
_T = _re.compile(r"(\\d{4}-\\d{2}-\\d{2}[ T]\\d{2}:\\d{2}(?::\\d{2})?|[A-Z][a-z]{2}\\s+\\d{1,2}\\s+\\d{2}:\\d{2}:\\d{2}|\\d{2}:\\d{2}:\\d{2})")


def b_timeline(x, ctx):
    """Строки -> [{time, event}] по порядку времени (строки без времени отбрасываются)."""
    out = []
    for s in x:
        m = _T.search(s)
        if m:
            out.append({"time": m.group(1), "event": s[m.end():].strip()[:160]})
    return sorted(out, key=lambda r: r["time"])
''', '''
r = b_timeline(["10:05:00 b", "без времени", "09:00:01 a"], None)
assert [x["event"] for x in r] == ["a", "b"]
''', AUTH_SAMPLE)

brick("public_only", "Только внешние IP", 3, ["list"], "list", ["внешн", "публичн", "интернет", "чужие"],
      '''
import ipaddress as _ia


def b_public_only(x, ctx):
    """IP-адреса -> только публичные (без 10.x, 192.168.x, 127.x и т.п.)."""
    out = []
    for v in x:
        try:
            if _ia.ip_address(str(v)).is_global:
                out.append(v)
        except ValueError:
            pass
    return out
''', '''
assert b_public_only(["10.0.0.1", "8.8.8.8", "192.168.1.2"], None) == ["8.8.8.8"]
''')

brick("suspicious_urls", "Подозрительные ссылки", 3, ["list"], "records",
      ["подозр", "фишин", "опасн", "мошен", "поддел"],
      '''
import re as _re


def b_suspicious_urls(x, ctx):
    """Ссылки -> [{url, score, why}] по признакам фишинга (IP вместо имени, punycode, http,
    @ в адресе, слова login/verify/secure, много поддоменов, сокращатели, длина)."""
    out = []
    for u in x:
        low, why = str(u).lower(), []
        host = _re.sub(r"^(?:https?://)?", "", low).split("/")[0].split("@")[-1]
        if _re.fullmatch(r"[\\d.]+(?::\\d+)?", host):
            why.append("IP вместо имени")
        if "xn--" in host:
            why.append("punycode (похожие буквы)")
        if low.startswith("http://"):
            why.append("без https")
        if "@" in low.split("?")[0]:
            why.append("@ в адресе")
        if _re.search(r"login|verify|secure|account|update|confirm|bank|pay|подтверд", low):
            why.append("слова-приманки")
        if host.count(".") >= 3:
            why.append("много поддоменов")
        if host in ("bit.ly", "clck.ru", "tinyurl.com", "goo.su", "t.co"):
            why.append("сокращатель ссылок")
        if len(low) > 90:
            why.append("очень длинная")
        out.append({"url": u, "score": len(why), "why": ", ".join(why) or "признаков нет"})
    return sorted(out, key=lambda r: -r["score"])
''', '''
r = b_suspicious_urls(["http://203.0.113.9/login", "https://ya.ru"], None)
assert r[0]["score"] >= 3 and r[-1]["score"] == 0
''')

brick("count", "Посчитать по значениям", 4, ["records", "list"], "counts",
      ["скольк", "посчит", "топ", "част", "групп", "кажд", "количест", "статист"],
      '''
from collections import Counter as _C


def b_count(x, ctx):
    """Список или записи -> [(значение, сколько раз)] по убыванию. У записей берётся ip, иначе user/type/первое поле."""
    vals = []
    for r in x:
        if isinstance(r, dict):
            key = next((k for k in ("ip", "user", "type", "url") if k in r), None) or (list(r)[0] if r else "")
            vals.append(str(r.get(key)))
        else:
            vals.append(str(r))
    return _C(vals).most_common()
''', '''
assert b_count([{"ip": "a"}, {"ip": "b"}, {"ip": "a"}], None) == [("a", 2), ("b", 1)]
assert b_count(["x", "x", "y"], None)[0] == ("x", 2)
''')

brick("threshold", "Оставить выше порога", 4.5, ["counts"], "counts",
      ["порог", "больш", "чаще", "свыше", "брутф", "подбор", "атак", "много"],
      '''
import re as _re


def b_threshold(x, ctx):
    """[(значение, n)] -> только n >= порога. Порог из фразы («больше 5», «от 3 раз»), иначе 3."""
    m = _re.search(r"(?:больше|чаще|свыше|от|>=?)\\s*(\\d+)", str(getattr(ctx, "text", "") or ""))
    n = int(m.group(1)) + (1 if m and m.group(0).startswith(("больше", "чаще", "свыше", ">")) and not m.group(0).startswith(">=") else 0) if m else 3
    return [(k, c) for k, c in x if c >= n]
''', '''
class C: text = "больше 2"
assert b_threshold([("a", 5), ("b", 2), ("c", 3)], C()) == [("a", 5), ("c", 3)]
class D: text = ""
assert b_threshold([("a", 5), ("b", 2)], D()) == [("a", 5)]
''')

brick("stats", "Статистика чисел", 4, ["list"], "report", ["статист", "средн", "минимум", "максим", "сумм", "разброс"],
      '''
def b_stats(x, ctx):
    """Числа -> строка: количество, минимум, максимум, среднее, сумма."""
    v = [float(a) for a in x]
    if not v:
        return "Чисел нет."
    return "Чисел: %d; минимум %g; максимум %g; среднее %.4g; сумма %g" % (len(v), min(v), max(v), sum(v) / len(v), sum(v))
''', '''
assert "среднее 2" in b_stats([1, 2, 3], None)
''')

brick("blocklist", "Сверить со списком блокировок", 5, ["counts", "list", "records"], "records",
      ["блок", "чёрн", "черн", "бан", "blocklist", "сверь", "сравни", "известн"],
      '''
import re as _re


def b_blocklist(x, ctx):
    """Значения (или [(значение, n)]) -> [{value, n, listed}]. Список блокировок: ctx.state["blocklist"]
    + адреса из фразы после «блок:»/«список:». «запомни блок: ...» пополняет список приложения."""
    text = str(getattr(ctx, "text", "") or "")
    st = getattr(ctx, "state", None)
    bl = set((st or {}).get("blocklist", []))
    m = _re.search(r"(?:блок|список)\\w*\\s*:\\s*(.+)", text, _re.I)
    if m:
        extra = set(_re.findall(r"[\\w.:-]+", m.group(1)))
        bl |= extra
        if st is not None and _re.search(r"запомни", text, _re.I):
            st["blocklist"] = sorted(set(st.get("blocklist", [])) | extra)
            try:
                ctx.save_state()
            except Exception:
                pass
    out = []
    for r in x:
        if isinstance(r, tuple):
            v, n = r
        elif isinstance(r, dict):
            v, n = r.get("ip") or r.get("value") or r.get("url"), 1
        else:
            v, n = r, 1
        out.append({"value": str(v), "n": n, "listed": str(v) in bl})
    return out
''', '''
class C: text = "сверь, блок: 1.1.1.1"; state = {}
r = b_blocklist([("1.1.1.1", 4), ("2.2.2.2", 1)], C())
assert r[0]["listed"] is True and r[1]["listed"] is False
''')

brick("password", "Проверить надёжность пароля", 2, ["text"], "report",
      ["пароль", "пароля", "надёжн", "надежн", "сложност", "стойк"],
      '''
import math as _m
import re as _re


def b_password(x, ctx):
    """Фраза с паролем (после «пароль» или в кавычках) -> оценка: длина, наборы символов, энтропия, советы. Пароль не сохраняется."""
    s = str(x)
    m = _re.search(r"[«\\"']([^»\\"']+)[»\\"']", s) or _re.search(r"парол\\w*\\s*[:\\-]?\\s*(\\S+)\\s*$", s, _re.I)
    if not m:
        return "Пришлите пароль в кавычках: проверь пароль «...». Я его не сохраняю."
    p = m.group(1)
    pools = [(r"[a-z]", 26), (r"[A-Z]", 26), (r"\\d", 10), (r"[а-яё]", 33), (r"[А-ЯЁ]", 33), (r"[^\\w]", 32)]
    size = sum(n for rx, n in pools if _re.search(rx, p))
    bits = len(p) * _m.log2(size or 1)
    tips = []
    if len(p) < 12:
        tips.append("длина от 12 символов")
    if not _re.search(r"\\d", p):
        tips.append("добавьте цифры")
    if not _re.search(r"[^\\w]", p):
        tips.append("добавьте спецсимволы")
    if _re.search(r"(.)\\1\\1", p) or p.lower() in ("password", "qwerty", "123456", "пароль", "admin"):
        tips.append("уберите повторы и словарные слова")
    level = "слабый" if bits < 40 else "средний" if bits < 70 else "сильный"
    return "Пароль: %s (длина %d, ~%d бит).%s" % (level, len(p), bits, (" Советы: " + "; ".join(tips) + ".") if tips else "")
''', '''
assert "слабый" in b_password("проверь пароль «qwerty»", None)
assert "сильный" in b_password("проверь пароль «Tr0ub4dor&3-Long-Pass!»", None)
''', "проверь пароль «Admin2024»")

brick("report", "Оформить отчёт", 6, ["records", "list", "counts", "lines", "text", "report"], "report",
      ["отчёт", "отчет", "покажи", "выведи", "итог", "список"],
      '''
def b_report(x, ctx):
    """Любой результат -> читаемый текст (таблица записей, топ значений, список)."""
    if isinstance(x, str):
        return x
    if not x:
        return "Ничего не найдено."
    lines = []
    for r in list(x)[:50]:
        if isinstance(r, dict):
            lines.append("• " + "; ".join("%s: %s" % (k, v) for k, v in r.items() if k != "line"))
        elif isinstance(r, tuple):
            lines.append("• %s — %s" % r)
        else:
            lines.append("• %s" % r)
    more = "\\n…и ещё %d" % (len(x) - 50) if len(x) > 50 else ""
    return "Найдено: %d\\n%s%s" % (len(x), "\\n".join(lines), more)
''', '''
assert b_report([("a", 2)], None) == "Найдено: 1\\n• a — 2"
assert b_report([], None) == "Ничего не найдено."
''')

brick("checklist", "Чек-лист реагирования на инцидент", 6.5, ["report"], "report",
      ["инцид", "реагир", "чек", "что делать", "действ", "сработ"],
      '''
def b_checklist(x, ctx):
    """Отчёт -> отчёт + порядок действий при инциденте (по этапам: выявить, сдержать, устранить, восстановить, выводы)."""
    steps = ("1. Выявить: зафиксируйте время, источники (IP, учётки), сохраните журналы до изменений.",
             "2. Сдержать: заблокируйте адреса в файрволе/fail2ban, смените пароли затронутых учёток, отключите лишний доступ.",
             "3. Устранить: найдите вход злоумышленника (ключи SSH, cron, новые пользователи), закройте уязвимость.",
             "4. Восстановить: верните сервисы, проверьте целостность, усилите мониторинг.",
             "5. Выводы: запишите хронологию и меры в базу знаний, обновите правила.")
    return str(x).rstrip() + "\\n\\nЧто делать:\\n" + "\\n".join(steps)
''', '''
assert "Сдержать" in b_checklist("Найдено: 0", None)
''')

brick("save_kb", "Сохранить в базу знаний", 7, ["report"], "report",
      ["сохран", "зафиксир", "запиш", "база знан", "базу знан", "журнал инцид"],
      '''
import time as _t


def b_save_kb(x, ctx):
    """Отчёт -> тот же отчёт; копия — статьёй в базу знаний (если доступна)."""
    title = "Инцидент %s" % _t.strftime("%Y-%m-%d %H:%M")
    try:
        ctx.kb.add(title, str(x))
        return str(x) + "\\n\\n(Сохранено в базу знаний: «%s».)" % title
    except Exception as e:
        return str(x) + "\\n\\n(В базу знаний не сохранено: %s.)" % e
''', '''
class K:
    def add(self, t, x): self.t = t
class C: kb = K()
assert "Сохранено" in b_save_kb("отчёт", C())
''')

# ---------------------------------------------------- v3.234: аппаратный уровень
brick("hw_read", "Прочитать выводы платы", 1, ["text"], "list",
      ["датчик", "аналог", "a5", "прочитай", "измерь", "замер", "показан", "напряж", "вывод", "пин"],
      '''
import re as _re


def b_hw_read(x, ctx):
    """Фраза -> показания платы: A5 (АЦП 0..1023) и цифровые D5/D7, если названы во фразе.
    Аппаратный уровень: ctx.boards.hw (свободные выводы МК; плата 2.111+)."""
    s = str(getattr(ctx, "text", "") or x)
    pins = [p.upper() for p in _re.findall(r"\\b[dD](5|7)\\b", s)]
    cmds = ["a A5"] + ["r D%s" % p for p in dict.fromkeys(pins)]
    out = ctx.boards.hw(";".join(cmds))
    vals = []
    for line in str(out).splitlines():
        m = _re.search(r"ok:(-?\\d+)", line)
        if m:
            vals.append(float(m.group(1)))
    return vals
''', '''
class B:
    def hw(self, c): return "a A5 → ok:512\\nr D7 → ok:1" if "D7" in c else "a A5 → ok:512"
class C: text = "прочитай датчик и D7"; boards = B()
assert b_hw_read("", C()) == [512.0, 1.0]
''', "прочитай датчик A5; если больше 300 включи D7")

brick("hw_set", "Включить/выключить вывод платы", 7, ["report", "list", "text"], "report",
      ["включи", "выключи", "реле", "светодиод", "мигни", "d5", "d7", "вывод", "пин", "лампу"],
      '''
import re as _re


def b_hw_set(x, ctx):
    """Фраза -> команда выводу: «включи D7», «выключи D7», «мигни D5».
    Только свободные выводы D5 и D7 (плата проверяет ещё раз; ШИМ недоступен — таймеры заняты мозгом)."""
    s = str(getattr(ctx, "text", "") or "").lower()
    m = _re.search(r"\\b[dд](5|7)\\b", s)
    if not m:
        return str(x) + "\\n(Вывод не назван: D5 или D7.)"
    pin = "D" + m.group(1)
    if _re.search(r"шим|pwm|яркост", s):
        return str(x) + "\\n(ШИМ на свободных выводах недоступен: таймеры заняты словами D6 и речью D9.)"
    if _re.search(r"мигни|импульс|pulse", s):
        cmd = "pulse %s 200" % pin
    elif _re.search(r"выключи|погаси|отключи", s):
        cmd = "w %s 0" % pin
    else:
        cmd = "w %s 1" % pin
    r = ctx.boards.hw(cmd)
    head = "" if isinstance(x, str) and x == getattr(ctx, "text", None) else str(x) + "\\n"
    return head + "🔌 " + str(r)
''', '''
class B:
    def hw(self, c): self.c = c; return c + " → ok"
class C: text = "выключи D7"; boards = B()
assert "w D7 0" in b_hw_set("отчёт", C())
C.text = "мигни d5"; b_hw_set("", C()); assert C.boards.c == "pulse D5 200"
''')

brick("hw_rule", "Правило: порог → вывод", 5, ["list"], "report",
      ["если", "порог", "автомат", "когда", "превыс", "больш", "меньш"],
      '''
import re as _re


def b_hw_rule(x, ctx):
    """Числа (показания) -> правило из фразы «если больше 600 включи D7» (или «меньше …»):
    условие выполнено — команда выводу, иначе — противоположная. Только D5/D7."""
    s = str(getattr(ctx, "text", "") or "").lower()
    th = _re.search(r"(больше|выше|меньше|ниже)\\s*(\\d+)", s)
    pin = _re.search(r"\\b[dд](5|7)\\b", s)
    if not x:
        return "Показаний нет."
    v = float(x[0])
    if not th or not pin:
        return "Показание: %g. Правило не задано (пример: «если больше 600 включи D7»)." % v
    n = float(th.group(2))
    hit = v > n if th.group(1) in ("больше", "выше") else v < n
    on = 0 if _re.search(r"выключи|погаси", s) else 1
    want = on if hit else 1 - on
    r = ctx.boards.hw("w D%s %d" % (pin.group(1), want))
    return "Показание %g %s %g → D%s = %d. %s" % (v, "≥" if hit else "не", n, pin.group(1), want, r)
''', '''
class B:
    def hw(self, c): self.c = c; return "ok"
class C: text = "если больше 600 включи D7"; boards = B()
assert "D7 = 1" in b_hw_rule([700.0], C()) and C.boards.c == "w D7 1"
assert "D7 = 0" in b_hw_rule([100.0], C())
''')

# смысл, а не только тип: кирпичик ставится только после этих «поставщиков»
AFTER = {"suspicious_urls": {"urls"}, "public_only": {"ips"}, "stats": {"numbers", "hw_read"},
         "hw_rule": {"numbers", "hw_read"}, "threshold": {"count"}}


# область задачи: в одной цепочке не смешиваются ИБ, данные и железо (общие кирпичики — везде)
DOMAIN = {"failed_logins": "sec", "ips": "sec", "urls": "sec", "ioc": "sec", "emails": "sec", "public_only": "sec",
          "suspicious_urls": "sec", "blocklist": "sec", "checklist": "sec", "password": "sec",
          "numbers": "data", "csv": "data", "timeline": "data", "stats": "data",
          "hw_read": "hw", "hw_set": "hw", "hw_rule": "hw"}
EXCLUDE = {"hw_set": {"hw_rule"}, "hw_rule": {"hw_set"}}      # правило само включает вывод


def _domain_ok(bid, chain):
    d = DOMAIN.get(bid)
    if not d:
        return True
    have = {DOMAIN.get(c) for c in chain} - {None}
    if bid == "stats" and "hw" in have:            # статистика показаний платы — можно
        return True
    return not have or have == {d}


def _after_ok(bid, chain):
    if EXCLUDE.get(bid, set()) & set(chain):
        return False
    if not _domain_ok(bid, chain):
        return False
    need = AFTER.get(bid)
    if not need:
        return True
    prev = [c for c in chain if c not in ("lines", "report")]
    return bool(prev) and prev[-1] in need or (bid == "threshold" and prev and prev[-1] == "count")


# переходники типов: (из, в) -> id кирпичика
ADAPT = {("text", "lines"): "lines", ("records", "counts"): "count", ("list", "counts"): "count"}
for _t in ("records", "list", "counts", "lines", "text"):
    ADAPT[(_t, "report")] = "report"


# ----------------------------------------------------------------- библиотека
def _stems(s):
    return {w[:5] for w in re.findall(r"[a-zа-яё0-9]{2,}", str(s).lower())}


def learned():
    """Кирпичики из включённых приложений: def b_<имя>(x, ctx) с docstring
    «Кирпичик: название | этап: N | вход: T | выход: T | слова: a, b»."""
    out = []
    try:
        import pyapps
        apps = [a for a in pyapps.apps() if a.get("enabled")]
    except Exception:
        return out
    for a in apps:
        g = pyapps.get(a["id"], with_code=True) or {}
        code = g.get("code") or ""
        if "Кирпичик:" not in code:
            continue
        try:
            tree = ast.parse(code)
        except SyntaxError:
            continue
        imports = "\n".join(ast.get_source_segment(code, n) for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom)))
        for n in tree.body:
            if not (isinstance(n, ast.FunctionDef) and n.name.startswith("b_")):
                continue
            doc = ast.get_docstring(n) or ""
            m = re.match(r"Кирпичик:\s*([^|]+)\|\s*этап:\s*([\d.]+)\s*\|\s*вход:\s*([\w, ]+)\|\s*выход:\s*(\w+)\s*\|\s*слова:\s*(.+)", doc)
            if not m:
                continue
            bid = n.name[2:]
            if any(x["id"] == bid for x in B):
                continue
            src = ast.get_source_segment(code, n)
            out.append({"id": bid, "title": m.group(1).strip(), "stage": float(m.group(2)),
                        "in": [t.strip() for t in m.group(3).split(",") if t.strip()], "out": m.group(4).strip(),
                        "words": [w.strip().lower()[:6] for w in m.group(5).split(",") if w.strip()],
                        "code": (imports + "\n\n\n" if imports else "") + src + "\n", "test": "", "sample": "",
                        "origin": "выучен из приложения «%s»" % a.get("name", a["id"])})
    return out


# ------------------------------------------- v3.234: свои кирпичики (инструмент)
_USER = "bricks_user.json"
_TPL = "brick_templates.json"
TYPES = ("text", "lines", "records", "list", "counts", "report")
_BAD = ("subprocess", "os.system", "eval(", "exec(", "__import__", "socket", "shutil.rmtree", "os.remove", "ctypes", "open(")


def _store():
    import store
    return store


def user_bricks(all_=False):
    lst = _store().load_json(_USER, []) or []
    return [b for b in lst if all_ or b.get("enabled", True)]


def check_brick(spec):
    """Проверка кирпичика -> (ok, причина, нормализованный)."""
    bid = str(spec.get("id") or "").strip().lower()
    if not re.fullmatch(r"[a-z][a-z0-9_]{1,30}", bid):
        return False, "id: латиница, цифры и _, 2–31 знак, с буквы", None
    if any(b["id"] == bid for b in B):
        return False, "так зовут встроенный кирпичик — выберите другое id", None
    try:
        stage = float(spec.get("stage") or 0)
    except (TypeError, ValueError):
        stage = 0
    if not 1 <= stage <= 7:
        return False, "этап от 1 до 7", None
    tin = [t for t in (spec.get("in") or []) if t in TYPES] if isinstance(spec.get("in"), list) else \
        [t.strip() for t in str(spec.get("in") or "").split(",") if t.strip() in TYPES]
    tout = str(spec.get("out") or "")
    if not tin or tout not in TYPES:
        return False, "вход и выход — из: " + ", ".join(TYPES), None
    words = spec.get("words") if isinstance(spec.get("words"), list) else str(spec.get("words") or "").split(",")
    words = [w.strip().lower()[:20] for w in words if w.strip()][:20]
    if not words:
        return False, "нужны слова задачи — по ним мозг ставит кирпичик в цепочку", None
    code = str(spec.get("code") or "")
    if any(t in code for t in _BAD):
        return False, "в коде запрещённое: " + next(t for t in _BAD if t in code), None
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return False, "синтаксис: %s (строка %s)" % (e.msg, e.lineno), None
    fn = next((n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "b_" + bid), None)
    if not fn or len(fn.args.args) != 2:
        return False, "нужна функция b_%s(x, ctx)" % bid, None
    b = {"id": bid, "title": str(spec.get("title") or bid)[:60], "stage": stage, "in": tin, "out": tout, "words": words,
         "code": code.rstrip() + "\n", "test": str(spec.get("test") or "").rstrip() + "\n", "sample": str(spec.get("sample") or "")[:3000]}
    return True, "", b


def test_brick(b, timeout=10):
    """Тест кирпичика в песочнице App Lab (поток с таймаутом) -> (ok, сообщение)."""
    import pyapps
    t = (b.get("test") or "").strip()
    if not t:
        return True, "теста нет — проверен только синтаксис"
    body = "\n".join("    " + ln for ln in t.splitlines())
    code = b["code"] + "\n\ndef run(text, ctx):\n" + body + "\n    return 'тест пройден'\n"
    r = pyapps.run_code("brick_" + b["id"], code, "", timeout=timeout)
    return (True, "тест пройден") if r.get("ok") else (False, r.get("error") or "тест не прошёл")


def save_user_brick(spec, origin="владелец"):
    ok, why, b = check_brick(spec)
    if not ok:
        return {"ok": False, "error": why}
    tok, msg = test_brick(b)
    if not tok:
        return {"ok": False, "error": "тест: " + msg}
    b.update(origin=str(origin)[:60], ts=__import__("time").time(), enabled=True)
    st = _store()
    lst = [x for x in (st.load_json(_USER, []) or []) if x.get("id") != b["id"]]
    lst.append(b)
    st.save_json(_USER, lst[-300:])
    st.log("КИРПИЧИКИ", "сохранён кирпичик b_%s (%s): %s" % (b["id"], origin, msg))
    return {"ok": True, "brick": b, "test": msg}


def delete_user_brick(bid):
    st = _store()
    lst = st.load_json(_USER, []) or []
    new = [x for x in lst if x.get("id") != bid]
    st.save_json(_USER, new)
    return {"ok": len(new) != len(lst)}


def library():
    out = list(B)
    seen = {b["id"] for b in out}
    for b in user_bricks():
        if b["id"] not in seen:
            seen.add(b["id"])
            out.append(dict(b, origin="свой: " + str(b.get("origin") or "")))
    for b in learned():
        if b["id"] not in seen:
            seen.add(b["id"])
            out.append(b)
    return out


def by_id(bid):
    return next((b for b in library() if b["id"] == bid), None)


# ----------------------------------------------------------------- планировщик
def _score(b, q):
    """Сколько слов кирпичика есть в задаче. Многословное («база знан») — все части; одно слово — по началу."""
    hit = 0
    for w in b["words"]:
        parts = re.findall(r"[a-zа-яё0-9]{2,}", w.lower())
        if len(parts) > 1:
            hit += all(any(x.startswith(p[:5]) for x in q) for p in parts)
        elif parts:
            hit += any(x.startswith(parts[0][:5]) for x in q)
    return hit


def plan(task):
    """Задача -> {chain:[id], why:[...], skipped:[...]} | None (не за что зацепиться)."""
    q = _stems(task)
    lib = library()
    scored = [(b, _score(b, q)) for b in lib]
    scored = [(b, s) for b, s in scored if s > 0]
    if not scored:
        return None
    dom = {}
    for b, s in scored:
        if DOMAIN.get(b["id"]):
            dom[DOMAIN[b["id"]]] = dom.get(DOMAIN[b["id"]], 0) + s
    if dom:
        lead = max(dom, key=lambda k: (dom[k], k == "hw"))
        scored = [(b, s) for b, s in scored if DOMAIN.get(b["id"]) in (None, lead)
                  or (lead == "hw" and b["id"] == "stats")]
        if lead == "hw" and any(b["id"] == "hw_rule" for b, _s in scored):
            scored = [(b, s) for b, s in scored if b["id"] not in ("hw_set", "count", "threshold")]
    best = {}
    for b, s in scored:
        k = b["stage"]
        if k not in best or s > best[k][1]:
            best[k] = (b, s)
    chosen = [best[k][0] for k in sorted(best)]
    if all(b["stage"] >= 6 for b in chosen) and not any(b["id"] in ("password", "hw_set") for b in chosen):
        return None                       # одно оформление без дела — это не задача
    chain, cur, why, skipped = [], "text", [], []
    for b in chosen:
        if cur not in b["in"]:
            for need in b["in"]:
                ad = ADAPT.get((cur, need))
                if ad and ad != b["id"]:
                    chain.append(ad)
                    why.append("переходник «%s»: %s → %s" % (by_id(ad)["title"], cur, by_id(ad)["out"]))
                    cur = by_id(ad)["out"]
                    break
        if cur not in b["in"]:
            skipped.append("%s (ждёт %s, а есть %s)" % (b["title"], "/".join(b["in"]), cur))
            continue
        if not _after_ok(b["id"], chain):
            skipped.append("%s (не к месту после «%s»)" % (b["title"], by_id(chain[-1])["title"] if chain else "начала"))
            continue
        chain.append(b["id"])
        why.append("%s (этап %g)" % (b["title"], b["stage"]))
        cur = b["out"]
    if not chain or all(by_id(c)["stage"] >= 6 for c in chain) and "password" not in chain and "hw_set" not in chain:
        return None
    if cur != "report":
        chain.append("report")
        why.append("Оформить отчёт (этап 6, добавлено для вывода)")
    return {"chain": chain, "why": why, "skipped": skipped}


def compose(task, desc="", chain=None):
    """-> {ok, code, chain, why, skipped, sample} | {ok: False, why}. chain — готовая цепочка (автошаблон)."""
    p = {"chain": list(chain), "why": [by_id(c)["title"] for c in chain], "skipped": []} if chain else plan(task)
    if not p:
        return {"ok": False, "why": "по словам задачи не подобрались кирпичики"}
    if any(by_id(c) is None for c in p["chain"]):
        return {"ok": False, "why": "в цепочке есть кирпичик, которого больше нет"}
    seen, parts = set(), []
    for bid in p["chain"]:
        if bid in seen:
            continue
        seen.add(bid)
        parts.append("# --- кирпичик «%s» (%s)\n%s" % (by_id(bid)["title"], by_id(bid)["origin"], by_id(bid)["code"]))
    body = "\n".join("    x = b_%s(x, ctx)" % bid for bid in p["chain"])
    sample = next((by_id(b)["sample"] for b in p["chain"] if by_id(b)["sample"]), "")
    doc = ("App Lab: " + " ".join(str(desc or task).split()))[:120].replace('"', "'")
    code = ("# Собрано мозгом-12 из кирпичиков: %s\n"
            "# Данные — текст сообщения (журнал, письмо, список). Во фразе данных нет — показывается пример на SAMPLE.\n\n%s\n\n"
            "SAMPLE = %r\n\n\n"
            "def _chain(x, ctx):\n%s\n    return x\n\n\n"
            "def _empty(r):\n    return not r or str(r).startswith((\"Ничего не найдено\", \"Чисел нет\", \"Пришлите\"))\n\n\n"
            "def run(text, ctx):\n    \"\"\"%s\"\"\"\n    r = _chain(text, ctx)\n"
            "    if SAMPLE and \"\\n\" not in str(text) and _empty(r):\n"
            "        return \"Во фразе нет данных — показываю на образце (пришлите журнал или текст целиком):\\n\" + str(_chain(SAMPLE, ctx))\n"
            "    return r\n"
            % (" → ".join(by_id(b)["title"] for b in p["chain"]), "\n\n".join(parts), sample, body, doc))
    return {"ok": True, "code": code, "chain": p["chain"], "why": p["why"], "skipped": p["skipped"], "sample": sample}


# ------------------------------------------- v3.234: автогенерация шаблонов-цепочек
_EMPTY = ("Ничего не найдено", "Чисел нет", "Пришлите", "Во фразе нет данных", "Показаний нет", "Ошибок нет")


def _chains(max_len):
    """Все цепочки по этапам с совместимыми типами (переходники — сами)."""
    lib = [b for b in library() if b["id"] not in ("report", "lines")]
    res = []

    def walk(chain, cur, last):
        if chain:
            fin = list(chain) + ([] if cur == "report" else ["report"])
            res.append(fin)
        if len([c for c in chain if c not in ADAPT.values()]) >= max_len:
            return
        for b in lib:
            if b["stage"] <= last:
                continue
            seq, c = [], cur
            if c not in b["in"]:
                ad = next((ADAPT.get((c, need)) for need in b["in"] if ADAPT.get((c, need))), None)
                if not ad or ad == b["id"]:
                    continue
                seq.append(ad)
                c = by_id(ad)["out"]
            if c not in b["in"] or not _after_ok(b["id"], chain + seq):
                continue
            walk(chain + seq + [b["id"]], b["out"], b["stage"])
    walk([], "text", 0)
    return res


def autogen(max_len=3, limit=150, budget_s=40.0, per_head=10):
    """Сгенерировать проверенные шаблоны: перебрать цепочки, собрать каждую, запустить на образце
    данных её кирпичиков (заглушка ctx, без сети и железа) и оставить рабочие. -> {ok, made, tried}."""
    import time as _t
    import applab_guide
    t0, tried, made, out, heads = _t.time(), 0, 0, [], {}
    for chain in _chains(max_len):
        if _t.time() - t0 > budget_s or made >= limit:
            break
        core = [c for c in chain if c not in ("report", "lines", "count")]
        if not core or all(by_id(c)["stage"] >= 6 for c in core):
            continue
        if heads.get(core[0], 0) >= per_head:          # разнообразие: не больше per_head шаблонов на один «вход»
            continue
        sample = next((by_id(c)["sample"] for c in chain if by_id(c)["sample"]), "")
        if not sample:
            continue
        c = compose("", chain=chain)
        if not c.get("ok"):
            continue
        tried += 1
        ns = {}
        try:
            exec(compile(c["code"], "tpl", "exec"), ns)
            r = str(ns["_chain"](sample, applab_guide.stub_ctx(sample)) or "")
        except Exception:
            continue
        if not r.strip() or r.startswith(_EMPTY) or "Вывод не назван" in r or "Правило не задано" in r:
            continue
        heads[core[0]] = heads.get(core[0], 0) + 1
        words = []
        for cid in chain:
            for w in by_id(cid)["words"]:
                if w not in words:
                    words.append(w)
        out.append({"id": "t_" + "_".join(chain)[:60], "chain": chain, "title": " → ".join(by_id(x)["title"] for x in chain),
                    "words": words[:24], "example": r[:300], "ts": _t.time(),
                    "hw": any(x.startswith("hw_") for x in chain)})
        made += 1
    _store().save_json(_TPL, out)
    _store().log("КИРПИЧИКИ", "автошаблоны: проверено цепочек %d, рабочих %d" % (tried, made))
    return {"ok": True, "made": made, "tried": tried}


def templates():
    return _store().load_json(_TPL, []) or []


def match_template(task):
    """Лучший автошаблон под задачу -> (оценка, шаблон) | None (≥2 слова задачи в словах шаблона)."""
    q = _stems(task)
    best = None
    for t in templates():
        hit = sum(1 for w in t.get("words") or [] if any(x.startswith(w[:5]) for x in q))
        if hit >= 2 and (not best or hit > best[0] or (hit == best[0] and len(t["chain"]) < len(best[1]["chain"]))):
            best = (hit, t)
    return best


def verify_brick(b):
    """Тест кирпичика в отдельном пространстве имён -> (ok, ошибка)."""
    ns = {}
    try:
        exec(compile(b["code"] + "\n" + (b.get("test") or ""), "brick_" + b["id"], "exec"), ns)
        return True, ""
    except Exception as e:
        return False, "%s: %s" % (type(e).__name__, e)


def lesson_text(b):
    return ("Кирпичик мозга-12 «%s» (b_%s): этап %g, вход %s, выход %s. Слова задачи: %s. Источник: %s.\n```python\n%s```"
            % (b["title"], b["id"], b["stage"], "/".join(b["in"]), b["out"], ", ".join(b["words"]), b["origin"], b["code"]))


def corpus():
    return [("bp-" + b["id"], lesson_text(b)) for b in library()] + \
        [("bt-" + t["id"], "Шаблон-цепочка мозга-12: %s. Слова: %s. Пример результата: %s"
          % (t["title"], ", ".join(t["words"]), t["example"][:200])) for t in templates()]


def status():
    lib = library()
    return {"bricks": len(lib), "builtin": len(B), "user": len(user_bricks()), "learned": len(learned()),
            "templates": len(templates())}
