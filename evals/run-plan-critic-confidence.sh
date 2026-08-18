#!/usr/bin/env bash
# Евал шкалы уверенности у критика планов: критик обязан печатать уверенность
# каждого замечания и держать показ только замечаниями корзин critical и major
# с уверенностью от порога — догадка без улики показ не держит.
#
# ЧТО ИМЕННО МЕРИТСЯ. Рубрика критика (.claude/agents/plan-critic.md) как
# правило: прогон читает её файлом и работает по ней. Запуск самого подагента
# закрыт песочницей раннера (Task и Agent в EVAL_DENY_TOOLS), поэтому кейс
# меряет агента, исполняющего рубрику, а не вызов plan-critic.
#
# Фикстуры планов — evals/fixtures/plans/: blocker с правкой без адреса
# (выполнить нельзя), guess с местом, к которому легко придраться рассуждением
# без улики, clean с одной стилевой шероховатостью.
#
# Usage: run-plan-critic-confidence.sh [model ...]   (default: claude-sonnet-5)
#        EVAL_RUNS=3 run-plan-critic-confidence.sh … — N прогонов каждого кейса
#
# Дисциплина прогонов и порог устойчивости — evals/README.md.
set -u
cd "$(dirname "$0")/.." || exit 1
source evals/lib/runner.sh

eval_run evals/cases/behavioral/plan-critic-confidence.jsonl "$@"
