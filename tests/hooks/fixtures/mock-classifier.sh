#!/usr/bin/env bash
# Мок команды модели для тестов классификатора: печатает заданный env-ответ.
# MOCK_CLASSIFIER_ANSWER=fail — имитация недоступности (ненулевой выход).
[[ "${MOCK_CLASSIFIER_ANSWER:-}" == "fail" ]] && exit 1
printf '%s\n' "${MOCK_CLASSIFIER_ANSWER:-СООТВЕТСТВУЕТ}"
