"""Independent lanes and selected-source publication fences, synthetic only."""
from dataclasses import asdict
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from prototypes.assertion_memory.capture import CaptureInput
from prototypes.assertion_memory.engine import MemoryEngine
from prototypes.assertion_memory.model import ModelError, ModelRequest
from prototypes.assertion_memory.queue import CaptureQueue
from prototypes.assertion_memory.storage import MemoryStore
from prototypes.assertion_memory.worker import LinkWorker


class WorkerLanesTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'synthetic.sqlite3'
        self.store = MemoryStore(self.path)
        self.addCleanup(self.store.close)
        self.queue = CaptureQueue(self.store)
        self.engine = MemoryEngine(self.store)

    def phase_job(self, phase, index):
        event = self.queue.capture_event('queue', str(index), 'Synthetic source ' + str(index), source='fixture://queue')
        with self.store.transaction(write=True) as db:
            db.execute('UPDATE memory_capture_jobs SET phase=? WHERE id=?', (phase, event.id))
        return event

    def test_source_claim_round_robin_never_takes_refresh_or_starves_links(self):
        for index, phase in enumerate(('extract', 'extract', 'audit', 'link', 'refresh')):
            self.phase_job(phase, index)
        claimed = [self.queue._claim(with_model=True, source_first=True, lane='source') for _ in range(4)]
        self.assertEqual([claim.job.phase for claim in claimed[:3]], ['extract', 'audit', 'link'])
        self.assertIsNone(self.queue._claim(with_model=True, source_first=True, lane='source'))
        self.assertEqual(self.queue._claim(with_model=True, source_first=True, lane='maintenance').job.phase, 'refresh')

    def test_legacy_no_model_done_reenters_source_link_without_resetting_data(self):
        self.store.append_assertion(assertion_id='candidate',text='Synthetic common evidence for the second report.',
            actor='fixture',source='fixture://candidate',episode='legacy',recorded_at='2026-01-01')
        source = self.queue.capture(CaptureInput('Synthetic common evidence for the first report.',
            'fixture','fixture://source','legacy',assertion_id='source'))
        before = [asdict(row) for row in self.store.list_assertions()]
        first = LinkWorker(self.queue,self.engine,lane='source').run_once()
        self.assertEqual((first.id,first.phase),(source.assertion_id,'refresh'))
        self.assertTrue(first.candidates, 'the no-model link must really have a candidate')
        waiting = LinkWorker(self.queue,self.engine,lane='maintenance').run_once()
        self.assertEqual((waiting.status,waiting.phase),('waiting_model','done'))
        self.assertIsNone(self.queue._seconds_until_work(with_model=False,lane='source'))
        self.assertEqual(self.queue._seconds_until_work(with_model=True,lane='source'),0)
        claim = self.queue._claim(with_model=True,source_first=True,lane='source')
        self.assertIsNotNone(claim)
        self.assertEqual((claim.job.id,claim.job.phase),(waiting.id,'link'))
        for field in ('candidates','proposals','refreshed_views','extracted_ids'):
            self.assertEqual(getattr(claim.job,field),getattr(waiting,field),field)
        self.assertEqual([asdict(row) for row in self.store.list_assertions()],before)
        self.assertEqual(self.store.list_edges(),[])

    def test_waiting_done_uses_link_fairness_bucket_and_completed_done_stays_terminal(self):
        waiting = self.phase_job('done',0)
        completed = self.phase_job('done',1)
        with self.store.transaction(write=True) as db:
            db.execute("UPDATE memory_capture_jobs SET status='waiting_model' WHERE id=?",(waiting.id,))
            db.execute("UPDATE memory_capture_jobs SET status='completed' WHERE id=?",(completed.id,))
        for index,phase in enumerate(('extract','extract','audit','link'),2):
            self.phase_job(phase,index)
        claims = [self.queue._claim(with_model=True,source_first=True,lane='source') for _ in range(3)]
        self.assertEqual([claim.job.phase for claim in claims],['extract','audit','link'])
        self.assertEqual(claims[-1].job.id,waiting.id)
        self.assertEqual(self.queue.get(completed.id).status,'completed')
        self.assertEqual(self.queue.get(completed.id).phase,'done')

    def test_completed_done_is_never_ready_in_source_lane(self):
        event = self.phase_job('done',0)
        with self.store.transaction(write=True) as db:
            db.execute("UPDATE memory_capture_jobs SET status='completed' WHERE id=?",(event.id,))
        self.assertIsNone(self.queue._claim(with_model=True,source_first=True,lane='source'))
        self.assertIsNone(self.queue._seconds_until_work(with_model=True,lane='source'))

    def test_maintenance_turns_rotate_independently_of_source_backlog(self):
        self.phase_job('extract', 1)
        chosen = []
        for _ in range(3):
            kind = self.queue._maintenance_choice({'source','derived','editorial'},32,with_model=True,independent=True)
            chosen.append(kind)
            self.queue._refresh_turn_taken(kind)
        self.assertEqual(set(chosen),{'source','derived','editorial'})
        self.assertEqual(len(chosen),len(set(chosen)))

    def test_maintenance_editorial_route_passes_the_real_worker(self):
        worker = LinkWorker(self.queue,self.engine,lane='maintenance')
        sentinel = object()
        with patch.object(worker.names,'seconds_until_work',return_value=0), \
                patch.object(worker.names,'reconcile_page'), \
                patch('prototypes.assertion_memory.view_name_work.run_turn',return_value=sentinel) as run:
            self.assertIs(worker.run_once(),sentinel)
        run.assert_called_once_with(worker,fairness=True)

    def test_source_observer_covers_one_real_claim_and_its_release(self):
        event = self.phase_job('extract', 1)
        notifications = []
        def provider(request):
            if request.task.startswith('Extract'):
                return '{"assertions":[]}'
            return '{"source_coverage":{"status":"accepted","reason":"Synthetic source checked.","omissions":[]}}'
        worker = LinkWorker(self.queue, self.engine, provider, lane='source', claim_observer=lambda *args: notifications.append(args))
        result = worker.run_once()
        self.assertEqual(result.id, event.id)
        self.assertEqual([row[0:3] for row in notifications], [('claimed', 'source', event.id), ('released', 'source', event.id)])
        self.assertEqual(notifications[0][3], notifications[1][3])
        self.assertTrue(notifications[0][3])

    def test_source_observer_releases_registration_after_provider_failure(self):
        self.phase_job('extract', 1)
        notifications = []
        def provider(request):
            raise ModelError('Synthetic provider failure')
        worker = LinkWorker(self.queue,self.engine,provider,lane='source',
            claim_observer=lambda *args:notifications.append(args))
        self.assertEqual(worker.run_once().status,'pending')
        self.assertEqual([item[0] for item in notifications],['claimed','released'])
        self.assertEqual(notifications[0][1:],notifications[1][1:])

    def test_same_session_sources_can_complete_in_reverse_without_losing_raw(self):
        first = self.queue.capture_event('ordered','first','First synthetic report.',source='fixture://first',position=0)
        later = self.queue.capture_event('ordered','later','Later synthetic report.',source='fixture://later',position=1)
        acquired = threading.Barrier(2)
        later_done = threading.Event()
        completed, errors = [], []
        def run():
            try:
                with MemoryStore(self.path) as store:
                    queue = CaptureQueue(store)
                    def provider(request):
                        if request.task.startswith('Extract'):
                            target = request.records[0]
                            acquired.wait(timeout=5)
                            if target['id'] == first.id:
                                if not later_done.wait(timeout=5):raise RuntimeError('later source did not complete')
                            return json.dumps({'assertions':[{'text':target['text'],'quote':target['text']}]})
                        if request.task.startswith('Verify contextual'):
                            return json.dumps({'verdicts':[{'index':row['index'],'status':'accepted','reason':'Synthetic independent check.'}
                                for row in request.records if row.get('role')=='interpretation']})
                        return '{"source_coverage":{"status":"accepted","reason":"Synthetic complete source check.","omissions":[]}}'
                    result = LinkWorker(queue,MemoryEngine(store),provider,lane='source').run_once()
                    completed.append(result.id)
                    if result.id == later.id:later_done.set()
            except BaseException as error:
                errors.append(type(error).__name__)
        threads = [threading.Thread(target=run) for _ in range(2)]
        for thread in threads:thread.start()
        for thread in threads:thread.join(timeout=7)
        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(errors,[])
        self.assertEqual(completed,[later.id,first.id])
        self.assertEqual([row.prompt for row in self.queue.list_events('ordered')],[first.prompt,later.prompt])
        self.assertEqual(len(self.store.list_assertions()),2)

    def test_maintenance_refresh_runs_provider_steps_despite_ready_sources(self):
        self.phase_job('extract', 1)
        assertion = self.queue.capture(CaptureInput('Retained synthetic assertion.', 'fixture', 'fixture://a', 'queue'))
        with self.store.transaction(write=True) as db:
            db.execute("UPDATE memory_capture_jobs SET phase='refresh' WHERE id=?", (assertion.assertion_id,))
        calls = []
        self.engine.model = lambda request: calls.append(request.task) or 'Synthetic response'
        worker = LinkWorker(self.queue, self.engine, lane='maintenance')
        def refresh(*args, **kwargs):
            self.engine.model(ModelRequest('Synthetic first step', [], 'Inspect synthetic data.'))
            self.engine.model(ModelRequest('Synthetic second step', [], 'Inspect synthetic data.'))
            return []
        with patch.object(self.engine, 'refresh_from', side_effect=refresh):
            result = worker.run_once()
        self.assertEqual(result.phase, 'done')
        self.assertEqual(len(calls), 2)
        self.assertTrue(self.queue._ready_sources(with_model=True))

    def test_inserted_antecedent_rejects_frozen_extraction_without_touching_raw(self):
        target = self.queue.capture_event('ordered', 'later', 'Synthetic dependent report.',
            source='fixture://later', position=1)
        claim = self.queue._claim(with_model=True)
        frozen = self.queue._freeze_claim_input(claim)
        self.queue.capture_event('ordered', 'earlier', 'Synthetic newly delivered antecedent.',
            source='fixture://earlier', position=0)
        result = self.queue._finish_extraction(claim, target,
            {'input_fingerprint': frozen.fingerprint, 'assertions': [], 'reviews': []})
        self.assertEqual(result.status, 'pending')
        self.assertEqual(result.phase, 'extract')
        self.assertEqual(self.queue.get_event(target.id), target)
        self.assertEqual(self.store.list_assertions(), [])


class LinkPublicationFenceTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'synthetic.sqlite3'
        self.store = MemoryStore(self.path)
        self.addCleanup(self.store.close)
        self.queue = CaptureQueue(self.store)
        self.engine = MemoryEngine(self.store)
        self.raw = {}
        self.assertions = {}
        for key in ('a', 'b', 'c'):
            raw = self.queue.capture_event('session-' + key, 'one', 'Synthetic evidence ' + key + '.',
                source='fixture://' + key, scope='synthetic', position=1)
            assertion = self.queue.capture(CaptureInput(raw.prompt, 'fixture', raw.source, raw.session_id,
                assertion_id=key, scope='synthetic', recorded_at=raw.recorded_at))
            with self.store.transaction(write=True) as db:
                db.execute("UPDATE memory_capture_jobs SET status='completed',phase='done' WHERE event_id=?", (raw.id,))
                db.execute('INSERT INTO memory_extraction_runs(event_id,fingerprint,completed_at,result) VALUES(?,?,?,?)',
                    (raw.id, 'legacy-' + key, raw.recorded_at, json.dumps({'assertion_ids': [key]})))
            self.raw[key], self.assertions[key] = raw, assertion

    def link(self, mutate, *, include_unused=False, execute=False):
        targets = ('b', 'c') if include_unused else ('b',)
        matches = [{'id': key, 'score': 1, 'method': 'synthetic', 'assertion': asdict(self.assertions[key])} for key in targets]
        def provider(request):
            mutate()
            return json.dumps({'links': [{'target_id': 'b', 'kind': 'supports',
                'source_quote': self.assertions['a'].text, 'target_quote': self.assertions['b'].text}]})
        worker = LinkWorker(self.queue, self.engine, provider, source_first=True)
        claim = self.queue._claim(with_model=True, source_first=True, maintenance_phase='link')
        self.assertEqual(claim.job.assertion_id, 'a')
        with patch.object(self.engine, 'suggest_links', return_value=matches):
            if execute:
                from prototypes.assertion_memory.worker_lanes import execute_claim
                return execute_claim(worker,claim)
            return worker._link(claim)

    def insert_prefix(self, key):
        self.queue.capture_event('session-' + key, 'earlier', 'Synthetic inserted antecedent.',
            source='fixture://earlier-' + key, scope='synthetic', position=0)

    def withdraw(self, key):
        raw = self.raw[key]
        manifest = {'schema': 'source-interpretation/v1', 'event_id': raw.id, 'protocol': 'synthetic-v1',
            'input_fingerprint': 'synthetic-withdrawal', 'status': 'accepted_complete', 'assertion_ids': [],
            'reconciliation': [{'assertion_id': key, 'decision': 'withdrawn', 'verification': {
                'status': 'accepted', 'verifier': 'independent-synthetic', 'reason': 'Synthetic withdrawal proof.',
                'evidence_refs': [{'event_id': raw.id, 'start': 0, 'end': len(raw.prompt)}]}}]}
        with self.store.transaction(write=True) as db:
            db.execute('INSERT INTO memory_extraction_runs(event_id,fingerprint,completed_at,result) VALUES(?,?,?,?)',
                (raw.id, 'withdraw-' + key, self.queue._time(), json.dumps({'source_revision': manifest})))

    def test_selected_source_antecedent_change_after_model_cannot_publish_link(self):
        with self.assertRaisesRegex(ModelError, 'source.*changed'):
            self.link(lambda: self.insert_prefix('a'))
        self.assertEqual(self.store.list_edges(), [])

    def test_changed_link_source_requeues_under_existing_attempt_budget(self):
        result = self.link(lambda:self.insert_prefix('a'),execute=True)
        self.assertEqual((result.status,result.phase,result.attempts),('pending','link',1))
        self.assertIn('ModelError: Selected link source revision changed',result.error)
        self.assertEqual(self.store.list_edges(),[])

    def test_selected_endpoint_withdrawal_after_model_cannot_publish_link(self):
        with self.assertRaisesRegex(ModelError, 'source.*changed'):
            self.link(lambda: self.withdraw('b'))
        self.assertEqual(self.store.list_edges(), [])

    def test_unselected_candidate_revision_does_not_invalidate_selected_pair(self):
        self.link(lambda: self.insert_prefix('c'), include_unused=True)
        self.assertEqual([(edge.from_id, edge.to_id) for edge in self.store.list_edges()], [('a', 'b')])

    def test_two_link_claims_in_opposite_directions_publish_one_relation(self):
        acquired = threading.Barrier(2)
        errors = []
        def run():
            try:
                with MemoryStore(self.path) as store:
                    queue, engine = CaptureQueue(store), MemoryEngine(store)
                    def suggest(identity, **kwargs):
                        other = 'b' if identity == 'a' else 'a'
                        return [{'id':other,'score':1,'method':'synthetic','assertion':asdict(self.assertions[other])}]
                    engine.suggest_links = suggest
                    def provider(request):
                        identity = request.records[0]['assertion_id']
                        acquired.wait(timeout=5)
                        return json.dumps({'links':[{'target_id':'b' if identity=='a' else 'a',
                            'kind':'supports','direction':'source_to_candidate' if identity=='a' else 'candidate_to_source',
                            'source_quote':self.assertions['a'].text,'target_quote':self.assertions['b'].text}]})
                    worker = LinkWorker(queue,engine,provider,source_first=True)
                    claim = queue._claim(with_model=True,source_first=True,maintenance_phase='link')
                    worker._link(claim)
            except BaseException as error:
                errors.append(type(error).__name__)
        threads = [threading.Thread(target=run) for _ in range(2)]
        for thread in threads:thread.start()
        for thread in threads:thread.join(timeout=7)
        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(errors,[])
        self.assertEqual([(edge.from_id,edge.to_id,edge.kind) for edge in self.store.list_edges()],[('a','b','supports')])


if __name__ == '__main__':
    unittest.main()
