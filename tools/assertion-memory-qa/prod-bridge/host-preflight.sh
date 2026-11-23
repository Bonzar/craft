#!/usr/bin/env bash
# Read-only qualification. Run from existing authorized VM admin context; no changes.
set -Eeuo pipefail
export PATH=/usr/sbin:/usr/bin:/sbin:/bin LC_ALL=C
[[ ${EUID} == 0 && $# == 2 ]] || { echo 'Usage: sudo host-preflight.sh <actual-client-ip> <actual-server-ip>'; exit 2; }
read -r client server < <(python3 -c 'import ipaddress,sys; print(*(ipaddress.ip_address(x) for x in sys.argv[1:]))' "$@")
sshd -t
systemctl is-active ssh.service
for u in bonzarrr assertion-test-deploy assertion-production-deploy; do
  if getent passwd "$u" >/dev/null; then
    printf 'Account %s present: ' "$u"
    getent passwd "$u" | awk -F: '{print "uid="$3,"home="$6,"shell="$7}'
    sshd -T -C "user=$u,host=$client,addr=$client,laddr=$server,lport=22" |
      awk '/^(authenticationmethods|authorizedkeysfile|forcecommand|permittty|disableforwarding|permituserrc) /'
  else
    printf 'Account %s absent\n' "$u"
  fi
done
for p in /etc/assertion-release/production /etc/assertion-release/production/receive.py \
 /etc/assertion-release/production/receiver-login /etc/assertion-release/production/service-hook.sh \
 /etc/assertion-release/production/allow-activation /etc/ssh/assertion-release/production.authorized_keys \
 /srv/assertion-release/production /srv/assertion-release/production/current \
 /run/assertion-release-authorizer/production.sock; do
  if [[ -e "$p" || -L "$p" ]]; then
    stat -c 'present %n type=%F owner=%u:%g mode=%a' -- "$p"
  else
    printf 'absent %s\n' "$p"
  fi
done
ssh-keygen -E sha256 -lf /etc/ssh/ssh_host_ed25519_key.pub | awk '{print "host_ed25519_fingerprint=" $2}'
printf '%s\n' 'No application data, tokens, private keys or authorization file contents read.'
