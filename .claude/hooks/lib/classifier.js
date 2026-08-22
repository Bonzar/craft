// Вызов классификатора план-гейта — LLM-проверки, которую регулярка дать не
// может. Сам классификатор остаётся отдельной командой (он оборачивает вызов
// модели), здесь только его запуск и разбор ответа.
//
// Ответ — одна строка. Разбор сверяет СНАЧАЛА отрицательную форму: положительная
// входит в отрицательную подстрокой, и наивная проверка «содержит СООТВЕТСТВУЕТ»
// пропустила бы «НЕ СООТВЕТСТВУЕТ».
//
// Таймаут, обрыв и нераспознанный ответ дают UNAVAILABLE: решение о судьбе
// правки принимает гейт, а не помощник.
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { repoRootOf } from './paths.js';

// Корень считается от ЭТОГО модуля, а не от файла вызывающего хука: у них разная
// глубина (хук лежит на уровень выше), и общая формула на стороне вызова давала
// бы каталог мимо чекаута — классификатор молча оказывался бы недоступен.
export function classifierPath() {
  return process.env.PLAN_CLASSIFIER_BIN
    || path.join(repoRootOf(import.meta.url), 'tools', 'plan-scope-classifier.sh');
}

export function classifierAvailable(bin) {
  return Boolean(bin) && fs.existsSync(bin);
}

// classify(bin, mode, args, description) → строка вердикта.
// Аварийный выключатель PLAN_CLASSIFIER=off обрабатывает сам классификатор.
export function classify(bin, mode, args, description, { timeoutSec } = {}) {
  if (!classifierAvailable(bin)) return 'UNAVAILABLE';
  const env = { ...process.env };
  if (timeoutSec) env.PLAN_CLASSIFIER_TIMEOUT = String(timeoutSec);
  const res = spawnSync('bash', [bin, mode, ...args], {
    input: description,
    env,
    encoding: 'utf8',
    maxBuffer: 16 * 1024 * 1024,
  });
  const verdict = (res.stdout || '').trim();
  return verdict || 'UNAVAILABLE';
}
