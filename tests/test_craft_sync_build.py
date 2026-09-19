"""`craft-sync` собирается своим модулем, а не setup-скриптом окружения."""

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

from . import CORE_SOURCE, INSTALLER, MODULES_DIR

SLUG = 'craft-sync'


def session_event(session_id: str) -> str:
    return json.dumps({'hook_event_name': 'SessionStart', 'session_id': session_id,
                       'cwd': '/work', 'source': 'startup'})


def load_library():
    """Библиотека в исходном модуле: именно её зовёт craft-snapshot."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        'craft_sync_library_for_test', MODULES_DIR / SLUG / 'lib' / '__init__.py'
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Storage:
    """Минимальная зона сессии для проверки публикации build-маркера."""

    def __init__(self) -> None:
        self.marker = None

    def read_json(self, name, default=None):
        return self.marker if name == 'craft-sync-build.json' else default


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

    def test_consumer_waits_for_a_marker_published_after_it_starts(self) -> None:
        # Это порядок из P1 review: start-context уже спросил craft-sync, а
        # build-hook ещё не успел записать BUILDING. Первый wait публикует
        # маркер и бинарник как завершившийся builder; без grace библиотека
        # упала бы до этого wait.
        library = load_library()
        storage = Storage()
        binary = self.root / 'craft-sync'

        def publish(_seconds):
            storage.marker = {'state': library.BUILDING, 'pid': 1}
            binary.write_text('binary', encoding='utf-8')

        with unittest.mock.patch.object(library, 'BINARY', binary), \
                unittest.mock.patch.object(library, 'still_running', return_value=True), \
                unittest.mock.patch.object(library.time, 'sleep', side_effect=publish) as slept:
            self.assertEqual(library.binary(storage), str(binary))
        slept.assert_called_once_with(library.WAIT_STEP)

    def test_absent_builder_waits_only_for_the_bounded_marker_grace(self) -> None:
        library = load_library()
        storage = Storage()
        missing = self.root / 'missing'
        # deadline=0.1; first observation is 0, second is already past it.
        with unittest.mock.patch.object(library, 'BINARY', missing), \
                unittest.mock.patch.object(library, 'MARKER_GRACE_SECONDS', 0.1), \
                unittest.mock.patch.object(library.time, 'monotonic', side_effect=(0, 0, 1)), \
                unittest.mock.patch.object(library.time, 'sleep') as slept:
            with self.assertRaises(library.CraftSyncError):
                library.binary(storage)
        slept.assert_called_once_with(library.WAIT_STEP)


if __name__ == '__main__':
    unittest.main()
