# -*- coding: utf-8 -*-
"""desktop.py — v3.150.0: управление рабочим столом компьютера, где живёт облако.

Работает, только когда включено «Терминал и рабочий стол» (terminal_on), и
только для владельца (сессия панели или чат панели — не боты и не платы).

Windows (основной случай): снимок экрана — Pillow ImageGrab (иначе
PowerShell), мышь и клавиатура — SendInput (user32), окна — EnumWindows,
открыть программу/файл/сайт — ShellExecute (os.startfile).
Linux: scrot/import/spectacle, xdotool, wmctrl, xdg-open — если установлены.

Координаты для мыши приходят ДОЛЯМИ экрана (fx, fy от 0 до 1) — так клик по
уменьшенному снимку в панели попадает в ту же точку настоящего экрана; из
чата можно задать и пиксели (x, y).

Облако должно работать в сеансе пользователя (как его запускает установка —
cloud_bg.vbs при входе в Windows). Служба Windows рабочего стола не видит.
"""
import io
import os
import subprocess
import sys
import time

import store

WIN = sys.platform.startswith("win")
_SHOTS = "desktop"           # data/desktop/ — снимки (отдаются только владельцу)


def enabled():
    try:
        return bool(store.settings().get("terminal_on"))
    except Exception:
        return False


def _log(what):
    try:
        store.log("РАБОЧИЙ СТОЛ", str(what)[:300])
    except Exception:
        pass


# ============================== Windows: user32 ==============================
if WIN:
    import ctypes
    import ctypes.wintypes as W

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    try:   # координаты в настоящих пикселях (иначе при масштабе 125 % промах)
        ctypes.WinDLL("shcore").SetProcessDpiAwareness(2)
    except Exception:
        try:
            user32.SetProcessDPIAware()
        except Exception:
            pass

    ULONG_PTR = ctypes.c_size_t

    class MOUSEINPUT(ctypes.Structure):
        _fields_ = [("dx", W.LONG), ("dy", W.LONG), ("mouseData", W.DWORD),
                    ("dwFlags", W.DWORD), ("time", W.DWORD),
                    ("dwExtraInfo", ULONG_PTR)]

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [("wVk", W.WORD), ("wScan", W.WORD), ("dwFlags", W.DWORD),
                    ("time", W.DWORD), ("dwExtraInfo", ULONG_PTR)]

    class HARDWAREINPUT(ctypes.Structure):
        _fields_ = [("uMsg", W.DWORD), ("wParamL", W.WORD), ("wParamH", W.WORD)]

    class _U(ctypes.Union):
        _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]

    class INPUT(ctypes.Structure):
        _anonymous_ = ("u",)
        _fields_ = [("type", W.DWORD), ("u", _U)]

    user32.SendInput.argtypes = (W.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
    WNDENUMPROC = ctypes.WINFUNCTYPE(W.BOOL, W.HWND, W.LPARAM)

    def _send(*inputs):
        arr = (INPUT * len(inputs))(*inputs)
        return user32.SendInput(len(inputs), arr, ctypes.sizeof(INPUT))

    def _mouse(flags, data=0):
        i = INPUT(type=0)
        i.mi = MOUSEINPUT(0, 0, data & 0xFFFFFFFF, flags, 0, 0)
        return i

    def _key(vk=0, scan=0, flags=0):
        i = INPUT(type=1)
        i.ki = KEYBDINPUT(vk, scan, flags, 0, 0)
        return i


def screen():
    """Виртуальный экран (все мониторы): x, y — левый верхний угол."""
    if WIN:
        g = user32.GetSystemMetrics
        return {"x": g(76), "y": g(77), "w": g(78) or g(0), "h": g(79) or g(1)}
    try:
        out = subprocess.run(["xdotool", "getdisplaygeometry"], capture_output=True,
                             text=True, timeout=5).stdout.split()
        return {"x": 0, "y": 0, "w": int(out[0]), "h": int(out[1])}
    except Exception:
        return {"x": 0, "y": 0, "w": 1920, "h": 1080}


def _xy(fx=None, fy=None, x=None, y=None):
    s = screen()
    if fx is not None and fy is not None:
        fx = min(1.0, max(0.0, float(fx)))
        fy = min(1.0, max(0.0, float(fy)))
        return (s["x"] + int(round(fx * (s["w"] - 1))),
                s["y"] + int(round(fy * (s["h"] - 1))))
    return int(x), int(y)


# ================================== снимок ==================================
def grab(max_w=1600, fmt="jpeg"):
    """Снимок экрана: (bytes, content_type) или (None, ошибка)."""
    img = None
    try:
        from PIL import ImageGrab
        img = ImageGrab.grab(all_screens=True) if WIN else ImageGrab.grab()
    except Exception as e:
        err = "Pillow: %s" % e
    if img is None and WIN:
        tmp = store.p(_SHOTS, "_ps.png")
        os.makedirs(os.path.dirname(tmp), exist_ok=True)
        ps = ("Add-Type -AssemblyName System.Windows.Forms,System.Drawing;"
              "$b=[System.Windows.Forms.SystemInformation]::VirtualScreen;"
              "$i=New-Object System.Drawing.Bitmap $b.Width,$b.Height;"
              "$g=[System.Drawing.Graphics]::FromImage($i);"
              "$g.CopyFromScreen($b.Left,$b.Top,0,0,$i.Size);"
              "$i.Save('%s',[System.Drawing.Imaging.ImageFormat]::Png)" % tmp.replace("'", "''"))
        try:
            subprocess.run(["powershell", "-NoProfile", "-Command", ps], timeout=20,
                           capture_output=True, creationflags=0x08000000)
            with open(tmp, "rb") as f:
                data = f.read()
            os.remove(tmp)
            try:
                from PIL import Image
                img = Image.open(io.BytesIO(data))
                img.load()
            except Exception:
                return data, "image/png"
        except Exception as e:
            err = "PowerShell: %s" % e
    if img is None and not WIN:
        tmp = store.p(_SHOTS, "_x.png")
        os.makedirs(os.path.dirname(tmp), exist_ok=True)
        for tool in (["scrot", "-o", tmp], ["import", "-window", "root", tmp],
                     ["spectacle", "-b", "-n", "-o", tmp]):
            try:
                subprocess.run(tool, timeout=8, capture_output=True)
                if os.path.isfile(tmp) and os.path.getsize(tmp) > 100:
                    with open(tmp, "rb") as f:
                        return f.read(), "image/png"
            except Exception:
                continue
        return None, "нет инструмента снимка (Pillow, scrot, import, spectacle)"
    if img is None:
        return None, err
    try:
        if max_w and img.width > max_w:
            img = img.resize((max_w, int(img.height * max_w / img.width)))
        buf = io.BytesIO()
        if fmt == "png":
            img.save(buf, "PNG", optimize=True)
            return buf.getvalue(), "image/png"
        img.convert("RGB").save(buf, "JPEG", quality=72)
        return buf.getvalue(), "image/jpeg"
    except Exception as e:
        return None, "снимок не сохранён: %s" % e


def save_shot(max_w=1600):
    """Снимок в data/desktop/ — для чата. {ok, file} или {ok:False, error}."""
    data, ct = grab(max_w)
    if not data:
        return {"ok": False, "error": ct}
    d = store.p(_SHOTS)
    os.makedirs(d, exist_ok=True)
    fn = "shot_%d.%s" % (int(time.time()), "png" if ct == "image/png" else "jpg")
    with open(os.path.join(d, fn), "wb") as f:
        f.write(data)
    # хранить последние 20 снимков
    try:
        old = sorted(x for x in os.listdir(d) if x.startswith("shot_"))
        for x in old[:-20]:
            os.remove(os.path.join(d, x))
    except Exception:
        pass
    return {"ok": True, "file": fn}


def read_shot(fn):
    fn = os.path.basename(str(fn or ""))
    fp = os.path.join(store.p(_SHOTS), fn)
    if not fn.startswith("shot_") or not os.path.isfile(fp):
        return None, None
    with open(fp, "rb") as f:
        return f.read(), "image/png" if fn.endswith(".png") else "image/jpeg"


# ================================== мышь ===================================
def click(fx=None, fy=None, x=None, y=None, button="left", double=False):
    px, py = _xy(fx, fy, x, y)
    if WIN:
        user32.SetCursorPos(px, py)
        dn, up = {"left": (0x2, 0x4), "right": (0x8, 0x10),
                  "middle": (0x20, 0x40)}.get(button, (0x2, 0x4))
        for _ in range(2 if double else 1):
            _send(_mouse(dn), _mouse(up))
            time.sleep(0.05)
    else:
        b = {"left": "1", "middle": "2", "right": "3"}.get(button, "1")
        _xdo("mousemove", str(px), str(py), "click", "--repeat", "2" if double else "1", b)
    _log("клик %s%s в %d,%d" % (button, " двойной" if double else "", px, py))
    return {"ok": True, "x": px, "y": py}


def move(fx=None, fy=None, x=None, y=None):
    px, py = _xy(fx, fy, x, y)
    if WIN:
        user32.SetCursorPos(px, py)
    else:
        _xdo("mousemove", str(px), str(py))
    return {"ok": True, "x": px, "y": py}


def scroll(dy=-3):
    """dy < 0 — вниз, dy > 0 — вверх (в «щелчках» колеса)."""
    dy = max(-50, min(50, int(dy)))
    if WIN:
        _send(_mouse(0x0800, dy * 120))
    else:
        _xdo("click", "--repeat", str(abs(dy)), "4" if dy > 0 else "5")
    return {"ok": True, "dy": dy}


# =============================== клавиатура ================================
_VK = {
    "ctrl": 0x11, "control": 0x11, "ктрл": 0x11, "alt": 0x12, "альт": 0x12,
    "shift": 0x10, "шифт": 0x10, "win": 0x5B, "вин": 0x5B, "windows": 0x5B,
    "enter": 0x0D, "ввод": 0x0D, "энтер": 0x0D, "return": 0x0D,
    "esc": 0x1B, "escape": 0x1B, "эскейп": 0x1B, "tab": 0x09, "таб": 0x09,
    "space": 0x20, "пробел": 0x20, "backspace": 0x08, "бэкспейс": 0x08,
    "delete": 0x2E, "del": 0x2E, "удалить": 0x2E, "insert": 0x2D, "ins": 0x2D,
    "home": 0x24, "end": 0x23, "pageup": 0x21, "pgup": 0x21,
    "pagedown": 0x22, "pgdn": 0x22, "up": 0x26, "вверх": 0x26,
    "down": 0x28, "вниз": 0x28, "left": 0x25, "влево": 0x25,
    "right": 0x27, "вправо": 0x27, "printscreen": 0x2C, "prtsc": 0x2C,
    "capslock": 0x14, "menu": 0x5D,
}
for _i in range(1, 13):
    _VK["f%d" % _i] = 0x6F + _i
_EXT = {0x2E, 0x2D, 0x24, 0x23, 0x21, 0x22, 0x26, 0x28, 0x25, 0x27, 0x5B, 0x5D}
_XNAME = {0x11: "ctrl", 0x12: "alt", 0x10: "shift", 0x5B: "super", 0x0D: "Return",
          0x1B: "Escape", 0x09: "Tab", 0x20: "space", 0x08: "BackSpace",
          0x2E: "Delete", 0x2D: "Insert", 0x24: "Home", 0x23: "End",
          0x21: "Prior", 0x22: "Next", 0x26: "Up", 0x28: "Down",
          0x25: "Left", 0x27: "Right", 0x2C: "Print", 0x14: "Caps_Lock", 0x5D: "Menu"}


def parse_keys(s):
    """«ctrl+c», «alt+tab», «win+r», «ввод» → список VK или None."""
    parts = [p.strip().lower() for p in str(s or "").replace(" + ", "+").split("+")]
    if not parts or not all(parts):
        return None
    out = []
    for p in parts:
        if p in _VK:
            out.append(_VK[p])
        elif len(p) == 1 and ("a" <= p <= "z" or "0" <= p <= "9"):
            out.append(ord(p.upper()))
        else:
            return None
    return out


def keys(combo):
    vks = parse_keys(combo)
    if not vks:
        return {"ok": False, "error": "не понял клавиши «%s» (пример: ctrl+c, alt+tab, win+r, enter)" % combo}
    if WIN:
        ext = lambda v: 0x1 if v in _EXT else 0
        _send(*[_key(v, 0, ext(v)) for v in vks])
        time.sleep(0.03)
        _send(*[_key(v, 0, ext(v) | 0x2) for v in reversed(vks)])
    else:
        names = [_XNAME.get(v) or chr(v).lower() for v in vks]
        _xdo("key", "+".join(names))
    _log("клавиши %s" % combo)
    return {"ok": True, "keys": combo}


def type_text(text):
    text = str(text or "")[:5000]
    if not text:
        return {"ok": False, "error": "пустой текст"}
    if WIN:
        for ch in text:
            if ch == "\n":
                _send(_key(0x0D), _key(0x0D, 0, 0x2))
                continue
            b = ch.encode("utf-16-le")
            for k in range(0, len(b), 2):
                cu = b[k] | (b[k + 1] << 8)
                _send(_key(0, cu, 0x4), _key(0, cu, 0x4 | 0x2))
            time.sleep(0.004)
    else:
        _xdo("type", "--delay", "8", text)
    _log("напечатано %d знаков" % len(text))
    return {"ok": True, "chars": len(text)}


# ================================== окна ===================================
def windows():
    out = []
    if WIN:
        def cb(h, _):
            if user32.IsWindowVisible(h):
                n = user32.GetWindowTextLengthW(h)
                if n:
                    buf = ctypes.create_unicode_buffer(n + 1)
                    user32.GetWindowTextW(h, buf, n + 1)
                    t = buf.value.strip()
                    if t and t not in ("Program Manager",):
                        out.append({"id": int(h), "title": t})
            return True
        user32.EnumWindows(WNDENUMPROC(cb), 0)
    else:
        try:
            r = subprocess.run(["wmctrl", "-l"], capture_output=True, text=True, timeout=5)
            for ln in r.stdout.splitlines():
                p = ln.split(None, 3)
                if len(p) == 4:
                    out.append({"id": int(p[0], 16), "title": p[3]})
        except Exception:
            pass
    return out[:60]


def focus(title=None, wid=None):
    ws = windows()
    w = None
    if wid:
        w = next((x for x in ws if x["id"] == int(wid)), None)
    elif title:
        t = str(title).lower()
        w = next((x for x in ws if t in x["title"].lower()), None)
    if not w:
        return {"ok": False, "error": "окно не найдено"}
    if WIN:
        h = w["id"]
        if user32.IsIconic(h):
            user32.ShowWindow(h, 9)                 # SW_RESTORE
        _send(_key(0x12), _key(0x12, 0, 0x2))       # Alt — снимает запрет переднего плана
        user32.SetForegroundWindow(h)
    else:
        subprocess.run(["wmctrl", "-ia", hex(w["id"])], timeout=5)
    _log("окно «%s»" % w["title"][:80])
    return {"ok": True, "title": w["title"]}


def open_target(target):
    target = str(target or "").strip().strip('"')
    if not target:
        return {"ok": False, "error": "что открыть?"}
    try:
        if WIN:
            os.startfile(target)                   # программа, файл, папка, сайт
        else:
            subprocess.Popen(["xdg-open", target], stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
        _log("открыто «%s»" % target[:120])
        return {"ok": True, "target": target}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


def _xdo(*args):
    try:
        subprocess.run(["xdotool"] + list(args), timeout=10, capture_output=True)
    except Exception as e:
        raise RuntimeError("нужен xdotool: %s" % e)


# ============================ команда из панели =============================
def act(b):
    """POST /api/desktop — одно действие. b — словарь из панели."""
    if not enabled():
        return {"ok": False, "error": "«Терминал и рабочий стол» выключен в Настройках → Общие"}
    a = str(b.get("act") or "")
    g = lambda k: b.get(k)
    try:
        if a == "click":
            return click(g("fx"), g("fy"), g("x"), g("y"), g("button") or "left", bool(g("double")))
        if a == "move":
            return move(g("fx"), g("fy"), g("x"), g("y"))
        if a == "scroll":
            return scroll(int(g("dy") or -3))
        if a == "type":
            return type_text(g("text"))
        if a == "key":
            return keys(g("keys"))
        if a == "open":
            return open_target(g("target"))
        if a == "focus":
            return focus(g("title"), g("id"))
        if a == "windows":
            return {"ok": True, "windows": windows()}
        return {"ok": False, "error": "неизвестное действие"}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


# ============================ команды из чата ===============================
def chat_command(text):
    """Команда рабочего стола из чата панели. None — это не команда."""
    import re
    t = str(text or "").strip()
    low = t.lower()
    m = re.match(r"^(двойной клик|правый клик|клик|кликни|мышь|курсор)\s+(\d{1,5})[\s,;x×]+(\d{1,5})\s*$", low)
    if m:
        kind = m.group(1)
        x, y = int(m.group(2)), int(m.group(3))
        if kind in ("мышь", "курсор"):
            r = move(x=x, y=y)
            return "Курсор: %d, %d" % (r["x"], r["y"])
        r = click(x=x, y=y, button="right" if kind == "правый клик" else "left",
                  double=kind == "двойной клик")
        return "%s в точке %d, %d." % (kind.capitalize(), r["x"], r["y"])
    m = re.match(r"^(напечатай|напиши на компьютере|введи текст)\s*[:：]?\s+([\s\S]+)$", t, re.I)
    if m:
        r = type_text(m.group(2))
        return "Напечатано знаков: %d." % r.get("chars", 0) if r.get("ok") else r.get("error")
    m = re.match(r"^(нажми|нажать|клавиши)\s+(.+)$", low)
    if m and parse_keys(m.group(2)):
        keys(m.group(2))
        return "Нажато: %s." % m.group(2)
    m = re.match(r"^(открой на компьютере|запусти программу|запусти|открой программу|открой папку|открой файл)\s+(.+)$", t, re.I)
    if m:
        r = open_target(m.group(2))
        return "Открываю: %s" % r["target"] if r.get("ok") else "Не открылось: %s" % r.get("error")
    m = re.match(r"^прокрути\s+(вниз|вверх)(?:\s+(\d{1,2}))?\s*$", low)
    if m:
        n = int(m.group(2) or 5)
        scroll(-n if m.group(1) == "вниз" else n)
        return "Прокрутил %s на %d." % (m.group(1), n)
    if low in ("окна", "список окон", "какие окна открыты"):
        ws = windows()
        return "Открытые окна:\n" + "\n".join("• " + w["title"] for w in ws) if ws else "Окон не нашёл."
    m = re.match(r"^(переключись на|перейди в окно|активируй окно|окно)\s+(.+)$", low)
    if m:
        r = focus(title=m.group(2))
        return "Окно: %s" % r["title"] if r.get("ok") else "Окно «%s» не найдено." % m.group(2)
    if low in ("скриншот", "снимок экрана", "покажи экран", "покажи рабочий стол",
               "сделай скриншот", "сделай снимок экрана"):
        r = save_shot()
        if not r.get("ok"):
            return "Снимок не удался: %s" % r.get("error")
        return ("Снимок рабочего стола:\n\n[ИЛЛЮСТРАЦИЯ src=desk:%s]рабочий стол, %s[/ИЛЛЮСТРАЦИЯ]\n\n"
                "Управлять мышью и клавиатурой удобнее в разделе «🖥 Рабочий стол» (правое меню)."
                % (r["file"], time.strftime("%H:%M:%S")))
    return None


_OFF = ("«Терминал и рабочий стол» выключен. Включите его в Настройки → Общие "
        "и нажмите «Сохранить».")


def panel_command(text):
    """v3.150: команда компьютера из ЧАТА ПАНЕЛИ (владелец, любой режим чата —
    Авто, Облако, Разговор, Код). None — это не команда, отвечает мозг."""
    t = str(text or "").strip()
    low = t.lower()
    if low.startswith(("выполни ", "команда ", "терминал ", "cmd ")):
        if not enabled():
            return _OFF
        import terminal
        cmd = t.split(None, 1)[1].strip()
        r = terminal.run(cmd, timeout=60)
        out = r.get("out") or r.get("error") or "(нет вывода)"
        return "Команда: `%s`\nКод: %s\n```\n%s\n```" % (cmd, r.get("code", "?"), out[:3000])
    if not enabled():
        if low in ("скриншот", "снимок экрана", "покажи экран", "покажи рабочий стол",
                   "сделай скриншот", "сделай снимок экрана"):
            return _OFF
        return None
    return chat_command(t)
