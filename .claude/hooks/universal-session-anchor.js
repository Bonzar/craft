#!/usr/bin/env node
// Якорь сессии: у каждой сессии есть своя задача в базе — место, куда ложится
// «надо сделать», итог работы и ссылки на всё, что сессия создала. Правило живёт
// в базе, подстраницей роутера; здесь его машинная часть.
//
// ДВЕ РОЛИ ОДНОГО ФАЙЛА:
//   SessionStart  — кладёт в контекст директиву выбрать якорь;
//   PostToolUse   — принимает ТАП по кнопке якорного вопроса и запоминает выбор.
//
// Блокировкой этот файл больше не занимается: правило якоря подключено к
// план-гейту плагином (lib/gate-rules/anchor.js) и судит по единственному на весь
// контур определению записи. Своё определение у гварда расходилось с гейтовым —
// одна и та же команда у одного была записью, у другого нет.
//
// ГЕЙТ ОТКРЫВАЕТ ТАП, А НЕ ПОЯВИВШИЙСЯ BLOCK-ID. Заведение самой задачи-якоря —
// тоже запись в базу: правило, стоящее на «в файле есть block-ID», запирало бы
// сессию, для которой подходящей задачи ещё нет. Поэтому открывает его ответ
// Влада, а какой именно задачей всё кончится — дело самой сессии.
//
// Опознаётся якорный вопрос заголовком ANCHOR_HEADER: директива старта диктует
// его дословно, и это метка нашего контура, а не догадка по тексту вопроса.
// Событие рождается только настоящим тапом — PostToolUse не срабатывает на
// отклонённый вопрос (то же свойство, на котором стоит universal-plan-gate-button).
import fs from 'node:fs';
import { readEvent } from './lib/event.js';
import { hookOnce } from './lib/once.js';
import { sessionAnchor } from './lib/paths.js';
import { ANCHOR_HEADER } from './lib/gate-rules/anchor.js';

const DIRECTIVE = `[session-anchor] У этой сессии ещё нет задачи-якоря. До начала работы найди в базе задачи по теме запроса и спроси Влада кнопками, какая из них якорь этой сессии, — вопрос с заголовком «${ANCHOR_HEADER}», среди вариантов найденные задачи и «завести новую». Пока Влад не ответил, идёт всё, что не пишет в постоянные места: чтение базы и файлов, разбор, команды без записи, файл плана и временные файлы. Запись до ответа закрыта план-гейтом — правку файлов, команду с целью записи или меняющую рабочее дерево, запись в базу он не пропустит. Новая задача заводится по смыслу — в свою сферу, внутрь проекта, в нужный этап.`;

if (process.env.CRAFT_AUTONOMOUS || process.env.CRAFT_EVAL || process.env.CRAFT_NESTED_CALL) {
  process.exit(0);
}

const { raw, event, tool, name, input, response } = readEvent();
if (!hookOnce(raw, event, import.meta.url)) process.exit(0);

const state = sessionAnchor();

// --- Старт сессии ------------------------------------------------------------
// Печать голым текстом, как у остальных инжекторов старта: харнесс кладёт stdout
// SessionStart-хука в контекст сам.
if (name === 'SessionStart') {
  // Пустой идентификатор сессии: запомнить ответ негде, и правило якоря всё равно
  // молчит — просить выбор, который ни на что не влияет, нечестно.
  if (!state) process.exit(0);
  if (fs.existsSync(state)) process.exit(0);
  process.stdout.write(`${DIRECTIVE}\n`);
  process.exit(0);
}

// --- Приём тапа по якорному вопросу -----------------------------------------
if (name === 'PostToolUse' && tool === 'AskUserQuestion') {
  if (!state) process.exit(0);
  const questions = Array.isArray(input.questions) ? input.questions : [];
  const answers = (response && typeof response === 'object' && !Array.isArray(response) && response.answers)
    || input.answers
    || {};

  for (const q of questions) {
    if (!q || typeof q !== 'object' || q.header !== ANCHOR_HEADER) continue;
    const text = typeof q.question === 'string' ? q.question : '';
    const answer = text && answers && typeof answers === 'object' ? answers[text] : undefined;
    if (answer === null || answer === undefined || answer === '') continue;
    const chosen = typeof answer === 'string' ? answer : JSON.stringify(answer);
    try {
      fs.writeFileSync(state, `${chosen}\n`);
    } catch { /* не записалось — правило спросит ещё раз, это не потеря данных */ }
    break;
  }
  process.exit(0);
}

process.exit(0);
