//go:build linux

// os_linux.go — v3.159: лаунчер «Star Gate» для Linux (РЕД ОС, Ubuntu и другие).
//
// Окно. В Linux нет WebView2, а встроенный WebKitGTK потребовал бы cgo и свою
// сборку под каждую версию дистрибутива. Поэтому окно лаунчера — браузер на
// Chromium (Chromium, Яндекс Браузер, Chromium-GOST, Chrome, Edge…) в режиме
// приложения (--app: без вкладок и адресной строки) со своим профилем. Если
// такого нет — Firefox или браузер по умолчанию (xdg-open).
//
// Облако и плата «в этом окне». Чтобы на их страницах была та же верхняя
// панель (Star Gate · Запуск · Облако · Плата), лаунчер открывает их через
// локальный мост (обратный прокси) и добавляет панель в каждую страницу:
//   - облако — 127.0.0.1:<свободный порт>;
//   - плата a.b.c.d — 127.42.c.d:7000 (у каждой платы свой адрес — куки и
//     вкладки разных плат не путаются).
//
// Остальное: консоль — окно терминала (gnome-terminal, mate-terminal, konsole,
// xfce4-terminal…), права администратора — pkexec (окно пароля), выбор папки —
// zenity/kdialog, таблица ARP — /proc/net/arp, MAC компьютера — NetworkManager.
package main

import (
	"bufio"
	"bytes"
	_ "embed"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/http/httputil"
	"net/url"
	"os"
	"os/exec"
	"os/signal"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"syscall"
	"time"
)

//go:embed n12_mac_pc.sh
var macPCsh string

const (
	cloudHint    = "~/.local/share/neuro12-cloud"
	catCmd       = "cat"
	boardProxyOn = true
)

func osName() string {
	b, _ := os.ReadFile("/etc/os-release")
	for _, l := range strings.Split(string(b), "\n") {
		if strings.HasPrefix(l, "PRETTY_NAME=") {
			return "Linux: " + strings.Trim(strings.TrimPrefix(l, "PRETTY_NAME="), `"`)
		}
	}
	return "Linux"
}

func dataHome() string {
	if d := os.Getenv("XDG_DATA_HOME"); d != "" {
		return d
	}
	h, _ := os.UserHomeDir()
	return filepath.Join(h, ".local", "share")
}

func launcherDir() string { return filepath.Join(dataHome(), "neuro12-launcher") }

func cloudCandidates() []string {
	h, _ := os.UserHomeDir()
	return []string{filepath.Join(dataHome(), "neuro12-cloud"), filepath.Join(h, "neuro12-cloud"), "/opt/neuro12-cloud"}
}

// downloadDirs — «Загрузки» по xdg-user-dirs (у русской системы — ~/Загрузки)
func downloadDirs() []string {
	h, _ := os.UserHomeDir()
	out := []string{}
	if b, err := os.ReadFile(filepath.Join(h, ".config", "user-dirs.dirs")); err == nil {
		for _, l := range strings.Split(string(b), "\n") {
			if strings.HasPrefix(l, "XDG_DOWNLOAD_DIR=") {
				v := strings.Trim(strings.TrimPrefix(l, "XDG_DOWNLOAD_DIR="), `"`)
				out = append(out, strings.ReplaceAll(v, "$HOME", h))
			}
		}
	}
	return append(out, filepath.Join(h, "Загрузки"), filepath.Join(h, "Downloads"), h)
}

// hidden — фоновый процесс в своей сессии: без терминала (ssh не застрянет
// на вопросе о пароле) и не умирает, если закрыли терминал, откуда запущен лаунчер.
func hidden(cmd *exec.Cmd) *exec.Cmd {
	cmd.SysProcAttr = &syscall.SysProcAttr{Setsid: true}
	return cmd
}

func dpiAware() {}

func has(name string) bool { _, err := exec.LookPath(name); return err == nil }

func graphical() bool { return os.Getenv("DISPLAY") != "" || os.Getenv("WAYLAND_DISPLAY") != "" }

func msgBox(title, text string) {
	fmt.Fprintln(os.Stderr, title+": "+text)
	switch {
	case !graphical():
	case has("zenity"):
		exec.Command("zenity", "--info", "--no-wrap", "--title", title, "--text", text).Run()
	case has("kdialog"):
		exec.Command("kdialog", "--title", title, "--msgbox", text).Run()
	case has("notify-send"):
		exec.Command("notify-send", title, text).Run()
	}
}

// pickFolder — диалог выбора папки (zenity в GNOME/MATE, kdialog в KDE)
func pickFolder(title string) string {
	h, _ := os.UserHomeDir()
	var cmd *exec.Cmd
	switch {
	case has("zenity"):
		cmd = exec.Command("zenity", "--file-selection", "--directory", "--title", title, "--filename", h+"/")
	case has("kdialog"):
		cmd = exec.Command("kdialog", "--title", title, "--getexistingdirectory", h)
	case has("yad"):
		cmd = exec.Command("yad", "--file", "--directory", "--title", title)
	default:
		logf("Нет диалога выбора папки (поставь zenity) — впиши путь в поле вручную.")
		return ""
	}
	out, err := cmd.Output()
	if err != nil {
		return ""
	}
	return strings.TrimSpace(string(out))
}

func openURL(u string) {
	for _, c := range []string{"xdg-open", "gio", "sensible-browser"} {
		if has(c) {
			args := []string{u}
			if c == "gio" {
				args = []string{"open", u}
			}
			if hidden(exec.Command(c, args...)).Start() == nil {
				return
			}
		}
	}
	logf("Открой в браузере: %s", u)
}

// ------------------------------------------------------------------ облако
func findPython() string {
	for _, c := range []string{"python3", "python"} {
		if p, err := exec.LookPath(c); err == nil {
			return p
		}
	}
	return ""
}

func writable(dir string) bool {
	f := filepath.Join(dir, ".n12w")
	if os.WriteFile(f, nil, 0644) != nil {
		return false
	}
	os.Remove(f)
	return true
}

// launchCloud — сервис systemd (если облако поставлено сервисом) или python3
func launchCloud(dir string) bool {
	h, _ := os.UserHomeDir()
	if exists(filepath.Join(h, ".config", "systemd", "user", "neuro12-cloud.service")) && has("systemctl") {
		if err := hidden(exec.Command("systemctl", "--user", "start", "neuro12-cloud")).Run(); err == nil {
			logf("Облако запускается (сервис пользователя neuro12-cloud)…")
			return true
		}
	}
	os.MkdirAll(filepath.Join(dir, "data"), 0755)
	if !writable(filepath.Join(dir, "data")) {
		// /opt/neuro12-cloud от install.sh: системный сервис
		if has("systemctl") {
			if hidden(exec.Command("systemctl", "--no-ask-password", "start", "neuro12-cloud")).Run() == nil {
				logf("Облако запускается (системный сервис neuro12-cloud)…")
				return true
			}
			if has("pkexec") && graphical() {
				setStep("Облако: подтвердите пароль администратора (запуск сервиса neuro12-cloud)")
				if exec.Command("pkexec", "systemctl", "start", "neuro12-cloud").Run() == nil {
					logf("Облако запускается (системный сервис neuro12-cloud)…")
					return true
				}
			}
		}
		logf("Облако: нет прав на папку %s. Запусти сервис: sudo systemctl start neuro12-cloud", dir)
		return false
	}
	py := findPython()
	if py == "" {
		logf("Python 3 не найден. РЕД ОС: sudo dnf install python3 · Ubuntu: sudo apt install python3 (или install-linux.sh).")
		return false
	}
	lf, _ := os.OpenFile(filepath.Join(dir, "data", "launcher_cloud.log"), os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0644)
	cmd := hidden(exec.Command(py, filepath.Join(dir, "server", "app.py")))
	cmd.Dir = dir
	cmd.Env = append(os.Environ(), "NEURO_DATA="+filepath.Join(dir, "data"),
		"NEURO_PORT="+strconv.Itoa(cloudPort(dir)), "PYTHONUNBUFFERED=1")
	if lf != nil {
		cmd.Stdout, cmd.Stderr = lf, lf
	}
	if err := cmd.Start(); err != nil {
		logf("Облако: не запустилось: %v", err)
		return false
	}
	go func() { cmd.Wait(); lf.Close() }()
	logf("Облако запускается: %s", py)
	return true
}

// ---------------------------------------------------------------- сеть
func arpTable() map[string]string {
	m := map[string]string{}
	f, err := os.Open("/proc/net/arp")
	if err != nil {
		return m
	}
	defer f.Close()
	sc := bufio.NewScanner(f)
	sc.Scan() // заголовок
	for sc.Scan() {
		x := strings.Fields(sc.Text())
		// IP address  HW type  Flags  HW address  Mask  Device
		if len(x) < 4 || x[2] == "0x0" || x[3] == "00:00:00:00:00:00" || net.ParseIP(x[0]) == nil {
			continue
		}
		m[x[0]] = strings.ToUpper(x[3])
	}
	return m
}

// ------------------------------------------------------- окно терминала
var terms = [][]string{
	{"gnome-terminal", "--wait", "--"},
	{"mate-terminal", "--disable-factory", "-x"},
	{"konsole", "--nofork", "-e"},
	{"xfce4-terminal", "--disable-server", "-x"},
	{"x-terminal-emulator", "-e"},
	{"tilix", "-e"},
	{"terminator", "-x"},
	{"lxterminal", "-e"},
	{"qterminal", "-e"},
	{"alacritty", "-e"},
	{"kitty"},
	{"xterm", "-e"},
}

// consoleSteps — запасное окно терминала: каждый шаг подписан, окно не стоит
// пустым. Ждём, пока скрипт закончится (метка-файл: закрыли окно — тоже конец).
func consoleSteps(title string, steps []cstep) {
	n := time.Now().UnixNano() % 1000000
	sh := filepath.Join(os.TempDir(), fmt.Sprintf("n12_%d.sh", n))
	mark := sh + ".done"
	var b strings.Builder
	b.WriteString("#!/usr/bin/env bash\ntrap 'touch " + shq(mark) + "' EXIT\n")
	b.WriteString("printf '\\033]0;%s\\007' " + shq(title) + "\n")
	b.WriteString("echo '============================================================'\n")
	b.WriteString("echo " + shq(" "+title) + "\n")
	b.WriteString("echo ' Если спросит пароль платы — введите его и нажмите Enter.'\n")
	b.WriteString("echo ' Символы пароля при вводе НЕ видны — это нормально.'\n")
	b.WriteString("echo '============================================================'\n")
	for i, s := range steps {
		b.WriteString(fmt.Sprintf("echo; echo %s; echo\n", shq(fmt.Sprintf(" ШАГ %d из %d: %s", i+1, len(steps), s.label))))
		b.WriteString(s.cmd + "\n")
		b.WriteString(fmt.Sprintf("if [ $? -ne 0 ]; then echo; echo ' !! ШАГ %d не удался — смотри строки выше'; fi\n", i+1))
	}
	b.WriteString("echo; echo ' Готово. Нажмите Enter, чтобы закрыть окно.'; read -r _\n")
	os.WriteFile(sh, []byte(b.String()), 0755)
	defer os.Remove(sh)
	defer os.Remove(mark)
	if !graphical() {
		logf("Нет графического сеанса — выполни в терминале: bash %s", sh)
		for i := 0; i < 3600 && !exists(mark); i++ {
			time.Sleep(time.Second)
		}
		return
	}
	for _, t := range terms {
		if !has(t[0]) {
			continue
		}
		args := append(append([]string{}, t[1:]...), "bash", sh)
		if t[0] == "x-terminal-emulator" || t[0] == "lxterminal" || t[0] == "qterminal" {
			args = append(append([]string{}, t[1:]...), "bash "+shq(sh))
		}
		cmd := exec.Command(t[0], args...)
		if err := cmd.Start(); err != nil {
			continue
		}
		done := make(chan error, 1)
		go func() { done <- cmd.Wait() }()
		t0, failed := time.Now(), false
		for !exists(mark) {
			select {
			case err := <-done:
				done = nil
				if err != nil && time.Since(t0) < 3*time.Second && !exists(mark) {
					failed = true
				}
			case <-time.After(500 * time.Millisecond):
			}
			if failed || time.Since(t0) > 3*time.Hour {
				break
			}
		}
		if failed {
			continue
		}
		return
	}
	logf("Не нашёл окно терминала (gnome-terminal, mate-terminal, konsole, xterm…). Выполни в терминале: bash %s", sh)
	for i := 0; i < 3600 && !exists(mark); i++ {
		time.Sleep(time.Second)
	}
}

// pcMacApply — зеркальный MAC компьютеру через NetworkManager (pkexec — окно
// пароля администратора). Ответ: ["OK", адаптер, старый MAC, новый MAC].
func pcMacApply(mode, pcip, mac string) ([]string, error) {
	os.MkdirAll(launcherDir(), 0755)
	res := filepath.Join(launcherDir(), "n12_mac_pc.txt")
	os.Remove(res)
	sh := filepath.Join(launcherDir(), "n12_mac_pc.sh")
	os.WriteFile(sh, []byte(macPCsh), 0755)
	args := []string{"bash", sh, res, pcip, mode, strings.ToLower(mac)}
	var cmd *exec.Cmd
	if os.Geteuid() != 0 && has("pkexec") && graphical() {
		setStep("MAC компьютера: введите пароль администратора в окне Linux")
		cmd = exec.Command("pkexec", args...)
	} else if os.Geteuid() != 0 && has("sudo") && exec.Command("sudo", "-n", "true").Run() == nil {
		cmd = exec.Command("sudo", append([]string{"-n"}, args...)...)
	} else {
		cmd = exec.Command(args[0], args[1:]...) // NetworkManager часто разрешает и без прав
	}
	if out, err := cmd.CombinedOutput(); err != nil && !exists(res) {
		return nil, fmt.Errorf("права администратора не получены (%v) %s", err, strings.TrimSpace(string(out)))
	}
	b, _ := os.ReadFile(res)
	r := strings.TrimSpace(string(b))
	f := strings.Split(r, "|")
	if len(f) < 2 || f[0] != "OK" {
		return nil, fmt.Errorf("не получилось: %s", strings.TrimPrefix(r, "ERR|"))
	}
	return f, nil
}

// ------------------------------------------------- интерфейс под Linux
func uiFix(s string) string {
	s = strings.NewReplacer(
		"Нужны права администратора (окно Windows).", "Нужны права администратора (окно пароля Linux).",
		"каждый раз — окно Windows для прав администратора", "каждый раз — окно пароля администратора",
	).Replace(s)
	i := strings.Index(s, `  ["Лаунчер (neuro12-start.exe)", [`)
	j := strings.Index(s, `  ["Облако (neuro12-cloud)", [`)
	if i > 0 && j > i {
		s = s[:i] + `  ["Лаунчер (neuro12-start, Linux)", [
    ["Go — стандартная библиотека", "BSD-3-Clause", "вшита в программу", "сохранить копирайт и текст лицензии"],
    ["Браузер окна: Chromium / Яндекс Браузер / Chrome / Firefox", "лицензии браузеров", "из репозитория ОС, в лаунчер не входит", "не распространяем — ничего делать не нужно"],
    ["OpenSSH, NetworkManager, polkit (pkexec), zenity", "BSD / GPL / LGPL", "из репозитория ОС, вызываются отдельными программами", "не распространяем — ничего делать не нужно"],
  ]],
` + s[j:]
	}
	return s
}

// ------------------------------------------- мост: облако и плата в окне
var (
	lastHit atomic.Int64
	brMu    sync.Mutex
	bridges = map[string]string{} // цель → адрес моста
	uiShown atomic.Bool
	runFile string
)

func touch()            { lastHit.Store(time.Now().Unix()) }
func uiReady() bool     { return uiShown.Load() }
func cloudView() string { return homeURL + "gocloud" }
func navigate(u string) { mu.Lock(); pendingNav = u; mu.Unlock() }
func touchMW(h http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { touch(); h.ServeHTTP(w, r) })
}

func barJS(isB bool) string {
	js := strings.ReplaceAll(homeBtnJS, "__HOME__", homeURL)
	return strings.ReplaceAll(js, "__ISB__", strconv.FormatBool(isB))
}

func injectBar(b []byte) []byte {
	tag := []byte(`<script src="/__n12bar.js"></script>`)
	low := bytes.ToLower(b[:min(len(b), 8192)])
	if i := bytes.Index(low, []byte("<head")); i >= 0 {
		if k := bytes.IndexByte(b[i:], '>'); k >= 0 {
			p := i + k + 1
			return append(append(append([]byte{}, b[:p]...), tag...), b[p:]...)
		}
	}
	return append(tag, b...)
}

const bridgeErr = `<!doctype html><meta charset="utf-8"><title>Star Gate</title>
<body style="background:#0b0f17;color:#cfd6e4;font:15px system-ui,sans-serif;display:grid;place-items:center;height:100vh;margin:0">
<div style="text-align:center"><h2 style="color:#eef2f8">%s не отвечает</h2><p>%s</p>
<p>Пробую снова через 3 с… <a style="color:#2dd4bf" href="%s">⟵ Запуск</a></p></div>
<script>setTimeout(function(){location.reload()},3000)</script>`

// bridge — мост к цели (http://хост:порт). Слушает bind, при неудаче — 127.0.0.1:0.
func bridge(target, bind string, isB bool) (string, error) {
	brMu.Lock()
	defer brMu.Unlock()
	if a, ok := bridges[target]; ok {
		return a, nil
	}
	tu, err := url.Parse(target)
	if err != nil {
		return "", err
	}
	ln, err := net.Listen("tcp", bind)
	if err != nil {
		if ln, err = net.Listen("tcp", "127.0.0.1:0"); err != nil {
			return "", err
		}
	}
	base := "http://" + ln.Addr().String()
	what := map[bool]string{true: "Плата " + tu.Hostname(), false: "Облако"}[isB]
	rp := &httputil.ReverseProxy{
		Rewrite: func(pr *httputil.ProxyRequest) {
			pr.SetURL(tu)
			pr.Out.Header.Del("Accept-Encoding") // HTML без сжатия — чтобы вставить панель
			if o := pr.Out.Header.Get("Origin"); o != "" {
				pr.Out.Header.Set("Origin", target)
			}
			if rf := pr.Out.Header.Get("Referer"); strings.HasPrefix(rf, base) {
				pr.Out.Header.Set("Referer", target+strings.TrimPrefix(rf, base))
			}
		},
		FlushInterval: -1,
		Transport: &http.Transport{Proxy: nil, DialContext: (&net.Dialer{Timeout: 5 * time.Second}).DialContext,
			ResponseHeaderTimeout: 10 * time.Minute, MaxIdleConnsPerHost: 16},
		ModifyResponse: func(r *http.Response) error {
			if l := r.Header.Get("Location"); strings.HasPrefix(l, target) {
				r.Header.Set("Location", base+strings.TrimPrefix(l, target))
			}
			ct := r.Header.Get("Content-Type")
			if !strings.Contains(ct, "text/html") || r.Header.Get("Content-Encoding") != "" || r.Request.Method != "GET" {
				return nil
			}
			b, err := io.ReadAll(io.LimitReader(r.Body, 32<<20))
			r.Body.Close()
			if err != nil {
				return err
			}
			b = injectBar(b)
			r.Body = io.NopCloser(bytes.NewReader(b))
			r.ContentLength = int64(len(b))
			r.Header.Set("Content-Length", strconv.Itoa(len(b)))
			return nil
		},
		ErrorHandler: func(w http.ResponseWriter, r *http.Request, err error) {
			if strings.Contains(r.Header.Get("Accept"), "text/html") {
				w.Header().Set("Content-Type", "text/html; charset=utf-8")
				w.WriteHeader(http.StatusBadGateway)
				fmt.Fprintf(w, bridgeErr, what, strings.ReplaceAll(err.Error(), "<", "&lt;"), homeURL)
				return
			}
			http.Error(w, err.Error(), http.StatusBadGateway)
		},
	}
	mux := http.NewServeMux()
	mux.HandleFunc("/__n12bar.js", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/javascript; charset=utf-8")
		w.Header().Set("Cache-Control", "no-store")
		io.WriteString(w, barJS(isB))
	})
	mux.Handle("/", rp)
	go http.Serve(ln, touchMW(mux))
	bridges[target] = base
	return base, nil
}

func extraRoutes(mux *http.ServeMux) {
	mux.HandleFunc("/gocloud", func(w http.ResponseWriter, r *http.Request) {
		port := cloudPort(getCfg().Cloud)
		b, err := bridge(fmt.Sprintf("http://127.0.0.1:%d", port), "127.0.0.1:0", false)
		if err != nil {
			http.Redirect(w, r, cloudURL(), http.StatusFound)
			return
		}
		http.Redirect(w, r, b+"/", http.StatusFound)
	})
	mux.HandleFunc("/goboard", func(w http.ResponseWriter, r *http.Request) {
		ip := net.ParseIP(r.URL.Query().Get("ip")).To4()
		if ip == nil {
			http.Error(w, "нужен IPv4 платы", http.StatusBadRequest)
			return
		}
		bind := fmt.Sprintf("127.42.%d.%d:7000", ip[2], ip[3])
		b, err := bridge("http://"+ip.String()+":7000", bind, true)
		if err != nil {
			http.Redirect(w, r, "http://"+ip.String()+":7000/", http.StatusFound)
			return
		}
		http.Redirect(w, r, b+"/", http.StatusFound)
	})
}

// --------------------------------------------------------- окно браузера
type browser struct {
	path, kind string // kind: chromium | firefox | xdg
}

var chromiums = []string{"chromium", "chromium-browser", "yandex-browser-stable", "yandex-browser",
	"chromium-gost-stable", "chromium-gost", "google-chrome-stable", "google-chrome",
	"microsoft-edge-stable", "microsoft-edge", "brave-browser", "vivaldi-stable", "opera"}

func findBrowser() browser {
	pick := os.Getenv("N12_BROWSER")
	if pick == "" {
		pick = getCfg().Browser
	}
	if pick != "" {
		if p, err := exec.LookPath(pick); err == nil {
			k := "chromium"
			if strings.Contains(filepath.Base(p), "firefox") {
				k = "firefox"
			}
			return browser{p, k}
		}
	}
	for _, c := range chromiums {
		if p, err := exec.LookPath(c); err == nil {
			return browser{p, "chromium"}
		}
	}
	for _, c := range []string{"firefox", "firefox-esr"} {
		if p, err := exec.LookPath(c); err == nil {
			return browser{p, "firefox"}
		}
	}
	return browser{"xdg-open", "xdg"}
}

// profileDir — свой профиль окна. Браузер из snap (Ubuntu) видит только
// ~/snap/<имя>/…, поэтому для него профиль там.
func profileDir(p string) string {
	real, _ := filepath.EvalSymlinks(p)
	if strings.HasPrefix(p, "/snap/") || strings.HasPrefix(real, "/snap/") || strings.Contains(real, "/usr/bin/snap") {
		h, _ := os.UserHomeDir()
		return filepath.Join(h, "snap", filepath.Base(p), "common", "neuro12-window")
	}
	return filepath.Join(launcherDir(), "window")
}

func openWindow(u string) (*exec.Cmd, browser) {
	b := findBrowser()
	var cmd *exec.Cmd
	switch b.kind {
	case "chromium":
		args := []string{"--app=" + u, "--user-data-dir=" + profileDir(b.path), "--no-first-run",
			"--no-default-browser-check", "--start-maximized", "--class=neuro12", "--disable-features=Translate"}
		if os.Geteuid() == 0 {
			args = append(args, "--no-sandbox")
		}
		cmd = exec.Command(b.path, args...)
	case "firefox":
		cmd = exec.Command(b.path, "--new-window", u)
	default:
		cmd = exec.Command("xdg-open", u)
	}
	cmd.Stdout, cmd.Stderr = nil, nil
	if err := cmd.Start(); err != nil {
		logf("Окно: не запустился %s: %v", b.path, err)
		return nil, b
	}
	logf("Окно: %s (%s)", filepath.Base(b.path), map[string]string{"chromium": "режим приложения", "firefox": "новое окно", "xdg": "браузер по умолчанию"}[b.kind])
	return cmd, b
}

// otherInstance — лаунчер уже открыт: показываем его окно и выходим
func otherInstance() bool {
	runFile = filepath.Join(launcherDir(), "running.url")
	b, err := os.ReadFile(runFile)
	if err != nil {
		return false
	}
	u := strings.TrimSpace(string(b))
	c := &http.Client{Timeout: 1500 * time.Millisecond, Transport: &http.Transport{Proxy: nil}}
	r, err := c.Get(u)
	if err != nil {
		return false
	}
	r.Body.Close()
	if !graphical() {
		fmt.Println("Лаунчер уже работает:", u)
		return true
	}
	homeURL = u
	for _, a := range os.Args[1:] {
		if a == "--cloud" || a == "/cloud" {
			u += "?go=cloud" // ярлык «Открыть облако» при открытом лаунчере
		}
	}
	openWindow(u)
	return true
}

func runUI() {
	os.WriteFile(runFile, []byte(homeURL), 0600)
	quit := make(chan os.Signal, 1)
	signal.Notify(quit, os.Interrupt, syscall.SIGTERM, syscall.SIGHUP)
	go func() { <-quit; os.Remove(runFile); os.Exit(0) }()
	defer os.Remove(runFile)
	touch()
	headless := !graphical()
	for _, a := range os.Args[1:] {
		if a == "--no-window" || a == "--headless" {
			headless = true
		}
	}
	if headless {
		uiShown.Store(true)
		fmt.Println("Star Gate: окно браузера не открываю (нет графического сеанса).")
		fmt.Println("Интерфейс лаунчера:", homeURL)
		fmt.Println("С другого компьютера: ssh -L 8780:" + strings.TrimSuffix(strings.TrimPrefix(homeURL, "http://"), "/") + " этот_компьютер → http://127.0.0.1:8780/")
		select {}
	}
	cmd, b := openWindow(homeURL)
	uiShown.Store(true)
	if cmd != nil {
		t0 := time.Now()
		cmd.Wait()
		// окно Chromium со своим профилем закрыли — выходим, как в Windows
		if b.kind == "chromium" && time.Since(t0) > 5*time.Second {
			logf("Окно закрыто — лаунчер завершает работу (облако продолжает работать).")
			return
		}
	} else {
		msgBox("Star Gate", "Не открылось окно браузера. Открой вручную: "+homeURL)
	}
	// окно отдано уже запущенному браузеру (Firefox, xdg-open): работаем,
	// пока окно обращается к лаунчеру; 10 минут тишины — выход
	for {
		time.Sleep(30 * time.Second)
		if time.Since(time.Unix(lastHit.Load(), 0)) > 10*time.Minute {
			return
		}
	}
}

// v3.161: выбор ZIP-архива
func pickFile(title, startDir string) string {
	var cmd *exec.Cmd
	switch {
	case has("zenity"):
		cmd = exec.Command("zenity", "--file-selection", "--title", title, "--filename", startDir+"/", "--file-filter=ZIP | *.zip", "--file-filter=Все файлы | *")
	case has("kdialog"):
		cmd = exec.Command("kdialog", "--title", title, "--getopenfilename", startDir, "*.zip")
	case has("yad"):
		cmd = exec.Command("yad", "--file", "--title", title, "--filename", startDir+"/")
	default:
		logf("Нет диалога выбора файла (поставь zenity) — положи архив в «Загрузки» и выбери его там.")
		return ""
	}
	out, err := cmd.Output()
	if err != nil {
		return ""
	}
	return strings.TrimSpace(string(out))
}

// v3.161: остановить процесс облака (перед заменой его файлов из ZIP)
func stopCloudProc(dir string) {
	if has("systemctl") { // служба с Restart= подняла бы облако снова
		exec.Command("systemctl", "--user", "stop", "neuro12-cloud").Run()
		exec.Command("systemctl", "--no-ask-password", "stop", "neuro12-cloud").Run()
	}
	if exec.Command("pkill", "-f", filepath.Join(dir, "server", "app.py")).Run() != nil {
		exec.Command("pkill", "-f", "server/app.py").Run()
	}
}

// v3.161: открыть папку с файлом
func revealFile(p string) { openURL("file://" + filepath.Dir(p)) }

// v3.164: запрет адресов на этом компьютере (Linux): iptables через pkexec/sudo
func blockLocal(ips []string) {
	sc := "for ip in " + strings.Join(ips, " ") + "; do iptables -C OUTPUT -d $ip -j DROP 2>/dev/null || iptables -I OUTPUT -d $ip -j DROP; iptables -C INPUT -s $ip -j DROP 2>/dev/null || iptables -I INPUT -s $ip -j DROP; done"
	var cmd *exec.Cmd
	if has("pkexec") {
		cmd = exec.Command("pkexec", "sh", "-c", sc)
	} else {
		cmd = exec.Command("sudo", "-n", "sh", "-c", sc)
	}
	if out, err := cmd.CombinedOutput(); err != nil {
		logf("Запрет на этом компьютере не поставлен (%v %s) — выполни вручную: sudo sh -c '%s'", err, strings.TrimSpace(string(out)), sc)
		return
	}
	logf("На этом компьютере запрещены: %s (iptables, до перезагрузки).", strings.Join(ips, ", "))
}
