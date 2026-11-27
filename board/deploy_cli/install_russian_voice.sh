#!/usr/bin/env bash
# Run in the environment that executes python/main.py (or on the cloud for cloud synthesis).
set -euo pipefail
MODE="${1:---check}"
case "$MODE" in
 --install)
  if [ "$(id -u)" -ne 0 ]; then exec sudo bash "$0" --install; fi
  command -v apt-get >/dev/null || { echo 'Нужен Debian/Ubuntu либо установка espeak-ng и ALSA средствами вашей системы'; exit 1; }
  apt-get update
  apt-get install -y espeak-ng alsa-utils
  ;;
 --check|--test) ;;
 *) echo 'Использование: bash install_russian_voice.sh --check|--install|--test'; exit 1;;
esac
ENGINE="$(command -v espeak-ng || command -v espeak || true)"
if [ -z "$ENGINE" ]; then echo 'Нет espeak-ng: выполните --install в среде приложения или используйте /api/tts/ru облака.'; exit 1; fi
"$ENGINE" --voices=ru
if command -v aplay >/dev/null; then aplay -l || true; else echo 'Нет aplay: голос можно синтезировать, но вывод на динамик платы недоступен.'; fi
if [ ! -d /dev/snd ]; then echo 'Нет /dev/snd: проверьте USB-аудиоустройство и доступ из контейнера App Lab.'; fi
if [ "$MODE" = '--test' ]; then
 FILE="$(mktemp --suffix=.wav)"
 trap 'rm -f "$FILE"' EXIT
 printf '%s\n' 'Проверка русской речи. Мозг готов к работе.' | "$ENGINE" -v ru -s 150 -w "$FILE" --stdin
 aplay "$FILE"
fi
