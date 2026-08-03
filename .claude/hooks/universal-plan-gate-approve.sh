#!/usr/bin/env bash
# PostToolUse on ExitPlanMode: a plan was approved, so set the plan-gate marker —
# craft_write in this turn is allowed until the next user message clears it
# (plan-gate-reset.sh). Scoped to ExitPlanMode by the settings matcher, so it only
# fires when a plan is actually exited/approved. CRAFT_AUTONOMOUS runs don't gate,
# so they don't mark either.
set -u
[[ -n "${CRAFT_AUTONOMOUS:-}" ]] && exit 0
marker="${CRAFT_PLAN_GATE_MARKER:-/tmp/craft-plan-gate.${CLAUDE_CODE_SESSION_ID:-default}.approved}"
: > "$marker" 2>/dev/null || true
# Показ состоялся — обкатка кончилась: счётчик прогонов критика начинает следующую с нуля.
# Обкатка привязана к плану, а не к ходу, поэтому обнуляет её именно одобрение, а не
# реплика Влада — по реплике плато было недостижимо в живом диалоге.
runs="${CRAFT_PLAN_CRITIC_RUNS:-/tmp/plan-critic.${CLAUDE_CODE_SESSION_ID:-default}.runs}"
rm -f "$runs" 2>/dev/null || true
exit 0
