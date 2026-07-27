#!/usr/bin/env bash
# SessionStart hook: build the plan-gate exempt SCOPE — every block-ID living
# under the pre-authorised «direct-edit» pages listed in gate-exempt-pages.txt.
# The plan-gate (universal-guard-plan-gate.sh) lets a craft_write through
# without an approved plan when ALL block-IDs the command targets are in this
# set.
#
# Why target-IDs and not wording: this exemption has the INVERSE asymmetry of
# the incident detector — falsely opening the gate for a real project/sphere
# write is EXPENSIVE, an extra plan on a pre-approved list op is CHEAP. Phrases
# («купил X») misfire — Влад buys rings and tickets, not only groceries — so
# only the write target is a safe key.
#
# Fetch mechanics mirror craft-inject-router.sh (connect-API, CRAFT_API_BASE
# from env / .env). Fail-quiet toward the CHEAP side: missing env/config or a
# failed fetch → no scope file → the gate simply keeps requiring plans. A block
# created mid-session is absent from the snapshot — a rare, cheap miss.
#
# ONE canonical cache location: the checkout holding the REAL hook file
# (resolved through the ~/.claude symlink). The gate reads the cache by the
# same formula, so every session — cloud project, local worktree, arc-mounts,
# scheduled task — agrees on a single place. Installed user-level via
# install.sh so any local session refreshes it on start.
set -u
log(){ echo "[universal-cache-gate-exempt-scope] $*" >&2; }

self="$(realpath "$0" 2>/dev/null || echo "$0")"
DIR="$(cd "$(dirname "$self")" && pwd)"
# Load repo .env so CRAFT_API_BASE is available (Claude Code doesn't do it).
. "$DIR/_load-env.sh"

CONFIG="${CRAFT_GATE_EXEMPT_PAGES:-$DIR/gate-exempt-pages.txt}"
OUT="${CRAFT_GATE_EXEMPT_SCOPE:-$(cd "$DIR/../.." && pwd)/.claude/craft-gate-exempt-scope.txt}"

# Drop the previous snapshot first: a stale scope must never pose as fresh.
# If the build fails below, no file remains and the gate gates everything.
rm -f "$OUT"

[[ -f "$CONFIG" ]] || { log "config $CONFIG missing; scope not built (gate applies as usual)"; exit 0; }

base="${CRAFT_API_BASE:-}"
if [[ -z "$base" ]]; then
  log "CRAFT_API_BASE not set; scope not built (gate applies as usual)"
  exit 0
fi
base="${base%/}"

UUID_RE='[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}'
tmp="$(mktemp 2>/dev/null || echo "$OUT.tmp")"
pages=0 fetched=0
while IFS= read -r line || [[ -n "$line" ]]; do
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
  log "no block IDs collected from $pages page(s); scope not built (gate applies as usual)"
  exit 0
fi

# Uppercase + unique — the gate uppercases command IDs before the lookup.
tr 'a-f' 'A-F' < "$tmp" | sort -u > "$OUT"
rm -f "$tmp"
echo "Предодобренный scope план-гейта: $(wc -l < "$OUT" | tr -d ' ') block-ID из $fetched/$pages страниц (.claude/craft-gate-exempt-scope.txt) — запись целиком внутри них идёт без плана."
