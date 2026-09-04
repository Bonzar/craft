// Обёртка решения для Claude Code: форма ответа, которую читает этот харнес, и
// имена его событий. Общая часть (decide.js) знает только словарь исходов.
//
// Компактный JSON одной строкой плюс перевод строки; форма и порядок ключей — как
// у jq. Развёрнутая форма (отступ в два пробела) осталась второй: харнесу разницы
// нет, но кейсы сверяют вывод побайтно.
//
// Каждое решение сперва ложится строкой в журнал решений (decide.record), и лишь
// потом печатается: печать может уйти в закрытый поток, а process.exit ничего не
// разматывает.
//
// Не записалось — строка кладётся В ЖУРНАЛ МЕТРИК, туда же, откуда её возьмёт
// свёртка. Молчание тут читалось бы как «решения не было», то есть как проход: у
// отказа поменялся бы ЗНАК, а не точность. Оба журнала лежат в одном каталоге, так
// что запасной путь спасает ровно те случаи, когда сорвался один файл, а не весь
// каталог; отказ обоих виден тем, что сводки не станет вовсе.
import { OUTCOMES, record } from './decide.js';
import { markDecided } from './decided.js';
import { readEvent, harnessEventName } from './event-claude.js';
import { EVENTS } from './event.js';
import { append, currentMetricsLog } from './metrics.js';

function print(payload, { compact = true } = {}) {
  const text = compact ? JSON.stringify(payload) : JSON.stringify(payload, null, 2);
  process.stdout.write(`${text}\n`);
}

// Имя исполняемого сейчас хука ставит диспетчер перед вызовом. Читает его ОБЁРТКА
// и передаёт общей части значением: вне диспетчера хук запущен поштучно, имени у
// него нет, и это законно — поле остаётся пустым.
function currentHook() {
  return process.env.CRAFT_HOOK_NAME || '';
}

// Записать решение и напечатать ответ. Пометка «решение принято» ставится ПЕРВОЙ и
// не зависит от записи журнала: цепочку обрывает она, и отказ диска не имеет права
// её снять. Ставится она здесь, а не в общей части: сюда приходят только
// исключающие исходы, и только они дают харнесу ответ, второй экземпляр которого
// он прочитать не должен.
function finish(outcome, reason, payload, options) {
  const event = readEvent();
  markDecided();
  keep(record(event, outcome, reason, { hook: currentHook() }));
  print(payload, options);
  process.exit(0);
}

// Запасной путь для строки, не легшей в журнал решений.
function keep({ ok, line }) {
  if (ok) return;
  append(currentMetricsLog(), line);
}

// PreToolUse: запрет вызова с причиной, которую прочитает модель.
export function deny(reason, options) {
  finish(OUTCOMES.DENY, reason, {
    hookSpecificOutput: {
      hookEventName: harnessEventName(EVENTS.PRE_TOOL),
      permissionDecision: 'deny',
      permissionDecisionReason: reason,
    },
  }, options);
}

// PreToolUse: вызов уходит человеку на подтверждение — сильнее запрета, потому
// что требует живого решения, а не повторной попытки агента.
export function ask(reason) {
  finish(OUTCOMES.ASK, reason, {
    hookSpecificOutput: {
      hookEventName: harnessEventName(EVENTS.PRE_TOOL),
      permissionDecision: 'ask',
      permissionDecisionReason: reason,
    },
  });
}

// Stop: ход не заканчивается, агенту возвращается причина.
export function block(reason) {
  finish(OUTCOMES.BLOCK, reason, { decision: 'block', reason });
}

// Текст, дописываемый в контекст агента. Имя события идёт параметром и берётся из
// НАШЕГО словаря (EVENTS): один и тот же приём работает на старте сессии, на
// реплике, после вызова и после его провала, а харнес сверяет имя с тем событием,
// на которое хук подписан. Перевод в имя харнеса — здесь.
//
// Строка в журнал ложится, а цепочка НЕ обрывается: дописанный контекст не
// исключает чужого, и инжекторов в цепочке бывает несколько подряд. Пометку
// ставит только finish() — на запрете, вопросе и блокировке.
export function inject(event, additionalContext, options) {
  const hookEventName = harnessEventName(event);
  keep(record(readEvent(), OUTCOMES.NONE, '', { hook: currentHook() }));
  print({ hookSpecificOutput: { hookEventName, additionalContext } }, options);
  process.exit(0);
}

// Прохода отдельным входом ЗДЕСЬ НЕТ. Гвард на проходе молча выходит сам: пустой
// stdout и есть «разрешено», и строки в журнале у него нет — молчание каждого
// гварда на каждом вызове раздуло бы журнал, а «решения не было» и значит allow.
