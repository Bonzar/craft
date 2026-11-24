"""Canonical background work keeps raw leases, pause and local recovery."""
import fcntl
import tempfile
import unittest
from pathlib import Path
from prototypes.assertion_memory.model import ModelRequest
from prototypes.assertion_memory.queue import CaptureQueue
from prototypes.assertion_memory.storage import MemoryStore
from prototypes.assertion_memory.thought_engine import ThoughtEngine
from prototypes.assertion_memory.thought_store import ThoughtStore
from prototypes.assertion_memory.thought_worker import ThoughtWorker


class Engine:
    _source_key = staticmethod(ThoughtEngine._source_key)

    def __init__(self, thoughts):
        self.thoughts, self.store = thoughts, thoughts.store
        self.model = lambda request: '{"text":"accepted"}'
        self.gateway = None
        self.calls, self.consolidations, self.events = 0, [], []

    def _emit(self, stage, **payload):
        self.events.append({'stage': stage, **payload})

    _raw_origin = staticmethod(ThoughtEngine._raw_origin)
    _model_origin = staticmethod(ThoughtEngine._model_origin)
    _source_packet = ThoughtEngine._source_packet

    def reusable_source_thoughts(self, event_id, origin):
        return []

    def _prepare_source(self, records, bases, text, origin):
        self.calls += 1
        request = ModelRequest('fixture', [{'text': text}])
        self.gateway(request.task, request, tuple(bases[:1])) if self.gateway else self.model(request)
        return [({'text':text},bases[:1])]

    def _publish(self, drafts, *, origin):
        return [self.thoughts.publish(draft['text'], thought_id='from-' + origin['event_id'], bases=bases)
                for draft,bases in drafts]

    def question_texts(self, thought):
        return self.thoughts.questions(thought)

    def _basis(self, thought):
        return {'thought_id': thought.id, 'version': thought.version}

    def _content(self, thought):
        return {'text': thought.text, 'questions': self.question_texts(thought)}

    def _current_material(self, question, candidates, **kwargs):
        return ([{'role': 'material', **self._content(row)} for row in candidates],
                [self._basis(row) for row in candidates])

    def _construct(self, records, bases, **kwargs):
        return [kwargs['target']]

    def retrieve(self, question, explicit_ids=(), limit=10):
        return []

    def answer(self, question, explicit_ids=()):
        return self.thoughts.get(explicit_ids[0])

    def consolidate(self, changed, *, on_affected=None):
        self.consolidations.append([row.id for row in changed])
        if on_affected is not None:
            on_affected([])
        return []


class ThoughtWorkerTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.memory = MemoryStore(self.directory.name + '/memory.sqlite')
        self.addCleanup(self.memory.close)
        self.queue = CaptureQueue(self.memory)
        self.thoughts = ThoughtStore(self.memory)
        self.engine = Engine(self.thoughts)
        self.worker = ThoughtWorker(self.queue, self.engine)
        self.event = self.queue.capture_event('session', 'turn', 'Useful source fact.', source='test')

    def test_source_publishes_canonical_and_consolidation_has_separate_turn(self):
        result = self.worker.run_once()
        self.assertEqual(result.status, 'completed')
        self.assertEqual(self.engine.consolidations, [])
        self.assertEqual(self.thoughts.get('from-' + self.event.id).text, self.event.prompt)
        self.assertEqual(self.worker.run_once()['status'], 'completed')
        self.assertEqual(self.engine.consolidations, [['from-' + self.event.id]])
        self.assertIsNone(self.worker.run_once())
        with self.memory.transaction() as c:
            self.assertEqual(c.execute('SELECT count(*) FROM memory_assertions').fetchone()[0], 0)
            self.assertEqual(c.execute('SELECT count(*) FROM memory_edges').fetchone()[0], 0)

    def test_existing_worker_lock_pauses_processing_but_raw_capture_continues(self):
        with Path(self.memory.path + '.worker.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assertIsNone(self.worker.run_once())
            self.queue.capture_event('session', 'second', 'Another fact.', source='test')
            self.assertEqual(self.engine.calls, 0)
            self.assertEqual(len(self.queue.list_events()), 2)
        self.assertEqual(self.worker.run_once().status, 'completed')

    def test_context_respects_native_session_within_a_shared_capture_session(self):
        foreign = self.queue.capture_event('shared', 'foreign', 'Foreign context', source='test',
                                           metadata={'native_session_id': 'foreign'})
        own = self.queue.capture_event('shared', 'own', 'Own context', source='test',
                                       metadata={'native_session_id': 'chosen'})
        target = self.queue.capture_event('shared', 'target', 'Chosen message', source='test',
                                          metadata={'native_session_id': 'chosen'})
        worker = ThoughtWorker(self.queue, self.engine, processing_session_ids=('chosen',))
        self.assertNotEqual(foreign.id, own.id)
        self.assertEqual([], [row.id for row in worker._context(foreign)])
        self.assertEqual([own.id], [row.id for row in worker._context(target)])

    def test_context_budget_reports_known_omissions_and_uninspected_prefix(self):
        oldest = self.queue.capture_event('bounded', 'old', 'Older context.', source='test')
        large = self.queue.capture_event('bounded', 'large', 'A large contextual message.', source='test')
        target = self.queue.capture_event('bounded', 'target', 'Target.', source='test')
        worker = ThoughtWorker(self.queue, self.engine, context_limit=1, context_characters=5)
        self.assertEqual(worker._context(target), [])
        trace = self.engine.events[-1]
        self.assertEqual(trace['stage'], 'worker_context')
        self.assertEqual(trace['selected'], [])
        self.assertEqual({row['event_id']: row['reason'] for row in trace['omitted']},
            {oldest.id: 'event_budget', large.id: 'character_budget'})
        self.assertTrue(trace['earlier_uninspected'])
        self.assertFalse(trace['complete_context'])

    def test_revoked_lease_cannot_publish_a_thought(self):
        def revoke(request):
            with self.memory.transaction(write=True) as c:
                c.execute("UPDATE memory_capture_jobs SET lease_token='other' WHERE id=?", (self.event.id,))
            return '{}'
        self.engine.model = revoke
        self.worker.run_once()
        self.assertEqual(self.thoughts.list_thoughts(), [])

    def test_changed_thought_work_is_coalesced_without_losing_newer_request(self):
        first = self.thoughts.publish('First.', thought_id='change')
        self.worker.enqueue_changed([first])
        second = self.thoughts.publish('Second.', thought_id='change', expected_version=1)
        self.worker.enqueue_changed([second, second])
        maintenance = ThoughtWorker(self.queue, self.engine, lane='maintenance')
        self.assertEqual(maintenance.run_once()['version'], 2)
        self.assertEqual(self.engine.consolidations, [['change']])
        maintenance.enqueue_changed([second])
        self.assertIsNone(maintenance.run_once())

    def test_new_child_requeues_same_parent_version_once_and_unchanged_refresh_stops(self):
        child = self.thoughts.publish('Child first.', thought_id='child')
        parent = self.thoughts.publish('Parent first.', thought_id='parent', question='Parent question?',
            bases=[{'thought_id': child.id, 'version': 1}])
        grandparent = self.thoughts.publish('Grandparent.', thought_id='grandparent', question='Grand question?',
            bases=[{'thought_id': parent.id, 'version': 1}])
        maintenance = ThoughtWorker(self.queue, self.engine, lane='maintenance')
        maintenance.enqueue_dependents([child])
        self.assertIsNone(maintenance.run_once())
        changed = self.thoughts.publish('Child second.', thought_id='child', expected_version=1)
        maintenance.enqueue_dependents([changed, changed])
        result = maintenance.run_once()
        self.assertEqual(result['version'], 1)
        self.assertEqual(result['completed_generation'], 1)
        self.assertIsNone(maintenance.work.get(grandparent.id))
        maintenance.enqueue_dependents([changed])
        self.assertIsNone(maintenance.run_once())

    def test_changed_parent_refresh_propagates_only_after_new_publication(self):
        child = self.thoughts.publish('Child first.', thought_id='child')
        parent = self.thoughts.publish('Parent first.', thought_id='parent', question='Parent question?',
            bases=[{'thought_id': child.id, 'version': 1}])
        grandparent = self.thoughts.publish('Grandparent.', thought_id='grandparent', question='Grand question?',
            bases=[{'thought_id': parent.id, 'version': 1}])
        child = self.thoughts.publish('Child second.', thought_id='child', expected_version=1)
        def refresh(question, explicit_ids=()):
            return self.thoughts.publish('Parent second.', thought_id=parent.id, question=question,
                bases=[{'thought_id': child.id, 'version': child.version}], expected_version=1)
        self.engine._construct = lambda records, bases, **kwargs: [refresh(parent.question, [parent.id])]
        maintenance = ThoughtWorker(self.queue, self.engine, lane='maintenance')
        maintenance.enqueue_dependents([child])
        self.assertEqual(maintenance.run_once()['completed_version'], 2)
        maintenance.run_once()
        self.assertEqual(maintenance.work.get(grandparent.id)['action'], 'refresh')
        self.assertEqual(self.thoughts.direct_dependents(child.id)[0].version, 2)

    def test_failed_old_generation_cannot_fail_a_newer_parent_request(self):
        self.queue._max_attempts = 1
        parent = self.thoughts.publish('Parent.', question='Parent question?')
        self.worker.work.enqueue_refresh(parent, ('child', 1))
        claim = self.worker.work.claim()
        self.worker.work.enqueue_refresh(parent, ('child', 2))
        result = self.worker.work.fail(claim, RuntimeError('old generation failed'))
        self.assertEqual(result['status'], 'pending')
        self.assertEqual(result['attempts'], 0)
        self.assertEqual(self.worker.work.claim()['generation'], 2)

    def test_refresh_lost_lease_cannot_publish_new_parent_version(self):
        child = self.thoughts.publish('Child.', thought_id='child')
        parent = self.thoughts.publish('Parent.', thought_id='parent', question='Parent question?',
            bases=[{'thought_id': child.id, 'version': 1}])
        maintenance = ThoughtWorker(self.queue, self.engine, lane='maintenance')
        maintenance.enqueue_dependents([child])
        def revoke(question, explicit_ids=()):
            with self.memory.transaction(write=True) as c:
                c.execute("UPDATE memory_thought_work SET lease_token='other' WHERE thought_id=?", (parent.id,))
            return self.thoughts.publish('Lost lease output.', thought_id=parent.id, question=question, expected_version=1)
        self.engine._construct = lambda records, bases, **kwargs: [revoke(parent.question, [parent.id])]
        maintenance.run_once()
        self.assertEqual(self.thoughts.get(parent.id), parent)

    def test_engine_failure_retries_and_reuses_accepted_model_reply(self):
        calls = []
        self.engine.model = lambda request: calls.append(request) or '{}'
        original = self.engine._prepare_source
        def fail_after_response(records, bases, text, origin):
            first = ModelRequest('fixture', [{'text': text}])
            second = ModelRequest('later-step', [{'text': text}])
            self.engine.gateway(first.task, first, tuple(bases[:1]))
            self.engine.gateway(second.task, second, tuple(bases[:1]))
            raise RuntimeError('after consumed step')
        self.engine._prepare_source = fail_after_response
        self.assertEqual(self.worker.run_once().status, 'pending')
        self.engine._prepare_source = original
        self.queue.retry(self.event.id)
        self.assertEqual(self.worker.run_once().status, 'completed')
        self.assertEqual(len(calls), 2)


class ImmutableWorkerLaunchTest(unittest.TestCase):
    def test_worker_and_nested_python_leave_release_bytes_untouched(self):
        import os
        import subprocess
        from unittest.mock import patch
        from prototypes.assertion_memory import mcp_server

        real_popen = subprocess.Popen
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'release'
            state = Path(temp) / 'state'
            state.mkdir()
            pkg = root / 'prototypes' / 'assertion_memory'
            nested = pkg / 'turn_source'
            nested.mkdir(parents=True)
            for directory in (pkg.parent, pkg, nested):
                (directory / '__init__.py').write_text('')
            (nested / 'fixture.py').write_text('VALUE = 2\n')
            entry = pkg / 'mcp_server.py'
            entry.write_text(
                'import subprocess, sys\n'
                'from .turn_source.fixture import VALUE\n'
                'assert VALUE == 2 and sys.dont_write_bytecode\n'
                'subprocess.run([sys.executable, "-c", '
                '"from prototypes.assertion_memory.turn_source.fixture import VALUE; '
                'import sys; assert VALUE == 2 and sys.dont_write_bytecode"], check=True)\n'
            )
            baseline = {p.relative_to(root): p.read_bytes() for p in root.rglob('*') if p.is_file()}
            children = []

            def launch(argv, **kwargs):
                self.assertEqual(argv[1:3], ['-B', '-m'])
                self.assertEqual(kwargs['env']['PYTHONDONTWRITEBYTECODE'], '1')
                child = real_popen(argv, **kwargs)
                children.append(child)
                return child

            config = mcp_server.ServiceConfig(db=str(state / 'fixture.sqlite3'),
                                              provider='codex', model='fictional')
            with (patch.object(mcp_server, '__file__', str(entry)),
                  patch.object(mcp_server, '_eligible_processing_work', return_value=True),
                  patch.object(mcp_server.subprocess, 'Popen', side_effect=launch),
                  patch.dict(os.environ, {'PYTHONDONTWRITEBYTECODE': '0'})):
                for _ in range(2):
                    self.assertTrue(mcp_server.WorkerLauncher(config).start()['scheduled'])
                    self.assertEqual(children[-1].wait(timeout=10), 0)
                    self.assertEqual({p.relative_to(root):p.read_bytes()
                                      for p in root.rglob('*') if p.is_file()}, baseline)
                    self.assertFalse(list(root.rglob('__pycache__')))


if __name__ == '__main__':
    unittest.main()
