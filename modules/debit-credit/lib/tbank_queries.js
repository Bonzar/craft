/** Read-only T-Bank timeline queries for the given own accounts. */
const TBANK_ORIGIN = 'https://www.tbank.ru';
const TIMELINE_PATH = '/mybank/api/operations/timeline/public/legacy/v1/operations';
const SESSION_PARAMS = ['sessionid', 'origin', 'appName', 'appVersion', 'platform'];
const MAX_CHUNK_MS = 31 * 86400000;
const RETRY_DELAYS_MS = [1000, 3000, 8000];
const TRANSIENT_STATUS = new Set([429, 500, 502, 503, 504]);
const REQUEST_TIMEOUT_MS = 30000;

/** @returns {object} */
function pageEnvironment() {
  if (typeof window === 'undefined') return { origin: '', resourceUrls: [], fetch: null };
  return { origin: window.location.origin, fetch: window.fetch.bind(window),
    resourceUrls: performance.getEntriesByType('resource').map(entry => entry.name) };
}

/** Session parameters come from the page's own requests and stay inside the request URL.
 * @param {Array<string>} urls @returns {URLSearchParams} */
function sessionParams(urls) {
  const source = urls.map(value => new URL(value))
    .find(url => url.origin === TBANK_ORIGIN && url.searchParams.get('sessionid'));
  if (!source) throw new Error('Не найдена сессия Т-Банка: открой ленту операций');
  const params = new URLSearchParams();
  for (const key of SESSION_PARAMS) {
    if (source.searchParams.get(key)) params.set(key, source.searchParams.get(key));
  }
  return params;
}

/** @param {{start: number, end: number, accounts: Array<string>}} query @param {object=} environment
 * @returns {Promise<object>} */
async function requestTimeline(query, environment) {
  const env = environment || pageEnvironment();
  if (env.origin !== TBANK_ORIGIN || typeof env.fetch !== 'function') {
    throw new Error('Запуск нужен в авторизованной вкладке Т-Банка');
  }
  const url = new URL(TIMELINE_PATH, TBANK_ORIGIN);
  url.search = sessionParams(env.resourceUrls).toString();
  url.searchParams.set('start', String(query.start));
  url.searchParams.set('end', String(query.end));
  url.searchParams.set('accounts', query.accounts.join(','));
  const response = await env.fetch(url.toString(), { method: 'GET', credentials: 'include',
    signal: typeof AbortSignal?.timeout === 'function' ? AbortSignal.timeout(REQUEST_TIMEOUT_MS) : undefined });
  if (!response.ok) {
    const error = new Error(`Чтение ленты: HTTP ${response.status}`);
    error.httpStatus = response.status;
    throw error;
  }
  return response.json();
}

/** @param {string} value @returns {number} */
function periodTime(value) {
  if (typeof value !== 'string' || !/(Z|[+-]\d{2}:\d{2})$/.test(value)) {
    throw new Error('Нужна дата с явным часовым поясом');
  }
  const time = Date.parse(value);
  if (!Number.isFinite(time)) throw new Error('Неверная дата периода');
  return time;
}

/** @param {object} options @returns {object} */
function collectorOptions(options) {
  const from = periodTime(options.fromInclusive), to = periodTime(options.toExclusive);
  if (from >= to) throw new Error('Начало периода должно предшествовать концу');
  const accounts = options.accounts;
  if (!Array.isArray(accounts) || !accounts.length
      || accounts.some(value => typeof value !== 'string' || !/^\d+$/.test(value))) {
    throw new Error('Нужен непустой список номеров своих счетов');
  }
  return { from, to, accounts: [...new Set(accounts)], request: options.request || requestTimeline,
    onProgress: options.onProgress || (() => {}),
    pause: options.pause || (delay => new Promise(resolve => setTimeout(resolve, delay))) };
}

/** @param {object} config @param {object} query @returns {Promise<Array<object>>} */
async function readChunk(config, query) {
  for (let attempt = 0; ; attempt += 1) {
    try {
      const answer = await config.request(query);
      if (answer?.resultCode !== 'OK' || !Array.isArray(answer.payload)) {
        throw new Error(`Банк не вернул ленту: ${answer?.resultCode ?? 'нет ответа'}`);
      }
      return answer.payload;
    } catch (error) {
      const transient = TRANSIENT_STATUS.has(error?.httpStatus) || error instanceof TypeError;
      if (!transient || attempt >= RETRY_DELAYS_MS.length) throw error;
      config.onProgress({ phase: 'retry', attempt: attempt + 1, delayMs: RETRY_DELAYS_MS[attempt] });
      await config.pause(RETRY_DELAYS_MS[attempt]);
    }
  }
}

/** @param {object} row @param {Map} seen @param {object} result @param {object} config @returns {void} */
function observe(row, seen, result, config) {
  result.observations.push(row);
  if (!row || typeof row.id !== 'string' || !row.id) throw new Error('У операции нет ID');
  const time = row.operationTime?.milliseconds;
  if (!config.accounts.includes(row.account)) {
    result.excluded.push({ record: row, reason: 'account_not_requested' });
    return;
  }
  if (!Number.isFinite(time) || time < config.from || time >= config.to) {
    result.excluded.push({ record: row, reason: 'outside_requested_period' });
    return;
  }
  if (seen.has(row.id)) {
    if (JSON.stringify(seen.get(row.id)) === JSON.stringify(row)) result.repeatedObservations += 1;
    else result.conflicts.push({ id: row.id, previous: seen.get(row.id), current: row });
    return;
  }
  seen.set(row.id, row);
  result.records.push(row);
}

/** @param {object} options @returns {Promise<object>} */
async function collectTbankHistory(options) {
  const config = collectorOptions(options);
  const result = { schema_version: 1, adapter: 'tbank-timeline', complete: false,
    period: { fromInclusive: options.fromInclusive, toExclusive: options.toExclusive },
    accounts: config.accounts, chunks: [], records: [], observations: [], excluded: [],
    conflicts: [], repeatedObservations: 0 };
  const seen = new Map();
  try {
    for (let start = config.from; start < config.to; start += MAX_CHUNK_MS) {
      const query = { start, end: Math.min(start + MAX_CHUNK_MS, config.to), accounts: config.accounts };
      const payload = await readChunk(config, query);
      result.chunks.push({ start: query.start, end: query.end, count: payload.length });
      for (const row of payload) observe(row, seen, result, config);
      config.onProgress({ phase: 'list', chunks: result.chunks.length, records: result.records.length });
    }
    if (result.conflicts.length) throw new Error('Разные данные у одного ID, требуется повторное чтение');
    const accounted = result.records.length + result.excluded.length
      + result.conflicts.length + result.repeatedObservations;
    if (accounted !== result.observations.length) throw new Error('Нарушен счёт найденных операций');
    result.complete = true;
    return result;
  } catch (cause) {
    const error = cause instanceof Error ? cause : new Error('Ошибка чтения ленты');
    error.partial = result;
    throw error;
  }
}

if (typeof module !== 'undefined') {
  module.exports = { collectTbankHistory, requestTimeline };
}
