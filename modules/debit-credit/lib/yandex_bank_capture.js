/**
 * Read-only DOM projection for tab.playwright.evaluate(captureBankDom).
 * The caller supplies URL, capture time and display timezone outside page scope.
 * No navigation, requests, clicks, application state or storage access.
 * @returns {{title:string,date:string,amount:string,rows:string[][],
 *   transferTitles:string[],pointsAmount:boolean}}
 */
function captureBankDom() {
  const root = document.querySelector('[data-testid="transactionOperationDetails"]');
  if (!root || !root.hasAttribute('open')) {
    throw new Error('Открытая карточка операции не найдена');
  }
  const info = root.querySelector('div[class*="OperationInfo-module"]');
  if (!info) throw new Error('Карточка ещё не загружена');
  const transferTitles = Array.from(info.querySelectorAll('[class*="OperationTitle"] p'))
    .map(element => element.innerText.trim());
  const title = info.querySelector('p[class*="operationTitle"]')?.innerText.trim()
    || transferTitles.join(' → ');
  const date = root.querySelector('[data-testid="operation-info-block-date-text"]')?.innerText;
  const pointsAmount = !!info.querySelector('[class*="YandexPlusPoints"]');
  const amount = pointsAmount
    ? info.querySelector('[class*="YandexPlusPoints"] span[class*="plusPoints"]')?.innerText
    : root.querySelector('[data-testid="operation-info-block-amount"]')?.innerText;
  if (!title || !date || !amount) throw new Error('Поля карточки ещё не загружены');
  const rows = Array.from(root.querySelectorAll('li')).map(element =>
    element.innerText.split('\n').map(value => value.trim()).filter(Boolean));
  return { title, date, amount, rows, transferTitles, pointsAmount };
}

/** Read only the visible main-page card tile, never storage or application state.
 * @returns {{account:string,amount:string,currency:string,balance_type:string,
 *   blocked_amount:null,raw_text:string}} */
function captureCardBalanceDom() {
  const tiles = Array.from(document.querySelectorAll('a[role="button"][href="/my/new-debit"]'))
    .filter(element => element.getClientRects().length > 0);
  if (tiles.length !== 1) throw new Error('Однозначный видимый остаток карты не найден');
  return parseCardBalanceText(tiles[0].innerText.trim());
}

/** @param {string} rawText @returns {object} */
function parseCardBalanceText(rawText) {
  const normalized = rawText.replace(/\s+/g, ' ');
  const match = /^Карта\s*(-?(?:\d{1,3}(?: \d{3})+|\d+))(?:,(\d{1,2}))? ₽$/.exec(normalized);
  if (!match) throw new Error('Показанный остаток карты не распознан');
  return { account: 'Карта Пэй', amount: `${match[1].replace(/ /g, '')}.${(match[2] || '').padEnd(2, '0')}`,
    currency: 'RUB', balance_type: 'displayed_unspecified', blocked_amount: null, raw_text: rawText };
}

/** Request fresh server HTML without navigation, page controls or credential extraction.
 * Duplicate responsive tiles must agree. Their raw texts remain in the observation.
 * @param {object=} environment @returns {Promise<object>} */
async function requestCardBalance(environment) {
  const env = environment || { origin: window.location.origin, fetch: window.fetch.bind(window),
    parseHTML: html => new DOMParser().parseFromString(html, 'text/html') };
  if (env.origin !== 'https://bank.yandex.ru' || typeof env.fetch !== 'function'
      || typeof env.parseHTML !== 'function') throw new Error('Запуск нужен в сессии банка');
  const requestStartedAt = new Date().toISOString();
  const response = await env.fetch('https://bank.yandex.ru/my', {
    method: 'GET', credentials: 'same-origin', cache: 'no-store',
  });
  if (!response.ok || response.url !== 'https://bank.yandex.ru/my') {
    throw new Error('Не получена главная страница банка');
  }
  const page = env.parseHTML(await response.text());
  const texts = Array.from(page.querySelectorAll('a[role="button"][href="/my/new-debit"]'))
    .map(tile => tile.textContent.replace(/\s+/g, ' ').trim());
  const unique = [...new Set(texts)];
  if (unique.length !== 1) throw new Error('В ответе нет однозначного остатка карты');
  return { ...parseCardBalanceText(unique[0]), raw_texts: texts,
    read_method: 'same_origin_html_no_store', request_started_at: requestStartedAt,
    response_received_at: new Date().toISOString(), server_date: response.headers.get('date'),
    cache_control: response.headers.get('cache-control') };
}

if (typeof module !== 'undefined') module.exports = { captureBankDom, captureCardBalanceDom, requestCardBalance };
