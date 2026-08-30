#!/usr/bin/env node
// Read-action guard: во время замера закрывает от агента его
// материалы — кейсы, раннер и сохранённые прогоны. В них лежат ожидаемые
// ответы, и чтение обесценивает измерение.
//
// Действует ТОЛЬКО внутри замера: в обычной сессии агент работает с эвалами как
// с любым другим кодом. Fail open на всём неожиданном.
import { readEvent } from './lib/event.js';
import { deny } from './lib/decide.js';
import { hookOnce } from './lib/once.js';

if (!process.env.CRAFT_EVAL) process.exit(0);

const { raw, event, input } = readEvent();
if (!hookOnce(raw, event, import.meta.url)) process.exit(0);

const target = [input.target || '', input.query || ''].join(' ');
if (target.trim() === '') process.exit(0);

const CLOSED = /evals\/cases|evals\/lib|evals\/runs|evals\/run-[a-z-]*\.sh|evals\/selftest\.sh/;
if (CLOSED.test(target)) {
  // Развёрнутая форма ответа — та же, что печатала bash-версия.
  deny('Это материалы замера, в котором ты сейчас участвуешь: кейсы, раннер и сохранённые прогоны. В них лежат ожидаемые ответы, и чтение обесценивает измерение. Решай задачу по существу — правила, скиллы и остальной код читать можно.', { compact: false });
}
process.exit(0);
