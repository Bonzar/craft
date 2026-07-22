#!/usr/bin/env bash
# PreToolUse guard: block any mcp__Craft__craft_write call that writes via the raw
# --markdown flag instead of --json.
#
# Craft rule (see "MCP-механики Craft" in the router): writing/updating goes only
# through --json (a structural block object whose text sits in the "markdown"
# field). The bare --markdown FLAG is never used for writes — it normalises
# indentation to 0 and carries no structural fields, and update re-parses the
# text. This hook denies the call so the agent is forced to rebuild it as --json.
#
# Heuristic to avoid false positives: if the command already uses --json, allow it
# (the literal text "--markdown" may legitimately appear inside a --json payload's
# markdown field). Only a --markdown flag WITHOUT --json is blocked.
set -u

input="$(cat)"
tool="$(jq -r '.tool_name // ""' <<<"$input" 2>/dev/null)" || exit 0
[[ "$tool" == "mcp__Craft__craft_write" ]] || exit 0

cmd="$(jq -r '.tool_input.command // ""' <<<"$input" 2>/dev/null)" || exit 0

# Structural --json form is the correct one — allow (and its payload may contain
# the substring "--markdown" harmlessly).
if grep -qE '(^|[[:space:]])--json([[:space:]]|=)' <<<"$cmd"; then
  exit 0
fi

# Bare --markdown flag on a write → block.
if grep -qE '(^|[[:space:]])--markdown([[:space:]]|=|$)' <<<"$cmd"; then
  reason="Заблокировано правилом Craft: запись и правка идут только через --json (структурный блок-объект, текст в поле markdown). Голый флаг --markdown при записи не используется никогда — он нормализует отступ к 0 и не несёт структурных полей, а update перепарсивает текст. Пересобери команду через --json (см. «MCP-механики Craft»)."
  jq -cn --arg r "$reason" '{hookSpecificOutput:{hookEventName:"PreToolUse",permissionDecision:"deny",permissionDecisionReason:$r}}'
  exit 0
fi

exit 0
