#!/usr/bin/env bash
# SessionStart hook (устанавливается в ~/.claude): живой инжект правил общения
# с Владом из Craft в сессии ВНЕ craft-репо — код-сессии, чужие проекты. Канон
# правил остаётся в Craft (правится с телефона, действует сразу во всех
# сессиях); никакого коммитнутого кэша — только живое чтение на старте.
#
# В craft-репо не работает: там роутер (включая «Общение с Владом») инжектится
# целиком через craft-inject-router.sh + импорт в CLAUDE.md.
#
# ДОСТАВКА — ФАЙЛОМ, НЕ ПЕЧАТЬЮ. stdout SessionStart-хука обрезается на 10 000
# символах, а страница правил общения давно длиннее: печатью терялась её
# последняя треть, причём молча. Поэтому тело уходит в файл-снимок, а его
# подтягивает `@`-импорт в пользовательском ~/.claude/CLAUDE.md — у импортов
# потолка нет (проверено живой пробой: вложенный вызов из чужого каталога видит
# импортированный текст). В stdout остаётся только строка-отчёт.
#
# Снимок ОБЩИЙ для всех сессий, поэтому он никогда не пустеет и не сносится:
# перезапись идёт атомарно, уже готовым текстом, и соседняя сессия читает либо
# прежнюю версию, либо новую, но не пустоту. Свежесть показывает метка времени
# внутри снимка, а не его снос: при мёртвой сети прежний текст остаётся, но
# датирован — агент видит, что читает вчерашнее.
#
# Канал не установлен (в личном CLAUDE.md нет строки импорта) — хук печатает
# тело в stdout по-старому: молчать нельзя, правила обязательны.
#
# Fail quiet: нет env/сети → короткая пометка-директива вместо правил.
set -u

# В craft-репо (там есть свой инжектор роутера) — не дублируем. Снимок при этом
# НЕ трогаем: он общий, и опустошение снесло бы правила у соседней код-сессии.
if [[ -n "${CLAUDE_PROJECT_DIR:-}" \
      && -e "$CLAUDE_PROJECT_DIR/.claude/hooks/craft-inject-router.sh" ]]; then
  exit 0
fi

self="$(realpath "$0" 2>/dev/null || echo "$0")"
# shellcheck disable=SC1091
. "$(dirname "$self")/_load-env.sh" 2>/dev/null || true

COMMUNICATION_ID="${CRAFT_COMMUNICATION_ID:-7485dec3-f1c2-4f17-e88a-72994f772b84}"
CLAUDE_MD="${CRAFT_USER_CLAUDE_MD:-$HOME/.claude/CLAUDE.md}"
SNAPSHOT="${CRAFT_BEHAVIOR_SNAPSHOT:-$HOME/.claude/craft-live/behavior-rules.md}"
# Печать тела остаётся аварийным путём, и её потолок прежний: stdout капится.
BUDGET=9500

fallback() {
  echo "⚠️ Правила общения с Владом не загружены из Craft ($1). Они обязательны в любой сессии: при доступном Craft MCP прочитай блок $COMMUNICATION_ID (blocks get --depth -1) перед содержательными ответами; без Craft — держи минимум: структура вместо полотна, выбор — кнопками, факт отдельно от догадки, ссылки кликабельными."
  exit 0
}

# Канал жив, только пока импорт снимка стоит в личном CLAUDE.md: без него файл
# никто не прочитает, и тело обязано идти печатью.
channel_ready() {
  [[ -r "$CLAUDE_MD" ]] && grep -qF "$SNAPSHOT" "$CLAUDE_MD" 2>/dev/null
}

# Тестовый шов стоит ДО загрузки .env и сети: прогон кейсов идёт без живого
# Craft, а проверяемое — куда уходит тело и что остаётся в stdout, не сам текст
# правил. Пустой CRAFT_API_BASE снаружи для этого не годится: .env грузится с
# set -a и перекрывает переданное окружение.
if [[ -n "${BEHAVIOR_RULES_TEST_MD:-}" ]]; then
  [[ -r "$BEHAVIOR_RULES_TEST_MD" ]] || fallback "тестовый шов без источника"
  md="$(cat "$BEHAVIOR_RULES_TEST_MD" 2>/dev/null)"
  stamp="тестовый инжект"
else
  base="${CRAFT_API_BASE:-}"
  [[ -z "$base" ]] && fallback "CRAFT_API_BASE не задан"
  base="${base%/}"

  md="$(curl -sS --fail --max-time 30 --retry 2 --retry-all-errors \
    -H 'Accept: text/markdown' \
    "$base/blocks?id=$COMMUNICATION_ID&maxDepth=-1" 2>/dev/null)" || fallback "сеть/API недоступны"
  [[ -z "$md" ]] && fallback "пустой ответ API"

  stamp="$(date -u +%FT%TZ)"
fi
out="=== Craft: «Общение с Владом», живой инжект ($stamp) ===
$md
=== конец правил общения — действуют в этой сессии ==="

if [[ -n "${BEHAVIOR_RULES_TEST_SNAPSHOT:-}" ]] || channel_ready; then
  # Атомарная перезапись: соседняя сессия читает целую версию, не половину.
  mkdir -p "$(dirname "$SNAPSHOT")" 2>/dev/null || true
  if printf '%s\n' "$out" > "${SNAPSHOT}.tmp" 2>/dev/null \
     && mv -f "${SNAPSHOT}.tmp" "$SNAPSHOT" 2>/dev/null; then
    echo "Правила общения с Владом обновлены из Craft ($(wc -c < "$SNAPSHOT") байт, $stamp) — полный текст в контексте через импорт снимка, обрезки нет."
    exit 0
  fi
  rm -f "${SNAPSHOT}.tmp" 2>/dev/null
  # Снимок не записался — печатаем тело, иначе правила пропадут молча.
fi

if [[ ${#out} -gt $BUDGET ]]; then
  out="${out:0:$BUDGET}
…[обрезано бюджетом инжекта — канал импорта не установлен, поставь его прогоном install.sh; дочитай источник живьём: Craft MCP blocks get $COMMUNICATION_ID --depth -1]"
fi

printf '%s\n' "$out"
exit 0
