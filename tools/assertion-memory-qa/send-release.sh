#!/usr/bin/env bash
set -euo pipefail
umask 077
# Existing sender remains TEST-only. PROD use requires its separate proof adapter.
target="${1:?test or production required}"
if [[ "$target" == production ]]; then
  echo 'PROD transport disabled: exact host proof bridge is not integrated.' >&2
  exit 1
fi
[[ "$target" == test && $# == 1 ]]
here="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
exec /usr/bin/env python3 "$here/send_test_release.py"
