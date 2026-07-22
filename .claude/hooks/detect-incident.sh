#!/usr/bin/env bash
# UserPromptSubmit hook: when Влад's message carries an incident signal, inject a
# hard directive plus the live body of "Разбор инцидента" (cached by
# inject-craft-incident.sh at session start) straight into context — so the
# incident-review doc is in front of the agent at the moment of the signal,
# instead of relying on the agent choosing to open it.
#
# Markers live in incident-markers.txt (one extended-regex per line, '#' comments
# ignored) so the set grows without touching this logic. The incident-review
# procedure itself mandates adding a missed marker there whenever a real incident
# slips past the detector.
#
# Fail quiet (exit 0, no stdout) on anything unexpected: a broken detector must
# never block Влад's message from going through.
set -u
# POSIX locale (the container default) makes grep -i skip case-folding for
# Cyrillic, so capitalised markers («Инцидент», «Сломал») would be missed —
# only lowercase matched. Force a UTF-8 locale so -i works on Cyrillic.
export LC_ALL=C.UTF-8
DIR="$(cd "$(dirname "$0")" && pwd)"
MARKERS="$DIR/incident-markers.txt"
CACHE="${CLAUDE_PROJECT_DIR:-$(cd "$DIR/../.." && pwd)}/.claude/craft-incident-context.md"

input="$(cat)"
prompt="$(jq -r '.prompt // ""' <<<"$input" 2>/dev/null)" || exit 0
[[ -z "$prompt" ]] && exit 0
[[ -f "$MARKERS" ]] || exit 0

# Match the message against any marker (case-insensitive, extended regex).
matched=0
while IFS= read -r pat || [[ -n "$pat" ]]; do
  [[ -z "$pat" || "$pat" == \#* ]] && continue
  if grep -qiE -- "$pat" <<<"$prompt"; then matched=1; break; fi
done < "$MARKERS"
[[ "$matched" -eq 0 ]] && exit 0

echo "⚠️ СИГНАЛ ИНЦИДЕНТА в сообщении Влада. Первым действием веди разбор строго по «⚙️ SKILL: Разбор инцидента» (тело ниже). Причину, урок или правку не формулируй, пока не свернул к нему. Вариант, нарушающий уже записанное правило, не предлагается; нарушено записанное правило → нужен ДРУГОЙ рычаг, не копия правила рядом; без рычага инцидент не закрыт."
echo
if [[ -f "$CACHE" ]]; then
  echo "----- живое тело «Разбор инцидента» (кэш SessionStart) -----"
  cat "$CACHE"
else
  echo "(локальный кэш craft-incident-context.md отсутствует — открой док через MCP: blocks get cbb1ba47-c05b-60b5-f86e-16c05b77bb4f --depth -1)"
fi
exit 0
