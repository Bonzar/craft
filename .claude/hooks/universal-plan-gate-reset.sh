#!/usr/bin/env bash
# UserPromptSubmit: a new message from Влад starts a new turn — the previous plan
# approval no longer covers it, so clear the plan-gate marker. The next craft_write
# must be preceded by a fresh approved plan (guard-plan-gate.sh). Never blocks the
# message (no stdout, exit 0).
#
# СЛУЖЕБНОЕ СОБЫТИЕ ходом не считается. Этим же событием приходят уведомления о
# завершении фоновых подагентов, сообщения наших стоп-хуков и продолжения хода после
# прерывания — сброс по ним обнулял одобрение посреди исполнения. Признак — дословное
# начало текста; последний якорь наш собственный, его ставят сами стоп-хуки. Пометку
# прерывания в якоря НЕ берём: она наблюдалась отдельным сообщением лишь однажды, а
# склеенная со словами Влада проглотила бы настоящую реплику.
# Пустой текст (нет поля, битый вход, нет разборщика — так же подаёт вход тестовый
# раннер) считаем настоящим ходом: лишний сброс дешевле пропущенного.
#
# Отметку критика (plan-critic.*.done) reset НЕ трогает: гейт сверяет её СОДЕРЖИМОЕ
# с хешем файла плана, поэтому правка плана обесценивает отметку сама, а гасить её
# репликой значило бы гонять критика по неизменившемуся тексту.
set -u
input="$(cat)"
prompt="$(jq -r '.prompt // ""' <<<"$input" 2>/dev/null)"
case "$prompt" in
  '<task-notification>'*|'Stop hook feedback:'*) exit 0 ;;
  'Continue from where you left off'*|'[stop-hook]'*) exit 0 ;;
esac
[[ -n "${CRAFT_AUTONOMOUS:-}" ]] && exit 0
marker="${CRAFT_PLAN_GATE_MARKER:-/tmp/craft-plan-gate.${CLAUDE_CODE_SESSION_ID:-default}.approved}"
rm -f "$marker" 2>/dev/null || true
exit 0
