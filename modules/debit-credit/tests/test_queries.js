const test = require('node:test');
const assert = require('node:assert/strict');
const { collectHistory, requestHistory, resumeHistory, collectHistoryWithRechecks,
  collectDetailsByIds, requestYandexBalances } = require('../lib/yandex_bank_queries.js');

const scope = {
  fromInclusive: '2026-07-04T00:00:00+03:00',
  toExclusive: '2026-07-05T00:00:00+03:00',
};
const item = (id, extra = {}) => ({ id, date: '2026-07-04T10:00:00+03:00', ...extra });
const page = (items, cursor = null, pendingItems = []) => ({
  getTransactionsFeedPfm: { items, pendingItems, cursor, analytics: [] },
});

test('Old IDs are re-read directly without a new-period feed or cached details', async () => {
  const calls = [];
  const ids = ['old-purchase', 'old-purchase', 'old-rounding'];
  const result = await collectDetailsByIds(ids, { request: async (operation, variables) => {
    calls.push({ operation, id: variables.id });
    return { getTransactionOperationDetails: item(`native-${variables.id}`,
      { date: '2026-01-01T10:00:00+03:00' }) };
  } });
  assert.equal(result.complete, true);
  assert.equal(result.coverage, 'requested_versions_only');
  assert.ok(result.limitations.includes('current_feed_identity_required'));
  assert.deepEqual(result.inputIds, ids);
  assert.deepEqual(calls.map(c => c.operation), Array(2).fill('TransactionOperationDetails'));
  assert.deepEqual(result.details.map(d => d.requestedId).sort(), ['old-purchase', 'old-rounding']);
  assert.deepEqual(result.details.map(d => d.id).sort(), ['native-old-purchase', 'native-old-rounding']);
  assert.deepEqual(ids, ['old-purchase', 'old-purchase', 'old-rounding']);
});

test('Unread old IDs retain successful rechecks and do not claim completeness', async () => {
  await assert.rejects(collectDetailsByIds(['bad', 'good'], { concurrency: 1,
    request: async (operation, { id }) => {
      if (id === 'bad') throw new Error('not found');
      return { getTransactionOperationDetails: item(id) };
    },
  }), error => error.partial.complete === false
    && error.partial.details[0].requestedId === 'good'
    && error.partial.unreadIds[0] === 'bad');
});

test('Old-operation rechecks follow the new history', async () => {
  const calls = [];
  const result = await collectHistoryWithRechecks({ ...scope, recheckIds: ['old'],
    request: async (operation, variables) => {
      calls.push(operation);
      return operation === 'TransactionsFeedPfm' ? page([])
        : { getTransactionOperationDetails: item(variables.id) };
    },
  });
  assert.deepEqual(calls, ['TransactionsFeedPfm', 'TransactionOperationDetails']);
  assert.equal(result.rechecks.complete, true);
  assert.equal(result.rechecks.details[0].requestedId, 'old');
  assert.equal(result.records.length, 0);
});

test('Malformed recheck IDs are rejected before any bank request', async () => {
  let requests = 0;
  await assert.rejects(collectDetailsByIds(['good', null], { request: async () => { requests++; } }), /ID/);
  assert.equal(requests, 0);
});

test('Failed old-operation recheck preserves new history and is repeated on resume', async () => {
  let partial;
  await assert.rejects(collectHistoryWithRechecks({ ...scope, recheckIds: ['old'],
    request: async operation => {
      if (operation === 'TransactionsFeedPfm') return page([]);
      throw new Error('old card unavailable');
    },
  }), error => { partial = error.partial; return !!partial; });
  assert.equal(partial.complete, false);
  assert.deepEqual(partial.rechecks.unreadIds, ['old']);
  const calls = [];
  const result = await resumeHistory(partial, { request: async (operation, variables) => {
    calls.push({ operation, id: variables.id });
    return { getTransactionOperationDetails: item('native-old') };
  } });
  assert.deepEqual(calls, [{ operation: 'TransactionOperationDetails', id: 'old' }]);
  assert.equal(result.complete, true);
  assert.equal(result.rechecks.complete, true);
  assert.equal(partial.complete, false);
});

const yandexEnvironment = (answers, calls = []) => ({ origin: 'https://bank.yandex.ru',
  fetch: async (url, options) => {
    const body = JSON.parse(options.body);
    calls.push({ url, method: options.method, credentials: options.credentials, body });
    return { ok: true, json: async () => ({ data: answers[body.operationName] }) };
  } });
const products = amount => ({ homeProducts: { products: [
  { id: 'CARD', title: 'Карта', value: { amount, currency: 'RUB' } },
  { id: 'SAVING', title: 'Сейвы', value: { amount: '99.00', currency: 'RUB' } }] } });
const saving = (id, name, status, amount) => ({ id, name, status, productCode: 'SavingsAccount',
  balance: { amount, currency: 'RUB' } });

test('Card and open savings balances are read with the bank persisted queries', async () => {
  const calls = [];
  const balances = await requestYandexBalances(yandexEnvironment({ HomeProductsV2: products('5717.00'),
    SavingsAccountsList: { bankUser: { savingsAccounts: { items: [
      saving('sa_open', 'Округление', 'Open', '12.30'), saving('sa_plain', null, 'Open', '1.00'),
      saving('sa_closed', 'Старый', 'Closed', '0.00'), saving('dp_off', null, 'Annulled', '0.00')] } } },
  }, calls));
  assert.deepEqual(balances, [
    { account: 'CARD', account_name: 'Карта Пэй', amount: '5717.00', currency: 'RUB' },
    { account: 'sa_open', account_name: 'Округление', amount: '12.30', currency: 'RUB' },
    { account: 'sa_plain', account_name: null, amount: '1.00', currency: 'RUB' }]);
  assert.deepEqual(calls.map(call => call.body.operationName), ['HomeProductsV2', 'SavingsAccountsList']);
  assert.ok(calls.every(call => call.method === 'POST' && call.credentials === 'same-origin'
    && call.url.startsWith('https://bank.yandex.ru/graphql?operationId=')
    && call.url.endsWith(call.body.extensions.persistedQuery.sha256Hash)));
});

test('A saving in an unknown state stops the balance read', async () => {
  await assert.rejects(requestYandexBalances(yandexEnvironment({ HomeProductsV2: products('1.00'),
    SavingsAccountsList: { bankUser: { savingsAccounts: { items: [
      saving('sa_x', null, 'Frozen', '5.00')] } } } })), /состояни/);
});

test('A missing card balance stops the balance read', async () => {
  await assert.rejects(requestYandexBalances(yandexEnvironment({
    HomeProductsV2: { homeProducts: { products: [] } },
    SavingsAccountsList: { bankUser: { savingsAccounts: { items: [] } } } })), /карт/);
});

test('Balances are not requested outside the bank page', async () => {
  let wasCalled = false;
  await assert.rejects(requestYandexBalances({ origin: 'https://example.com',
    fetch: async () => { wasCalled = true; } }), /банка/);
  assert.equal(wasCalled, false);
});

/** Bank feed with a small page: a window with more items returns a cursor. */
function fakeBank(rows, pageSize = 2) {
  const calls = [];
  const request = async (operation, variables) => {
    if (operation === 'TransactionOperationDetails') {
      return { getTransactionOperationDetails: item(variables.id) };
    }
    const { dateStartInclusive, dateEndExclusive } = variables.input.baseFilters.date;
    calls.push({ from: dateStartInclusive, to: dateEndExclusive, cursor: variables.input.cursor });
    const inside = rows.filter(row => Date.parse(row.date) >= Date.parse(dateStartInclusive)
      && Date.parse(row.date) < Date.parse(dateEndExclusive));
    const offset = Number(variables.input.cursor ?? 0);
    const next = offset + pageSize < inside.length ? String(offset + pageSize) : null;
    return page(inside.slice(offset, offset + pageSize), next);
  };
  return { request, calls };
}

const at = (id, time) => item(id, { date: `2026-07-04T${time}+03:00` });

test('a day that fits one page is read without a cursor', async () => {
  const bank = fakeBank([at('a', '10:00:00'), at('b', '11:00:00')]);
  const result = await collectHistory({ ...scope, request: bank.request });
  assert.equal(result.complete, true);
  assert.deepEqual(result.records.map(row => row.id).sort(), ['a', 'b']);
  assert.deepEqual(bank.calls, [{ from: '2026-07-03T21:00:00.000Z', to: '2026-07-04T21:00:00.000Z',
    cursor: undefined }]);
  assert.equal(result.pages.length, 1);
});

test('a window with a cursor is split in half by time instead of followed', async () => {
  const rows = ['01', '05', '09', '13', '17', '21'].map(hour => at(`h${hour}`, `${hour}:00:00`));
  const bank = fakeBank(rows);
  const result = await collectHistory({ ...scope, request: bank.request });
  assert.equal(result.complete, true);
  assert.deepEqual(result.records.map(row => row.id).sort(), rows.map(row => row.id));
  assert.ok(bank.calls.every(call => call.cursor === undefined));
  assert.ok(result.pages.every(read => read.cursor === null));
  assert.ok(result.windowSplits > 0);
});

test('more operations than a page within one second are read by cursor and marked', async () => {
  const rows = ['a', 'b', 'c'].map(id => at(id, '10:00:00'));
  const bank = fakeBank(rows);
  const result = await collectHistory({ ...scope, request: bank.request });
  assert.equal(result.complete, true);
  assert.deepEqual(result.records.map(row => row.id).sort(), ['a', 'b', 'c']);
  assert.equal(result.cursorWindows.length, 1);
  assert.ok(Date.parse(result.cursorWindows[0].to) - Date.parse(result.cursorWindows[0].from) <= 1000);
});

test('pending items are read and repeated observations are counted', async () => {
  const result = await collectHistory({ ...scope, includeDetails: false,
    request: async () => page([item('a')], null, [item('a'), item('b')]),
  });
  assert.equal(result.records.length, 2);
  assert.equal(result.observations.length, 3);
  assert.equal(result.repeatedObservations, 1);
});

test('repeated cursor inside a one-second window stops with partial data', async () => {
  const request = async (operation, variables) => page([at('a', '10:00:00'), at('b', '10:00:00')], 'same');
  await assert.rejects(collectHistory({ ...scope, request }),
    error => error.partial.complete === false);
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

test('native detail ID is retained together with the requested feed ID', async () => {
  const request = async operation => operation === 'TransactionsFeedPfm'
    ? page([item('feed-one'), item('feed-two')])
    : { getTransactionOperationDetails: item('native-id') };

  const result = await collectHistory({ ...scope, request });

  assert.equal(result.complete, true);
  assert.deepEqual(result.details.map(row => row.requestedId).sort(), ['feed-one', 'feed-two']);
  assert.deepEqual(result.details.map(row => row.id), ['native-id', 'native-id']);
  assert.deepEqual(result.unreadIds, []);
});

test('detail without an identifier stays unread with its raw response preserved', async () => {
  const request = async operation => operation === 'TransactionsFeedPfm'
    ? page([item('feed')]) : { getTransactionOperationDetails: { date: item('a').date } };
  await assert.rejects(collectHistory({ ...scope, request }), error => {
    assert.deepEqual(error.partial.unreadIds, ['feed']);
    assert.equal(error.partial.detailObservations.length, 1);
    return true;
  });
});

test('transient HTTP failure is retried before a detail is marked unread', async () => {
  let attempts = 0;
  const pauses = [];
  const request = async (operation, variables) => {
    if (operation === 'TransactionsFeedPfm') return page([item('a')]);
    attempts += 1;
    if (attempts < 3) throw Object.assign(new Error('HTTP 500'), { httpStatus: 500 });
    return { getTransactionOperationDetails: item(variables.id) };
  };
  const result = await collectHistory({ ...scope, request, pause: async delay => pauses.push(delay) });
  assert.equal(result.complete, true);
  assert.equal(attempts, 3);
  assert.deepEqual(pauses, [1000, 3000]);
});

test('one permanently failing card does not prevent reading later cards', async () => {
  const request = async (operation, variables) => {
    if (operation === 'TransactionsFeedPfm') return page([item('bad'), item('good')]);
    if (variables.id === 'bad') throw new Error('Unusable detail');
    return { getTransactionOperationDetails: item('native') };
  };
  await assert.rejects(collectHistory({ ...scope, request, concurrency: 1 }), error => {
    assert.deepEqual(error.partial.unreadIds, ['bad']);
    assert.equal(error.partial.details[0].requestedId, 'good');
    return true;
  });
});

test('resume reads only missing cards and keeps the original snapshot unchanged', async () => {
  let partial;
  const request = async (operation, variables) => {
    if (operation === 'TransactionsFeedPfm') return page([item('a'), item('b')]);
    if (variables.id === 'b') throw new Error('offline');
    return { getTransactionOperationDetails: item('native-a') };
  };
  try { await collectHistory({ ...scope, request }); } catch (error) { partial = error.partial; }
  const before = JSON.stringify(partial), calls = [];

  const result = await resumeHistory(partial, { request: async (operation, variables) => {
    calls.push({ operation, variables });
    assert.equal(operation, 'TransactionOperationDetails');
    return { getTransactionOperationDetails: item('native-b') };
  } });

  assert.equal(result.complete, true);
  assert.deepEqual(calls.map(call => call.variables.id), ['b']);
  assert.equal(result.details.length, 2);
  assert.equal(result.detailObservations.length, 2);
  assert.equal(result.previousDetailErrors.length, 1);
  assert.equal(result.detailErrors.length, 0);
  assert.equal(JSON.stringify(partial), before);
});

test('resume rejects missing feed records instead of claiming a complete collection', async () => {
  const snapshot = await collectHistory({ ...scope, includeDetails: false,
    request: async () => page([item('a')]) });
  snapshot.records = [];
  let calls = 0;
  await assert.rejects(resumeHistory(snapshot, { request: async () => { calls += 1; } }),
    /список/);
  assert.equal(calls, 0);
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
