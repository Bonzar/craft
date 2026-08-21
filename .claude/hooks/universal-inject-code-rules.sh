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
BUDGET=9500

fallback() {
  echo "⚠️ Ядро «Правил кода» не загружено из Craft ($1). Перед правками кода прочитай его живьём: Craft MCP blocks get $CODE_RULES_ID --depth 1 (ядро + диспетчер доменных страниц; страницу своего домена читай целиком). Пока не прочитал, держи минимум: файл в чате и плане адресуется АБСОЛЮТНЫМ путём — относительный резолвится не от того места, где его читают, и не откроется."
  exit 0
}

# Тестовый шов стоит ДО загрузки .env: прогон кейсов идёт без сети и без живого
# Craft, а проверяемое — само условие «инжектить или молчать» и текст фолбека, не
# тело правил. Задать пустой CRAFT_API_BASE снаружи для этого нельзя: .env
# грузится с set -a и перекрывает переданное окружение.
if [[ -n "${CODE_RULES_TEST_MD:-}" ]]; then
  [[ -r "$CODE_RULES_TEST_MD" ]] || fallback "тестовый шов без источника"
  echo "=== Craft: «⚙️ Правила кода» — ядро, тестовый инжект ==="
  cat "$CODE_RULES_TEST_MD" 2>/dev/null
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

out="=== Craft: «⚙️ Правила кода» — ядро, живой инжект ($(date -u +%FT%TZ)) ===
$md
=== конец ядра. Работаешь с доменом — прочитай его страницу ЦЕЛИКОМ (Craft MCP, blocks get по ссылке из диспетчера, --depth -1) до первых правок кода ==="

if [[ ${#out} -gt $BUDGET ]]; then
  out="${out:0:$BUDGET}
…[обрезано бюджетом — дочитай ядро живьём: blocks get $CODE_RULES_ID --depth 1]"
fi

printf '%s\n' "$out"
exit 0
