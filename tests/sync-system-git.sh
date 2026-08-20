#!/usr/bin/env bash
# Тест git-логики universal-sync-system.sh на временных репозиториях. Сети не
# требует: «origin» — локальный bare-репозиторий, fetch ходит по файловому пути.
#
# Раннер кейсов (tests/run.sh) кормит хук событием на stdin и смотрит stdout —
# этого хватает для печати директив, но не для того, ЧТО хук делает с гитом:
# сдвинулся ли HEAD, цел ли незакоммиченный файл, откачен ли конфликт, продвинут
# ли указатель main у общего чекаута. Проверяется это здесь — по состоянию
# репозитория после прогона.
#
# Запуск: bash tests/sync-system-git.sh   (exit 0 — всё зелёное)
set -u
export LC_ALL=C.UTF-8

REPO="$(cd "$(dirname "$0")/.." && pwd)"
HOOK="$REPO/.claude/hooks/universal-sync-system.sh"

pass=0; fail=0; fails=()
ok()  { pass=$((pass+1)); printf 'PASS  %s\n' "$1"; }
bad() { fail=$((fail+1)); fails+=("$1 — $2"); printf 'FAIL  %s — %s\n' "$1" "$2"; }

G() { git -c user.email=t@t -c user.name=t -c init.defaultBranch=main -c advice.detachedHead=false "$@"; }

# Песочница: bare-origin + рабочий клон, снятый ДО того, как main уехал вперёд —
# ровно как система уезжает вперёд от живой сессии.
sandbox() {
  local sb; sb="$(mktemp -d "${TMPDIR:-/tmp}/sync-system-test.XXXXXX")"
  G init --quiet --bare "$sb/origin.git"
  G clone --quiet "$sb/origin.git" "$sb/seed" 2>/dev/null
  mkdir -p "$sb/seed/.claude/hooks"
  echo "base" > "$sb/seed/file.txt"
  echo "rule v1" > "$sb/seed/CLAUDE.md"
  echo "hook v1" > "$sb/seed/.claude/hooks/universal-example.sh"
  G -C "$sb/seed" add -A
  G -C "$sb/seed" commit --quiet -m "base"
  G -C "$sb/seed" push --quiet origin main
  G clone --quiet "$sb/origin.git" "$sb/work" 2>/dev/null
  # Хук зовёт git без своих -c: рабочий чекаут обязан нести identity сам, иначе
  # вливание падает «tell me who you are» и тест мерил бы не то.
  git -C "$sb/work" config user.email t@t
  git -C "$sb/work" config user.name t
  echo "base + upstream" > "$sb/seed/file.txt"
  echo "hook v2" > "$sb/seed/.claude/hooks/universal-example.sh"
  echo "rule v2" > "$sb/seed/CLAUDE.md"
  G -C "$sb/seed" add -A
  G -C "$sb/seed" commit --quiet -m "upstream work"
  G -C "$sb/seed" push --quiet origin main
  printf '%s' "$sb"
}

# $1 — событие, $2 — префикс состояния, $3 — цель синка, $4 — корень проекта сессии.
run_hook() {
  printf '{"hook_event_name":"%s","session_id":"git-test","prompt":"проба %s"}' "$1" "$RANDOM" \
    | env SYNC_SYSTEM_STATE="$2" \
          SYNC_SYSTEM_TARGET="$3" \
          SYNC_SYSTEM_WORKER_INLINE=1 \
          SYNC_SYSTEM_INTERVAL=0 \
          HOOK_ONCE=off \
          CLAUDE_PROJECT_DIR="$4" \
          bash "$HOOK" 2>/dev/null
}

# --- A. свой чекаут, дерево чистое: подтянуто ---------------------------------
t="own / чистое дерево — подтянуто"
sb="$(sandbox)"; st="$sb/state"
G -C "$sb/work" checkout --quiet -b feature
before="$(G -C "$sb/work" rev-parse HEAD)"
run_hook Stop "$st" "$sb/work" "$sb/work" >/dev/null
out="$(run_hook UserPromptSubmit "$st" "$sb/work" "$sb/work")"
after="$(G -C "$sb/work" rev-parse HEAD)"
if [[ "$before" == "$after" ]]; then
  bad "$t" "HEAD не сдвинулся"
elif ! G -C "$sb/work" merge-base --is-ancestor origin/main HEAD 2>/dev/null; then
  bad "$t" "origin/main не влит в HEAD"
elif ! grep -q "Система обновлена" <<<"$out"; then
  bad "$t" "нет строки об обновлении: ${out:0:150}"
elif ! grep -q "CLAUDE.md" <<<"$out"; then
  bad "$t" "не назван CLAUDE.md, который в контекст не доедет: ${out:0:150}"
else
  ok "$t"
fi
rm -rf "$sb"

# --- B. свой чекаут, несохранённая работа: не тронуто --------------------------
t="own / грязное дерево — не тронуто, сигнал"
sb="$(sandbox)"; st="$sb/state"
G -C "$sb/work" checkout --quiet -b feature
echo "моя незакоммиченная работа" > "$sb/work/file.txt"
before="$(G -C "$sb/work" rev-parse HEAD)"
run_hook Stop "$st" "$sb/work" "$sb/work" >/dev/null
out="$(run_hook UserPromptSubmit "$st" "$sb/work" "$sb/work")"
after="$(G -C "$sb/work" rev-parse HEAD)"
if [[ "$before" != "$after" ]]; then
  bad "$t" "HEAD сдвинулся при грязном дереве"
elif [[ "$(cat "$sb/work/file.txt")" != "моя незакоммиченная работа" ]]; then
  bad "$t" "незакоммиченная работа потеряна"
elif ! grep -q "подтянуть нельзя" <<<"$out"; then
  bad "$t" "нет сигнала об отказе: ${out:0:150}"
else
  ok "$t"
fi
rm -rf "$sb"

# --- C. свой чекаут, конфликт: откачено ---------------------------------------
t="own / конфликт — вливание откачено"
sb="$(sandbox)"; st="$sb/state"
G -C "$sb/work" checkout --quiet -b feature
echo "своя правка того же файла" > "$sb/work/file.txt"
G -C "$sb/work" add -A
G -C "$sb/work" commit --quiet -m "local edit"
before="$(G -C "$sb/work" rev-parse HEAD)"
run_hook Stop "$st" "$sb/work" "$sb/work" >/dev/null
out="$(run_hook UserPromptSubmit "$st" "$sb/work" "$sb/work")"
after="$(G -C "$sb/work" rev-parse HEAD)"
if [[ "$before" != "$after" ]]; then
  bad "$t" "HEAD сдвинулся, хотя вливание конфликтует"
elif [[ -n "$(G -C "$sb/work" status --porcelain)" ]]; then
  bad "$t" "дерево осталось в состоянии конфликта"
elif ! grep -q "конфликт" <<<"$out"; then
  bad "$t" "нет сигнала о конфликте: ${out:0:150}"
else
  ok "$t"
fi
rm -rf "$sb"

# --- D. общий чекаут не на main: рабочее дерево не трогаем ---------------------
t="shared / не на main — двигаем только указатель"
sb="$(sandbox)"; st="$sb/state"
mkdir -p "$sb/other-project"
G -C "$sb/work" checkout --quiet -b vlad-branch
before="$(G -C "$sb/work" rev-parse HEAD)"
run_hook Stop "$st" "$sb/work" "$sb/other-project" >/dev/null
out="$(run_hook UserPromptSubmit "$st" "$sb/work" "$sb/other-project")"
after="$(G -C "$sb/work" rev-parse HEAD)"
if [[ "$before" != "$after" ]]; then
  bad "$t" "рабочее дерево общего чекаута сдвинуто"
elif [[ "$(G -C "$sb/work" rev-parse main)" != "$(G -C "$sb/work" rev-parse origin/main)" ]]; then
  bad "$t" "указатель main не продвинут"
elif ! grep -q "не на main" <<<"$out"; then
  bad "$t" "нет сигнала о посторонней ветке: ${out:0:150}"
else
  ok "$t"
fi
rm -rf "$sb"

# --- E. общий чекаут на main и чист: перемотка ---------------------------------
t="shared / на main и чист — перемотан"
sb="$(sandbox)"; st="$sb/state"
mkdir -p "$sb/other-project"
before="$(G -C "$sb/work" rev-parse HEAD)"
run_hook Stop "$st" "$sb/work" "$sb/other-project" >/dev/null
out="$(run_hook UserPromptSubmit "$st" "$sb/work" "$sb/other-project")"
after="$(G -C "$sb/work" rev-parse HEAD)"
if [[ "$before" == "$after" ]]; then
  bad "$t" "HEAD общего чекаута не перемотан"
elif [[ "$after" != "$(G -C "$sb/work" rev-parse origin/main)" ]]; then
  bad "$t" "HEAD не совпал с origin/main"
elif ! grep -q "Система обновлена" <<<"$out"; then
  bad "$t" "нет строки об обновлении: ${out:0:150}"
else
  ok "$t"
fi
rm -rf "$sb"

# --- F. отставания нет: молчание ----------------------------------------------
t="отставания нет — молчание"
sb="$(sandbox)"; st="$sb/state"
G -C "$sb/work" pull --quiet --ff-only origin main 2>/dev/null
run_hook Stop "$st" "$sb/work" "$sb/work" >/dev/null
out="$(run_hook UserPromptSubmit "$st" "$sb/work" "$sb/work")"
if [[ -n "${out//[$' \t\n\r']/}" ]]; then
  bad "$t" "хук что-то напечатал: ${out:0:150}"
else
  ok "$t"
fi
rm -rf "$sb"

# --- G. отчёт применяется один раз --------------------------------------------
t="отчёт применяется один раз"
sb="$(sandbox)"; st="$sb/state"
G -C "$sb/work" checkout --quiet -b feature
run_hook Stop "$st" "$sb/work" "$sb/work" >/dev/null
first="$(run_hook UserPromptSubmit "$st" "$sb/work" "$sb/work")"
second="$(run_hook UserPromptSubmit "$st" "$sb/work" "$sb/work")"
if ! grep -q "Система обновлена" <<<"$first"; then
  bad "$t" "первый вызов не применил отчёт"
elif [[ -n "${second//[$' \t\n\r']/}" ]]; then
  bad "$t" "второй вызов напечатал повтор: ${second:0:150}"
else
  ok "$t"
fi
rm -rf "$sb"

# --- H. автономный прогон ничего не мутирует ----------------------------------
t="автономный прогон — без мутаций и молча"
sb="$(sandbox)"; st="$sb/state"
G -C "$sb/work" checkout --quiet -b feature
before="$(G -C "$sb/work" rev-parse HEAD)"
out="$(CRAFT_AUTONOMOUS=1 run_hook Stop "$st" "$sb/work" "$sb/work")"
out+="$(CRAFT_AUTONOMOUS=1 run_hook UserPromptSubmit "$st" "$sb/work" "$sb/work")"
after="$(G -C "$sb/work" rev-parse HEAD)"
if [[ "$before" != "$after" ]]; then
  bad "$t" "автономный прогон подтянул код"
elif [[ -n "${out//[$' \t\n\r']/}" ]]; then
  bad "$t" "автономный прогон напечатал: ${out:0:150}"
else
  ok "$t"
fi
rm -rf "$sb"

# --- I. цель без ветки-источника: сигнал «проверить не удалось» ----------------
t="источник недоступен — сигнал о непроверенной свежести"
sb="$(sandbox)"; st="$sb/state"
G -C "$sb/work" checkout --quiet -b feature
G -C "$sb/work" remote set-url origin "$sb/no-such-origin.git"
run_hook Stop "$st" "$sb/work" "$sb/work" >/dev/null
out="$(run_hook UserPromptSubmit "$st" "$sb/work" "$sb/work")"
if ! grep -q "проверить не удалось" <<<"$out"; then
  bad "$t" "молчание вместо сигнала: ${out:0:150}"
else
  ok "$t"
fi
rm -rf "$sb"

# --- J. ветка цели сменилась между сетевым шагом и применением ----------------
t="ветка цели сменилась после отчёта — судим по свежей"
sb="$(sandbox)"; st="$sb/state"
mkdir -p "$sb/other-project"
run_hook Stop "$st" "$sb/work" "$sb/other-project" >/dev/null
# Отчёт снят, когда цель стояла на main; теперь Влад переключил её на свою ветку.
G -C "$sb/work" checkout --quiet -b vlad-branch
before="$(G -C "$sb/work" rev-parse HEAD)"
out="$(run_hook UserPromptSubmit "$st" "$sb/work" "$sb/other-project")"
after="$(G -C "$sb/work" rev-parse HEAD)"
if [[ "$before" != "$after" ]]; then
  bad "$t" "перемотана ветка, на которую переключились после отчёта"
elif [[ "$(G -C "$sb/work" rev-parse main)" != "$(G -C "$sb/work" rev-parse origin/main)" ]]; then
  bad "$t" "указатель main не продвинут"
elif ! grep -q "не на main" <<<"$out"; then
  bad "$t" "решение принято по ветке из отчёта: ${out:0:150}"
else
  ok "$t"
fi
rm -rf "$sb"

# --- K. цель уехала вперёд сама между отчётом и применением -------------------
t="цель подтянулась сама — вливания нет, сигнал есть"
sb="$(sandbox)"; st="$sb/state"
G -C "$sb/work" checkout --quiet -b feature
run_hook Stop "$st" "$sb/work" "$sb/work" >/dev/null
G -C "$sb/work" merge --quiet --no-edit origin/main
head_after_manual="$(G -C "$sb/work" rev-parse HEAD)"
out="$(run_hook UserPromptSubmit "$st" "$sb/work" "$sb/work")"
if [[ "$(G -C "$sb/work" rev-parse HEAD)" != "$head_after_manual" ]]; then
  bad "$t" "хук влил поверх уже подтянутой цели"
elif [[ -n "${out//[$' \t\n\r']/}" ]]; then
  bad "$t" "отставания уже нет, а хук напечатал: ${out:0:150}"
else
  ok "$t"
fi
rm -rf "$sb"

# --- Ветка правил Craft: сравнение снимков ------------------------------------
# Отчёт подкладывается готовым — сеть в этих кейсах не нужна, проверяется ровно
# сравнение базы со свежим снимком и то, что попадает в контекст.
rules_case() {  # $1 база, $2 свежий снимок; печатает stdout применения
  local sb st
  sb="$(mktemp -d "${TMPDIR:-/tmp}/sync-rules-test.XXXXXX")"
  st="$sb/state"
  mkdir -p "$sb/target/.claude"
  printf '%s' "$1" > "$st.rules-base"
  printf '%s' "$2" > "$st.rules-fresh"
  printf 'ahead=0\nscope=own\nbranch=main\nhead_before=x\nrules=%s\n' "$st.rules-fresh" > "$st.report"
  printf '{"hook_event_name":"UserPromptSubmit","session_id":"rules-test","prompt":"проба %s"}' "$RANDOM" \
    | env SYNC_SYSTEM_STATE="$st" SYNC_SYSTEM_TARGET="$sb/target" HOOK_ONCE=off \
          CLAUDE_PROJECT_DIR="$sb/target" bash "$HOOK" 2>/dev/null
  rm -rf "$sb"
}

HEAD_LINE='=== Craft: роутер «Память для Claude», авто-обновлён SessionStart-хуком (2026-08-20T11:00:00Z) ==='
MEM_OPEN='<page id="604c8d7f"><pageTitle>🧠 Память (регенерируемая)</pageTitle>'

t="правила изменились — печатается дельта"
out="$(rules_case "$HEAD_LINE
Правило: писать план заголовками уровня два.
$MEM_OPEN
факты про квартиру" "$HEAD_LINE
Правило: писать план заголовками уровня три.
$MEM_OPEN
факты про квартиру")"
if ! grep -q "Правила Craft изменились" <<<"$out"; then
  bad "$t" "нет директивы: ${out:0:150}"
elif ! grep -q "уровня три" <<<"$out"; then
  bad "$t" "в дельте нет изменившегося правила: ${out:0:200}"
else
  ok "$t"
fi

t="правила те же, отличается только время сборки — молчание"
out="$(rules_case "$HEAD_LINE
Правило: одно и то же." '=== Craft: роутер «Память для Claude», авто-обновлён SessionStart-хуком (2026-08-20T23:59:59Z) ===
Правило: одно и то же.')"
if [[ -n "${out//[$' \t\n\r']/}" ]]; then
  bad "$t" "напечатана ложная дельта: ${out:0:150}"
else
  ok "$t"
fi

t="изменилась только регенерируемая память — молчание"
out="$(rules_case "$HEAD_LINE
Правило: не меняется.
$MEM_OPEN
цена квартиры 17,7 млн" "$HEAD_LINE
Правило: не меняется.
$MEM_OPEN
цена квартиры 18,1 млн, сделка прошла")"
if [[ -n "${out//[$' \t\n\r']/}" ]]; then
  bad "$t" "рутина актуализации фактов подняла дельту правил: ${out:0:150}"
else
  ok "$t"
fi

t="базы нет — молчание и засев"
sb="$(mktemp -d "${TMPDIR:-/tmp}/sync-rules-seed.XXXXXX")"; st="$sb/state"
mkdir -p "$sb/target/.claude"
printf '%s\nПравило.' "$HEAD_LINE" > "$st.rules-fresh"
printf 'ahead=0\nscope=own\nbranch=main\nhead_before=x\nrules=%s\n' "$st.rules-fresh" > "$st.report"
out="$(printf '{"hook_event_name":"UserPromptSubmit","session_id":"seed-test","prompt":"x"}' \
  | env SYNC_SYSTEM_STATE="$st" SYNC_SYSTEM_TARGET="$sb/target" HOOK_ONCE=off \
        CLAUDE_PROJECT_DIR="$sb/target" bash "$HOOK" 2>/dev/null)"
if [[ -n "${out//[$' \t\n\r']/}" ]]; then
  bad "$t" "вместо молчания напечатан обрезок роутера: ${out:0:150}"
elif [[ ! -s "$st.rules-base" ]]; then
  bad "$t" "база не засеяна"
else
  ok "$t"
fi
rm -rf "$sb"

# Базой обязан быть снимок ЭТОЙ сессии — тот, что импортировал её CLAUDE.md.
# Снимок из чекаута цели за базу не годится: в сессии другого проекта это чужой
# файл произвольного возраста.
rules_case_snapshot() {  # $1 снимок в чекауте сессии, $2 снимок в чекауте цели, $3 свежий
  local sb st
  sb="$(mktemp -d "${TMPDIR:-/tmp}/sync-seed-test.XXXXXX")"
  st="$sb/state"
  mkdir -p "$sb/session/.claude" "$sb/target/.claude"
  [[ -n "$1" ]] && printf '%s' "$1" > "$sb/session/.claude/craft-router-context.md"
  [[ -n "$2" ]] && printf '%s' "$2" > "$sb/target/.claude/craft-router-context.md"
  printf '%s' "$3" > "$st.rules-fresh"
  printf 'ahead=0\nscope=shared\nbranch=main\nhead_before=x\nrules=%s\n' "$st.rules-fresh" > "$st.report"
  printf '{"hook_event_name":"UserPromptSubmit","session_id":"seed-%s","prompt":"x","cwd":"%s"}' "$RANDOM" "$sb/session" \
    | env SYNC_SYSTEM_STATE="$st" SYNC_SYSTEM_TARGET="$sb/target" HOOK_ONCE=off \
          CLAUDE_PROJECT_DIR="$sb/session" bash "$HOOK" 2>/dev/null
  rm -rf "$sb"
}

t="база — снимок старта СВОЕЙ сессии, дельта против него"
out="$(rules_case_snapshot "$HEAD_LINE
Правило: версия из контекста сессии." "$HEAD_LINE
Правило: чужой снимок в чекауте цели." "$HEAD_LINE
Правило: версия свежая, правило изменили.")"
if ! grep -q "Правила Craft изменились" <<<"$out"; then
  bad "$t" "дельта не напечатана: ${out:0:150}"
elif ! grep -q "правило изменили" <<<"$out"; then
  bad "$t" "в дельте нет изменившегося правила: ${out:0:200}"
elif grep -q "чужой снимок" <<<"$out"; then
  bad "$t" "базой взят снимок чекаута цели, а не своей сессии: ${out:0:200}"
else
  ok "$t"
fi

t="снимок есть только в чекауте цели — дельты нет"
out="$(rules_case_snapshot "" "$HEAD_LINE
Правило: чужой снимок произвольного возраста." "$HEAD_LINE
Правило: версия свежая.")"
if [[ -n "${out//[$' \t\n\r']/}" ]]; then
  bad "$t" "дельта посчитана против чужого снимка: ${out:0:150}"
else
  ok "$t"
fi

t="дельта режется по бюджету"
big_old="$HEAD_LINE"; big_new="$HEAD_LINE"
for i in $(seq 1 400); do big_old+="
строка правила $i"; big_new+="
строка правила $i изменена"; done
out="$(SYNC_SYSTEM_DELTA_BUDGET=1200 rules_case "$big_old" "$big_new")"
if [[ "${#out}" -gt 3000 ]]; then
  bad "$t" "дельта не обрезана: ${#out} символов"
elif ! grep -q "Правила Craft изменились" <<<"$out"; then
  bad "$t" "директива потерялась при обрезке"
else
  ok "$t"
fi

printf -- '---\n%d passed, %d failed\n' "$pass" "$fail"
if [[ $fail -gt 0 ]]; then
  printf '%s\n' "${fails[@]}"
  exit 1
fi
exit 0
