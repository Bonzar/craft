#!/usr/bin/env bash
# Проверка раннера БЕЗ моделей и сети: подставной `claude` отдаёт записанный
# поток с паузой, раннер работает с ним как с настоящим.
#
# ЗАЧЕМ. Параллельность и страницу хода нельзя проверять живым замером — это
# деньги и десятки минут. Здесь то же самое за секунды и детерминированно:
# видно, что прогоны идут пачками, что сводка совпадает с отчётом, что строки
# отчёта целы, и что страница доходит до готовности.
#
# Usage: bash evals/fake-run.sh [jobs]   (default: 4)
set -u
cd "$(dirname "$0")/.." || exit 1

JOBS="${1:-4}"
FAKE_DELAY="${FAKE_DELAY:-2}"     # сколько «думает» подставной прогон
STREAM="evals/fixtures/stream/rule-both.jsonl"

bin="$(mktemp -d)"
cat > "$bin/claude" <<EOF
#!/usr/bin/env bash
sleep "$FAKE_DELAY"
cat "$PWD/$STREAM"
EOF
chmod +x "$bin/claude"

cases="$(mktemp)"
for n in a b c d; do
  jq -cn --arg n "case-$n" --arg p "ты нарушил записанное правило, разбери инцидент" \
    '{name:$n,prompt:$p,level:"neutral",material:"м",expect_present:["четыре"],
      rule_evidence:["incident doc cached","роутер обновлён"]}' >> "$cases"
done

run_id="fake-$(date +%s)"
started=$(date +%s)
PATH="$bin:$PATH" EVAL_JOBS="$JOBS" EVAL_RUNS=3 EVAL_RUN_ID="$run_id" \
  bash -c 'source evals/lib/runner.sh; eval_run "$1" claude-sonnet-5' _ "$cases" \
  > /tmp/fake-run.out 2>&1
elapsed=$(( $(date +%s) - started ))
rm -rf "$bin" "$cases"

dir="evals/runs/$run_id"
rows=$(( $(wc -l < "$dir/report.tsv") - 1 ))
summary="$(grep -o 'PASS [0-9]* / FAIL [0-9]* / ERROR [0-9]*' /tmp/fake-run.out | head -1)"
counted=$(awk '{s=0; for(i=1;i<=NF;i++) if ($i ~ /^[0-9]+$/) s+=$i; print s}' <<<"$summary")
torn=$(awk -F'\t' 'NR>1 && NF!=6' "$dir/report.tsv" | wc -l | tr -d ' ')
page_total=$(jq -r '.total' "$dir/progress/index.json" 2>/dev/null)
page_done=$(jq -r '.done' "$dir/progress/index.json" 2>/dev/null)

fail=0
say() { printf '%-6s %s\n' "$1" "$2"; [[ "$1" == FAIL ]] && fail=1; return 0; }

[[ "$rows" -eq 12 ]] && say PASS "прогонов в отчёте: 12" || say FAIL "прогонов в отчёте: $rows (ждали 12)"
[[ "$counted" -eq 12 ]] && say PASS "сводка сходится с отчётом: $summary" \
                        || say FAIL "сводка не сходится: $summary (сумма $counted)"
[[ "$torn" -eq 0 ]] && say PASS "строки отчёта целы" || say FAIL "порванных строк: $torn"
[[ "$page_total" == "12" && "$page_done" == "12" ]] \
  && say PASS "страница дошла до готовности: $page_done из $page_total" \
  || say FAIL "страница: $page_done из $page_total"
# Последовательно двенадцать прогонов заняли бы 12×FAKE_DELAY; пачками — заметно
# меньше. Порог с запасом, чтобы не флапать на медленной машине.
seq_time=$(( 12 * FAKE_DELAY ))
[[ "$elapsed" -lt $(( seq_time / 2 )) ]] \
  && say PASS "пачками быстрее: ${elapsed}s против ${seq_time}s последовательно" \
  || say FAIL "ускорения нет: ${elapsed}s при последовательных ${seq_time}s"

rm -rf "$dir"
echo "---"
[[ $fail -eq 0 ]] && echo "fake-run: OK" || { echo "fake-run: провал (вывод в /tmp/fake-run.out)"; exit 1; }
