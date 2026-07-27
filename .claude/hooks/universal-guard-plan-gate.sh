#!/usr/bin/env bash
# PreToolUse plan-gate: refuse a base/system change unless a plan was approved
# in the current turn. The lever for the "запись без плана" incident — a change
# must follow a plan Влад approved in plan-mode, not go straight off a "запиши".
#
# Covers TWO surfaces:
#   - Craft MCP craft_write               — writes to the Craft base;
#   - Write|Edit|MultiEdit|NotebookEdit   — file edits ANYWHERE. Coverage is
#     INVERTED: the hook cannot know the session's additional working dirs
#     (arc-mounts and the like are not in the PreToolUse JSON), so instead of
#     listing what to gate it gates every path EXCEPT known-ephemeral places
#     (plan files, tmp/scratchpad, harness service state under ~/.claude).
#
# State is a single marker file (kept OUT of the repo — ephemeral runtime
# state):
#   - set   by universal-plan-gate-approve.sh (PostToolUse on ExitPlanMode =
#     plan APPROVED; a rejected ExitPlanMode never fires PostToolUse, so a
#     text "ок" cannot set it)
#   - clear by universal-plan-gate-reset.sh   (UserPromptSubmit = new turn
#     needs a fresh plan)
# CRAFT_AUTONOMOUS=1 bypasses the gate entirely — cron rutinas (гигиена,
# актуализация, ночная) and headless evals are pre-authorised, no interactive
# Влад to approve a plan.
#
# One more way past the gate WITHOUT a plan: a craft_write whose every target
# block-ID lies inside a pre-authorised «direct-edit» page — the exempt scope
# cached by craft-cache-gate-exempt-scope.sh (e.g. «Продукты»). File edits have
# no such scope exemption.
#
# Known accepted limitation (mirrors arc-mount-guard): Bash file writes
# (sed -i, echo >, tee…) are NOT gated — Bash must stay free for builds/copies.
# The gate targets the file tools the agent actually edits with.
#
# Fail open on anything unexpected: a broken gate must never wedge legit work.
set -u

# When this same hook is ALSO installed at user level (~/.claude, via
# install.sh), the project-level registration yields to it — otherwise a craft
# session would run the gate twice per call. Cloud sessions have no user-level
# install, so the project copy stays active there.
if [[ -n "${CLAUDE_PROJECT_DIR:-}" && "$0" == "$CLAUDE_PROJECT_DIR"/* \
      && -e "$HOME/.claude/hooks/$(basename "$0")" ]]; then
  exit 0
fi

[[ -n "${CRAFT_AUTONOMOUS:-}" ]] && exit 0

input="$(cat)"
tool="$(jq -r '.tool_name // ""' <<<"$input" 2>/dev/null)" || exit 0

is_craft_write=0
is_file_edit=0
# Match craft_write by suffix, not the full qualified name: the Craft MCP
# server's prefix changes on reconnect (mcp__Craft__… one session,
# mcp__<uuid>__… the next) — an exact match silently stops gating the moment
# the ID rotates.
if [[ "$tool" =~ __craft_write$ ]]; then
  is_craft_write=1
else
  case "$tool" in
    Write|Edit|MultiEdit|NotebookEdit) is_file_edit=1 ;;
    *) exit 0 ;;
  esac
fi

marker="${CRAFT_PLAN_GATE_MARKER:-/tmp/craft-plan-gate.${CLAUDE_CODE_SESSION_ID:-default}.approved}"
[[ -f "$marker" ]] && exit 0

deny() {
  jq -cn --arg r "$1" \
    '{hookSpecificOutput:{hookEventName:"PreToolUse",permissionDecision:"deny",permissionDecisionReason:$r}}'
  exit 0
}

# --- File edits (Write/Edit/MultiEdit/NotebookEdit) --------------------------
if [[ "$is_file_edit" -eq 1 ]]; then
  fp="$(jq -r '.tool_input.file_path // .tool_input.notebook_path // ""' <<<"$input" 2>/dev/null)"
  [[ -z "$fp" ]] && exit 0

  # Plan files are written by plan-mode BEFORE the marker exists — gating them
  # would deadlock planning itself.
  [[ "$fp" == */plans/*.md ]] && exit 0

  # Ephemeral scratch locations — not system changes.
  case "$fp" in
    /tmp/*|/private/tmp/*|/var/folders/*|/private/var/folders/*|*/scratchpad/*) exit 0 ;;
  esac
  [[ -n "${TMPDIR:-}" && "$fp" == "${TMPDIR%/}"/* ]] && exit 0

  # ~/.claude: the harness writes service state there continuously (memory,
  # sessions, tasks, todos…) — that must stay free. Only the SYSTEM zones are
  # gated: skills, hooks, agents, rules, commands, workflows, settings, env.
  if [[ "$fp" == "$HOME/.claude/"* ]]; then
    rel="${fp#"$HOME/.claude/"}"
    case "$rel" in
      skills/*|hooks/*|agents/*|rules/*|commands/*|workflows/*|settings.json|settings.local.json|craft.env) ;;
      *) exit 0 ;;
    esac
  fi

  deny "Заблокировано план-гейтом: правка файла ($fp) без одобренного плана в этом ходе. Правки кода и системы в любой рабочей директории идут через тот же план-гейт, что и запись в Craft: план-мод → ExitPlanMode (одобрение Влада именно тулзой, не текстом) → правки. Автономному прогону — CRAFT_AUTONOMOUS=1."
fi

# --- Craft writes ------------------------------------------------------------
# Exempt-scope bypass: allow without a plan when the command targets ONLY
# blocks inside a pre-authorised direct-edit page. Keyed on the WRITE TARGET,
# not on wording: a real project/sphere write references block-IDs outside the
# scope, so it still needs a plan.
self="$(realpath "$0" 2>/dev/null || echo "$0")"
scope="${CRAFT_GATE_EXEMPT_SCOPE:-${CLAUDE_PROJECT_DIR:-$(cd "$(dirname "$self")/../.." && pwd)}/.claude/craft-gate-exempt-scope.txt}"
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

deny "Заблокировано план-гейтом: запись в Craft без одобренного плана в этом ходе. Сначала покажи план и получи ок Влада (план-мод → ExitPlanMode), потом пиши. Запись целиком внутри предодобренной зоны (напр. «Продукты») проходит без плана. Автономному прогону (рутина, евал) — CRAFT_AUTONOMOUS=1."
