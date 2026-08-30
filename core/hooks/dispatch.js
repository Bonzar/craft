#!/usr/bin/env node
// Диспетчер хуков: ОДНА регистрация на событие вместо строки на каждый хук.
//
// Зачем. Раньше харнесс запускал отдельный процесс на каждый зарегистрированный
// хук: на одно командное действие это пять запусков подряд. Диспетчер стартует один
// раз, читает событие и вызывает нужные хуки внутри себя — цена события падает
// до одного старта.
//
// Хуки при этом остаются САМОСТОЯТЕЛЬНЫМИ файлами: каждый запускается и руками,
// и раннером кейсов, и его можно зарегистрировать поштучно. Диспетчер лишь
// подключает те же файлы модулями, а состав и порядок берёт из таблицы
// маршрутов (dispatch-table.js) — единственного места, где расписан слой.
//
// Как это работает. Хук — скрипт, который заканчивает себя выходом из процесса;
// под диспетчером выход перехватывается и означает ровно «этот хук закончил».
// Перехват снимается сразу после вызова, так что стоящий следом хук работает в
// обычных условиях.
//
// Первое же РЕШЕНИЕ (запрет, вопрос человеку, блокировка конца хода) обрывает
// цепочку: вывод у хуков общий, и второе решение легло бы в него следом за
// первым — харнесс прочитал бы два ответа на один вопрос.
//
// Аргумент задаёт контур: `universal` — пользовательский слой в чужих проектах,
// без аргумента — проектный. Fail open: сломанный хук не рвёт цепочку, а
// сломанный диспетчер оставляет сессию без хуков, но не без работы.
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { readEvent } from './lib/event.js';
import { hooksFor } from './dispatch-table.js';
import { hookFailureDecision } from './lib/failure-policy.js';

const argv = process.argv.slice(2);
const dir = path.dirname(fileURLToPath(import.meta.url));

// Служебный режим: показать, кого позовёт таблица на такое событие и такой
// инструмент. Им же смоук-проверки тестов сверяют объявленную поверхность хука
// с фактическим маршрутом — без него они судили бы по тексту таблицы.
if (argv[0] === '--list') {
  const [, listEvent = '', listTool = '', listScope = 'project'] = argv;
  process.stdout.write(`${hooksFor(listEvent, listTool, listScope).join('\n')}\n`);
  process.exit(0);
}

const scope = ['universal', 'client'].includes(argv[0]) ? argv[0] : 'project';

const { event, name: eventName, route } = readEvent();
if (!eventName) process.exit(0);

if (event.sessionId && !process.env.CRAFT_SESSION_ID) process.env.CRAFT_SESSION_ID = event.sessionId;

// Выход хука из процесса — не ошибка, а его нормальный конец.
class HookFinished extends Error {
  constructor(name, code) {
    super(name);
    this.code = Number(code || 0);
  }
}

function failureOutput(name, detail) {
  process.stderr.write(`[dispatch] хук ${name} не выполнен: ${detail}\n`);
  const failure = hookFailureDecision(eventName, name, detail);
  if (!failure) return '';
  globalThis.hookDecided = true;
  return `${JSON.stringify(failure)}\n`;
}

async function runHook(name) {
  const extension = process.env.CRAFT_HOOK_EXTENSION_DIR || '';
  const own = path.join(dir, `${name}.js`);
  const file = fs.existsSync(own) ? own : path.join(extension, `${name}.js`);
  if (!fs.existsSync(file)) {
    const output = failureOutput(name, 'missing');
    if (output && process.env.CRAFT_HOOK_CAPTURE !== '1') process.stdout.write(output);
    return output;
  }

  const realExit = process.exit;
  const realWrite = process.stdout.write;
  let captured = '';
  if (process.env.CRAFT_HOOK_CAPTURE === '1') {
    process.stdout.write = (chunk, encoding, callback) => {
      captured += Buffer.isBuffer(chunk) ? chunk.toString(encoding || 'utf8') : String(chunk);
      if (typeof encoding === 'function') encoding();
      if (typeof callback === 'function') callback();
      return true;
    };
  }
  process.exit = (code = 0) => {
    throw new HookFinished(name, code);
  };
  try {
    await import(pathToFileURL(file).href);
  } catch (error) {
    if (error instanceof HookFinished) {
      if (error.code !== 0) process.stdout.write(failureOutput(name, 'nonzero-exit'));
    } else {
      // Упавший хук не должен уносить с собой остальную цепочку: он один
      // остаётся неотработавшим, о чём и сообщается в служебный поток.
      process.stdout.write(failureOutput(name, `exception: ${error && error.message}`));
    }
  } finally {
    process.exit = realExit;
    process.stdout.write = realWrite;
  }
  return captured;
}

const outputs = [];
for (const name of hooksFor(eventName, route, scope)) {
  outputs.push(await runHook(name));
  if (globalThis.hookDecided) break;
}
if (process.env.CRAFT_HOOK_CAPTURE === '1') process.stdout.write(`${JSON.stringify(outputs)}\n`);
