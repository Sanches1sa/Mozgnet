#!/usr/bin/env bash
# stop.sh — остановить фоновый запуск (pkill app.py нейро-облака)
pkill -f "server/app\.py" 2>/dev/null && echo "neuro12-cloud остановлен" \
  || echo "запущенных процессов не найдено"
