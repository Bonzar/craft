// Адаптер классификатора: КАК его запустить. Сам классификатор — отдельная
// команда-обёртка вокруг вызова модели, лежит спутником слоя и написана на shell.
// Всё, что знает про интерпретатор, расширение файла и способ передать материал,
// живёт здесь; общая часть (classifier.js) получает от адаптера готовый ответ.
//
// Контракт адаптера: path() → путь к команде, available(bin) → есть ли она,
// run(bin, mode, args, input, {timeoutSec}) → текст ответа.
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { repoRootOf } from './paths.js';

// Корень считается от ЭТОГО модуля, а не от файла вызывающего хука: у них разная
// глубина (хук лежит на уровень выше), и общая формула на стороне вызова давала бы
// каталог мимо чекаута — классификатор молча оказывался бы недоступен.
export function classifierPath() {
  return process.env.PLAN_CLASSIFIER_BIN
    || path.join(repoRootOf(import.meta.url), 'tools', 'plan-scope-classifier.sh');
}

export function available(bin) {
  return Boolean(bin) && fs.existsSync(bin);
}

// Материал уходит на вход команды, а не аргументом: он бывает в десятки килобайт,
// и в аргументах упёрся бы в предел командной строки.
export function run(bin, mode, args, input, { timeoutSec } = {}) {
  const env = { ...process.env };
  if (timeoutSec) env.PLAN_CLASSIFIER_TIMEOUT = String(timeoutSec);
  const res = spawnSync('bash', [bin, mode, ...args], {
    input,
    env,
    encoding: 'utf8',
    maxBuffer: 16 * 1024 * 1024,
  });
  return (res.stdout || '').trim();
}

// Аварийный выключатель обрабатывает сам классификатор, но знать, что он сработал,
// нужно общей части: с ним модель не зовётся, и такой вызов в счётчик не идёт.
export function offSwitchOn() {
  return process.env.PLAN_CLASSIFIER === 'off';
}
