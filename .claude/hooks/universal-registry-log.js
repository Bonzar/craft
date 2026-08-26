#!/usr/bin/env node
// PostToolUse на правках: дописывает в реестр, что сделано под целью.
//
// Лог пишется ПО ФАКТУ, а не при разрешении. Гейт срабатывает до инструмента, и
// запись оттуда фиксировала бы намерение: упавшая или отклонённая следом правка
// считалась бы сделанной, и следующая сверка судила бы от неверной картины.
//
// Что писать, гейт уже знает: сверка вернула адрес покрывшей задачи и одну
// фразу «что сделано» — отдельного вызова модели тут нет. Гейт кладёт это
// меткой рядом с реестром, а метка привязана к идентификатору вызова
// инструмента: без привязки её съела бы ближайшая правка, прошедшая мимо
// модели (эфемерная, файл плана), а параллельные вызовы одного блока перетёрли
// бы метки друг друга — в обоих случаях лог сел бы на чужую цель.
//
// Метка одноразовая: снимается той же записью, что её потратила.
import fs from 'node:fs';
import { readEvent } from './lib/event.js';
import { approvalRegistry } from './lib/paths.js';
import { readRegistry, appendLog } from './lib/registry.js';

if (process.env.CRAFT_AUTONOMOUS) process.exit(0);

const { event, tool, input } = readEvent();
const registry = approvalRegistry();
if (!registry) process.exit(0);

const markFile = `${registry}.covered`;
let mark = null;
try {
  mark = JSON.parse(fs.readFileSync(markFile, 'utf8'));
} catch {
  process.exit(0);
}
if (!mark || !mark.task) process.exit(0);

// Метка принадлежит ТОМУ вызову, для которого её выдала сверка. Событие от
// другого вызова её не тратит и не гасит: свой хозяин ещё придёт.
if (mark.tool_use_id && event.tool_use_id && mark.tool_use_id !== event.tool_use_id) process.exit(0);

try {
  fs.rmSync(markFile, { force: true });
} catch { /* метка не снялась — следующая запись перезапишет её целиком */ }

// Адрес задачи вида Ц1.2: первое число — цель по порядку в реестре, второе —
// задача внутри неё. Цель адресуется номером, а не заголовком: заголовок модель
// каждый раз формулирует своими словами.
const at = /^Ц(\d+)\.(\d+)$/.exec(String(mark.task));
if (!at) process.exit(0);
const goal = readRegistry(registry)[Number(at[1]) - 1];
if (!goal) process.exit(0);

const target = input.file_path || input.notebook_path || input.command || '';
const short = String(target).split('/').slice(-1)[0] || tool;
appendLog(registry, goal.title, `задача ${mark.task} · ${short} · ${mark.said || 'правка выполнена'}`);
