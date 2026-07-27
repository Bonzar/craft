#!/usr/bin/env bash
# Smoke-тест install.sh: идемпотентность и сохранность чужих настроек.
# Гоняется во временном HOME — реальный ~/.claude не трогается.
#   1. Первый прогон: симлинки созданы, регистрации в settings.json есть.
#   2. Чужой hook-блок, существовавший до установки, не затёрт.
#   3. Второй прогон: no-op (0 новых симлинков, "no changes needed").
set -u

REPO="$(cd "$(dirname "$0")/.." && pwd)"
FAILS=()

TESTHOME="$(mktemp -d)"
trap 'rm -rf "$TESTHOME"' EXIT

# Чужая запись, которую install обязан сохранить.
mkdir -p "$TESTHOME/.claude"
cat > "$TESTHOME/.claude/settings.json" <<'JSON'
{"hooks":{"PreToolUse":[{"matcher":"Bash","hooks":[{"type":"command","command":"/opt/foreign-guard.sh"}]}]},"permissions":{"allow":["Bash(ls:*)"]}}
JSON

out1="$(HOME="$TESTHOME" INSTALL_ALLOW_WORKTREE=1 bash "$REPO/install.sh" 2>&1)" \
  || FAILS+=("first run exited non-zero: $out1")

[[ -L "$TESTHOME/.claude/hooks/universal-guard-plan-gate.sh" ]] \
  || FAILS+=("plan-gate symlink missing after install")
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

out2="$(HOME="$TESTHOME" INSTALL_ALLOW_WORKTREE=1 bash "$REPO/install.sh" 2>&1)" \
  || FAILS+=("second run exited non-zero: $out2")
grep -q 'hooks: 0 new symlink' <<<"$out2" || FAILS+=("second run created hook symlinks (not idempotent)")
grep -q 'no changes needed' <<<"$out2" || FAILS+=("second run changed settings (not idempotent)")

if [[ ${#FAILS[@]} -gt 0 ]]; then
  echo "install-smoke: FAIL"
  for f in "${FAILS[@]}"; do echo "  - $f"; done
  exit 1
fi
echo "install-smoke: OK"
