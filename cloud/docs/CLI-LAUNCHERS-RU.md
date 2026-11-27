# Командные лаунчеры и обновлятор «всё сразу» (3.227)

Графический лаунчер `neuro12-start` (Go, Windows и Linux) уже есть в `launcher/` — он не менялся.
Новое — командная строка, без графики, только Python 3 (stdlib): `cloud/launcher/n12ctl.py`.

## Установка команд
```bash
bash cloud/launcher/install-cli.sh     # ссылки neuro12-cli и neuro12-server в ~/.local/bin (с sudo — /usr/local/bin)
neuro12-cli doctor                     # проверка окружения
```

## neuro12-cli — Linux-компьютер
`start | stop | restart | status [--json] | logs [-n 60] [-f] | open | doctor | update …`
Облако запускается обычным процессом пользователя (PID и журнал в `data/n12ctl.*`); если установлена и активна служба `neuro12-cloud`, используется она. Папка облака: `--home`, `N12_HOME`, рядом, `~/.local/share/neuro12-cloud`, `/opt/neuro12-cloud`.

## neuro12-server — сервер
То же плюс: `install` / `uninstall` (штатные `install.sh` / `uninstall.sh`, ключи после `--`), `enable` / `disable` (автозапуск службы), `backup [--out файл]` (tar.gz данных), `firewall status|open` (ufw или firewalld). `start/stop/logs` идут через systemd и journalctl.

## Обновить все модули с одного компьютера
1. Положите в одну папку свежие пакеты: `neuro12-cloud-*.zip`, `neuro12-board-*.zip`, бинарники `neuro12-start-linux-amd64|arm64`, при желании `*.apk`. Вид пакета определяется по содержимому, из нескольких берётся самый новый.
2. `neuro12-cli update --init-fleet` создаёт образец `~/.config/neuro12/fleet.json` (права 600). Перечислите свои машины:

| type | как обновляется | поля |
|---|---|---|
| `cloud`, `via: local` | `server/updater.py apply` + перезапуск | `path`, `url` |
| `cloud`, `via: ssh` | scp пакета, `updater.py apply`, перезапуск `n12ctl restart` на машине | `host`, `user`, `path`, `url`, `key`, `port`, `profile: server` для systemd |
| `cloud`, `via: http` | вход по паролю панели, `POST /api/update/upload` | `url`, `password` |
| `board` | SSH: папка приложения + `n12_deploy.sh` (сборка на плате) | `host`, `user`, `cloud_url`, `token` |
| `launcher` | замена бинарника (локально или по SSH) | `path`, `arch`, `host`, `user` |
| `app` | `adb install -r` | `serial`, `apk` |

   Пароли и токены пишите как `"env:ИМЯ"` — значение берётся из переменной окружения, в файле его нет.
3. Сначала посмотрите план: `neuro12-cli update --bundle ~/релиз --dry-run`. Затем: `neuro12-cli update --bundle ~/релиз [--only cloud,board] [--names VPS,UNO] [--auto-rollback] [--report итог.json]`.

Порядок: облако → плата → лаунчер → приложение (плата берёт конфиг у облака). Машины одного вида — параллельно (`--jobs`). Если облако обновилось с ошибкой, остальное не трогается (`--keep-going` — продолжить). После обновления облака лаунчер ждёт `/api/health` с новой версией (`--wait`, 90 с); не поднялось и задан `--auto-rollback` — откат из резервной копии, которую делает обновлятор. Машины с той же версией пропускаются (`--force`).

## Что проверено, что нет
Проверено: живое локальное обновление облака 3.226.0 → 3.226.1 с перезапуском, обновление по HTTP по паролю, ошибка неверного пароля; тесты (12) с подменой ssh/scp/adb для SSH-пути, платы, лаунчера, apk, порядка, отката. **Не проверено:** реальный SSH к серверу и к UNO Q (сборка скетча на плате), systemd-путь, adb с телефоном, доступ из внешней сети. Нужны ключи SSH без пароля (`BatchMode`). Android-приложение ставится только как готовый apk.

## Исправлено попутно
`server/updater.py apply|rollback` из командной строки раньше планировал перезапуск «себя» (процесса CLI), а не сервера — теперь по умолчанию перезапуска нет (флаг `--restart`), сервер перезапускает `n12ctl restart`.
