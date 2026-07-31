#!/usr/bin/env bash
# Stop-хук: гейт закрытия разбора инцидента.
#
# Разбор закрывается самой слабой ступенью (правкой текста правила), а евал —
# машинная проверка урока — не заводится: дефект виден только когда Влад
# спросит «а где тесты». Хук ловит это в момент завершения хода.
#
# Предикат (не просто вопрос — проверяемый факт по транскрипту хода):
#   взведён маркер сигнала инцидента (ставит universal-detect-incident.sh)
#   И в ходе была ЗАПИСЬ УРОКА — правка .claude/{skills,hooks,agents,rules,
#     commands} или craft_write
#   И НЕТ ни правки под evals/, ни письменного отказа («евал не завожу»)
# → один блок с чек-листом закрытия; повторное завершение проходит.
#
# Каждый НОВЫЙ сигнал инцидента снимает отметку «уже напоминали» (detect-incident),
# поэтому второй инцидент подряд в одной сессии гейтится заново.
# Анти-цикл: stop_hook_active → молчим. Автоном и евалы — молчим (некому
# закрывать разбор). Пустой session-id → молчим: маркер общий на все сессии,
# ложный блок дороже пропуска. Fail quiet везде.
set -u

if [[ -n "${CLAUDE_PROJECT_DIR:-}" && "$0" == "$CLAUDE_PROJECT_DIR"/* \
      && -e "$HOME/.claude/hooks/$(basename "$0")" ]]; then
  exit 0
fi

[[ -n "${CRAFT_AUTONOMOUS:-}" || -n "${CRAFT_EVAL:-}" ]] && exit 0

sid="${CLAUDE_CODE_SESSION_ID:-}"
[[ -n "$sid" ]] || exit 0

armed="${INCIDENT_CLOSURE_MARKER:-/tmp/incident-closure.$sid.armed}"
[[ -f "$armed" ]] || exit 0
reminded="${armed%.armed}.reminded"
[[ -f "$reminded" ]] && exit 0

input="$(cat)"
active="$(jq -r '.stop_hook_active // false' <<<"$input" 2>/dev/null)" || exit 0
[[ "$active" == "true" || "${CLAUDE_STOP_HOOK_ACTIVE:-}" == "true" ]] && exit 0

tr_path="$(jq -r '.transcript_path // ""' <<<"$input" 2>/dev/null)"
[[ -n "$tr_path" && -f "$tr_path" ]] || exit 0

# Транскрипт — jsonl всей сессии; смотрим хвост (текущий ход и ближайший
# контекст): запись урока, евал-артефакт, письменный отказ.
tail_txt="$(tail -c 400000 "$tr_path" 2>/dev/null)" || exit 0

lesson=0; evalproof=0
grep -qE '"file_path":"[^"]*/\.claude/(skills|hooks|agents|rules|commands)/' <<<"$tail_txt" && lesson=1
grep -qE '__craft_write' <<<"$tail_txt" && lesson=1
[[ "$lesson" -eq 0 ]] && exit 0

grep -qE '"file_path":"[^"]*/evals/' <<<"$tail_txt" && evalproof=1
grep -qiE 'евал не завожу|евалом не выражается|прогоном не выражается' <<<"$tail_txt" && evalproof=1
[[ "$evalproof" -eq 1 ]] && exit 0

: > "$reminded" 2>/dev/null || true

reason="Разбор инцидента не закрыт. Проверь по чек-листу: (1) названа ли ступень рычага словом; (2) приёмка поведенческая, а не «текст записан»; (3) заведён евал-кейс в evals/ или письменно обоснован отказ; (4) проверено, не рецидив ли это — тогда ступень обязана быть выше прежней. Закрыл или обосновал — завершай, повторное завершение пройдёт."

jq -cn --arg r "$reason" '{"decision":"block","reason":$r}'
exit 0
