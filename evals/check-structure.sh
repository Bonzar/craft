#!/usr/bin/env bash
# Read-only structural guard for the real «Продукты» page. Fetches the page over
# connect-REST and checks the invariants in structure-invariants.json — a legend
# up top, categories as page-type subpages, products as task blocks with a valid
# state, products nested under categories. It does NOT hardcode the product list
# (that churns), only the shape, so it stays green as products come and go but
# fails if the page's structure breaks. Makes ZERO writes — GET only.
#
# Usage: bash evals/check-structure.sh   (needs CRAFT_API_BASE + jq)
set -u
cd "$(dirname "$0")/.." || exit 1

base="${CRAFT_API_BASE:-}"
[[ -n "$base" ]] || { echo "ERROR: CRAFT_API_BASE not set" >&2; exit 2; }
base="${base%/}"
INV="evals/structure-invariants.json"
[[ -f "$INV" ]] || { echo "ERROR: missing $INV" >&2; exit 2; }
command -v jq >/dev/null 2>&1 || { echo "ERROR: jq required" >&2; exit 2; }

page="$(jq -r '.page' "$INV")"
minCat="$(jq -r '.min_category_subpages' "$INV")"
minTask="$(jq -r '.min_task_blocks' "$INV")"
minCatTasks="$(jq -r '.min_categories_with_tasks' "$INV")"
firstN="$(jq -r '.legend_in_first_top_level' "$INV")"
legendRe="$(jq -r '.legend_keywords_regex' "$INV")"
okStates="$(jq -c '.valid_task_states' "$INV")"

J="$(mktemp)"
curl -sS --fail --max-time 90 -H 'Accept: application/json' "$base/blocks?id=$page&maxDepth=-1" > "$J" \
  || { echo "ERROR: fetch of real «Продукты» page failed" >&2; rm -f "$J"; exit 2; }

pass=0; fail=0
check() { if [[ "$2" -eq 1 ]]; then echo "PASS  $1"; pass=$((pass+1)); else echo "FAIL  $1"; fail=$((fail+1)); fi; }

# 1. Categories are page-type subpages (no taskInfo).
cats="$(jq -r '[.content[] | select(.type=="page" and (.taskInfo|not))] | length' "$J")"
check "категории — подстраницы: $cats page-type (нужно ≥ $minCat)" "$([[ $cats -ge $minCat ]] && echo 1 || echo 0)"

# 2. Products are task blocks with taskInfo.
tasks="$(jq -r '[.. | objects | select(.taskInfo)] | length' "$J")"
check "товары — задачи с taskInfo: $tasks (нужно ≥ $minTask)" "$([[ $tasks -ge $minTask ]] && echo 1 || echo 0)"

# 3. Legend sits among the first N top-level blocks.
legend="$(jq -r --argjson n "$firstN" --arg re "$legendRe" '[.content[0:$n][] | .markdown // .text.value // ""] | map(select(test($re))) | length' "$J")"
check "легенда в первых $firstN блоках: найдено $legend (нужно ≥1)" "$([[ $legend -ge 1 ]] && echo 1 || echo 0)"

# 4. Every task block has a valid state (todo/done/canceled).
bad="$(jq -r --argjson ok "$okStates" '[.. | objects | select(.taskInfo) | .taskInfo.state | select(($ok | index(.)) | not)] | length' "$J")"
check "состояния задач валидны: невалидных $bad (нужно 0)" "$([[ $bad -eq 0 ]] && echo 1 || echo 0)"

# 5. Products nest under categories (categories that contain ≥1 task in subtree).
catTasks="$(jq -r '[.content[] | select(.type=="page" and (.taskInfo|not)) | select([recurse(.content[]?) | .taskInfo // empty] | length > 0)] | length' "$J")"
check "товары вложены в категории: категорий с задачами $catTasks (нужно ≥ $minCatTasks)" "$([[ $catTasks -ge $minCatTasks ]] && echo 1 || echo 0)"

rm -f "$J"
echo "----"
echo "STRUCTURE: $pass ok, $fail failed"
[[ $fail -eq 0 ]]
