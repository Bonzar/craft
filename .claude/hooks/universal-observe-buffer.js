#!/usr/bin/env node
// PostToolUse hook (все инструменты): пополняет СИГНАЛЬНЫЙ буфер сессии для
// инстинкт-контура. Пишутся только сигнальные события — ошибки инструментов;
// полный поток вызовов не логируется (шум, из которого нечего дистиллировать).
// Инцидент-маркеры дописывает universal-detect-incident тем же файлом.
//
// Буфер эфемерный (/tmp, per-session): это расходник для дистилляции в конце
// хода (universal-instinct-flush), терять его не жалко. Fail quiet.
//
// Уступки второму вызову здесь нет намеренно — её не было и у bash-версии:
// буфер копит сигналы, и лишняя строка в нём безобиднее пропущенной.
import fs from 'node:fs';
import { readEvent, responseIsError } from './lib/event.js';
import { observeBuffer } from './lib/paths.js';

const { event, tool, response } = readEvent();

// Индексирование не-объекта роняло jq, и хук выходил молча: строка или число в
// tool_response — не тот ответ, в котором ищут ошибку.
if (response !== undefined && response !== null
    && (typeof response !== 'object' || Array.isArray(response))) process.exit(0);
const res = response || {};

// Значение поля так, как его печатал `jq -r '… | tostring'`: строка остаётся
// собой, всё прочее сериализуется в JSON.
function asText(value) {
  if (value === undefined) return 'null';
  if (typeof value === 'string') return value;
  return JSON.stringify(value);
}

// Пусто по правилам jq: null и false уступают следующему кандидату.
function firstSet(...values) {
  for (const v of values) if (v !== null && v !== undefined && v !== false) return v;
  return '';
}

function headBytes(text, limit) {
  const cut = Buffer.from(`${text}\n`, 'utf8').subarray(0, limit);
  let end = cut.length;
  while (end > 0 && cut[end - 1] === 0x0a) end -= 1;
  return cut.subarray(0, end).toString('utf8');
}

// Ошибка инструмента опознаётся ОБЩИМ предикатом (lib/event.js): буфер и
// метрики обязаны считать ошибкой одно и то же, а две копии этой логики уже
// разъехались по полю error.
if (!responseIsError(res)) process.exit(0);
// Откуда брать текст, решает форма ответа: у помеченного is_error он лежит в
// содержимом, у прочих — в самом поле error.
let errText;
if (asText(firstSet(res.is_error, res.isError, false)) === 'true') {
  errText = headBytes(asText(firstSet(res.content, res.error, '')), 300);
} else {
  errText = headBytes(asText(firstSet(res.error, '')), 300);
  if (!errText || errText === 'null') process.exit(0);
}

// Однострочная запись: перевод строки в тексте ошибки схлопывается. Хвостовой
// пробел остаётся от того же схлопывания у bash-версии — строки буфера читает
// дистиллятор, и менять их форму на переезде нельзя.
const toolName = asText(firstSet(event.tool_name, '?'));
const line = `tool-error ${toolName}: ${errText.replace(/\n/g, ' ')} \n`;
try {
  fs.appendFileSync(observeBuffer(), line);
} catch { /* буфер не пополнился — расходник, терять не жалко */ }
