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

  # Чистка прежнего слоя: записи, указывающие на снесённые симлинки в
  # ~/.claude/hooks, иначе остались бы мёртвыми рядом с новыми.
  def purge_symlink_layer:
    .hooks = ((.hooks // {}) | with_entries(
      .value = [ .value[]
        | .hooks = [ (.hooks // [])[]
            | select(((.command // "") | startswith("\"$HOME\"/.claude/hooks/universal-")) | not) ]
        | select(((.hooks // []) | length) > 0) ]));

  purge_symlink_layer
  | drop("PostToolUse"; "Task";
         "\($hooks)/universal-mark-plan-critic.sh")
  | ensure("PreToolUse"; "Write|Edit|MultiEdit|NotebookEdit";
         "\($hooks)/universal-guard-plan-gate.sh")
  | ensure("PreToolUse"; "mcp__.*__craft_write";
         "\($hooks)/universal-guard-plan-gate.sh")
  | ensure("PreToolUse"; "Bash";
         "\($hooks)/universal-guard-plan-gate.sh")
  | ensure("PostToolUseFailure"; "ExitPlanMode";
         "\($hooks)/universal-guard-plan-exit-failure.sh")
  | ensure("PreToolUse"; "Bash";
         "\($hooks)/universal-sleep-waiter-guard.sh")
  | ensure("PreToolUse"; "Bash";
         "\($hooks)/universal-kill-by-name-guard.sh")
  | ensure("PreToolUse"; "Bash";
         "\($hooks)/universal-block-no-verify.sh")
  | ensure("PreToolUse"; "Write|Edit|MultiEdit";
         "\($hooks)/universal-config-protection.sh")
  | ensure("Stop"; "";
         "\($hooks)/universal-check-console-log.sh")
  | ensure("Stop"; "";
         "\($hooks)/universal-stop-quality-gate.sh")
  | ensure("PreCompact"; "";
         "\($hooks)/universal-pre-compact.sh")
  | ensure("PostToolUse"; "ExitPlanMode";
         "\($hooks)/universal-plan-gate-approve.sh")
  | ensure("PreToolUse"; "ExitPlanMode";
         "\($hooks)/universal-guard-plan-critic.sh")
  | ensure("PreToolUse"; "ExitPlanMode";
         "\($hooks)/universal-guard-plan-delta.sh")
  | ensure("PreToolUse"; "ExitPlanMode";
         "\($hooks)/universal-guard-plan-service-turn.sh")
  | ensure("PostToolUse"; "ExitPlanMode";
         "\($hooks)/universal-guard-plan-delta.sh")
  | ensure("PostToolUse"; "Task|Agent";
         "\($hooks)/universal-mark-plan-critic.sh")
  | ensure("PreToolUse"; "Task|Agent";
         "\($hooks)/universal-guard-critic-plateau.sh")
  | ensure("PostToolUse"; "AskUserQuestion";
         "\($hooks)/universal-plan-gate-button.sh")
  | ensure("PostToolUse"; "Write|Edit|MultiEdit";
         "\($hooks)/universal-mark-plan-file.sh")
  | ensure("UserPromptSubmit"; "";
         "\($hooks)/universal-plan-gate-reset.sh")
  | ensure("UserPromptSubmit"; "";
         "\($hooks)/universal-mark-plan-critic.sh")
  | ensure("UserPromptSubmit"; "";
         "\($hooks)/universal-detect-incident.sh")
  | ensure("SessionStart"; "";
         "\($hooks)/universal-env-capabilities.sh")
  | ensure("SessionStart"; "";
         "\($hooks)/universal-inject-behavior-rules.sh")
  | ensure("SessionStart"; "";
         "\($hooks)/universal-inject-code-rules.sh")
  | ensure("SessionStart"; "";
         "\($hooks)/universal-inject-instincts.sh")
  | ensure("SessionStart"; "";
         "\($hooks)/universal-cache-gate-exempt-scope.sh")
  | ensure("PostToolUse"; "";
         "\($hooks)/universal-observe-buffer.sh")
  | ensure("Stop"; "";
         "\($hooks)/universal-instinct-flush.sh")
  | ensure("PreToolUse"; "Bash";
         "\($hooks)/universal-fact-gate.sh")
  | ensure("PreToolUse"; "mcp__.*__craft_write";
         "\($hooks)/universal-fact-gate.sh")
  | ensure("Stop"; "";
         "\($hooks)/universal-stop-routine-facts.sh")
  | ensure("Stop"; "";
         "\($hooks)/universal-stop-incident-closure.sh")
  | ensure("Stop"; "";
         "\($hooks)/universal-stop-relative-link.sh")
  | ensure("PreToolUse"; "Read|Grep|Glob";
         "\($hooks)/universal-eval-materials-guard.sh")
  | ensure("UserPromptSubmit"; "";
         "\($hooks)/universal-sync-system.sh")
  | ensure("Stop"; "";
         "\($hooks)/universal-sync-system.sh")
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

# --- 4. канал импорта живых правил -------------------------------------------
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

echo "Done. Новые регистрации действуют в активных сессиях без перезапуска."
