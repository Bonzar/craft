#!/usr/bin/env bash
set -Eeuo pipefail
export PATH=/usr/sbin:/usr/bin:/sbin:/bin LC_ALL=C
umask 077
[[ $EUID == 0 && ${1-} == --stage-dormant && $# == 1 ]]
prefix=/etc/assertion-release
conf=$prefix/production
root=/srv/assertion-release/production
deploy_user=assertion-production-deploy
auth_user=assertion-release-auth
nologin=/usr/sbin/nologin
pin=a3afb72811483ddef20eb6371213e64bf9505424
url=https://raw.githubusercontent.com/Bonzar/craft/$pin
scope=safe-base-prod-dormant-v1
[[ -x $nologin && -d $prefix && ! -L $prefix && -d /srv/assertion-release && ! -L /srv/assertion-release ]]
root_mode_ok() {
  local owner mode
  read -r owner mode < <(stat -c '%u %a' "$1")
  [[ $owner == 0 && $mode =~ ^[0-7]{3,4}$ ]] && (( (8#$mode & 0022) == 0 ))
}
root_mode_ok "$prefix"
root_mode_ok /srv/assertion-release
for p in "$conf" "$root" /etc/ssh/assertion-release/production.authorized_keys \
  /run/assertion-release-authorizer/production.sock \
  /etc/systemd/system/assertion-memory-prod-auth.service \
  /etc/systemd/system/assertion-memory-prod-auth.socket /var/lib/assertion-release-auth; do
  [[ ! -e $p && ! -L $p ]] || { echo "$scope: unexpected existing production component"; exit 21; }
done
for name in "$deploy_user" "$auth_user"; do
  ! getent passwd "$name" >/dev/null && ! getent group "$name" >/dev/null \
    || { echo "$scope: unexpected existing production identity"; exit 22; }
done
/usr/bin/python3 -I -B -c 'import cryptography'
shared=$prefix/shared
[[ -d $shared && ! -L $shared ]] && root_mode_ok "$shared"
for f in release_core.py base_payload.py; do
  [[ -f $shared/$f && ! -L $shared/$f ]] && root_mode_ok "$shared/$f"
done
( cd "$shared"; sha256sum -c --status <<'SHAS'
3b861435d934c9571bb1ae2a57505ccf5d040df1d09d0e12a3efe76501d41f6a  base_payload.py
c34cbc0ddf79064f694046299d3a3f20d2ed9370d6f5eec15ab1da36941a0b7c  release_core.py
SHAS
) || { echo "$scope: shared source drift; no changes"; exit 23; }
stage=$(mktemp -d "$prefix/.base-prod-review.XXXXXXXX")
trap 'rm -rf -- "$stage"' EXIT
download() {
  local source=$1 file=$2 sha=$3
  curl --disable --fail --silent --show-error --proto =https --connect-timeout 8 --max-time 25 \
       --output "$stage/$file" "$url/tools/assertion-memory-qa/prod-bridge/$source"
  printf '%s  %s\n' "$sha" "$stage/$file" | sha256sum -c --status
}
download authorization-entry.py authorization-entry.py ae21954558fb6aaa6be02cfb9ae9cd02eef3097eee9ec5dedeb928626fabaa35
download authorizer.py authorizer.py d7df3886ad56065f41c71d8610017a8350fd744ec46f7bdf0f854448f4ed4310
download receive-authorized-entry.py receive-authorized-entry.py a3bd518f2da4c9ad49f56c22195349cc810ab7f1c714d809d83d4db1f19fdce0
download receive_authorized.py receive_authorized.py 03d9941e4af55716047f590db8a4c091051720a8054452632254b57959c83720
download receiver-login.py receiver-login bc100111d4440fd4fc040dc27162d852d29b5ae2dd7d639ac7e120193f603271
download receiver_gate.py receiver_gate.py 8ca5b2be6e38fc553aea31ec3c63f858996999de46615807d2c9f7ffe388945b
download reusable_proof.py reusable_proof.py 08772c5d50d6fdf445668ea3dd50f76c1ce3405ae58733db16b505c53347d934
download assertion-memory-prod-auth.service assertion-memory-prod-auth.service 6779909389058781750ed131cbf3dde51d4944d0272970f31df9406a74bff03a
download assertion-memory-prod-auth.socket assertion-memory-prod-auth.socket bb6b52a67b1a526f23d489db39898c2df2cf0e774bc19dbcad5cc6147723c369
# All downloaded bytes qualify before any persistent changes. Do not trust partial setup.
[[ ! -e $conf && ! -L $conf && ! -e $root && ! -L $root ]]
install -d -o root -g root -m 0755 "$conf" "$conf/authorization"
install -d -o root -g root -m 0700 "$conf/bootstrap-review"
printf '%s\n' "$pin" >"$conf/bootstrap-review/source-pin"
for f in authorization-entry.py receive-authorized-entry.py; do install -o root -g root -m 0644 "$stage/$f" "$conf/$f"; done
install -o root -g root -m 0755 "$stage/receiver-login" "$conf/receiver-login"
for f in authorizer.py reusable_proof.py receiver_gate.py receive_authorized.py; do
  install -o root -g root -m 0644 "$stage/$f" "$conf/authorization/$f"
done
for f in assertion-memory-prod-auth.service assertion-memory-prod-auth.socket; do
  install -o root -g root -m 0600 "$stage/$f" "$conf/bootstrap-review/$f"
done
useradd --system --user-group --home-dir "$root" --no-create-home --shell "$nologin" "$deploy_user"
useradd --system --user-group --home-dir /nonexistent --no-create-home --shell "$nologin" "$auth_user"
install -d -o "$deploy_user" -g "$deploy_user" -m 0710 "$root"
install -d -o "$deploy_user" -g "$deploy_user" -m 0700 "$root/releases" "$root/state"
printf '%s\n' 'Base production source and nologin identities staged; no systemd units enabled, no sshd edits, no route or data changes.'
