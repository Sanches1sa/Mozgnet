// predict.go — v3.155: предсказание адреса платы, в том числе во время
// зеркального спина MAC.
//
// Плата во время спина живёт под двумя MAC — прямым и перевёрнутым, — и роутер
// обычно выдаёт каждому свой IP. Зная один MAC платы, можно вычислить всё
// «семейство»: заводской, прямой (последний октет чётный) и зеркальный
// (перевёрнутый прямой). Лаунчер запоминает, какой IP был у каждого MAC, и
// когда плата пропадает по старому адресу, сначала проверяет:
//  1. таблицу ARP компьютера — нет ли там любого MAC из семейства;
//  2. IP, которые раньше были у этих MAC;
//  3. IP, который плата сама записала во время спина;
//
// и только если ничего не ответило — запускает полный скан.
// Полный скан тоже узнаёт плату по MAC, даже когда приложение на ней не отвечает.
package main

import (
	"fmt"
	"strings"
	"time"
)

func macParts(m string) []string {
	p := strings.Split(strings.ToLower(strings.ReplaceAll(strings.TrimSpace(m), "-", ":")), ":")
	if len(p) != 6 {
		return nil
	}
	for _, x := range p {
		if len(x) != 2 {
			return nil
		}
	}
	return p
}

func revMAC(m string) string {
	p := macParts(m)
	if p == nil {
		return ""
	}
	return strings.Join([]string{p[5], p[4], p[3], p[2], p[1], p[0]}, ":")
}

// fixLast — последний октет чётный «локальный», как делает n12_mac.sh (61 → 62)
func fixLast(m string) string {
	p := macParts(m)
	if p == nil {
		return ""
	}
	var v int
	fmt.Sscanf(p[5], "%x", &v)
	if v&1 == 1 {
		p[5] = fmt.Sprintf("%02x", (v&0xfe)|0x02)
	}
	return strings.Join(p, ":")
}

// macFamily — все адреса, под которыми может жить плата с этим MAC
func macFamily(m string) []string {
	if macParts(m) == nil {
		return nil
	}
	m = strings.Join(macParts(m), ":")
	f := []string{m, revMAC(m), fixLast(m), revMAC(fixLast(m))}
	var out []string
	seen := map[string]bool{}
	for _, x := range f {
		if x != "" && !seen[x] {
			seen[x] = true
			out = append(out, x)
		}
	}
	return out
}

// learnMAC — добавить MAC платы (и его семейство) в память лаунчера
func learnMAC(m, ip string) {
	fam := macFamily(m)
	if fam == nil {
		return
	}
	mu.Lock()
	changed := false
	have := map[string]bool{}
	for _, x := range cfg.BoardMACs {
		have[x] = true
	}
	for _, x := range fam {
		if !have[x] {
			cfg.BoardMACs = append(cfg.BoardMACs, x)
			have[x] = true
			changed = true
		}
	}
	if len(cfg.BoardMACs) > 12 {
		cfg.BoardMACs = cfg.BoardMACs[len(cfg.BoardMACs)-12:]
	}
	if ip != "" {
		if cfg.MacIPs == nil {
			cfg.MacIPs = map[string]string{}
		}
		k := strings.Join(macParts(m), ":")
		if cfg.MacIPs[k] != ip {
			cfg.MacIPs[k] = ip
			changed = true
		}
	}
	mu.Unlock()
	if changed {
		saveCfg()
	}
}

func isBoardMAC(m string) bool {
	k := strings.Join(macParts(m), ":")
	if k == "" {
		return false
	}
	mu.Lock()
	defer mu.Unlock()
	for _, x := range cfg.BoardMACs {
		if x == k {
			return true
		}
	}
	return false
}

// macRole — подпись: какой это адрес платы
func macRole(m string) string {
	k := strings.Join(macParts(m), ":")
	mu.Lock()
	orig, _ := macSt["orig"].(string)
	base, _ := macSt["base"].(string)
	mu.Unlock()
	switch {
	case k == "":
		return ""
	case k == orig:
		return "заводской MAC"
	case k == base:
		return "прямой MAC"
	case base != "" && k == revMAC(base):
		return "перевёрнутый MAC"
	}
	return "MAC платы"
}

var lastPredict time.Time

// predictBoard — быстрый поиск платы без полного скана. "" — не нашёл.
func predictBoard(reason string) string {
	if !setBusy("predict", true) {
		return ""
	}
	defer setBusy("predict", false)
	lastPredict = time.Now()
	c := getCfg()
	if len(c.BoardMACs) == 0 {
		return ""
	}
	type cand struct{ ip, why string }
	var cs []cand
	seen := map[string]bool{}
	add := func(ip, why string) {
		if ip != "" && !seen[ip] {
			seen[ip] = true
			cs = append(cs, cand{ip, why})
		}
	}
	for ip, m := range arpTable() {
		if isBoardMAC(m) {
			add(ip, "в таблице ARP "+macRole(m)+" "+strings.ToLower(m))
		}
	}
	for m, ip := range c.MacIPs {
		if isBoardMAC(m) {
			add(ip, "прежний адрес для "+macRole(m)+" "+m)
		}
	}
	mu.Lock()
	if ip, _ := macSt["sp_ip"].(string); ip != "" {
		mu.Unlock()
		add(ip, "адрес, который плата записала во время спина")
	} else {
		mu.Unlock()
	}
	for _, x := range cs {
		if x.ip == c.IP && reason != "" {
			continue // по нему плата как раз не отвечает
		}
		if portOpen(x.ip, 7000, 500*time.Millisecond) || portOpen(x.ip, 22, 500*time.Millisecond) {
			setPredict(x.ip, x.why)
			if x.ip != c.IP {
				mu.Lock()
				cfg.IP = x.ip
				mu.Unlock()
				saveCfg()
				logf("Плата предсказана: %s (%s)%s", x.ip, x.why, map[bool]string{true: " — " + reason, false: ""}[reason != ""])
			}
			return x.ip
		}
	}
	return ""
}

func setPredict(ip, why string) {
	mu.Lock()
	predSt = map[string]interface{}{"ip": ip, "why": why, "ts": time.Now().Unix()}
	mu.Unlock()
}

var predSt = map[string]interface{}{}

// learnFromBoard — плата отвечает по cfg.IP: запомнить её MAC и адрес
func learnFromBoard(ip string) {
	if m := arpTable()[ip]; m != "" {
		learnMAC(m, ip)
	}
}
