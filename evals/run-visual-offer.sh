#!/usr/bin/env bash
# Евал правила «Визуал в разделе, этапе и вехе»: агент сам ищет, что в тексте
# читается быстрее картинкой, и предлагает визуал вместе с текстом — без просьбы
# Влада. Визуал, чьи статусы придётся держать руками, не предлагается: третий
# кейс бьёт по пометкам «можно сейчас», «в работе» и цветным кружкам.
#
# Правило живёт в modules/behavior/rules/behavior.md и приезжает установщиком в
# ~/.claude/rules — оно всегда в контексте, улики доставки и триггер-хука нет.
#
# Usage: run-visual-offer.sh [model ...]     (default: claude-sonnet-5)
#        EVAL_RUNS=3 run-visual-offer.sh …   — N прогонов каждого кейса
#
# Дисциплина прогонов и порог устойчивости — evals/README.md.
set -u
cd "$(dirname "$0")/.." || exit 1
source evals/lib/runner.sh

eval_run evals/cases/behavioral/visual-offer.jsonl "$@"
