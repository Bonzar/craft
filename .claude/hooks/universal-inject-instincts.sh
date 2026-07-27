#!/usr/bin/env bash
# SessionStart hook (устанавливается в ~/.claude): живой инжект топ-инстинктов
# из Craft-дока «🤖 Инстинкты агента» в код-сессии. Инжектится жёсткий бюджет —
# не больше MAX_INSTINCTS блоков, отфильтрованных по скоупу текущего проекта
# ([<проект>] или [global] в начале блока) — исследование памяти агентов:
# инжект не растёт с ростом стора.
#
# Пустая или недоступная страница «Инстинкты» → молчание (это гипотезы, не
# правила; без них сессия полноценна). В craft-репо не работает.
set -u

if [[ -n "${CLAUDE_PROJECT_DIR:-}" \
      && -e "$CLAUDE_PROJECT_DIR/.claude/hooks/craft-inject-router.sh" ]]; then
  exit 0
fi

self="$(realpath "$0" 2>/dev/null || echo "$0")"
# shellcheck disable=SC1091
. "$(dirname "$self")/_load-env.sh" 2>/dev/null || true

INSTINCTS_PAGE_ID="${CRAFT_INSTINCTS_PAGE_ID:-b2b08ac0-b42e-382c-b1ee-8de5fb339fc6}"
MAX_INSTINCTS="${CRAFT_MAX_INSTINCTS:-6}"

base="${CRAFT_API_BASE:-}"
[[ -z "$base" ]] && exit 0
base="${base%/}"

md="$(curl -sS --fail --max-time 30 --retry 2 --retry-all-errors \
  -H 'Accept: text/markdown' \
  "$base/blocks?id=$INSTINCTS_PAGE_ID&maxDepth=1" 2>/dev/null)" || exit 0
[[ -z "$md" ]] && exit 0

# Скоуп текущего проекта: слаг git-remote → arc-проект → global.
scope="global"
if remote="$(git -C "${CLAUDE_PROJECT_DIR:-$PWD}" remote get-url origin 2>/dev/null)"; then
  scope="$(basename "${remote%.git}")"
elif command -v arc >/dev/null 2>&1 \
     && arc info >/dev/null 2>&1; then
  scope="arcadia-crm"
fi

# Строки-инстинкты: «[скоуп] при … → …». Берём свой скоуп + global, топ сверху
# (консолидация держит сильнейшие выше).
picked="$(grep -E '^\[' <<<"$md" \
  | grep -E "^\[(${scope}|global)\]" \
  | head -n "$MAX_INSTINCTS")"
[[ -z "$picked" ]] && exit 0

echo "=== Инстинкты агента (наблюдения, НЕ канон; скоуп: $scope) ==="
echo "$picked"
echo "=== Это гипотезы из прошлых сессий: применяй с проверкой; противоречат правилам или фактам — правила главнее ==="
exit 0
