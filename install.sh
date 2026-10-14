#!/usr/bin/env bash
# Convenience wrapper for developing from WSL: runs the Windows installer (Bluetooth is only reachable from Windows).
# Arguments are passed through, e.g. ./install.sh -SkipWidget
set -euo pipefail
script=$(wslpath -w "$(dirname "$(realpath "$0")")/install.ps1")
cd /mnt/c && powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$script" "$@"
