#!/usr/bin/env bash
# Copies the few core files the control server imports (listed in deploy/kernel-files.txt) into <dest>/kernel/...,
# so a release does not need the whole repository beside it. The unit puts <release>/vendor on PYTHONPATH.
# backend/tests/test_vendored_kernel.py fails when the list and the server's real imports drift apart.
#
#   scripts/vendor_kernel.sh <dest>
set -euo pipefail

CLOUD="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO="$(dirname "$CLOUD")"
DEST="${1:-}"
[[ -n "$DEST" ]] || { echo "usage: vendor_kernel.sh <dest>" >&2; exit 2; }

rm -rf "$DEST/kernel"
mkdir -p "$DEST"
(cd "$REPO" && xargs -a "$CLOUD/deploy/kernel-files.txt" cp --parents -t "$DEST")
echo "ok: $(wc -l < "$CLOUD/deploy/kernel-files.txt") core files vendored into $DEST"
