#!/usr/bin/env bash
# SessionStart hook (устанавливается в ~/.claude): живой инжект ЯДРА «Правил
# кода» из Craft в код-сессии вне craft-репо. Канон — Craft; никакого
# коммитнутого кэша.
#
# Инжектится ТОЛЬКО корень дока (maxDepth=1): ядро + диспетчер доменных
# страниц. Полные доменные страницы (TypeScript, React, …) агент читает
# целиком по триггеру домена — так велит сам диспетчер. Сжимать их в инжект
# нельзя (урок в «Обслуживании памяти»).
#
# В craft-репо не работает: craft-сессии код не пишут, ядро им не нужно.
# Fail quiet: нет env/сети → короткая директива-фолбек.
set -u

if [[ -n "${CLAUDE_PROJECT_DIR:-}" \
      && -e "$CLAUDE_PROJECT_DIR/.claude/hooks/craft-inject-router.sh" ]]; then
  exit 0
fi

self="$(realpath "$0" 2>/dev/null || echo "$0")"
# shellcheck disable=SC1091
. "$(dirname "$self")/_load-env.sh" 2>/dev/null || true

CODE_RULES_ID="${CRAFT_CODE_RULES_ID:-d3f184fb-2c70-6058-0797-d9851f4b16a7}"
BUDGET=9500

fallback() {
  echo "⚠️ Ядро «Правил кода» не загружено из Craft ($1). Перед правками кода прочитай его живьём: Craft MCP blocks get $CODE_RULES_ID --depth 1 (ядро + диспетчер доменных страниц; страницу своего домена читай целиком)."
  exit 0
}

base="${CRAFT_API_BASE:-}"
[[ -z "$base" ]] && fallback "CRAFT_API_BASE не задан"
base="${base%/}"

md="$(curl -sS --fail --max-time 30 --retry 2 --retry-all-errors \
  -H 'Accept: text/markdown' \
  "$base/blocks?id=$CODE_RULES_ID&maxDepth=1" 2>/dev/null)" || fallback "сеть/API недоступны"
[[ -z "$md" ]] && fallback "пустой ответ API"

out="=== Craft: «⚙️ Правила кода» — ядро, живой инжект ($(date -u +%FT%TZ)) ===
$md
=== конец ядра. Работаешь с доменом — прочитай его страницу ЦЕЛИКОМ (Craft MCP, blocks get по ссылке из диспетчера, --depth -1) до первых правок кода ==="

if [[ ${#out} -gt $BUDGET ]]; then
  out="${out:0:$BUDGET}
…[обрезано бюджетом — дочитай ядро живьём: blocks get $CODE_RULES_ID --depth 1]"
fi

printf '%s\n' "$out"
exit 0
