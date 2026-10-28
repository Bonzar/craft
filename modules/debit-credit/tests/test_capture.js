const test = require('node:test');
const assert = require('node:assert/strict');

function directBalanceEnvironment(texts, overrides = {}) {
  return { origin: 'https://bank.yandex.ru',
    fetch: async (url, options) => {
      assert.equal(url, 'https://bank.yandex.ru/my');
      assert.equal(options.method, 'GET');
      assert.equal(options.cache, 'no-store');
      assert.equal(options.credentials, 'same-origin');
      return { ok: true, url, text: async () => 'bank-html', headers: { get: () => null } };
    },
    parseHTML: html => {
      assert.equal(html, 'bank-html');
      return { querySelectorAll: () => texts.map(textContent => ({ textContent })) };
    }, ...overrides };
}

test('Equal mobile and desktop balances from a fresh response are retained with request times', async () => {
  const { requestCardBalance } = require('../lib/yandex_bank_capture.js');
  const result = await requestCardBalance(directBalanceEnvironment(['Карта0 ₽', 'Карта0 ₽']));
  assert.equal(result.amount, '0.00');
  assert.equal(result.raw_texts.length, 2);
  assert.equal(result.read_method, 'same_origin_html_no_store');
  assert.match(result.request_started_at, /Z$/);
  assert.match(result.response_received_at, /Z$/);
  assert.equal(result.blocked_amount, null);
});

test('Disagreeing mobile and desktop balances are rejected', async () => {
  const { requestCardBalance } = require('../lib/yandex_bank_capture.js');
  await assert.rejects(requestCardBalance(directBalanceEnvironment(['Карта0 ₽', 'Карта1 ₽'])), /остатк/);
});

test('Login redirects are rejected before the response body is read', async () => {
  const { requestCardBalance } = require('../lib/yandex_bank_capture.js');
  let wasBodyRead = false;
  const environment = directBalanceEnvironment([], { fetch: async () => ({ ok: true,
    url: 'https://bank.yandex.ru/_pay/login', text: async () => { wasBodyRead = true; return ''; } }) });
  await assert.rejects(requestCardBalance(environment), /главная страница/);
  assert.equal(wasBodyRead, false);
});

test('Requests from an unrelated origin are rejected before transport', async () => {
  const { requestCardBalance } = require('../lib/yandex_bank_capture.js');
  let wasCalled = false;
  const environment = directBalanceEnvironment([], { origin: 'https://example.com',
    fetch: async () => { wasCalled = true; } });
  await assert.rejects(requestCardBalance(environment), /сессии банка/);
  assert.equal(wasCalled, false);
});

test('Displayed zero card balance is retained without claiming blocked funds are zero', async () => {
  const { requestCardBalance } = require('../lib/yandex_bank_capture.js');
  const result = await requestCardBalance(directBalanceEnvironment(['Карта 0 ₽']));
  assert.equal(result.amount, '0.00');
  assert.equal(result.currency, 'RUB');
  assert.equal(result.blocked_amount, null);
  assert.equal(result.balance_type, 'displayed_unspecified');
});

test('Card balance is read with kopecks and grouped digits', async () => {
  const { requestCardBalance } = require('../lib/yandex_bank_capture.js');
  assert.equal((await requestCardBalance(directBalanceEnvironment(['Карта 12 345,67 ₽']))).amount, '12345.67');
});

test('Missing or masked card balances are rejected instead of becoming zero', async () => {
  const { requestCardBalance } = require('../lib/yandex_bank_capture.js');
  for (const texts of [[], ['Карта ••• ₽']]) {
    await assert.rejects(requestCardBalance(directBalanceEnvironment(texts)), /остат/);
  }
});

test('Balance is not read from the visible page', () => {
  assert.equal(Object.hasOwn(require('../lib/yandex_bank_capture.js'), 'captureCardBalanceDom'), false);
});
