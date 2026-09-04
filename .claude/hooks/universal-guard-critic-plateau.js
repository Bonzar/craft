#!/usr/bin/env node
// PreToolUse на запуске подагента: машинный гейт плато обкатки — четвёртый
// прогон критика планов по одному плану блокируется.
//
// Правило «после трёх прогонов — показ с открытым вопросом» перестаёт быть
// дисциплиной агента: счётчик завершённых прогонов ведёт хук отметки критика,
// обнуляет его одобрение плана, а этот гейт счётчик только читает.
//
// Гейтятся роли, которые КРУТЯТ счётчик, — те же, что ставят отметку: сводящий
// веера и одиночный критик. Юнитные критики и критик швов прогон не засчитывают,
// и блокировать их нечем: круг веера считается один раз, по своему сводящему.
// Чужие подагенты не трогаются вовсе.
//
// Автономный прогон обходит гейт: планов он не показывает. Fail open на всём
// неожиданном.
import fs from 'node:fs';
import { readEvent } from './lib/event-claude.js';
import { deny } from './lib/decide-claude.js';
import { hookOnce } from './lib/once.js';
import { planCriticRuns } from './lib/paths.js';

if (process.env.CRAFT_AUTONOMOUS) process.exit(0);

const { raw, core, tool, input } = readEvent();
if (!hookOnce(raw, core, import.meta.url)) process.exit(0);
if (tool !== 'Task' && tool !== 'Agent') process.exit(0);

const role = input.subagent_type || '';
if (role !== 'plan-critic' && role !== 'plan-critic-verdict') process.exit(0);

let runs = '';
try {
  runs = fs.readFileSync(planCriticRuns(), 'utf8').trim();
} catch { /* счётчика ещё нет — обкатка не начиналась */ }
if (!/^[0-9]+$/.test(runs)) process.exit(0);

if (Number(runs) >= 3) {
  deny(`Заблокировано гейтом плато: по этому плану уже ${runs} завершённых прогона критика. Плато — показывай план Владу с открытым вопросом об остатке замечаний, а не гоняй обкатку дальше. Счётчик обнулит одобрение плана.`);
}
process.exit(0);
