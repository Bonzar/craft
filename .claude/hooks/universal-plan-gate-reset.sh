#!/usr/bin/env bash
# UserPromptSubmit: a new message from Влад starts a new turn — the previous plan
# approval no longer covers it, so clear the plan-gate marker. The next craft_write
# must be preceded by a fresh approved plan (guard-plan-gate.sh). Never blocks the
# message (no stdout, exit 0).
#
# СЛУЖЕБНОЕ СОБЫТИЕ ходом не считается: сброс по нему обнулял одобрение посреди
# исполнения. Якоря и правила их пополнения — в service-anchors.txt рядом.
# Пустой текст (нет поля, битый вход, нет разборщика — так же подаёт вход тестовый
# раннер) считаем настоящим ходом: лишний сброс дешевле пропущенного.
#
# Отметку критика (plan-critic.*.done) reset НЕ трогает: гейт сверяет её СОДЕРЖИМОЕ
# с хешем файла плана, поэтому правка плана обесценивает отметку сама, а гасить её
# репликой значило бы гонять критика по неизменившемуся тексту.
set -u
input="$(cat)"
prompt="$(jq -r '.prompt // ""' <<<"$input" 2>/dev/null)"
SELF="$(realpath "$0" 2>/dev/null || echo "$0")"
ANCHORS="${CRAFT_SERVICE_ANCHORS:-$(cd "$(dirname "$SELF")" && pwd)/service-anchors.txt}"
# Метка «ход начат служебным сообщением» — её читает guard-plan-service-turn.sh, чтобы
# не показывать план повторно, пока Влад не ответил. Ставится здесь, а не в самом гейте:
# словарь якорей уже разобран, а гейт видит только событие показа.
serviceturn="${CRAFT_SERVICE_TURN_MARKER:-/tmp/plan-service-turn.${CLAUDE_CODE_SESSION_ID:-default}}"
planshown="${CRAFT_PLAN_SHOWN_MARKER:-/tmp/plan-shown.${CLAUDE_CODE_SESSION_ID:-default}}"
criticruns="${CRAFT_PLAN_CRITIC_RUNS:-/tmp/plan-critic.${CLAUDE_CODE_SESSION_ID:-default}.runs}"
criticpend="${CRAFT_PLAN_CRITIC_PENDING:-/tmp/plan-critic.${CLAUDE_CODE_SESSION_ID:-default}.pending}"
while IFS= read -r anchor || [[ -n "$anchor" ]]; do
  [[ -z "$anchor" || "$anchor" == \#* ]] && continue
  [[ "$prompt" == "$anchor"* ]] && { : > "$serviceturn" 2>/dev/null || true; exit 0; }
done < "$ANCHORS" 2>/dev/null
# Реплика Влада: ход снова его, показ плана разрешён — снимаем метку служебного хода,
# хеш показанного, счётчик прогонов критика и его ожидания, разговор начинается заново.
# Ожидания снимаются вместе со счётчиком: критик, запущенный до реплики, дозавершился бы
# уже в новом разговоре и накрутил чужой счётчик — плато набралось бы прогонами, которых
# в этом разговоре не было. Цена — такой критик отметки не поставит, нужен новый прогон.
rm -f "$serviceturn" "$planshown" "$criticruns" "$criticpend" 2>/dev/null || true
[[ -n "${CRAFT_AUTONOMOUS:-}" ]] && exit 0
marker="${CRAFT_PLAN_GATE_MARKER:-/tmp/craft-plan-gate.${CLAUDE_CODE_SESSION_ID:-default}.approved}"
rm -f "$marker" 2>/dev/null || true
exit 0
