#!/usr/bin/env bash
# Доставка демона ввода на мак и перезапуск его задания launchd.
set -u
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CM="${DB_SSH_CTL:-/tmp/sshcm}"
mkdir -p "$CM"
# Адрес узла — из окружения: в репозитории его нет, это персональные данные Влада.
_env_helper="$(cd "$DIR/../.." && pwd)/.claude/hooks/_load-env.sh"
[[ -f "$_env_helper" ]] && . "$_env_helper"
[[ -n "${MAC_NODE_ADDR:-}" ]] || { echo "нет MAC_NODE_ADDR в окружении — tailnet-адрес мака кладётся в настройки облачного окружения"; exit 1; }
HOST="agent@$MAC_NODE_ADDR"

OPTS=(-o "ProxyCommand=nc -X 5 -x 127.0.0.1:1055 %h %p"
      -o "ControlMaster=auto" -o "ControlPath=$CM/%r@%h:%p" -o "ControlPersist=10m"
      -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null
      -o LogLevel=ERROR -o ConnectTimeout=25)

ssh "${OPTS[@]}" "$HOST" 'cat > /Users/agent/osdaemon.py' < "$DIR/osdaemon.py" || exit 1
ssh "${OPTS[@]}" "$HOST" '
  /usr/bin/python3 -c "import ast,sys; ast.parse(open(\"/Users/agent/osdaemon.py\").read())" || exit 1
  launchctl kickstart -k gui/$(id -u)/do.craft.osinput
  sleep 1.5
  tail -3 ~/.dev-browser/tmp/osin.log
'
