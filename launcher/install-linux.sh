#!/usr/bin/env bash
# ============================================================================
#  install-linux.sh — ЛАУНЧЕР «STAR GATE» v3.159 ДЛЯ LINUX (РЕД ОС, Ubuntu)
#
#  Запуск из распакованной папки (БЕЗ sudo — пароль спросит сам, когда нужно):
#      bash install-linux.sh
#
#  Что делает:
#    · ставит нужные программы (dnf в РЕД ОС, apt в Ubuntu): python3, ssh,
#      NetworkManager, zenity, pkexec, xdg-utils, ethtool, numpy;
#    · если нет браузера на Chromium/Firefox — ставит Chromium (окно лаунчера);
#    · кладёт лаунчер в ~/.local/share/neuro12-cloud/launcher/neuro12-start
#      (как в Windows: %LOCALAPPDATA%\neuro12-cloud\launcher);
#    · облако: если рядом лежит пакет neuro12-cloud (папка или
#      neuro12-cloud-*.zip) — ставит/обновляет его в ~/.local/share/neuro12-cloud
#      (память data/ не трогает: добавляются только новые файлы), задаёт
#      пароль панели и мозг-токен; архивы *-update.zip рядом ставит поверх кода;
#    · открывает порт облака 8077 в файрволе (firewalld в РЕД ОС, ufw в Ubuntu),
#      чтобы плата достучалась до облака;
#    · ярлыки «Star Gate запуск» в меню и на рабочем столе, команда neuro12-start;
#    · по желанию — автозапуск облака при входе (сервис systemd пользователя).
#
#  Опции: --yes (без вопросов) · --no-deps · --no-cloud · --cloud ПУТЬ
#         --autostart | --no-autostart · --no-firewall · --no-run · -h
# ============================================================================
set -u

if [ -t 1 ]; then G='\033[0;32m'; Y='\033[0;33m'; R='\033[0;31m'; B='\033[0;94m'; N='\033[0m'
else G=''; Y=''; R=''; B=''; N=''; fi
say()  { printf '\n%b==> %s%b\n' "$B" "$*" "$N"; }
ok()   { printf '  %b[OK]%b %s\n' "$G" "$N" "$*"; }
warn() { printf '  %b[!!]%b %s\n' "$Y" "$N" "$*"; }
die()  { printf '  %b[XX]%b %s\n' "$R" "$N" "$*" >&2; exit 1; }

PKG="$(cd "$(dirname "$0")" && pwd)"
YES=0; DEPS=1; CLOUD_ON=1; CLOUD_SRC=""; AUTOSTART=""; FW=1; RUN=1
while [ $# -gt 0 ]; do
  case "$1" in
    --yes|-y) YES=1 ;;
    --no-deps) DEPS=0 ;;
    --no-cloud) CLOUD_ON=0 ;;
    --cloud) CLOUD_SRC="$2"; shift ;;
    --autostart) AUTOSTART=1 ;;
    --no-autostart) AUTOSTART=0 ;;
    --no-firewall) FW=0 ;;
    --no-run) RUN=0 ;;
    -h|--help) sed -n '2,27p' "$0"; exit 0 ;;
    *) die "неизвестная опция: $1 (см. --help)" ;;
  esac
  shift
done

ask() { # ask "вопрос" y|n → 0 = да
  local def="$2" a
  if [ "$YES" -eq 1 ] || [ ! -t 0 ]; then [ "$def" = y ]; return; fi
  if [ "$def" = y ]; then printf '  %s [Д/н]: ' "$1"; else printf '  %s [д/Н]: ' "$1"; fi
  read -r a
  [ -z "$a" ] && { [ "$def" = y ]; return; }
  case "$a" in [yYдД]*) return 0 ;; *) return 1 ;; esac
}

if [ "$(id -u)" -eq 0 ]; then
  if [ -n "${SUDO_USER:-}" ] && [ "$SUDO_USER" != root ]; then
    die "запусти без sudo: bash install-linux.sh (лаунчер ставится для пользователя $SUDO_USER, пароль спросит сам)"
  fi
  warn "установка от root: лаунчер будет у root. Лучше ставить обычному пользователю."
  SUDO=""
else
  SUDO="sudo"
  command -v sudo >/dev/null 2>&1 || SUDO=""
fi

# ------------------------------------------------------------ система
say "СИСТЕМА"
OS_ID=""; OS_LIKE=""; OS_NAME="Linux"
if [ -r /etc/os-release ]; then
  . /etc/os-release
  OS_ID="${ID:-}"; OS_LIKE="${ID_LIKE:-}"; OS_NAME="${PRETTY_NAME:-Linux}"
fi
ok "$OS_NAME"
PM=""
for p in dnf yum apt-get zypper pacman; do command -v $p >/dev/null 2>&1 && { PM=$p; break; }; done
[ -n "$PM" ] && ok "пакеты: $PM" || warn "менеджер пакетов не найден — программы ставь сам"

case "$(uname -m)" in
  x86_64|amd64) ARCH=amd64 ;;
  aarch64|arm64) ARCH=arm64 ;;
  *) die "процессор $(uname -m) не поддерживается (есть сборки amd64 и arm64)" ;;
esac
BIN="$PKG/bin/neuro12-start-linux-$ARCH"
[ -f "$BIN" ] || die "нет файла bin/neuro12-start-linux-$ARCH — запускай из папки распакованного архива"
ok "сборка лаунчера: linux-$ARCH"

# ------------------------------------------------------------ программы
pm_install() { # ставит пакеты по одному: отсутствующее имя не ломает остальное
  [ -n "$PM" ] && [ -n "$SUDO$( [ "$(id -u)" -eq 0 ] && echo root)" ] || return 1
  local okc=0 p
  case "$PM" in
    apt-get) for p in "$@"; do $SUDO env DEBIAN_FRONTEND=noninteractive apt-get install -y -q "$p" >/dev/null 2>&1 && okc=1; done ;;
    dnf|yum) for p in "$@"; do $SUDO $PM install -y -q "$p" >/dev/null 2>&1 && okc=1; done ;;
    zypper)  for p in "$@"; do $SUDO zypper -n install "$p" >/dev/null 2>&1 && okc=1; done ;;
    pacman)  for p in "$@"; do $SUDO pacman -S --noconfirm --needed "$p" >/dev/null 2>&1 && okc=1; done ;;
  esac
  [ $okc -eq 1 ]
}
need() { command -v "$1" >/dev/null 2>&1; }

if [ "$DEPS" -eq 1 ] && [ -n "$PM" ]; then
  say "ПРОГРАММЫ"
  [ -n "$SUDO" ] && echo "  (может спросить пароль пользователя для sudo)"
  [ "$PM" = apt-get ] && $SUDO apt-get update -q >/dev/null 2>&1
  case "$PM" in
    apt-get) LIST="python3 python3-numpy openssh-client network-manager zenity pkexec policykit-1 xdg-utils iproute2 ethtool unzip curl" ;;
    dnf|yum) LIST="python3 python3-numpy openssh-clients NetworkManager zenity polkit xdg-utils iproute ethtool unzip curl" ;;
    zypper)  LIST="python3 python3-numpy openssh NetworkManager zenity polkit xdg-utils iproute2 ethtool unzip curl" ;;
    pacman)  LIST="python python-numpy openssh networkmanager zenity polkit xdg-utils iproute2 ethtool unzip curl" ;;
  esac
  MISS=""
  for p in $LIST; do
    case "$p" in
      python3|python) need python3 || MISS="$MISS $p" ;;
      openssh-client|openssh-clients|openssh) need ssh || MISS="$MISS $p" ;;
      network-manager|NetworkManager|networkmanager) need nmcli || MISS="$MISS $p" ;;
      zenity) need zenity || need kdialog || MISS="$MISS $p" ;;
      pkexec|policykit-1|polkit) need pkexec || MISS="$MISS $p" ;;
      xdg-utils) need xdg-open || MISS="$MISS $p" ;;
      iproute|iproute2) need ip || MISS="$MISS $p" ;;
      python3-numpy|python-numpy) python3 -c 'import numpy' >/dev/null 2>&1 || MISS="$MISS $p" ;;
      *) need "$p" || MISS="$MISS $p" ;;
    esac
  done
  if [ -n "$MISS" ]; then
    echo "  ставлю:$MISS"
    pm_install $MISS || true
    STILL=""
    for p in $MISS; do
      case "$p" in
        openssh*) need ssh || STILL="$STILL $p" ;;
        network-manager|NetworkManager|networkmanager) need nmcli || STILL="$STILL $p" ;;
        zenity) need zenity || STILL="$STILL $p" ;;
        pkexec|policykit-1|polkit) need pkexec || STILL="$STILL $p" ;;
        iproute*) need ip || STILL="$STILL $p" ;;
        *numpy) python3 -c 'import numpy' >/dev/null 2>&1 || STILL="$STILL $p" ;;
        python*) need python3 || STILL="$STILL $p" ;;
        xdg-utils) need xdg-open || STILL="$STILL $p" ;;
        *) need "$p" || STILL="$STILL $p" ;;
      esac
    done
    [ -n "$STILL" ] && warn "не поставились:$STILL — вручную: sudo $PM install$STILL (нет интернета/репозитория?)"
  fi
  need python3 && ok "python3 $(python3 -c 'import sys;print("%d.%d"%sys.version_info[:2])')" || warn "нет python3 — облако не запустится"
  need ssh && ok "ssh" || warn "нет ssh — лаунчер не сможет работать с платой"
  need nmcli && ok "NetworkManager (nmcli)" || warn "нет nmcli — «Зеркало MAC на компьютер» пойдёт через ip link"
  { need zenity || need kdialog; } && ok "диалог выбора папки" || warn "нет zenity — папки вписывай вручную"
  need pkexec && ok "pkexec (окно пароля администратора)" || warn "нет pkexec — зеркало MAC попросит sudo"
  python3 -c 'import numpy' >/dev/null 2>&1 && ok "numpy (своя LLM быстрее)" || warn "нет numpy — своя LLM работает медленнее"

  # окно лаунчера — браузер на Chromium (режим приложения) или Firefox
  BR=""
  for b in chromium chromium-browser yandex-browser-stable yandex-browser chromium-gost-stable chromium-gost \
           google-chrome-stable google-chrome microsoft-edge-stable brave-browser vivaldi-stable firefox firefox-esr; do
    need "$b" && { BR="$b"; break; }
  done
  if [ -z "$BR" ]; then
    echo "  браузера для окна нет — ставлю Chromium"
    case "$PM" in
      apt-get) pm_install chromium chromium-browser ;;
      *) pm_install chromium ;;
    esac
    for b in chromium chromium-browser; do need "$b" && BR="$b" && break; done
  fi
  if [ -n "$BR" ]; then ok "окно лаунчера: $BR"
  else warn "браузер не найден — поставь Chromium или Яндекс Браузер (окно откроется в браузере по умолчанию)"; fi
  case "$BR" in firefox*) warn "Firefox откроет лаунчер обычным окном. Красивее — Chromium или Яндекс Браузер (окно-приложение).";; esac
fi

# ------------------------------------------------------------ лаунчер
DATA="${XDG_DATA_HOME:-$HOME/.local/share}"
CLOUD="$DATA/neuro12-cloud"
LDIR="$CLOUD/launcher"
EXE="$LDIR/neuro12-start"
say "ЛАУНЧЕР → $LDIR"
mkdir -p "$LDIR" "$HOME/.local/bin" "$DATA/applications" "$DATA/icons/hicolor/256x256/apps" || die "не создать $LDIR"
if [ -f "$EXE" ] && pgrep -f "^$EXE" >/dev/null 2>&1; then
  # запущенный файл не трогаем: меняем через .new (лаунчер сам подменит при старте)
  cp -f "$BIN" "$EXE.new" && chmod 755 "$EXE.new"
  pkill -f "^$EXE" 2>/dev/null; sleep 1
  mv -f "$EXE.new" "$EXE"
else
  cp -f "$BIN" "$EXE.tmp" && chmod 755 "$EXE.tmp" && mv -f "$EXE.tmp" "$EXE" || die "не скопировал лаунчер"
fi
cp -f "$PKG/brain.png" "$LDIR/brain.png" 2>/dev/null
cp -f "$PKG/brain.png" "$DATA/icons/hicolor/256x256/apps/neuro12.png" 2>/dev/null
ln -sf "$EXE" "$HOME/.local/bin/neuro12-start"
for f in README-linux.md THIRD_PARTY_LICENSES.txt uninstall-linux.sh; do [ -f "$PKG/$f" ] && cp -f "$PKG/$f" "$LDIR/"; done
chmod +x "$LDIR/uninstall-linux.sh" 2>/dev/null
ok "лаунчер $("$EXE" --version 2>/dev/null || echo v3.159) установлен, команда: neuro12-start"

# ------------------------------------------------------------ облако
ver() { [ -f "$1/VERSION" ] && tr -d ' \r\n' < "$1/VERSION" || echo ""; }
newer() { # $1 > $2 ?
  [ "$1" = "$2" ] && return 1
  [ "$(printf '%s\n%s\n' "$1" "$2" | sort -V | tail -1)" = "$1" ]
}
findroot() { # папка с server/app.py внутри $1 (до 3 уровней)
  local d
  for d in "$1" "$1"/* "$1"/*/* "$1"/*/*/*; do
    [ -f "$d/server/app.py" ] && { echo "$d"; return 0; }
  done
  return 1
}
stop_cloud() {
  if [ -f "$HOME/.config/systemd/user/neuro12-cloud.service" ]; then systemctl --user stop neuro12-cloud >/dev/null 2>&1; fi
  pkill -f "$CLOUD/server/app.py" >/dev/null 2>&1 && sleep 2
}
copy_code() { # код без data/
  local src="$1" x
  for x in server web docs tests VERSION README.md AGENT-GUIDE.md install.sh start.sh stop.sh uninstall.sh; do
    [ -e "$src/$x" ] || continue
    rm -rf "$CLOUD/$x.n12new"; cp -r "$src/$x" "$CLOUD/$x.n12new" && rm -rf "$CLOUD/$x" && mv "$CLOUD/$x.n12new" "$CLOUD/$x"
  done
  find "$CLOUD/server" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null
}

TMPU="$(mktemp -d)"; trap 'rm -rf "$TMPU"' EXIT
if [ "$CLOUD_ON" -eq 1 ]; then
  say "ОБЛАКО → $CLOUD"
  SRC=""
  if [ -n "$CLOUD_SRC" ]; then
    if [ -d "$CLOUD_SRC" ]; then SRC="$(findroot "$CLOUD_SRC")"
    elif [ -f "$CLOUD_SRC" ]; then unzip -q -o "$CLOUD_SRC" -d "$TMPU/full" && SRC="$(findroot "$TMPU/full")"; fi
    [ -n "$SRC" ] || die "в $CLOUD_SRC нет пакета облака (server/app.py)"
  else
    for d in "$PKG"/neuro12-cloud* "$PKG"/../neuro12-cloud*; do
      [ -d "$d" ] && [ "$(cd "$d" && pwd)" != "$CLOUD" ] && SRC="$(findroot "$d")" && [ -n "$SRC" ] && break
    done
    if [ -z "$SRC" ]; then
      for z in $(ls -1 "$PKG"/neuro12-cloud*.zip 2>/dev/null | grep -v -- '-update' | sort -V | tail -1); do
        unzip -q -o "$z" -d "$TMPU/full" && SRC="$(findroot "$TMPU/full")"
      done
    fi
  fi
  HAVE="$(ver "$CLOUD")"
  if [ -n "$SRC" ]; then
    NEW="$(ver "$SRC")"
    if [ -z "$HAVE" ] || [ ! -f "$CLOUD/server/app.py" ]; then
      echo "  ставлю облако v$NEW из $SRC"
      stop_cloud; mkdir -p "$CLOUD/data"; copy_code "$SRC"
      [ -d "$SRC/data" ] && cp -rn "$SRC/data/." "$CLOUD/data/" 2>/dev/null
      ok "облако v$NEW установлено"
    elif newer "$NEW" "$HAVE" || ask "облако v$HAVE уже стоит. Переставить код из пакета v$NEW?" n; then
      stop_cloud; copy_code "$SRC"
      [ -d "$SRC/data" ] && cp -rn "$SRC/data/." "$CLOUD/data/" 2>/dev/null
      ok "облако v$HAVE → v$NEW (память data/ сохранена, добавлены только новые файлы)"
    else
      ok "облако v$HAVE уже стоит — оставляю"
    fi
  elif [ -n "$HAVE" ]; then
    ok "облако v$HAVE уже стоит"
  fi
  # обновления: neuro12-cloud-*-update.zip рядом — по порядку версий
  for z in $(ls -1 "$PKG"/neuro12-cloud*-update.zip 2>/dev/null | sort -V); do
    [ -f "$CLOUD/server/app.py" ] || break
    rm -rf "$TMPU/u"; unzip -q -o "$z" -d "$TMPU/u" || { warn "битый архив $z"; continue; }
    U="$(findroot "$TMPU/u")" || { warn "в $z нет server/app.py"; continue; }
    UV="$(ver "$U")"; HAVE="$(ver "$CLOUD")"
    if newer "$UV" "$HAVE"; then stop_cloud; copy_code "$U"; ok "обновление v$HAVE → v$UV ($(basename "$z"))"; fi
  done

  if [ -f "$CLOUD/server/app.py" ]; then
    if need python3; then
      echo "  пароль панели и мозг-токен (install.sh облака, режим без systemd)…"
      ( cd "$CLOUD" && bash install.sh --no-systemd --no-start --yes 2>&1 | sed -n '/ПАРОЛЬ\|Пароль\|пароль\|Токен\|токен/p' | sed 's/^/  /' )
    fi
    [ -f "$CLOUD/data/token.txt" ] && ok "мозг-токен: data/token.txt"
    # автозапуск облака при входе
    if [ -z "$AUTOSTART" ]; then
      if ask "Запускать облако при входе в систему (без открытия лаунчера)?" y; then AUTOSTART=1; else AUTOSTART=0; fi
    fi
    UNIT="$HOME/.config/systemd/user/neuro12-cloud.service"
    if [ "$AUTOSTART" = 1 ]; then
      PYB="$(command -v python3)"
      PORT="$(cat "$CLOUD/data/port.txt" 2>/dev/null | tr -dc 0-9)"; PORT="${PORT:-8077}"
      if need systemctl && systemctl --user show-environment >/dev/null 2>&1; then
        mkdir -p "$(dirname "$UNIT")"
        cat > "$UNIT" <<EOF
[Unit]
Description=neuro12-cloud — облако «Star Gate» (лаунчер)
After=network-online.target

[Service]
WorkingDirectory=$CLOUD
Environment=NEURO_DATA=$CLOUD/data
Environment=NEURO_PORT=$PORT
Environment=PYTHONUNBUFFERED=1
ExecStart=$PYB $CLOUD/server/app.py
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
EOF
        rm -f "$HOME/.config/autostart/neuro12-cloud.desktop"
        systemctl --user daemon-reload && systemctl --user enable neuro12-cloud >/dev/null 2>&1 && \
          ok "автозапуск: сервис пользователя neuro12-cloud (systemctl --user status neuro12-cloud)"
      else
        mkdir -p "$HOME/.config/autostart"
        cat > "$HOME/.config/autostart/neuro12-cloud.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Star Gate: облако
Exec=sh -c 'cd "$CLOUD" && NEURO_DATA="$CLOUD/data" NEURO_PORT=$PORT exec $PYB server/app.py >>"$CLOUD/data/launcher_cloud.log" 2>&1'
Icon=neuro12
NoDisplay=true
X-GNOME-Autostart-enabled=true
EOF
        ok "автозапуск: ~/.config/autostart/neuro12-cloud.desktop"
      fi
    else
      [ -f "$UNIT" ] && { systemctl --user disable --now neuro12-cloud >/dev/null 2>&1; rm -f "$UNIT"; }
      rm -f "$HOME/.config/autostart/neuro12-cloud.desktop"
      ok "облако запускает лаунчер (автозапуск выключен)"
    fi
  else
    warn "облака нет. Положи рядом neuro12-cloud-*.zip (полный пакет) и запусти установку ещё раз,"
    warn "или укажи папку облака в лаунчере: ⚙ → «Папка облака»."
  fi
fi

# ------------------------------------------------------------ файрвол
if [ "$FW" -eq 1 ]; then
  say "ФАЙРВОЛ (плата → облако, порт 8077)"
  PORT="$(cat "$CLOUD/data/port.txt" 2>/dev/null | tr -dc 0-9)"; PORT="${PORT:-8077}"
  if need firewall-cmd && $SUDO firewall-cmd --state >/dev/null 2>&1; then
    if $SUDO firewall-cmd --query-port="$PORT/tcp" >/dev/null 2>&1; then ok "firewalld: порт $PORT уже открыт"
    else
      $SUDO firewall-cmd --permanent --add-port="$PORT/tcp" >/dev/null 2>&1 && $SUDO firewall-cmd --reload >/dev/null 2>&1 \
        && ok "firewalld: порт $PORT открыт" || warn "firewalld: не открыл порт $PORT — sudo firewall-cmd --permanent --add-port=$PORT/tcp"
    fi
  elif need ufw && $SUDO ufw status 2>/dev/null | grep -qi "status: active\|состояние: активен"; then
    $SUDO ufw allow "$PORT/tcp" >/dev/null 2>&1 && ok "ufw: порт $PORT открыт" || warn "ufw: sudo ufw allow $PORT/tcp"
  else
    ok "файрвол не активен — ничего делать не нужно"
  fi
fi

# ------------------------------------------------------------ ярлыки
say "ЯРЛЫКИ"
DESK_FILE="$DATA/applications/neuro12-start.desktop"
cat > "$DESK_FILE" <<EOF
[Desktop Entry]
Type=Application
Version=1.0
Name=Star Gate запуск
GenericName=Лаунчер «Star Gate»
Comment=Облако и плата «Star Gate»: запуск, сканер сети, вращение MAC
Exec="$EXE" %U
Icon=neuro12
Terminal=false
Categories=Network;Utility;Development;
Keywords=neuro12;мозг;arduino;uno q;
StartupNotify=true
StartupWMClass=neuro12
Actions=cloud;

[Desktop Action cloud]
Name=Открыть облако
Exec="$EXE" --cloud
EOF
chmod 755 "$DESK_FILE"
need update-desktop-database && update-desktop-database "$DATA/applications" >/dev/null 2>&1
need gtk-update-icon-cache && gtk-update-icon-cache -q -t "$DATA/icons/hicolor" >/dev/null 2>&1
ok "меню приложений: «Star Gate запуск»"
DESK="$(xdg-user-dir DESKTOP 2>/dev/null)"
[ -n "$DESK" ] && [ "$DESK" != "$HOME" ] || { [ -d "$HOME/Рабочий стол" ] && DESK="$HOME/Рабочий стол" || DESK="$HOME/Desktop"; }
if [ -d "$DESK" ]; then
  cp -f "$DESK_FILE" "$DESK/neuro12-start.desktop" && chmod 755 "$DESK/neuro12-start.desktop"
  need gio && gio set "$DESK/neuro12-start.desktop" metadata::trusted true >/dev/null 2>&1
  ok "рабочий стол: $DESK/neuro12-start.desktop"
fi

# ------------------------------------------------------------ готово
printf '\n%b  Готово. Лаунчер «Star Gate» установлен.%b\n' "$G" "$N"
printf '  Запуск: ярлык «Star Gate запуск» или команда  neuro12-start\n'
printf '  Без окна (сервер, ssh):  neuro12-start --no-window\n'
printf '  Удаление: bash %s/uninstall-linux.sh\n' "$LDIR"
case ":$PATH:" in *":$HOME/.local/bin:"*) ;; *) printf '  (команда neuro12-start появится в PATH после нового входа в систему)\n' ;; esac
if [ "$RUN" -eq 1 ] && { [ -n "${DISPLAY:-}" ] || [ -n "${WAYLAND_DISPLAY:-}" ]; }; then
  if ask "Открыть лаунчер сейчас?" y; then
    nohup "$EXE" >/dev/null 2>&1 &
    ok "лаунчер открывается…"
  fi
fi
