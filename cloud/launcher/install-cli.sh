#!/usr/bin/env bash
# Ставит команды neuro12-cli и neuro12-server (ссылки на эти скрипты). Без sudo — в ~/.local/bin; с sudo — в /usr/local/bin.
set -e
D="$(cd "$(dirname "$0")" && pwd)"
if [ "$(id -u)" -eq 0 ]; then B=/usr/local/bin; else B="$HOME/.local/bin"; fi
mkdir -p "$B"
chmod +x "$D/neuro12-cli" "$D/neuro12-server" "$D/n12ctl.py"
ln -sf "$D/neuro12-cli" "$B/neuro12-cli"
ln -sf "$D/neuro12-server" "$B/neuro12-server"
echo "Готово: $B/neuro12-cli и $B/neuro12-server"
case ":$PATH:" in *":$B:"*) ;; *) echo "Добавьте $B в PATH: export PATH=\"$B:\$PATH\"";; esac
echo "Дальше: neuro12-cli doctor   |   neuro12-cli update --init-fleet"
