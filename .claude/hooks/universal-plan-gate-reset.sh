#!/usr/bin/env bash
# UserPromptSubmit: обычная реплика Влада ПЕРИМЕТР ГЕЙТА НЕ ГАСИТ — маркер
# одобрения хранит список целей плана и живёт до реплики, начинающейся фразой
# «закрой гейт», либо до смены сессии (файл в /tmp с id). Хук чистит только
# пометки хода (служебный ход, показ плана, ожидания критика) и никогда не
# блокирует сообщение (no stdout, exit 0).
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
# Счётчик прогонов критика реплика НЕ снимает: обкатка привязана к плану, а не к ходу —
# «покажи» или замечание к плану её не завершают. Обнуление по реплике делало плато
# недостижимым в живом диалоге. Снимает его одобрение плана (plan-gate-approve.sh).
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
rm -f "$serviceturn" "$planshown" "$criticpend" 2>/dev/null || true
[[ -n "${CRAFT_AUTONOMOUS:-}" ]] && exit 0
# Периметр гасит только явная фраза: сверка префиксом сообщения, как у якорей.
# Вместе с маркером уходят его производные (микро-планы кнопки, след деградации
# классификатора) и непотраченный тап с сайдкаром вопроса: после фразы дельта
# не помнит кнопочных одобрений и сверять правки не с чем.
if [[ "$prompt" == "закрой гейт"* ]]; then
  sid="${CLAUDE_CODE_SESSION_ID:-}"
  if [[ -n "${CRAFT_PLAN_GATE_MARKER:-}" ]]; then
    marker="$CRAFT_PLAN_GATE_MARKER"
  elif [[ -n "$sid" ]]; then
    marker="/tmp/craft-plan-gate.${sid}.approved"
  else
    marker=""
  fi
  [[ -n "$marker" ]] && rm -f "$marker" "${marker}.button-plans" "${marker}.classifier-degraded" 2>/dev/null || true
  if [[ -n "${PLAN_GATE_BUTTON_MARKER:-}" ]]; then
    bmarker="$PLAN_GATE_BUTTON_MARKER"
  elif [[ -n "$sid" ]]; then
    bmarker="/tmp/plan-gate-button.${sid}.one"
  else
    bmarker=""
  fi
  [[ -n "$bmarker" ]] && rm -f "$bmarker" "${bmarker}.question" 2>/dev/null || true
fi
exit 0
