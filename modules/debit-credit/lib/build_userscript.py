"""Собрать один читающий userscript без cookies, ключей и личных данных."""

import os
import argparse
import json
from datetime import datetime
from pathlib import Path

HEADER = '''// ==UserScript==
// @name         Debit & Credit — сбор истории Яндекс Банка
// @version      0.3.0
// @match        https://bank.yandex.ru/my/transactions-history*
// @match        https://bank.yandex.ru/my
// @run-at       document-idle
// @grant        none
// ==/UserScript==
'''

INTERFACE = r'''
const isBalancePage = window.location.pathname === '/my';
if (window.location.origin !== 'https://bank.yandex.ru'
    || !(isBalancePage || window.location.pathname === '/my/transactions-history')) {
  throw new Error('Скрипт работает только на главной странице или в истории банка');
}
if (document.getElementById('debit-credit-history-reader')) return;
const panel = document.createElement('div');
panel.id = 'debit-credit-history-reader';
panel.style.cssText = 'position:fixed;right:16px;bottom:16px;z-index:2147483647;background:white;color:black;padding:14px;border:1px solid #888;border-radius:12px;font:14px system-ui;max-width:340px;box-shadow:0 3px 16px #0003';
const button = document.createElement('button');
button.textContent = isBalancePage ? 'Собрать историю и остаток в JSON' : 'Собрать историю в JSON';
const resumeButton = document.createElement('button');
resumeButton.textContent = 'Продолжить из JSON';
resumeButton.style.cssText = 'display:block;margin-top:8px';
const status = document.createElement('div');
status.style.cssText = 'margin-top:8px;white-space:pre-wrap';
status.textContent = isBalancePage
  ? 'История и показанный остаток карты. Платежи не выполняются.'
  : 'Только история. Для чтения остатка запусти скрипт на главной странице.';
panel.append(button, resumeButton, status);
document.body.append(panel);

function inputDay(value) {
  if (!/^[0-9]{4}-[0-9]{2}-[0-9]{2}$/.test(value)) throw new Error('Дата нужна в формате ГГГГ-ММ-ДД');
  const [year, month, day] = value.split('-').map(Number);
  const result = new Date(year, month - 1, day);
  if (result.getFullYear() !== year || result.getMonth() !== month - 1 || result.getDate() !== day) {
    throw new Error('Такой даты нет');
  }
  return result;
}

function saveResult(data, from, to, partial) {
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  const period = from && to ? `${from}_${to}` : 'resumed';
  link.download = `yandex-bank-history-${period}${partial ? '-partial' : ''}.json`;
  link.textContent = partial ? 'Скачать неполный результат' : 'Скачать результат';
  link.style.cssText = 'display:block;margin-top:8px';
  panel.append(link);
  link.click();
}

function showProgress(progress) {
  if (progress.phase === 'retry') {
    status.textContent = `Временная ошибка. Повтор ${progress.attempt} через ${progress.delayMs / 1000} с…`;
  } else {
    status.textContent = progress.phase === 'list'
      ? `Страниц: ${progress.pages}. Операций: ${progress.records}.`
      : `Карточки: ${progress.read} из ${progress.total}.`;
  }
}

function readDisplayedCardBalance() {
  if (window.location.pathname !== '/my') throw new Error('Главная страница банка больше не открыта');
  return captureCardBalanceDom();
}

function describeBalance(result) {
  if (!result.balanceObservations) return '';
  const [before, after] = result.balanceObservations;
  if (before.error || after.error) return '\nОстаток прочитан не полностью. Причины сохранены в JSON.';
  return `\nОстаток карты: ${before.balance.amount} → ${after.balance.amount} ₽.`
    + '\nВремя чтения сохранено. Свежесть остатка и учёт блокировок пока не подтверждены.';
}

resumeButton.addEventListener('click', () => {
  const input = document.createElement('input');
  input.type = 'file';
  input.accept = '.json,application/json';
  input.addEventListener('change', async () => {
    const file = input.files && input.files[0];
    if (!file) return;
    try {
      button.disabled = true;
      resumeButton.disabled = true;
      status.textContent = 'Проверяю сохранённые страницы и карточки…';
      const snapshot = JSON.parse(await file.text());
      const result = await resumeHistory(snapshot, { onProgress: showProgress });
      result.displayTimezone = Intl.DateTimeFormat().resolvedOptions().timeZone;
      status.textContent = `Собрано ${result.records.length} операций и ${result.details.length} карточек.\nНужно сверить результат.`;
      saveResult(result, null, null, false);
    } catch (error) {
      status.textContent = `Продолжение остановлено: ${error instanceof Error ? error.message : 'ошибка чтения'}`;
      if (error && error.partial) saveResult(error.partial, null, null, true);
    } finally {
      button.disabled = false;
      resumeButton.disabled = false;
    }
  }, { once: true });
  input.click();
});

button.addEventListener('click', async () => {
  let from, to;
  try {
    const current = new URL(window.location.href);
    from = window.prompt('Начало периода, ГГГГ-ММ-ДД', current.searchParams.get('dateFrom') || '2026-07-04');
    if (from === null) return;
    to = window.prompt('Конец периода включительно, ГГГГ-ММ-ДД', current.searchParams.get('dateTo') || '2026-07-05');
    if (to === null) return;
    const start = inputDay(from), end = inputDay(to);
    end.setDate(end.getDate() + 1);
    button.disabled = true;
    resumeButton.disabled = true;
    status.textContent = 'Читаю список операций…';
    const collect = isBalancePage ? collectHistoryWithBalance : collectHistory;
    const result = await collect({
      fromInclusive: start.toISOString(), toExclusive: end.toISOString(), concurrency: 4,
      onProgress: showProgress,
      readBalance: readDisplayedCardBalance, balanceSourceUrl: 'https://bank.yandex.ru/my',
    });
    result.displayTimezone = Intl.DateTimeFormat().resolvedOptions().timeZone;
    status.textContent = `Собрано ${result.records.length} операций и ${result.details.length} карточек.\nНужно сверить с историей банка.`;
    status.textContent += describeBalance(result);
    saveResult(result, from, to, false);
  } catch (error) {
    status.textContent = `Сбор остановлен: ${error instanceof Error ? error.message : 'ошибка чтения'}`;
    if (error && error.partial) saveResult(error.partial, from, to, true);
  } finally {
    button.disabled = false;
    resumeButton.disabled = false;
  }
});
'''


def validate_period(period: dict) -> None:
    if set(period) != {'fromInclusive', 'toExclusive'}:
        raise ValueError('Нужны начало и конец периода')
    dates = [datetime.fromisoformat(period[key]) for key in ('fromInclusive', 'toExclusive')]
    if any(value.utcoffset() is None for value in dates) or dates[0] >= dates[1]:
        raise ValueError('Нужен возрастающий период с явным часовым поясом')


def headless_body(period: dict, recheck_ids: list[str]) -> str:
    validate_period(period)
    options = json.dumps({**period, 'recheckIds': recheck_ids}, ensure_ascii=False)
    return '''
if (window.location.origin !== 'https://bank.yandex.ru'
    || !['/my', '/my/transactions-history'].includes(window.location.pathname)) {
  throw new Error('Запуск нужен на главной странице или в истории банка');
}
try {
  const snapshot = await collectHistoryWithBalance({ ...PERIOD, concurrency: 4,
    readBalance: requestCardBalance, balanceSourceUrl: 'https://bank.yandex.ru/my' });
  return { ok: true, snapshot };
} catch (error) {
  return { ok: false, error: error instanceof Error ? error.message : 'Ошибка чтения',
    snapshot: error?.partial || null };
}
'''.replace('PERIOD', options)


def build(output: Path, *, period: dict | None = None,
          recheck_ids: list[str] | None = None) -> None:
    ids = [] if recheck_ids is None else recheck_ids
    if (not isinstance(ids, list)
            or any(not isinstance(value, str) or not value.strip() for value in ids)):
        raise ValueError('Для повторного чтения нужен массив непустых ID')
    if ids and period is None:
        raise ValueError('Повторное чтение ID требует периода и запуска без интерфейса')
    body = INTERFACE if period is None else headless_body(period, ids)
    reader = Path(__file__).with_name('yandex_bank_queries.js').read_text(encoding='utf-8')
    reader = reader.split("\nif (typeof module !== 'undefined')")[0]
    capture = Path(__file__).with_name('yandex_bank_capture.js').read_text(encoding='utf-8')
    capture = capture.split("\nif (typeof module !== 'undefined')")[0]
    header = HEADER if period is None else '// Read-only bank collection through the supported CDP channel.\n'
    text = header + '\n(async () => {\n"use strict";\n' + reader + '\n' + capture + '\n' + body + '\n})();\n'
    descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
        stream.write(text)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    parser.add_argument('--from-inclusive')
    parser.add_argument('--to-exclusive')
    args = parser.parse_args()
    if bool(args.from_inclusive) != bool(args.to_exclusive):
        parser.error('Нужны обе границы периода')
    period = None if args.from_inclusive is None else {
        'fromInclusive': args.from_inclusive, 'toExclusive': args.to_exclusive}
    build(args.output, period=period)
