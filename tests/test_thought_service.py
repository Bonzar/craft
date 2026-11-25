import asyncio
import json
import tempfile
import threading
import unittest
from unittest.mock import patch
from pathlib import Path
from prototypes.assertion_memory.storage import MemoryStore
from prototypes.assertion_memory.thought_service import ThoughtService
from prototypes.assertion_memory.mcp_server import ServiceConfig


def navigation_focus(request):
    question = next(row['text'] for row in request.records if row['role'] == 'question')
    options = [row for row in request.records if row['role'] in ('option', 'current_thought')]
    selected = next((row for row in options if question in row.get('questions', [])), options[0])
    quote = question if question in selected.get('questions', []) else selected.get('description', selected.get('text'))
    return {'action': 'focus', 'question': None, 'quote': quote,
            'text': 'The visible quotation identifies the useful thought.'}


def readiness(request):
    question = next(row['text'] for row in request.records if row['role'] == 'question')
    selected = next(row for row in request.records if row['role'] == 'candidate')
    return {'verdict': 'ready' if question in selected.get('questions', []) else 'build',
            'question': None, 'text': 'The candidate answers this question, or needs a focused construction.'}


def construction(request, text, support):
    fragments = [fragment for row in request.records for fragment in row.get('fragments', [])
                 if fragment['text'] == support]
    if len(fragments) != 1:
        raise AssertionError('expected one exact prepared support fragment')
    item = {'text': text, 'question': None, 'support_fragments': [fragments[0]['option']]}
    key = 'thought' if 'thought' in request.response_schema['properties'] else 'thoughts'
    return {key: item if key == 'thought' else [item], 'needs_detail': None}


class ThoughtServiceTest(unittest.TestCase):
    def test_text_read_does_not_load_unrequested_version_history(self):
        with MemoryStore(':memory:') as store:
            service = ThoughtService(store)
            thought = service.thoughts.publish('A self-contained thought.')
            with patch.object(service.thoughts, 'versions', side_effect=AssertionError('unrequested history loaded')):
                result = service.source(thought.id, section='text')
            self.assertEqual(result['content'], thought.text)

    def test_retry_after_failed_review_reuses_consumed_construct_and_clears_checkpoint(self):
        from prototypes.assertion_memory.model import ModelError
        calls = []
        def model(request):
            calls.append(request.task)
            if request.task == 'Navigate thought graph':
                return json.dumps(navigation_focus(request))
            if request.task == 'Check ready thought':
                return json.dumps(readiness(request))
            if request.task == 'Construct thought':
                return json.dumps(construction(request, 'Approval is required.', 'Opening requires approval.'))
            if calls.count('Verify thought') == 1:
                raise ModelError('temporarily unavailable')
            return json.dumps({'verdict': 'accept', 'text': 'The condition is preserved.'})
        with MemoryStore(':memory:') as store:
            service = ThoughtService(store, model)
            service.thoughts.publish('Opening requires approval.')
            with self.assertRaisesRegex(ModelError, 'temporarily unavailable'):
                service.query('What is required to open?')
            result = service.query('What is required to open?')
            self.assertTrue(result['answer_accepted'])
            self.assertEqual(calls.count('Construct thought'), 1)
            self.assertEqual(calls.count('Verify thought'), 2)
            self.assertEqual(calls, ['Navigate thought graph', 'Check ready thought', 'Construct thought', 'Verify thought', 'Verify thought'])
            with store.transaction() as db:
                self.assertEqual(db.execute('SELECT count(*) FROM memory_thought_generations').fetchone()[0], 0)
                reused = db.execute("SELECT count(*) FROM memory_thought_trace_events WHERE stage='model_step_reused'").fetchone()[0]
            self.assertEqual(reused, 3)

    def test_cancelled_caller_does_not_discard_background_answer_and_repeat_is_free(self):
        from prototypes.assertion_memory.mcp_server import _query_with_connection
        from prototypes.assertion_memory.model import CandidateSearch
        with tempfile.TemporaryDirectory() as directory:
            config = ServiceConfig(str(Path(directory) / 'memory.sqlite3'), 'none')
            with MemoryStore(config.db) as store:
                config.service(store).thoughts.publish('Opening requires engineering approval.')
            entered, release, finished = (threading.Event() for _ in range(3))
            calls, outcomes = [], []
            def model(request):
                calls.append(request.task)
                if request.task == 'Navigate thought graph':
                    return json.dumps(navigation_focus(request))
                if request.task == 'Check ready thought':
                    return json.dumps(readiness(request))
                if request.task == 'Construct thought':
                    return json.dumps(construction(request, 'Engineering approval is required.',
                                                   'Opening requires engineering approval.'))
                entered.set()
                if not release.wait(2):
                    raise AssertionError('caller cancellation did not release the test provider')
                return json.dumps({'verdict': 'accept', 'text': 'The condition is preserved.'})
            def perform():
                try:
                    outcomes.append(_query_with_connection(config, 'What is needed to open?', None, None, None))
                finally:
                    finished.set()
            async def disconnect():
                task = asyncio.create_task(asyncio.to_thread(perform))
                try:
                    self.assertTrue(await asyncio.to_thread(entered.wait, 2))
                    task.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await task
                finally:
                    release.set()
                self.assertTrue(await asyncio.to_thread(finished.wait, 2))
            with patch.object(ServiceConfig, 'components', return_value=(model, CandidateSearch(), 'fixture')):
                asyncio.run(disconnect())
                self.assertEqual(calls, ['Navigate thought graph', 'Check ready thought', 'Construct thought', 'Verify thought'])
                repeat = _query_with_connection(config, 'What is needed to open?', None, None, None)
                warm_calls = len(calls)
                unchanged = _query_with_connection(config, 'What is needed to open?', None, None, None)
                self.assertEqual(len(calls), warm_calls)
            self.assertTrue(outcomes[0]['answer_accepted'])
            self.assertEqual(repeat['thought']['id'], outcomes[0]['thought']['id'])
            self.assertEqual(repeat['version_id'], outcomes[0]['version_id'])
            self.assertEqual(unchanged['thought']['id'], outcomes[0]['thought']['id'])
            self.assertEqual(calls.count('Construct thought'), 1)
            self.assertEqual(calls.count('Verify thought'), 1)
            self.assertIn(calls[4:], ([], ['Navigate thought graph', 'Check ready thought']))

    def test_parallel_answer_exposes_unresolved_compatibility_without_hiding_checked_text(self):
        with MemoryStore(':memory:') as store:
            service = ThoughtService(store)
            thought = service.thoughts.publish('The pilot has two possible opening windows.',
                question='When can it open?', metadata={'verification': 'accepted',
                    'publication': {'state': 'parallel_alternative'}})
            with patch('prototypes.assertion_memory.thought_service.traced_engine') as factory:
                factory.return_value.answer.return_value = thought
                factory.return_value._is_parallel_alternative.return_value = True
                result = service.query(thought.question)
            self.assertEqual(result['answer'], thought.text)
            self.assertTrue(result['answer_accepted'])
            self.assertEqual(result['thought']['publication']['state'], 'parallel_alternative')
            self.assertEqual(result['completeness'], 'incomplete')
            self.assertIn('не установлена', result['knowledge_limit'])

    def test_current_query_reuses_native_thought_and_retains_capture(self):
        with MemoryStore(':memory:') as store:
            calls=[]
            def model(request):
                calls.append(request.task)
                return json.dumps(navigation_focus(request) if request.task == 'Navigate thought graph' else readiness(request))
            service=ThoughtService(store, model)
            thought=service.thoughts.publish('Only after engineering approval.', question='When can it open?',
                metadata={'verification':'accepted'})
            result=service.query('When can it open?')
            self.assertEqual(result['answer'],thought.text)
            self.assertTrue(result['answer_accepted'])
            self.assertEqual(calls, ['Check ready thought'])
            service.engine.model=None
            repeat=service.query('When can it open?')
            self.assertEqual(repeat['thought']['id'], thought.id)
            self.assertEqual(calls, ['Check ready thought'])
            self.assertIsNone(store._connection.execute("SELECT 1 FROM sqlite_master WHERE name='memory_views'").fetchone())
            service.capture_message('session','msg','Vlad','user','Keep the condition.',0,'fixture')
            self.assertEqual(service.status()['pending_count'],1)
            source=service.source(thought.id,section='text')
            self.assertIn(thought.text,json.dumps(source))

    def test_empty_database_factory_uses_canonical_storage(self):
        with tempfile.TemporaryDirectory() as directory:
            config=ServiceConfig(str(Path(directory)/'memory.sqlite3'),'none')
            with MemoryStore(config.db) as store:
                self.assertIsInstance(config.service(store),ThoughtService)

    def test_query_construction_preserves_ids_outside_model(self):
        with MemoryStore(':memory:') as store:
            def model(request):
                self.assertFalse(store._connection.in_transaction)
                self.assertNotIn('thought-',json.dumps(request.records))
                if request.task == 'Navigate thought graph':
                    return json.dumps(navigation_focus(request))
                if request.task == 'Check ready thought':
                    return json.dumps({'verdict': 'build', 'question': None, 'text': 'Different question.'})
                if request.task == 'Construct thought':
                    return json.dumps(construction(request, 'Gate opening needs engineering approval.',
                                                   'Gate opens only after engineering approval.'))
                self.assertEqual(request.task, 'Verify thought')
                return json.dumps({'verdict': 'accept', 'text': 'All conditions retained.'})
            service=ThoughtService(store,model)
            base=service.thoughts.publish('Gate opens only after engineering approval.')
            result=service.query('What is required to open the gate?')
            self.assertEqual(result['thought']['bases'][0]['thought_id'],base.id)
            self.assertTrue(result['answer_accepted'])
            with store.transaction() as db:
                calls=db.execute('SELECT bindings,status FROM memory_thought_model_calls').fetchall()
            self.assertEqual(len(calls),4)
            self.assertTrue(all(row['status']=='succeeded' for row in calls))
