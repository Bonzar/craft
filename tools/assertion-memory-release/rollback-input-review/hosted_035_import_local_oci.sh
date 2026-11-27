#!/bin/bash
# Stock rootless runner only; both transports are local and preserve digests.
set -euo pipefail
[[ $# == 1 && "$(id -u)" != 0 ]]
layout="$(realpath -e -- "$1")"
[[ -d "$layout" && "$layout" != *:* && "$layout" != *$'\n'* ]]
scope="$(mktemp -d)"
trap 'rm -rf -- "$scope"' EXIT
mkdir -m 700 "$scope/graph" "$scope/run"
# Isolated local store shared only by this source-review helper's commands.
# Prevent inherited remote connections; no registry resolution or persistent auth.
unset CONTAINER_HOST CONTAINER_CONNECTION DOCKER_HOST DOCKER_CONTEXT
export CONTAINERS_STORAGE_CONF="$scope/storage.conf"
printf '[storage]\ndriver="vfs"\nrunroot="%s/run"\ngraphroot="%s/graph"\n' \
  "$scope" "$scope" > "$CONTAINERS_STORAGE_CONF"
manifest=sha256:3dafba6ea29fa67f90e74f649383eb2ae3d5d40407f50db0e0ef5a1d7d59a008
config=sha256:f3e1eee55ce80142e1dd9f00251dfe49ebeed80437ce49e3c9077403558fcc32
ref=localhost/rollback-base035:qualification
# Prove source and post-copy raw manifest bytes independently.
wanted="${manifest#sha256:}"
before="$(skopeo inspect --raw "oci:$layout" | sha256sum)"
[[ "${before%% *}" == "$wanted" ]]
skopeo copy --preserve-digests "oci:$layout" "containers-storage:$ref"
after="$(skopeo inspect --raw "containers-storage:$ref" | sha256sum)"
[[ "${after%% *}" == "$wanted" ]]
observed="$(podman --remote=false image inspect --format '{{.Digest}} {{.Id}} {{.Os}} {{.Architecture}}' "$ref")"
[[ "$observed" == "$manifest ${config#sha256:} linux amd64" || "$observed" == "$manifest $config linux amd64" ]]
# Execution is by the verified config ID within this private store, never by tag.
podman --remote=false run --rm --pull=never --network=none --image-volume=ignore \
  --http-proxy=false --read-only --user 65534:65534 --cap-drop=ALL \
  --security-opt=no-new-privileges --pids-limit=32 \
  --entrypoint /usr/bin/env "${config#sha256:}" \
  -i PATH=/usr/bin:/bin LC_ALL=C /bin/bash --noprofile --norc -c '
set -euo pipefail
[[ "$(id -u)" == 65534 ]]
[[ ! -S /var/run/docker.sock && ! -S /run/podman/podman.sock ]]
[[ ! -e /github/workspace && ! -e /control && ! -e /input ]]
awk -F: '"'"'NR>2 { gsub(/[[:space:]]/,"",$1); if ($1 != "lo") exit 1 }'"'"' /proc/net/dev
awk '"'"'$2 == "/" { n++; if ($4 !~ /(^|,)ro(,|$)/) exit 1 } END {if (n!=1) exit 1}'"'"' /proc/mounts
grep -Eq "^NoNewPrivs:[[:space:]]+1$" /proc/self/status
grep -Eq "^CapEff:[[:space:]]+0+$" /proc/self/status
printf "%s\n" "OCI synthetic isolation probe passed"
'
