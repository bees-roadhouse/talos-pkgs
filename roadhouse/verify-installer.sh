#!/usr/bin/env bash
# Pulls an installer image and runs inspect_installer.py on it.
# Usage: verify-installer.sh <image ref>   (needs crane, python3, zstd)
set -euo pipefail

ref="${1:?installer image ref}"
work=$(mktemp -d)
trap 'rm -rf "${work}"' EXIT

crane export --platform linux/arm64 "${ref}" "${work}/installer.tar"
python3 "$(dirname "$0")/inspect_installer.py" "${work}/installer.tar"
