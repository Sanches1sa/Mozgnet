#!/usr/bin/env bash
# uninstall-linux.sh — удаление лаунчера «Star Gate» (Linux). Облако и его память
# (data/) удаляются ТОЛЬКО если ответить «да» (или ключ --with-cloud).
DATA="${XDG_DATA_HOME:-$HOME/.local/share}"
CLOUD="$DATA/neuro12-cloud"
WITH=0; [ "${1:-}" = "--with-cloud" ] && WITH=1
pkill -f "$CLOUD/launcher/neuro12-start" 2>/dev/null
rm -f "$HOME/.local/bin/neuro12-start" "$DATA/applications/neuro12-start.desktop" \
      "$DATA/icons/hicolor/256x256/apps/neuro12.png"
for d in "$(xdg-user-dir DESKTOP 2>/dev/null)" "$HOME/Рабочий стол" "$HOME/Desktop"; do
  [ -n "$d" ] && rm -f "$d/neuro12-start.desktop"
done
rm -rf "$DATA/neuro12-launcher"
echo "[OK] лаунчер удалён (ярлыки, команда, настройки и профиль окна)"
if [ $WITH -eq 0 ] && [ -t 0 ] && [ -d "$CLOUD/data" ]; then
  printf 'Удалить и облако ВМЕСТЕ С ПАМЯТЬЮ (%s/data)? [д/Н]: ' "$CLOUD"; read -r a
  case "$a" in [yYдД]*) WITH=1 ;; esac
fi
if [ $WITH -eq 1 ]; then
  systemctl --user disable --now neuro12-cloud >/dev/null 2>&1
  rm -f "$HOME/.config/systemd/user/neuro12-cloud.service" "$HOME/.config/autostart/neuro12-cloud.desktop"
  pkill -f "$CLOUD/server/app.py" 2>/dev/null
  rm -rf "$CLOUD"
  echo "[OK] облако удалено"
else
  rm -rf "$CLOUD/launcher"
  echo "[OK] облако оставлено: $CLOUD"
fi
