export function switchRule(context) {
  const at = context.switchAt(context.goals());
  if (at < 0) return { decision: 'abstain', rule: 'switch' };
  context.appendSwitchLog(at);
  return { decision: 'allow', rule: 'switch' };
}
