import { anchorRule } from './anchor.js';
import { switchRule } from './switch.js';
import { exemptScopeRule } from './exempt-scope.js';
import { registryRule } from './registry.js';

export const PLAN_GATE_RULES = [anchorRule, exemptScopeRule, switchRule, registryRule];

export function runPlanGateRules(context, rules = PLAN_GATE_RULES) {
  for (const rule of rules) {
    let result;
    try {
      result = rule(context);
    } catch {
      return { decision: 'deny', rule: 'failure', reason: 'Заблокировано план-гейтом: правило завершилось с ошибкой.' };
    }
    if (!result || result.decision === 'abstain') continue;
    if (result.decision === 'allow' || result.decision === 'deny') return result;
    return { decision: 'deny', rule: 'contract', reason: 'Заблокировано план-гейтом: правило вернуло невалидное решение.' };
  }
  return { decision: 'deny', rule: 'default', reason: 'Заблокировано план-гейтом: ни одно правило не разрешило вызов.' };
}
