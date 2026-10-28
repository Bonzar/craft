"""Скрипт для вкладки СберБанк Онлайн: только запросы истории, без интерфейса."""

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from build_sber_reader import build

PERIOD = {'fromInclusive': '2026-02-01T00:00:00+03:00', 'toExclusive': '2026-02-03T00:00:00+03:00'}
RUNNER = r'''
const fs = require('node:fs'), vm = require('node:vm');
const calls = [];
const sandbox = { JSON, AbortSignal,
  document: { createElement: () => { throw new Error('interface is not allowed'); } },
  window: { location: { origin: 'https://online.sberbank.ru' },
    fetch: async (url, options) => {
      calls.push({ url, method: options.method });
      return { ok: true, json: async () => ({ success: true, body: { operations: [
        { uohId: 'one', date: '01.02.2026T12:00:00' }] } }) };
    } } };
vm.runInNewContext(fs.readFileSync(process.argv[1], 'utf8'), sandbox).then(result => {
  console.log(JSON.stringify({ result, calls }));
}).catch(error => { console.error(error); process.exitCode = 1; });
'''


class SberReaderBuildTest(unittest.TestCase):
    def test_script_reads_history_with_requests_only(self):
        node = shutil.which('node')
        if node is None:
            self.skipTest('Node.js нужен для проверки собранного скрипта')
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / 'sber.js'
            build(output, period=PERIOD)
            result = subprocess.run([node, '-e', RUNNER, str(output)],
                                    capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertTrue(report['result']['ok'], report['result'])
        self.assertTrue(report['result']['snapshot']['complete'])
        self.assertEqual([row['uohId'] for row in report['result']['snapshot']['records']], ['one'])
        self.assertEqual({call['method'] for call in report['calls']}, {'POST'})

    def test_build_needs_a_period_with_timezone(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(ValueError):
                build(Path(folder) / 'a.js', period={'fromInclusive': '2026-02-01T00:00:00',
                                                      'toExclusive': '2026-02-03T00:00:00+03:00'})

    def test_existing_output_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / 'sber.js'
            output.write_text('keep')
            with self.assertRaises(FileExistsError):
                build(output, period=PERIOD)
            self.assertEqual(output.read_text(), 'keep')


if __name__ == '__main__':
    unittest.main()
