"""HTML-review client contract and module delivery to a fresh Codex profile."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import urllib.error

from . import CORE_SOURCE, MODULES_DIR
from .test_installer import installer

CLIENT = MODULES_DIR / 'html-review/lib/review.py'


def load_client():
    spec = importlib.util.spec_from_file_location('html_review_client', CLIENT)
    client = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(client)
    return client


class HtmlReviewTests(unittest.TestCase):
    def test_requires_explicit_chat_session(self):
        env = dict(os.environ)
        env.pop('REVIEW_SESSION', None)
        result = subprocess.run(
            [sys.executable, str(CLIENT), '--url', 'https://example.test', 'read'],
            capture_output=True, text=True, env=env,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Pass --session', result.stderr)

    def test_credential_stays_on_site_and_redirects_are_refused(self):
        client = load_client()
        captured = []

        class Opener:
            def open(self, req, timeout):
                captured.append(req)
                raise urllib.error.HTTPError(req.full_url, 302, 'Found', {}, None)

        with patch.object(client.urllib.request, 'build_opener', return_value=Opener()):
            with self.assertRaisesRegex(RuntimeError, 'Access denied'):
                client.request('https://example.test', 'test-only', 'GET', 'sessions/a')
        self.assertEqual(captured[0].get_header('Oai-sites-authorization'), 'Bearer test-only')
        self.assertIsNone(client.NoRedirect().redirect_request(
            captured[0], None, 302, 'Found', {}, 'https://elsewhere.test',
        ))

    def test_publish_scopes_html_and_returns_this_chat_link(self):
        client = load_client()
        with tempfile.TemporaryDirectory() as folder:
            html = Path(folder) / 'draft.html'
            html.write_text('<p>My draft</p>', encoding='utf-8')
            argv = ['review.py', '--url', 'https://example.test', '--session', 'chat-a',
                    'publish-html', str(html), '--id', 'v1', '--title', 'Draft']
            with patch.object(sys, 'argv', argv), patch.object(client, 'request', return_value={'id':'v1'}) as request, patch('builtins.print') as output:
                client.main()
            payload = request.call_args.args[4]
            self.assertEqual(payload['session'], 'chat-a')
            self.assertEqual(payload['html'], '<p>My draft</p>')
            self.assertTrue(payload['activate'])
            self.assertEqual(json.loads(output.call_args.args[0])['url'], 'https://example.test/?session=chat-a')

    def test_cursor_changes_when_an_answer_arrives(self):
        client = load_client()
        self.assertEqual(client.fingerprint({'a':1,'b':2}), client.fingerprint({'b':2,'a':1}))
        self.assertNotEqual(client.fingerprint({'threads':[]}), client.fingerprint({'threads':[{'body':'reply'}]}))

    def test_fresh_codex_install_has_callable_client_and_resolved_rules(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / 'modules'
            shutil.copytree(MODULES_DIR / 'html-review', source / 'html-review')
            settings = root / 'codex'
            installer.install(installer.parse_args([
                '--harness', 'codex', '--no-trust', '--settings-dir', str(settings),
                '--modules', str(source), '--core', str(CORE_SOURCE), '--state-dir', str(root / 'state'),
            ]))
            installed = settings / 'jarvis/modules/html-review/lib/review.py'
            rules = (settings / 'AGENTS.md').read_text(encoding='utf-8')
            self.assertIn(str(installed), rules)
            self.assertNotIn('{{JARVIS_MODULES}}', rules)
            result = subprocess.run([sys.executable, str(installed), '--help'], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('publish-html', result.stdout)


if __name__ == '__main__':
    unittest.main()
