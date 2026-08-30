#!/usr/bin/env node
// PreToolUse на показе плана: не ПОВТОРЯТЬ показ в ходе, начатом СЛУЖЕБНЫМ
// сообщением.
//
// План-режим при отсутствии Влада закрывается сам, следом приходит техническое
// продолжение хода — и показ соблазняет повторить. Повтор ничего не даёт: Влад
// не отвечал, закроется снова.
//
// Запрещён именно ПОВТОР, а не показ вообще. Первая версия гейта судила по
// одному признаку «ход служебный» и блокировала первый показ исправленного
// плана. Различитель — ИЗМЕНИЛСЯ ЛИ ПЛАН: при пропуске гейт запоминает хеш файла
// плана и отклоняет только совпадение. План правился после замечания Влада — это
// новый показ, а не повтор.
//
// Метку служебного хода ставит и снимает хук сброса периметра: он уже разбирает
// словарь якорей. Он же снимает хеш показа — реплика Влада начинает разговор
// заново.
//
// Файла плана нет — судить не по чему, гейт молчит: пропущенный повтор дешевле
// заблокированного показа. Fail open на всём неожиданном.
import fs from 'node:fs';
import { readEvent } from './lib/event.js';
import { deny } from './lib/decide.js';
import { hookOnce } from './lib/once.js';
import { sha256File } from './lib/hash.js';
import { planFileMarker, planShownMarker, serviceTurnMarker } from './lib/paths.js';

const { raw, event, route } = readEvent();
if (!hookOnce(raw, event, import.meta.url)) process.exit(0);
if (route !== 'plan.submit') process.exit(0);

function planPath() {
  if (process.env.CRAFT_PLAN_FILE) return process.env.CRAFT_PLAN_FILE;
  try {
    return fs.readFileSync(planFileMarker(), 'utf8').trim();
  } catch {
    return '';
  }
}

const plan = planPath();
const now = plan ? sha256File(plan) : '';
const shown = planShownMarker();

let lastShown = '';
try {
  lastShown = fs.readFileSync(shown, 'utf8').trim();
} catch { /* показов ещё не было */ }

if (fs.existsSync(serviceTurnMarker()) && now && now === lastShown) {
  deny('Ход начат служебным сообщением, а план с прошлого показа не менялся — это повтор. План уже показан и закрылся сам: Влада не было. Дождись его реплики, ничего не выполняй и план текстом не пересказывай. Правил план по замечанию — показывай, повтором это не считается.');
}

// Показ проходит — запоминаем, ЧТО именно показано.
if (now) {
  try {
    fs.writeFileSync(shown, `${now}\n`);
  } catch { /* не записалось — гейт просто не поймает следующий повтор */ }
}
process.exit(0);
