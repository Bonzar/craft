#!/usr/bin/env bash
# Regression tests for the Claude Code bash hooks under .claude/hooks/.
#
# Each case (tests/hooks/*.jsonl) is one JSON object per line:
#   {"name","hook","input":{…event JSON…},"expect":"deny|allow|ask|inject|silent|contains:<substr>"}
# The runner feeds `input` to the target hook on stdin and checks the outcome:
#   deny   — stdout is a PreToolUse JSON with permissionDecision "deny"
#   allow  — the hook did NOT deny/ask (guards stay silent on pass)
#   ask    — stdout is a PreToolUse JSON with permissionDecision "ask"
#   inject — stdout carries the incident directive (detect-incident matched)
#   silent — stdout is empty (no marker matched / nothing injected)
#   contains:<substr> — stdout contains the substring
# Exit 0 iff every case passes AND every hook-outcome is covered; else exit 1.
#
# External hook sets (e.g. the local-only yandex layer in ~/.claude, not in
# git): EXTRA_HOOKS_DIR + EXTRA_CASES_DIR run additional case files whose
# `hook` field resolves to $EXTRA_HOOKS_DIR/<hook>.sh (no REQUIRED coverage):
#   EXTRA_HOOKS_DIR=~/.claude/hooks EXTRA_CASES_DIR=~/.claude/tests/hooks bash tests/run.sh
#
# Dependencies: bash + jq only. Hooks are invoked via `bash <hook>` so they need
# not be executable. Run from anywhere: `bash tests/run.sh`.
set -u

# UTF-8-локаль обязательна: часть дефектов видна ТОЛЬКО в ней. В bash подстановка
# `$var` вплотную к не-ASCII символу в UTF-8 читается как имя вместе с этим
# символом и валит скрипт по set -u, а в локали C та же строка работает. Из-за
# этого сломанный guard-plan-delta прошёл ревью: CI был зелёный, а на рабочей
# машине гвард молча падал. Локаль та же, что у хуков (см. universal-detect-incident.sh).
export LC_ALL=C.UTF-8

REPO="$(cd "$(dirname "$0")/.." && pwd)"
HOOKS="$REPO/.claude/hooks"
CASES_DIR="$REPO/tests/hooks"

command -v jq >/dev/null 2>&1 || { echo "ERROR: jq is required" >&2; exit 2; }

declare -A SCRIPT=(
  [guard-craft-markdown]="$HOOKS/craft-guard-markdown.sh"
  [guard-plan-hygiene]="$HOOKS/craft-guard-plan-hygiene.sh"
  [detect-incident]="$HOOKS/universal-detect-incident.sh"
  [guard-plan-gate]="$HOOKS/universal-guard-plan-gate.sh"
  [plan-gate-approve]="$HOOKS/universal-plan-gate-approve.sh"
  [plan-gate-reset]="$HOOKS/universal-plan-gate-reset.sh"
  [sleep-waiter-guard]="$HOOKS/universal-sleep-waiter-guard.sh"
  [config-protection]="$HOOKS/universal-config-protection.sh"
  [block-no-verify]="$HOOKS/universal-block-no-verify.sh"
  [fact-gate]="$HOOKS/universal-fact-gate.sh"
  [stop-routine-facts]="$HOOKS/universal-stop-routine-facts.sh"
  [guard-plan-critic]="$HOOKS/universal-guard-plan-critic.sh"
  [guard-plan-delta]="$HOOKS/universal-guard-plan-delta.sh"
  [guard-plan-service-turn]="$HOOKS/universal-guard-plan-service-turn.sh"
  [mark-plan-critic]="$HOOKS/universal-mark-plan-critic.sh"
  [mark-plan-file]="$HOOKS/universal-mark-plan-file.sh"
  [stop-incident-closure]="$HOOKS/universal-stop-incident-closure.sh"
  [detect-incident-arm]="$HOOKS/universal-detect-incident.sh"
)

is_deny() { jq -e '.hookSpecificOutput.permissionDecision=="deny"' >/dev/null 2>&1 <<<"$1"; }
is_ask()  { jq -e '.hookSpecificOutput.permissionDecision=="ask"'  >/dev/null 2>&1 <<<"$1"; }
is_block(){ jq -e '.decision=="block"' >/dev/null 2>&1 <<<"$1"; }
trim() { local s="$1"; printf '%s' "${s//[$' \t\n\r']/}"; }

total=0; pass=0; fail=0
declare -A covered
fails=()

shopt -s nullglob
files=("$CASES_DIR"/*.jsonl)
[[ -n "${EXTRA_CASES_DIR:-}" ]] && files+=("$EXTRA_CASES_DIR"/*.jsonl)
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
    # {TESTS_DIR} подставляется и в input (file_path-кейсам нужны фикстуры).
    input="$(jq -c '.input' <<<"$line")"; input="${input//\{TESTS_DIR\}/$CASES_DIR}"
    script="${SCRIPT[$hook]:-}"
    # Unknown key → resolve by filename convention (repo hooks, then the
    # external set for local-only hooks like the yandex layer).
    [[ -z "$script" && -f "$HOOKS/$hook.sh" ]] && script="$HOOKS/$hook.sh"
    [[ -z "$script" && -n "${EXTRA_HOOKS_DIR:-}" && -f "$EXTRA_HOOKS_DIR/$hook.sh" ]] && script="$EXTRA_HOOKS_DIR/$hook.sh"
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
    # Hermetic side-effect paths: hooks with write side effects (observe
    # buffer, fact-gate markers, routine-facts reminder) must never touch the
    # LIVE session state during a test run.
    obsbuf="$(mktemp -u "${TMPDIR:-/tmp}/observe-buffer-test.XXXXXX")"
    fgdir="$(mktemp -d "${TMPDIR:-/tmp}/fact-gate-test.XXXXXX")"
    rfmark="$(mktemp -u "${TMPDIR:-/tmp}/routine-facts-test.XXXXXX")"
    planpath="$(mktemp -u "${TMPDIR:-/tmp}/plan-file-test.XXXXXX")"
    criticmark="$(mktemp -u "${TMPDIR:-/tmp}/plan-critic-test.XXXXXX")"
    deltastore="$(mktemp -u "${TMPDIR:-/tmp}/plan-delta-test.XXXXXX")"
    icmark="$(mktemp -u "${TMPDIR:-/tmp}/incident-closure-test.XXXXXX").armed"
    serviceturn="$(mktemp -u "${TMPDIR:-/tmp}/plan-service-turn-test.XXXXXX")"
    criticpend="$(mktemp -u "${TMPDIR:-/tmp}/plan-critic-pending-test.XXXXXX")"
    caseenv=("CRAFT_PLAN_GATE_MARKER=$marker" "OBSERVE_BUFFER=$obsbuf"
             "FACT_GATE_STATE_DIR=$fgdir" "ROUTINE_FACTS_MARKER=$rfmark"
             "CRAFT_PLAN_FILE_MARKER=$planpath" "CRAFT_PLAN_CRITIC_MARKER=$criticmark"
             "CRAFT_PLAN_DELTA_STORE=$deltastore" "INCIDENT_CLOSURE_MARKER=$icmark"
             "CRAFT_SERVICE_TURN_MARKER=$serviceturn"
             "CRAFT_PLAN_CRITIC_PENDING=$criticpend")
    # `arm: true` — предусловие «маркер взведён»: файл, путь которого хук берёт
    # из env, создаётся до прогона (взводом в жизни занимается другой хук).
    [[ "$(jq -r '.arm // false' <<<"$line")" == "true" ]] && : > "$icmark"
    # Env values may reference fixture files via the {TESTS_DIR} placeholder —
    # cases are static JSONL and cannot know the checkout's absolute path.
    while IFS=$'\t' read -r k v; do
      [[ -n "$k" ]] && caseenv+=("$k=${v//\{TESTS_DIR\}/$CASES_DIR}")
    done \
      < <(jq -r '(.env // {}) | to_entries[] | "\(.key)\t\(.value)"' <<<"$line")
    # Setup-хукам по умолчанию подаётся ТОТ ЖЕ input и то же окружение, что целевому:
    # хуки без чтения stdin (plan-gate-approve/reset) его игнорируют, а хуки-метки на
    # нём проверяемы — событие не их природы метку ставить не должно. Кейс может задать
    # подготовке своё событие (`setup_input`) и свои переменные (`setup_env`) — это
    # нужно связкам, где подготовка и цель обязаны отличаться (напр. запись в накопитель
    # одним планом и проверка другим).
    # setup_input — одиночное событие всем шагам подготовки либо СПИСОК: свой элемент
    # каждому шагу. Список нужен связкам, где один и тот же хук зовётся дважды разными
    # событиями (расписка о запуске, затем уведомление о завершении).
    setup_is_list="$(jq -r '(.setup_input // null) | type == "array"' <<<"$line")"
    setup_input="$(jq -c 'if ((.setup_input // null) | type) == "array" then empty else (.setup_input // empty) end' <<<"$line")"
    setup_input="${setup_input//\{TESTS_DIR\}/$CASES_DIR}"
    setupenv=("${caseenv[@]}")
    while IFS=$'\t' read -r k v; do
      [[ -n "$k" ]] && setupenv+=("$k=${v//\{TESTS_DIR\}/$CASES_DIR}")
    done \
      < <(jq -r '(.setup_env // {}) | to_entries[] | "\(.key)\t\(.value)"' <<<"$line")
    setup_i=0
    while IFS= read -r sh; do
      [[ -z "$sh" ]] && continue
      step="$setup_input"
      if [[ "$setup_is_list" == "true" ]]; then
        step="$(jq -c --argjson i "$setup_i" '.setup_input[$i] // empty' <<<"$line")"
        step="${step//\{TESTS_DIR\}/$CASES_DIR}"
      fi
      printf '%s' "${step:-$input}" | env "${setupenv[@]}" bash "${SCRIPT[$sh]:-/nonexistent}" >/dev/null 2>&1
      setup_i=$((setup_i+1))
    done < <(jq -r '(.setup // [])[]' <<<"$line")
    # `repeat: N` — feed the SAME input N times (deny-once / remind-once hooks:
    # the assertion is on the LAST invocation's output).
    rpt="$(jq -r '.repeat // 1' <<<"$line")"
    out=""
    for ((r_i=0; r_i<rpt; r_i++)); do
      out="$(printf '%s' "$input" | env "${caseenv[@]}" bash "$script" 2>/dev/null)"
    done
    rm -f "$marker" "$obsbuf" "$rfmark" "$planpath" "$criticmark" "$deltastore" \
          "$icmark" "${icmark%.armed}.reminded" "$serviceturn" "$criticpend"; rm -rf "$fgdir"
    ok=0
    case "$expect" in
      deny)   is_deny "$out" && ok=1 ;;
      allow)  { is_deny "$out" || is_ask "$out" || is_block "$out"; } || ok=1 ;;
      ask)    is_ask "$out" && ok=1 ;;
      block)  is_block "$out" && ok=1 ;;
      inject) grep -q 'СИГНАЛ ИНЦИДЕНТА' <<<"$out" && ok=1 ;;
      silent) [[ -z "$(trim "$out")" ]] && ok=1 ;;
      contains:*) grep -qF -- "${expect#contains:}" <<<"$out" && ok=1 ;;
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
  "guard-plan-gate:deny"      "guard-plan-gate:allow"
  "sleep-waiter-guard:deny"   "sleep-waiter-guard:allow"
  "config-protection:deny"    "config-protection:allow"
  "block-no-verify:deny"      "block-no-verify:allow"
  "fact-gate:deny"            "fact-gate:allow"
  "stop-routine-facts:block"  "stop-routine-facts:silent"
  "guard-plan-critic:deny"    "guard-plan-critic:allow"
  "guard-plan-delta:deny"     "guard-plan-delta:allow"     "guard-plan-delta:silent"
  "guard-plan-service-turn:deny" "guard-plan-service-turn:allow"
  "mark-plan-critic:silent"   "mark-plan-file:silent"
  "stop-incident-closure:block" "stop-incident-closure:silent"
)
missing=()
for k in "${REQUIRED[@]}"; do [[ -n "${covered[$k]:-}" ]] || missing+=("$k"); done

# Smoke: every hook command registered in .claude/settings.json must exist and
# be executable — catches path regressions from hook renames/moves.
smoke=()
while IFS= read -r c; do
  [[ -z "$c" ]] && continue
  p="${c//\$CLAUDE_PROJECT_DIR/$REPO}"
  if [[ ! -f "$p" ]]; then smoke+=("missing hook file: $p")
  elif [[ ! -x "$p" ]]; then smoke+=("hook not executable: $p")
  fi
done < <(jq -r '.hooks[]?[]?.hooks[]?.command // empty' "$REPO/.claude/settings.json" 2>/dev/null)

# Reverse smoke: every hook FILE under .claude/hooks must be registered in at
# least one contour (repo settings.json or install.sh) — an unregistered hook
# lies dead while looking installed. Whitelist: sourced helpers that are not
# hooks themselves.
REVERSE_WHITELIST=("_load-env.sh")
reverse_orphans() {  # args: file paths; echoes orphan basenames
  local reg f b w skip
  reg="$(jq -r '.hooks[]?[]?.hooks[]?.command // empty' "$REPO/.claude/settings.json" 2>/dev/null; cat "$REPO/install.sh" 2>/dev/null)"
  for f in "$@"; do
    b="$(basename "$f")"
    skip=0
    for w in "${REVERSE_WHITELIST[@]}"; do [[ "$b" == "$w" ]] && skip=1; done
    [[ $skip -eq 1 ]] && continue
    grep -qF "$b" <<<"$reg" || echo "$b"
  done
}
# Self-test: the red path must be provably reachable — a fictitious orphan
# must be caught, else the check itself has silently broken.
if [[ "$(reverse_orphans "$HOOKS/zz-selftest-orphan.sh")" != "zz-selftest-orphan.sh" ]]; then
  smoke+=("reverse-smoke self-test failed: fictitious orphan not caught")
fi
while IFS= read -r b; do
  [[ -n "$b" ]] && smoke+=("orphan hook (not registered in settings.json or install.sh): $b")
done < <(reverse_orphans "$HOOKS"/*.sh)

# Static check: a `$var` substitution glued to a non-ASCII character is a bug.
# In a UTF-8 locale bash reads the name together with that character, so the
# script dies on set -u — and only there: in the C locale the same
# line works, which is why a broken guard sat in main with a green CI. Braces
# (`${var}`) end the name explicitly and are the fix.
# The prior run in the UTF-8 locale only catches lines the tests actually
# execute; this catches the rest of the tree.
utf8_glue() {  # args: files; echoes "path:line" for each offending line
  grep -nHE '\$[A-Za-z_][A-Za-z0-9_]*[^ -~]' "$@" 2>/dev/null | cut -d: -f1,2
}
# Self-test: the red path must be provably reachable. The samples are assembled
# from parts so this file itself carries no literal defect for the check to find.
D='$'; Q='«'; QQ='»'
selftest_file="$(mktemp)"
printf 'x="%s%st%s "\n' "$Q" "$D" "$QQ" > "$selftest_file"
[[ -n "$(utf8_glue "$selftest_file")" ]] \
  || smoke+=("utf8-glue self-test failed: planted defect not caught")
# The negative side: braces and an ASCII quote after the name are both legal.
printf 'a="%s%s{t}%s "\nb="%sname"\n' "$Q" "$D" "$QQ" "$D" > "$selftest_file"
[[ -z "$(utf8_glue "$selftest_file")" ]] \
  || smoke+=("utf8-glue self-test failed: legal forms flagged: $(utf8_glue "$selftest_file")")
rm -f "$selftest_file"
while IFS= read -r hit; do
  [[ -n "$hit" ]] && smoke+=("\$var glued to a non-ASCII char (use \${var}): $hit")
done < <(utf8_glue "$HOOKS"/*.sh "$REPO"/tests/*.sh "$REPO"/evals/*.sh "$REPO"/evals/lib/*.sh)

printf -- '---------------------------------------------------------------------------\n'
if [[ ${#fails[@]} -gt 0 ]]; then
  echo "Failures:"; for m in "${fails[@]}"; do echo "  - $m"; done
fi
if [[ ${#missing[@]} -gt 0 ]]; then
  echo "Uncovered outcomes (each hook must cover each outcome):"
  for m in "${missing[@]}"; do echo "  - $m"; done
fi
if [[ ${#smoke[@]} -gt 0 ]]; then
  echo "Settings smoke failures:"
  for m in "${smoke[@]}"; do echo "  - $m"; done
fi
echo "TOTAL: $pass/$total passed; $fail failed; uncovered outcomes: ${#missing[@]}; settings smoke: ${#smoke[@]}"
[[ $fail -eq 0 && ${#missing[@]} -eq 0 && ${#smoke[@]} -eq 0 ]]
