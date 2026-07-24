#!/usr/bin/env bash
# PreToolUse plan-gate: refuse a base/system change unless a plan was approved in
# the current turn. The lever for the "запись без плана" incident — a change must
# follow a plan Влад approved in plan-mode, not go straight off a "запиши …".
#
# Covers TWO surfaces:
#   - Craft MCP  craft_write         — writes to the Craft base.
#   - Write | Edit | MultiEdit       — edits to the repo (this repo IS the agent
#                                      system: hooks, settings, skills). A repo
#                                      edit is a system change and gates the same.
#
# State is a single marker file (kept OUT of the repo — pure ephemeral runtime
# state, no commit footprint):
#   - set   by plan-gate-approve.sh  (PostToolUse on ExitPlanMode = plan APPROVED;
#                                      a rejected/aborted ExitPlanMode never fires
#                                      PostToolUse, so a text "ок" cannot set it)
#   - clear by plan-gate-reset.sh    (UserPromptSubmit = new turn needs a fresh plan)
# CRAFT_AUTONOMOUS=1 bypasses the gate entirely — cron rutinas (гигиена,
# актуализация, ночная) and headless evals are pre-authorised, no interactive Влад.
#
# Two ways past the gate WITHOUT a fresh plan:
#   1. Approved-plan marker present (either surface).
#   2. craft_write whose every target block-ID lies inside a pre-authorised
#      «direct-edit» page — the exempt scope cached by cache-gate-exempt-scope.sh.
#      (Repo edits have no such scope exemption.)
#
# Fail open on anything unexpected: a broken gate must never wedge legitimate work.
set -u

[[ -n "${CRAFT_AUTONOMOUS:-}" ]] && exit 0

input="$(cat)"
tool="$(jq -r '.tool_name // ""' <<<"$input" 2>/dev/null)" || exit 0

is_craft_write=0
is_repo_edit=0
# Match craft_write by suffix, not the full qualified name: the Craft MCP server's
# prefix changes on reconnect (mcp__Craft__... one session, mcp__<uuid>__... the
# next) — an exact match silently stops gating the moment the ID rotates.
if [[ "$tool" =~ __craft_write$ ]]; then
  is_craft_write=1
else
  case "$tool" in Write|Edit|MultiEdit) is_repo_edit=1 ;; *) exit 0 ;; esac
fi

marker="${CRAFT_PLAN_GATE_MARKER:-/tmp/craft-plan-gate.${CLAUDE_CODE_SESSION_ID:-default}.approved}"
[[ -f "$marker" ]] && exit 0

# --- Repo edits (Write/Edit/MultiEdit) ---------------------------------------
# Gate only files INSIDE the repo tree, and never the plan file itself (plan-mode
# writes it BEFORE approval — gating it would deadlock planning). Files outside
# the repo (scratchpad, /tmp) are not system changes — pass them through.
if [[ "$is_repo_edit" -eq 1 ]]; then
  fp="$(jq -r '.tool_input.file_path // ""' <<<"$input" 2>/dev/null)"
  [[ -z "$fp" ]] && exit 0
  # Plan files are written by plan-mode before the marker exists — always allow.
  [[ "$fp" == */plans/*.md ]] && exit 0
  root="${CLAUDE_PROJECT_DIR:-$(git rev-parse --show-toplevel 2>/dev/null)}"
  # Only gate paths under the repo root; anything else is not a system change.
  if [[ -n "$root" && "$fp" == "$root"/* ]]; then
    reason="Заблокировано план-гейтом: правка репозитория ($fp) без одобренного плана в этом ходе. Репо — это агентская система (хуки, настройки, скиллы), правка идёт через тот же план-гейт, что и запись в Craft: план-мод → ExitPlanMode (одобрение Влада именно тулзой, не текстом), потом правь. План может быть в стандартном формате. Автономному прогону — CRAFT_AUTONOMOUS=1."
    jq -cn --arg r "$reason" '{hookSpecificOutput:{hookEventName:"PreToolUse",permissionDecision:"deny",permissionDecisionReason:$r}}'
  fi
  exit 0
fi

# --- Craft writes -------------------------------------------------------------
# Exempt-scope bypass: if the command targets ONLY blocks inside a pre-authorised
# direct-edit page (e.g. «Продукты»), allow without a plan. Precise, not keyword-
# based: a real project/sphere write references block-IDs outside the scope.
scope="${CRAFT_GATE_EXEMPT_SCOPE:-${CLAUDE_PROJECT_DIR:-$(cd "$(dirname "$0")/../.." && pwd)}/.claude/craft-gate-exempt-scope.txt}"
if [[ -s "$scope" ]]; then
  cmd="$(jq -r '.tool_input.command // ""' <<<"$input" 2>/dev/null)"
  UUID_RE='[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}'
  ids="$(grep -oE "$UUID_RE" <<<"$cmd" | tr 'a-f' 'A-F' | sort -u)"
  if [[ -n "$ids" ]]; then
    all_in=1
    while IFS= read -r id; do
      grep -qxF "$id" "$scope" || { all_in=0; break; }
    done <<<"$ids"
    [[ "$all_in" -eq 1 ]] && exit 0
  fi
fi

reason="Заблокировано план-гейтом: запись в Craft без одобренного плана в этом ходе. Два законных пути: (1) показать план и получить ок Влада в план-моде (ExitPlanMode одобряется именно тулзой, не текстом), потом писать; (2) запись, все блоки-цели которой лежат внутри предодобренной зоны прямого редактирования (напр. «Продукты»), — идёт без плана. Автономному прогону (рутина, евал) — выставить CRAFT_AUTONOMOUS=1."
jq -cn --arg r "$reason" '{hookSpecificOutput:{hookEventName:"PreToolUse",permissionDecision:"deny",permissionDecisionReason:$r}}'
exit 0
