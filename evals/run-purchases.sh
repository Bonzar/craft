#!/usr/bin/env bash
# Level-1 (mock) eval for the "Продукты" skill.
#
# Runs a headless `claude -p` agent against the mock Craft MCP (real reads via
# connect-REST, writes intercepted into a log) and asserts on the intended
# write. The real Craft base is never modified.
#
# Usage: run-purchases.sh [model] [prompt] [expected_id] [expected_state]
#   defaults: haiku · "Закончилось молоко" · Молоко id · todo
set -u
cd "$(dirname "$0")/.." || exit 1

MODEL="${1:-claude-haiku-4-5-20251001}"
PROMPT="${2:-Закончилось молоко}"
EXPECT_ID="${3:-2A63FEF0-34D9-485A-B9D4-0B58000D98BD}"
EXPECT_STATE="${4:-todo}"

LOG="/tmp/craft-eval-write.log"; : > "$LOG"   # must match mcp-config.json env
RUN_OUT="$(mktemp)"

echo "[eval] model=$MODEL  prompt=\"$PROMPT\"  expect: $EXPECT_ID → $EXPECT_STATE"
timeout 360 claude -p "$PROMPT" \
  --mcp-config ./evals/mcp-config.json \
  --strict-mcp-config \
  --settings ./evals/settings.eval.json \
  --model "$MODEL" \
  --allowedTools Skill mcp__Craft__craft_read mcp__Craft__craft_write \
  --output-format json > "$RUN_OUT" 2>&1
echo "[eval] claude exit=$?"

log="$(cat "$LOG" 2>/dev/null)"
echo "=== intercepted writes ==="; echo "${log:-<none>}"

pass=1; reasons=()
[[ -n "$log" ]] || { pass=0; reasons+=("нет ни одного craft_write"); }
grep -qiE 'tasks +update' <<<"$log" || { pass=0; reasons+=("операция не 'tasks update'"); }
grep -qiE -- "--state +$EXPECT_STATE" <<<"$log" || { pass=0; reasons+=("--state != $EXPECT_STATE"); }
grep -qi -- "$EXPECT_ID" <<<"$log" || { pass=0; reasons+=("не тот ID товара (ждали $EXPECT_ID)"); }
grep -qE -- '(^|[[:space:]])--markdown([[:space:]]|=)' <<<"$log" && { pass=0; reasons+=("нарушение: использован --markdown"); }

echo "=== verdict ==="
if [[ "$pass" -eq 1 ]]; then
  echo "PASS ✅"
else
  echo "FAIL ❌"; for r in "${reasons[@]}"; do echo "  - $r"; done
  echo "--- agent result (tail) ---"; jq -r '.result // .' "$RUN_OUT" 2>/dev/null | tail -20 || tail -20 "$RUN_OUT"
fi
