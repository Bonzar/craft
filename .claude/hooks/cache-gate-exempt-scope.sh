#!/usr/bin/env bash
# SessionStart hook: build the plan-gate exempt SCOPE — the set of block-IDs that
# live under the pre-authorised «direct-edit» pages (gate-exempt-pages.txt). The
# plan-gate (guard-plan-gate.sh) lets a craft_write through without an approved
# plan when EVERY block-ID the command targets is in this set.
#
# Why an ID scope and not keyword detection: this exemption has the INVERSE
# asymmetry of the incident detector — a false positive (opening the gate for a
# real project/sphere write) is EXPENSIVE, a miss (a genuine list op still needs
# a plan) is CHEAP. A phrase like «купил X» misfires (Влад buys rings, tickets,
# not only groceries), so intent can't gate safely. We key on the WRITE TARGET:
# an exempt op touches only blocks under a pre-authorised page. We fetch each
# such page's subtree once per session and cache every block-ID.
#
# Same fetch pattern / env as inject-craft-router.sh (connect-API, CRAFT_API_BASE).
# Fail-quiet toward the CHEAP direction: no env / no config / fetch failure /
# empty → no cache file → the gate keeps requiring a plan for those ops (status
# quo), never wedges startup. A block added mid-session isn't in the snapshot, so
# editing it that same session needs a plan — rare, and a cheap miss.
set -u
log(){ echo "[cache-gate-exempt-scope] $*" >&2; }

DIR="$(cd "$(dirname "$0")" && pwd)"
# Load repo .env so CRAFT_API_BASE is available (Claude Code doesn't do it).
. "$DIR/_load-env.sh"

CONFIG="${CRAFT_GATE_EXEMPT_PAGES:-$DIR/gate-exempt-pages.txt}"
OUT="${CLAUDE_PROJECT_DIR:-$(cd "$DIR/../.." && pwd)}/.claude/craft-gate-exempt-scope.txt"

# Drop the previous snapshot up front: a stale scope must never masquerade as
# fresh. If the build fails we stay with no file and the gate keeps gating.
rm -f "$OUT"

[[ -f "$CONFIG" ]] || { log "config $CONFIG missing; no exempt scope built (plan-gate applies as usual)"; exit 0; }

base="${CRAFT_API_BASE:-}"
if [[ -z "$base" ]]; then
  log "CRAFT_API_BASE not set; no exempt scope built (plan-gate applies as usual)"
  exit 0
fi
base="${base%/}"

UUID_RE='[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}'
tmp="$(mktemp 2>/dev/null || echo "${OUT}.tmp")"
pages=0
fetched=0
while IFS= read -r line || [[ -n "$line" ]]; do
  # strip inline comments / whitespace; skip blanks and '#'-only lines
  page="${line%%#*}"
  page="$(tr -d '[:space:]' <<<"$page")"
  [[ -z "$page" ]] && continue
  pages=$((pages+1))
  body="$(curl -sS --fail --max-time 60 --retry 3 --retry-all-errors \
    "$base/blocks?id=$page&maxDepth=-1" 2>/dev/null)" || { log "fetch failed for $page; skipped"; continue; }
  grep -oE "$UUID_RE" <<<"$body" >> "$tmp" || true
  fetched=$((fetched+1))
done < "$CONFIG"

if [[ ! -s "$tmp" ]]; then
  rm -f "$tmp"
  log "no block IDs collected from $pages page(s); no exempt scope built (plan-gate applies as usual)"
  exit 0
fi

# Uppercase + unique — guard-plan-gate.sh uppercases command IDs before lookup.
tr 'a-f' 'A-F' < "$tmp" | sort -u > "$OUT"
rm -f "$tmp"
echo "Предодобренный scope собран: $(wc -l < "$OUT" | tr -d ' ') block-ID из $fetched/$pages страниц в .claude/craft-gate-exempt-scope.txt — план-гейт пропускает запись, целиком лежащую внутри них, без плана."
