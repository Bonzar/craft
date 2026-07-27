#!/usr/bin/env bash
# PreToolUse(Bash) guard: блокирует ОДИНОЧНЫЙ длинный `sleep N` — анти-паттерн
# «поставить произвольную задержку и потом проверить». Будиться надо на СОБЫТИИ:
#   - run_in_background:true → харнесс будит агента на exit процесса;
#   - блокирующий сторож в фоне, который выходит ровно на событии:
#       tail -n +1 -F log | grep -q -m1 'PATTERN'
#       npx wait-on tcp:PORT | http://host/health
#       curl --retry N --retry-delay 1 --retry-connrefused -sf URL
#       fswatch -1 path
#   - текущее состояние фоновой задачи читать через TaskOutput (без sleep).
#
# Намеренно КОНСЕРВАТИВНЫЙ: ловит только команду, которая ЦЕЛИКОМ = `sleep <число>`.
# Пропускается (НЕ блок):
#   - sleep внутри пайпа/цикла/&&/||/; (легитимные tight-loop сторожа);
#   - короткий sleep < 3с (мелкая пауза-устаканивание);
#   - осознанный таймер с маркером `# timed-wait` (дать поработать N сек и снять метрику).
set -u

# Project-уровень уступает user-уровню (install.sh) — не гейтим дважды.
if [[ -n "${CLAUDE_PROJECT_DIR:-}" && "$0" == "$CLAUDE_PROJECT_DIR"/* \
      && -e "$HOME/.claude/hooks/$(basename "$0")" ]]; then
  exit 0
fi

cmd=$(cat | jq -r '.tool_input.command // ""')

# Осознанный таймер — пропускаем.
if printf '%s' "$cmd" | grep -q '# *timed-wait'; then
  exit 0
fi

# Нормализуем края.
trimmed=$(printf '%s' "$cmd" | sed -E 's/^[[:space:]]+//; s/[[:space:]]+$//')

# Только одиночный sleep: ВСЯ команда = sleep <число>[смхд]?, без ; | && ||.
if printf '%s' "$trimmed" | grep -Eq '^sleep[[:space:]]+[0-9]+(\.[0-9]+)?[smhd]?$'; then
  num=$(printf '%s' "$trimmed" | grep -oE '[0-9]+(\.[0-9]+)?' | head -1)
  unit=$(printf '%s' "$trimmed" | grep -oE '[smhd]$')

  block=0
  # Любая единица кроме секунд (m/h/d) → заведомо долго.
  if [ -n "$unit" ] && [ "$unit" != "s" ]; then
    block=1
  else
    whole=${num%%.*}
    if [ "${whole:-0}" -ge 3 ] 2>/dev/null; then
      block=1
    fi
  fi

  if [ "$block" = "1" ]; then
    jq -n '{
      hookSpecificOutput: {
        hookEventName: "PreToolUse",
        permissionDecision: "deny",
        permissionDecisionReason: "Одиночный sleep — произвольная задержка ожидания. Будись на событии, а не по таймеру: (1) запусти процесс с run_in_background:true — харнесс разбудит на exit; (2) для условия (строка в логе / порт / файл) повесь в фоне блокирующий сторож, который выходит на событии: tail -n +1 -F log | grep -q -m1 PATTERN | npx wait-on tcp:PORT|http://host/health | curl --retry N --retry-connrefused -sf URL | fswatch -1 path; (3) текущее состояние фоновой задачи читай через TaskOutput. Если это ОСОЗНАННЫЙ таймер (дать поработать N секунд и снять метрику) — добавь маркер: sleep N # timed-wait."
      }
    }'
  fi
fi

exit 0
