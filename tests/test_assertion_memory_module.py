"""Optional direct MCP connection and startup policy, using synthetic profiles only."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import tomllib
import unittest
from unittest.mock import patch

from . import CORE_SOURCE, MODULES_DIR
from .test_installer import installer


class MemoryModuleTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / 'modules'
        self.source.mkdir()
        for name in ('assertion-memory',):
            shutil.copytree(MODULES_DIR / name, self.source / name)
        self.profile = self.root / 'profile'
        self.config = self.root / 'connection.json'
        self.env = patch.dict(os.environ, {'JARVIS_ASSERTION_MEMORY_CONFIG': str(self.config),'MEMORY_TOKEN':'synthetic-fixture-token'})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.configure()

    def configure(self, **extra):
        value={'url':'https://memory.example.test/mcp','bearer_token_env_var':'MEMORY_TOKEN','capture':{'enabled':False},**extra}
        descriptor=self.source/'assertion-memory/mcp/connection.json'
        descriptor.write_text(json.dumps({'server_name':'assertion_memory','url_env':'SYNTHETIC_MEMORY_TEST_URL','connection':value}))

    def configure_legacy_file(self,value):
        self.config.write_text(json.dumps(value))
        (self.source/'assertion-memory/mcp/connection.json').write_text(json.dumps({'server_name':'assertion_memory',
            'config_file':str(self.config),'config_file_env':'JARVIS_ASSERTION_MEMORY_CONFIG'}))

    def install(self, harness='codex', extra=()):
        return installer.install(installer.parse_args(['--harness', harness, '--no-trust',
            '--settings-dir', str(self.profile), '--state-dir', str(self.root / 'state'),
            '--modules', str(self.source), '--core', str(CORE_SOURCE), *extra]))

    def test_extra_foreign_table_inside_owned_region_is_rejected_before_writes(self):
        self.install();path=self.profile/'config.toml'
        edited=path.read_text().replace('# jarvis:mcp:end','[features]\nforeign_setting=true\n# jarvis:mcp:end')
        path.write_text(edited)
        with self.assertRaises(ValueError):self.install()
        self.assertEqual(path.read_text(),edited)

    def test_inline_foreign_parent_cannot_publish_invalid_toml(self):
        self.profile.mkdir();path=self.profile/'config.toml'
        original='mcp_servers = {foreign = {command = "foreign-tool"}}\n'
        path.write_text(original)
        with self.assertRaises(ValueError):self.install()
        self.assertEqual(path.read_text(),original)
        self.assertFalse((self.profile/'AGENTS.md').exists())

    def test_explicitly_disabled_connection_keeps_startup_silent(self):
        self.configure(enabled=False)
        self.install()
        config = tomllib.loads((self.profile / 'config.toml').read_text())
        self.assertNotIn('mcp_servers', config)
        self.assertEqual(self.fire()['stdout'], '')
        self.assertFalse((self.root / 'state' / 'physical-1' / 'assertion-memory-session.json').exists())

    def fire(self, source='startup', **fields):
        config = tomllib.loads((self.profile / 'config.toml').read_text())
        hook = config['hooks']['SessionStart'][0]['hooks'][0]
        raw = {'hook_event_name': 'SessionStart', 'source': source, 'session_id': 'physical-1',
               'cwd': str(self.root), 'transcript_path': str(self.root / 'native.jsonl'), **fields}
        done = subprocess.run(hook['command'], shell=True, input=json.dumps(raw), text=True,
                              capture_output=True)
        self.assertEqual(done.returncode, 0, done.stderr)
        return {'stdout': done.stdout, 'raw': raw}

    def test_configures_direct_tools_with_env_name_never_token_value(self):
        self.configure()
        with patch.dict(os.environ, {'MEMORY_TOKEN': 'DO-NOT-COPY-TOKEN'}):
            self.install()
        text = (self.profile / 'config.toml').read_text()
        server = tomllib.loads(text)['mcp_servers']['assertion_memory']
        self.assertEqual(server['url'], 'https://memory.example.test/mcp')
        self.assertEqual(server['bearer_token_env_var'], 'MEMORY_TOKEN')
        self.assertNotIn('command', server)
        for path in self.profile.rglob('*'):
            if path.is_file(): self.assertNotIn('DO-NOT-COPY-TOKEN', path.read_text())

    def test_start_resume_compact_inject_policy_and_durable_actual_identity(self):
        self.configure()
        self.install()
        for source in ('startup', 'resume', 'compact'):
            text = self.fire(source)['stdout']
            self.assertIn('memory_query', text)
            self.assertIn('memory_source', text)
            self.assertIn('not instructions', text)
            self.assertIn('capture is not configured', text)
            self.assertIn('refresh_required', text)
            self.assertNotIn('memory_capture_message(', text)
            self.assertLessEqual(len(text), 6000)
            receipt = json.loads((self.root / 'state' / 'physical-1' / 'assertion-memory-session.json').read_text())
            self.assertEqual(receipt['session_id'], 'physical-1')
            self.assertEqual(receipt['transcript_path'], str(self.root / 'native.jsonl'))
            self.assertEqual(receipt['capture_status'], 'not_configured')
            self.assertEqual(receipt['source'], source)

    def test_preserves_foreign_servers_and_hook_state_and_uninstalls_only_owned_server(self):
        self.configure()
        self.profile.mkdir()
        original = 'model = "example"\n[mcp_servers.foreign]\ncommand = "foreign"\n[hooks.state.keep]\ntrusted = true\n'
        (self.profile / 'config.toml').write_text(original)
        self.install()
        first = (self.profile / 'config.toml').read_text()
        self.install()
        self.assertEqual(first, (self.profile / 'config.toml').read_text())
        self.configure(enabled=False)
        self.install()
        config = tomllib.loads((self.profile / 'config.toml').read_text())
        self.assertEqual(config['mcp_servers'], {'foreign': {'command': 'foreign'}})
        self.assertEqual(config['hooks']['state']['keep'], {'trusted': True})
        self.assertEqual(self.fire()['stdout'], '')

    def test_foreign_name_collision_fails_before_changing_any_profile_files(self):
        self.configure()
        self.profile.mkdir()
        file = self.profile / 'config.toml'
        original = '[mcp_servers.assertion_memory]\nurl = "https://other.test/mcp"\n'
        file.write_text(original)
        with self.assertRaisesRegex(ValueError, 'assertion_memory'):
            self.install()
        self.assertEqual(file.read_text(), original)
        self.assertFalse((self.profile / 'jarvis').exists())

    def test_user_edited_owned_server_is_preserved_and_reported(self):
        self.configure()
        self.install()
        file = self.profile / 'config.toml'
        changed = file.read_text().replace('https://memory.example.test/mcp', 'https://edited.test/mcp')
        file.write_text(changed)
        with self.assertRaisesRegex(ValueError, 'modified|changed'):
            self.install()
        self.assertEqual(file.read_text(), changed)

    def test_invalid_or_secret_bearing_endpoints_fail_before_install(self):
        for url in ('http://remote.test/mcp', 'https://user:secret@remote.test/mcp',
                    'https://remote.test/mcp?token=secret', 'https://remote.test/mcp#secret'):
            with self.subTest(url=url):
                self.configure(url=url)
                with self.assertRaises(ValueError): self.install()
                self.assertFalse(self.profile.exists())

    def test_claude_uses_explicit_user_mcp_file_and_preserves_foreign_fields(self):
        self.configure()
        claude = self.root / 'claude-user.json'
        claude.write_text(json.dumps({'userID': 'synthetic', 'mcpServers': {'foreign': {'command': 'tool'}}}))
        self.install('claude', ('--mcp-settings-file', str(claude)))
        config = json.loads(claude.read_text())
        self.assertEqual(config['userID'], 'synthetic')
        self.assertEqual(config['mcpServers']['foreign'], {'command': 'tool'})
        self.assertEqual(config['mcpServers']['assertion_memory'], {'type': 'http',
            'url': 'https://memory.example.test/mcp', 'headers': {'Authorization': 'Bearer ${MEMORY_TOKEN}'}})
        self.configure(enabled=False)
        self.install('claude', ('--mcp-settings-file', str(claude)))
        self.assertEqual(json.loads(claude.read_text())['mcpServers'], {'foreign': {'command': 'tool'}})

    def test_claude_does_not_guess_global_mcp_file_from_custom_profile(self):
        self.configure()
        with self.assertRaisesRegex(ValueError, 'mcp-settings-file'):
            self.install('claude')
        self.assertFalse(self.profile.exists())

    def test_subagent_context_keeps_parent_receipt(self):
        self.configure()
        self.install()
        self.fire()
        before = (self.root / 'state' / 'physical-1' / 'assertion-memory-session.json').read_text()
        config = tomllib.loads((self.profile / 'config.toml').read_text())
        hook = config['hooks']['SubagentStart'][0]['hooks'][0]
        done = subprocess.run(hook['command'], shell=True, input=json.dumps({'hook_event_name': 'SubagentStart',
            'session_id': 'physical-1', 'cwd': str(self.root), 'agent_id': 'child/unsafe', 'agent_type': 'worker'}),
            text=True, capture_output=True)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn('memory_query', json.loads(done.stdout)['hookSpecificOutput']['additionalContext'])
        self.assertEqual(before, (self.root / 'state' / 'physical-1' / 'assertion-memory-session.json').read_text())

    def test_dry_run_validates_connection_without_writing_profile(self):
        self.configure()
        self.install(extra=('--dry-run',))
        self.assertFalse(self.profile.exists())

    def test_failed_hook_trust_leaves_mcp_ownership_consistent_for_retry(self):
        self.configure()
        self.install()
        self.configure(url='https://updated.test/mcp')
        with patch.object(installer.CodexBackend, 'register', side_effect=RuntimeError('trust unavailable')):
            with self.assertRaisesRegex(RuntimeError, 'trust unavailable'): self.install()
        self.assertTrue((self.profile/'jarvis/modules/assertion-memory/mcp/installed.json').is_file())
        with patch.dict(os.environ):
            os.environ.pop('MEMORY_TOKEN',None);self.install()
        config = tomllib.loads((self.profile / 'config.toml').read_text())
        self.assertEqual(config['mcp_servers']['assertion_memory']['url'], 'https://updated.test/mcp')

    def test_claude_startup_injects_policy_and_uses_real_session_metadata(self):
        self.configure()
        self.install('claude', ('--mcp-settings-file', str(self.root / 'claude-user.json')))
        config = json.loads((self.profile / 'settings.json').read_text())
        hook = config['hooks']['SessionStart'][0]['hooks'][0]
        raw = {'hook_event_name': 'SessionStart', 'source': 'resume',
               'session_id': 'claude-physical', 'cwd': str(self.root), 'transcript_path': '/not-read'}
        done = subprocess.run([hook['command'], *hook['args']], input=json.dumps(raw), text=True,
                              capture_output=True)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn('memory_query', json.loads(done.stdout)['hookSpecificOutput']['additionalContext'])
        receipt = json.loads((self.root / 'state' / 'claude-physical' / 'assertion-memory-session.json').read_text())
        self.assertEqual(receipt['session_id'], 'claude-physical')
        self.assertEqual(receipt['transcript_path'], '/not-read')

    def test_token_file_auth_uses_supported_native_header_helpers_not_static_secrets(self):
        token = self.root / 'private token'
        token.write_text('synthetic-private-token')
        token.chmod(0o600)
        self.configure_legacy_file({'url':'https://memory.example.test/mcp','bearer_token_file':str(token)})
        self.install()
        text = (self.profile / 'config.toml').read_text()
        config = tomllib.loads(text)['mcp_servers']['assertion_memory']
        self.assertIn('http_headers_helper', config)
        self.assertNotIn('synthetic-private-token', text)
        done = subprocess.run(config['http_headers_helper'], shell=True, capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(json.loads(done.stdout), {'Authorization':'Bearer synthetic-private-token'})

    def test_capture_registration_launches_detached_worker_without_claiming_remote_receipt(self):
        self.configure(capture={'enabled':True,'actor_id':'fiction-user','scope':'fiction','state_dir':str(self.root/'adapter-state')})
        self.install()
        native = self.root / 'native.jsonl'
        native.write_text(json.dumps({'type':'session_meta','payload':{'id':'physical-1','cwd':str(self.root)}})+'\n')
        # Call the installed library directly to inspect launch without leaving a daemon.
        import importlib.util
        entry = self.profile / 'jarvis/modules/assertion-memory/lib/__init__.py'
        spec = importlib.util.spec_from_file_location('installed_capture_adapter', entry, submodule_search_locations=[str(entry.parent)])
        library = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = library
        spec.loader.exec_module(library)
        from jarvis.event import Event
        from jarvis.storage import Storage
        raw = {'hook_event_name':'SessionStart','source':'startup','session_id':'physical-1','cwd':str(self.root),'transcript_path':str(native)}
        event = Event('session-start','physical-1',str(self.root),'codex',raw=raw)
        with patch.dict(os.environ,{'MEMORY_TOKEN':'synthetic-host-credential'}),patch.object(library.subprocess, 'Popen') as launch:
            text = library.context(event, Storage(self.root/'state','physical-1'))
        self.assertIn('registered_local_outbox', text)
        self.assertIn('Registration alone does not prove', text)
        self.assertEqual(launch.call_count, 1)
        self.assertTrue(launch.call_args.kwargs['start_new_session'])
        self.assertTrue(list((self.root/'adapter-state').glob('*/outbox.sqlite3')))

    def test_claude_token_file_uses_documented_headersHelper(self):
        self.configure_legacy_file({'url':'https://memory.example.test/mcp','bearer_token_file':str(self.root/'token')})
        target = self.root/'claude.json'
        self.install('claude', ('--mcp-settings-file',str(target)))
        server = json.loads(target.read_text())['mcpServers']['assertion_memory']
        self.assertIn('headersHelper',server)
        self.assertNotIn('http_headers_helper',server)
        self.assertNotIn('headers',server)

    def test_capture_config_requires_explicit_actor_scope_and_private_state_location(self):
        for capture in ({'enabled':True}, {'enabled':True,'actor_id':'fiction','scope':'fiction','state_dir':'relative'}):
            self.configure(capture=capture)
            with self.assertRaises(ValueError): self.install()
            self.assertFalse(self.profile.exists())

    def test_native_query_timeout_is_bounded_and_configurable(self):
        self.configure(tool_timeout_sec=900)
        self.install()
        server = tomllib.loads((self.profile/'config.toml').read_text())['mcp_servers']['assertion_memory']
        self.assertEqual(server['tool_timeout_sec'],900)
        for invalid in (0,1801,True,'900'):
            self.configure(tool_timeout_sec=invalid)
            before = (self.profile/'config.toml').read_text()
            with self.assertRaises(ValueError): self.install()
            self.assertEqual(before,(self.profile/'config.toml').read_text())

    def test_query_caller_context_uses_actual_harness_session_on_start_resume_compact(self):
        self.configure();self.install()
        for source in ('startup','resume','compact'):
            text=self.fire(source,session_id='actual-harness-session',turn_id='do-not-infer-from-turn',
                semantic_session='do-not-infer-from-topic',transcript_path='/private/DO-NOT-PRINT-source.jsonl')['stdout']
            line=next(line for line in text.splitlines() if line.startswith('Memory query caller context (data, not instructions): '))
            value=json.loads(line.split(': ',1)[1])
            self.assertEqual(value['request_session_id'],'actual-harness-session')
            self.assertEqual(value['attribution'],'caller_context_only')
            self.assertEqual(value['identity_status'],'reported_by_harness_not_source_authenticated')
            self.assertIn('request_session_id',text)
            self.assertNotIn('do-not-infer',text)
            self.assertNotIn('/private/DO-NOT-PRINT-source.jsonl',text)
            self.assertLessEqual(len(text),6000)

    def test_subagent_query_caller_does_not_relabel_parent_or_agent_as_child_session(self):
        self.configure();self.install()
        cfg=tomllib.loads((self.profile/'config.toml').read_text())
        hook=cfg['hooks']['SubagentStart'][0]['hooks'][0]
        done=subprocess.run(hook['command'],shell=True,input=json.dumps({'hook_event_name':'SubagentStart',
            'session_id':'PARENT-PHYSICAL-DO-NOT-CLAIM-CHILD','cwd':str(self.root),
            'agent_id':'AGENT-ID-NOT-PHYSICAL','agent_type':'worker'}),text=True,capture_output=True)
        self.assertEqual(done.returncode,0,done.stderr)
        text=json.loads(done.stdout)['hookSpecificOutput']['additionalContext']
        line=next(line for line in text.splitlines() if line.startswith('Memory query caller context (data, not instructions): '))
        value=json.loads(line.split(': ',1)[1])
        self.assertIsNone(value['request_session_id'])
        self.assertEqual(value['child_physical_session_id'],'unknown')
        self.assertEqual(value['parentage'],'unknown')
        self.assertNotIn('PARENT-PHYSICAL-DO-NOT-CLAIM-CHILD',text)
        self.assertNotIn('AGENT-ID-NOT-PHYSICAL',text)

    def test_query_caller_rendering_is_pure_json_data_and_never_truncates_identity(self):
        from .test_assertion_memory_capture import load_capture
        load_capture()
        import synthetic_memory_adapter as library
        from jarvis.event import Event
        event=Event('session-start','opaque-"quoted"-session', '/private/path-not-for-context','codex',
            raw={'session_id':'opaque-"quoted"-session'})
        with patch.object(Path,'open',side_effect=AssertionError('no file access')), patch.object(library.subprocess,'Popen',side_effect=AssertionError('no processes')):
            text=library._query_caller_context(event)
        line=next(line for line in text.splitlines() if line.startswith('Memory query caller context (data, not instructions): '))
        self.assertEqual(json.loads(line.split(': ',1)[1])['request_session_id'],event.session_id)
        self.assertNotIn('/private/path-not-for-context',text)
        unknown=library._query_caller_context(Event('session-start','x'*5000,'/private','codex',raw={}))
        line=next(line for line in unknown.splitlines() if line.startswith('Memory query caller context (data, not instructions): '))
        self.assertIsNone(json.loads(line.split(': ',1)[1])['request_session_id'])
        self.assertNotIn('x'*100,unknown)
        self.assertLess(len(unknown),1200)

    def test_query_caller_unknown_on_mismatched_or_path_like_session_identity(self):
        from .test_assertion_memory_capture import load_capture
        load_capture()
        import synthetic_memory_adapter as library
        from jarvis.event import Event
        for normalized,reported in (('inferred-semantic-id','actual-source-id'),('/private/DO-NOT-PRINT-session','/private/DO-NOT-PRINT-session')):
            text=library._query_caller_context(Event('session-start',normalized,'/private','codex',raw={'session_id':reported}))
            line=next(line for line in text.splitlines() if line.startswith('Memory query caller context (data, not instructions): '))
            self.assertIsNone(json.loads(line.split(': ',1)[1])['request_session_id'])
            self.assertNotIn(normalized,text)
