#!/usr/bin/env node
// Failure после показа плана — НЕ разрешение писать.
//
// Рычаг инцидента «387 строк без одобренного плана». Последовательность была такая:
// прерывание хода закрыло план-режим → показ упал с «You are not in plan mode» →
// харнесс написал «Exited Plan Mode. You can now make edits» → агент прочёл это как
// разрешение. Разрешения там нет: план Владу не показывался вовсе.
//
// Core returns a typed transition request. The adapter either performs a
// native next-turn transition or reports unsupported while staying blocked.
//
// Приходит ли это событие на ОТКЛОНЁННЫЙ Владом показ, не проверено. Не приходит —
// хук просто не запускается, ломаться нечему.
//
// Fail quiet на всём неожиданном.
import { readEvent } from './lib/event.js';
import { planRequired } from './lib/decide.js';
import { hookOnce } from './lib/once.js';
import { sha256 } from './lib/hash.js';

const { raw, event, action, route } = readEvent();
if (!hookOnce(raw, event, import.meta.url)) process.exit(0);
if (route !== 'plan.submit') process.exit(0);

planRequired('Показ плана не состоялся, значит одобрения НЕТ. Активный режим выполнения не изменился, а права на правки не открыты.', {
  blockedAction: action,
  originalIntent: event.originalIntent || '',
  transitionId: sha256(`${event.sessionId || ''}\n${raw}`),
});
