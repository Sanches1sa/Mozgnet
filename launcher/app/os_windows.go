//go:build windows

// os_windows.go — v3.159: всё, что относится только к Windows (окно WebView2,
// скрытые процессы, cloud_bg.vbs, arp -a, консоль cmd.exe, зеркало MAC через
// PowerShell с UAC). Общая логика лаунчера — в main.go/board.go/mac.go.
package main

import (
	_ "embed"
	"fmt"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"strings"
	"syscall"
	"time"

	webview2 "github.com/jchv/go-webview2"
)

//go:embed n12_mac_pc.ps1
var macPS string

const (
	cloudHint    = `%LOCALAPPDATA%\neuro12-cloud`
	catCmd       = "type"
	boardProxyOn = false
)

var wv webview2.WebView

func osName() string { return "Windows" }

func launcherDir() string { return filepath.Join(os.Getenv("LOCALAPPDATA"), "neuro12-launcher") }

func cloudCandidates() []string {
	return []string{filepath.Join(os.Getenv("LOCALAPPDATA"), "neuro12-cloud")}
}

func downloadDirs() []string {
	return []string{filepath.Join(os.Getenv("USERPROFILE"), "Downloads")}
}

func hidden(cmd *exec.Cmd) *exec.Cmd {
	cmd.SysProcAttr = &syscall.SysProcAttr{HideWindow: true, CreationFlags: 0x08000000}
	return cmd
}

func findPythonW() string {
	for _, c := range [][]string{{"py", "-3"}, {"python"}} {
		args := append(c[1:], "-c", "import sys;print(sys.executable)")
		out, err := hidden(exec.Command(c[0], args...)).Output()
		if err != nil {
			continue
		}
		p := strings.TrimSpace(string(out))
		if exists(p) {
			w := filepath.Join(filepath.Dir(p), "pythonw.exe")
			if exists(w) {
				return w
			}
			return p
		}
	}
	return ""
}

// launchCloud — запуск процесса облака (ожидание ответа — в startCloud)
func launchCloud(dir string) bool {
	vbs := filepath.Join(dir, "cloud_bg.vbs")
	if exists(vbs) {
		if err := hidden(exec.Command("wscript.exe", vbs)).Start(); err != nil {
			logf("Облако: не запустился cloud_bg.vbs: %v", err)
			return false
		}
		logf("Облако запускается (cloud_bg.vbs, как при установке)…")
		return true
	}
	py := filepath.Join(dir, "runtime", "pythonw.exe") // v3.160: свой Python пакета Windows
	if !exists(py) {
		py = findPythonW()
	}
	if py == "" {
		logf("Python 3 не найден. Поставь облако пакетом neuro12-windows-*.zip (в нём свой Python) через install.exe.")
		return false
	}
	cmd := hidden(exec.Command(py, filepath.Join(dir, "server", "app.py")))
	cmd.Dir = dir
	cmd.Env = append(os.Environ(), "NEURO_DATA="+filepath.Join(dir, "data"))
	if err := cmd.Start(); err != nil {
		logf("Облако: не запустилось: %v", err)
		return false
	}
	logf("Облако запускается: %s", py)
	return true
}

var reArp = regexp.MustCompile(`(\d+\.\d+\.\d+\.\d+)\s+([0-9a-fA-F]{2}(?:-[0-9a-fA-F]{2}){5})`)

func arpTable() map[string]string {
	out, _ := hidden(exec.Command("arp", "-a")).Output()
	m := map[string]string{}
	for _, x := range reArp.FindAllStringSubmatch(string(out), -1) {
		m[x[1]] = strings.ToUpper(strings.ReplaceAll(x[2], "-", ":"))
	}
	return m
}

func openURL(u string) { exec.Command("rundll32", "url.dll,FileProtocolHandler", u).Start() }

// consoleSteps — запасное окно консоли: каждый шаг подписан, чтобы окно
// никогда не стояло чёрным и пустым.
func consoleSteps(title string, steps []cstep) {
	var b strings.Builder
	b.WriteString("@echo off\r\nchcp 65001 >nul\r\ntitle " + title + "\r\n")
	b.WriteString("echo ============================================================\r\n")
	b.WriteString("echo  " + title + "\r\n")
	b.WriteString("echo  Если спросит пароль платы — введите его и нажмите Enter.\r\n")
	b.WriteString("echo  Символы пароля при вводе НЕ видны — это нормально.\r\n")
	b.WriteString("echo ============================================================\r\n")
	for i, s := range steps {
		b.WriteString(fmt.Sprintf("echo.\r\necho  ШАГ %d из %d: %s\r\necho.\r\n", i+1, len(steps), s.label))
		b.WriteString(s.cmd + "\r\n")
		b.WriteString(fmt.Sprintf("if errorlevel 1 (echo.& echo  !! ШАГ %d не удался — смотри строки выше)\r\n", i+1))
	}
	b.WriteString("echo.\r\necho  Готово. Окно можно закрыть.\r\npause >nul\r\n")
	bat := filepath.Join(os.TempDir(), fmt.Sprintf("n12_%d.cmd", time.Now().UnixNano()%1000000))
	os.WriteFile(bat, []byte(b.String()), 0644)
	cmd := exec.Command("cmd.exe", "/c", bat)
	cmd.SysProcAttr = newConsole()
	if err := cmd.Run(); err != nil {
		logf("Окно консоли: %v", err)
	}
	os.Remove(bat)
}

// pcMacApply — зеркальный MAC компьютеру (PowerShell с правами администратора).
// Ответ: ["OK", адаптер, старый MAC, новый MAC].
func pcMacApply(mode, pcip, mac string) ([]string, error) {
	res := filepath.Join(os.TempDir(), "n12_mac_pc.txt")
	os.Remove(res)
	ps := strings.NewReplacer("__OUT__", res, "__PCIP__", pcip, "__MODE__", mode,
		"__MAC__", strings.ToUpper(strings.ReplaceAll(mac, ":", "-"))).Replace(macPS)
	psf := filepath.Join(os.TempDir(), "n12_mac_pc.ps1")
	os.WriteFile(psf, append([]byte{0xEF, 0xBB, 0xBF}, []byte(strings.ReplaceAll(ps, "\n", "\r\n"))...), 0644)
	setStep("MAC компьютера: подтвердите окно Windows «Разрешить изменения» (нужны права администратора)")
	cmd := hidden(exec.Command("powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command",
		"Start-Process powershell -Verb RunAs -Wait -WindowStyle Hidden -ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-File','\""+psf+"\"'"))
	if out, err := cmd.CombinedOutput(); err != nil {
		return nil, fmt.Errorf("права администратора не получены (%v) %s", err, strings.TrimSpace(string(out)))
	}
	b, _ := os.ReadFile(res)
	r := strings.TrimSpace(strings.TrimPrefix(string(b), "\xEF\xBB\xBF"))
	f := strings.Split(r, "|")
	if len(f) < 2 || f[0] != "OK" {
		return nil, fmt.Errorf("не получилось: %s", strings.TrimPrefix(r, "ERR|"))
	}
	return f, nil
}

// ------------------------------------------------------- окно WebView2
func uiFix(s string) string               { return s }
func cloudView() string                   { return cloudURL() }
func extraRoutes(mux *http.ServeMux)      {}
func touchMW(h http.Handler) http.Handler { return h }
func otherInstance() bool                 { return false }
func uiReady() bool                       { return wv != nil }
func navigate(u string)                   { wv.Dispatch(func() { wv.Navigate(u) }) }

func runUI() {
	data := filepath.Join(launcherDir(), "webview")
	wv = webview2.NewWithOptions(webview2.WebViewOptions{
		AutoFocus: true,
		DataPath:  data,
		WindowOptions: webview2.WindowOptions{
			Title: "Мозг-12", Width: 1280, Height: 860, IconId: 1, Center: true,
		},
	})
	if wv == nil {
		msgBox("Мозг-12", "Не запустился встроенный браузер WebView2.\n\nОн входит в Windows 11; если его нет — установи «Microsoft Edge WebView2 Runtime» с сайта Microsoft.\nПока можно открыть облако в браузере: "+cloudURL())
		openURL(cloudURL())
		return
	}
	defer wv.Destroy()
	maximize(wv.Window())
	js := strings.ReplaceAll(homeBtnJS, "__HOME__", homeURL)
	wv.Init(strings.ReplaceAll(js, "__ISB__", `location.port==="7000"`))
	wv.Navigate(homeURL)
	wv.Run()
}
