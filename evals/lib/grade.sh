#!/usr/bin/env bash
# Ядро грейдера поведенческих евалов: разбор потока событий агента и ассерты.
#
# ЗАЧЕМ. Грейдить финальный текст (.result из --output-format json) нельзя:
# при обрыве хода по лимиту поля .result нет вовсе, и грейдер мерит пустоту —
# «правило не соблюдается» и «прогон не состоялся» выглядят одинаково. Поэтому
# измерение идёт по потоку событий (--output-format stream-json --verbose): что
# агент вызывал, что ему ответили, что он написал. Поток остаётся даже у
# оборванного хода.
#
# ИСПОЛЬЗОВАНИЕ (source из раннера):
#   source evals/lib/grade.sh
#   grade_load stream.jsonl "$rc"    # rc — код возврата claude (124 = таймаут)
#   grade_expect_present "ступень"
#   grade_expect_any_of '["евал","тест"]'
#   grade_expect_tool Read
#   grade_verdict                    # кладёт GRADE_VERDICT и GRADE_DETAIL
#   echo "$GRADE_VERDICT $GRADE_DETAIL"
#
# grade_verdict НИЧЕГО не печатает и вызывается без $(…): подстановка запускает
# подоболочку, и присвоенная в ней деталь до вызывающего не доходит.
#
# ИСХОДЫ. PASS — все ассерты сошлись. FAIL — поведение не то. ERROR — прогон не
# состоялся (таймаут, битый поток, нет ответных событий, нечего мерить); такой
# прогон в знаменатель pass rate не входит и ретраится раннером. Обрыв по лимиту
# ходов сам по себе НЕ ошибка: если поток непустой, измерение состоялось.
set -u

# --- состояние (перезаписывается каждым grade_load) --------------------------
GRADE_VERDICT=""     # PASS | FAIL | ERROR — итог grade_verdict
GRADE_TEXT=""        # склейка ассистентских текстовых блоков и .result (lowercase)
GRADE_TOOLS=""       # по строке на tool_use: "имя<TAB>вход-json"
GRADE_DENIALS=""     # по строке на отказ разрешения
GRADE_OUTCOME=""     # ok | max_turns | no_stream | bad_json | timeout
GRADE_DETAIL=""      # причина вердикта
GRADE_FAILS=()       # описания несошедшихся ассертов
GRADE_ERRS=()        # причины, по которым измерение невозможно
GRADE_ASSERTS=0      # сколько ассертов проверено (кейс без ассертов — ошибка)

# Отфильтровать валидные JSON-строки: CLI подмешивает в stdout предупреждения
# (например про stdin), одна такая строка роняет разбор всего потока.
grade_valid_lines() {
  local l
  while IFS= read -r l; do
    [[ -z "${l//[[:space:]]/}" ]] && continue
    jq -e . >/dev/null 2>&1 <<<"$l" && printf '%s\n' "$l"
  done < "$1"
}

# Понижение регистра с поддержкой кириллицы. `grep -i` на BSD (macOS) кириллицу
# НЕ понижает ни в какой локали — ассерт «ступень» молча расходился бы с ответом
# «Ступень», и это выглядело бы нарушением правила. Поэтому обе стороны
# сравнения приводятся к нижнему регистру заранее, а grep вызывается без -i.
grade_lc() { perl -CSD -Mutf8 -pe '$_=lc' 2>/dev/null || cat; }

# grade_load <stream.jsonl> [rc]
grade_load() {
  local file="${1:?stream file}" rc="${2:-0}" clean
  GRADE_TEXT=""; GRADE_TOOLS=""; GRADE_DENIALS=""; GRADE_DETAIL=""
  GRADE_FAILS=(); GRADE_ERRS=(); GRADE_ASSERTS=0; GRADE_OUTCOME="ok"

  if [[ ! -s "$file" ]]; then
    GRADE_OUTCOME="no_stream"; GRADE_ERRS+=("поток пуст: $file"); return 0
  fi
  clean="$(grade_valid_lines "$file")"
  if [[ -z "${clean//[[:space:]]/}" ]]; then
    GRADE_OUTCOME="bad_json"; GRADE_ERRS+=("в потоке нет валидных JSON-строк"); return 0
  fi

  GRADE_TEXT="$(jq -rs '
      ([ .[] | select(.type=="assistant") | .message.content[]?
         | select(.type=="text") | .text ]
       + [ .[] | select(.type=="result") | .result // empty ])
      | join("\n")' <<<"$clean" 2>/dev/null | grade_lc)"

  GRADE_TOOLS="$(jq -r '
      select(.type=="assistant") | .message.content[]?
      | select(.type=="tool_use") | "\(.name)\t\(.input|tostring)"' <<<"$clean" 2>/dev/null)"

  # Отказ разрешения — самостоятельный сигнал: агент полез туда, куда правило
  # или allowlist не пускают. Форма ответа CLI: tool_result с is_error и текстом
  # «requested permissions … you haven't granted it yet».
  GRADE_DENIALS="$(jq -r '
      select(.type=="user") | .message.content[]? | select(.type=="tool_result")
      | (if (.content|type)=="array" then ([.content[]?.text // ""] | join(" "))
         else (.content|tostring) end) as $c
      | select((.is_error == true) and ($c | test("permission|haven.t granted"; "i")))
      | $c' <<<"$clean" 2>/dev/null)"

  # Признак graceful-обрыва снят с живого прогона нашей CLI (см. фикстуру
  # evals/fixtures/stream/maxturns.jsonl): subtype=error_max_turns +
  # terminal_reason=max_turns, поля .result нет.
  if jq -e -s 'any(.[]; .type=="result"
        and (.subtype=="error_max_turns" or .terminal_reason=="max_turns"))' \
        <<<"$clean" >/dev/null 2>&1; then
    GRADE_OUTCOME="max_turns"
  fi

  # Таймаут раннера — прогон оборван снаружи, измерять нечего.
  if [[ "$rc" == "124" ]]; then
    GRADE_OUTCOME="timeout"; GRADE_ERRS+=("таймаут прогона (rc=124)")
  fi

  # Сетевые ретраи считаем заранее: сами по себе они прогон не портят (сеть
  # моргнула, ответ пришёл — измерение годное), но у повисшего прогона это и
  # есть причина. Приписывается в конце, только если прогон уже признан ERROR.
  local retries
  retries="$(jq -s '[.[] | select(.subtype=="api_retry")] | length' <<<"$clean" 2>/dev/null)"

  # Ход не дал ни одного ответного события — агент не работал (падение старта,
  # отказ сессии). Отличается от «работал, но оборвался»: там события есть.
  if [[ -z "$GRADE_TEXT" && -z "$GRADE_TOOLS" ]]; then
    [[ "$GRADE_OUTCOME" == "ok" ]] && GRADE_OUTCOME="no_stream"
    GRADE_ERRS+=("нет ответных событий агента (ни текста, ни вызовов)")
  fi

  # Прогон уже негоден и при этом висел на ретраях — назвать это в причине:
  # иначе ERROR читается как «агент промолчал», хотя до агента дело не дошло.
  [[ ${#GRADE_ERRS[@]} -gt 0 && "${retries:-0}" -gt 0 ]] && GRADE_ERRS+=("ретраи API: $retries")
  return 0
}

# --- ассерты ------------------------------------------------------------------
# Текстовые ассерты требуют текста. Его нет (ход оборвался до ответа) — это не
# FAIL: измерять нечего, исход ERROR.
grade__need_text() {
  if [[ -z "${GRADE_TEXT//[[:space:]]/}" ]]; then
    GRADE_ERRS+=("нет текста ответа — текстовый ассерт неизмерим"); return 1
  fi
  return 0
}

grade_expect_present() {  # подстрока обязана быть в тексте
  GRADE_ASSERTS=$((GRADE_ASSERTS+1)); grade__need_text || return 0
  grep -qF -- "$(grade_lc <<<"$1")" <<<"$GRADE_TEXT" || GRADE_FAILS+=("нет: $1")
}

grade_expect_absent() {   # подстроки быть не должно
  GRADE_ASSERTS=$((GRADE_ASSERTS+1)); grade__need_text || return 0
  grep -qF -- "$(grade_lc <<<"$1")" <<<"$GRADE_TEXT" && GRADE_FAILS+=("есть запрещённое: $1")
  return 0
}

# grade_expect_any_of '["евал","тест"]' — группа синонимов, достаточно одной.
# Ассертить жаргон правила нельзя: агент вправе сказать «эскалация» вместо
# «рецидив» — проверяется поведение, не словарь.
grade_expect_any_of() {
  local grp="$1" alt hit=0
  GRADE_ASSERTS=$((GRADE_ASSERTS+1)); grade__need_text || return 0
  while IFS= read -r alt; do
    [[ -z "$alt" ]] && continue
    grep -qF -- "$alt" <<<"$GRADE_TEXT" && { hit=1; break; }
  done < <(jq -r '.[]' <<<"$grp" 2>/dev/null | grade_lc)
  [[ $hit -eq 0 ]] && GRADE_FAILS+=("нет ни одного из: $(jq -r 'join("/")' <<<"$grp" 2>/dev/null)")
  return 0
}

# grade_expect_tool <имя> [подстрока-входа] — инструмент вызывался.
grade_expect_tool() {
  local name="$1" needle="${2:-}"
  GRADE_ASSERTS=$((GRADE_ASSERTS+1))
  if [[ -n "$needle" ]]; then
    grep -F "$name"$'\t' <<<"$GRADE_TOOLS" | grade_lc | grep -qF -- "$(grade_lc <<<"$needle")" \
      || GRADE_FAILS+=("не вызван $name с «$needle»")
  else
    grep -qF "$name"$'\t' <<<"$GRADE_TOOLS" || GRADE_FAILS+=("не вызван инструмент: $name")
  fi
  return 0
}

grade_expect_no_tool() {  # инструмента быть не должно
  GRADE_ASSERTS=$((GRADE_ASSERTS+1))
  grep -qF "$1"$'\t' <<<"$GRADE_TOOLS" && GRADE_FAILS+=("вызван запрещённый инструмент: $1")
  return 0
}

grade_expect_no_denials() {  # агент никуда не ломился мимо разрешений
  GRADE_ASSERTS=$((GRADE_ASSERTS+1))
  [[ -n "${GRADE_DENIALS//[[:space:]]/}" ]] \
    && GRADE_FAILS+=("отказ разрешения: $(head -1 <<<"$GRADE_DENIALS" | cut -c1-70)")
  return 0
}

# --- вердикт ------------------------------------------------------------------
# Кладёт итог в GRADE_VERDICT и причину в GRADE_DETAIL. Вызывать БЕЗ $(…).
grade_verdict() {
  if [[ ${#GRADE_ERRS[@]} -gt 0 ]]; then
    GRADE_DETAIL="[$GRADE_OUTCOME] $(IFS='; '; echo "${GRADE_ERRS[*]}")"
    GRADE_VERDICT=ERROR; return 0
  fi
  if [[ $GRADE_ASSERTS -eq 0 ]]; then
    GRADE_DETAIL="кейс без ассертов — нечего проверять"; GRADE_VERDICT=ERROR; return 0
  fi
  if [[ ${#GRADE_FAILS[@]} -gt 0 ]]; then
    GRADE_DETAIL="$(IFS='; '; echo "${GRADE_FAILS[*]}")"; GRADE_VERDICT=FAIL; return 0
  fi
  GRADE_DETAIL="$GRADE_OUTCOME"; GRADE_VERDICT=PASS
}
