#!/usr/bin/env bash
# SessionStart hook: (re)build craft-sync into the container's PATH.
# The container is ephemeral, but this repo is cloned fresh each session,
# so building on start keeps the binary available without committing a
# platform-specific binary blob. Exit 0 on any failure so a broken build
# can never wedge session startup.
#
# Opt-in: builds only when CRAFT_SYNC_BUILD=1 is set in the environment
# settings — most sessions don't run craft-sync, no point paying the build
# on every start. Manual build in any session:
#   bash .claude/hooks/build-craft-sync.sh --force
set -u
log(){ echo "[build-craft-sync] $*" >&2; }

# Load repo .env so CRAFT_SYNC_BUILD is available (Claude Code doesn't do it).
. "$(dirname "${BASH_SOURCE[0]}")/_load-env.sh"

if [[ "${1:-}" != "--force" && "${CRAFT_SYNC_BUILD:-0}" != "1" ]]; then
  log "CRAFT_SYNC_BUILD != 1; skipping build (run with --force to build now)"
  exit 0
fi

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="${CLAUDE_PROJECT_DIR:-$(cd "$here/../.." && pwd)}"
src="$repo/craft-sync"

command -v go >/dev/null 2>&1 || { log "go not found; skipping"; exit 0; }
[[ -f "$src/main.go" ]] || { log "source not found at $src; skipping"; exit 0; }

out="${CRAFT_SYNC_BIN:-$HOME/.local/bin/craft-sync}"
mkdir -p "$(dirname "$out")"
if (cd "$src" && CGO_ENABLED=0 go build -ldflags="-s -w" -o "$out" .); then
  log "built $out"
else
  log "build failed (non-fatal)"
fi
exit 0
