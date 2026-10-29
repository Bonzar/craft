/** Own-account balances read before and after a history collection. */
const BALANCE_AMOUNT = /^-?\d+(\.\d+)?$/;
const BALANCE_CURRENCY = /^[A-Z][A-Z_]*$/;

/** @param {object} row @returns {boolean} */
function isValidBalance(row) {
  return typeof row?.account === 'string' && row.account !== ''
    && (row.account_name === null || typeof row.account_name === 'string')
    && typeof row.amount === 'string' && BALANCE_AMOUNT.test(row.amount)
    && typeof row.currency === 'string' && BALANCE_CURRENCY.test(row.currency);
}

/** @param {() => Promise<Array<object>>} readBalances @returns {Promise<object>} */
async function observeBalances(readBalances) {
  const observedAt = new Date().toISOString();
  try {
    const accounts = await readBalances();
    if (!Array.isArray(accounts) || !accounts.length || !accounts.every(isValidBalance)
        || new Set(accounts.map(row => row.account)).size !== accounts.length) {
      throw new Error('Банк вернул некорректные остатки');
    }
    return { observed_at: observedAt, accounts, error: null };
  } catch (error) {
    return { observed_at: observedAt, accounts: null,
      error: error instanceof Error ? error.message : 'Не удалось прочитать остатки' };
  }
}

/** @param {object} before @param {object} after @returns {boolean|null} */
function balancesChanged(before, after) {
  if (!before.accounts || !after.accounts) return null;
  const amounts = new Map(before.accounts.map(row => [row.account, `${row.amount} ${row.currency}`]));
  return before.accounts.length !== after.accounts.length
    || after.accounts.some(row => amounts.get(row.account) !== `${row.amount} ${row.currency}`);
}

/** Balances surround the history; a partial history keeps them too.
 * @param {() => Promise<object>} collect @param {() => Promise<Array<object>>} readBalances
 * @returns {Promise<object>} */
async function collectWithBalances(collect, readBalances) {
  const before = await observeBalances(readBalances);
  let result, failure;
  try { result = await collect(); } catch (error) {
    if (!error?.partial) throw error;
    result = error.partial;
    failure = error;
  }
  const after = await observeBalances(readBalances);
  const output = { ...result, balances: { before, after, changed: balancesChanged(before, after) } };
  if (failure) { failure.partial = output; throw failure; }
  return output;
}

if (typeof module !== 'undefined') module.exports = { collectWithBalances };
