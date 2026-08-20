#!/usr/bin/env bash
# PostToolUse на AskUserQuestion: регистратор пар «вопрос + выбранный ответ»
# для семантического разрешения план-гейта. Каждый завершённый вопрос с
# ответом ложится записью в файл окна разрешений у маркера периметра; гейт
# (universal-guard-plan-gate.sh) на правке вне периметра сверяет её с этим
# окном классификатором — явное разрешение открывает цель. Дословного лейбла
# и маркера тапа больше нет: решение «это было разрешение» принимает модель,
# а не совпадение строки.
#
# Схема входа снята с живого следа этой сессии (см. отладочный след ниже):
# выбранный ответ лежит в .tool_response.answers — карта «текст вопроса →
# лейбл выбранной опции». Ответ по вопросу не разобрался — пара не пишется:
# окна из одних вопросов без ответов не бывает. Событие рождается только
# настоящим тапом: PostToolUse не срабатывает на отклонённый вопрос.
#
# Окно — последние 5 записей (вместе с репликами-указаниями, которые пишет
# universal-plan-gate-reset.sh), старые вытесняются. Гасит окно только смена
# сессии — файл в /tmp с session-id.
set -u

# Уступка второму вызову того же события: хук зарегистрирован и project-level, и
# пользовательски (install.sh), а после сноса симлинков обе регистрации ведут в
# ОДИН файл — различить их путями нельзя. Признак — метка занятия события.
# shellcheck disable=SC1091
. "$(dirname "$(realpath "$0" 2>/dev/null || echo "$0")")/_hook-once.sh" 2>/dev/null || true

[[ -n "${CRAFT_AUTONOMOUS:-}" ]] && exit 0

input="$(cat)"
declare -F hook_once >/dev/null 2>&1 && { hook_once "$input" || exit 0; }
# Отладочный след входа: по нему проверяются факты о схеме tool_response.
printf '%s' "$input" > "/tmp/plan-gate-button-last-input.${CLAUDE_CODE_SESSION_ID:-default}.json" 2>/dev/null || true

tool="$(jq -r '.tool_name // ""' <<<"$input" 2>/dev/null)" || exit 0
[[ "$tool" == "AskUserQuestion" ]] || exit 0

sid="${CLAUDE_CODE_SESSION_ID:-}"
if [[ -n "${CRAFT_PLAN_GATE_MARKER:-}" ]]; then
  marker="$CRAFT_PLAN_GATE_MARKER"
elif [[ -n "$sid" ]]; then
  marker="/tmp/craft-plan-gate.${sid}.approved"
else
  exit 0
fi
qa="${marker}.qa-window"

# Пары «вопрос + ответ»: все вопросы вызова, у которых есть выбранный ответ.
pairs="$(jq -r '
  (.tool_response.answers // .tool_input.answers // {}) as $a
  | [(.tool_input.questions // [])[]
     | .question as $q
     | ($a[$q] // "") as $ans
     | select(($q // "") != "" and $ans != "")
     | "## Запись: вопрос\nВопрос: \($q)\nОтвет: \($ans)\n"]
  | join("\n")' <<<"$input" 2>/dev/null)"
[[ -z "${pairs//[[:space:]]/}" ]] && exit 0

{ cat "$qa" 2>/dev/null; printf '%s\n' "$pairs"; } > "${qa}.tmp" 2>/dev/null || exit 0
# Окно: оставить последние 5 записей (по заголовкам «## Запись»).
awk '
  /^## Запись/ { n++ }
  { line[NR] = $0; rec[NR] = n }
  END { for (i = 1; i <= NR; i++) if (rec[i] > n - 5) print line[i] }
' "${qa}.tmp" > "$qa" 2>/dev/null || true
rm -f "${qa}.tmp" 2>/dev/null
exit 0
