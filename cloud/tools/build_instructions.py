# -*- coding: utf-8 -*-
"""Генератор библиотеки инструкций: python tools/build_instructions.py
Пишет server/instructions/{board,cloud,linux,windows}.json и server/platform_api.json."""
import ast
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "..", "server", "instructions")
SRV = os.path.join(HERE, "..", "server")
APP = "~/ArduinoApps/neuro_12"


def R(cmd, note="", expect=None, stop=False):
    s = {"tool": "run", "command": cmd}
    if note:
        s["note"] = note
    if expect:
        s["expect"] = expect
    if stop:
        s["stop_on_fail"] = True
    return s


def N(text):
    return {"tool": "note", "text": text}


def W(sec):
    return {"tool": "wait", "seconds": sec}


def C(op, **kw):
    return dict({"tool": "cloud", "op": op}, **kw)


def CHK():
    return {"tool": "check"}


def I(id, title, ask, target, steps, risk="read", verify=None, rollback="", notes="", slots=None):
    d = {"id": id, "title": title, "ask": ask, "target": target, "risk": risk, "steps": steps}
    if slots:
        d["slots"] = slots
    if verify:
        d["verify"] = verify
    if rollback:
        d["rollback"] = rollback
    if notes:
        d["notes"] = notes
    d["verified"] = False
    return d


SVC = {"service": r"(?:служб\w*|сервис\w*|service|юнит\w*)\s+([\w.@-]+)"}
PKG = {"package": r"(?:пакет\w*|package|установи|поставь|install|удали|remove)\s+([\w.+-]+)"}
CTR = {"container": r"(?:контейнер\w*|container)\s+([\w.-]+)"}
PORT = {"port": r"(?:порт\w*|port)\s+(\d{1,5})"}
PID = {"pid": r"(?:pid|процесс\w*)\s+(\d{1,7})"}
PROC = {"proc": r"(?:процесс\w*|process)\s+([\w.-]+)"}
HOST = {"host": r"(?:на|до|к|host|хост\w*)\s+([\w.-]+\.[\w.-]+|\d+\.\d+\.\d+\.\d+)"}
DIR = {"dir": r"(?:папк\w*|каталог\w*|директори\w*|dir)\s+((?:/|~/)[\w./-]+)"}
USER = {"user": r"(?:пользовател\w*|user|юзер\w*)\s+([a-z_][\w-]{0,30})"}

# ------------------------------------------------------------------ ПЛАТА
BOARD = [
    I("bd.status", "Состояние платы Arduino UNO Q",
      ["состояние платы", "как плата", "проверь плату", "плата жива", "обзор платы"], "board",
      [CHK(), R("uptime; free -h; df -h / | tail -1"), R("cat /sys/class/thermal/thermal_zone0/temp 2>/dev/null || echo 'датчика нет'"),
       R("arduino-app-cli app list 2>&1 | head -20")],
      verify=["плата отвечает, видно нагрузку, память, диск и список приложений"],
      notes="Температура в тысячных градуса (65000 = 65 °C). Выше 80 °C — охлаждение."),
    I("bd.app.status", "Статус приложения neuro_12 на плате",
      ["статус приложения платы", "запущено ли приложение", "работает ли neuro_12", "приложение мозга работает"], "board",
      [R("arduino-app-cli app list 2>&1 | head -20"), R("systemctl is-active neuro12 2>&1; systemctl is-enabled neuro12 2>&1"),
       R("ps aux | grep -iE 'neuro|main.py' | grep -v grep | head -5")],
      verify=["видно, запущено ли приложение, включён ли автозапуск службы neuro12"],
      notes="Службы neuro12 может не быть, если автозапуск не ставили: тогда после перезагрузки приложение стартует только вручную."),
    I("bd.app.logs", "Логи приложения neuro_12",
      ["логи приложения платы", "покажи лог платы", "что пишет плата", "журнал приложения мозга", "ошибки приложения платы"], "board",
      [R("arduino-app-cli app logs %s 2>&1 | tail -80" % APP)],
      verify=["последние 80 строк журнала приложения"]),
    I("bd.app.restart", "Перезапустить приложение neuro_12 на плате",
      ["перезапусти приложение на плате", "рестарт приложения платы", "перезапусти neuro_12", "перезапусти мозг на плате", "перезапусти скетч и python"], "board",
      [R("arduino-app-cli app restart %s 2>&1 | tail -5" % APP), W(25),
       R("arduino-app-cli app list 2>&1 | head -10", expect="neuro_12"),
       R("arduino-app-cli app logs %s 2>&1 | tail -30" % APP)],
      risk="change", verify=["приложение в списке, в журнале нет ошибок запуска"],
      rollback="если не поднялось: arduino-app-cli app start %s, затем смотри логи" % APP,
      notes="Перезапуск грузит и скетч, и Python. Если мост пишет «ждёт скетч» — инструкция bd.bridge.waiting."),
    I("bd.app.stop", "Остановить приложение neuro_12 на плате",
      ["останови приложение на плате", "выключи приложение платы", "стоп neuro_12"], "board",
      [R("arduino-app-cli app stop %s 2>&1 | tail -5" % APP), R("arduino-app-cli app list 2>&1 | head -10")],
      risk="change", verify=["приложения нет среди запущенных"]),
    I("bd.app.start", "Запустить приложение neuro_12 на плате",
      ["запусти приложение на плате", "включи приложение платы", "старт neuro_12", "запусти мозг на плате"], "board",
      [R("arduino-app-cli app start %s 2>&1 | tail -5" % APP), W(20), R("arduino-app-cli app list 2>&1 | head -10", expect="neuro_12")],
      risk="change", verify=["приложение запущено"],
      notes="`app start` сам не переживает перезагрузку — для автозапуска bd.autostart.install."),
    I("bd.reboot", "Перезагрузить плату Arduino UNO Q",
      ["перезагрузи плату", "перезагрузка платы", "рестарт платы", "reboot платы", "перезапусти плату", "перезагрузи uno q"], "board",
      [R("arduino-app-cli app stop %s 2>&1 | tail -3" % APP, note="штатно остановить приложение перед перезагрузкой"),
       R("sudo -n reboot", note="нужен sudo без пароля; если пишет «password is required» — остановись и скажи владельцу"),
       W(90), CHK(), W(30), CHK(),
       R("uptime -p; arduino-app-cli app list 2>&1 | head -10", note="после загрузки смотрим, поднялось ли приложение"),
       N("Если приложения нет в списке: выполни инструкцию «Запустить приложение на плате» (arduino-app-cli app start). "
         "Чтобы приложение стартовало само после перезагрузки — «Установить автозапуск neuro_12» (systemd-служба neuro12).")],
      risk="danger", verify=["плата снова отвечает по SSH, uptime маленький, приложение запущено"],
      rollback="если плата не вернулась за 3–4 минуты: отключи питание на 10 секунд и включи снова",
      notes="Связь по SSH оборвётся сразу после reboot — это нормально. Агент ждёт и проверяет порт 22."),
    I("bd.power.cycle", "Плата зависла и не отвечает",
      ["плата не отвечает", "плата зависла", "плата не на связи", "не могу подключиться к плате", "плата пропала из сети"], "board",
      [CHK(), N("1) Проверь питание (кабель, блок питания не слабее 5 В / 3 А). 2) Посмотри индикаторы на плате. "
                "3) Проверь Wi-Fi/кабель и IP платы в роутере. 4) Если пинга нет: отключи питание на 10 секунд и включи. "
                "5) Подожди 2–3 минуты (первая загрузка медленнее) и повтори проверку.")],
      verify=["понятно, что делать владельцу: питание, сеть, перезапуск по питанию"],
      notes="Дистанционно плату без сети не перезагрузить — только питанием."),
    I("bd.autostart.status", "Автозапуск neuro_12 при загрузке платы",
      ["автозапуск платы", "стартует ли приложение само", "включён ли автозапуск", "служба neuro12"], "board",
      [R("systemctl status neuro12 --no-pager 2>&1 | head -15"), R("systemctl is-enabled neuro12 2>&1"),
       R("loginctl show-user $(id -un) 2>/dev/null | grep -i linger")],
      verify=["видно, есть ли служба neuro12 и включена ли она"]),
    I("bd.autostart.install", "Установить автозапуск neuro_12 (служба neuro12 + сторож)",
      ["установи автозапуск на плате", "сделай автозапуск приложения", "чтобы после перезагрузки приложение стартовало", "установи сторож neuro12"], "board",
      [R("ls %s/deploy_cli/install_autostart.sh 2>&1" % APP, stop=True, note="скрипт должен лежать в приложении"),
       R("sudo -n bash %s/deploy_cli/install_autostart.sh watchdog 2>&1 | tail -15" % APP, note="нужен sudo без пароля"),
       R("systemctl is-enabled neuro12 2>&1; systemctl is-active neuro12 2>&1")],
      risk="change", verify=["служба neuro12 включена"],
      rollback="sudo systemctl disable --now neuro12",
      notes="Скрипт и поведение описаны в README платы (deploy_cli). Нужен интернет не обязательно."),
    I("bd.service.restart", "Перезапустить службу neuro12",
      ["перезапусти службу neuro12", "перезапусти сторож платы", "restart neuro12"], "board",
      [R("sudo -n systemctl restart neuro12 2>&1", note="нужен sudo без пароля"), W(20),
       R("systemctl is-active neuro12 2>&1", expect="^active"), R("journalctl -u neuro12 -n 20 --no-pager 2>&1 | tail -20")],
      risk="change", verify=["служба active"]),
    I("bd.bridge.waiting", "Мост ждёт скетч",
      ["мост ждёт скетч", "bridge ждёт скетч", "скетч не грузится", "плата не видит скетч", "ждёт скетч"], "board",
      [R("arduino-app-cli app logs %s 2>&1 | grep -iE 'sketch|скетч|llext|undefined|bridge|мост|rpc' | tail -30" % APP),
       R("arduino-app-cli app restart %s 2>&1 | tail -5" % APP, note="часто достаточно перезапуска"),
       W(30), R("arduino-app-cli app logs %s 2>&1 | grep -iE 'sketch|скетч|мост|bridge' | tail -15" % APP),
       N("Если «ждёт скетч» повторяется после обновления скетча: скетч не загрузился в MCU (в App Lab смотри лог загрузки: "
         "undefined reference / llext). В sketch.ino поставь NEURO_PCM_MODE 0 (программный PCM без драйверов Zephyr), "
         "пересобери и пришли лог загрузки — по нему найдётся точный символ.")],
      risk="change", verify=["в журнале нет «ждёт скетч»"],
      notes="Известная причина: драйверы Zephyr (PWM/counter) в скетче-llext могут тихо не загрузиться."),
    I("bd.net", "Сеть платы",
      ["сеть платы", "ip платы", "wifi платы", "плата не подключается к wi-fi", "интернет на плате"], "board",
      [R("ip -br a 2>/dev/null || hostname -I"), R("nmcli dev status 2>&1 | head"), R("nmcli -f IN-USE,SSID,SIGNAL dev wifi 2>&1 | head -8"),
       R("ip route | head -3"), R("ping -c 3 -W 2 1.1.1.1 2>&1 | tail -3")],
      verify=["видно IP, Wi-Fi, шлюз и выход в интернет"],
      notes="Первый старт после перезагрузки требует Wi-Fi."),
    I("bd.ping.cloud", "Достаёт ли плата до облака",
      ["плата видит облако", "связь платы с облаком", "ping облака с платы", "облако недоступно с платы"], "board",
      [R("ping -c 3 -W 2 {host} 2>&1 | tail -4", note="адрес облака — в задаче"),
       R("arduino-app-cli app logs %s 2>&1 | grep -iE 'облак|cloud|link|https|ssl' | tail -15" % APP)],
      slots=HOST, verify=["пинг проходит; в логах нет ошибок связи с облаком"],
      notes="Если облако на HTTPS с самоподписанным сертификатом, плата закрепляет отпечаток SHA-256 (см. /api/tls)."),
    I("bd.resources", "Память и нагрузка на плате",
      ["память платы", "нагрузка на плате", "плата тормозит", "что ест память на плате", "плата перегружена"], "board",
      [R("free -h; uptime"), R("ps aux --sort=-%mem | head -8"), R("ps aux --sort=-%cpu | head -8"), R("dmesg 2>/dev/null | grep -i 'out of memory' | tail -3")],
      verify=["видно, кто занимает память и процессор"],
      notes="Если память на пределе — перезапуск приложения (bd.app.restart) освобождает её."),
    I("bd.disk", "Диск платы",
      ["место на плате", "диск платы", "плата заполнена", "не хватает места на плате"], "board",
      [R("df -h / /home 2>/dev/null"), R("du -xh --max-depth=1 ~ 2>/dev/null | sort -h | tail -10"),
       R("docker system df 2>/dev/null || echo 'docker не отвечает'"), R("journalctl --disk-usage 2>&1")],
      verify=["видно, что занимает место"]),
    I("bd.clean", "Освободить место на плате",
      ["освободи место на плате", "почисти плату", "очисти журналы платы"], "board",
      [R("journalctl --disk-usage 2>&1"), R("sudo -n journalctl --vacuum-size=100M 2>&1 | tail -3", note="журнал до 100 МБ"),
       R("sudo -n apt-get clean 2>&1 | tail -2"), R("df -h / | tail -1")],
      risk="change", verify=["места стало больше"],
      notes="docker prune здесь не делаем: App Lab может заново скачивать образы."),
    I("bd.temp", "Температура платы",
      ["температура платы", "плата греется", "перегрев платы"], "board",
      [R("cat /sys/class/thermal/thermal_zone*/temp 2>/dev/null"), R("uptime")],
      verify=["значения в тысячных долях градуса; выше 80000 — перегрев"]),
    I("bd.time", "Время на плате",
      ["время на плате", "дата на плате", "часы платы сбились"], "board",
      [R("date; timedatectl 2>&1 | head -8")], verify=["System clock synchronized: yes"],
      notes="Сбитое время ломает HTTPS-сертификаты: плата не примет облако."),
    I("bd.usb", "USB, камера, микрофон, последовательные порты платы",
      ["камера на плате", "usb платы", "микрофон платы", "звук на плате", "порты платы", "ttyACM"], "board",
      [R("lsusb 2>&1"), R("ls -l /dev/video* /dev/ttyACM* /dev/ttyUSB* 2>&1"), R("arecord -l 2>&1 | head -8; aplay -l 2>&1 | head -8"),
       R("dmesg 2>/dev/null | grep -iE 'uvc|usb|video' | tail -10")],
      verify=["видно устройства USB/звук/камеры"]),
    I("bd.update.system", "Обновить систему платы",
      ["обнови систему платы", "обновления платы", "apt upgrade на плате"], "board",
      [R("sudo -n apt-get update 2>&1 | tail -3", note="нужны интернет и sudo без пароля"),
       R("apt list --upgradable 2>/dev/null | head -20"),
       N("Системные пакеты платы безопаснее обновлять через App Lab. Если решил обновлять здесь — подтверди отдельно: "
         "sudo apt-get -y upgrade (может перезагрузить службы и потребовать перезагрузку платы).")],
      risk="change", verify=["список доступных обновлений"]),
    I("bd.backup.app", "Резервная копия приложения neuro_12",
      ["бэкап приложения платы", "резервная копия приложения", "сохрани приложение платы"], "board",
      [R("ls -la %s 2>&1 | head -20" % APP), R("tar czf ~/n12_backup_$(date +%F_%H%M).tgz -C ~/ArduinoApps neuro_12 2>&1 && ls -la ~/n12_backup_*.tgz | tail -3")],
      risk="change", verify=["архив ~/n12_backup_*.tgz создан"],
      notes="Память мозга платы и облака сохраняются отдельно (облако — «сейф памяти плат»)."),
    I("bd.update.app", "Обновить приложение neuro_12 на плате (из zip)",
      ["обнови приложение на плате", "установи новую версию приложения платы", "залей новую версию neuro_12"], "board",
      [R("ls -la %s 2>&1 | head -10" % APP), R("tar czf ~/n12_backup_$(date +%F_%H%M).tgz -C ~/ArduinoApps neuro_12 2>&1 | tail -2", note="сначала копия"),
       N("С компьютера: scp -r neuro_12 arduino@<IP платы>:ArduinoApps/ (или через App Lab: «Загрузить приложение»). Архив — из релиза."),
       R("arduino-app-cli app stop %s 2>&1 | tail -3" % APP), N("Файлы приложения заменены новой версией (app.yaml, python/, sketch/)."),
       R("arduino-app-cli app start %s 2>&1 | tail -5" % APP), W(25),
       R("arduino-app-cli app logs %s 2>&1 | tail -30" % APP)],
      risk="change", verify=["в логе новая версия, нет ошибок"],
      rollback="распаковать ~/n12_backup_*.tgz в ~/ArduinoApps и запустить приложение",
      notes="Агент сам не может копировать файлы с вашего компьютера — этот шаг вручную."),
    I("bd.brain.link", "Связь платы с облаком в логах",
      ["почему плата не шлёт мысли", "плата не связана с облаком", "связь мозгов", "плата не видна в облаке"], "board",
      [R("arduino-app-cli app logs %s 2>&1 | grep -iE 'облак|link|связь|cloud|https|ssl|401|403|timeout' | tail -25" % APP),
       R("ping -c 2 -W 2 1.1.1.1 2>&1 | tail -2"), C("boards")],
      verify=["понятно, где обрывается связь: сеть платы, адрес/токен облака или сертификат"],
      notes="Проверь адрес и токен облака в панели платы. 127.0.0.1 в качестве адреса облака на плате не работает."),
    I("bd.hostname", "Имя и адрес платы",
      ["как называется плата", "имя платы", "hostname платы"], "board",
      [R("hostnamectl 2>&1 | head -6; hostname -I")], verify=["видно имя и IP"]),
]

# ------------------------------------------------------------------ ОБЛАКО
CLOUD = [
    I("cl.status", "Состояние облака", ["состояние облака", "как облако", "проверь облако", "облако живо", "статус облака"], "cloud",
      [C("status"), C("boards"), C("thoughts_stats")], verify=["версия, порт, HTTPS, платы на связи, банк мыслей"]),
    I("cl.logs", "Журнал облака", ["журнал облака", "логи облака", "что пишет облако", "ошибки облака"], "cloud",
      [C("logs")], verify=["последние 40 строк журнала"]),
    I("cl.restart", "Перезапустить облако", ["перезапусти облако", "рестарт облака", "перезагрузи облако", "перезапусти себя", "перезагрузи себя"], "cloud",
      [C("save_memory"), {"tool": "restart_cloud"}], risk="change",
      verify=["облако поднимется на том же порту через 10–20 секунд"],
      rollback="если не поднялось: data/restart.log, затем запустить start.sh / neuro12-cloud.exe вручную",
      notes="Перед перезапуском память сохраняется. После перезапуска агент завершает задание."),
    I("cl.save", "Сохранить память облака", ["сохрани память облака", "сохрани мозг", "сделай бэкап памяти облака"], "cloud",
      [C("save_memory")], risk="change", verify=["перечислено, что сохранено"]),
    I("cl.kb.reindex", "Переиндексировать базу знаний", ["переиндексируй базу знаний", "пересобери индекс знаний", "база знаний не находит"], "cloud",
      [C("kb_reindex"), W(15), C("status")], risk="change", verify=["индекс ready"]),
    I("cl.llm.status", "Состояние своей LLM", ["состояние llm", "обучена ли llm", "как моя llm", "статус своей llm"], "cloud",
      [C("llm_status")], verify=["шаги обучения, готовность"]),
    I("cl.llm.train", "Дообучить свою LLM", ["обучи llm", "дообучи llm", "запусти обучение llm", "переобучи свою llm"], "cloud",
      [C("llm_train"), W(10), C("llm_status")], risk="change", verify=["обучение идёт"],
      notes="Обучение грузит процессор; идёт на вдохе мозга. Новые уроки администрирования подхватываются потоком «admin»."),
    I("cl.maxbot.restart", "Перезагрузить бота МАКС", ["перезагрузи бота макс", "бот макс завис", "перезапусти бота"], "cloud",
      [C("maxbot_restart")], risk="change", verify=["бот перезагружен без перезапуска облака"]),
    I("cl.tls.status", "HTTPS облака", ["https облака", "сертификат облака", "ssl облака", "статус сертификата"], "cloud",
      [C("tls_status")], verify=["включён ли HTTPS, отпечаток SHA-256"],
      notes="Установка/обновление сертификата: Linux — cloud/launcher/install-ssl-linux.sh; плата закрепляет отпечаток."),
    I("cl.update.status", "Версия облака и резервные копии", ["версия облака", "какая версия облака", "резервные копии облака", "список копий"], "cloud",
      [C("update_status")], verify=["версия и список копий для отката"]),
    I("cl.rollback", "Откатить облако к резервной копии", ["откати облако", "верни прошлую версию облака", "откат обновления"], "cloud",
      [C("update_status"), N("Выбери имя копии из списка (например pre-3.218.0-…) и повтори задачу: «откати облако на копию <имя>»."),
       {"tool": "cloud", "op": "rollback", "backup": "{backup}"}],
      risk="danger", slots={"backup": r"(?:копи\w+|backup)\s+(pre-[\w.\-]+)"},
      verify=["облако перезапустится на выбранной версии"], rollback="повторный откат/обновление на нужную версию",
      notes="Данные (data/) не откатываются — меняется только код."),
    I("cl.update.manual", "Обновить облако из zip", ["обнови облако", "установи новую версию облака", "обновление облака"], "cloud",
      [C("update_status"), N("В панели: Настройки → Обновление → загрузить zip облака (или CLI: python server/updater.py apply <zip>). "
                            "Облако само сделает копию, заменит код и перезапустится."), C("save_memory")],
      risk="change", verify=["версия после перезапуска новая"], rollback="инструкция «Откатить облако»"),
    I("cl.thoughts", "Банк мыслей и журнал LLM", ["мысли облака", "банк мыслей", "что делала llm с мыслями", "журнал мыслей"], "cloud",
      [C("thoughts_stats")], verify=["сколько мыслей в банке, что собрала LLM, что оставил мозг"]),
    I("cl.boards", "Какие платы на связи", ["какие платы на связи", "список плат", "платы онлайн", "плата на связи"], "cloud",
      [C("boards")], verify=["список плат с версиями"]),
]

# ------------------------------------------------------------------ LINUX
def L(id, title, ask, steps, risk="read", verify=None, rollback="", notes="", slots=None):
    return I(id, title, ask, "linux", steps, risk, verify, rollback, notes, slots)


LINUX = [
    L("lx.overview", "Общий обзор Linux-сервера", ["проверь сервер", "проверь web1", "проверь хост", "проверь машину", "покажи состояние web1", "как дела на сервере", "обзор сервера", "состояние сервера", "осмотри машину"],
      [CHK(), R("uptime; nproc; free -h; df -h / | tail -1"), R("systemctl --failed --no-pager 2>/dev/null | head -15 || echo 'systemd нет (контейнер?)'")],
      verify=["понятно, жив ли сервер, нагрузка, память, диск, упавшие службы"]),
    L("lx.os", "Версия ОС и ядра", ["версия системы", "какая ос", "версия ядра", "какой дистрибутив"],
      [R("cat /etc/os-release | head -6; uname -a"), R("hostnamectl 2>/dev/null | head -10")]),
    L("lx.uptime", "Время работы и нагрузка", ["аптайм", "сколько работает сервер", "нагрузка сервера", "load average"],
      [R("uptime -p; uptime; nproc")], verify=["нагрузка выше числа ядер = перегрузка"]),
    L("lx.mem", "Память", ["сколько памяти", "память занята", "не хватает оперативки", "swap"],
      [R("free -h"), R("ps aux --sort=-%mem | head -10"), R("dmesg 2>/dev/null | grep -i 'out of memory' | tail -5")]),
    L("lx.cpu", "Процессор и нагрузка по процессам", ["что грузит процессор", "процессор загружен", "сервер тормозит", "лагает", "какой процессор"],
      [R("lscpu | head -15"), R("ps aux --sort=-%cpu | head -12"), R("top -bn1 | head -15")]),
    L("lx.disk", "Место на дисках", ["сколько места", "диск заполнен", "место на диске", "закончилось место"],
      [R("df -h -x tmpfs -x devtmpfs"), R("df -i / | tail -2"), R("du -xh --max-depth=1 / 2>/dev/null | sort -h | tail -12")],
      notes="inode закончились — много мелких файлов (df -i)."),
    L("lx.bigfiles", "Самые большие файлы", ["найди большие файлы", "что занимает место", "самые тяжёлые файлы"],
      [R("find ~ -xdev -type f -size +100M -printf '%s %p\\n' 2>/dev/null | sort -n | tail -10"), R("du -xh --max-depth=2 /var 2>/dev/null | sort -h | tail -12")]),
    L("lx.dirsize", "Размер папки", ["сколько весит папка", "размер каталога", "размер директории"],
      [R("du -sh {dir} 2>&1"), R("du -xh --max-depth=1 {dir} 2>/dev/null | sort -h | tail -12")], slots=DIR),
    L("lx.mounts", "Диски и монтирование", ["какие диски подключены", "смонтированные разделы", "fstab", "lsblk"],
      [R("lsblk -f"), R("findmnt --real 2>/dev/null | head -20"), R("cat /etc/fstab")]),
    L("lx.smart", "Здоровье диска", ["здоровье диска", "smart диска", "диск умирает"],
      [R("sudo -n smartctl -H /dev/sda 2>&1 | head -12"), R("lsblk -d -o NAME,SIZE,MODEL,ROTA")], notes="нужен пакет smartmontools"),
    L("lx.temp", "Температура и датчики", ["температура сервера", "перегрев", "датчики температуры"],
      [R("sensors 2>/dev/null | head -20 || echo 'lm-sensors не установлен'"), R("cat /sys/class/thermal/thermal_zone*/temp 2>/dev/null")]),
    L("lx.hw", "Железо", ["какое железо", "характеристики сервера", "pci устройства"],
      [R("lscpu | head -12"), R("lspci 2>/dev/null | head -15"), R("lsusb 2>/dev/null"), R("lsblk -d -o NAME,SIZE,MODEL")]),
    L("lx.proc.find", "Найти процесс", ["найди процесс", "запущен ли процесс", "pgrep"],
      [R("pgrep -a -f {proc} 2>&1 | head -10 || echo 'не найден'"), R("ps aux | grep -i {proc} | grep -v grep | head -5")], slots=PROC),
    L("lx.proc.kill", "Завершить процесс по PID", ["убей процесс", "заверши процесс", "kill процесс"],
      [R("ps -p {pid} -o pid,etime,cmd 2>&1", note="сначала убедись, что это тот процесс", stop=True), R("kill {pid} 2>&1"), W(3),
       R("ps -p {pid} 2>&1 || echo 'завершён'")], risk="change", slots=PID, rollback="процесс не вернуть; если служба — systemctl start",
      notes="kill шлёт SIGTERM. kill -9 — только если не завершился, и после отдельного вопроса владельцу."),
    L("lx.zombie", "Зависшие и зомби-процессы", ["зомби процессы", "зависшие процессы", "процессы в состоянии D"],
      [R("ps -eo pid,ppid,stat,etime,cmd | awk '$3 ~ /^[ZD]/' | head -20 || true")]),
    L("lx.openfiles", "Открытые файлы и лимиты", ["too many open files", "лимит файлов", "открытые файлы", "ulimit"],
      [R("ulimit -n; cat /proc/sys/fs/file-nr"), R("lsof 2>/dev/null | wc -l")]),
    L("lx.net", "Сетевые интерфейсы и маршруты", ["сетевые интерфейсы", "какой ip", "маршрут по умолчанию", "сетевая карта"],
      [R("ip -br a 2>/dev/null || hostname -I"), R("ip route | head"), R("cat /etc/resolv.conf")]),
    L("lx.ports", "Открытые порты и кто их слушает", ["какие порты открыты", "что слушает порт", "открытые порты", "кто слушает"],
      [R("ss -tlnp 2>/dev/null | head -30"), R("ss -s")]),
    L("lx.port.who", "Кто занял порт", ["кто занял порт", "что слушает порт", "порт занят"],
      [R("ss -tlnp 2>/dev/null | grep ':{port} ' || echo 'порт не слушается'"), R("sudo -n lsof -i :{port} 2>&1 | head -5")], slots=PORT),
    L("lx.ping", "Доступность сети", ["нет интернета", "интернет пропал", "проверь сеть", "ping", "сеть не работает"],
      [R("ping -c 3 -W 2 1.1.1.1 2>&1 | tail -4"), R("ping -c 3 -W 2 ya.ru 2>&1 | tail -4"), R("ip route | head -3")],
      verify=["по IP есть, по имени нет — проблема DNS"]),
    L("lx.ping.host", "Пинг указанного хоста", ["пингани хост", "ping хоста", "доступен ли хост", "проверь доступность адреса"],
      [R("ping -c 4 -W 2 {host} 2>&1 | tail -5"), R("tracepath -n -m 8 {host} 2>&1 | head -12")], slots=HOST),
    L("lx.dns", "DNS", ["dns не работает", "не открывается по имени", "проверь dns", "резолв"],
      [R("getent hosts ya.ru"), R("cat /etc/resolv.conf"), R("resolvectl status 2>/dev/null | head -15")]),
    L("lx.http", "HTTP-проверка сайта", ["проверь сайт", "сайт отвечает", "http код", "сайт не открывается"],
      [R("curl -sSI -m 10 http://localhost/ 2>&1 | head -12")], notes="Для внешнего сайта подставь адрес в задаче: curl -sSI -m 10 https://адрес/"),
    L("lx.firewall", "Файрвол", ["статус файрвола", "правила файрвола", "ufw status", "iptables"],
      [R("sudo -n ufw status verbose 2>&1 | head -25"), R("sudo -n nft list ruleset 2>&1 | head -30"), R("sudo -n iptables -S 2>&1 | head -25")]),
    L("lx.fw.allow", "Открыть порт в ufw", ["открой порт", "разреши порт", "ufw allow"],
      [R("sudo -n ufw status 2>&1 | head -5"), R("sudo -n ufw allow {port}/tcp 2>&1"), R("sudo -n ufw status numbered 2>&1 | head -15")],
      risk="change", slots=PORT, rollback="sudo ufw delete allow {port}/tcp"),
    L("lx.svc.list", "Службы systemd", ["список служб", "какие службы запущены", "службы systemd", "упавшие службы"],
      [R("systemctl list-units --type=service --state=running --no-pager | head -30"), R("systemctl --failed --no-pager")]),
    L("lx.svc.status", "Статус службы", ["статус службы", "работает ли служба", "что со службой", "служба упала"],
      [R("systemctl status {service} --no-pager 2>&1 | head -20"), R("journalctl -u {service} -n 30 --no-pager 2>&1 | tail -30")], slots=SVC),
    L("lx.svc.restart", "Перезапустить службу", ["перезапусти службу", "рестарт службы", "restart service", "перезапусти сервис"],
      [R("systemctl status {service} --no-pager 2>&1 | head -6", note="что было до"), R("sudo -n systemctl restart {service} 2>&1", stop=True),
       W(5), R("systemctl is-active {service} 2>&1", expect="^active"), R("journalctl -u {service} -n 15 --no-pager 2>&1 | tail -15")],
      risk="change", slots=SVC, verify=["служба active"], rollback="смотри журнал службы; конфиг проверь командой самой службы (например nginx -t)"),
    L("lx.svc.start", "Запустить службу", ["запусти службу", "стартуй службу", "start service"],
      [R("sudo -n systemctl start {service} 2>&1", stop=True), R("systemctl is-active {service} 2>&1", expect="^active")], risk="change", slots=SVC),
    L("lx.svc.stop", "Остановить службу", ["останови службу", "выключи службу", "stop service"],
      [R("sudo -n systemctl stop {service} 2>&1"), R("systemctl is-active {service} 2>&1")], risk="change", slots=SVC, rollback="sudo systemctl start {service}"),
    L("lx.svc.enable", "Включить автозапуск службы", ["включи автозапуск службы", "enable service", "службу в автозагрузку"],
      [R("sudo -n systemctl enable {service} 2>&1"), R("systemctl is-enabled {service} 2>&1")], risk="change", slots=SVC),
    L("lx.svc.disable", "Выключить автозапуск службы", ["выключи автозапуск службы", "disable service", "убери службу из автозагрузки"],
      [R("sudo -n systemctl disable {service} 2>&1"), R("systemctl is-enabled {service} 2>&1")], risk="change", slots=SVC, rollback="sudo systemctl enable {service}"),
    L("lx.timers", "Таймеры и cron", ["что запускается по расписанию", "таймеры systemd", "cron задачи", "расписание"],
      [R("systemctl list-timers --all --no-pager | head -20"), R("crontab -l 2>&1"), R("ls -la /etc/cron.d /etc/cron.daily 2>/dev/null | head -25")]),
    L("lx.boot", "Автозапуск и скорость загрузки", ["что в автозапуске", "почему долго грузится", "systemd-analyze"],
      [R("systemctl list-unit-files --state=enabled --no-pager | head -30"), R("systemd-analyze 2>/dev/null; systemd-analyze blame 2>/dev/null | head -10")]),
    L("lx.journal", "Ошибки в журнале", ["ошибки в журнале", "что случилось", "журнал системы", "логи системы", "сбой"],
      [R("journalctl -p err -n 40 --no-pager 2>/dev/null || tail -n 60 /var/log/syslog"), R("journalctl -b -p warning -n 30 --no-pager 2>/dev/null | tail -30")]),
    L("lx.journal.size", "Размер журнала и очистка", ["журнал занимает место", "очисти журнал", "journalctl vacuum"],
      [R("journalctl --disk-usage 2>&1"), R("sudo -n journalctl --vacuum-size=200M 2>&1 | tail -3"), R("journalctl --disk-usage 2>&1")], risk="change"),
    L("lx.auth", "Входы в систему и подбор паролей", ["кто заходил", "кто входил по ssh", "попытки подбора пароля", "last", "неудачные входы"],
      [R("last -n 15 2>/dev/null"), R("lastb -n 10 2>/dev/null | head -12"), R("journalctl -u ssh -u sshd -n 40 --no-pager 2>/dev/null | tail -30"), R("who")],
      verify=["много Failed password — подбор: ключи вместо паролей, fail2ban"]),
    L("lx.users", "Пользователи и права", ["какие пользователи", "кто в sudo", "список пользователей", "учётные записи"],
      [R("id; whoami"), R("cut -d: -f1,3,6 /etc/passwd | awk -F: '$2>=1000'"), R("getent group sudo wheel 2>/dev/null"), R("awk -F: '$3==0{print $1}' /etc/passwd")]),
    L("lx.user.add", "Создать пользователя", ["создай пользователя", "добавь пользователя", "новый юзер"],
      [R("id {user} 2>&1 | head -1 || true"), R("sudo -n adduser --disabled-password --gecos '' {user} 2>&1 | tail -3")], risk="change", slots=USER,
      rollback="sudo deluser --remove-home {user}", notes="Без пароля: вход по SSH-ключу. Пароль и права sudo выдавай отдельно."),
    L("lx.sshkeys", "SSH: ключи и настройки", ["настройки ssh", "authorized_keys", "вход по ключу", "sshd_config"],
      [R("ls -la ~/.ssh 2>&1"), R("grep -E '^(PermitRootLogin|PasswordAuthentication|PubkeyAuthentication|Port)' /etc/ssh/sshd_config 2>&1"), R("sudo -n sshd -T 2>/dev/null | grep -E 'permitrootlogin|passwordauth|port' | head")]),
    L("lx.ssh.harden", "Запретить вход по паролю в SSH", ["отключи вход по паролю", "запрети пароль ssh", "усили ssh"],
      [R("test -s ~/.ssh/authorized_keys && echo 'ключ есть' || echo 'НЕТ КЛЮЧЕЙ'", expect="ключ есть", stop=True, note="без ключа не отключаем"),
       R("sudo -n cp /etc/ssh/sshd_config /etc/ssh/sshd_config.bak 2>&1"),
       R("sudo -n sed -i 's/^#\\?PasswordAuthentication.*/PasswordAuthentication no/' /etc/ssh/sshd_config 2>&1"),
       R("sudo -n sshd -t 2>&1 && echo 'конфиг ок'", expect="конфиг ок", stop=True), R("sudo -n systemctl reload ssh 2>&1 || sudo -n systemctl reload sshd 2>&1")],
      risk="danger", rollback="sudo cp /etc/ssh/sshd_config.bak /etc/ssh/sshd_config && sudo systemctl reload ssh",
      notes="Не закрывай текущую сессию, пока не проверишь вход по ключу из второй."),
    L("lx.cert", "Сертификаты и срок действия", ["срок сертификата", "ssl сертификат", "когда истекает сертификат", "https сертификат"],
      [R("ls -la /etc/letsencrypt/live 2>&1 | head"), R("echo | openssl s_client -connect localhost:443 2>/dev/null | openssl x509 -noout -dates -subject 2>&1 | head -4")]),
    L("lx.time", "Время и NTP", ["время на сервере", "ntp", "часовой пояс", "время сбилось"],
      [R("timedatectl 2>&1 | head -10"), R("date; date -u")]),
    L("lx.tz", "Сменить часовой пояс", ["установи часовой пояс", "смени часовой пояс на москву"],
      [R("sudo -n timedatectl set-timezone Europe/Moscow 2>&1"), R("timedatectl | head -4")], risk="change", rollback="timedatectl set-timezone <прежний>"),
    L("lx.pkg.list", "Установленные пакеты", ["какие пакеты установлены", "список пакетов", "версия пакета"],
      [R("dpkg -l 2>/dev/null | tail -20 || rpm -qa | tail -20")]),
    L("lx.pkg.updates", "Доступные обновления", ["какие есть обновления", "нужны ли обновления", "что можно обновить"],
      [R("apt list --upgradable 2>/dev/null | head -30 || dnf check-update 2>/dev/null | head -30"), R("test -f /var/run/reboot-required && echo 'нужна перезагрузка' || echo 'перезагрузка не нужна'")]),
    L("lx.pkg.update", "Обновить список пакетов", ["обнови список пакетов", "apt update"],
      [R("sudo -n apt-get update 2>&1 | tail -5 || sudo -n dnf makecache 2>&1 | tail -5")], risk="change"),
    L("lx.pkg.upgrade", "Обновить систему", ["обнови систему", "установи обновления", "apt upgrade"],
      [R("sudo -n apt-get update 2>&1 | tail -3"), R("sudo -n apt-get -y upgrade 2>&1 | tail -15"), R("test -f /var/run/reboot-required && echo 'нужна перезагрузка'")],
      risk="change", rollback="apt: sudo apt-get install пакет=старая_версия", notes="Перезагрузку делай отдельной инструкцией."),
    L("lx.pkg.install", "Установить пакет", ["установи пакет", "поставь программу", "install package"],
      [R("command -v {package} || echo 'не установлен'"),
       R("if command -v apt-get >/dev/null; then sudo -n apt-get install -y {package}; elif command -v dnf >/dev/null; then sudo -n dnf install -y {package}; else sudo -n yum install -y {package}; fi 2>&1 | tail -8"),
       R("command -v {package} || dpkg -s {package} 2>&1 | head -3")], risk="change", slots=PKG, rollback="sudo apt-get remove {package}"),
    L("lx.pkg.remove", "Удалить пакет", ["удали пакет", "удали программу", "remove package"],
      [R("dpkg -s {package} 2>&1 | head -3"), R("sudo -n apt-get remove -y {package} 2>&1 | tail -8")], risk="change", slots=PKG,
      rollback="sudo apt-get install {package}"),
    L("lx.pip", "Python и pip на сервере", ["версия python", "какие python пакеты", "pip list", "виртуальное окружение"],
      [R("python3 --version; pip3 --version"), R("pip3 list 2>/dev/null | head -25")]),
    L("lx.venv", "Создать виртуальное окружение Python", ["создай venv", "создай виртуальное окружение"],
      [R("python3 -m venv ~/neuro12-agent/venv 2>&1 && ~/neuro12-agent/venv/bin/python --version")], risk="change"),
    L("lx.git", "Состояние git-репозитория", ["состояние git", "git status", "что изменилось в репозитории"],
      [R("git status -sb 2>&1 | head -15"), R("git log --oneline -n 10 2>&1"), R("git remote -v 2>&1")]),
    L("lx.git.pull", "Обновить репозиторий (git pull)", ["обнови репозиторий", "git pull", "стяни изменения"],
      [R("git status -sb 2>&1 | head -5"), R("git pull --ff-only 2>&1 | tail -8")], risk="change", rollback="git reset --hard ORIG_HEAD (только если нет своих правок)"),
    L("lx.docker", "Docker: контейнеры и ресурсы", ["какие контейнеры", "docker ps", "состояние docker", "что запущено в docker"],
      [R("docker ps -a --format '{{.Names}}\\t{{.Status}}\\t{{.Image}}'"), R("docker stats --no-stream 2>/dev/null | head -15"), R("docker system df 2>/dev/null")]),
    L("lx.docker.logs", "Логи контейнера", ["логи контейнера", "что пишет контейнер", "docker logs"],
      [R("docker logs --tail 80 {container} 2>&1 | tail -80")], slots=CTR),
    L("lx.docker.restart", "Перезапустить контейнер", ["перезапусти контейнер", "рестарт контейнера", "docker restart"],
      [R("docker ps -a --filter name={container} --format '{{.Names}} {{.Status}}'"), R("docker restart {container} 2>&1", stop=True), W(5),
       R("docker ps --filter name={container} --format '{{.Names}} {{.Status}}'", expect="Up"), R("docker logs --tail 15 {container} 2>&1 | tail -15")],
      risk="change", slots=CTR),
    L("lx.docker.stop", "Остановить контейнер", ["останови контейнер", "docker stop"],
      [R("docker stop {container} 2>&1"), R("docker ps -a --filter name={container} --format '{{.Names}} {{.Status}}'")], risk="change", slots=CTR,
      rollback="docker start {container}"),
    L("lx.docker.prune", "Почистить Docker (неиспользуемое)", ["очисти docker", "docker prune", "освободи место от docker"],
      [R("docker system df 2>&1"), R("docker image prune -f 2>&1 | tail -3"), R("docker system df 2>&1")], risk="change",
      notes="Удаляет только неиспользуемые образы без тегов. `system prune -a` — отдельное решение владельца."),
    L("lx.compose", "Docker Compose: состояние", ["состояние compose", "docker compose ps"],
      [R("docker compose ps 2>&1 | head -20"), R("docker compose logs --tail 30 2>&1 | tail -30")]),
    L("lx.compose.up", "Docker Compose: обновить и поднять", ["обнови compose", "docker compose up", "перезапусти compose"],
      [R("docker compose pull 2>&1 | tail -5"), R("docker compose up -d 2>&1 | tail -8"), R("docker compose ps 2>&1 | head -15")], risk="change",
      rollback="docker compose down; вернуть прежние теги образов"),
    L("lx.nginx", "Веб-сервер nginx/apache", ["проверь nginx", "nginx не работает", "веб-сервер", "apache"],
      [R("systemctl status nginx --no-pager 2>&1 | head -12"), R("sudo -n nginx -t 2>&1"), R("tail -n 30 /var/log/nginx/error.log 2>&1"), R("apachectl -S 2>&1 | head")]),
    L("lx.nginx.reload", "Перезагрузить конфиг nginx", ["перезагрузи nginx", "reload nginx", "применить конфиг nginx"],
      [R("sudo -n nginx -t 2>&1", expect="successful", stop=True, note="конфиг должен пройти проверку"), R("sudo -n systemctl reload nginx 2>&1"), R("systemctl is-active nginx 2>&1", expect="^active")],
      risk="change", rollback="вернуть прежний конфиг и снова reload"),
    L("lx.db", "Базы данных: службы и порты", ["проверь базу данных", "postgres", "mysql", "redis", "mongo"],
      [R("systemctl status postgresql mysql mariadb redis-server mongod --no-pager 2>&1 | grep -E 'Active|●' | head -10"), R("ss -tlnp 2>/dev/null | grep -E '5432|3306|6379|27017'")]),
    L("lx.backup.tar", "Резервная копия папки", ["сделай бэкап папки", "заархивируй папку", "резервная копия каталога"],
      [R("du -sh {dir} 2>&1"), R("mkdir -p ~/backups && tar czf ~/backups/backup_$(date +%F_%H%M).tgz {dir} 2>&1 | tail -3"), R("ls -lah ~/backups | tail -5")],
      risk="change", slots=DIR, rollback="tar xzf ~/backups/<файл>.tgz -C /"),
    L("lx.cron.show", "Задания cron", ["покажи cron", "задачи cron", "crontab"],
      [R("crontab -l 2>&1"), R("ls -la /etc/cron.d 2>/dev/null")]),
    L("lx.env", "Окружение и PATH", ["переменные окружения", "path", "printenv"],
      [R("printenv | sort | head -30"), R("echo $PATH | tr ':' '\\n'")]),
    L("lx.find", "Найти файл по имени", ["найди файл", "где лежит файл", "поиск файла"],
      [R("find ~ -xdev -type f -mtime -2 2>/dev/null | head -20"), R("ls -lat ~ | head -15")]),
    L("lx.perm", "Права на файлы", ["права доступа", "кто владелец файла", "chmod"],
      [R("ls -la ~ | head -25"), R("stat -c '%A %U:%G %n' ~/.ssh/* 2>/dev/null")]),
    L("lx.swap", "Подкачка", ["файл подкачки", "swap", "добавь swap"],
      [R("swapon --show; free -h | grep -i swap; cat /proc/sys/vm/swappiness")]),
    L("lx.usb", "USB и камеры", ["usb устройства", "камера не видна", "lsusb", "/dev/video"],
      [R("lsusb 2>&1"), R("ls -l /dev/video* 2>&1"), R("dmesg 2>/dev/null | grep -iE 'uvc|usb' | tail -12")]),
    L("lx.audio", "Звук", ["звук не работает", "микрофон", "колонки", "alsa"],
      [R("arecord -l 2>&1 | head -12"), R("aplay -l 2>&1 | head -12"), R("amixer 2>&1 | head -12")]),
    L("lx.serial", "Последовательные порты", ["serial порты", "ttyUSB", "ttyACM", "arduino подключена"],
      [R("ls -l /dev/tty* 2>/dev/null | grep -E 'ACM|USB|AMA|S0' | head"), R("dmesg 2>/dev/null | grep -iE 'tty|cdc_acm|ftdi' | tail -10")]),
    L("lx.wifi", "Wi-Fi", ["wifi", "беспроводная сеть", "nmcli"],
      [R("nmcli dev status 2>&1 | head"), R("nmcli -f SSID,SIGNAL,SECURITY dev wifi 2>&1 | head -12")]),
    L("lx.hostname", "Имя хоста", ["как называется сервер", "hostname"], [R("hostnamectl 2>&1 | head -8")]),
    L("lx.hostname.set", "Сменить имя хоста", ["смени имя хоста", "переименуй сервер"],
      [R("hostnamectl 2>&1 | head -4"), N("Укажи новое имя в задаче: «смени имя хоста на web2». Выполнится: sudo hostnamectl set-hostname web2")],
      risk="change", verify=["имя изменено"]),
    L("lx.reboot", "Перезагрузить сервер", ["перезагрузи сервер", "reboot сервера", "рестарт сервера", "перезагрузи машину"],
      [R("uptime; who", note="кто в системе сейчас"), R("test -f /var/run/reboot-required && echo 'нужна' || echo 'не требуется'"),
       R("sudo -n systemctl reboot 2>&1", note="связь оборвётся"), W(60), CHK(), W(30), CHK(), R("uptime -p; systemctl --failed --no-pager | head -8")],
      risk="danger", verify=["сервер вернулся, нет упавших служб"], rollback="если не поднялся — консоль/IPMI/питание"),
    L("lx.shutdown", "Выключить сервер", ["выключи сервер", "shutdown", "poweroff"],
      [R("uptime; who"), R("sudo -n systemctl poweroff 2>&1")], risk="danger",
      rollback="включить по сети (Wake-on-LAN, если настроен) или кнопкой питания",
      notes="Дистанционно обратно включить можно только через WoL/IPMI."),
    L("lx.wake", "Включить выключенный сервер по сети", ["включи сервер", "разбуди сервер", "wake on lan", "включи компьютер по сети"],
      [{"tool": "wake"}, CHK()], risk="change", verify=["порт SSH открыт"], notes="Нужен MAC сервера в окне «Серверы» и включённый WoL в BIOS."),
    L("lx.script", "Написать и запустить скрипт", ["напиши скрипт", "создай скрипт", "сделай программу", "напиши код на сервере"],
      [{"tool": "code"}], risk="change", verify=["код написан своей LLM, проверен синтаксисом и запущен"],
      notes="Скрипт кладётся в ~/neuro12-agent/, перед запуском проверяется синтаксис."),
]

# ------------------------------------------------------------------ WINDOWS
def Wn(id, title, ask, steps, risk="read", verify=None, rollback="", notes="", slots=None):
    return I(id, title, ask, "windows", steps, risk, verify, rollback, notes, slots)


WSVC = {"service": r"(?:служб\w*|сервис\w*|service)\s+([\w.\-]+)"}
WPROC = {"proc": r"(?:процесс\w*|process)\s+([\w.\-]+)"}
WINDOWS = [
    Wn("wn.overview", "Общий обзор Windows", ["проверь компьютер", "обзор windows", "состояние компьютера", "как дела на windows", "проверь сервер windows"],
       [R("Get-CimInstance Win32_OperatingSystem | Select-Object Caption,Version,CSName,LastBootUpTime | Format-List"),
        R("Get-PSDrive -PSProvider FileSystem | Select-Object Name,@{n='ИспГБ';e={[math]::Round($_.Used/1GB,1)}},@{n='СвобГБ';e={[math]::Round($_.Free/1GB,1)}}"),
        R("Get-Service | Where-Object {$_.Status -eq 'Stopped' -and $_.StartType -eq 'Automatic'} | Select-Object -First 15 Name,DisplayName")]),
    Wn("wn.os", "Версия Windows", ["версия windows", "какая windows", "сборка windows"],
       [R("Get-CimInstance Win32_OperatingSystem | Select-Object Caption,Version,BuildNumber,OSArchitecture | Format-List"), R("systeminfo | Select-Object -First 25")]),
    Wn("wn.uptime", "Время работы", ["аптайм windows", "когда включали компьютер", "сколько работает компьютер"],
       [R("((Get-Date)-(Get-CimInstance Win32_OperatingSystem).LastBootUpTime).ToString()")]),
    Wn("wn.mem", "Память", ["память windows", "сколько памяти", "что ест память", "не хватает памяти"],
       [R("Get-CimInstance Win32_OperatingSystem | Select-Object @{n='ВсегоГБ';e={[math]::Round($_.TotalVisibleMemorySize/1MB,1)}},@{n='СвободноГБ';e={[math]::Round($_.FreePhysicalMemory/1MB,1)}} | Format-List"),
        R("Get-Process | Sort-Object WS -Descending | Select-Object -First 10 Name,Id,@{n='МБ';e={[math]::Round($_.WS/1MB)}}")]),
    Wn("wn.cpu", "Процессор и нагрузка", ["что грузит процессор windows", "компьютер тормозит", "лагает windows", "процессор загружен"],
       [R("Get-CimInstance Win32_Processor | Select-Object Name,NumberOfCores,NumberOfLogicalProcessors,LoadPercentage | Format-List"),
        R("Get-Process | Sort-Object CPU -Descending | Select-Object -First 12 Name,Id,CPU,@{n='МБ';e={[math]::Round($_.WS/1MB)}}")]),
    Wn("wn.disk", "Диски и место", ["место на диске windows", "диск заполнен windows", "сколько места на c", "свободное место"],
       [R("Get-PSDrive -PSProvider FileSystem | Select-Object Name,@{n='ИспГБ';e={[math]::Round($_.Used/1GB,1)}},@{n='СвобГБ';e={[math]::Round($_.Free/1GB,1)}}"),
        R("Get-Volume | Select-Object DriveLetter,FileSystemLabel,HealthStatus,SizeRemaining,Size")]),
    Wn("wn.bigfiles", "Большие файлы", ["найди большие файлы windows", "что занимает место windows"],
       [R("Get-ChildItem $HOME -Recurse -File -ErrorAction SilentlyContinue | Sort-Object Length -Descending | Select-Object -First 10 FullName,@{n='МБ';e={[math]::Round($_.Length/1MB)}}")]),
    Wn("wn.diskhealth", "Здоровье дисков", ["здоровье диска windows", "smart windows", "диск умирает windows"],
       [R("Get-PhysicalDisk | Select-Object FriendlyName,MediaType,HealthStatus,OperationalStatus")]),
    Wn("wn.proc", "Процессы", ["список процессов", "что запущено на windows", "какие процессы"],
       [R("Get-Process | Sort-Object CPU -Descending | Select-Object -First 20 Name,Id,CPU")]),
    Wn("wn.proc.kill", "Завершить процесс", ["заверши процесс windows", "убей процесс windows", "закрой процесс"],
       [R("Get-Process -Name {proc} -ErrorAction SilentlyContinue | Select-Object Name,Id,StartTime", stop=True),
        R("Stop-Process -Name {proc} -ErrorAction Stop"), W(2), R("Get-Process -Name {proc} -ErrorAction SilentlyContinue | Select-Object Name,Id")],
       risk="change", slots=WPROC, rollback="запустить программу заново"),
    Wn("wn.svc.list", "Службы Windows", ["службы windows", "какие службы работают", "остановленные службы"],
       [R("Get-Service | Where-Object {$_.Status -eq 'Running'} | Select-Object -First 30 Name,DisplayName"),
        R("Get-Service | Where-Object {$_.Status -eq 'Stopped' -and $_.StartType -eq 'Automatic'} | Select-Object Name,DisplayName")]),
    Wn("wn.svc.status", "Статус службы Windows", ["статус службы windows", "работает ли служба windows"],
       [R("Get-Service -Name {service} | Format-List Name,DisplayName,Status,StartType")], slots=WSVC),
    Wn("wn.svc.restart", "Перезапустить службу Windows", ["перезапусти службу windows", "рестарт службы windows"],
       [R("Get-Service -Name {service} | Select-Object Name,Status", stop=True), R("Restart-Service -Name {service} -ErrorAction Stop"), W(5),
        R("Get-Service -Name {service} | Select-Object Name,Status", expect="Running")], risk="change", slots=WSVC, verify=["Status Running"]),
    Wn("wn.events", "Ошибки в журнале событий", ["журнал windows", "ошибки windows", "что случилось на windows", "события windows"],
       [R("Get-WinEvent -FilterHashtable @{LogName='System';Level=1,2} -MaxEvents 20 | Select-Object TimeCreated,ProviderName,Id,Message | Format-List"),
        R("Get-WinEvent -FilterHashtable @{LogName='Application';Level=2} -MaxEvents 10 | Select-Object TimeCreated,ProviderName,Message | Format-List")]),
    Wn("wn.net", "Сетевые настройки", ["сеть windows", "ip windows", "настройки сети"],
       [R("Get-NetIPConfiguration | Select-Object InterfaceAlias,IPv4Address,IPv4DefaultGateway,DNSServer | Format-List"), R("ipconfig /all | Select-Object -First 40")]),
    Wn("wn.ports", "Открытые порты", ["порты windows", "что слушает порт windows", "открытые порты windows"],
       [R("Get-NetTCPConnection -State Listen | Sort-Object LocalPort | Select-Object -First 30 LocalAddress,LocalPort,OwningProcess"),
        R("Get-Process -Id (Get-NetTCPConnection -State Listen | Select-Object -First 20 -ExpandProperty OwningProcess -Unique) -ErrorAction SilentlyContinue | Select-Object Id,Name")]),
    Wn("wn.ping", "Доступность сети", ["нет интернета windows", "проверь сеть windows", "ping windows"],
       [R("Test-Connection 1.1.1.1 -Count 3 | Select-Object Address,ResponseTime"), R("Test-Connection ya.ru -Count 3 | Select-Object Address,ResponseTime"),
        R("Test-NetConnection ya.ru -Port 443 | Select-Object ComputerName,TcpTestSucceeded")]),
    Wn("wn.dns", "DNS", ["dns windows", "сбрось dns", "dns не работает windows"],
       [R("Resolve-DnsName ya.ru | Select-Object -First 5 Name,Type,IPAddress"), R("Get-DnsClientServerAddress | Select-Object InterfaceAlias,ServerAddresses"),
        R("Clear-DnsClientCache; 'кэш DNS очищен'")], risk="change"),
    Wn("wn.fw", "Брандмауэр Windows", ["файрвол windows", "брандмауэр", "правила брандмауэра"],
       [R("Get-NetFirewallProfile | Select-Object Name,Enabled"), R("Get-NetFirewallRule -Enabled True -Direction Inbound | Select-Object -First 20 DisplayName,Action")]),
    Wn("wn.defender", "Защитник Windows", ["защитник windows", "антивирус windows", "defender"],
       [R("Get-MpComputerStatus | Select-Object AMServiceEnabled,AntivirusEnabled,RealTimeProtectionEnabled,AntivirusSignatureLastUpdated | Format-List")]),
    Wn("wn.updates", "Обновления Windows", ["обновления windows", "какие обновления установлены", "hotfix"],
       [R("Get-HotFix | Sort-Object InstalledOn -Descending | Select-Object -First 10 HotFixID,InstalledOn,Description")]),
    Wn("wn.software", "Установленные программы", ["какие программы установлены", "список программ windows"],
       [R("winget list 2>&1 | Select-Object -First 30")]),
    Wn("wn.winget.upgrade", "Обновить программы через winget", ["обнови программы windows", "winget upgrade"],
       [R("winget upgrade 2>&1 | Select-Object -First 20"), N("Чтобы обновить всё: winget upgrade --all (займёт время; подтверди отдельно).")], risk="change"),
    Wn("wn.tasks", "Планировщик заданий", ["задания по расписанию windows", "планировщик windows", "schtasks"],
       [R("Get-ScheduledTask | Where-Object {$_.State -ne 'Disabled'} | Select-Object -First 25 TaskName,State,TaskPath")]),
    Wn("wn.users", "Пользователи Windows", ["пользователи windows", "кто админ windows", "учётные записи windows"],
       [R("Get-LocalUser | Select-Object Name,Enabled,LastLogon"), R("Get-LocalGroupMember Administrators -ErrorAction SilentlyContinue")]),
    Wn("wn.startup", "Автозагрузка", ["автозагрузка windows", "что запускается при старте windows"],
       [R("Get-CimInstance Win32_StartupCommand | Select-Object Name,Command,Location")]),
    Wn("wn.time", "Время Windows", ["время windows", "синхронизация времени windows"],
       [R("Get-Date; w32tm /query /status 2>&1 | Select-Object -First 10")]),
    Wn("wn.time.sync", "Синхронизировать время", ["синхронизируй время windows", "сбилось время windows"],
       [R("w32tm /resync 2>&1")], risk="change"),
    Wn("wn.usb", "USB, камеры, звук", ["usb windows", "камера windows", "звук windows", "микрофон windows"],
       [R("Get-PnpDevice -PresentOnly | Where-Object {$_.Class -in 'USB','Camera','Image','AudioEndpoint'} | Select-Object Status,Class,FriendlyName")]),
    Wn("wn.serial", "COM-порты", ["com порты", "arduino windows", "последовательные порты windows"],
       [R("[System.IO.Ports.SerialPort]::GetPortNames()"), R("Get-CimInstance Win32_SerialPort | Select-Object DeviceID,Name")]),
    Wn("wn.docker", "Docker на Windows", ["docker windows", "контейнеры windows"],
       [R("docker ps -a --format '{{.Names}}  {{.Status}}  {{.Image}}'"), R("docker images")]),
    Wn("wn.python", "Python на Windows", ["python windows", "версия python windows", "pip windows"],
       [R("python --version; pip --version"), R("pip list 2>&1 | Select-Object -First 25")]),
    Wn("wn.git", "Git на Windows", ["git windows", "состояние репозитория windows"],
       [R("git status -sb 2>&1 | Select-Object -First 15"), R("git log --oneline -n 10 2>&1")]),
    Wn("wn.reboot", "Перезагрузить Windows", ["перезагрузи windows", "перезагрузи компьютер", "рестарт windows"],
       [R("query user 2>&1", note="кто вошёл"), R("Restart-Computer -Force"), W(90), CHK(), W(30), CHK()],
       risk="danger", verify=["компьютер вернулся"], notes="Если на машине работает облако, оно остановится — свяжись по другому каналу."),
    Wn("wn.shutdown", "Выключить Windows", ["выключи компьютер", "выключи windows"],
       [R("query user 2>&1"), R("Stop-Computer -Force")], risk="danger", rollback="включить кнопкой или Wake-on-LAN"),
    Wn("wn.sfc", "Проверка системных файлов (sfc)", ["проверь системные файлы", "sfc scannow", "починить windows"],
       [R("sfc /verifyonly 2>&1 | Select-Object -Last 8")], verify=["нарушений целостности нет"],
       notes="Исправление: sfc /scannow от администратора — долго, подтверди отдельно."),
    Wn("wn.hosts", "Файл hosts", ["файл hosts", "hosts windows"],
       [R("Get-Content $env:SystemRoot\\System32\\drivers\\etc\\hosts | Select-Object -First 30")]),
    Wn("wn.env", "Переменные окружения", ["переменные окружения windows", "path windows"],
       [R("Get-ChildItem Env: | Sort-Object Name | Select-Object -First 30")]),
]


def api_reference():
    """Справочник API платформы из исходников: маршруты и первые строки docstring обработчиков."""
    out = []
    for fn in ("api_ui.py", "api_board.py"):
        src = open(os.path.join(SRV, fn), encoding="utf-8").read()
        tree = ast.parse(src)
        docs = {n.name: (ast.get_docstring(n) or "").strip().split("\n")[0][:220]
                for n in tree.body if isinstance(n, ast.FunctionDef)}
        for m in re.finditer(r'^_r\("(GET|POST|PUT|DELETE)",\s*"([^"]+)",\s*([\w.]+)([^\n]*)', src, re.M):
            meth, path, h, rest = m.groups()
            out.append({"method": meth, "path": "/" + path.lstrip("/"), "handler": h, "doc": docs.get(h, ""),
                        "auth": "auth=True" in rest, "file": fn})
    return out


def main():
    os.makedirs(OUT, exist_ok=True)
    sets = {"board": BOARD, "cloud": CLOUD, "linux": LINUX, "windows": WINDOWS}
    total = 0
    for name, items in sets.items():
        ids = [i["id"] for i in items]
        assert len(ids) == len(set(ids)), "дубли id в %s" % name
        with open(os.path.join(OUT, name + ".json"), "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False, indent=1)
        total += len(items)
        print(name, len(items))
    ref = api_reference()
    with open(os.path.join(SRV, "platform_api.json"), "w", encoding="utf-8") as f:
        json.dump(ref, f, ensure_ascii=False, indent=1)
    print("инструкций:", total, "· маршрутов API:", len(ref))


if __name__ == "__main__":
    main()
