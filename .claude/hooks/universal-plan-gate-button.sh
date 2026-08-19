#!/usr/bin/env bash
# PostToolUse на AskUserQuestion: ОДНОРАЗОВАЯ кнопка разрешения план-гейта.
# Влад тапнул опцию с дословным лейблом «Разрешаю без плана (одну правку)» —
# хук ставит одноразовый маркер; гейт (universal-guard-plan-gate.sh) тратит
# его на первый вызов, прошедший именно по нему (эфемерная правка маркер не
# трогает), снимая атомарным переименованием. Любой другой ответ кнопки
# маркер не ставит. Событие рождается только настоящим тапом: PostToolUse
# не срабатывает на отклонённый или упавший вопрос.
#
# Отладочный след входа — тем же приёмом, что у гейта: по нему проверяется
# факт, что tool_response несёт выбранный пользователем ответ.
set -u

if [[ -n "${CLAUDE_PROJECT_DIR:-}" && "$0" == "$CLAUDE_PROJECT_DIR"/* \
      && -e "$HOME/.claude/hooks/$(basename "$0")" ]]; then
  exit 0
fi

[[ -n "${CRAFT_AUTONOMOUS:-}" ]] && exit 0

input="$(cat)"
printf '%s' "$input" > "/tmp/plan-gate-button-last-input.${CLAUDE_CODE_SESSION_ID:-default}.json" 2>/dev/null || true

tool="$(jq -r '.tool_name // ""' <<<"$input" 2>/dev/null)" || exit 0
[[ "$tool" == "AskUserQuestion" ]] || exit 0

LABEL="Разрешаю без плана (одну правку)"
# Ответ пользователя ищется по всему tool_response: точная строка лейбла.
resp="$(jq -r '.tool_response | tostring' <<<"$input" 2>/dev/null)"
grep -qF "$LABEL" <<<"$resp" || exit 0

sid="${CLAUDE_CODE_SESSION_ID:-}"
if [[ -n "${PLAN_GATE_BUTTON_MARKER:-}" ]]; then
  bmarker="$PLAN_GATE_BUTTON_MARKER"
elif [[ -n "$sid" ]]; then
  bmarker="/tmp/plan-gate-button.${sid}.one"
else
  exit 0
fi
: > "$bmarker" 2>/dev/null || true
exit 0
