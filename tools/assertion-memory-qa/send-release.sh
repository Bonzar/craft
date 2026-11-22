#!/usr/bin/env bash
set -euo pipefail
umask 077
target="${1:?test or production required}"
[[ "$target" == test || "$target" == production ]]
[[ "$GITHUB_REPOSITORY" == Bonzar/craft && "$GITHUB_REF" == refs/heads/codex/assertion-memory-release && "$GITHUB_EVENT_NAME" == push ]]
[[ "${RELEASE_ENABLED:-}" == true ]] || { echo 'Deployment is not provisioned/enabled for this environment.' >&2; exit 1; }
[[ "${RELEASE_USER:-}" == "assertion-$target-deploy" ]]
[[ "${RELEASE_HOST:-}" =~ ^[0-9A-Za-z][0-9A-Za-z.-]{0,252}$ && "${RELEASE_PORT:-}" =~ ^[1-9][0-9]{0,4}$ && "$RELEASE_PORT" -le 65535 ]]
[[ "${GITHUB_SHA:-}" =~ ^[a-f0-9]{40}$ && "${RELEASE_ARCHIVE_SHA256:-}" =~ ^[a-f0-9]{64}$ && "${ARTIFACT_ID:-}" =~ ^[1-9][0-9]{0,19}$ ]]
[[ -n "${SSH_PRIVATE_KEY:-}" && -n "${SSH_HOST_PIN:-}" ]] || { echo 'Deployment credentials or pinned host key missing.' >&2; exit 1; }
file="${RELEASE_DOWNLOAD_DIR:?}/release.tar.gz"
test "$(sha256sum < "$file" | cut -d ' ' -f1)" = "$RELEASE_ARCHIVE_SHA256"
if [[ "$target" == production ]]; then
  [[ "${TESTED_ARTIFACT_ID:-}" == "$ARTIFACT_ID" && "${TESTED_REVISION:-}" == "$GITHUB_SHA" && "${TESTED_SHA256:-}" == "$RELEASE_ARCHIVE_SHA256" ]] || { echo 'Production must promote the exact green TEST artifact.' >&2; exit 1; }
fi
tmp="$(mktemp -d)"
trap 'rm -rf -- "$tmp"' EXIT
printf '%s\n' "$SSH_PRIVATE_KEY" > "$tmp/key"
printf '%s\n' "$SSH_HOST_PIN" > "$tmp/known_hosts"
# Only the exact independently provisioned Ed25519 pin is accepted.
if [[ "$RELEASE_PORT" == 22 ]]; then host_field="$RELEASE_HOST"; else host_field="[$RELEASE_HOST]:$RELEASE_PORT"; fi
awk -v host="$host_field" 'NF!=3||$1!=host||$2!="ssh-ed25519"{exit 1} END{if(NR!=1) exit 1}' "$tmp/known_hosts"
ssh-keygen -lf "$tmp/known_hosts" >/dev/null
# Never run in repo checkout's SSH context. Accept no forwarding, shell, auth agent,
# ambient config or key; never disable host verification or learn host identity at deploy.
unset SSH_PRIVATE_KEY SSH_AUTH_SOCK SSH_HOST_PIN
if ! timeout 570 ssh -F /dev/null -T -p "$RELEASE_PORT" \
  -i "$tmp/key" -o BatchMode=yes -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes \
  -o UserKnownHostsFile="$tmp/known_hosts" -o GlobalKnownHostsFile=/dev/null \
  -o HostKeyAlgorithms=ssh-ed25519 -o ControlMaster=no -o ClearAllForwardings=yes \
  -o PasswordAuthentication=no -o KbdInteractiveAuthentication=no \
  -o ConnectTimeout=10 -o ServerAliveInterval=15 -o ServerAliveCountMax=4 \
  -- "$RELEASE_USER@$RELEASE_HOST" \
  "deploy-release $target $GITHUB_SHA $RELEASE_ARCHIVE_SHA256" < "$file" > "$tmp/receipt" 2> "$tmp/ssh-errors"; then
  echo 'Restricted deployment command failed; inspect trusted VM release logs.' >&2
  exit 1
fi
python3 - "$tmp/receipt" "$target" <<'PY'
import json, os, pathlib, sys
p=pathlib.Path(sys.argv[1])
if p.stat().st_size > 2048:
    raise SystemExit('Invalid VM deployment receipt')
value=json.loads(p.read_bytes())
expected={'status':'ok','environment':sys.argv[2],'revision':os.environ['GITHUB_SHA'],
          'sha256':os.environ['RELEASE_ARCHIVE_SHA256']}
if value!=expected:
    raise SystemExit('VM did not confirm activated and verified release')
print('Activated and verified:',expected['environment'],expected['revision'],expected['sha256'])
with open(os.environ['GITHUB_OUTPUT'],'a') as target:
    target.write('revision='+expected['revision']+'\n')
    target.write('sha256='+expected['sha256']+'\n')
    target.write('artifact_id='+os.environ['ARTIFACT_ID']+'\n')
PY
