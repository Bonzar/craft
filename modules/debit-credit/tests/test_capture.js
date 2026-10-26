const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function captureBalanceWithDom(texts) {
  const sandbox = { document: { querySelectorAll() {
    return texts.map(innerText => ({ innerText, getClientRects: () => [1] }));
  } }, module: { exports: {} } };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,
    '../lib/yandex_bank_capture.js'), 'utf8'), sandbox);
  return () => sandbox.module.exports.captureCardBalanceDom();
}

test('Displayed zero card balance is retained without claiming blocked funds are zero', () => {
  const result = captureBalanceWithDom(['Карта\n0 ₽'])();
  assert.equal(result.amount, '0.00');
  assert.equal(result.currency, 'RUB');
  assert.equal(result.blocked_amount, null);
  assert.equal(result.balance_type, 'displayed_unspecified');
});

test('Displayed card balance is read with kopecks and grouped digits', () => {
  assert.equal(captureBalanceWithDom(['Карта\n12 345,67 ₽'])().amount, '12345.67');
});

test('Missing, masked or ambiguous card balances are rejected instead of becoming zero', () => {
  for (const texts of [[], ['Карта ••• ₽'], ['Карта 1 ₽', 'Карта 2 ₽']]) {
    assert.throws(captureBalanceWithDom(texts), /остаток/);
  }
});

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
