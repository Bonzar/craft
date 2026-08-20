#!/usr/bin/env bash
# ExitPlanMode, две роли по имени события:
#   PreToolUse  — не показывать Владу юнит, который он в этой сессии уже одобрял;
#   PostToolUse — одобренный план оставляет хеши своих юнитов в накопителе.
# Обе роли в одном файле, чтобы разборщик юнитов существовал в единственном виде:
# разъехавшиеся копии дали бы хеши, которые никогда не совпадут.
#
# Сравниваются только сущностные юниты — заголовок любого уровня, начинающийся с типа
# в квадратных скобках. «Ответы», «Следующими планами» и прочие разделы повторяются из
# плана в план по своей природе.
#
# Нормализация до хеша: пустые строки не учитываются (иначе разделитель перед следующим
# заголовком менял бы хеш одного и того же юнита). Строка цитаты заголовком не
# считается — дословный текст чужого плана живёт именно там, — но в тело юнита входит:
# иначе правка дословного текста не меняла бы хеш и новая редакция правила читалась бы
# как повтор старой.
#
# Вырезателя код-блоков нет намеренно, как и у критика: разбор заборов обманывается
# вложенностью и возвращает ноль юнитов, то есть глушит гейт молча. Ценой этого
# заголовок из примера кода посчитается юнитом — лишний хеш дешевле молчания.
#
# PLAN_DELTA=off — аварийный выключатель, как FACT_GATE=off у факт-гейта.
# Fail open на всём неожиданном: сломанный гейт не должен клинить работу.
set -u

# Хеш из потока. Запасная команда обязательна: на маке основной нет — хеши разъехались
# бы в пустоту и гейт молча выключился бы.
hash_stdin() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum | cut -d' ' -f1
  elif command -v shasum >/dev/null 2>&1; then
    shasum -a 256 | cut -d' ' -f1
  fi
}

# Уступка второму вызову того же события: хук зарегистрирован и project-level, и
# пользовательски (install.sh), а после сноса симлинков обе регистрации ведут в
# ОДИН файл — различить их путями нельзя. Признак — метка занятия события.
# shellcheck disable=SC1091
. "$(dirname "$(realpath "$0" 2>/dev/null || echo "$0")")/_hook-once.sh" 2>/dev/null || true

[[ "${PLAN_DELTA:-}" == "off" ]] && exit 0

input="$(cat)"
declare -F hook_once >/dev/null 2>&1 && { hook_once "$input" || exit 0; }
tool="$(jq -r '.tool_name // ""' <<<"$input" 2>/dev/null)" || exit 0
[[ "$tool" == "ExitPlanMode" ]] || exit 0
event="$(jq -r '.hook_event_name // "PreToolUse"' <<<"$input" 2>/dev/null)"

sid="${CLAUDE_CODE_SESSION_ID:-default}"
plan="${CRAFT_PLAN_FILE:-$(cat "${CRAFT_PLAN_FILE_MARKER:-/tmp/plan-file.${sid}.path}" 2>/dev/null)}"
store="${CRAFT_PLAN_DELTA_STORE:-/tmp/plan-delta.${sid}.hashes}"
[[ -n "$plan" && -r "$plan" ]] || exit 0

units() {  # печатает «хеш<таб>заголовок» на каждый сущностный юнит файла
  local title="" buf="" line
  emit() {
    [[ -n "$title" ]] || return 0
    printf '%s\t%s\n' "$(printf '%s' "$buf" | hash_stdin)" "$title"
  }
  while IFS= read -r line || [[ -n "$line" ]]; do
    [[ -z "${line//[[:space:]]/}" ]] && continue
    # Строка цитаты — дословный текст, а не поле плана: заголовком не считается, но в
    # тело юнита входит, иначе правка дословного текста не меняла бы хеш.
    if [[ "$line" =~ ^[[:space:]]*\> ]]; then
      [[ -n "$title" ]] && buf+="$line"$'\n'
      continue
    fi
    if [[ "$line" =~ ^#+[[:space:]]*\[ ]]; then
      emit; title="[${line#*\[}"; buf="$line"$'\n'; continue
    fi
    if [[ "$line" =~ ^#+[[:space:]] ]]; then
      emit; title=""; buf=""; continue
    fi
    [[ -n "$title" ]] && buf+="$line"$'\n'
  done < "$1"
  emit
}

now="$(units "$plan")"
[[ -n "$now" ]] || exit 0

if [[ "$event" == "PostToolUse" ]]; then
  # Снимок последнего одобренного плана, а не копилка за всю сессию: юнит из давней
  # работы иначе блокировал бы новый план навсегда. Цена — повтор через план обратно
  # не ловится; ложный отказ дороже пропущенного повтора.
  cut -f1 <<<"$now" > "$store" 2>/dev/null || true
  # Рядом с хешами — ТЕКСТ одобренного плана: вход сравнения по смыслу.
  cp "$plan" "${store}.snapshot" 2>/dev/null || true
  exit 0
fi

# Файл микро-планов разрешений — производная маркера периметра, путь тем же
# правилом, что у гейта: env-переопределение маркера, иначе непустой
# session-id, при пустом session-id файла нет (общий default не читается).
if [[ -n "${CRAFT_PLAN_GATE_MARKER:-}" ]]; then
  bp="${CRAFT_PLAN_GATE_MARKER}.button-plans"
elif [[ -n "${CLAUDE_CODE_SESSION_ID:-}" ]]; then
  bp="/tmp/craft-plan-gate.${CLAUDE_CODE_SESSION_ID}.approved.button-plans"
else
  bp=""
fi
self1="$(realpath "$0" 2>/dev/null || echo "$0")"
classifier="${PLAN_CLASSIFIER_BIN:-$(cd "$(dirname "$self1")/../.." && pwd)/tools/plan-scope-classifier.sh}"

# Одобренных планов ещё нет, но семантические разрешения есть: повтор
# одобренного вопросом или указанием ловится сравнением по смыслу против файла
# микро-планов — иначе показ плана, повторяющего разрешённую цель, шёл бы как
# первый план сессии.
if [[ ! -s "$store" ]]; then
  if [[ -n "$bp" && -s "$bp" && -r "$classifier" ]]; then
    verdict="$(: | bash "$classifier" delta "$bp" "$plan" 2>/dev/null)"
    if [[ "$verdict" == REPEATS:* ]]; then
      jq -cn --arg r "План повторяет одобренное вопросом-разрешением:${verdict#REPEATS:}. Одобренное повторно не показывается — покажи только новое. Аварийный выключатель — PLAN_DELTA=off." \
        '{hookSpecificOutput:{hookEventName:"PreToolUse",permissionDecision:"deny",permissionDecisionReason:$r}}'
      exit 0
    fi
  fi
  exit 0
fi
approved="$(cat "$store" 2>/dev/null)"

total=0; rep=0; names=""
while IFS=$'\t' read -r h t; do
  [[ -n "$h" ]] || continue
  total=$((total + 1))
  # Скобки обязательны: без них в UTF-8-локали bash читает имя как «t»» вместе
  # с кавычкой и валит хук по set -u — гвард молча пропускал повторы.
  grep -qxF -- "$h" <<<"$approved" && { rep=$((rep + 1)); names+="«${t}» "; }
done <<<"$now"

[[ "$rep" -eq "$total" ]] && exit 0   # перепоказ того же плана целиком

# Хеш ловит только ДОСЛОВНЫЙ повтор: переформулированный одобренный юнит даёт
# rep=0 и раньше проезжал полным перепоказом. Сравнение по смыслу закрывает
# это классификатором (tools/plan-scope-classifier.sh, режим delta); его
# недоступность возвращает к хеш-поведению — ложный отказ дороже пропуска.
if [[ "$rep" -eq 0 ]]; then
  snap="${store}.snapshot"
  if [[ -r "$snap" && -r "$classifier" ]]; then
    # Вход сравнения — снапшот плана вместе с микро-планами разрешений:
    # семантическое одобрение равносильно плановому и в дельте. Снапшот файл
    # разрешений не затирает — конкатенация собирается на время вызова.
    if [[ -n "$bp" && -s "$bp" ]]; then
      merged="$(mktemp "${TMPDIR:-/tmp}/plan-delta-approved.XXXXXX")"
      cat "$snap" "$bp" > "$merged" 2>/dev/null
      verdict="$(: | bash "$classifier" delta "$merged" "$plan" 2>/dev/null)"
      rm -f "$merged" 2>/dev/null
    else
      verdict="$(: | bash "$classifier" delta "$snap" "$plan" 2>/dev/null)"
    fi
    if [[ "$verdict" == REPEATS:* ]]; then
      jq -cn --arg r "План повторяет уже одобренные юниты по смыслу:${verdict#REPEATS:}. Одобренное повторно не показывается — оставь только изменившееся с прошлого одобрения, а изменённый юнит пометь ревизией с причиной. Аварийный выключатель — PLAN_DELTA=off." \
        '{hookSpecificOutput:{hookEventName:"PreToolUse",permissionDecision:"deny",permissionDecisionReason:$r}}'
      exit 0
    fi
  fi
  exit 0                              # чистая дельта (по смыслу или по фолбеку)
fi

jq -cn --arg r "План повторяет уже одобренные юниты: ${names}. Одобренное повторно не показывается — оставь только изменившееся с прошлого одобрения, а изменённый юнит пометь ревизией с причиной. Аварийный выключатель — PLAN_DELTA=off." \
  '{hookSpecificOutput:{hookEventName:"PreToolUse",permissionDecision:"deny",permissionDecisionReason:$r}}'
exit 0
