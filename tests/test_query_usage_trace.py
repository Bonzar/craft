"""Provider usage belongs to the current query context, never a time-window guess."""
import concurrent.futures
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest

from prototypes.assertion_memory.model_usage import UsageLedger, read_usage, usage_attempt
from prototypes.assertion_memory.usage_context import query_usage_trace


class QueryUsageTraceTest(unittest.TestCase):
    def test_concurrent_contexts_and_failed_attempts_keep_exact_attribution(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory)/'memory.sqlite3'
            ledger = UsageLedger.for_database(db)
            barrier = threading.Barrier(2)
            def run(tokens):
                with query_usage_trace() as trace:
                    try:
                        with usage_attempt(ledger, task='Generate memory answer', provider='fixture') as attempt:
                            barrier.wait(timeout=3)
                            attempt.observe({'usage':{'prompt_tokens':tokens,'completion_tokens':1}}, 'openai-chat')
                            raise ValueError('fixture failure')
                    except ValueError:
                        pass
                return trace
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                traces = list(executor.map(run, (3, 7)))
            for trace, tokens in zip(traces, (3,7)):
                report = read_usage(db, trace_id=trace)
                self.assertEqual(report['totals']['attempts'], 1)
                self.assertEqual(report['totals']['input_tokens'], tokens)
                self.assertEqual(report['totals']['failed'], 1)
                self.assertGreaterEqual(report['totals']['duration_ms'], 0)
                self.assertEqual(report['totals']['duration_ms_known_attempts'], 1)
            with ledger.attempt(task='Generate memory answer', provider='fixture'):
                pass
            with sqlite3.connect(ledger.path) as connection:
                self.assertIsNone(connection.execute('SELECT trace_id FROM attempts ORDER BY rowid DESC LIMIT 1').fetchone()[0])

    def test_nested_trace_restores_parent_and_cached_query_has_zero_attempts(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory)/'memory.sqlite3'
            ledger = UsageLedger.for_database(db)
            with query_usage_trace() as outer:
                with query_usage_trace() as cached:
                    pass
                with ledger.attempt(task='Generate memory answer', provider='fixture'):
                    pass
            self.assertNotEqual(outer, cached)
            self.assertEqual(read_usage(db, trace_id=outer)['totals']['attempts'], 1)
            empty = read_usage(db, trace_id=cached)['totals']
            self.assertEqual(empty['attempts'], 0)
            self.assertEqual(empty['duration_ms_known_attempts'], 0)
            self.assertIsNone(empty['duration_ms'])

    def test_legacy_migration_preserves_unknown_trace_and_duration(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory)/'memory.sqlite3'
            from prototypes.assertion_memory.model_usage import _SCHEMA
            path = Path(str(db)+'.model-usage.sqlite3')
            with sqlite3.connect(path) as connection:
                connection.executescript(_SCHEMA.replace('trace_id TEXT, duration_ms INTEGER, ', ''))
                connection.execute("INSERT INTO tracking VALUES (1,'2026-01-01')")
                connection.execute("INSERT INTO attempts(id,started_at,status,task,provider) VALUES ('old','2026-01-01','succeeded','fixture','codex')")
            self.assertEqual(read_usage(db)['totals']['attempts'], 1)
            with query_usage_trace() as trace:
                self.assertEqual(read_usage(db, trace_id=trace)['status'], 'unavailable')
            UsageLedger.for_database(db)
            with sqlite3.connect(path) as connection:
                self.assertEqual(connection.execute('SELECT trace_id,duration_ms FROM attempts').fetchone(), (None,None))
            self.assertEqual(read_usage(db)['totals']['duration_ms_known_attempts'], 0)

    def test_context_exception_restores_unknown_and_filter_rejects_non_uuid(self):
        from prototypes.assertion_memory.usage_context import current_query_trace
        self.assertIsNone(current_query_trace())
        with self.assertRaises(RuntimeError):
            with query_usage_trace():
                raise RuntimeError('fixture')
        self.assertIsNone(current_query_trace())
        with self.assertRaisesRegex(ValueError, 'UUID'):
            read_usage('/tmp/unused.sqlite3', trace_id='not-a-trace')
