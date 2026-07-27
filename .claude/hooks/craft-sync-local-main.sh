#!/usr/bin/env bash
# SessionStart hook (LOCAL ONLY): fast-forward the local `main` ref to
# origin/main. Claude Desktop cuts each session's per-session worktree from
# local `main`, so keeping main current makes every new session start on fresh
# upstream code. A SessionStart hook runs *inside* the already-cut worktree, so
# it can't rebase the current session — it primes main for the next ones.
#
# Local gate: CRAFT_LOCAL=1, set only in the repo `.env` (loaded below). `.env`
# is gitignored and exists only in local checkouts; cloud sessions clone from
# git and inject vars through the environment settings, so CRAFT_LOCAL is unset
# there and this returns before touching anything — cloud is untouched.
# CRAFT_API_BASE can't be the marker: cloud sets it too.
#
# `git fetch origin main:main` only advances the main *ref* by fast-forward; it
# never touches the current worktree's working tree, and it refuses (harmless)
# when it wouldn't be a fast-forward or when main is checked out somewhere.
# Never wedges startup: any failure logs and exits 0.
set -u
log(){ echo "[sync-local-main] $*" >&2; }

# Load repo .env so CRAFT_LOCAL (the local-session marker) becomes available.
. "$(dirname "$0")/_load-env.sh"

if [[ "${CRAFT_LOCAL:-}" != "1" ]]; then
  log "CRAFT_LOCAL != 1 (not a local session); skipping main sync"
  exit 0
fi

root="${CLAUDE_PROJECT_DIR:-$(cd "$(dirname "$0")/../.." && pwd)}"
if git -C "$root" fetch --quiet origin main:main 2>/dev/null; then
  log "local main -> origin/main ($(git -C "$root" rev-parse --short main 2>/dev/null))"
else
  log "main sync skipped (offline / non-ff / main checked out) — non-fatal"
fi
exit 0
