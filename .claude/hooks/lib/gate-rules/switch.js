// Рубильник Влада: проверки сняты, но след остаётся — каждая пропущенная правка
// ложится в лог его записи. Молча пропускать нельзя: иначе непонятно, что
// делалось, пока проверки не работали.
//
// Снять проверки можно ТОЛЬКО тапом Влада. Ни переменная окружения, ни режим
// харнесса, ни автономный прогон гейт не открывают: у них не остаётся следа, а
// задание рутины и так ложится в реестр целью.
import { switchAt, appendLog } from '../registry.js';

export const name = 'switch';

export function run(ctx) {
  const goals = ctx.registry();
  const off = switchAt(goals);
  if (off < 0) return { decision: 'next' };

  appendLog(ctx.registryFile, off, `без сверки · ${String(ctx.description()).split('\n')[0].slice(0, 120)}`);
  return { decision: 'allow' };
}
