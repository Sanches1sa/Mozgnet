# -*- coding: utf-8 -*-
"""v3.178: ОБУЧАЮЩИЙ МАТЕРИАЛ ПОТОКА «Администрирование серверов» своей LLM.

Поток «admin» (llmstreams) учит свою LLM на трёх видах текста:
  1. Уроки (LESSONS ниже) — короткие объяснения: задача → команды → как
     читать вывод → что опасно. Написаны так, как агент окна «Серверы»
     должен рассуждать.
  2. Примеры диалога агента (EXAMPLES) — «Задача: … / Шаг: {json} /
     Вывод: … / Итог: …» — тот самый протокол инструментов агента.
  3. Живой опыт — успешные задания агента «Серверы» (data/admin_learn.json:
     задача, команды, сжатый вывод, итог). Чем больше агент работает,
     тем больше своего материала у потока.
"""
import hashlib

import store

_LEARN = "admin_learn.json"

LESSONS = [
    ("Состояние сервера за минуту",
     "Задача: понять, жив ли сервер и чем он занят. Команды: uptime (время работы и "
     "нагрузка за 1, 5, 15 минут), free -h (память и swap), df -h (место на дисках), "
     "systemctl --failed (упавшие службы), journalctl -p err -n 30 --no-pager (свежие "
     "ошибки). Нагрузка выше числа ядер (nproc) — сервер перегружен. Диск занят больше "
     "90 процентов — скоро начнутся отказы записи. Все эти команды только читают и "
     "безопасны."),
    ("Службы systemd",
     "systemctl status имя — состояние службы и последние строки журнала. systemctl "
     "restart имя — перезапуск, меняет состояние: сначала спроси владельца. systemctl "
     "enable --now имя — включить и запустить, после загрузки служба поднимется сама. "
     "journalctl -u имя -n 80 --no-pager — журнал службы. Если служба не стартует, "
     "смотри строку Main PID и код выхода, потом журнал."),
    ("Место на диске",
     "df -h показывает разделы. du -xh --max-depth=1 / 2>/dev/null | sort -h | tail -15 "
     "показывает самые большие папки. Частые виновники: /var/log (старые журналы), "
     "/var/lib/docker (образы), /tmp. Очистка: journalctl --vacuum-size=200M, docker "
     "system prune (спроси владельца — удаляет остановленные контейнеры), apt-get clean. "
     "Никогда не удаляй файлы в /usr, /etc, /boot вслепую."),
    ("Память и процессы",
     "free -h — сколько свободно. ps aux --sort=-%mem | head -15 — кто ест память. "
     "ps aux --sort=-%cpu | head -15 — кто грузит процессор. top -bn1 | head -25 — снимок "
     "в пакетном режиме (интерактивный top агенту не подходит). kill PID — мягко "
     "остановить процесс, kill -9 — только если мягко не вышло и владелец согласен."),
    ("Сеть и порты",
     "ip -br a — адреса интерфейсов. ss -tlnp — какие порты слушает сервер и какой "
     "процесс. ping -c 3 адрес — доступность. curl -sS -o /dev/null -w '%{http_code}' "
     "http://localhost:порт — отвечает ли веб-служба. Не открывай порты в брандмауэре "
     "без просьбы владельца: ufw status только читает, ufw allow меняет."),
    ("Пакеты Debian и Ubuntu",
     "apt-get update обновляет список пакетов. apt list --upgradable — что можно "
     "обновить. apt-get install -y имя — установка, меняет систему: спроси владельца. "
     "dpkg -l | grep имя — установлен ли пакет. Перед apt-get upgrade проверь место на "
     "диске и предупреди о возможном перезапуске служб."),
    ("Пакеты RED OS, Fedora, CentOS",
     "dnf check-update — что можно обновить. dnf install -y имя — установка. rpm -qa | "
     "grep имя — установлен ли пакет. В RED OS работает тот же systemctl и journalctl. "
     "SELinux: getenforce показывает режим; не выключай его ради удобства."),
    ("Docker",
     "docker ps — запущенные контейнеры, docker ps -a — все. docker logs --tail 80 имя — "
     "журнал контейнера. docker stats --no-stream — память и процессор. docker restart "
     "имя — перезапуск (спроси). docker compose ps в папке проекта — состояние стека. "
     "docker system df — сколько места занимают образы."),
    ("Журналы",
     "journalctl -b -p warning --no-pager | tail -50 — предупреждения с последней "
     "загрузки. tail -n 100 /var/log/syslog или /var/log/messages — классический журнал. "
     "dmesg -T | tail -30 — ядро: ошибки дисков, нехватка памяти (Out of memory), USB. "
     "Ищи слова error, failed, denied, timeout, killed."),
    ("Код на сервере",
     "Чтобы написать файл на сервере, агент кодирует текст в base64 и выполняет "
     "echo ДАННЫЕ | base64 -d > путь — так кавычки и спецсимволы не ломают команду. "
     "Потом проверяет синтаксис: python3 -m py_compile путь, bash -n путь. Запуск — "
     "timeout 60 python3 путь. Код пишется в отдельную папку ~/neuro12-agent, а не "
     "поверх системных файлов."),
    ("Python-окружение на сервере",
     "python3 --version — версия. python3 -m venv ~/neuro12-agent/venv — отдельное "
     "окружение, чтобы не ломать системный Python. ~/neuro12-agent/venv/bin/pip install "
     "пакет — установка в окружение. pip list --outdated — что устарело."),
    ("Плата Arduino UNO Q как сервер",
     "UNO Q — это Linux (Debian) на процессоре Qualcomm плюс микроконтроллер STM32. "
     "Пользователь по умолчанию arduino. Приложения App Lab лежат в ~/ArduinoApps, "
     "запускаются в Docker: arduino-app-cli app list — список, arduino-app-cli app logs "
     "имя — журнал. Скетч микроконтроллера и Python-часть общаются через Bridge (RPC). "
     "Микрофон USB: arecord -l, динамик: aplay -l. Камера ESP32-CAM по проводу идёт в "
     "Serial1 (D0/RX) на 460800 бод."),
    ("Плата: проверка железа",
     "arecord -l — видит ли плата микрофон. arecord -d 3 -f S16_LE -r 16000 /tmp/t.wav — "
     "записать 3 секунды. vcgencmd нет, температура: cat /sys/class/thermal/thermal_zone*/temp "
     "(в тысячных градуса). lsusb — USB-устройства. ip -br a — адрес платы в сети."),
    ("Включение серверов по сети",
     "Wake-on-LAN будит выключенный компьютер волшебным пакетом: 6 байт FF и 16 раз "
     "MAC-адрес сетевой карты, широковещательно на UDP-порт 9. В BIOS должно быть "
     "включено Wake on LAN, а сам компьютер подключён кабелем. После пакета сервер "
     "поднимается 30–90 секунд; агент проверяет порт 22, прежде чем выполнять команды."),
    ("Безопасность агента",
     "Опасно и запрещено без явного согласия владельца: rm -rf с корнем или домашней "
     "папкой, mkfs, dd в устройство, shutdown, reboot, poweroff, chmod -R 777, изменение "
     "/etc/passwd и /etc/sudoers, остановка sshd (агент потеряет доступ). Изменяющие "
     "команды (установка, перезапуск, запись файлов, kill) агент показывает владельцу и "
     "ждёт кнопки «Разрешить». Читающие команды выполняет сразу."),
    ("Как читать код выхода",
     "Код 0 — успех. 1 — общая ошибка. 2 — неверное использование команды. 126 — нет "
     "прав на запуск. 127 — команда не найдена (пакет не установлен). 124 — сработал "
     "timeout. 137 — процесс убит (часто нехватка памяти). 255 — ошибка SSH: нет "
     "соединения, неверный ключ или хост недоступен."),
    ("SSH-ключ облака",
     "Облако ходит на серверы по ключу без пароля. Публичный ключ облака добавляется в "
     "файл ~/.ssh/authorized_keys пользователя на сервере (одна строка ssh-ed25519 …). "
     "Права: chmod 700 ~/.ssh и chmod 600 ~/.ssh/authorized_keys. Ошибка Permission "
     "denied (publickey) — ключ не добавлен или права неверные."),
    ("Удалённый рабочий стол",
     "Рабочим столом компьютера, где работает облако, управляет окно «Рабочий стол»: "
     "снимок экрана, клик, ввод текста, горячие клавиши, список окон. На Linux-сервере "
     "без экрана рабочего стола нет — там всё делается командами. На Windows-сервере "
     "можно смотреть процессы через tasklist и службы через sc query."),
    ("Windows-сервер по SSH",
     "Если на Windows включён OpenSSH Server, команды идут в cmd. Полезно: systeminfo, "
     "tasklist, sc query состояние служб, wmic logicaldisk get size,freespace, "
     "powershell -NoProfile -Command \"Get-Service | Where Status -eq Running\"."),
    ("Порядок работы агента",
     "1) list — какие серверы есть. 2) check — доступен ли нужный. 3) Несколько "
     "читающих команд, чтобы понять ситуацию. 4) Если нужно изменение — одно действие, "
     "владелец разрешает, затем проверка результата. 5) finish — короткий итог: что "
     "было, что сделано, что осталось. Не больше 12 шагов на задачу."),
]

EXAMPLES = [
    ("покажи состояние сервера web1",
     [('{"tool": "run", "server": "web1", "command": "uptime; free -h; df -h /"}',
       "load average: 0.21 … Mem: 3.8Gi total, 1.2Gi used … /dev/sda1 40G 31G 7G 82% /"),
      ('{"tool": "run", "server": "web1", "command": "systemctl --failed --no-pager"}',
       "0 loaded units listed.")],
     "web1 работает, нагрузка низкая, память в норме, диск занят на 82 процента — "
     "стоит почистить журналы. Упавших служб нет."),
    ("почему не открывается сайт на web1",
     [('{"tool": "run", "server": "web1", "command": "systemctl status nginx --no-pager | head -15"}',
       "Active: failed (Result: exit-code)"),
      ('{"tool": "run", "server": "web1", "command": "journalctl -u nginx -n 30 --no-pager"}',
       "nginx: [emerg] unknown directive \"proxy_passs\" in /etc/nginx/sites-enabled/app:12"),
      ('{"tool": "run", "server": "web1", "command": "nginx -t"}',
       "nginx: configuration file test failed")],
     "nginx не запускается из-за опечатки в /etc/nginx/sites-enabled/app, строка 12: "
     "proxy_passs вместо proxy_pass. Исправь строку и выполни systemctl restart nginx — "
     "агент может сделать это после разрешения."),
    ("установи htop на плату",
     [('{"tool": "run", "server": "uno-q", "command": "which htop || echo нет"}', "нет"),
      ('{"tool": "run", "server": "uno-q", "command": "sudo -n apt-get install -y htop"}',
       "ждёт разрешения владельца → разрешено → Setting up htop …")],
     "htop установлен на плату uno-q."),
    ("напиши на сервере скрипт, который раз в минуту пишет свободную память в файл",
     [('{"tool": "write", "server": "web1", "path": "~/neuro12-agent/memlog.sh", '
       '"content": "#!/bin/sh\\nfree -m | awk \'/Mem:/{print strftime(), $7}\' >> ~/memlog.txt\\n"}',
       "записано, bash -n — ок"),
      ('{"tool": "run", "server": "web1", "command": "(crontab -l 2>/dev/null; echo \'* * * * * sh ~/neuro12-agent/memlog.sh\') | crontab -"}',
       "ждёт разрешения → разрешено → готово")],
     "Скрипт ~/neuro12-agent/memlog.sh записан и добавлен в cron раз в минуту; "
     "данные копятся в ~/memlog.txt."),
    ("включи сервер backup",
     [('{"tool": "wake", "server": "backup"}', "пакет Wake-on-LAN отправлен на 3c:7c:3f:…"),
      ('{"tool": "check", "server": "backup"}', "порт 22 открыт через 48 с")],
     "backup включён и доступен по SSH."),
]


def _sig(t):
    return hashlib.md5(t.encode("utf-8", "replace")).hexdigest()[:16]


TOOL_LESSONS = [
    ("Общий обзор сервера", "Задача владельца: проверь сервер, проверь машину, проверь состояние, проверь как дела, покажи состояние, покажи состояние сервера, как дела на сервере, состояние сервера, всё ли в порядке, "
     "обзор, осмотри машину, что с сервером, диагностика. Сначала читающие команды, потом по найденному (диск, "
     "упавшая служба, нагрузка) — следующий урок.\nШаг: {\"tool\": \"check\"}\nКоманды (Linux):\n```bash\n"
     "uptime; nproc; free -h; df -h / 2>/dev/null | tail -1\n"
     "systemctl --failed --no-pager 2>/dev/null | head -15 || echo 'systemd нет (контейнер?)'\n```\n"
     "Команды (Windows PowerShell):\n```powershell\n"
     "Get-CimInstance Win32_OperatingSystem | Select-Object Caption,Version,CSName | Format-List\n"
     "Get-PSDrive -PSProvider FileSystem | Select-Object Name,Used,Free\n"
     "Get-Service | Where-Object {$_.Status -eq 'Stopped' -and $_.StartType -eq 'Automatic'} | Select-Object -First 15 Name\n```"),
    ("Перезапустить облако", "Задача владельца: перезапусти облако, рестарт облака, перезагрузи себя, перезапусти neuro12. "
     "Это не служба сервера, а сам процесс облака: данные сохраняются, он поднимется на том же порту. Инструмент агента.\n"
     "Шаг: {\"tool\": \"restart_cloud\"}"),
    ("Снимок рабочего стола", "Задача владельца: покажи экран, сделай скриншот, что на рабочем столе ПК, снимок экрана.\n"
     "Шаг: {\"tool\": \"shot\"}"),
    ("Камеры плат: кадры и битые кадры", "Задача владельца: что видят камеры, почему битые кадры, зрение плат, камера не работает.\n"
     "Шаг: {\"tool\": \"vision\"}"),
    ("Окна рабочего стола", "Задача владельца: какие окна открыты на компьютере, список окон, что запущено на рабочем столе.\n"
     "Шаг: {\"tool\": \"desk\", \"act\": \"windows\"}"),
    ("Список целей", "Задача владельца: какие серверы есть, список целей, какими машинами управляет агент.\n"
     "Шаг: {\"tool\": \"list\"}"),
    ("Доступность цели по порту SSH", "Задача владельца: сервер жив, доступен ли сервер, отвечает ли плата, проверь связь с целью.\n"
     "Шаг: {\"tool\": \"check\"}"),
    ("Включить сервер по сети", "Задача владельца: включи сервер, разбуди компьютер, Wake-on-LAN, поднять выключенный сервер.\n"
     "Шаг: {\"tool\": \"wake\"}"),
    ("Написать скрипт на цели", "Задача владельца: напиши скрипт, создай программу, сделай код, который делает что-то на сервере. "
     "Мозг пишет код сам, кладёт на цель, проверяет и запускает.\n"
     "Шаг: {\"tool\": \"code\"}"),
]


def tool_lessons():
    return list(TOOL_LESSONS)


def win_lessons():
    try:
        import admincmds
        return admincmds.lessons(win=True)
    except Exception:
        return []


def catalog_lessons():
    """v3.216: ~55 уроков с командами из admincmds (команды в ```)."""
    try:
        import admincmds
        return admincmds.lessons()
    except Exception:
        return []


def lesson_texts():
    out = []
    for title, body in win_lessons() + tool_lessons():
        t = "Урок администрирования: %s.\n%s" % (title, body)
        out.append(("adm-w-" + _sig(t), t))
    for title, body in catalog_lessons():
        t = "Урок администрирования: %s.\n%s" % (title, body)
        out.append(("adm-c-" + _sig(t), t))
    for title, body in LESSONS:
        t = "Урок администрирования: %s.\n%s" % (title, body)
        out.append(("adm-l-" + _sig(t), t))
    for task, steps, fin in EXAMPLES:
        lines = ["Задача владельца: %s" % task]
        for act, res in steps:
            lines.append("Шаг: %s" % act)
            lines.append("Вывод: %s" % res)
        lines.append("Итог: %s" % fin)
        t = "\n".join(lines)
        out.append(("adm-e-" + _sig(t), t))
    return out


def learned_texts(n=400):
    out = []
    for it in (store.load_json(_LEARN, []) or [])[-n:]:
        t = str(it.get("text") or "").strip()
        if len(t) >= 40:
            out.append(("adm-x-" + _sig(t), t[:3000]))
    return out


def instruction_texts():
    """v3.219: библиотека инструкций (agent_instructions.py) — уроки и примеры для LLM."""
    try:
        import agent_instructions as instructions
        return instructions.corpus()
    except Exception:
        return []


def code_texts():
    """v3.220: уроки программирования (проверенные шаблоны) — в поток обучения LLM."""
    try:
        import codelessons
        return codelessons.corpus()
    except Exception:
        return []


def applab_texts():
    """v3.225: инструкции App Lab (функции и кирпичики) + код с задачей «# task: App Lab: …».
    v3.226: + то, что Star Gate написал/проверил/вызвал (LLM наблюдает)."""
    try:
        import applab_guide
        import applab_brain
        import brickplan
        return [t for _s, t in applab_guide.corpus()] + applab_brain.learned_texts() + applab_brain.fix_texts() + \
            [t for _s, t in brickplan.corpus()]
    except Exception:
        return []


def contour_texts():
    """v3.221: инструкции Контура + то, что LLM увидела в работе мозга."""
    try:
        import project_brain
        return [t for _s, t in project_brain.corpus()] + project_brain.learned_texts()
    except Exception:
        return []


def texts():
    return lesson_texts() + instruction_texts() + code_texts() + applab_texts() + contour_texts() + learned_texts()


def learn_from_job(job):
    """Успешное задание агента -> материал потока (задача, шаги, итог)."""
    try:
        steps = [s for s in (job.get("steps") or []) if s.get("tool") in ("run", "write", "wake", "check")]
        if not steps or job.get("status") != "done":
            return False
        lines = ["Задача владельца: %s" % str(job.get("task") or "")[:300]]
        for s in steps[:10]:
            lines.append("Шаг: %s" % str(s.get("act") or "")[:300])
            res = s.get("result") or {}
            lines.append("Вывод: %s" % str(res.get("out") or res.get("error") or "")[:400].replace("\n", " ⏎ "))
        lines.append("Итог: %s" % str(job.get("result") or "")[:400])
        lst = store.load_json(_LEARN, []) or []
        lst.append({"ts": job.get("finished"), "text": "\n".join(lines)})
        store.save_json(_LEARN, lst[-600:])
        return True
    except Exception:
        return False
