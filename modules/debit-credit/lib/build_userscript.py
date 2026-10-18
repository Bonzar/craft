"""Собрать один читающий userscript без cookies, ключей и личных данных."""

import os
import sys
from pathlib import Path

HEADER = '''// ==UserScript==
// @name         Debit & Credit — сбор истории Яндекс Банка
// @version      0.2.0
// @match        https://bank.yandex.ru/my/transactions-history*
// @run-at       document-idle
// @grant        none
// ==/UserScript==
'''

INTERFACE = r'''
if (window.location.origin !== 'https://bank.yandex.ru'
    || !window.location.pathname.startsWith('/my/transactions-history')) {
  throw new Error('Скрипт работает только в истории банка');
}
if (document.getElementById('debit-credit-history-reader')) return;
const panel = document.createElement('div');
panel.id = 'debit-credit-history-reader';
panel.style.cssText = 'position:fixed;right:16px;bottom:16px;z-index:2147483647;background:white;color:black;padding:14px;border:1px solid #888;border-radius:12px;font:14px system-ui;max-width:340px;box-shadow:0 3px 16px #0003';
const button = document.createElement('button');
button.textContent = 'Собрать историю в JSON';
const resumeButton = document.createElement('button');
resumeButton.textContent = 'Продолжить из JSON';
resumeButton.style.cssText = 'display:block;margin-top:8px';
const status = document.createElement('div');
status.style.cssText = 'margin-top:8px;white-space:pre-wrap';
status.textContent = 'Только чтение истории. Платежи не выполняются.';
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
    const result = await collectHistory({
      fromInclusive: start.toISOString(), toExclusive: end.toISOString(), concurrency: 4,
      onProgress: showProgress,
    });
    result.displayTimezone = Intl.DateTimeFormat().resolvedOptions().timeZone;
    status.textContent = `Собрано ${result.records.length} операций и ${result.details.length} карточек.\nНужно сверить с историей банка.`;
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


def build(output: Path) -> None:
    reader = Path(__file__).with_name('yandex_bank_queries.js').read_text(encoding='utf-8')
    reader = reader.split("\nif (typeof module !== 'undefined')")[0]
    text = HEADER + '\n(async () => {\n"use strict";\n' + reader + '\n' + INTERFACE + '\n})();\n'
    descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
        stream.write(text)


if __name__ == '__main__':
    if len(sys.argv) != 2:
        raise SystemExit('usage: python3 build_userscript.py output.user.js')
    build(Path(sys.argv[1]))
