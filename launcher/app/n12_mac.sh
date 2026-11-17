#!/bin/bash
# n12_mac.sh v3.154 — вращение MAC платы («виртуальное тело»). Выполняется НА ПЛАТЕ.
#   bash n12_mac.sh rotate  [iface]  — повернуть на один шаг
#   bash n12_mac.sh restore [iface]  — вернуть заводской MAC
#   bash n12_mac.sh show    [iface]  — только показать
#   bash n12_mac.sh spin <сек>        — v3.154: быстрый спин ПРЯМО НА ПЛАТЕ:
#        цикл переворачивает MAC каждые <сек> (0 — так быстро, как успевает
#        переподключаться сеть); повторный spin меняет только промежуток
#   bash n12_mac.sh stop              — остановить спин (плата вернётся на прямой адрес)
#
# Вращение — ПЕРЕВОРОТ октетов (v3.152.1, по правилу владельца):
#   6-й становится 1-м, 5-й — 2-м, 4-й меняется местами с 3-м
#   aa:bb:cc:dd:ee:ff -> ff:ee:dd:cc:bb:aa
# v3.152.2 — «оба 62»: если последний октет нечётный (например 61), после
# переворота он стал бы первым, а нечётный первый октет — multicast, такой MAC
# ядро не примет. Поэтому он правится ОДИНАКОВО в обоих адресах (61 -> 62:
# младший бит 0, следующий 1), и прямой и перевёрнутый адреса — точные зеркала:
#   прямой      a4:cf:12:34:56:62
#   перевёрнутый 62:56:34:12:cf:a4
# Повороты чередуют прямой и перевёрнутый; restore возвращает ЗАВОДСКОЙ адрес.
#
# Смена идёт через NetworkManager (cloned-mac-address) в ОТОРВАННОМ процессе:
# интерфейс переподключается, SSH на это время рвётся, а плата может получить
# новый IP от роутера — лаунчер найдёт её сканером.
export LC_ALL=C
SELF="$(cd "$(dirname "$0")" && pwd)/$(basename "$0")"
ST="${N12_ST:-$(dirname "$SELF")/.n12_mac}"   # v3.158: цикл спина живёт в копии — папку задаём явно
mkdir -p "$ST" 2>/dev/null
MODE="${1:-rotate}"; IFACE="${2:-}"

spin_alive() { local p; p=$(cat "$ST/spin.pid" 2>/dev/null); [ -n "$p" ] && kill -0 "$p" 2>/dev/null; }
now() { date +%s.%N; }

case "$MODE" in
version) echo "N12MAC:3.158"; exit 0;;
spin)
  GAP="${2:-0}"; WIN="${3:-4}"; IFACE=""
  echo "$GAP" > "$ST/spin.gap"
  echo "$WIN" > "$ST/spin.win"
  if spin_alive; then echo "SPIN:on"; echo "OK:spin-gap $GAP win $WIN"; exit 0; fi
  # права проверяем заранее: в цикле спросить пароль некому
  if [ "$(id -u)" -ne 0 ] && ! sudo -n true 2>/dev/null && \
     ! nmcli -t -f PERMISSION,VALUE general permissions 2>/dev/null | grep -q 'settings.modify.system:yes'; then
    echo "ERR:SUDO"; exit 3
  fi
  rm -f "$ST/spin.stop"
  # v3.158: цикл работает со СВОЕЙ копией скрипта — обновление n12_mac.sh
  # лаунчером не ломает уже идущий цикл (bash читает файл по ходу работы)
  cp "$SELF" "$ST/spinloop.sh"
  N12_ST="$ST" setsid nohup bash "$ST/spinloop.sh" spinloop </dev/null >/dev/null 2>&1 &
  sleep 0.3; echo "SPIN:on"; echo "OK:spin $GAP"; exit 0;;
stop)
  touch "$ST/spin.stop"
  if spin_alive; then echo "SPIN:stopping"; else echo "SPIN:off"; rm -f "$ST/spin.stop"; fi
  echo "OK:stop"; exit 0;;
spinloop)
  echo $$ > "$ST/spin.pid"
  N=0; FAILS=0; T0=$(now); MSG=""
  while [ ! -e "$ST/spin.stop" ]; do
    GAP=$(cat "$ST/spin.gap" 2>/dev/null); GAP=${GAP:-0}
    A=$(now)
    if N12_SYNC=1 bash "$SELF" rotate >"$ST/spin.last" 2>&1 && grep -q 'OK:started' "$ST/spin.last" && grep -q '^RC:0' "$ST/last.log"; then
      N=$((N+1)); FAILS=0
    else
      FAILS=$((FAILS+1)); sleep 1
      if [ $FAILS -ge 5 ]; then MSG="остановлен: 5 неудачных поворотов подряд ($(tail -1 "$ST/last.log" 2>/dev/null))"; break; fi
    fi
    B=$(now)
    printf 'COUNT:%s\nLASTSEC:%s\nAVGSEC:%s\nGAPSEC:%s\nIP:%s\n' "$N" \
      "$(awk "BEGIN{printf \"%.2f\", $B-$A}")" "$(awk "BEGIN{printf \"%.2f\", ($N>0)?($B-$T0)/$N:0}")" "$GAP" \
      "$(hostname -I 2>/dev/null | awk '{print $1}')" > "$ST/spin.status"
    # v3.158: ОКНО СВЯЗИ — после смены адреса плата минимум WIN секунд в сети:
    # без него на предельной скорости до неё не достучаться (ни сменить спин,
    # ни остановить). Спин медленнее предела (спин 2 = смена раз в 15 с) окно
    # не замедляет: пауза до следующей смены и так длиннее.
    WIN=$(cat "$ST/spin.win" 2>/dev/null); WIN=${WIN:-4}
    REST=$(awk "BEGIN{r=$GAP-($B-$A); if (r<$WIN) r=$WIN; print (r>0)?r:0}")
    [ "$REST" != "0" ] && sleep "$REST"
  done
  # остановка: вернуться на ПРЯМОЙ адрес
  [ "$(cat "$ST/step_$(cat "$ST/iface" 2>/dev/null)" 2>/dev/null)" = "1" ] && N12_SYNC=1 bash "$SELF" rotate >/dev/null 2>&1
  echo "STOPPED:${MSG:-по команде}" >> "$ST/spin.status"
  rm -f "$ST/spin.pid" "$ST/spin.stop"
  exit 0;;
esac

# интерфейс запоминается: во время переподключения маршрута по умолчанию нет
[ -z "$IFACE" ] && [ -s "$ST/iface" ] && [ -e "/sys/class/net/$(cat "$ST/iface")" ] && IFACE=$(cat "$ST/iface")

if [ -z "$IFACE" ]; then
  IFACE=$(ip -4 route show default 2>/dev/null | awk '{for(i=1;i<NF;i++) if($i=="dev"){print $(i+1); exit}}')
fi
if [ -z "$IFACE" ]; then
  IFACE=$(ip -o link show | awk -F': ' '{sub(/@.*/,"",$2)} $2!="lo" && $2 !~ /^(docker|br-|veth|virbr|can|usb)/{print $2; exit}')
fi
[ -z "$IFACE" ] && { echo "ERR:нет сетевого интерфейса"; exit 1; }
CUR=$(cat "/sys/class/net/$IFACE/address" 2>/dev/null | tr 'A-F' 'a-f')
[ -z "$CUR" ] && { echo "ERR:не прочитал MAC интерфейса $IFACE"; exit 1; }
echo "$IFACE" > "$ST/iface"

# родной адрес — запоминаем один раз (заводской, если драйвер его отдаёт)
OF="$ST/orig_$IFACE"; SF="$ST/step_$IFACE"
if [ ! -s "$OF" ]; then
  P=$(ethtool -P "$IFACE" 2>/dev/null | awk '{print tolower($NF)}')
  case "$P" in ""|00:00:00:00:00:00) P="$CUR";; esac
  echo "$P" > "$OF"
fi
ORIG=$(cat "$OF"); STEP=$(cat "$SF" 2>/dev/null); STEP=${STEP:-0}

echo "IFACE:$IFACE"
echo "WAS:$CUR"
echo "ORIG:$ORIG"

# прямой адрес: заводской, последний октет — чётный «локальный» (61 -> 62)
IFS=':' read -r -a O <<< "$ORIG"
if [ $(( 0x${O[5]} & 1 )) -eq 1 ]; then
  O[5]=$(printf '%02x' $(( (0x${O[5]} & 0xfe) | 0x02 )))
fi
BASE=$(IFS=:; echo "${O[*]}")
echo "BASE:$BASE"

if [ "$MODE" = "show" ]; then
  if [ "$CUR" = "$ORIG" ]; then STEP=R; fi
  echo "STEP:$STEP"; echo "NOW:$CUR"
  if spin_alive; then echo "SPIN:on"; else echo "SPIN:off"; fi
  [ -s "$ST/spin.status" ] && sed 's/^/SP_/' "$ST/spin.status"
  exit 0
fi

if [ "$MODE" = "restore" ]; then
  K=R; NEW="$ORIG"
else
  [ "$STEP" = "R" ] && STEP=0
  K=$(( (STEP + 1) % 2 ))
  if [ "$K" -eq 0 ]; then
    NEW="$BASE"
  else
    R=("${O[5]}" "${O[4]}" "${O[3]}" "${O[2]}" "${O[1]}" "${O[0]}")
    NEW=$(IFS=:; echo "${R[*]}")
    if [ "$NEW" = "$BASE" ]; then   # адрес-палиндром: переворот ничего не меняет
      R[4]=$(printf '%02x' $(( 0x${R[4]} ^ 0x01 ))); NEW=$(IFS=:; echo "${R[*]}")
    fi
  fi
fi
echo "STEP:$K"
echo "NEW:$NEW"

# права: root, sudo без пароля или права NetworkManager
if [ "$(id -u)" -eq 0 ]; then SU=""; elif sudo -n true 2>/dev/null; then SU="sudo -n"; else SU="-"; fi

LOG="$ST/last.log"
CON=""; NMT=""
if command -v nmcli >/dev/null 2>&1; then
  CON=$(nmcli -g GENERAL.CONNECTION device show "$IFACE" 2>/dev/null | head -1)
  case "$(nmcli -g GENERAL.TYPE device show "$IFACE" 2>/dev/null | head -1)" in
    wifi) NMT="802-11-wireless.cloned-mac-address";;
    ethernet) NMT="802-3-ethernet.cloned-mac-address";;
  esac
fi

if [ -n "$CON" ] && [ -n "$NMT" ]; then
  VAL="$NEW"; [ "$NEW" = "$ORIG" ] && VAL="permanent"
  if nmcli connection modify "$CON" "$NMT" "$VAL" 2>/dev/null; then RUN=""
  elif [ "$SU" != "-" ] && $SU nmcli connection modify "$CON" "$NMT" "$VAL" 2>/dev/null; then RUN="$SU"
  else echo "ERR:SUDO"; exit 3; fi
  echo "VIA:NetworkManager «$CON»"
  if [ -n "$N12_SYNC" ]; then   # спин: ждём, пока сеть поднимется, — это и есть предел скорости
    $RUN nmcli -w 20 connection up "$CON" >"$LOG" 2>&1; echo "RC:$?" >>"$LOG"
  else
    ( sleep 2; $RUN nmcli connection up "$CON" >"$LOG" 2>&1; echo "RC:$?" >>"$LOG" ) </dev/null >/dev/null 2>&1 &
  fi
else
  [ "$SU" = "-" ] && { echo "ERR:SUDO"; exit 3; }
  echo "VIA:ip link"
  # интерфейс поднимается в любом случае, даже если адрес не принят
  iplink() { $SU ip link set dev "$IFACE" down; $SU ip link set dev "$IFACE" address "$NEW"; RC=$?
    $SU ip link set dev "$IFACE" up; echo "RC:$RC"; }
  if [ -n "$N12_SYNC" ]; then iplink >"$LOG" 2>&1
  else ( sleep 2; iplink >"$LOG" 2>&1 ) </dev/null >/dev/null 2>&1 &
  fi
fi

[ "$K" = "R" ] && echo 0 > "$SF" || echo "$K" > "$SF"
echo "$NEW" > "$ST/current"; echo "$NEW" > /tmp/n12_mac_current 2>/dev/null
echo "$IFACE" > /tmp/n12_mac_iface 2>/dev/null
[ "$(id -u)" -eq 0 ] && chmod -R a+rwX "$ST" 2>/dev/null
echo "OK:started"
