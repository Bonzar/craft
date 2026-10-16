"""Версионируемая проба Cloud: протокол проверяется без запуска харнеса.

Синтетические события идут только во временный state. Эти тесты не являются
доказательством автоматического запуска хуков настоящим Cloud.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from . import REPO_ROOT


PROBE = REPO_ROOT / 'tests' / 'cloud_repo_probe.py'
JOURNAL = 'cloud-repo-probe.jsonl'


class CloudRepositoryProbeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = Path(self.tmp.name) / 'state'
        self.env = {**os.environ, 'JARVIS_STATE_DIR': str(self.state)}

    def invoke(self, event: dict, env=None):
        self.assertTrue(PROBE.is_file(), 'Нет обработчика версионируемой пробы Cloud')
        return subprocess.run(
            [sys.executable, str(PROBE)], input=json.dumps(event), text=True,
            capture_output=True, env=self.env if env is None else env, check=False,
        )

    def event(self, name='SessionStart', source='startup') -> dict:
        return {'hook_event_name': name, 'source': source,
                'session_id': 'cloud-session-test', 'cwd': '/workspace'}

    def entries(self) -> list[dict]:
        return [json.loads(line) for line in (self.state / JOURNAL).read_text().splitlines()]

    def test_startup_and_compact_have_distinct_observable_events(self) -> None:
        for source in ('startup', 'compact'):
            done = self.invoke(self.event(source=source))
            self.assertEqual(done.returncode, 0, done.stderr)
            self.assertIn('JARVIS_CLOUD_HOOK_V1', done.stdout)
            self.assertIn(f'source={source}', done.stdout)
            self.assertEqual(done.stderr, '')
        entries = self.entries()
        self.assertEqual([row['source'] for row in entries], ['startup', 'compact'])
        self.assertTrue(all(row['session_id'] == 'cloud-session-test' for row in entries))
        self.assertTrue(all(row['cwd'] == '/workspace' for row in entries))
        self.assertTrue(all(row['pid'] > 0 and row['at'] > 0 for row in entries))

    def test_permission_and_subagent_are_observed_without_changing_decisions(self) -> None:
        for name in ('PermissionRequest', 'SubagentStart'):
            done = self.invoke(self.event(name))
            self.assertEqual(done.returncode, 0, done.stderr)
            self.assertEqual(done.stdout, '')
        self.assertEqual([row['event'] for row in self.entries()],
                         ['PermissionRequest', 'SubagentStart'])

    def test_missing_state_variable_is_an_error_not_a_global_fallback(self) -> None:
        env = {key: value for key, value in self.env.items() if key != 'JARVIS_STATE_DIR'}
        done = self.invoke(self.event(), env)
        self.assertEqual(done.returncode, 1)
        self.assertIn('JARVIS_STATE_DIR', done.stderr)
        self.assertEqual(done.stdout, '')

    def test_unwritable_journal_has_a_failure_code_and_reason(self) -> None:
        self.state.write_text('not a directory')
        done = self.invoke(self.event())
        self.assertEqual(done.returncode, 1)
        self.assertIn('ОШИБКА', done.stderr)
        self.assertIn(str(self.state), done.stderr)
        self.assertEqual(done.stdout, '')

    def test_invalid_native_event_does_not_create_a_success_record(self) -> None:
        for event in ({}, self.event('not-supported'), {**self.event(), 'session_id': ''}):
            with self.subTest(event=event):
                done = self.invoke(event)
                self.assertEqual(done.returncode, 1)
                self.assertIn('ОШИБКА', done.stderr)
                self.assertEqual(done.stdout, '')
                self.assertFalse((self.state / JOURNAL).exists())

    def test_prompt_transcript_and_credentials_are_not_copied_to_the_journal(self) -> None:
        event = {**self.event(), 'prompt': 'private prompt', 'transcript_path': '/private/chat',
                 'tool_input': {'token': 'private-token'}}
        done = self.invoke(event)
        self.assertEqual(done.returncode, 0, done.stderr)
        text = (self.state / JOURNAL).read_text()
        for value in ('private prompt', '/private/chat', 'private-token'):
            self.assertNotIn(value, text)

