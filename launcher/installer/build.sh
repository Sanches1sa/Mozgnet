#!/bin/sh
# Сборка install.exe (Go, без зависимостей): из Linux/macOS/Windows
cd "$(dirname "$0")" && GOOS=windows GOARCH=amd64 CGO_ENABLED=0 go build -trimpath -ldflags "-s -w" -o ../../install.exe .
