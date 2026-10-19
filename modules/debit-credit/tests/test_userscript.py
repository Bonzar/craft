"""Сборка переносимого скрипта не меняет глобальный модуль страницы."""

import tempfile
import unittest
from pathlib import Path

from build_userscript import build


class UserscriptBuildTest(unittest.TestCase):
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
