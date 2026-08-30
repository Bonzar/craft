#!/usr/bin/env bash
# Smoke-тест install.sh: Claude и Codex, идемпотентность и сохранность чужого.
# Гоняется во временном HOME — реальные пользовательские каталоги не трогает.
#   1. Первый прогон: регистрации в settings.json указывают в чекаут репы.
#   2. Чужой hook-блок, существовавший до установки, не затёрт.
#   3. Второй прогон: no-op ("no changes needed").
#   4. Дом со СТАРЫМ слоем: симлинки на репу сняты, регистрации со старым
#      адресом ~/.claude/hooks не остались рядом с новыми.
set -u

REPO="$(cd "$(dirname "$0")/.." && pwd)"
FAILS=()

TESTHOME="$(mktemp -d)"
trap 'rm -rf "$TESTHOME"' EXIT

# Чужая запись, которую install обязан сохранить.
mkdir -p "$TESTHOME/.claude"
cat > "$TESTHOME/.claude.json" <<'JSON'
{"mcpServers":{"foreign":{"command":"foreign-server","args":[]}}}
JSON
cat > "$TESTHOME/.claude/settings.json" <<'JSON'
{"hooks":{"PreToolUse":[{"matcher":"Bash","hooks":[{"type":"command","command":"/opt/foreign-guard.sh"}]}],"PostToolUse":[{"matcher":"Task","hooks":[{"type":"command","command":"\"$HOME\"/.claude/hooks/universal-mark-plan-critic.sh"},{"type":"command","command":"/opt/foreign-on-task.sh"}]}]},"permissions":{"allow":["Bash(ls:*)"]}}
JSON
mkdir -p "$TESTHOME/.codex" "$TESTHOME/.agents/skills/foreign-skill"
mkdir -p "$TESTHOME/.claude/agents" "$TESTHOME/.codex/agents"
printf '%s\n' 'foreign claude agent' > "$TESTHOME/.claude/agents/foreign-agent.md"
printf '%s\n' 'foreign codex agent' > "$TESTHOME/.codex/agents/foreign-agent.toml"
cat > "$TESTHOME/.codex/hooks.json" <<'JSON'
{"hooks":{"PreToolUse":[{"matcher":"shell_command","hooks":[{"type":"command","command":"/opt/foreign-codex-guard"}]},{"hooks":[{"type":"command","command":"'/tmp/test-home/.codex/hooks/dispatch.js' universal"}]}],"UserPromptSubmit":[{"hooks":[{"type":"command","command":"'/tmp/test-home/.Codex/hooks/dispatch.js' universal"}]}]}}
JSON
cat > "$TESTHOME/.codex/AGENTS.md" <<'MD'
# Personal rules

Keep this paragraph.
@/Users/tester/.Codex/craft-live/behavior-rules.md
@/Users/tester/.Codex/craft-live/code-rules.md
MD

out1="$(HOME="$TESTHOME" INSTALL_ALLOW_WORKTREE=1 bash "$REPO/install.sh" 2>&1)" \
  || FAILS+=("first run exited non-zero: $out1")
grep -q 'ACTION REQUIRED.*open /hooks' <<<"$out1" \
  || FAILS+=("first install did not require Codex hook trust review")

# Регистрация одна на событие — диспетчер, и ведёт она в чекаут репы, а не в
# ~/.claude/hooks. Состав хуков живёт в таблице маршрутов, не в настройках.
jq -e --arg cmd "node $REPO/adapters/claude/hooks/dispatch.mjs universal" '.hooks.PreToolUse[]?.hooks[]?.command
       | select(. == $cmd)' \
  "$TESTHOME/.claude/settings.json" >/dev/null 2>&1 \
  || FAILS+=("dispatcher registration does not point at the checkout")
[[ -L "$TESTHOME/.claude/hooks/universal-guard-plan-gate.sh" ]] \
  && FAILS+=("install created a symlink layer again")
for ev in SessionStart UserPromptSubmit PreToolUse PostToolUse PostToolUseFailure Stop PreCompact; do
  jq -e --arg e "$ev" --arg cmd "node $REPO/adapters/claude/hooks/dispatch.mjs universal" '.hooks[$e][]?.hooks[]?.command
         | select(. == $cmd)' \
    "$TESTHOME/.claude/settings.json" >/dev/null 2>&1 \
    || FAILS+=("dispatcher not registered on $ev")
done

# Codex adapter points at the same core, preserves foreign hooks and discovers
# the same skill directories through ~/.agents/skills.
for ev in SessionStart UserPromptSubmit PreToolUse PostToolUse Stop PreCompact; do
  jq -e --arg e "$ev" --arg cmd "node $REPO/adapters/codex/hooks/dispatch.mjs" \
    '.hooks[$e][]?.hooks[]?.command | select(. == $cmd)' \
    "$TESTHOME/.codex/hooks.json" >/dev/null 2>&1 \
    || FAILS+=("Codex dispatcher not registered on $ev")
done
jq -e '.hooks.PreToolUse[]?.hooks[]?.command | select(. == "/opt/foreign-codex-guard")' \
  "$TESTHOME/.codex/hooks.json" >/dev/null 2>&1 \
  || FAILS+=("foreign Codex hook was lost")
jq -e '.. | strings | select(test("\\.codex/hooks/dispatch\\.js|\\.Codex/hooks/dispatch\\.js"))' \
  "$TESTHOME/.codex/hooks.json" >/dev/null 2>&1 \
  && FAILS+=("stale Codex dispatcher survived")
grep -qF 'Keep this paragraph.' "$TESTHOME/.codex/AGENTS.md" \
  || FAILS+=("personal Codex AGENTS content was lost")
grep -qF '<!-- craft-agent-system:start -->' "$TESTHOME/.codex/AGENTS.md" \
  || FAILS+=("managed Codex AGENTS block is missing")
grep -qF 'must use a directly exposed native' "$TESTHOME/.codex/AGENTS.md" \
  || FAILS+=("managed Codex AGENTS block does not require direct tool routing")
grep -q '/craft-live/' "$TESTHOME/.codex/AGENTS.md" \
  && FAILS+=("stale Codex craft-live imports survived")
grep -qE '^code_mode_host[[:space:]]*=[[:space:]]*false$' "$TESTHOME/.codex/config.toml" \
  || FAILS+=("Codex programmatic tool container was not disabled")
[[ -L "$TESTHOME/.agents/skills/council" ]] \
  || FAILS+=("shared Codex skill link was not installed")
[[ -d "$TESTHOME/.agents/skills/foreign-skill" ]] \
  || FAILS+=("foreign Codex skill was lost")
[[ -f "$TESTHOME/.claude/agents/comment-analyzer.md" ]] \
  || FAILS+=("Claude agent adapter was not installed")
[[ -f "$TESTHOME/.codex/agents/comment-analyzer.toml" ]] \
  || FAILS+=("Codex agent adapter was not installed")
grep -qF "$REPO/core/agents/definitions/comment-analyzer.md" "$TESTHOME/.claude/agents/comment-analyzer.md" \
  || FAILS+=("Claude agent adapter does not point to the core definition")
grep -qF 'model = "gpt-5.3-codex-spark"' "$TESTHOME/.codex/agents/comment-analyzer.toml" \
  || FAILS+=("Codex fast agent adapter does not use Spark")
grep -qF 'mcp__craft_agent__invoke' "$TESTHOME/.claude/agents/comment-analyzer.md" \
  || FAILS+=("Claude agent adapter does not expose core agent.invoke")
jq -e --arg server "$REPO/core/agents/mcp-server.mjs" '
    .mcpServers.craft_agent.command == "node"
    and .mcpServers.craft_agent.args == [$server]
    and .mcpServers.craft_agent.env.CRAFT_AGENT_BACKEND == "claude"
    and .mcpServers.craft_agent.env.CRAFT_AGENT_PERMISSION == "read-only"
  ' "$TESTHOME/.claude.json" >/dev/null 2>&1 \
  || FAILS+=("Claude core agent.invoke MCP config is incomplete")
jq -e '.mcpServers.foreign.command == "foreign-server"' "$TESTHOME/.claude.json" >/dev/null 2>&1 \
  || FAILS+=("foreign Claude MCP server was lost")
[[ -f "$TESTHOME/.claude/agents/foreign-agent.md" ]] \
  || FAILS+=("foreign Claude agent was lost")
[[ -f "$TESTHOME/.codex/agents/foreign-agent.toml" ]] \
  || FAILS+=("foreign Codex agent was lost")
jq -e '.hooks.PreToolUse[]?.hooks[]?.command
       | select(. == "/opt/foreign-guard.sh")' \
  "$TESTHOME/.claude/settings.json" >/dev/null 2>&1 \
  || FAILS+=("foreign hook entry was lost")
jq -e '.permissions.allow | index("Bash(ls:*)")' \
  "$TESTHOME/.claude/settings.json" >/dev/null 2>&1 \
  || FAILS+=("foreign permissions were lost")

# Поштучные регистрации прежнего слоя сняты: их место занял диспетчер, и
# оставленная запись звала бы тот же хук вторым процессом. Чужая команда в той
# же группе цела.
n_mark="$(jq '[.hooks[]?[]?.hooks[]?.command
       | select(contains("universal-mark-plan-critic"))] | length' \
  "$TESTHOME/.claude/settings.json")"
[[ "$n_mark" == "0" ]] || FAILS+=("stale per-hook registration survived ($n_mark left)")
jq -e '.hooks.PostToolUse[]?.hooks[]?.command
       | select(. == "/opt/foreign-on-task.sh")' \
  "$TESTHOME/.claude/settings.json" >/dev/null 2>&1 \
  || FAILS+=("foreign hook in the Task group was lost")

# Канал импорта живых правил: две строки в CLAUDE.md и пустые снимки под них.
# Без файла-снимка импорт битый, без строки — снимок никто не прочитает.
for snap in behavior-rules code-rules; do
  [[ -f "$TESTHOME/.claude/craft-live/$snap.md" ]] \
    || FAILS+=("snapshot $snap.md was not created")
  grep -qxF "@$TESTHOME/.claude/craft-live/$snap.md" "$TESTHOME/.claude/CLAUDE.md" 2>/dev/null \
    || FAILS+=("import line for $snap.md missing in CLAUDE.md")
done

out2="$(HOME="$TESTHOME" INSTALL_ALLOW_WORKTREE=1 bash "$REPO/install.sh" 2>&1)" \
  || FAILS+=("second run exited non-zero: $out2")
grep -q 'no changes needed' <<<"$out2" || FAILS+=("second run changed settings (not idempotent)")
grep -q 'codex hooks: no changes needed' <<<"$out2" \
  || FAILS+=("second run changed Codex hooks")
grep -q 'if these hooks have not been trusted yet, open /hooks' <<<"$out2" \
  || FAILS+=("idempotent install lost the Codex hook trust reminder")
grep -q 'codex AGENTS.md: no changes needed' <<<"$out2" \
  || FAILS+=("second run changed Codex AGENTS.md")
managed_count="$(grep -c '^<!-- craft-agent-system:start -->$' "$TESTHOME/.codex/AGENTS.md")"
[[ "$managed_count" == "1" ]] || FAILS+=("Codex AGENTS managed block duplicated")

# Вторая установка не задваивает строки импорта в личном файле Влада.
for snap in behavior-rules code-rules; do
  n="$(grep -cxF "@$TESTHOME/.claude/craft-live/$snap.md" "$TESTHOME/.claude/CLAUDE.md" 2>/dev/null || echo 0)"
  [[ "$n" == "1" ]] || FAILS+=("import line for $snap.md duplicated on second run (count=$n)")
done

# --- 4. Дом, где уже разложен СТАРЫЙ слой ------------------------------------
# У Влада на машине симлинки и регистрации с адресом ~/.claude/hooks стоят с
# прошлой установки: обязаны исчезнуть, иначе скиллы задвоятся, а сорок записей
# будут указывать на снесённые файлы.
OLDHOME="$(mktemp -d)"
mkdir -p "$OLDHOME/.claude/hooks" "$OLDHOME/.claude/skills"
ln -s "$REPO/.claude/hooks/universal-guard-plan-gate.sh" "$OLDHOME/.claude/hooks/universal-guard-plan-gate.sh"
ln -s "$REPO/.claude/skills/council" "$OLDHOME/.claude/skills/council"
cat > "$OLDHOME/.claude/settings.json" <<'JSON'
{"hooks":{"PreToolUse":[{"matcher":"Bash","hooks":[{"type":"command","command":"\"$HOME\"/.claude/hooks/universal-guard-plan-gate.sh"},{"type":"command","command":"/opt/foreign-guard.sh"}]}]}}
JSON

out3="$(HOME="$OLDHOME" INSTALL_ALLOW_WORKTREE=1 bash "$REPO/install.sh" 2>&1)" \
  || FAILS+=("upgrade run exited non-zero: $out3")

[[ -L "$OLDHOME/.claude/hooks/universal-guard-plan-gate.sh" ]] \
  && FAILS+=("stale hook symlink survived the upgrade")
[[ -L "$OLDHOME/.claude/skills/council" ]] \
  && FAILS+=("stale skill symlink survived the upgrade")
jq -e '[.hooks[]?[]?.hooks[]?.command
       | select(startswith("\"$HOME\"/.claude/hooks/universal-"))] | length == 0' \
  "$OLDHOME/.claude/settings.json" >/dev/null 2>&1 \
  || FAILS+=("stale registrations pointing at ~/.claude/hooks survived")
jq -e '.hooks.PreToolUse[]?.hooks[]?.command | select(. == "/opt/foreign-guard.sh")' \
  "$OLDHOME/.claude/settings.json" >/dev/null 2>&1 \
  || FAILS+=("foreign hook was lost during the upgrade")
rm -rf "$OLDHOME"

# --- 5. Ошибка обязательного MCP не оставляет роли с несуществующим invoke --
# Native discovery рекламирует agent.invoke только после подтверждённой
# регистрации endpoint. Установщик обязан завершиться ошибкой, а не warning.
FAILHOME="$(mktemp -d)"
FAILCODEX="$FAILHOME/failing-codex"
cat > "$FAILCODEX" <<'SH'
#!/usr/bin/env bash
if [[ "$1 $2" == "mcp remove" ]]; then exit 0; fi
exit 1
SH
chmod +x "$FAILCODEX"
if HOME="$FAILHOME" CRAFT_CODEX_CMD="$FAILCODEX" INSTALL_ALLOW_WORKTREE=1 \
  bash "$REPO/install.sh" >"$FAILHOME/out" 2>&1; then
  FAILS+=("install succeeded after mandatory Codex MCP registration failed")
fi
grep -q 'native agents would have no recursive runtime' "$FAILHOME/out" \
  || FAILS+=("MCP registration failure was not reported as fail-closed")
[[ ! -f "$FAILHOME/.codex/agents/comment-analyzer.toml" ]] \
  || FAILS+=("native agents were generated after MCP registration failure")
rm -rf "$FAILHOME"

if [[ ${#FAILS[@]} -gt 0 ]]; then
  echo "install-smoke: FAIL"
  for f in "${FAILS[@]}"; do echo "  - $f"; done
  exit 1
fi
echo "install-smoke: OK"
