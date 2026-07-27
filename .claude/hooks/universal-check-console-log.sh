#!/usr/bin/env bash
# Stop-хук: предупреждение (НЕ блок) о console.log в JS/TS-файлах, правленных
# за сессию. Портирован из ECC (check-console-log.js): отладочные логи легко
# забываются в коде — хук напоминает убрать их до коммита.
#
# Механика: из stdin-JSON события Stop берётся transcript_path, из JSONL
# транскрипта — file_path всех вызовов Edit/Write/MultiEdit за сессию. Файлы
# фильтруются: существующие .ts/.tsx/.js/.jsx вне node_modules и тестов
# (*.test.*, *.spec.*, __tests__). В отфильтрованных ищется console.log;
# строки с маркером // keep-console (осознанный лог) не считаются.
#
# Нашёл → stdout-предупреждение со списком файл:строка. Ничего не нашёл или
# нет транскрипта → тихий exit 0. Fail open на всём неожиданном.
set -u

# Project-уровень уступает user-уровню (install.sh) — не гейтим дважды.
if [[ -n "${CLAUDE_PROJECT_DIR:-}" && "$0" == "$CLAUDE_PROJECT_DIR"/* \
      && -e "$HOME/.claude/hooks/$(basename "$0")" ]]; then
  exit 0
fi

input="$(cat)"
tp="$(jq -r '.transcript_path // ""' <<<"$input" 2>/dev/null)" || exit 0
[[ -n "$tp" && -f "$tp" ]] || exit 0

files="$(jq -r 'try (.message.content[]?
                     | select(.type=="tool_use")
                     | select(.name=="Edit" or .name=="Write" or .name=="MultiEdit")
                     | .input.file_path // empty)' "$tp" 2>/dev/null | sort -u)"
[[ -n "$files" ]] || exit 0

found=""
while IFS= read -r f; do
  [[ -z "$f" || ! -f "$f" ]] && continue
  case "$f" in
    *node_modules/*) continue ;;
    *.test.*|*.spec.*|*__tests__*) continue ;;
  esac
  case "$f" in
    *.ts|*.tsx|*.js|*.jsx) ;;
    *) continue ;;
  esac
  hits="$(grep -n 'console\.log' "$f" 2>/dev/null | grep -v 'keep-console' | cut -d: -f1)"
  [[ -z "$hits" ]] && continue
  while IFS= read -r ln; do
    [[ -n "$ln" ]] && found+="  $f:$ln"$'\n'
  done <<<"$hits"
done <<<"$files"

if [[ -n "$found" ]]; then
  echo "console.log в правленных за сессию файлах:"
  printf '%s' "$found"
  echo "Убери или замени логгером; осознанный лог — пометь комментарием // keep-console."
fi
exit 0
