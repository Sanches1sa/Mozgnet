// block.go — v3.164: «⛔ Запретить адрес» — на плате (брандмауэр, переживает
// перезагрузку) и на этом компьютере (брандмауэр Windows / iptables Linux).
package main

import (
	"encoding/json"
	"fmt"
	"net"
	"os"
	"path/filepath"
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
# neuro12_block.sh — запрещённые адреса (ставит лаунчер Star Gate)
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

// crashReport — v3.165: «🩺 Почему падает облако»: падения из data/crashes.json,
// хвост data/crash.log и строки сторожа из журнала — в журнал лаунчера и в файл
// mozg12-отчёт-падений.txt в «Загрузках» (его можно прислать разработчику).
func crashReport() {
	c := getCfg()
	data := filepath.Join(c.Cloud, "data")
	var b strings.Builder
	fmt.Fprintf(&b, "Star Gate: отчёт о падениях облака, %s\nпапка облака: %s\n\n", time.Now().Format("02.01.2006 15:04:05"), c.Cloud)
	var crashes []map[string]interface{}
	if raw, err := os.ReadFile(filepath.Join(data, "crashes.json")); err == nil {
		json.Unmarshal(raw, &crashes)
	}
	hour := 0
	for _, x := range crashes {
		if ts, ok := x["ts"].(float64); ok && time.Since(time.Unix(int64(ts), 0)) < time.Hour {
			hour++
		}
	}
	fmt.Fprintf(&b, "падений записано: %d, за последний час: %d\n", len(crashes), hour)
	for i := len(crashes) - 1; i >= 0 && i >= len(crashes)-15; i-- {
		x := crashes[i]
		ts, _ := x["ts"].(float64)
		fmt.Fprintf(&b, "  %s  код %v  проработало %v с  %v\n     %v\n", time.Unix(int64(ts), 0).Format("02.01 15:04:05"), x["code"], x["up"], x["hint"], x["why"])
	}
	if raw, err := os.ReadFile(filepath.Join(data, "log.txt")); err == nil {
		lines := strings.Split(string(raw), "\n")
		b.WriteString("\n===== журнал облака: сторож и ошибки (последние) =====\n")
		n := 0
		for i := len(lines) - 1; i >= 0 && n < 40; i-- {
			l := lines[i]
			if strings.Contains(l, "СТОРОЖ") || strings.Contains(l, "ИСКЛЮЧЕНИЕ") || strings.Contains(l, "Traceback") || strings.Contains(l, "ПАДЕНИЕ") {
				b.WriteString(l + "\n")
				n++
			}
		}
	}
	if raw, err := os.ReadFile(filepath.Join(data, "crash.log")); err == nil {
		t := string(raw)
		if len(t) > 12000 {
			t = t[len(t)-12000:]
		}
		b.WriteString("\n===== crash.log (хвост) =====\n" + t)
	}
	rep := b.String()
	for _, l := range strings.Split(rep, "\n")[:min2(25, len(strings.Split(rep, "\n")))] {
		if strings.TrimSpace(l) != "" {
			logf("%s", l)
		}
	}
	dd := downloadDirs()[0]
	os.MkdirAll(dd, 0755)
	out := filepath.Join(dd, "mozg12-отчёт-падений.txt")
	if err := os.WriteFile(out, []byte(rep), 0644); err == nil {
		logf("Отчёт сохранён: %s — пришли этот файл, по нему видно, почему падает облако.", out)
		revealFile(out)
	}
}

func min2(a, b int) int {
	if a < b {
		return a
	}
	return b
}
