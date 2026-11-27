#!/usr/bin/env bash
# Install an unprivileged systemd user unit. No GUI is required.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
if [ "$(id -u)" -eq 0 ]; then
  echo "Запустите от обычного пользователя Ubuntu, а не через sudo." >&2
  exit 1
fi
command -v python3 >/dev/null || { echo "Установите python3" >&2; exit 1; }
command -v systemctl >/dev/null || { echo "Нет systemd; запускайте bash launcher/linux-start.sh --start-cloud" >&2; exit 1; }
export NEURO_LAUNCHER_ROOT="$ROOT"
python3 - <<'PY'
import os
from pathlib import Path
root=Path(os.environ['NEURO_LAUNCHER_ROOT'])
# systemd quoting, including literal percent specifiers.
def quote(s):return '"'+str(s).replace('\\','\\\\').replace('"','\\"').replace('%','%%')+'"'
for x in [str(root)]:
    if '\n' in x or '\r' in x:raise ValueError('Каталог не должен содержать перевод строки')
unit='''[Unit]
Description=Neuro12 Linux launcher and cloud
After=network.target

[Service]
Type=simple
WorkingDirectory=%s
ExecStart=/usr/bin/python3 %s --start-cloud
Restart=on-failure
RestartSec=5
KillMode=control-group
TimeoutStopSec=30
UMask=0077

[Install]
WantedBy=default.target
'''%(quote(root),quote(root/'launcher/neuro12-linux.py'))
folder=Path.home()/'.config/systemd/user';folder.mkdir(parents=True,exist_ok=True)
p=folder/'neuro12-launcher.service';p.write_text(unit)
print('Создан '+str(p))
PY
systemctl --user daemon-reload
systemctl --user enable --now neuro12-launcher.service
echo "Лаунчер установлен. Ссылка с токеном:"
journalctl --user -u neuro12-launcher -n 20 --no-pager
echo "Для работы после выхода/перезагрузки: sudo loginctl enable-linger $(id -un)"
echo "Если облако уже работает как системная служба, лаунчер не запускает второй процесс."
