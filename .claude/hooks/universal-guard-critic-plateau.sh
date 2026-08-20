#!/usr/bin/env bash
# PreToolUse на Task|Agent: машинный гейт плато обкатки — ЧЕТВЁРТЫЙ запуск
# критика планов по одному плану блокируется. Правило «после трёх прогонов —
# показ с открытым вопросом» перестаёт быть дисциплиной агента: счётчик
# завершённых прогонов ведёт universal-mark-plan-critic.sh, обнуляет его
# одобрение плана (universal-plan-gate-approve.sh) — этот гейт счётчик только
# читает. Чужие подагенты (не plan-critic) не трогаются.
#
# CRAFT_AUTONOMOUS=1 обходит гейт: автономные прогоны планов не показывают.
# Fail open на всём неожиданном: сломанный гейт не должен клинить работу.
set -u

if [[ -n "${CLAUDE_PROJECT_DIR:-}" && "$0" == "$CLAUDE_PROJECT_DIR"/* \
      && -e "$HOME/.claude/hooks/$(basename "$0")" ]]; then
  exit 0
fi

[[ -n "${CRAFT_AUTONOMOUS:-}" ]] && exit 0

input="$(cat)"
tool="$(jq -r '.tool_name // ""' <<<"$input" 2>/dev/null)" || exit 0
case "$tool" in Task|Agent) ;; *) exit 0 ;; esac
# Гейтятся роли, которые КРУТЯТ счётчик, — те же, что ставят отметку: сводящий веера и
# одиночный критик. Юнитные критики и критик швов прогон не засчитывают, и блокировать их
# нечем: круг веера считается один раз, по своему сводящему.
agent="$(jq -r '.tool_input.subagent_type // ""' <<<"$input" 2>/dev/null)"
case "$agent" in plan-critic|plan-critic-verdict) ;; *) exit 0 ;; esac

runs="${CRAFT_PLAN_CRITIC_RUNS:-/tmp/plan-critic.${CLAUDE_CODE_SESSION_ID:-default}.runs}"
n="$(cat "$runs" 2>/dev/null)"
[[ "$n" =~ ^[0-9]+$ ]] || exit 0
if [[ "$n" -ge 3 ]]; then
  jq -cn --arg r "Заблокировано гейтом плато: по этому плану уже $n завершённых прогона критика. Плато — показывай план Владу с открытым вопросом об остатке замечаний, а не гоняй обкатку дальше. Счётчик обнулит одобрение плана." \
    '{hookSpecificOutput:{hookEventName:"PreToolUse",permissionDecision:"deny",permissionDecisionReason:$r}}'
fi
exit 0
