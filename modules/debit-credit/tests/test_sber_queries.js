const test = require('node:test');
const assert = require('node:assert/strict');
const { collectSberHistory, requestOperations, sberDate } = require('../lib/sber_queries.js');

const scope = { fromInclusive: '2026-02-01T00:00:00+03:00', toExclusive: '2026-03-01T00:00:00+03:00' };
const operation = (uohId, extra = {}) => ({ uohId, date: '10.02.2026T12:00:00', ...extra });
const answer = operations => ({ success: true, body: { operations } });

test('Moscow dates are written in the bank format', () => {
  assert.equal(sberDate(Date.parse('2026-02-01T00:00:00+03:00')), '01.02.2026T00:00:00');
  assert.equal(sberDate(Date.parse('2026-02-28T20:59:59Z')), '28.02.2026T23:59:59');
});

test('History is read page by page until a short page, with the period in the request', async () => {
  const calls = [];
  const pages = [[operation('a'), operation('b')], [operation('c')]];
  const result = await collectSberHistory({ ...scope, pageSize: 2, request: async query => {
    calls.push(query);
    return answer(pages[calls.length - 1]);
  } });
  assert.equal(result.complete, true);
  assert.equal(result.adapter, 'sber-operations');
  assert.deepEqual(result.records.map(row => row.uohId), ['a', 'b', 'c']);
  assert.deepEqual(calls.map(call => call.paginationOffset), [0, 2]);
  assert.ok(calls.every(call => call.from === '01.02.2026T00:00:00' && call.to === '28.02.2026T23:59:59'));
  assert.ok(calls.every(call => call.paginationSize === 2));
});

test('Operation outside the period is kept aside with a reason', async () => {
  const early = operation('e', { date: '31.01.2026T23:59:59' });
  const result = await collectSberHistory({ ...scope, request: async () => answer([early]) });
  assert.equal(result.records.length, 0);
  assert.equal(result.excluded[0].reason, 'outside_requested_period');
});

test('Repeated identical operation is counted, a changed one stops with the partial result', async () => {
  let page = 0;
  const repeated = await collectSberHistory({ ...scope, pageSize: 1, request: async () => {
    page += 1;
    return answer(page <= 2 ? [operation('a')] : []);
  } });
  assert.equal(repeated.records.length, 1);
  assert.equal(repeated.repeatedObservations, 1);
  page = 0;
  await assert.rejects(collectSberHistory({ ...scope, pageSize: 1, request: async () => {
    page += 1;
    return answer(page === 1 ? [operation('a')] : page === 2 ? [operation('a', { form: 'x' })] : []);
  } }), error => error.partial.complete === false && error.partial.conflicts.length === 1);
});

test('Unsuccessful answer stops collection and keeps already read pages', async () => {
  let page = 0;
  await assert.rejects(collectSberHistory({ ...scope, pageSize: 1, request: async () => {
    page += 1;
    return page === 1 ? answer([operation('a')]) : { success: false };
  } }), error => error.partial.records.length === 1 && error.partial.complete === false);
});

test('Page limit without a short page does not claim completeness', async () => {
  await assert.rejects(collectSberHistory({ ...scope, pageSize: 1, maxPages: 2,
    request: async query => answer([operation(`op-${query.paginationOffset}`)]) }),
  error => error.partial.complete === false && error.partial.records.length === 2);
});

test('Operations request is a credentialed POST from the bank page', async () => {
  const calls = [];
  const env = { origin: 'https://online.sberbank.ru', fetch: async (url, options) => {
    calls.push({ url, options });
    return { ok: true, json: async () => answer([]) };
  } };
  const result = await requestOperations({ paginationOffset: 0, paginationSize: 50,
    from: '01.02.2026T00:00:00', to: '28.02.2026T23:59:59' }, env);
  assert.deepEqual(result, answer([]));
  const { url, options } = calls[0];
  assert.equal(url, 'https://web-node3.online.sberbank.ru/uoh-bh/v1/operations/list');
  assert.equal(options.method, 'POST');
  assert.equal(options.credentials, 'include');
  assert.equal(options.headers['X-Requested-With'], 'XMLHttpRequest');
  const body = JSON.parse(options.body);
  assert.equal(body.showHidden, false);
  assert.equal(body.from, '01.02.2026T00:00:00');
});

test('Operations request refuses to run outside the bank', async () => {
  await assert.rejects(requestOperations({}, { origin: 'https://example.com',
    fetch: async () => { throw new Error('must not be called'); } }), /Сбер/);
});

test('Fractional end of period keeps its last whole second', async () => {
  const calls = [];
  await collectSberHistory({ fromInclusive: '2026-02-01T00:00:00+03:00',
    toExclusive: '2026-02-01T12:00:00.500+03:00', request: async query => { calls.push(query); return answer([]); } });
  assert.equal(calls[0].to, '01.02.2026T12:00:00');
});
