#!/usr/bin/env bash
# SessionStart hook: inject the Craft agent-memory router into session context.
#
# stdout of a SessionStart hook is appended to the model's context, so this
# fetches the live router document («🧠 Память для Claude») over the Craft
# connect-link REST API and prints it — the router is in context from turn
# zero, no lazy MCP round-trip needed.
#
# Requires CRAFT_API_BASE in the environment settings (connect-link base URL
# with the token embedded, e.g. https://connect.craft.do/links/XXXX/api/v1).
# Exits 0 quietly when it is missing or the fetch fails, so a broken network
# can never wedge session startup.
set -u
log(){ echo "[inject-craft-router] $*" >&2; }

ROUTER_ID="${CRAFT_ROUTER_ID:-e8132891-81f4-2d63-36f1-d3623d0147b6}"
base="${CRAFT_API_BASE:-}"
if [[ -z "$base" ]]; then
  log "CRAFT_API_BASE not set; skipping router injection"
  exit 0
fi
base="${base%/}"

md="$(curl -sS --fail --max-time 60 --retry 3 --retry-all-errors \
  -H 'Accept: text/markdown' \
  "$base/blocks?id=$ROUTER_ID&maxDepth=-1")" || { log "router fetch failed"; exit 0; }

if [[ -z "$md" ]]; then
  log "empty router response; skipping"
  exit 0
fi

echo "=== Craft: роутер «Память для Claude», авто-инжект SessionStart ($(date -u +%FT%TZ)) ==="
echo "$md"
echo "=== конец роутера — действуй по его директивам ==="
