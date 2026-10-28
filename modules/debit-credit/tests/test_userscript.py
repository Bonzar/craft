"""Сборка переносимого скрипта не меняет глобальный модуль страницы."""

import tempfile
import unittest
import json
import shutil
import subprocess
from pathlib import Path

from build_userscript import build

PERIOD = {'fromInclusive': '2026-10-18T00:00:00+03:00', 'toExclusive': '2026-10-20T00:00:00+03:00'}


class UserscriptBuildTest(unittest.TestCase):
    def test_headless_script_collects_new_history_and_old_ids_without_page_controls(self):
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
    const { operationName: operation, variables } = JSON.parse(options.body);
    const data = operation === 'TransactionsFeedPfm'
      ? { getTransactionsFeedPfm: { items: [{ id: 'one', date: '2026-10-18T12:00:00Z' }],
          pendingItems: [], cursor: null, analytics: [] } }
      : { getTransactionOperationDetails: { id: `native-${variables.id}` } };
    return { ok: true, json: async () => ({ data }) };
  }
} };
vm.runInNewContext(fs.readFileSync(process.argv[1], 'utf8'), sandbox).then(result => {
  console.log(JSON.stringify({ result, calls }));
}).catch(error => { console.error(error); process.exitCode = 1; });
'''
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / 'headless.js'
            build(output, period=period, recheck_ids=['old'])
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
        self.assertTrue(snapshot['rechecks']['complete'])
        self.assertEqual(snapshot['rechecks']['details'][0]['requestedId'], 'old')
        self.assertEqual(snapshot['rechecks']['details'][0]['id'], 'native-old')
        self.assertEqual([r['balance']['amount'] for r in snapshot['balanceObservations']], ['0.00', '0.00'])
        self.assertEqual([r['method'] for r in report['calls']], ['GET', 'POST', 'POST', 'POST', 'GET'])
        self.assertNotIn('document.createElement', text)
        self.assertNotIn('window.prompt', text)

    def test_invalid_recheck_ids_are_rejected_before_script_creation(self):
        period = {'fromInclusive': '2026-10-18T00:00:00+03:00',
                  'toExclusive': '2026-10-20T00:00:00+03:00'}
        for ids in ('old', ['valid', None], ['']):
            with self.subTest(ids=ids), tempfile.TemporaryDirectory() as folder:
                output = Path(folder) / 'headless.js'
                with self.assertRaises(ValueError):
                    build(output, period=period, recheck_ids=ids)
                self.assertFalse(output.exists())

    def test_headless_period_without_timezone_is_rejected_before_file_creation(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / 'headless.js'
            with self.assertRaises(ValueError):
                build(output, period={'fromInclusive': '2026-10-18T00:00:00',
                                      'toExclusive': '2026-10-20T00:00:00+03:00'})
            self.assertFalse(output.exists())

    def test_browser_script_has_requests_only_and_no_node_exports(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / 'history.js'
            build(output, period=PERIOD)
            text = output.read_text()
        self.assertNotIn('module.exports', text)
        self.assertNotIn('document.cookie', text)
        self.assertNotIn('==UserScript==', text)
        self.assertNotIn('captureCardBalanceDom', text)
        self.assertIn('collectHistoryWithBalance', text)
        self.assertIn('requestCardBalance', text)

    def test_script_without_period_is_not_built(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / 'history.js'
            with self.assertRaises(ValueError):
                build(output)
            self.assertFalse(output.exists())

    def test_resume_script_reads_only_unread_cards_from_saved_snapshot(self):
        node = shutil.which('node')
        if node is None:
            self.skipTest('Node.js нужен для проверки собранного скрипта')
        rows = [{'id': 'one', 'date': '2026-10-18T12:00:00Z'}, {'id': 'two', 'date': '2026-10-18T13:00:00Z'}]
        saved = {'schema_version': 1, 'adapter': 'yandex-bank-graphql', 'complete': False, 'period': PERIOD,
                 'pages': [{'items': rows, 'pendingItems': [], 'cursor': None, 'analytics': []}],
                 'records': rows, 'observations': rows, 'repeatedObservations': 0, 'excluded': [],
                 'conflicts': [], 'cursorExhausted': True, 'detailsRequested': True,
                 'details': [{'id': 'native-one', 'requestedId': 'one'}],
                 'detailObservations': [{'requestedId': 'one',
                                         'response': {'getTransactionOperationDetails': {'id': 'native-one'}}}],
                 'detailErrors': [{'id': 'two', 'message': 'HTTP 500'}], 'unreadIds': ['two']}
        runner = r'''
const fs = require('node:fs'), vm = require('node:vm');
const calls = [];
const sandbox = { URL, Intl, window: { location: { origin: 'https://bank.yandex.ru',
    pathname: '/my/transactions-history' },
  fetch: async (url, options) => {
    const { operationName, variables } = JSON.parse(options.body);
    calls.push(operationName);
    return { ok: true, json: async () => ({ data: { getTransactionOperationDetails: { id: `native-${variables.id}` } } }) };
  } } };
vm.runInNewContext(fs.readFileSync(process.argv[1], 'utf8'), sandbox).then(result => {
  console.log(JSON.stringify({ result, calls }));
}).catch(error => { console.error(error); process.exitCode = 1; });
'''
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / 'resume.js'
            build(output, resume=saved)
            result = subprocess.run([node, '-e', runner, str(output)],
                                    capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertTrue(report['result']['ok'], report['result'])
        self.assertEqual(report['calls'], ['TransactionOperationDetails'])
        self.assertTrue(report['result']['snapshot']['complete'])
        self.assertEqual(len(report['result']['snapshot']['details']), 2)

    def test_period_and_resume_are_not_combined(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(ValueError):
                build(Path(folder) / 'x.js', period=PERIOD, resume={'adapter': 'yandex-bank-graphql'})

    def test_existing_script_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / 'history.js'
            output.write_text('keep')
            with self.assertRaises(FileExistsError):
                build(output, period=PERIOD)
            self.assertEqual(output.read_text(), 'keep')
