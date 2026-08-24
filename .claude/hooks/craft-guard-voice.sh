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
# Two exemptions, and the second is the load-bearing one. A command touching a
# zone from voice-exempt-roots.txt passes — there this vocabulary IS the subject.
# But that root ID only appears when the write targets the doc itself: an update
# of a paragraph deep inside the router carries the CHILD block ID and nothing
# else, so the root check silently misses it. Resolving ancestry would need a
# subtree cache (Craft has no parent lookup in the command), so the guard instead
# narrows WHAT it inspects: it fires only on text shaped like a sphere entity —
# a task checkbox or a type tag. Rules and skill docs are plain paragraphs and
# pass untouched. An unrecognised target passes too: a false refusal costs more
# than a miss, and the written rule still covers the gap.
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

# Shape check: only a sphere entity is inspected — a task checkbox or a type
# tag. This is what keeps legitimate system-zone edits (plain paragraphs of the
# router and skill docs, addressed by child block ID) out of the guard's reach.
grep -qE -- '- \[[ xX]\]|#задача|#заметка|#тема|#алгоритм' <<<"$cmd" || exit 0

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
