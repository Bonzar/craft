#!/usr/bin/env bash
# PreToolUse plan-gate for mcp__Craft__craft_write: refuse any write to Craft
# unless a plan was approved in the current turn. This is the lever for the
# "запись без плана" incident — a base write must follow a plan Влад approved in
# plan-mode, not go straight off a "запиши …".
#
# State is a single marker file (kept OUT of the repo — pure ephemeral runtime
# state, no commit footprint):
#   - set   by plan-gate-approve.sh  (PostToolUse on ExitPlanMode = plan approved)
#   - clear by plan-gate-reset.sh    (UserPromptSubmit = new turn needs a fresh plan)
# CRAFT_AUTONOMOUS=1 bypasses the gate entirely — cron rutinas (гигиена,
# актуализация, ночная) and the headless evals are pre-authorised and have no
# interactive Влад to approve a plan.
#
# Fail open on anything unexpected: a broken gate must never wedge legitimate work.
set -u

[[ -n "${CRAFT_AUTONOMOUS:-}" ]] && exit 0

input="$(cat)"
tool="$(jq -r '.tool_name // ""' <<<"$input" 2>/dev/null)" || exit 0
[[ "$tool" == "mcp__Craft__craft_write" ]] || exit 0

marker="${CRAFT_PLAN_GATE_MARKER:-/tmp/craft-plan-gate.${CLAUDE_CODE_SESSION_ID:-default}.approved}"
[[ -f "$marker" ]] && exit 0

reason="Заблокировано план-гейтом: запись в Craft без одобренного плана в этом ходе. Сначала покажи план и получи ок Влада (план-мод → ExitPlanMode), потом пиши. Автономному прогону (рутина, евал) — выставить CRAFT_AUTONOMOUS=1."
jq -cn --arg r "$reason" '{hookSpecificOutput:{hookEventName:"PreToolUse",permissionDecision:"deny",permissionDecisionReason:$r}}'
exit 0
