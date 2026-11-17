#!/usr/bin/env bash
# uninstall.sh — удалить neuro12-cloud (данные можно оставить)
# Использование: sudo bash uninstall.sh [--purge]   (--purge — и данные тоже)
set -e
C_Y='\033[0;33m'; C_0='\033[0m'
DIR="/opt/neuro12-cloud"
PURGE=0
[ "$1" = "--purge" ] && PURGE=1

printf '%s==> Остановка сервиса…%s\n' "$C_Y" "$C_0"
if command -v systemctl >/dev/null 2>&1; then
  sudo systemctl disable --now neuro12-cloud 2>/dev/null || true
  sudo rm -f /etc/systemd/system/neuro12-cloud.service
  sudo systemctl daemon-reload 2>/dev/null || true
fi

if [ "$PURGE" -eq 1 ]; then
  printf '%s==> Удаление каталога с данными%s\n' "$C_Y" "$C_0"
  sudo rm -rf "$DIR"
  echo "    $DIR удалён полностью (память плат стёрта)."
else
  echo "    Код удалён, но $DIR/data сохранён (память плат цела)."
  echo "    Чтобы стереть и данные: sudo bash uninstall.sh --purge"
fi
echo "Готово."
