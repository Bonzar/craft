"""Server model selection and body-free provider accounting contracts."""
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from prototypes.assertion_memory.mcp_server import ServiceConfig
from prototypes.assertion_memory.model import CodexModel, ModelRequest
from prototypes.assertion_memory.model_usage import UsageLedger, read_usage


class ExplicitModelPolicyTest(unittest.TestCase):
    def test_server_codex_requires_explicit_nonblank_model(self):
        for model in (None, '', ' ', 42):
            with self.subTest(model=model), self.assertRaisesRegex(ValueError, 'explicit.*model'):
                ServiceConfig('/tmp/policy.sqlite3', 'codex', model=model)

    def test_codex_model_is_configurable_without_vendor_whitelist(self):
        self.assertEqual(ServiceConfig('/tmp/policy.sqlite3', 'codex', model='future-model').model,
                         'future-model')

    def test_other_providers_and_embedding_selection_are_preserved(self):
        config = ServiceConfig('/tmp/policy.sqlite3', 'local', endpoint='http://localhost:8000',
                               model='custom-local', embedding_model='custom-embedding')
        self.assertEqual(config.model, 'custom-local')
        self.assertEqual(config.embedding_model, 'custom-embedding')
        ServiceConfig('/tmp/policy.sqlite3', 'none')
        ServiceConfig('/tmp/policy.sqlite3', 'subprocess', command=('fixture',))

    def test_codex_pin_reaches_command_and_ledger_without_inventing_actual(self):
        with tempfile.TemporaryDirectory() as directory:
            memory = Path(directory)/'memory.sqlite3'
            ledger = UsageLedger.for_database(memory)
            model = CodexModel(model='gpt-6.1-sol', usage_ledger=ledger)
            self.assertIn('--model', model.command)
            self.assertEqual(model.command[model.command.index('--model')+1], 'gpt-6.1-sol')
            stream = '\n'.join(json.dumps(event) for event in (
                {'type':'item.completed','item':{'type':'agent_message','text':'fixture'}},
                {'type':'turn.completed','usage':{'input_tokens':3,'output_tokens':1}},
            ))
            with patch('prototypes.assertion_memory.model.subprocess.run') as run:
                run.return_value.stdout = stream
                run.return_value.returncode = 0
                self.assertEqual(model(ModelRequest('Generate memory answer', ())), 'fixture')
            group = read_usage(memory)['by_model'][0]
            self.assertEqual(group['requested_model'], 'gpt-6.1-sol')
            self.assertIsNone(group['actual_model'])
            self.assertEqual(group['selection_basis'], 'explicit_model')

    def test_codex_only_provider_completion_reports_actual_model(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger = UsageLedger(Path(directory)/'usage.sqlite3')
            with ledger.attempt(task='Generate memory answer', provider='codex', model='gpt-6.1-sol',
                                selection_basis='explicit_model') as attempt:
                attempt.observe(json.dumps({'type':'turn.completed','model':'provider-revision',
                                            'usage':{}}), 'codex')
            with sqlite3.connect(ledger.path) as connection:
                self.assertEqual(connection.execute('SELECT actual_model,selection_basis FROM attempts').fetchone(),
                                 ('provider-revision','explicit_model'))

    def test_existing_sidecar_is_readable_and_migrated_without_rewriting_history(self):
        with tempfile.TemporaryDirectory() as directory:
            memory = Path(directory)/'memory.sqlite3'
            path = Path(str(memory)+'.model-usage.sqlite3')
            from prototypes.assertion_memory.model_usage import _SCHEMA
            with sqlite3.connect(path) as connection:
                connection.executescript(_SCHEMA.replace('selection_basis TEXT, ', ''))
                connection.execute("INSERT INTO tracking VALUES (1,'2026-01-01')")
                connection.execute("INSERT INTO attempts(id,started_at,status,task,provider) VALUES ('old','2026-01-01','succeeded','fixture','codex')")
            self.assertEqual(read_usage(memory)['by_model'][0]['selection_basis'], None)
            UsageLedger.for_database(memory)
            with sqlite3.connect(path) as connection:
                self.assertEqual(connection.execute('SELECT requested_model,actual_model,selection_basis FROM attempts').fetchone(),
                                 (None,None,None))

    def test_model_identity_changes_pipeline_key_without_provider_call(self):
        with tempfile.TemporaryDirectory() as directory:
            from prototypes.assertion_memory.storage import MemoryStore
            db = str(Path(directory)/'memory.sqlite3')
            store = MemoryStore(db)
            try:
                first = ServiceConfig(db, 'codex', model='gpt-6.1-sol').engine(store)
                second = ServiceConfig(db, 'codex', model='gpt-6-sol').engine(store)
                self.assertNotEqual(first.pipeline_key, second.pipeline_key)
            finally:
                store.close()

    def test_legacy_direct_codex_default_is_reported_as_default_with_unknown_actual(self):
        with tempfile.TemporaryDirectory() as directory:
            memory = Path(directory)/'memory.sqlite3'
            model = CodexModel(usage_ledger=UsageLedger.for_database(memory))
            stream = '\n'.join(json.dumps(event) for event in (
                {'type':'item.completed','item':{'type':'agent_message','text':'fixture'}},
                {'type':'turn.completed','usage':{}},
            ))
            with patch('prototypes.assertion_memory.model.subprocess.run') as run:
                run.return_value.stdout, run.return_value.returncode = stream, 0
                model(ModelRequest('Generate memory answer', ()))
            group = read_usage(memory)['by_model'][0]
            self.assertIsNone(group['requested_model'])
            self.assertIsNone(group['actual_model'])
            self.assertEqual(group['selection_basis'], 'provider_default')
