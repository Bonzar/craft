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

# Уступка второму вызову того же события: хук зарегистрирован и project-level, и
# пользовательски (install.sh), а после сноса симлинков обе регистрации ведут в
# ОДИН файл — различить их путями нельзя. Признак — метка занятия события.
# shellcheck disable=SC1091
. "$(dirname "$(realpath "$0" 2>/dev/null || echo "$0")")/_hook-once.sh" 2>/dev/null || true

input="$(cat)"
declare -F hook_once >/dev/null 2>&1 && { hook_once "$input" || exit 0; }
fp="$(jq -r '.tool_input.file_path // ""' <<<"$input" 2>/dev/null)" || exit 0
[[ "$fp" == */plans/*.md ]] || exit 0
[[ "$fp" == *-agent-* ]] && exit 0

store="${CRAFT_PLAN_FILE_MARKER:-/tmp/plan-file.${CLAUDE_CODE_SESSION_ID:-default}.path}"
printf '%s' "$fp" > "$store" 2>/dev/null || true
exit 0
