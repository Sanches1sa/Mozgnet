// call.go — v3.238: «📞 Позвонить плате» — синхронный спин MAC двух плат.
//
// Боковая панель лаунчера показывает платы из реестра облака: видно, какие
// на связи и какие заняты (уже в сессии — им позвонить нельзя). Звонок соединяет
// ЭТУ плату (по её адресу cfg.IP) с выбранной: облако заводит сессию спина с
// общим seed, временем старта и одним периодом, обе платы крутят MAC синхронно
// не дольше 38 минут, скрытно общаясь с облаком. Предсказание IP и загрузка
// скрипта на платах включаются сами (см. board/python/mac_spin.py).
package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strings"
	"time"
)

type callBoard struct {
	ID     string `json:"id"`
	Name   string `json:"name"`
	IP     string `json:"ip"`
	Online bool   `json:"online"`
	Busy   bool   `json:"busy"`
}

var (
	callSt     = map[string]interface{}{} // то, что видит панель: boards, active, self, max_minutes
	callPeriod = 8.0                       // период смены MAC в сессии, с
)

// cloudTok — токен для обращения к облаку (своё локальное или в домашней сети).
func cloudTok() string {
	c := getCfg()
	if _, ok := remoteCloud(); ok {
		return strings.NewReplacer("'", "", "\"", "", " ", "").Replace(strings.TrimSpace(c.RemoteToken))
	}
	return cloudToken(c.Cloud)
}

func cloudGet(path string, to time.Duration) (map[string]interface{}, error) {
	u := strings.TrimRight(cloudURL(), "/") + path
	if strings.Contains(path, "?") {
		u += "&token=" + cloudTok()
	} else {
		u += "?token=" + cloudTok()
	}
	cl := &http.Client{Timeout: to, Transport: &http.Transport{Proxy: nil}}
	r, err := cl.Get(u)
	if err != nil {
		return nil, err
	}
	defer r.Body.Close()
	b, _ := io.ReadAll(io.LimitReader(r.Body, 1<<20))
	var d map[string]interface{}
	json.Unmarshal(b, &d)
	return d, nil
}

func cloudPost(path string, body map[string]interface{}, to time.Duration) (map[string]interface{}, error) {
	u := strings.TrimRight(cloudURL(), "/") + path
	raw, _ := json.Marshal(body)
	req, _ := http.NewRequest("POST", u, bytes.NewReader(raw))
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("X-Mozg-Token", cloudTok())
	cl := &http.Client{Timeout: to, Transport: &http.Transport{Proxy: nil}}
	r, err := cl.Do(req)
	if err != nil {
		return nil, err
	}
	defer r.Body.Close()
	b, _ := io.ReadAll(io.LimitReader(r.Body, 1<<20))
	var d map[string]interface{}
	json.Unmarshal(b, &d)
	return d, nil
}

// myBoardID — id ЭТОЙ платы в облаке: из /api/alive платы, иначе по совпадению IP.
func myBoardID(boards []interface{}) string {
	c := getCfg()
	if c.IP == "" {
		return ""
	}
	mu.Lock()
	id, _ := boardSt["board_id"].(string)
	mu.Unlock()
	if id != "" {
		return id
	}
	for _, x := range boards {
		m, _ := x.(map[string]interface{})
		ip, _ := m["ip"].(string)
		if ip != "" && strings.Contains(ip, c.IP) {
			if s, _ := m["id"].(string); s != "" {
				return s
			}
		}
	}
	return ""
}

// callPoll — состояние для панели (раз в несколько секунд из monitor).
func callPoll() {
	d, err := cloudGet("/api/spin", 4*time.Second)
	if err != nil || d == nil {
		mu.Lock()
		callSt = map[string]interface{}{"err": "облако не отвечает"}
		mu.Unlock()
		return
	}
	boards, _ := d["boards"].([]interface{})
	self := myBoardID(boards)
	mu.Lock()
	callSt = map[string]interface{}{"boards": boards, "active": d["active"], "recent": d["recent"],
		"self": self, "max_minutes": d["max_minutes"], "period": callPeriod}
	mu.Unlock()
}

// ensureMacSudo — чтобы спин вращался сам (без окна паролей на каждом шаге),
// у пользователя ЭТОЙ платы должен работать `sudo -n` для nmcli/ip. Ставим
// разовый sudoers (один раз спросит пароль платы, дальше звонки идут молча).
func ensureMacSudo() {
	c := getCfg()
	if c.IP == "" || !keyOK() {
		return
	}
	if out, err := sshRun("sudo -n true 2>/dev/null && echo n12ok", "", 10*time.Second); err == nil && strings.Contains(out, "n12ok") {
		return // sudo -n уже есть — спин сможет крутить MAC сам
	}
	script := "U=" + shq(c.User) + "\n" +
		"F=/etc/sudoers.d/neuro12-mac\n" +
		"printf '%s ALL=(root) NOPASSWD: /usr/bin/nmcli, /bin/nmcli, /usr/bin/ip, /bin/ip, /sbin/ip, /usr/sbin/ip\\n' \"$U\" > \"$F\"\n" +
		"chmod 440 \"$F\"\n" +
		"if command -v visudo >/dev/null 2>&1 && ! visudo -cf \"$F\" >/dev/null 2>&1; then rm -f \"$F\"; echo ERR:visudo; exit 1; fi\n" +
		"echo ok"
	logf("Звонок: настраиваю на плате право менять MAC без пароля (sudo -n), чтобы спин вращался сам…")
	sudoRun("Разрешить плате менять MAC без пароля (для звонков)", script, 3*time.Minute)
}

func placeCall(to string) {
	if !setBusy("call", true) {
		return
	}
	defer setBusy("call", false)
	ensureMacSudo()
	d, _ := cloudGet("/api/spin", 4*time.Second)
	boards, _ := d["boards"].([]interface{})
	from := myBoardID(boards)
	if from == "" {
		logf("Звонок: эта плата не в реестре облака (запусти её и дождись регистрации) — звонить не от кого.")
		return
	}
	if to == "" || to == from {
		logf("Звонок: выбери ДРУГУЮ плату (не эту же).")
		return
	}
	logf("Звонок: соединяю %s ↔ %s (период %.0f с, до 38 мин)…", from, to, callPeriod)
	r, err := cloudPost("/api/spin", map[string]interface{}{"action": "call", "a": from, "b": to, "period": callPeriod}, 8*time.Second)
	if err != nil {
		logf("Звонок не прошёл: %v", err)
		return
	}
	if r["ok"] != true {
		logf("Звонок отклонён: %v", r["error"])
		return
	}
	logf("Звонок пошёл: обе платы крутят MAC синхронно. Адреса меняются — ищу платы по предсказанию.")
	go predictBoard("звонок платам")
	callPoll()
}

func hangupCall(id string) {
	body := map[string]interface{}{"action": "hangup"}
	if id != "" {
		body["id"] = id
	} else if b := myBoardID(nil); b != "" {
		body["board"] = b
	}
	r, err := cloudPost("/api/spin", body, 6*time.Second)
	if err != nil {
		logf("Отбой: %v", err)
		return
	}
	logf("Отбой: сессий остановлено %v. Платы вернут заводской MAC.", r["stopped"])
	callPoll()
}

func setCallPeriod(sec float64) {
	if sec < 2 {
		sec = 2
	}
	if sec > 600 {
		sec = 600
	}
	callPeriod = sec
	logf("Звонок: период смены MAC = %s", fmt.Sprintf("%.0f с", sec))
}
