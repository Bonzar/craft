#!/bin/bash
# Фикстура сводки: одна и та же последовательность событий через диспетчер, слово
# в слово. Задевает все четыре метрики, которые считаются по ИСХОДУ вызова: отказ
# гварда (denies), ложный отказ (тот же вызов прошёл после вмешательства Влада),
# отбитый показ плана (plan.bounced) и блокировку конца хода (stop_blocks).
#
# Зачем. Исход вызова переехал из записи метрик в журнал решений, и сверять
# «числа не поехали» надо не глазами, а прогоном. Скрипт принимает ЛЮБОЙ чекаут
# слоя, поэтому им сравнивают две ветки: одна и та же фикстура, два ответа.
#
#   bash tools/metrics-fixture.sh /путь/к/чекауту
#   bash tools/metrics-fixture.sh            # текущий чекаут
#
# Печатает ДВА прогона: обычный (за блокированным концом хода идёт следующий) и
# тот, где сессия кончилась прямо на блокировке.
#
# Сеть и модель не зовутся (PLAN_CLASSIFIER=off), хранение выключено, всё
# состояние — во временном каталоге, который снимается за собой.
set -u
side() {
  local repo="$1" tail_mode="$2"
  local st cwd; st=$(mktemp -d); cwd=$(mktemp -d)
  local SID="fix-$$-${RANDOM}"
  local PLAN="$st/plan.md"
  local AUTO=""
  printf '# [система] правка диспетчера\n- где: .claude/hooks/dispatch.js\n' > "$PLAN"
  send() {
    echo "$1" | env \
      CRAFT_STATE_DIR="$st" CRAFT_SESSION_ID="$SID" CLAUDE_CODE_SESSION_ID="$SID" \
      CRAFT_METRICS_LOG="$st/metrics.jsonl" SESSION_ANCHOR_STATE="$st/anchor" \
      CRAFT_APPROVAL_REGISTRY="$st/approvals.jsonl" CRAFT_PLAN_CRITIC_MARKER="$st/critic.done" \
      CRAFT_PLAN_FILE="$PLAN" CRAFT_PLAN_SHOWN_MARKER="$st/plan-shown" \
      CRAFT_SERVICE_TURN_MARKER="$st/service-turn" ROUTINE_FACTS_MARKER="$st/routine-facts" \
      OBSERVE_BUFFER="$st/observe.log" FACT_GATE_STATE_DIR="$st" \
      PLAN_CLASSIFIER=off METRICS_STORE=off HOOK_ONCE=off SYNC_SYSTEM=off CRAFT_AUTONOMOUS="$AUTO" \
      node "$repo/.claude/hooks/dispatch.js" universal >/dev/null 2>&1
  }
  ev() { # ev <имя события> <хвост json>
    send "{\"hook_event_name\":\"$1\",\"session_id\":\"$SID\",\"cwd\":\"$cwd\"${2:+,$2}}"
  }
  local RM="\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"rm -rf $cwd/junk\"}"
  ev SessionStart '"source":"startup"'
  ev UserPromptSubmit '"prompt":"поправь README, пожалуйста, это важно"'
  # отказ гварда якоря: писать в мир, пока якорь не выбран, нельзя
  ev PreToolUse "\"tool_use_id\":\"t1\",\"tool_name\":\"Write\",\"tool_input\":{\"file_path\":\"/home/user/craft/README.md\",\"content\":\"x\"}"
  # отказ факт-гейта на деструктиве
  ev PreToolUse "\"tool_use_id\":\"t2\",$RM"
  # вмешательство Влада кнопкой: выбор якоря
  ev PreToolUse "\"tool_use_id\":\"t3\",\"tool_name\":\"AskUserQuestion\",\"tool_input\":{\"questions\":[{\"question\":\"Какая задача будет якорем этой сессии?\",\"header\":\"Якорь сессии\",\"options\":[{\"label\":\"Завести новую\"}]}]}"
  ev PostToolUse "\"tool_use_id\":\"t3\",\"tool_name\":\"AskUserQuestion\",\"tool_input\":{\"questions\":[{\"question\":\"Какая задача будет якорем этой сессии?\",\"header\":\"Якорь сессии\",\"options\":[{\"label\":\"Завести новую\"}]}]},\"tool_response\":{\"answers\":{\"Какая задача будет якорем этой сессии?\":\"Завести новую\"}}"
  # ТОТ ЖЕ деструктив после вмешательства проходит — это и есть ложный отказ
  ev PreToolUse "\"tool_use_id\":\"t4\",$RM"
  ev PostToolUse "\"tool_use_id\":\"t4\",$RM,\"tool_response\":{\"ok\":true}"
  # отказ сторожа ожидания
  ev PreToolUse "\"tool_use_id\":\"t5\",\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"sleep 30\"}"
  # отбитый показ плана: план трогает системную зону, критик его не обкатывал
  ev PreToolUse "\"tool_use_id\":\"t6\",\"tool_name\":\"ExitPlanMode\",\"tool_input\":{\"plan\":\"# [система] правка диспетчера\n- где: .claude/hooks/dispatch.js\"}"
  AUTO=1
  ev Stop ''
  # Хвост задаётся вызывающим: либо ход продолжился (второй конец хода), либо
  # сессия кончилась прямо на заблокированном конце хода. Второй случай отдельный:
  # строки решений приезжают к наблюдателю СЛЕДУЮЩИМ событием, и если следующего
  # хода нет, донести блокировку может только конец сессии.
  if [ "$tail_mode" = "session-end" ]; then
    ev SessionEnd '"reason":"clear"'
  else
    ev Stop ''
  fi
  local sum="$st/metrics.jsonl.summary.json"
  if [ -f "$sum" ]; then
    node -e '
      const s = JSON.parse(require("fs").readFileSync(process.argv[1], "utf8"));
      console.log(JSON.stringify({
        denies: s.denies, false_denies: s.false_denies, plan: s.plan, stop_blocks: s.stop_blocks,
      }));
    ' "$sum"
  else
    echo "СВОДКИ НЕТ"
  fi
  rm -rf "$st" "$cwd"
}
ROOT="${1:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
echo "ход продолжился:            $(side "$ROOT" next-stop)"
echo "сессия кончилась на блоке:  $(side "$ROOT" session-end)"
