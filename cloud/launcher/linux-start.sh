#!/usr/bin/env bash
set -e
cd "$(dirname "$0")/.."
exec python3 launcher/neuro12-linux.py "$@"
