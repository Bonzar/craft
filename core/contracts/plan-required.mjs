const KEYS = ['continuation', 'reason', 'schemaVersion', 'type'];
const CONTINUATION_KEYS = ['blockedAction', 'originalIntent', 'transitionId'];

function exactKeys(value, expected, label) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error(`${label} must be an object`);
  const actual = Object.keys(value).sort();
  if (actual.length !== expected.length || actual.some((key, index) => key !== expected[index])) {
    throw new Error(`${label} has an invalid schema`);
  }
}

export function validatePlanRequired(value) {
  exactKeys(value, KEYS, 'plan_required decision');
  if (value.schemaVersion !== 1 || value.type !== 'plan_required') throw new Error('invalid plan_required discriminator');
  if (typeof value.reason !== 'string' || !value.reason) throw new Error('plan_required reason is required');
  exactKeys(value.continuation, CONTINUATION_KEYS, 'plan_required continuation');
  const { blockedAction, originalIntent, transitionId } = value.continuation;
  if (!blockedAction || typeof blockedAction !== 'object' || typeof blockedAction.route !== 'string') {
    throw new Error('blockedAction must be canonical');
  }
  if (typeof originalIntent !== 'string') throw new Error('originalIntent must be a string');
  if (typeof transitionId !== 'string' || !transitionId) throw new Error('transitionId is required');
  return value;
}

export function createPlanRequired(reason, continuation) {
  return validatePlanRequired({ schemaVersion: 1, type: 'plan_required', reason, continuation });
}
