"""Synthetic contracts: durable attributed assistant/tool sources, never user adoption."""
import json
import tempfile
import unittest
from pathlib import Path

from prototypes.assertion_memory.capture import RawEvent
from prototypes.assertion_memory.dialogue import extract_dialogue, event_role
from prototypes.assertion_memory.engine import MemoryEngine
from prototypes.assertion_memory.queue import CaptureQueue
from prototypes.assertion_memory.storage import MemoryStore
from prototypes.assertion_memory.worker import LinkWorker
from prototypes.assertion_memory import source_generation, source_reprocessing


def event(role, text='I propose keeping retries bounded.'):
    return RawEvent(role, 'session', 'turn', text, 'probe' if role == 'tool' else role,
                    'synthetic://' + role, 'scope', '2026-11-16T00:00:00+00:00', {'role': role})


class UnifiedOrigins(unittest.TestCase):
    def provider(self, target, *, omission=False, reject=False, kind=None):
        self.calls = []
        claim = {'text': ('The tool reports: ' if target.metadata['role'] == 'tool' else 'The assistant proposes: ') + target.prompt,
                 'quote': target.prompt}
        if kind is not None:
            claim['kind'] = kind
        def model(request):
            self.calls.append(request)
            if request.task.startswith('Extract'):
                return json.dumps({'assertions': [] if omission else [claim]})
            if request.task == 'Repair omitted source meanings':
                return json.dumps({'assertions': [claim]})
            if request.task.startswith('Repair rejected'):
                return json.dumps({'assertions': []})
            if request.task == 'Review source material coverage':
                present = any(r.get('role') == 'interpretation' for r in request.records)
                return json.dumps({'source_coverage': {'status': 'accepted' if present else 'incomplete',
                    'reason': 'Target proposal/output is attributable without user adoption.',
                    'omissions': [] if present else [{'quote': target.prompt, 'reason': 'Material attributed target omitted.'}]}})
            if request.task == 'Verify contextual assertion interpretations':
                return json.dumps({'verdicts': [{'index': r['index'], 'status': 'unsupported' if reject else 'accepted',
                    'reason': 'Attribution only; no user adoption or independent world verification.'}
                    for r in request.records if r.get('role') == 'interpretation']})
            raise AssertionError(request.task)
        return model

    def test_assistant_proposal_survives_without_user_adoption(self):
        target = event('assistant')
        result = extract_dialogue(self.provider(target), target, [target])
        self.assertEqual(len(result['assertions']), 1)
        claim = result['assertions'][0]
        self.assertEqual(claim['evidence_refs'][0]['role'], 'assistant')
        self.assertEqual(claim['interpretation']['verification_scope'], 'attributed_source_entailment')
        self.assertNotIn('kind', claim, 'Absent optional classification must remain absent.')
        self.assertTrue(any(r.task == 'Review source material coverage' for r in self.calls))

    def test_tool_origin_survives_extraction_and_evidence(self):
        target = event('tool', 'Probe returned status=failed; no successful result was observed.')
        self.assertEqual(event_role(target), 'tool')
        result = extract_dialogue(self.provider(target), target, [target])
        self.assertEqual(result['assertions'][0]['evidence_refs'][0]['role'], 'tool')
        targets = [r.records[0] for r in self.calls if r.task.startswith('Extract')]
        self.assertEqual(targets[0]['actor'], 'probe')
        self.assertEqual(targets[0]['role'], 'tool')

    def test_assistant_omission_repair_receives_same_verification(self):
        target = event('assistant')
        result = extract_dialogue(self.provider(target, omission=True), target, [target])
        self.assertEqual(len(result['assertions']), 1)
        self.assertTrue(any(r.task == 'Repair omitted source meanings' for r in self.calls))
        self.assertTrue(any(r.task == 'Verify contextual assertion interpretations' for r in self.calls))

    def test_unsupported_nonuser_claim_cannot_bypass_verification(self):
        for role in ('assistant', 'tool'):
            target = event(role)
            result = extract_dialogue(self.provider(target, reject=True), target, [target])
            self.assertEqual(result['assertions'], [])
            self.assertTrue(any(r.task == 'Verify contextual assertion interpretations' for r in self.calls))

    def test_optional_open_classification_is_preserved(self):
        target = event('assistant')
        result = extract_dialogue(self.provider(target, kind='investigation-note'), target, [target])
        self.assertEqual(result['assertions'][0]['kind'], 'investigation-note')

    def test_source_roles_are_not_operational_instructions(self):
        for role in ('system', 'developer', 'reasoning'):
            target = event(role)
            with self.assertRaises(ValueError):
                extract_dialogue(lambda request: self.fail('Excluded origin reached model'), target, [target])

    def test_all_semantic_passes_explicitly_preserve_nonuser_attribution(self):
        target = event('assistant')
        extract_dialogue(self.provider(target, omission=True), target, [target])
        semantic = [r for r in self.calls if r.task.startswith(('Extract', 'Verify', 'Repair omitted', 'Review source'))]
        self.assertGreaterEqual(len(semantic), 4)
        for request in semantic:
            self.assertIn('Tool output', request.instructions)
            self.assertIn('user adoption', request.instructions)
            self.assertNotIn('Unreferenced assistant proposals remain context only', request.instructions)
            self.assertNotIn('Do not independently inventory unrelated assistant-origin claims', request.instructions)


class UnifiedQueue(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = MemoryStore(Path(self.temp.name) / 'memory.sqlite3')
        self.addCleanup(self.store.close)
        self.queue = CaptureQueue(self.store)

    def capture(self, role):
        target = event(role)
        return self.queue.capture_event(target.session_id, target.turn_id, target.prompt,
            actor=target.actor, source=target.source, scope=target.scope, metadata=target.metadata,
            message_id=role, position=0)

    def test_assistant_is_pending_until_real_extraction_and_preserves_actor(self):
        target = self.capture('assistant')
        self.assertEqual(self.queue.get(target.id).status, 'waiting_model')
        fixture = UnifiedOrigins()
        worker = LinkWorker(self.queue, MemoryEngine(self.store), fixture.provider(target))
        job = worker.run_once()
        self.assertEqual(job.id, target.id)
        self.assertEqual(len(job.extracted_ids), 1, job.error)
        assertion = self.store.get_assertion(job.extracted_ids[0])
        self.assertEqual(assertion.actor, 'assistant')
        self.assertEqual(assertion.metadata['role'], 'assistant')
        self.assertEqual(assertion.metadata['evidence_refs'][0]['role'], 'assistant')

    def test_no_raw_reasoning_or_system_intake(self):
        for role in ('system', 'developer', 'reasoning'):
            with self.assertRaises(ValueError):
                self.capture(role)
        with self.assertRaises(ValueError):
            self.queue.capture_event('s', 't', 'private reasoning', actor='assistant', source='synthetic://r',
                                     metadata={'role': 'assistant', 'channel': 'analysis'})
        self.assertEqual(self.queue.list_events('session'), [])

    def test_generation_admits_assistant_as_source(self):
        target = self.capture('assistant')
        result = source_generation.admit_page(self.queue, 'synthetic-upgrade')
        self.assertEqual(result['context'], 0)
        with self.store.transaction() as c:
            ids = [r[0] for r in c.execute('SELECT event_id FROM memory_source_generation_events')]
        self.assertEqual(ids, [target.id])

    def test_resumed_legacy_generation_rescans_previously_skipped_assistant(self):
        target = self.capture('assistant')
        with self.store.transaction(write=True) as c:
            source_generation.schema(c)
            c.execute("INSERT INTO memory_source_generations VALUES('legacy',999,1)")
        result = source_generation.admit_page(self.queue, 'legacy')
        self.assertEqual(result['scanned'], 1)
        self.assertEqual(source_generation.progress(self.queue, 'legacy')['context'], 0)
        with self.store.transaction() as c:
            ids = [r[0] for r in c.execute("SELECT event_id FROM memory_source_generation_events WHERE operation='legacy'")]
        self.assertEqual(ids, [target.id])

    def test_old_context_only_job_is_requeued_without_rewriting_raw(self):
        target = self.capture('assistant')
        with self.store.transaction(write=True) as c:
            c.execute("UPDATE memory_capture_jobs SET status='completed' WHERE id=?", (target.id,))
        source_reprocessing.tick(self.queue)
        job = self.queue.get(target.id)
        self.assertEqual(job.phase, 'extract')
        self.assertEqual(job.status, 'waiting_model')
        self.assertEqual(self.queue.get_event(target.id), target)


class UnifiedConsumers(unittest.TestCase):
    setUp = UnifiedQueue.setUp
    capture = UnifiedQueue.capture
    def test_pending_assistant_is_a_query_processing_gap(self):
        target = self.capture('assistant')
        snapshot = MemoryEngine(self.store).capture_snapshot()
        row = next(r for r in snapshot.source_processing_evidence if r['event_id'] == target.id)
        self.assertFalse(row['context_only'])
        self.assertEqual(row['status'], 'pending')

    def test_assistant_zero_canonical_omission_has_source_authority(self):
        from prototypes.assertion_memory.overview_source_proof import SourceProofResolver
        target = self.capture('assistant')
        def provider(request):
            if request.task.startswith('Extract'):
                return json.dumps({'assertions': []})
            if request.task == 'Review source material coverage':
                return json.dumps({'source_coverage': {'status': 'unresolved', 'reason': 'Synthetic missing detail.', 'omissions': []}})
            raise AssertionError(request.task)
        engine = MemoryEngine(self.store)
        worker = LinkWorker(self.queue, engine, provider)
        worker.run_once()
        self.assertIsNone(self.queue.get(target.id).error)
        snapshot = engine.capture_snapshot()
        gap = next(r for r in snapshot.source_processing_evidence if r['event_id'] == target.id)
        self.assertEqual(gap['raw_source']['role'], 'assistant')
        authority = SourceProofResolver(self.store, snapshot).omission_authority({}, {}, gap=gap)
        self.assertEqual(authority['event_id'], target.id)
        self.assertTrue(self.queue.request_source_audit(target.id, 'synthetic attributable omission'))

    def test_catalog_counts_humans_separately_without_excluding_agent_sources(self):
        from prototypes.assertion_memory.navigation_catalog import _source_metadata
        target = self.capture('assistant')
        with self.store.transaction() as c:
            rows = _source_metadata(c, 'scope')
        self.assertEqual(len(rows), 1)
        self.assertFalse(rows[0]['context_only'])
        self.assertEqual(rows[0]['origin_role'], 'assistant')
        self.assertEqual(rows[0]['id'], target.id)

    def test_tool_or_assistant_output_cannot_supply_user_decision_anchor(self):
        from prototypes.assertion_memory.link_proof import reported_decision_proof
        for role in ('assistant', 'tool', 'user'):
            ref = {'event_id': 'e', 'start': 0, 'end': 3, 'role': role, 'relation': 'statement', 'text': 'Yes'}
            record = {'actor': role, 'metadata': {'event_id': 'e', 'evidence_refs': [ref]}}
            verdict = {'status': 'accepted', 'basis': 'reported_decision',
                       'decision_evidence': {'source_refs': [ref], 'target_refs': [ref]}}
            result = reported_decision_proof(record, record, verdict)
            if role == 'user':
                self.assertIsNotNone(result)
            else:
                self.assertIsNone(result)

    def test_same_agent_can_correct_its_own_report_without_user_adoption(self):
        from prototypes.assertion_memory.link_proof import self_correction_proof
        from prototypes.assertion_memory.worker import _correction_anchor
        for role in ('assistant', 'tool'):
            ref = {'event_id': 'e', 'start': 0, 'end': 10, 'role': role, 'relation': 'statement', 'text': 'Correction'}
            record = {'actor': role, 'metadata': {'event_id': 'e', 'evidence_refs': [ref]}}
            anchor = {k: v for k, v in ref.items() if k != 'text'}
            verdict = {'status': 'accepted', 'basis': 'self_correction',
                       'correction_evidence': {'source': anchor, 'target': anchor}}
            self.assertTrue(self_correction_proof(record, record, verdict))
            self.assertIsNotNone(_correction_anchor(record, 'Correction'))


if __name__ == '__main__':
    unittest.main()
