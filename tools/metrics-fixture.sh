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
# ГЕРМЕТИЧНОСТЬ, перечислена поимённо — список выключателей, и напротив каждого
# названо то, ЧЕМ он держится. Одиннадцатое ревью показало, что это не педантизм:
# суммарно фикстура была герметична, но три выключателя из пяти закрывали путь не
# сами, и снявший «вспомогательный» вернул бы и звонки в сеть, и снос файла.
#   PLAN_CLASSIFIER=off      — модель не зовётся;
#   METRICS_STORE=off        — сводка никуда не увозится;
#   CRAFT_API_BASE=          — ни инжекторы правил, ни сборщик зоны не идут в
#                              connect-API. Держится тем, что «не задано» в
#                              lib/env.js — это ОТСУТСТВИЕ ключа: пустую строку
#                              личный `craft.env` больше не перебивает
#                              (юниты `env-unset.test.mjs`);
#   CODEX_AUTH_JSON=         — тем же правилом: хук входа в codex выходит сразу,
#                              не трогает ~/.codex и не ставит пакет глобально;
#   CRAFT_GATE_EXEMPT_PAGES  — тоже ПУТЬ, а не пустота: на несуществующий список,
#                              на котором сборщик зоны выходит. Снос прежнего
#                              снимка теперь ПОСЛЕ чтения списка, а не до
#                              (юнит `exempt-scope-keep.test.mjs`);
#   CRAFT_GATE_EXEMPT_SCOPE  — ПУТЬ во временный файл: пишет сборщик туда, а не в
#                              `.claude/craft-gate-exempt-scope.txt` ЧЕКАУТА —
#                              вторая защита того же файла, независимая от первой;
#   HOME, CODEX_HOME, CLAUDE_PROJECT_DIR — БЕЗ знака равенства: им даются ПУТИ во
#                              временный каталог, а не пустота. Пустой
#                              CLAUDE_PROJECT_DIR дал бы обратное — корень
#                              посчитался бы от самого модуля, то есть от
#                              чекаута, и `.env` вернул бы доступ к connect-API
#                              (проба: 0 connect с путём, 18 с пустотой — но 18
#                              воспроизводится только на машине, где у чекаута
#                              ЕСТЬ `.env` с доступом; в CI и в пустой среде обе
#                              стороны дадут 0, и охрана всё равно нужна).
#                              На этих трёх и держится то, что личный `craft.env`
#                              и `.env` чекаута не находятся вовсе.
# Всё остальное состояние — во временном каталоге, который снимается за собой.
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
      HOME="$st/home" CODEX_HOME="$st/codex" CLAUDE_PROJECT_DIR="$cwd" \
      CRAFT_GATE_EXEMPT_SCOPE="$st/exempt-scope.txt" CODEX_AUTH_JSON= \
      CRAFT_GATE_EXEMPT_PAGES="$st/нет-такого-списка.txt" CRAFT_API_BASE= \
      CRAFT_PLAN_FILE="$PLAN" CRAFT_PLAN_SHOWN_MARKER="$st/plan-shown" \
      CRAFT_SERVICE_TURN_MARKER="$st/service-turn" ROUTINE_FACTS_MARKER="$st/routine-facts" \
      CRAFT_JOURNAL_LOG="$st/journal.jsonl" INSTINCT_FLUSH_MARKER="$st/instinct-flush.done" \
      INSTINCT_FLUSH_STATE="$st/instinct-flush.state" \
      FACT_GATE_STATE_DIR="$st" \
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
  # Цель записи — ПОСТОЯННЫЙ выдуманный путь, а не путь чекаута. Гвард якоря
  # отличает мир от черновиков по эфемерности пути, и чекаут, лежащий во
  # временном каталоге (`git clone` базы в /tmp — самый естественный способ её
  # достать), делал бы эту цель черновиком: отказ гварда якоря пропадал бы
  # молча, судья давал бы ложное расхождение, а `--self` терял бы метрику,
  # ничего не заметив. Файлу существовать не нужно — событие до вызова судит
  # намерение.
  ev PreToolUse "\"tool_use_id\":\"t1\",\"tool_name\":\"Write\",\"tool_input\":{\"file_path\":\"/craft-fixture-world/README.md\",\"content\":\"x\"}"
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
  # Сводка, которая ЕСТЬ, но не читается, прежде давала пустую строку — и охраны,
  # ищущие плохую примету, на ней молчали. Теперь это своя примета.
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
    ' "$sum" 2>/dev/null || echo "СВОДКА НЕ ЧИТАЕТСЯ"
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

show() { printf '%s\n' "$1" | while IFS='|' read -r name json; do printf '%-28s%s\n' "$name:" "$json"; done; }
json_of() { printf '%s\n' "$1" | sed -n "${2}p" | cut -d'|' -f2-; }

# Охрана, которая ищет ПЛОХУЮ примету, молчит на пустоте: девятое ревью показало,
# что сводка, которая есть, но не читается, проходила и самопроверку, и сверку.
# Поэтому спрашивается НАЛИЧИЕ ожидаемого: три прогона, в каждом настоящая сводка.
check_summary() { # check_summary <три строки> <чей чекаут>
  local n=0 name json
  while IFS='|' read -r name json; do
    n=$((n + 1))
    case "$json" in
      *'"denies"'*) ;;
      *) echo "фикстура: у $2 прогон «$name» не дал сводки: ${json:-пусто}"; return 1 ;;
    esac
  done <<EOF
$1
EOF
  [ "$n" = 3 ] || { echo "фикстура: у $2 не три прогона, а $n"; return 1; }
  return 0
}

# Событий без исхода быть не должно. Спрашивается ОТДЕЛЬНО, а не сравнением: поле
# из сверки вынимается (у старого чекаута его нет вовсе — другая схема, а не
# расхождение метрик), и без отдельного вопроса «числа совпали» прошло бы и на
# сводке, где половина событий потерялась.
check_unknown() { # check_unknown <три строки> <чей чекаут> <обязательно ли поле>
  local name json
  while IFS='|' read -r name json; do
    case "$json" in
      *'"unknown_events":0'*) ;;
      *'"unknown_events":'*) echo "фикстура: у $2 в прогоне «$name» есть события без исхода"; return 1 ;;
      *) [ "$3" = "нужно" ] && { echo "фикстура: у $2 в прогоне «$name» НЕТ поля unknown_events"; return 1; } ;;
    esac
  done <<EOF
$1
EOF
  return 0
}

# Метрика, пропавшая целиком, — это не расхождение, а тишина: сравнение двух
# одинаково обеднённых сводок сойдётся. Поэтому состав отказов спрашивается
# ПОИМЁННО: фикстура задевает четыре класса намеренно, и если хоть один перестал
# задеваться (сменилось место чекаута, уехал гвард, изменилось правило
# эфемерности) — это отказ, а не зелёное.
#
# Спрашивается он У СВОЕГО ПОЛЯ, а не подстрокой по всей строке сводки. Поиск по
# строке дал бы тот же ответ и сегодня — но лишь потому, что других полей с этими
# именами в сводке случайно нет. Охрана, стоящая не там, куда смотрит, — ровно тот
# класс дефекта, который в этом инструменте ловили четыре раунда подряд.
#
# ЦЕНА НАЗВАНА: список классов зашит здесь. Переименуют гвард — кейс покраснеет на
# исправном коде, и это задумано: список приёмки обязан меняться сознательно, а не
# усыхать молча вместе с переименованием.
by_class() { # by_class <строка сводки> — только состав отказов, или пусто
  node -e 'try {
    const j = JSON.parse(process.argv[1]);
    process.stdout.write(JSON.stringify((j.denies || {}).by_class || {}));
  } catch { process.stdout.write(""); }' "$1"
}

check_denies() { # check_denies <три строки> <чей чекаут>
  local name json class classes
  while IFS='|' read -r name json; do
    classes="$(by_class "$json")"
    if [ -z "$classes" ]; then
      echo "фикстура: у $2 в прогоне «$name» состав отказов не разобрался"
      return 1
    fi
    for class in session-anchor fact-gate sleep-waiter-guard guard-plan-critic; do
      case "$classes" in
        *"\"$class\":"*) ;;
        *) echo "фикстура: у $2 в прогоне «$name» ПРОПАЛ класс отказа $class"; return 1 ;;
      esac
    done
  done <<EOF
$1
EOF
  return 0
}

# Оборванный канал не должен давать НИКАКОЙ разницы: строки решения, признака и
# замеров идут запасным путём в журнал метрик. Значит первый и третий прогоны
# одного чекаута обязаны совпасть. Это и есть тот инвариант, ради которого
# фикстуру писали, — и он держится на одном чекауте, то есть переживает слияние.
check_broken() { # check_broken <три строки> <чей чекаут>
  local a b
  a="$(json_of "$1" 1)"
  b="$(json_of "$1" 3)"
  [ "$a" = "$b" ] && return 0
  echo "фикстура: у $2 ОБОРВАННЫЙ КАНАЛ МЕНЯЕТ ЧИСЛА — запасной путь не спас"
  echo "  ход продолжился:    $a"
  echo "  канал оборван:      $b"
  return 1
}

if [ -z "$OTHER" ]; then
  runs "$ROOT" | while IFS='|' read -r name json; do printf '%-28s%s\n' "$name:" "$json"; done
  exit 0
fi

# Самопроверка ОДНОГО чекаута: сравнивать не с чем, но три свойства проверяются и
# так — сводка сложилась, событий без исхода нет, оборванный канал чисел не меняет.
# Сверка с базой живёт ровно столько, сколько живёт база, а эти три держатся и
# после слияния, поэтому в прогоне стоит именно самопроверка.
if [ "$OTHER" = "--self" ]; then
  self="$(runs "$ROOT")"
  check_summary "$self" "чекаута" || { show "$self"; exit 1; }
  check_unknown "$self" "чекаута" нужно || { show "$self"; exit 1; }
  check_denies "$self" "чекаута" || { show "$self"; exit 1; }
  check_broken "$self" "чекаута" || exit 1
  show "$self"
  echo "фикстура: сводка складывается, все четыре класса отказов задеты, событий без исхода нет, оборванный канал чисел не меняет (три прогона)"
  exit 0
fi

# Чекаут сам с собой не сравнивается: совпадение было бы тривиальным, а
# приёмочная проверка — зелёной ни о чём.
if [ "$(cd "$ROOT" && pwd)" = "$(cd "$OTHER" && pwd 2>/dev/null || echo "$OTHER")" ]; then
  echo "фикстура: ОБА АРГУМЕНТА — ОДИН ЧЕКАУТ, сравнивать нечего"
  exit 1
fi

# Прогоны делаются ПО РАЗУ: печать в конце берёт уже снятые числа, а не гоняет
# диспетчер второй раз — иначе печаталось бы не то, что сравнивалось.
mine="$(runs "$ROOT")"
theirs="$(runs "$OTHER")"

check_summary "$mine" "первого чекаута" || { show "$mine"; exit 1; }
check_summary "$theirs" "второго чекаута" || { show "$theirs"; exit 1; }
# Поле обязательно у ПЕРВОГО: он и есть проверяемая голова (у базы схемы нет).
check_unknown "$mine" "первого чекаута" нужно || { show "$mine"; exit 1; }
check_unknown "$theirs" "второго чекаута" необязательно || { show "$theirs"; exit 1; }
check_denies "$mine" "первого чекаута" || { show "$mine"; exit 1; }
check_denies "$theirs" "второго чекаута" || { show "$theirs"; exit 1; }
check_broken "$mine" "первого чекаута" || exit 1

strip() { sed 's/,"unknown_events":[0-9]*//'; }
if [ "$(printf '%s\n' "$mine" | strip)" = "$(printf '%s\n' "$theirs" | strip)" ]; then
  echo "фикстура: числа совпали на обоих чекаутах (три прогона)"
  show "$mine"
  exit 0
fi
echo "фикстура: РАСХОЖДЕНИЕ"
diff <(printf '%s\n' "$theirs" | strip) <(printf '%s\n' "$mine" | strip) || true
exit 1
