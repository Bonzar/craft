#!/bin/bash
# First adoption only; does not provision users, SSH, ports, DBs or services.
# Use --check under existing authorized root. --apply changes only root-owned
# program files; requires separately approved host use and fresh admin session.
set -Eeuo pipefail
export PATH=/usr/sbin:/usr/bin:/sbin:/bin LC_ALL=C
umask 077
mode="${1:-}"
[[ $EUID == 0 && $# == 4 && ( "$mode" == --check || "$mode" == --apply ) ]]
src=$2
manifest_hash=$3
proof_sha=$4
[[ "$src" == /* && "$manifest_hash" =~ ^[0-9a-f]{64}$ && "$proof_sha" =~ ^[0-9a-f]{64}$ ]]
[[ -d "$src" && ! -L "$src" ]]
[[ $(stat -c '%u' -- "$src") == 0 ]]
[[ $(stat -c '%a' -- "$src") == 700 || $(stat -c '%a' -- "$src") == 750 || $(stat -c '%a' -- "$src") == 755 ]]
legacy_sha=3b3cc89899d5d5286baede09738e05681be82631c1518533ed8740412cae460b
pinned_hook=69c8a3719a10334690d7e4c5da57da695869221868a63d8ecc23b75e4e6ff5c6
home=/srv/assertion-release/test
config=/etc/assertion-release/test
control=/etc/assertion-release
shared=$control/shared
receiver=$config/receive.py
wrapper=$config/receiver-login
keys=/etc/ssh/assertion-release/test.authorized_keys
hook=$config/service-hook.sh
candidate_files=(base_payload.py receive.py release_core.py)
check_hash() { [[ $(sha256sum -- "$1" | cut -d' ' -f1) == "$2" ]]; }
root_file() { [[ -f "$1" && ! -L "$1" && $(stat -c '%u:%g:%a' -- "$1") == "0:0:$2" ]]; }
for folder in "$control" "$config"; do
  [[ -d "$folder" && ! -L "$folder" && $(stat -c '%u:%g:%a' -- "$folder") == 0:0:755 ]]
done
root_file "$src/SHA256SUMS" 644
check_hash "$src/SHA256SUMS" "$manifest_hash"
[[ $(awk 'NF==2 {print $2}' "$src/SHA256SUMS" | sort | paste -sd, -) == base_payload.py,receive.py,release_core.py ]]
for f in "${candidate_files[@]}"; do root_file "$src/$f" 644; done
(cd -- "$src"; sha256sum --check --strict --status SHA256SUMS)
root_file "$receiver" 644
root_file "$wrapper" 755
root_file "$keys" 644
# Both deployed owner-only-write modes are compatible with the exact reviewed
# hook: this receiver invokes it by /bin/bash, not as an executable.
if ! root_file "$hook" 755; then root_file "$hook" 644; fi
check_hash "$hook" "$pinned_hook"
check_hash "$receiver" "$legacy_sha"
check_hash "$wrapper" "$proof_sha"
entry=$(getent passwd assertion-test-deploy)
[[ "$entry" == *":$home:$wrapper" ]]
[[ -d "$home/releases" && ! -L "$home/releases" && -L "$home/current" ]]
active=$(readlink -- "$home/current")
[[ "$active" =~ ^/srv/assertion-release/test/releases/[0-9a-f]{40}-[0-9a-f]{64}$ ]]
[[ -d "$active" && ! -L "$active" && -f "$active/.prepared-v1" && ! -L "$active/.prepared-v1" ]]
[[ ! -e "$home/.activation-pending-v1" && ! -L "$home/.activation-pending-v1" ]]
if [[ -e "$shared" || -L "$shared" ]]; then
  [[ -d "$shared" && ! -L "$shared" && $(stat -c '%u:%g:%a' -- "$shared") == 0:0:755 ]]
  for f in release_core.py base_payload.py; do root_file "$shared/$f" 644; cmp -s -- "$src/$f" "$shared/$f"; done
fi
probe() {
  local output rc=0
  expected='{"status":"rejected","error":"command_denied"}'
  output=$(runuser -u assertion-test-deploy -- env -i SSH_ORIGINAL_COMMAND=receiver-probe     "$wrapper" -c '/usr/bin/python3 -I -B /etc/assertion-release/test/receive.py test' </dev/null) || rc=$?
  [[ $rc == 1 && "$output" == "$expected" ]]
  rc=0
  runuser -u assertion-test-deploy -- env -i "$wrapper" -c id </dev/null >/dev/null 2>&1 || rc=$?
  [[ $rc == 126 ]]
}
# Read-only open and exclusive nonblocking lock prevent entrypoint swap during deploy.
[[ -f "$home/deploy.lock" && ! -L "$home/deploy.lock" ]]
exec {test_lock}<"$home/deploy.lock"
flock -n -x "$test_lock"
probe
if [[ "$mode" == --check ]]; then
  printf '%s\n' 'TEST first adoption preflight passed; unchanged receiver, no live install.'
  exit 0
fi
# Operator must keep two independently verified admin sessions open. Only
# reviewed sources can supply the pinned hashes; no URL or shell input.
[[ -t 0 ]] && read -r -t 60 -p 'Confirm two fresh admin sessions (enter VERIFIED-ADMIN): ' confirmed
[[ "${confirmed:-}" == VERIFIED-ADMIN ]]
staging=$(mktemp -d "$control/.shared-adopt.XXXXXXXX")
install -d -o root -g root -m 0700 -- "$staging/input"
for f in SHA256SUMS "${candidate_files[@]}"; do
  root_file "$src/$f" 644
  install -o root -g root -m 0644 -- "$src/$f" "$staging/input/$f"
  root_file "$staging/input/$f" 644
done
check_hash "$staging/input/SHA256SUMS" "$manifest_hash" || { rm -rf -- "$staging"; exit 1; }
[[ $(awk 'NF==2 {print $2}' "$staging/input/SHA256SUMS" | sort | paste -sd, -) == base_payload.py,receive.py,release_core.py ]]
(cd -- "$staging/input"; sha256sum --check --strict --status SHA256SUMS) || { rm -rf -- "$staging"; exit 1; }
src=$staging/input
prior=$(mktemp "$config/.receive-before-shared.XXXXXXXX")
next=$(mktemp "$config/.receive-next-shared.XXXXXXXX")
[[ "$(stat -c '%d' -- "$next")" == "$(stat -c '%d' -- "$receiver")" ]]
install -o root -g root -m 0644 -- "$receiver" "$prior"
root_file "$prior" 644
initial_keys=$(sha256sum -- "$keys")
initial_hook=$(sha256sum -- "$hook")
shared_created=0
switch_started=0
done_ok=0
cleanup() {
  rc=$?
  trap - EXIT HUP INT TERM
  if (( switch_started && !done_ok )); then
    install -o root -g root -m 0644 -- "$prior" "$next"
    root_file "$next" 644
    mv -Tf -- "$next" "$receiver"
    check_hash "$receiver" "$legacy_sha"
  fi
  if (( shared_created && !done_ok )); then
    # Remove only the unused first-adoption exact files. Never touch a preexisting module directory.
    rm -- "$shared/release_core.py" "$shared/base_payload.py"
    rmdir -- "$shared"
  fi
  rm -f -- "$next"
  rm -rf -- "$staging"
  exit "$rc"
}
trap cleanup EXIT
trap 'exit 99' HUP INT TERM
if [[ ! -d "$shared" ]]; then
  install -d -o root -g root -m 0755 -- "$staging/shared"
  for f in release_core.py base_payload.py; do install -o root -g root -m 0644 -- "$src/$f" "$staging/shared/$f"; done
  mv -T -- "$staging/shared" "$shared"
  shared_created=1
fi
install -o root -g root -m 0644 -- "$src/receive.py" "$next"
root_file "$next" 644
[[ "$(stat -c '%d' -- "$next")" == "$(stat -c '%d' -- "$receiver")" ]]
check_hash "$receiver" "$legacy_sha"
switch_started=1
mv -Tf -- "$next" "$receiver"
root_file "$receiver" 644
cmp -s -- "$src/receive.py" "$receiver"
probe
[[ "$(readlink -- "$home/current")" == "$active" && "$(getent passwd assertion-test-deploy)" == "$entry" ]]
[[ "$(sha256sum -- "$keys")" == "$initial_keys" && "$(sha256sum -- "$hook")" == "$initial_hook" ]]
done_ok=1
printf '%s\n' 'TEST entrypoint changed only; existing TEST archive, credentials, state and hook unchanged.'
printf 'Retain exact rollback entrypoint: %s\n' "$prior"
