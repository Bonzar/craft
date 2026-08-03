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
# Улик может быть несколько: правила кейса приходят разными каналами — тело
# скилла своим инжектом, а «Отчёт об изменениях» и «Факт против догадки» только
# роутером. Требуется каждая: пропади один источник, замер остался бы зелёным на
# пустом месте. Поле принимает и строку, и перечень.
eval__assert_case() {
  local c="$1" s g ev
  while IFS= read -r ev; do
    [[ -n "$ev" ]] && grade_require_rule_delivered "$ev"
  done < <(jq -r 'if (.rule_evidence // "") | type == "array"
                  then .rule_evidence[] else (.rule_evidence // "") end' <<<"$c")
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

# --- ход прогона --------------------------------------------------------------
# Показывается страницей, а не строкой в терминале: строка не переживает ни
# параллельность (несколько прогонов пишут разом), ни фоновый запуск (в файле
# она превращается в кашу из недописанных строк). Страница сама обновляется,
# состояние каждого прогона лежит в своём файле и пишется целиком.
eval__progress_init() {  # <dir> <total>
  local dir="$1" total="$2"
  mkdir -p "$dir/progress"
  printf '%s\n' "$total" > "$dir/progress/total"
  eval__progress_page "$dir"
  echo "Ход прогона: file://$(cd "$dir" && pwd)/progress.html  (обновляется само)"
}

# Состояние одного прогона: файл переписывается целиком, читатель не поймает
# полузаписанное.
eval__progress_set() {  # <dir> <slug> <state> <name> [detail]
  local dir="$1" slug="$2" state="$3" name="$4" detail="${5:-}"
  local tmp="$dir/progress/.$slug.$$"
  printf '%s\t%s\t%s\t%s\n' "$state" "$name" "$(date +%s)" "$detail" > "$tmp"
  mv -f "$tmp" "$dir/progress/$slug.tsv"
}

# Данные вписываются В САМУ страницу. Тянуть их запросом к соседнему файлу
# нельзя: браузер запрещает такие запросы для локальных файлов, и страница
# показывала пустые ячейки. Секунды пересчитываются на месте от времени старта —
# иначе секундомер стоял бы между снимками, а снимки делаются только на старте и
# финише прогона.
eval__progress_page() {  # <dir>
  local dir="$1" total done_n running rows now tmp
  now="$(date +%s)"
  total="$(cat "$dir/progress/total" 2>/dev/null || echo 0)"
  rows="$(cat "$dir"/progress/*.tsv 2>/dev/null | awk -F'\t' -v now="$now" '
    { st=$1; nm=$2; t0=($3==""?now:$3); det=$4
      gsub(/&/,"\\&amp;",nm); gsub(/</,"\\&lt;",nm)
      gsub(/&/,"\\&amp;",det); gsub(/</,"\\&lt;",det)
      printf "<tr><td>%s<td class=\"%s\">%s<td data-t0=\"%s\">–<td>%s\n", nm, st, st, (st=="RUN"?t0:""), det }')"
  done_n="$(grep -hcv $'^RUN\t' "$dir"/progress/*.tsv 2>/dev/null | paste -sd+ - | bc 2>/dev/null || echo 0)"
  running="$(grep -hc $'^RUN\t' "$dir"/progress/*.tsv 2>/dev/null | paste -sd+ - | bc 2>/dev/null || echo 0)"
  tmp="$dir/.progress.html.$$"
  { cat <<HTML
<!doctype html><meta charset="utf-8"><title>Ход прогона евалов</title>
<meta http-equiv="refresh" content="3">
<style>
 body{font:14px/1.5 -apple-system,system-ui,sans-serif;margin:2rem;max-width:60rem}
 h1{font-size:1.1rem;font-weight:600;margin:0 0 1rem}
 table{border-collapse:collapse;width:100%}
 td,th{padding:.35rem .6rem;border-bottom:1px solid #e5e5e5;text-align:left}
 th{font-weight:600;color:#666;font-size:.85rem}
 .RUN{color:#b8860b}.PASS{color:#2e7d32}.FAIL{color:#c62828}.ERROR{color:#8e24aa}
 .sum{color:#666}
</style>
<h1>Ход прогона евалов</h1>
<p class=sum>${done_n:-0} из ${total} готово, идёт ${running:-0}</p>
<table><thead><tr><th>прогон<th>состояние<th>время<th>детали</tr></thead><tbody>
$rows
</tbody></table>
<script>
// Секунды тикают на месте: страница знает время старта каждого идущего прогона.
function tick(){
  const now = Math.floor(Date.now()/1000);
  document.querySelectorAll('td[data-t0]').forEach(td => {
    const t0 = parseInt(td.dataset.t0, 10);
    td.textContent = t0 ? (now - t0) + 's' : '';
  });
}
tick(); setInterval(tick, 1000);
</script>
HTML
  } > "$tmp" && mv -f "$tmp" "$dir/progress.html"
}

# Сборка состояния для страницы: читает файлы прогонов и пишет один json целиком.
# Собирается ОДНИМ вызовом jq по всем файлам состояния. Вызов на каждый прогон
# отдельно съедал весь выигрыш от параллельности: снимков вдвое больше числа
# прогонов, и на дюжине это секунды чистых накладных.
eval__progress_snapshot() {  # <dir>
  local dir="$1" total tmp
  total="$(cat "$dir/progress/total" 2>/dev/null || echo 0)"
  eval__progress_page "$dir"   # страница держит данные в себе — рисуем тем же снимком
  tmp="$dir/progress/.index.$$"
  cat "$dir"/progress/*.tsv 2>/dev/null \
  | jq -R -s --argjson total "$total" --argjson now "$(date +%s)" '
      [ split("\n")[] | select(length > 0) | split("\t")
        | {name: .[1], state: .[0], detail: (.[3] // ""),
           secs: ($now - ((.[2] // "0") | tonumber))} ]
      | {total: $total,
         done:    ([.[] | select(.state != "RUN")] | length),
         running: ([.[] | select(.state == "RUN")] | length),
         rows: .}' > "$tmp" 2>/dev/null \
  && mv -f "$tmp" "$dir/progress/index.json" || rm -f "$tmp"
}

eval__progress_finish() { eval__progress_snapshot "$1"; }

# Один прогон целиком: запуск, грейд, ретрай при ERROR, запись своего результата.
# Ретрай остаётся внутри воркера и последовательным — параллелится только то, что
# относится к разным прогонам.
eval__worker() {  # <item> <dir>
  local item="$1" dir="$2"
  local line model ml i out
  IFS=$'\x1f' read -r line model ml i out <<<"$item"
  local name level slug rc attempt
  name="$(jq -r '.name' <<<"$line")"
  level="$(jq -r ".level // \"$EVAL_DEF_LEVEL\"" <<<"$line")"
  slug="$(eval__slug "$name")-$ml-$i"
  eval__progress_set "$dir" "$slug" RUN "$name"
  eval__progress_snapshot "$dir"

  local prompt tools turns
  prompt="$(jq -r '.prompt' <<<"$line")"
  tools="$(jq -r ".allowed_tools // \"$EVAL_DEF_TOOLS\"" <<<"$line")"
  turns="$(jq -r ".max_turns // $EVAL_DEF_MAX_TURNS" <<<"$line")"

  for attempt in 1 2; do
    eval__once "$prompt" "$model" "$tools" "$turns" "$out"; rc=$?
    grade_load "$out" "$rc"
    eval__assert_case "$line"
    grade_verdict
    [[ "$GRADE_VERDICT" != "ERROR" ]] && break
    [[ $attempt -eq 1 ]] && cp "$out" "$out.error-attempt1"
  done

  # Результат — свой файл, записанный целиком: сводка собирается из них после
  # ожидания, потому что счётчики из фоновой ветки до родителя не долетают.
  local tmp="$dir/.result-$slug.$$"
  printf '%s\t%s\t%s\t%s\t%s\t%s\n' "$name" "$level" "$ml" "$i" "$GRADE_VERDICT" "$GRADE_DETAIL" > "$tmp"
  mv -f "$tmp" "$dir/result-$slug.tsv"
  eval__progress_set "$dir" "$slug" "$GRADE_VERDICT" "$name" "${GRADE_DETAIL:0:60}"
  eval__progress_snapshot "$dir"
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

  # Прогоны идут пачками: последний замер из двенадцати занял 55 минут чистого
  # времени только потому, что они ждали друг друга. Каждый воркер пишет ТОЛЬКО
  # свои файлы — счётчики в фоновой ветке до родителя не доходят, поэтому сводка
  # собирается после ожидания, из файлов результата.
  local jobs="${EVAL_JOBS:-4}" active=0
  local line name prompt level tools turns problem model ml i out
  local -a queue=()
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
    for model in "${models[@]}"; do
      ml="$(eval__label "$model")"
      for ((i=1; i<=runs; i++)); do
        out="$dir/$(eval__slug "$name")-$ml-$i.jsonl"
        queue+=("$line"$'\x1f'"$model"$'\x1f'"$ml"$'\x1f'"$i"$'\x1f'"$out")
      done
    done
  done < "$cases"

  eval__progress_init "$dir" "${#queue[@]}"
  local item
  for item in "${queue[@]}"; do
    while (( active >= jobs )); do wait -n 2>/dev/null || wait; active=$((active-1)); done
    eval__worker "$item" "$dir" &
    active=$((active+1))
  done
  wait
  eval__progress_finish "$dir"

  # Сводка из файлов результата — по одному на прогон, каждый записан целиком.
  local res v lv
  for res in "$dir"/result-*.tsv; do
    [[ -e "$res" ]] || continue
    IFS=$'\t' read -r name lv ml i v detail < "$res"
    total=$((total+1)); lvl_t[$lv]=$(( ${lvl_t[$lv]:-0}+1 ))
    case "$v" in
      PASS) passc=$((passc+1)); lvl_p[$lv]=$(( ${lvl_p[$lv]:-0}+1 ));;
      FAIL) failc=$((failc+1));;
      *)    errc=$((errc+1));   lvl_t[$lv]=$(( ${lvl_t[$lv]:-0}-1 ));;
    esac
    printf '%s\t%s\t%s\t%s\t%s\t%s\n' "$name" "$lv" "$ml" "$i" "$v" "$detail" >> "$report"
    printf '%-38s %-10s %-7s %-6s %s\n' "${name:0:37}" "$lv" "$ml" "$v" "${detail:0:60}"
  done

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
