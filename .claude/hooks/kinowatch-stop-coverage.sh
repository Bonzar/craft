#!/usr/bin/env bash
# Stop-хук (БЛОКИРУЮЩИЙ): не давать закончить ход, пока в kinowatch есть
# площадки без работающего инструмента.
#
# Зачем. Инцидент: агент отчитывался о частичной работе как о законченной —
# критерием готовности было состояние его собственной работы, а не запроса
# Влада. Формулировка правила эту привычку не остановила, поэтому счёт вынесен
# из головы агента в команду, которую он не решает запускать.
#
# Число берётся из состояния последнего прогона, а не из пометок агента:
# `--coverage` считает площадку покрытой только по LastOk — времени живого
# ответа её канала, которое пишет исключительно опрос.
#
# Self-gating — как у universal-stop-quality-gate.sh:
#   - в проекте есть kinowatch/;
#   - за сессию правились файлы kinowatch (по транскрипту);
#   - есть состояние последнего прогона (KINOWATCH_STATE или файл по умолчанию).
# Ничего из этого нет → тихий exit 0: хук про kinowatch, а не про весь репозиторий.
#
# Анти-зацикливание: повторный Stop → тихий exit 0. Хук напоминает один раз за
# ход; его дело — не дать проскочить мимо числа, а не держать сессию силой.
set -u

[[ "${CLAUDE_STOP_HOOK_ACTIVE:-}" == "true" ]] && exit 0
input="$(cat)"
[[ "$(jq -r '.stop_hook_active // false' <<<"$input" 2>/dev/null)" == "true" ]] && exit 0

proj="${CLAUDE_PROJECT_DIR:-$PWD}"
[[ -d "$proj/kinowatch" ]] || exit 0

# Правились ли файлы kinowatch за эту сессию.
tp="$(jq -r '.transcript_path // ""' <<<"$input" 2>/dev/null)" || exit 0
[[ -n "$tp" && -f "$tp" ]] || exit 0
touched="$(jq -r '
  select(.message.content != null)
  | .message.content[]?
  | select(.type == "tool_use")
  | select(.name == "Write" or .name == "Edit" or .name == "MultiEdit")
  | .input.file_path // ""
' "$tp" 2>/dev/null | grep -c '/kinowatch/' || true)"
[[ "${touched:-0}" -gt 0 ]] || exit 0

# Состояние последнего прогона. Без него считать нечего — и это не молчаливый
# пропуск: об отсутствии состояния хук говорит, иначе «нет файла» стало бы
# способом обойти счёт.
state="${KINOWATCH_STATE:-$proj/kinowatch/.last-run.json}"
bin="${KINOWATCH_BIN:-$HOME/.local/bin/kinowatch}"
[[ -x "$bin" ]] || bin=""

if [[ ! -f "$state" ]]; then
  jq -n --arg r "kinowatch: состояния последнего прогона нет ($state) — покрытие не посчитано. Прогони --enrich и --probe, сохрани результат в это состояние и завершай ход снова." \
    '{decision:"block", reason:$r}'
  exit 0
fi

if [[ -z "$bin" ]]; then
  # Бинарника нет — считать нечем. Fail open, но с явным словом: тихое
  # прохождение выглядело бы как «покрытие полное».
  jq -n --arg r "kinowatch: бинарника нет ($HOME/.local/bin/kinowatch), покрытие не посчитано. Собери его и завершай ход снова." \
    '{decision:"block", reason:$r}'
  exit 0
fi

out="$("$bin" --coverage --short <"$state" 2>&1)"
code=$?
[[ $code -eq 0 ]] && exit 0

jq -n --arg r "kinowatch: работа не закончена — $out. Список незакрытых площадок: kinowatch --coverage <$state. Заверши их или предъяви улику источника по каждой." \
  '{decision:"block", reason:$r}'
exit 0
