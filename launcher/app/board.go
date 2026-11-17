// board.go — v3.150: работа с платой БЕЗ ЧЁРНОГО ОКНА КОНСОЛИ.
//
// Раньше каждое действие с платой (запуск, стоп, починка сборки) открывало
// окно консоли с ssh/scp. Пока шло копирование или сборка, а ssh ждал пароль,
// окно оставалось чёрным и пустым — непонятно, что происходит.
// Теперь:
//   - если настроен «Вход по ключу», всё идёт СКРЫТО, а вывод платы строка
//     за строкой попадает в журнал лаунчера, шаг — под кнопкой «Запуск»;
//   - если ключа нет, лаунчер один раз открывает окно для ввода пароля с
//     понятной надписью, настраивает ключ и дальше работает без окон;
//   - окно консоли остаётся только запасным путём, и в нём каждый шаг
//     подписан («ШАГ 1 из 3: …», «введите пароль платы — символы не видны»).
//
// Интернет платы (Wi-Fi через NetworkManager): состояние, список сетей,
// подключение. Пароль сети уходит на плату через stdin, не в командной строке.
package main

import (
	"bufio"
	"context"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"sync"
	"time"
)

var (
	stepMu  sync.Mutex
	stepTxt = ""
	netMu   sync.Mutex
	netSt   = map[string]interface{}{}
	wifiL   []map[string]interface{}
)

func setStep(f string, a ...interface{}) {
	s := fmt.Sprintf(f, a...)
	stepMu.Lock()
	stepTxt = s
	stepMu.Unlock()
	if s != "" {
		logf("%s", s)
	}
}

func getStep() string { stepMu.Lock(); defer stepMu.Unlock(); return stepTxt }

func sshBase(c Cfg, batch bool) []string {
	a := []string{"-o", "StrictHostKeyChecking=accept-new", "-o", "ConnectTimeout=8",
		"-o", "ServerAliveInterval=15"}
	if batch {
		a = append(a, "-o", "BatchMode=yes")
	}
	return a
}

// sshRun — команда на плате без окна (нужен вход по ключу). stdin — текст на вход.
func sshRun(remote, stdin string, to time.Duration) (string, error) {
	c := getCfg()
	if c.IP == "" {
		return "", fmt.Errorf("нет адреса платы")
	}
	ctx, cancel := context.WithTimeout(context.Background(), to)
	defer cancel()
	args := append(sshBase(c, true), c.User+"@"+c.IP, remote)
	cmd := hidden(exec.CommandContext(ctx, "ssh", args...))
	if stdin != "" {
		cmd.Stdin = strings.NewReader(stdin)
	}
	out, err := cmd.CombinedOutput()
	return string(out), err
}

// sshStream — то же, но вывод платы построчно в журнал (долгие действия).
func sshStream(remote, stdin string, to time.Duration) error {
	c := getCfg()
	ctx, cancel := context.WithTimeout(context.Background(), to)
	defer cancel()
	args := append(sshBase(c, true), c.User+"@"+c.IP, remote)
	cmd := hidden(exec.CommandContext(ctx, "ssh", args...))
	if stdin != "" {
		cmd.Stdin = strings.NewReader(stdin)
	}
	pr, pw, err := os.Pipe()
	if err != nil {
		return err
	}
	cmd.Stdout, cmd.Stderr = pw, pw
	if err := cmd.Start(); err != nil {
		pw.Close()
		pr.Close()
		return err
	}
	pw.Close()
	sc := bufio.NewScanner(pr)
	sc.Buffer(make([]byte, 64*1024), 1024*1024)
	for sc.Scan() {
		ln := strings.TrimRight(strings.ReplaceAll(sc.Text(), "\r", ""), " ")
		if ln == "" {
			continue
		}
		if len(ln) > 400 {
			ln = ln[:400] + "…"
		}
		logf("  плата │ %s", ln)
	}
	pr.Close()
	return cmd.Wait()
}

func scpHidden(srcs []string, dst string, to time.Duration) error {
	c := getCfg()
	ctx, cancel := context.WithTimeout(context.Background(), to)
	defer cancel()
	args := append(sshBase(c, true), "-r")
	args = append(args, srcs...)
	args = append(args, c.User+"@"+c.IP+":"+dst)
	out, err := hidden(exec.CommandContext(ctx, "scp", args...)).CombinedOutput()
	if err != nil {
		return fmt.Errorf("%v: %s", err, strings.TrimSpace(string(out)))
	}
	return nil
}

// keyOK — пускает ли плата по ключу (без пароля).
func keyOK() bool {
	out, err := sshRun("echo n12ok", "", 15*time.Second)
	return err == nil && strings.Contains(out, "n12ok")
}

// ensureKey — ключа нет: одно окно для пароля (с понятными подписями) и
// проверка. true — дальше можно без окон.
func ensureKey() bool {
	if keyOK() {
		return true
	}
	setStep("Нужен вход по ключу: откроется окно — введите пароль платы (один раз)")
	keySetup()
	if keyOK() {
		logf("Вход по ключу работает — дальше без окон и паролей.")
		return true
	}
	logf("Вход по ключу не получился (пароль неверный или окно закрыли).")
	return false
}

type cstep struct{ label, cmd string }

// boardRun — команда на плате: по ключу — скрыто с выводом в журнал, иначе — окно.
func boardRun(title, remote string, to time.Duration) {
	c := getCfg()
	if c.IP == "" {
		logf("Нет адреса платы — сначала сканер или впиши IP.")
		return
	}
	if keyOK() {
		setStep("%s…", title)
		if err := sshStream(remote, "", to); err != nil {
			logf("%s: %v", title, err)
		} else {
			logf("%s — готово.", title)
		}
		setStep("")
		return
	}
	consoleSteps("Мозг-12: "+title, []cstep{{title, "ssh -t -o StrictHostKeyChecking=accept-new " +
		c.User + "@" + c.IP + " " + q(remote)}})
}

// sudoRun — то, что требует прав root: sudo без пароля — скрыто; иначе окно.
func sudoRun(title, remote string, to time.Duration) {
	if out, err := sshRun("sudo -n true && echo n12sudo", "", 15*time.Second); err == nil && strings.Contains(out, "n12sudo") {
		boardRun(title, "sudo -n bash -c "+shq(remote), to)
		return
	}
	c := getCfg()
	logf("%s: нужен пароль платы для sudo — откроется окно.", title)
	consoleSteps("Мозг-12: "+title, []cstep{{title + " (введите пароль платы для sudo)",
		"ssh -t -o StrictHostKeyChecking=accept-new " + c.User + "@" + c.IP + " " + q("sudo bash -c "+shq(remote))}})
}

func shq(s string) string { return "'" + strings.ReplaceAll(s, "'", `'\''`) + "'" }

// runHidden — «Запустить всё» без окон (вход по ключу).
func runHidden(stage, cu, tok string) bool {
	c := getCfg()
	setStep("ШАГ 1 из 4: подготовка папки на плате %s", c.IP)
	if out, err := sshRun("mkdir -p ~/ArduinoApps && rm -rf ~/ArduinoApps/neuro_12.new && echo ok", "", 30*time.Second); err != nil {
		logf("Плата не ответила по SSH: %v %s", err, strings.TrimSpace(out))
		return false
	}
	setStep("ШАГ 2 из 4: копирую приложение на плату (без окна)")
	if err := scpHidden([]string{filepath.Join(stage, "neuro_12.new"), filepath.Join(stage, "n12_setcfg.py"),
		filepath.Join(stage, "n12_deploy.sh")}, "ArduinoApps/", 15*time.Minute); err != nil {
		logf("Копирование не прошло: %v", err)
		return false
	}
	setStep("ШАГ 3 из 4: сборка скетча и запуск (несколько минут, вывод платы — в журнале)")
	if err := sshStream("bash ~/ArduinoApps/n12_deploy.sh "+shq(cu)+" "+shq(tok), "", 30*time.Minute); err != nil {
		logf("Запуск на плате закончился с ошибкой: %v (строки платы — выше в журнале)", err)
		return false
	}
	return true
}

// ---------------------------------------------------------- интернет платы
const netScript = `export LC_ALL=C
echo "DEV:$(nmcli -t -f DEVICE,TYPE,STATE,CONNECTION dev 2>/dev/null | tr '\n' ';')"
echo "ROUTE:$(ip -4 route show default 2>/dev/null | head -1)"
echo "IP:$(hostname -I 2>/dev/null)"
if ping -c1 -W3 1.1.1.1 >/dev/null 2>&1 || ping -c1 -W3 8.8.8.8 >/dev/null 2>&1; then echo NET:ok; else echo NET:no; fi
if getent hosts ya.ru >/dev/null 2>&1 || getent hosts google.com >/dev/null 2>&1; then echo DNS:ok; else echo DNS:no; fi
command -v nmcli >/dev/null 2>&1 && echo NM:yes || echo NM:no
`

func netCheck() map[string]interface{} {
	st := map[string]interface{}{"ts": time.Now().Unix()}
	out, err := sshRun("bash -s", netScript, 25*time.Second)
	if err != nil {
		st["err"] = "нет связи с платой по ключу (настрой «Вход по ключу»): " + firstLn(out, err)
	} else {
		for _, ln := range strings.Split(out, "\n") {
			k, v, ok := strings.Cut(strings.TrimSpace(ln), ":")
			if !ok {
				continue
			}
			v = strings.TrimSpace(v)
			switch k {
			case "NET":
				st["internet"] = v == "ok"
			case "DNS":
				st["dns"] = v == "ok"
			case "NM":
				st["nm"] = v == "yes"
			case "ROUTE":
				st["route"] = v
			case "IP":
				st["ips"] = v
			case "DEV":
				var devs []map[string]string
				for _, d := range strings.Split(v, ";") {
					f := splitNM(d)
					if len(f) >= 4 && f[1] != "loopback" {
						devs = append(devs, map[string]string{"dev": f[0], "type": f[1], "state": f[2], "conn": f[3]})
					}
				}
				st["devs"] = devs
			}
		}
	}
	netMu.Lock()
	netSt = st
	netMu.Unlock()
	return st
}

func firstLn(out string, err error) string {
	s := strings.TrimSpace(out)
	if i := strings.IndexByte(s, '\n'); i >= 0 {
		s = s[:i]
	}
	if s == "" && err != nil {
		s = err.Error()
	}
	return s
}

// splitNM — поля вывода nmcli -t (двоеточие внутри поля экранировано \:)
func splitNM(s string) []string {
	var out []string
	var cur strings.Builder
	esc := false
	for _, r := range s {
		switch {
		case esc:
			cur.WriteRune(r)
			esc = false
		case r == '\\':
			esc = true
		case r == ':':
			out = append(out, cur.String())
			cur.Reset()
		default:
			cur.WriteRune(r)
		}
	}
	return append(out, cur.String())
}

func wifiScan() {
	if !setBusy("wifi", true) {
		return
	}
	defer setBusy("wifi", false)
	logf("Интернет платы: ищу Wi-Fi сети…")
	out, err := sshRun("LC_ALL=C nmcli -t -f IN-USE,SSID,SIGNAL,SECURITY dev wifi list --rescan yes 2>&1", "", 40*time.Second)
	if err != nil {
		logf("Список сетей не получен: %s", firstLn(out, err))
		return
	}
	seen := map[string]map[string]interface{}{}
	for _, ln := range strings.Split(out, "\n") {
		f := splitNM(strings.TrimSpace(ln))
		if len(f) < 4 || strings.TrimSpace(f[1]) == "" {
			continue
		}
		sig, _ := strconv.Atoi(f[2])
		ssid := f[1]
		if old, ok := seen[ssid]; ok && old["signal"].(int) >= sig {
			continue
		}
		seen[ssid] = map[string]interface{}{"ssid": ssid, "signal": sig, "security": f[3],
			"active": strings.TrimSpace(f[0]) == "*"}
	}
	var l []map[string]interface{}
	for _, v := range seen {
		l = append(l, v)
	}
	sort.Slice(l, func(i, j int) bool { return l[i]["signal"].(int) > l[j]["signal"].(int) })
	netMu.Lock()
	wifiL = l
	netMu.Unlock()
	logf("Интернет платы: найдено сетей %d", len(l))
}

var reBad = regexp.MustCompile(`[\r\n\x00]`)

// wifiConnect — подключить плату к сети. Подключение идёт в ОТОРВАННОМ от
// SSH процессе: если плата сменит сеть, связь с ней на время оборвётся,
// а подключение всё равно закончится; лаунчер потом проверит и, если адрес
// сменился, найдёт плату сканером.
func wifiConnect(ssid, pass string, hidden bool) {
	if !setBusy("wifi", true) {
		return
	}
	defer setBusy("wifi", false)
	if ssid == "" || reBad.MatchString(ssid) || reBad.MatchString(pass) {
		logf("Интернет платы: неверное имя сети или пароль")
		return
	}
	script := "SSID=$(cat <<'__N12S__'\n" + ssid + "\n__N12S__\n)\nPASS=$(cat <<'__N12P__'\n" + pass + "\n__N12P__\n)\n" +
		`F=/tmp/n12wifi.log
HID="` + map[bool]string{true: "yes", false: ""}[hidden] + `"
try() { set -- $1 nmcli dev wifi connect "$SSID"; [ -n "$PASS" ] && set -- "$@" password "$PASS"; [ -n "$HID" ] && set -- "$@" hidden yes; "$@"; }
( sleep 1; { try "" || try "sudo -n"; } >$F 2>&1; echo "RC:$?" >>$F ) </dev/null >/dev/null 2>&1 &
echo started
`
	logf("Интернет платы: подключаю к сети «%s»…", ssid)
	out, err := sshRun("bash -s", script, 25*time.Second)
	if err != nil || !strings.Contains(out, "started") {
		logf("Не удалось начать подключение: %s", firstLn(out, err))
		return
	}
	time.Sleep(15 * time.Second)
	res, err := sshRun("cat /tmp/n12wifi.log 2>/dev/null; rm -f /tmp/n12wifi.log", "", 20*time.Second)
	if err != nil {
		logf("Плата пока не отвечает по старому адресу (могла сменить сеть) — ищу её сканером…")
		runScan()
		res, err = sshRun("cat /tmp/n12wifi.log 2>/dev/null; rm -f /tmp/n12wifi.log", "", 20*time.Second)
	}
	res = strings.TrimSpace(res)
	switch {
	case strings.Contains(res, "successfully activated") || strings.Contains(res, "RC:0"):
		logf("Интернет платы: подключена к «%s».", ssid)
	case strings.Contains(strings.ToLower(res), "not authorized") || strings.Contains(res, "sudo"):
		logf("Интернет платы: у пользователя платы нет прав менять сеть без пароля — открою окно, там nmcli спросит пароль сети, а sudo — пароль платы.")
		c := getCfg()
		consoleSteps("Мозг-12: Wi-Fi платы", []cstep{{"подключение к «" + ssid + "» (пароль платы для sudo, затем пароль сети)",
			"ssh -t -o StrictHostKeyChecking=accept-new " + c.User + "@" + c.IP + " " +
				q("sudo nmcli --ask dev wifi connect "+shq(ssid)+map[bool]string{true: " hidden yes", false: ""}[hidden])}})
	case res == "":
		logf("Интернет платы: ответа о подключении нет — проверь состояние кнопкой «Проверить».")
	default:
		logf("Интернет платы: %s", strings.ReplaceAll(res, "\n", " · "))
	}
	netCheck()
}
