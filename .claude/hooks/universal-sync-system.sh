#!/usr/bin/env bash
# Доставка правок системы в ЖИВУЮ сессию. Регистрируется на два события:
#
#   Stop             — конец хода: опрос сети (fetch + сборка свежего снимка
#                      правил Craft) и отчёт на диск. Здесь Влада никто не
#                      заставляет ждать, поэтому сеть живёт именно тут.
#   UserPromptSubmit — отправка сообщения: применение готового отчёта. Только
#                      локальная работа — git-операции по уже скачанному и
#                      печать в контекст. Сети на этом пути нет никогда.
#
# Зачем вообще: чекаут сессии режется один раз на старте, снимки правил Craft
# строятся хуками старта. Без этого хука живая сессия работает на коде и
# правилах момента своего рождения, а Владу приходится просить «подтяни main» —
# агенту при этом неоткуда знать, что он отстал.
#
# Цель синка — репа, в которой лежит САМ файл хука (симлинков больше нет, так
# что это всегда настоящий чекаут). Два случая:
#   свой чекаут   — сессия запущена в этой же репе: вливаем main в её ветку,
#                   но только при чистом рабочем дереве; конфликт откатываем.
#   общий чекаут  — репа лишь подключена сессии рабочей директорией: там может
#                   стоять ветка Влада, поэтому вливаем, только если чекаут на
#                   main и чист, иначе двигаем ОДИН указатель main.
#
# Молчит ровно в одном случае — отставания нет. Отказ проверки (нет сети,
# позиция чекаута неопределённая) печатает строку один раз за сессию: молчание
# не должно выглядеть как «всё свежее».
#
# Fail quiet на всём неожиданном: сломанный синк не должен задерживать
# сообщение Влада.
set -u
export LC_ALL=C.UTF-8

[[ "${SYNC_SYSTEM:-}" == "off" ]] && exit 0
# Автономный прогон (рутина) не мутирует систему под собой на середине, евал —
# не портит кейсы своим выводом.
[[ -n "${CRAFT_AUTONOMOUS:-}" ]] && exit 0
[[ -n "${CRAFT_EVAL:-}" ]] && exit 0

SELF="$(realpath "$0" 2>/dev/null || echo "$0")"
DIR="$(cd "$(dirname "$SELF")" && pwd)"

input="$(cat)"
event="$(jq -r '.hook_event_name // ""' <<<"$input" 2>/dev/null)" || exit 0
sid="$(jq -r '.session_id // "default"' <<<"$input" 2>/dev/null)" || exit 0

# Хук зарегистрирован и project-level, и пользовательски — уступаем второму вызову.
# shellcheck disable=SC1091
. "$DIR/_hook-once.sh" 2>/dev/null || true
if declare -F hook_once >/dev/null 2>&1; then
  hook_once "$input" || exit 0
fi

TARGET="${SYNC_SYSTEM_TARGET:-$(cd "$DIR/../.." && pwd)}"
STATE="${SYNC_SYSTEM_STATE:-${TMPDIR:-/tmp}/sync-system.$sid}"
INTERVAL="${SYNC_SYSTEM_INTERVAL:-900}"
BUDGET="${SYNC_SYSTEM_DELTA_BUDGET:-5000}"

REPORT="$STATE.report"
LOCK="$STATE.lock"
STAMP="$STATE.stamp"
NOTIFIED="$STATE.notified"
BASE="$STATE.rules-base"
FRESH="$STATE.rules-fresh"

g() { git -C "$TARGET" "$@" 2>/dev/null; }

# ---------------------------------------------------------------- сетевой шаг

# Свежий снимок правил Craft — существующим инжектором, нацеленным на свой файл
# (у него для этого своя переменная). Свою копию логики загрузки не заводим.
build_rules_snapshot() {
  local injector="$DIR/craft-inject-router.sh"
  [[ -x "$injector" || -f "$injector" ]] || return 1
  CRAFT_ROUTER_SNAPSHOT="$FRESH.tmp" bash "$injector" >/dev/null 2>&1
  if [[ -s "$FRESH.tmp" ]]; then
    mv -f "$FRESH.tmp" "$FRESH" 2>/dev/null && return 0
  fi
  rm -f "$FRESH.tmp" 2>/dev/null
  return 1
}

probe() {
  local now last
  now="$(date +%s 2>/dev/null || echo 0)"
  last="$(cat "$STAMP" 2>/dev/null || echo 0)"
  [[ "$last" =~ ^[0-9]+$ ]] || last=0
  if [[ "$INTERVAL" =~ ^[0-9]+$ ]] && (( INTERVAL > 0 )) && (( now - last < INTERVAL )); then
    return 0
  fi

  # Замок: один сетевой прогон за раз, иначе два fetch дерутся за .git/index.lock.
  mkdir "$LOCK" 2>/dev/null || return 0
  trap 'rm -rf "$LOCK" 2>/dev/null' EXIT

  echo "$now" > "$STAMP" 2>/dev/null

  local report="$REPORT.tmp" head_before ahead branch scope
  : > "$report" 2>/dev/null || return 0

  head_before="$(g rev-parse HEAD)"
  branch="$(g rev-parse --abbrev-ref HEAD)"
  if [[ -z "$head_before" || -z "$branch" || "$branch" == "HEAD" ]]; then
    printf 'probe_error=%s\n' "позиция чекаута неопределённая" >> "$report"
    mv -f "$report" "$REPORT" 2>/dev/null
    return 0
  fi

  if ! g fetch --quiet origin main; then
    printf 'probe_error=%s\n' "нет сети" >> "$report"
    mv -f "$report" "$REPORT" 2>/dev/null
    return 0
  fi

  # Клон в облаке обрезанный: без общего предка вливание не соберётся.
  if [[ "$(g rev-parse --is-shallow-repository)" == "true" ]]; then
    g merge-base HEAD origin/main >/dev/null || g fetch --quiet --deepen=200 origin main
  fi

  ahead="$(g rev-list --count HEAD..origin/main)"
  [[ "$ahead" =~ ^[0-9]+$ ]] || ahead=0

  scope=own
  local proj; proj="$(cd "${CLAUDE_PROJECT_DIR:-/nonexistent}" 2>/dev/null && pwd)"
  [[ "$proj" != "$(cd "$TARGET" 2>/dev/null && pwd)" ]] && scope=shared

  {
    printf 'ahead=%s\n' "$ahead"
    printf 'scope=%s\n' "$scope"
    printf 'branch=%s\n' "$branch"
    printf 'head_before=%s\n' "$head_before"
  } >> "$report"

  build_rules_snapshot && printf 'rules=%s\n' "$FRESH" >> "$report"

  mv -f "$report" "$REPORT" 2>/dev/null
  return 0
}

# ------------------------------------------------------------ применение

dirty() { [[ -n "$(g status --porcelain)" ]]; }

# Раскладывает подтянутые файлы по тому, доедут ли они в живую сессию сами.
changed_report() {  # $1 — head до подтягивания
  local files live watched cold other f
  files="$(g diff --name-only "$1" HEAD)"
  live=(); watched=(); cold=(); other=()
  while IFS= read -r f; do
    [[ -z "$f" ]] && continue
    case "$f" in
      .claude/hooks/*|.claude/skills/*|.claude/agents/*|.claude/commands/*) live+=("$f") ;;
      .claude/settings.json)                                                watched+=("$f") ;;
      CLAUDE.md|.claude/rules/*|.claude/CLAUDE.md)                          cold+=("$f") ;;
      *)                                                                    other+=("$f") ;;
    esac
  done <<<"$files"

  local msg=""
  (( ${#live[@]} )) && msg+=" Уже действуют (читаются с диска при срабатывании): ${live[*]}."
  (( ${#watched[@]} )) && msg+=" Изменилась регистрация хуков — её подхватывает файл-вотчер."
  (( ${#cold[@]} )) && msg+=" НЕ доехало в контекст: ${cold[*]} — файл на диске новый, контекст старый, перечитай его, прежде чем на него опираться."
  (( ${#other[@]} )) && msg+=" Прочее: ${other[*]}."
  printf '%s' "$msg"
}

# Пересборка бинарника craft-sync, если приехали его исходники: иначе исходник
# новый, а инструмент на PATH старый.
maybe_rebuild_sync() {  # $1 — head до подтягивания
  g diff --name-only "$1" HEAD | grep -q '^craft-sync/' || return 0
  [[ -f "$TARGET/.claude/hooks/craft-build-sync.sh" ]] || return 0
  CRAFT_SYNC_BUILD=1 bash "$TARGET/.claude/hooks/craft-build-sync.sh" >/dev/null 2>&1 || true
}

# Регистрация хуков живёт в пользовательских настройках: без переустановки новый
# хук приезжает файлом, о котором никто не знает.
maybe_reinstall() {
  [[ -f "$TARGET/install.sh" ]] || return 0
  bash "$TARGET/install.sh" >/dev/null 2>&1 || true
}

apply_code() {  # печатает директиву; $1 ahead, $2 scope, $3 branch, $4 head_before
  local ahead="$1" scope="$2" branch="$3" head_before="$4" reason="" ok=0

  if [[ "$scope" == own ]]; then
    if dirty; then
      reason="в рабочем дереве $TARGET несохранённые изменения"
    elif g merge --no-edit origin/main >/dev/null; then
      ok=1
    else
      g merge --abort >/dev/null
      reason="вливание упёрлось в конфликт и откачено"
    fi
  else
    if [[ "$branch" != "main" ]]; then
      # Рабочее дерево общего чекаута не трогаем вовсе — двигаем один указатель.
      g fetch --quiet origin main:main
      reason="$TARGET стоит не на main"
    elif dirty; then
      reason="в рабочем дереве $TARGET несохранённые изменения"
    elif g merge --ff-only origin/main >/dev/null; then
      ok=1
    else
      reason="перемотка $TARGET не удалась"
    fi
  fi

  if (( ok )); then
    maybe_rebuild_sync "$head_before"
    [[ "$scope" == shared ]] && maybe_reinstall
    printf '🔄 Система обновлена: подтянуто %s коммитов main в %s.%s\n' \
      "$ahead" "$TARGET" "$(changed_report "$head_before")"
  else
    printf '🔄 Система ушла вперёд на %s коммитов main, подтянуть нельзя: %s. Сессия работает на устаревших правилах и хуках. Разберись с этим чекаутом и подтяни main, прежде чем опираться на системные правила.\n' \
      "$ahead" "$reason"
  fi
}

# Зона правил снимка: без служебного заголовка со временем сборки (иначе отличие
# всегда) и без регенерируемой памяти (её переписывают рутины актуализации).
rules_zone() {  # $1 — файл снимка
  awk '
    /^=== Craft: роутер/ { next }
    /<pageTitle>🧠 Память \(регенерируемая\)<\/pageTitle>/ { stop = 1 }
    stop { next }
    { print }
  ' "$1" 2>/dev/null
}

apply_rules() {  # $1 — путь свежего снимка
  local fresh="$1" a b delta
  [[ -s "$fresh" ]] || return 0

  # Базы нет — засеиваем и молчим: обрезок всего роутера вместо дельты бесполезен.
  if [[ ! -s "$BASE" ]]; then
    cp -f "$fresh" "$BASE" 2>/dev/null
    return 0
  fi

  a="$(mktemp)"; b="$(mktemp)"
  rules_zone "$BASE" > "$a"
  rules_zone "$fresh" > "$b"
  if diff -q "$a" "$b" >/dev/null 2>&1; then
    rm -f "$a" "$b"
    cp -f "$fresh" "$BASE" 2>/dev/null
    return 0
  fi

  delta="$(diff -u "$a" "$b" 2>/dev/null | tail -n +3 | head -c "$BUDGET")"
  rm -f "$a" "$b"
  cp -f "$fresh" "$BASE" 2>/dev/null

  # Публичный снимок, который импортирует CLAUDE.md, подменяем атомарно: его
  # читает и импорт при компакте, и детектор инцидентов на том же событии.
  local public="$TARGET/.claude/craft-router-context.md"
  if [[ -d "$TARGET/.claude" ]]; then
    cp -f "$fresh" "$public.tmp" 2>/dev/null && mv -f "$public.tmp" "$public" 2>/dev/null
  fi

  printf '🧠 Правила Craft изменились с начала сессии. Дельта ниже; она обрезана по бюджету, полный текст — в снимке правил на диске: он уже обновлён, но сам в контекст не вернётся. Дальше действуй по свежей версии правила, а не по той, что в контексте выше.\n%s\n' "$delta"
}

apply() {
  [[ -s "$REPORT" ]] || return 0

  # Забираем отчёт атомарно: второй вызов (двойная регистрация, повторное
  # событие) уже ничего не найдёт и промолчит.
  local taken="$REPORT.taken"
  mv -f "$REPORT" "$taken" 2>/dev/null || return 0

  local ahead scope branch head_before rules probe_error
  ahead="$(sed -n 's/^ahead=//p' "$taken" | head -1)"
  scope="$(sed -n 's/^scope=//p' "$taken" | head -1)"
  branch="$(sed -n 's/^branch=//p' "$taken" | head -1)"
  head_before="$(sed -n 's/^head_before=//p' "$taken" | head -1)"
  rules="$(sed -n 's/^rules=//p' "$taken" | head -1)"
  probe_error="$(sed -n 's/^probe_error=//p' "$taken" | head -1)"
  rm -f "$taken" 2>/dev/null

  if [[ -n "$probe_error" ]]; then
    # Один раз за сессию: повторять на каждом сообщении незачем, а промолчать нельзя.
    if [[ ! -e "$NOTIFIED" ]]; then
      : > "$NOTIFIED" 2>/dev/null
      printf '🔄 Свежесть системы проверить не удалось: %s. Считай, что сессия может работать на устаревших правилах.\n' "$probe_error"
    fi
    return 0
  fi

  [[ "$ahead" =~ ^[0-9]+$ ]] || ahead=0
  (( ahead > 0 )) && apply_code "$ahead" "${scope:-own}" "${branch:-main}" "$head_before"

  [[ -n "$rules" ]] && apply_rules "$rules"
  return 0
}

case "$event" in
  Stop|SubagentStop)
    if [[ -n "${SYNC_SYSTEM_WORKER_INLINE:-}" ]]; then
      probe
    else
      # Ход уже закончен, ждать сеть некому — уходим в фон.
      ( probe >/dev/null 2>&1 & ) >/dev/null 2>&1
    fi
    ;;
  UserPromptSubmit)
    apply
    ;;
esac
exit 0
