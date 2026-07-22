#!/usr/bin/env bash
# Level-1 (mock) eval matrix for the "Продукты" skill: cases × models.
#
# Each case (evals/cases/purchases.jsonl) is {prompt, item, state}. For every
# model we run a headless `claude -p` against the mock Craft MCP (real reads via
# connect-REST, writes intercepted), resolve the item's real block-ID from the
# live page, and assert the intended write is `tasks update <id> --state <state>`
# with no raw --markdown. The real base is never modified.
#
# Usage: run-matrix.sh [model ...]   (default: haiku)
set -u
cd "$(dirname "$0")/.." || exit 1
base="${CRAFT_API_BASE%/}"
PAGE="395450FC-468E-4EF6-8267-BC158A4E2EBC"
CASES="evals/cases/purchases.jsonl"
# Fixed write-log path — must match mcp-config.json's env, so the (warm-spare
# reused) mock server always writes here regardless of run order.
LOG="/tmp/craft-eval-write.log"
if [[ $# -gt 0 ]]; then MODELS=("$@"); else MODELS=("claude-haiku-4-5-20251001"); fi

PAGE_JSON="$(mktemp)"
curl -sS --fail --max-time 60 -H 'Accept: application/json' "$base/blocks?id=$PAGE&maxDepth=-1" > "$PAGE_JSON" \
  || { echo "fetch page failed"; exit 1; }

resolve_id() {
  # only task blocks (products), not page-category subpages whose title may
  # contain the item name (e.g. «Молочка - Яйца» contains «Яйца»).
  jq -r --arg it "$1" '[.. | objects | select((.taskInfo != null) and ((.markdown // "") | contains($it)))] | (.[0].id // "")' "$PAGE_JSON"
}
label() { grep -oE 'haiku|sonnet|opus|fable' <<<"$1" | head -1; }

total=0; passc=0
declare -A mt mp
printf '%-26s %-7s %-6s %s\n' "PROMPT" "MODEL" "RES" "DETAIL"
printf -- '---------------------------------------------------------------\n'
while IFS= read -r line; do
  [[ -z "$line" ]] && continue
  prompt="$(jq -r '.prompt' <<<"$line")"
  item="$(jq -r '.item' <<<"$line")"
  state="$(jq -r '.state' <<<"$line")"
  eid="$(resolve_id "$item")"
  for model in "${MODELS[@]}"; do
    ml="$(label "$model")"
    : > "$LOG"
    timeout 300 claude -p "$prompt" \
      --mcp-config ./evals/mcp-config.json --strict-mcp-config \
      --model "$model" \
      --allowedTools Skill mcp__Craft__craft_read mcp__Craft__craft_write \
      --output-format json >/dev/null 2>&1
    log="$(cat "$LOG" 2>/dev/null)"
    ok=1; d=""
    grep -qiE 'tasks +update' <<<"$log" || { ok=0; d="no update"; }
    if [[ -n "$eid" ]]; then grep -qi -- "$eid" <<<"$log" || { ok=0; d="${d:+$d,}wrong id"; }; fi
    grep -qiE -- "--state +$state" <<<"$log" || { ok=0; d="${d:+$d,}state!=$state"; }
    grep -qE -- '(^|[[:space:]])--markdown([[:space:]]|=)' <<<"$log" && { ok=0; d="${d:+$d,}--markdown!"; }
    total=$((total+1)); mt[$ml]=$(( ${mt[$ml]:-0}+1 ))
    if [[ $ok -eq 1 ]]; then passc=$((passc+1)); mp[$ml]=$(( ${mp[$ml]:-0}+1 )); r="PASS"; else r="FAIL"; fi
    printf '%-26s %-7s %-6s %s\n' "${prompt:0:25}" "$ml" "$r" "$d"
  done
done < "$CASES"
printf -- '---------------------------------------------------------------\n'
for model in "${MODELS[@]}"; do ml="$(label "$model")"; echo "$ml: ${mp[$ml]:-0}/${mt[$ml]:-0}"; done
echo "TOTAL: $passc/$total"
rm -f "$PAGE_JSON"
