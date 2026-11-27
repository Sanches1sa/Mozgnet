#!/bin/bash
# ===========================================================================
# install_autostart.sh — автозапуск neuro_12 на Arduino UNO Q без App Lab.
#
# Запускать НА САМОЙ ПЛАТЕ (по SSH), с правами root:
#   sudo bash install_autostart.sh watchdog   # автозапуск + слежение (советую)
#   sudo bash install_autostart.sh simple     # только старт при загрузке
#   sudo bash install_autostart.sh watchdog /home/arduino/ArduinoApps/neuro_12
#                                             # явный путь к приложению
#
# Скрипт сам находит arduino-app-cli, домашнюю папку пользователя и путь к
# приложению, создаёт /etc/systemd/system/neuro12.service и включает его.
# ===========================================================================
set -e

MODE="${1:-watchdog}"
if [ "$MODE" != "watchdog" ] && [ "$MODE" != "simple" ]; then
  echo "использование: sudo bash install_autostart.sh [watchdog|simple]"; exit 1
fi

# --- кто пользователь и где приложение -------------------------------------
APP_USER="${SUDO_USER:-arduino}"
if ! getent passwd "$APP_USER" >/dev/null; then APP_USER="arduino"; fi
APP_HOME="$(getent passwd "$APP_USER" | cut -d: -f6)"
[ -n "$APP_HOME" ] && [ "$APP_HOME" != "/" ] || APP_HOME="/home/$APP_USER"

APP_DIR=""
# v2.62: путь можно задать вторым аргументом; иначе — папка выше deploy_cli,
# ~/ArduinoApps/neuro_12, либо самая свежая копия neuro_12, которую
# импортировал App Lab (бывает neuro_12_1 и т.п.)
CANDS=("${2:-}" "$(cd "$(dirname "$0")/.." 2>/dev/null && pwd)" "$APP_HOME/ArduinoApps/neuro_12")
for Y in $(ls -t "$APP_HOME"/ArduinoApps/*/app.yaml 2>/dev/null); do
  if grep -q "^name: *neuro_12" "$Y" 2>/dev/null; then CANDS+=("$(dirname "$Y")"); fi
done
CANDS+=("$PWD/.." "$PWD")
for CAND in "${CANDS[@]}"; do
  [ -n "$CAND" ] || continue
  if [ -f "$CAND/python/main.py" ] && [ -f "$CAND/app.yaml" ]; then APP_DIR="$(cd "$CAND" && pwd)"; break; fi
done
if [ -z "$APP_DIR" ]; then
  echo "ОШИБКА: не нашёл приложение neuro_12 (ожидаю $APP_HOME/ArduinoApps/neuro_12"
  echo "или папку выше deploy_cli с файлами app.yaml и python/main.py)"; exit 1
fi

CLI="$(command -v arduino-app-cli || true)"
if [ -z "$CLI" ]; then
  echo "ОШИБКА: arduino-app-cli не найден на плате."
  echo "Обнови систему платы через App Lab (Help -> Updates) и попробуй снова."; exit 1
fi

echo "пользователь : $APP_USER"
echo "приложение   : $APP_DIR"
echo "cli          : $CLI"
echo "режим        : $MODE"

# --- вариант watchdog: скрипт слежения --------------------------------------
if [ "$MODE" = "watchdog" ]; then
  SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
  # sed — на случай, если файл пришёл с Windows с окончаниями CRLF
  sed 's/\r$//' "$SCRIPT_DIR/neuro12_watchdog.sh" > /usr/local/bin/neuro12_watchdog.sh
  chmod 0755 /usr/local/bin/neuro12_watchdog.sh
fi

# --- v2.71: право сторожу перезапускать службу моста MPU<->MCU -------------
# (если после перезапуска скетч не оживает — сторож перезапускает мост)
if [ "$MODE" = "watchdog" ]; then
  SCTL="$(command -v systemctl || echo /usr/bin/systemctl)"
  SUD=/etc/sudoers.d/neuro12-watchdog
  echo "$APP_USER ALL=(root) NOPASSWD: $SCTL restart arduino-router, $SCTL restart arduino-router.service" > "$SUD.tmp"
  chmod 0440 "$SUD.tmp"
  if command -v visudo >/dev/null 2>&1 && visudo -cf "$SUD.tmp" >/dev/null; then
    mv -f "$SUD.tmp" "$SUD"; echo "право на перезапуск моста: $SUD"
  elif ! command -v visudo >/dev/null 2>&1; then
    mv -f "$SUD.tmp" "$SUD"; echo "право на перезапуск моста: $SUD (visudo нет — без проверки)"
  else
    rm -f "$SUD.tmp"; echo "ВНИМАНИЕ: правило sudo не прошло проверку — глубокий перезапуск будет без моста"
  fi
fi

# --- юнит -------------------------------------------------------------------
UNIT=/etc/systemd/system/neuro12.service
{
  echo "[Unit]"
  echo "Description=neuro_12 (Star Gate) via arduino-app-cli"
  echo "After=network-online.target"
  echo "Wants=network-online.target"
  echo ""
  echo "[Service]"
  echo "User=$APP_USER"
  echo "Environment=HOME=$APP_HOME"
  if [ "$MODE" = "watchdog" ]; then
    echo "Type=simple"
    echo "ExecStart=/usr/local/bin/neuro12_watchdog.sh $CLI $APP_DIR"
    echo "Restart=always"
    echo "RestartSec=20"
  else
    echo "Type=oneshot"
    echo "RemainAfterExit=yes"
    echo "ExecStart=$CLI app start $APP_DIR"
    echo "ExecStop=$CLI app stop $APP_DIR"
  fi
  echo ""
  echo "[Install]"
  echo "WantedBy=multi-user.target"
} > "$UNIT"

systemctl daemon-reload
systemctl enable neuro12.service

echo
echo "Готово. Управление:"
echo "  sudo systemctl start neuro12    # запустить сейчас"
echo "  systemctl status neuro12        # состояние"
if [ "$MODE" = "watchdog" ]; then
  echo "  journalctl -u neuro12 -f        # логи слежения"
else
  echo "  arduino-app-cli app logs $APP_DIR   # логи приложения"
fi
echo "  sudo systemctl stop neuro12     # остановить"
echo "  sudo systemctl disable --now neuro12   # убрать автозапуск"
echo
echo "Веб-интерфейс после старта: http://<IP-платы>:7000"
if [ "$MODE" = "watchdog" ]; then
  echo "Сторож проверяет порт 7000 каждые 15 с; 3 неудачи подряд (или зависание"
  echo "потока связи > 10 мин) — перезапуск приложения: app stop + app start."
  echo "Приостановить сторожа, не останавливая службу: touch $APP_DIR/.watchdog_pause"
  echo "История перезапусков: $APP_DIR/neuro12_watchdog.json"
fi
