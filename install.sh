#!/usr/bin/env bash
# Установка универсального слоя системы агента в ~/.claude (локальная машина).
#
# Что делает (идемпотентно, повторный запуск = no-op):
#   1. Симлинкает universal-* хуки (+ их данные) из этого репо в ~/.claude/hooks/.
#   2. Дописывает их регистрацию в ~/.claude/settings.json (jq-мерж с бэкапом;
#      существующие чужие хуки и permissions не трогаются).
#   3. Создаёт ~/.claude/craft.env (chmod 600), перенося CRAFT_* из репо-.env,
#      если тот есть, — универсальные хуки берут оттуда connect-доступ к Craft
#      в сессиях вне этого репо.
#
# Облаку этот скрипт не нужен: в облачных сессиях craft-local хуки работают
# project-level из .claude/settings.json. Для НОВЫХ облачных реп — одна строка
# в bootstrap окружения:  git clone <this-repo> ~/agent-system && bash ~/agent-system/install.sh
#
# После установки действующие сессии Claude Code надо перезапустить: конфиг
# хуков снимается на старте сессии.
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

# --- 1. Симлинки универсальных хуков и их данных ------------------------------
linked=0
for f in "$HOOKS_SRC"/universal-*.sh "$HOOKS_SRC/_load-env.sh" "$HOOKS_SRC/incident-markers.txt"; do
  [[ -e "$f" ]] || continue
  dst="$HOOKS_DST/$(basename "$f")"
  if [[ -L "$dst" && "$(readlink "$dst")" == "$f" ]]; then
    continue
  fi
  if [[ -e "$dst" && ! -L "$dst" ]]; then
    echo "SKIP: $dst существует и не симлинк — разберись вручную" >&2
    continue
  fi
  ln -sf "$f" "$dst"
  linked=$((linked+1))
done
echo "hooks: $linked new symlink(s) in $HOOKS_DST"

# --- 1b. Симлинки универсальных скиллов ---------------------------------------
SKILLS_SRC="$REPO/.claude/skills"
SKILLS_DST="$CLAUDE_DIR/skills"
mkdir -p "$SKILLS_DST"
slinked=0
for d in "$SKILLS_SRC"/*/; do
  [[ -d "$d" ]] || continue
  name="$(basename "$d")"
  dst="$SKILLS_DST/$name"
  src="${d%/}"
  if [[ -L "$dst" && "$(readlink "$dst")" == "$src" ]]; then
    continue
  fi
  if [[ -e "$dst" && ! -L "$dst" ]]; then
    echo "SKIP: $dst существует и не симлинк — разберись вручную" >&2
    continue
  fi
  ln -sfn "$src" "$dst"
  slinked=$((slinked+1))
done
echo "skills: $slinked new symlink(s) in $SKILLS_DST"

# --- 2. Регистрация в ~/.claude/settings.json --------------------------------
[[ -f "$SETTINGS" ]] || echo '{}' > "$SETTINGS"
backup="$SETTINGS.bak.$(date +%Y%m%d%H%M%S)"
cp "$SETTINGS" "$backup"

merged="$(jq '
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

  ensure("PreToolUse"; "Write|Edit|MultiEdit|NotebookEdit";
         "\"$HOME\"/.claude/hooks/universal-guard-plan-gate.sh")
  | ensure("PreToolUse"; "mcp__.*__craft_write";
         "\"$HOME\"/.claude/hooks/universal-guard-plan-gate.sh")
  | ensure("PreToolUse"; "Bash";
         "\"$HOME\"/.claude/hooks/universal-sleep-waiter-guard.sh")
  | ensure("PostToolUse"; "ExitPlanMode";
         "\"$HOME\"/.claude/hooks/universal-plan-gate-approve.sh")
  | ensure("UserPromptSubmit"; "";
         "\"$HOME\"/.claude/hooks/universal-plan-gate-reset.sh")
  | ensure("UserPromptSubmit"; "";
         "\"$HOME\"/.claude/hooks/universal-detect-incident.sh")
  | ensure("SessionStart"; "";
         "\"$HOME\"/.claude/hooks/universal-env-capabilities.sh")
  | ensure("SessionStart"; "";
         "\"$HOME\"/.claude/hooks/universal-inject-behavior-rules.sh")
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

echo "Done. Перезапусти активные сессии Claude Code — хуки читаются на старте."
