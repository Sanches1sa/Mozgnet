#!/usr/bin/env bash
# install-github.sh — mozgnet: репозиторий на GitHub + автосборка APK (Linux: РЕД ОС, Ubuntu)
#   bash install-github.sh [--public] [--name mozgnet] [--cloud ПАПКА_ОБЛАКА]
# Ставит git и gh (GitHub CLI), входит в GitHub (браузер), создаёт репозиторий,
# отправляет проект, ждёт сборку APK на GitHub и скачивает его сюда.
set -u
cd "$(dirname "$0")"
PUB=0; NAME=mozgnet; CLOUD=""
while [ $# -gt 0 ]; do
  case "$1" in
    --public) PUB=1 ;;
    --name) NAME="$2"; shift ;;
    --cloud) CLOUD="$2"; shift ;;
    *) echo "неизвестная опция $1"; exit 1 ;;
  esac; shift
done
say() { printf '\n==> %s\n' "$*"; }
die() { printf '  [XX] %s\n' "$*" >&2; exit 1; }
need() { command -v "$1" >/dev/null 2>&1; }

say "ПРОГРАММЫ"
if ! need git || ! need gh; then
  if need apt-get; then sudo apt-get update -q && sudo apt-get install -y git gh
  elif need dnf; then sudo dnf install -y git gh
  fi
fi
need git || die "нет git"
need gh || die "нет gh — поставь GitHub CLI: https://cli.github.com"

say "ВХОД В GITHUB"
gh auth status >/dev/null 2>&1 || gh auth login --web --git-protocol https --scopes "repo,workflow" || die "вход не выполнен"
ME=$(gh api user --jq .login)
echo "  вошёл как $ME"

if [ -n "$CLOUD" ]; then
  [ -f "$CLOUD/server/app.py" ] || die "в $CLOUD нет server/app.py"
  D=cloud; rm -rf "$D/server" "$D/web" "$D/docs"; mkdir -p "$D"
  for x in server web docs VERSION README.md AGENT-GUIDE.md; do [ -e "$CLOUD/$x" ] && cp -r "$CLOUD/$x" "$D/"; done
  find "$D" -name __pycache__ -prune -exec rm -rf {} +
  echo "  облако v$(cat "$D/VERSION")"
fi

say "РЕПОЗИТОРИЙ $ME/$NAME"
[ -d .git ] || git init -q -b main
git config user.name >/dev/null || git config user.name "$ME"
git config user.email >/dev/null || git config user.email "$ME@users.noreply.github.com"
git add -A
git commit -q -m "mozgnet: $(date '+%F %R')" 2>/dev/null
if ! gh repo view "$ME/$NAME" >/dev/null 2>&1; then
  VIS=--private; [ $PUB -eq 1 ] && VIS=--public
  gh repo create "$ME/$NAME" $VIS --source . --remote origin --push --description "mozgnet — облако Мозг-12 в телефоне" || die "не создал репозиторий"
else
  git remote | grep -q origin || git remote add origin "https://github.com/$ME/$NAME.git"
  git push -u origin main || die "push не прошёл"
fi

say "СБОРКА APK НА GITHUB (10–20 минут)"
sleep 8
RUN=$(gh run list -R "$ME/$NAME" --workflow build.yml --limit 1 --json databaseId --jq '.[0].databaseId')
[ -n "$RUN" ] || die "сборка не началась — https://github.com/$ME/$NAME/actions"
echo "  https://github.com/$ME/$NAME/actions/runs/$RUN"
gh run watch "$RUN" -R "$ME/$NAME" --exit-status || die "сборка упала — лог по ссылке выше"
gh release download -R "$ME/$NAME" --pattern "*.apk" --clobber
ls -1t mozgnet-*.apk 2>/dev/null | head -1 | sed 's/^/  APK: /'
echo "  На телефоне: https://github.com/$ME/$NAME/releases/latest"
