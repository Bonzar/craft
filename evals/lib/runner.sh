#!/usr/bin/env bash
# Раннер поведенческих евалов: читает кейсы, гоняет агента, грейдит потоком.
#
# ЗАЧЕМ ОБЩЕЕ ЯДРО. Каждый набор кейсов раньше нёс свою копию цикла прогона и
# ассертов — поломка чинилась в одном месте и оставалась в остальных. Здесь цикл
# один, кейс-сеты отличаются только файлом кейсов.
#
# ИСПОЛЬЗОВАНИЕ (из раннера набора):
#   source evals/lib/runner.sh
#   eval_run evals/cases/behavioral/<набор>.jsonl "$@"
#
# ФОРМАТ КЕЙСА — см. evals/README.md, раздел «Как написать рабочий тест».
# Обязательны name, prompt и хотя бы один ассерт; кейс без ассертов отвергается,
# а не проходит вакуумно.
set -u

EVAL_LIB="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$EVAL_LIB/grade.sh"

# Дефолты кейса. allowed_tools — только читающие: поведенческий кейс проверяет
# рассуждение и решение, а не правку файлов; запись из евала ещё и мусорила бы в
# рабочем дереве.
EVAL_DEF_TOOLS="${EVAL_DEF_TOOLS:-Read,Grep,Glob,Skill}"
# Пишущие инструменты запрещены явно: --allowedTools лишь дополняет проектные
# разрешения и остальное НЕ закрывает — в первом прогоне агент выполнил
# `rm -f` своего буфера. Поведенческий кейс проверяет рассуждение, а не правку.
# Субагенты закрыты вместе с ними: у субагента свой набор инструментов, и через
# него запрет обходится — в прогонах агент спавнил помощника с заданием удалить
# файл. Разведка субагентом в поведенческом кейсе всё равно не нужна.
EVAL_DENY_TOOLS="${EVAL_DENY_TOOLS:-Bash Write Edit MultiEdit NotebookEdit Task Agent}"
EVAL_DEF_MAX_TURNS="${EVAL_DEF_MAX_TURNS:-25}"
EVAL_DEF_LEVEL="${EVAL_DEF_LEVEL:-neutral}"
EVAL_TIMEOUT="${EVAL_TIMEOUT:-600}"

eval__label() { grep -oE 'haiku|sonnet|opus|fable' <<<"$1" | head -1; }

# Имя файла из имени кейса: кириллица схлопывается, а не превращается в частокол
# подчёркиваний — по имени файла должно быть видно, чей это поток.
eval__slug() {
  printf '%s' "$1" | tr -c 'a-zA-Z0-9_-' '-' | tr -s '-' \
    | sed -e 's/^-//' -e 's/-$//' | cut -c1-40
}

# eval__once <prompt> <model> <tools> <max_turns> <outfile> → rc
# Прогон идёт ИЗ РЕПО: проектные SessionStart-хуки инжектят роутер и правила —
# в песочнице агент не получил бы проверяемого правила, и кейс падал бы по
# причине, не связанной с поведением.
eval__once() {
  local prompt="$1" model="$2" tools="$3" turns="$4" out="$5"
  # env -u: свежая изолированная сессия на каждый прогон, без общего warm-spare
  # и накопленного состояния хуков.
  # < /dev/null: иначе CLI пишет в stdout предупреждение про stdin.
  env -u CLAUDE_CODE_SESSION_ID -u CLAUDE_CODE_CHILD_SESSION -u CLAUDE_PID \
      -u CLAUDE_CODE_REMOTE_SESSION_ID -u CLAUDE_CODE_WORKER_EPOCH \
    timeout "$EVAL_TIMEOUT" claude -p "$prompt" --model "$model" \
      --allowedTools "$tools" --disallowedTools $EVAL_DENY_TOOLS --max-turns "$turns" \
      --output-format stream-json --verbose < /dev/null > "$out" 2>/dev/null
  return $?
}

# eval__assert_case <case-json> — применяет ассерты кейса к загруженному потоку.
eval__assert_case() {
  local c="$1" s g ev
  ev="$(jq -r '.rule_evidence // ""' <<<"$c")"
  [[ -n "$ev" ]] && grade_require_rule_delivered "$ev"
  [[ "$(jq -r '.expect_no_refusal // false' <<<"$c")" == "true" ]] && grade_expect_no_refusal
  while IFS= read -r s; do [[ -n "$s" ]] && grade_expect_present "$s"; done \
    < <(jq -r '(.expect_present // [])[]' <<<"$c")
  while IFS= read -r s; do [[ -n "$s" ]] && grade_expect_absent "$s"; done \
    < <(jq -r '(.expect_absent // [])[]' <<<"$c")
  while IFS= read -r g; do [[ -n "$g" ]] && grade_expect_any_of "$g"; done \
    < <(jq -c '(.expect_any_of // [])[]' <<<"$c")
  while IFS= read -r s; do [[ -n "$s" ]] && grade_expect_tool "$s"; done \
    < <(jq -r '(.expect_tool // [])[]' <<<"$c")
  while IFS= read -r s; do [[ -n "$s" ]] && grade_expect_no_tool "$s"; done \
    < <(jq -r '(.expect_no_tool // [])[]' <<<"$c")
}

# eval__validate <case-json> → печатает причину брака или молчит.
# Кейс без ассертов — ошибка формата, а не проходной кейс: молчаливый PASS на
# пустом кейсе создаёт иллюзию покрытия.
eval__validate() {
  local c="$1" n
  n="$(jq -r '.name // ""' <<<"$c")"
  [[ -z "$n" ]] && { echo "нет поля name"; return; }
  local prompt; prompt="$(jq -r '.prompt // ""' <<<"$c")"
  [[ -z "$prompt" ]] && { echo "нет поля prompt"; return; }
  local cnt
  cnt="$(jq '[(.expect_present // []), (.expect_absent // []), (.expect_any_of // []),
              (.expect_tool // []), (.expect_no_tool // [])] | map(length) | add' <<<"$c")"
  [[ "${cnt:-0}" -eq 0 ]] && { echo "нет ни одного ассерта"; return; }

  # Кейс про разбор обязан нести материал разбираемого случая. Без него правильное
  # поведение — не разбирать, а требовать факты, и замер меряет готовность
  # выдумывать. Признак такого кейса — требование не отказываться от работы.
  if [[ "$(jq -r '.expect_no_refusal // false' <<<"$c")" == "true" ]]; then
    [[ -z "$(jq -r '.material // ""' <<<"$c")" ]] && { echo "нет поля material — разбирать нечего"; return; }
  fi

  # Кейс, проверяющий правило из Craft, обязан назвать улику его доставки —
  # иначе вердикт вынесен вслепую. Заодно проверяем, что промпт вообще запускает
  # подачу правила: не запускает — правило до сессии не дойдёт, и любой вердикт
  # будет о памяти модели, а не о соблюдении.
  local ev; ev="$(jq -r '.rule_evidence // ""' <<<"$c")"
  if [[ -n "$ev" ]]; then
    local trig; trig="$(jq -r '.rule_trigger_hook // ""' <<<"$c")"
    if [[ -n "$trig" && -x "$trig" ]]; then
      # Буфер наблюдений подменяется: детектор пишет в него сигнал инцидента, и
      # предполётная проверка иначе мусорит в живое состояние текущей сессии.
      local out tmpbuf; tmpbuf="$(mktemp)"
      out="$(jq -n --arg p "$prompt" '{prompt:$p}' \
             | CLAUDE_PROJECT_DIR="$PWD" OBSERVE_BUFFER="$tmpbuf" bash "$trig" 2>/dev/null)"
      rm -f "$tmpbuf"
      [[ -z "${out//[[:space:]]/}" ]] && { echo "промпт не запускает подачу правила ($trig молчит)"; return; }
    fi
  fi
}

# eval_run <cases.jsonl> [model ...]
eval_run() {
  local cases="${1:?cases file}"; shift || true
  local models=("$@"); [[ ${#models[@]} -eq 0 ]] && models=("claude-sonnet-5")
  local runs="${EVAL_RUNS:-1}"
  local run_id="${EVAL_RUN_ID:-$(date +%Y%m%d-%H%M%S)}"
  local dir="evals/runs/$run_id"
  mkdir -p "$dir"

  export CRAFT_AUTONOMOUS=1   # байпас план-гейта: headless, одобрять план некому
  export CRAFT_EVAL=1         # Stop-энфорсеры молчат: меряем промптовый слой

  local total=0 passc=0 failc=0 errc=0 bad=0
  declare -A lvl_t lvl_p
  local report="$dir/report.tsv"
  printf 'case\tlevel\tmodel\trun\tverdict\tdetail\n' > "$report"

  printf '%-38s %-10s %-7s %-6s %s\n' CASE LEVEL MODEL RES DETAIL
  printf -- '-------------------------------------------------------------------------------\n'

  local line name prompt level tools turns problem model ml i out rc attempt
  while IFS= read -r line || [[ -n "$line" ]]; do
    [[ -z "${line//[[:space:]]/}" || "$line" == \#* ]] && continue
    problem="$(eval__validate "$line")"
    if [[ -n "$problem" ]]; then
      bad=$((bad+1))
      printf '%-38s %-10s %-7s %-6s %s\n' "$(jq -r '.name // "<без имени>"' <<<"$line" | cut -c1-37)" \
             "-" "-" "BADCASE" "$problem"
      continue
    fi
    name="$(jq -r '.name' <<<"$line")"
    prompt="$(jq -r '.prompt' <<<"$line")"
    level="$(jq -r ".level // \"$EVAL_DEF_LEVEL\"" <<<"$line")"
    tools="$(jq -r ".allowed_tools // \"$EVAL_DEF_TOOLS\"" <<<"$line")"
    turns="$(jq -r ".max_turns // $EVAL_DEF_MAX_TURNS" <<<"$line")"

    for model in "${models[@]}"; do
      ml="$(eval__label "$model")"
      for ((i=1; i<=runs; i++)); do
        out="$dir/$(eval__slug "$name")-$ml-$i.jsonl"
        # ERROR ретраится один раз: прогон не состоялся — это про механику
        # (таймаут, обрыв сессии), а не про поведение агента.
        for attempt in 1 2; do
          eval__once "$prompt" "$model" "$tools" "$turns" "$out"; rc=$?
          grade_load "$out" "$rc"
          eval__assert_case "$line"
          grade_verdict
          [[ "$GRADE_VERDICT" != "ERROR" ]] && break
          [[ $attempt -eq 1 ]] && cp "$out" "$out.error-attempt1"
        done

        total=$((total+1)); lvl_t[$level]=$(( ${lvl_t[$level]:-0}+1 ))
        case "$GRADE_VERDICT" in
          PASS) passc=$((passc+1)); lvl_p[$level]=$(( ${lvl_p[$level]:-0}+1 ));;
          FAIL) failc=$((failc+1));;
          *)    errc=$((errc+1));   lvl_t[$level]=$(( ${lvl_t[$level]:-0}-1 ));;
        esac
        printf '%s\t%s\t%s\t%s\t%s\t%s\n' "$name" "$level" "$ml" "$i" "$GRADE_VERDICT" "$GRADE_DETAIL" >> "$report"
        printf '%-38s %-10s %-7s %-6s %s\n' "${name:0:37}" "$level" "$ml" "$GRADE_VERDICT" "${GRADE_DETAIL:0:60}"
      done
    done
  done < "$cases"

  printf -- '-------------------------------------------------------------------------------\n'
  # ERROR в знаменатель pass rate не входит: измерения не было.
  local l
  for l in "${!lvl_t[@]}"; do
    printf '%-10s %s/%s\n' "$l" "${lvl_p[$l]:-0}" "${lvl_t[$l]:-0}"
  done
  echo "TOTAL: PASS $passc / FAIL $failc / ERROR $errc (прогонов $total), брак кейсов: $bad"
  echo "Отчёт и сырые потоки: $dir"
  [[ $failc -eq 0 && $bad -eq 0 && $passc -gt 0 ]]
}
