#!/usr/bin/env bash
# PostToolUse hook (все инструменты): пополняет СИГНАЛЬНЫЙ буфер сессии для
# инстинкт-контура. Пишутся только сигнальные события — ошибки инструментов;
# полный поток вызовов не логируется (шум, из которого нечего дистиллировать).
# Инцидент-маркеры дописывает universal-detect-incident.sh тем же файлом.
#
# Буфер эфемерный (/tmp, per-session): это расходник для дистилляции в конце
# хода (universal-instinct-flush.sh), терять его не жалко. Fail quiet.
set -u

input="$(cat)"

# Ошибка инструмента: is_error в tool_response либо явное поле error.
is_err="$(jq -r '(.tool_response.is_error // .tool_response.isError // false) | tostring' <<<"$input" 2>/dev/null)" || exit 0
err_text=""
if [[ "$is_err" == "true" ]]; then
  err_text="$(jq -r '.tool_response.content // .tool_response.error // "" | tostring' <<<"$input" 2>/dev/null | head -c 300)"
else
  err_text="$(jq -r '.tool_response.error // "" | tostring' <<<"$input" 2>/dev/null | head -c 300)"
  [[ -z "$err_text" || "$err_text" == "null" ]] && exit 0
fi

tool="$(jq -r '.tool_name // "?"' <<<"$input" 2>/dev/null)"
buf="${OBSERVE_BUFFER:-/tmp/agent-observe.${CLAUDE_CODE_SESSION_ID:-default}.log}"
# Однострочная запись: перевод строки в тексте ошибки схлопывается.
printf 'tool-error %s: %s\n' "$tool" "$(tr '\n' ' ' <<<"$err_text")" >> "$buf" 2>/dev/null || true
exit 0
