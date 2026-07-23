#!/usr/bin/env bash
# UserPromptSubmit hook: recognise the harness auto-continue message and inject a
# directive at the decision point. See plan «Рычаг 1». The technical string
# "Continue from where you left off." is NOT Влад — treating it as approval or a new
# instruction, and re-issuing an auto-rejected gate or re-emitting already-shown text,
# is the "объяснял одно и то же на каждом continue" loop this hook breaks by re-injecting
# «Влад не в сети — не его отказ» exactly when the signal arrives.
#
# Matches ONLY when the whole message (trimmed, any case) is exactly the auto-continue
# phrase (± trailing period). Bare "continue", the phrase inside a real sentence, or
# any other text does NOT match — near-zero false positives.
#
# Fail quiet (exit 0, no stdout) on anything unexpected: a broken detector must never
# block Влад's message from going through.
set -u
export LC_ALL=C.UTF-8

input="$(cat)"
prompt="$(jq -r '.prompt // ""' <<<"$input" 2>/dev/null)" || exit 0

shopt -s nocasematch
if [[ ! "$prompt" =~ ^[[:space:]]*continue[[:space:]]from[[:space:]]where[[:space:]]you[[:space:]]left[[:space:]]off\.?[[:space:]]*$ ]]; then
  exit 0
fi

cat <<'EOF'
⚠️ ТЕХНИЧЕСКИЙ АВТО-CONTINUE — не сообщение Влада, а автосигнал harness (гейт авто-отклонён либо Влад отошёл). Не считать его ни одобрением, ни новой инструкцией.
- Не переиздавать авто-отклонённый гейт: план-мод, ExitPlanMode, AskUserQuestion.
- Уже показанный Владу текст НЕ повторять.
- Был в упавшем гейте вопрос или контент, которого Влад не видел, — вынести его текстом один раз.
- Оборвалась реальная работа в процессе — доделать её.
- Иначе — тихо ждать содержательного ответа Влада, ничего не выполняя.
EOF
exit 0
