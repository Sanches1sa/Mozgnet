#!/usr/bin/env bash
# n12_mac_pc.sh v3.159 — зеркало MAC на компьютере с Linux (РЕД ОС, Ubuntu).
# Запускается лаунчером через pkexec (окно пароля администратора).
# Аргументы: <файл ответа> <IP компьютера в сети платы> <mirror|restore> <MAC>
# Адаптер выбирается по IP компьютера. Смена — через NetworkManager
# (cloned-mac-address: адрес переживает переподключение), без NetworkManager —
# через ip link. Ответ одной строкой: OK|адаптер|старый|новый или ERR|текст.
OUT="$1"; PCIP="$2"; MODE="$3"; MAC="$4"
err() { echo "ERR|$*" > "$OUT"; exit 0; }
IF=$(ip -o -4 addr show 2>/dev/null | awk -v ip="$PCIP" '{split($4,a,"/"); if (a[1]==ip) {print $2; exit}}')
[ -n "$IF" ] || err "нет адаптера с адресом $PCIP"
OLD=$(cat "/sys/class/net/$IF/address" 2>/dev/null)
CON=""
if command -v nmcli >/dev/null 2>&1; then
  CON=$(nmcli -g GENERAL.CONNECTION device show "$IF" 2>/dev/null | head -1)
fi
if [ -n "$CON" ] && [ "$CON" != "--" ]; then
  TYPE=$(nmcli -g connection.type connection show "$CON" 2>/dev/null)
  case "$TYPE" in
    802-11-wireless|wifi) KEY=802-11-wireless.cloned-mac-address ;;
    *) KEY=802-3-ethernet.cloned-mac-address ;;
  esac
  if [ "$MODE" = "restore" ]; then VAL=""; else VAL="$MAC"; fi
  nmcli connection modify "$CON" "$KEY" "$VAL" 2>/tmp/n12_nm_err.$$ || { E=$(cat /tmp/n12_nm_err.$$); rm -f /tmp/n12_nm_err.$$; err "NetworkManager: $E"; }
  rm -f /tmp/n12_nm_err.$$
  nmcli -w 40 connection up "$CON" >/dev/null 2>&1 || true
else
  if [ "$MODE" = "restore" ]; then
    MAC=$(ethtool -P "$IF" 2>/dev/null | awk '{print $NF}')
    [ -n "$MAC" ] || err "не узнал заводской MAC $IF (нет ethtool)"
  fi
  ip link set dev "$IF" down || err "ip link down $IF"
  ip link set dev "$IF" address "$MAC" 2>/dev/null
  ip link set dev "$IF" up
fi
sleep 4
NOW=$(cat "/sys/class/net/$IF/address" 2>/dev/null)
echo "OK|$IF${CON:+ ($CON)}|$OLD|$NOW" > "$OUT"
