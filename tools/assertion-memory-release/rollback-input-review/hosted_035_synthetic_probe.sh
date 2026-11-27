#!/bin/bash
# Intended only on disposable runner with stock local engine. No source
# checkout, mounts, credentials, release effects or network inside container.
set -euo pipefail
[[ $# == 2 ]]
image="$1"; control="$2"
[[ "$image" =~ ^127\.0\.0\.1:[0-9]+/rollback-base035@sha256:3dafba6ea29fa67f90e74f649383eb2ae3d5d40407f50db0e0ef5a1d7d59a008$ ]]
unset DOCKER_HOST DOCKER_CONTEXT DOCKER_TLS_VERIFY DOCKER_CERT_PATH
bash "$control/hosted_035_import_preflight.sh" "$image" "$control"
docker --host unix:///var/run/docker.sock run --rm --pull=never --network=none \
  --read-only --user 65534:65534 --cap-drop=ALL --security-opt=no-new-privileges \
  --pids-limit=32 --memory=128m --entrypoint /usr/bin/env "$image" \
  -i PATH=/usr/bin:/bin LC_ALL=C /bin/bash --noprofile --norc -c '
set -euo pipefail
[[ "$(id -u)" == 65534 ]]
[[ ! -S /var/run/docker.sock ]]
[[ ! -e /github/workspace && ! -e /control && ! -e /input ]]
awk -F: '"'"'NR>2 { gsub(/[[:space:]]/,"",$1); if ($1 != "lo") exit 1 }'"'"' /proc/net/dev
awk '"'"'$2 == "/" { n++; if ($4 !~ /(^|,)ro(,|$)/) exit 1 } END {if (n!=1) exit 1}'"'"' /proc/mounts
grep -Eq "^NoNewPrivs:[[:space:]]+1$" /proc/self/status
grep -Eq "^CapEff:[[:space:]]+0+$" /proc/self/status
printf "%s\n" "OCI synthetic isolation probe passed"
'
