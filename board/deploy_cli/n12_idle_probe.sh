#!/bin/bash
# n12_idle_probe.sh — замер RPC к скетчу при ОСТАНОВЛЕННОМ приложении (чистый канал):
# контейнер приложения стоит, скетч на MCU работает; пробник — отдельный контейнер.
# После замера приложение запускается снова (arduino-app-cli app start).
A="$HOME/ArduinoApps/neuro_12"
IMG=$(docker inspect neuro_12-main-1 --format '{{.Config.Image}}' 2>/dev/null)
echo "образ: $IMG"
docker stop -t 15 neuro_12-main-1 >/dev/null 2>&1 && echo "приложение остановлено"
sleep 4
docker run --rm --network none \
  -v /var/run/arduino-router.sock:/var/run/arduino-router.sock \
  -v "$A":/app -v /tmp/n12_rpc_probe.py:/tmp/n12_rpc_probe.py \
  "$IMG" sh -c 'cd /app && timeout 70 /app/.cache/.venv/bin/python /tmp/n12_rpc_probe.py 2' 2>&1 | tail -8
echo "== запускаю приложение обратно"
nohup arduino-app-cli app start "$A" > /tmp/n12_restart.log 2>&1 &
