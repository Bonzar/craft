#!/usr/bin/env node
// Якорь сессии: у каждой сессии есть своя задача в базе — место, куда ложится
// «надо сделать», итог работы и ссылки на всё, что сессия создала. Правило живёт
// в базе, подстраницей роутера; здесь только его машинная часть.
//
// ТРИ РОЛИ ОДНОГО ФАЙЛА:
//   SessionStart  — кладёт в контекст директиву выбрать якорь;
//   PostToolUse   — принимает ТАП по кнопке якорного вопроса и запоминает выбор;
//   PreToolUse    — гвард: пока выбора нет, запись не идёт.
//
// ГЕЙТ ОТКРЫВАЕТ ТАП, А НЕ ПОЯВИВШИЙСЯ BLOCK-ID. Заведение самой задачи-якоря —
// тоже запись в базу: гвард, стоящий на «в файле есть block-ID», запирал бы сам
// себя у любой сессии, для которой подходящей задачи ещё нет. Поэтому открывает
// его ответ Влада, а какой именно задачей всё кончится — дело самой сессии.
//
// Опознаётся якорный вопрос заголовком ANCHOR_HEADER: директива старта диктует
// его дословно, и это метка нашего контура, а не догадка по тексту вопроса.
// Событие рождается только настоящим тапом — PostToolUse не срабатывает на
// отклонённый вопрос (то же свойство, на котором стоит universal-plan-gate-button).
//
// Молчит там, где спрашивать некого: автономные рутины и замеры (CRAFT_AUTONOMOUS,
// CRAFT_EVAL), вложенный вызов подагента (CRAFT_NESTED_CALL) и пустой
// идентификатор сессии — у последнего файла состояния нет вовсе, и отказ запер бы
// сессию насмерть. Fail open во всех этих случаях сознателен: гвард якоря страхует
// дисциплину, а не безопасность.
import fs from 'node:fs';
import { readEvent } from './lib/event.js';
import { deny } from './lib/decide.js';
import { hookOnce } from './lib/once.js';
import { sessionAnchor } from './lib/paths.js';
import { isEphemeral, gitEphemeral, bashWriteTargets, cleanTarget } from './lib/write-targets.js';

const ANCHOR_HEADER = 'Якорь сессии';

const DIRECTIVE = `[session-anchor] У этой сессии ещё нет задачи-якоря. До первой записи найди в базе задачи по теме запроса и спроси Влада кнопками, какая из них якорь этой сессии, — вопрос с заголовком «${ANCHOR_HEADER}», среди вариантов найденные задачи и «завести новую». Пока Влад не ответил, работа не начинается: чтение базы и разведка идут, записи нет. Новая задача заводится по смыслу — в свою сферу, внутрь проекта, в нужный этап.`;

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
  if (state && fs.existsSync(state)) process.exit(0);
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
    } catch { /* не записалось — гвард спросит ещё раз, это не потеря данных */ }
    break;
  }
  process.exit(0);
}

// --- Гвард записи ------------------------------------------------------------
if (name && name !== 'PreToolUse') process.exit(0);
// Пустой идентификатор сессии: файла состояния не существует в принципе, и
// отказывать по нему значило бы запереть сессию без единого способа открыться.
if (!state) process.exit(0);
if (fs.existsSync(state) && fs.readFileSync(state, 'utf8').trim() !== '') process.exit(0);

const isFileEdit = ['Write', 'Edit', 'MultiEdit', 'NotebookEdit'].includes(tool);
const isCraftWrite = /^mcp__.*__craft_write$/.test(tool);
const isBash = tool === 'Bash';

const refuse = (what) => deny(`Заблокировано якорем сессии: ${what} идёт без задачи-якоря. Спроси Влада кнопками, какая задача базы будет якорем этой сессии (вопрос с заголовком «${ANCHOR_HEADER}»: найденные по теме задачи и «завести новую»), и продолжай после ответа. Автономному прогону — CRAFT_AUTONOMOUS=1.`);

if (isCraftWrite) refuse('запись в базу');

if (isFileEdit) {
  const fp = input.file_path || input.notebook_path || '';
  if (!fp) process.exit(0);
  if (isEphemeral(fp) || gitEphemeral(fp)) process.exit(0);
  refuse(`правка файла (${fp})`);
}

if (isBash) {
  const cmd = input.command || '';
  if (!cmd) process.exit(0);
  for (const rawTarget of bashWriteTargets(cmd)) {
    const t = cleanTarget(rawTarget);
    if (!t) continue;
    if (isEphemeral(t) || gitEphemeral(t)) continue;
    refuse(`запись в файл (${t})`);
  }
}

process.exit(0);
