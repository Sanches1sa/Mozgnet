#!/bin/bash
# n12_flood_test.sh — перепрошивка (скетч изменён) и таймлайн моста первые 300 с.
A="$HOME/ArduinoApps/neuro_12"
arduino-app-cli app restart "$A" > /tmp/n12_restart.log 2>&1
echo "прошивка/перезапуск завершены $(date +%T): $(grep -c 'restarted successfully' /tmp/n12_restart.log)"
sleep 8
docker cp /tmp/n12_rpc_probe.py neuro_12-main-1:/tmp/
timeout 330 docker exec neuro_12-main-1 /app/.cache/.venv/bin/python /tmp/n12_rpc_probe.py 300 2>&1
echo "== журнал моста (ошибки RPC с метками времени):"
docker logs -t --since 6m neuro_12-main-1 2>/dev/null | tr -d '\000' | grep -aE 'мост:|МОСТ' | head -40
