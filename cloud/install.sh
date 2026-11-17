#!/usr/bin/env bash
# ============================================================================
#  install.sh — neuro12-cloud v3.0 · ОБЛАЧНАЯ ПЛАТФОРМА «МОЗГ-12»
#  ОДИН СКРИПТ СТАВИТ ВСЁ. ЗАВИСИМОСТЕЙ НЕТ: сервер работает на чистом
#  Python 3 (stdlib) — pip/venv НЕ НУЖНЫ, ставиться можно даже офлайн.
#
#  Запуск на VPS в папке распакованного пакета:
#      unzip -o neuro12-cloud-3.0.0.zip && cd neuro12-cloud
#      sudo bash install.sh
#
#  Скрипт сам:
#    · проверит python3 (>= 3.8);
#    · скопирует платформу в /opt/neuro12-cloud;
#    · создаст пользователя neuro12 и сгенерирует токен платы;
#    · поставит systemd-сервис (автозапуск, автоперезапуск);
#    · установит ПАРОЛЬ веб-панели (сгенерирует или возьмёт --password);
#    · откроет порт в файрволе (если есть ufw/firewall-cmd);
#    · проверит здоровье и в конце ПЕЧАТАЕТ ГОТОВУЮ ПАРУ:
#      адрес облака + токен — их надо вписать в настройках платы neuro_12.
#
#  Опции: --port 8077 | --dir /opt/neuro12-cloud | --user neuro12
#         --token XXX (свой токен) | --password YYY (пароль панели)
#         --reset-password (сменить пароль на новый сгенерированный)
#         --no-systemd | --no-start | --yes
#         -h, --help
# ============================================================================
set -e

# ------------------------------- вывод --------------------------------------
if [ -t 1 ]; then
  C_G='\033[0;32m'; C_Y='\033[0;33m'; C_R='\033[0;31m'; C_B='\033[0;94m'
  C_0='\033[0m'
else
  C_G=''; C_Y=''; C_R=''; C_B=''; C_0=''
fi
say()  { printf '\n%s==> %s%s\n' "$C_B" "$*" "$C_0"; }
ok()   { printf '  %s[OK]%s %s\n'  "$C_G" "$C_0" "$*"; }
warn() { printf '  %s[!!]%s %s\n'  "$C_Y" "$C_0" "$*"; }
die()  { printf '  %s[XX]%s %s\n'  "$C_R" "$C_0" "$*" >&2; exit 1; }

# --------------------------- значения по умолчанию --------------------------
PKG_DIR="$(cd "$(dirname "$0")" && pwd)"
DIR="/opt/neuro12-cloud"
RUN_USER="neuro12"
PORT_SET=""
PORT="8077"
TOKEN=""
PASSWORD=""
RESET_PW=0
NO_START=0
NO_SYSTEMD=0
QUIET=0

usage() {
  cat <<'HELP'
Использование: sudo bash install.sh [опции]

  --port N         порт платформы (по умолчанию 8077)
  --dir ПУТЬ       каталог установки (по умолчанию /opt/neuro12-cloud)
  --user ИМЯ       пользователь сервиса (по умолчанию neuro12)
  --token TOKEN    свой токен платы вместо сгенерированного
  --password PW    свой пароль веб-панели вместо сгенерированного
  --reset-password сменить пароль панели даже если он уже был
  --no-systemd     без systemd (запуск вручную ./start.sh; работает без root)
  --no-start       установить, но не запускать
  --yes, -y        без вопросов (для автоустановки по ssh)
  -h, --help       эта справка

HELP
}

while [ $# -gt 0 ]; do
  case "$1" in
    --port)        PORT_SET="$2"; shift 2 ;;
    --dir)         DIR="$2"; shift 2 ;;
    --user)        RUN_USER="$2"; shift 2 ;;
    --token)       TOKEN="$2"; shift 2 ;;
    --password)    PASSWORD="$2"; RESET_PW=1; shift 2 ;;
    --reset-password) RESET_PW=1; shift ;;
    --no-start)    NO_START=1; shift ;;
    --no-systemd)  NO_SYSTEMD=1; shift ;;
    --yes|-y)      QUIET=1; shift ;;
    -h|--help)     usage; exit 0 ;;
    *) usage >&2; die "неизвестная опция: $1" ;;
  esac
done
[ -n "$PORT_SET" ] && PORT="$PORT_SET"

SUDO=""
if [ "$(id -u)" -ne 0 ]; then
  if command -v sudo >/dev/null 2>&1; then SUDO="sudo"; else NO_SYSTEMD=1; fi
fi

# ------------------------------- проверки -----------------------------------
say "ПРОВЕРКА ОКРУЖЕНИЯ"
PY=""
for c in python3 python; do
  if command -v "$c" >/dev/null 2>&1; then PY="$c"; break; fi
done
[ -n "$PY" ] || die "не найден python3. Поставь его: apt install python3"
PYVER="$($PY -c 'import sys;print("%d.%d"%sys.version_info[:2])')"
ok "python3 $PYVER: $PY"
$PY - "$PYVER" <<'EOF' || die "нужен python3 >= 3.8"
import sys
v = sys.argv[1].split(".")
sys.exit(0 if (int(v[0]), int(v[1])) >= (3, 8) else 1)
EOF
for f in server/app.py web/index.html; do
  [ -f "$PKG_DIR/$f" ] || die "не найден $f — запускай из папки пакета"
done
ok "файлы пакета на месте"

# ------------------------------ python-тест ---------------------------------
say "ПРОВЕРКА ЯДРА (импорт модулей)"
cd "$PKG_DIR"
NEURO_DATA="$(mktemp -d)" $PY - <<'EOF' || die "ядро платформы не стартует"
import os, sys
sys.path.insert(0, os.path.join(os.getcwd(), "server"))
import store, skills, brain, api_board, api_ui   # noqa
print("  [OK] все модули импортируются, mozgnet готов")
EOF

# ------------------------------- установка ----------------------------------
if [ "$NO_SYSTEMD" -eq 0 ]; then
  say "УСТАНОВКА В $DIR"
  $SUDO mkdir -p "$DIR"
  $SUDO cp -r "$PKG_DIR/server" "$PKG_DIR/web" "$PKG_DIR/VERSION" \
      "$PKG_DIR/README.md" "$DIR/" 2>/dev/null || \
      $SUDO cp -r "$PKG_DIR/server" "$PKG_DIR/web" "$DIR/"
  $SUDO mkdir -p "$DIR/data"

  if ! id -u "$RUN_USER" >/dev/null 2>&1; then
    if id -u "$SUDO_USER" >/dev/null 2>&1 && [ "$SUDO_USER" != "root" ]; then
      RUN_USER="$SUDO_USER"
      warn "пользователь neuro12 не создаю — сервис будет работать от $RUN_USER"
    else
      $SUDO useradd --system --home "$DIR" --shell /usr/sbin/nologin \
        "$RUN_USER" 2>/dev/null || RUN_USER="root"
      ok "создан пользователь $RUN_USER"
    fi
  fi
  $SUDO chown -R "$RUN_USER": "$DIR"
else
  say "РЕЖИМ БЕЗ SYSTEMD: платформа остаётся в $PKG_DIR"
  DIR="$PKG_DIR"
  RUN_USER="$(id -un)"
  mkdir -p "$DIR/data"
fi

# --------------------------------- токен ------------------------------------
if [ -z "$TOKEN" ]; then
  if [ -f "$DIR/data/token.txt" ] && [ -s "$DIR/data/token.txt" ]; then
    TOKEN="$(head -c 64 "$DIR/data/token.txt" | tr -d '[:space:]')"
    ok "токен уже был — оставляю прежний"
  else
    if command -v openssl >/dev/null 2>&1; then
      TOKEN="nz-$(openssl rand -hex 12)"
    else
      TOKEN="nz-$(head -c 12 /dev/urandom | od -An -tx1 | tr -d ' \n')"
    fi
    ok "сгенерирован новый токен платы"
  fi
fi
printf '%s\n' "$TOKEN" > "$DIR/data/token.txt"
chmod 600 "$DIR/data/token.txt" 2>/dev/null || true
if [ "$NO_SYSTEMD" -eq 0 ]; then
  $SUDO chown "$RUN_USER": "$DIR/data/token.txt" 2>/dev/null || true
fi

# ------------------------- пароль веб-панели --------------------------------
say "ПАРОЛЬ ВЕБ-ПАНЕЛИ"
if [ -z "$PASSWORD" ]; then
  if command -v openssl >/dev/null 2>&1; then
    PASSWORD="n12-$(openssl rand -hex 5)"
  else
    PASSWORD="n12-$(head -c 5 /dev/urandom | od -An -tx1 | tr -d ' \n')"
  fi
fi
NEURO_DATA="$DIR/data" N12PW="$PASSWORD" N12RESET="$RESET_PW" $PY - <<'PYEOF'
import os, sys
sys.path.insert(0, os.path.join(os.getcwd(), "server"))
import auth
new_pw = os.environ["N12PW"]
if not auth.has_password():
    auth.set_password(new_pw)
    print("  [OK] создан пароль веб-панели (PBKDF2-хеш в data/auth.json)")
elif os.environ.get("N12RESET") == "1":
    auth.set_password(new_pw)
    print("  [OK] пароль веб-панели СМЕНЕН")
else:
    print("  [OK] пароль уже был — оставляю прежний")
    print("  (сменить: install.sh --password НОВЫЙ или --reset-password)")
PYEOF

# ------------------------------- systemd ------------------------------------
if [ "$NO_SYSTEMD" -eq 0 ] && command -v systemctl >/dev/null 2>&1; then
  say "SYSTEMD-СЕРВИС neuro12-cloud"
  $SUDO tee /etc/systemd/system/neuro12-cloud.service >/dev/null <<EOF
[Unit]
Description=neuro12-cloud — облачная платформа «мозг-12» (без LLM)
After=network.target

[Service]
User=$RUN_USER
WorkingDirectory=$DIR
Environment=NEURO_PORT=$PORT
Environment=NEURO_DATA=$DIR/data
ExecStart=$PY $DIR/server/app.py
Restart=always
RestartSec=4

[Install]
WantedBy=multi-user.target
EOF
  $SUDO systemctl daemon-reload
  if [ "$NO_START" -eq 0 ]; then
    $SUDO systemctl enable --now neuro12-cloud >/dev/null 2>&1
    ok "сервис запущен и добавлен в автозапуск"
  else
    $SUDO systemctl enable neuro12-cloud >/dev/null 2>&1
    ok "сервис установлен (запуск отложен: --no-start)"
  fi
else
  NO_SYSTEMD=1
  warn "systemd недоступен — запускай вручную: $DIR/start.sh"
fi

# ------------------------------- файрвол ------------------------------------
if [ "$NO_SYSTEMD" -eq 0 ] && [ "$NO_START" -eq 0 ]; then
  say "ФАЙРВОЛ"
  if command -v ufw >/dev/null 2>&1 && $SUDO ufw status 2>/dev/null | \
      grep -q "Status: active"; then
    $SUDO ufw allow "$PORT/tcp" >/dev/null 2>&1 && ok "ufw: порт $PORT открыт"
  elif command -v firewall-cmd >/dev/null 2>&1 && \
      $SUDO firewall-cmd --state >/dev/null 2>&1; then
    $SUDO firewall-cmd --permanent --add-port="$PORT/tcp" >/dev/null 2>&1
    $SUDO firewall-cmd --reload >/dev/null 2>&1
    ok "firewalld: порт $PORT открыт"
  else
    ok "файрвол неактивен — ничего делать не нужно"
  fi
fi

# ----------------------------- проверка здоровья ----------------------------
if [ "$NO_START" -eq 0 ]; then
  say "ПРОВЕРКА ЗДОРОВЬЯ"
  sleep 2
  if [ "$NO_SYSTEMD" -eq 1 ]; then
    # без systemd: краткий самотест — поднять, проверить, погасить
    NEURO_PORT="$PORT" NEURO_DATA="$DIR/data" "$PY" "$DIR/server/app.py" \
      >/dev/null 2>&1 &
    SELFPID=$!
    sleep 2
  fi
  HEALTH=""
  for i in 1 2 3 4 5; do
    HEALTH="$(curl -s -m 3 "http://127.0.0.1:$PORT/api/health" 2>/dev/null || true)"
    [ -n "$HEALTH" ] && break
    sleep 2
  done
  if [ "$NO_SYSTEMD" -eq 1 ]; then
    kill "$SELFPID" 2>/dev/null || true
    wait "$SELFPID" 2>/dev/null || true
  fi
  if echo "$HEALTH" | grep -q '"ok"'; then
    ok "облако отвечает: $HEALTH"
  else
    if [ "$NO_SYSTEMD" -eq 0 ]; then
      warn "health-check не прошёл. Лог: $S journalctl -u neuro12-cloud -n 30"
    else
      warn "health-check не прошёл — запусти вручную: $DIR/start.sh и смотри вывод"
    fi
  fi
fi

# ------------------------------- итог ---------------------------------------
IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
[ -n "$IP" ] || IP="127.0.0.1"

printf '\n'
printf '%s╔══════════════════════════════════════════════════════════════════╗%s\n' "$C_G" "$C_0"
printf '%s║%s  neuro12-cloud v3.8 установлен — ОБЛАКО «МОЗГ-12» готово         %s║%s\n' "$C_G" "$C_0" "$C_G" "$C_0"
printf '%s╚══════════════════════════════════════════════════════════════════╝%s\n' "$C_G" "$C_0"
printf '\n'
printf '  %sВЕБ-ПАНЕЛЬ (в стиле ChatGPT):%s    %shttp://%s:%s%s\n' "$C_B" "$C_0" "$C_B" "$IP" "$PORT" "$C_0"
printf '  Пароль веб-панели:               %s%s%s\n' "$C_B" "$PASSWORD" "$C_0"
printf '  Токен платы (X-Mozg-Token):      %s%s%s\n' "$C_B" "$TOKEN" "$C_0"
printf '\n'
printf '  %sПОДКЛЮЧЕНИЕ ПЛАТЫ neuro_12:%s\n' "$C_Y" "$C_0"
printf '   1. На плате открой чат (порт 7000) → панель «⚙ Настройки».\n'
printf '   2. Впиши:  облако (cloud_url) = http://%s:%s\n' "$IP" "$PORT"
printf '              токен            = %s\n' "$TOKEN"
printf '   3. Включи «мозг-почта», «словари» и «платформа» — плата сама\n'
printf '      зарегистрируется, синхронизирует память и словари.\n'
printf '\n'
printf '  Управление сервисом:\n'
if [ "$NO_SYSTEMD" -eq 0 ]; then
  printf '    systemctl status|restart|stop neuro12-cloud\n'
  printf '    журнал:  journalctl -u neuro12-cloud -f\n'
else
  printf '    запуск:  %s/start.sh     остановка: %s/stop.sh\n' "$DIR" "$DIR"
fi
printf '  Данные и память облака: %s/data\n' "$DIR"
printf '  Обновление: распакуй новый пакет поверх и повтори install.sh\n'
printf '              (память в data/ сохраняется).\n'
printf '\n'
printf '  LLM-надстройка ВЫКЛЮЧЕНА: чат отвечает мозгом без нейросетей-гигантов.\n'
printf '  Внешнего учителя (только обучение) можно включить в веб-панели:\n'
printf '  Настройки → Учитель (LLM).\n\n'
