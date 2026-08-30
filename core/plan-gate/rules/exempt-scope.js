import fs from 'node:fs';

export function exemptScopeRule(context) {
  if (context.intent.category !== 'database' || !context.intent.ids.length || !context.scopeFile) {
    return { decision: 'abstain', rule: 'exempt-scope' };
  }
  let allowed = [];
  try { allowed = fs.readFileSync(context.scopeFile, 'utf8').split('\n'); } catch { return { decision: 'abstain', rule: 'exempt-scope' }; }
  const inside = context.intent.ids.every((id) => allowed.includes(id.toUpperCase()));
  return inside ? { decision: 'allow', rule: 'exempt-scope' } : { decision: 'abstain', rule: 'exempt-scope' };
}
