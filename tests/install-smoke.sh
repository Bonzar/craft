#!/usr/bin/env bash
# Smoke-тест install.sh: идемпотентность и сохранность чужих настроек.
# Гоняется во временном HOME — реальный ~/.claude не трогается.
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
cat > "$TESTHOME/.claude/settings.json" <<'JSON'
{"hooks":{"PreToolUse":[{"matcher":"Bash","hooks":[{"type":"command","command":"/opt/foreign-guard.sh"}]}],"PostToolUse":[{"matcher":"Task","hooks":[{"type":"command","command":"\"$HOME\"/.claude/hooks/universal-mark-plan-critic.sh"},{"type":"command","command":"/opt/foreign-on-task.sh"}]}]},"permissions":{"allow":["Bash(ls:*)"]}}
JSON

out1="$(HOME="$TESTHOME" INSTALL_ALLOW_WORKTREE=1 bash "$REPO/install.sh" 2>&1)" \
  || FAILS+=("first run exited non-zero: $out1")

# Команда регистрации ведёт в чекаут репы, а не в ~/.claude/hooks.
jq -e --arg h "$REPO/.claude/hooks" '.hooks.PreToolUse[]?.hooks[]?.command
       | select(. == ($h + "/universal-guard-plan-gate.sh"))' \
  "$TESTHOME/.claude/settings.json" >/dev/null 2>&1 \
  || FAILS+=("plan-gate registration does not point at the checkout")
[[ -L "$TESTHOME/.claude/hooks/universal-guard-plan-gate.sh" ]] \
  && FAILS+=("install created a symlink layer again")
jq -e '.hooks.PreToolUse[]?.hooks[]?.command
       | select(contains("universal-guard-plan-gate.sh"))' \
  "$TESTHOME/.claude/settings.json" >/dev/null 2>&1 \
  || FAILS+=("plan-gate registration missing in settings.json")
jq -e '.hooks.PreToolUse[]?.hooks[]?.command
       | select(. == "/opt/foreign-guard.sh")' \
  "$TESTHOME/.claude/settings.json" >/dev/null 2>&1 \
  || FAILS+=("foreign hook entry was lost")
jq -e '.permissions.allow | index("Bash(ls:*)")' \
  "$TESTHOME/.claude/settings.json" >/dev/null 2>&1 \
  || FAILS+=("foreign permissions were lost")

# Миграция матчера: устаревшая регистрация отметки критика на «Task» снята, новая на
# «Task|Agent» одна, чужая команда в той же группе цела.
n_mark="$(jq '[.hooks.PostToolUse[]?.hooks[]?.command
       | select(endswith("universal-mark-plan-critic.sh"))] | length' \
  "$TESTHOME/.claude/settings.json")"
[[ "$n_mark" == "1" ]] || FAILS+=("mark-plan-critic registered $n_mark times, expected 1")
jq -e '.hooks.PostToolUse[]? | select((.matcher // "") == "Task|Agent")
       | .hooks[]? | select(endswith("universal-mark-plan-critic.sh") | not) | empty,
       (.hooks | length)' "$TESTHOME/.claude/settings.json" >/dev/null 2>&1
jq -e '.hooks.PostToolUse[]?.hooks[]?.command
       | select(. == "/opt/foreign-on-task.sh")' \
  "$TESTHOME/.claude/settings.json" >/dev/null 2>&1 \
  || FAILS+=("foreign hook in the Task group was lost")

out2="$(HOME="$TESTHOME" INSTALL_ALLOW_WORKTREE=1 bash "$REPO/install.sh" 2>&1)" \
  || FAILS+=("second run exited non-zero: $out2")
grep -q 'no changes needed' <<<"$out2" || FAILS+=("second run changed settings (not idempotent)")

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

if [[ ${#FAILS[@]} -gt 0 ]]; then
  echo "install-smoke: FAIL"
  for f in "${FAILS[@]}"; do echo "  - $f"; done
  exit 1
fi
echo "install-smoke: OK"
