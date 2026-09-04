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
import { OUTCOMES, record } from './decide.js';
import { readEvent, harnessEventName } from './event-claude.js';
import { EVENTS } from './event.js';
import { callHash } from './call-hash.js';
import { semanticInput } from './tool-flags-claude.js';

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

// Хеш вызова считает ОБЁРТКА: служебные поля входа этого харнеса отсеивает его
// адаптер, а общая часть их имён не знает. Хеш идёт в журнал решений, потому что у
// отказанного вызова записи метрик нет вовсе — цепочка обрывается на решении, и
// сшить «отказ, затем тот же вызов прошёл» больше не по чему.
function finish(outcome, reason, payload, options) {
  const event = readEvent();
  record(event, outcome, reason, {
    hook: currentHook(),
    tool: event.tool,
    h: event.tool ? callHash(event.tool, semanticInput(event.tool, event.input)) : '',
  });
  print(payload, options);
  process.exit(0);
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
export function inject(event, additionalContext, options) {
  const hookEventName = harnessEventName(event);
  record(readEvent(), OUTCOMES.NONE, '', { hook: currentHook() });
  print({ hookSpecificOutput: { hookEventName, additionalContext } }, options);
  process.exit(0);
}

// Проход. Гварды на проходе молчат — пустой stdout и есть «разрешено», и строки в
// журнале у него нет: молчание каждого гварда на каждом вызове раздуло бы журнал,
// а «решения не было» и значит allow.
export function allow() {
  process.exit(0);
}
