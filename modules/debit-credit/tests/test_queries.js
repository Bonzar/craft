const test = require('node:test');
const assert = require('node:assert/strict');
const { collectHistory, requestHistory } = require('../lib/yandex_bank_queries.js');

const scope = {
  fromInclusive: '2026-07-04T00:00:00+03:00',
  toExclusive: '2026-07-06T00:00:00+03:00',
};
const item = (id, extra = {}) => ({ id, date: '2026-07-04T10:00:00+03:00', ...extra });
const page = (items, cursor = null, pendingItems = []) => ({
  getTransactionsFeedPfm: { items, pendingItems, cursor, analytics: [] },
});

test('all cursor pages and distinct IDs are retained', async () => {
  const calls = [];
  const request = async (operation, variables) => {
    calls.push({ operation, variables });
    if (operation === 'TransactionOperationDetails') {
      return { getTransactionOperationDetails: item(variables.id) };
    }
    return variables.input.cursor ? page([item('b')]) : page([item('a')], 'next');
  };

  const result = await collectHistory({ ...scope, request });

  assert.equal(result.complete, true);
  assert.deepEqual(result.records.map(row => row.id), ['a', 'b']);
  assert.equal(result.details.length, 2);
  assert.equal(result.pages.length, 2);
  assert.deepEqual(calls[0].variables.input.baseFilters.filters, []);
  assert.equal(calls[0].variables.input.baseFilters.date.dateEndExclusive, '2026-07-05T21:00:00.000Z');
});

test('pending items are read and repeated observations are counted', async () => {
  const result = await collectHistory({ ...scope, includeDetails: false,
    request: async () => page([item('a')], null, [item('a'), item('b')]),
  });
  assert.equal(result.records.length, 2);
  assert.equal(result.observations.length, 3);
  assert.equal(result.repeatedObservations, 1);
});

test('repeated cursor stops with partial data instead of claiming completeness', async () => {
  await assert.rejects(collectHistory({ ...scope, request: async () => page([item('a')], 'same') }),
    error => error.partial.complete === false && error.partial.pages.length === 2);
});

test('missing pending array is an error instead of an empty result', async () => {
  await assert.rejects(collectHistory({ ...scope,
    request: async () => ({ getTransactionsFeedPfm: { items: [], cursor: null } }),
  }), /pendingItems/);
});

test('out-of-period observations are preserved with a reason', async () => {
  const result = await collectHistory({ ...scope, includeDetails: false,
    request: async () => page([item('a'), item('old', { date: '2026-07-03T23:59:59+03:00' })]),
  });
  assert.equal(result.records.length, 1);
  assert.equal(result.excluded[0].record.id, 'old');
  assert.equal(result.excluded[0].reason, 'outside_requested_period');
  assert.equal(result.observations.length, 2);
});

test('conflicting data for the same ID is retained and blocks completeness', async () => {
  await assert.rejects(collectHistory({ ...scope,
    request: async () => page([item('a')], null, [item('a', { statusCode: 'changed' })]),
  }), error => error.partial.conflicts.length === 1 && !error.partial.complete);
});

test('failed detail request preserves feed data and identifies the unread ID', async () => {
  const request = async operation => {
    if (operation === 'TransactionsFeedPfm') return page([item('a')]);
    throw new Error('offline');
  };
  await assert.rejects(collectHistory({ ...scope, request }),
    error => error.partial.records.length === 1 && error.partial.detailErrors[0].id === 'a');
});

test('bank GraphQL error payload survives in the partial detail result', async () => {
  const payload = { errors: [{ message: 'PersistedQueryNotFound',
    extensions: { code: 'PERSISTED_QUERY_NOT_FOUND' } }] };
  const request = async (operation, variables) => {
    if (operation === 'TransactionsFeedPfm') return page([item('a')]);
    return requestHistory(operation, variables, {
      origin: 'https://bank.yandex.ru',
      fetch: async () => ({ ok: true, json: async () => payload }),
    });
  };
  await assert.rejects(collectHistory({ ...scope, request }), error => {
    assert.deepEqual(error.partial.detailErrors[0].payload, payload);
    assert.equal(error.partial.cursorExhausted, true);
    assert.equal(error.partial.records.length, 1);
    return true;
  });
});

test('detail concurrency is bounded', async () => {
  let active = 0, maxActive = 0;
  const request = async (operation, variables) => {
    if (operation === 'TransactionsFeedPfm') return page([item('a'), item('b'), item('c')]);
    active += 1;
    maxActive = Math.max(maxActive, active);
    await new Promise(resolve => setTimeout(resolve, 5));
    active -= 1;
    return { getTransactionOperationDetails: item(variables.id) };
  };
  const result = await collectHistory({ ...scope, request, concurrency: 2 });
  assert.equal(result.complete, true);
  assert.equal(maxActive, 2);
});

test('unknown operations are rejected before the transport is called', async () => {
  let called = false;
  await assert.rejects(requestHistory('TransferMoney', {}, {
    origin: 'https://bank.yandex.ru',
    fetch: async () => { called = true; },
  }), /Разрешены только/);
  assert.equal(called, false);
});

test('only the fixed bank origin and history endpoint receive the request', async () => {
  let captured;
  const environment = {
    origin: 'https://bank.yandex.ru',
    timezone: 'Europe/Moscow',
    fetch: async (url, options) => {
      captured = { url, options };
      return { ok: true, json: async () => ({ data: page([]) }) };
    },
  };
  await requestHistory('TransactionsFeedPfm', { input: {} }, environment);
  assert.match(captured.url, /^https:\/\/bank\.yandex\.ru\/transactions\/api\/graphql\?operationId=[a-f0-9]+$/);
  assert.equal(captured.options.credentials, 'same-origin');
  assert.equal(captured.options.method, 'POST');
  assert.equal(captured.options.headers['x-yabank-time-zone'], 'Europe/Moscow');
  assert.equal(JSON.parse(captured.options.body).operationName, 'TransactionsFeedPfm');
  await assert.rejects(requestHistory('TransactionsFeedPfm', {}, {
    origin: 'https://evil.example', fetch: environment.fetch,
  }), /банка/);
});
