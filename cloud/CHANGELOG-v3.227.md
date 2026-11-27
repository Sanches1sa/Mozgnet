# 3.227.0 — командные лаунчеры (Linux и сервер) и обновлятор всех модулей с одного компьютера

- `launcher/n12ctl.py` + `neuro12-cli`, `neuro12-server`, `install-cli.sh`: start/stop/restart/status/logs/open/doctor; сервер: install/uninstall/enable/disable/backup/firewall через systemd.
- `update`: определяет пакеты (облако, плата, бинарник лаунчера, apk) по содержимому, читает `fleet.json`, обновляет облако (local/ssh/http), плату (ssh + `n12_deploy.sh`), лаунчер, приложение; проверка версии по `/api/health`, автооткат, план `--dry-run`, отчёт JSON. Секреты — `env:ИМЯ`.
- Исправление: CLI `updater.py apply|rollback` больше не перезапускает себя вместо сервера (флаг `--restart`); перезапуск — `n12ctl restart` (systemd либо процесс по /proc).
- Документация `docs/CLI-LAUNCHERS-RU.md`. Тесты: `tests/test_n12ctl227.py` (12).
- Честно: SSH/плата/systemd/adb не проверялись вживую; проверено локальное и HTTP-обновление реального облака.
