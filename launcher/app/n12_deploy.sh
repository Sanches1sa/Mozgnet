#!/bin/bash
# n12_deploy.sh — выполняется НА ПЛАТЕ лаунчером «Мозг-12» (v3.138):
#   bash ~/ArduinoApps/n12_deploy.sh <cloud_url> <token>
# Новая копия приложения уже лежит в ~/ArduinoApps/neuro_12.new.
# 1) стоп старого приложения; 2) замена папки (память платы переносится);
# 3) адрес облака и мозг-токен — в конфиг; 4) сборка и заливка скетча.
# Битый кэш сборки (core.a «file truncated» — прошлую сборку оборвали или
# кончилось место) чистится, и сборка повторяется один раз сама.
URL="$1"; TOKEN="$2"
A="$HOME/ArduinoApps"; APP="$A/neuro_12"; LOG="$APP/neuro12_start.log"
cd "$A" || { echo "нет папки $A"; exit 1; }
[ -d neuro_12.new ] || { echo "нет новой копии neuro_12.new — копирование не прошло"; exit 1; }

free_mb() { df -Pm "$A" | awk 'NR==2{print $4}'; }

# v3.162.1: СТОРОЖ НА ПАУЗУ. Пока приложение остановлено, сторож видел
# «порт 7000 молчит» и запускал СВОЮ сборку — две сборки писали в один кэш
# и портили объектные файлы (ld: «error adding symbols: bad value»,
# «.symtab local symbol…», «file truncated»). Теперь на время заливки —
# файл паузы сторожа, а чужие сборки останавливаются.
pause_wd() { for d in "$APP" "$A/neuro_12.new"; do [ -d "$d" ] && touch "$d/.watchdog_pause"; done; }
unpause_wd() { rm -f "$APP/.watchdog_pause" "$A/neuro_12.new/.watchdog_pause" 2>/dev/null; true; }
trap unpause_wd EXIT
pause_wd
if pgrep -f "[a]rduino-app-cli app start" >/dev/null 2>&1 || pgrep -f "[a]rduino-cli.*compile" >/dev/null 2>&1; then
  echo "== идёт чужая сборка (сторож/App Lab) — останавливаю её, чтобы не портила кэш"
  pkill -f "[a]rduino-app-cli app start" 2>/dev/null
  pkill -f "[a]rduino-cli.*compile" 2>/dev/null
  sleep 3
fi
echo "== место на плате: $(df -h "$A" | awk 'NR==2{print $4" свободно из "$2}')"

echo "== останавливаю приложение"
arduino-app-cli app stop "$APP" 2>/dev/null || true

rm -rf neuro_12.old
[ -d neuro_12 ] && mv neuro_12 neuro_12.old
mv neuro_12.new neuro_12 || { echo "не удалось заменить папку приложения"; exit 1; }
pause_wd
if [ -d neuro_12.old ]; then          # память платы — из прежней копии
  # v3.152: память платы ПЕРЕКРЫВАЕТ файлы из копии с компьютера (раньше cp -n
  # оставлял файлы с компьютера, и накопленная память платы терялась)
  for f in neuro_12.old/neuro12_*.json neuro_12.old/mozgnet.json neuro_12.old/*.log; do
    [ -f "$f" ] && cp -f "$f" neuro_12/
  done
  for f in neuro_12.old/python/neuro12_*.json neuro_12.old/python/mozgnet.json; do
    [ -f "$f" ] && mkdir -p neuro_12/python && cp -f "$f" neuro_12/python/
  done
  rm -rf neuro_12.old/.cache          # кэш старой сборки — только место занимает
fi
# v3.138: оба кэша сборки — приложения и ОБЩИЙ кэш собранного ядра
# arduino-cli (~/.cache/arduino/cores/…/core.a). Битый core.a живёт именно
# там и копируется в каждую новую сборку — чистим перед каждой заливкой.
clean_cache() {
  rm -rf "$APP/.cache" "${XDG_CACHE_HOME:-$HOME/.cache}/arduino" "$HOME/.cache/arduino" 2>/dev/null
  true
}
clean_cache
echo "== кэши сборки очищены (приложения и ядра arduino)"

if [ "$(free_mb)" -lt 400 ]; then
  echo "== мало места ($(free_mb) МБ) — чищу кэши сборки arduino"
  rm -rf "$HOME/.cache/arduino" "$A"/*/.cache 2>/dev/null
  echo "   теперь свободно: $(free_mb) МБ"
fi

python3 "$A/n12_setcfg.py" "$URL" "$TOKEN" "$APP"

# сторож (если стоит) — обновить его скрипт из новой копии
if [ -f /usr/local/bin/neuro12_watchdog.sh ] && [ -f "$APP/deploy_cli/neuro12_watchdog.sh" ]; then
  if ! sed 's/\r$//' "$APP/deploy_cli/neuro12_watchdog.sh" | cmp -s - /usr/local/bin/neuro12_watchdog.sh; then
    if sed 's/\r$//' "$APP/deploy_cli/neuro12_watchdog.sh" | sudo -n tee /usr/local/bin/neuro12_watchdog.sh >/dev/null 2>&1; then
      echo "== сторож обновлён"
    else
      echo "== сторож старый; обнови его: sudo bash $APP/deploy_cli/install_autostart.sh watchdog"
    fi
  fi
fi

start() {
  echo "===== $(date '+%F %T') app start (лаунчер) =====" >>"$LOG"
  arduino-app-cli app start "$APP" 2>&1 | tee -a "$LOG" | tee /tmp/n12_start.out
  return "${PIPESTATUS[0]}"
}

echo "== сборка и заливка скетча + запуск Python (несколько минут)"
if ! start || grep -qE "bad value|file truncated|symtab|error adding symbols" /tmp/n12_start.out; then
  echo
  echo "== сборка не прошла (или битый объектный файл) — чищу кэши ещё раз и собираю заново"
  pkill -f "[a]rduino-cli.*compile" 2>/dev/null
  clean_cache
  echo "   свободно: $(free_mb) МБ"
  if ! start; then
    echo
    echo "!! сборка снова не прошла. Если выше «No space left» — на плате кончилось место:"
    echo "   df -h ;  du -sh ~/ArduinoApps/* ~/.cache/* 2>/dev/null | sort -h | tail"
    exit 2
  fi
fi
echo "== приложение запущено"
