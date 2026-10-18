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

if (typeof module !== 'undefined') module.exports = { captureBankDom };
