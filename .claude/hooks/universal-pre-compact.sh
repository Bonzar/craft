#!/usr/bin/env bash
# PreCompact-хук: перед компакцией контекста записывает след сессии в
# ~/.claude/session-data/precompact-<session_id>.md — timestamp, transcript_path
# и cwd, чтобы resume-session мог найти контекст после потери. Портирован из
# ECC (pre-compact.js) в минимальном виде: без LLM-резюме, только якорь.
#
# Тихий и fail open: любая ошибка — молчаливый exit 0, компакцию не задерживаем.
set -u

# Project-уровень уступает user-уровню (install.sh) — не пишем дважды.
if [[ -n "${CLAUDE_PROJECT_DIR:-}" && "$0" == "$CLAUDE_PROJECT_DIR"/* \
      && -e "$HOME/.claude/hooks/$(basename "$0")" ]]; then
  exit 0
fi

input="$(cat)"
sid="$(jq -r '.session_id // ""' <<<"$input" 2>/dev/null)" || sid=""
[[ -z "$sid" ]] && sid="unknown"
sid="$(printf '%s' "$sid" | tr -c 'a-zA-Z0-9_-' '_')"
tp="$(jq -r '.transcript_path // ""' <<<"$input" 2>/dev/null)" || tp=""
cwd="$(jq -r '.cwd // ""' <<<"$input" 2>/dev/null)" || cwd=""

dir="$HOME/.claude/session-data"
mkdir -p "$dir" 2>/dev/null || exit 0

{
  echo "# PreCompact snapshot"
  echo ""
  echo "- timestamp: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "- transcript_path: ${tp:-—}"
  echo "- cwd: ${cwd:-$PWD}"
} > "$dir/precompact-$sid.md" 2>/dev/null || true

exit 0
