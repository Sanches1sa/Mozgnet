//go:build windows

package main

import "syscall"

// консоль Windows в UTF-8 (русский текст без «кракозябр»)
func utf8Console() {
	k := syscall.NewLazyDLL("kernel32.dll")
	k.NewProc("SetConsoleOutputCP").Call(65001)
	k.NewProc("SetConsoleCP").Call(65001)
}
