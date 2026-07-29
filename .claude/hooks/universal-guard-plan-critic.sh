#!/usr/bin/env bash
# PreToolUse на ExitPlanMode: не показывать Владу план, трогающий системную
# зону, пока эту его версию не обкатал plan-critic. Рычаг инцидента «показал
# системный план без критика» — правило про обкатку живёт в Craft-доке «План
# правок», хук лишь не даёт его пропустить молча.
#
# Перехват именно на ПОКАЗЕ, а не на записи файла плана: критик читает план
# из файла, поэтому гейт на записи заблокировал бы само планирование.
#
# Известная дыра (сознательная): содержимое плана в вызов показа рантайм не
# передаёт, поэтому гейт судит по файлу плана, записанному в этой сессии
# (universal-mark-plan-file.sh). Файла нет — гейт молчит.
#
# Обхода по CRAFT_AUTONOMOUS здесь нет намеренно: автономный прогон планов не
# показывает, а флаг-лазейка в гейте — приглашение ею воспользоваться.
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

sid="${CLAUDE_CODE_SESSION_ID:-default}"
plan="${CRAFT_PLAN_FILE:-$(cat "${CRAFT_PLAN_FILE_MARKER:-/tmp/plan-file.${sid}.path}" 2>/dev/null)}"
[[ -n "$plan" && -r "$plan" ]] || exit 0

# Системная зона: правило (юнит [система] в плане), скилл, хук, настройки,
# CLAUDE.md. Триггер намеренно шире источника — «План правок» пускает без
# критика точечную правку системной зоны по дословной формулировке Влада, а
# машинного различителя «дословно от Влада» против «сочинил агент» нет.
grep -qE '\[система|\.claude/|CLAUDE\.md' "$plan" || exit 0

want="$(sha256sum "$plan" 2>/dev/null | cut -d' ' -f1)"
have="$(cat "${CRAFT_PLAN_CRITIC_MARKER:-/tmp/plan-critic.${sid}.done}" 2>/dev/null)"
[[ -n "$want" && "$want" == "$have" ]] && exit 0

jq -cn '{hookSpecificOutput:{hookEventName:"PreToolUse",permissionDecision:"deny",permissionDecisionReason:"План трогает системную зону (правило, скилл, хук, CLAUDE.md), а plan-critic эту его версию не обкатывал. Запусти агента plan-critic на файле плана, отработай замечания и показывай план после этого."}}'
exit 0
