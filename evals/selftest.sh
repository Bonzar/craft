#!/usr/bin/env bash
# Самотест ядра грейдера (evals/lib/grade.sh) на записанных потоках.
#
# ЗАЧЕМ. Правку грейдера нельзя проверять прогоном моделей: это 10-15 минут,
# деньги и недетерминизм — из-за этого три поломки раннера подряд обнаружились
# только вручную. Здесь ядро гоняется на фикстурах: без сети, без моделей, за
# секунду. Фикстуры сняты с ЖИВЫХ прогонов CLI (см. fixtures/stream/README.md) —
# рукописный поток воспроизвёл бы ошибку в детекте, а не поймал её.
#
# Usage: bash evals/selftest.sh
set -u
cd "$(dirname "$0")/.." || exit 1
source evals/lib/grade.sh

FIX=evals/fixtures/stream
pass=0; fail=0; fails=(); declare -A covered

# check <имя> <ожидаемый вердикт> <фактический> [деталь]
check() {
  local name="$1" want="$2" got="$3" detail="${4:-}"
  if [[ "$got" == "$want" ]]; then
    pass=$((pass+1)); printf '%-6s %-44s %s\n' PASS "$name" "$got"
  else
    fail=$((fail+1)); fails+=("$name — ждали $want, получили $got: $detail")
    printf '%-6s %-44s %s\n' FAIL "$name" "$got ($detail)"
  fi
}

printf '%-6s %-44s %s\n' RES CASE VERDICT
printf -- '--------------------------------------------------------------------\n'

# 1. Успешный прогон: текст собран, ассерты сходятся → PASS
grade_load "$FIX/success.jsonl" 0
grade_expect_present "четыре"
grade_verdict; covered["ok:PASS"]=1
check "success / текст собран, ассерт сошёлся" PASS "$GRADE_VERDICT" "$GRADE_DETAIL"

# 2. Тот же поток, ассерт не сходится → FAIL (поведение не то)
grade_load "$FIX/success.jsonl" 0
grade_expect_present "пять"
grade_verdict; covered["ok:FAIL"]=1
check "success / несошедшийся ассерт" FAIL "$GRADE_VERDICT" "$GRADE_DETAIL"

# 3. Обрыв по лимиту ходов: событие вызова есть → измерение состоялось, PASS.
#    Это главный регресс: старый грейдер видел здесь пустоту и печатал FAIL.
grade_load "$FIX/maxturns.jsonl" 1
grade_expect_tool Read
grade_verdict; covered["max_turns:PASS"]=1
check "maxturns / вызов виден несмотря на обрыв" PASS "$GRADE_VERDICT" "$GRADE_DETAIL"
[[ "$GRADE_OUTCOME" == "max_turns" ]] || { fail=$((fail+1)); fails+=("maxturns: исход $GRADE_OUTCOME, ждали max_turns"); }

# 4. Тот же обрыв, но ассерт текстовый: текста ответа нет → ERROR, не FAIL
grade_load "$FIX/maxturns.jsonl" 1
grade_expect_present "что угодно"
grade_verdict; covered["max_turns:ERROR"]=1
check "maxturns / текстовый ассерт неизмерим" ERROR "$GRADE_VERDICT" "$GRADE_DETAIL"

# 5. Отказ разрешения виден отдельным сигналом
grade_load "$FIX/denial.jsonl" 0
grade_expect_no_denials
grade_verdict; covered["denial:FAIL"]=1
check "denial / отказ разрешения пойман" FAIL "$GRADE_VERDICT" "$GRADE_DETAIL"

# 6. Запрещённый инструмент вызывался
grade_load "$FIX/denial.jsonl" 0
grade_expect_no_tool Write
grade_verdict; covered["tool:FAIL"]=1
check "denial / запрещённый инструмент пойман" FAIL "$GRADE_VERDICT" "$GRADE_DETAIL"

# 7. Поток без ответных событий → ERROR (прогон не состоялся)
grade_load "$FIX/empty.jsonl" 0
grade_expect_present "что угодно"
grade_verdict; covered["no_stream:ERROR"]=1
check "empty / нет ответных событий" ERROR "$GRADE_VERDICT" "$GRADE_DETAIL"

# 8. Битый поток → ERROR, а не молчаливый ноль
tmp="$(mktemp)"; printf 'не json\nтоже не json\n' > "$tmp"
grade_load "$tmp" 0
grade_expect_present "что угодно"
grade_verdict; covered["bad_json:ERROR"]=1
check "bad_json / битый поток" ERROR "$GRADE_VERDICT" "$GRADE_DETAIL"
rm -f "$tmp"

# 9. Мусорные строки CLI (предупреждение про stdin) не ломают разбор.
#    Реальный случай: первая строка stdout — «Warning: no stdin data received».
tmp="$(mktemp)"
{ echo "Warning: no stdin data received in 3s, proceeding without it."; cat "$FIX/success.jsonl"; } > "$tmp"
grade_load "$tmp" 0
grade_expect_present "четыре"
grade_verdict; covered["noise:PASS"]=1
check "noise / предупреждение CLI отфильтровано" PASS "$GRADE_VERDICT" "$GRADE_DETAIL"
rm -f "$tmp"

# 9b. Регистр кириллицы. Ответ «Четыре.» обязан найтись по ассерту «ЧЕТЫРЕ»:
#     `grep -i` на BSD кириллицу не понижает, и такой ассерт молча расходился бы
#     с верным ответом — это выглядело бы нарушением правила.
grade_load "$FIX/success.jsonl" 0
grade_expect_present "ЧЕТЫРЕ"
grade_verdict; covered["case:PASS"]=1
check "регистр / кириллица понижается с обеих сторон" PASS "$GRADE_VERDICT" "$GRADE_DETAIL"

# 9c. Повисший на сетевых ретраях прогон: причина обязана быть в детали, иначе
#     ERROR читается как «агент промолчал», хотя до агента дело не дошло.
grade_load "$FIX/api-retry.jsonl" 124
grade_expect_present "что угодно"
grade_verdict; covered["retry:ERROR"]=1
check "api_retry / причина названа в детали" ERROR "$GRADE_VERDICT" "$GRADE_DETAIL"
[[ "$GRADE_DETAIL" == *"ретраи API: 7"* ]] || { fail=$((fail+1)); fails+=("api_retry: в детали нет числа ретраев: $GRADE_DETAIL"); }

# 9d. Ретраи сами по себе прогон не портят: сеть моргнула, ответ пришёл — это
#     PASS. Иначе любая сетевая икота превращала бы годное измерение в ERROR.
grade_load "$FIX/retry-recovered.jsonl" 0
grade_expect_present "четыре"
grade_verdict; covered["retry:PASS"]=1
check "api_retry / ответ пришёл — прогон засчитан" PASS "$GRADE_VERDICT" "$GRADE_DETAIL"

# 9e. Улика доставки правила. Без неё вердикт не выпускается: прогон не
#     доказывает, что проверяемое правило вообще дошло до сессии. Именно на
#     отсутствии этой проверки я принял догадку за факт — дважды подряд.
grade_load "$FIX/rule-delivered.jsonl" 0
grade_require_rule_delivered "incident doc cached"
grade_expect_present "четыре"
grade_verdict; covered["evidence:PASS"]=1
check "улика / правило доставлено — вердикт выпускается" PASS "$GRADE_VERDICT" "$GRADE_DETAIL"

grade_load "$FIX/rule-missing.jsonl" 0
grade_require_rule_delivered "incident doc cached"
grade_expect_present "четыре"
grade_verdict; covered["evidence:ERROR"]=1
check "улика / нет улики — ERROR, а не вердикт" ERROR "$GRADE_VERDICT" "$GRADE_DETAIL"

# 9f. Обрыв соединения на полуслове: весь «ответ» агента — сообщение об ошибке.
#     Поток непустой, поэтому прежний грейдер писал FAIL, то есть «правило не
#     соблюдено», хотя агент не сказал ничего по существу.
grade_load "$FIX/api-cut.jsonl" 0
grade_expect_present "ступень"
grade_verdict; covered["api_cut:ERROR"]=1
check "обрыв связи / не провал поведения" ERROR "$GRADE_VERDICT" "$GRADE_DETAIL"

# 10. Таймаут раннера → ERROR, даже если поток выглядит полным
grade_load "$FIX/success.jsonl" 124
grade_expect_present "четыре"
grade_verdict; covered["timeout:ERROR"]=1
check "timeout / rc=124 поверх годного потока" ERROR "$GRADE_VERDICT" "$GRADE_DETAIL"

# 11. Кейс без ассертов не проходит вакуумно
grade_load "$FIX/success.jsonl" 0
grade_verdict; covered["noassert:ERROR"]=1
check "без ассертов / вакуумный проход закрыт" ERROR "$GRADE_VERDICT" "$GRADE_DETAIL"

# --- регресс поведения: разбор против произнесения слов -----------------------
# Четыре записанных ответа агента, покрывающих все сочетания. До этой правки
# вердикт решала лексика: отказ, в котором мимоходом прозвучало «eval», проходил
# как зелёный, а почти такой же отказ падал.
BEH=evals/fixtures/behavior
beh() {  # <имя> <фикстура> <ожидаемый вердикт>
  grade_load "$BEH/$2.jsonl" 0
  grade_expect_no_refusal
  grade_expect_present "ступень"
  grade_expect_any_of '["евал","eval","тест"]'
  grade_verdict
  check "$1" "$3" "$GRADE_VERDICT" "$GRADE_DETAIL"
}
beh "поведение / отказ, прошедший по лексике" refusal-passed-on-lexis FAIL
beh "поведение / отказ без нужных слов"       refusal-failed          FAIL
beh "поведение / разбор без названной ступени" analysis-no-rung       FAIL
beh "поведение / полный разбор"                analysis-full          PASS
covered["behavior:FAIL"]=1; covered["behavior:PASS"]=1

# --- валидация формата кейса (evals/lib/runner.sh) ----------------------------
# Кейс без ассертов не должен молча проходить: пустой кейс создаёт иллюзию
# покрытия — набор зелёный, а не проверяет ничего.
source evals/lib/runner.sh
vcheck() {  # <имя> <ожидаемая-подстрока-ошибки|-> <case-json>
  local name="$1" want="$2" got; got="$(eval__validate "$3")"
  if [[ "$want" == "-" ]]; then
    [[ -z "$got" ]] && { pass=$((pass+1)); printf '%-6s %-44s %s\n' PASS "$name" "валиден"; return; }
    fail=$((fail+1)); fails+=("$name — кейс валиден, а забракован: $got")
    printf '%-6s %-44s %s\n' FAIL "$name" "$got"; return
  fi
  if [[ "$got" == *"$want"* ]]; then
    pass=$((pass+1)); printf '%-6s %-44s %s\n' PASS "$name" "$got"
  else
    fail=$((fail+1)); fails+=("$name — ждали ошибку «${want}», получили «${got}»")
    printf '%-6s %-44s %s\n' FAIL "$name" "${got:-<нет ошибки>}"
  fi
}
vcheck "формат / кейс без ассертов забракован" "нет ни одного ассерта" \
  '{"name":"x","prompt":"y"}'
vcheck "формат / кейс без prompt забракован" "нет поля prompt" '{"name":"x","expect_present":["a"]}'
vcheck "формат / кейс без name забракован" "нет поля name" '{"prompt":"y","expect_present":["a"]}'
vcheck "формат / ассерт по инструменту засчитан" "-" '{"name":"x","prompt":"y","expect_tool":["Read"]}'
# Промпт, не запускающий подачу правила, бракуется ДО прогона: правило до сессии
# не дойдёт, и вердикт был бы о памяти модели, а не о соблюдении.
vcheck "формат / промпт не запускает подачу правила" "не запускает подачу правила" \
  '{"name":"x","prompt":"посчитай два плюс два","expect_present":["a"],"rule_evidence":"incident doc cached","rule_trigger_hook":".claude/hooks/universal-detect-incident.sh"}'
vcheck "формат / промпт запускает подачу правила" "-" \
  '{"name":"x","prompt":"ты нарушил записанное правило, разбери инцидент","expect_present":["a"],"rule_evidence":"incident doc cached","rule_trigger_hook":".claude/hooks/universal-detect-incident.sh"}'
vcheck "формат / кейс про разбор без материала" "нет поля material" \
  '{"name":"x","prompt":"y","expect_present":["a"],"expect_no_refusal":true}'
covered["validate:BAD"]=1; covered["validate:OK"]=1; covered["trigger:BAD"]=1; covered["trigger:OK"]=1
covered["material:BAD"]=1

# Песочница прогона: пишущие инструменты и делегирование субагенту должны быть
# закрыты. Живая проверка — в приёмке прогона; здесь сторожим состав списка,
# чтобы правка раннера не открыла дыру молча.
sandbox_missing=()
for t in Bash Write Edit MultiEdit NotebookEdit Task Agent; do
  [[ " $EVAL_DENY_TOOLS " == *" $t "* ]] || sandbox_missing+=("$t")
done
if [[ ${#sandbox_missing[@]} -eq 0 ]]; then
  pass=$((pass+1)); printf '%-6s %-44s %s\n' PASS "песочница / запрет записи и субагентов" "ok"
else
  fail=$((fail+1)); printf '%-6s %-44s %s\n' FAIL "песочница / запрет записи и субагентов" "открыто: ${sandbox_missing[*]}"
  fails+=("песочница: в EVAL_DENY_TOOLS не закрыты ${sandbox_missing[*]}")
fi

# Все кейсы всех наборов обязаны проходить валидацию — опечатка в jsonl иначе
# всплывёт только на живом прогоне через 15 минут.
badcases=()
for f in evals/cases/behavioral/*.jsonl; do
  [[ -e "$f" ]] || continue
  n=0
  while IFS= read -r l || [[ -n "$l" ]]; do
    [[ -z "${l//[[:space:]]/}" || "$l" == \#* ]] && continue
    n=$((n+1))
    if ! jq -e . >/dev/null 2>&1 <<<"$l"; then badcases+=("$f:$n — не JSON"); continue; fi
    p="$(eval__validate "$l")"; [[ -n "$p" ]] && badcases+=("$f:$n — $p")
  done < "$f"
done
if [[ ${#badcases[@]} -eq 0 ]]; then
  pass=$((pass+1)); printf '%-6s %-44s %s\n' PASS "формат / все кейсы наборов валидны" "ok"
else
  fail=$((fail+1)); printf '%-6s %-44s %s\n' FAIL "формат / все кейсы наборов валидны" "${#badcases[@]} шт"
  for m in "${badcases[@]}"; do fails+=("$m"); done
fi

# --- покрытие исходов: каждая ветка вердикта должна быть проверена ------------
REQUIRED=(
  "ok:PASS" "ok:FAIL"
  "max_turns:PASS" "max_turns:ERROR"
  "denial:FAIL" "tool:FAIL"
  "no_stream:ERROR" "bad_json:ERROR" "timeout:ERROR" "noassert:ERROR"
  "noise:PASS" "case:PASS" "validate:BAD" "validate:OK" "retry:ERROR" "retry:PASS"
  "evidence:PASS" "evidence:ERROR" "trigger:BAD" "trigger:OK"
  "behavior:PASS" "behavior:FAIL" "material:BAD" "api_cut:ERROR"
)
missing=()
for k in "${REQUIRED[@]}"; do [[ -n "${covered[$k]:-}" ]] || missing+=("$k"); done

# --- самотест красного пути ---------------------------------------------------
# Проверка полезна, только если она в принципе способна упасть. Подменяем
# фикстуру заведомо негодной и убеждаемся, что ядро это ловит.
selftest=()
tmp="$(mktemp)"; printf '{"type":"system","subtype":"init"}\n' > "$tmp"
grade_load "$tmp" 0; grade_expect_present "четыре"
grade_verdict; [[ "$GRADE_VERDICT" == "ERROR" ]] || selftest+=("подмена фикстуры пустышкой не поймана")
rm -f "$tmp"
# И обратное: годная фикстура с заведомо ложным ассертом обязана дать FAIL,
# иначе ассерты не работают вовсе.
grade_load "$FIX/success.jsonl" 0; grade_expect_present "заведомо отсутствующая строка"
grade_verdict; [[ "$GRADE_VERDICT" == "FAIL" ]] || selftest+=("ложный ассерт не дал FAIL")

printf -- '--------------------------------------------------------------------\n'
if [[ ${#fails[@]} -gt 0 ]]; then
  echo "Failures:"; for m in "${fails[@]}"; do echo "  - $m"; done
fi
if [[ ${#missing[@]} -gt 0 ]]; then
  echo "Uncovered outcomes:"; for m in "${missing[@]}"; do echo "  - $m"; done
fi
if [[ ${#selftest[@]} -gt 0 ]]; then
  echo "Self-test failures:"; for m in "${selftest[@]}"; do echo "  - $m"; done
fi
echo "TOTAL: $pass passed; $fail failed; uncovered: ${#missing[@]}; self-test: ${#selftest[@]}"
[[ $fail -eq 0 && ${#missing[@]} -eq 0 && ${#selftest[@]} -eq 0 ]]
