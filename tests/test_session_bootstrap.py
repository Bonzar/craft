"""Установка и старт в активном профиле, без записи в домашний каталог."""

import json
import os
import shlex
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest.mock import patch

from . import CORE_SOURCE, INSTALLER
from .test_installer import installer


HOOK = '''import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / '_core'))
import jarvis
from jarvis import wrappers
class Module(jarvis.Module):
    def handle(self, event, runtime):
        runtime.storage.write_json('ready.json', {'event': event.event})
        return jarvis.Context('STARTUP_READY')
if __name__ == '__main__':
    sys.exit(wrappers.run_hook(__file__, Module))
'''


class SessionBootstrapTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / 'source'
        module = self.source / 'startup'
        (module / 'hooks').mkdir(parents=True)
        (module / 'module.toml').write_text(
            'slug = "startup"\nevents = ["session-start", "after-compact"]\n'
        )
        (module / 'hooks' / 'module.py').write_text(HOOK)
        self.profile = self.root / 'active-profile'
        self.state = self.root / 'state'

    def install(self, harness='codex', extra=()):
        args = installer.parse_args([
            '--harness', harness, '--modules', str(self.source),
            '--core', str(CORE_SOURCE), '--no-trust', *extra,
        ])
        with patch.dict(os.environ, {'CODEX_HOME': str(self.profile),
                                     'JARVIS_STATE_DIR': str(self.state),
                                     'HOME': str(self.root / 'installer-home')}):
            return installer.install(args)

    def test_codex_installs_in_the_inherited_active_profile(self):
        self.install()
        self.assertTrue((self.profile / 'config.toml').is_file())
        ledger = json.loads((self.profile / 'jarvis' / 'installed.json').read_text())
        self.assertEqual(ledger['source']['state_dir'], str(self.state))

    def test_an_explicit_profile_takes_precedence_over_codex_home(self):
        explicit = self.root / 'explicit'
        self.install(extra=('--settings-dir', str(explicit)))
        self.assertTrue((explicit / 'config.toml').is_file())
        self.assertFalse(self.profile.exists())

    def test_installed_hooks_use_the_selected_state_without_runtime_env(self):
        for harness in ('codex', 'claude'):
            with self.subTest(harness=harness):
                profile = self.root / harness
                state = self.root / (harness + '-state')
                self.install(harness, ('--settings-dir', str(profile),
                                       '--state-dir', str(state)))
                if harness == 'codex':
                    config = tomllib.loads((profile / 'config.toml').read_text())
                    hook = config['hooks']['SessionStart'][0]['hooks'][0]
                    command = shlex.split(hook['command'])
                else:
                    config = json.loads((profile / 'settings.json').read_text())
                    hook = config['hooks']['SessionStart'][0]['hooks'][0]
                    command = [hook['command'], *hook['args']]
                environment = {key: value for key, value in os.environ.items()
                               if key != 'JARVIS_STATE_DIR'}
                environment['HOME'] = str(self.root / 'unused-home')
                for source, expected in [('startup', 'session-start'),
                                         ('compact', 'after-compact')]:
                    event = {'hook_event_name': 'SessionStart', 'source': source,
                             'session_id': 'clean-session', 'cwd': str(self.root)}
                    done = subprocess.run(command, input=json.dumps(event), text=True,
                                          capture_output=True, env=environment)
                    self.assertEqual(done.returncode, 0, done.stderr)
                    self.assertIn('STARTUP_READY', done.stdout)
                    ready = json.loads((state / 'clean-session' / 'ready.json').read_text())
                    self.assertEqual(ready['event'], expected)
                self.assertFalse((self.root / 'unused-home').exists())

    def test_app_server_diagnostics_cannot_block_rpc_answers(self):
        cli = self.root / 'noisy-codex'
        cli.write_text(
            f'#!{sys.executable}\n'
            'import json, os, sys\n'
            "os.write(2, b'diagnostic\\n' * 30000)\n"
            'for line in sys.stdin:\n'
            '    request = json.loads(line)\n'
            "    if 'id' in request:\n"
            "        print(json.dumps({'id': request['id'], 'result': {}}), flush=True)\n"
        )
        cli.chmod(0o755)
        answers = installer.app_server_call(str(cli), self.root,
                                            [{'id': 1, 'method': 'initialize'}], timeout=2)
        self.assertIn('result', answers.get(1, {}))


if __name__ == '__main__':
    unittest.main()
