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
  [[ "$release" == "$root/releases/"* && -f "$release/requirements-graph-lexical.txt" ]]
  cd "$release"
  python3 -m venv venv
  py="$release/venv/bin/python"
  "$py" -m pip install --disable-pip-version-check --only-binary=:all: --no-cache-dir \
    -r "$release/prototypes/assertion_memory/requirements-mcp.txt" -r "$release/requirements-graph-lexical.txt"
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
