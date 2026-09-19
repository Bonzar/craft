"""`craft-sync` собирается своим модулем, а не setup-скриптом окружения."""

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from . import CORE_SOURCE, INSTALLER, MODULES_DIR

SLUG = 'craft-sync'


def session_event(session_id: str) -> str:
    return json.dumps({'hook_event_name': 'SessionStart', 'session_id': session_id,
                       'cwd': '/work', 'source': 'startup'})


class CraftSyncBuildTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.home = self.root / 'home'
        self.home.mkdir()
        self.settings = self.root / 'settings'
        self.stubs = self.root / 'stubs'
        self.stubs.mkdir()
        self.calls = self.root / 'go-calls.log'
        source_root = self.root / 'source'
        source_root.mkdir()
        shutil.copytree(MODULES_DIR / SLUG, source_root / SLUG,
                        ignore=shutil.ignore_patterns('src', 'bin', '__pycache__'))
        source = source_root / SLUG / 'src'
        source.mkdir()
        (source / 'main.go').write_text('package main\n', encoding='utf-8')
        (source / 'go.mod').write_text('module craft-sync\n', encoding='utf-8')
        subprocess.run([sys.executable, str(INSTALLER), '--settings-dir', str(self.settings),
                        '--modules', str(source_root), '--core', str(CORE_SOURCE)],
                       check=True, capture_output=True)
        self.module = self.settings / 'jarvis' / 'modules' / SLUG
        self.binary = self.module / 'bin' / SLUG

    def stub_go(self) -> None:
        tool = self.stubs / 'go'
        tool.write_text(
            '#!/bin/sh\n'
            f'echo "$@" >> {self.calls}\n'
            'while [ $# -gt 0 ]; do\n'
            '  if [ "$1" = "-o" ]; then shift; printf binary > "$1"; chmod +x "$1"; fi\n'
            '  shift\n'
            'done\n', encoding='utf-8')
        tool.chmod(0o755)

    def hook(self) -> dict:
        settings = json.loads((self.settings / 'settings.json').read_text(encoding='utf-8'))
        hooks = [hook for group in settings['hooks']['SessionStart'] for hook in group['hooks']
                 if SLUG in ' '.join(hook.get('args', []))]
        self.assertEqual(len(hooks), 1)
        return hooks[0]

    def fire(self, session_id: str) -> subprocess.CompletedProcess:
        hook = self.hook()
        done = subprocess.run([hook['command'], *hook['args']], input=session_event(session_id),
                              text=True, capture_output=True,
                              env={'PATH': f'{self.stubs}:/usr/bin:/bin', 'HOME': str(self.home)})
        self.assertEqual(done.returncode, 0, done.stderr)
        return done

    def test_installer_copies_source_but_not_a_prebuilt_binary(self) -> None:
        self.assertTrue((self.module / 'src' / 'main.go').is_file())
        self.assertTrue((self.module / 'lib' / '__init__.py').is_file())
        self.assertTrue((self.module / 'hooks' / 'module.py').is_file())
        self.assertFalse((self.module / 'bin').exists())

    def test_session_start_builds_once_then_uses_the_fingerprint(self) -> None:
        self.stub_go()
        first = self.fire('first')
        self.fire('second')
        self.assertEqual(first.stdout.strip(), '')
        self.assertTrue(self.binary.is_file())
        self.assertEqual(len(self.calls.read_text(encoding='utf-8').splitlines()), 1)

    def test_snapshot_declares_craft_sync_as_an_explicit_requirement(self) -> None:
        text = (MODULES_DIR / 'craft-snapshot' / 'module.toml').read_text(encoding='utf-8')
        self.assertIn('requires = ["craft-sync"]', text)


if __name__ == '__main__':
    unittest.main()
