/** Read-only Sber operation history queries for a period. */
const SBER_ORIGIN = 'https://online.sberbank.ru';
const OPERATIONS_URL = 'https://web-node3.online.sberbank.ru/uoh-bh/v1/operations/list';
const MOSCOW_OFFSET_MS = 3 * 3600000;
const DEFAULT_PAGE_SIZE = 50;
const DEFAULT_MAX_PAGES = 400;
const RETRY_DELAYS_MS = [1000, 3000, 8000];
const TRANSIENT_STATUS = new Set([429, 500, 502, 503, 504]);
const REQUEST_TIMEOUT_MS = 30000;

/** @param {number} time @returns {string} Moscow time as DD.MM.YYYYTHH:mm:ss */
function sberDate(time) {
  const iso = new Date(time + MOSCOW_OFFSET_MS).toISOString();
  return `${iso.slice(8, 10)}.${iso.slice(5, 7)}.${iso.slice(0, 4)}T${iso.slice(11, 19)}`;
}

/** @param {string} value @returns {number} */
function parseSberDate(value) {
  const match = /^(\d{2})\.(\d{2})\.(\d{4})T(\d{2}:\d{2}:\d{2})$/.exec(value ?? '');
  if (!match) return NaN;
  return Date.parse(`${match[3]}-${match[2]}-${match[1]}T${match[4]}+03:00`);
}

/** @param {object} query @param {object=} environment @returns {Promise<object>} */
async function requestOperations(query, environment) {
  const env = environment || { origin: window.location.origin, fetch: window.fetch.bind(window) };
  if (env.origin !== SBER_ORIGIN || typeof env.fetch !== 'function') {
    throw new Error('Запуск нужен в авторизованной вкладке СберБанк Онлайн');
  }
  // Без этих заголовков банк отвечает пустой историей.
  const response = await env.fetch(OPERATIONS_URL, { method: 'POST', credentials: 'include',
    headers: { Accept: 'application/json, text/plain, */*',
      'Content-Type': 'application/json;charset=UTF-8', 'X-Requested-With': 'XMLHttpRequest' },
    body: JSON.stringify({ ...query, showHidden: false, showNotTransactionBonuses: true,
      showOpenBanking: true }),
    signal: typeof AbortSignal?.timeout === 'function' ? AbortSignal.timeout(REQUEST_TIMEOUT_MS) : undefined });
  if (!response.ok) {
    const error = new Error(`Чтение истории: HTTP ${response.status}`);
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
  const pageSize = options.pageSize ?? DEFAULT_PAGE_SIZE, maxPages = options.maxPages ?? DEFAULT_MAX_PAGES;
  if (![pageSize, maxPages].every(value => Number.isInteger(value) && value > 0)) {
    throw new Error('Неверный размер или предел страниц');
  }
  return { from, to, pageSize, maxPages, request: options.request || requestOperations,
    onProgress: options.onProgress || (() => {}),
    pause: options.pause || (delay => new Promise(resolve => setTimeout(resolve, delay))) };
}

/** @param {object} config @param {object} query @returns {Promise<Array<object>>} */
async function readPage(config, query) {
  for (let attempt = 0; ; attempt += 1) {
    try {
      const answer = await config.request(query);
      if (answer?.success !== true || !Array.isArray(answer.body?.operations)) {
        throw new Error('Банк не вернул страницу истории');
      }
      return answer.body.operations;
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
  if (!row || typeof row.uohId !== 'string' || !row.uohId) throw new Error('У операции нет ID');
  const time = parseSberDate(row.date);
  if (!Number.isFinite(time) || time < config.from || time >= config.to) {
    result.excluded.push({ record: row, reason: 'outside_requested_period' });
    return;
  }
  if (seen.has(row.uohId)) {
    if (JSON.stringify(seen.get(row.uohId)) === JSON.stringify(row)) result.repeatedObservations += 1;
    else result.conflicts.push({ id: row.uohId, previous: seen.get(row.uohId), current: row });
    return;
  }
  seen.set(row.uohId, row);
  result.records.push(row);
}

/** @param {object} result @param {object} config @returns {Promise<void>} */
async function readPages(result, config) {
  const seen = new Map();
  // Банк принимает конец периода включительно и с точностью до секунды.
  const lastSecond = Math.ceil(config.to / 1000) * 1000 - 1000;
  const range = { from: sberDate(config.from), to: sberDate(lastSecond) };
  for (let page = 0; page < config.maxPages; page += 1) {
    const query = { paginationOffset: page * config.pageSize, paginationSize: config.pageSize, ...range };
    const operations = await readPage(config, query);
    result.pages.push({ offset: query.paginationOffset, count: operations.length });
    for (const row of operations) observe(row, seen, result, config);
    config.onProgress({ phase: 'list', pages: result.pages.length, records: result.records.length });
    if (operations.length < config.pageSize) return;
  }
  throw new Error('Достигнут предел страниц, полнота не доказана');
}

/** @param {object} options @returns {Promise<object>} */
async function collectSberHistory(options) {
  const config = collectorOptions(options);
  const result = { schema_version: 1, adapter: 'sber-operations', complete: false,
    period: { fromInclusive: options.fromInclusive, toExclusive: options.toExclusive },
    pages: [], records: [], observations: [], excluded: [], conflicts: [], repeatedObservations: 0 };
  try {
    await readPages(result, config);
    if (result.conflicts.length) throw new Error('Разные данные у одного ID, требуется повторное чтение');
    const accounted = result.records.length + result.excluded.length
      + result.conflicts.length + result.repeatedObservations;
    if (accounted !== result.observations.length) throw new Error('Нарушен счёт найденных операций');
    result.complete = true;
    return result;
  } catch (cause) {
    const error = cause instanceof Error ? cause : new Error('Ошибка чтения истории');
    error.partial = result;
    throw error;
  }
}

if (typeof module !== 'undefined') {
  module.exports = { collectSberHistory, requestOperations, sberDate };
}
