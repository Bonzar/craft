#!/usr/bin/env bash
# Regression tests for the Claude Code bash hooks under .claude/hooks/.
#
# Each case (tests/hooks/*.jsonl) is one JSON object per line:
#   {"name","hook","input":{…event JSON…},"expect":"deny|allow|inject|silent"}
# The runner feeds `input` to the target hook on stdin and checks the outcome:
#   deny   — stdout is a PreToolUse JSON with permissionDecision "deny"
#   allow  — the hook did NOT deny (guards stay silent when they let a call pass)
#   inject — stdout carries the incident directive (detect-incident matched)
#   silent — stdout is empty (no marker matched / nothing injected)
# Exit 0 iff every case passes AND every hook-outcome is covered; else exit 1.
#
# Dependencies: bash + jq only. Hooks are invoked via `bash <hook>` so they need
# not be executable. Run from anywhere: `bash tests/run.sh`.
set -u

REPO="$(cd "$(dirname "$0")/.." && pwd)"
HOOKS="$REPO/.claude/hooks"
CASES_DIR="$REPO/tests/hooks"

command -v jq >/dev/null 2>&1 || { echo "ERROR: jq is required" >&2; exit 2; }

declare -A SCRIPT=(
  [guard-craft-markdown]="$HOOKS/guard-craft-markdown.sh"
  [guard-plan-hygiene]="$HOOKS/guard-plan-hygiene.sh"
  [detect-incident]="$HOOKS/detect-incident.sh"
  [detect-autocontinue]="$HOOKS/detect-autocontinue.sh"
  [guard-plan-gate]="$HOOKS/guard-plan-gate.sh"
  [plan-gate-approve]="$HOOKS/plan-gate-approve.sh"
  [plan-gate-reset]="$HOOKS/plan-gate-reset.sh"
)

# The `inject` outcome is per-detector: each injecting hook prints a distinctive
# lead marker, and a case passes `inject` iff its hook's marker is present.
declare -A INJECT_MARKER=(
  [detect-incident]='СИГНАЛ ИНЦИДЕНТА'
  [detect-autocontinue]='ТЕХНИЧЕСКИЙ АВТО-CONTINUE'
)

is_deny() { jq -e '.hookSpecificOutput.permissionDecision=="deny"' >/dev/null 2>&1 <<<"$1"; }
trim() { local s="$1"; printf '%s' "${s//[$' \t\n\r']/}"; }

total=0; pass=0; fail=0
declare -A covered
fails=()

shopt -s nullglob
files=("$CASES_DIR"/*.jsonl)
shopt -u nullglob
[[ ${#files[@]} -gt 0 ]] || { echo "ERROR: no case files in $CASES_DIR" >&2; exit 2; }

printf '%-6s %-22s %-7s %s\n' "RESULT" "HOOK" "EXPECT" "NAME"
printf -- '---------------------------------------------------------------------------\n'

for f in "${files[@]}"; do
  lineno=0
  while IFS= read -r line || [[ -n "$line" ]]; do
    lineno=$((lineno+1))
    [[ -z "${line//[[:space:]]/}" || "$line" == \#* ]] && continue
    total=$((total+1))
    if ! name="$(jq -r '.name' <<<"$line" 2>/dev/null)"; then
      fail=$((fail+1)); fails+=("$(basename "$f"):$lineno — invalid JSON")
      printf '%-6s %-22s %-7s %s\n' "FAIL" "?" "?" "$(basename "$f"):$lineno invalid JSON"; continue
    fi
    hook="$(jq -r '.hook' <<<"$line")"
    expect="$(jq -r '.expect' <<<"$line")"
    input="$(jq -c '.input' <<<"$line")"
    script="${SCRIPT[$hook]:-}"
    covered["$hook:$expect"]=1
    if [[ -z "$script" || ! -f "$script" ]]; then
      fail=$((fail+1)); fails+=("$hook / $name — unknown hook or missing script")
      printf '%-6s %-22s %-7s %s\n' "FAIL" "$hook" "$expect" "$name"; continue
    fi
    # Per-case hermetic state for the plan-gate: a fresh marker path (absent until
    # a setup hook creates it) plus any case-declared env vars. `setup` runs the
    # named hooks first (e.g. plan-gate-approve to set the marker, plan-gate-reset
    # to clear it) under the same env, exercising the state machine for real.
    marker="$(mktemp -u "${TMPDIR:-/tmp}/plan-gate-test.XXXXXX")"
    caseenv=("CRAFT_PLAN_GATE_MARKER=$marker")
    while IFS=$'\t' read -r k v; do [[ -n "$k" ]] && caseenv+=("$k=$v"); done \
      < <(jq -r '(.env // {}) | to_entries[] | "\(.key)\t\(.value)"' <<<"$line")
    while IFS= read -r sh; do
      [[ -z "$sh" ]] && continue
      env "${caseenv[@]}" bash "${SCRIPT[$sh]:-/nonexistent}" </dev/null >/dev/null 2>&1
    done < <(jq -r '(.setup // [])[]' <<<"$line")
    out="$(printf '%s' "$input" | env "${caseenv[@]}" bash "$script" 2>/dev/null)"
    rm -f "$marker"
    ok=0
    case "$expect" in
      deny)   is_deny "$out" && ok=1 ;;
      allow)  is_deny "$out" || ok=1 ;;
      inject) im="${INJECT_MARKER[$hook]:-}"; [[ -n "$im" ]] && grep -qF "$im" <<<"$out" && ok=1 ;;
      silent) [[ -z "$(trim "$out")" ]] && ok=1 ;;
      *)      fails+=("$hook / $name — unknown expect '$expect'") ;;
    esac
    if [[ $ok -eq 1 ]]; then
      pass=$((pass+1)); printf '%-6s %-22s %-7s %s\n' "PASS" "$hook" "$expect" "$name"
    else
      fail=$((fail+1)); fails+=("$hook / $name — expected $expect, got: $(trim "${out:0:120}")")
      printf '%-6s %-22s %-7s %s\n' "FAIL" "$hook" "$expect" "$name"
    fi
  done < "$f"
done

# Coverage: each hook must exercise each of its outcomes at least once.
REQUIRED=(
  "guard-craft-markdown:deny" "guard-craft-markdown:allow"
  "guard-plan-hygiene:deny"   "guard-plan-hygiene:allow"
  "detect-incident:inject"    "detect-incident:silent"
  "detect-autocontinue:inject" "detect-autocontinue:silent"
  "guard-plan-gate:deny"      "guard-plan-gate:allow"
)
missing=()
for k in "${REQUIRED[@]}"; do [[ -n "${covered[$k]:-}" ]] || missing+=("$k"); done

printf -- '---------------------------------------------------------------------------\n'
if [[ ${#fails[@]} -gt 0 ]]; then
  echo "Failures:"; for m in "${fails[@]}"; do echo "  - $m"; done
fi
if [[ ${#missing[@]} -gt 0 ]]; then
  echo "Uncovered outcomes (each hook must cover each outcome):"
  for m in "${missing[@]}"; do echo "  - $m"; done
fi
echo "TOTAL: $pass/$total passed; $fail failed; uncovered outcomes: ${#missing[@]}"
[[ $fail -eq 0 && ${#missing[@]} -eq 0 ]]
