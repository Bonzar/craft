export function capability(config, feature) {
  const supported = config?.capabilities?.[feature] === true;
  return supported
    ? { status: 'supported', feature }
    : { status: 'unsupported', feature, reason: 'adapter does not implement this core capability' };
}

export function requireCapability(config, feature) {
  const state = capability(config, feature);
  if (state.status !== 'supported') {
    const error = new Error(state.reason);
    error.code = 'UNSUPPORTED_CAPABILITY';
    error.feature = feature;
    throw error;
  }
  return state;
}
