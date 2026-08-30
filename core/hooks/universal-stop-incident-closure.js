#!/usr/bin/env node
// Stop-хук: гейт закрытия разбора инцидента.
//
// Разбор закрывается самой слабой ступенью (правкой текста правила), а евал —
// машинная проверка урока — не заводится: дефект виден только когда Влад
// спросит «а где тесты». Хук ловит это в момент завершения хода.
//
// Предикат (не просто вопрос — проверяемый факт по транскрипту хода):
//   взведён маркер сигнала инцидента (ставит universal-detect-incident)
//   И в ходе была ЗАПИСЬ УРОКА — правка системного слоя или craft_write
//   И НЕТ ни правки под evals/, ни письменного отказа («евал не завожу»)
// → один блок с чек-листом закрытия; повторное завершение проходит.
//
// Каждый НОВЫЙ сигнал инцидента снимает отметку «уже напоминали» (detect-incident),
// поэтому второй инцидент подряд в одной сессии гейтится заново.
// Анти-цикл: stop_hook_active → молчим. Автоном и евалы — молчим (некому
// закрывать разбор). Пустой session-id → молчим: маркер общий на все сессии,
// ложный блок дороже пропуска. Fail quiet везде.
import fs from 'node:fs';
import { readEvent } from './lib/event.js';
import { block } from './lib/decide.js';
import { hookOnce } from './lib/once.js';
import { incidentClosureMarker, sessionId } from './lib/paths.js';
import { sessionEditedFiles } from './lib/session-files.js';
import { layoutPattern } from './lib/layout.js';

// Служебный вложенный вызов — тот же класс, что евал: разбирать инцидент там некому.
if (process.env.CRAFT_AUTONOMOUS || process.env.CRAFT_EVAL || process.env.CRAFT_NESTED_CALL) {
  process.exit(0);
}
if (!sessionId()) process.exit(0);

const armed = incidentClosureMarker();
if (!fs.existsSync(armed)) process.exit(0);
const reminded = `${armed.replace(/\.armed$/, '')}.reminded`;
if (fs.existsSync(reminded)) process.exit(0);

const { raw, event } = readEvent();
if (!hookOnce(raw, event, import.meta.url)) process.exit(0);
if (event.stopActive === true || process.env.CRAFT_STOP_HOOK_ACTIVE === 'true') {
  process.exit(0);
}
const tail = typeof event.assistantTurnText === 'string' ? event.assistantTurnText : '';

const changed = sessionEditedFiles(event.sessionEditedFiles);
const lesson = changed.some((file) => layoutPattern('lessonPattern').test(file))
  || layoutPattern('lessonPattern').test(tail);
if (!lesson) process.exit(0);

const evalProof = changed.some((file) => /\/evals\//.test(file))
  || /евал не завожу|евалом не выражается|прогоном не выражается/i.test(tail);
if (evalProof) process.exit(0);

try {
  fs.writeFileSync(reminded, '');
} catch { /* не записалось — в худшем случае чек-лист придёт ещё раз */ }

block('Разбор инцидента не закрыт. Проверь по чек-листу: (1) названа ли ступень рычага словом; (2) приёмка поведенческая, а не «текст записан»; (3) заведён евал-кейс в evals/ или письменно обоснован отказ; (4) проверено, не рецидив ли это — тогда ступень обязана быть выше прежней. Закрыл или обосновал — завершай, повторное завершение пройдёт.');
