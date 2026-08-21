#!/usr/bin/env bash
# SessionStart hook (устанавливается в ~/.claude): живой инжект ЯДРА «Правил
# кода» из Craft в код-сессии вне craft-репо. Канон — Craft; никакого
# коммитнутого кэша.
#
# Инжектится ТОЛЬКО корень дока (maxDepth=1): ядро + диспетчер доменных
# страниц. Полные доменные страницы (TypeScript, React, …) агент читает
# целиком по триггеру домена — так велит сам диспетчер. Сжимать их в инжект
# нельзя (урок в «Обслуживании памяти»).
#
# В craft-репо ядро по умолчанию не инжектится: craft-сессии код не пишут. Но
# сессия, запущенная В craft-репо со ВТОРЫМ рабочим корнем снаружи (craft-local
# первой директорией ради воркtree, маунт кода второй), — как раз код-сессия, и
# без этого исключения она оставалась без правил кода вовсе: ни лимитов, ни
# адресации файла ссылкой. Постоянная запись в permissions под признак не
# годится — она стоит у всех сессий подряд и ничего не различает; корень,
# заданный при ЗАПУСКЕ сессии, различает.
#
# Fail quiet: нет env/сети → короткая директива-фолбек.
set -u

# Корни, заданные при запуске сессии: аргументы --add-dir у процесса-предка.
# Путь с пробелом здесь не разберётся и просто не будет учтён — тогда сессия
# останется без ядра, как и раньше, а не получит мусор.
code_rules_extra_dirs() {
  [[ -n "${CODE_RULES_EXTRA_DIRS+x}" ]] && { printf '%s\n' ${CODE_RULES_EXTRA_DIRS}; return; }
  local p=$$ ppid args
  for _ in 1 2 3 4 5 6; do
    ppid="$(ps -o ppid= -p "$p" 2>/dev/null | tr -d ' ')"
    [[ -z "$ppid" || "$ppid" == "0" ]] && break
    args="$(ps -o args= -p "$ppid" 2>/dev/null)"
    if [[ "$args" == *--add-dir* ]]; then
      tr ' ' '\n' <<<"$args" | awk '/^--add-dir$/{getline; print}'
      return
    fi
    p="$ppid"
  done
}

# Сравнение путей — по нормализованному виду: «..» и хвостовая косая в сыром
# аргументе не дают префиксу совпасть, и директория ВНУТРИ репо сошла бы за
# второй корень.
code_rules_abs() { (cd "$1" 2>/dev/null && pwd) || printf '%s' "$1"; }

if [[ -n "${CLAUDE_PROJECT_DIR:-}" \
      && -e "$CLAUDE_PROJECT_DIR/.claude/hooks/craft-inject-router.sh" ]]; then
  code_project="$(code_rules_abs "$CLAUDE_PROJECT_DIR")"
  code_second_root=0
  while IFS= read -r d; do
    [[ -z "$d" ]] && continue
    d="$(code_rules_abs "$d")"
    [[ "$d" == "$code_project" || "$d" == "$code_project"/* ]] && continue
    code_second_root=1
    break
  done < <(code_rules_extra_dirs)
  (( code_second_root )) || exit 0
fi

CODE_RULES_ID="${CRAFT_CODE_RULES_ID:-d3f184fb-2c70-6058-0797-d9851f4b16a7}"
CLAUDE_MD="${CRAFT_USER_CLAUDE_MD:-$HOME/.claude/CLAUDE.md}"
SNAPSHOT="${CRAFT_CODE_RULES_SNAPSHOT:-$HOME/.claude/craft-live/code-rules.md}"
# Инвариант Craft: страница свыше 50 прямых блоков читается с пагинацией, и
# правило из хвоста молча не доезжает. Порог держим тут, счёт — по json-ответу.
BLOCK_LIMIT="${CRAFT_BLOCK_LIMIT:-50}"
# Печать тела остаётся аварийным путём, и её потолок прежний: stdout капится.
BUDGET=9500

# Канал жив, только пока импорт снимка стоит в личном CLAUDE.md: без него файл
# никто не прочитает, и тело обязано идти печатью.
channel_ready() {
  [[ -r "$CLAUDE_MD" ]] && grep -qF "$SNAPSHOT" "$CLAUDE_MD" 2>/dev/null
}

fallback() {
  echo "⚠️ Ядро «Правил кода» не загружено из Craft ($1). Перед правками кода прочитай его живьём: Craft MCP blocks get $CODE_RULES_ID --depth 1 (ядро + диспетчер доменных страниц; страницу своего домена читай целиком). Пока не прочитал, держи минимум: файл в чате и плане адресуется АБСОЛЮТНЫМ путём — относительный резолвится не от того места, где его читают, и не откроется."
  exit 0
}

# deliver <текст> — доставка тела правил. Канал установлен (импорт снимка стоит в
# личном CLAUDE.md) — тело уходит в файл, в stdout остаётся строка-отчёт; иначе
# печатаем по-старому, с прежним потолком stdout. Перезапись атомарная: снимок
# общий для всех сессий, и соседняя читает либо прежнюю версию, либо новую, но
# не половину и не пустоту. Снимок не сносится ни на одном пути — вчерашний
# текст честнее пустоты, а его возраст виден по метке времени внутри.
deliver() {
  local body="$1"
  if [[ -n "${CODE_RULES_TEST_SNAPSHOT:-}" ]] || channel_ready; then
    mkdir -p "$(dirname "$SNAPSHOT")" 2>/dev/null || true
    if printf '%s\n' "$body" > "${SNAPSHOT}.tmp" 2>/dev/null \
       && mv -f "${SNAPSHOT}.tmp" "$SNAPSHOT" 2>/dev/null; then
      echo "Ядро «Правил кода» обновлено из Craft ($(wc -c < "$SNAPSHOT") байт, $stamp) — полный текст в контексте через импорт снимка, обрезки нет.${BLOCK_WARN:-}"
      return 0
    fi
    rm -f "${SNAPSHOT}.tmp" 2>/dev/null
  fi
  if [[ ${#body} -gt $BUDGET ]]; then
    body="${body:0:$BUDGET}
…[обрезано бюджетом — канал импорта не установлен, поставь его прогоном install.sh; дочитай ядро живьём: blocks get $CODE_RULES_ID --depth 1]"
  fi
  printf '%s\n' "$body"
  printf '%s\n' "${BLOCK_WARN:-}"
}

# Тестовый шов стоит ДО загрузки .env: прогон кейсов идёт без сети и без живого
# Craft, а проверяемое — само условие «инжектить или молчать» и текст фолбека, не
# тело правил. Задать пустой CRAFT_API_BASE снаружи для этого нельзя: .env
# грузится с set -a и перекрывает переданное окружение.
if [[ -n "${CODE_RULES_TEST_MD:-}" ]]; then
  [[ -r "$CODE_RULES_TEST_MD" ]] || fallback "тестовый шов без источника"
  md="$(cat "$CODE_RULES_TEST_MD" 2>/dev/null)"
  stamp="тестовый инжект"
  # Порог инварианта проверяется и в шве: живой счёт идёт по json из сети,
  # которой в прогоне кейсов нет, поэтому число подаётся напрямую. Сам порог и
  # текст сигнала — те же, что на живом пути ниже.
  if [[ "${CODE_RULES_TEST_BLOCKS:-}" =~ ^[0-9]+$ ]] \
     && (( CODE_RULES_TEST_BLOCKS > BLOCK_LIMIT )); then
    BLOCK_WARN=" ⚠️ Ядро переросло инвариант: $CODE_RULES_TEST_BLOCKS прямых блоков при потолке $BLOCK_LIMIT — страница читается с пагинацией, и правила из хвоста молча не доезжают. Дробление — по чек-листу гигиены."
  fi
  out="=== Craft: «⚙️ Правила кода» — ядро, тестовый инжект ===
$md"
  deliver "$out"
  exit 0
fi

self="$(realpath "$0" 2>/dev/null || echo "$0")"
# shellcheck disable=SC1091
. "$(dirname "$self")/_load-env.sh" 2>/dev/null || true

base="${CRAFT_API_BASE:-}"
[[ -z "$base" ]] && fallback "CRAFT_API_BASE не задан"
base="${base%/}"

md="$(curl -sS --fail --max-time 30 --retry 2 --retry-all-errors \
  -H 'Accept: text/markdown' \
  "$base/blocks?id=$CODE_RULES_ID&maxDepth=1" 2>/dev/null)" || fallback "сеть/API недоступны"
[[ -z "$md" ]] && fallback "пустой ответ API"

# Счёт прямых блоков — по json того же запроса: в markdown границ блока не видно,
# абзац, пункт списка и callout там неразличимы. Отдельного сетевого вызова не
# добавляется — тот же адрес, другой формат ответа; сбой счёта молчит, это
# сигнал гигиены, а не условие доставки.
blocks_json="$(curl -sS --fail --max-time 30 \
  -H 'Accept: application/json' \
  "$base/blocks?id=$CODE_RULES_ID&maxDepth=1" 2>/dev/null)" || blocks_json=""
if [[ -n "$blocks_json" ]]; then
  direct="$(jq -r '(.content // []) | length' <<<"$blocks_json" 2>/dev/null)" || direct=""
  if [[ "$direct" =~ ^[0-9]+$ ]] && (( direct > BLOCK_LIMIT )); then
    BLOCK_WARN=" ⚠️ Ядро переросло инвариант: $direct прямых блоков при потолке $BLOCK_LIMIT — страница читается с пагинацией, и правила из хвоста молча не доезжают. Дробление — по чек-листу гигиены."
  fi
fi

stamp="$(date -u +%FT%TZ)"
out="=== Craft: «⚙️ Правила кода» — ядро, живой инжект ($stamp) ===
$md
=== конец ядра. Работаешь с доменом — прочитай его страницу ЦЕЛИКОМ (Craft MCP, blocks get по ссылке из диспетчера, --depth -1) до первых правок кода ==="

deliver "$out"
exit 0
