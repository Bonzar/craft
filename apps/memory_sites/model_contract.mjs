/** Canonical thought reply shape only; no semantic judgment, persistence or model calls. */

export class ModelContractError extends Error {}

const fail = (message) => { throw new ModelContractError(message); };
const object = (value) => value !== null && typeof value === 'object' && !Array.isArray(value)
  && Object.getPrototypeOf(value) === Object.prototype;
const get = (value, key) => Object.hasOwn(value, key) ? value[key] : null;
const fields = (value, allowed) => {
  if (!object(value) || Object.keys(value).some(key => !allowed.includes(key))) {
    fail('thought model returned unexpected fields');
  }
};

// Match Python str.strip(), including its non-JSON control whitespace and excluding BOM.
const strip = value => value.replace(
  /^[\u0009-\u000d\u001c-\u0020\u0085\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]+|[\u0009-\u000d\u001c-\u0020\u0085\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]+$/gu, '',
);
const semanticText = (value, field = 'text', nullable = false) => {
  if (nullable && value === null) return null;
  if (typeof value !== 'string' || !strip(value)) fail(`thought ${field} must contain semantic text`);
  return strip(value);
};

class JsonInteger {
  constructor(source) { this.value = BigInt(source); }
}

const offered = (value, count) => value instanceof JsonInteger && value.value >= 0n
  && (count === null || value.value < BigInt(count));

function draftRows(response, supportCount, singleThought) {
  const bodyKey = singleThought ? 'thought' : 'thoughts';
  fields(response, [bodyKey, 'needs_detail']);
  let rows = get(response, bodyKey);
  if (singleThought) {
    if (Object.keys(response).length !== 2) fail('a requested thought response requires thought and needs_detail');
    const detail = response.needs_detail;
    if (detail !== null) {
      if (rows !== null) fail('a requested thought needing detail must have no draft');
      fields(detail, ['question', 'text']);
      semanticText(get(detail, 'question'), 'question');
      semanticText(get(detail, 'text'));
      if (supportCount) fail('a requested thought must preserve known facts and state gaps in its text');
      return [];
    }
    if (!object(rows)) fail('a requested thought must be one object or request missing detail');
    rows = [rows];
  }
  if (!Array.isArray(rows)) fail('thought draft batch must be a list');
  return rows.map(row => {
    fields(row, ['text', 'question', 'support_fragments']);
    const support = get(row, 'support_fragments');
    if (!Array.isArray(support) || !support.length || support.some(value => !offered(value, supportCount))) {
      fail('thought support must select existing prepared fragment options');
    }
    return {
      text: semanticText(get(row, 'text')),
      question: semanticText(get(row, 'question'), 'question', true),
      support_fragments: [...new Set(support.map(value => value.value))]
        .map(value => value > BigInt(Number.MAX_SAFE_INTEGER) ? value : Number(value)),
    };
  });
}

function construct(response, {supportCount, singleThought}) {
  if (singleThought) return draftRows(response, supportCount, true);
  let detail = get(response, 'needs_detail');
  if (get(response, 'verdict') === 'needs_detail') {
    fields(response, ['verdict', 'question', 'text']);
    detail = response;
  }
  if (detail !== null) {
    if (detail !== response) {
      fields(response, ['thoughts', 'needs_detail']);
      fields(detail, ['question', 'text']);
    }
    semanticText(get(detail, 'question'), 'question');
    semanticText(get(detail, 'text'));
    return [];
  }
  return draftRows(response, supportCount, false);
}

function sourceReview(response, draftCount, optionCount) {
  const allowed = ['verdict', 'text', 'rejected_drafts', 'missing_meaning'];
  if (optionCount) allowed.push('open_support');
  fields(response, allowed);
  if (Object.keys(response).length !== allowed.length) fail('source review omitted required fields');
  semanticText(response.text);
  const {verdict, rejected_drafts: rejected, missing_meaning: missing} = response;
  semanticText(missing, 'missing source meaning', true);
  if (!Array.isArray(rejected)) fail('source review rejected_drafts must be a list');
  const seen = new Set();
  for (const row of rejected) {
    fields(row, ['draft', 'text']);
    const index = get(row, 'draft');
    if (!offered(index, draftCount) || seen.has(index.value)) {
      fail('source review must reject each offered draft at most once');
    }
    seen.add(index.value);
    semanticText(get(row, 'text'));
  }
  const option = get(response, 'open_support');
  const defects = rejected.length > 0 || missing !== null;
  if (verdict === 'drill' && !defects && offered(option, optionCount)) return;
  if (verdict !== (defects ? 'reject' : 'accept') || option !== null) {
    fail('source review verdict must agree with its defects and support selection');
  }
}

function review(task, response, {reviewOptionCount, sourceDraftCount, compareMeaning}) {
  if (task === 'Verify thought' && sourceDraftCount !== null) {
    return sourceReview(response, sourceDraftCount, reviewOptionCount);
  }
  if (task === 'Verify thought' && compareMeaning) {
    const changed = get(response, 'meaning_changed');
    if (!Object.hasOwn(response, 'meaning_changed')
      || (get(response, 'verdict') === 'accept' ? typeof changed !== 'boolean' : changed !== null)) {
      fail('meaning_changed must be Boolean on acceptance, null otherwise');
    }
    const {meaning_changed: _checked, ...remaining} = response;
    response = remaining;
  }
  const verdict = get(response, 'verdict');
  if (task === 'Verify thought' && reviewOptionCount) {
    fields(response, ['verdict', 'text', 'open_support']);
    if (!Object.hasOwn(response, 'open_support')) fail('review must specify whether it opens support');
    if (verdict === 'drill') {
      if (!offered(response.open_support, reviewOptionCount)) fail('review drill requires an offered support option');
    } else if (!['accept', 'reject'].includes(verdict) || response.open_support !== null) {
      fail('review must accept, reject, or open one offered support option');
    }
  } else {
    const checkReady = task === 'Check ready thought';
    fields(response, checkReady ? ['verdict', 'text', 'question'] : ['verdict', 'text']);
    if (!(checkReady ? ['ready', 'build', 'drill'] : ['accept', 'reject']).includes(verdict)) {
      fail('thought response has an invalid verdict');
    }
    if (verdict === 'drill') semanticText(get(response, 'question'), 'question');
  }
  semanticText(get(response, 'text'));
}

/**
 * Validate a raw RFC 8259 JSON reply against the caller's task and offered option counts.
 * Returns normalized drafts for Construct thought; review tasks return undefined on success.
 * Counts are supplied by a trusted prepared catalog, never the model.
 * Accept text (not a parsed object) so Python's int vs float distinction remains observable.
 */
export function validateModelReply(task, body, {
  supportCount = null, singleThought = false, reviewOptionCount = 0,
  sourceDraftCount = null, compareMeaning = false,
} = {}) {
  for (const count of [supportCount, reviewOptionCount, sourceDraftCount]) {
    if (count !== null && (!Number.isSafeInteger(count) || count < 0)) throw new TypeError('invalid option count');
  }
  if (!['Construct thought', 'Verify thought', 'Verify source thought batch', 'Check ready thought'].includes(task)) {
    throw new TypeError('unsupported model task');
  }
  let response;
  try {
    if (typeof body !== 'string') fail('thought model returned invalid JSON');
    response = JSON.parse(body, (_key, value, context) => {
      if (typeof value === 'number' && /^-?(?:0|[1-9]\d*)$/.test(context.source)) {
        return new JsonInteger(context.source);
      }
      return value;
    });
  } catch {
    fail('thought model returned invalid JSON');
  }
  if (!object(response)) fail('thought model must return an object');
  if (task === 'Construct thought') return construct(response, {supportCount, singleThought});
  return review(task, response, {reviewOptionCount, sourceDraftCount, compareMeaning});
}
