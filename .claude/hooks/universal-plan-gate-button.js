#!/usr/bin/env node
// PostToolUse на AskUserQuestion: регистратор пар «вопрос + выбранный ответ»
// для семантического разрешения план-гейта. Каждый завершённый вопрос с
// ответом ложится записью в файл окна разрешений у маркера периметра; гейт
// (universal-guard-plan-gate) на правке вне периметра сверяет её с этим
// окном классификатором — явное разрешение открывает цель. Дословного лейбла
// и маркера тапа больше нет: решение «это было разрешение» принимает модель,
// а не совпадение строки.
//
// Схема входа снята с живого следа сессии (см. отладочный след ниже):
// выбранный ответ лежит в .tool_response.answers — карта «текст вопроса →
// лейбл выбранной опции». Ответ по вопросу не разобрался — пара не пишется:
// окна из одних вопросов без ответов не бывает. Событие рождается только
// настоящим тапом: PostToolUse не срабатывает на отклонённый вопрос.
//
// Окно — последние 5 записей (вместе с репликами-указаниями, которые пишет
// universal-plan-gate-reset), старые вытесняются. Гасит окно только смена
// сессии — файл в /tmp с session-id.
import fs from 'node:fs';
import { readEvent } from './lib/event.js';
import { hookOnce } from './lib/once.js';
import { permissionWindow, lastInputTrace } from './lib/paths.js';
import { appendRecord } from './lib/qa-window.js';

if (process.env.CRAFT_AUTONOMOUS) process.exit(0);

const { raw, event, tool, input, response } = readEvent();
if (!hookOnce(raw, event, import.meta.url)) process.exit(0);

// Отладочный след входа: по нему проверяются факты о схеме tool_response.
try {
  fs.writeFileSync(lastInputTrace('plan-gate-button'), raw);
} catch { /* след не записался — на решение это не влияет */ }

if (tool !== 'AskUserQuestion') process.exit(0);

const qa = permissionWindow();
if (!qa) process.exit(0);

// Вопрос о задаче-якоре в окно не пишется. Он задаётся на старте КАЖДОЙ сессии,
// разрешением правки не является, а окно держит последние пять записей — иначе
// якорь вытеснял бы из него настоящий ответ Влада и гонял бы классификатор на
// каждой записи. Опознаётся заголовком, который диктует директива якоря.
const ANCHOR_HEADER = 'Якорь сессии';

// Значение так, как его подставлял jq: строка остаётся собой, всё прочее
// сериализуется в JSON.
const asText = (value) => (typeof value === 'string' ? value : JSON.stringify(value));

const answers = (response && typeof response === 'object' && !Array.isArray(response) && response.answers)
  || input.answers
  || {};
const questions = Array.isArray(input.questions) ? input.questions : [];

// Пары «вопрос + ответ»: все вопросы вызова, у которых есть выбранный ответ.
const pairs = questions
  .map((q) => {
    const text = q && typeof q === 'object' ? q.question : undefined;
    if (typeof text !== 'string' || text === '') return '';
    if (q.header === ANCHOR_HEADER) return '';
    const answer = answers && typeof answers === 'object' ? answers[text] : undefined;
    if (answer === null || answer === undefined || answer === '') return '';
    return `## Запись: вопрос\nВопрос: ${text}\nОтвет: ${asText(answer)}\n`;
  })
  .filter(Boolean)
  .join('\n');
if (!/\S/.test(pairs)) process.exit(0);

// Запись оканчивается ровно одним переводом строки: у bash-версии хвостовые
// переводы срезала подстановка команды, и один добавлял printf.
appendRecord(qa, `${pairs.replace(/\n+$/, '')}\n`);
