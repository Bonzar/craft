"""Модуль финансовых адаптеров доступен через реестр и выполняется отдельно."""

import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

from jarvis.manifest import load
from jarvis.registry import library_of

MODULE = Path(__file__).resolve().parents[1] / 'modules' / 'debit-credit'


class DebitCreditModuleTest(unittest.TestCase):
    def test_readers_are_available_through_module_registry(self):
        manifest = load(MODULE)
        self.assertEqual(manifest.slug, 'debit-credit')
        self.assertEqual(manifest.events, ())
        library = library_of(manifest).load()
        self.assertTrue(callable(library.read_csv))
        self.assertTrue(callable(library.read_backup))
        self.assertTrue(callable(library.read_cards))

    def test_isolated_adapter_suite_passes_without_external_dependencies(self):
        result = subprocess.run(
            [sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-v'],
            cwd=MODULE, env={**os.environ, 'PYTHONPATH': str(MODULE / 'lib')},
            capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_browser_dom_reader_suite_passes_when_node_is_available(self):
        node = shutil.which('node')
        if node is None:
            self.skipTest('Node.js нужен только для теста DOM-считывателя браузера')
        result = subprocess.run(
            [node, '--test', str(MODULE / 'tests' / 'test_capture.js'),
             str(MODULE / 'tests' / 'test_queries.js')],
            capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
