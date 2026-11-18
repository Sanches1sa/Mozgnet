// block.go — v3.164: «⛔ Запретить адрес» — на плате (брандмауэр, переживает
// перезагрузку) и на этом компьютере (брандмауэр Windows / iptables Linux).
package main

import (
	"net"
	"strings"
	"time"
)

// boardBlockScript — правила на плате: исходящие/входящие и для контейнеров
// (DOCKER-USER), плюс служба neuro12-block, которая ставит их при загрузке.
func boardBlockScript(ips []string) string {
	list := strings.Join(ips, " ")
	return `set -e
cat > /usr/local/bin/neuro12_block.sh <<'N12EOF'
#!/bin/sh
# neuro12_block.sh — запрещённые адреса (ставит лаунчер Мозг-12)
IPS="` + list + `"
if command -v iptables >/dev/null 2>&1; then
  for ip in $IPS; do
    for rule in "OUTPUT -d $ip" "INPUT -s $ip" "FORWARD -d $ip" "FORWARD -s $ip"; do
      iptables -C $rule -j DROP 2>/dev/null || iptables -I $rule -j DROP
    done
    if iptables -L DOCKER-USER >/dev/null 2>&1; then
      iptables -C DOCKER-USER -d $ip -j DROP 2>/dev/null || iptables -I DOCKER-USER -d $ip -j DROP
    fi
  done
elif command -v nft >/dev/null 2>&1; then
  nft add table inet n12block 2>/dev/null || true
  nft add chain inet n12block out '{ type filter hook output priority -10; }' 2>/dev/null || true
  nft add chain inet n12block inp '{ type filter hook input priority -10; }' 2>/dev/null || true
  nft add chain inet n12block fwd '{ type filter hook forward priority -10; }' 2>/dev/null || true
  for ip in $IPS; do
    nft add rule inet n12block out ip daddr $ip drop 2>/dev/null || true
    nft add rule inet n12block inp ip saddr $ip drop 2>/dev/null || true
    nft add rule inet n12block fwd ip daddr $ip drop 2>/dev/null || true
  done
else
  echo "нет iptables и nft — брандмауэр не настроен"; exit 1
fi
echo "запрещены: $IPS"
N12EOF
chmod 755 /usr/local/bin/neuro12_block.sh
cat > /etc/systemd/system/neuro12-block.service <<'N12EOF'
[Unit]
Description=Mozg-12: blocked addresses
After=network-pre.target docker.service
Wants=network-pre.target
[Service]
Type=oneshot
ExecStart=/usr/local/bin/neuro12_block.sh
RemainAfterExit=yes
[Install]
WantedBy=multi-user.target
N12EOF
systemctl daemon-reload
systemctl enable neuro12-block.service >/dev/null 2>&1 || true
/usr/local/bin/neuro12_block.sh
for ip in ` + list + `; do
  echo "== кто сейчас держит соединение с $ip:"
  (ss -tnp 2>/dev/null | grep -F "$ip") || echo "   никто"
done
echo "== правило переживёт перезагрузку (служба neuro12-block)"`
}

func blockIPs(raw string) {
	var ips []string
	for _, f := range strings.FieldsFunc(raw, func(r rune) bool { return r == ',' || r == ' ' || r == ';' || r == '\n' }) {
		f = strings.TrimSpace(f)
		if ip := net.ParseIP(f); ip != nil && ip.To4() != nil {
			ips = append(ips, ip.String())
		} else if _, n, err := net.ParseCIDR(f); err == nil && n.IP.To4() != nil {
			ips = append(ips, n.String())
		} else if f != "" {
			logf("«%s» — не IPv4-адрес, пропускаю", f)
		}
	}
	if len(ips) == 0 {
		logf("Нет адресов для запрета.")
		return
	}
	logf("Запрещаю %s: на этом компьютере и на плате…", strings.Join(ips, ", "))
	blockLocal(ips)
	if getCfg().IP != "" {
		sudoRun("Запрет адресов на плате", boardBlockScript(ips), 5*time.Minute)
	} else {
		logf("Плата ещё не найдена — на плате запрет поставлю, когда она появится (нажми ещё раз).")
	}
}
