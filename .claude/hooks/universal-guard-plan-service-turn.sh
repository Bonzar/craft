#!/usr/bin/env bash
# PreToolUse на ExitPlanMode: не показывать план в ходе, начатом СЛУЖЕБНЫМ сообщением.
#
# План-режим при отсутствии Влада закрывается сам, следом приходит техническое
# продолжение хода — и показ соблазняет повторить. Повтор ничего не даёт: Влад не
# отвечал, закроется снова. Рычаг инцидента «четыре показа подряд после служебного
# продолжения»; правило — «Влад не в сети — не его отказ» в «Общении с Владом».
#
# Метку ставит и снимает universal-plan-gate-reset.sh: он уже разбирает словарь якорей
# (service-anchors.txt), а гейт видит только событие показа. Реплика Влада метку снимает.
#
# Fail open на всём неожиданном: сломанный гейт не должен клинить работу.
set -u

# Уступаем user-level копии — иначе двойной прогон на локальной машине.
if [[ -n "${CLAUDE_PROJECT_DIR:-}" && "$0" == "$CLAUDE_PROJECT_DIR"/* \
      && -e "$HOME/.claude/hooks/$(basename "$0")" ]]; then
  exit 0
fi

input="$(cat)"
tool="$(jq -r '.tool_name // ""' <<<"$input" 2>/dev/null)" || exit 0
[[ "$tool" == "ExitPlanMode" ]] || exit 0

marker="${CRAFT_SERVICE_TURN_MARKER:-/tmp/plan-service-turn.${CLAUDE_CODE_SESSION_ID:-default}}"
[[ -e "$marker" ]] || exit 0

jq -cn '{hookSpecificOutput:{hookEventName:"PreToolUse",permissionDecision:"deny",permissionDecisionReason:"Ход начат служебным сообщением, а не репликой Влада — показ плана не повторяется. План уже показан и закрылся сам: Влада не было. Дождись его реплики, ничего не выполняй и план текстом не пересказывай."}}'
exit 0
