"""Сборка переносимого скрипта не меняет глобальный модуль страницы."""

import tempfile
import unittest
import json
import shutil
import subprocess
from pathlib import Path

from build_userscript import build


class UserscriptBuildTest(unittest.TestCase):
    def test_headless_script_collects_history_and_balance_without_page_controls(self):
        node = shutil.which('node')
        if node is None:
            self.skipTest('Node.js нужен для проверки собранного скрипта')
        period = {'fromInclusive': '2026-10-18T00:00:00+03:00',
                  'toExclusive': '2026-10-20T00:00:00+03:00'}
        runner = r'''
const fs = require('node:fs'), vm = require('node:vm');
const calls = [];
class Parser {
  parseFromString(html) {
    if (html !== 'bank-html') throw new Error('Unexpected HTML');
    return { querySelectorAll: () => [{ textContent: 'Карта0 ₽' }, { textContent: 'Карта0 ₽' }] };
  }
}
const sandbox = { URL, DOMParser: Parser, Intl, window: {
  location: { origin: 'https://bank.yandex.ru', pathname: '/my' },
  fetch: async (url, options) => {
    calls.push({ url, method: options.method });
    if (url === 'https://bank.yandex.ru/my') return { ok: true, url,
      text: async () => 'bank-html', headers: { get: () => null } };
    const operation = JSON.parse(options.body).operationName;
    const data = operation === 'TransactionsFeedPfm'
      ? { getTransactionsFeedPfm: { items: [{ id: 'one', date: '2026-10-18T12:00:00Z' }],
          pendingItems: [], cursor: null, analytics: [] } }
      : { getTransactionOperationDetails: { id: 'native-one' } };
    return { ok: true, json: async () => ({ data }) };
  }
} };
vm.runInNewContext(fs.readFileSync(process.argv[1], 'utf8'), sandbox).then(result => {
  console.log(JSON.stringify({ result, calls }));
}).catch(error => { console.error(error); process.exitCode = 1; });
'''
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / 'headless.js'
            build(output, period=period)
            text = output.read_text()
            result = subprocess.run([node, '-e', runner, str(output)],
                                    capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertTrue(report['result']['ok'])
        snapshot = report['result']['snapshot']
        self.assertTrue(snapshot['complete'])
        self.assertEqual(len(snapshot['records']), 1)
        self.assertEqual(len(snapshot['details']), 1)
        self.assertEqual([r['balance']['amount'] for r in snapshot['balanceObservations']], ['0.00', '0.00'])
        self.assertEqual([r['method'] for r in report['calls']], ['GET', 'POST', 'POST', 'GET'])
        self.assertNotIn('document.createElement', text)
        self.assertNotIn('window.prompt', text)

    def test_headless_period_without_timezone_is_rejected_before_file_creation(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / 'headless.js'
            with self.assertRaises(ValueError):
                build(output, period={'fromInclusive': '2026-10-18T00:00:00',
                                      'toExclusive': '2026-10-20T00:00:00+03:00'})
            self.assertFalse(output.exists())

    def test_browser_script_does_not_write_node_module_exports(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / 'history.user.js'
            build(output)
            text = output.read_text()
        self.assertNotIn('module.exports', text)
        self.assertNotIn('document.cookie', text)
        self.assertIn('@match        https://bank.yandex.ru/my/transactions-history*', text)
        self.assertIn('@match        https://bank.yandex.ru/my', text)
        self.assertIn('collectHistoryWithBalance', text)
        self.assertIn('function captureCardBalanceDom', text)

    def test_existing_script_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / 'history.user.js'
            output.write_text('keep')
            with self.assertRaises(FileExistsError):
                build(output)
            self.assertEqual(output.read_text(), 'keep')
