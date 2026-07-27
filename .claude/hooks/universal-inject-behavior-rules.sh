#!/usr/bin/env bash
# SessionStart hook (устанавливается в ~/.claude): живой инжект правил общения
# с Владом из Craft в сессии ВНЕ craft-репо — код-сессии, чужие проекты. Канон
# правил остаётся в Craft (правится с телефона, действует сразу во всех
# сессиях); никакого коммитнутого кэша — только живое чтение на старте.
#
# В craft-репо не работает: там роутер (включая «Общение с Владом») инжектится
# целиком через craft-inject-router.sh + импорт в CLAUDE.md.
#
# Бюджет: stdout SessionStart-хука капится ~10K символов, и «Общение с
# Владом» (~9K живьём) занимает его почти целиком — поэтому этот хук несёт
# ТОЛЬКО правила общения. Страница «Правила кода» (появится в Craft, фаза 6)
# инжектится отдельным хуком со своим бюджетом, не сюда.
#
# Fail quiet: нет env/сети → короткая пометка-директива вместо правил.
set -u

# В craft-репо (там есть свой инжектор роутера) — не дублируем.
if [[ -n "${CLAUDE_PROJECT_DIR:-}" \
      && -e "$CLAUDE_PROJECT_DIR/.claude/hooks/craft-inject-router.sh" ]]; then
  exit 0
fi

self="$(realpath "$0" 2>/dev/null || echo "$0")"
# shellcheck disable=SC1091
. "$(dirname "$self")/_load-env.sh" 2>/dev/null || true

COMMUNICATION_ID="${CRAFT_COMMUNICATION_ID:-7485dec3-f1c2-4f17-e88a-72994f772b84}"
BUDGET=9500

fallback() {
  echo "⚠️ Правила общения с Владом не загружены из Craft ($1). Они обязательны в любой сессии: при доступном Craft MCP прочитай блок $COMMUNICATION_ID (blocks get --depth -1) перед содержательными ответами; без Craft — держи минимум: структура вместо полотна, выбор — кнопками, факт отдельно от догадки, ссылки кликабельными."
  exit 0
}

base="${CRAFT_API_BASE:-}"
[[ -z "$base" ]] && fallback "CRAFT_API_BASE не задан"
base="${base%/}"

md="$(curl -sS --fail --max-time 30 --retry 2 --retry-all-errors \
  -H 'Accept: text/markdown' \
  "$base/blocks?id=$COMMUNICATION_ID&maxDepth=-1" 2>/dev/null)" || fallback "сеть/API недоступны"
[[ -z "$md" ]] && fallback "пустой ответ API"

out="=== Craft: «Общение с Владом», живой инжект ($(date -u +%FT%TZ)) ===
$md
=== конец правил общения — действуют в этой сессии ==="

if [[ ${#out} -gt $BUDGET ]]; then
  out="${out:0:$BUDGET}
…[обрезано бюджетом инжекта — дочитай источник живьём: Craft MCP blocks get $COMMUNICATION_ID --depth -1]"
fi

printf '%s\n' "$out"
exit 0
