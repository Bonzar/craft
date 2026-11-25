#!/bin/bash
set -euo pipefail
umask 077
[[ $(id -un) == assertion-test-deploy && $# == 2 ]]
root=/srv/assertion-release/test
state=$root/state
release=$2
units=(assertion-test-deploy-graph.service assertion-test-deploy-mcp.service)
gate=/etc/assertion-release/test/test-sites-graph-gate.py
gate_config=/etc/assertion-release/test/sites-graph-gate.json
canonical=false
if [[ -e "$gate_config" || -L "$gate_config" ]]; then
  [[ -f "$gate_config" && ! -L "$gate_config" && $(stat -c '%u:%a:%h' -- "$gate_config") == 0:644:1 ]]
  [[ -f "$gate" && ! -L "$gate" && $(stat -c '%u:%a:%h' -- "$gate") == 0:644:1 ]]
  canonical=true
fi
case "$1" in
prepare)
  [[ "${release%/*}" == "$root/releases" && "${release##*/}" =~ ^[a-f0-9]{40}-[a-f0-9]{64}$ ]]
  [[ -d "$release" && "$(readlink -e -- "$release")" == "$release" ]]
  if [[ -e "$root/current" || -L "$root/current" ]]; then
    current=$(readlink -e -- "$root/current")
    [[ "$current" != "$release" ]]
  fi
  if [[ -e "$release/venv" || -L "$release/venv" ]]; then
    [[ -d "$release/venv" && ! -L "$release/venv" ]]
    [[ "$(readlink -e -- "$release/venv")" == "$release/venv" ]]
  fi
  [[ -f "$release/requirements-graph-lexical.txt" ]]
  # TEST uses its own reviewed Linux/Python wheelhouse, not the PROD path or a remote index.
  deps=/etc/assertion-release/test
  wheels="$deps/wheels"
  lock="$deps/locked-requirements.txt"
  mcp="$deps/approved-requirements-mcp.txt"
  graph="$deps/approved-requirements-graph-lexical.txt"
  for dir in "$deps" "$wheels"; do
    [[ -d "$dir" && ! -L "$dir" && $(stat -c '%u:%a' -- "$dir") == 0:755 ]]
  done
  for file in "$lock" "$mcp" "$graph"; do
    [[ -f "$file" && ! -L "$file" && $(stat -c '%u:%a:%h' -- "$file") == 0:644:1 ]]
  done
  invalid_wheel=$(find "$wheels" -mindepth 1 -maxdepth 1 \( ! -type f -o ! -user root -o -perm /022 -o ! -name '*.whl' -o -links +1 \) -print -quit)
  [[ -z "$invalid_wheel" ]]
  cmp -s -- "$mcp" "$release/prototypes/assertion_memory/requirements-mcp.txt"
  cmp -s -- "$graph" "$release/requirements-graph-lexical.txt"
  # --no-index alone does not exclude direct URL requirements; constrain the
  # reviewed lock to the same strictly hashed, local pins grammar as PROD.
  /usr/bin/python3 -I -B - "$lock" <<'PY_LOCK'
import re, sys
from pathlib import Path
lock = Path(sys.argv[1])
if not 0 < lock.stat().st_size <= 262144:
    raise SystemExit('invalid TEST wheel lock')
lines = lock.read_text(encoding='ascii').splitlines()
pattern = r'[A-Za-z0-9_.-]+==[A-Za-z0-9_.-]+(?: --hash=sha256:[a-f0-9]{64})+'
if not lines or any(not re.fullmatch(pattern, line) for line in lines):
    raise SystemExit('invalid TEST wheel lock')
PY_LOCK
  cd "$release"
  /usr/bin/python3 -m venv --clear venv
  py="$release/venv/bin/python"
  PIP_CONFIG_FILE=/dev/null "$py" -m pip --isolated install --no-index --find-links "$wheels" --no-deps \
    --require-hashes --only-binary=:all: --disable-pip-version-check --no-cache-dir -r "$lock"
  PIP_CONFIG_FILE=/dev/null "$py" -m pip --isolated check
  PYTHONPATH="$release" "$py" -B /etc/assertion-release/test/test-health.py initialize "$state"
  ;;
restart|restore)
  systemctl --user restart "${units[@]}"
  if "$canonical"; then /usr/bin/python3 -I -B "$gate" restart "$release"; fi
  ;;
smoke)
  cd "$release"
  for attempt in 1 2 3 4 5; do
    if "$canonical"; then
      if "$release/venv/bin/python" -B /etc/assertion-release/test/test-health.py smoke-mcp "$state" &&
           /usr/bin/python3 -I -B "$gate" smoke "$release"; then exit 0; fi
    elif "$release/venv/bin/python" -B /etc/assertion-release/test/test-health.py smoke "$state"; then exit 0
    fi
    sleep 1
  done
  exit 1
  ;;
stop)
  if "$canonical"; then /usr/bin/python3 -I -B "$gate" stop "$release"; fi
  systemctl --user stop "${units[@]}"
  ;;
*)
  exit 2
  ;;
esac
