const test = require('node:test');
const assert = require('node:assert/strict');
const { collectTbankHistory, requestTbankBalances, requestTimeline } = require('../lib/tbank_queries.js');

const OWN = ['1000000001', '1000000002'];
const scope = { fromInclusive: '2026-01-15T00:00:00+03:00',
  toExclusive: '2026-03-10T00:00:00+03:00', accounts: OWN };
const operation = (id, extra = {}) => ({ id, account: OWN[0], type: 'Debit', status: 'OK',
  operationTime: { milliseconds: Date.parse('2026-02-01T12:00:00+03:00') }, ...extra });
const answer = payload => ({ resultCode: 'OK', payload });

test('Period is read in chunks of at most 31 days with only the requested accounts', async () => {
  const calls = [];
  const result = await collectTbankHistory({ ...scope, request: async query => {
    calls.push(query);
    return answer(calls.length === 2 ? [operation('a')] : []);
  } });
  assert.equal(result.complete, true);
  assert.equal(result.adapter, 'tbank-timeline');
  assert.deepEqual(result.accounts, OWN);
  assert.equal(calls[0].start, Date.parse(scope.fromInclusive));
  assert.equal(calls.at(-1).end, Date.parse(scope.toExclusive));
  for (let i = 1; i < calls.length; i += 1) assert.equal(calls[i].start, calls[i - 1].end);
  assert.ok(calls.every(call => call.end - call.start <= 31 * 86400000));
  assert.ok(calls.every(call => JSON.stringify(call.accounts) === JSON.stringify(OWN)));
  assert.deepEqual(result.records.map(row => row.id), ['a']);
  assert.equal(result.chunks.length, calls.length);
});

test('Collection without own accounts is refused before any bank request', async () => {
  let requests = 0;
  await assert.rejects(collectTbankHistory({ ...scope, accounts: [],
    request: async () => { requests += 1; return answer([]); } }), /счет/i);
  assert.equal(requests, 0);
});

test('Operation of an account that was not requested is kept aside, not dropped', async () => {
  const foreign = operation('f', { account: '9999999999' });
  const result = await collectTbankHistory({ ...scope, request: async () => answer([foreign]) });
  assert.equal(result.records.length, 0);
  assert.equal(result.excluded[0].reason, 'account_not_requested');
  assert.equal(result.excluded[0].record.id, 'f');
});

test('Operation outside the requested period is kept aside with a reason', async () => {
  const early = operation('e', { operationTime: { milliseconds: Date.parse('2025-12-31T12:00:00+03:00') } });
  const result = await collectTbankHistory({ ...scope, request: async () => answer([early]) });
  assert.equal(result.excluded[0].reason, 'outside_requested_period');
  assert.equal(result.records.length, 0);
});

test('Repeated identical operation is counted, a changed one stops collection with the partial result', async () => {
  const repeated = await collectTbankHistory({ ...scope, request: async () => answer([operation('a')]) });
  assert.equal(repeated.records.length, 1);
  assert.equal(repeated.repeatedObservations, repeated.chunks.length - 1);
  let step = 0;
  await assert.rejects(collectTbankHistory({ ...scope, request: async () => {
    step += 1;
    return answer([operation('a', { status: step === 1 ? 'OK' : 'FAILED' })]);
  } }), error => error.partial.complete === false && error.partial.conflicts.length === 1
    && error.partial.records.length === 1);
});

test('Bank error code stops collection and keeps already read chunks', async () => {
  let step = 0;
  await assert.rejects(collectTbankHistory({ ...scope, request: async () => {
    step += 1;
    return step === 1 ? answer([operation('a')]) : { resultCode: 'INVALID_REQUEST_DATA' };
  } }), error => error.partial.records.length === 1 && error.partial.complete === false);
});

test('Transient HTTP errors are retried with a pause', async () => {
  const pauses = [];
  let attempts = 0;
  const result = await collectTbankHistory({ ...scope, pause: async ms => { pauses.push(ms); },
    request: async () => {
      attempts += 1;
      if (attempts === 1) { const error = new Error('busy'); error.httpStatus = 503; throw error; }
      return answer([]);
    } });
  assert.equal(result.complete, true);
  assert.equal(pauses.length, 1);
});

test('Timeline request is a GET from the bank page and keeps the session out of the result', async () => {
  const calls = [];
  const env = { origin: 'https://www.tbank.ru',
    resourceUrls: ['https://www.tbank.ru/mybank/api/web/bundles/v1/allBundles?sessionid=SECRET&origin=web'
      + '&appName=supreme&appVersion=1&platform=web'],
    fetch: async (url, options) => {
      calls.push({ url: new URL(url), options });
      return { ok: true, json: async () => answer([]) };
    } };
  const result = await requestTimeline({ start: 1, end: 2, accounts: OWN }, env);
  assert.deepEqual(result, answer([]));
  const { url, options } = calls[0];
  assert.equal(url.pathname, '/mybank/api/operations/timeline/public/legacy/v1/operations');
  assert.equal(url.searchParams.get('accounts'), OWN.join(','));
  assert.equal(url.searchParams.get('start'), '1');
  assert.equal(url.searchParams.get('end'), '2');
  assert.equal(url.searchParams.get('sessionid'), 'SECRET');
  assert.equal(options.method, 'GET');
  assert.equal(JSON.stringify(result).includes('SECRET'), false);
});

test('Timeline request refuses to run outside the bank or without a session', async () => {
  const fetch = async () => { throw new Error('must not be called'); };
  await assert.rejects(requestTimeline({ start: 1, end: 2, accounts: OWN },
    { origin: 'https://example.com', resourceUrls: [], fetch }), /Т-Банк/);
  await assert.rejects(requestTimeline({ start: 1, end: 2, accounts: OWN },
    { origin: 'https://www.tbank.ru', resourceUrls: [], fetch }), /сесси/i);
});

const rub = value => ({ currency: { code: 643, name: 'RUB', strCode: '643' }, value });
const tbankAccounts = (extra = []) => ({ resultCode: 'OK', payload: [
  { id: '1000000001', name: 'Основной', accountType: 'Current', status: 'NORM', currency: { name: 'RUB' },
    moneyAmount: rub(12.5), creditLimit: rub(0), sharedByMeFlag: false },
  { id: '1000000002', name: 'Платинум', accountType: 'Credit', status: 'NORM', currency: { name: 'RUB' },
    moneyAmount: rub(1719.3), creditLimit: rub(135000), debtAmount: rub(133280.7), sharedByMeFlag: false },
  { id: '1000000003', name: 'Накопительный счет', accountType: 'Saving', status: 'NORM',
    currency: { name: 'RUB' }, moneyAmount: rub(0), sharedByMeFlag: false },
  { id: '1000000004', name: 'Чужой детский', accountType: 'SharedCurrentKids', moneyAmount: rub(340.11),
    shared: { ownerName: 'Другой человек' } },
  { id: '1000000005', name: 'Сбербанк *0000', accountType: 'ExternalAccount' },
  { id: '1000000006', name: 'Долями', accountType: 'BNPL', status: 'NOCONTRACTS' },
  ...extra] });
const tbankEnvironment = (answer, calls = []) => ({ origin: 'https://www.tbank.ru',
  resourceUrls: ['https://www.tbank.ru/api/common/v1/session_status?sessionid=SECRET&origin=web'],
  fetch: async (url, options) => {
    calls.push({ url, options });
    return { ok: true, json: async () => answer };
  } });

test('Own account balances are read, a credit card owes the used limit', async () => {
  const calls = [];
  const balances = await requestTbankBalances(tbankEnvironment(tbankAccounts(), calls));
  assert.deepEqual(balances, [
    { account: '1000000001', account_name: 'Основной', amount: '12.50', currency: 'RUB' },
    { account: '1000000002', account_name: 'Платинум', amount: '-133280.70', currency: 'RUB' },
    { account: '1000000003', account_name: 'Накопительный счет', amount: '0.00', currency: 'RUB' }]);
  const url = new URL(calls[0].url);
  assert.equal(url.pathname, '/api/common/v1/accounts_light_ib');
  assert.equal(url.searchParams.get('sessionid'), 'SECRET');
  assert.equal(calls[0].options.credentials, 'include');
  assert.equal(calls[0].options.method, 'GET');
});

test('An account of an unknown kind stops the balance read', async () => {
  await assert.rejects(requestTbankBalances(tbankEnvironment(tbankAccounts([
    { id: '1000000009', name: 'Новое', accountType: 'Deposit', moneyAmount: rub(5) }]))), /Deposit/);
});

test('A refused account list stops the balance read', async () => {
  await assert.rejects(requestTbankBalances(tbankEnvironment({ resultCode: 'INSUFFICIENT_PRIVILEGES' })),
    /INSUFFICIENT_PRIVILEGES/);
});
