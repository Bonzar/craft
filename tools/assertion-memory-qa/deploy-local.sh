#!/usr/bin/env bash
set -euo pipefail
umask 077
[[ "$(id -un)" == assertion-qa && "$(id -u)" != 0 ]] || { echo 'Run under the provisioned, non-root assertion-qa account only.' >&2; exit 1; }
[[ $# == 3 ]] || { echo 'usage: deploy-local.sh /absolute/path/to/approved.tar.gz SHA256 GIT_SHA' >&2; exit 2; }
[[ "$1" == /* && -f "$1" && "$2" =~ ^[a-f0-9]{64}$ && "$3" =~ ^[a-f0-9]{40}$ ]]
here="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
root="$HOME/.local/share/assertion-memory-ci-qa"
mkdir -p -- "$root/releases"
[[ ! -L "$root" && ! -L "$root/releases" && "$(stat -c %a "$root")" == 700 ]]
exec 9>"$root/deploy.lock"
flock -n 9 || { echo 'Another QA deployment is running.' >&2; exit 1; }
units=(assertion-memory-ci-qa-graph.service assertion-memory-ci-qa-mcp.service)
for u in "${units[@]}"; do
  [[ "$(systemctl --user show -P FragmentPath "$u")" == "$HOME/.config/systemd/user/$u" ]] || { echo "QA-only service not provisioned: $u" >&2; exit 1; }
done
current="$root/current"
previous="$(readlink -- "$current" || true)"
if [[ -e "$current" || -L "$current" ]]; then
  [[ -L "$current" && "$previous" =~ ^releases/[a-f0-9]{40}$ && -d "$root/$previous" ]]
fi
release="$root/releases/$3"
[[ ! -e "$release" && ! -L "$release" ]] || { echo 'Release already staged; use manual rollback/recheck.' >&2; exit 1; }
mkdir -m 700 -- "$release"
python3 "$here/verify-unpack.py" "$1" "$release" "$2" "$3"
python3 -m venv "$release/venv"
python="$release/venv/bin/python"
"$python" -m pip install --disable-pip-version-check --only-binary=:all: -r "$release/prototypes/assertion_memory/requirements-mcp.txt" -r "$release/requirements-graph-lexical.txt"
mkdir -m 700 "$release/private"
(
  cd "$release"
  export PYTHONPATH="$release"
  "$python" "$here/synthetic_fixture.py" "$release/private/fictional.sqlite3"
  "$python" "$here/smoke_local.py" "$release/private/fictional.sqlite3" "$release/apps/graph_viewer/web/dist"
)
"$python" - "$release/private" <<'PY'
import json, pathlib, secrets, sys
d=pathlib.Path(sys.argv[1])
for key in ('mcp-token','graph-password'):
    (d/key).write_text(secrets.token_urlsafe(40)+'\n')
(d/'http.json').write_text(json.dumps({
  'service':{'db':str(d/'fictional.sqlite3'),'provider':'none','embedding_mode':'lexical','processing_session_ids':[]},
  'public_host':'localhost','port':8893,'token_file':str(d/'mcp-token')}))
PY
link_to() {
  local link="$root/.current.$BASHPID"
  ln -s -- "$1" "$link" && mv -Tf -- "$link" "$current"
}
rollback() {
  echo 'QA smoke failed: reverting pointer and services.' >&2
  systemctl --user stop "${units[@]}" || true
  if [[ -n "$previous" ]]; then
    link_to "$previous"
    systemctl --user start "${units[@]}"
  else
    test "$(readlink -- "$current" || true)" = "releases/$3" && rm -- "$current"
  fi
}
systemctl --user stop "${units[@]}"
if ! link_to "releases/$3"; then rollback; exit 1; fi
if ! systemctl --user start "${units[@]}"; then rollback; exit 1; fi
for attempt in 1 2 3 4 5; do
  if "$python" "$here/live-smoke.py" "$release/private" >"$release/private/live-smoke.log" 2>&1; then
    cat "$release/private/live-smoke.log"
    printf 'QA active: %s\n' "$3"
    exit 0
  fi
  sleep 1
done
rollback
exit 1
