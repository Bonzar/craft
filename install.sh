#!/usr/bin/env bash
# Установка универсального слоя системы агента в ~/.claude (локальная машина).
#
# Что делает (идемпотентно, повторный запуск = no-op):
#   1. Регистрирует universal-* хуки в ~/.claude/settings.json командами в этот чекаут.
#   2. Снимает прежний симлинк-слой в ~/.claude (хуки, скиллы, агенты, команды)
#      и регистрации, которые на него указывали: скиллы, команды и агенты
#      сессия берёт из самой репы, подключённой рабочей директорией.
#   3. Создаёт ~/.claude/craft.env (chmod 600), перенося CRAFT_* из репо-.env,
#      если тот есть, — универсальные хуки берут оттуда connect-доступ к Craft
#      в сессиях вне этого репо.
#   4. Ставит ПАКЕТЫ слоя (tools/jarvis.py install): их харнес запускает своей
#      строкой на модуль и событие, мимо диспетчера, — без этого шага гварды,
#      живущие пакетами, не встают вовсе.
#
# Облаку этот скрипт не нужен: в облачных сессиях craft-local хуки работают
# project-level из .claude/settings.json. Для НОВЫХ облачных реп — одна строка
# в bootstrap окружения:  git clone <this-repo> ~/agent-system && bash ~/agent-system/install.sh
#
# Перезапускать активные сессии после установки не нужно: регистрацию хуков в
# settings.json подхватывает файл-вотчер Claude Code, а тела хуков читаются с
# диска в момент срабатывания.
set -euo pipefail

REPO="$(cd "$(dirname "$0")" && pwd)"
# Симлинки должны указывать на долгоживущий основной чекаут: воркри сессий
# удаляются, и линки из них протухают.
if [[ -z "${INSTALL_ALLOW_WORKTREE:-}" && "$REPO" == */.claude/worktrees/* ]]; then
  echo "ERROR: запуск из сессионного воркри ($REPO)." >&2
  echo "Запусти из основного чекаута (bash ~/craft-local/install.sh)" >&2
  echo "или форсируй: INSTALL_ALLOW_WORKTREE=1 bash install.sh" >&2
  exit 1
fi
CLAUDE_DIR="$HOME/.claude"
HOOKS_SRC="$REPO/.claude/hooks"
HOOKS_DST="$CLAUDE_DIR/hooks"
SETTINGS="$CLAUDE_DIR/settings.json"

command -v jq >/dev/null 2>&1 || { echo "ERROR: jq is required" >&2; exit 1; }
# Слой хуков исполняется node: без него зарегистрированные команды не запустятся
# вовсе, и сессия молча останется без гвардов. Проверяем до правки настроек.
command -v node >/dev/null 2>&1 || { echo "ERROR: node is required (слой хуков на JS)" >&2; exit 1; }
# Пакеты слоя на python3, и часть гвардов живёт уже в них: без него установка
# дала бы слой без этих гвардов — молча.
command -v python3 >/dev/null 2>&1 || { echo "ERROR: python3 is required (гварды слоя живут пакетами)" >&2; exit 1; }
# Разбор оболочки: на нём стоят цели записи, доказанное чтение и цели чтения, то
# есть план-гейт, гвард якоря сессии и гвард необратимого. Требование ЖЁСТКОЕ,
# как node и python3: без разбора слой не гадает, а называет непокрытое — и
# каждый вызов шелла идёт на сверку. Работать в таком состоянии можно, ставить
# его молча — нельзя.
command -v shfmt >/dev/null 2>&1 || {
  echo "ERROR: shfmt is required (разбор команд; без него гейт сверяет каждый вызов шелла)" >&2
  echo "  мак:    brew install shfmt" >&2
  echo "  линукс: sudo bash \"$REPO/tools/install-shfmt.sh\"" >&2
  exit 1
}

mkdir -p "$HOOKS_DST"

# --- 1. Снос прежнего симлинк-слоя -------------------------------------------
# Симлинков больше нет: хуки исполняются прямо из чекаута по зарегистрированному
# пути, а скиллы, команды и агенты Claude Code берёт из репы, подключённой к
# сессии рабочей директорией (--add-dir грузит .claude/skills, .claude/commands
# и .claude/agents). Оставленные линки задвоили бы скиллы (личная копия
# перекрывает проектную) и указывали бы в чекаут мимо регистраций.
unlinked=0
for dir_name in hooks skills agents commands; do
  d="$CLAUDE_DIR/$dir_name"
  [[ -d "$d" ]] || continue
  for dst in "$d"/*; do
    [[ -L "$dst" ]] || continue
    target="$(readlink "$dst")"
    [[ "$target" == "$REPO"/* ]] || continue
    rm -f "$dst"
    unlinked=$((unlinked+1))
  done
done
echo "symlinks: $unlinked stale link(s) removed from $CLAUDE_DIR"

# --- 2. Регистрация в ~/.claude/settings.json --------------------------------
[[ -f "$SETTINGS" ]] || echo '{}' > "$SETTINGS"
backup="$SETTINGS.bak.$(date +%Y%m%d%H%M%S)"
cp "$SETTINGS" "$backup"

merged="$(jq --arg hooks "$HOOKS_SRC" '
  # Снятие устаревшей регистрации: ensure() умеет только дописывать, поэтому смена
  # матчера без drop оставляет старую запись, и хук отрабатывает дважды. Сверка идёт по
  # ПАРЕ «матчер и команда» — чужая регистрация с тем же матчером не страдает.
  def drop(event; matcher; cmd):
    if (.hooks[event]? // null) == null then .
    else .hooks[event] = [ .hooks[event][]
      | if (.matcher // "") == matcher
        then .hooks = [ (.hooks // [])[] | select(.command != cmd) ]
        else . end
      | select(((.hooks // []) | length) > 0) ]
    end;

  def ensure(event; matcher; cmd):
    .hooks = (.hooks // {})
    | .hooks[event] = (.hooks[event] // [])
    | if any(.hooks[event][]; ((.matcher // "") == matcher)
             and any((.hooks // [])[]; .command == cmd)) then .
      elif any(.hooks[event][]; (.matcher // "") == matcher) then
        .hooks[event] = [ .hooks[event][]
          | if (.matcher // "") == matcher
            then .hooks = ((.hooks // []) + [{"type":"command","command":cmd}])
            else . end ]
      else
        .hooks[event] += [ if matcher == ""
          then {"hooks":[{"type":"command","command":cmd}]}
          else {"matcher":matcher,"hooks":[{"type":"command","command":cmd}]} end ]
      end;

  # Чистка прежнего слоя: поштучные регистрации хуков. Их место занял диспетчер,
  # и оставленные записи звали бы те же хуки во второй раз.
  def purge_hook_layer:
    .hooks = ((.hooks // {}) | with_entries(
      .value = [ .value[]
        | .hooks = [ (.hooks // [])[]
            | select(((.command // "") | startswith("\"$HOME\"/.claude/hooks/universal-")) | not)
            | select(((.command // "") | startswith($hooks + "/universal-")) | not)
            | select(((.command // "") | startswith($hooks + "/craft-")) | not) ]
        | select(((.hooks // []) | length) > 0) ]));

  # Одна регистрация на событие: состав и порядок хуков живут в таблице маршрутов
  # (.claude/hooks/dispatch-table.js), аргумент задаёт пользовательский контур.
  purge_hook_layer
  | ensure("SessionStart"; ""; "\($hooks)/dispatch.js universal")
  | ensure("UserPromptSubmit"; ""; "\($hooks)/dispatch.js universal")
  | ensure("PreToolUse"; ""; "\($hooks)/dispatch.js universal")
  | ensure("PostToolUse"; ""; "\($hooks)/dispatch.js universal")
  | ensure("PostToolUseFailure"; ""; "\($hooks)/dispatch.js universal")
  | ensure("Stop"; ""; "\($hooks)/dispatch.js universal")
  | ensure("SessionEnd"; ""; "\($hooks)/dispatch.js universal")
  | ensure("PreCompact"; ""; "\($hooks)/dispatch.js universal")
' "$SETTINGS")"

if [[ "$(jq -S . <<<"$merged")" == "$(jq -S . "$SETTINGS")" ]]; then
  rm -f "$backup"
  echo "settings: no changes needed"
else
  printf '%s\n' "$merged" > "$SETTINGS"
  echo "settings: hook registrations merged (backup: $backup)"
fi

# --- 3. ~/.claude/craft.env ---------------------------------------------------
CRAFT_ENV="$CLAUDE_DIR/craft.env"
if [[ ! -f "$CRAFT_ENV" ]]; then
  {
    echo "# Craft connect-доступ для универсальных хуков в сессиях вне craft-репо."
    echo "# Заполни CRAFT_API_BASE (connect-ссылка с токеном) — или значения ниже"
    echo "# перенесены из репо-.env установщиком."
    if [[ -f "$REPO/.env" ]]; then
      grep -E '^(CRAFT_API_BASE|CRAFT_LINKS_STORE)=' "$REPO/.env" || true
    else
      echo "#CRAFT_API_BASE="
      echo "#CRAFT_LINKS_STORE="
    fi
  } > "$CRAFT_ENV"
  chmod 600 "$CRAFT_ENV"
  echo "craft.env: created at $CRAFT_ENV"
else
  echo "craft.env: already present"
fi

# --- 4. пакеты слоя -----------------------------------------------------------
# Гварды, живущие ПАКЕТАМИ, харнес запускает своей строкой регистрации, а не
# через диспетчера: у пакетов общего диспетчера нет (решение 9). Значит
# установка слоя без установки пакетов — это слой БЕЗ ЭТИХ ГВАРДОВ, причём молча.
# Отсюда шаг здесь, а не отдельной командой, которую надо помнить.
#
# Отказ установщика пакетов ОСТАНАВЛИВАЕТ установку: незакрытая зависимость или
# несобравшаяся обёртка значат, что гвард не встанет, — а тихо поставленный
# наполовину слой и есть худший исход.
echo "packages: $(python3 "$REPO/tools/jarvis.py" --root "$REPO" install | tr '\n' '; ')"

# --- 5. канал импорта живых правил -------------------------------------------
# stdout SessionStart-хука обрезается на 10 000 символах, и страницы правил давно
# длиннее: печатью терялся хвост, причём молча. Поэтому инжект-хуки пишут тело в
# файлы-снимки, а подтягивает их `@`-импорт в пользовательском CLAUDE.md — у
# импортов потолка нет. Здесь ставится сам канал: каталог снимков и две строки
# импорта, идемпотентно и с бэкапом личного файла.
LIVE_DIR="$CLAUDE_DIR/craft-live"
USER_MD="$CLAUDE_DIR/CLAUDE.md"
mkdir -p "$LIVE_DIR"

md_added=0
for snap in behavior-rules code-rules; do
  line="@$LIVE_DIR/$snap.md"
  # Снимок заводится пустым: до первого прогона хука импортировать нечего, а
  # отсутствующий файл делает импорт битым.
  [[ -f "$LIVE_DIR/$snap.md" ]] || : > "$LIVE_DIR/$snap.md"
  if [[ -f "$USER_MD" ]] && grep -qxF "$line" "$USER_MD"; then
    continue
  fi
  if (( ! md_added )) && [[ -f "$USER_MD" ]]; then
    md_backup="$USER_MD.bak.$(date +%Y%m%d%H%M%S)"
    cp "$USER_MD" "$md_backup"
    echo "CLAUDE.md: backup at $md_backup"
  fi
  printf '\n%s\n' "$line" >> "$USER_MD"
  md_added=1
done

if (( md_added )); then
  echo "live-rules: канал импорта установлен в $USER_MD (снимки в $LIVE_DIR)"
else
  echo "live-rules: канал импорта уже стоит"
fi

# --- 6. git-хуки репо ---------------------------------------------------------
# pre-commit из .githooks не пускает в коммит снимки живого Craft (роутер,
# инцидент, кэш зоны, прогретый кэш): .gitignore закрывает их по пути, а хук —
# по маске имени, в любом каталоге. Тот же скрипт гоняет CI по дереву коммита.
if git -C "$REPO" rev-parse --git-dir >/dev/null 2>&1; then
  hooks_path="$(git -C "$REPO" config --get core.hooksPath || true)"
  if [[ "$hooks_path" == ".githooks" ]]; then
    echo "git-hooks: core.hooksPath уже .githooks"
  elif [[ -n "$hooks_path" ]]; then
    # Чужой каталог хуков не затирается: в нём могут жить свои pre-commit и
    # pre-push, и подмена молча выключила бы их все. Решение — Влада.
    echo "git-hooks: core.hooksPath=$hooks_path уже задан — оставлен как есть." >&2
    echo "git-hooks: чтобы включить гвард снимков, добавь в $hooks_path/pre-commit строку" >&2
    echo "           node tools/no-snapshot-files.js --staged" >&2
    echo "           или переключи: git -C $REPO config core.hooksPath .githooks" >&2
  else
    git -C "$REPO" config core.hooksPath .githooks
    echo "git-hooks: core.hooksPath=.githooks (pre-commit: tools/no-snapshot-files.js)"
  fi
else
  echo "git-hooks: $REPO не git-чекаут, pre-commit не подключён"
fi

echo "Done. Новые регистрации действуют в активных сессиях без перезапуска."
