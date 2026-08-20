#!/usr/bin/env bash
# PostToolUse on ExitPlanMode: план одобрен — периметр его строк «- где:»
# ДОПИСЫВАЕТСЯ в маркер-файл гейта. Маркер больше не булев «одобрено»: это
# список целей, и гейт (universal-guard-plan-gate.sh) открывает только их.
# Одобрения складываются: второй план сессии — дельта (guard-plan-delta), его
# цели добавляются к прежним, ничего не стирая. Гасят список только реплика
# «закрой гейт» (plan-gate-reset) и смена сессии — файл живёт в /tmp с id.
#
# Цели извлекаются из строк «- где:» СУЩНОСТНЫХ заголовков (## [тип · …]):
#   - файловые — токены в бэктиках с косой чертой или точкой в имени;
#   - Craft — последняя ссылка docs.craft.do строки: сегмент после /x/, а без
#     него — последний сегмент пути (корневой блок; открывает только его).
# Захват лишних токенов (пояснения в бэктиках) принят: периметр — грубый
# фильтр, точность даёт классификатор содержания (tools/plan-scope-classifier).
#
# Защита от протечки привязана к ИСТОЧНИКУ пути: путь, выведенный из пустого
# session-id (общий default), для записи не используется — им делили бы
# периметр параллельные headless-прогоны; путь из env-переопределения
# используется всегда (тесты герметичны через него).
#
# Файла плана нет или целей не извлеклось — список не пишется, гейт закрыт:
# иначе агент открывал бы гейт целиком, просто не оставив метку плана.
set -u
[[ -n "${CRAFT_AUTONOMOUS:-}" ]] && exit 0

sid="${CLAUDE_CODE_SESSION_ID:-}"
if [[ -n "${CRAFT_PLAN_GATE_MARKER:-}" ]]; then
  marker="$CRAFT_PLAN_GATE_MARKER"
elif [[ -n "$sid" ]]; then
  marker="/tmp/craft-plan-gate.${sid}.approved"
else
  marker=""
fi

targets_of_plan() {  # stdin: файл плана → цели, по одной на строку
  awk '
    /^##[[:space:]]*\[/ { ent = 1; next }
    /^#/ { ent = 0; next }
    ent && /^[[:space:]]*-[[:space:]]*где:/ { print }
  ' | while IFS= read -r line; do
    grep -oE '`[^`]+`' <<<"$line" 2>/dev/null | tr -d '`' | grep -E '/|\.' || true
    url="$(grep -oE 'https://docs\.craft\.do/[^) ]+' <<<"$line" 2>/dev/null | tail -1)"
    if [[ -n "$url" ]]; then
      if [[ "$url" == */x/* ]]; then
        printf '%s\n' "${url##*/x/}" | cut -d'/' -f1
      else
        printf '%s\n' "${url##*/}"
      fi
    fi
  done
}

if [[ -n "$marker" ]]; then
  plan="${CRAFT_PLAN_FILE:-$(cat "${CRAFT_PLAN_FILE_MARKER:-/tmp/plan-file.${sid:-default}.path}" 2>/dev/null)}"
  if [[ -n "$plan" && -r "$plan" ]]; then
    targets="$(targets_of_plan < "$plan")"
    if [[ -n "${targets//[[:space:]]/}" ]]; then
      { cat "$marker" 2>/dev/null; printf '%s\n' "$targets"; } | awk 'NF && !seen[$0]++' \
        > "${marker}.tmp" 2>/dev/null && mv "${marker}.tmp" "$marker" 2>/dev/null || true
      # Накопитель одобренных ТЕКСТОВ — вход сверки содержания у гейта: правка
      # старой цели сверяется со своим планом, а правка файла плана после
      # одобрения одобренного не меняет. Пишется тем же вызовом, что цели
      # (нет целей — нет и записи), окно — последние 5 одобрений.
      plans="${marker}.plans"
      { cat "$plans" 2>/dev/null
        printf '=== ОДОБРЕНИЕ ===\n'; cat "$plan"; printf '\n'
      } > "${plans}.tmp" 2>/dev/null \
        && awk '
             /^=== ОДОБРЕНИЕ ===$/ { n++ }
             { line[NR] = $0; rec[NR] = n }
             END { for (i = 1; i <= NR; i++) if (rec[i] > n - 5) print line[i] }
           ' "${plans}.tmp" > "$plans" 2>/dev/null || true
      rm -f "${plans}.tmp" 2>/dev/null
    fi
  fi
fi

# Показ состоялся — обкатка кончилась: счётчик прогонов критика начинает следующую с нуля.
# Обкатка привязана к плану, а не к ходу, поэтому обнуляет её именно одобрение, а не
# реплика Влада — по реплике плато было недостижимо в живом диалоге.
runs="${CRAFT_PLAN_CRITIC_RUNS:-/tmp/plan-critic.${CLAUDE_CODE_SESSION_ID:-default}.runs}"
rm -f "$runs" 2>/dev/null || true
exit 0
