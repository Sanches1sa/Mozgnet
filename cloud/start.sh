#!/usr/bin/env bash
# start.sh — запуск neuro12-cloud вручную (без systemd; работает и на Windows
# через WSL/Git-Bash, и на обычном Linux). Данные: ./data
# Порт: ./start.sh [порт]  или env NEURO_PORT=...   (по умолчанию 8077)
set -e
cd "$(dirname "$0")"
PORT="${1:-${NEURO_PORT:-8077}}"
export NEURO_PORT="$PORT"
export NEURO_DATA="${NEURO_DATA:-$(pwd)/data}"
PY=""
for c in python3 python; do
  command -v "$c" >/dev/null 2>&1 && PY="$c" && break
done
[ -n "$PY" ] || { echo "python3 не найден"; exit 1; }
mkdir -p "$NEURO_DATA"
echo "==> neuro12-cloud: http://0.0.0.0:$PORT (данные: $NEURO_DATA)"
echo "    Остановка: Ctrl+C"
exec "$PY" server/app.py
