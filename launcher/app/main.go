// neuro12-start — ЛАУНЧЕР «МОЗГ-12» (v3.159: Windows — Go + WebView2, Linux — окно браузера, os_linux.go)
//
// Одно окно программы:
//   - вкладка «Запуск»: облако на этом компьютере, приложение платы, сканер
//     сети (ищет плату по портам 7000/22/8077 и сам прописывает её адрес);
//   - вкладка «Облако»: панель облака ПРЯМО В ОКНЕ (WebView2 — движок Edge,
//     встроен в Windows 11), без браузера;
//   - вкладка «Плата»: веб-интерфейс платы (порт 7000) там же.
//
// На страницах облака и платы внизу справа — кнопка «⟵ Запуск».
//
// Интерфейс лаунчера — страница с локального сервера 127.0.0.1:<случайный
// порт>; её запросы подписаны секретом, который знает только эта страница
// (страницы облака/платы вызвать действия лаунчера не могут).
// Сборка: build.sh (GOOS=windows, без cgo).
package main

import (
	"bytes"
	"crypto/rand"
	_ "embed"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"net"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"sync"
	"time"
)

const VER = "3.161.0"

//go:embed ui.html
var uiHTML string

//go:embed n12_deploy.sh
var deploySh string

//go:embed n12_setcfg.py
var setcfgPy string

//go:embed n12_mac.sh
var macSh string

// ------------------------------------------------------------- состояние
type Cfg struct {
	Cloud     string            `json:"cloud"`
	App       string            `json:"app"`
	IP        string            `json:"ip"`
	User      string            `json:"user"`
	Ports     string            `json:"ports"`
	Range     string            `json:"range"`
	AutoCloud *bool             `json:"auto_cloud,omitempty"`
	OpenCloud *bool             `json:"open_cloud,omitempty"`
	MacAuto   *bool             `json:"mac_auto,omitempty"`
	MacPC     *bool             `json:"mac_pc,omitempty"`
	MacEvery  int               `json:"mac_every,omitempty"`
	BoardMACs []string          `json:"board_macs,omitempty"` // v3.155: семейство MAC платы
	MacIPs    map[string]string `json:"mac_ips,omitempty"`    // v3.155: какой IP был у какого MAC
	MacHz     float64           `json:"mac_hz,omitempty"`     // v3.153: частота спина (Гц), до 15 ТГц
	Browser   string            `json:"browser,omitempty"`    // v3.159 (Linux): браузер окна, напр. chromium
	Apk       string            `json:"apk,omitempty"`        // v3.161: APK телефона из общего архива
}

type Host struct {
	IP    string `json:"ip"`
	Name  string `json:"name"`
	MAC   string `json:"mac"`
	Ports []int  `json:"ports"`
	SSH   string `json:"ssh"`
	App   string `json:"app"`
	Cloud string `json:"cloud"`
	Kind  string `json:"kind"`
	Score int    `json:"score"`
}

type ScanState struct {
	Running bool   `json:"running"`
	Done    int    `json:"done"`
	Total   int    `json:"total"`
	Hosts   []Host `json:"hosts"`
	Note    string `json:"note"`
	Secs    int    `json:"secs"`
}

var (
	mu       sync.Mutex
	cfg      Cfg
	cfgPath  string
	logs     []string
	logBase  int
	cloudSt  = map[string]interface{}{"ok": false}
	boardSt  = map[string]interface{}{"ok": false}
	scan     ScanState
	busy     = map[string]bool{}
	secret   string
	homeURL  string
	exeDir   string
	firstNav = true
	// v3.159: переход для окна браузера (Linux) — отдаётся в /api/state
	pendingNav string
)

func bptr(b bool) *bool { return &b }
func on(p *bool) bool   { return p == nil || *p }

func logf(f string, a ...interface{}) {
	s := time.Now().Format("15:04:05 ") + fmt.Sprintf(f, a...)
	mu.Lock()
	logs = append(logs, s)
	if len(logs) > 600 {
		logs = logs[100:]
		logBase += 100
	}
	mu.Unlock()
}

func setBusy(k string, v bool) bool {
	mu.Lock()
	defer mu.Unlock()
	if v && busy[k] {
		return false
	}
	busy[k] = v
	return true
}

func getCfg() Cfg { mu.Lock(); defer mu.Unlock(); return cfg }

func loadCfg() {
	dir := launcherDir()
	os.MkdirAll(dir, 0755)
	cfgPath = filepath.Join(dir, "launcher.json")
	if b, err := os.ReadFile(cfgPath); err == nil {
		json.Unmarshal(b, &cfg)
	}
	if cfg.Cloud == "" || !exists(filepath.Join(cfg.Cloud, "server", "app.py")) {
		cfg.Cloud = guessCloud()
	}
	if cfg.App == "" {
		cfg.App = guessApp()
	}
	if cfg.User == "" {
		cfg.User = "arduino"
	}
	if cfg.Ports == "" {
		cfg.Ports = "7000,22,8077"
	}
}

func saveCfg() {
	mu.Lock()
	b, _ := json.MarshalIndent(cfg, "", " ")
	mu.Unlock()
	os.WriteFile(cfgPath, b, 0644)
}

// v3.154: частота спина MAC. Полный оборот спина (прямой → перевёрнутый →
// прямой) — один период 1/f, значит смена адреса — каждые 1/(2f). Задать
// можно до 15 ТГц. Ограничения в 30 с больше нет: чаще раза в минуту спин
// крутит сама плата (mac.go, macTick), с предельной скоростью сети.
const (
	macMaxHz = 15e12
	macMinHz = 1e-6
)

func clampHz(f float64) float64 {
	if f != f || f <= 0 { // NaN или ноль
		return 0
	}
	if f > macMaxHz {
		return macMaxHz
	}
	if f < macMinHz {
		return macMinHz
	}
	return f
}

// macHz — заданная частота (старый «каждые N мин» переводится в частоту)
func macHz(c Cfg) float64 {
	if c.MacHz > 0 {
		return clampHz(c.MacHz)
	}
	return 1 / (2 * float64(clampEvery(c.MacEvery)) * 60)
}

// macGap — реальный промежуток между сменами MAC
func macGap(c Cfg) time.Duration {
	g := 1 / (2 * macHz(c)) // секунды
	if g < 1e-9 {
		return 0 // быстрее наносекунды — для цикла на плате это «без пауз»
	}
	return time.Duration(g * float64(time.Second))
}

func fmtHz(f float64) string {
	for _, u := range []struct {
		k float64
		n string
	}{{1e12, "ТГц"}, {1e9, "ГГц"}, {1e6, "МГц"}, {1e3, "кГц"}, {1, "Гц"}, {1e-3, "мГц"}} {
		if f >= u.k {
			return strconv.FormatFloat(f/u.k, 'g', 6, 64) + " " + u.n
		}
	}
	return strconv.FormatFloat(f*1e6, 'g', 6, 64) + " мкГц"
}

func clampEvery(n int) int {
	if n < 5 {
		return 30
	}
	if n > 1440 {
		return 1440
	}
	return n
}

func exists(p string) bool { _, err := os.Stat(p); return err == nil }

func guessCloud() string {
	for _, c := range append([]string{filepath.Dir(exeDir), exeDir}, cloudCandidates()...) {
		if exists(filepath.Join(c, "server", "app.py")) {
			return c
		}
	}
	return ""
}

func guessApp() string {
	best, bt := "", time.Time{}
	for _, dl := range downloadDirs() {
		ents, _ := os.ReadDir(dl)
		for _, e := range ents {
			if !e.IsDir() || !strings.HasPrefix(e.Name(), "neuro_12") {
				continue
			}
			for _, c := range []string{filepath.Join(dl, e.Name(), "neuro_12"), filepath.Join(dl, e.Name())} {
				if exists(filepath.Join(c, "app.yaml")) {
					if fi, err := os.Stat(c); err == nil && fi.ModTime().After(bt) {
						best, bt = c, fi.ModTime()
					}
					break
				}
			}
		}
	}
	return best
}

// --------------------------------------------------------------- облако
func cloudPort(dir string) int {
	if b, err := os.ReadFile(filepath.Join(dir, "data", "port.txt")); err == nil {
		if p, err := strconv.Atoi(strings.TrimSpace(string(b))); err == nil && p > 0 {
			return p
		}
	}
	return 8077
}

func cloudToken(dir string) string {
	b, _ := os.ReadFile(filepath.Join(dir, "data", "token.txt"))
	return strings.TrimSpace(string(b))
}

var hc = &http.Client{Timeout: 3 * time.Second, Transport: &http.Transport{Proxy: nil}}

func getJSON(url string, to time.Duration) (map[string]interface{}, int, error) {
	c := &http.Client{Timeout: to, Transport: &http.Transport{Proxy: nil}}
	r, err := c.Get(url)
	if err != nil {
		return nil, 0, err
	}
	defer r.Body.Close()
	b, _ := io.ReadAll(io.LimitReader(r.Body, 1<<20))
	var d map[string]interface{}
	json.Unmarshal(b, &d)
	return d, r.StatusCode, nil
}

func cloudURL() string { return fmt.Sprintf("http://127.0.0.1:%d/", cloudPort(getCfg().Cloud)) }

func startCloud() bool {
	c := getCfg()
	if !exists(filepath.Join(c.Cloud, "server", "app.py")) {
		logf("Облако: в папке нет server/app.py — укажи папку neuro12-cloud (обычно %s).", cloudHint)
		return false
	}
	port := cloudPort(c.Cloud)
	health := fmt.Sprintf("http://127.0.0.1:%d/api/health", port)
	if _, code, err := getJSON(health, 2*time.Second); err == nil && code < 500 {
		logf("Облако уже работает: порт %d", port)
		return true
	}
	if !setBusy("cloud", true) {
		// v3.152: облако уже запускается (автозапуск при открытии) — не бросаем
		// «Запустить всё», а ждём, пока оно ответит
		logf("Облако уже запускается — жду его…")
		for i := 0; i < 70; i++ {
			time.Sleep(time.Second)
			if _, code, err := getJSON(fmt.Sprintf("http://127.0.0.1:%d/api/health", cloudPort(c.Cloud)), 1500*time.Millisecond); err == nil && code < 500 {
				return true
			}
			mu.Lock()
			still := busy["cloud"]
			mu.Unlock()
			if !still {
				break
			}
		}
		_, code, err := getJSON(fmt.Sprintf("http://127.0.0.1:%d/api/health", cloudPort(c.Cloud)), 1500*time.Millisecond)
		return err == nil && code < 500
	}
	defer setBusy("cloud", false)
	t0 := time.Now()
	if !launchCloud(c.Cloud) {
		return false
	}
	// v3.159.1: ждём до 3 минут (облако до v3.175.1 открывало порт только
	// после всех посевов — с большой базой дольше минуты) и по ходу смотрим,
	// не упало ли оно: причина падения сразу в журнал лаунчера
	seen := ""
	for i := 0; i < 180; i++ {
		time.Sleep(time.Second)
		port = cloudPort(c.Cloud)
		health = fmt.Sprintf("http://127.0.0.1:%d/api/health", port)
		if d, code, err := getJSON(health, 1500*time.Millisecond); err == nil && code < 500 {
			if d != nil && d["starting"] == true {
				logf("Облако открыло порт %d и заканчивает запуск…", port)
			} else {
				logf("Облако работает: http://127.0.0.1:%d", port)
			}
			return true
		}
		if i%10 == 9 {
			if dg := cloudDiag(c.Cloud, t0); dg != "" && dg != seen {
				seen = dg
				logf("Облако: %s", dg)
			} else if i == 59 || i == 119 {
				logf("Облако ещё запускается (%d с)…", i+1)
			}
		}
	}
	logf("Облако не ответило за 3 мин — журнал: %s, падения: %s",
		filepath.Join(c.Cloud, "data", "log.txt"), filepath.Join(c.Cloud, "data", "crash.log"))
	if dg := cloudDiag(c.Cloud, time.Time{}); dg != "" {
		logf("Облако, последнее в журнале: %s", dg)
	}
	return false
}

// cloudDiag — почему облако не поднимается: строки сторожа/падений из
// data/log.txt (после t0) и последняя ошибка из data/crash.log
func cloudDiag(dir string, t0 time.Time) string {
	tail := func(p string, n int64) string {
		f, err := os.Open(p)
		if err != nil {
			return ""
		}
		defer f.Close()
		if st, err := f.Stat(); err == nil && st.Size() > n {
			f.Seek(st.Size()-n, 0)
		}
		b, _ := io.ReadAll(f)
		return string(b)
	}
	out := []string{}
	for _, ln := range strings.Split(tail(filepath.Join(dir, "data", "log.txt"), 32768), "\n") {
		ln = strings.TrimSpace(ln)
		if len(ln) < 20 {
			continue
		}
		if ts, err := time.ParseInLocation("2006-01-02 15:04:05", ln[:19], time.Local); err == nil && ts.Before(t0.Add(-2*time.Second)) {
			continue
		}
		for _, k := range []string{"СТОРОЖ", "ПАДЕНИЕ", "не смог занять порт", "УЖЕ запущена", "уже работает"} {
			if strings.Contains(ln, k) {
				out = append(out, ln[20:])
				break
			}
		}
	}
	if len(out) > 3 {
		out = out[len(out)-3:]
	}
	cr := tail(filepath.Join(dir, "data", "crash.log"), 16384)
	if i := strings.LastIndex(cr, "=== старт"); i >= 0 {
		cr = cr[i:]
		var last string
		for _, ln := range strings.Split(cr, "\n") {
			l := strings.TrimSpace(ln)
			if strings.Contains(l, "Error") || strings.Contains(l, "Exception") || strings.Contains(strings.ToLower(l), "fatal") {
				last = l
			}
		}
		if last != "" && (t0.IsZero() || len(out) > 0) {
			out = append(out, "crash.log: "+last)
		}
	}
	return strings.Join(out, " | ")
}

// ----------------------------------------------------------------- сеть
type lnet struct {
	ip  net.IP
	net *net.IPNet
}

func localNets() []lnet {
	var o []lnet
	ifs, _ := net.Interfaces()
	for _, i := range ifs {
		if i.Flags&net.FlagUp == 0 || i.Flags&net.FlagLoopback != 0 {
			continue
		}
		addrs, _ := i.Addrs()
		for _, a := range addrs {
			n, ok := a.(*net.IPNet)
			if !ok || n.IP.To4() == nil || n.IP.IsLinkLocalUnicast() {
				continue
			}
			o = append(o, lnet{n.IP.To4(), n})
		}
	}
	return o
}

func pcIPFor(board string) string {
	ns := localNets()
	bip := net.ParseIP(board)
	for _, n := range ns {
		if bip != nil && n.net.Contains(bip) {
			return n.ip.String()
		}
	}
	pfx := strings.Join(strings.Split(board, ".")[:min(3, len(strings.Split(board, ".")))], ".") + "."
	for _, n := range ns {
		if strings.HasPrefix(n.ip.String(), pfx) {
			return n.ip.String()
		}
	}
	if len(ns) > 0 {
		return ns[0].ip.String()
	}
	return "127.0.0.1"
}

func portOpen(ip string, port int, to time.Duration) bool {
	c, err := net.DialTimeout("tcp", net.JoinHostPort(ip, strconv.Itoa(port)), to)
	if err != nil {
		return false
	}
	c.Close()
	return true
}

func ipToU(ip net.IP) uint32 {
	ip = ip.To4()
	return uint32(ip[0])<<24 | uint32(ip[1])<<16 | uint32(ip[2])<<8 | uint32(ip[3])
}
func uToIP(u uint32) string {
	return fmt.Sprintf("%d.%d.%d.%d", u>>24, (u>>16)&255, (u>>8)&255, u&255)
}

// цели скана: поле «диапазон» (CIDR, a.b.c.x-y, список) или свои сети (/24)
func scanTargets(rng string) ([]string, string) {
	seen := map[string]bool{}
	var out []string
	add := func(s string) {
		if !seen[s] {
			seen[s] = true
			out = append(out, s)
		}
	}
	note := ""
	rng = strings.TrimSpace(rng)
	if rng != "" {
		for _, part := range strings.FieldsFunc(rng, func(r rune) bool { return r == ',' || r == ' ' || r == ';' }) {
			if strings.Contains(part, "/") {
				ip, n, err := net.ParseCIDR(part)
				if err != nil || ip.To4() == nil {
					continue
				}
				ones, _ := n.Mask.Size()
				if ones < 20 {
					ones = 20
					note = "диапазон шире /20 урезан до /20 (4096 адресов)"
				}
				base := ipToU(ip) & ^uint32(0) << (32 - ones)
				cnt := uint32(1) << (32 - ones)
				for i := uint32(1); i+1 < cnt; i++ {
					add(uToIP(base + i))
				}
			} else if strings.Contains(part, "-") {
				ab := strings.SplitN(part, "-", 2)
				a := net.ParseIP(strings.TrimSpace(ab[0]))
				if a == nil || a.To4() == nil {
					continue
				}
				last := strings.TrimSpace(ab[1])
				var b net.IP
				if strings.Contains(last, ".") {
					b = net.ParseIP(last)
				} else {
					p := strings.Split(a.To4().String(), ".")
					b = net.ParseIP(p[0] + "." + p[1] + "." + p[2] + "." + last)
				}
				if b == nil {
					continue
				}
				for u := ipToU(a); u <= ipToU(b) && u-ipToU(a) < 4096; u++ {
					add(uToIP(u))
				}
			} else if ip := net.ParseIP(part); ip != nil && ip.To4() != nil {
				add(ip.To4().String())
			}
		}
		return out, note
	}
	for _, n := range localNets() {
		ones, _ := n.net.Mask.Size()
		if ones < 24 {
			ones = 24
			note = "сеть шире /24 — сканирую /24 вокруг адреса этого компьютера (другой диапазон можно задать вручную)"
		}
		base := ipToU(n.ip) & ^uint32(0) << (32 - ones)
		cnt := uint32(1) << (32 - ones)
		for i := uint32(1); i+1 < cnt; i++ {
			s := uToIP(base + i)
			if s != n.ip.String() {
				add(s)
			}
		}
	}
	return out, note
}

func parsePorts(s string) []int {
	var o []int
	seen := map[int]bool{}
	for _, p := range strings.FieldsFunc(s, func(r rune) bool { return r == ',' || r == ' ' || r == ';' }) {
		if n, err := strconv.Atoi(strings.TrimSpace(p)); err == nil && n > 0 && n < 65536 && !seen[n] {
			seen[n] = true
			o = append(o, n)
		}
	}
	if len(o) == 0 {
		o = []int{7000, 22, 8077}
	}
	if len(o) > 32 {
		o = o[:32]
	}
	return o
}

func sshBanner(ip string) string {
	c, err := net.DialTimeout("tcp", net.JoinHostPort(ip, "22"), 1500*time.Millisecond)
	if err != nil {
		return ""
	}
	defer c.Close()
	c.SetReadDeadline(time.Now().Add(2 * time.Second))
	b := make([]byte, 256)
	n, _ := c.Read(b)
	return strings.TrimSpace(strings.SplitN(string(b[:n]), "\n", 2)[0])
}

func lookupName(ip string) string {
	ch := make(chan string, 1)
	go func() {
		ns, _ := net.LookupAddr(ip)
		if len(ns) > 0 {
			ch <- strings.TrimSuffix(ns[0], ".")
		} else {
			ch <- ""
		}
	}()
	select {
	case s := <-ch:
		return s
	case <-time.After(2 * time.Second):
		return ""
	}
}

func probeHost(ip string, ports []int) *Host {
	var open []int
	for _, p := range ports {
		if portOpen(ip, p, 400*time.Millisecond) {
			open = append(open, p)
		}
	}
	if len(open) == 0 {
		return nil
	}
	h := &Host{IP: ip, Ports: open}
	has := func(p int) bool {
		for _, x := range open {
			if x == p {
				return true
			}
		}
		return false
	}
	if has(22) {
		h.SSH = sshBanner(ip)
	}
	for _, p := range open {
		if p == 22 {
			continue
		}
		base := fmt.Sprintf("http://%s:%d", ip, p)
		if d, code, err := getJSON(base+"/api/alive", 2500*time.Millisecond); err == nil && code == 200 && d != nil && d["ver"] != nil {
			sk := "скетч: не отвечает"
			if v, _ := d["sketch_alive"].(bool); v {
				sk = "скетч: жив"
			} else if v, _ := d["sketch_seen"].(bool); v {
				sk = "скетч: молчит"
			}
			h.App = fmt.Sprintf("neuro_12 v%v на порту %d, %s", d["ver"], p, sk)
			continue
		}
		if d, code, err := getJSON(base+"/api/health", 2500*time.Millisecond); err == nil && code == 200 && d != nil {
			if s, _ := d["service"].(string); s == "neuro12-cloud" {
				h.Cloud = fmt.Sprintf("облако мозг-12 v%v на порту %d", d["version"], p)
				continue
			}
		}
		if p == 7000 && h.App == "" {
			if _, code, err := getJSON(base+"/api/state", 2500*time.Millisecond); err == nil && code == 200 {
				h.App = "neuro_12 (старая версия) на порту 7000"
			}
		}
	}
	h.Name = lookupName(ip)
	low := strings.ToLower(h.Name + " " + h.SSH)
	switch {
	case h.App != "":
		h.Kind, h.Score = "плата neuro_12 — приложение работает", 100
	case h.SSH != "" && strings.Contains(low, "debian") && (strings.Contains(low, "uno") || strings.Contains(low, "arduino")):
		h.Kind, h.Score = "похоже на Arduino UNO Q (Debian, SSH), приложение не запущено", 70
	case h.SSH != "" && strings.Contains(low, "debian"):
		h.Kind, h.Score = "Linux Debian с SSH — может быть плата (приложение не запущено)", 40
	case h.Cloud != "":
		h.Kind, h.Score = "облако мозг-12", 20
	case h.SSH != "":
		h.Kind, h.Score = "устройство с SSH", 10
	default:
		h.Kind, h.Score = "устройство (открыты порты)", 0
	}
	return h
}

func runScan() {
	if !setBusy("scan", true) {
		return
	}
	defer setBusy("scan", false)
	c := getCfg()
	targets, note := scanTargets(c.Range)
	ports := parsePorts(c.Ports)
	t0 := time.Now()
	mu.Lock()
	scan = ScanState{Running: true, Total: len(targets), Note: note}
	mu.Unlock()
	logf("Сканер: %d адресов, порты %v%s", len(targets), ports, map[bool]string{true: " (" + note + ")", false: ""}[note != ""])
	jobs := make(chan string)
	var wg sync.WaitGroup
	for w := 0; w < 128; w++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for ip := range jobs {
				h := probeHost(ip, ports)
				mu.Lock()
				scan.Done++
				scan.Secs = int(time.Since(t0).Seconds())
				if h != nil {
					scan.Hosts = append(scan.Hosts, *h)
				}
				mu.Unlock()
			}
		}()
	}
	for _, ip := range targets {
		jobs <- ip
	}
	close(jobs)
	wg.Wait()
	arp := arpTable()
	mu.Lock()
	for i := range scan.Hosts {
		scan.Hosts[i].MAC = arp[scan.Hosts[i].IP]
	}
	mu.Unlock()
	// v3.155: плата узнаётся по MAC (в том числе перевёрнутому во время спина)
	for i := range scan.Hosts {
		h := &scan.Hosts[i]
		if h.MAC == "" {
			continue
		}
		if h.Score == 100 {
			learnMAC(h.MAC, h.IP)
		}
		if isBoardMAC(h.MAC) {
			learnMAC(h.MAC, h.IP)
			if h.Score < 100 {
				h.Kind, h.Score = "плата — узнана по MAC ("+macRole(h.MAC)+"), приложение не отвечает", 90
			} else {
				h.Kind = "плата neuro_12 — приложение работает · " + macRole(h.MAC)
			}
		}
	}
	mu.Lock()
	sort.Slice(scan.Hosts, func(i, j int) bool {
		a, b := scan.Hosts[i], scan.Hosts[j]
		if a.Score != b.Score {
			return a.Score > b.Score
		}
		return ipToU(net.ParseIP(a.IP)) < ipToU(net.ParseIP(b.IP))
	})
	scan.Running = false
	scan.Secs = int(time.Since(t0).Seconds())
	hosts := append([]Host(nil), scan.Hosts...)
	mu.Unlock()
	var boards, maybe, known []Host
	for _, h := range hosts {
		if h.Score == 100 {
			boards = append(boards, h)
			if isBoardMAC(h.MAC) {
				known = append(known, h)
			}
		} else if h.Score == 90 {
			known = append(known, h)
		} else if h.Score >= 40 {
			maybe = append(maybe, h)
		}
	}
	// v3.155: несколько плат — берём ту, что узнана по MAC
	if len(boards) > 1 && len(known) == 1 && known[0].Score == 100 {
		boards = known
	}
	if len(boards) == 0 && len(known) == 1 {
		boards = known
		logf("Плата узнана по MAC (%s): %s — приложение на ней пока не отвечает", macRole(known[0].MAC), known[0].IP)
	}
	logf("Сканер: готово за %d с, найдено устройств: %d (плат с приложением: %d)", int(time.Since(t0).Seconds()), len(hosts), len(boards))
	pick := ""
	if len(boards) == 1 {
		pick = boards[0].IP
		logf("Адрес платы определён автоматически: %s (%s)", pick, map[bool]string{true: boards[0].App, false: boards[0].Kind}[boards[0].App != ""])
		setPredict(pick, "сканер: "+boards[0].Kind)
	} else if len(boards) == 0 && len(maybe) == 1 {
		pick = maybe[0].IP
		logf("Плата, вероятно, %s (%s) — адрес подставлен; приложение на ней пока не запущено", pick, maybe[0].Kind)
	} else if len(boards) > 1 {
		logf("Плат с приложением несколько — выбери нужную в таблице (кнопка «Выбрать»).")
	} else if len(hosts) == 0 {
		logf("Ничего не найдено. Компьютер и плата в одной сети? Можно задать диапазон вручную, например 192.168.1.0/24.")
	}
	if pick != "" {
		mu.Lock()
		cfg.IP = pick
		mu.Unlock()
		saveCfg()
	}
}

// ----------------------------------------------------------------- плата
func configureBoard(ip string) {
	c := getCfg()
	url := fmt.Sprintf("http://%s:%d", pcIPFor(ip), cloudPort(c.Cloud))
	tok := cloudToken(c.Cloud)
	body, _ := json.Marshal(map[string]interface{}{"cloud_url": url, "token": tok, "brain_on": true})
	cl := &http.Client{Timeout: 8 * time.Second, Transport: &http.Transport{Proxy: nil}}
	r, err := cl.Post("http://"+ip+":7000/api/settings", "application/json", bytes.NewReader(body))
	if err != nil {
		logf("Плата %s: не прописал облако (%v). Приложение на плате запущено?", ip, err)
		return
	}
	r.Body.Close()
	logf("Плата %s: облако %s и мозг-токен прописаны (HTTP %d)", ip, url, r.StatusCode)
}

func q(s string) string { return "\"" + strings.ReplaceAll(s, "\"", "\\\"") + "\"" }

func copyDir(src, dst string) error {
	return filepath.Walk(src, func(p string, fi os.FileInfo, err error) error {
		if err != nil {
			return err
		}
		rel, _ := filepath.Rel(src, p)
		name := fi.Name()
		if fi.IsDir() {
			if rel != "." && (name == "__pycache__" || name == ".cache" || name == ".git") {
				return filepath.SkipDir
			}
			return os.MkdirAll(filepath.Join(dst, rel), 0755)
		}
		if strings.HasSuffix(name, ".pyc") || strings.HasSuffix(name, ".bak") {
			return nil
		}
		b, err := os.ReadFile(p)
		if err != nil {
			return err
		}
		return os.WriteFile(filepath.Join(dst, rel), b, 0644)
	})
}

func needIP() string {
	c := getCfg()
	if c.IP == "" {
		logf("Нет адреса платы — запускаю сканер сети…")
		runScan()
		c = getCfg()
	}
	if c.IP == "" {
		logf("Адрес платы не найден. Впиши IP вручную или выбери строку в таблице сканера.")
	}
	return c.IP
}

func runAll() {
	if !setBusy("run", true) {
		logf("Запуск уже идёт…")
		return
	}
	defer setBusy("run", false)
	defer setStep("")
	// v3.152: идёт смена MAC — связь с платой рвётся, ждём её окончания
	for i := 0; i < 60; i++ {
		mu.Lock()
		m := busy["mac"]
		mu.Unlock()
		if !m {
			break
		}
		if i == 0 {
			setStep("Жду окончания смены MAC…")
		}
		time.Sleep(2 * time.Second)
	}
	// v3.154: быстрый спин на плате мешает копированию — останавливаем на время
	if spinOn {
		spinStop()
	}
	setStep("ШАГ 0: облако на этом компьютере")
	if !startCloud() {
		logf("«Запустить всё» остановлен: облако не запустилось (⚙ → папка облака).")
		return
	}
	ip := needIP()
	if ip == "" {
		return
	}
	c := getCfg()
	if !exists(filepath.Join(c.App, "app.yaml")) {
		logf("В папке приложения платы нет app.yaml — укажи папку neuro_12 (из архива neuro_12-v….zip).")
		return
	}
	cu := fmt.Sprintf("http://%s:%d", pcIPFor(ip), cloudPort(c.Cloud))
	tok := strings.NewReplacer("'", "", "\"", "", " ", "").Replace(cloudToken(c.Cloud))
	logf("Облако для платы: %s%s", cu, map[bool]string{true: ", мозг-токен из data\\token.txt", false: ", токена нет (открытый режим)"}[tok != ""])
	stage := filepath.Join(os.TempDir(), "n12stage")
	os.RemoveAll(stage)
	if err := copyDir(c.App, filepath.Join(stage, "neuro_12.new")); err != nil {
		logf("Не скопировал приложение во временную папку: %v", err)
		return
	}
	os.WriteFile(filepath.Join(stage, "n12_setcfg.py"), []byte(setcfgPy), 0644)
	os.WriteFile(filepath.Join(stage, "n12_deploy.sh"), []byte(strings.ReplaceAll(deploySh, "\r\n", "\n")), 0644)
	host := c.User + "@" + ip
	opt := "-o StrictHostKeyChecking=accept-new "
	if ensureKey() {
		if !runHidden(stage, cu, tok) {
			setStep("")
			return
		}
	} else {
		// запасной путь — окно, где каждый шаг подписан
		logf("Работаю через окно консоли (вход по ключу не настроен).")
		consoleSteps("Мозг-12: запуск на плате", []cstep{
			{"подготовка папки на плате", "ssh " + opt + host + " \"mkdir -p ~/ArduinoApps && rm -rf ~/ArduinoApps/neuro_12.new\""},
			{"копирую приложение на плату (может занять минуту)", "scp " + opt + "-r " + q(filepath.Join(stage, "neuro_12.new")) + " " +
				q(filepath.Join(stage, "n12_setcfg.py")) + " " + q(filepath.Join(stage, "n12_deploy.sh")) + " " + host + ":ArduinoApps/"},
			{"сборка скетча и запуск (несколько минут)", "ssh -t " + opt + host + " \"bash ~/ArduinoApps/n12_deploy.sh '" + cu + "' '" + tok + "'\""},
		})
	}
	setStep("ШАГ 4 из 4: жду интерфейс платы")
	logf("Жду интерфейс платы http://%s:7000 (сборка скетча — несколько минут)…", ip)
	for t0 := time.Now(); time.Since(t0) < 6*time.Minute; {
		if _, code, err := getJSON("http://"+ip+":7000/api/state", 2*time.Second); err == nil && code < 500 {
			configureBoard(ip)
			setStep("")
			logf("Плата работает — вкладка «Плата» открывает её интерфейс в этом окне.")
			return
		}
		time.Sleep(4 * time.Second)
	}
	setStep("")
	logf("Плата не открыла интерфейс за 6 минут. Журнал запуска: ssh %s \"tail -50 ~/ArduinoApps/neuro_12/neuro12_start.log\"", host)
}

func fixBuild() {
	logf("Чиню сборку скетча: стоп приложения, удаление кэшей сборки, сборка заново…")
	boardRun("Починка сборки скетча",
		"A=~/ArduinoApps/neuro_12; arduino-app-cli app stop $A; rm -rf $A/.cache ~/.cache/arduino; echo; df -h ~ | tail -1; echo; arduino-app-cli app start $A 2>&1 | tee -a $A/neuro12_start.log",
		30*time.Minute)
}

func keySetup() {
	c := getCfg()
	if c.IP == "" {
		logf("Нет адреса платы.")
		return
	}
	home, _ := os.UserHomeDir()
	os.MkdirAll(filepath.Join(home, ".ssh"), 0700)
	key := filepath.Join(home, ".ssh", "id_ed25519")
	gen := ""
	if !exists(key) {
		gen = "ssh-keygen -t ed25519 -N \"\" -f " + q(key)
	}
	steps := []cstep{}
	if gen != "" {
		steps = append(steps, cstep{"создаю ключ этого компьютера", strings.TrimSpace(gen)})
	}
	steps = append(steps, cstep{"передаю ключ плате " + c.IP + " — введите пароль платы",
		catCmd + " " + q(key+".pub") + " | ssh -o StrictHostKeyChecking=accept-new " + c.User + "@" + c.IP +
			" \"mkdir -p ~/.ssh && chmod 700 ~/.ssh && cat >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys && echo КЛЮЧ ПРИНЯТ\""})
	consoleSteps("Мозг-12: вход по ключу", steps)
	if keyOK() {
		logf("Вход по ключу работает — дальше без окон и паролей.")
	} else {
		logf("Вход по ключу не проверился — пароль был верный?")
	}
}

// ------------------------------------------------------------- мониторинг
var lastLearn time.Time

func monitor() {
	miss, lastAuto := 0, time.Time{}
	lastMacRot = time.Now() // v3.152: не вращать MAC сразу при открытии лаунчера
	for {
		c := getCfg()
		cs := map[string]interface{}{"ok": false, "port": cloudPort(c.Cloud), "url": cloudURL(), "view": cloudView(), "dir_ok": exists(filepath.Join(c.Cloud, "server", "app.py")),
			"lan": fmt.Sprintf("http://%s:%d", pcIPFor(c.IP), cloudPort(c.Cloud))}
		if d, code, err := getJSON(fmt.Sprintf("http://127.0.0.1:%d/api/health", cloudPort(c.Cloud)), 2*time.Second); err == nil && code == 200 {
			cs["ok"] = true
			if d != nil {
				cs["version"] = d["version"]
			}
		}
		bs := map[string]interface{}{"ok": false, "ip": c.IP}
		if c.IP != "" {
			if d, code, err := getJSON("http://"+c.IP+":7000/api/alive", 2500*time.Millisecond); err == nil && code == 200 && d != nil {
				bs["ok"] = true
				for _, k := range []string{"ver", "sketch_seen", "sketch_alive", "stuck", "up"} {
					bs[k] = d[k]
				}
			} else if err == nil && code == 404 {
				bs["ok"] = true
				bs["ver"] = "старая"
			} else {
				bs["ssh"] = portOpen(c.IP, 22, 800*time.Millisecond)
			}
		}
		mu.Lock()
		cloudSt, boardSt = cs, bs
		running := busy["run"] || busy["scan"] || busy["mac"]
		macAuto := on2(cfg.MacAuto) // v3.152: по умолчанию ВЫКЛЮЧЕНО, как и галочка
		gap := macGap(cfg)
		hz := macHz(cfg)
		cc := cfg
		mu.Unlock()
		// v3.154: быстрый спин (чаще раза в минуту) — цикл на самой плате
		macTick(cc, bs["ok"] == true || bs["ssh"] == true, running)
		// медленное авто-вращение MAC: лаунчер поворачивает сам
		if macAuto && gap >= macFastGap && !running && c.IP != "" && bs["ok"] == true && time.Since(lastMacRot) > gap {
			lastMacRot = time.Now()
			logf("MAC: спин %s — смена адреса (раз в %s)", fmtHz(hz), gap.Round(time.Second))
			go macJob("rotate", true)
		}
		// v3.138: плата пропала по старому адресу (роутер выдал ей новый IP)
		// — сразу ищем её сканером и подставляем новый адрес
		if c.IP != "" && bs["ok"] != true && bs["ssh"] != true {
			miss++
		} else {
			miss = 0
		}
		// v3.155: плата не ответила — сразу пробуем предсказать её адрес по MAC
		if c.IP != "" && bs["ok"] != true && bs["ssh"] != true && !running && time.Since(lastPredict) > 5*time.Second {
			go predictBoard("по " + c.IP + " не отвечает")
		}
		if bs["ok"] == true && time.Since(lastLearn) > time.Minute {
			lastLearn = time.Now()
			go learnFromBoard(c.IP)
		}
		need := 3
		if spinOn {
			need = 15 // во время спина плата то и дело пропадает — это нормально
		}
		if miss >= need && !running && time.Since(lastAuto) > 90*time.Second {
			lastAuto = time.Now()
			miss = 0
			logf("Плата не отвечает по %s — ищу её в сети (адрес мог смениться)…", c.IP)
			go runScan()
		}
		time.Sleep(4 * time.Second)
	}
}

// -------------------------------------------------------------- HTTP API
func api(w http.ResponseWriter, r *http.Request) {
	if r.Header.Get("X-N12") != secret || !strings.HasPrefix(r.Host, "127.0.0.1:") {
		http.Error(w, "forbidden", 403)
		return
	}
	var in map[string]interface{}
	if r.Method == "POST" {
		json.NewDecoder(io.LimitReader(r.Body, 1<<16)).Decode(&in)
	}
	s := func(k string) string { v, _ := in[k].(string); return strings.TrimSpace(v) }
	out := map[string]interface{}{"ok": true}
	switch r.URL.Path {
	case "/api/state":
		since, _ := strconv.Atoi(r.URL.Query().Get("since"))
		mu.Lock()
		i := since - logBase
		if i < 0 {
			i = 0
		}
		if i > len(logs) {
			i = len(logs)
		}
		out["logs"] = append([]string(nil), logs[i:]...)
		out["next"] = logBase + len(logs)
		out["cfg"] = cfg
		out["cloud"] = cloudSt
		out["board"] = boardSt
		out["scan"] = scan
		bc := map[string]bool{}
		for k, v := range busy { // копия: карта меняется в других потоках
			bc[k] = v
		}
		out["busy"] = bc
		out["ver"] = VER
		out["os"] = osName()
		if pendingNav != "" { // v3.159: переход, заданный лаунчером (Linux: окно браузера)
			out["nav"] = pendingNav
			pendingNav = ""
		}
		out["bproxy"] = boardProxyOn
		mc := map[string]interface{}{}
		for k, v := range macSt {
			mc[k] = v
		}
		out["mac"] = mc
		pc := map[string]interface{}{}
		for k, v := range predSt {
			pc[k] = v
		}
		out["predict"] = pc
		mu.Unlock()
		out["pkg"] = pkgState() // v3.161: установка из ZIP
		out["apk"] = apkState() // v3.161: APK для телефона
		out["step"] = getStep()
		netMu.Lock()
		out["net"] = netSt
		out["wifi"] = wifiL
		netMu.Unlock()
	case "/api/save":
		mu.Lock()
		for k, p := range map[string]*string{"cloud": &cfg.Cloud, "app": &cfg.App, "ip": &cfg.IP, "user": &cfg.User, "ports": &cfg.Ports, "range": &cfg.Range} {
			if _, ok := in[k]; ok {
				*p = s(k)
			}
		}
		if v, ok := in["auto_cloud"].(bool); ok {
			cfg.AutoCloud = bptr(v)
		}
		if v, ok := in["open_cloud"].(bool); ok {
			cfg.OpenCloud = bptr(v)
		}
		if v, ok := in["mac_auto"].(bool); ok {
			cfg.MacAuto = bptr(v)
		}
		if v, ok := in["mac_pc"].(bool); ok {
			cfg.MacPC = bptr(v)
		}
		if v, ok := in["mac_every"].(float64); ok {
			cfg.MacEvery = clampEvery(int(v))
		}
		if v, ok := in["mac_hz"].(float64); ok {
			cfg.MacHz = clampHz(v)
		}
		mu.Unlock()
		saveCfg()
	case "/api/pick":
		p := pickFolder(map[string]string{"cloud": "Папка облака neuro12-cloud (в ней server/app.py)", "app": "Папка приложения платы neuro_12 (в ней app.yaml)"}[s("which")])
		out["path"] = p
		if p != "" {
			mu.Lock()
			if s("which") == "cloud" {
				cfg.Cloud = p
			} else {
				cfg.App = p
			}
			mu.Unlock()
			saveCfg()
		}
	case "/api/action":
		ip := s("ip")
		switch s("do") {
		case "cloud":
			go startCloud()
		case "runall":
			go runAll()
		case "scan":
			go runScan()
		case "stop":
			go boardRun("Остановка приложения на плате", "arduino-app-cli app stop ~/ArduinoApps/neuro_12", 3*time.Minute)
		case "net":
			go func() {
				if !setBusy("net", true) {
					return
				}
				defer setBusy("net", false)
				st := netCheck()
				if e, _ := st["err"].(string); e != "" {
					logf("Интернет платы: %s", e)
				} else if st["internet"] == true {
					logf("Интернет платы: есть (DNS %s)", map[bool]string{true: "работает", false: "НЕ работает"}[st["dns"] == true])
				} else {
					logf("Интернет платы: нет")
				}
			}()
		case "wifiscan":
			go wifiScan()
		case "wificonnect":
			go wifiConnect(s("ssid"), func() string { v, _ := in["pass"].(string); return v }(), in["hidden"] == true)
		case "key":
			go keySetup()
		case "mac":
			go macJob("rotate", false)
		case "macmirror":
			go macJob("mirror", false)
		case "macrestore":
			go macJob("restore", false)
		case "predict":
			go func() {
				if predictBoard("") == "" {
					logf("Предсказание: ни один известный адрес платы не ответил — запускаю сканер")
					runScan()
				}
			}()
		case "macshow":
			go macJob("show", false)
		case "fixbuild":
			go fixBuild()
		case "zip": // v3.161: общий архив — облако, плата, APK
			dl := ""
			if d := downloadDirs(); len(d) > 0 {
				dl = d[0]
			}
			zp := pickFile("Общий архив Мозг-12 (neuro12-all-v….zip) или архив облака/платы", dl)
			out["path"] = zp
			if zp != "" {
				go installZip(zp, in["deploy"] == true)
			}
		case "apksave":
			go apkSave(s("path"))
		case "apkshare":
			go apkShare(s("path"))
		case "apkstop":
			apkShareStop()
		case "watchdog":
			go func() {
				sudoRun("Установка сторожа платы", "bash ~/ArduinoApps/neuro_12/deploy_cli/install_autostart.sh watchdog && systemctl restart neuro12", 10*time.Minute)
			}()
		case "use":
			if net.ParseIP(ip) != nil {
				mu.Lock()
				cfg.IP = ip
				mu.Unlock()
				saveCfg()
				logf("Адрес платы: %s", ip)
			}
		case "configure":
			if net.ParseIP(ip) != nil {
				go configureBoard(ip)
			}
		case "browser":
			u := s("url")
			if strings.HasPrefix(u, "http://") || strings.HasPrefix(u, "https://") {
				openURL(u)
			}
		default:
			out["ok"] = false
		}
	default:
		http.NotFound(w, r)
		return
	}
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.Header().Set("Cache-Control", "no-store")
	json.NewEncoder(w).Encode(out)
}

func serveUI(w http.ResponseWriter, r *http.Request) {
	if r.URL.Path != "/" {
		http.NotFound(w, r)
		return
	}
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	w.Header().Set("Cache-Control", "no-store")
	io.WriteString(w, uiFix(strings.Replace(uiHTML, "__N12_SECRET__", secret, 1)))
}

// самообновление: облако при обновлении кладёт neuro12-start.exe.new рядом
// (запущенный .exe заменить нельзя) — меняемся при следующем старте
func selfUpdate(exe string) bool {
	nw := exe + ".new"
	if !exists(nw) {
		return false
	}
	old := exe + ".old"
	os.Remove(old)
	if os.Rename(exe, old) != nil {
		return false
	}
	if os.Rename(nw, exe) != nil {
		os.Rename(old, exe)
		return false
	}
	exec.Command(exe, os.Args[1:]...).Start()
	return true
}

func main() {
	for _, a := range os.Args[1:] {
		if a == "--version" || a == "-v" {
			fmt.Println("v" + VER)
			return
		}
	}
	dpiAware()
	exe, _ := os.Executable()
	exeDir = filepath.Dir(exe)
	if selfUpdate(exe) {
		return
	}
	os.Remove(exe + ".old")
	loadCfg()
	if otherInstance() {
		return
	}
	b := make([]byte, 16)
	rand.Read(b)
	secret = hex.EncodeToString(b)
	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		msgBox("Мозг-12", "Не открыть локальный порт: "+err.Error())
		return
	}
	homeURL = "http://" + ln.Addr().String() + "/"
	mux := http.NewServeMux()
	mux.HandleFunc("/api/", api)
	mux.HandleFunc("/", serveUI)
	extraRoutes(mux)
	go http.Serve(ln, touchMW(mux))
	go monitor()

	logf("Лаунчер мозг-12 v%s (%s). Облако и плата открываются в этом окне (вкладки сверху).", VER, osName())
	c := getCfg()
	startOpen := false
	for _, a := range os.Args[1:] {
		if a == "--cloud" || a == "/cloud" {
			startOpen = true
		}
	}
	if on(c.AutoCloud) && c.Cloud != "" {
		go func() {
			ok := startCloud()
			for i := 0; i < 100 && !uiReady(); i++ {
				time.Sleep(100 * time.Millisecond)
			}
			// облако — сразу в окне, если адрес платы уже известен (иначе
			// остаёмся на «Запуске»: там сканер ищет плату)
			if ok && uiReady() && (startOpen || (on(getCfg().OpenCloud) && getCfg().IP != "")) {
				mu.Lock()
				first := firstNav
				firstNav = false
				mu.Unlock()
				if first {
					navigate(cloudView())
				}
			}
		}()
	}
	if c.IP == "" {
		go runScan()
	}
	runUI()
}

// v3.155: на страницах облака и платы вместо кнопки «⟵ Запуск» — та же
// верхняя панель, что на главной (Мозг-12 · Запуск · Облако · Плата).
// Страница сдвигается вниз на высоту панели, закреплённые сверху элементы
// страницы — тоже, чтобы панель ничего не закрывала.
const homeBtnJS = `(function(){try{
if(location.href.indexOf("__HOME__")===0||window.top!==window)return;
var H="__HOME__",PX=46,isB=__ISB__;
function tab(t,href,on){var a=document.createElement("a");a.href=href;a.textContent=t;
a.style.cssText="padding:6px 14px;border-radius:10px;text-decoration:none;font:500 13.5px Segoe UI,system-ui,sans-serif;border:1px solid "+(on?"#243043":"transparent")+";color:"+(on?"#eef2f8":"#8b95ab")+";background:"+(on?"#151a25":"none");
a.onmouseenter=function(){if(!on)a.style.color="#eef2f8"};a.onmouseleave=function(){if(!on)a.style.color="#8b95ab"};return a;}
function shift(){var all=document.body.getElementsByTagName("*");for(var i=0;i<all.length&&i<4000;i++){var e=all[i];if(e.id==="n12bar"||e.dataset.n12s)continue;
var cs=getComputedStyle(e);if((cs.position==="fixed"||cs.position==="sticky")&&parseFloat(cs.top)<PX&&cs.top!=="auto"){e.style.top=(parseFloat(cs.top)+PX)+"px";e.dataset.n12s="1";}}}
function add(){if(document.getElementById("n12bar"))return;var b=document.createElement("div");b.id="n12bar";
b.style.cssText="position:fixed;left:0;right:0;top:0;height:"+PX+"px;z-index:2147483647;display:flex;align-items:center;gap:4px;padding:0 18px;background:rgba(15,19,28,.96);border-bottom:1px solid #243043;box-shadow:0 2px 14px rgba(0,0,0,.35);backdrop-filter:blur(10px);box-sizing:border-box";
var t=document.createElement("b");t.textContent="🧠 Мозг-12";t.style.cssText="font:600 15px Segoe UI,system-ui,sans-serif;margin-right:16px;background:linear-gradient(90deg,#2dd4bf,#818cf8);-webkit-background-clip:text;background-clip:text;color:transparent;cursor:pointer";t.onclick=function(){location.href=H};
b.appendChild(t);b.appendChild(tab("Запуск",H,false));b.appendChild(tab("Облако",H+"?go=cloud",!isB));b.appendChild(tab("Плата",H+"?go=board",isB));
document.body.appendChild(b);
var st=document.createElement("style");st.id="n12fit";st.textContent="html{padding-top:"+PX+"px!important;box-sizing:border-box!important;height:100%}";document.head.appendChild(st);
fit();shift();setTimeout(function(){fit();shift()},600);setTimeout(function(){fit();shift()},2500);window.addEventListener("resize",fit);}
function fit(){var H=window.innerHeight,q=[document.body],n=0;while(q.length&&n<300){var e=q.shift();n++;if(e.id==="n12bar")continue;
if(e.dataset.n12h||e.getBoundingClientRect().height>=H-PX-2){var cs=getComputedStyle(e);if(e.dataset.n12h||(cs.position!=="fixed"&&parseFloat(cs.height)>=H-2)){e.style.setProperty("height","calc(100vh - "+PX+"px)","important");e.style.setProperty("max-height","calc(100vh - "+PX+"px)","important");e.dataset.n12h="1";}
for(var i=0;i<e.children.length;i++)q.push(e.children[i]);}}}
if(document.body)add();else document.addEventListener("DOMContentLoaded",add);}catch(e){}})();`
