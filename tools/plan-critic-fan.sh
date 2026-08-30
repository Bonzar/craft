#!/usr/bin/env bash
# Универсальный запуск веера критиков. Граф и канонические agentId живут в
# core/workflows/plan-critic-fan.mjs; этот файл оставляет стабильный CLI и
# детерминированно ведёт отметку plan-gate.
#
#   run <план> [--dossier f] [--findings f] — полный проход + отметка;
#   args <план> [--dossier f] [--findings f] — JSON-вход core workflow;
#   mark <план> [файл-вердикта]             — совместимый ручной фолбек;
#   script                                  — тонкий Claude Workflow adapter
#                                             без копии бизнес-графа.
set -u

repo="$(cd "$(dirname "$0")/.." && pwd)"
sid="${CRAFT_AGENT_SESSION_ID:-${CODEX_THREAD_ID:-${CLAUDE_CODE_SESSION_ID:-default}}}"
marker="${CRAFT_PLAN_CRITIC_MARKER:-/tmp/plan-critic.${sid}.done}"
runs="${CRAFT_PLAN_CRITIC_RUNS:-/tmp/plan-critic.${sid}.runs}"

hash_of() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" 2>/dev/null | cut -d' ' -f1
  elif command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$1" 2>/dev/null | cut -d' ' -f1
  fi
}

make_args() {
  local plan="$1"; shift
  [[ -n "$plan" && -r "$plan" ]] || { echo "нет файла плана: ${plan:-<пусто>}" >&2; return 1; }
  local dossier="" findings=""
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --dossier) dossier="${2:-}"; shift 2 ;;
      --findings) findings="${2:-}"; shift 2 ;;
      *) echo "неизвестный флаг: $1" >&2; return 1 ;;
    esac
  done
  local n titles
  n="$(cat "$runs" 2>/dev/null)"
  if [[ "$n" =~ ^[0-9]+$ && "$n" -ge 3 ]]; then
    echo "Плато: по этому плану уже $n завершённых прогона критика — показывай план с открытым вопросом об остатке замечаний." >&2
    return 1
  fi
  titles="$(grep -E '^#+[[:space:]]*\[' "$plan" | sed -E 's/^#+[[:space:]]*//')"
  if [[ -z "$titles" ]]; then
    echo "юнитов «[тип · …]» в плане не найдено — используй одиночный plan-critic" >&2
    return 1
  fi
  jq -Rn --arg plan "$(realpath "$plan" 2>/dev/null || echo "$plan")" \
        --arg dossier "$dossier" --arg findings "$findings" \
    '{plan: $plan, units: [inputs]}
     + (if $dossier  != "" then {dossier:  $dossier}  else {} end)
     + (if $findings != "" then {findings: $findings} else {} end)' <<<"$titles"
}

mark_result() {
  local plan="$1" vtext="$2" last verdict hash want n
  last="$(grep -v '^[[:space:]]*$' <<<"$vtext" | tail -1)"
  verdict=""
  if grep -qF 'Вердикт: блокеров нет' <<<"$last"; then verdict="noblockers"
  elif grep -qF 'Вердикт: есть блокеры' <<<"$last"; then verdict="blockers"
  else echo "невалидный вердикт — отметка не поставлена" >&2; return 1
  fi
  hash="$(hash_of "$plan")"
  [[ -n "$hash" ]] || { echo "не смогла посчитать хеш плана" >&2; return 1; }
  want="$hash"$'\t'"$verdict"
  if [[ "$(cat "$marker" 2>/dev/null)" == "$want" ]]; then
    echo "отметка уже стоит — счётчик не трогаю" >&2
    return 0
  fi
  printf '%s\n' "$want" > "$marker"
  n="$(cat "$runs" 2>/dev/null)"; [[ "$n" =~ ^[0-9]+$ ]] || n=0
  printf '%s\n' "$((n + 1))" > "$runs"
  echo "отметка поставлена ($verdict), прогонов: $((n + 1))" >&2
}

cmd="${1:-}"
case "$cmd" in
  run)
    plan="${2:-}"; shift 2
    payload="$(make_args "$plan" "$@")" || exit 1
    output="$(printf '%s' "$payload" | node "$repo/core/workflows/plan-critic-fan.mjs)" || { printf '%s\n' "$output"; exit 1; }
    result="$(jq -er 'select(.schemaVersion == 1 and .status == "ok" and .workflowId == "plan-critic-fan") | .result' <<<"$output")" \
      || { echo "невалидный результат core workflow" >&2; exit 1; }
    mark_result "$plan" "$result" || exit 1
    printf '%s\n' "$result"
    ;;
  args)
    plan="${2:-}"; shift 2
    make_args "$plan" "$@"
    ;;
  mark)
    plan="${2:-}"; [[ -n "$plan" && -r "$plan" ]] || { echo "нет файла плана: ${plan:-<пусто>}" >&2; exit 1; }
    source="${3:--}"
    if [[ "$source" == "-" ]]; then vtext="$(cat)"; else vtext="$(cat "$source" 2>/dev/null)"; fi
    mark_result "$plan" "$vtext"
    ;;
  script)
    cat <<EOF
export const meta = { name: 'plan-critic-fan-adapter', description: 'Thin Claude adapter for the shared core workflow' };
return await agent('Execute the shared plan critic workflow with the supplied JSON input by running: node $repo/core/workflows/plan-critic-fan.mjs. Return its result field unchanged.', { label: 'core:plan-critic-fan' });
EOF
    ;;
  *)
    echo "usage: plan-critic-fan.sh run|args <план> [--dossier f] [--findings f] | mark <план> [файл-вердикта] | script" >&2
    exit 1
    ;;
esac
