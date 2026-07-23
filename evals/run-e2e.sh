#!/usr/bin/env bash
# Level-2 (end-to-end) eval for the «Продукты» skill.
#
# A headless `claude -p` runs the real produkty-pokupki skill and its writes are
# APPLIED for real — but through mock-craft-sandbox.js, which redirects every read
# of the real «Продукты» page (395450FC-…) to a disposable SANDBOX subtree and
# hard-guards every write so the real page can NEVER be touched. Each case is
# verified by GET-ing the sandbox product and asserting its taskInfo.state actually
# changed. Sandboxes live under the "Для тестов" doc and are torn down on exit.
#
# Isolation, in layers:
#   1. guard pre-flight self-check — proves a real-page write throws with ZERO REST
#      calls (SELFCHECK mode blocks all network); the run ABORTS if the guard is broken.
#   2. read redirect (fail-closed) — the agent only ever sees sandbox ids, so it can
#      only ever write sandbox ids.
#   3. per-write guards in the mock — page-id guard + sandbox-membership guard.
#   4. post-run scan — every applied-write body is checked for the real page id.
#
# Determinism: each case gets its OWN fresh sandbox (created, run, verified, deleted),
# so even warm-spare session carryover between sequential `claude -p` calls cannot let
# one case's write satisfy another's — a carried-over id belongs to a torn-down sandbox
# and the membership guard rejects it. A trap deletes every sandbox on any exit.
#
# Usage: run-e2e.sh [model]   (default: haiku)
set -u
cd "$(dirname "$0")/.." || exit 1

export CRAFT_AUTONOMOUS=1   # bypass the plan-gate hook — headless, pre-authorised
base="${CRAFT_API_BASE%/}"
REAL_PAGE_NORM="395450fc468e4ef68267bc158a4e2ebc"
PARENT="E3D227EA-D686-48A0-8A41-4E8C99597072"   # doc "Для тестов" (sandbox home)
NODE="/opt/node22/bin/node"
CFG="./evals/mcp-config-sandbox.json"
CASES="evals/e2e-cases.jsonl"
LOG="/tmp/craft-e2e-write.log"          # must match mcp-config-sandbox.json env
ALLLOG="/tmp/craft-e2e-write.all.log"   # cumulative, for the final real-page scan
MODEL="${1:-claude-haiku-4-5-20251001}"

[[ -n "$base" ]] || { echo "CRAFT_API_BASE unset — abort"; exit 1; }
: > "$ALLLOG"

# Every sandbox root we create, so the trap can guarantee removal on any exit.
SANDBOXES=()
teardown() {
  local code=$? hc r
  ((${#SANDBOXES[@]})) || return $code
  echo "== teardown =="
  for r in "${SANDBOXES[@]}"; do
    [[ -n "$r" ]] || continue
    curl -sS --max-time 60 -X DELETE "$base/blocks" -H 'Content-Type: application/json' -H 'Accept: application/json' \
      --data "$(jq -n --arg r "$r" '{blockIds:[$r]}')" >/dev/null 2>&1
    hc="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 60 -H 'Accept: application/json' "$base/blocks?id=$r&maxDepth=-1")"
    if [[ "$hc" == "404" ]]; then echo "  sandbox $r → deleted, GET 404 (gone)"; else echo "  WARNING: sandbox $r still present (HTTP $hc)"; fi
  done
  return $code
}
trap teardown EXIT INT TERM

# ---------------------------------------------------------------------------
# 1. GUARD PRE-FLIGHT SELF-CHECK — abort the whole run if the guard is broken.
#    Runs in SELFCHECK mode: no network is possible, so this can never touch the
#    live base regardless of guard correctness.
# ---------------------------------------------------------------------------
echo "== [1] guard pre-flight self-check =="
CRAFT_SELFCHECK=1 CRAFT_SANDBOX_ROOT="deadbeef-0000-0000-0000-000000000000" \
  "$NODE" -e '
  const m = require("./evals/mock-craft-sandbox.js");
  function must(c, msg){ if(!c){ console.error("  FAIL: "+msg); process.exit(1); } }

  m.resetRestCalls(); let t=false, msg="";
  try { m.applyWriteCommand("tasks update --task 395450FC-468E-4EF6-8267-BC158A4E2EBC --state done"); }
  catch(e){ t=true; msg=e.message; }
  console.log("  real-page write → threw="+t+", restCalls="+m.getRestCalls());
  console.log("    "+msg);
  must(t, "guard did not throw on real page"); must(m.getRestCalls()===0, "REST issued despite guard");

  m.resetRestCalls(); t=false;
  try { m.applyWriteCommand("tasks update --task 395450fc468e4ef68267bc158a4e2ebc --state canceled"); } catch(e){ t=true; }
  must(t && m.getRestCalls()===0, "normalized real-page write not blocked with zero REST");

  m.resetRestCalls(); t=false;
  try { m.applyWriteCommand("blocks add --id 395450FC-468E-4EF6-8267-BC158A4E2EBC --json {\"type\":\"text\",\"markdown\":\"- [ ] X\"}"); } catch(e){ t=true; }
  must(t && m.getRestCalls()===0, "add-into-real-page not blocked with zero REST");

  m.resetRestCalls();
  try { m.applyWriteCommand("tasks update --task aaaaaaaa-1111-2222-3333-444444444444 --state done"); } catch(e){}
  must(m.getRestCalls()>=1, "REST counter never increments — self-check is vacuous");

  console.log("  guard self-check: PASS (real-page writes refused with zero REST; counter live)");
' || { echo "!! GUARD SELF-CHECK FAILED — aborting. No sandbox created, real page untouched."; exit 1; }

# ---------------------------------------------------------------------------
# helpers — build a fresh sandbox mirroring the Продукты shape (category subpage
# holding product task-blocks) inside the "Для тестов" doc.
# ---------------------------------------------------------------------------
mk_page() { # $1 parentId  $2 title  -> new id
  curl -sS --max-time 60 -X POST "$base/blocks" -H 'Content-Type: application/json' -H 'Accept: application/json' \
    --data "$(jq -n --arg p "$1" --arg t "$2" '{blocks:[{type:"page",markdown:$t}],position:{position:"end",pageId:$p}}')" \
    | jq -r '.items[0].id // empty'
}
mk_task() { # $1 parentPageId  $2 name  $3 state  -> new id
  curl -sS --max-time 60 -X POST "$base/blocks" -H 'Content-Type: application/json' -H 'Accept: application/json' \
    --data "$(jq -n --arg p "$1" --arg m "- [ ] $2" --arg s "$3" '{blocks:[{type:"text",markdown:$m,listStyle:"task",taskInfo:{state:$s}}],position:{position:"end",pageId:$p}}')" \
    | jq -r '.items[0].id // empty'
}

# ---------------------------------------------------------------------------
# 2+3. Per case: fresh sandbox → real agent run → verify actual sandbox state → drop.
# ---------------------------------------------------------------------------
echo "== [2+3] run cases (model=$MODEL), one fresh sandbox each =="
printf '%-28s %-9s %-6s %s\n' "PROMPT" "WANT" "RES" "DETAIL"
printf -- '-------------------------------------------------------------------------\n'
total=0; passc=0
while IFS= read -r line; do
  [[ -z "$line" ]] && continue
  prompt="$(jq -r '.prompt' <<<"$line")"
  item="$(jq -r '.item'   <<<"$line")"
  from="$(jq -r '.from'    <<<"$line")"
  want="$(jq -r '.state'  <<<"$line")"

  # fresh sandbox: root › category › target product (in its "from" state) + a decoy
  root="$(mk_page "$PARENT" "🧪 E2E — $item (auto, disposable)")"
  [[ -n "$root" ]] || { printf '%-28s %-9s %-6s %s\n' "${prompt:0:27}" "$want" "ERR" "sandbox create failed"; continue; }
  SANDBOXES+=("$root")
  cat_id="$(mk_page "$root" "Продукты-тест")"
  id="$(mk_task "$cat_id" "$item" "$from")"
  decoy="$(mk_task "$cat_id" "Батон" "todo")"   # never targeted; present so search must discriminate
  [[ -n "$id" ]] || { printf '%-28s %-9s %-6s %s\n' "${prompt:0:27}" "$want" "ERR" "seed failed"; continue; }

  # Warm-spare session carryover between back-to-back `claude -p` runs can make the
  # agent occasionally emit no write. Retry once, after a cooldown, if the log is empty.
  for attempt in 1 2; do
    : > "$LOG"
    env -u CLAUDE_CODE_SESSION_ID -u CLAUDE_CODE_CHILD_SESSION -u CLAUDE_PID \
        -u CLAUDE_CODE_REMOTE_SESSION_ID -u CLAUDE_CODE_WORKER_EPOCH \
        CRAFT_SANDBOX_ROOT="$root" \
      timeout 360 claude -p "$prompt" \
      --mcp-config "$CFG" --strict-mcp-config \
      --model "$MODEL" \
      --allowedTools Skill 'mcp__Craft__*' \
      --disallowedTools Bash Read \
      --output-format json >/dev/null 2>&1
    [[ -s "$LOG" ]] && break
    [[ "$attempt" -eq 1 ]] && sleep 5
  done
  cat "$LOG" >> "$ALLLOG" 2>/dev/null

  # PRIMARY (L2) assertion: the sandbox product actually reached the wanted state.
  got="$(curl -sS --fail --max-time 60 -H 'Accept: application/json' "$base/blocks?id=$id&maxDepth=-1" | jq -r '.taskInfo.state // "?"')"
  cmds="$(jq -r '.command // empty' "$LOG" 2>/dev/null)"
  bodies="$(jq -r '.body|tostring' "$LOG" 2>/dev/null | tr -d '-' | tr 'A-Z' 'a-z')"

  ok=1; d=""
  [[ "$got" == "$want" ]] || { ok=0; d="state=$got≠$want(from $from)"; }
  grep -qiE 'tasks +update' <<<"$cmds"        || { ok=0; d="${d:+$d,}no tasks update"; }
  grep -qi -- "$id" <<<"$cmds"                 || { ok=0; d="${d:+$d,}target id absent"; }
  grep -q -- "$REAL_PAGE_NORM" <<<"$bodies"    && { ok=0; d="${d:+$d,}REAL PAGE!"; }
  grep -qE -- '(^|[[:space:]])--markdown([[:space:]]|=)' <<<"$cmds" && { ok=0; d="${d:+$d,}--markdown!"; }

  total=$((total+1))
  if [[ $ok -eq 1 ]]; then passc=$((passc+1)); r="PASS"; d="sandbox $item: $from → $got"; else r="FAIL"; fi
  printf '%-28s %-9s %-6s %s\n' "${prompt:0:27}" "$want" "$r" "$d"

  # drop this case's sandbox immediately (trap is the backstop)
  curl -sS --max-time 60 -X DELETE "$base/blocks" -H 'Content-Type: application/json' -H 'Accept: application/json' \
    --data "$(jq -n --arg r "$root" '{blockIds:[$r]}')" >/dev/null 2>&1
  sleep 3   # inter-case cooldown — reduce warm-spare carryover to the next run
done < "$CASES"
printf -- '-------------------------------------------------------------------------\n'
echo "E2E TOTAL: $passc/$total"

# ---------------------------------------------------------------------------
# 4. Post-run isolation audit: NOT ONE applied write targeted the real page.
# ---------------------------------------------------------------------------
echo "== [4] isolation audit =="
napplied="$(grep -c . "$ALLLOG" 2>/dev/null || echo 0)"
bad="$(jq -r '.body|tostring' "$ALLLOG" 2>/dev/null | tr -d '-' | tr 'A-Z' 'a-z' | grep -c "$REAL_PAGE_NORM" || true)"
echo "  applied writes logged: $napplied"
if [[ "${bad:-0}" -eq 0 ]]; then
  echo "  real-page writes in applied log: 0 — every write targeted a sandbox only. OK"
else
  echo "  real-page writes in applied log: $bad — ISOLATION BREACH"
fi

# teardown runs here via trap (idempotent — per-case deletes already ran)
[[ "$passc" -eq "$total" && "$total" -gt 0 && "${bad:-0}" -eq 0 ]] && exit 0 || exit 1
