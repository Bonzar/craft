#!/usr/bin/env bash
# SessionStart hook: cache the Craft "Разбор инцидента" SKILL doc locally so the
# incident detector (detect-incident.sh, UserPromptSubmit) can inject its live
# body the moment an incident signal appears — without a network call per
# message. Mirrors inject-craft-router.sh: fetch via connect-API, write to a
# file, and fail quiet so a broken network never wedges session startup; a stale
# previously-written file (if any) is left in place as a fallback.
#
# Requires CRAFT_API_BASE (connect-link base URL with token, same as the router).
set -u
log(){ echo "[inject-craft-incident] $*" >&2; }

INCIDENT_ID="${CRAFT_INCIDENT_ID:-cbb1ba47-c05b-60b5-f86e-16c05b77bb4f}"
OUT="${CLAUDE_PROJECT_DIR:-$(cd "$(dirname "$0")/../.." && pwd)}/.claude/craft-incident-context.md"
base="${CRAFT_API_BASE:-}"
if [[ -z "$base" ]]; then
  log "CRAFT_API_BASE not set; skipping incident-doc fetch"
  exit 0
fi
base="${base%/}"

md="$(curl -sS --fail --max-time 60 --retry 3 --retry-all-errors \
  -H 'Accept: text/markdown' \
  "$base/blocks?id=$INCIDENT_ID&maxDepth=-1")" || { log "incident fetch failed; keeping previous snapshot if any"; exit 0; }

if [[ -z "$md" ]]; then
  log "empty incident response; keeping previous snapshot if any"
  exit 0
fi

{
  echo "=== Craft: «⚙️ SKILL: Разбор инцидента», авто-обновлён SessionStart-хуком ($(date -u +%FT%TZ)) ==="
  echo "$md"
  echo "=== конец SKILL-дока ==="
} > "$OUT"

log "incident doc cached: $(wc -c < "$OUT") bytes -> .claude/craft-incident-context.md"
exit 0
