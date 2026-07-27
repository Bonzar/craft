#!/usr/bin/env bash
# PreToolUse(Write|Edit|MultiEdit) guard: блокирует правку СУЩЕСТВУЮЩИХ конфигов
# линтеров/форматтеров. Портирован из ECC (config-protection.js): агент, у
# которого не проходит линт/формат, склонен «чинить» конфиг вместо кода —
# гейт разворачивает его обратно к коду.
#
# Покрытие (по basename файла):
#   .eslintrc*, eslint.config.*, .prettierrc*, prettier.config.*, biome.json*,
#   .stylelintrc*, stylelint.config.*, ruff.toml, .ruff.toml, .editorconfig.
# pyproject.toml НАМЕРЕННО не гейтится: там метаданные проекта вперемешку с
# конфигом линтера — блок ломал бы легитимные правки зависимостей.
#
# Создание НОВОГО конфига (файла нет на диске) — разрешено: это сетап проекта,
# не ослабление проверок. Env-байпаса (CRAFT_AUTONOMOUS и т.п.) намеренно НЕТ:
# файловые тулзы не несут команды, куда можно вписать маркер, — обход только
# через явное разрешение Влада (кнопка allow на конкретный вызов).
#
# Fail open на всём неожиданном: сломанный гейт не должен клинить работу.
set -u

# Project-уровень уступает user-уровню (install.sh) — не гейтим дважды.
if [[ -n "${CLAUDE_PROJECT_DIR:-}" && "$0" == "$CLAUDE_PROJECT_DIR"/* \
      && -e "$HOME/.claude/hooks/$(basename "$0")" ]]; then
  exit 0
fi

input="$(cat)"
fp="$(jq -r '.tool_input.file_path // ""' <<<"$input" 2>/dev/null)" || exit 0
[[ -z "$fp" ]] && exit 0

base="$(basename "$fp")"
protected=0
case "$base" in
  .eslintrc|.eslintrc.*|eslint.config.*)          protected=1 ;;
  .prettierrc|.prettierrc.*|prettier.config.*)     protected=1 ;;
  biome.json|biome.json?*)                         protected=1 ;;
  .stylelintrc|.stylelintrc.*|stylelint.config.*)  protected=1 ;;
  ruff.toml|.ruff.toml)                            protected=1 ;;
  .editorconfig)                                   protected=1 ;;
esac
[[ "$protected" -eq 0 ]] && exit 0

# Файла нет на диске → первичное создание конфига, разрешено.
[[ -e "$fp" ]] || exit 0

jq -cn --arg fp "$fp" \
  '{hookSpecificOutput:{hookEventName:"PreToolUse",permissionDecision:"deny",permissionDecisionReason:("Заблокировано: правка конфига линтера/форматтера (" + $fp + "). Чини код, а не ослабляй конфиг — падающая проверка указывает на код, правило под неё не подгоняется. Правка конфига — только по явной просьбе Влада. Влад явно попросил поменять конфиг → скажи об этом в ответе и попроси его нажать allow на этот вызов.")}}'
exit 0
