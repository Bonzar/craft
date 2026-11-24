#!/bin/bash
set -euo pipefail
umask 077
[[ $(id -un) == assertion-test-deploy && $# == 2 ]]
root=/srv/assertion-release/test
state=$root/state
release=$2
units=(assertion-test-deploy-graph.service assertion-test-deploy-mcp.service)
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
  ;;
smoke)
  cd "$release"
  for attempt in 1 2 3 4 5; do
    if "$release/venv/bin/python" -B /etc/assertion-release/test/test-health.py smoke "$state"; then exit 0; fi
    sleep 1
  done
  exit 1
  ;;
stop)
  systemctl --user stop "${units[@]}"
  ;;
*)
  exit 2
  ;;
esac
