#!/bin/bash
# Hermetic build-only controller bound to proved local Docker image identity.
set -euo pipefail
[[ $# == 4 && "$(id -u)" != 0 ]]
layout="$1"; control="$2"; input="$3"; out="$4"
[[ "$layout" == /* && "$control" == /* && "$input" == /* && "$out" == /* && ! -e "$out" && ! -L "$out" ]]
python3 -B "$control/verify_035_material.py" "$input"
python3 -B "$control/verify_035_material.py" --source "$input/source.tar"
work="$(mktemp -d)"
export DOCKER_CONFIG="$work/docker-config"
mkdir -m 700 "$DOCKER_CONFIG"
unset DOCKER_HOST DOCKER_CONTEXT DOCKER_TLS_VERIFY DOCKER_CERT_PATH
docker_local() { timeout 120 docker --host unix:///var/run/docker.sock "$@"; }
tag=qualification.invalid/base035:fixed
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
id="$(docker_local image inspect --format '{{json .}}' "$tag" |
  python3 -B "$control/docker_035_verified_archive.py" check-inspect "$observed" --saved "$work/observed.tar")"
[[ "$id" =~ ^sha256:[a-f0-9]{64}$ ]]
python3 -B "$control/prepare_035_toolchain.py" "$input" "$work/toolchain"
mkdir -m 700 "$out"
# The proven immutable image ID, never the convenience tag, enters Docker run.
timeout 900 docker --host unix:///var/run/docker.sock run --rm --pull=never --network=none --ipc=private --read-only \
  --user "$(id -u):$(id -g)" --cap-drop=ALL --security-opt=no-new-privileges \
  --pids-limit=256 --memory=4g --cpus=2 \
  --tmpfs /tmp:rw,nosuid,nodev,mode=1777,size=1g \
  --mount "type=bind,src=$input,dst=/input,readonly" \
  --mount "type=bind,src=$work/toolchain,dst=/toolchain,readonly" \
  --mount "type=bind,src=$control/hosted_035_compile_inside.sh,dst=/compile.sh,readonly" \
  --mount "type=bind,src=$control/base-035-python-full.lock,dst=/python-full.lock,readonly" \
  --mount "type=bind,src=$out,dst=/out" \
  --entrypoint /usr/bin/env "$id" -i PATH=/usr/bin:/bin LC_ALL=C TZ=UTC \
    HOME=/tmp SOURCE_DATE_EPOCH=0 PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1 \
    /bin/bash --noprofile --norc /compile.sh
python3 -B "$control/package_035_rebuild.py" pack \
  "$out/src/apps/graph_viewer/web/dist" "$out/mcp-tool-schema.json" "$out/proof"
