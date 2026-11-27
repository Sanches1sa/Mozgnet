# -*- coding: utf-8 -*-
"""admincmds.py — v3.216: СПРАВОЧНИК КОМАНД АГЕНТА «АДМИНИСТРИРОВАНИЕ».

Раньше агент знал ~20 уроков и десяток намерений в правилах, остальное падало
в «обзор сервера». Здесь — каталог: намерение (регулярка по задаче) → готовые
команды Linux. Он используется тремя способами:
  1) plan(задача) — правила агента подбирают команды (ssh_agent._rule_plan);
  2) reference() — компактная шпаргалка в подсказку модели-планировщика;
  3) lessons() — уроки с командами в кодовых блоках для БАЗЫ ЗНАНИЙ и обучения
     своей LLM (потока «admin»). Латинские команды в ``` не отсекаются.
Читающие команды выполняются сразу, изменяющие агент подтверждает у владельца
(servers.classify), опасные — всегда.
"""
import re

# (ключ, регулярка по задаче, заголовок, [команды], пояснение)
CATALOG = [
    ("uptime", r"аптайм|uptime|как давно (работает|включ)|время работы", "Время работы и нагрузка",
     ["uptime -p; uptime", "who -b"], "load average выше числа ядер (nproc) — перегрузка"),
    ("os", r"верси\w+ (системы|ос|linux|дистрибутив)|какая (ос|система)|os-release|\buname\b|ядро", "Система и ядро",
     ["cat /etc/os-release | head -6", "uname -a", "hostnamectl 2>/dev/null | head -10"], "дистрибутив, ядро, архитектура"),
    ("cpu", r"процессор|\bcpu\b|ядер|lscpu|частот", "Процессор",
     ["lscpu | head -20", "nproc", "grep MHz /proc/cpuinfo | head -4"], "модель, ядра, частота"),
    ("mem", r"памят|\bram\b|\bswap\b|озу|oom", "Память",
     ["free -h", "ps aux --sort=-%mem | head -10", "dmesg 2>/dev/null | grep -i 'out of memory' | tail -5"], "OOM-killer в dmesg — не хватало памяти"),
    ("disk", r"диск|мест[оа] на|df\b|заполн|раздел", "Диски и место",
     ["df -h -x tmpfs -x devtmpfs", "df -i / | tail -2", "du -xh --max-depth=1 / 2>/dev/null | sort -h | tail -12"], "inode закончились — df -i"),
    ("blk", r"lsblk|блочн|смонтир|mount\b|точк\w+ монтир|fstab|флешк|usb[- ]?накопит", "Блочные устройства и монтирование",
     ["lsblk -f", "findmnt --real 2>/dev/null | head -20", "cat /etc/fstab"], "UUID и файловые системы"),
    ("smart", r"smart|здоровь\w+ диск|состояние диск|smartctl", "Здоровье диска",
     ["sudo -n smartctl -H /dev/sda 2>&1 | head -12", "ls /dev/disk/by-id | head"], "нужен пакет smartmontools"),
    ("temp", r"температур|перегрев|sensors|охлажд|vcgencmd", "Температура",
     ["sensors 2>/dev/null | head -20 || echo 'lm-sensors не установлен'", "cat /sys/class/thermal/thermal_zone*/temp 2>/dev/null"], "значения в тысячных долях градуса"),
    ("proc", r"процесс|что (ест|грузит)|тормоз|нагруз|\btop\b|\bhtop\b", "Процессы и нагрузка",
     ["ps aux --sort=-%cpu | head -12", "top -bn1 | head -15", "uptime"], "top без -bn1 — интерактивный, запрещён"),
    ("findproc", r"найди процесс|запущен ли|pgrep|pidof|слушает ли", "Поиск процесса",
     ["pgrep -a -f . | head -20", "ss -tlnp | head -25"], "pgrep -a -f имя — по части команды"),
    ("kill", r"убей процесс|заверши процесс|kill\b|pkill|завис\w+ процесс", "Остановка процесса",
     ["ps aux --sort=-%cpu | head -8"], "сначала найди PID; kill PID — SIGTERM, kill -9 — крайняя мера (агент спросит)"),
    ("svc", r"служб|сервис|systemctl|юнит|unit", "Службы systemd",
     ["systemctl list-units --type=service --state=running --no-pager | head -30", "systemctl --failed --no-pager"], "status имя, restart имя (спросит)"),
    ("timer", r"таймер|systemd timer|по расписанию systemd", "Таймеры systemd",
     ["systemctl list-timers --all --no-pager | head -20"], "NEXT/LAST — когда запускался"),
    ("boot", r"загрузк|boot\b|автозапуск|enable|при старте", "Автозапуск и загрузка",
     ["systemctl list-unit-files --state=enabled --no-pager | head -30", "systemd-analyze 2>/dev/null; systemd-analyze blame 2>/dev/null | head -10"], "что включено и что грузится дольше"),
    ("journal", r"журнал|лог\b|логи|journalctl|syslog|ошибк", "Журналы",
     ["journalctl -p err -n 40 --no-pager 2>/dev/null || tail -n 60 /var/log/syslog", "journalctl -b -p warning -n 30 --no-pager 2>/dev/null | tail -30"], "-u служба -n 80 — по службе; --since '1 hour ago'"),
    ("auth", r"вход\w* в систему|кто (заходил|входил|подключ)|авторизац|ssh вход|last\b|lastb|подбор парол|bruteforce", "Входы и попытки подбора",
     ["last -n 15 2>/dev/null", "lastb -n 10 2>/dev/null | head -12", "journalctl -u ssh -u sshd -n 40 --no-pager 2>/dev/null | tail -30"], "много Failed password — подбор, включи fail2ban/ключи"),
    ("who", r"кто сейчас|who\b|сессии|залогин", "Кто в системе",
     ["who", "w"], "активные сессии"),
    ("net", r"\bip\b|адрес|сетев|интерфейс|ifconfig", "Сетевые интерфейсы",
     ["ip -br a 2>/dev/null || hostname -I", "ip route 2>/dev/null | head", "cat /etc/resolv.conf"], "маршрут по умолчанию и DNS"),
    ("ports", r"порт|слушает|ss\b|netstat|открыт\w+ порт", "Открытые порты",
     ["ss -tlnp 2>/dev/null | head -30 || netstat -tln", "ss -s"], "-tlnp — TCP, слушающие, с процессами"),
    ("ping", r"пинг|ping|доступн\w+ (хост|интернет|сайт)|нет интернета|traceroute|tracepath", "Доступность сети",
     ["ping -c 3 -W 2 1.1.1.1 2>&1 | tail -4", "ping -c 3 -W 2 ya.ru 2>&1 | tail -4", "tracepath -n -m 8 1.1.1.1 2>&1 | head -12"], "по IP есть, по имени нет — проблема DNS"),
    ("dns", r"\bdns\b|резолв|nslookup|\bdig\b|не открывается по имени", "DNS",
     ["getent hosts ya.ru", "cat /etc/resolv.conf", "resolvectl status 2>/dev/null | head -15"], "getent — как видит система"),
    ("curl", r"curl|http[- ]?код|сайт (отвечает|открывается|не открывается)|проверь сайт|http-заголовк", "Запрос HTTP к сайту",
     ["curl -sSI -m 10 http://localhost/ | head -12"], "для внешнего сайта подставь адрес"),
    ("fw", r"firewall|фаервол|брандмауэр|ufw|iptables|nftables|firewalld", "Файрвол",
     ["sudo -n ufw status verbose 2>&1 | head -25", "sudo -n iptables -S 2>&1 | head -25", "sudo -n nft list ruleset 2>&1 | head -30"], "ufw allow/deny меняет правила — агент спросит"),
    ("wifi", r"wi-?fi|вайфай|беспровод|wlan|nmcli", "Wi-Fi и сетевые профили",
     ["nmcli dev status 2>&1 | head", "nmcli -f SSID,SIGNAL,SECURITY dev wifi 2>&1 | head -12", "iwconfig 2>/dev/null | head -12"], "SIGNAL — уровень сигнала"),
    ("nginx", r"nginx|веб-?сервер|apache|httpd|caddy", "Веб-сервер",
     ["systemctl status nginx --no-pager 2>&1 | head -12", "nginx -t 2>&1", "tail -n 30 /var/log/nginx/error.log 2>&1", "apachectl -S 2>&1 | head"], "nginx -t — проверка конфига до перезапуска"),
    ("docker", r"docker|докер|контейнер|образ\b|compose", "Docker",
     ["docker ps -a --format '{{.Names}}\\t{{.Status}}\\t{{.Image}}'", "docker stats --no-stream 2>/dev/null | head -15", "docker images --format '{{.Repository}}:{{.Tag}}\\t{{.Size}}' | head -15", "docker system df 2>/dev/null"], "logs --tail 50 имя — журнал контейнера"),
    ("pkg", r"установленн\w+ пакет|список пакетов|какие пакеты|dpkg|rpm -qa|версия пакета|apt list", "Пакеты",
     ["dpkg -l 2>/dev/null | tail -20 || rpm -qa | tail -20", "apt list --upgradable 2>/dev/null | head -20"], "установка — apt/dnf install (спросит)"),
    ("upd", r"обновлени|обнови систему|upgrade|security update|уязвим", "Обновления",
     ["apt list --upgradable 2>/dev/null | head -30 || dnf check-update 2>/dev/null | head -30", "ls /var/run/reboot-required 2>/dev/null && echo 'нужна перезагрузка'"], "apt update/upgrade меняет систему (спросит)"),
    ("pip", r"\bpip\b|модул\w+ python|python-пакет|virtualenv|venv|версия python|python3", "Python на сервере",
     ["python3 --version; pip3 --version", "pip3 list 2>/dev/null | head -25", "python3 -c \"import sys;print(sys.executable, sys.path[:3])\""], "venv: python3 -m venv ~/env && ~/env/bin/pip install пакет"),
    ("git", r"\bgit\b|гит|репозитор|коммит|ветк", "Git",
     ["git status -sb 2>&1 | head -15", "git log --oneline -n 10 2>&1", "git remote -v 2>&1"], "git pull/push меняют состояние (спросит)"),
    ("cron", r"cron|крон|расписани|по расписанию|планировщик", "Планировщик cron",
     ["crontab -l 2>&1", "ls -la /etc/cron.d /etc/cron.daily 2>/dev/null | head -25", "systemctl list-timers --no-pager | head -12"], "формат: мин час день мес деньнедели команда"),
    ("user", r"пользовател|учётн\w+ запис|users|группы|sudoers|id\b", "Пользователи",
     ["id; whoami", "cut -d: -f1,3,6 /etc/passwd | awk -F: '$2>=1000' ", "getent group sudo wheel 2>/dev/null"], "UID>=1000 — обычные пользователи"),
    ("ssh", r"ssh-?ключ|authorized_keys|sshd_config|доступ по ключу|вход по паролю", "Настройки SSH",
     ["ls -la ~/.ssh 2>&1", "grep -E '^(PermitRootLogin|PasswordAuthentication|PubkeyAuthentication|Port)' /etc/ssh/sshd_config 2>&1", "sshd -T 2>/dev/null | grep -E 'permitrootlogin|passwordauth|port' | head"], "правка sshd_config — только после sshd -t"),
    ("cert", r"сертификат|ssl|tls|https|openssl|срок действия", "Сертификаты",
     ["ls -la /etc/letsencrypt/live 2>&1 | head", "echo | openssl s_client -connect localhost:443 2>/dev/null | openssl x509 -noout -dates -subject 2>&1 | head -4"], "notAfter — когда истекает"),
    ("time", r"время|дата\b|часов\w+ пояс|ntp|timedatectl|синхронизац\w+ времени", "Время и NTP",
     ["timedatectl 2>&1 | head -10", "date; date -u"], "System clock synchronized: yes"),
    ("host", r"имя хоста|hostname|как называется (сервер|машина)", "Имя хоста",
     ["hostnamectl 2>&1 | head -8", "hostname -f 2>&1"], ""),
    ("find", r"найди файл|где лежит|find\b|поиск файл|большие файлы|что занимает", "Поиск файлов",
     ["find ~ -xdev -type f -size +100M -printf '%s %p\\n' 2>/dev/null | sort -n | tail -10", "find /var/log -type f -mtime -1 2>/dev/null | head -20"], "поиск только читает"),
    ("grep", r"grep|найди в (логе|файле)|строки с", "Поиск по тексту",
     ["grep -rIl 'error' /var/log 2>/dev/null | head -10", "grep -ri 'error' /var/log/syslog 2>/dev/null | tail -15"], "подставь своё слово"),
    ("tail", r"хвост|последние строки|tail\b|покажи файл|cat\b", "Просмотр файла",
     ["tail -n 40 /var/log/syslog 2>&1", "ls -lat /var/log | head -15"], "путь — из задачи"),
    ("perm", r"прав\w+ доступа|chmod|chown|владел\w+ файл|ls -l", "Права файлов",
     ["ls -la ~ | head -25", "stat -c '%A %U:%G %n' ~/.ssh/* 2>/dev/null"], "chmod/chown меняют права (спросит)"),
    ("archive", r"архив|tar\b|zip\b|бэкап|резервн|rsync|копи\w+ на другой", "Архивы и резервные копии",
     ["ls -lah ~/backup* /var/backups 2>/dev/null | head -20", "df -h /var/backups 2>/dev/null"], "tar czf имя.tgz папка; rsync -a --info=progress2 откуда куда"),
    ("tmux", r"tmux|screen\b|сессия терминала|в фоне|nohup", "Фоновые сессии",
     ["tmux ls 2>&1", "screen -ls 2>&1", "jobs; ps -eo pid,etime,cmd | grep -E 'nohup|python' | head"], "nohup команда & — запуск в фоне"),
    ("env", r"переменн\w+ окружения|env\b|\bpath\b|printenv", "Окружение",
     ["printenv | sort | head -30", "echo $PATH | tr ':' '\\n'"], ""),
    ("limits", r"лимит|ulimit|открыт\w+ файл|too many open", "Лимиты",
     ["ulimit -a | head -20", "cat /proc/sys/fs/file-nr", "lsof 2>/dev/null | wc -l"], "file-nr: открыто / выделено / максимум"),
    ("usb", r"usb|подключ\w+ устройств|lsusb|камер\w+ не|веб-?камер|/dev/video", "USB и камеры",
     ["lsusb 2>&1", "ls -l /dev/video* 2>&1", "dmesg 2>/dev/null | grep -iE 'uvc|usb' | tail -12", "v4l2-ctl --list-devices 2>&1 | head -15"], "uvcvideo в dmesg — драйвер камеры"),
    ("audio", r"микрофон|звук|колонк|alsa|arecord|aplay|pulseaudio|pipewire", "Звук",
     ["arecord -l 2>&1 | head -12", "aplay -l 2>&1 | head -12", "amixer 2>&1 | head -12"], "card N, device M — для plughw:N,M"),
    ("gpio", r"gpio|spi\b|i2c|uart|serial|ttyacm|ttyusb|порт ардуино|stm32", "Железо: шины и последовательные порты",
     ["ls -l /dev/tty* 2>/dev/null | grep -E 'ACM|USB|AMA|S0' | head", "i2cdetect -l 2>&1 | head", "ls /dev/spidev* /dev/gpiochip* 2>&1 | head"], "ttyACM — обычно Arduino/STM32"),
    ("arduino", r"arduino-app|app lab|приложени\w+ платы|мост|bridge|sketch|скетч", "Arduino UNO Q и App Lab",
     ["arduino-app-cli app list 2>&1 | head -20", "arduino-app-cli system version 2>&1 | head -5", "docker ps --format '{{.Names}}\\t{{.Status}}' 2>&1 | head"], "start/stop/restart меняют приложение (спросит)"),
    ("board", r"плат[аеуы] (жива|работает)|состояние платы|память платы|температура платы", "Плата UNO Q: аптайм, температура, порты",
     ["uptime; free -h; df -h / | tail -1", "cat /sys/class/thermal/thermal_zone0/temp 2>/dev/null", "ls /dev/ttyACM* /dev/ttyHS* 2>&1 | head"], ""),
    ("zephyr", r"zephyr|llext|stm32u5|прошивк", "Прошивка MCU (Zephyr)",
     ["ls -la /opt/arduino* 2>&1 | head", "dmesg 2>/dev/null | grep -iE 'stm32|zephyr|rpmsg|mcu' | tail -12"], "скетч грузится как llext; ошибки загрузки — в журнале App Lab"),
    ("swapon", r"файл подкачки|добавь swap|swapon|подкачк", "Файл подкачки",
     ["swapon --show", "free -h | grep -i swap", "cat /proc/sys/vm/swappiness"], "создание swap-файла — fallocate/mkswap/swapon (спросит)"),
    ("lang", r"локал|раскладк|кодировк|locale|utf-?8", "Локаль и кодировка",
     ["locale", "localectl status 2>&1 | head"], ""),
    ("smb", r"samba|smb\b|общая папка|nfs|сетевая папка", "Сетевые папки",
     ["showmount -e localhost 2>&1 | head", "smbstatus 2>&1 | head -15", "findmnt -t nfs,nfs4,cifs 2>&1"], ""),
    ("db", r"postgres|mysql|mariadb|sqlite|база данных|\bбд\b|redis|mongo", "Базы данных",
     ["systemctl status postgresql mysql mariadb redis-server mongod --no-pager 2>&1 | grep -E 'Active|●' | head -10", "ss -tlnp 2>/dev/null | grep -E '5432|3306|6379|27017'"], "доступ к данным — только по просьбе"),
    ("wol", r"wake|разбуди|включи сервер|wol|по сети включить", "Включение по сети (WoL)",
     ["# агент: инструмент wake — магический пакет на MAC сервера"], "в окне «Серверы» у сервера должен быть MAC"),
    ("win", r"windows|виндовс|powershell|\bcmd\b", "Windows-сервер по SSH",
     ["powershell -NoProfile -Command \"Get-ComputerInfo | Select-Object OsName,OsVersion,CsName\"",
      "powershell -NoProfile -Command \"Get-Service | Where-Object {$_.Status -eq 'Running'} | Select-Object -First 25\"",
      "powershell -NoProfile -Command \"Get-PSDrive -PSProvider FileSystem\""], "Get-EventLog/Get-WinEvent — журналы Windows"),
]


# Windows (PowerShell): те же намерения. Читающие команды; вывод — UTF-8.
WIN = {
    "uptime": ["(Get-CimInstance Win32_OperatingSystem).LastBootUpTime; ((Get-Date)-(Get-CimInstance Win32_OperatingSystem).LastBootUpTime).ToString()"],
    "os": ["Get-CimInstance Win32_OperatingSystem | Select-Object Caption,Version,OSArchitecture,CSName | Format-List", "systeminfo | Select-Object -First 25"],
    "cpu": ["Get-CimInstance Win32_Processor | Select-Object Name,NumberOfCores,NumberOfLogicalProcessors,MaxClockSpeed | Format-List"],
    "mem": ["Get-CimInstance Win32_OperatingSystem | Select-Object @{n='ВсегоГБ';e={[math]::Round($_.TotalVisibleMemorySize/1MB,1)}},@{n='СвободноГБ';e={[math]::Round($_.FreePhysicalMemory/1MB,1)}} | Format-List", "Get-Process | Sort-Object WS -Descending | Select-Object -First 10 Name,@{n='МБ';e={[math]::Round($_.WS/1MB)}}"],
    "disk": ["Get-PSDrive -PSProvider FileSystem | Select-Object Name,@{n='ИспГБ';e={[math]::Round($_.Used/1GB,1)}},@{n='СвобГБ';e={[math]::Round($_.Free/1GB,1)}}", "Get-Volume | Select-Object DriveLetter,FileSystemLabel,HealthStatus,SizeRemaining,Size"],
    "blk": ["Get-Disk | Select-Object Number,FriendlyName,Size,HealthStatus", "Get-Partition | Select-Object DiskNumber,PartitionNumber,DriveLetter,Size"],
    "smart": ["Get-PhysicalDisk | Select-Object FriendlyName,MediaType,HealthStatus,OperationalStatus"],
    "temp": ["Get-CimInstance -Namespace root/wmi -ClassName MSAcpi_ThermalZoneTemperature -ErrorAction SilentlyContinue | Select-Object @{n='C';e={[math]::Round($_.CurrentTemperature/10-273.15,1)}}"],
    "proc": ["Get-Process | Sort-Object CPU -Descending | Select-Object -First 12 Name,Id,CPU,@{n='МБ';e={[math]::Round($_.WS/1MB)}}"],
    "findproc": ["Get-Process | Select-Object -First 40 Name,Id", "Get-NetTCPConnection -State Listen | Select-Object -First 25 LocalAddress,LocalPort,OwningProcess"],
    "kill": ["Get-Process | Sort-Object CPU -Descending | Select-Object -First 8 Name,Id"],
    "svc": ["Get-Service | Where-Object {$_.Status -eq 'Running'} | Select-Object -First 30 Name,DisplayName", "Get-Service | Where-Object {$_.Status -eq 'Stopped' -and $_.StartType -eq 'Automatic'} | Select-Object Name,DisplayName"],
    "boot": ["Get-CimInstance Win32_StartupCommand | Select-Object Name,Command,Location"],
    "journal": ["Get-WinEvent -FilterHashtable @{LogName='System';Level=1,2,3} -MaxEvents 20 | Select-Object TimeCreated,ProviderName,Id,Message | Format-List", "Get-WinEvent -FilterHashtable @{LogName='Application';Level=2} -MaxEvents 10 | Select-Object TimeCreated,ProviderName,Message | Format-List"],
    "auth": ["Get-WinEvent -FilterHashtable @{LogName='Security';Id=4624,4625} -MaxEvents 15 -ErrorAction SilentlyContinue | Select-Object TimeCreated,Id"],
    "who": ["query user 2>&1"],
    "net": ["Get-NetIPConfiguration | Select-Object InterfaceAlias,IPv4Address,IPv4DefaultGateway,DNSServer | Format-List", "ipconfig /all | Select-Object -First 40"],
    "ports": ["Get-NetTCPConnection -State Listen | Sort-Object LocalPort | Select-Object -First 30 LocalAddress,LocalPort,OwningProcess", "netstat -ano | Select-Object -First 30"],
    "ping": ["Test-Connection 1.1.1.1 -Count 3 | Select-Object Address,ResponseTime", "Test-Connection ya.ru -Count 3 | Select-Object Address,ResponseTime", "Test-NetConnection ya.ru -Port 443 | Select-Object ComputerName,TcpTestSucceeded"],
    "dns": ["Resolve-DnsName ya.ru | Select-Object -First 5 Name,Type,IPAddress", "Get-DnsClientServerAddress | Select-Object InterfaceAlias,ServerAddresses"],
    "curl": ["(Invoke-WebRequest http://localhost/ -UseBasicParsing -TimeoutSec 10).StatusCode"],
    "fw": ["Get-NetFirewallProfile | Select-Object Name,Enabled", "Get-NetFirewallRule -Enabled True -Direction Inbound | Select-Object -First 20 DisplayName,Action"],
    "wifi": ["netsh wlan show interfaces", "netsh wlan show profiles"],
    "docker": ["docker ps -a --format '{{.Names}}  {{.Status}}  {{.Image}}'", "docker images"],
    "pkg": ["winget list 2>&1 | Select-Object -First 30", "Get-Package | Select-Object -First 30 Name,Version"],
    "upd": ["Get-HotFix | Sort-Object InstalledOn -Descending | Select-Object -First 10 HotFixID,InstalledOn", "winget upgrade 2>&1 | Select-Object -First 20"],
    "pip": ["python --version; pip --version", "pip list 2>&1 | Select-Object -First 25"],
    "git": ["git status -sb 2>&1 | Select-Object -First 15", "git log --oneline -n 10 2>&1"],
    "cron": ["Get-ScheduledTask | Where-Object {$_.State -ne 'Disabled'} | Select-Object -First 25 TaskName,State,TaskPath", "schtasks /query /fo LIST 2>&1 | Select-Object -First 30"],
    "user": ["Get-LocalUser | Select-Object Name,Enabled,LastLogon", "Get-LocalGroupMember Administrators 2>&1"],
    "time": ["Get-Date; w32tm /query /status 2>&1 | Select-Object -First 10"],
    "host": ["hostname; [System.Net.Dns]::GetHostByName($env:COMPUTERNAME).HostName"],
    "find": ["Get-ChildItem $HOME -Recurse -File -ErrorAction SilentlyContinue | Sort-Object Length -Descending | Select-Object -First 10 FullName,Length"],
    "grep": ["Select-String -Path $HOME\\*.log -Pattern 'error' -ErrorAction SilentlyContinue | Select-Object -First 15"],
    "tail": ["Get-ChildItem $HOME -File | Sort-Object LastWriteTime -Descending | Select-Object -First 15 Name,Length,LastWriteTime"],
    "perm": ["Get-Acl $HOME | Format-List"],
    "usb": ["Get-PnpDevice -PresentOnly | Where-Object {$_.Class -in 'USB','Camera','Image'} | Select-Object Status,Class,FriendlyName"],
    "audio": ["Get-PnpDevice -Class AudioEndpoint -PresentOnly | Select-Object Status,FriendlyName", "Get-CimInstance Win32_SoundDevice | Select-Object Name,Status"],
    "env": ["Get-ChildItem Env: | Sort-Object Name | Select-Object -First 30"],
    "limits": ["Get-Process | Measure-Object | Select-Object Count", "(Get-Process | Measure-Object Handles -Sum).Sum"],
    "gpio": ["Get-CimInstance Win32_SerialPort | Select-Object DeviceID,Name", "[System.IO.Ports.SerialPort]::GetPortNames()"],
    "arduino": ["Get-Process | Where-Object {$_.Name -match 'arduino|lab'} | Select-Object Name,Id", "[System.IO.Ports.SerialPort]::GetPortNames()"],
    "nginx": ["Get-Service *nginx*,*apache*,*w3svc* -ErrorAction SilentlyContinue | Select-Object Name,Status"],
    "db": ["Get-Service *sql*,*postgres*,*mysql*,*mongo*,*redis* -ErrorAction SilentlyContinue | Select-Object Name,Status"],
    "lang": ["Get-WinSystemLocale; Get-Culture"],
    "archive": ["Get-ChildItem $HOME -Include *.zip,*.7z,*.bak -Recurse -ErrorAction SilentlyContinue | Select-Object -First 15 FullName,Length"],
}

# синонимы: основа слова задачи -> слова, которые добавляются для подбора
_SYN = {
    "слыш": "микрофон звук", "слух": "микрофон звук", "голос": "микрофон звук", "громк": "звук",
    "вид": "камер", "зрен": "камер", "камер": "usb камер", "интернет": "ping dns", "сеть": "ping ip",
    "медлен": "тормоз процесс", "лагае": "тормоз процесс", "завис": "процесс", "жив": "uptime",
    "работает": "uptime служб", "включен": "uptime", "перегре": "температура", "горяч": "температура",
    "место": "диск", "свобод": "диск память", "файлы": "найди файл", "папк": "найди файл",
    "ошиб": "журнал", "сбо": "журнал", "упал": "служб журнал", "пароль": "пользователи",
}


_GENERIC = {"прове", "пока", "узна", "смотр", "сдела", "нужн", "можн", "могу", "мозг", "плат", "серве", "пере", "переза",
            "комп", "этот", "напи", "созд", "стат", "сост", "поче", "почт", "скаж", "расск", "дела", "есть", "ещё", "when", "what"}


def _stem(w):
    return w[:5] if re.match(r"^[а-яё]+$", w) else w


def _hit(w, k):
    """Слово задачи w совпало с основой справочника k (k — до 5 букв)."""
    return w[:len(k)] == k or (len(w) < len(k) and k.startswith(w))


def _kw(rx):
    return {_stem(w) for w in re.findall(r"[а-яёa-z]{4,}", rx.lower().replace("\\w", " "))}


_KWS = {c[0]: _kw(c[1] + " " + c[2]) for c in CATALOG}


def _expand(task):
    lo = str(task or "").lower()
    extra = [v for k, v in _SYN.items() if k in lo]
    return lo + (" " + " ".join(extra) if extra else "")


def match(task, limit=3, exclude=()):
    """Ключи намерений по задаче: регулярки + нечёткое совпадение основ слов + синонимы."""
    lo = _expand(task)
    scored = []
    words = {_stem(w) for w in re.findall(r"[а-яёa-z]{4,}", lo)} - _GENERIC
    for idx, (key, rx, _t, _c, _n) in enumerate(CATALOG):
        if key in exclude or key in ("wol", "win"):
            continue
        sc = 3 if re.search(rx, lo) else 0
        sc += sum(1 for k in _KWS.get(key, ()) if any(_hit(w, k) for w in words))
        if sc:
            scored.append((-sc, idx, key))
    scored.sort()
    return [k for _s, _i, k in scored[:limit]]


def plan(task, limit=3, exclude=(), win=False):
    """Команды по тексту задачи -> [(ключ, команда)], до limit намерений.
    win=True — команды PowerShell для Windows (где они есть)."""
    out = []
    by = {c[0]: c for c in CATALOG}
    for key in match(task, limit, exclude):
        cmds = (WIN.get(key) if win else by[key][3]) or []
        for c in cmds[:3]:
            if not c.startswith("#"):
                out.append((key, c))
    return out


def overview(win=False):
    """Общий обзор, когда задача не распознана."""
    if win:
        return [WIN["os"][0], WIN["mem"][0], WIN["disk"][0], WIN["svc"][1]]
    return ["uptime; nproc; free -h; df -h / 2>/dev/null | tail -1",
            "systemctl --failed --no-pager 2>/dev/null | head -15 || echo 'systemd нет (контейнер?)'"]


def reference(max_chars=1800):
    """Шпаргалка для подсказки модели: «намерение: команда»."""
    lines = ["Справочник (читающие команды выполняются сразу):"]
    for key, _rx, title, cmds, _n in CATALOG:
        c = next((x for x in cmds if not x.startswith("#")), "")
        if c:
            lines.append("- %s: %s" % (title, c))
    txt = "\n".join(lines)
    return txt[:max_chars]


# как это говорят люди (в урок для поиска по памяти; в коде по ним ничего не решается)
_EXTRA = {
    "audio": "слышать, слух, услышать, голос, громкость, не слышно, динамики",
    "proc": "лагает, медленно работает, тормозит, завис, что грузит компьютер",
    "ping": "нет интернета, интернет пропал, сеть не работает, нет связи",
    "disk": "свободное место, закончилось место, диск полон, сколько места",
    "temp": "греется, горячий, перегрев, жарко",
    "journal": "что случилось, сбой, упало, ошибки в системе",
    "uptime": "давно работает, перезагружался, когда включали",
    "mem": "не хватает памяти, оперативка, много памяти занято",
    "usb": "камера, видеть, веб-камера, подключённое устройство",
    "net": "ip-адрес, какой адрес, сетевая карта",
    "fw": "закрыт порт, блокирует соединение",
}


def _phrases(rx, key=""):
    """Слова из регулярки намерения — как «так спрашивают» (для поиска по памяти)."""
    ws = re.findall(r"[а-яёa-z][а-яёa-z\-]{2,}", re.sub(r"\\[a-z]|\[\^?[^\]]*\]|\(\?:|[()?*+]", " ", rx.lower()))
    if key in _EXTRA:
        ws += re.findall(r"[а-яёa-z][а-яёa-z\- ]*[а-яёa-z]", _EXTRA[key].replace(",", ";").replace("; ", ";"))
    return ", ".join(dict.fromkeys(w.strip() for w in ws))[:420]


def lessons(win=False):
    """[(заголовок, текст)] — уроки для базы знаний и потока LLM; команды в ```.
    win=True — PowerShell-версии (заголовок «Команды (Windows): …»)."""
    out = []
    for key, rx, title, cmds, note in CATALOG:
        if win:
            cmds = WIN.get(key)
            if not cmds:
                continue
        body = "Задача: %s. Так спрашивают: %s.\nКоманды (%s):\n```%s\n%s\n```\n%s" % (
            title.lower(), _phrases(rx, key), "Windows PowerShell" if win else "Linux",
            "powershell" if win else "bash", "\n".join(cmds), (note + ".") if (note and not win) else "")
        out.append(("Команды%s: %s" % (" (Windows)" if win else "", title), body))
    return out
