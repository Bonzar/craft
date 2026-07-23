#!/usr/bin/env bash
# PreToolUse plan-gate: refuse any WRITE unless a plan was approved in the current
# turn. Covers both Craft base writes (mcp__Craft__craft_write) and edits to files
# inside the repo (Write/Edit/MultiEdit). This is the lever for the "правка без
# одобрения" incidents — a change must follow a plan Влад approved in plan-mode, not
# go straight off an instruction or a button-choice.
#
# Repo scope: Write/Edit/MultiEdit are gated only for files under $CLAUDE_PROJECT_DIR.
# Edits OUTSIDE the repo pass untouched — the plan file itself (~/.claude/plans), /tmp,
# the scratchpad: scaffolding, not a committed deliverable. Belt-and-suspenders
# exemption for any */plans/*.md path.
#
# State — single ephemeral marker file (kept OUT of the repo, no commit footprint):
#   - set   by plan-gate-approve.sh  (PostToolUse on ExitPlanMode = plan approved)
#   - clear by plan-gate-reset.sh    (UserPromptSubmit = new turn needs a fresh plan)
# One approval covers every edit of that plan until the next user message; the marker
# is a per-turn boolean, so "only the plan's targets" is discipline, not a hard check.
# CRAFT_AUTONOMOUS=1 bypasses the gate entirely — cron rutinas (гигиена, актуализация,
# ночная) and the headless evals are pre-authorised and have no interactive Влад to
# approve a plan.
#
# Fail open on anything unexpected: a broken gate must never wedge legitimate work.
set -u

[[ -n "${CRAFT_AUTONOMOUS:-}" ]] && exit 0

input="$(cat)"
tool="$(jq -r '.tool_name // ""' <<<"$input" 2>/dev/null)" || exit 0

case "$tool" in
  mcp__Craft__craft_write) ;;                       # always gated
  Write|Edit|MultiEdit)
    fp="$(jq -r '.tool_input.file_path // ""' <<<"$input" 2>/dev/null)"
    repo="${CLAUDE_PROJECT_DIR:-}"
    # gate only edits to files inside the repo working tree; everything else
    # (plan file, /tmp, scratchpad) passes untouched.
    [[ -n "$repo" && "$fp" == "$repo"/* ]] || exit 0
    case "$fp" in */plans/*.md) exit 0 ;; esac
    ;;
  *) exit 0 ;;
esac

marker="${CRAFT_PLAN_GATE_MARKER:-/tmp/craft-plan-gate.${CLAUDE_CODE_SESSION_ID:-default}.approved}"
[[ -f "$marker" ]] && exit 0

reason="Заблокировано план-гейтом: правка без одобренного в этом ходе плана. Покажи план и получи ок Влада (план-мод → ExitPlanMode), потом правь. Автономному прогону (рутина, евал) — CRAFT_AUTONOMOUS=1."
jq -cn --arg r "$reason" '{hookSpecificOutput:{hookEventName:"PreToolUse",permissionDecision:"deny",permissionDecisionReason:$r}}'
exit 0
