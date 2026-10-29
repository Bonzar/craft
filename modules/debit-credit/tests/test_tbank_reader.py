"""Скрипт для вкладки Т-Банка: только запросы ленты, без интерфейса."""

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from build_tbank_reader import build

PERIOD = {'fromInclusive': '2026-02-01T00:00:00+03:00', 'toExclusive': '2026-02-03T00:00:00+03:00'}
ACCOUNTS = ['1000000001', '1000000002']
RUNNER = r'''
const fs = require('node:fs'), vm = require('node:vm');
const calls = [];
const sandbox = { URL, URLSearchParams, AbortSignal, JSON,
  performance: { getEntriesByType: () => [{ name:
    'https://www.tbank.ru/mybank/api/web/bundles/v1/allBundles?sessionid=SECRET&appName=supreme' }] },
  document: { createElement: () => { throw new Error('interface is not allowed'); } },
  window: { location: { origin: 'https://www.tbank.ru' },
    fetch: async (url, options) => {
      calls.push({ url, method: options.method });
      const payload = url.includes('accounts_light_ib')
        ? [{ id: '1000000001', name: 'Основной', accountType: 'Current', currency: { name: 'RUB' },
          moneyAmount: { value: 5 }, creditLimit: { value: 0 } }]
        : [{ id: 'one', account: '1000000001',
          operationTime: { milliseconds: Date.parse('2026-02-01T12:00:00Z') } }];
      return { ok: true, json: async () => ({ resultCode: 'OK', payload }) };
    } } };
vm.runInNewContext(fs.readFileSync(process.argv[1], 'utf8'), sandbox).then(result => {
  console.log(JSON.stringify({ result, calls }));
}).catch(error => { console.error(error); process.exitCode = 1; });
'''


class TbankReaderBuildTest(unittest.TestCase):
    def run_script(self, output: Path) -> dict:
        node = shutil.which('node')
        if node is None:
            self.skipTest('Node.js нужен для проверки собранного скрипта')
        result = subprocess.run([node, '-e', RUNNER, str(output)],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_script_reads_own_accounts_with_requests_only(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / 'tbank.js'
            build(output, period=PERIOD, accounts=ACCOUNTS)
            report = self.run_script(output)
        self.assertTrue(report['result']['ok'])
        snapshot = report['result']['snapshot']
        self.assertTrue(snapshot['complete'])
        self.assertEqual(snapshot['accounts'], ACCOUNTS)
        self.assertEqual([row['id'] for row in snapshot['records']], ['one'])
        self.assertTrue(all(call['method'] == 'GET' for call in report['calls']))
        self.assertNotIn('SECRET', json.dumps(report['result']))
        self.assertEqual(snapshot['balances']['after']['accounts'][0]['amount'], '5.00')
        self.assertIn('accounts_light_ib', report['calls'][0]['url'])
        self.assertIn('accounts_light_ib', report['calls'][-1]['url'])

    def test_build_needs_period_and_own_accounts(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(ValueError):
                build(Path(folder) / 'a.js', period=PERIOD, accounts=[])
            with self.assertRaises(ValueError):
                build(Path(folder) / 'b.js', period={'fromInclusive': PERIOD['fromInclusive']},
                      accounts=ACCOUNTS)

    def test_existing_output_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / 'tbank.js'
            output.write_text('keep')
            with self.assertRaises(FileExistsError):
                build(output, period=PERIOD, accounts=ACCOUNTS)
            self.assertEqual(output.read_text(), 'keep')


if __name__ == '__main__':
    unittest.main()
