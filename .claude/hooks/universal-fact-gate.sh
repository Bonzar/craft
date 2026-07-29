#!/usr/bin/env bash
# PreToolUse fact-gate on DESTRUCTIVE operations (перенос gateguard из ECC).
# Самопроверка «я уверен» перед деструктивом не работает — гейт останавливает
# принудительно и требует предъявить факты в чате; ПОВТОРНЫЙ идентичный вызов
# проходит (deny-once): повтор после предъявленных фактов = осознанное
# подтверждение.
#
# Одна универсальная копия, две ветки по имени инструмента (как план-гейт):
#   - Bash                → шелл-список: rm -rf, git reset --hard, git clean -f,
#                           git push --force, arc clean / branch -D /
#                           checkout --force / stash drop|clear /
#                           unmount --forget. Список стартовый, пополняется
#                           уроками. Покрытое локальными ask-гардами Влада
#                           (arc push --force, arc reset, amend) НЕ дублируем:
#                           ask требует человека — сильнее deny-once.
#   - суффикс __craft_write → blocks delete, documents delete,
#                           collections schema-update — всегда; blocks move —
#                           только вне CRAFT_AUTONOMOUS (перенос закрытых
#                           задач — белый список гигиены).
#
# CRAFT_AUTONOMOUS гейт НЕ байпасит (деструктив в автономе опаснее); байпас —
# только явный выключатель FACT_GATE=off.
# Состояние: маркер-файл на (session, sha256 нормализованной команды) в
# FACT_GATE_STATE_DIR (default /tmp) — герметизируется тестами. Счётчик полных
# deny-текстов: после трёх — краткий однострочник (анти-раздувание контекста).
# Fail open: сломанный гейт не должен клинить работу.
set -u

# Уступка project-копии user-копии (иначе двойной прогон в craft-сессиях).
if [[ -n "${CLAUDE_PROJECT_DIR:-}" && "$0" == "$CLAUDE_PROJECT_DIR"/* \
      && -e "$HOME/.claude/hooks/$(basename "$0")" ]]; then
  exit 0
fi

[[ "${FACT_GATE:-}" == "off" ]] && exit 0

input="$(cat)"
tool="$(jq -r '.tool_name // ""' <<<"$input" 2>/dev/null)" || exit 0

is_craft_write=0
if [[ "$tool" =~ __craft_write$ ]]; then
  is_craft_write=1
elif [[ "$tool" != "Bash" ]]; then
  exit 0
fi

cmd="$(jq -r '.tool_input.command // ""' <<<"$input" 2>/dev/null)"
[[ -n "$cmd" ]] || exit 0

hit=""   # shell | craft
if [[ "$is_craft_write" -eq 1 ]]; then
  if grep -qE '(blocks delete|documents delete|collections schema-update)' <<<"$cmd"; then
    hit="craft"
  elif [[ -z "${CRAFT_AUTONOMOUS:-}" ]] && grep -qE 'blocks move' <<<"$cmd"; then
    hit="craft"
  fi
else
  # rm с одновременными r и f (короткие сцепленные флаги в любом порядке или
  # длинные), git/arc-деструктив. Токенные границы, чтобы не ловить имена.
  # Данные — не команды (инцидент pr-body-force): heredoc-хвост отрезается,
  # кавычённые строки вырезаются, составные пары (push+force, branch+-D)
  # ищутся внутри ОДНОГО shell-сегмента, а не по всей строке.
  san="${cmd%%<<*}"
  if command -v perl >/dev/null 2>&1; then
    # slurp-режим: кавычённая строка бывает многострочной (PR-body) —
    # построчный sed её не вырежет.
    san="$(perl -0777 -pe "s/\"[^\"]*\"//gs; s/'[^']*'//gs" <<<"$san")"
  else
    san="$(sed -E "s/'[^']*'//g; s/\"[^\"]*\"//g" <<<"$san")"
  fi
  FORCE_TOK='(^|[[:space:]])(--force|-f)([[:space:]]|$)'
  RM_RE='(^|[[:space:]])rm[[:space:]]+((-[[:alnum:]]*[rR][[:alnum:]]*f)|(-[[:alnum:]]*f[[:alnum:]]*[rR])|(--recursive[[:space:]].*--force)|(--force[[:space:]].*--recursive))'
  GIT_RE='(^|[[:space:]])git[[:space:]]+((reset[[:space:]].*--hard)|(clean[[:space:]]+-[[:alnum:]]*f))'
  GIT_PUSH_RE='(^|[[:space:]])git[[:space:]]+push([[:space:]]|$)'
  ARC_RE='(^|[[:space:]])arc[[:space:]]+((clean([[:space:]]|$))|(checkout[[:space:]].*--force)|(stash[[:space:]]+(drop|clear))|(unmount[[:space:]].*--forget))'
  ARC_BRANCH_RE='(^|[[:space:]])arc[[:space:]]+branch([[:space:]]|$)'
  DELETE_TOK='(^|[[:space:]])-D([[:space:]]|$)'
  while IFS= read -r seg; do
    [[ -z "${seg//[[:space:]]/}" ]] && continue
    if grep -qE "$RM_RE" <<<"$seg" || grep -qE "$GIT_RE" <<<"$seg" || grep -qE "$ARC_RE" <<<"$seg"; then
      hit="shell"; break
    elif grep -qE "$GIT_PUSH_RE" <<<"$seg" && grep -qE "$FORCE_TOK" <<<"$seg"; then
      hit="shell"; break
    elif grep -qE "$ARC_BRANCH_RE" <<<"$seg" && grep -qE "$DELETE_TOK" <<<"$seg"; then
      hit="shell"; break
    fi
  done < <(tr ';|&' '\n' <<<"$san")
fi
[[ -z "$hit" ]] && exit 0

# --- deny-once: маркер по хэшу нормализованной команды -----------------------
norm="$(tr -s '[:space:]' ' ' <<<"$cmd")"
if command -v sha256sum >/dev/null 2>&1; then
  h="$(printf '%s' "$norm" | sha256sum | cut -d' ' -f1)"
elif command -v shasum >/dev/null 2>&1; then
  h="$(printf '%s' "$norm" | shasum -a 256 | cut -d' ' -f1)"
else
  h="$(printf '%s' "$norm" | cksum | tr ' ' '_')"
fi
sid="${CLAUDE_CODE_SESSION_ID:-default}"
dir="${FACT_GATE_STATE_DIR:-/tmp}"
marker="$dir/fact-gate.$sid.$h"
[[ -f "$marker" ]] && exit 0          # повторный идентичный вызов — проходит
: > "$marker" 2>/dev/null || true     # первый — ставим маркер и стопаем

# Счётчик полных deny-текстов за сессию.
countf="$dir/fact-gate.$sid.count"
n=0; [[ -f "$countf" ]] && n="$(cat "$countf" 2>/dev/null || echo 0)"
n=$((n + 1)); printf '%s' "$n" > "$countf" 2>/dev/null || true

deny() {
  jq -cn --arg r "$1" \
    '{hookSpecificOutput:{hookEventName:"PreToolUse",permissionDecision:"deny",permissionDecisionReason:$r}}'
  exit 0
}

if (( n > 3 )); then
  deny "Факт-гейт: предъяви факты и повтори вызов."
fi

if [[ "$hit" == "shell" ]]; then
  deny "Деструктивная команда остановлена факт-гейтом. Предъяви в чате факты: (1) что именно затронется — поимённый список; (2) однострочный откат; (3) дословная инструкция Влада или правило, разрешающее операцию. Предъявил — повтори ту же команду, второй вызов пройдёт."
else
  deny "Удаление в Craft остановлено факт-гейтом. Предъяви факты: (1) свежие бэклинки цели — кто ссылается и что сломается; (2) свежее чтение положения блока (родитель, соседи); (3) чем операция предписана. Предъявил — повтори команду."
fi
