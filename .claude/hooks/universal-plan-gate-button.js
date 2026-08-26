#!/usr/bin/env node
// PostToolUse на AskUserQuestion: выбранный Владом ответ уходит в реестр
// одобренного тем же фоновым приёмом, что реплика. Разбор сам решает, заводить
// новую цель или дописать задачу к уже одобренной; окна записей как отдельного
// файла больше нет.
//
// Схема входа снята с живого следа сессии (см. отладочный след ниже):
// выбранный ответ лежит в .tool_response.answers — карта «текст вопроса →
// лейбл выбранной опции». Ответ по вопросу не разобрался — пара не пишется:
// вопроса без ответа в реестре не бывает. Событие рождается только настоящим
// тапом: PostToolUse не срабатывает на отклонённый вопрос.
import fs from 'node:fs';
import { readEvent } from './lib/event.js';
import { hookOnce } from './lib/once.js';
import { lastInputTrace, approvalRegistry } from './lib/paths.js';
import { ingestInBackground } from './lib/registry.js';

if (process.env.CRAFT_AUTONOMOUS) process.exit(0);

const { raw, event, tool, input, response } = readEvent();
if (!hookOnce(raw, event, import.meta.url)) process.exit(0);

// Отладочный след входа: по нему проверяются факты о схеме tool_response.
try {
  fs.writeFileSync(lastInputTrace('plan-gate-button'), raw);
} catch { /* след не записался — на решение это не влияет */ }

if (tool !== 'AskUserQuestion') process.exit(0);

// Вопрос о задаче-якоре в реестр не пишется. Он задаётся на старте КАЖДОЙ
// сессии, разрешением правки не является и работы не поручает — приём на нём
// вхолостую гонял бы модель на каждом старте. Опознаётся заголовком, который
// диктует директива якоря.
const ANCHOR_HEADER = 'Якорь сессии';

// Значение так, как его подставлял jq: строка остаётся собой, всё прочее
// сериализуется в JSON.
const asText = (value) => (typeof value === 'string' ? value : JSON.stringify(value));

const answers = (response && typeof response === 'object' && !Array.isArray(response) && response.answers)
  || input.answers
  || {};
const questions = Array.isArray(input.questions) ? input.questions : [];

// Пары «вопрос + ответ»: все вопросы вызова, у которых есть выбранный ответ.
// Один вызов законно несёт до четырёх вопросов, и каждая пара — свой факт.
const chosen = questions
  .map((q) => {
    const text = q && typeof q === 'object' ? q.question : undefined;
    if (typeof text !== 'string' || text === '') return null;
    if (q.header === ANCHOR_HEADER) return null;
    const answer = answers && typeof answers === 'object' ? answers[text] : undefined;
    if (answer === null || answer === undefined || answer === '') return null;
    return { question: text, answer: asText(answer) };
  })
  .filter(Boolean);

// Пары уходят в реестр тем же фоновым приёмом, что реплика: разбор сам решит,
// заводить новую цель или дописать задачу к уже одобренной.
for (const { question, answer } of chosen) {
  ingestInBackground(approvalRegistry(), 'button', `Вопрос: ${question}\nОтвет: ${answer}`);
}

// Окна записей больше нет: разрешение живёт целью в реестре, и сверка читает
// его оттуда.
