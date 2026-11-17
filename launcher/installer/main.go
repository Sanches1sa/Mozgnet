// install.exe — УСТАНОВЩИК И ОБНОВЛЯТОР ОБЛАКА «МОЗГ-12» ДЛЯ WINDOWS (v3.89)
//
// Динамический: всё решает по тому, что лежит РЯДОМ с ним и что уже
// установлено, а не по зашитому списку файлов:
//   * пакет — папка рядом (server\app.py, web, data…) или полный архив
//     neuro12-cloud-full-*.zip рядом (распакуется во временную папку);
//   * установка — %LOCALAPPDATA%\neuro12-cloud (или /dir:<путь>);
//   * данные пакета (база знаний, файлы, сеть материалов, своя LLM с файлами
//     подкачки Adam — data\llm_adam) переносятся: в пустую установку — все;
//     в существующую — по выбору: только новые и более свежие файлы
//     (по умолчанию), целиком с резервной копией, или не трогать;
//   * обновления: архивы neuro12-cloud-*-update.zip рядом с install.exe,
//     версия которых новее установленной, ставятся поверх кода (данные не
//     трогаются) — самый новый последним;
//   * дальше всё делает install.ps1 из пакета (Python, numpy, пароль,
//     автозапуск, ярлыки, запуск облака).
// Ключи: /merge /replace /code /yes /dir:<путь> (без ключей — спросит).
// Сборка: GOOS=windows GOARCH=amd64 go build -o install.exe (build.sh).
package main

import (
	"archive/zip"
	"bufio"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"time"
)

var in = bufio.NewReader(os.Stdin)

func say(f string, a ...interface{})  { fmt.Printf("\n==> "+f+"\n", a...) }
func ok(f string, a ...interface{})   { fmt.Printf("  [OK] "+f+"\n", a...) }
func warn(f string, a ...interface{}) { fmt.Printf("  [!!] "+f+"\n", a...) }

func pause() {
	fmt.Print("\nНажмите Enter, чтобы закрыть окно…")
	in.ReadString('\n')
}

func die(f string, a ...interface{}) {
	fmt.Printf("  [XX] "+f+"\n", a...)
	pause()
	os.Exit(1)
}

func exists(p string) bool { _, err := os.Stat(p); return err == nil }

func readVer(dir string) string {
	b, err := os.ReadFile(filepath.Join(dir, "VERSION"))
	if err != nil {
		return ""
	}
	return strings.TrimSpace(string(b))
}

func verNum(v string) []int {
	out := []int{}
	for _, p := range strings.Split(strings.TrimSpace(v), ".") {
		n, _ := strconv.Atoi(p)
		out = append(out, n)
	}
	for len(out) < 3 {
		out = append(out, 0)
	}
	return out
}

func newer(a, b string) bool { // a > b
	x, y := verNum(a), verNum(b)
	for i := 0; i < 3; i++ {
		if x[i] != y[i] {
			return x[i] > y[i]
		}
	}
	return false
}

func dirSize(p string) (int64, int) {
	var s int64
	n := 0
	filepath.Walk(p, func(_ string, fi os.FileInfo, err error) error {
		if err == nil && !fi.IsDir() {
			s += fi.Size()
			n++
		}
		return nil
	})
	return s, n
}

func mb(b int64) string { return fmt.Sprintf("%.1f МБ", float64(b)/1048576.0) }

// распаковать zip; strip — префикс внутри архива («neuro12-cloud/»);
// skip(rel) — не распаковывать
func unzip(zipPath, dst, strip string, skip func(string) bool) (int, error) {
	r, err := zip.OpenReader(zipPath)
	if err != nil {
		return 0, err
	}
	defer r.Close()
	n := 0
	for _, f := range r.File {
		name := f.Name
		if strip != "" {
			if !strings.HasPrefix(name, strip) {
				continue
			}
			name = strings.TrimPrefix(name, strip)
		}
		if name == "" || (skip != nil && skip(name)) {
			continue
		}
		target := filepath.Join(dst, filepath.FromSlash(name))
		if !strings.HasPrefix(filepath.Clean(target), filepath.Clean(dst)) {
			continue // защита от «../»
		}
		if f.FileInfo().IsDir() {
			os.MkdirAll(target, 0755)
			continue
		}
		os.MkdirAll(filepath.Dir(target), 0755)
		rc, err := f.Open()
		if err != nil {
			return n, err
		}
		w, err := os.Create(target)
		if err != nil {
			rc.Close()
			return n, err
		}
		_, err = io.Copy(w, rc)
		w.Close()
		rc.Close()
		if err != nil {
			return n, err
		}
		os.Chtimes(target, f.Modified, f.Modified)
		n++
	}
	return n, nil
}

func copyFile(src, dst string, mt time.Time) error {
	os.MkdirAll(filepath.Dir(dst), 0755)
	s, err := os.Open(src)
	if err != nil {
		return err
	}
	defer s.Close()
	tmp := dst + ".n12tmp"
	d, err := os.Create(tmp)
	if err != nil {
		return err
	}
	if _, err = io.Copy(d, s); err != nil {
		d.Close()
		os.Remove(tmp)
		return err
	}
	d.Close()
	os.Remove(dst)
	if err = os.Rename(tmp, dst); err != nil {
		return err
	}
	return os.Chtimes(dst, mt, mt)
}

// модель своей LLM — единое целое: веса (tinyllm_np.*) и файлы подкачки
// Adam (llm_adam\*) переносятся ВМЕСТЕ, иначе состояние оптимизатора не
// совпадёт с весами
func isModel(low string) bool {
	return strings.HasPrefix(low, "tinyllm_np.") || strings.HasPrefix(low, "llm_adam")
}

func mergeModel(src, dst string) string {
	sj := filepath.Join(src, "tinyllm_np.json")
	si, e := os.Stat(sj)
	if e != nil {
		return "в пакете модели нет"
	}
	if di, e := os.Stat(filepath.Join(dst, "tinyllm_np.json")); e == nil &&
		!si.ModTime().After(di.ModTime()) {
		return "своя модель установки новее или та же — оставлена"
	}
	os.RemoveAll(filepath.Join(dst, "llm_adam"))
	n := 0
	filepath.Walk(src, func(p string, fi os.FileInfo, err error) error {
		if err != nil || fi.IsDir() {
			return nil
		}
		rel, _ := filepath.Rel(src, p)
		if isModel(strings.ToLower(filepath.ToSlash(rel))) {
			if copyFile(p, filepath.Join(dst, rel), fi.ModTime()) == nil {
				n++
			}
		}
		return nil
	})
	return fmt.Sprintf("модель из пакета перенесена целиком (файлов %d, вместе с файлами подкачки Adam)", n)
}

// перенести в dst только новые и более свежие файлы из src
func mergeData(src, dst string) (int, int) {
	copied, kept := 0, 0
	filepath.Walk(src, func(p string, fi os.FileInfo, err error) error {
		if err != nil || fi.IsDir() {
			return nil
		}
		rel, _ := filepath.Rel(src, p)
		low := strings.ToLower(filepath.ToSlash(rel))
		if strings.HasPrefix(low, "_exports") || strings.HasSuffix(low, ".tmp") ||
			isModel(low) {
			return nil
		}
		t := filepath.Join(dst, rel)
		if di, e := os.Stat(t); e == nil && !fi.ModTime().After(di.ModTime()) {
			kept++
			return nil
		}
		if e := copyFile(p, t, fi.ModTime()); e != nil {
			warn("не скопирован %s: %v", rel, e)
		} else {
			copied++
		}
		return nil
	})
	return copied, kept
}

func stopCloud() {
	cmd := exec.Command("powershell", "-NoProfile", "-Command",
		"Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*server\\app.py*' -and $_.Name -match 'python' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force; Write-Host ('  [OK] остановлено облако, процесс ' + $_.ProcessId) }")
	cmd.Stdout, cmd.Stderr = os.Stdout, os.Stderr
	cmd.Run()
	time.Sleep(1500 * time.Millisecond)
}

func ask(q, def string) string {
	fmt.Print(q)
	s, _ := in.ReadString('\n')
	s = strings.TrimSpace(s)
	if s == "" {
		return def
	}
	return s
}

func main() {
	utf8Console()
	fmt.Println("\n  Мозг-12 · установка и обновление облака (install.exe)")
	fmt.Println("  =====================================================")

	mode, yes, dir := "", false, ""
	for _, a := range os.Args[1:] {
		l := strings.ToLower(a)
		switch {
		case l == "/merge" || l == "-merge":
			mode = "merge"
		case l == "/replace" || l == "-replace":
			mode = "replace"
		case l == "/code" || l == "-code":
			mode = "code"
		case l == "/yes" || l == "-yes" || l == "/y":
			yes = true
		case strings.HasPrefix(l, "/dir:"):
			dir = a[5:]
		}
	}
	exe, _ := os.Executable()
	here := filepath.Dir(exe)

	// ---------- 1. пакет ----------
	say("ПАКЕТ")
	pkg := ""
	for _, c := range []string{here, filepath.Join(here, "neuro12-cloud")} {
		if exists(filepath.Join(c, "server", "app.py")) && exists(filepath.Join(c, "install.ps1")) {
			pkg = c
			break
		}
	}
	if pkg == "" {
		full, _ := filepath.Glob(filepath.Join(here, "neuro12-cloud-full-*.zip"))
		sort.Strings(full)
		if len(full) > 0 {
			z := full[len(full)-1]
			tmp := filepath.Join(os.TempDir(), "n12pkg-"+strconv.FormatInt(time.Now().Unix(), 10))
			fmt.Printf("  распаковываю %s …\n", filepath.Base(z))
			if _, err := unzip(z, tmp, "", nil); err != nil {
				die("архив не распаковался: %v", err)
			}
			pkg = filepath.Join(tmp, "neuro12-cloud")
		}
	}
	if pkg == "" {
		die("рядом с install.exe нет облака: положите install.exe в распакованную папку neuro12-cloud или рядом с архивом neuro12-cloud-full-*.zip")
	}
	pver := readVer(pkg)
	ok("облако v%s: %s", pver, pkg)
	pdata := filepath.Join(pkg, "data")
	hasData := exists(filepath.Join(pdata, "settings.json"))
	if hasData {
		s, n := dirSize(pdata)
		ok("данные в пакете: %d файлов, %s", n, mb(s))
		if s2, n2 := dirSize(filepath.Join(pdata, "kb")); n2 > 0 {
			ok("  база знаний: %d статей-файлов, %s", n2, mb(s2))
		}
		if fi, e := os.Stat(filepath.Join(pdata, "tinyllm_np.npz")); e == nil {
			ok("  своя LLM (веса): %s", mb(fi.Size()))
		}
		if s3, n3 := dirSize(filepath.Join(pdata, "llm_adam")); n3 > 0 {
			ok("  своя LLM, файлы подкачки (Adam): %d файлов, %s", n3, mb(s3))
		}
		if fi, e := os.Stat(filepath.Join(pdata, "mozgnet_state.json")); e == nil {
			ok("  сеть материалов (мозг): %s", mb(fi.Size()))
		}
	} else {
		ok("данных в пакете нет — только код")
	}

	// ---------- 2. установка ----------
	if dir == "" {
		dir = filepath.Join(os.Getenv("LOCALAPPDATA"), "neuro12-cloud")
	}
	say("УСТАНОВКА: %s", dir)
	iver := readVer(dir)
	idata := filepath.Join(dir, "data")
	haveOld := exists(filepath.Join(idata, "settings.json"))
	if iver != "" {
		rel := "та же версия"
		if newer(pver, iver) {
			rel = "будет обновлена до v" + pver
		} else if newer(iver, pver) {
			rel = "установлена НОВЕЕ пакета (код всё равно заменится на v" + pver + ")"
		}
		ok("установлено облако v%s — %s", iver, rel)
	} else {
		ok("облако ещё не установлено")
	}
	if hasData && haveOld && mode == "" {
		if yes {
			mode = "merge"
		} else {
			fmt.Println("\n  В установке уже есть свои данные. Что сделать с данными из пакета?")
			fmt.Println("   1 — добавить новые и более свежие файлы (база, модель, сеть), остальное оставить [по умолчанию]")
			fmt.Println("   2 — заменить данные целиком (прежние — в резервную копию data.bak-…)")
			fmt.Println("   3 — не трогать данные, обновить только код")
			switch ask("  Ваш выбор [1]: ", "1") {
			case "2":
				mode = "replace"
			case "3":
				mode = "code"
			default:
				mode = "merge"
			}
		}
	}

	// ---------- 3. данные ----------
	stopCloud()
	psArgs := []string{"-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
		filepath.Join(pkg, "install.ps1"), "-Dir", dir}
	if hasData && haveOld {
		switch mode {
		case "merge":
			say("ДАННЫЕ: новые и более свежие файлы")
			c, k := mergeData(pdata, idata)
			ok("скопировано %d, оставлено своих (новее или те же) %d", c, k)
			ok("своя LLM: %s", mergeModel(pdata, idata))
		case "replace":
			psArgs = append(psArgs, "-ImportData")
			ok("данные будут заменены целиком (с резервной копией)")
		default:
			ok("данные установки не тронуты")
		}
	}

	// ---------- 4. install.ps1: код, Python, автозапуск, запуск ----------
	say("УСТАНОВКА КОДА И НАСТРОЙКА (install.ps1)")
	cmd := exec.Command("powershell", psArgs...)
	cmd.Stdin, cmd.Stdout, cmd.Stderr = os.Stdin, os.Stdout, os.Stderr
	if err := cmd.Run(); err != nil {
		warn("install.ps1 завершился с ошибкой: %v", err)
	}

	// ---------- 5. обновления рядом ----------
	ups, _ := filepath.Glob(filepath.Join(here, "neuro12-cloud-*-update.zip"))
	type up struct{ path, ver string }
	list := []up{}
	cur := readVer(dir)
	for _, u := range ups {
		v := strings.TrimSuffix(strings.TrimPrefix(filepath.Base(u), "neuro12-cloud-v"), "-update.zip")
		v = strings.ReplaceAll(v, "_", ".")
		if newer(v, cur) {
			list = append(list, up{u, v})
		}
	}
	sort.Slice(list, func(i, j int) bool { return newer(list[j].ver, list[i].ver) })
	if len(list) > 0 {
		say("ОБНОВЛЕНИЯ РЯДОМ С install.exe")
		stopCloud()
		for _, u := range list {
			n, err := unzip(u.path, dir, "neuro12-cloud/", func(rel string) bool {
				return strings.HasPrefix(strings.ToLower(rel), "data/")
			})
			if err != nil {
				warn("%s: %v", filepath.Base(u.path), err)
				continue
			}
			ok("v%s: обновлено файлов %d", u.ver, n)
		}
		bg := filepath.Join(dir, "cloud_bg.vbs")
		if exists(bg) {
			exec.Command("wscript", bg).Start()
			ok("облако запущено заново")
		}
	}
	say("ГОТОВО: облако v%s в %s", readVer(dir), dir)
	pause()
}
