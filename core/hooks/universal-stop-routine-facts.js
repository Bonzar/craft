#!/usr/bin/env node
// Stop-хук: энфорсер фактов завершения автономных рутин (редукция delivery-gate
// из ECC — сознательная: пороги диска/библиотек не наш контекст, mtime/cutoff
// хук проверить не может, он не знает рутину; сами факты живут в секциях
// «Факты завершения» SKILL-доков рутин).
//
// В автономном режиме (CRAFT_AUTONOMOUS=1) первое завершение сессии блокируется
// ОДИН раз директивой самопроверки фактов; повторное проходит (маркер, как у
// instinct-flush). Headless-евалы исключены признаком CRAFT_EVAL=1 — иначе хук
// ломал бы детерминизм каждого евал-кейса лишним ходом.
// Анти-цикл: stop_hook_active → молчим. Fail quiet.
import fs from 'node:fs';
import { readEvent } from './lib/event.js';
import { block } from './lib/decide.js';
import { hookOnce } from './lib/once.js';
import { routineFactsMarker } from './lib/paths.js';

if (!process.env.CRAFT_AUTONOMOUS) process.exit(0);
// Евал и служебный вложенный вызов исключены одной причиной: у обоих нет рутины,
// чьи «Факты завершения» можно сверить, а лишний ход ломает сам вызов.
if (process.env.CRAFT_EVAL || process.env.CRAFT_NESTED_CALL) process.exit(0);

const { raw, event } = readEvent();
if (!hookOnce(raw, event, import.meta.url)) process.exit(0);
if (event.stopActive === true || process.env.CRAFT_STOP_HOOK_ACTIVE === 'true') {
  process.exit(0);
}

const marker = routineFactsMarker();
if (fs.existsSync(marker)) process.exit(0);
try {
  fs.writeFileSync(marker, '');
} catch { /* не записалось — в худшем случае директива придёт ещё раз */ }

block('[stop-hook] Перед завершением сверь секцию «Факты завершения» своего SKILL-дока: каждый факт проверен чтением или командой, не заявлен. Несходящийся факт — блокер в отчёте, не примечание. Сверил — завершай, повторное завершение пройдёт.');
