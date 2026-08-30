#!/usr/bin/env node
// Session anchor state has two transport roles only:
//   SessionStart — ask the user to choose an anchor task;
//   prompt/tool response — persist the explicit choice.
// Enforcement is a plan-gate rule, so one gate and one write-intent classifier
// decide every non-read tool call.
import fs from 'node:fs';
import path from 'node:path';
import { readEvent } from './lib/event.js';
import { hookOnce } from './lib/once.js';
import { sessionAnchor } from './lib/paths.js';

const ANCHOR_HEADER = 'Якорь сессии';
const DIRECTIVE = `[session-anchor] У этой сессии ещё нет задачи-якоря. До начала работы найди в базе задачи по теме запроса и спроси Влада, какая из них якорь этой сессии, — вопрос с заголовком «${ANCHOR_HEADER}», среди вариантов найденные задачи и «завести новую». Если у харнесса нет структурированного вопроса, попроси ответить строкой «Якорь сессии: <выбор>». До ответа свободны чтение, состояние разговора, файл плана и временные файлы; любой другой вызов блокирует plan-gate с подключённым правилом якоря. Новая задача заводится по смыслу — в свою сферу, внутрь проекта, в нужный этап.`;

if (process.env.CRAFT_AUTONOMOUS || process.env.CRAFT_EVAL || process.env.CRAFT_NESTED_CALL) process.exit(0);

const { raw, event, route, name, input, response } = readEvent();
if (!hookOnce(raw, event, import.meta.url)) process.exit(0);
const state = sessionAnchor();

function persistAnchor(value) {
  if (!state) return false;
  try {
    fs.mkdirSync(path.dirname(state), { recursive: true, mode: 0o700 });
    const tmp = `${state}.${process.pid}.tmp`;
    fs.writeFileSync(tmp, `${value}\n`, { mode: 0o600 });
    fs.renameSync(tmp, state);
    fs.chmodSync(state, 0o600);
    return true;
  } catch {
    return false;
  }
}

if (name === 'user.prompt' && state) {
  const prompt = String(event.prompt || '');
  const chosen = /^[ \t]*(?:`{1,3})?Якорь сессии:\s*(.+?)(?:`{1,3})?[ \t]*$/im.exec(prompt);
  if (chosen) {
    persistAnchor(chosen[1].trim());
  }
  process.exit(0);
}

if (name === 'session.start') {
  if (!state || fs.existsSync(state)) process.exit(0);
  process.stdout.write(`${DIRECTIVE}\n`);
  process.exit(0);
}

if (name === 'action.after' && route === 'session.question') {
  if (!state) process.exit(0);
  const questions = Array.isArray(input.questions) ? input.questions : [];
  const answers = (response && typeof response === 'object' && !Array.isArray(response) && response.answers)
    || input.answers || {};
  for (const question of questions) {
    if (!question || question.header !== ANCHOR_HEADER) continue;
    const answer = answers[question.question];
    if (answer === null || answer === undefined || answer === '') continue;
    persistAnchor(typeof answer === 'string' ? answer : JSON.stringify(answer));
    break;
  }
}
