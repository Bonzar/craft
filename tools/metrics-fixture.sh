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
# Печатает ТРИ прогона: обычный (за блокированным концом хода идёт следующий), тот,
# где сессия кончилась прямо на блокировке, и тот, где ОБОРВАН САМ КАНАЛ (журнал
# решений уведён в непишущийся каталог).
#
# Со вторым аргументом — чекаутом для сравнения — скрипт не печатает, а СУДИТ:
# гоняет обе стороны на одной фикстуре и выходит с ненулевым кодом, если хоть один
# прогон разошёлся. Так им можно пользоваться в проверке, а не только глазами.
#
#   bash tools/metrics-fixture.sh . /путь/к/базе   # сравнить и вернуть код
#
# Сеть и модель не зовутся (PLAN_CLASSIFIER=off), хранение выключено, всё
# состояние — во временном каталоге, который снимается за собой.
set -u
side() {
  local repo="$1" tail_mode="$2" broken="${3:-}"
  local st cwd; st=$(mktemp -d); cwd=$(mktemp -d)
  local SID="fix-$$-${RANDOM}"
  local PLAN="$st/plan.md"
  local AUTO=""
  printf '# [система] правка диспетчера\n- где: .claude/hooks/dispatch.js\n' > "$PLAN"
  # Третий прогон рвёт КАНАЛ: журнал решений уводится в непишущийся каталог. Это
  # единственное место, где голова и база обязаны разойтись — и разойтись честно:
  # голова говорит «не знаю» числом, а не выдаёт отказ за проход.
  local journal=""
  [ -n "$broken" ] && journal="/proc/нет-такого-каталога/decisions.jsonl"
  send() {
    echo "$1" | env \
      ${journal:+CRAFT_DECISION_LOG="$journal"} \
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
  # Реплика намеренно инцидентная: признак инцидента ставит другой хук цепочки и
  # приходит он тем же каналом, что и решение, — значит и его надо сверять.
  ev UserPromptSubmit '"prompt":"ты сломал мою заметку, откатись и поправь README"'
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
  if [ "$tail_mode" != "session-end" ]; then
    ev Stop ''
  fi
  # Конец сессии в обоих вариантах: сводка снимается после него, и в ней нет
  # события, чьи строки ещё в пути. Без этого сравнивать было бы нечестно — у
  # последнего события сессии исход и правда неизвестен, о чём сводка и говорит
  # числом `unknown_events`.
  ev SessionEnd '"reason":"clear"'
  local sum="$st/metrics.jsonl.summary.json"
  if [ -f "$sum" ]; then
    node -e '
      const s = JSON.parse(require("fs").readFileSync(process.argv[1], "utf8"));
      // Кроме четырёх метрик приёмки печатаются ещё две: доля разборов (она тоже
      // переехала на новый канал — признак инцидента приходит строкой) и число
      // событий без исхода. Без последнего «числа совпали» ничего не значит: они
      // совпали бы и на сводке, где половина событий потерялась.
      console.log(JSON.stringify({
        denies: s.denies,
        false_denies: s.false_denies,
        plan: s.plan,
        stop_blocks: s.stop_blocks,
        incidents: s.incidents,
        unknown_events: s.unknown_events,
      }));
    ' "$sum"
  else
    echo "СВОДКИ НЕТ"
  fi
  rm -rf "$st" "$cwd"
}
ROOT="${1:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
OTHER="${2:-}"

runs() {
  printf '%s\n' "ход продолжился|$(side "$1" next-stop)" \
    "сессия кончилась на блоке|$(side "$1" session-end)" \
    "канал решений оборван|$(side "$1" next-stop broken)"
}

if [ -z "$OTHER" ]; then
  runs "$ROOT" | while IFS='|' read -r name json; do printf '%-28s%s\n' "$name:" "$json"; done
  exit 0
fi

# Сравнение: поле unknown_events у старого чекаута отсутствует, и это не
# расхождение метрик, а другая схема — оно из сверки вынимается.
strip() { sed 's/,"unknown_events":[0-9]*//'; }
mine="$(runs "$ROOT" | strip)"
theirs="$(runs "$OTHER" | strip)"
if [ "$mine" = "$theirs" ]; then
  echo "фикстура: числа совпали на обоих чекаутах (три прогона)"
  runs "$ROOT" | while IFS='|' read -r name json; do printf '%-28s%s\n' "$name:" "$json"; done
  exit 0
fi
echo "фикстура: РАСХОЖДЕНИЕ"
diff <(printf '%s\n' "$theirs") <(printf '%s\n' "$mine") || true
exit 1
