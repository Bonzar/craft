"""Ordinary full-set module delivery, host-env auth, and retired-hook cleanup."""
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

from . import CORE_SOURCE,MODULES_DIR
from .test_installer import installer


class SingleMemoryModuleTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.source=self.root/'modules';shutil.copytree(MODULES_DIR,self.source,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
        self.profile=self.root/'profile';self.config=self.source/'assertion-memory/mcp/connection.json'
        self.env=patch.dict(os.environ,{'JARVIS_ASSERTION_MEMORY_CONFIG':str(self.root/'absent-legacy.json'),'ASSERTION_MEMORY_URL':'https://unit.invalid/memory/mcp','ASSERTION_MEMORY_TOKEN':'synthetic-fixture-token'})
        self.env.start();self.addCleanup(self.env.stop)
        self.safe_descriptor()
        self.dotenv=patch.object(installer.mcp_config,'repository_env',return_value=None,create=True)
        self.dotenv.start();self.addCleanup(self.dotenv.stop)

    def safe_descriptor(self):
        value=json.loads(self.config.read_text())
        if 'connection' in value:value['connection']['capture']={'enabled':False}
        self.config.write_text(json.dumps(value))

    def install(self):
        return installer.install(installer.parse_args(['--harness','codex','--no-trust','--modules',str(self.source),
            '--settings-dir',str(self.profile),'--state-dir',str(self.root/'hook-state'),'--core',str(CORE_SOURCE)]))

    def test_unconfigured_module_does_not_require_claude_mcp_settings(self):
        with patch.dict(os.environ):
            os.environ.pop('ASSERTION_MEMORY_TOKEN',None)
            installer.install(installer.parse_args(['--harness','claude','--modules',str(self.source),
                '--settings-dir',str(self.profile),'--state-dir',str(self.root/'hook-state'),'--core',str(CORE_SOURCE)]))
        self.assertFalse((self.profile/'jarvis/modules/assertion-memory/mcp/installed.json').exists())

    def test_dotenv_connects_native_mcp_without_export_and_keeps_secrets_out_of_config(self):
        dotenv=self.root/'.env';secret='SYNTHETIC_DOTENV_SECRET'
        dotenv.write_text('UNRELATED=ignored\nexport ASSERTION_MEMORY_TOKEN="'+secret+'" # local\n')
        with patch.dict(os.environ),patch.object(installer.mcp_config,'repository_env',return_value=dotenv):
            os.environ.pop('ASSERTION_MEMORY_TOKEN',None);self.install()
            server=tomllib.loads((self.profile/'config.toml').read_text())['mcp_servers']['assertion_memory']
            self.assertNotIn('bearer_token_env_var',server)
            result=subprocess.run(server['http_headers_helper'],shell=True,capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(json.loads(result.stdout),{'Authorization':'Bearer '+secret})
        for path in (self.profile/'config.toml',self.profile/'jarvis/installed.json',
                     self.profile/'jarvis/modules/assertion-memory/mcp/installed.json'):
            self.assertNotIn(secret,path.read_text())

    def test_unrelated_dotenv_does_not_enable_optional_memory_connection(self):
        dotenv=self.root/'.env';dotenv.write_text('CRAFT_API_BASE=synthetic-other-service\n')
        with patch.dict(os.environ),patch.object(installer.mcp_config,'repository_env',return_value=dotenv):
            os.environ.pop('ASSERTION_MEMORY_TOKEN',None)
            installer.install(installer.parse_args(['--harness','claude','--modules',str(self.source),
                '--settings-dir',str(self.profile),'--state-dir',str(self.root/'hook-state'),'--core',str(CORE_SOURCE)]))
        self.assertFalse((self.profile/'jarvis/modules/assertion-memory/mcp/installed.json').exists())

    def test_dotenv_worker_transport_uses_env_priority_and_reloads_file(self):
        # Install the real module, then exercise its public transport credential resolver.
        dotenv=self.root/'.env';dotenv.write_text('ASSERTION_MEMORY_TOKEN=file-first\n')
        with patch.object(installer.mcp_config,'repository_env',return_value=dotenv):self.install()
        entry=self.profile/'jarvis/modules/assertion-memory/lib/headers.py'
        spec=importlib.util.spec_from_file_location('dotenv_headers',entry)
        headers=importlib.util.module_from_spec(spec);spec.loader.exec_module(headers)
        binding=json.loads((self.profile/'jarvis/modules/assertion-memory/mcp/installed.json').read_text())
        config=binding['connection']
        self.assertEqual(headers.token(config),'synthetic-fixture-token')
        with patch.dict(os.environ):
            os.environ.pop('ASSERTION_MEMORY_TOKEN',None)
            self.assertEqual(headers.token(config),'file-first')
            dotenv.write_text("ASSERTION_MEMORY_TOKEN='file-second'\n")
            self.assertEqual(headers.token(config),'file-second')
            dotenv.write_text('ASSERTION_MEMORY_TOKEN=$(touch forbidden)\n')
            with self.assertRaises(ValueError):headers.token(config)

    def test_dotenv_missing_or_malformed_token_fails_without_echoing_secrets(self):
        dotenv=self.root/'.env';dotenv.write_text('ASSERTION_MEMORY_TOKEN=synthetic-initial-token\n')
        with patch.object(installer.mcp_config,'repository_env',return_value=dotenv):self.install()
        server=tomllib.loads((self.profile/'config.toml').read_text())['mcp_servers']['assertion_memory']
        for content in ('UNRELATED=private-other-value\n', 'ASSERTION_MEMORY_TOKEN="bad secret"\n',
                        'ASSERTION_MEMORY_TOKEN="$(touch forbidden)"\n', 'ASSERTION_MEMORY_TOKEN=\n', None):
            if content is None:dotenv.unlink()
            else:dotenv.write_text(content)
            with patch.dict(os.environ):
                os.environ.pop('ASSERTION_MEMORY_TOKEN',None)
                result=subprocess.run(server['http_headers_helper'],shell=True,capture_output=True,text=True)
            self.assertEqual(result.returncode,1)
            self.assertEqual(result.stdout,'')
            self.assertEqual(result.stderr,'Memory credential unavailable\n')
        self.assertFalse((self.root/'forbidden').exists())

    def test_dotenv_path_is_main_checkout_even_when_installer_runs_from_worktree(self):
        main=self.root/'main';main.mkdir();worktree=self.root/'worktree'
        for args in (['init',str(main)], ['-C',str(main),'-c','user.name=Fixture','-c',
                     'user.email=fixture@example.invalid','commit','--allow-empty','-m','fixture'],
                     ['-C',str(main),'worktree','add','--detach',str(worktree)]):
            subprocess.run(['git',*args],check=True,capture_output=True)
        dotenv=main/'.env';dotenv.write_text('ASSERTION_MEMORY_TOKEN=synthetic-main-secret\n')
        (worktree/'.env').write_text('ASSERTION_MEMORY_TOKEN=wrong-worktree-secret\n')
        shutil.copytree(self.source,worktree/'modules');self.source=worktree/'modules'
        # Undo only this test's discovery seam: exercise actual Git resolution.
        self.dotenv.stop()
        before=Path.cwd()
        try:
            os.chdir(worktree)
            with patch.dict(os.environ):
                os.environ.pop('ASSERTION_MEMORY_TOKEN',None);self.install()
                server=tomllib.loads((self.profile/'config.toml').read_text())['mcp_servers']['assertion_memory']
                result=subprocess.run(server['http_headers_helper'],shell=True,capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(json.loads(result.stdout),{'Authorization':'Bearer synthetic-main-secret'})
        finally:os.chdir(before)

    def test_claude_dotenv_uses_native_header_helper(self):
        dotenv=self.root/'.env';dotenv.write_text('ASSERTION_MEMORY_TOKEN=synthetic-claude-secret\n')
        with patch.dict(os.environ),patch.object(installer.mcp_config,'repository_env',return_value=dotenv):
            os.environ.pop('ASSERTION_MEMORY_TOKEN',None)
            installer.install(installer.parse_args(['--harness','claude','--modules',str(self.source),
                '--settings-dir',str(self.profile),'--state-dir',str(self.root/'hook-state'),'--core',str(CORE_SOURCE),
                '--mcp-settings-file',str(self.root/'claude.json')]))
            server=json.loads((self.root/'claude.json').read_text())['mcpServers']['assertion_memory']
            result=subprocess.run(server['headersHelper'],shell=True,capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(json.loads(result.stdout),{'Authorization':'Bearer synthetic-claude-secret'})
        self.assertNotIn('headers',server)

    def test_configured_connection_survives_update_without_installer_credentials(self):
        self.install();before=tomllib.loads((self.profile/'config.toml').read_text())['mcp_servers']
        binding=self.profile/'jarvis/modules/assertion-memory/mcp/installed.json'
        prior=json.loads(binding.read_text())
        with patch.dict(os.environ):
            os.environ.pop('ASSERTION_MEMORY_TOKEN',None);self.install()
        self.assertEqual(tomllib.loads((self.profile/'config.toml').read_text())['mcp_servers'],before)
        self.assertEqual(json.loads(binding.read_text()),prior)

    def test_unrelated_dotenv_preserves_existing_binding_without_export(self):
        self.install()
        before=tomllib.loads((self.profile/'config.toml').read_text())['mcp_servers']
        binding=self.profile/'jarvis/modules/assertion-memory/mcp/installed.json'
        prior=binding.read_text();dotenv=self.root/'.env';dotenv.write_text('CRAFT_API_BASE=other-service\n')
        with patch.dict(os.environ),patch.object(installer.mcp_config,'repository_env',return_value=dotenv):
            os.environ.pop('ASSERTION_MEMORY_TOKEN',None)
            os.environ['ASSERTION_MEMORY_URL']='https://changed.invalid/mcp'
            self.install()
        self.assertEqual(tomllib.loads((self.profile/'config.toml').read_text())['mcp_servers'],before)
        self.assertEqual(binding.read_text(),prior)

    def test_main_module_owns_all_hooks_and_default_env_binding_without_machine_json(self):
        self.install();cfg=tomllib.loads((self.profile/'config.toml').read_text());server=cfg['mcp_servers']['assertion_memory']
        self.assertEqual(server['url'],'https://unit.invalid/memory/mcp');self.assertEqual(server['bearer_token_env_var'],'ASSERTION_MEMORY_TOKEN')
        self.assertNotIn('http_headers_helper',server)
        ledger=json.loads((self.profile/'jarvis/installed.json').read_text())['modules']
        self.assertNotIn('assertion-memory-context',ledger)
        self.assertEqual(ledger['assertion-memory']['events'],['session-start','after-compact','subagent-start'])
        for event in ('SessionStart','SubagentStart'):
            commands=[h['command'] for group in cfg['hooks'][event] for h in group['hooks']]
            self.assertEqual(sum('/assertion-memory/hooks/module.py' in c for c in commands),1)
            self.assertFalse(any('assertion-memory-context' in c for c in commands))

    def test_installer_never_reads_host_token_value_or_serializes_it(self):
        original_get=os.environ.get
        original_item=type(os.environ).__getitem__
        def safe_get(key,*args):
            if key=='ASSERTION_MEMORY_TOKEN':raise AssertionError('installer inspected host secret')
            return original_get(key,*args)
        def safe_item(environment,key):
            if key=='ASSERTION_MEMORY_TOKEN':raise AssertionError('installer resolved host secret')
            return original_item(environment,key)
        with patch.dict(os.environ,{'ASSERTION_MEMORY_TOKEN':'SYNTHETIC_HOST_UUID_DO_NOT_SERIALIZE'}),patch.object(os.environ,'get',side_effect=safe_get),patch.object(type(os.environ),'__getitem__',safe_item):self.install()
        for path in (self.profile/'config.toml',self.profile/'jarvis/installed.json',self.profile/'jarvis/modules/assertion-memory/mcp/installed.json'):
            self.assertNotIn('SYNTHETIC_HOST_UUID_DO_NOT_SERIALIZE',path.read_text())

    def test_startup_policy_is_main_library_context_and_does_not_claim_gui_environment(self):
        self.install();cfg=tomllib.loads((self.profile/'config.toml').read_text())
        command=next(h['command'] for group in cfg['hooks']['SessionStart'] for h in group['hooks'] if '/assertion-memory/hooks/module.py' in h['command'])
        done=subprocess.run(command,shell=True,text=True,capture_output=True,input=json.dumps({'hook_event_name':'SessionStart','source':'startup',
            'session_id':'synthetic-physical','cwd':str(self.root),'transcript_path':None}))
        self.assertEqual(done.returncode,0,done.stderr);self.assertIn('memory_query',done.stdout)
        self.assertIn('ASSERTION_MEMORY_TOKEN',done.stdout);self.assertIn('GUI',done.stdout)
        self.assertIn('not verified',done.stdout);self.assertIn('unrelated shell',done.stdout)
        self.assertLessEqual(len(done.stdout),6000)

    def test_full_source_migration_removes_retired_hook_only_and_keeps_foreign_modules_agents_and_server(self):
        # Model an already installed old split source without touching live profiles.
        old=self.source/'assertion-memory-context';(old/'hooks').mkdir(parents=True,exist_ok=True)
        (old/'module.toml').write_text('slug="assertion-memory-context"\nrequires=["assertion-memory"]\nfor=["assertion-memory"]\nevents=["session-start","after-compact","subagent-start"]\n')
        (old/'hooks/module.py').write_text('pass\n')
        main=self.source/'assertion-memory/module.toml';new_manifest=main.read_text();main.write_text('slug="assertion-memory"\n')
        desired=json.loads(self.config.read_text());legacy=self.root/'old-machine.json';legacy.write_text(json.dumps({'url':'https://old.invalid/mcp','bearer_token_file':str(self.root/'old-key')}))
        self.config.write_text(json.dumps({'server_name':'assertion_memory','config_file':str(legacy),'config_file_env':'SYNTHETIC_OLD_MEMORY_CONFIG'}))
        self.profile.mkdir();(self.profile/'config.toml').write_text('[mcp_servers.node_repl]\ncommand="foreign-original"\n')
        self.install();before=json.loads((self.profile/'jarvis/installed.json').read_text())['modules'];agents=(self.profile/'AGENTS.md').read_bytes()
        main.write_text(new_manifest);self.config.write_text(json.dumps(desired));shutil.rmtree(old);self.install()
        after=json.loads((self.profile/'jarvis/installed.json').read_text())['modules'];cfg=tomllib.loads((self.profile/'config.toml').read_text())
        self.assertEqual(set(after),set(before)-{'assertion-memory-context'})
        self.assertEqual((self.profile/'AGENTS.md').read_bytes(),agents)
        self.assertEqual(cfg['mcp_servers']['node_repl'],{'command':'foreign-original'})
        self.assertEqual(cfg['mcp_servers']['assertion_memory']['bearer_token_env_var'],'ASSERTION_MEMORY_TOKEN')
        self.assertNotIn('http_headers_helper',cfg['mcp_servers']['assertion_memory'])
        self.assertFalse((self.profile/'jarvis/modules/assertion-memory-context').exists())
        self.assertFalse(any('assertion-memory-context' in h['command'] for groups in cfg['hooks'].values() if isinstance(groups,list) for group in groups for h in group['hooks']))
        self.assertIn('probe',after)

    def test_runtime_missing_env_sends_nothing_and_present_env_authenticates_without_key_file(self):
        self.install();entry=self.profile/'jarvis/modules/assertion-memory/lib/__init__.py'
        spec=importlib.util.spec_from_file_location('single_memory_runtime',entry,submodule_search_locations=[str(entry.parent)])
        library=importlib.util.module_from_spec(spec);sys.modules[spec.name]=library;spec.loader.exec_module(library)
        from single_memory_runtime.remote_client import Client,TransportError
        called=[]
        class Opener:
            def open(_,request,timeout):called.append(request);raise OSError('synthetic transport boundary')
        with patch.dict(os.environ,{},clear=True):
            with self.assertRaises(TransportError):Client({'url':'https://unit.invalid/mcp','bearer_token_env_var':'ASSERTION_MEMORY_TOKEN'},opener=Opener()).call('memory_status',{})
        self.assertEqual(called,[])
        with patch.dict(os.environ,{'ASSERTION_MEMORY_TOKEN':'00000000-0000-0000-0000-000000000001'}):
            with self.assertRaises(TransportError):Client({'url':'https://unit.invalid/mcp','bearer_token_env_var':'ASSERTION_MEMORY_TOKEN'},opener=Opener()).call('memory_status',{})
        self.assertEqual(len(called),1);self.assertEqual(called[0].get_header('Authorization'),'Bearer 00000000-0000-0000-0000-000000000001')

    def test_missing_gui_env_never_starts_a_worker_that_would_keep_empty_environment(self):
        descriptor=json.loads(self.config.read_text())
        descriptor['connection']['capture']={'enabled':True,'actor_id':'vlad','scope':'conversation','state_dir':str(self.root/'capture-state')}
        self.config.write_text(json.dumps(descriptor));self.install()
        native=self.root/'native.jsonl';native.write_text(json.dumps({'type':'session_meta','payload':{'id':'physical','cwd':str(self.root)}})+'\n')
        entry=self.profile/'jarvis/modules/assertion-memory/lib/__init__.py';spec=importlib.util.spec_from_file_location('env_lifecycle_runtime',entry,submodule_search_locations=[str(entry.parent)])
        library=importlib.util.module_from_spec(spec);sys.modules[spec.name]=library;spec.loader.exec_module(library)
        from jarvis.event import Event
        from jarvis.storage import Storage
        raw={'session_id':'physical','cwd':str(self.root),'source':'startup','transcript_path':str(native)}
        event=Event('session-start','physical',str(self.root),'codex',raw=raw)
        for environment in ({},{'ASSERTION_MEMORY_TOKEN':''}):
            with patch.dict(os.environ,environment,clear=True),patch.object(library.subprocess,'Popen') as spawn:
                text=library.context(event,Storage(self.root/'hook-state','physical'))
                spawn.assert_not_called();self.assertIn('auth_environment_missing',text)
        with patch.dict(os.environ,{'ASSERTION_MEMORY_TOKEN':'00000000-0000-0000-0000-000000000001'},clear=True),patch.object(library.subprocess,'Popen') as spawn:
            text=library.context(event,Storage(self.root/'hook-state','physical'))
            self.assertEqual(spawn.call_count,1);self.assertIn('registered_local_outbox',text)
            self.assertNotIn('00000000-0000-0000-0000-000000000001',text)
        dotenv=self.root/'.env';dotenv.write_text('ASSERTION_MEMORY_TOKEN=synthetic-worker-secret\n')
        binding_path=self.profile/'jarvis/modules/assertion-memory/mcp/installed.json'
        binding=json.loads(binding_path.read_text());binding['connection']['bearer_token_env_file']=str(dotenv)
        binding_path.write_text(json.dumps(binding))
        with patch.dict(os.environ,{},clear=True),patch.object(library.subprocess,'Popen') as spawn:
            text=library.context(event,Storage(self.root/'hook-state','physical'))
            self.assertEqual(spawn.call_count,1);self.assertIn('registered_local_outbox',text)
            self.assertNotIn('synthetic-worker-secret',text)
