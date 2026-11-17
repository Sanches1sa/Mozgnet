#!/bin/bash
# ===========================================================================
# push_and_run.sh — скопировать neuro_12 на Arduino UNO Q и запустить без
# App Lab (официальный путь через arduino-app-cli). Выполняется С КОМПЬЮТЕРА.
#
# Использование:
#   bash push_and_run.sh arduino@<IP-платы> [путь/к/папке/neuro_12]
#
# Если второй аргумент не задан, берётся папка neuro_12 рядом с deploy_cli
# (то есть распакованный zip как есть).
# ===========================================================================
set -e

TARGET="${1:?использование: push_and_run.sh arduino@<IP-платы> [путь/к/neuro_12]}"
HERE="$(cd "$(dirname "$0")" && pwd)"
SRC="${2:-$(dirname "$HERE")/neuro_12}"

if [ ! -f "$SRC/python/main.py" ] || [ ! -f "$SRC/app.yaml" ]; then
  echo "ОШИБКА: в $SRC нет app.yaml / python/main.py — укажи путь вторым аргументом"
  exit 1
fi

echo "==> папка приложения : $SRC"
echo "==> плата            : $TARGET"
echo "==> проверка связи..."
ssh "$TARGET" "echo ok"

echo "==> создаю ~/ArduinoApps на плате"
ssh "$TARGET" 'mkdir -p ~/ArduinoApps'

echo "==> копирую приложение (scp)..."
scp -r "$SRC" "$TARGET:ArduinoApps/"

echo "==> запускаю на плате..."
ssh "$TARGET" 'arduino-app-cli app start ~/ArduinoApps/neuro_12'

echo
echo "Готово. Первый старт может идти несколько минут (сборка скетча)."
echo "Логи приложения : ssh $TARGET 'arduino-app-cli app logs ~/ArduinoApps/neuro_12'"
echo "Веб-интерфейс   : http://<IP-платы>:7000"
echo "Остановить      : ssh $TARGET 'arduino-app-cli app stop ~/ArduinoApps/neuro_12'"
echo "Автозапуск      : см. deploy_cli/install_autostart.sh (запуск на плате с sudo)"
