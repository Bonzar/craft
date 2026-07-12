#!/usr/bin/env bash
# SessionStart hook: fetch the Craft agent-memory router for session context.
#
# Hook stdout is capped at 10 000 characters (larger output gets persisted to
# a file with only a 2 KB preview in context), so the router — ~75K chars —
# cannot be injected through stdout. Instead this writes the fetched document
# to .claude/craft-router-context.md, which CLAUDE.md imports via
# `@.claude/craft-router-context.md`; CLAUDE.md imports have no such cap, so
# the full live router lands in context from turn zero.
#
# Requires CRAFT_API_BASE in the environment settings (connect-link base URL
# with the token embedded, e.g. https://connect.craft.do/links/XXXX/api/v1).
# Exits 0 quietly when it is missing or the fetch fails, so a broken network
# can never wedge session startup; a stale previously-written file (if any)
# is left in place as a fallback.
set -u
log(){ echo "[inject-craft-router] $*" >&2; }

ROUTER_ID="${CRAFT_ROUTER_ID:-e8132891-81f4-2d63-36f1-d3623d0147b6}"
OUT="${CLAUDE_PROJECT_DIR:-$(cd "$(dirname "$0")/../.." && pwd)}/.claude/craft-router-context.md"
base="${CRAFT_API_BASE:-}"
if [[ -z "$base" ]]; then
  log "CRAFT_API_BASE not set; skipping router fetch"
  exit 0
fi
base="${base%/}"

md="$(curl -sS --fail --max-time 60 --retry 3 --retry-all-errors \
  -H 'Accept: text/markdown' \
  "$base/blocks?id=$ROUTER_ID&maxDepth=-1")" || { log "router fetch failed; keeping previous snapshot if any"; exit 0; }

if [[ -z "$md" ]]; then
  log "empty router response; keeping previous snapshot if any"
  exit 0
fi

{
  echo "=== Craft: роутер «Память для Claude», авто-обновлён SessionStart-хуком ($(date -u +%FT%TZ)) ==="
  echo "$md"
  echo "=== конец роутера — действуй по его директивам ==="
} > "$OUT"

echo "Craft-роутер обновлён: $(wc -c < "$OUT") байт записано в .claude/craft-router-context.md; полный текст уже в контексте через импорт в CLAUDE.md."
