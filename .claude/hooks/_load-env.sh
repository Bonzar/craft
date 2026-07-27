#!/usr/bin/env bash
# Sourced (not executed) by the SessionStart hooks to load the repo `.env` into
# the hook's environment. Claude Code does NOT auto-load `.env` for hooks, so
# without this CRAFT_API_BASE / CRAFT_LINKS_STORE / CRAFT_SYNC_BUILD stay unset
# and the fetch hooks silently skip ("CRAFT_API_BASE not set").
#
# `.env` is gitignored (holds the connect-link token) and lives in the main
# checkout root. This resolves it both there and from any git worktree (where
# the untracked `.env` is absent — the shared git-common-dir's parent is the
# main checkout). No-op when `.env` is absent (e.g. cloud sessions that inject
# these vars through the environment directly). Never exits the caller.
#
# Usage, right after `set -u` in a hook:  . "$(dirname "$0")/_load-env.sh"

_le_self="$(realpath "${BASH_SOURCE[0]}" 2>/dev/null || echo "${BASH_SOURCE[0]}")"
_le_root="${CLAUDE_PROJECT_DIR:-$(cd "$(dirname "$_le_self")/../.." && pwd)}"
_le_env="$_le_root/.env"
if [[ ! -f "$_le_env" ]]; then
  _le_common="$(git -C "$_le_root" rev-parse --path-format=absolute --git-common-dir 2>/dev/null || true)"
  [[ -n "$_le_common" ]] && _le_env="$(dirname "$_le_common")/.env"
fi
if [[ -f "$_le_env" ]]; then
  set -a
  # shellcheck disable=SC1090
  . "$_le_env"
  set +a
fi
# Outside the craft repo there is no `.env`: universal hooks installed into
# ~/.claude run in arbitrary sessions and take the Craft connect credentials
# from ~/.claude/craft.env instead (created by install.sh, chmod 600).
if [[ -z "${CRAFT_API_BASE:-}" && -f "$HOME/.claude/craft.env" ]]; then
  set -a
  # shellcheck disable=SC1090
  . "$HOME/.claude/craft.env"
  set +a
fi
unset _le_self _le_root _le_env _le_common
