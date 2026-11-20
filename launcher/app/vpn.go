package main

// v3.166: VPN и домашняя сеть.
//
// Жалоба владельца: «когда работает VPN, облако отключается и плату не
// открыть». VPN с туннелем «весь интернет» (AmneziaWG/WireGuard с
// AllowedIPs = 0.0.0.0/0, hidemy.name и др.) включает «kill-switch» — блок
// всего трафика мимо туннеля, В ТОМ ЧИСЛЕ домашней сети: плата 192.168.1.x
// не достучится до облака на этом компьютере, лаунчер — до платы. Облако на
// самом компьютере (127.0.0.1) при этом живо.
//
// Лаунчер сам настройки VPN не меняет (это настройки безопасности чужой
// программы). Он:
//   - видит включённый VPN (сетевые адаптеры туннелей) и пишет честную
//     причину вместо «плата не отвечает»;
//   - считает AllowedIPs «весь интернет через туннель, домашняя подсеть
//     платы — мимо» — вставить в туннель AmneziaWG/WireGuard;
//   - не берёт адрес туннеля как адрес облака для платы (pcIPFor).

import (
	"fmt"
	"net"
	"regexp"
	"sort"
	"strings"
	"sync"
	"time"
)

var descCache struct {
	sync.Mutex
	at time.Time
	m  map[string]string
}

// descs — описания адаптеров, раз в 30 с (запрос к Windows не каждый цикл)
func descs() map[string]string {
	descCache.Lock()
	defer descCache.Unlock()
	if descCache.m == nil || time.Since(descCache.at) > 30*time.Second {
		descCache.m, descCache.at = adapterDescs(), time.Now()
	}
	return descCache.m
}

var vpnRe = regexp.MustCompile(`(?i)amnezia|wireguard|wintun|(^|[^a-z])wg\d*($|[^a-z])|tap-?windows|(^|[^a-z])(tap|tun|utun)\d*($|[^a-z])|vpn|hide\.?me|hidemy|openvpn|outline|proton|nord|surfshark|expressvpn|clash|v2ray|xray|sing-?box|hiddify|happ|nekoray|cloudflare warp|warp`)

type vpnIf struct {
	Name string `json:"name"`
	IP   string `json:"ip"`
}

func isVPNName(name string) bool { return vpnRe.MatchString(name) }

// isVPNIf — адаптер похож на VPN-туннель: имя или описание (Windows:
// «AmneziaWG Tunnel», «WireGuard Tunnel», «Wintun», «TAP-Windows»), либо
// соединение «точка-точка» / адрес с маской /32 (так поднимаются туннели).
func isVPNIf(i net.Interface, descs map[string]string) (bool, string) {
	if i.Flags&net.FlagLoopback != 0 {
		return false, ""
	}
	if isVPNName(i.Name) {
		return true, i.Name
	}
	if d := descs[i.Name]; d != "" && isVPNName(d) {
		return true, i.Name + " (" + d + ")"
	}
	if i.Flags&net.FlagPointToPoint != 0 {
		return true, i.Name
	}
	addrs, _ := i.Addrs()
	for _, a := range addrs {
		if n, ok := a.(*net.IPNet); ok && n.IP.To4() != nil && !n.IP.IsLinkLocalUnicast() {
			if ones, _ := n.Mask.Size(); ones == 32 {
				return true, i.Name
			}
		}
	}
	return false, ""
}

// vpnAdapters — включённые сетевые адаптеры, похожие на VPN-туннели.
func vpnAdapters() []vpnIf {
	var out []vpnIf
	ds := descs()
	ifs, _ := net.Interfaces()
	for _, i := range ifs {
		if i.Flags&net.FlagUp == 0 {
			continue
		}
		ok, label := isVPNIf(i, ds)
		if !ok {
			continue
		}
		ip := ""
		addrs, _ := i.Addrs()
		for _, a := range addrs {
			if n, ok := a.(*net.IPNet); ok && n.IP.To4() != nil && !n.IP.IsLinkLocalUnicast() {
				ip = n.IP.String()
				break
			}
		}
		if ip == "" {
			continue // адаптер есть, но туннель не поднят
		}
		out = append(out, vpnIf{label, ip})
	}
	return out
}

func vpnNames(v []vpnIf) string {
	var n []string
	for _, x := range v {
		n = append(n, x.Name)
	}
	return strings.Join(n, ", ")
}

// boardSubnet — домашняя подсеть платы: подсеть сетевой карты, в которой
// лежит адрес платы, иначе /24 вокруг её адреса.
func boardSubnet(board string) *net.IPNet {
	bip := net.ParseIP(board).To4()
	if bip == nil {
		return nil
	}
	for _, n := range localNets() {
		if n.net.Contains(bip) && !isVPNNet(n) {
			ones, _ := n.net.Mask.Size()
			if ones >= 16 {
				return &net.IPNet{IP: n.net.IP.Mask(n.net.Mask), Mask: n.net.Mask}
			}
		}
	}
	m := net.CIDRMask(24, 32)
	return &net.IPNet{IP: bip.Mask(m), Mask: m}
}

func isVPNNet(n lnet) bool {
	ds := descs()
	ifs, _ := net.Interfaces()
	for _, i := range ifs {
		if ok, _ := isVPNIf(i, ds); !ok {
			continue
		}
		addrs, _ := i.Addrs()
		for _, a := range addrs {
			if x, ok := a.(*net.IPNet); ok && x.IP.Equal(n.ip) {
				return true
			}
		}
	}
	return false
}

type cidr struct{ base, size uint64 } // size — число адресов (2^(32-prefix))

func (c cidr) String() string {
	p := 32
	for s := c.size; s > 1; s >>= 1 {
		p--
	}
	return fmt.Sprintf("%d.%d.%d.%d/%d", byte(c.base>>24), byte(c.base>>16), byte(c.base>>8), byte(c.base), p)
}

func toCidr(n *net.IPNet) cidr {
	ones, _ := n.Mask.Size()
	ip := n.IP.To4()
	b := uint64(ip[0])<<24 | uint64(ip[1])<<16 | uint64(ip[2])<<8 | uint64(ip[3])
	return cidr{b, 1 << uint(32-ones)}
}

func exclude(n, e cidr) []cidr {
	if e.base+e.size <= n.base || n.base+n.size <= e.base {
		return []cidr{n} // не пересекаются
	}
	if e.base <= n.base && n.base+n.size <= e.base+e.size {
		return nil // n целиком внутри исключения
	}
	h := n.size / 2
	return append(exclude(cidr{n.base, h}, e), exclude(cidr{n.base + h, h}, e)...)
}

// allowedIPsWithoutLAN — «0.0.0.0/0 минус домашняя подсеть (и служебные
// link-local / multicast)»: строка для AllowedIPs туннеля WireGuard/AmneziaWG.
func allowedIPsWithoutLAN(lans ...*net.IPNet) string {
	all := []cidr{{0, 1 << 32}}
	var ex []*net.IPNet
	ex = append(ex, lans...)
	for _, s := range []string{"169.254.0.0/16", "224.0.0.0/4", "240.0.0.0/4"} {
		_, n, _ := net.ParseCIDR(s)
		ex = append(ex, n)
	}
	for _, e := range ex {
		if e == nil || e.IP.To4() == nil {
			continue
		}
		ec := toCidr(e)
		var next []cidr
		for _, n := range all {
			next = append(next, exclude(n, ec)...)
		}
		all = next
	}
	sort.Slice(all, func(i, j int) bool { return all[i].base < all[j].base })
	var s []string
	for _, c := range all {
		s = append(s, c.String())
	}
	return strings.Join(s, ", ")
}

// vpnAdvice — для статуса лаунчера: какой VPN включён и что сделать.
func vpnAdvice(board string) map[string]interface{} {
	v := vpnAdapters()
	if len(v) == 0 {
		return nil
	}
	sub := boardSubnet(board)
	lan := ""
	if sub != nil {
		lan = sub.String()
	}
	return map[string]interface{}{
		"names":   vpnNames(v),
		"ifs":     v,
		"lan":     lan,
		"allowed": allowedIPsWithoutLAN(sub),
	}
}
