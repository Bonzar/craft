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

# Регистрация одна на событие — диспетчер, и ведёт она в чекаут репы, а не в
# ~/.claude/hooks. Состав хуков живёт в таблице маршрутов, не в настройках.
jq -e --arg h "$REPO/.claude/hooks" '.hooks.PreToolUse[]?.hooks[]?.command
       | select(. == ($h + "/dispatch.js universal"))' \
  "$TESTHOME/.claude/settings.json" >/dev/null 2>&1 \
  || FAILS+=("dispatcher registration does not point at the checkout")
[[ -L "$TESTHOME/.claude/hooks/universal-guard-plan-gate.sh" ]] \
  && FAILS+=("install created a symlink layer again")
for ev in SessionStart UserPromptSubmit PreToolUse PostToolUse PostToolUseFailure Stop SessionEnd PreCompact; do
  jq -e --arg e "$ev" --arg h "$REPO/.claude/hooks" '.hooks[$e][]?.hooks[]?.command
         | select(. == ($h + "/dispatch.js universal"))' \
    "$TESTHOME/.claude/settings.json" >/dev/null 2>&1 \
    || FAILS+=("dispatcher not registered on $ev")
done
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

# --- 5. git-хуки репо ---------------------------------------------------------
# Чекаут без core.hooksPath получает .githooks; чекаут с чужим каталогом хуков
# остаётся при своём — подмена молча выключила бы его pre-commit и pre-push.
# Гоняется на временных чекаутах: конфиг настоящего репо не трогается.
HOOKREPO="$(mktemp -d)"
mkdir -p "$HOOKREPO/.claude/hooks"
cp "$REPO/install.sh" "$HOOKREPO/install.sh"
git -C "$HOOKREPO" init -q
out5="$(HOME="$TESTHOME" INSTALL_ALLOW_WORKTREE=1 bash "$HOOKREPO/install.sh" 2>&1)" \
  || FAILS+=("hooks run (fresh checkout) exited non-zero: $out5")
[[ "$(git -C "$HOOKREPO" config --get core.hooksPath)" == ".githooks" ]] \
  || FAILS+=("fresh checkout did not get core.hooksPath=.githooks")

git -C "$HOOKREPO" config core.hooksPath .foreign-hooks
out6="$(HOME="$TESTHOME" INSTALL_ALLOW_WORKTREE=1 bash "$HOOKREPO/install.sh" 2>&1)" \
  || FAILS+=("hooks run (foreign hooksPath) exited non-zero: $out6")
[[ "$(git -C "$HOOKREPO" config --get core.hooksPath)" == ".foreign-hooks" ]] \
  || FAILS+=("foreign core.hooksPath was overwritten")
grep -q 'оставлен как есть' <<<"$out6" || FAILS+=("foreign hooksPath kept silently, no notice printed")
rm -rf "$HOOKREPO"

if [[ ${#FAILS[@]} -gt 0 ]]; then
  echo "install-smoke: FAIL"
  for f in "${FAILS[@]}"; do echo "  - $f"; done
  exit 1
fi
echo "install-smoke: OK"
