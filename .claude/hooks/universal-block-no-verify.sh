#!/usr/bin/env bash
# PreToolUse(Bash) guard: блокирует обход git-хуков. Портирован из ECC
# (block-no-verify.js): pre-commit/pre-push хуки — часть контракта репо, агент
# не пропускает их флагами.
#
# Ловит:
#   - git commit … --no-verify   (и push --no-verify);
#   - git -c core.hooksPath=…    (подмена каталога хуков на пустой).
#
# Намеренно КОНСЕРВАТИВЕН против ложных срабатываний: --no-verify считается
# только в сегменте команды (между ; | && ||), где есть и слово git, и
# подкоманда commit/push; текст в одинарных/двойных кавычках (сообщения
# коммитов) перед проверкой вырезается. grep/echo про --no-verify без git —
# проходят свободно.
#
# Fail open на всём неожиданном: сломанный гейт не должен клинить работу.
set -u

# Project-уровень уступает user-уровню (install.sh) — не гейтим дважды.
if [[ -n "${CLAUDE_PROJECT_DIR:-}" && "$0" == "$CLAUDE_PROJECT_DIR"/* \
      && -e "$HOME/.claude/hooks/$(basename "$0")" ]]; then
  exit 0
fi

cmd="$(cat | jq -r '.tool_input.command // ""' 2>/dev/null)" || exit 0
[[ -z "$cmd" ]] && exit 0

# Быстрый пропуск: git как отдельного слова в команде нет вовсе.
grep -Eq '(^|[^[:alnum:]_-])git([[:space:]]|$)' <<<"$cmd" || exit 0

deny() {
  jq -cn --arg r "$1" \
    '{hookSpecificOutput:{hookEventName:"PreToolUse",permissionDecision:"deny",permissionDecisionReason:$r}}'
  exit 0
}

# --- 1) Подмена каталога хуков: git -c core.hooksPath=… ----------------------
# Проверяется по СЫРОЙ команде (значение бывает в кавычках); имена git-конфига
# регистронезависимы, поэтому -i.
if grep -Eiq '(^|[[:space:]])-c[[:space:]]+["'\'']?core\.hookspath=' <<<"$cmd"; then
  deny "Заблокировано: git -c core.hooksPath=… — подмена каталога git-хуков, то есть их обход. Хуки репо (pre-commit, pre-push) ловят то, что сломает CI: падает хук → чини причину, а не выключай проверку. Обход действительно нужен (сломанный хук, осознанное решение) — это решение Влада: скажи об этом в ответе и попроси его нажать allow."
fi

# --- 2) --no-verify при commit/push ------------------------------------------
# Кавычные строки вырезаются (git commit -m "не используй --no-verify" — не
# обход), затем команда режется на сегменты по ; | & — деним только сегмент,
# где есть и git, и commit/push, и токен --no-verify.
stripped="$(printf '%s' "$cmd" | sed -E "s/'[^']*'/''/g; s/\"[^\"]*\"/\"\"/g")"
while IFS= read -r seg; do
  [[ -z "$seg" ]] && continue
  grep -Eq '(^|[^[:alnum:]_-])git([[:space:]]|$)' <<<"$seg" || continue
  grep -Eq '(^|[[:space:]])(commit|push)([[:space:]]|$)' <<<"$seg" || continue
  if grep -Eq '(^|[[:space:]])--no-verify([[:space:]]|$)' <<<"$seg"; then
    deny "Заблокировано: git commit/push с --no-verify — обход git-хуков репо. Хуки (pre-commit, pre-push) ловят то, что сломает CI: падает хук → чини причину, а не пропускай проверку. Обход действительно нужен (сломанный хук, осознанное решение) — это решение Влада: скажи об этом в ответе и попроси его нажать allow."
  fi
done < <(printf '%s\n' "$stripped" | tr ';|&' '\n\n\n')

exit 0
