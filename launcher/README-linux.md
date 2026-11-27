# Лаунчер «Star Gate» для Linux v3.159.1 (РЕД ОС, Ubuntu)

Тот же лаунчер, что `neuro12-start.exe` в Windows: большая кнопка «Запустить всё»,
облако и плата в одном окне (вкладки Запуск · Облако · Плата), сканер сети,
предсказание адреса, вход по ключу, интернет платы, вращение и спин MAC,
зеркало MAC на компьютер, лицензии.

## Установка

```
unzip neuro12-launcher-linux-v3_159_0.zip
cd neuro12-launcher-linux-v3.159.0
bash install-linux.sh          # без sudo — пароль спросит сам
```

Установщик:
- ставит программы: в РЕД ОС — через `dnf`, в Ubuntu — через `apt`
  (python3, ssh, NetworkManager, zenity, pkexec, numpy…);
- если нет браузера для окна — ставит Chromium;
- кладёт лаунчер в `~/.local/share/neuro12-cloud/launcher/` (как в Windows —
  `%LOCALAPPDATA%\neuro12-cloud\launcher`) и делает команду `neuro12-start`;
- ставит облако из папки `neuro12-cloud` в архиве (v3.175.1) в
  `~/.local/share/neuro12-cloud`. Если облако уже стоит, память `data/` не трогается.
  Архивы `neuro12-cloud-*-update.zip` рядом ставятся поверх кода;
- печатает **пароль веб-панели** и **мозг-токен** — запиши пароль;
- открывает порт облака 8077 в файрволе: firewalld в РЕД ОС включён по
  умолчанию, без этого плата не достучится до облака;
- ярлык «Star Gate запуск» появляется в меню и на рабочем столе. Пункт «Открыть облако» — по правому щелчку на ярлыке;
- по желанию включает автозапуск облака при входе в систему
  (`systemctl --user status neuro12-cloud`).

Ключи: `--yes` · `--no-deps` · `--no-cloud` · `--cloud ПАПКА|ZIP` ·
`--autostart` / `--no-autostart` · `--no-firewall` · `--no-run`.

Удаление: `bash ~/.local/share/neuro12-cloud/launcher/uninstall-linux.sh`.
Облако и его память удаляются, только если ответить «да».

## Чем отличается от Windows

| | Windows | Linux |
|---|---|---|
| Окно | WebView2 | браузер на Chromium в режиме приложения (Chromium, Яндекс Браузер, Chromium-GOST, Chrome, Edge), со своим профилем; иначе Firefox |
| Облако и плата «в этом окне» | панель вставляет WebView2 | лаунчер открывает их через локальный мост и сам вставляет ту же верхнюю панель. Облако открывается на `127.0.0.1:<порт>`, плата `a.b.c.d` — на `127.42.c.d:7000` |
| Окно консоли (пароль платы) | cmd.exe | окно терминала: gnome-terminal, mate-terminal, konsole, xfce4-terminal… |
| Права администратора (зеркало MAC) | окно UAC + PowerShell | окно пароля `pkexec` + NetworkManager (`cloned-mac-address`), без NM — `ip link` |
| Выбор папки | диалог Windows | zenity (GNOME, MATE) или kdialog (KDE) |
| Таблица ARP | `arp -a` | `/proc/net/arp` |
| Запуск облака | cloud_bg.vbs / pythonw | сервис пользователя `neuro12-cloud`, системный сервис (для `/opt`) или `python3 server/app.py` |

Скрипты на плате (`n12_deploy.sh`, `n12_mac.sh`, `n12_setcfg.py`) те же самые.

- Закрыл окно — лаунчер завершается, облако продолжает работать (как в Windows).
- Лаунчер уже открыт — повторный запуск просто показывает его окно.
- Окно лаунчера — другой браузер: `N12_BROWSER=yandex-browser-stable neuro12-start`
  или `"browser": "chromium"` в `~/.local/share/neuro12-launcher/launcher.json`.
- Без графики (сервер, ssh): `neuro12-start --no-window`. Лаунчер напечатает адрес
  интерфейса, его можно открыть через `ssh -L`.

## Сборка

```
cd app
./build.sh linux     # → ../bin/neuro12-start-linux-amd64, -arm64 (Go 1.21+, без cgo, статически)
./build.sh           # → ../neuro12-start.exe (Windows)
```

Исходники общие. Только для Windows — `os_windows.go` и `winapi.go`, только для
Linux — `os_linux.go` и `n12_mac_pc.sh`.
