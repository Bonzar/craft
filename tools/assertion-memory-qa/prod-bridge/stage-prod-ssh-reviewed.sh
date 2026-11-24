#!/usr/bin/env bash
# Prepare/verify only by default. Install/reload solely under explicit operator action.
set -Eeuo pipefail
export PATH=/usr/sbin:/usr/bin:/sbin:/bin LC_ALL=C
umask 077
[[ $EUID == 0 && $# == 2 ]]
mode=$1
[[ $mode == --preview || $mode == --apply-reviewed || $mode == --rollback-reviewed ]]
scope=$2
[[ $scope =~ ^/root/[A-Za-z0-9_-]{1,80}$ && -d $scope && ! -L $scope ]]
[[ $(stat -c '%u:%a' "$scope") == 0:700 ]]
root_input() {
  [[ -f $1 && ! -L $1 && $(stat -c '%u:%a' "$1") == 0:600 ]]
}
key=$scope/production.authorized_keys.candidate
fragment=$scope/sshd-dropin.candidate
contexts=$scope/connection-contexts.tsv
expected_fingerprint=$scope/approved-key-fingerprint
for f in "$key" "$fragment" "$contexts" "$expected_fingerprint"; do root_input "$f"; done
conf=/etc/ssh/sshd_config
included=/etc/ssh/sshd_config.d
base=99-assertion-memory-production.conf
live_match=$included/$base
live_key=/etc/ssh/assertion-release/production.authorized_keys
nologin=/usr/sbin/nologin
login=/etc/assertion-release/production/receiver-login
target=assertion-production-deploy
[[ -f $conf && ! -L $conf && -d $included && ! -L $included && -d ${live_key%/*} && ! -L ${live_key%/*} ]]
[[ -f $login && ! -L $login && $(stat -c '%u:%a' "$login") == 0:755 ]]
[[ -S /run/assertion-release-authorizer/production.sock ]]
tmp=$(mktemp -d /run/base-prod-ssh-review.XXXXXXXX)
trap 'rm -rf -- "$tmp"' EXIT
cat > "$tmp/expected" <<'MATCH'
Match User assertion-production-deploy
    AuthenticationMethods publickey
    PubkeyAuthentication yes
    PasswordAuthentication no
    KbdInteractiveAuthentication no
    AuthorizedKeysFile /etc/ssh/assertion-release/production.authorized_keys
    ForceCommand /usr/bin/python3 -I -B /etc/assertion-release/production/receive-authorized-entry.py production
    DisableForwarding yes
    PermitTTY no
    PermitUserRC no
Match all
MATCH
cmp -s "$fragment" "$tmp/expected"
[[ $(wc -l < "$key") == 1 ]]
awk 'NF == 3 && $1 == "restrict" && ($2 == "ssh-ed25519" || $2 == "ssh-rsa") && $3 ~ /^[A-Za-z0-9+\/=]+$/ {print $2,$3; ok=1} END {if (!ok) exit 1}' "$key" >"$tmp/key.pub"
fp=$(ssh-keygen -E sha256 -lf "$tmp/key.pub" | awk '{print $2}')
[[ $(wc -l < "$expected_fingerprint") == 1 && $fp == "$(cat "$expected_fingerprint")" ]]
user_shell() { getent passwd "$target" | cut -d: -f7; }
svc() { systemctl reload ssh.service; }
if [[ $mode == --rollback-reviewed ]]; then
  [[ $(user_shell) == "$login" && -f $live_match && ! -L $live_match && -f $live_key && ! -L $live_key ]]
  cmp -s "$live_match" "$fragment" && cmp -s "$live_key" "$key"
  usermod --shell "$nologin" "$target"
  rm -- "$live_match" "$live_key"
  sshd -t && svc
  echo 'Scoped PROD SSH changes reverted; no application, database, or TEST changes.'
  exit
fi
[[ $(user_shell) == "$nologin" ]]
[[ ! -e $live_match && ! -L $live_match && ! -e $live_key && ! -L $live_key ]]
systemctl is-active --quiet ssh.service
sshd -t
mkdir "$tmp/includes"
shopt -s nullglob
for p in "$included/"*.conf; do
  [[ -f $p && ! -L $p && $(stat -c %u "$p") == 0 ]]
  ! grep -Eiq '^[[:space:]]*Include[[:space:]]' "$p"
  cp -- "$p" "$tmp/includes/"
done
# Refuse nonstandard include topology instead of guessing at parser behavior.
awk -v d="$tmp/includes" '
  tolower($1)=="include" {
    if(NF!=2 || $2!="/etc/ssh/sshd_config.d/*.conf") exit 2
    print "Include " d "/*.conf"; seen++; next
  }
  {print}
  END {if(seen!=1) exit 3}
' "$conf" >"$tmp/sshd"
cp -- "$fragment" "$tmp/includes/$base"
sshd -t -f "$tmp/sshd"
declare -A seen=()
while IFS=$'\t' read -r label user host addr laddr lport extra; do
  [[ $label =~ ^(admin|test|visual_test|visual_prod|prod)$ && -z ${seen[$label]-} && -z ${extra-} ]]
  seen[$label]=1
  [[ $user =~ ^[A-Za-z0-9_-]{1,80}$ && $host =~ ^[A-Za-z0-9.-]{1,253}$ && $lport =~ ^[0-9]{1,5}$ ]]
  python3 -I -c 'import ipaddress,sys; [ipaddress.ip_address(s) for s in sys.argv[1:]]' "$addr" "$laddr"
  params="user=$user,host=$host,addr=$addr,laddr=$laddr,lport=$lport"
  sshd -T -C "$params" >"$tmp/$label.old"
  sshd -T -f "$tmp/sshd" -C "$params" >"$tmp/$label.new"
  case "$label" in
    test) [[ $user == assertion-test-deploy ]];;
    visual_test) [[ $user == assertion-visual-test-deploy ]];;
    visual_prod) [[ $user == assertion-visual-prod-deploy ]];;
    admin) [[ $user != assertion*-deploy ]];;
  esac
  if [[ $label == prod ]]; then
    [[ $user == "$target" ]]
    for line in 'authenticationmethods publickey' "authorizedkeysfile $live_key" \
      'forcecommand /usr/bin/python3 -I -B /etc/assertion-release/production/receive-authorized-entry.py production' \
      'disableforwarding yes' 'permittty no' 'permituserrc no' 'passwordauthentication no' \
      'kbdinteractiveauthentication no' 'pubkeyauthentication yes'; do
      grep -Fxq "$line" "$tmp/$label.new"
    done
  else
    [[ $user != "$target" ]]
    cmp -s "$tmp/$label.old" "$tmp/$label.new"
  fi
done <"$contexts"
for label in admin test visual_test visual_prod prod; do [[ ${seen[$label]-} == 1 ]]; done
if [[ $mode == --preview ]]; then
  echo 'Exact candidate syntax, admin/TEST/visual parity and restricted PROD effective settings verified; no live changes.'
  exit
fi
# After preview, an operator must personally verify two still-live independent admin sessions.
root_input "$scope/two-admin-sessions-confirmed"
[[ $(cat "$scope/two-admin-sessions-confirmed") == yes ]]
rollback_needed=0
rollback() {
  local status=$?
  trap - ERR
  if (( rollback_needed )); then
    usermod --shell "$nologin" "$target" || true
    if [[ -f $live_match && ! -L $live_match ]] && cmp -s "$live_match" "$fragment"; then rm -- "$live_match"; fi
    if [[ -f $live_key && ! -L $live_key ]] && cmp -s "$live_key" "$key"; then rm -- "$live_key"; fi
    if sshd -t; then svc || true; fi
  fi
  exit "$status"
}
trap rollback ERR
rollback_needed=1
install -o root -g root -m 0644 -- "$key" "$live_key"
install -o root -g root -m 0644 -- "$fragment" "$live_match"
usermod --shell "$login" "$target"
sshd -t
while IFS=$'\t' read -r label user host addr laddr lport extra; do
  sshd -T -C "user=$user,host=$host,addr=$addr,laddr=$laddr,lport=$lport" >"$tmp/$label.installed"
  cmp -s "$tmp/$label.installed" "$tmp/$label.new"
done < "$contexts"
svc
systemctl is-active --quiet ssh.service
rollback_needed=0
echo 'Restricted PROD SSH config reloaded only. Confirm a new admin session and TEST path before proceeding; rollback mode is available.'
