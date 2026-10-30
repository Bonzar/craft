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
  const maxPages = options.maxPages ?? 20000;
  if (!Number.isInteger(maxPages) || maxPages < 1) throw new Error('Неверный предел страниц');
  return { from, to, maxPages, includeDetails: options.includeDetails !== false,
    ...detailOptions(options) };
}

/** @param {object} options @returns {object} */
function detailOptions(options) {
  const concurrency = options.concurrency ?? 4;
  if (!Number.isInteger(concurrency) || concurrency < 1 || concurrency > 8) {
    throw new Error('Одновременно читаются от 1 до 8 карточек');
  }
  return { concurrency, request: options.request || requestHistory,
    onProgress: options.onProgress || (() => {}),
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

const DAY_MS = 86400000;
// Меньше секунды окно не делится: время операций в ленте с точностью до миллисекунд,
// но пачка покупки, округления и кешбэка приходит в одну секунду.
const MIN_WINDOW_MS = 1000;

/** @param {object} config @param {number} from @param {number} to @param {string=} cursor
 * @returns {Promise<object>} */
async function requestFeedPage(config, from, to, cursor) {
  const input = { categories: [], cursor, baseFilters: { filters: [], date: {
    dateStartInclusive: new Date(from).toISOString(), dateEndExclusive: new Date(to).toISOString(),
  } } };
  config.requests += 1;
  if (config.requests > config.maxPages) throw new Error('Достигнут предел запросов, полнота не доказана');
  const data = await requestWithRetry(config, 'TransactionsFeedPfm', { input });
  return validateHistoryPage(data.getTransactionsFeedPfm);
}

/** @param {object} page @param {Map} seen @param {object} result @param {object} config */
function acceptPage(page, seen, result, config) {
  result.pages.push(page);
  for (const row of [...page.items, ...(page.pendingItems || [])]) observeHistoryRow(row, seen, result, config);
  config.onProgress({ phase: 'list', pages: result.pages.length, records: result.records.length });
}

/** Окно в одну секунду, которое не влезло в страницу, читается по курсору и помечается.
 * @returns {Promise<void>} */
async function readWindowByCursor(first, from, to, seen, result, config) {
  const cursors = new Set();
  let page = first;
  result.cursorWindows.push({ from: new Date(from).toISOString(), to: new Date(to).toISOString() });
  for (;;) {
    acceptPage(page, seen, result, config);
    if (page.cursor === null || page.cursor === '') return;
    if (cursors.has(page.cursor)) throw new Error('Банк повторил cursor, полнота не доказана');
    cursors.add(page.cursor);
    page = await requestFeedPage(config, from, to, page.cursor);
  }
}

/** Лента читается окнами по суткам. Окно, которое не влезло в одну страницу, делится
 * пополам по времени, а не дочитывается курсором: на стыках страниц банк теряет операции.
 * @param {object} result @param {object} config @returns {Promise<void>} */
async function readHistoryPages(result, config) {
  const seen = new Map(), queue = [];
  config.requests = 0;
  for (let from = config.from; from < config.to; from += DAY_MS) queue.push([from, Math.min(from + DAY_MS, config.to)]);
  while (queue.length) {
    const [from, to] = queue.shift();
    const page = await requestFeedPage(config, from, to);
    if (page.cursor === null || page.cursor === '') { acceptPage(page, seen, result, config); continue; }
    if (to - from > MIN_WINDOW_MS) {
      const middle = from + Math.floor((to - from) / 2);
      queue.unshift([from, middle], [middle, to]);
      result.windowSplits += 1;
      continue;
    }
    await readWindowByCursor(page, from, to, seen, result, config);
  }
  result.cursorExhausted = true;
}

/** Сохранённые страницы заново дают тот же список, без запросов к банку.
 * @param {Array<object>} pages @param {object} config @returns {object} */
function replayPages(pages, config) {
  const seen = new Map();
  const verified = { pages: [], records: [], observations: [], excluded: [], conflicts: [],
    repeatedObservations: 0 };
  for (const page of pages) {
    validateHistoryPage(page);
    for (const row of [...page.items, ...(page.pendingItems || [])]) observeHistoryRow(row, seen, verified, config);
  }
  return verified;
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

/** @param {Array<string>} ids @returns {object} */
function recheckResult(ids) {
  if (!Array.isArray(ids) || ids.some(id => typeof id !== 'string' || !id.trim())) {
    throw new Error('Для повторного чтения нужен массив непустых ID');
  }
  const uniqueIds = [...new Set(ids)];
  return { schema_version: 1, adapter: 'yandex-bank-detail-rechecks', complete: false,
    coverage: 'requested_versions_only', limitations: ['current_feed_identity_required'],
    inputIds: [...ids], records: uniqueIds.map(id => ({ id })), details: [],
    detailObservations: [], detailErrors: [], unreadIds: uniqueIds };
}

/** @param {Array<string>} ids @param {object=} options @returns {Promise<object>} */
async function collectDetailsByIds(ids, options = {}) {
  const result = recheckResult(ids), config = detailOptions(options);
  try {
    await readHistoryDetails(result, config);
    result.complete = true;
    return result;
  } catch (cause) {
    const error = cause instanceof Error ? cause : new Error('Ошибка повторного чтения');
    error.partial = result;
    throw error;
  }
}

/** @param {object} result @param {Array<string>} ids @param {object} options
 * @returns {Promise<void>} */
async function refreshRechecks(result, ids, options) {
  try { result.rechecks = await collectDetailsByIds(ids, options); } catch (error) {
    result.rechecks = error.partial;
    result.complete = false;
    throw error;
  }
}

/** @param {object} snapshot @param {object} config @returns {Promise<void>} */
async function validateResumeFeed(snapshot, config) {
  if (snapshot?.adapter !== 'yandex-bank-graphql' || snapshot.schema_version !== 1
      || snapshot.cursorExhausted !== true || !Array.isArray(snapshot.pages)
      || !Array.isArray(snapshot.records) || !Array.isArray(snapshot.detailObservations)
      || !Array.isArray(snapshot.details) || !Array.isArray(snapshot.detailErrors)) {
    throw new Error('Продолжение требует сохранённый полный список операций');
  }
  const verified = replayPages(snapshot.pages, config);
  if (!snapshot.pages.length || verified.conflicts.length
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
    if (snapshot.rechecks) await refreshRechecks(result, snapshot.rechecks.inputIds, config);
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
    unreadIds: [], cursorExhausted: false, detailsRequested: config.includeDetails,
    windowSplits: 0, cursorWindows: [] };
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

/** New history first, then fresh versions of earlier operations.
 * @param {object} options @returns {Promise<object>} */
async function collectHistoryWithRechecks(options) {
  const rechecks = recheckResult(options.recheckIds ?? []);
  let result;
  try { result = await collectHistory(options); } catch (error) {
    if (error?.partial && rechecks.inputIds.length) error.partial.rechecks = rechecks;
    throw error;
  }
  if (!rechecks.inputIds.length) return result;
  result.rechecks = rechecks;
  try { await refreshRechecks(result, rechecks.inputIds, options); } catch (error) {
    error.partial = result;
    throw error;
  }
  return result;
}

const BALANCE_ENDPOINT = 'https://bank.yandex.ru/graphql';
const BALANCE_QUERIES = Object.freeze({
  HomeProductsV2: { hash: '990f85eda7d15aa75684acbf4a0fe06b0645d67b044e7eb30f69561795971251',
    variables: { homeProductsInput: {} } },
  SavingsAccountsList: { hash: '9ce03dbdca982b8c5fc4ba65386c0007bc5fb887ccb35304d943ac3dcfd42806',
    variables: {} },
});
const OPEN_SAVING = 'Open';
const ENDED_SAVINGS = new Set(['Closed', 'Annulled']);

/** @param {object} env @param {string} operation @returns {Promise<object>} */
async function requestBalanceQuery(env, operation) {
  const { hash, variables } = BALANCE_QUERIES[operation];
  const response = await env.fetch(`${BALANCE_ENDPOINT}?operationId=${hash}`, {
    method: 'POST', credentials: 'same-origin', headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ operationName: operation, variables,
      extensions: { persistedQuery: { version: 1, sha256Hash: hash } } }),
  });
  if (!response.ok) throw new Error(`Чтение остатков: HTTP ${response.status}`);
  const payload = await response.json();
  if (payload?.errors?.length || !payload?.data) throw new Error('Банк не вернул остатки');
  return payload.data;
}

/** Card and every open saving; ended savings carry no money and are skipped.
 * @param {object=} environment @returns {Promise<Array<object>>} */
async function requestYandexBalances(environment) {
  const env = environment || { origin: window.location.origin, fetch: window.fetch.bind(window) };
  if (env.origin !== 'https://bank.yandex.ru' || typeof env.fetch !== 'function') {
    throw new Error('Запуск нужен в авторизованной сессии банка');
  }
  const home = await requestBalanceQuery(env, 'HomeProductsV2');
  const card = home.homeProducts?.products?.find(product => product.id === 'CARD');
  if (!card?.value) throw new Error('Банк не вернул остаток карты');
  const balances = [{ account: 'CARD', account_name: 'Карта Пэй', amount: card.value.amount,
    currency: card.value.currency }];
  const savings = await requestBalanceQuery(env, 'SavingsAccountsList');
  const items = savings.bankUser?.savingsAccounts?.items;
  if (!Array.isArray(items)) throw new Error('Банк не вернул список сейвов');
  for (const item of items) {
    if (ENDED_SAVINGS.has(item.status)) continue;
    if (item.status !== OPEN_SAVING) throw new Error(`Неизвестное состояние сейва: ${item.status}`);
    balances.push({ account: item.id, account_name: item.name, amount: item.balance?.amount,
      currency: item.balance?.currency });
  }
  return balances;
}

if (typeof module !== 'undefined') {
  module.exports = { collectHistory, requestHistory, resumeHistory, collectHistoryWithRechecks,
    collectDetailsByIds, requestYandexBalances };
}
