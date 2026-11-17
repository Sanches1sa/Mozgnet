// mac.go — v3.152: вращение MAC («виртуальное тело» платы) и зеркало на компьютере.
//
// Что было не так в v3.151:
//   - авто-вращение было ВКЛЮЧЕНО по умолчанию (галочка выглядела выключенной)
//     и срабатывало сразу при открытии лаунчера;
//   - скрипт платы менял MAC без sudo (не работало) или через ip link down по
//     тому же SSH — связь рвалась, интерфейс мог остаться выключенным;
//   - «вращение» переворачивало адрес и сразу портило его (из ff:…  получался
//     multicast-адрес, ядро его отвергает), второй поворот возвращал почти тот же;
//   - «Зеркало» ставило компьютеру ТОТ ЖЕ MAC, что у платы, — два устройства с
//     одним MAC в одной сети ломают связь обоим; bat брал вместо имени адаптера
//     слово «Enabled», не просил прав администратора и писал «готово» всегда.
//
// Теперь:
//   - плата: переворот октетов (6→1, 5→2, 4↔3), повороты чередуют родной и перевёрнутый адрес,
//     через NetworkManager в оторванном процессе, родной адрес можно вернуть;
//   - компьютер: зеркальный (перевёрнутый) адрес платы, всегда ОТЛИЧНЫЙ от неё,
//     через окно UAC; после смены адреса лаунчер находит плату и заново
//     прописывает ей адрес облака.
package main

import (
	"fmt"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"time"
)

var (
	macSt      = map[string]interface{}{}
	lastMacRot time.Time
	lastAutoAt time.Time // v3.157: начало прошлого авто-поворота
)

func setMacSt(k string, v interface{}) { mu.Lock(); macSt[k] = v; mu.Unlock() }

func parseKV(out string) map[string]string {
	m := map[string]string{}
	for _, ln := range strings.Split(out, "\n") {
		if k, v, ok := strings.Cut(strings.TrimSpace(ln), ":"); ok && k != "" && strings.ToUpper(k) == k {
			if _, dup := m[k]; !dup {
				m[k] = strings.TrimSpace(v)
			}
		}
	}
	return m
}

// mirrorMAC — перевёрнутый адрес, первый октет локальный одноадресный,
// и обязательно не равный исходному.
func mirrorMAC(m string) string {
	p := strings.Split(strings.ToLower(m), ":")
	if len(p) != 6 {
		return ""
	}
	o := make([]uint64, 6)
	for i := range p {
		v, err := strconv.ParseUint(p[5-i], 16, 8)
		if err != nil {
			return ""
		}
		o[i] = v
	}
	o[0] = (o[0] &^ 1) | 2
	s := func() string {
		return fmt.Sprintf("%02x:%02x:%02x:%02x:%02x:%02x", o[0], o[1], o[2], o[3], o[4], o[5])
	}
	if s() == strings.ToLower(m) {
		o[5] ^= 1
	}
	return s()
}

// pushMacScript — v3.158: скрипт кладётся под временным именем и меняется
// одной командой mv (идущий цикл спина работает со своей копией).
func pushMacScript() error {
	tmp := filepath.Join(os.TempDir(), "n12_mac.sh.new")
	os.WriteFile(tmp, []byte(strings.ReplaceAll(macSh, "\r\n", "\n")), 0644)
	if err := scpHidden([]string{tmp}, "ArduinoApps/", 30*time.Second); err != nil {
		return err
	}
	_, err := sshRun("mv -f ~/ArduinoApps/n12_mac.sh.new ~/ArduinoApps/n12_mac.sh", "", 15*time.Second)
	if err == nil {
		macPushed = true
	}
	return err
}

var macPushed bool // скрипт этой версии уже на плате

// macShow — прочитать MAC платы и шаг вращения (для строки в ⚙).
func macShow() {
	if getCfg().IP == "" || !keyOK() {
		return
	}
	if pushMacScript() != nil {
		return
	}
	out, err := sshRun("bash ~/ArduinoApps/n12_mac.sh show", "", 20*time.Second)
	if err != nil {
		return
	}
	kv := parseKV(out)
	setMacSt("board", kv["NOW"])
	setMacSt("orig", kv["ORIG"])
	setMacSt("base", kv["BASE"])
	setMacSt("step", kv["STEP"])
	setMacSt("iface", kv["IFACE"])
}

// waitBoard — плата вернулась после смены MAC? Адрес мог смениться — сканер.
func waitBoard(oldIP string) string {
	time.Sleep(8 * time.Second)
	for i := 0; i < 12; i++ {
		if portOpen(oldIP, 22, time.Second) || portOpen(oldIP, 7000, time.Second) {
			logf("MAC: плата снова в сети по прежнему адресу %s", oldIP)
			return oldIP
		}
		time.Sleep(3 * time.Second)
	}
	if ip := predictBoard("после смены MAC"); ip != "" {
		return ip
	}
	logf("MAC: по адресу %s плата не отвечает — роутер, видимо, выдал новый адрес, ищу сканером…", oldIP)
	runScan()
	ip := getCfg().IP
	if ip != "" && ip != oldIP {
		logf("MAC: плата найдена по новому адресу %s", ip)
	}
	return ip
}

// boardMac — mode: "rotate" | "restore". auto — из таймера (без окон).
func boardMac(mode string, auto bool) bool {
	c := getCfg()
	if c.IP == "" {
		logf("MAC: нет адреса платы")
		return false
	}
	if !keyOK() {
		logf("MAC: нужен вход по ключу (⚙ → «Вход по ключу»)")
		return false
	}
	defer setStep("")
	setStep("MAC: загружаю скрипт на плату")
	if err := pushMacScript(); err != nil {
		logf("MAC: не скопировал скрипт: %v", err)
		return false
	}
	setStep("%s", map[string]string{"rotate": "MAC: поворачиваю адрес платы", "restore": "MAC: возвращаю родной адрес платы"}[mode])
	out, err := sshRun("bash ~/ArduinoApps/n12_mac.sh "+mode, "", 25*time.Second)
	kv := parseKV(out)
	if strings.Contains(out, "ERR:SUDO") {
		if auto {
			logf("MAC: авто-вращение пропущено — пользователю платы нужен sudo без пароля (или поверни вручную: откроется окно для пароля)")
			return false
		}
		logf("MAC: нужны права root — откроется окно, введите пароль платы для sudo")
		consoleSteps("Мозг-12: MAC платы", []cstep{{"смена MAC (пароль платы для sudo; связь затем на миг оборвётся — это нормально)",
			"ssh -t -o StrictHostKeyChecking=accept-new " + c.User + "@" + c.IP + " " + q("sudo bash ~/ArduinoApps/n12_mac.sh "+mode)}})
		out, _ = sshRun("cat ~/ArduinoApps/.n12_mac/current", "", 15*time.Second)
		kv = map[string]string{"NEW": strings.TrimSpace(out), "WAS": kv["WAS"], "STEP": kv["STEP"], "IFACE": kv["IFACE"]}
	} else if err != nil || !strings.Contains(out, "OK:started") {
		msg := kv["ERR"]
		if msg == "" {
			msg = firstLn(out, err)
		}
		logf("MAC: ошибка: %s", msg)
		return false
	}
	logf("MAC платы (%s): %s → %s · %s · %s", kv["IFACE"], kv["WAS"], kv["NEW"],
		map[string]string{"1": "перевёрнутый", "0": "прямой", "R": "заводской"}[kv["STEP"]], kv["VIA"])
	setStep("MAC: плата переподключается с новым адресом…")
	ip := waitBoard(c.IP)
	if ip == "" {
		logf("MAC: плату не нашёл. Если связи нет совсем — перезагрузи плату (адрес от NetworkManager сохранится) или нажми «Вернуть родные MAC».")
		return false
	}
	setMacSt("board", kv["NEW"])
	setMacSt("step", kv["STEP"])
	setMacSt("orig", kv["ORIG"])
	setMacSt("base", kv["BASE"])
	setMacSt("iface", kv["IFACE"])
	if ip != c.IP {
		configureBoard(ip)
	}
	return true
}

// pcMac — зеркало на компьютере: mode "mirror" | "restore". Окно UAC.
func pcMac(mode string) bool {
	c := getCfg()
	if c.IP == "" {
		logf("MAC компьютера: нет адреса платы")
		return false
	}
	defer setStep("")
	mac := ""
	if mode == "mirror" {
		setStep("MAC компьютера: читаю адрес платы")
		macShow()
		mu.Lock()
		bm, _ := macSt["board"].(string)
		mu.Unlock()
		mac = mirrorMAC(bm)
		if mac == "" {
			logf("MAC компьютера: не прочитал MAC платы (нужен «Вход по ключу»)")
			return false
		}
		logf("MAC компьютера: зеркало адреса платы %s → %s (одинаковый адрес в одной сети нельзя — будет конфликт)", bm, mac)
	}
	pcip := pcIPFor(c.IP)
	if pcip == "127.0.0.1" {
		logf("MAC компьютера: не нашёл сетевой адаптер компьютера")
		return false
	}
	f, err := pcMacApply(mode, pcip, mac)
	if err != nil {
		logf("MAC компьютера: %v", err)
		return false
	}
	if len(f) >= 4 {
		norm := func(s string) string { return strings.ToLower(strings.ReplaceAll(s, "-", ":")) }
		if mode == "mirror" && norm(f[3]) != mac {
			logf("MAC компьютера: адаптер «%s» не принял новый адрес (так бывает у Wi-Fi-адаптеров — драйвер не даёт менять MAC). Сейчас: %s", f[1], f[3])
		} else {
			logf("MAC компьютера (адаптер «%s»): %s → %s", f[1], f[2], f[3])
		}
		setMacSt("pc", norm(f[3]))
	}
	// адаптер переподключился: адрес компьютера в сети мог смениться —
	// ждём адрес и заново прописываем плате облако
	setStep("MAC компьютера: жду адрес в сети и прописываю облако плате")
	for i := 0; i < 20; i++ {
		time.Sleep(2 * time.Second)
		if p := pcIPFor(c.IP); p != "127.0.0.1" && portOpen(c.IP, 7000, time.Second) {
			if p != pcip {
				logf("MAC компьютера: адрес компьютера сменился %s → %s", pcip, p)
			}
			configureBoard(c.IP)
			return true
		}
	}
	logf("MAC компьютера: плата пока не видна — облако ей пропишет «Запустить всё» или кнопка «Прописать облако» в сканере")
	return true
}

// macJob — одно действие с MAC за раз; во время «Запустить всё» и скана — нет.
func macJob(what string, auto bool) {
	if !setBusy("mac", true) {
		logf("MAC: уже идёт смена адреса…")
		return
	}
	defer setBusy("mac", false)
	// v3.157: промежуток считается от НАЧАЛА поворота — реальный спин = заданному
	// (раньше — от конца, и каждый оборот удлинялся на время самой смены)
	mu.Lock()
	running := busy["run"]
	mu.Unlock()
	if running {
		logf("MAC: идёт «Запустить всё» — сменю адрес после запуска (нажми ещё раз)")
		return
	}
	if !auto {
		lastMacRot = time.Now() // ручной поворот — авто ждёт полный промежуток
	}
	if auto && what == "rotate" { // реальный интервал авто-поворотов
		if !lastAutoAt.IsZero() {
			g := time.Since(lastAutoAt).Seconds()
			mu.Lock()
			old, _ := macSt["real_gap"].(float64)
			if old > 0 {
				g = 0.6*old + 0.4*g
			}
			macSt["real_gap"] = g
			mu.Unlock()
		}
		lastAutoAt = time.Now()
	}
	if spinOn && (what == "rotate" || what == "mirror") {
		logf("MAC: на плате идёт быстрый спин — сними галочку «вращать MAC с этой частотой», чтобы поворачивать вручную")
		return
	}
	switch what {
	case "spin":
		spinStart(macGap(getCfg()).Seconds())
	case "spinstop":
		spinStop()
	case "rotate":
		if boardMac("rotate", auto) && auto && on2(getCfg().MacPC) {
			pcMac("mirror")
		}
	case "mirror":
		pcMac("mirror")
	case "restore":
		if spinOn {
			mu.Lock()
			cfg.MacAuto = bptr(false)
			mu.Unlock()
			saveCfg()
			if !spinStop() {
				return
			}
		}
		boardMac("restore", false)
		pcMac("restore")
	case "show":
		macPoll()
	}
}

// on2 — галочка по умолчанию ВЫКЛЮЧЕНА (в отличие от on()).
func on2(p *bool) bool { return p != nil && *p }

// ---------------------------------------------------------- v3.154: быстрый спин
// Если смена адреса нужна чаще раза в минуту, лаунчер не крутит MAC сам
// (каждый поворот через SSH — десятки секунд), а запускает цикл ПРЯМО НА
// ПЛАТЕ: он переворачивает адрес так быстро, как успевает переподключаться
// сеть. Лаунчер только включает/выключает цикл и меняет его промежуток.
const macFastGap = time.Minute

var (
	spinOn    bool
	spinGap   = -1.0
	spinRetry time.Time
	spinPoll  time.Time
	macSeen   bool
)

func macCmd(arg string, to time.Duration) (string, error) {
	if !macPushed { // v3.158: старый скрипт на плате не понимает спин — обновить
		if v, e := sshRun("bash ~/ArduinoApps/n12_mac.sh version 2>/dev/null", "", 10*time.Second); e != nil ||
			!strings.Contains(v, "N12MAC:3.158") {
			if e2 := pushMacScript(); e2 != nil && e == nil {
				return "", e2
			}
		} else {
			macPushed = true
		}
	}
	out, err := sshRun("bash ~/ArduinoApps/n12_mac.sh "+arg, "", to)
	if strings.Contains(out, "No such file") {
		if pushMacScript() == nil {
			out, err = sshRun("bash ~/ArduinoApps/n12_mac.sh "+arg, "", to)
		}
	}
	return out, err
}

// macPoll — состояние MAC и спина с платы (тихо, без окон и без busy)
func macPoll() {
	out, err := macCmd("show", 8*time.Second)
	if err != nil {
		return
	}
	kv := parseKV(out)
	mu.Lock()
	for k, f := range map[string]string{"NOW": "board", "ORIG": "orig", "BASE": "base", "STEP": "step", "IFACE": "iface",
		"SP_COUNT": "sp_count", "SP_AVGSEC": "sp_avg", "SP_LASTSEC": "sp_last", "SP_STOPPED": "sp_stopped"} {
		macSt[f] = kv[k]
	}
	macSt["spin"] = kv["SPIN"] == "on"
	macSt["sp_ip"] = kv["SP_IP"]
	mu.Unlock()
	// v3.155: запомнить семейство MAC платы для предсказания адреса
	learnMAC(kv["ORIG"], "")
	learnMAC(kv["BASE"], "")
	learnMAC(kv["NOW"], getCfg().IP)
	if kv["SPIN"] == "on" {
		spinOn = true
	} else if kv["SPIN"] == "off" {
		spinOn = false
	}
}

func spinStart(gap float64) {
	if time.Now().Before(spinRetry) {
		return
	}
	g := strconv.FormatFloat(gap, 'f', -1, 64)
	out, err := macCmd("spin "+g+" 4", 15*time.Second) // 4 с — окно связи после смены
	switch {
	case strings.Contains(out, "ERR:SUDO"):
		logf("MAC: быстрый спин не запущен — пользователю платы нужен sudo без пароля (повтор через 5 мин)")
		spinRetry = time.Now().Add(5 * time.Minute)
	case err != nil || !strings.Contains(out, "OK:spin"):
		// плата во время спина то и дело вне сети, а с новым MAC — на новом IP:
		// ищем её по MAC и повторяем часто, пока новый спин не дойдёт
		logf("MAC: спин на плату не дошёл (%s) — повторю через 5 с", firstLn(out, err))
		go predictBoard("смена спина")
		spinRetry = time.Now().Add(5 * time.Second)
	default:
		if spinOn {
			logf("MAC: спин на плате — новый промежуток %s с", g)
		} else {
			logf("MAC: быстрый спин запущен НА ПЛАТЕ (промежуток %s с; быстрее сеть не успевает — плата почти всё время переподключается)", g)
		}
		spinOn, spinGap = true, gap
		setMacSt("spin", true)
	}
}

// spinStop — остановить цикл на плате; он вернёт прямой адрес. true — остановлен.
func spinStop() bool {
	setStep("MAC: останавливаю спин на плате…")
	defer setStep("")
	for i := 0; i < 2; i++ {
		for t0 := time.Now(); time.Since(t0) < 45*time.Second; time.Sleep(time.Second) {
			if out, err := macCmd("stop", 8*time.Second); err == nil && strings.Contains(out, "OK:stop") {
				for t1 := time.Now(); time.Since(t1) < 45*time.Second; time.Sleep(2 * time.Second) {
					if o, e := macCmd("show", 8*time.Second); e == nil && strings.Contains(o, "SPIN:off") {
						spinOn, spinGap = false, -1
						setMacSt("spin", false)
						logf("MAC: спин остановлен, плата на прямом адресе")
						return true
					}
				}
			}
		}
		if i == 0 {
			if predictBoard("во время спина") == "" {
				logf("MAC: плата не отвечает во время спина — ищу её сканером…")
				runScan()
			}
		}
	}
	logf("MAC: спин остановить не удалось — плата не отвечает. Перезагрузка платы тоже останавливает спин.")
	return false
}

// macTick — из monitor(): решает, нужен ли быстрый спин, медленный поворот или ничего.
func macTick(c Cfg, boardUp bool, running bool) {
	want := on2(c.MacAuto) && macGap(c) < macFastGap
	gapSec := macGap(c).Seconds()
	if boardUp && !macSeen {
		macSeen = true
		go macPoll() // спин мог остаться с прошлого запуска лаунчера
	}
	if running || c.IP == "" {
		return
	}
	switch {
	case want && (!spinOn || spinGap != gapSec) && boardUp:
		go macJob("spin", true)
	case !want && spinOn:
		go macJob("spinstop", true)
	case spinOn && time.Since(spinPoll) > 10*time.Second:
		spinPoll = time.Now()
		go macPoll()
	}
}
