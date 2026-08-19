#!/usr/bin/env bash
# PreToolUse plan-gate: рабочие правки — код, система, Craft — по умолчанию
# закрыты; открывает их ПЕРИМЕТР одобренного плана, а не факт одобрения.
#
# Маркер (universal-plan-gate-approve.sh) — список целей из строк «- где:»
# одобренного плана: файловые пути и block-ID Craft. Гейт открывает только
# совпадение с целью; одобрения складываются, реплики Влада периметр не гасят
# (гасят реплика «закрой гейт» — plan-gate-reset — и смена сессии).
#
# Поверхности:
#   - Write|Edit|MultiEdit|NotebookEdit — правки файлов где угодно, кроме
#     эфемерного (планы, tmp/scratchpad, служебное ~/.claude) и игнорируемого
#     гитом ВНЕ .claude/ — внутри .claude/ живут игнорируемые, но системные
#     файлы (settings.local.json, кэш предодобренной зоны), их игнор-лазейка
#     открывала бы без плана;
#   - Bash — разбор команды на цели записи: перенаправление, tee, sed/perl -i,
#     cp/mv, запись из интерпретатора. Команда со смешанными целями проходит
#     только когда ВСЕ цели в периметре;
#   - craft_write — каждый UUID команды обязан быть в периметре; отдельно и
#     раньше периметра — предодобренная зона (exempt-scope, напр. «Продукты»).
#
# CRAFT_AUTONOMOUS=1 обходит гейт целиком — рутины и headless-евалы
# предавторизованы, интерактивного Влада там нет.
#
# Защита от протечки привязана к источнику пути маркера: путь, выведенный из
# ПУСТОГО session-id (общий default), не читается и не пишется; путь из
# env-переопределения используется всегда — тесты герметичны через него.
#
# Непокрыто и названо честно: неопознанная конструкция записи; пакетные
# менеджеры и операции гита над рабочим деревом (пишут своей логикой, не
# перенаправлением); перенаправление в закавыченную цель — кавычки
# вычёркиваются, чтобы «больше» в сравнении не считалось записью.
#
# Fail open on anything unexpected: a broken gate must never wedge legit work.
set -u

# When this same hook is ALSO installed at user level (~/.claude, via
# install.sh), the project-level registration yields to it — otherwise a craft
# session would run the gate twice per call. Cloud sessions have no user-level
# install, so the project copy stays active there.
if [[ -n "${CLAUDE_PROJECT_DIR:-}" && "$0" == "$CLAUDE_PROJECT_DIR"/* \
      && -e "$HOME/.claude/hooks/$(basename "$0")" ]]; then
  exit 0
fi

[[ -n "${CRAFT_AUTONOMOUS:-}" ]] && exit 0

input="$(cat)"
tool="$(jq -r '.tool_name // ""' <<<"$input" 2>/dev/null)" || exit 0

is_craft_write=0
is_file_edit=0
is_bash=0
# Match craft_write by suffix, not the full qualified name: the Craft MCP
# server's prefix changes on reconnect (mcp__Craft__… one session,
# mcp__<uuid>__… the next) — an exact match silently stops gating the moment
# the ID rotates.
if [[ "$tool" =~ __craft_write$ ]]; then
  is_craft_write=1
else
  case "$tool" in
    Write|Edit|MultiEdit|NotebookEdit) is_file_edit=1 ;;
    Bash) is_bash=1 ;;
    *) exit 0 ;;
  esac
fi

sid="${CLAUDE_CODE_SESSION_ID:-}"
if [[ -n "${CRAFT_PLAN_GATE_MARKER:-}" ]]; then
  marker="$CRAFT_PLAN_GATE_MARKER"
elif [[ -n "$sid" ]]; then
  marker="/tmp/craft-plan-gate.${sid}.approved"
else
  marker=""
fi
scopelist=""
[[ -n "$marker" && -s "$marker" ]] && scopelist="$(cat "$marker" 2>/dev/null)"

# in_scope <цель> — цель входит в периметр: точное имя, вложенность в названный
# каталог, либо сам идентификатор строкой (Craft-UUID).
in_scope() {
  local t="$1" e
  [[ -n "$scopelist" ]] || return 1
  while IFS= read -r e; do
    [[ -z "$e" ]] && continue
    [[ "$t" == "$e" ]] && return 0
    [[ "$t" == "${e%/}"/* ]] && return 0
  done <<<"$scopelist"
  return 1
}

deny() {
  jq -cn --arg r "$1" \
    '{hookSpecificOutput:{hookEventName:"PreToolUse",permissionDecision:"deny",permissionDecisionReason:$r}}'
  exit 0
}

deny_scope() {
  deny "Заблокировано план-гейтом: цель ($1) не входит в одобренный план. Пути дальше: дельта плана с этой целью, кнопка разрешения, режим acceptEdits. Автономному прогону — CRAFT_AUTONOMOUS=1."
}

# is_ephemeral <path> — путь, правка которого системным изменением не является.
# Общий для правки файлов и для Bash-записи: разъехавшиеся списки дали бы поверхность,
# где одно и то же место то гейтится, то нет.
is_ephemeral() {
  local fp="$1" rel
  # Plan files are written by plan-mode BEFORE the marker exists — gating them
  # would deadlock planning itself.
  [[ "$fp" == */plans/*.md ]] && return 0
  case "$fp" in
    /tmp/*|/private/tmp/*|/var/folders/*|/private/var/folders/*|*/scratchpad/*) return 0 ;;
  esac
  [[ -n "${TMPDIR:-}" && "$fp" == "${TMPDIR%/}"/* ]] && return 0
  # ~/.claude: the harness writes service state there continuously (memory,
  # sessions, tasks, todos…) — that must stay free. Only the SYSTEM zones are
  # gated: skills, hooks, agents, rules, commands, workflows, settings, env.
  if [[ "$fp" == "$HOME/.claude/"* ]]; then
    rel="${fp#"$HOME/.claude/"}"
    case "$rel" in
      skills/*|hooks/*|agents/*|rules/*|commands/*|workflows/*|settings.json|settings.local.json|craft.env) return 1 ;;
      *) return 0 ;;
    esac
  fi
  return 1
}

# git_ephemeral <path> — игнорируемое гитом эфемерно (сборка, логи) для ЛЮБОГО
# инструмента записи, кроме путей внутри .claude/: там игнор не оправдание.
git_ephemeral() {
  local fp="$1"
  case "$fp" in
    .claude/*|*/.claude/*) return 1 ;;
  esac
  git check-ignore -q -- "$fp" 2>/dev/null
}

# --- File edits (Write/Edit/MultiEdit/NotebookEdit) --------------------------
if [[ "$is_file_edit" -eq 1 ]]; then
  fp="$(jq -r '.tool_input.file_path // .tool_input.notebook_path // ""' <<<"$input" 2>/dev/null)"
  [[ -z "$fp" ]] && exit 0
  is_ephemeral "$fp" && exit 0
  git_ephemeral "$fp" && exit 0

  in_scope "$fp" && exit 0
  # Абсолютный путь к цели внутри текущего репо матчится и по репо-относительной
  # записи плана: строки «- где:» пишутся от корня репозитория.
  rel="${fp#"$PWD"/}"
  [[ "$rel" != "$fp" ]] && in_scope "$rel" && exit 0

  [[ -n "$scopelist" ]] && deny_scope "$fp"
  deny "Заблокировано план-гейтом: правка файла ($fp) без одобренного плана. Правки кода и системы идут через план-гейт: план-мод → ExitPlanMode (одобрение Влада именно тулзой, не текстом) → правки целей плана. Автономному прогону — CRAFT_AUTONOMOUS=1."
fi

# --- Bash writes -------------------------------------------------------------
# Разбор строки на ЦЕЛИ записи. Гейтится цель, а не команда: сборка, копирование в
# игнорируемый путь и любое чтение проходят.
if [[ "$is_bash" -eq 1 ]]; then
  cmd="$(jq -r '.tool_input.command // ""' <<<"$input" 2>/dev/null)"
  [[ -z "$cmd" ]] && exit 0

  # Тела heredoc с ЗАКАВЫЧЕННЫМ маркером вычёркиваются ПЕРВЫМИ, до снятия кавычек:
  # после снятия маркер <<'PY' неотличим от << и опознать его нечем. Внутри такого
  # тела shell-подстановок не бывает по определению, а «больше» там — сравнение кода
  # (i>0:), не перенаправление; сама строка-открыватель остаётся в скане целиком,
  # потому что перенаправление формы `cat <<'EOF' > файл` стоит именно на ней.
  # Незакавыченный маркер не вычёркивается: в его теле живут подстановки.
  strip_quoted_heredocs() {
    awk -v q="'" '
      inhd { if ($0 == mark) inhd = 0; next }
      {
        re = "<<[ \t]*[\"" q "][A-Za-z_][A-Za-z0-9_]*[\"" q "]"
        if (match($0, re)) {
          m = substr($0, RSTART, RLENGTH)
          sub("<<[ \t]*[\"" q "]", "", m); sub("[\"" q "]$", "", m)
          mark = m; inhd = 1
        }
        print
      }'
  }
  # Знак «больше» бывает и сравнением: в кавычках (jq 'select(.size > 10)') и в условных
  # скобках ([[ a > b ]]). Оба места вычёркиваются — но в ОТДЕЛЬНУЮ строку: разбору
  # записи из интерпретатора нужны буквальные кавычки вокруг пути, на вычеркнутой он бы
  # ослеп. Цена — перенаправление в закавыченную цель (> "мой файл") не увидится.
  scan="$(strip_quoted_heredocs <<<"$cmd" \
    | sed -E "s/'[^']*'/ /g; s/\"[^\"]*\"/ /g; s/\[\[[^]]*\]\]/ /g; s/\(\([^)]*\)\)/ /g")"

  # Цели: перенаправление (> >>), tee, правка на месте (-i), cp/mv (последний аргумент
  # либо явная цель после -t), запись из интерпретатора (open(…,'w'), write_text/bytes).
  targets="$(
    grep -oE '>>?[[:space:]]*[^|&;()<>[:space:]]+' <<<"$scan" 2>/dev/null | sed -E 's/^>>?[[:space:]]*//'
    grep -oE '\btee\b([[:space:]]+-[a-zA-Z]+)*[[:space:]]+[^|&;()<>[:space:]]+' <<<"$scan" 2>/dev/null | awk '{print $NF}'
    grep -oE '\b(sed|perl)\b[^|&;]*[[:space:]]-i[^|&;]*' <<<"$scan" 2>/dev/null | tr ' ' '\n' | grep -E '/|\.'
    grep -oE '\b(cp|mv)\b[^|&;]*[[:space:]]-t[[:space:]]+[^[:space:]|&;]+' <<<"$scan" 2>/dev/null \
      | sed -E 's/.*[[:space:]]-t[[:space:]]+//'
    grep -vE '[[:space:]]-t[[:space:]]' <<<"$scan" 2>/dev/null \
      | grep -oE '\b(cp|mv)\b[[:space:]]+[^|&;()<>]+' 2>/dev/null | awk '{print $NF}'
    grep -oE "open\([[:space:]]*['\"][^'\"]+['\"][[:space:]]*,[[:space:]]*['\"][wa]" <<<"$cmd" 2>/dev/null \
      | sed -E "s/^open\([[:space:]]*['\"]//; s/['\"].*$//"
    grep -oE "Path\([[:space:]]*['\"][^'\"]+['\"][[:space:]]*\)[[:space:]]*\.[[:space:]]*write_(text|bytes)" <<<"$cmd" 2>/dev/null \
      | sed -E "s/^Path\([[:space:]]*['\"]//; s/['\"].*$//"
  )"
  [[ -z "${targets//[[:space:]]/}" ]] && exit 0

  # Команда проходит, только когда КАЖДАЯ неэфемерная цель в периметре: смешанная
  # команда (одна цель из плана, другая нет) не проезжает по половине разрешения.
  offender=""
  while IFS= read -r t; do
    [[ -z "${t//[[:space:]]/}" ]] && continue
    # Дескрипторы и устройства целями записи в дерево не являются.
    case "$t" in
      /dev/*|0|1|2|"&1"|"&2"|-*) continue ;;
    esac
    t="${t%\"}"; t="${t#\"}"; t="${t%\'}"; t="${t#\'}"
    is_ephemeral "$t" && continue
    git_ephemeral "$t" && continue
    in_scope "$t" && continue
    rel="${t#"$PWD"/}"
    [[ "$rel" != "$t" ]] && in_scope "$rel" && continue
    offender="$t"; break
  done <<<"$targets"
  [[ -z "$offender" ]] && exit 0

  [[ -n "$scopelist" ]] && deny_scope "$offender"
  deny "Заблокировано план-гейтом: запись в файл ($offender) через Bash без одобренного плана. Шелл-запись — та же правка файла, что Write/Edit, и идёт через тот же гейт: план-мод → ExitPlanMode (одобрение Влада именно тулзой, не текстом) → правки целей плана. Сборка, вывод во временный каталог и в игнорируемый гитом путь проходят без плана. Автономному прогону — CRAFT_AUTONOMOUS=1."
fi

# --- Craft writes ------------------------------------------------------------
# Exempt-scope bypass: allow without a plan when the command targets ONLY
# blocks inside a pre-authorised direct-edit page. Keyed on the WRITE TARGET,
# not on wording: a real project/sphere write references block-IDs outside the
# scope, so it still needs a plan.
#
# ONE canonical scope location — the checkout holding the REAL hook file
# (resolve through the ~/.claude symlink); the cache builder
# (universal-cache-gate-exempt-scope.sh) writes it by the same formula, so
# cloud, local worktrees, arc-mounts and scheduled sessions all agree.
self="$(realpath "$0" 2>/dev/null || echo "$0")"
scope="${CRAFT_GATE_EXEMPT_SCOPE:-$(cd "$(dirname "$self")/../.." && pwd)/.claude/craft-gate-exempt-scope.txt}"
cmd="$(jq -r '.tool_input.command // ""' <<<"$input" 2>/dev/null)"
UUID_RE='[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}'
ids="$(grep -oE "$UUID_RE" <<<"$cmd" | sort -u)"
if [[ -s "$scope" && -n "$ids" ]]; then
  all_in=1
  while IFS= read -r id; do
    grep -qxF "$(tr 'a-f' 'A-F' <<<"$id")" "$scope" || { all_in=0; break; }
  done <<<"$ids"
  [[ "$all_in" -eq 1 ]] && exit 0
fi

# Периметр плана: каждый UUID команды обязан быть в списке целей. Команда без
# единого UUID адресуемой цели не несёт — остаётся deny, как раньше.
if [[ -n "$scopelist" && -n "$ids" ]]; then
  all_in=1
  while IFS= read -r id; do
    lid="$(tr 'A-F' 'a-f' <<<"$id")"
    in_scope "$lid" || in_scope "$id" || { all_in=0; break; }
  done <<<"$ids"
  [[ "$all_in" -eq 1 ]] && exit 0
  deny_scope "craft: $(head -c 120 <<<"$ids" | tr '\n' ' ')"
fi

deny "Заблокировано план-гейтом: запись в Craft без одобренного плана. Сначала покажи план и получи ок Влада (план-мод → ExitPlanMode), потом пиши цели плана. Запись целиком внутри предодобренной зоны (напр. «Продукты») проходит без плана. Автономному прогону (рутина, евал) — CRAFT_AUTONOMOUS=1."
