#!/usr/bin/env bash
# Level-1 (mock) eval: a new task gets its dependency link when it is created.
#
# Regression-tests the real incident: in the renovation project 32 tasks, stages and
# milestones were created with empty «Зависит от» / «Следующий шаг», among them
# «Повесить временный карниз», which cannot start without the perforator bought in
# «Купить базовый инструмент». The craft-call rule now makes linking part of task
# creation.
#
# Hermetic: reads are served from evals/fixtures/ (CRAFT_FIXTURE_DIR). The fixture
# page holds a stage with the purchase task. Each case asks the agent to create the
# curtain-rod task; we assert on the intercepted writes: (a) some write links to the
# purchase task (block://<purchase id>), (b) some write puts the back link into the
# purchase task (its «Следующий шаг» toggle or the task itself). When the agent stops
# to show its plan first, the plan must name the dependency on the purchase task. The real base is
# never touched (craft_write is intercepted into the log).
#
# Usage: run-task-deps.sh [model ...]   (default: haiku)
set -u
cd "$(dirname "$0")/.." || exit 1

FIX_PAGE="dddddddd-0000-0000-0000-000000000001"
PURCHASE="dddddddd-0000-0000-0000-000000000003"
NEXT_TOGGLE="dddddddd-0000-0000-0000-000000000005"
FIX_DIR="$PWD/evals/fixtures"
CASES="evals/cases/task-deps.jsonl"
LOG="/tmp/craft-eval-write.log"
OUT="$(mktemp)"
if [[ $# -gt 0 ]]; then MODELS=("$@"); else MODELS=("claude-haiku-4-5-20251001"); fi

export CRAFT_FIXTURE_DIR="$FIX_DIR"
export CRAFT_EVAL=1

label() { grep -oE 'haiku|sonnet|opus|fable' <<<"$1" | head -1; }

total=0; passc=0
declare -A mt mp
printf '%-40s %-7s %-6s %s\n' "CASE" "MODEL" "RES" "DETAIL"
printf -- '-------------------------------------------------------------------------\n'
while IFS= read -r line; do
  [[ -z "$line" ]] && continue
  name="$(jq -r '.name' <<<"$line")"
  prompt="$(jq -r '.prompt' <<<"$line")"
  full="Проект ремонта лежит на странице Craft с id ${FIX_PAGE}. ${prompt}"

  for model in "${MODELS[@]}"; do
    ml="$(label "$model")"
    : > "$LOG"
    env -u CLAUDE_CODE_SESSION_ID -u CLAUDE_CODE_CHILD_SESSION -u CLAUDE_PID \
        -u CLAUDE_CODE_REMOTE_SESSION_ID -u CLAUDE_CODE_WORKER_EPOCH \
      timeout 300 claude -p "$full" \
      --mcp-config ./evals/mcp-config.json --strict-mcp-config \
      --model "$model" \
      --allowedTools Skill 'mcp__Craft__*' \
      --disallowedTools Bash Read \
      --output-format json > "$OUT" 2>/dev/null

    cmds="$(jq -r '.command // empty' "$LOG" 2>/dev/null)"
    reply="$(jq -r '.result // empty' "$OUT" 2>/dev/null)"
    ok=1; d=""
    if grep -qiE 'blocks +add' <<<"$cmds"; then
      # Written: (a) the new task links to the purchase it waits for,
      # (b) the back link lands in the purchase task.
      grep -qi -- "block://${PURCHASE}" <<<"$cmds" || { ok=0; d="no link to purchase"; }
      grep -qiE -- "${NEXT_TOGGLE}|--id[[:space:]]+\"?${PURCHASE}" <<<"$cmds" || { ok=0; d="${d:+$d,}no back link"; }
    else
      # Shown before writing (the behavior rule asks for confirmation): the plan
      # must already carry the dependency on the purchase task.
      grep -qi 'Зависит от' <<<"$reply" || { ok=0; d="plan: no «Зависит от»"; }
      grep -qiE 'Купить базовый инструмент' <<<"$reply" || { ok=0; d="${d:+$d,}plan: no purchase task"; }
    fi
    grep -qE -- '(^|[[:space:]])--markdown([[:space:]]|=)' <<<"$cmds" && { ok=0; d="${d:+$d,}--markdown!"; }

    total=$((total+1)); mt[$ml]=$(( ${mt[$ml]:-0}+1 ))
    if [[ $ok -eq 1 ]]; then passc=$((passc+1)); mp[$ml]=$(( ${mp[$ml]:-0}+1 )); r="PASS"; else r="FAIL"; fi
    printf '%-40s %-7s %-6s %s\n' "${name:0:39}" "$ml" "$r" "${d:-linked both ways}"
  done
done < "$CASES"
printf -- '-------------------------------------------------------------------------\n'
for model in "${MODELS[@]}"; do ml="$(label "$model")"; echo "$ml: ${mp[$ml]:-0}/${mt[$ml]:-0}"; done
echo "TOTAL: $passc/$total"
