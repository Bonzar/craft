export function registryRule(context) {
  return context.registryDecision(context.goals());
}
