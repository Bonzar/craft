#!/usr/bin/env bash
# Веер обкатки плана одним Workflow: юнитные критики + критик швов параллельно,
# затем сводящий с вердиктом. Заменяет последовательные Agent-вызовы конвейера
# крупного плана; повторные (дельта-)прогоны идут тем же веером с суженной
# рубрикой. Живёт в tools/ по той же причине, что классификатор: помощник — не хук.
#
# Подкоманды:
#   script                                — JS-скрипт workflow в stdout. Скрипт
#     статичен: план, юниты и файлы контекста приходят в него через args.
#     Сохранять под именем, содержащим «plan-critic-fan» (например
#     /tmp/plan-critic-fan.js): mark-хук распознаёт веер по этой подстроке
#     в scriptPath.
#   args <план> [--dossier f] [--findings f] — JSON для входа args Workflow:
#     юниты из заголовков «[тип · …]» плана, плюс пути досье и замечаний
#     прошлого прохода. Здесь же гейт плато: третий завершённый прогон уже
#     есть — отказ (PreToolUse-гейт слушает Task|Agent и запуск Workflow не
#     видит, поэтому правило держит генератор входа).
#   mark <план> [файл-вердикта]           — фолбек отметки: события Workflow до
#     mark-хука могли не дойти (модель событий не гарантирована) — тогда отметку
#     и счётчик пишет этот вызов по тексту вердикта из файла или stdin.
#     Идемпотентен: та же пара «хеш+вердикт» уже стоит — счётчик не крутится.
#
# Пути отметки/счётчика и формат отметки — те же, что у
# .claude/hooks/universal-mark-plan-critic.js (env-переопределения совпадают).
set -u

sid="${CLAUDE_CODE_SESSION_ID:-default}"
marker="${CRAFT_PLAN_CRITIC_MARKER:-/tmp/plan-critic.${sid}.done}"
runs="${CRAFT_PLAN_CRITIC_RUNS:-/tmp/plan-critic.${sid}.runs}"

hash_of() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" 2>/dev/null | cut -d' ' -f1
  elif command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$1" 2>/dev/null | cut -d' ' -f1
  fi
}

cmd="${1:-}"
case "$cmd" in

script)
  cat <<'JS_EOF'
export const meta = {
  name: 'plan-critic-fan',
  description: 'Веер обкатки плана: юнитные критики, критик швов, сводящий вердикт',
  phases: [{ title: 'Критики' }, { title: 'Вердикт' }],
}
// args: { plan, units: [заголовки], dossier?, findings? } — собирает
// tools/plan-critic-fan.sh args <план>.
const plan = args && args.plan
const units = (args && args.units) || []
if (!plan) throw new Error('args.plan обязателен — путь к файлу плана')
if (!units.length) throw new Error('args.units пуст — точечный план обкатывает одиночный plan-critic')

const ctx = args.dossier
  ? `Досье фактов сборки — файл ${args.dossier}: читай вместе с планом, непроверенным считается только выбор, не подпёртый ни планом, ни досье.\n`
  : ''
const frozen = args.findings
  ? `Это повторный проход обкатки. Замечания прошлого прохода и что с каждым сделано — файл ${args.findings}. Рубрика сужена: закрытие прошлых замечаний, регрессии и новые находки ТОЛЬКО корзины critical — новых замечаний прочих корзин не печатай.\n`
  : ''
const tail = 'Верни замечания текстом в формате рубрики; замечаний нет — верни «Замечаний нет».'

// Барьер обоснован: сводящему нужны ВСЕ отчёты разом — раньше их полного
// набора ему нечего склеивать.
const findings = await parallel([
  ...units.map((t, i) => () => agent(
    `Разбери юнит «${t}» плана в файле ${plan}. Только этот юнит: остальные юниты и швы проверяют другие критики.\n${ctx}${frozen}${tail}`,
    { agentType: 'plan-critic-unit', label: `unit:${i + 1}`, phase: 'Критики' })),
  () => agent(
    `Разбери швы плана в файле ${plan}: полнота охвата источника, противоречия и дубли между юнитами, порядок, единство формы. Внутрь юнитов не лезь.\n${ctx}${frozen}${tail}`,
    { agentType: 'plan-critic-seams', label: 'seams', phase: 'Критики' }),
])
const reports = findings.filter(Boolean)
log(`Отчётов критиков: ${reports.length} из ${units.length + 1}`)

// Возврат — текст сводящего как есть: его последняя строка «Вердикт: …»
// читается mark-хуком из уведомления о завершении.
return await agent(
  'Сведи находки критиков одного плана. Отчёты ниже, каждый в своём блоке.\n\n'
    + reports.map((r, i) => `=== ОТЧЁТ ${i + 1} ===\n${r}`).join('\n\n'),
  { agentType: 'plan-critic-verdict', label: 'verdict', phase: 'Вердикт' })
JS_EOF
  ;;

args)
  plan="${2:-}"
  [[ -n "$plan" && -r "$plan" ]] || { echo "нет файла плана: ${plan:-<пусто>}" >&2; exit 1; }
  shift 2
  dossier=""; findings=""
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --dossier)  dossier="${2:-}";  shift 2 ;;
      --findings) findings="${2:-}"; shift 2 ;;
      *) echo "неизвестный флаг: $1" >&2; exit 1 ;;
    esac
  done
  n="$(cat "$runs" 2>/dev/null)"
  if [[ "$n" =~ ^[0-9]+$ && "$n" -ge 3 ]]; then
    echo "Плато: по этому плану уже $n завершённых прогона критика — показывай план Владу с открытым вопросом об остатке замечаний, а не гоняй обкатку дальше." >&2
    exit 1
  fi
  # Юниты — тем же правилом, что дельта-гвард: заголовок любого уровня,
  # начинающийся с типа в квадратных скобках. Забор код-блоков не разбирается
  # (та же цена, что там: лишний юнит дешевле молчания).
  titles="$(grep -E '^#+[[:space:]]*\[' "$plan" | sed -E 's/^#+[[:space:]]*//')"
  if [[ -z "$titles" ]]; then
    echo "юнитов «[тип · …]» в плане не найдено — точечный план обкатывает одиночный plan-critic, веер не нужен" >&2
    exit 1
  fi
  jq -Rn --arg plan "$(realpath "$plan" 2>/dev/null || echo "$plan")" \
        --arg dossier "$dossier" --arg findings "$findings" \
    '{plan: $plan, units: [inputs]}
     + (if $dossier  != "" then {dossier:  $dossier}  else {} end)
     + (if $findings != "" then {findings: $findings} else {} end)' <<<"$titles"
  ;;

mark)
  plan="${2:-}"
  [[ -n "$plan" && -r "$plan" ]] || { echo "нет файла плана: ${plan:-<пусто>}" >&2; exit 1; }
  vsrc="${3:--}"
  if [[ "$vsrc" == "-" ]]; then vtext="$(cat)"; else vtext="$(cat "$vsrc" 2>/dev/null)"; fi
  last="$(grep -v '^[[:space:]]*$' <<<"$vtext" | tail -1)"
  verdict=""
  if grep -qF 'Вердикт: блокеров нет' <<<"$last"; then verdict="noblockers"
  elif grep -qF 'Вердикт: есть блокеры' <<<"$last"; then verdict="blockers"; fi
  hash="$(hash_of "$plan")"
  [[ -n "$hash" ]] || { echo "не смог посчитать хеш плана" >&2; exit 1; }
  want="$hash"; [[ -n "$verdict" ]] && want="$hash	$verdict"
  if [[ "$(cat "$marker" 2>/dev/null)" == "$want" ]]; then
    echo "отметка уже стоит — счётчик не трогаю"
    exit 0
  fi
  printf '%s\n' "$want" > "$marker"
  n="$(cat "$runs" 2>/dev/null)"; [[ "$n" =~ ^[0-9]+$ ]] || n=0
  printf '%s\n' "$((n + 1))" > "$runs"
  echo "отметка поставлена (${verdict:-без вердикта}), прогонов: $((n + 1))"
  ;;

*)
  echo "usage: plan-critic-fan.sh script | args <план> [--dossier f] [--findings f] | mark <план> [файл-вердикта]" >&2
  exit 1
  ;;
esac
exit 0
