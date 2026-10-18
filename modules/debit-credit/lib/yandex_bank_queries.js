/** Read-only history queries observed in web-transactions-ssr/v2019.0. */
const HISTORY_ENDPOINT = 'https://bank.yandex.ru/transactions/api/graphql';
const HISTORY_RETRY_DELAYS_MS = [1000, 3000, 8000];
const HISTORY_TRANSIENT_STATUS = new Set([429, 500, 502, 503, 504]);
const MAX_DETAIL_FAILURES = 20;
const HISTORY_QUERIES = Object.freeze({
  TransactionsFeedPfm: '9bf3cf40fd987f779649b53757e1ea3b6d24526cbcc16e193bb78ac398ca4e02',
  TransactionOperationDetails: 'a6773d390b7054e50574c774470564a89b6c14d68c855d4750240e9b6dccf38e',
});

/** @param {string} operation @param {object} variables @param {object=} environment
 * @returns {Promise<object>} */
async function requestHistory(operation, variables, environment) {
  if (typeof operation !== 'string') throw new Error('Разрешены только запросы истории');
  const hash = HISTORY_QUERIES[operation];
  if (!Object.hasOwn(HISTORY_QUERIES, operation)) throw new Error('Разрешены только запросы истории');
  const env = environment || {
    origin: typeof window === 'undefined' ? '' : window.location.origin,
    fetch: typeof window === 'undefined' ? null : window.fetch.bind(window),
  };
  if (env.origin !== 'https://bank.yandex.ru' || typeof env.fetch !== 'function') {
    throw new Error('Запуск нужен в авторизованной сессии банка');
  }
  // Штатный TransactionsApolloProvider добавляет этот заголовок ко всем запросам.
  const timezone = env.timezone ?? Intl.DateTimeFormat().resolvedOptions().timeZone;
  const response = await env.fetch(`${HISTORY_ENDPOINT}?operationId=${hash}`, {
    method: 'POST', credentials: 'same-origin',
    headers: { 'content-type': 'application/json', 'x-yabank-time-zone': timezone },
    body: JSON.stringify({ operationName: operation, variables,
      extensions: { persistedQuery: { version: 1, sha256Hash: hash } } }),
  });
  if (!response.ok) {
    const error = new Error(`Чтение истории: HTTP ${response.status}`);
    error.httpStatus = response.status;
    throw error;
  }
  const payload = await response.json();
  if (!payload || typeof payload !== 'object' || payload.errors?.length || !payload.data) {
    const error = new Error('Банк не вернул полный ответ запроса истории');
    error.payload = payload;
    error.retryable = payload?.errors?.some(item =>
      HISTORY_TRANSIENT_STATUS.has(item.extensions?.response?.status)) || false;
    throw error;
  }
  return payload.data;
}

/** @param {string} value @returns {number} */
function historyDate(value) {
  if (typeof value !== 'string' || !/(Z|[+-]\d{2}:\d{2})$/.test(value)) {
    throw new Error('Нужна дата с явным часовым поясом');
  }
  const time = Date.parse(value);
  if (!Number.isFinite(time)) throw new Error('Неверная дата истории');
  return time;
}

/** @param {object} options @returns {object} */
function historyOptions(options) {
  const from = historyDate(options.fromInclusive), to = historyDate(options.toExclusive);
  if (from >= to) throw new Error('Начало периода должно предшествовать концу');
  const concurrency = options.concurrency ?? 4, maxPages = options.maxPages ?? 2000;
  if (!Number.isInteger(concurrency) || concurrency < 1 || concurrency > 8) {
    throw new Error('Одновременно читаются от 1 до 8 карточек');
  }
  if (!Number.isInteger(maxPages) || maxPages < 1) throw new Error('Неверный предел страниц');
  return { from, to, concurrency, maxPages, includeDetails: options.includeDetails !== false,
    request: options.request || requestHistory, onProgress: options.onProgress || (() => {}),
    pause: options.pause || (delay => new Promise(resolve => setTimeout(resolve, delay))) };
}

/** @param {object} config @param {string} operation @param {object} variables
 * @returns {Promise<object>} */
async function requestWithRetry(config, operation, variables) {
  for (let attempt = 0; ; attempt += 1) {
    try { return await config.request(operation, variables); } catch (error) {
      const transient = HISTORY_TRANSIENT_STATUS.has(error?.httpStatus)
        || error?.retryable === true || error instanceof TypeError;
      if (!transient || attempt >= HISTORY_RETRY_DELAYS_MS.length) throw error;
      const delayMs = HISTORY_RETRY_DELAYS_MS[attempt];
      config.onProgress({ phase: 'retry', operation, attempt: attempt + 1, delayMs });
      await config.pause(delayMs);
    }
  }
}

/** @param {object} page @returns {object} */
function validateHistoryPage(page) {
  if (!page || !Array.isArray(page.items)) throw new Error('В ответе нет массива items');
  if (!Array.isArray(page.pendingItems) && page.pendingItems !== null) {
    throw new Error('В ответе нет массива pendingItems');
  }
  if (!Object.hasOwn(page, 'cursor') || (page.cursor !== null && typeof page.cursor !== 'string')) {
    throw new Error('В ответе нет корректного cursor');
  }
  return page;
}

/** @param {object} row @param {Map} seen @param {object} result @param {object} config
 * @returns {void} */
function observeHistoryRow(row, seen, result, config) {
  result.observations.push(row);
  if (!row || typeof row.id !== 'string' || !row.id) throw new Error('У операции нет ID');
  const time = historyDate(row.date);
  if (time < config.from || time >= config.to) {
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

/** @param {object} result @param {object} config @returns {Promise<void>} */
async function readHistoryPages(result, config) {
  const seen = new Map(), cursors = new Set();
  let cursor;
  for (let pageNumber = 0; pageNumber < config.maxPages; pageNumber += 1) {
    const input = { categories: [], cursor, baseFilters: { filters: [], date: {
      dateStartInclusive: new Date(config.from).toISOString(),
      dateEndExclusive: new Date(config.to).toISOString(),
    } } };
    const data = await requestWithRetry(config, 'TransactionsFeedPfm', { input });
    const page = validateHistoryPage(data.getTransactionsFeedPfm);
    result.pages.push(page);
    for (const row of [...page.items, ...(page.pendingItems || [])]) {
      observeHistoryRow(row, seen, result, config);
    }
    config.onProgress({ phase: 'list', pages: result.pages.length, records: result.records.length });
    if (page.cursor === null || page.cursor === '') { result.cursorExhausted = true; return; }
    if (cursors.has(page.cursor)) throw new Error('Банк повторил cursor, полнота не доказана');
    cursors.add(page.cursor);
    cursor = page.cursor;
  }
  throw new Error('Достигнут предел страниц, полнота не доказана');
}

/** @param {object} result @param {object} config @returns {Promise<void>} */
async function readHistoryDetails(result, config) {
  // Курсор и stop принадлежат только этой группе работников, не банковской сессии.
  let next = 0, stop = false;
  const alreadyRead = new Set(result.details.map(row => row.requestedId));
  const queue = result.records.filter(row => !alreadyRead.has(row.id));
  async function worker() {
    while (!stop && next < queue.length) {
      const record = queue[next++];
      try {
        const response = await requestWithRetry(config, 'TransactionOperationDetails', { id: record.id });
        result.detailObservations.push({ requestedId: record.id, response });
        const detail = response.getTransactionOperationDetails;
        if (!detail || typeof detail.id !== 'string' || !detail.id) {
          throw new Error('У карточки отсутствует собственный ID');
        }
        // Денежная карточка возвращает native ID, а список — непрозрачный FEED_VIEW ID.
        result.details.push({ ...detail, requestedId: record.id });
        config.onProgress({ phase: 'details', read: result.details.length, total: result.records.length });
      } catch (error) {
        result.detailErrors.push({ id: record.id,
          message: error instanceof Error ? error.message : 'Ошибка чтения',
          payload: error?.payload ?? null, httpStatus: error?.httpStatus ?? null });
        stop = error?.httpStatus === 401 || error?.httpStatus === 403
          || result.detailErrors.length >= MAX_DETAIL_FAILURES;
      }
    }
  }
  await Promise.all(Array.from({ length: config.concurrency }, worker));
  const readIds = new Set(result.details.map(row => row.requestedId));
  result.unreadIds = result.records.filter(row => !readIds.has(row.id)).map(row => row.id);
  if (result.unreadIds.length || result.detailErrors.length) throw new Error('Не все карточки прочитаны');
}

/** @param {object} snapshot @param {object} config @returns {Promise<void>} */
async function validateResumeFeed(snapshot, config) {
  if (snapshot?.adapter !== 'yandex-bank-graphql' || snapshot.schema_version !== 1
      || snapshot.cursorExhausted !== true || !Array.isArray(snapshot.pages)
      || !Array.isArray(snapshot.records) || !Array.isArray(snapshot.detailObservations)
      || !Array.isArray(snapshot.details) || !Array.isArray(snapshot.detailErrors)) {
    throw new Error('Продолжение требует сохранённый полный список операций');
  }
  let pageIndex = 0;
  const verified = { pages: [], records: [], observations: [], excluded: [], conflicts: [],
    repeatedObservations: 0, cursorExhausted: false };
  await readHistoryPages(verified, { ...config, onProgress: () => {},
    request: async () => ({ getTransactionsFeedPfm: snapshot.pages[pageIndex++] }) });
  if (pageIndex !== snapshot.pages.length || verified.conflicts.length
      || JSON.stringify(verified.records) !== JSON.stringify(snapshot.records)) {
    throw new Error('Сохранённый список не совпадает с исходными страницами');
  }
}

/** @param {object} snapshot @returns {Array<object>} */
function restoreReadDetails(snapshot) {
  const feedIds = new Set(snapshot.records.map(row => row.id)), restored = new Map();
  for (const observation of snapshot.detailObservations) {
    const detail = observation?.response?.getTransactionOperationDetails;
    const requestedId = observation?.requestedId;
    if (!feedIds.has(requestedId) || typeof detail?.id !== 'string' || !detail.id) {
      throw new Error('Сохранённый ответ карточки не сопоставлен с исходным запросом');
    }
    const entry = { ...detail, requestedId };
    if (restored.has(requestedId) && JSON.stringify(restored.get(requestedId)) !== JSON.stringify(entry)) {
      throw new Error('В сохранённых карточках разные ответы на один запрос');
    }
    restored.set(requestedId, entry);
  }
  for (const detail of snapshot.details) {
    const candidate = restored.get(detail.requestedId || detail.id);
    if (!candidate || candidate.id !== detail.id) {
      throw new Error('Сохранённая карточка не подтверждена исходным ответом');
    }
  }
  return Array.from(restored.values());
}

/** @param {object} snapshot @param {object=} options @returns {Promise<object>} */
async function resumeHistory(snapshot, options = {}) {
  const config = historyOptions({ ...options, ...snapshot?.period, includeDetails: true });
  await validateResumeFeed(snapshot, config);
  const result = { ...snapshot, complete: false, detailsRequested: true,
    details: restoreReadDetails(snapshot), detailObservations: [...snapshot.detailObservations],
    previousDetailErrors: [...(snapshot.previousDetailErrors || []), ...snapshot.detailErrors],
    detailErrors: [], unreadIds: [], resumedAt: new Date().toISOString() };
  try {
    await readHistoryDetails(result, config);
    result.complete = true;
    return result;
  } catch (cause) {
    const error = cause instanceof Error ? cause : new Error('Ошибка продолжения истории');
    error.partial = result;
    throw error;
  }
}

/** @param {object} options @returns {Promise<object>} */
async function collectHistory(options) {
  const config = historyOptions(options);
  const result = { schema_version: 1, adapter: 'yandex-bank-graphql', complete: false,
    period: { fromInclusive: options.fromInclusive, toExclusive: options.toExclusive },
    pages: [], records: [], observations: [], repeatedObservations: 0,
    excluded: [], conflicts: [], details: [], detailObservations: [], detailErrors: [],
    unreadIds: [], cursorExhausted: false, detailsRequested: config.includeDetails };
  try {
    await readHistoryPages(result, config);
    if (result.conflicts.length) throw new Error('Разные данные у одного ID, требуется повторное чтение');
    if (config.includeDetails) await readHistoryDetails(result, config);
    const accounted = result.records.length + result.repeatedObservations
      + result.conflicts.length + result.excluded.length;
    if (accounted !== result.observations.length) throw new Error('Нарушен счёт найденных операций');
    result.complete = true;
    return result;
  } catch (cause) {
    const error = cause instanceof Error ? cause : new Error('Ошибка чтения истории');
    error.partial = result;
    throw error;
  }
}

if (typeof module !== 'undefined') module.exports = { collectHistory, requestHistory, resumeHistory };
