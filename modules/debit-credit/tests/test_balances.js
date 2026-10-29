const test = require('node:test');
const assert = require('node:assert/strict');
const { collectWithBalances } = require('../lib/balances.js');

const row = (account, amount) => ({ account, account_name: `Счёт ${account}`, amount, currency: 'RUB' });
const history = { complete: true, records: [] };

test('Balances are read before and after the history and kept with the snapshot', async () => {
  const calls = [];
  const result = await collectWithBalances(async () => { calls.push('history'); return history; },
    async () => { calls.push('balances'); return [row('a', '10.00')]; });
  assert.deepEqual(calls, ['balances', 'history', 'balances']);
  assert.equal(result.complete, true);
  assert.deepEqual(result.balances.before.accounts, [row('a', '10.00')]);
  assert.deepEqual(result.balances.after.accounts, [row('a', '10.00')]);
  assert.match(result.balances.before.observed_at, /Z$/);
  assert.equal(result.balances.before.error, null);
  assert.equal(result.balances.changed, false);
});

test('A balance that moved during the history read is marked as changed', async () => {
  let reads = 0;
  const result = await collectWithBalances(async () => history,
    async () => [row('a', reads++ ? '9.00' : '10.00')]);
  assert.equal(result.balances.changed, true);
});

test('An account that appeared during the history read is marked as changed', async () => {
  let reads = 0;
  const result = await collectWithBalances(async () => history,
    async () => (reads++ ? [row('a', '1.00'), row('b', '0.00')] : [row('a', '1.00')]));
  assert.equal(result.balances.changed, true);
});

test('A failed balance read keeps the history and names the error', async () => {
  const result = await collectWithBalances(async () => history,
    async () => { throw new Error('offline'); });
  assert.equal(result.complete, true);
  assert.equal(result.balances.before.accounts, null);
  assert.equal(result.balances.before.error, 'offline');
  assert.equal(result.balances.changed, null);
});

test('Malformed balances are not kept as numbers', async () => {
  const cases = [[], [row('a', '1,00')], [row('a', '1.00'), row('a', '2.00')],
    [{ ...row('a', '1.00'), currency: 'rub' }], [{ ...row('', '1.00') }]];
  for (const accounts of cases) {
    const result = await collectWithBalances(async () => history, async () => accounts);
    assert.equal(result.balances.before.accounts, null, JSON.stringify(accounts));
    assert.match(result.balances.before.error, /остат/);
  }
});

test('A failed history keeps its partial result together with the balances', async () => {
  const failure = Object.assign(new Error('offline'), { partial: { complete: false, records: [1] } });
  await assert.rejects(collectWithBalances(async () => { throw failure; },
    async () => [row('a', '1.00')]),
  error => error.message === 'offline' && error.partial.complete === false
    && error.partial.records.length === 1 && error.partial.balances.after.accounts.length === 1);
});

test('A history error without a partial result is not hidden', async () => {
  await assert.rejects(collectWithBalances(async () => { throw new Error('bad options'); },
    async () => [row('a', '1.00')]), /bad options/);
});
