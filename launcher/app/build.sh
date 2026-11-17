#!/bin/sh
# Сборка лаунчера «Мозг-12» (Go, без cgo).
#   ./build.sh          → ../neuro12-start.exe (Windows: Go + WebView2, иконка из .syso)
#   ./build.sh linux    → ../bin/neuro12-start-linux-amd64 и -arm64 (Linux: окно браузера)
# Windows: зависимость github.com/jchv/go-webview2; golang.org/x/sys — с зеркала GitHub.
cd "$(dirname "$0")" || exit 1
if [ "$1" = "linux" ]; then
  mkdir -p ../bin
  for a in amd64 arm64; do
    GOOS=linux GOARCH=$a CGO_ENABLED=0 go build -trimpath -ldflags "-s -w" -o ../bin/neuro12-start-linux-$a . || exit 1
  done
else
  GOOS=windows GOARCH=amd64 CGO_ENABLED=0 \
    go build -trimpath -ldflags "-s -w -H windowsgui" -o ../neuro12-start.exe .
fi
