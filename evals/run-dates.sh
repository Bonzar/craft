#!/usr/bin/env bash
# Level-1 (mock) eval matrix for the «срок vs дедлайн» date-field rule: cases × models.
#
# Regression-tests the real incident: Влад names a DEAL/event date ("договариваемся
# на 28.07") and the agent must set taskInfo.scheduleDate — NOT deadlineDate. The
# «☑️ Задача» router rule: срок = плановая дата работы/события → scheduleDate (это же
# дата задачи); дедлайн = ТОЛЬКО крайний срок «надо успеть к…» с последствием →
# deadlineDate; «когда делаем» ≠ «крайний срок», поле выбирается по смыслу и при
# создании, и при сдвиге.
#
# Hermetic: reads are served from evals/fixtures/ (CRAFT_FIXTURE_DIR) instead of the
# live base, so this depends on nothing in Влад's real Craft. Each case gives the
# agent the fixture page id + a Влад-style instruction; the agent reads the page,
# resolves the task, and writes the date. We resolve the same task id locally (jq
# over the fixture .json) and assert on the intercepted write: (a) an update on that
# id, (b) the CORRECT field = the case date, (c) the OPPOSITE field carries no date.
# The real base is never touched (craft_write is intercepted into the log).
#
# Usage: run-dates.sh [model ...]   (default: haiku)
set -u
cd "$(dirname "$0")/.." || exit 1

FIX_PAGE="aaaaaaaa-0000-0000-0000-000000000001"
FIX_DIR="$PWD/evals/fixtures"
FIX_JSON="$FIX_DIR/${FIX_PAGE}.json"
CASES="evals/cases/dates.jsonl"
# Fixed write-log path — must match mcp-config.json's env so the (warm-spare
# reused) mock server always writes here regardless of run order.
LOG="/tmp/craft-eval-write.log"
if [[ $# -gt 0 ]]; then MODELS=("$@"); else MODELS=("claude-haiku-4-5-20251001"); fi

# Serve reads from the local fixtures instead of the live curl (see the mock's
# curlBlocks). Inherited by `claude` and by the mock it spawns.
export CRAFT_FIXTURE_DIR="$FIX_DIR"
# Kept for parity / forward-compat only — there is no plan-gate hook, so this
# bypasses nothing (harmless). The live PreToolUse guard is guard-craft-markdown:
# it allows --json writes and denies a bare --markdown flag, so date changes must
# go through `blocks update --json {…taskInfo:{scheduleDate|deadlineDate}}`.
export CRAFT_AUTONOMOUS=1

# Resolve a task id from the fixture json by markdown substring — same shape as
# run-matrix.sh, but read straight from the fixture file (no mock/curl needed).
resolve_id() {
  jq -r --arg it "$1" '[.. | objects | select((.taskInfo != null) and ((.markdown // "") | contains($it)))] | (.[0].id // "")' "$FIX_JSON"
}
label() { grep -oE 'haiku|sonnet|opus|fable' <<<"$1" | head -1; }

total=0; passc=0
declare -A mt mp
printf '%-30s %-7s %-6s %s\n' "PROMPT" "MODEL" "RES" "DETAIL"
printf -- '-------------------------------------------------------------------------\n'
while IFS= read -r line; do
  [[ -z "$line" ]] && continue
  prompt="$(jq -r '.prompt' <<<"$line")"
  task="$(jq -r '.task'   <<<"$line")"
  field="$(jq -r '.field' <<<"$line")"
  date="$(jq -r '.date'   <<<"$line")"
  eid="$(resolve_id "$task")"
  # Give the agent the entry point (which page holds the tasks); the srok/дедлайн
  # rule itself is already in context via CLAUDE.md → craft-router-context.md.
  full="Задачи по ипотечной сделке лежат на странице Craft с id ${FIX_PAGE}. Прочитай её, найди нужную задачу и обнови её дату. ${prompt}"

  # Which json key / CLI flag is correct, which is the opposite (must stay clean).
  if [[ "$field" == "scheduleDate" ]]; then
    ckey="scheduleDate"; cflag="schedule"; okey="deadlineDate"; oflag="deadline"
  else
    ckey="deadlineDate"; cflag="deadline"; okey="scheduleDate"; oflag="schedule"
  fi

  for model in "${MODELS[@]}"; do
    ml="$(label "$model")"
    : > "$LOG"
    # env -u: drop this session's CLAUDE_CODE_* ids so each run is a fresh isolated
    #   session. --disallowedTools Bash Read: force writes through the Craft MCP
    #   (no shell `craft …`), same as run-matrix.sh.
    env -u CLAUDE_CODE_SESSION_ID -u CLAUDE_CODE_CHILD_SESSION -u CLAUDE_PID \
        -u CLAUDE_CODE_REMOTE_SESSION_ID -u CLAUDE_CODE_WORKER_EPOCH \
      timeout 300 claude -p "$full" \
      --mcp-config ./evals/mcp-config.json --strict-mcp-config \
      --model "$model" \
      --allowedTools Skill 'mcp__Craft__*' \
      --disallowedTools Bash Read \
      --output-format json >/dev/null 2>&1

    # Assert on the intercepted write commands (unescaped via jq).
    cmds="$(jq -r '.command // empty' "$LOG" 2>/dev/null)"
    ok=1; d=""
    grep -qiE 'blocks +update|tasks +update' <<<"$cmds" || { ok=0; d="no update"; }
    if [[ -n "$eid" ]]; then grep -qi -- "$eid" <<<"$cmds" || { ok=0; d="${d:+$d,}wrong id"; }; fi
    # (b) correct field carries the case date: json "field":"DATE" or --flag DATE.
    grep -qiE -- "\"?${ckey}\"?[[:space:]]*:[[:space:]]*\"?${date}|--${cflag}[[:space:]=]+\"?${date}" <<<"$cmds" \
      || { ok=0; d="${d:+$d,}${ckey}!=${date}"; }
    # (c) opposite field must NOT be set to any date (the incident-class mistake).
    if grep -qiE -- "\"?${okey}\"?[[:space:]]*:[[:space:]]*\"?[0-9]{4}-[0-9]{2}-[0-9]{2}|--${oflag}[[:space:]=]+\"?[0-9]{4}-[0-9]{2}-[0-9]{2}" <<<"$cmds"; then
      ok=0; d="${d:+$d,}${okey} set!"
    fi
    # Parity with the markdown guard: writes must be --json, never bare --markdown.
    grep -qE -- '(^|[[:space:]])--markdown([[:space:]]|=)' <<<"$cmds" && { ok=0; d="${d:+$d,}--markdown!"; }

    total=$((total+1)); mt[$ml]=$(( ${mt[$ml]:-0}+1 ))
    if [[ $ok -eq 1 ]]; then passc=$((passc+1)); mp[$ml]=$(( ${mp[$ml]:-0}+1 )); r="PASS"; else r="FAIL"; fi
    printf '%-30s %-7s %-6s %s\n' "${prompt:0:29}" "$ml" "$r" "${d:-→ $ckey=$date}"
  done
done < "$CASES"
printf -- '-------------------------------------------------------------------------\n'
for model in "${MODELS[@]}"; do ml="$(label "$model")"; echo "$ml: ${mp[$ml]:-0}/${mt[$ml]:-0}"; done
echo "TOTAL: $passc/$total"
