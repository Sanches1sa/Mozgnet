//go:build windows

package main

import (
	"unsafe"

	"golang.org/x/sys/windows"
)

// adapterDescs — описания сетевых адаптеров Windows по их именам
// («Подключение 2» -> «AmneziaWG Tunnel», «TAP-Windows Adapter V9»):
// туннель AmneziaWG/WireGuard называется как туннель, а описание выдаёт его.
func adapterDescs() map[string]string {
	out := map[string]string{}
	size := uint32(15000)
	for try := 0; try < 3; try++ {
		buf := make([]byte, size)
		aa := (*windows.IpAdapterAddresses)(unsafe.Pointer(&buf[0]))
		err := windows.GetAdaptersAddresses(windows.AF_UNSPEC, windows.GAA_FLAG_INCLUDE_PREFIX, 0, aa, &size)
		if err == windows.ERROR_BUFFER_OVERFLOW {
			continue
		}
		if err != nil {
			return out
		}
		for a := aa; a != nil; a = a.Next {
			out[windows.UTF16PtrToString(a.FriendlyName)] = windows.UTF16PtrToString(a.Description)
		}
		return out
	}
	return out
}
