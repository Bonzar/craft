#!/usr/bin/env bash
# Классификатор план-гейта: LLM-проверка, которую регулярка дать не может.
# Живёт в tools/, а не в .claude/hooks/ намеренно: реверс-smoke тестов требует
# регистрации каждого файла hooks/ в settings, а помощник — не хук.
#
# Режимы (первый аргумент), описание правки — на stdin:
#   match <план>  — соответствует ли правка одобренному плану;
#   throwaway     — очевидная ли это времянка (плана нет или правка мимо него);
#   delta <снапшот> <новый план> — какие юниты нового плана повторяют одобренный.
#
# Ответ одной строкой в stdout:
#   MATCH | NOMATCH:<причина> | THROWAWAY | NOTHROWAWAY:<причина>
#   | CLEAN | REPEATS:<заголовки> | UNAVAILABLE
# Разбор ответа модели сверяет СНАЧАЛА отрицательную форму: положительная
# («СООТВЕТСТВУЕТ») входит в отрицательную («НЕ СООТВЕТСТВУЕТ») подстрокой.
#
# Вложенный вызов модели: haiku, один ход, без инструментов, из /tmp (вне
# проекта — проектные SessionStart-хуки не поднимаются), env сессии очищен тем
# же набором, что у раннера евалов, CRAFT_AUTONOMOUS=1 глушит гейты обоих
# контуров. Таймаут, обрыв и нераспознанный ответ = UNAVAILABLE — решение о
# судьбе правки принимает гейт, не помощник.
#
# Env: PLAN_CLASSIFIER_CMD — команда модели (тесты подменяют моком);
#      PLAN_CLASSIFIER_TIMEOUT — бюджет секунд; дефолт 20 = медиана трёх
#        холодных замеров в облачном контейнере (6.2/6.8/8.1 с) с двойным
#        запасом, заведомо меньше лимита PreToolUse-хука;
#      PLAN_CLASSIFIER=off — принудительный UNAVAILABLE (аварийный выключатель).
set -u

[[ "${PLAN_CLASSIFIER:-}" == "off" ]] && { echo "UNAVAILABLE"; exit 0; }

mode="${1:-match}"
budget="${PLAN_CLASSIFIER_TIMEOUT:-20}"
cmd="${PLAN_CLASSIFIER_CMD:-claude}"
change="$(cat)"

prompt=""
case "$mode" in
  match)
    planfile="${2:-}"
    [[ -n "$planfile" && -r "$planfile" ]] || { echo "UNAVAILABLE"; exit 0; }
    prompt="Ты — проверка соответствия правки одобренному плану. Ниже план целиком и одна правка. Ответь ровно одной строкой: НЕ СООТВЕТСТВУЕТ: ‹причина одним предложением› — либо СООТВЕТСТВУЕТ. Правка соответствует, когда выполняет обещанное планом в этой цели, включая прямые следствия обещанного. Самовольное изменение сверх плана — НЕ СООТВЕТСТВУЕТ.

=== ПЛАН ===
$(cat "$planfile")

=== ПРАВКА ===
$change"
    ;;
  throwaway)
    prompt="Ты — проверка класса правки. Ниже одна правка без плана. Ответь ровно одной строкой: ВРЕМЯНКА — если это очевидно временное (отладочная печать, временный файл рядом с работой, закомментированный на пробу блок) — либо НЕ ВРЕМЯНКА: ‹причина одним предложением›. Сомневаешься — НЕ ВРЕМЯНКА.

=== ПРАВКА ===
$change"
    ;;
  delta)
    snapfile="${2:-}"; newplan="${3:-}"
    [[ -n "$snapfile" && -r "$snapfile" && -n "$newplan" && -r "$newplan" ]] \
      || { echo "UNAVAILABLE"; exit 0; }
    prompt="Ты — проверка дельты планов. Ниже одобренный план и новый план. Перечисли заголовки юнитов нового плана, которые повторяют одобренные без содержательных изменений — переформулировка тем же смыслом считается повтором; ответ одной строкой: ПОВТОРЫ: ‹заголовки через запятую› — либо ДЕЛЬТА ЧИСТАЯ. Юнит с ревизией, новой целью или изменённым дословным текстом повтором не считается.

=== ОДОБРЕННЫЙ ПЛАН ===
$(cat "$snapfile")

=== НОВЫЙ ПЛАН ===
$(cat "$newplan")"
    ;;
  *) echo "UNAVAILABLE"; exit 0 ;;
esac

answer="$(cd /tmp && timeout "$budget" env \
    -u CLAUDE_CODE_SESSION_ID -u CLAUDE_CODE_CHILD_SESSION \
    -u CLAUDE_PID -u CLAUDE_CODE_REMOTE_SESSION_ID \
    CRAFT_AUTONOMOUS=1 \
    "$cmd" -p "$prompt" --model claude-haiku-4-5-20251001 \
    --max-turns 1 < /dev/null 2>/dev/null)" || { echo "UNAVAILABLE"; exit 0; }

# Последняя непустая строка ответа; отрицательные формы сверяются первыми.
last="$(grep -v '^[[:space:]]*$' <<<"$answer" | tail -1)"
case "$mode" in
  match)
    if grep -qF 'НЕ СООТВЕТСТВУЕТ' <<<"$last"; then
      printf 'NOMATCH:%s\n' "${last#*НЕ СООТВЕТСТВУЕТ}" | sed 's/^NOMATCH::*/NOMATCH:/'
    elif grep -qF 'СООТВЕТСТВУЕТ' <<<"$last"; then echo "MATCH"
    else echo "UNAVAILABLE"; fi ;;
  throwaway)
    if grep -qF 'НЕ ВРЕМЯНКА' <<<"$last"; then
      printf 'NOTHROWAWAY:%s\n' "${last#*НЕ ВРЕМЯНКА}" | sed 's/^NOTHROWAWAY::*/NOTHROWAWAY:/'
    elif grep -qF 'ВРЕМЯНКА' <<<"$last"; then echo "THROWAWAY"
    else echo "UNAVAILABLE"; fi ;;
  delta)
    if grep -qF 'ПОВТОРЫ:' <<<"$last"; then
      printf 'REPEATS:%s\n' "${last#*ПОВТОРЫ:}"
    elif grep -qF 'ДЕЛЬТА ЧИСТАЯ' <<<"$last"; then echo "CLEAN"
    else echo "UNAVAILABLE"; fi ;;
esac
exit 0
