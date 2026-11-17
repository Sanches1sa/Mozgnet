//go:build windows

package main

import (
	"runtime"
	"syscall"
	"unsafe"
)

var (
	shell32            = syscall.NewLazyDLL("shell32.dll")
	ole32              = syscall.NewLazyDLL("ole32.dll")
	user32             = syscall.NewLazyDLL("user32.dll")
	pSHBrowseForFolder = shell32.NewProc("SHBrowseForFolderW")
	pSHGetPathFromID   = shell32.NewProc("SHGetPathFromIDListW")
	pCoInit            = ole32.NewProc("CoInitializeEx")
	pCoTaskMemFree     = ole32.NewProc("CoTaskMemFree")
	pMessageBox        = user32.NewProc("MessageBoxW")
	pGetForeground     = user32.NewProc("GetForegroundWindow")
	pSetDpiCtx         = user32.NewProc("SetProcessDpiAwarenessContext")
	pShowWindow        = user32.NewProc("ShowWindow")
)

// maximize — v3.155.1: окно на весь экран БЕЗ панели «Пуск» (развёрнутое окно
// Windows само не заходит под панель задач). Раньше окно 1280×860 при
// масштабе 125 % было выше рабочей области, и панель «Пуск» закрывала низ
// облака — строку ввода чата.
func maximize(hwnd unsafe.Pointer) {
	if hwnd != nil {
		pShowWindow.Call(uintptr(hwnd), 3) // SW_MAXIMIZE
	}
}

type browseInfo struct {
	Owner        uintptr
	Root         uintptr
	DisplayName  *uint16
	Title        *uint16
	Flags        uint32
	CallbackFunc uintptr
	LParam       uintptr
	Image        int32
}

// pickFolder — стандартный диалог выбора папки Windows
func pickFolder(title string) string {
	runtime.LockOSThread()
	defer runtime.UnlockOSThread()
	pCoInit.Call(0, 2) // COINIT_APARTMENTTHREADED
	name := make([]uint16, 260)
	t, _ := syscall.UTF16PtrFromString(title)
	owner, _, _ := pGetForeground.Call()
	bi := browseInfo{Owner: owner, DisplayName: &name[0], Title: t, Flags: 0x0001 | 0x0040} // RETURNONLYFSDIRS | NEWDIALOGSTYLE
	pidl, _, _ := pSHBrowseForFolder.Call(uintptr(unsafe.Pointer(&bi)))
	if pidl == 0 {
		return ""
	}
	defer pCoTaskMemFree.Call(pidl)
	buf := make([]uint16, 1024)
	r, _, _ := pSHGetPathFromID.Call(pidl, uintptr(unsafe.Pointer(&buf[0])))
	if r == 0 {
		return ""
	}
	return syscall.UTF16ToString(buf)
}

func msgBox(title, text string) {
	t, _ := syscall.UTF16PtrFromString(title)
	m, _ := syscall.UTF16PtrFromString(text)
	pMessageBox.Call(0, uintptr(unsafe.Pointer(m)), uintptr(unsafe.Pointer(t)), 0x40)
}

// чёткий текст на экранах с масштабом 125–200 %
func dpiAware() {
	if pSetDpiCtx.Find() == nil {
		pSetDpiCtx.Call(^uintptr(3)) // DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4
	}
}

// newConsole — атрибуты процесса: отдельное окно консоли
func newConsole() *syscall.SysProcAttr { return &syscall.SysProcAttr{CreationFlags: 0x00000010} }
