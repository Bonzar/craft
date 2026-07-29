#!/usr/bin/env bash
# PostToolUse на завершение подагента: отмечает, что отработал ИМЕННО
# plan-critic, и по какой версии плана. Отметка — хеш файла плана; гейт
# (universal-guard-plan-critic.sh) сверяет его с текущим содержимым, поэтому
# переписанный после обкатки план отметку не наследует.
#
# Различитель — subagent_type в tool_input: без него гейт обходился бы
# запуском любого подагента.
#
# Fail quiet: не смог посчитать хеш — отметки нет, гейт просто не пропустит.
set -u

# Уступаем user-level копии — иначе двойной прогон на локальной машине.
if [[ -n "${CLAUDE_PROJECT_DIR:-}" && "$0" == "$CLAUDE_PROJECT_DIR"/* \
      && -e "$HOME/.claude/hooks/$(basename "$0")" ]]; then
  exit 0
fi

input="$(cat)"
jq -e '(.tool_input.subagent_type // "") == "plan-critic"' >/dev/null 2>&1 <<<"$input" || exit 0

sid="${CLAUDE_CODE_SESSION_ID:-default}"
plan="${CRAFT_PLAN_FILE:-$(cat "${CRAFT_PLAN_FILE_MARKER:-/tmp/plan-file.${sid}.path}" 2>/dev/null)}"
[[ -n "$plan" && -r "$plan" ]] || exit 0

sha256sum "$plan" 2>/dev/null | cut -d' ' -f1 \
  > "${CRAFT_PLAN_CRITIC_MARKER:-/tmp/plan-critic.${sid}.done}" 2>/dev/null || true
exit 0
