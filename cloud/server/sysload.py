# -*- coding: utf-8 -*-
# ===========================================================================
# sysload.py — НАГРУЗКА ПЛАТФОРМЫ НА КОМПЬЮТЕР (v3.31, только stdlib)
# ---------------------------------------------------------------------------
# Процесс облака: доля ЦП (по process_time между замерами), память RSS,
# потоки, время работы; компьютер: ядра, память всего/свободно (Linux —
# /proc/meminfo, Windows — GlobalMemoryStatusEx через ctypes), средняя
# нагрузка (Linux); данные облака: размер каталога data (считается раз в
# 5 минут, в фоне — большая база не тормозит статус).
# ===========================================================================
import os
import sys
import threading
import time

import store

_PREV = {"wall": time.time(), "cpu": time.process_time(), "pct": 0.0}
_DATA = {"bytes": 0, "files": 0, "ts": 0.0, "busy": False}
_START = time.time()


def _lanes():
    """v3.186.2: фоновые очереди учёбы на данных плат (bgwork)."""
    try:
        import bgwork
        return bgwork.stats()
    except Exception:
        return {}


def _rss_mb():
    try:
        if sys.platform.startswith("win"):
            import ctypes
            from ctypes import wintypes

            class PMC(ctypes.Structure):
                _fields_ = [("cb", wintypes.DWORD),
                            ("PageFaultCount", wintypes.DWORD),
                            ("PeakWorkingSetSize", ctypes.c_size_t),
                            ("WorkingSetSize", ctypes.c_size_t),
                            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                            ("QuotaPagedPoolUsage", ctypes.c_size_t),
                            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                            ("PagefileUsage", ctypes.c_size_t),
                            ("PeakPagefileUsage", ctypes.c_size_t)]
            pmc = PMC()
            pmc.cb = ctypes.sizeof(PMC)
            k32 = ctypes.windll.kernel32
            k32.GetCurrentProcess.restype = wintypes.HANDLE
            h = k32.GetCurrentProcess()
            fn = getattr(k32, "K32GetProcessMemoryInfo", None)
            if fn is None:
                fn = ctypes.windll.psapi.GetProcessMemoryInfo
            fn.argtypes = [wintypes.HANDLE, ctypes.POINTER(PMC),
                           wintypes.DWORD]
            fn.restype = wintypes.BOOL
            if fn(h, ctypes.byref(pmc), pmc.cb):
                return pmc.WorkingSetSize / 1048576.0
        else:
            with open("/proc/self/status", "r") as f:
                for ln in f:
                    if ln.startswith("VmRSS:"):
                        return int(ln.split()[1]) / 1024.0
    except Exception:
        pass
    try:
        import resource
        v = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return v / 1024.0 if sys.platform != "darwin" else v / 1048576.0
    except Exception:
        return 0.0


def _sys_mem():
    """(всего МБ, свободно МБ) компьютера."""
    try:
        if sys.platform.startswith("win"):
            import ctypes

            class MS(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong),
                            ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong),
                            ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong),
                            ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong),
                            ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
            ms = MS()
            ms.dwLength = ctypes.sizeof(MS)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(ms))
            return ms.ullTotalPhys / 1048576.0, ms.ullAvailPhys / 1048576.0
        tot = avail = 0.0
        with open("/proc/meminfo", "r") as f:
            for ln in f:
                if ln.startswith("MemTotal:"):
                    tot = int(ln.split()[1]) / 1024.0
                elif ln.startswith("MemAvailable:"):
                    avail = int(ln.split()[1]) / 1024.0
        return tot, avail
    except Exception:
        return 0.0, 0.0


def _data_size_bg():
    if _DATA["busy"]:
        return
    _DATA["busy"] = True

    def job():
        n = b = 0
        try:
            for root, dirs, files in os.walk(store.p("")):
                dirs[:] = [d for d in dirs if d != "_exports"]
                for fn in files:
                    try:
                        b += os.path.getsize(os.path.join(root, fn))
                        n += 1
                    except OSError:
                        pass
        finally:
            _DATA.update({"bytes": b, "files": n, "ts": time.time(),
                          "busy": False})
    threading.Thread(target=job, daemon=True, name="sysload-du").start()


def snapshot():
    """Сводка нагрузки (для /api/status и панели)."""
    now = time.time()
    cpu = time.process_time()
    dt = now - _PREV["wall"]
    if dt >= 1.0:
        _PREV["pct"] = max(0.0, (cpu - _PREV["cpu"]) / dt * 100.0)
        _PREV["wall"], _PREV["cpu"] = now, cpu
    cores = os.cpu_count() or 1
    tot, avail = _sys_mem()
    if now - _DATA["ts"] > 300:
        _data_size_bg()
    load = None
    try:
        load = [round(x, 2) for x in os.getloadavg()]
    except Exception:
        pass
    return {"cpu_pct": round(_PREV["pct"], 1),           # % одного ядра
            "cpu_pct_total": round(_PREV["pct"] / cores, 1),
            "cores": cores, "rss_mb": round(_rss_mb(), 1),
            "mem_total_mb": round(tot), "mem_free_mb": round(avail),
            "threads": threading.active_count(),
            "uptime_s": int(now - _START), "loadavg": load,
            "data_mb": round(_DATA["bytes"] / 1048576.0, 1),
            "data_files": _DATA["files"],
            "python": sys.version.split()[0], "os": sys.platform,
            "lanes": _lanes()}
