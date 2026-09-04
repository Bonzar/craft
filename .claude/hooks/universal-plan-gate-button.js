#!/usr/bin/env node
// PostToolUse на AskUserQuestion: выбранный Владом ответ уходит в реестр
// одобренного тем же фоновым приёмом, что реплика. Разбор сам решает, заводить
// новую цель или дописать задачу к уже одобренной; окна записей как отдельного
// файла больше нет.
//
// Схема входа снята с живого события сессии: выбранный ответ лежит в
// .tool_response.answers — карте «текст вопроса → лейбл выбранной опции».
// Ответ по вопросу не разобрался — пара не пишется: вопроса без ответа в
// реестре не бывает. Событие рождается только настоящим
// тапом: PostToolUse не срабатывает на отклонённый вопрос.
import { readEvent } from './lib/event-claude.js';
import { hookOnce } from './lib/once.js';
import { approvalRegistry } from './lib/paths.js';
import { ingestInBackground, switchOn, switchOff } from './lib/registry.js';
import { withAgentContext } from './lib/transcript.js';

if (process.env.CRAFT_AUTONOMOUS) process.exit(0);

const { raw, core, tool, input, response, transcript } = readEvent();
if (!hookOnce(raw, core, import.meta.url)) process.exit(0);

if (tool !== 'AskUserQuestion') process.exit(0);

// Вопрос о задаче-якоре в реестр не пишется. Он задаётся на старте КАЖДОЙ
// сессии, разрешением правки не является и работы не поручает — приём на нём
// вхолостую гонял бы модель на каждом старте. Опознаётся заголовком, который
// диктует директива якоря.
const ANCHOR_HEADER = 'Якорь сессии';

// Тап по вопросу с этим заголовком снимает или возвращает проверки. Рубильник
// включает ТОЛЬКО Влад: агент такой вопрос по своей инициативе не задаёт.
const SWITCH_HEADER = 'Проверки';
const SWITCH_OFF = /^(сн(я|и)ть|без проверок|да)/i;

// Значение так, как его подставлял jq: строка остаётся собой, всё прочее
// сериализуется в JSON.
const asText = (value) => (typeof value === 'string' ? value : JSON.stringify(value));

const answers = (response && typeof response === 'object' && !Array.isArray(response) && response.answers)
  || input.answers
  || {};
const questions = Array.isArray(input.questions) ? input.questions : [];
// Заметки Влада к выбору: карта «текст вопроса → { notes }».
const annotations = input.annotations && typeof input.annotations === 'object' ? input.annotations : {};

// Пары «вопрос + ответ»: все вопросы вызова, у которых есть выбранный ответ.
// Один вызов законно несёт до четырёх вопросов, и каждая пара — свой факт.
const chosen = questions
  .map((q) => {
    const text = q && typeof q === 'object' ? q.question : undefined;
    if (typeof text !== 'string' || text === '') return null;
    if (q.header === ANCHOR_HEADER) return null;
    if (q.header === SWITCH_HEADER) {
      const chosen = answers && typeof answers === 'object' ? answers[text] : undefined;
      if (typeof chosen === 'string') {
        if (SWITCH_OFF.test(chosen.trim())) switchOn(approvalRegistry());
        else switchOff(approvalRegistry());
      }
      return null;
    }
    const answer = answers && typeof answers === 'object' ? answers[text] : undefined;
    if (answer === null || answer === undefined || answer === '') return null;
    const label = asText(answer);
    // Ярлык кнопки — два-три слова: «Вид у записи», «Да, обнови». Что именно
    // выбрано, сказано в ОПИСАНИИ варианта, и без него разбор получает материал,
    // из которого работы не собрать. Заметку Влада к выбору берём туда же:
    // она уточняет решение и в ярлык не помещается.
    const option = (Array.isArray(q.options) ? q.options : [])
      .find((o) => o && o.label === label);
    const note = annotations && typeof annotations === 'object' && annotations[text]
      ? annotations[text].notes
      : '';
    return {
      question: text,
      answer: label,
      description: option && typeof option.description === 'string' ? option.description : '',
      note: typeof note === 'string' ? note : '',
    };
  })
  .filter(Boolean);

// Пары уходят в реестр тем же фоновым приёмом, что реплика: разбор сам решит,
// заводить новую цель или дописать задачу к уже одобренной. Вместе с выбором
// уходит и то, на что Влад отвечает, — последнее сообщение агента: вопрос
// формулирует агент, но что стоит за вариантами, сказано в тексте перед ним.
for (const { question, answer, description, note } of chosen) {
  const said = [
    `Вопрос: ${question}`,
    `Ответ: ${answer}`,
    description ? `Что это значит: ${description}` : '',
    note ? `Заметка Влада: ${note}` : '',
  ].filter(Boolean).join('\n');
  ingestInBackground(approvalRegistry(), 'button', withAgentContext(transcript, said));
}

// Окна записей больше нет: разрешение живёт целью в реестре, и сверка читает
// его оттуда.
