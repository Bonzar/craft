"""Request accounting preserves query inputs, concurrency and response packing."""
import concurrent.futures
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from prototypes.assertion_memory.model_usage import UsageLedger
from prototypes.assertion_memory.service import MemoryService, _fits
from prototypes.assertion_memory.storage import MemoryStore


class QueryUsageWrapperTest(unittest.TestCase):
    def test_cached_query_has_zero_attempts_and_input_is_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory)/'memory.sqlite3'
            UsageLedger.for_database(db)
            store = MemoryStore(db)
            try:
                service = MemoryService(store)
                with patch.object(service, '_query', return_value={'answer':'cached'}) as query:
                    result = service.query('literal question', 'scope')
                query.assert_called_once_with('literal question','scope',None,None)
                self.assertEqual(result['request_usage']['totals']['attempts'], 0)
                self.assertGreaterEqual(result['request_usage']['wall_ms'], 0)
                self.assertNotIn('trace_id', json.dumps(result))
            finally:
                store.close()

    def test_concurrent_queries_attach_only_their_own_provider_attempts(self):
        with tempfile.TemporaryDirectory() as directory:
            barrier = threading.Barrier(2)
            def run(tokens):
                db = Path(directory)/'memory.sqlite3'
                ledger = UsageLedger.for_database(db)
                store = MemoryStore(db)
                try:
                    service = MemoryService(store)
                    def query(*args):
                        with ledger.attempt(task='Generate memory answer',provider='fixture') as attempt:
                            barrier.wait(timeout=3)
                            attempt.observe({'usage':{'prompt_tokens':tokens,'completion_tokens':1}},'openai-chat')
                        return {'answer':'fixture'}
                    with patch.object(service,'_query',side_effect=query):
                        return service.query('same question')['request_usage']['totals']
                finally:
                    store.close()
            # Initialize schema before separate concurrent connection owners start.
            store = MemoryStore(Path(directory)/'memory.sqlite3'); MemoryService(store); store.close()
            UsageLedger.for_database(Path(directory)/'memory.sqlite3')
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                reports = list(executor.map(run,(3,7)))
            self.assertEqual([row['input_tokens'] for row in reports], [3,7])
            self.assertEqual([row['attempts'] for row in reports], [1,1])

    def test_packing_reserves_usage_before_returning_answer_conditions(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory)/'memory.sqlite3'
            ledger = UsageLedger.for_database(db)
            store = MemoryStore(db)
            try:
                service = MemoryService(store)
                def query(*args):
                    for task in ('Generate memory answer','Name memory view','Review memory view name'):
                        with ledger.attempt(task=task,provider='fixture'):
                            pass
                    result = {'answer':'x','conditions':['keep this condition'],'answer_continuations':[]}
                    while _fits({**result,'answer':result['answer']+'x'*100}):
                        result['answer'] += 'x'*100
                    return result
                with patch.object(service,'_query',side_effect=query):
                    result = service.query('fixture')
                self.assertTrue(_fits(result))
                self.assertEqual(result['conditions'],['keep this condition'])
                self.assertEqual(result['answer_continuations'],[])
                self.assertLessEqual(len(result['request_usage']['stages']),2)
                self.assertEqual(result['request_usage']['stages_omitted_count'],1)
            finally:
                store.close()
