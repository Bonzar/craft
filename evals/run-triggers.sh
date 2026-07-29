#!/usr/bin/env bash
# Триггер-эвалы скиллов: проверяют, что на характерный запрос модель вызывает
# НУЖНЫЙ скилл (Skill-тул с ожидаемым именем), а не работает мимо него.
#
# Кейсы — evals/cases/skill-triggers.jsonl: {"name","prompt","expect_skill"}.
# Грейдер детерминированный: в JSON-выводе headless-прогона ищется вызов тула
# Skill с ожидаемым именем (ассерт на вызов, не на текст ответа).
#
# Запуск ЛОКАЛЬНЫЙ и ручной (модельные вызовы, недетерминированность/стоимость):
#   source ./.env && bash evals/run-triggers.sh [model]
# Требует: claude CLI, jq. Скиллы должны быть видны сессии (запуск из корня
# репо: project-скиллы; яндекс-скиллы — из ~/.claude при локальном запуске).
# CRAFT_AUTONOMOUS=1 — байпас план-гейта, сетевые SessionStart-хуки не мешают.
set -u

REPO="$(cd "$(dirname "$0")/.." && pwd)"
CASES="$REPO/evals/cases/skill-triggers.jsonl"
MODEL="${1:-claude-haiku-4-5-20251001}"

command -v claude >/dev/null 2>&1 || { echo "ERROR: claude CLI required" >&2; exit 2; }
command -v jq >/dev/null 2>&1 || { echo "ERROR: jq required" >&2; exit 2; }

total=0; pass=0
fails=()

while IFS= read -r line || [[ -n "$line" ]]; do
  [[ -z "${line//[[:space:]]/}" || "$line" == \#* ]] && continue
  name="$(jq -r '.name' <<<"$line")"
  prompt="$(jq -r '.prompt' <<<"$line")"
  want="$(jq -r '.expect_skill' <<<"$line")"
  total=$((total+1))

  # --max-turns 2: хватает, чтобы модель вызвала Skill; дальше ход обрывается.
  out="$(cd "$REPO" && CRAFT_AUTONOMOUS=1 CRAFT_EVAL=1 timeout 180 claude -p "$prompt" \
        --model "$MODEL" --max-turns 2 --output-format json \
        --permission-mode plan 2>/dev/null)" || out=""

  got="$(jq -r '[.. | objects | select(.type? == "tool_use" and .name? == "Skill")
                 | .input.skill] | join(",")' <<<"$out" 2>/dev/null)"

  if grep -q "$want" <<<"${got:-}"; then
    pass=$((pass+1)); printf 'PASS  %-45s → %s\n' "$name" "$got"
  else
    fails+=("$name — ожидался $want, вызвано: ${got:-ничего}")
    printf 'FAIL  %-45s → %s (ожидался %s)\n' "$name" "${got:-—}" "$want"
  fi
done < "$CASES"

echo "-----"
echo "TRIGGERS: $pass/$total"
[[ ${#fails[@]} -eq 0 ]]
