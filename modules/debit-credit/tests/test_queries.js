const test = require('node:test');
const assert = require('node:assert/strict');
const { collectHistory, requestHistory, resumeHistory, collectHistoryWithBalance,
  collectDetailsByIds } = require('../lib/yandex_bank_queries.js');

const scope = {
  fromInclusive: '2026-07-04T00:00:00+03:00',
  toExclusive: '2026-07-06T00:00:00+03:00',
};
const item = (id, extra = {}) => ({ id, date: '2026-07-04T10:00:00+03:00', ...extra });
const page = (items, cursor = null, pendingItems = []) => ({
  getTransactionsFeedPfm: { items, pendingItems, cursor, analytics: [] },
});

const balance = amount => ({ account: 'Карта Пэй', amount, currency: 'RUB',
  balance_type: 'displayed_unspecified', blocked_amount: null });

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

test('Balance observations surround both new history and old-operation rechecks', async () => {
  const calls = [];
  const result = await collectHistoryWithBalance({ ...scope, recheckIds: ['old'],
    readBalance: () => { calls.push('balance'); return balance('1.00'); },
    balanceSourceUrl: 'https://bank.yandex.ru/my',
    request: async (operation, variables) => {
      calls.push(operation);
      return operation === 'TransactionsFeedPfm' ? page([])
        : { getTransactionOperationDetails: item(variables.id) };
    },
  });
  assert.deepEqual(calls, ['balance', 'TransactionsFeedPfm', 'TransactionOperationDetails', 'balance']);
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
  await assert.rejects(collectHistoryWithBalance({ ...scope, recheckIds: ['old'],
    readBalance: () => balance('1.00'), balanceSourceUrl: 'https://bank.yandex.ru/my',
    request: async operation => {
      if (operation === 'TransactionsFeedPfm') return page([]);
      throw new Error('old card unavailable');
    },
  }), error => { partial = error.partial; return !!partial; });
  assert.equal(partial.complete, false);
  assert.deepEqual(partial.rechecks.unreadIds, ['old']);
  assert.equal(partial.balanceObservations.length, 2);
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

test('Balance observations bracket history collection without claiming an atomic checkpoint', async () => {
  let reads = 0;
  const result = await collectHistoryWithBalance({ ...scope, includeDetails: false,
    request: async () => page([]), readBalance: () => balance(reads++ ? '2.00' : '1.00'),
    balanceSourceUrl: 'https://bank.yandex.ru/my',
  });
  assert.equal(result.complete, true);
  assert.equal(result.balanceObservations.length, 2);
  assert.equal(result.balanceObservations[0].phase, 'before');
  assert.equal(result.balanceObservations[1].phase, 'after');
  assert.match(result.balanceObservations[0].observed_at, /Z$/);
  assert.equal(result.balanceControl.changed, true);
  assert.equal(result.balanceControl.atomic, false);
  assert.equal(result.balanceControl.freshness, 'unknown');
});

test('Missing balance preserves a complete history with explicit observation errors', async () => {
  const result = await collectHistoryWithBalance({ ...scope, includeDetails: false,
    request: async () => page([]), readBalance: () => { throw new Error('hidden'); },
    balanceSourceUrl: 'https://bank.yandex.ru/my',
  });
  assert.equal(result.complete, true);
  assert.equal(result.balanceObservations.length, 2);
  assert.equal(result.balanceObservations[0].balance, null);
  assert.equal(result.balanceControl.changed, null);
  assert.equal(result.balanceObservations[0].error, 'hidden');
});

test('Partial history retains balance observations when a history request fails', async () => {
  await assert.rejects(collectHistoryWithBalance({ ...scope,
    request: async () => { throw new Error('offline'); }, readBalance: () => balance('0.00'),
    balanceSourceUrl: 'https://bank.yandex.ru/my',
  }), error => error.partial.complete === false
    && error.partial.balanceObservations.length === 2);
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
