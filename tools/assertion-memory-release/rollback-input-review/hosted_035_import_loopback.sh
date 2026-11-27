#!/bin/bash
# Read-only loopback transfer into the preinstalled daemon; no privileged
# container, external registry, daemon settings, or remote writes.
set -euo pipefail
[[ $# == 2 ]]
layout="$1"; control="$2"
state="$(mktemp -d)"
pid=''
cleanup() {
  if [[ -n "$pid" ]]; then
    kill "$pid" 2>/dev/null || true
    wait "$pid" 2>/dev/null || true
  fi
  rm -rf -- "$state"
}
trap cleanup EXIT
python3 -B "$control/serve_035_oci_loopback.py" "$layout" >"$state/port" &
pid="$!"
for _ in {1..100}; do
  [[ -s "$state/port" ]] && break
  kill -0 "$pid" 2>/dev/null || exit 1
  sleep .1
done
read -r port <"$state/port"
[[ "$port" =~ ^[1-9][0-9]{0,4}$ ]] && (( port <= 65535 ))
image="127.0.0.1:$port/rollback-base035@sha256:3dafba6ea29fa67f90e74f649383eb2ae3d5d40407f50db0e0ef5a1d7d59a008"
# A fresh empty client config supplies no credentials, aliases or contexts.
mkdir -m 700 "$state/docker"
env -u DOCKER_HOST -u DOCKER_CONTEXT -u DOCKER_TLS_VERIFY -u DOCKER_CERT_PATH \
  timeout 120 docker --config "$state/docker" --host unix:///var/run/docker.sock \
  pull --platform linux/amd64 "$image" >&2
kill "$pid"
wait "$pid" 2>/dev/null || true
pid=''
env -u DOCKER_HOST -u DOCKER_CONTEXT -u DOCKER_TLS_VERIFY -u DOCKER_CERT_PATH \
  bash "$control/hosted_035_import_preflight.sh" "$image" "$control"
printf '%s\n' "$image"
