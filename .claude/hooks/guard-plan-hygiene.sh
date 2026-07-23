#!/usr/bin/env bash
# PreToolUse guard on Craft-plan writes (Write/Edit to */plans/*.md). It polices
# ONLY Craft «План правок» files — detected by their structure (a «где:» locator
# line or a Craft link/ref) — and passes plans про КОД through untouched, since
# those legitimately name files, commands and IDs.
#
# Enforces two of Влад's plan rules that the built-in Plan-mode actively pushes
# against (it templates a verification/order section and doesn't produce links):
#   1. NO execution mechanics in a plan — no verification/order sections, no
#      commands (blocks get/update, tasks, git, curl, --json/--id). A plan says
#      WHAT changes and WHERE, not HOW to do or verify it.
#   2. Block references are clickable docs.craft.do links, not bare UUIDs.
#
# Heuristic — narrow patterns to limit false positives; on a hit it denies the
# write with a reason so the plan gets rewritten. Fail quiet on anything odd.
set -u

input="$(cat)"
tool="$(jq -r '.tool_name // ""' <<<"$input" 2>/dev/null)" || exit 0
case "$tool" in Write|Edit|MultiEdit) ;; *) exit 0 ;; esac

fp="$(jq -r '.tool_input.file_path // ""' <<<"$input" 2>/dev/null)"
[[ "$fp" == */plans/*.md ]] || exit 0

content="$(jq -r '.tool_input.content // .tool_input.new_string // ""' <<<"$input" 2>/dev/null)"
[[ -n "$content" ]] || exit 0

# Only Craft-plans («План правок») are policed. A plan про КОД legitimately names
# files, commands and flags, so the mechanics/command/ID checks below must not
# touch it. Detect a Craft-plan by its structural signals — the «где:» locator
# line every entity carries, or a Craft link/ref — and pass anything else (a code
# or other plan) straight through.
grep -qE 'docs\.craft\.do|block://|(^|[[:space:]])где:' <<<"$content" || exit 0

# Dictated verbatim text in a «План правок» sits in ``` code-blocks and may legitimately
# contain command tokens, IDs, even a «Проверка» heading — that is the content being
# written, not plan mechanics. Strip fenced code-blocks first so only the plan's PROSE
# is policed.
body="$(awk 'BEGIN{f=0} /^[[:space:]]*```/{f=!f; next} !f' <<<"$content")"

problems=()

# 1a. verification / order / test section headings
if grep -qiE '^#+[[:space:]]*(Порядок|Проверка|Verification|Verify|Тесты|Testing|Проверка результата|Порядок выполнения)' <<<"$body"; then
  problems+=("секция механики/проверки/порядка — механика в план не выносится")
fi
# 1b. explicit execution commands / mechanics tokens
if grep -qE '(blocks (get|update|add|move|delete|learn)|tasks (update|add|delete)|(^|[[:space:]])--(json|id|markdown|siblingId|depth)([[:space:]]|=)|git (commit|push|add)|curl )' <<<"$body"; then
  problems+=("команды/механика выполнения в тексте плана")
fi

# 2. bare block-IDs (UUID) not inside a docs.craft.do link.
# Strip all docs.craft.do URLs first; a UUID left in the remainder is bare.
stripped="$(sed -E 's#https?://docs\.craft\.do[^ )]*##g' <<<"$body")"
if grep -qiE '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}' <<<"$stripped"; then
  problems+=("голый block-ID вне ссылки — отсылка к блоку должна быть кликабельной ссылкой docs.craft.do")
fi

[[ ${#problems[@]} -eq 0 ]] && exit 0

reason="План нарушает правила: $(printf '%s; ' "${problems[@]}")План отвечает ЧТО меняется и КУДА ложится — механика, команды, проверка и порядок в него не выносятся; отсылки к блокам даются кликабельными ссылками docs.craft.do, не голым ID. Перепиши план и запиши снова."
jq -cn --arg r "$reason" '{hookSpecificOutput:{hookEventName:"PreToolUse",permissionDecision:"deny",permissionDecisionReason:$r}}'
exit 0
