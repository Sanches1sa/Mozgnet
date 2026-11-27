#!/usr/bin/env bash
# install-ssl-linux.sh — v3.210: SSL/HTTPS для neuro12-cloud на Linux.
#
#   ./install-ssl-linux.sh self-signed [--data DIR] [--name HOST]... [--ip IP]... [--trust]
#   sudo ./install-ssl-linux.sh letsencrypt --domain cloud.example.com --email me@example.com [--data DIR] [--webroot|--standalone]
#   ./install-ssl-linux.sh custom --cert fullchain.pem --key privkey.pem [--data DIR]
#   ./install-ssl-linux.sh status  [--data DIR]
#   ./install-ssl-linux.sh remove  [--data DIR]
#
# Каталог данных (--data) — тот же, что у облака (NEURO_DATA; по умолчанию
# ~/neuro12-cloud-data или ./data рядом со скриптом, если он там есть).
# Сертификат кладётся в DIR/tls/cert.pem + key.pem; облако само поднимает
# HTTPS на порту 8443 (NEURO_TLS_PORT) и подхватывает смену сертификата
# в течение часа — перезапуск не нужен (после первой установки перезапусти
# облако один раз, если оно уже работало).
set -euo pipefail

CMD="${1:-help}"; [ $# -gt 0 ] && shift || true
DATA="${NEURO_DATA:-}"; NAMES=(); IPS=(); DOMAIN=""; EMAIL=""; MODE="standalone"
CERT=""; KEY=""; TRUST=0; DAYS=3650; HPORT="${NEURO_TLS_PORT:-8443}"

say(){ printf '\033[1;32m==>\033[0m %s\n' "$*"; }
warn(){ printf '\033[1;33m!!\033[0m %s\n' "$*" >&2; }
die(){ printf '\033[1;31mОШИБКА:\033[0m %s\n' "$*" >&2; exit 1; }

while [ $# -gt 0 ]; do
  case "$1" in
    --data) DATA="$2"; shift 2;;
    --name) NAMES+=("$2"); shift 2;;
    --ip) IPS+=("$2"); shift 2;;
    --domain) DOMAIN="$2"; shift 2;;
    --email) EMAIL="$2"; shift 2;;
    --webroot) MODE="webroot"; shift;;
    --standalone) MODE="standalone"; shift;;
    --cert) CERT="$2"; shift 2;;
    --key) KEY="$2"; shift 2;;
    --days) DAYS="$2"; shift 2;;
    --trust) TRUST=1; shift;;
    -h|--help) CMD=help; shift;;
    *) die "неизвестный параметр: $1";;
  esac
done

if [ -z "$DATA" ]; then
  HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  for d in "$HERE/data" "$HERE/../data" "$HOME/neuro12-cloud-data" "$HOME/.neuro12-cloud"; do
    [ -d "$d" ] && DATA="$d" && break
  done
  [ -n "$DATA" ] || DATA="$HOME/neuro12-cloud-data"
fi
TLS="$DATA/tls"

need(){ command -v "$1" >/dev/null 2>&1 || die "нужна утилита '$1' (установи: $2)"; }

usage(){ sed -n '2,15p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; }

local_sans(){
  local s="DNS:localhost,IP:127.0.0.1" h ip
  h="$(hostname 2>/dev/null || true)"
  [ -n "$h" ] && s="$s,DNS:$h"
  for ip in $(hostname -I 2>/dev/null || true); do s="$s,IP:$ip"; done
  local n; for n in ${NAMES[@]+"${NAMES[@]}"}; do s="$s,DNS:$n"; done
  for n in ${IPS[@]+"${IPS[@]}"}; do s="$s,IP:$n"; done
  printf '%s' "$s"
}

fp(){ openssl x509 -in "$1" -noout -fingerprint -sha256 2>/dev/null | cut -d= -f2; }

finish(){
  chmod 600 "$TLS/key.pem" 2>/dev/null || true
  chmod 644 "$TLS/cert.pem" 2>/dev/null || true
  say "Готово. Сертификат: $TLS/cert.pem"
  say "SHA-256: $(fp "$TLS/cert.pem")"
  echo "Дальше: перезапусти облако — оно откроет https://<адрес>:$HPORT/"
  echo "Проверка: curl --cacert $TLS/cert.pem https://localhost:$HPORT/api/tls"
}

do_self(){
  need openssl "sudo apt install openssl"
  mkdir -p "$TLS"
  if [ -f "$TLS/custom" ]; then die "в $TLS стоит боевой сертификат (custom). Сначала: $0 remove"; fi
  local san; san="$(local_sans)"
  say "Самоподписанный сертификат, SAN: $san"
  openssl req -x509 -newkey rsa:2048 -sha256 -nodes -days "$DAYS" \
    -keyout "$TLS/key.pem" -out "$TLS/cert.pem" \
    -subj "/CN=neuro12-cloud/O=neuro12" \
    -addext "subjectAltName=$san" \
    -addext "basicConstraints=critical,CA:FALSE" \
    -addext "extendedKeyUsage=serverAuth" 2>/dev/null
  echo 1 > "$TLS/selfsigned"; rm -f "$TLS/custom"
  if [ "$TRUST" = 1 ]; then trust_cert; fi
  finish
}

trust_cert(){
  # Добавить сертификат в доверенные системные (чтобы curl/браузер без -k).
  [ "$(id -u)" = 0 ] || { warn "--trust нужен root: запусти через sudo"; return 0; }
  if [ -d /usr/local/share/ca-certificates ] && command -v update-ca-certificates >/dev/null; then
    cp "$TLS/cert.pem" /usr/local/share/ca-certificates/neuro12-cloud.crt
    update-ca-certificates >/dev/null && say "Сертификат добавлен в доверенные (Debian/Ubuntu)"
  elif [ -d /etc/pki/ca-trust/source/anchors ] && command -v update-ca-trust >/dev/null; then
    cp "$TLS/cert.pem" /etc/pki/ca-trust/source/anchors/neuro12-cloud.pem
    update-ca-trust extract && say "Сертификат добавлен в доверенные (Fedora/RHEL)"
  else
    warn "не нашёл update-ca-certificates / update-ca-trust — доверие не настроено"
  fi
}

do_le(){
  [ -n "$DOMAIN" ] || die "укажи --domain (публичное имя, A-запись которого ведёт на этот сервер)"
  [ -n "$EMAIL" ] || die "укажи --email (для уведомлений Let's Encrypt)"
  command -v certbot >/dev/null 2>&1 || die "нет certbot: sudo apt install certbot  (или snap install --classic certbot)"
  [ "$(id -u)" = 0 ] || die "Let's Encrypt: запусти через sudo"
  mkdir -p "$TLS"
  say "Let's Encrypt для $DOMAIN (режим $MODE; нужен открытый порт 80)"
  if [ "$MODE" = webroot ]; then
    mkdir -p /var/www/neuro12-acme
    certbot certonly --webroot -w /var/www/neuro12-acme -d "$DOMAIN" -m "$EMAIL" --agree-tos --non-interactive
  else
    certbot certonly --standalone -d "$DOMAIN" -m "$EMAIL" --agree-tos --non-interactive
  fi
  local live="/etc/letsencrypt/live/$DOMAIN"
  [ -f "$live/fullchain.pem" ] || die "certbot не создал $live/fullchain.pem"
  copy_le "$live"
  # хук продления: после каждого обновления копируем новые файлы
  mkdir -p /etc/letsencrypt/renewal-hooks/deploy
  cat > /etc/letsencrypt/renewal-hooks/deploy/neuro12-cloud.sh <<HOOK
#!/bin/sh
# neuro12-cloud: копирует продлённый сертификат (облако подхватит само за час)
[ -f "$live/fullchain.pem" ] || exit 0
cp "$live/fullchain.pem" "$TLS/cert.pem"; cp "$live/privkey.pem" "$TLS/key.pem"
chmod 600 "$TLS/key.pem"
OWN=\$(stat -c %u:%g "$DATA" 2>/dev/null) && chown "\$OWN" "$TLS/cert.pem" "$TLS/key.pem"
HOOK
  chmod 755 /etc/letsencrypt/renewal-hooks/deploy/neuro12-cloud.sh
  say "Хук автопродления установлен (certbot продлевает сам, обычно через systemd-таймер)"
  finish
}

copy_le(){
  cp "$1/fullchain.pem" "$TLS/cert.pem"; cp "$1/privkey.pem" "$TLS/key.pem"
  echo 1 > "$TLS/custom"; rm -f "$TLS/selfsigned"
  local own; own="$(stat -c %u:%g "$DATA" 2>/dev/null || true)"
  [ -n "$own" ] && chown "$own" "$TLS" "$TLS"/* 2>/dev/null || true
}

do_custom(){
  need openssl "sudo apt install openssl"
  [ -f "$CERT" ] || die "нет файла --cert"; [ -f "$KEY" ] || die "нет файла --key"
  local a b
  a="$(openssl x509 -in "$CERT" -noout -pubkey | openssl sha256)"
  b="$(openssl pkey -in "$KEY" -pubout 2>/dev/null | openssl sha256)"
  [ "$a" = "$b" ] || die "ключ не подходит к сертификату"
  mkdir -p "$TLS"; cp "$CERT" "$TLS/cert.pem"; cp "$KEY" "$TLS/key.pem"
  echo 1 > "$TLS/custom"; rm -f "$TLS/selfsigned"
  finish
}

do_status(){
  if [ ! -f "$TLS/cert.pem" ]; then echo "Сертификата нет ($TLS). Создай: $0 self-signed"; exit 0; fi
  need openssl "sudo apt install openssl"
  echo "Каталог:   $TLS"
  echo "Тип:       $([ -f "$TLS/custom" ] && echo боевой || echo самоподписанный)"
  openssl x509 -in "$TLS/cert.pem" -noout -subject -enddate
  echo "SAN:       $(openssl x509 -in "$TLS/cert.pem" -noout -ext subjectAltName 2>/dev/null | tail -n1 | sed 's/^ *//')"
  echo "SHA-256:   $(fp "$TLS/cert.pem")"
  if openssl x509 -in "$TLS/cert.pem" -noout -checkend $((30*86400)) >/dev/null; then
    echo "Срок:      больше 30 дней — ок"
  else
    warn "Срок меньше 30 дней или истёк"
  fi
}

do_remove(){
  rm -f "$TLS/cert.pem" "$TLS/key.pem" "$TLS/selfsigned" "$TLS/custom"
  if [ "$(id -u)" = 0 ]; then
    rm -f /usr/local/share/ca-certificates/neuro12-cloud.crt /etc/pki/ca-trust/source/anchors/neuro12-cloud.pem
    rm -f /etc/letsencrypt/renewal-hooks/deploy/neuro12-cloud.sh
    command -v update-ca-certificates >/dev/null && update-ca-certificates --fresh >/dev/null 2>&1 || true
  fi
  say "Сертификат удалён. При следующем старте облако создаст новый самоподписанный (или NEURO_TLS=0 — без HTTPS)."
}

case "$CMD" in
  self-signed|selfsigned|self) do_self;;
  letsencrypt|le) do_le;;
  custom) do_custom;;
  status) do_status;;
  remove) do_remove;;
  help|-h|--help|"") usage;;
  *) usage; exit 2;;
esac
