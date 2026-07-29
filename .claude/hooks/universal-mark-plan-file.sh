#!/usr/bin/env bash
# PostToolUse на запись файла плана: запоминает путь плана ЭТОЙ сессии, по
# которому судят гейт критика (universal-guard-plan-critic.sh) и его метка
# (universal-mark-plan-critic.sh). Каталог планов общий на все сессии и
# проекты, поэтому «самый свежий файл» там — ненадёжный признак: параллельная
# сессия подсунет чужой план.
#
# Планы подагентов (в имени «-agent-») не запоминаются: гейт судит о плане,
# который показывают Владу, а не о черновиках подагентов.
#
# Fail quiet: сломанная запоминалка не должна клинить работу.
set -u

# Когда тот же хук установлен и на user-level (~/.claude, через install.sh),
# project-level регистрация уступает ему — иначе хук отработает дважды.
if [[ -n "${CLAUDE_PROJECT_DIR:-}" && "$0" == "$CLAUDE_PROJECT_DIR"/* \
      && -e "$HOME/.claude/hooks/$(basename "$0")" ]]; then
  exit 0
fi

input="$(cat)"
fp="$(jq -r '.tool_input.file_path // ""' <<<"$input" 2>/dev/null)" || exit 0
[[ "$fp" == */plans/*.md ]] || exit 0
[[ "$fp" == *-agent-* ]] && exit 0

store="${CRAFT_PLAN_FILE_MARKER:-/tmp/plan-file.${CLAUDE_CODE_SESSION_ID:-default}.path}"
printf '%s' "$fp" > "$store" 2>/dev/null || true
exit 0
