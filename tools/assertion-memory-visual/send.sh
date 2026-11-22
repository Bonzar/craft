#!/usr/bin/env bash
set -euo pipefail
umask 077
mode="${1:?}"
[[ "$mode" == test || "$mode" == prod ]]
[[ "$GITHUB_WORKFLOW_REF" == "$GITHUB_REPOSITORY/.github/workflows/assertion-memory-visual.yml@refs/heads/main" ]]
[[ "$GITHUB_REF" == refs/heads/main && "$GITHUB_EVENT_NAME" == workflow_dispatch && "$GITHUB_RUN_ATTEMPT" == 1 ]]
[[ "${VISUAL_DEPLOY_ENABLED:-}" == true ]] || { echo 'Visual receiver is not enabled' >&2; exit 1; }
[[ "${VISUAL_HOST:-}" =~ ^[0-9A-Za-z][0-9A-Za-z.-]{0,252}$ && "${VISUAL_PORT:-}" =~ ^[1-9][0-9]{0,4}$ && "$VISUAL_PORT" -le 65535 ]]
[[ "${VISUAL_SHA:-}" =~ ^[a-f0-9]{64}$ && "${VISUAL_DIGEST:-}" =~ ^[a-f0-9]{64}$ ]]
[[ -n "${VISUAL_SSH_KEY:-}" && -n "${VISUAL_HOST_PIN:-}" ]]
artifact="${VISUAL_DOWNLOAD:?}/visual.tar"
test "$(sha256sum < "$artifact" | cut -d ' ' -f1)" = "$VISUAL_SHA"
temp="$(mktemp -d)"
trap 'rm -rf -- "$temp"' EXIT
printf '%s\n' "$VISUAL_SSH_KEY" > "$temp/key"
printf '%s\n' "$VISUAL_HOST_PIN" > "$temp/known_hosts"
host_field="$VISUAL_HOST"
[[ "$VISUAL_PORT" == 22 ]] || host_field="[$VISUAL_HOST]:$VISUAL_PORT"
awk -v host="$host_field" 'NF!=3||$1!=host||$2!="ssh-ed25519"{exit 1} END{if(NR!=1) exit 1}' "$temp/known_hosts"
ssh-keygen -lf "$temp/known_hosts" >/dev/null
unset VISUAL_SSH_KEY VISUAL_HOST_PIN SSH_AUTH_SOCK
if ! timeout 50 ssh -F /dev/null -T -p "$VISUAL_PORT" \
    -i "$temp/key" -o BatchMode=yes -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes \
    -o HostKeyAlgorithms=ssh-ed25519 -o GlobalKnownHostsFile=/dev/null -o UserKnownHostsFile="$temp/known_hosts" \
    -o ControlMaster=no -o ClearAllForwardings=yes -o PasswordAuthentication=no -o KbdInteractiveAuthentication=no \
    -o ConnectTimeout=10 -- "assertion-visual-${mode}-deploy@$VISUAL_HOST" \
    "deploy-visual $mode $VISUAL_SHA" < "$artifact" > "$temp/receipt" 2> "$temp/error"; then
    echo 'Restricted visual receiver did not activate; inspect authorized VM status' >&2
    exit 1
fi
python3 - "$temp/receipt" "$mode" <<'PY'
import json,os,pathlib,sys
p=pathlib.Path(sys.argv[1])
if p.stat().st_size>1024 or json.loads(p.read_bytes())!={'environment':sys.argv[2], 'archive_sha256':os.environ['VISUAL_SHA'],'release':os.environ['VISUAL_DIGEST']}:
    raise SystemExit('Visual receiver receipt did not match approved artifact')
PY
