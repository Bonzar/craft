#!/usr/bin/env bash
# Евал закрытия разбора инцидента: агент, ведущий разбор, обязан НАЗВАТЬ ступень
# рычага и решить про евал-кейс (завести или письменно отказаться) — не
# ограничиваться правкой текста правила.
#
# Regression-tests реальный инцидент: два разбора подряд закрыты самой слабой
# ступенью (усиление формулировки), евал не заведён, приёмка была «текст
# записан». Урок живёт в скилле «Разбор инцидента» (пункты «назвать ступень» и
# «приёмка поведенческая» внутри нумерованного списка) — этот прогон сторожит
# поведение, а не пересказывает правило.
#
# Грейдер детерминированный: по финальному тексту ответа (.result из
# --output-format json) ищутся обязательные подстроки (expect_present) и
# отсутствие запрещённых (expect_absent). Пустой результат = FAIL (защита от
# вакуумного прохода при обрыве хода).
#
# MCP не подключается: кейсы прямо говорят «Craft недоступен, изложи текстом» —
# проверяется рассуждение агента, не запись.
#
# Usage: run-incident-closure.sh [model ...]        (default: sonnet-5)
#        EVAL_RUNS=3 run-incident-closure.sh …      — N прогонов каждого кейса
#        (дисциплина прогонов: один прогон недетерминированного агента ничего
#        не доказывает; для кейсов инцидентов — от трёх)
#
# Модель: дефолт — рабочий sonnet-5. Флапает (разные исходы между прогонами) —
# добавляй матрицу с opus-5 вторым аргументом: `… claude-sonnet-5 claude-opus-5`;
# матрица, однажды применённая к кейс-сету, используется и в дальнейших его
# прогонах (иначе сравнение с прошлым результатом теряет смысл).
set -u
cd "$(dirname "$0")/.." || exit 1

CASES="evals/cases/incident-closure.jsonl"
if [[ $# -gt 0 ]]; then MODELS=("$@"); else MODELS=("claude-sonnet-5"); fi

export CRAFT_AUTONOMOUS=1      # байпас план-гейта: headless, одобрять план некому
export CRAFT_EVAL=1            # headless-евал: Stop-энфорсеры молчат

label() { grep -oE 'haiku|sonnet|opus|fable' <<<"$1" | head -1; }

total=0; passc=0
declare -A mt mp
printf '%-34s %-7s %-6s %s\n' "CASE" "MODEL" "RES" "DETAIL"
printf -- '-------------------------------------------------------------------------\n'
while IFS= read -r line || [[ -n "$line" ]]; do
  [[ -z "${line//[[:space:]]/}" || "$line" == \#* ]] && continue
  name="$(jq -r '.name' <<<"$line")"
  prompt="$(jq -r '.prompt' <<<"$line")"
  for model in "${MODELS[@]}"; do
   for ((run_i=0; run_i<${EVAL_RUNS:-1}; run_i++)); do
    ml="$(label "$model")"
    # env -u: свежая изолированная сессия на каждый прогон (без общего
    # warm-spare и накопленного состояния хуков).
    # --disallowedTools: кейс требует ТЕКСТОВОГО разбора. Без запрета агент
    # уходит работать (читает git, пишет файлы евалов), в headless упирается в
    # permission-denials, ход обрывается (aborted_streaming) и поля .result нет
    # вовсе — грейдер получал бы не поведение, а обрыв.
    out="$(env -u CLAUDE_CODE_SESSION_ID -u CLAUDE_CODE_CHILD_SESSION -u CLAUDE_PID \
             -u CLAUDE_CODE_REMOTE_SESSION_ID -u CLAUDE_CODE_WORKER_EPOCH \
           timeout 300 claude -p "$prompt" --model "$model" \
             --disallowedTools Bash Read Write Edit MultiEdit NotebookEdit \
                               Glob Grep Task WebFetch WebSearch \
             --max-turns 6 --output-format json 2>/dev/null)" || out=""
    text="$(jq -r '.result // ""' <<<"$out" 2>/dev/null)"

    ok=1; d=""
    if [[ -z "${text//[[:space:]]/}" ]]; then
      ok=0; d="пустой ответ"        # вакуумный проход недопустим
    else
      while IFS= read -r sub; do
        [[ -z "$sub" ]] && continue
        grep -qiF -- "$sub" <<<"$text" || { ok=0; d="${d:+$d,}нет: $sub"; }
      done < <(jq -r '(.expect_present // [])[]' <<<"$line")
      while IFS= read -r sub; do
        [[ -z "$sub" ]] && continue
        grep -qiF -- "$sub" <<<"$text" && { ok=0; d="${d:+$d,}есть: $sub"; }
      done < <(jq -r '(.expect_absent // [])[]' <<<"$line")
      # expect_any_of: группы синонимов — в каждой группе достаточно ОДНОЙ
      # подстроки. Ассертить жаргон скилла нельзя: агент вправе сказать
      # «эскалация» вместо «рецидив» — проверяется поведение, не словарь.
      while IFS= read -r grp; do
        [[ -z "$grp" ]] && continue
        hit=0
        while IFS= read -r alt; do
          [[ -z "$alt" ]] && continue
          grep -qiF -- "$alt" <<<"$text" && { hit=1; break; }
        done < <(jq -r '.[]' <<<"$grp")
        [[ $hit -eq 0 ]] && { ok=0; d="${d:+$d,}нет ни одного из: $(jq -r 'join("/")' <<<"$grp")"; }
      done < <(jq -c '(.expect_any_of // [])[]' <<<"$line")
    fi

    total=$((total+1)); mt[$ml]=$(( ${mt[$ml]:-0}+1 ))
    if [[ $ok -eq 1 ]]; then passc=$((passc+1)); mp[$ml]=$(( ${mp[$ml]:-0}+1 )); r="PASS"; else r="FAIL"; fi
    printf '%-34s %-7s %-6s %s\n' "${name:0:33}" "$ml" "$r" "$d"
   done
  done
done < "$CASES"
printf -- '-------------------------------------------------------------------------\n'
for model in "${MODELS[@]}"; do ml="$(label "$model")"; echo "$ml: ${mp[$ml]:-0}/${mt[$ml]:-0}"; done
echo "TOTAL: $passc/$total"
[[ $passc -eq $total ]]
