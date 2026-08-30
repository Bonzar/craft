#!/usr/bin/env bash
# Установка core и адаптеров системы агента для Claude Code и Codex.
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
# Claude подхватывает settings.json файловым вотчером. Codex требует отдельного
# trust-review для новой или изменённой пользовательской hook-конфигурации:
# установка файла сама по себе намеренно не выдаёт ему доверие.
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
HOOKS_SRC="$REPO/core/hooks"
CLAUDE_DISPATCH="node $REPO/adapters/claude/hooks/dispatch.mjs"
HOOKS_DST="$CLAUDE_DIR/hooks"
SETTINGS="$CLAUDE_DIR/settings.json"

command -v jq >/dev/null 2>&1 || { echo "ERROR: jq is required" >&2; exit 1; }
# Слой хуков исполняется node: без него зарегистрированные команды не запустятся
# вовсе, и сессия молча останется без гвардов. Проверяем до правки настроек.
command -v node >/dev/null 2>&1 || { echo "ERROR: node is required (слой хуков на JS)" >&2; exit 1; }

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

merged="$(jq --arg hooks "$HOOKS_SRC" --arg dispatch "$CLAUDE_DISPATCH" '
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
            | select(((.command // "") | startswith($hooks + "/craft-")) | not)
            | select(((.command // "") | startswith($hooks + "/dispatch.js")) | not) ]
        | select(((.hooks // []) | length) > 0) ]));

  # Одна регистрация на событие: состав и порядок хуков живут в таблице маршрутов
  # (core/hooks/dispatch-table.js), аргумент задаёт пользовательский контур.
  purge_hook_layer
  | ensure("SessionStart"; ""; $dispatch + " universal")
  | ensure("UserPromptSubmit"; ""; $dispatch + " universal")
  | ensure("PreToolUse"; ""; $dispatch + " universal")
  | ensure("PostToolUse"; ""; $dispatch + " universal")
  | ensure("PostToolUseFailure"; ""; $dispatch + " universal")
  | ensure("Stop"; ""; $dispatch + " universal")
  | ensure("PreCompact"; ""; $dispatch + " universal")
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

# --- 5. Codex: lifecycle hooks ------------------------------------------------
# Codex читает отдельный hooks.json. Нативное событие всегда проходит через
# Codex-адаптер; только канонический конверт затем попадает в общий core.
CODEX_DIR="${CODEX_HOME:-$HOME/.codex}"
CODEX_HOOKS="$CODEX_DIR/hooks.json"
mkdir -p "$CODEX_DIR"
node "$REPO/adapters/codex/tool-routing-config.mjs" ensure "$CODEX_DIR/config.toml"
[[ -f "$CODEX_HOOKS" ]] || echo '{}' > "$CODEX_HOOKS"
codex_backup="$CODEX_HOOKS.bak.$(date +%Y%m%d%H%M%S)"
cp "$CODEX_HOOKS" "$codex_backup"

codex_merged="$(jq --arg cmd "node $REPO/adapters/codex/hooks/dispatch.mjs" '
  def purge_legacy_dispatchers:
    .hooks = ((.hooks // {}) | with_entries(
      .value = [ .value[]
        | .hooks = [ (.hooks // [])[]
            | select(((.command // "") | contains("/.codex/hooks/dispatch.js")) | not)
            | select(((.command // "") | contains("/.Codex/hooks/dispatch.js")) | not) ]
        | select(((.hooks // []) | length) > 0) ]));

  def ensure(event; matcher; handler):
    .hooks = (.hooks // {})
    | .hooks[event] = (.hooks[event] // [])
    | if any(.hooks[event][]; ((.matcher // "") == matcher)
             and any((.hooks // [])[]; .command == handler.command)) then .
      else .hooks[event] += [if matcher == ""
        then {"hooks":[handler]}
        else {"matcher":matcher,"hooks":[handler]} end]
      end;
  def base: {"type":"command","command":$cmd,"timeout":3600};
  def context: base + {"additionalContextLimit":0};
  purge_legacy_dispatchers
  | ensure("SessionStart"; "startup|resume|clear|compact"; context + {"statusMessage":"Loading the Craft core"})
  | ensure("UserPromptSubmit"; ""; context)
  | ensure("SubagentStart"; ""; context)
  | ensure("SubagentStop"; ""; base)
  | ensure("PreToolUse"; ""; base)
  | ensure("PostToolUse"; ""; base)
  | ensure("Stop"; ""; base)
  | ensure("PreCompact"; ""; base)
' "$CODEX_HOOKS")"

if [[ "$(jq -S . <<<"$codex_merged")" == "$(jq -S . "$CODEX_HOOKS")" ]]; then
  rm -f "$codex_backup"
  codex_hooks_changed=0
  echo "codex hooks: no changes needed"
else
  printf '%s\n' "$codex_merged" > "$CODEX_HOOKS"
  codex_hooks_changed=1
  echo "codex hooks: registrations merged (backup: $codex_backup)"
fi

# --- 6. Codex: shared skills --------------------------------------------------
# Codex discovers personal skills in ~/.agents/skills. Link each core skill
# separately so unrelated personal skills survive installation and upgrades.
AGENT_SKILLS="$HOME/.agents/skills"
mkdir -p "$AGENT_SKILLS"
linked=0
for src in "$REPO/core/skills"/*; do
  [[ -d "$src" ]] || continue
  dst="$AGENT_SKILLS/$(basename "$src")"
  if [[ -L "$dst" && "$(readlink "$dst")" == "$src" ]]; then
    continue
  fi
  if [[ -e "$dst" || -L "$dst" ]]; then
    echo "codex skills: kept existing $dst"
    continue
  fi
  ln -s "$src" "$dst"
  linked=$((linked+1))
done
echo "codex skills: $linked link(s) added"

# --- 7. Universal agents: recursive runtime ----------------------------------
# Core owns recursive agent invocation. Both native harnesses receive the same
# stdio MCP endpoint; their generated agent files name only the universal
# agent.invoke capability, while this installation maps it to the local core.
# Registration is mandatory when a harness is installed: discovery files are
# generated only after every available harness has a verified endpoint.
AGENT_MCP="$REPO/core/agents/mcp-server.mjs"
CLAUDE_GLOBAL="$HOME/.claude.json"
[[ -f "$CLAUDE_GLOBAL" ]] || echo '{}' > "$CLAUDE_GLOBAL"
tmp_claude_mcp="$(mktemp)"
jq --arg server "$AGENT_MCP" '
  .mcpServers = (.mcpServers // {})
  | .mcpServers.craft_agent = {
      command: "node",
      args: [$server],
      env: {CRAFT_AGENT_BACKEND: "claude", CRAFT_AGENT_PERMISSION: "read-only"}
    }
' "$CLAUDE_GLOBAL" > "$tmp_claude_mcp"
if cmp -s "$tmp_claude_mcp" "$CLAUDE_GLOBAL"; then
  rm -f "$tmp_claude_mcp"
  echo "claude agent runtime: no changes needed"
else
  mkdir -p "$CLAUDE_DIR/backups"
  cp "$CLAUDE_GLOBAL" "$CLAUDE_DIR/backups/claude.json.$(date +%Y%m%d%H%M%S)"
  mv "$tmp_claude_mcp" "$CLAUDE_GLOBAL"
  echo "claude agent runtime: agent.invoke connected"
fi
if ! jq -e --arg server "$AGENT_MCP" '
    .mcpServers.craft_agent.command == "node"
    and .mcpServers.craft_agent.args == [$server]
    and .mcpServers.craft_agent.env.CRAFT_AGENT_BACKEND == "claude"
    and .mcpServers.craft_agent.env.CRAFT_AGENT_PERMISSION == "read-only"
  ' "$CLAUDE_GLOBAL" >/dev/null 2>&1; then
  echo "ERROR: verified Claude agent.invoke configuration is incomplete" >&2
  exit 1
fi

CODEX_CMD="${CRAFT_CODEX_CMD:-$(command -v codex 2>/dev/null || true)}"
if [[ -z "${CRAFT_CODEX_CMD:-}" && -x /Applications/ChatGPT.app/Contents/Resources/codex ]]; then
  CODEX_CMD=/Applications/ChatGPT.app/Contents/Resources/codex
fi
if [[ -n "$CODEX_CMD" ]]; then
  codex_mcp="$("$CODEX_CMD" mcp get craft_agent --json 2>/dev/null || true)"
  if node "$REPO/adapters/codex/mcp-config.mjs" check "$AGENT_MCP" <<<"$codex_mcp"; then
    echo "codex agent runtime: no changes needed"
  else
    "$CODEX_CMD" mcp remove craft_agent >/dev/null 2>&1 || true
    if "$CODEX_CMD" mcp add craft_agent \
      --env CRAFT_AGENT_BACKEND=codex --env CRAFT_AGENT_PERMISSION=read-only \
      -- node "$AGENT_MCP" >/dev/null 2>&1; then
      node "$REPO/adapters/codex/mcp-config.mjs" set-timeout "$CODEX_DIR/config.toml"
      codex_mcp="$("$CODEX_CMD" mcp get craft_agent --json 2>/dev/null || true)"
      if node "$REPO/adapters/codex/mcp-config.mjs" check "$AGENT_MCP" <<<"$codex_mcp"; then
        echo "codex agent runtime: agent.invoke connected"
      else
        echo "ERROR: Codex reported MCP registration success, but backend, permission or timeout is not the managed value" >&2
        exit 1
      fi
    else
      echo "ERROR: Codex agent.invoke MCP could not be installed; native agents would have no recursive runtime" >&2
      exit 1
    fi
  fi
else
  echo "WARNING: Codex CLI not found; agent.invoke MCP was not installed globally" >&2
fi

# --- 8. Universal agents: provider discovery adapters ------------------------
# Канонические определения и контракты запуска остаются в core. Генератор
# раскладывает только нативные discovery-файлы, чтобы один и тот же agentId был
# виден обоим харнессам. Чужие файлы с другими именами не затрагиваются.
node "$REPO/adapters/generate-agents.mjs" \
  --claude-dir "$CLAUDE_DIR/agents" \
  --codex-dir "$CODEX_DIR/agents" \
  --reference-root "$REPO"
echo "agents: Claude and Codex adapters generated from the core registry"

# --- 9. Codex: small global instruction adapter ------------------------------
# Большой router передаёт SessionStart; в AGENTS лежит только стабильный
# bootstrap-контракт. Managed block можно обновлять без касания личных правил.
CODEX_AGENTS="$CODEX_DIR/AGENTS.md"
tmp_agents="$(mktemp)"
if [[ -f "$CODEX_AGENTS" ]]; then
  awk '
    /^<!-- craft-agent-system:start -->$/ {pending=""; skip=1; next}
    /^<!-- craft-agent-system:end -->$/ {skip=0; next}
    !skip && $0 ~ /^@.*\/craft-live\/(behavior-rules|code-rules)\.md$/ {next}
    !skip && $0 == "" {pending=pending "\n"; next}
    !skip {printf "%s%s\n", pending, $0; pending=""}
  ' "$CODEX_AGENTS" > "$tmp_agents"
fi
cat >> "$tmp_agents" <<EOF

<!-- craft-agent-system:start -->
# Craft agent system

The shared Craft core is connected through $CODEX_HOOKS. Treat SessionStart
router and code-rules context as authoritative. Repository-specific AGENTS.md
files may add harness instructions but must not duplicate core policy.

Every action that is not proven read-only must use a directly exposed native
tool call. Do not route such actions through a programmatic tool container:
the plan-gate must receive the concrete call before it can execute.
<!-- craft-agent-system:end -->
EOF
if [[ ! -f "$CODEX_AGENTS" ]] || ! cmp -s "$tmp_agents" "$CODEX_AGENTS"; then
  [[ ! -f "$CODEX_AGENTS" ]] || cp "$CODEX_AGENTS" "$CODEX_AGENTS.bak.$(date +%Y%m%d%H%M%S)"
  mv "$tmp_agents" "$CODEX_AGENTS"
  echo "codex AGENTS.md: managed block updated"
else
  rm -f "$tmp_agents"
  echo "codex AGENTS.md: no changes needed"
fi

echo "Done. Claude and Codex are connected to the shared core."
if (( codex_hooks_changed )); then
  echo "Codex hooks: ACTION REQUIRED — open /hooks, review $CODEX_HOOKS and trust its Craft command hooks."
  echo "Codex hooks: then restart the desktop app or open a fresh thread; until trust is granted, gate and SessionStart context are inactive."
else
  echo "Codex desktop: if these hooks have not been trusted yet, open /hooks and trust $CODEX_HOOKS; then restart or open a fresh thread."
fi
