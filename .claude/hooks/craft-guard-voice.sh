#!/usr/bin/env bash
# PreToolUse guard: block a Craft write whose text carries the agent's own
# kitchen vocabulary — the system-zone register leaking into Vlad's spheres.
#
# Rule (Craft, "Как пишет Влад"): entities in the spheres are written in Vlad's
# voice; the agent's system terms are not carried over. The lexis list is
# deliberately NARROW — only phrases that are always kitchen, never the subject
# of a note. Broad words (гейт, периметр, приёмка, инвариант) are legitimate in
# the sphere about the agent itself, so they are NOT machine-checked; the
# written rule covers them.
#
# Exemptions: a command touching one of the zones in voice-exempt-roots.txt
# (system-zone docs, the sphere about the agent) passes — there this vocabulary
# IS the subject. An unrecognised target passes too: a false refusal costs more
# than a miss, and the written rule still applies.
set -u

input="$(cat)"
tool="$(jq -r '.tool_name // ""' <<<"$input" 2>/dev/null)" || exit 0
[[ "$tool" =~ __craft_write$ ]] || exit 0

cmd="$(jq -r '.tool_input.command // ""' <<<"$input" 2>/dev/null)" || exit 0
[[ -n "$cmd" ]] || exit 0

HOOK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LEXIS="${CRAFT_VOICE_LEXIS:-$HOOK_DIR/voice-stop-lexis.txt}"
EXEMPT="${CRAFT_VOICE_EXEMPT:-$HOOK_DIR/voice-exempt-roots.txt}"

if [[ -f "$EXEMPT" ]]; then
  while IFS= read -r id; do
    id="${id%%#*}"; id="$(tr -d '[:space:]' <<<"$id")"
    [[ -z "$id" ]] && continue
    grep -qiF -- "$id" <<<"$cmd" && exit 0
  done < "$EXEMPT"
fi

[[ -f "$LEXIS" ]] || exit 0

hit=""
while IFS= read -r phrase; do
  phrase="${phrase%%#*}"
  phrase="$(sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//' <<<"$phrase")"
  [[ -z "$phrase" ]] && continue
  if grep -qiF -- "$phrase" <<<"$cmd"; then hit="$phrase"; break; fi
done < "$LEXIS"

[[ -n "$hit" ]] || exit 0

reason="Заблокировано правилом языка: в записываемом тексте кухонная лексика агента — «${hit}». Сущности сфер ведутся голосом Влада: простые короткие фразы, от первого лица, без терминов системной зоны. Перепиши по памятке «Как пишет Влад» (подстраница правил записи) и повтори запись. Слово и правда предмет самой записи — тогда её место в зоне из voice-exempt-roots.txt."
jq -cn --arg r "$reason" '{hookSpecificOutput:{hookEventName:"PreToolUse",permissionDecision:"deny",permissionDecisionReason:$r}}'
exit 0
