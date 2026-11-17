// pkg.go — v3.161: УСТАНОВКА ИЗ ОБЩЕГО ZIP-АРХИВА и APK для телефона.
//
// Общий архив neuro12-all-v….zip (собирается GitHub Actions):
//
//	neuro12-cloud/  — облако (server, web, runtime — свой Python для Windows, launcher)
//	neuro12-app/    — приложение платы neuro_12 (app.yaml, python, sketch, assets)
//	apk/            — mozgnet-….apk (приложение для телефона)
//	install.exe, README-Windows.txt
//
// ⚙ → «Установка из ZIP-архива» → выбрать архив. Лаунчер сам решает, что
// куда: папку с server/app.py — в папку облака (data/ не трогает, облако
// останавливает и запускает снова), папку с app.yaml — в свою папку
// приложений платы (её и зальёт на плату ⏻), APK — в свою папку apk (кнопка
// 📱 сбоку: сохранить в «Загрузки» или раздать на телефон по Wi-Fi).
// Подходят и отдельные архивы: обновление облака, neuro_12-v….zip платы.
package main

import (
	"archive/zip"
	"crypto/rand"
	"encoding/hex"
	"fmt"
	"io"
	"net"
	"net/http"
	"os"
	"path"
	"path/filepath"
	"regexp"
	"runtime"
	"sort"
	"strings"
	"sync"
	"time"
)

var (
	pkgMu sync.Mutex
	pkgSt = map[string]interface{}{"state": "", "msg": ""}
	shr   = struct {
		sync.Mutex
		ln    net.Listener
		urls  []string
		until time.Time
		hits  int
	}{}
)

func setPkg(state, msg string) {
	pkgMu.Lock()
	pkgSt["state"], pkgSt["msg"], pkgSt["ts"] = state, msg, time.Now().Unix()
	pkgMu.Unlock()
	if msg != "" {
		logf("Архив: %s", msg)
	}
}

func pkgState() map[string]interface{} {
	pkgMu.Lock()
	defer pkgMu.Unlock()
	o := map[string]interface{}{}
	for k, v := range pkgSt {
		o[k] = v
	}
	return o
}

// safeRel — путь внутри архива без «..», абсолютных путей и дисков
func safeRel(name string) (string, bool) {
	n := strings.ReplaceAll(name, "\\", "/")
	if strings.HasPrefix(n, "/") || strings.Contains(n, ":") {
		return "", false
	}
	for _, p := range strings.Split(n, "/") {
		if p == ".." {
			return "", false
		}
	}
	return n, true
}

type zipPlan struct {
	cloud, app string // префиксы внутри архива ("" допустим для app)
	hasCloud   bool
	hasApp     bool
	apks       []*zip.File
	appVer     string
	cloudVer   string
}

var reAppVer = regexp.MustCompile(`APP_VERSION = "([^"]+)"`)

func inspectZip(zr *zip.ReadCloser) zipPlan {
	names := map[string]*zip.File{}
	for _, f := range zr.File {
		if n, ok := safeRel(f.Name); ok {
			names[n] = f
		}
	}
	var pl zipPlan
	best := func(cur string, have bool, cand string) bool {
		return !have || strings.Count(cand, "/") < strings.Count(cur, "/")
	}
	for n, f := range names {
		switch {
		case strings.HasSuffix(n, "server/app.py"):
			p := strings.TrimSuffix(n, "server/app.py")
			if names[p+"web/index.html"] != nil && best(pl.cloud, pl.hasCloud, p) {
				pl.cloud, pl.hasCloud = p, true
			}
		case n == "app.yaml" || strings.HasSuffix(n, "/app.yaml"):
			p := strings.TrimSuffix(n, "app.yaml")
			if strings.Contains(p, "neuro12-cloud/") {
				continue
			}
			if names[p+"python/main.py"] != nil && names[p+"sketch/sketch.ino"] != nil && best(pl.app, pl.hasApp, p) {
				pl.app, pl.hasApp = p, true
			}
		case strings.HasSuffix(strings.ToLower(n), ".apk") && !f.FileInfo().IsDir():
			pl.apks = append(pl.apks, f)
		}
	}
	if pl.hasApp {
		if f := names[pl.app+"python/main.py"]; f != nil {
			if rc, err := f.Open(); err == nil {
				b, _ := io.ReadAll(io.LimitReader(rc, 1<<20))
				rc.Close()
				if m := reAppVer.FindSubmatch(b); m != nil {
					pl.appVer = string(m[1])
				}
			}
		}
	}
	if pl.hasCloud {
		if f := names[pl.cloud+"VERSION"]; f != nil {
			if rc, err := f.Open(); err == nil {
				b, _ := io.ReadAll(io.LimitReader(rc, 100))
				rc.Close()
				pl.cloudVer = strings.TrimSpace(string(b))
			}
		}
	}
	return pl
}

func writeZipFile(f *zip.File, dst string) error {
	if err := os.MkdirAll(filepath.Dir(dst), 0755); err != nil {
		return err
	}
	rc, err := f.Open()
	if err != nil {
		return err
	}
	defer rc.Close()
	tmp := dst + ".n12tmp"
	w, err := os.Create(tmp)
	if err != nil {
		return err
	}
	_, err = io.Copy(w, rc)
	w.Close()
	if err != nil {
		os.Remove(tmp)
		return err
	}
	if f.Mode()&0111 != 0 {
		os.Chmod(tmp, 0755)
	}
	if err := os.Rename(tmp, dst); err != nil { // Windows: занятый файл — копия рядом
		os.Remove(tmp)
		return err
	}
	return nil
}

func cloudUp(dir string) bool {
	_, code, err := getJSON(fmt.Sprintf("http://127.0.0.1:%d/api/health", cloudPort(dir)), 1500*time.Millisecond)
	return err == nil && code < 500
}

// installZip — разложить общий архив по местам
func installZip(zpath string, deploy bool) {
	if !setBusy("zip", true) {
		logf("Архив уже устанавливается…")
		return
	}
	defer setBusy("zip", false)
	setPkg("работаю", "открываю "+filepath.Base(zpath))
	zr, err := zip.OpenReader(zpath)
	if err != nil {
		setPkg("ошибка", "не ZIP-архив: "+err.Error())
		return
	}
	defer zr.Close()
	pl := inspectZip(zr)
	if !pl.hasCloud && !pl.hasApp && len(pl.apks) == 0 {
		setPkg("ошибка", "в архиве нет ни облака (neuro12-cloud/server/app.py), ни приложения платы (app.yaml), ни APK")
		return
	}
	done := []string{}
	exe, _ := os.Executable()
	exe, _ = filepath.Abs(exe)

	// ---------------- облако ----------------
	if pl.hasCloud {
		c := getCfg()
		dst := c.Cloud
		if dst == "" || !exists(filepath.Join(dst, "server", "app.py")) {
			dst = cloudCandidates()[0]
		}
		wasUp := cloudUp(dst)
		if wasUp {
			setPkg("работаю", "останавливаю облако, чтобы заменить его файлы…")
			stopCloudProc(dst)
			for i := 0; i < 20 && cloudUp(dst); i++ {
				time.Sleep(500 * time.Millisecond)
			}
		}
		haveRT := exists(filepath.Join(dst, "runtime", "python.exe")) || exists(filepath.Join(dst, "runtime", "bin", "python3"))
		n, skipped, delayed := 0, 0, 0
		setPkg("работаю", "облако "+pl.cloudVer+" → "+dst)
		for _, f := range zr.File {
			rel, ok := safeRel(f.Name)
			if !ok || !strings.HasPrefix(rel, pl.cloud) || f.FileInfo().IsDir() {
				continue
			}
			r := strings.TrimPrefix(rel, pl.cloud)
			if r == "" || strings.HasPrefix(r, "data/") || strings.Contains(r, "__pycache__/") {
				continue
			}
			if strings.HasPrefix(r, "runtime/") && (haveRT || runtime.GOOS != "windows") {
				skipped++ // свой Python уже стоит (или это не Windows)
				continue
			}
			out := filepath.Join(dst, filepath.FromSlash(r))
			if a, _ := filepath.Abs(out); strings.EqualFold(a, exe) {
				out = exe + ".new" // запущенный лаунчер — заменится при следующем старте
				delayed++
			}
			if err := writeZipFile(f, out); err != nil {
				setPkg("ошибка", fmt.Sprintf("облако: не записался %s: %v", r, err))
				return
			}
			n++
		}
		if c.Cloud != dst {
			mu.Lock()
			cfg.Cloud = dst
			mu.Unlock()
			saveCfg()
		}
		msg := fmt.Sprintf("облако %s: %d файлов в %s (data/ не тронута)", pl.cloudVer, n, dst)
		if skipped > 0 && runtime.GOOS != "windows" {
			msg += "; встроенный Python для Windows пропущен (здесь — системный)"
		} else if skipped > 0 {
			msg += "; свой Python уже стоит — runtime не перезаписан"
		}
		if delayed > 0 {
			msg += "; лаунчер обновится при следующем открытии"
		}
		done = append(done, msg)
		setPkg("работаю", msg)
		if wasUp || on(c.AutoCloud) {
			go startCloud()
		}
	}

	// ---------------- приложение платы ----------------
	if pl.hasApp {
		ver := pl.appVer
		if ver == "" {
			ver = time.Now().Format("20060102-150405")
		}
		base := filepath.Join(launcherDir(), "apps")
		dst := filepath.Join(base, "neuro_12-v"+ver)
		os.RemoveAll(dst)
		n := 0
		for _, f := range zr.File {
			rel, ok := safeRel(f.Name)
			if !ok || !strings.HasPrefix(rel, pl.app) || f.FileInfo().IsDir() {
				continue
			}
			r := strings.TrimPrefix(rel, pl.app)
			if r == "" || strings.Contains(r, "__pycache__/") || strings.HasPrefix(r, "neuro12-cloud/") || strings.HasPrefix(r, "apk/") {
				continue
			}
			if err := writeZipFile(f, filepath.Join(dst, filepath.FromSlash(r))); err != nil {
				setPkg("ошибка", fmt.Sprintf("плата: не записался %s: %v", r, err))
				return
			}
			n++
		}
		// старые версии — оставить три последних
		if es, err := os.ReadDir(base); err == nil {
			var ds []string
			for _, e := range es {
				if e.IsDir() && strings.HasPrefix(e.Name(), "neuro_12-v") {
					ds = append(ds, filepath.Join(base, e.Name()))
				}
			}
			sort.Slice(ds, func(i, j int) bool {
				a, _ := os.Stat(ds[i])
				b, _ := os.Stat(ds[j])
				return a.ModTime().After(b.ModTime())
			})
			keep := 3
			if len(ds) < keep {
				keep = len(ds)
			}
			for _, d := range ds[keep:] {
				if d != dst {
					os.RemoveAll(d)
				}
			}
		}
		mu.Lock()
		cfg.App = dst
		mu.Unlock()
		saveCfg()
		done = append(done, fmt.Sprintf("приложение платы v%s: %d файлов → %s (его зальёт на плату ⏻)", ver, n, dst))
	}

	// ---------------- APK ----------------
	if len(pl.apks) > 0 {
		dir := filepath.Join(launcherDir(), "apk")
		os.MkdirAll(dir, 0755)
		var last string
		for _, f := range pl.apks {
			out := filepath.Join(dir, path.Base(strings.ReplaceAll(f.Name, "\\", "/")))
			if err := writeZipFile(f, out); err != nil {
				setPkg("ошибка", "APK не записался: "+err.Error())
				return
			}
			last = out
		}
		mu.Lock()
		cfg.Apk = last
		mu.Unlock()
		saveCfg()
		done = append(done, "APK для телефона: "+filepath.Base(last)+" (кнопка 📱 сбоку)")
	}
	setPkg("готово", strings.Join(done, " · "))
	if deploy && pl.hasApp {
		logf("Архив: заливаю приложение на плату (как кнопка ⏻)…")
		go runAll()
	}
}

// ------------------------------------------------------------------- APK
func apkFiles() []string {
	var out []string
	seen := map[string]bool{}
	add := func(p string) {
		if p != "" && exists(p) && !seen[strings.ToLower(p)] {
			seen[strings.ToLower(p)] = true
			out = append(out, p)
		}
	}
	add(getCfg().Apk)
	dirs := []string{filepath.Join(launcherDir(), "apk")}
	if c := getCfg().Cloud; c != "" {
		dirs = append(dirs, filepath.Join(filepath.Dir(c), "apk"), filepath.Join(c, "apk"))
	}
	dirs = append(dirs, filepath.Join(exeDir, "apk"), filepath.Join(filepath.Dir(exeDir), "apk"))
	dirs = append(dirs, downloadDirs()...)
	for _, d := range dirs {
		ms, _ := filepath.Glob(filepath.Join(d, "mozgnet*.apk"))
		for _, m := range ms {
			add(m)
		}
	}
	sort.SliceStable(out, func(i, j int) bool {
		a, _ := os.Stat(out[i])
		b, _ := os.Stat(out[j])
		return a.ModTime().After(b.ModTime())
	})
	return out
}

func apkState() map[string]interface{} {
	fs := apkFiles()
	lst := []map[string]interface{}{}
	for _, f := range fs {
		if fi, err := os.Stat(f); err == nil {
			lst = append(lst, map[string]interface{}{"path": f, "name": filepath.Base(f), "mb": float64(fi.Size()*10/1048576) / 10,
				"date": fi.ModTime().Format("02.01.2006 15:04")})
		}
	}
	shr.Lock()
	defer shr.Unlock()
	o := map[string]interface{}{"files": lst}
	if shr.ln != nil && time.Now().Before(shr.until) {
		o["share"] = map[string]interface{}{"urls": shr.urls, "left": int(time.Until(shr.until).Seconds()), "hits": shr.hits}
	}
	return o
}

func apkPick(p string) string {
	for _, f := range apkFiles() {
		if p == "" || f == p {
			return f
		}
	}
	return ""
}

// apkSave — копия APK в «Загрузки» и показать её
func apkSave(p string) {
	src := apkPick(p)
	if src == "" {
		logf("APK не найден — установи общий архив (⚙ → «Установка из ZIP-архива»).")
		return
	}
	dd := downloadDirs()[0]
	os.MkdirAll(dd, 0755)
	dst := filepath.Join(dd, filepath.Base(src))
	if !strings.EqualFold(src, dst) {
		in, err := os.Open(src)
		if err != nil {
			logf("APK: %v", err)
			return
		}
		out, err := os.Create(dst)
		if err != nil {
			in.Close()
			logf("APK: %v", err)
			return
		}
		io.Copy(out, in)
		in.Close()
		out.Close()
	}
	logf("APK сохранён: %s — перекинь его на телефон (кабель, мессенджер) или раздай по Wi-Fi кнопкой «Раздать на телефон».", dst)
	revealFile(dst)
}

// apkShare — раздать APK телефону по Wi-Fi: временный адрес на 15 минут
// со случайным кодом в пути; кроме этого файла сервер ничего не отдаёт.
func apkShare(p string) {
	src := apkPick(p)
	if src == "" {
		logf("APK не найден — установи общий архив (⚙ → «Установка из ZIP-архива»).")
		return
	}
	shr.Lock()
	if shr.ln != nil {
		shr.ln.Close()
		shr.ln = nil
	}
	b := make([]byte, 4)
	rand.Read(b)
	code := hex.EncodeToString(b)
	ln, err := net.Listen("tcp", ":0")
	if err != nil {
		shr.Unlock()
		logf("Раздача APK: не открыть порт: %v", err)
		return
	}
	port := ln.Addr().(*net.TCPAddr).Port
	name := filepath.Base(src)
	shr.ln, shr.until, shr.hits, shr.urls = ln, time.Now().Add(15*time.Minute), 0, nil
	for _, n := range localNets() {
		shr.urls = append(shr.urls, fmt.Sprintf("http://%s:%d/%s", n.ip, port, code))
	}
	urls := append([]string(nil), shr.urls...)
	shr.Unlock()
	mux := http.NewServeMux()
	mux.HandleFunc("/", func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/"+code && r.URL.Path != "/"+code+"/"+name {
			http.NotFound(w, r)
			return
		}
		if r.URL.Path == "/"+code { // страница с кнопкой — телефонный браузер так понятнее
			w.Header().Set("Content-Type", "text/html; charset=utf-8")
			fmt.Fprintf(w, `<!doctype html><meta name="viewport" content="width=device-width,initial-scale=1"><body style="font:18px system-ui;background:#0b0e14;color:#eef2f8;text-align:center;padding:40px 16px">`+
				`<h2>🧠 mozgnet для телефона</h2><p>%s</p><p><a style="display:inline-block;padding:16px 26px;border-radius:14px;background:#2dd4bf;color:#04110f;text-decoration:none;font-weight:600" href="/%s/%s">⬇ Скачать APK</a></p>`+
				`<p style="color:#8b95ab;font-size:14px">После скачивания открой файл и разреши установку из этого источника.</p>`, name, code, name)
			return
		}
		shr.Lock()
		shr.hits++
		shr.Unlock()
		w.Header().Set("Content-Type", "application/vnd.android.package-archive")
		w.Header().Set("Content-Disposition", `attachment; filename="`+name+`"`)
		http.ServeFile(w, r, src)
	})
	srv := &http.Server{Handler: mux}
	go srv.Serve(ln)
	go func() {
		time.Sleep(15 * time.Minute)
		shr.Lock()
		if shr.ln == ln {
			shr.ln = nil
		}
		shr.Unlock()
		srv.Close()
	}()
	logf("Раздаю %s по Wi-Fi 15 минут. Открой на телефоне (та же сеть): %s", name, strings.Join(urls, "  или  "))
	if runtime.GOOS == "windows" {
		logf("Если телефон не открывает адрес — разреши лаунчеру доступ в окне брандмауэра Windows (частная сеть).")
	}
}

func apkShareStop() {
	shr.Lock()
	if shr.ln != nil {
		shr.ln.Close()
		shr.ln = nil
	}
	shr.Unlock()
	logf("Раздача APK остановлена.")
}
