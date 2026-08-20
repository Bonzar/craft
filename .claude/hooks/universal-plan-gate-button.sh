#!/usr/bin/env bash
# PostToolUse на AskUserQuestion: кнопка разрешения план-гейта — МИКРО-ПЛАН.
# Влад тапнул опцию с дословным лейблом «Разрешаю без плана (эту цель)» —
# хук ставит маркер тапа и сохраняет в сайдкар текст вопроса: гейт
# (universal-guard-plan-gate.sh) на трате дописывает цель правки в периметр
# и привязывает к ней этот вопрос как предмет сверки содержания. Любой
# другой ответ кнопки маркер не ставит. Новый тап до траты заменяет прежний
# целиком — и маркер, и сайдкар. Событие рождается только настоящим тапом:
# PostToolUse не срабатывает на отклонённый или упавший вопрос.
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

LABEL="Разрешаю без плана (эту цель)"
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
# Сайдкар вопроса: поле question вопроса, среди опций которого тапнутый лейбл;
# входы без options (историческая форма) — первый вопрос вызова. Вопросов нет
# вовсе — тап не распознан, маркер не ставится.
q="$(jq -r --arg L "$LABEL" '
  (.tool_input.questions // []) as $qs
  | ([$qs[] | select((.options // []) | any(.label == $L)) | .question] | first)
    // ($qs | first | .question) // empty' <<<"$input" 2>/dev/null)"
[[ -z "$q" ]] && exit 0
printf '%s\n' "$q" > "${bmarker}.question" 2>/dev/null || true
: > "$bmarker" 2>/dev/null || true
exit 0
