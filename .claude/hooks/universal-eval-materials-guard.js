#!/usr/bin/env node
// PreToolUse(Read|Grep|Glob) guard: во время замера закрывает от агента его
// материалы — кейсы, раннер и сохранённые прогоны. В них лежат ожидаемые
// ответы, и чтение обесценивает измерение.
//
// Действует ТОЛЬКО внутри замера: в обычной сессии агент работает с эвалами как
// с любым другим кодом. Fail open на всём неожиданном.
import { readEvent } from './lib/event-claude.js';
import { deny } from './lib/decide-claude.js';
import { hookOnce } from './lib/once.js';

if (!process.env.CRAFT_EVAL) process.exit(0);

const { raw, core, input } = readEvent();
if (!hookOnce(raw, core, import.meta.url)) process.exit(0);

// Путь приходит в любом из трёх полей: у чтения файла, у поиска по каталогу и
// самим шаблоном у обхода — проверяем все, иначе обход тривиален.
const target = [input.file_path || '', input.path || '', input.pattern || ''].join(' ');
if (target.trim() === '') process.exit(0);

const CLOSED = /evals\/cases|evals\/lib|evals\/runs|evals\/run-[a-z-]*\.sh|evals\/selftest\.sh/;
if (CLOSED.test(target)) {
  // Развёрнутая форма ответа — та же, что печатала bash-версия.
  deny('Это материалы замера, в котором ты сейчас участвуешь: кейсы, раннер и сохранённые прогоны. В них лежат ожидаемые ответы, и чтение обесценивает измерение. Решай задачу по существу — правила, скиллы и остальной код читать можно.', { compact: false });
}
process.exit(0);
