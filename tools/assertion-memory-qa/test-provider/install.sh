#!/bin/bash
# Verify or stage only. Never starts a unit, changes routing, or handles secret content.
set -Eeuo pipefail
export PATH=/usr/sbin:/usr/bin:/sbin:/bin LC_ALL=C
umask 077
usage(){ echo "usage: $0 --check | --stage"; exit 64; }
[[ $# == 1 ]] || usage
mode=$1
[[ $mode == --check || $mode == --stage ]] || usage
origin=$(dirname -- "$(realpath -e -- "$0")")
provider_sha=2d1e150c5c2664e38c18cfa4ac8dd094e81252afbae8ebac569e8263e36faa9a
unit_sha=268a4cf2fbf860d039031a5036c3a4d5ac0202ad45dbf4c8481efe44b9fc5e99
unit=assertion-memory-test-proof.service
conf=/etc/assertion-release/test-proof
unitdir=/etc/systemd/system
die(){ echo "DENIED: $*" >&2; exit 1; }
member(){
  [[ -f $1 && ! -L $1 && $(realpath -e -- "$1") = "$1" ]] &&
  [[ $(sha256sum -- "$1" | cut -d ' ' -f1) = "$2" ]]
}
rootdir(){
  [[ -d $1 && ! -L $1 && $(realpath -e -- "$1") = "$1" && $(stat -c '%u:%g' -- "$1") = 0:0 ]] ||
    die 'unexpected destination directory owner or type'
  local mode
  mode=$(stat -c %a -- "$1")
  (( !(8#$mode & 8#022) )) || die 'destination directory is not root-controlled'
}
member "$origin/provider.py" "$provider_sha" || die 'provider hash mismatch'
member "$origin/$unit" "$unit_sha" || die 'unit hash mismatch'
python3 -I -B - "$origin/provider.py" <<'PY'
import ast,pathlib,sys
ast.parse(pathlib.Path(sys.argv[1]).read_text(encoding='utf-8'))
PY
if [[ $mode == --check ]]; then
   echo 'PASS: pinned TEST provider + sandbox unit source verified; nothing installed or started.'
   exit
fi
[[ $EUID = 0 ]] || die 'staging requires root'
# Package must first be verified into its own root-owned inaccessible scratch dir.
rootdir "$origin"
for file in "$origin/provider.py" "$origin/$unit"; do
  [[ $(stat -c '%u:%g:%a' -- "$file") = 0:0:600 ]] || die 'stage members must be root-owned mode 0600'
done
rootdir /etc/assertion-release
rootdir "$unitdir"
[[ $(getent passwd assertion-test-deploy | cut -d: -f6) = /srv/assertion-release/test ]] || die 'unexpected TEST account'
getent group assertion-test-deploy >/dev/null || die 'missing TEST group'
[[ ! -e $conf && ! -L $conf && ! -e $unitdir/$unit && ! -L $unitdir/$unit ]] || die 'existing paths require separate review'
systemd-analyze verify "$origin/$unit"
if ss -H -ltn '( sport = :8831 )' | grep -q .; then die 'TEST loopback port is not free'; fi
made=0
pending=
cleanup(){
  local rc=$?
  [[ -z $pending ]] || rm -f -- "$pending"
  if ((rc && made)); then
    rm -f -- "$conf/provider.py"
    rmdir -- "$conf"
  fi
  exit "$rc"
}
trap cleanup EXIT
install -d -o root -g root -m 0755 -- "$conf"
made=1
install -o root -g root -m 0644 -- "$origin/provider.py" "$conf/provider.py"
member "$conf/provider.py" "$provider_sha" || die 'installed source hash mismatch'
pending=$(mktemp "$unitdir/.assertion-memory-test-proof.XXXXXXXX")
install -o root -g root -m 0644 -- "$origin/$unit" "$pending"
member "$pending" "$unit_sha" || die 'staged unit hash mismatch'
ln -- "$pending" "$unitdir/$unit"
echo 'Staged only; no daemon-reload, credentials, service start, release movement, or routing performed.'
