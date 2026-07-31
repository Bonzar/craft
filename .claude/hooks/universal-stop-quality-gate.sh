#!/usr/bin/env bash
# Stop-хук (БЛОКИРУЮЩИЙ): гейт качества JS/TS-правок сессии. Портирован из ECC
# (stop-format-typecheck.js): агент не заканчивает ход с неотформатированным
# или не проходящим типы кодом — нарушения возвращаются ему на починку.
#
# Self-gating — работает только когда:
#   - в проекте (CLAUDE_PROJECT_DIR или cwd) есть tsconfig.json/package.json;
#   - за сессию правились .ts/.tsx/.js/.jsx (по транскрипту, как в
#     universal-check-console-log.sh).
# Прогоняет по изменённым файлам: prettier --check (если prettier доступен
# через npx --no-install), затем tsc --noEmit (если есть tsconfig и tsc
# доступен). Всё с таймаутом 120с; нет тулзы или таймаут — fail open, не блок.
# Ошибки tsc фильтруются до правленных файлов: чужие (доправочные) ошибки
# проекта ход не блокируют.
#
# Есть нарушения → {"decision":"block","reason":…} — агент чинит и завершает
# снова. Анти-зацикливание: повторный Stop (env CLAUDE_STOP_HOOK_ACTIVE=true
# или stop_hook_active в stdin-JSON) → тихий exit 0.
set -u

# Project-уровень уступает user-уровню (install.sh) — не гейтим дважды.
if [[ -n "${CLAUDE_PROJECT_DIR:-}" && "$0" == "$CLAUDE_PROJECT_DIR"/* \
      && -e "$HOME/.claude/hooks/$(basename "$0")" ]]; then
  exit 0
fi

# Анти-зацикливание: этот Stop уже вызван из-под стоп-хука → пропуск.
[[ "${CLAUDE_STOP_HOOK_ACTIVE:-}" == "true" ]] && exit 0
input="$(cat)"
[[ "$(jq -r '.stop_hook_active // false' <<<"$input" 2>/dev/null)" == "true" ]] && exit 0

proj="${CLAUDE_PROJECT_DIR:-$PWD}"
[[ -f "$proj/tsconfig.json" || -f "$proj/package.json" ]] || exit 0

tp="$(jq -r '.transcript_path // ""' <<<"$input" 2>/dev/null)" || exit 0
[[ -n "$tp" && -f "$tp" ]] || exit 0

files_arr=()
while IFS= read -r f; do
  [[ -z "$f" || ! -f "$f" ]] && continue
  case "$f" in
    *node_modules/*) continue ;;
    *.test.*|*.spec.*|*__tests__*) continue ;;
  esac
  case "$f" in
    *.ts|*.tsx|*.js|*.jsx) files_arr+=("$f") ;;
  esac
done < <(jq -r 'try (.message.content[]?
                      | select(.type=="tool_use")
                      | select(.name=="Edit" or .name=="Write" or .name=="MultiEdit")
                      | .input.file_path // empty)' "$tp" 2>/dev/null | sort -u)
[[ ${#files_arr[@]} -gt 0 ]] || exit 0

# Таймаут-обёртка: coreutils timeout / gtimeout, без них — без таймаута.
run_to() {
  if command -v timeout >/dev/null 2>&1; then timeout 120 "$@"
  elif command -v gtimeout >/dev/null 2>&1; then gtimeout 120 "$@"
  else "$@"
  fi
}

problems=""
command -v npx >/dev/null 2>&1 && has_npx=1 || has_npx=0

# --- prettier --check по правленным файлам -----------------------------------
if [[ $has_npx -eq 1 ]] && (cd "$proj" && npx --no-install prettier --version) >/dev/null 2>&1; then
  out="$(cd "$proj" && run_to npx --no-install prettier --check "${files_arr[@]}" 2>&1)" && rc=0 || rc=$?
  if [[ $rc -ne 0 && $rc -ne 124 ]]; then
    problems+="prettier --check (почини: npx prettier --write <файлы>):"$'\n'
    problems+="$(head -30 <<<"$out")"$'\n'
  fi
fi

# --- tsc --noEmit (только при tsconfig; ошибки — лишь по правленным файлам) --
if [[ $has_npx -eq 1 && -f "$proj/tsconfig.json" ]] \
   && (cd "$proj" && npx --no-install tsc --version) >/dev/null 2>&1; then
  out="$(cd "$proj" && run_to npx --no-install tsc --noEmit --pretty false 2>&1)" && rc=0 || rc=$?
  if [[ $rc -ne 0 && $rc -ne 124 ]]; then
    rel=""
    for f in "${files_arr[@]}"; do
      rel+="${f#"$proj"/}"$'\n'
    done
    filtered="$(grep -F -f <(printf '%s' "$rel") <<<"$out" | head -30)"
    if [[ -n "$filtered" ]]; then
      problems+="tsc --noEmit (ошибки типов в правленных файлах):"$'\n'"$filtered"$'\n'
    fi
  fi
fi

if [[ -n "$problems" ]]; then
  jq -cn --arg r "[stop-hook] Stop-гейт качества: в правленных за сессию файлах есть нарушения — почини их и заверши ход снова."$'\n'"$problems" \
    '{decision:"block", reason:$r}'
fi
exit 0
