#!/usr/bin/env node
// Stop-хук: гейт закрытия разбора инцидента.
//
// Разбор закрывается самой слабой ступенью (правкой текста правила), а евал —
// машинная проверка урока — не заводится: дефект виден только когда Влад
// спросит «а где тесты». Хук ловит это в момент завершения хода.
//
// Предикат (не просто вопрос — проверяемый факт по транскрипту хода):
//   взведён маркер сигнала инцидента (ставит universal-detect-incident)
//   И в ходе была ЗАПИСЬ УРОКА — правка .claude/{skills,hooks,agents,rules,
//     commands} или craft_write
//   И НЕТ ни правки под evals/, ни письменного отказа («евал не завожу»)
// → один блок с чек-листом закрытия; повторное завершение проходит.
//
// Каждый НОВЫЙ сигнал инцидента снимает отметку «уже напоминали» (detect-incident),
// поэтому второй инцидент подряд в одной сессии гейтится заново.
// Анти-цикл: stop_hook_active → молчим. Автоном и евалы — молчим (некому
// закрывать разбор). Пустой session-id → молчим: маркер общий на все сессии,
// ложный блок дороже пропуска. Fail quiet везде.
import fs from 'node:fs';
import { readEvent } from './lib/event-claude.js';
import { block } from './lib/decide-claude.js';
import { hookOnce } from './lib/once.js';
import { incidentClosureMarker, sessionId } from './lib/paths.js';

// Служебный вложенный вызов — тот же класс, что евал: разбирать инцидент там некому.
if (process.env.CRAFT_AUTONOMOUS || process.env.CRAFT_EVAL || process.env.CRAFT_NESTED_CALL) {
  process.exit(0);
}
if (!sessionId()) process.exit(0);

const armed = incidentClosureMarker();
if (!fs.existsSync(armed)) process.exit(0);
const reminded = `${armed.replace(/\.armed$/, '')}.reminded`;
if (fs.existsSync(reminded)) process.exit(0);

const { raw, core, transcript, stop_active } = readEvent();
if (!hookOnce(raw, core, import.meta.url)) process.exit(0);
if (stop_active || process.env.CLAUDE_STOP_HOOK_ACTIVE === 'true') {
  process.exit(0);
}
if (!transcript || !fs.existsSync(transcript)) process.exit(0);

// Транскрипт — jsonl всей сессии; смотрим хвост (текущий ход и ближайший
// контекст): запись урока, евал-артефакт, письменный отказ.
let tail = '';
try {
  const fd = fs.openSync(transcript, 'r');
  try {
    const { size } = fs.fstatSync(fd);
    const want = Math.min(size, 400000);
    const buf = Buffer.alloc(want);
    fs.readSync(fd, buf, 0, want, size - want);
    tail = buf.toString('utf8');
  } finally {
    fs.closeSync(fd);
  }
} catch {
  process.exit(0);
}

const lesson = /"file_path":"[^"]*\/\.claude\/(skills|hooks|agents|rules|commands)\//.test(tail)
  || /__craft_write/.test(tail);
if (!lesson) process.exit(0);

const evalProof = /"file_path":"[^"]*\/evals\//.test(tail)
  || /евал не завожу|евалом не выражается|прогоном не выражается/i.test(tail);
if (evalProof) process.exit(0);

try {
  fs.writeFileSync(reminded, '');
} catch { /* не записалось — в худшем случае чек-лист придёт ещё раз */ }

block('Разбор инцидента не закрыт. Проверь по чек-листу: (1) названа ли ступень рычага словом; (2) приёмка поведенческая, а не «текст записан»; (3) заведён евал-кейс в evals/ или письменно обоснован отказ; (4) проверено, не рецидив ли это — тогда ступень обязана быть выше прежней. Закрыл или обосновал — завершай, повторное завершение пройдёт.');
