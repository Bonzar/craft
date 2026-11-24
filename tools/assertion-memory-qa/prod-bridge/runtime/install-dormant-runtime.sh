#!/usr/bin/env bash
set -Eeuo pipefail
export PATH=/usr/sbin:/usr/bin:/sbin:/bin LC_ALL=C
umask 077
[[ $EUID == 0 && $# == 1 && $1 == --stage-files-only ]]
base=/etc/assertion-release/production
input=$base/bootstrap-review/runtime
[[ -d $input && ! -L $input && $(stat -c '%u:%a' "$input") == 0:700 ]]
[[ -d /etc/systemd/user && ! -L /etc/systemd/user && $(stat -c %u /etc/systemd/user) == 0 ]]
[[ -d $base && ! -L $base && $(stat -c %u "$base") == 0 ]]
for dir in "$base" "$base/bootstrap-review" "$input" /etc/systemd/user; do
  [[ -d $dir && ! -L $dir ]] || exit 31
  read -r owner mode < <(stat -c '%u %a' "$dir")
  [[ $owner == 0 && $mode =~ ^[0-7]{3,4}$ ]] || exit 31
  (( (8#$mode & 0022) == 0 )) || exit 31
done
units=(assertion-production-deploy-mcp.service assertion-production-deploy-graph.service)
sources=("${units[@]}" prod_service_hook.py service-hook.sh test_prod_service_hook.py rehearse-prod-db-shadow.py)
for f in "${sources[@]}"; do
  q=$input/$f
  [[ -f $q && ! -L $q && $(stat -c %u "$q") == 0 ]]
done
(
  cd "$input"
  sha256sum --strict -c <<'SHAS'
8b108c3ba18b700772ae0eabb8cbaec17a51bd62e1d2e7bf1f3fc26e433b3f65  assertion-production-deploy-mcp.service
7a49f3e028e498b388f674666ea1df0b594d6ed712a5334587efcb27fe373524  assertion-production-deploy-graph.service
6b35c0252667ec98e91de49e8acfabef0642c3868a814ac977ae6a279d4f1578  prod_service_hook.py
9801563b96c7e6d3702f5a9d2635691f9c33c5361fff1907fff91efa823aa533  service-hook.sh
db20ad65e621cde277c6fb42f449aeae87c0454cfc0c5c2ccfc0686f625ea4a3  test_prod_service_hook.py
801005dfbc913bbe1223a081c924e893ae91848bbdcaf2f11c7eeb5428b83361  rehearse-prod-db-shadow.py
SHAS
  bash -n service-hook.sh
  /usr/bin/python3 -I -B - <<'PY'
import ast
from pathlib import Path
for f in ('prod_service_hook.py','test_prod_service_hook.py','rehearse-prod-db-shadow.py'):
    ast.parse(Path(f).read_bytes(),filename=f)
PY
)
paths=(/etc/systemd/user/"${units[0]}" /etc/systemd/user/"${units[1]}" "$base/prod_service_hook.py" "$base/service-hook.sh")
modes=(0644 0644 0644 0755)
for p in "${paths[@]}" "$base/application-policy.json" "$base/app-runtime.env" /srv/assertion-release/production/current; do
  [[ ! -e $p && ! -L $p ]] || { echo 'Unexpected existing application runtime; no overwrite'; exit 31; }
done
[[ $(getent passwd assertion-production-deploy | cut -d: -f6) == /srv/assertion-release/production ]]
installed=()
pending=''
restore() {
  local code=$? p
  trap - ERR
  if [[ -n $pending ]]; then rm -f -- "$pending"; fi
  for p in "${installed[@]}"; do
    if [[ -f $p && ! -L $p ]] && cmp -s "$p" "$input/${p##*/}"; then rm -- "$p"; fi
  done
  exit "$code"
}
trap restore ERR
for i in "${!paths[@]}"; do
  p=${paths[$i]}
  # Stage adjacent then publish with no overwrite; crossing a destination symlink fails.
  tmp=$(mktemp "${p%/*}/.assertion-runtime.XXXXXXXX")
  pending=$tmp
  install -o root -g root -m "${modes[$i]}" "$input/${p##*/}" "$tmp"
  if ! cmp -s "$tmp" "$input/${p##*/}" || ! ln -T -- "$tmp" "$p"; then
    rm -f -- "$tmp"
    false
  fi
  installed+=("$p")
  rm -- "$tmp"
  pending=''
  cmp -s "$p" "$input/${p##*/}"
done
trap - ERR
echo 'Exact four scoped runtime source files installed dormant. No unit reload/start/enable, no config, router, or database changes.'
