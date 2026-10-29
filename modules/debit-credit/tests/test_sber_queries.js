const test = require('node:test');
const assert = require('node:assert/strict');
const { collectSberHistory, requestOperations, requestSberBalances, sberDate } = require('../lib/sber_queries.js');

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

const SECTION_URL = 'https://web-node3.online.sberbank.ru/main-screen/rest/v2/m1/web/section/meta';
const CARD_INFO_URL = 'https://web-node3.online.sberbank.ru/ufs-carddetail/rest/card/v1/cardInfo';
const money = amount => ({ amount, currency: { code: 'RUB', name: 'руб.' } });
const products = (overrides = {}) => ({ success: true, body: { sections: { technicalSection: {
  sectionProductData: {
    cardsInWallet: { state: 'SUCCESS', data: [
      { id: 1000000001, name: 'Кредитная СберКарта', type: 'credit', state: 'active',
        number: '2202 20** **** 0001', availableLimit: money('148061.00') },
      { id: 1000000002, name: 'MIR Classic', type: 'debit', state: 'active',
        number: '2202 20** **** 0002', availableLimit: money('12.50') }] },
    accounts: { state: 'SUCCESS', data: [{ id: 3000000001, name: 'Сберегательный счет', state: 'OPENED',
      number: '40817810000000000003', balance: money('0.00') }] },
    ctaccounts: { state: 'SUCCESS', data: [] },
    sberThanks: { state: 'SUCCESS', data: [{ loyaltyType: 'SBERSPASIBO', balance: { amount: '0.12' } }] },
    ...overrides } } } } });
const cardInfo = { success: true, body: { cardDetails: { cards: [{ id: 1000000001,
  creditType: { creditLimit: money('150000.00'), creditOwnSum: money('0.00'), creditDebt: money('1939.00') } }] } } };
const sberEnvironment = (answers, calls = []) => ({ origin: 'https://online.sberbank.ru',
  fetch: async (url, options) => {
    calls.push({ url, options });
    return { ok: true, json: async () => answers[url] };
  } });

test('Cards, accounts and SberSpasibo balances come with the history resource ids', async () => {
  const calls = [];
  const balances = await requestSberBalances(sberEnvironment({ [SECTION_URL]: products(),
    [CARD_INFO_URL]: cardInfo }, calls));
  assert.deepEqual(balances, [
    { account: 'card:1000000001', account_name: 'Кредитная СберКарта •• 0001', amount: '-1939.00', currency: 'RUB' },
    { account: 'card:1000000002', account_name: 'MIR Classic •• 0002', amount: '12.50', currency: 'RUB' },
    { account: 'account:3000000001', account_name: 'Сберегательный счет •• 0003', amount: '0.00', currency: 'RUB' },
    { account: 'СберСпасибо', account_name: 'СберСпасибо', amount: '0.12', currency: 'SBER_SPASIBO' }]);
  assert.deepEqual(JSON.parse(calls[0].options.body), { withData: true, forceUpdate: true });
  assert.deepEqual(JSON.parse(calls[1].options.body), { cardIds: [1000000001] });
  assert.ok(calls.every(call => call.options.method === 'POST' && call.options.credentials === 'include'
    && call.options.headers['X-Requested-With'] === 'XMLHttpRequest'));
});

test('A product section the bank did not load stops the balance read', async () => {
  await assert.rejects(requestSberBalances(sberEnvironment({
    [SECTION_URL]: products({ accounts: { state: 'ERROR', data: [] } }), [CARD_INFO_URL]: cardInfo })),
  /accounts/);
});

test('A payment account the reader does not know yet stops the balance read', async () => {
  await assert.rejects(requestSberBalances(sberEnvironment({
    [SECTION_URL]: products({ ctaccounts: { state: 'SUCCESS', data: [{ id: 1 }] } }),
    [CARD_INFO_URL]: cardInfo })), /Платёжн/);
});

test('A credit card without its debt stops the balance read', async () => {
  await assert.rejects(requestSberBalances(sberEnvironment({ [SECTION_URL]: products(),
    [CARD_INFO_URL]: { success: true, body: { cardDetails: { cards: [] } } } })), /задолженност/);
});

test('Balances are not requested outside the bank tab', async () => {
  await assert.rejects(requestSberBalances({ origin: 'https://example.com',
    fetch: async () => { throw new Error('must not be called'); } }), /Сбер/);
});
