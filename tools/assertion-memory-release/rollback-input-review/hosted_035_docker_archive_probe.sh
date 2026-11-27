#!/bin/bash
# Exact Docker local archive qualification; no registry, host policy or app.
set -euo pipefail
[[ $# == 2 && "$(id -u)" != 0 ]]
layout="$1"; control="$2"
work="$(mktemp -d)"
export DOCKER_CONFIG="$work/docker-config"
mkdir -m 700 "$DOCKER_CONFIG"
unset DOCKER_HOST DOCKER_CONTEXT DOCKER_TLS_VERIFY DOCKER_CERT_PATH
docker_local() { timeout 120 docker --host unix:///var/run/docker.sock "$@"; }
tag=qualification.invalid/base035:fixed
id=sha256:f3e1eee55ce80142e1dd9f00251dfe49ebeed80437ce49e3c9077403558fcc32
loaded=''
cleanup() {
  rc=$?
  trap - EXIT
  if [[ -n "$loaded" ]]; then
    docker_local image rm "$tag" >/dev/null || rc=1
  fi
  rm -rf -- "$work"
  exit "$rc"
}
trap cleanup EXIT
if docker_local image inspect "$tag" >/dev/null 2>&1; then
  echo 'Unexpected pre-existing qualification tag' >&2
  exit 1
fi
python3 -B "$control/docker_035_verified_archive.py" write "$layout" "$work/payload.tar"
expected="$(python3 -B "$control/docker_035_verified_archive.py" verify "$work/payload.tar")"
docker_local image load --input "$work/payload.tar" >/dev/null
loaded=1
docker_local image save --output "$work/observed.tar" "$tag"
observed="$(python3 -B "$control/docker_035_verified_archive.py" verify "$work/observed.tar")"
[[ "$expected" == "$observed" ]]
docker_local image inspect --format '{{json .}}' "$tag" |
python3 -c 'import json,sys; i=json.load(sys.stdin); p=json.loads(sys.argv[1]); assert i["Id"]==p["id"] and i["Os"]=="linux" and i["Architecture"]=="amd64" and i["RootFS"]["Layers"]==p["diff_ids"]' "$observed"
docker_local run --rm --pull=never --network=none --read-only --user 65534:65534 \
  --cap-drop=ALL --security-opt=no-new-privileges --pids-limit=32 --memory=128m \
  --entrypoint /usr/bin/env "$id" -i PATH=/usr/bin:/bin LC_ALL=C \
  /bin/bash --noprofile --norc -c '
set -euo pipefail
[[ "$(id -u)" == 65534 ]]
[[ ! -S /var/run/docker.sock && ! -e /github/workspace && ! -e /control && ! -e /input ]]
awk -F: '"'"'NR>2 { gsub(/[[:space:]]/,"",$1); if ($1 != "lo") exit 1 }'"'"' /proc/net/dev
awk '"'"'$2 == "/" { n++; if ($4 !~ /(^|,)ro(,|$)/) exit 1 } END {if (n!=1) exit 1}'"'"' /proc/mounts
grep -Eq "^NoNewPrivs:[[:space:]]+1$" /proc/self/status
grep -Eq "^CapEff:[[:space:]]+0+$" /proc/self/status
printf "%s\n" "OCI synthetic isolation probe passed"
'
