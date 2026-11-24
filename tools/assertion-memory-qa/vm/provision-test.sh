#!/bin/bash
set -euo pipefail
umask 077
[[ "$EUID" == 0 && $# -le 1 ]]
name=assertion-test-deploy
root=/srv/assertion-release/test
config=/etc/assertion-release/test
if command -v ss >/dev/null && ss -H -ltn '( sport = :8891 or sport = :8893 )' | grep -q .; then
  echo 'TEST ports already occupied. Leaving existing processes untouched.' >&2
  exit 2
fi
[[ ! -e "$root" && ! -L "$root" && ! -e "$config" && ! -L "$config" ]]
! getent passwd "$name" >/dev/null
[[ -x /usr/bin/python3 ]] && /usr/bin/python3 -c 'import venv,ensurepip' >/dev/null
if [[ $# == 1 ]]; then
  pubkey_file=$1
  [[ -f "$pubkey_file" ]]
  IFS= read -r key < "$pubkey_file"
  [[ "$key" =~ ^ssh-ed25519\ [A-Za-z0-9+/=]{60,120}(\ [A-Za-z0-9_@.+-]{1,128})?$ ]]
  ssh-keygen -lf "$pubkey_file" >/dev/null
  [[ $(wc -l < "$pubkey_file") == 1 ]]
fi
runtime_source="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
shared_source="$runtime_source/../../assertion-memory-release"
shared_target=/etc/assertion-release/shared
for file in "$runtime_source/receive.py" "$runtime_source/test-service-hook.sh" "$runtime_source/test-health.py" "$shared_source/release_core.py" "$shared_source/base_payload.py"; do
  [[ -f "$file" && ! -L "$file" ]]
done
if [[ -e "$shared_target" || -L "$shared_target" ]]; then
  [[ -d "$shared_target" && ! -L "$shared_target" && $(stat -c '%u:%g:%a' -- "$shared_target") == 0:0:755 ]]
  for file in release_core.py base_payload.py; do
    [[ ! -L "$shared_target/$file" && $(stat -c '%u:%g:%a' -- "$shared_target/$file") == 0:0:644 ]]
    cmp -s -- "$shared_source/$file" "$shared_target/$file"
  done
fi
install -d -m 0755 /srv/assertion-release /etc/assertion-release
useradd --system --user-group --home-dir "$root" --shell /bin/bash "$name"
install -d -m 0700 -o "$name" -g "$name" "$root" "$root/releases" "$root/state"
install -d -m 0755 "$config"
install -o root -g root -m 0644 -- "$runtime_source/receive.py" "$config/receive.py"
install -o root -g root -m 0644 -- "$runtime_source/test-service-hook.sh" "$config/service-hook.sh"
install -o root -g root -m 0644 -- "$runtime_source/test-health.py" "$config/test-health.py"
if [[ ! -d "$shared_target" ]]; then
  install -d -o root -g root -m 0755 -- "$shared_target"
  for file in release_core.py base_payload.py; do
    install -o root -g root -m 0644 -- "$shared_source/$file" "$shared_target/$file"
  done
fi
chmod 0644 "$config/receive.py"
chmod 0755 "$config/service-hook.sh"
if [[ $# == 1 ]]; then
  install -d -m 0700 -o "$name" -g "$name" "$root/.ssh"
  printf 'restrict,command="/usr/bin/python3 -B /etc/assertion-release/test/receive.py test" %s\n' "$key" > "$root/.ssh/authorized_keys"
  chown "$name:$name" "$root/.ssh/authorized_keys"
fi
install -d -m 0700 -o "$name" -g "$name" "$root/.config" "$root/.config/systemd" "$root/.config/systemd/user"
/usr/bin/python3 - "$root/state" <<'PYTHON'
import json, pathlib, secrets, sys
state=pathlib.Path(sys.argv[1])
for key in ('mcp-token','graph-password'):
    (state/key).write_text(secrets.token_urlsafe(40)+'\n')
(state/'http.json').write_text(json.dumps({'service':{'db':str(state/'fictional.sqlite3'),
 'provider':'none','embedding_mode':'lexical','processing_session_ids':[]},
 'public_host':'localhost','port':8893,'token_file':str(state/'mcp-token')}))
PYTHON
chown -R "$name:$name" "$root/state"
cat > "$root/.config/systemd/user/assertion-test-deploy-graph.service" <<'UNIT'
[Unit]
Description=Assertion Memory TEST graph (isolated TEST database)
[Service]
Type=simple
UMask=0077
WorkingDirectory=%h/current
NoNewPrivileges=yes
Restart=on-failure
ExecStart=%h/current/venv/bin/python -B -m apps.graph_viewer.server --source %h/state/fictional.sqlite3 --state-dir %h/state/graph-state --password-file %h/state/graph-password --static-dir %h/current/apps/graph_viewer/web/dist --port 8891
UNIT
cat > "$root/.config/systemd/user/assertion-test-deploy-mcp.service" <<'UNIT'
[Unit]
Description=Assertion Memory TEST MCP (isolated TEST database)
[Service]
Type=simple
UMask=0077
WorkingDirectory=%h/current
NoNewPrivileges=yes
Restart=on-failure
ExecStart=%h/current/venv/bin/python -B -m prototypes.assertion_memory.http_server --config %h/state/http.json
UNIT
# Root-owned files must still be readable to the unprivileged user manager.
chmod 0644 "$root/.config/systemd/user/"*.service
# No production systemd service, data, port, DNS, or nginx modifications.
loginctl enable-linger "$name"
id=$(id -u "$name")
systemctl start "user@$id.service"
runuser -u "$name" -- env XDG_RUNTIME_DIR="/run/user/$id" systemctl --user daemon-reload
echo 'TEST provisioned; submit approved archive as assertion-test-deploy (no production changes).'
