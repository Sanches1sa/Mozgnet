#!/bin/bash
# n12_cleanup_old.sh — УБРАТЬ С ПЛАТЫ СТАРЫЕ/ЧУЖИЕ ПЛАТФОРМЫ (v2.96.5).
#   Запуск на плате:  sudo bash ~/ArduinoApps/n12_cleanup_old.sh          (сухой прогон — только показать)
#                     sudo bash ~/ArduinoApps/n12_cleanup_old.sh --do     (выполнить)
#                     ... --do --with-node     (ещё и ~/node_modules, 367 МБ)
#                     ... --do --with-old      (ещё и ArduinoApps/neuro_12.old — откат прошлой версии)
# Что делает:
#   1) останавливает и отключает systemd-юниты СТАРОЙ платформы neuro_brain_12
#      (neuro-all, neuro-brain, neuro-chat, neuro-sync(.timer), neuro-yolo) —
#      её процессы (python3 main.py / chat_server.py) ходят на ЧУЖОЕ облако
#      157.22.190.54:8077 и камеру 192.168.1.50;
#   2) папки старой платформы в /home/arduino (neuro_brain_12, deploy, memory,
#      micropython, server, sketch, logs, tools, main.py, chat_server.py, *.conf)
#      СНАЧАЛА пакует в /home/arduino/old_platform_<дата>.tar.gz, потом удаляет;
#   3) выключенный контейнер mozg12 и неиспользуемые образы docker
#      (ei-models-runner ≈970 МБ, influxdb ≈190 МБ) — только если их не использует
#      ни один контейнер;
#   4) ничего из neuro_12 (текущее приложение), arduino-*, системы — не трогает.
set -u
DO=0; NODE=0; OLD=0
for a in "$@"; do case "$a" in --do) DO=1;; --with-node) NODE=1;; --with-old) OLD=1;; esac; done
H=/home/arduino
STAMP=$(date +%Y%m%d-%H%M)
say() { echo "[$([ $DO = 1 ] && echo DO || echo DRY)] $*"; }
run() { say "$*"; [ $DO = 1 ] && eval "$@"; }

echo "== 1. сервисы старой платформы"
UNITS="neuro-all.service neuro-brain.service neuro-chat.service neuro-sync.timer neuro-sync.service neuro-yolo.service"
for u in $UNITS; do
  if systemctl list-unit-files "$u" 2>/dev/null | grep -q "$u"; then
    run "systemctl disable --now $u 2>/dev/null; rm -f /etc/systemd/system/$u"
  fi
done
run "systemctl daemon-reload"
run "pkill -u arduino -f '^/usr/bin/python3 main.py$' ; pkill -u arduino -f 'chat_server.py' ; true"

echo "== 2. папки и файлы старой платформы (сначала в архив)"
OLDITEMS=""
for p in neuro_brain_12 deploy memory micropython server sketch logs tools main.py chat_server.py ip_agent.py cloud.conf; do
  [ -e "$H/$p" ] && OLDITEMS="$OLDITEMS $p"
done
[ $NODE = 1 ] && [ -d "$H/node_modules" ] && OLDITEMS="$OLDITEMS node_modules"
if [ -n "$OLDITEMS" ]; then
  say "архив: $H/old_platform_$STAMP.tar.gz  <-$OLDITEMS"
  if [ $DO = 1 ]; then
    (cd "$H" && tar -czf "old_platform_$STAMP.tar.gz" $OLDITEMS 2>/dev/null) && \
      (cd "$H" && rm -rf $OLDITEMS) && chown arduino:arduino "$H/old_platform_$STAMP.tar.gz"
  fi
else
  say "папок старой платформы не найдено"
fi
if [ $OLD = 1 ] && [ -d "$H/ArduinoApps/neuro_12.old" ]; then
  run "rm -rf $H/ArduinoApps/neuro_12.old"
fi

echo "== 3. docker: контейнер mozg12 и неиспользуемые образы"
docker ps -a --format '{{.Names}}' | grep -q '^mozg12-main-1$' && run "docker rm -f mozg12-main-1"
for img in ghcr.io/arduino/app-bricks/ei-models-runner:0.10.0 influxdb:2.7-alpine; do
  if docker images --format '{{.Repository}}:{{.Tag}}' | grep -qx "$img"; then
    if ! docker ps -a --format '{{.Image}}' | grep -qx "$img"; then run "docker rmi $img"; else say "образ $img используется контейнером — оставлен"; fi
  fi
done

echo "== итог"
df -h / | awk 'NR==2{print "свободно на /:", $4, "из", $2}'
[ $DO = 1 ] || echo "(это был сухой прогон; чтобы выполнить — добавьте --do)"
