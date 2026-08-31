const COVERED = /^COVERED:(Ц[0-9]+\.[0-9]+):\s*(\S.*)$/;
const WITH_DETAIL = /^(OVERRIDE|FORBIDDEN|UNCOVERED):(\S.*)$/;
const PREFLIGHT_WITH_DETAIL = /^(ALLOW_READ|ALLOW_SESSION|ALLOW_EPHEMERAL|CHECK_REGISTRY|DENY):[ \t]*(\S.*)$/;

export function parsePreflightVerdict(value) {
  if (typeof value !== 'string' || value.includes('\n') || value.includes('\r')) return null;
  const match = PREFLIGHT_WITH_DETAIL.exec(value);
  if (!match) return null;
  return {
    kind: match[1],
    allowing: ['ALLOW_READ', 'ALLOW_SESSION', 'ALLOW_EPHEMERAL'].includes(match[1]),
    detail: match[2],
  };
}

export function parseCoverVerdict(value) {
  if (typeof value !== 'string' || value.includes('\n') || value.includes('\r')) return null;
  if (value === 'DRAFT') return { kind: 'DRAFT', allowing: true, detail: '' };
  if (value === 'UNAVAILABLE') return { kind: 'UNAVAILABLE', allowing: false, detail: '' };
  const covered = COVERED.exec(value);
  if (covered) return { kind: 'COVERED', allowing: true, detail: `${covered[1]}: ${covered[2]}` };
  const detailed = WITH_DETAIL.exec(value);
  if (!detailed) return null;
  return {
    kind: detailed[1],
    allowing: detailed[1] === 'OVERRIDE',
    detail: detailed[2],
  };
}
