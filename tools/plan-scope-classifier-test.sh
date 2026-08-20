#!/usr/bin/env bash
# Самотест разбора ответов классификатора: гоняет tools/plan-scope-classifier.sh
# с фейковой командой модели и сверяет разбор форм. Ловушка, ради которой тест
# существует: положительная форма («СООТВЕТСТВУЕТ») входит в отрицательную
# («НЕ СООТВЕТСТВУЕТ») подстрокой — разбор обязан сверять отрицательную первой.
# Без сети и настоящих моделей: bash tools/plan-scope-classifier-test.sh
set -u
cd "$(dirname "$0")/.." || exit 1
MOCK="tests/hooks/fixtures/mock-classifier.sh"
PLAN="tests/hooks/fixtures/plan-scope.md"
pass=0; fail=0
check() { # <имя> <режим+аргументы...> <ответ мока> <ожидание>
  local name="$1" mode="$2" answer="$3" want="$4" got
  got="$(echo тест | PLAN_CLASSIFIER_CMD="$PWD/$MOCK" MOCK_CLASSIFIER_ANSWER="$answer" \
    bash tools/plan-scope-classifier.sh $mode 2>/dev/null)"
  if [[ "$got" == $want ]]; then
    pass=$((pass+1)); printf 'PASS  %-52s %s\n' "$name" "$got"
  else
    fail=$((fail+1)); printf 'FAIL  %-52s got=%s want=%s\n' "$name" "$got" "$want"
  fi
}
check "ловушка подстроки: НЕ СООТВЕТСТВУЕТ ≠ MATCH" "match $PLAN" "НЕ СООТВЕТСТВУЕТ: причина" "NOMATCH:*"
check "СООТВЕТСТВУЕТ → MATCH"                         "match $PLAN" "СООТВЕТСТВУЕТ" "MATCH"
check "ловушка подстроки: НЕ ВРЕМЯНКА ≠ THROWAWAY"    "throwaway"   "НЕ ВРЕМЯНКА: правка" "NOTHROWAWAY:*"
check "ВРЕМЯНКА → THROWAWAY"                          "throwaway"   "ВРЕМЯНКА" "THROWAWAY"
check "мусорный ответ → UNAVAILABLE"                  "throwaway"   "не знаю" "UNAVAILABLE"
check "упавшая команда → UNAVAILABLE"                 "match $PLAN" "fail" "UNAVAILABLE"
check "ПОВТОРЫ → REPEATS"        "delta $PLAN $PLAN"  "ПОВТОРЫ: Юнит 1" "REPEATS:*"
check "ДЕЛЬТА ЧИСТАЯ → CLEAN"    "delta $PLAN $PLAN"  "ДЕЛЬТА ЧИСТАЯ" "CLEAN"
check "ловушка подстроки: НЕ РАЗРЕШАЕТ ≠ PERMIT"      "permission $PLAN" "НЕ РАЗРЕШАЕТ: про другое" "NOPERMIT:*"
check "РАЗРЕШАЕТ → PERMIT"                            "permission $PLAN" "РАЗРЕШАЕТ" "PERMIT"
check "permission: мусорный ответ → UNAVAILABLE"      "permission $PLAN" "возможно" "UNAVAILABLE"
echo "TOTAL: $pass passed, $fail failed"
[[ "$fail" -eq 0 ]]
