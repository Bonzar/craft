#!/usr/bin/env bash
# PreToolUse guard on plan-file writes (Write/Edit to */plans/*.md).
#
# Enforces two of Влад's plan rules that the built-in Plan-mode actively pushes
# against (it templates a verification/order section and doesn't produce links):
#   1. NO execution mechanics in a plan — no verification/order sections, no
#      commands (blocks get/update, tasks, git, curl, --json/--id). A plan says
#      WHAT changes and WHERE, not HOW to do or verify it.
#   2. Block references are clickable docs.craft.do links, not bare UUIDs.
#
# Fenced code blocks (```) are excluded from the checks: the verbatim text of a
# [система] rule legitimately quotes Craft command names inside a code block —
# that is content being written, not plan mechanics.
#
# Heuristic — narrow patterns to limit false positives; on a hit it denies the
# write with a reason so the plan gets rewritten. Fail quiet on anything odd.
set -u
# POSIX locale skips case-folding for Cyrillic in grep -i; force UTF-8 so the
# Cyrillic section headings (Порядок/Проверка/…) match case-insensitively.
export LC_ALL=C.UTF-8

input="$(cat)"
tool="$(jq -r '.tool_name // ""' <<<"$input" 2>/dev/null)" || exit 0
case "$tool" in Write|Edit|MultiEdit) ;; *) exit 0 ;; esac

fp="$(jq -r '.tool_input.file_path // ""' <<<"$input" 2>/dev/null)"
[[ "$fp" == */plans/*.md ]] || exit 0

content="$(jq -r '.tool_input.content // .tool_input.new_string // ""' <<<"$input" 2>/dev/null)"
[[ -n "$content" ]] || exit 0

# Drop fenced code blocks before checking — verbatim rule text quotes command
# names there legitimately (content, not plan mechanics).
nocode="$(awk 'BEGIN{f=0} /^[[:space:]]*```/{f=!f; next} !f{print}' <<<"$content")"

problems=()

# 1a. verification / order / test section headings
if grep -qiE '^#+[[:space:]]*(Порядок|Проверка|Verification|Verify|Тесты|Testing|Проверка результата|Порядок выполнения)' <<<"$nocode"; then
  problems+=("секция механики/проверки/порядка — механика в план не выносится")
fi
# 1b. explicit execution commands / mechanics tokens
if grep -qE '(blocks (get|update|add|move|delete|learn)|tasks (update|add|delete)|(^|[[:space:]])--(json|id|markdown|siblingId|depth)([[:space:]]|=)|git (commit|push|add)|curl )' <<<"$nocode"; then
  problems+=("команды/механика выполнения в тексте плана")
fi

# 2. bare block-IDs (UUID) not inside a docs.craft.do link.
stripped="$(sed -E 's#https?://docs\.craft\.do[^ )]*##g' <<<"$nocode")"
if grep -qiE '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}' <<<"$stripped"; then
  problems+=("голый block-ID вне ссылки — отсылка к блоку должна быть кликабельной ссылкой docs.craft.do")
fi

[[ ${#problems[@]} -eq 0 ]] && exit 0

reason="План нарушает правила: $(printf '%s; ' "${problems[@]}")План отвечает ЧТО меняется и КУДА ложится — механика, команды, проверка и порядок в него не выносятся; отсылки к блокам даются кликабельными ссылками docs.craft.do, не голым ID. Перепиши план и запиши снова."
jq -cn --arg r "$reason" '{hookSpecificOutput:{hookEventName:"PreToolUse",permissionDecision:"deny",permissionDecisionReason:$r}}'
exit 0
