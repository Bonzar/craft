// Решение хука: словарь исходов и запись решения в журнал. Форму ОТВЕТА, которую
// читает харнес, этот файл не знает — её собирает обёртка (decide-claude.js) и
// подаёт сюда печатником.
//
// Исходы (решение 17): allow, ask, deny, none — на вызове инструмента; block — на
// конце хода. Рядом с решением могут идти reason, add_context и modified_input, у
// block — message; в журнал из них уходит только КЛАСС причины.
//
// Канал между хуками одного события — журнал решений (decision-log.js), не общая
// память процесса. Строка ложится ДО печати и выхода: упавший следом хук своего
// решения уже не теряет.
import { appendDecision } from './decision-log.js';
import { reasonClass } from './reason-class.js';

export const OUTCOMES = Object.freeze({
  ALLOW: 'allow',
  ASK: 'ask',
  DENY: 'deny',
  NONE: 'none',
  BLOCK: 'block',
});

// Имя хука, который сейчас исполняется. Ставит диспетчер перед вызовом; вне
// диспетчера хук запущен поштучно и имени у него нет — это законно, поле пустое.
export function currentHook() {
  return process.env.CRAFT_HOOK_NAME || '';
}

// Записать решение и напечатать его. `print` даёт обёртка — она знает форму
// ответа своего харнеса; `event` — каноническое событие, из него берутся сессия и
// идентификатор вызова, по которым свёртка сшивает решение с вызовом.
//
// Порядок важен: сперва журнал, потом печать, потом выход. Печать может уйти в
// закрытый поток, а выход не разматывает ничего — решение обязано быть записано
// раньше обоих.
export function record(event, outcome, reason = '', { h = '', tool = '' } = {}) {
  const hook = currentHook();
  appendDecision(event, {
    outcome,
    hook,
    reasonClass: outcome === OUTCOMES.DENY || outcome === OUTCOMES.ASK
      ? reasonClass(hook, reason) : '',
    h,
    tool,
  });
}
