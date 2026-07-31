#!/usr/bin/env bash
# Stop-хук: энфорсер фактов завершения автономных рутин (редукция delivery-gate
# из ECC — сознательная: пороги диска/библиотек не наш контекст, mtime/cutoff
# хук проверить не может, он не знает рутину; сами факты живут в секциях
# «Факты завершения» SKILL-доков рутин).
#
# В автономном режиме (CRAFT_AUTONOMOUS=1) первое завершение сессии блокируется
# ОДИН раз директивой самопроверки фактов; повторное проходит (маркер, как у
# instinct-flush). Headless-евалы исключены признаком CRAFT_EVAL=1 — иначе хук
# ломал бы детерминизм каждого евал-кейса лишним ходом.
# Анти-цикл: stop_hook_active → молчим. Fail quiet.
set -u

if [[ -n "${CLAUDE_PROJECT_DIR:-}" && "$0" == "$CLAUDE_PROJECT_DIR"/* \
      && -e "$HOME/.claude/hooks/$(basename "$0")" ]]; then
  exit 0
fi

[[ -n "${CRAFT_AUTONOMOUS:-}" ]] || exit 0
[[ -n "${CRAFT_EVAL:-}" ]] && exit 0

input="$(cat)"
active="$(jq -r '.stop_hook_active // false' <<<"$input" 2>/dev/null)" || exit 0
[[ "$active" == "true" || "${CLAUDE_STOP_HOOK_ACTIVE:-}" == "true" ]] && exit 0

marker="${ROUTINE_FACTS_MARKER:-/tmp/routine-facts.${CLAUDE_CODE_SESSION_ID:-default}.reminded}"
[[ -f "$marker" ]] && exit 0
: > "$marker" 2>/dev/null || true

reason="[stop-hook] Перед завершением сверь секцию «Факты завершения» своего SKILL-дока: каждый факт проверен чтением или командой, не заявлен. Несходящийся факт — блокер в отчёте, не примечание. Сверил — завершай, повторное завершение пройдёт."

jq -cn --arg r "$reason" '{"decision":"block","reason":$r}'
exit 0
