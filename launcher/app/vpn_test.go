package main

import (
	"net"
	"strings"
	"testing"
)

func TestAllowedIPsWithoutLAN(t *testing.T) {
	_, lan, _ := net.ParseCIDR("192.168.1.0/24")
	s := allowedIPsWithoutLAN(lan)
	parts := strings.Split(s, ", ")
	in := func(ip string) bool {
		for _, p := range parts {
			_, n, err := net.ParseCIDR(p)
			if err != nil {
				t.Fatalf("плохой CIDR %q", p)
			}
			if n.Contains(net.ParseIP(ip)) {
				return true
			}
		}
		return false
	}
	for _, ip := range []string{"1.1.1.1", "8.8.8.8", "10.8.1.1", "192.168.0.5", "192.168.2.1", "77.88.8.8", "223.255.255.254"} {
		if !in(ip) {
			t.Errorf("%s должен идти через VPN", ip)
		}
	}
	for _, ip := range []string{"192.168.1.108", "192.168.1.1", "169.254.1.1", "224.0.0.251", "255.255.255.255"} {
		if in(ip) {
			t.Errorf("%s не должен идти через VPN", ip)
		}
	}
	// покрытие: сумма размеров = 2^32 - исключённое
	var total uint64
	for _, p := range parts {
		_, n, _ := net.ParseCIDR(p)
		ones, _ := n.Mask.Size()
		total += 1 << uint(32-ones)
	}
	want := uint64(1<<32) - 256 - 65536 - (1 << 28) - (1 << 28)
	if total != want {
		t.Errorf("покрыто %d адресов, ждали %d", total, want)
	}
	t.Logf("%d сетей: %s", len(parts), s)
}

func TestVPNName(t *testing.T) {
	for _, n := range []string{"AmneziaWG Tunnel", "WireGuard Tunnel", "wg0", "TAP-Windows Adapter V9", "hidemy.name VPN", "Wintun Userspace Tunnel"} {
		if !isVPNName(n) {
			t.Errorf("%q — VPN", n)
		}
	}
	for _, n := range []string{"Ethernet", "Wi-Fi", "Беспроводная сеть", "vEthernet (WSL)", "Подключение по локальной сети", "Bluetooth Network Connection"} {
		if isVPNName(n) {
			t.Errorf("%q — не VPN", n)
		}
	}
}
