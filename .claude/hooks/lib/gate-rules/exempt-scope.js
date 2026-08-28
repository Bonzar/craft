// Предодобренная зона Craft: без плана проходит команда, чьи цели ЦЕЛИКОМ лежат
// внутри страницы прямого редактирования (напр. «Продукты»).
//
// Ключ — ЦЕЛЬ ЗАПИСИ, а не формулировка: настоящая запись в проект или сферу
// несёт block-ID вне зоны и плана всё равно требует.
//
// Зона открывает гейт, но не отменяет якорь: правило якоря стоит раньше в
// списке, и сессия про продукты выбирает якорь наравне со всеми.
import fs from 'node:fs';
import { exemptScopeFile } from '../paths.js';

export const name = 'exempt-scope';

const UUID = /[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}/g;

function nonEmptyFile(file) {
  try {
    return fs.statSync(file).size > 0;
  } catch {
    return false;
  }
}

export function run(ctx) {
  if (ctx.intent.kind !== 'craft') return { decision: 'next' };

  const scopeFile = exemptScopeFile();
  const ids = [...new Set(String(ctx.input.command || '').match(UUID) || [])].sort();
  if (!ids.length || !nonEmptyFile(scopeFile)) return { decision: 'next' };

  let allowed = [];
  try {
    allowed = fs.readFileSync(scopeFile, 'utf8').split('\n');
  } catch {
    return { decision: 'next' }; // зона нечитаема — идём дальше по правилам
  }

  const inScope = ids.every((id) => allowed.includes(id.replace(/[a-f]/g, (c) => c.toUpperCase())));
  return inScope ? { decision: 'allow' } : { decision: 'next' };
}
