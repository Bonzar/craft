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
PATH="$bin:$PATH" EVAL_JOBS="$JOBS" EVAL_RUNS=3 EVAL_RUN_ID="$run_id" EVAL_SKIP_WARMUP=1 \
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

# Страница обязана НЕСТИ данные в себе: тянуть их запросом к соседнему файлу
# нельзя — браузер запрещает такие запросы для локальных файлов, и таблица
# осталась бы пустой. Проверяется чтением самого файла, браузер не нужен.
html="$dir/progress.html"
names=$(grep -c '<tr><td>case-' "$html" 2>/dev/null || echo 0)
verdicts=$(grep -oE 'class="(PASS|FAIL|ERROR)"' "$html" 2>/dev/null | wc -l | tr -d ' ')
[[ "$names" -eq 12 && "$verdicts" -eq 12 ]] \
  && say PASS "в странице 12 строк с именами и вердиктами" \
  || say FAIL "в странице имён $names, вердиктов $verdicts (ждали по 12)"
grep -q 'fetch(' "$html" 2>/dev/null \
  && say FAIL "страница всё ещё тянет данные запросом" \
  || say PASS "страница не зависит от запросов к файлам"

# Недостача результатов обязана валить прогон. Имитация настоящая: подставной
# прогон для одного кейса убивает свой воркер, тот не успевает записать исход —
# без проверки сводка посчитала бы по остальным, и неполный эксперимент выглядел
# бы удачным.
# Недостача результатов обязана валить прогон. Проверяется напрямую: берём
# каталог удачного прогона, убираем один файл результата и зовём сводку — так
# видно поведение, а не имитация умирающего воркера (там убивается не тот
# процесс, и дефект остаётся непокрытым).
lost_dir="$dir-lost"
cp -R "$dir" "$lost_dir"
rm -f "$(ls "$lost_dir"/result-*.tsv | head -1)"
if ( source evals/lib/runner.sh; eval__summarize "$lost_dir" 12 0 "" ) > /tmp/fake-lost.out 2>&1; then
  say FAIL "неполный замер прошёл как успешный"
else
  grep -q "НЕПОЛНЫЙ ЗАМЕР" /tmp/fake-lost.out \
    && say PASS "неполный замер валит прогон: $(grep -o 'результатов [0-9]* из [0-9]*' /tmp/fake-lost.out | head -1)" \
    || say FAIL "прогон упал, но без внятной причины (см. /tmp/fake-lost.out)"
fi
# И обратное: полный набор сводка принимает — иначе проверка ловила бы всегда.
( source evals/lib/runner.sh; eval__summarize "$dir" 12 0 "" ) > /dev/null 2>&1 \
  && say PASS "полный набор сводка принимает" \
  || say FAIL "полный набор сводка забраковала"
rm -rf "$lost_dir"

rm -rf "$dir"
echo "---"
[[ $fail -eq 0 ]] && echo "fake-run: OK" || { echo "fake-run: провал (вывод в /tmp/fake-run.out)"; exit 1; }
