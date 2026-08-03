#!/usr/bin/env bash
# Отмечает, что plan-critic ОТРАБОТАЛ, и по какой версии плана. Отметка — хеш файла
# плана; гейт (universal-guard-plan-critic.sh) сверяет его с текущим содержимым, поэтому
# переписанный после обкатки план отметку не наследует.
#
# Две роли в одном файле, роль по имени события:
#   PostToolUse    — подагент запущен. Ответ инструмента при фоновом запуске — расписка о
#                    запуске, а не вердикт, поэтому отмечать здесь нельзя: убитый или
#                    упавший критик оставлял бы действительную отметку. Запоминаем
#                    ОЖИДАНИЕ: идентификатор подагента из расписки и хеш плана на этот
#                    момент. Расписки нет (подагент отработал синхронно и вернул текст) —
#                    отмечаем сразу, вердикт на руках.
#   UserPromptSubmit — уведомление о завершении задачи. Тот же идентификатор и успешный
#                    статус превращают ожидание в отметку; иной статус ожидание снимает.
#
# Хеш берётся в момент ЗАПУСКА: критик читал именно ту версию плана. Правка плана после
# запуска отметку обесценит сама — гейт сверяет её с текущим файлом.
#
# Различитель подагента — subagent_type: без него гейт обходился бы запуском любого.
#
# Fail quiet: не смог посчитать хеш — отметки нет, гейт просто не пропустит.
set -u

# Хеш файла. Запасная команда обязательна: на маке из README основной нет, хеш вышел бы
# пустым, и гейт молча выключился бы. Формат первого токена у обеих команд одинаков.
hash_of() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" 2>/dev/null | cut -d' ' -f1
  elif command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$1" 2>/dev/null | cut -d' ' -f1
  fi
}

# Уступаем user-level копии — иначе двойной прогон на локальной машине.
if [[ -n "${CLAUDE_PROJECT_DIR:-}" && "$0" == "$CLAUDE_PROJECT_DIR"/* \
      && -e "$HOME/.claude/hooks/$(basename "$0")" ]]; then
  exit 0
fi

input="$(cat)"
sid="${CLAUDE_CODE_SESSION_ID:-default}"
marker="${CRAFT_PLAN_CRITIC_MARKER:-/tmp/plan-critic.${sid}.done}"
pending="${CRAFT_PLAN_CRITIC_PENDING:-/tmp/plan-critic.${sid}.pending}"
runs="${CRAFT_PLAN_CRITIC_RUNS:-/tmp/plan-critic.${sid}.runs}"
event="$(jq -r '.hook_event_name // "PostToolUse"' <<<"$input" 2>/dev/null)" || exit 0

# Счётчик завершённых прогонов критика — машинное «Плато»: гейт по нему пропускает показ,
# когда обкатка перестала двигать план. Считается ПРОГОН, а не версия файла: правка по
# замечаниям меняет хеш, и счёт по хешу всегда был бы единица. Инкремент идёт всюду, где
# ставится отметка, — иначе синхронный вердикт плато не набирает.
bump_runs() {
  local n
  n="$(cat "$runs" 2>/dev/null)"
  [[ "$n" =~ ^[0-9]+$ ]] || n=0
  printf '%s\n' "$((n + 1))" > "$runs" 2>/dev/null || true
}

if [[ "$event" == "UserPromptSubmit" ]]; then
  prompt="$(jq -r '.prompt // ""' <<<"$input" 2>/dev/null)"
  [[ "$prompt" == *"<task-notification>"* ]] || exit 0
  id="$(sed -n 's/.*<task-id>\([^<]*\)<\/task-id>.*/\1/p' <<<"$prompt" | head -1)"
  [[ -n "$id" ]] || exit 0
  hash="$(grep -m1 -- "^${id}	" "$pending" 2>/dev/null | cut -f2)"
  [[ -n "$hash" ]] || exit 0
  # Ожидание закрыто в любом исходе: повторное уведомление о той же задаче отметку не
  # переставит, а незавершённый критик её не получит вовсе.
  grep -v -- "^${id}	" "$pending" > "${pending}.tmp" 2>/dev/null && mv "${pending}.tmp" "$pending"
  [[ "$prompt" == *"<status>completed</status>"* ]] || exit 0
  printf '%s\n' "$hash" > "$marker" 2>/dev/null || true
  bump_runs
  exit 0
fi

jq -e '(.tool_input.subagent_type // "") == "plan-critic"' >/dev/null 2>&1 <<<"$input" || exit 0

plan="${CRAFT_PLAN_FILE:-$(cat "${CRAFT_PLAN_FILE_MARKER:-/tmp/plan-file.${sid}.path}" 2>/dev/null)}"
[[ -n "$plan" && -r "$plan" ]] || exit 0
hash="$(hash_of "$plan")"
[[ -n "$hash" ]] || exit 0

resp="$(jq -r '.tool_response | tostring' <<<"$input" 2>/dev/null)"
id="$(sed -n 's/.*agentId: \([A-Za-z0-9_-]*\).*/\1/p' <<<"$resp" | head -1)"
if [[ -n "$id" ]]; then
  printf '%s\t%s\n' "$id" "$hash" >> "$pending" 2>/dev/null || true
else
  printf '%s\n' "$hash" > "$marker" 2>/dev/null || true
  bump_runs
fi
exit 0
