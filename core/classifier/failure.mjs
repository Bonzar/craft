const REASONS = new Set([
  'usage_limit', 'network', 'timeout', 'auth', 'invalid_output', 'unsupported', 'unknown',
]);

export function sanitizeClassifierDetail(value) {
  return String(value || '')
    .replace(/\b(?:Bearer|Api-Key)\s+\S+/gi, '<redacted>')
    .replace(/\b(?:y[01]_|t[01]_|AQAD-)[A-Za-z0-9._-]+/g, '<redacted>')
    .replace(/[\r\n\t]+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
    .slice(0, 160);
}

export function inferClassifierReason(value) {
  const text = String(value || '');
  if (/usage limit|rate.?limit|quota|capacity|overloaded/i.test(text)) return 'usage_limit';
  if (/timed? ?out|timeout|ETIMEDOUT/i.test(text)) return 'timeout';
  if (/network|connection|ERR_NETWORK|IO_SUSPENDED|EAI_AGAIN|ECONN|DNS|Cloudflare|attestation/i.test(text)) return 'network';
  if (/unauthori[sz]ed|authentication|log ?in|credential|api.?key|\b401\b/i.test(text)) return 'auth';
  if (/unsupported|unknown model|model .*not (?:available|found)/i.test(text)) return 'unsupported';
  return 'unknown';
}

export function extractRetryAt(value) {
  const match = /try again (?:at|after)\s+([0-9]{1,2}:[0-9]{2}\s*(?:AM|PM)?)/i.exec(String(value || ''));
  return match ? match[1].trim() : '';
}

export function classifierFailure(reason, { detail = '', retryAt = '' } = {}) {
  const safeReason = REASONS.has(reason) ? reason : 'unknown';
  const safeDetail = sanitizeClassifierDetail(detail);
  const error = new Error(safeDetail || safeReason);
  error.name = 'ClassifierBackendError';
  error.classifierReason = safeReason;
  if (safeDetail) error.classifierDetail = safeDetail;
  const safeRetryAt = sanitizeClassifierDetail(retryAt);
  if (safeRetryAt) error.retryAt = safeRetryAt;
  return error;
}

export function normalizeClassifierFailure(error) {
  const detail = sanitizeClassifierDetail(error?.classifierDetail || error?.message);
  const reason = REASONS.has(error?.classifierReason)
    ? error.classifierReason : inferClassifierReason(detail);
  const retryAt = sanitizeClassifierDetail(error?.retryAt || extractRetryAt(detail));
  return {
    reason,
    ...(detail ? { detail } : {}),
    ...(retryAt ? { retryAt } : {}),
  };
}

export function isTransientClassifierFailure(value) {
  return value?.reason === 'network' || value?.reason === 'timeout';
}
