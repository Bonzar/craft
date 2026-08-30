#!/usr/bin/env bash
# Самотест разбора ответов классификатора: гоняет core/classifier/run.sh
# с фейковой командой модели и сверяет разбор форм. Ловушка, ради которой тест
# существует: положительная форма входит в отрицательную подстрокой — «ПОКРЫТА»
# сидит внутри «НЕ ПОКРЫТА», — и разбор обязан сверять отрицательную первой.
# Своя ловушка у запрета: «РАЗРЕШЕНО ПОВЕРХ» слова «ЗАПРЕЩЕНО» не содержит, но
# оба про запрет, и разрешающая форма обязана проверяться раньше.
#
# Без сети и настоящих моделей: bash tools/plan-scope-classifier-test.sh
set -u
cd "$(dirname "$0")/.." || exit 1
MOCK="tests/hooks/fixtures/mock-classifier.sh"
PLAN="tests/hooks/fixtures/plan-scope.md"
REG="$(mktemp)"
printf 'Ц1 «Цель» — работа, источник: plan\n  задача Ц1.1 «задача» — открыта\n' > "$REG"
trap 'rm -f "$REG"' EXIT
pass=0; fail=0
check() { # <имя> <режим+аргументы...> <ответ мока> <ожидание>
  local name="$1" mode="$2" answer="$3" want="$4" got
  got="$(echo тест | PLAN_CLASSIFIER_CMD="$PWD/$MOCK" MOCK_CLASSIFIER_ANSWER="$answer" \
    bash core/classifier/run.sh $mode 2>/dev/null)"
  if [[ "$got" == $want ]]; then
    pass=$((pass+1)); printf 'PASS  %-52s %s\n' "$name" "$got"
  else
    fail=$((fail+1)); printf 'FAIL  %-52s got=%s want=%s\n' "$name" "$got" "$want"
  fi
}
check "ловушка подстроки: НЕ ПОКРЫТА ≠ ПОКРЫТА"       "cover $REG" "НЕ ПОКРЫТА: причина" "UNCOVERED:*"
check "ПОКРЫТА → COVERED"                            "cover $REG" "ПОКРЫТА Ц1.1: сделано" "COVERED:*"
check "ловушка запрета: РАЗРЕШЕНО ПОВЕРХ ≠ ЗАПРЕЩЕНО" "cover $REG" "РАЗРЕШЕНО ПОВЕРХ Ц1" "OVERRIDE:*"
check "ЗАПРЕЩЕНО → FORBIDDEN"                        "cover $REG" "ЗАПРЕЩЕНО Ц1: цитата" "FORBIDDEN:*"
check "ЧЕРНОВОЕ → DRAFT"                             "cover $REG" "ЧЕРНОВОЕ" "DRAFT"
check "мусорный ответ → UNAVAILABLE"                 "cover $REG" "не знаю" "UNAVAILABLE"
check "упавшая команда → UNAVAILABLE"                "cover $REG" "fail" "UNAVAILABLE"
check "ловушка дельты: ПОВТОРЯЕТ ВСЁ ≠ ПОВТОРЫ"      "delta $PLAN $PLAN" "ПОВТОРЯЕТ ВСЁ" "REPEATSALL"
check "ПОВТОРЫ → REPEATS"                            "delta $PLAN $PLAN" "ПОВТОРЫ: Юнит 1" "REPEATS:*"
check "ДЕЛЬТА ЧИСТАЯ → CLEAN"                        "delta $PLAN $PLAN" "ДЕЛЬТА ЧИСТАЯ" "CLEAN"
check "дельта: мусорный ответ → UNAVAILABLE"         "delta $PLAN $PLAN" "возможно" "UNAVAILABLE"
echo "TOTAL: $pass passed, $fail failed"
[[ "$fail" -eq 0 ]]
