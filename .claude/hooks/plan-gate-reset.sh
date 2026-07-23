#!/usr/bin/env bash
# UserPromptSubmit: a new message from Влад starts a new turn — the previous plan
# approval no longer covers it, so clear the plan-gate marker. The next craft_write
# must be preceded by a fresh approved plan (guard-plan-gate.sh). Never blocks the
# message (no stdout, exit 0).
set -u
[[ -n "${CRAFT_AUTONOMOUS:-}" ]] && exit 0
marker="${CRAFT_PLAN_GATE_MARKER:-/tmp/craft-plan-gate.${CLAUDE_CODE_SESSION_ID:-default}.approved}"
rm -f "$marker" 2>/dev/null || true
exit 0
