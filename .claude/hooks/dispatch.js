#!/usr/bin/env node
// Диспетчер хуков: ОДНА регистрация на событие вместо строки на каждый хук.
//
// Зачем. Раньше харнесс запускал отдельный процесс на каждый зарегистрированный
// хук: на одну команду Bash это пять запусков подряд. Диспетчер стартует один
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
// первым — харнес прочитал бы два ответа на один вопрос. Исключение — хуки из
// ALWAYS (метрики): они ничего не печатают и зовутся после решения, чтобы его
// увидеть.
//
// Решение и замеры времени идут через ЖУРНАЛ РЕШЕНИЙ в каталоге состояния
// (lib/decision-log.js), а не через общую память процесса: решение переживает
// падение решившего хука, и его видит любой процесс, а не только тот, кого
// позвали следом. Имя текущего хука и контур передаются окружением — их читает
// тот, кто записывает решение.
//
// Аргумент задаёт контур: `universal` — пользовательский слой в чужих проектах,
// без аргумента — проектный. Fail open: сломанный хук не рвёт цепочку, а
// сломанный диспетчер оставляет сессию без хуков, но не без работы.
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { readEvent } from './lib/event-claude.js';
import { appendTimings, decisionFor, journalStamp } from './lib/decision-log.js';
import { hooksFor, ALWAYS } from './dispatch-table.js';

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

const scope = argv[0] === 'universal' ? 'universal' : 'project';
// Контур — окружением: хук метрик по нему решает, писать ли ему, когда у чекаута
// сессии есть и проектная регистрация.
process.env.CRAFT_HOOK_SCOPE = scope;

const event = readEvent();
// Таблица маршрутов и регистрация в настройках — сторона харнеса, поэтому и
// ключом здесь остаётся ЕГО имя события. Хукам достаётся каноническое.
const eventName = event.harness_event;
if (!eventName) process.exit(0);

// Выход хука из процесса — не ошибка, а его нормальный конец.
class HookFinished extends Error {}

const timings = {};

async function runHook(name) {
  const file = path.join(dir, `${name}.js`);
  if (!fs.existsSync(file)) return;

  const realExit = process.exit;
  process.exit = () => {
    throw new HookFinished(name);
  };
  process.env.CRAFT_HOOK_NAME = name;
  const started = Date.now();
  try {
    await import(pathToFileURL(file).href);
  } catch (error) {
    if (!(error instanceof HookFinished)) {
      // Упавший хук не должен уносить с собой остальную цепочку: он один
      // остаётся неотработавшим, о чём и сообщается в служебный поток.
      process.stderr.write(`[dispatch] хук ${name} упал: ${error && error.message}\n`);
    }
  } finally {
    process.exit = realExit;
    process.env.CRAFT_HOOK_NAME = '';
    timings[name] = Date.now() - started;
  }
}

const chain = hooksFor(eventName, event.tool, scope);
let decided = false;
// Отметка журнала вместо разбора после каждого хука: цепочка стоит в ходе, и
// читать хвост ради вопроса «решили ли уже» дорого. Журнал не вырос — решения не
// было, разбирать нечего.
let stamp = journalStamp(event);
for (const [i, name] of chain.entries()) {
  // Замеры кладутся ОДНОЙ строкой перед последним хуком цепочки: последним стоит
  // наблюдатель, и это единственный, кому они нужны. Строка на каждый хук стоила
  // бы записи на диск на каждом шаге хода.
  if (i === chain.length - 1) appendTimings(event, timings);
  // После решения идут только хуки, которым положено видеть его (ALWAYS). Само
  // решение лежит в журнале — его пишет тот, кто решил, ещё до выхода из
  // процесса, поэтому оно видно и когда решивший хук следом упал.
  if (decided && !ALWAYS.has(name)) continue;
  await runHook(name);
  if (decided) continue;
  const grown = journalStamp(event);
  if (grown === stamp) continue;
  stamp = grown;
  decided = Boolean(decisionFor(event));
}
