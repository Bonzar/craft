"""Dist собирается из Git-коммита, отдельно от исходной рабочей копии."""

import hashlib
import json
import os
import shlex
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

from . import REPO_ROOT


BUILDER = REPO_ROOT / 'tools' / 'build-cloud-probe'
SKILL = '.agents/skills/probe/SKILL.md'
CALLBACK = '.codex/jarvis/cloud-repo-probe.py'
MANIFEST = 'cloud-dist.json'


class CloudProbeDistTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / 'source'
        self.output = self.root / 'dist'
        self.source.mkdir()
        files = {
            'AGENTS.md': b'# Project instructions\n',
            'modules/probe/skill/SKILL.md':
                (REPO_ROOT / 'modules/probe/skill/SKILL.md').read_bytes(),
            'tests/cloud_repo_probe.py': (REPO_ROOT / 'tests/cloud_repo_probe.py').read_bytes(),
        }
        for name, content in files.items():
            path = self.source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        self.git('init', '-q')
        self.git('add', '.')
        self.git('-c', 'user.name=Jarvis tests', '-c', 'user.email=tests@example.invalid',
                 'commit', '-qm', 'test source')
        self.commit = self.git('rev-parse', 'HEAD').strip()

    def git(self, *args) -> str:
        return subprocess.check_output(['git', *args], cwd=self.source, text=True)

    def build(self, output=None, runtime_root='/workspace/craft'):
        self.assertTrue(BUILDER.is_file(), 'Нет сборщика отдельного dist')
        return subprocess.run([
            sys.executable, str(BUILDER), '--source', str(self.source),
            '--ref', self.commit, '--output', str(output or self.output),
            '--runtime-root', runtime_root,
        ], capture_output=True, text=True, check=False)

    def check(self):
        return subprocess.run([sys.executable, str(BUILDER), '--check', str(self.output)],
                              capture_output=True, text=True, check=False)

    def tree(self, root) -> dict:
        return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file()}

    def test_dist_has_an_explicit_toml_hook_to_compare_with_json_discovery(self) -> None:
        done = self.build()
        self.assertEqual(done.returncode, 0, done.stderr)
        config_path = self.output / '.codex/config.toml'
        self.assertTrue(config_path.is_file(), 'Нет явного project config для пробы Cloud')
        config = tomllib.loads(config_path.read_text())
        self.assertIs(config['features']['hooks'], True)
        self.assertEqual(set(config), {'features', 'hooks'})
        group = config['hooks']['SessionStart'][0]
        self.assertEqual(group['matcher'], 'startup|resume|clear|compact|fork')
        handler = group['hooks'][0]
        self.assertEqual(handler['type'], 'command')
        self.assertEqual(shlex.split(handler['command']),
                         ['python3', '/workspace/craft/' + CALLBACK,
                          '--registration', 'inline-toml'])
        self.assertNotIn('state', config['hooks'])
        manifest = json.loads((self.output / MANIFEST).read_text())
        self.assertIn('.codex/config.toml', manifest['files'])

    def test_dist_has_native_paths_and_all_event_kinds(self) -> None:
        done = self.build()
        self.assertEqual(done.returncode, 0, done.stderr)
        config = json.loads((self.output / '.codex/hooks.json').read_text())
        self.assertEqual(set(config['hooks']),
                         {'SessionStart', 'PermissionRequest', 'SubagentStart'})
        for name, groups in config['hooks'].items():
            handler = groups[0]['hooks'][0]
            self.assertEqual(shlex.split(handler['command']),
                             ['python3', '/workspace/craft/' + CALLBACK])
            if name == 'SessionStart':
                self.assertEqual(groups[0]['matcher'], 'startup|resume|clear|compact|fork')
                self.assertEqual(handler['additionalContextLimit'], 0)
        self.assertEqual((self.output / SKILL).read_bytes(),
                         (self.source / 'modules/probe/skill/SKILL.md').read_bytes())
        self.assertIn('JARVIS_CLOUD_RULES_V1', (self.output / 'AGENTS.md').read_text())
        self.assertNotIn('trusted_hash', (self.output / '.codex/hooks.json').read_text())

    def test_dist_records_source_commit_and_checksums_and_is_reproducible(self) -> None:
        self.assertEqual(self.build().returncode, 0)
        second = self.root / 'second'
        self.assertEqual(self.build(second).returncode, 0)
        self.assertEqual(self.tree(self.output), self.tree(second))
        manifest = json.loads((self.output / MANIFEST).read_text())
        self.assertEqual(manifest['source_commit'], self.commit)
        self.assertEqual(manifest['runtime_root'], '/workspace/craft')
        for path, digest in manifest['files'].items():
            self.assertEqual(digest, hashlib.sha256((self.output / path).read_bytes()).hexdigest())
        self.assertEqual(self.check().returncode, 0)

    def test_build_uses_the_commit_and_does_not_copy_working_state(self) -> None:
        (self.source / 'AGENTS.md').write_text('UNCOMMITTED')
        (self.source / '.env').write_text('FAKE_SECRET=DO-NOT-SHIP')
        state = self.source / '.codex/state'
        state.mkdir(parents=True)
        (state / 'trace.jsonl').write_text('PRIVATE STATE')
        self.assertEqual(self.build().returncode, 0)
        files = self.tree(self.output)
        self.assertEqual(set(files), {'AGENTS.md', SKILL, CALLBACK,
                                    '.codex/hooks.json', '.codex/config.toml', MANIFEST})
        self.assertIn(b'Project instructions', files['AGENTS.md'])
        for value in files.values():
            for private in (b'UNCOMMITTED', b'DO-NOT-SHIP', b'PRIVATE STATE'):
                self.assertNotIn(private, value)
        self.assertFalse((self.source / '.agents').exists())
        self.assertFalse((self.source / '.codex/hooks.json').exists())

    def test_build_refuses_to_overwrite_an_existing_output(self) -> None:
        self.output.mkdir()
        sentinel = self.output / 'keep.txt'
        sentinel.write_text('keep me')
        done = self.build()
        self.assertEqual(done.returncode, 1)
        self.assertIn('ОШИБКА', done.stderr)
        self.assertEqual(sentinel.read_text(), 'keep me')

    def test_checker_rejects_tampered_artifacts(self) -> None:
        self.assertEqual(self.build().returncode, 0)
        (self.output / CALLBACK).write_text('tampered')
        done = self.check()
        self.assertEqual(done.returncode, 1)
        self.assertIn(CALLBACK, done.stderr)

    def test_built_hook_command_is_runnable_with_an_explicit_runtime_root(self) -> None:
        done = self.build(runtime_root=str(self.output))
        self.assertEqual(done.returncode, 0, done.stderr)
        config = json.loads((self.output / '.codex/hooks.json').read_text())
        handler = config['hooks']['SessionStart'][0]['hooks'][0]
        state = self.root / 'isolated-test-state'
        run = subprocess.run(
            shlex.split(handler['command']),
            input=json.dumps({'hook_event_name': 'SessionStart', 'source': 'compact',
                              'session_id': 'dist-test', 'cwd': '/workspace'}),
            env={**os.environ, 'JARVIS_STATE_DIR': str(state)},
            text=True, capture_output=True, check=False,
        )
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn('source=compact', run.stdout)
        self.assertTrue((state / 'cloud-repo-probe.jsonl').is_file())

    def test_inline_toml_hook_records_its_registration_in_isolated_state(self) -> None:
        self.assertEqual(self.build(runtime_root=str(self.output)).returncode, 0)
        config_path = self.output / '.codex/config.toml'
        self.assertTrue(config_path.is_file(), 'Нет inline TOML пробы')
        config = tomllib.loads(config_path.read_text())
        handler = config['hooks']['SessionStart'][0]['hooks'][0]
        state = self.root / 'toml-isolated-test-state'
        run = subprocess.run(shlex.split(handler['command']),
            input=json.dumps({'hook_event_name': 'SessionStart', 'source': 'startup',
                              'session_id': 'synthetic-toml-test', 'cwd': '/workspace'}),
            env={**os.environ, 'JARVIS_STATE_DIR': str(state)},
            text=True, capture_output=True, check=False)
        self.assertEqual(run.returncode, 0, run.stderr)
        entries = [json.loads(line) for line in (state / 'cloud-repo-probe.jsonl').read_text().splitlines()]
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]['registration'], 'inline-toml')
        self.assertIn('registration=inline-toml', run.stdout)
