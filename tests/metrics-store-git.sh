#!/usr/bin/env bash
# Тест хранения сводок (universal-metrics-store.js) на временных репозиториях.
# Сети не требует: origin — локальный bare-репозиторий. Раннер кейсов видит
# только stdout хука; что хук делает С ГИТОМ — появилась ли ветка metrics, легла
# ли сводка по дню, заменилась ли строка сессии, цела ли очередь без сети и
# доехала ли потом — проверяется здесь.
#
# Запуск: bash tests/metrics-store-git.sh   (exit 0 — всё зелёное)
set -u
export LC_ALL=C.UTF-8

REPO="$(cd "$(dirname "$0")/.." && pwd)"
HOOK="$REPO/.claude/hooks/universal-metrics-store.js"

pass=0; fail=0; fails=()
ok()  { pass=$((pass+1)); printf 'PASS  %s\n' "$1"; }
bad() { fail=$((fail+1)); fails+=("$1 — $2"); printf 'FAIL  %s — %s\n' "$1" "$2"; }

G() { git -c user.email=t@t -c user.name=t -c init.defaultBranch=main -c advice.detachedHead=false "$@"; }

sandbox() {
  local sb; sb="$(mktemp -d "${TMPDIR:-/tmp}/metrics-store-test.XXXXXX")"
  G init --quiet --bare "$sb/origin.git"
  G clone --quiet "$sb/origin.git" "$sb/work" 2>/dev/null
  echo "base" > "$sb/work/file.txt"
  G -C "$sb/work" add -A
  G -C "$sb/work" commit --quiet -m "base"
  G -C "$sb/work" push --quiet origin main
  printf '%s' "$sb"
}

# Хук ищет сводку по пути `<журнал>.summary.json`: журнал задаётся так, чтобы
# сводка нашлась.
run_hook() {
  local sb="$1" sid="$2" at="$3" turns="$4"
  printf '{"ts":"%s","sid":"%s","ended_at":"%s","turns":%s,"repo":"x"}\n' "$at" "$sid" "$at" "$turns" > "$sb/log.$sid.summary.json"
  printf '{"hook_event_name":"Stop","session_id":"%s"}' "$sid" \
    | env CRAFT_METRICS_LOG="$sb/log.$sid" \
          METRICS_STORE_TARGET="$sb/work" \
          METRICS_STORE_QUEUE="$sb/queue.jsonl" \
          METRICS_STORE_INLINE=1 \
          HOOK_ONCE=off \
          node "$HOOK" 2>&1
}
day_file() { G -C "$1/origin.git" show "metrics:summaries/$2.jsonl" 2>/dev/null; }

# `run_hook` зовётся и из подоболочки под `timeout` (зависание кейса — это и есть
# регресс, и ловится оно только сроком снаружи процесса хука), поэтому она и её
# помощники экспортируются.
export -f run_hook G
export HOOK

# --- A. первая сводка заводит ветку и файл дня --------------------------------
t="первая сводка заводит ветку metrics и файл дня"
sb="$(sandbox)"
out="$(run_hook "$sb" s1 2026-09-02T10:00:00Z 3)"
if ! G -C "$sb/origin.git" rev-parse --verify --quiet refs/heads/metrics >/dev/null; then
  bad "$t" "ветки metrics в origin нет: $out"
elif ! day_file "$sb" 2026-09-02 | grep -q '"sid":"s1"'; then
  bad "$t" "в summaries/2026-09-02.jsonl нет сводки s1"
elif [[ -e "$sb/queue.jsonl" ]]; then
  bad "$t" "очередь не опустела после доставки"
elif [[ "$(G -C "$sb/work" rev-parse --abbrev-ref HEAD)" != "main" ]]; then
  bad "$t" "чекаут сессии сменил ветку"
else ok "$t"; fi

# --- B. вторая сессия того же дня — вторая строка; повтор сессии — замена -----
t="строки по сессиям: новая добавляется, повтор той же заменяется"
run_hook "$sb" s2 2026-09-02T11:00:00Z 1 >/dev/null
run_hook "$sb" s1 2026-09-02T12:00:00Z 7 >/dev/null
n="$(day_file "$sb" 2026-09-02 | wc -l | tr -d ' ')"
if [[ "$n" != "2" ]]; then
  bad "$t" "ожидалось 2 строки, есть $n"
elif ! day_file "$sb" 2026-09-02 | grep -q '"sid":"s1".*"turns":7'; then
  bad "$t" "сводка s1 не заменилась на свежую"
else ok "$t"; fi

# --- C. другой день — другой файл, прежний цел ---------------------------------
t="другой день — свой файл, прежний цел"
run_hook "$sb" s3 2026-09-03T09:00:00Z 2 >/dev/null
if ! day_file "$sb" 2026-09-03 | grep -q '"sid":"s3"'; then
  bad "$t" "нет файла 2026-09-03"
elif [[ "$(day_file "$sb" 2026-09-02 | wc -l | tr -d ' ')" != "2" ]]; then
  bad "$t" "файл 2026-09-02 пострадал"
else ok "$t"; fi

# --- D. нет сети: очередь копится, доезжает на следующем Stop ----------------
t="без сети очередь копится и доезжает на следующем Stop"
sb="$(sandbox)"
run_hook "$sb" s1 2026-09-02T10:00:00Z 1 >/dev/null
G -C "$sb/work" remote set-url origin "$sb/nowhere.git"
out="$(run_hook "$sb" s2 2026-09-02T11:00:00Z 1)"
if ! grep -q "offline" <<<"$out"; then
  bad "$t" "без сети ожидался исход offline: $out"
elif ! grep -q '"sid":"s2"' "$sb/queue.jsonl"; then
  bad "$t" "очередь не сохранила сводку s2"
else
  G -C "$sb/work" remote set-url origin "$sb/origin.git"
  out="$(run_hook "$sb" s3 2026-09-02T12:00:00Z 1)"
  if ! day_file "$sb" 2026-09-02 | grep -q '"sid":"s2"'; then
    bad "$t" "s2 не доехала после восстановления сети: $out"
  elif ! day_file "$sb" 2026-09-02 | grep -q '"sid":"s3"'; then
    bad "$t" "s3 не доехала вместе с очередью"
  elif [[ -e "$sb/queue.jsonl" ]]; then
    bad "$t" "очередь не опустела"
  else ok "$t"; fi
fi

# --- E. чужой файл на ветке цел ---------------------------------------------------
t="посторонний файл ветки metrics переживает коммит сводки"
sb="$(sandbox)"
G clone --quiet "$sb/origin.git" "$sb/other" 2>/dev/null
G -C "$sb/other" checkout --quiet --orphan metrics
G -C "$sb/other" rm -rfq --cached . 2>/dev/null; rm -f "$sb/other/file.txt"
echo "заметка" > "$sb/other/README.md"
G -C "$sb/other" add -A && G -C "$sb/other" commit --quiet -m "metrics: README"
G -C "$sb/other" push --quiet origin metrics
run_hook "$sb" s1 2026-09-02T10:00:00Z 1 >/dev/null
if ! G -C "$sb/origin.git" show metrics:README.md 2>/dev/null | grep -q "заметка"; then
  bad "$t" "README.md с ветки пропал"
elif ! day_file "$sb" 2026-09-02 | grep -q '"sid":"s1"'; then
  bad "$t" "сводка не легла поверх существующей ветки"
elif [[ "$(G -C "$sb/origin.git" rev-list --count metrics)" != "2" ]]; then
  bad "$t" "коммит сводки не стал потомком существующей ветки"
else ok "$t"; fi

# --- F. выключатель ---------------------------------------------------------------
t="METRICS_STORE=off — ничего не уезжает"
sb="$(sandbox)"
printf '{"ts":"2026-09-02T10:00:00Z","sid":"s1","ended_at":"2026-09-02T10:00:00Z","turns":1}\n' > "$sb/log.s1.summary.json"
printf '{"hook_event_name":"Stop","session_id":"s1"}' \
  | env CRAFT_METRICS_LOG="$sb/log.s1" METRICS_STORE_TARGET="$sb/work" METRICS_STORE_QUEUE="$sb/queue.jsonl" \
        METRICS_STORE_INLINE=1 METRICS_STORE=off HOOK_ONCE=off node "$HOOK" >/dev/null 2>&1
if G -C "$sb/origin.git" rev-parse --verify --quiet refs/heads/metrics >/dev/null; then
  bad "$t" "ветка появилась при выключателе"
else ok "$t"; fi

# --- G. лок общий: очередь не теряется и не бросается ------------------------
# Прежний свой замок бросал выгрузку исходом locked, и сводка, дописанная во
# время чужой выгрузки, оставалась несданной. Общий лок ждёт, а брошенный
# упавшим процессом снимает по возрасту — проверяется тем, что выгрузка
# проходит при заведомо протухшем локе.
t="протухший лок не запирает очередь навсегда"
sb="$(sandbox)"
mkdir -p "$sb/queue.jsonl.lock"
node -e 'const fs=require("fs");const p=process.argv[1];const t=new Date(Date.now()-6*60*1000);fs.utimesSync(p,t,t);' "$sb/queue.jsonl.lock"
out="$(run_hook "$sb" s1 2026-09-02T10:00:00Z 1)"
if ! day_file "$sb" 2026-09-02 | grep -q '"sid":"s1"'; then
  bad "$t" "сводка не доехала при протухшем локе: $out"
elif [[ -e "$sb/queue.jsonl" ]]; then
  bad "$t" "очередь не опустела"
else ok "$t"; fi

# --- H. очередь держит по одной строке на сессию -----------------------------
t="очередь сжимается по сессиям, а не растёт строкой на ход"
sb="$(sandbox)"
G -C "$sb/work" remote set-url origin "$sb/nowhere.git"
for n in 1 2 3 4 5; do run_hook "$sb" s1 2026-09-02T10:0${n}:00Z "$n" >/dev/null; done
run_hook "$sb" s2 2026-09-02T10:06:00Z 1 >/dev/null
lines="$(wc -l < "$sb/queue.jsonl" | tr -d ' ')"
if [[ "$lines" != "2" ]]; then
  bad "$t" "пять Stop одной сессии дали $lines строк, ожидалось 2 (s1 и s2)"
elif ! grep -q '"turns":5' "$sb/queue.jsonl"; then
  bad "$t" "в очереди осталась не последняя сводка сессии"
else ok "$t"; fi

# --- I. день по началу сессии ------------------------------------------------
t="сессия через полночь UTC остаётся одной строкой одного дня"
sb="$(sandbox)"
printf '{"ts":"%s","sid":"s1","started_at":"2026-09-02T23:50:00Z","ended_at":"%s","turns":%s,"repo":"x"}\n' \
  2026-09-03T00:10:00Z 2026-09-03T00:10:00Z 6 > "$sb/log.s1.summary.json"
printf '{"hook_event_name":"Stop","session_id":"s1"}' \
  | env CRAFT_METRICS_LOG="$sb/log.s1" METRICS_STORE_TARGET="$sb/work" METRICS_STORE_QUEUE="$sb/queue.jsonl" \
        METRICS_STORE_INLINE=1 HOOK_ONCE=off node "$HOOK" >/dev/null 2>&1
if ! day_file "$sb" 2026-09-02 | grep -q '"sid":"s1"'; then
  bad "$t" "строка не легла в день НАЧАЛА сессии"
elif day_file "$sb" 2026-09-03 | grep -q '"sid":"s1"'; then
  bad "$t" "сессия посчиталась дважды: строка есть и в дне окончания"
else ok "$t"; fi

# --- J. выгрузка идёт на каждом Stop -----------------------------------------
# Троттлинга нет: отложенная сводка это сводка, которой может не стать вовсе —
# в облаке чекаут одноразовый и умирает вместе с непроехавшей очередью.
t="каждая сводка уезжает своим Stop, без ожидания интервала"
sb="$(sandbox)"
run_hook "$sb" s1 2026-09-02T10:00:00Z 1 >/dev/null
out="$(run_hook "$sb" s2 2026-09-02T10:01:00Z 1)"
if ! day_file "$sb" 2026-09-02 | grep -q '"sid":"s2"'; then
  bad "$t" "вторая сводка не уехала своим Stop: $out"
elif [[ -e "$sb/queue.jsonl" ]]; then
  bad "$t" "очередь не опустела"
else ok "$t"; fi

# --- K. исход доставки виден в журнале ---------------------------------------
# Работник отсоединён, его вывод никто не читает: без строки в журнале провал
# доставки в бою неотличим от того, что доставки не было.
t="исход доставки ложится в журнал строкой kind: store"
sb="$(sandbox)"
G -C "$sb/work" remote set-url origin "$sb/nowhere.git"
# Работник запускается КАК В БОЮ: без CRAFT_METRICS_LOG, только с путём к
# сводке. Журнал он выводит из этого пути; кейс, задающий переменную руками,
# зеленел бы и тогда, когда в живой сессии исход не пишется никуда.
printf '{"ts":"%s","sid":"s1","started_at":"%s","ended_at":"%s","turns":1,"repo":"x"}\n' \
  2026-09-02T10:00:00Z 2026-09-02T10:00:00Z 2026-09-02T10:00:00Z > "$sb/log.s1.summary.json"
out="$(env -u CRAFT_METRICS_LOG METRICS_STORE_WORKER=1 \
        METRICS_STORE_SUMMARY="$sb/log.s1.summary.json" \
        METRICS_STORE_TARGET="$sb/work" METRICS_STORE_QUEUE="$sb/queue.jsonl" \
        HOOK_ONCE=off node "$HOOK" 2>&1)"
line="$(grep '"kind":"store"' "$sb/log.s1" 2>/dev/null | tail -1)"
if [[ -z "$line" ]]; then
  bad "$t" "строки kind: store в журнале нет: $out"
elif ! grep -q '"status":"offline"' <<<"$line"; then
  bad "$t" "в строке журнала не тот исход: $line"
elif ! grep -q '"sid":"s1"' "$sb/queue.jsonl"; then
  bad "$t" "очередь не сохранила сводку"
else ok "$t"; fi

# --- L. занятый лок очереди не теряет сводку ---------------------------------
# Постановку в очередь делает отсоединённый работник, и его никто не ждёт:
# предыдущая выгрузка держит лок всё время сети, а короткий срок ожидания
# означал бы, что сводка последнего Stop сессии не стала durable вовсе —
# следующего Stop, который положил бы её заново, у сессии уже не будет.
t="сводка встаёт в очередь, даже когда лок занят соседней выгрузкой"
sb="$(sandbox)"
G -C "$sb/work" remote set-url origin "$sb/nowhere.git"   # без сети: очередь остаётся
printf '{"ts":"%s","sid":"s1","started_at":"%s","ended_at":"%s","turns":1,"repo":"x"}\n' \
  2026-09-02T10:00:00Z 2026-09-02T10:00:00Z 2026-09-02T10:00:00Z > "$sb/log.s1.summary.json"
mkdir -p "$sb/queue.jsonl.lock"
sleep 30 & holder=$!
printf '%s' "$holder" > "$sb/queue.jsonl.lock/owner"
( sleep 2; rm -rf "$sb/queue.jsonl.lock" ) &
# Ждать долго имеет право только ОТСОЕДИНЁННЫЙ работник, и гоняется здесь именно
# он, как в бою: инлайн-режим идёт в процессе хука и ждёт срок хода (кейс M).
out="$(env -u CRAFT_METRICS_LOG METRICS_STORE_WORKER=1 \
        METRICS_STORE_SUMMARY="$sb/log.s1.summary.json" \
        METRICS_STORE_TARGET="$sb/work" METRICS_STORE_QUEUE="$sb/queue.jsonl" \
        HOOK_ONCE=off node "$HOOK" 2>&1)"
kill "$holder" 2>/dev/null
if ! grep -q '"sid":"s1"' "$sb/queue.jsonl" 2>/dev/null; then
  bad "$t" "сводка не встала в очередь после освобождения лока: $out"
else ok "$t"; fi

# --- M. инлайн-режим не ждёт лок сроком работника ----------------------------
# Пять минут ожидания лока имеет право ждать только ОТСОЕДИНЁННЫЙ работник:
# инлайн-режим идёт в процессе хука, и зашитый срок работника превращал бы
# занятый лок очереди в пятиминутную паузу на конце хода.
t="инлайн-режим возвращается со срока хода, а не со срока работника"
sb="$(sandbox)"
G -C "$sb/work" remote set-url origin "$sb/nowhere.git"   # без сети: доставки не будет
mkdir -p "$sb/queue.jsonl.lock"
sleep 120 & holder=$!
printf '%s' "$holder" > "$sb/queue.jsonl.lock/owner"      # лок занят живым процессом и не отпускается
started=$(date +%s%3N)
out="$(timeout 90 bash -c 'run_hook "$@"' _ "$sb" s1 2026-09-02T10:00:00Z 1 2>&1)" || out="$out[timeout]"
spent=$(( $(date +%s%3N) - started ))
kill "$holder" 2>/dev/null
rm -rf "$sb/queue.jsonl.lock"
if [[ "$out" == *"[timeout]"* ]]; then
  bad "$t" "хук не вернулся за 90 с: ждал лок сроком работника"
elif (( spent > 2500 )); then
  bad "$t" "хук вернулся за ${spent} мс — это не срок хода: и постановка, и выгрузка обязаны уложиться в сотни миллисекунд"
elif ! grep -q '"kind":"store"' "$sb/log.s1"; then
  bad "$t" "исход не назван строкой журнала: $out"
else ok "$t"; fi

printf -- '---\n%d passed, %d failed\n' "$pass" "$fail"
for f in "${fails[@]:-}"; do [[ -n "$f" ]] && printf '  - %s\n' "$f"; done
[[ "$fail" -eq 0 ]]
