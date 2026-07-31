#!/usr/bin/env bash
# ExitPlanMode, две роли по имени события:
#   PreToolUse  — не показывать Владу юнит, который он в этой сессии уже одобрял;
#   PostToolUse — одобренный план оставляет хеши своих юнитов в накопителе.
# Обе роли в одном файле, чтобы разборщик юнитов существовал в единственном виде:
# разъехавшиеся копии дали бы хеши, которые никогда не совпадут.
#
# Сравниваются только сущностные юниты — заголовок любого уровня, начинающийся с типа
# в квадратных скобках. «Ответы», «Следующими планами» и прочие разделы повторяются из
# плана в план по своей природе.
#
# Нормализация до хеша: код-фенсы вырезаются (дословный текст чужого плана в примере —
# не юнит этого плана), пустые строки не учитываются (иначе разделитель перед
# следующим заголовком менял бы хеш одного и того же юнита).
#
# PLAN_DELTA=off — аварийный выключатель, как FACT_GATE=off у факт-гейта.
# Fail open на всём неожиданном: сломанный гейт не должен клинить работу.
set -u

# Уступаем user-level копии — иначе двойной прогон на локальной машине.
if [[ -n "${CLAUDE_PROJECT_DIR:-}" && "$0" == "$CLAUDE_PROJECT_DIR"/* \
      && -e "$HOME/.claude/hooks/$(basename "$0")" ]]; then
  exit 0
fi

[[ "${PLAN_DELTA:-}" == "off" ]] && exit 0

input="$(cat)"
tool="$(jq -r '.tool_name // ""' <<<"$input" 2>/dev/null)" || exit 0
[[ "$tool" == "ExitPlanMode" ]] || exit 0
event="$(jq -r '.hook_event_name // "PreToolUse"' <<<"$input" 2>/dev/null)"

sid="${CLAUDE_CODE_SESSION_ID:-default}"
plan="${CRAFT_PLAN_FILE:-$(cat "${CRAFT_PLAN_FILE_MARKER:-/tmp/plan-file.${sid}.path}" 2>/dev/null)}"
store="${CRAFT_PLAN_DELTA_STORE:-/tmp/plan-delta.${sid}.hashes}"
[[ -n "$plan" && -r "$plan" ]] || exit 0

units() {  # печатает «хеш<таб>заголовок» на каждый сущностный юнит файла
  local title="" buf="" line
  emit() {
    [[ -n "$title" ]] || return 0
    printf '%s\t%s\n' "$(printf '%s' "$buf" | sha256sum | cut -d' ' -f1)" "$title"
  }
  while IFS= read -r line || [[ -n "$line" ]]; do
    [[ -z "${line//[[:space:]]/}" ]] && continue
    if [[ "$line" =~ ^#+[[:space:]]*\[ ]]; then
      emit; title="[${line#*\[}"; buf="$line"$'\n'; continue
    fi
    if [[ "$line" =~ ^#+[[:space:]] ]]; then
      emit; title=""; buf=""; continue
    fi
    [[ -n "$title" ]] && buf+="$line"$'\n'
  done < <(awk 'BEGIN{f=0} /^[[:space:]]*```/{f=!f; next} !f' "$1")
  emit
}

now="$(units "$plan")"
[[ -n "$now" ]] || exit 0

if [[ "$event" == "PostToolUse" ]]; then
  cut -f1 <<<"$now" >> "$store" 2>/dev/null || true
  exit 0
fi

[[ -s "$store" ]] || exit 0
approved="$(cat "$store" 2>/dev/null)"

total=0; rep=0; names=""
while IFS=$'\t' read -r h t; do
  [[ -n "$h" ]] || continue
  total=$((total + 1))
  grep -qxF -- "$h" <<<"$approved" && { rep=$((rep + 1)); names+="«$t» "; }
done <<<"$now"

[[ "$rep" -eq 0 ]] && exit 0          # чистая дельта
[[ "$rep" -eq "$total" ]] && exit 0   # перепоказ того же плана целиком

jq -cn --arg r "План повторяет уже одобренные юниты: ${names}. Одобренное повторно не показывается — оставь только изменившееся с прошлого одобрения, а изменённый юнит пометь ревизией с причиной. Аварийный выключатель — PLAN_DELTA=off." \
  '{hookSpecificOutput:{hookEventName:"PreToolUse",permissionDecision:"deny",permissionDecisionReason:$r}}'
exit 0
