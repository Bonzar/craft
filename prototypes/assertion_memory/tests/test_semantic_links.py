"""Semantic relation verification is independent of quotation authenticity."""
import json
import unittest

from prototypes.assertion_memory.capture import CaptureInput
from prototypes.assertion_memory.engine import MemoryEngine
from prototypes.assertion_memory.model import ModelError
from prototypes.assertion_memory.queue import CaptureQueue
from prototypes.assertion_memory.storage import Assertion, MemoryStore
from prototypes.assertion_memory.worker import LinkWorker, _adjudicate, _propose, _verify_semantic_links


def assertion(identifier, text, *, modern=True, actor='user', kind='decision', day=1):
    metadata = {'kind': kind}
    if modern:
        metadata.update(interpretation={'status': 'model_verified'}, evidence_refs=[{
            'event_id': 'event-' + identifier, 'start': 0, 'end': len(text),
            'quote': text, 'role': 'user', 'relation': 'statement',
        }])
    return Assertion(identifier, text, actor, 'test://' + identifier, 'session', None,
                     f'2026-01-{day:02d}T00:00:00+00:00', 'product', metadata)


class SemanticLinksTest(unittest.TestCase):
    def setUp(self):
        self.old = assertion('old', 'We use A for this product.')
        self.new = assertion('new', 'We use B for this product because tests verified its compatibility.', day=2)

    def propose(self, source=None, target=None, *, verdict='accepted', condition=None,
                kind='supersedes', source_quote=None, target_quote=None):
        source, target = source or self.new, target or self.old
        self.requests = []

        def provider(request):
            self.requests.append(request)
            if request.task == 'Verify semantic evidence links':
                return json.dumps({'verdicts': [{'index': row['index'], 'status': verdict,
                                                 'reason': 'Independent relation assessment.'}
                                                for row in request.records if row.get('role') == 'proposed_link']})
            return json.dumps({'links': [{'target_id': target.assertion_id, 'kind': kind,
                                          'source_quote': source_quote or source.text,
                                          'target_quote': target_quote or target.text,
                                          'condition': condition}]})
        return _propose(provider, source, {target.assertion_id: target}, {})[0]

    def test_verified_revision_needs_no_magic_replacement_words(self):
        proposal = self.propose()
        self.assertEqual(proposal['status'], 'eligible')
        self.assertEqual(proposal['verification']['status'], 'accepted')
        verification = [r for r in self.requests if r.task == 'Verify semantic evidence links']
        self.assertEqual(len(verification), 1)
        records = {r['id']: r for r in verification[0].records if r.get('role') != 'proposed_link'}
        self.assertEqual(records['new']['metadata']['evidence_refs'][0]['quote'], self.new.text)

    def test_authentic_quotes_do_not_make_rejected_relation_eligible(self):
        self.assertEqual(self.propose(verdict='unsupported')['status'], 'rejected')
        self.assertEqual(self.propose(verdict='unresolved')['status'], 'pending')

    def test_conditional_coverage_omission_can_be_rejected_independently(self):
        scoped = assertion('new', 'We use B only during the pilot.', day=2)
        proposal = self.propose(source=scoped, verdict='unsupported')
        self.assertEqual(proposal['status'], 'rejected')
        self.assertEqual(proposal['verification']['status'], 'unsupported')

    def test_partial_source_or_target_cannot_reach_semantic_verifier(self):
        for kwargs in ({'source_quote': 'We use B'}, {'target_quote': 'We use A'}):
            with self.subTest(kwargs=kwargs):
                self.assertEqual(self.propose(**kwargs)['status'], 'pending')
                self.assertFalse(any(r.task == 'Verify semantic evidence links' for r in self.requests))

    def test_navigation_links_can_reference_a_proposal_without_adopting_it(self):
        proposed = assertion('draft', 'We propose using film instead of louvers.', kind='proposal')
        reported = assertion('built', 'The contractor reports that film was installed.',
                             kind='implementation', day=2)
        for kind in ('supports', 'realizes'):
            with self.subTest(kind=kind):
                result = self.propose(source=reported, target=proposed, kind=kind)
                self.assertEqual(result['status'], 'eligible')
                self.assertFalse(any(request.task == 'Verify semantic evidence links' for request in self.requests))
        self.assertEqual(proposed.metadata['kind'], 'proposal')
        self.assertEqual(self.propose(source=reported, target=proposed, kind='supersedes')['status'], 'pending')

    def test_structural_authority_blocks_verifier_override(self):
        for kwargs in ({'actor': 'other'}, {'kind': 'proposal'}, {'kind': 'inference'}, {'day': 1}):
            with self.subTest(kwargs=kwargs):
                source = assertion('new', self.new.text, **({'day': 2} | kwargs))
                self.assertEqual(self.propose(source=source)['status'], 'pending')
                self.assertFalse(any(r.task == 'Verify semantic evidence links' for r in self.requests))

    def test_overlapping_observation_intervals_reach_independent_conflict_verifier(self):
        from dataclasses import replace
        old = replace(assertion('old', 'At 07:40 the medical route was open.', kind='observation'),
                      valid_at='2025-02-22T07:40:00+03:00')
        interval = replace(assertion('new', 'The same medical route was blocked from 07:38 until 08:05.',
                                     kind='observation', actor='inspector', day=2),
                           valid_at='2025-02-22T07:38:00+03:00')
        result = self.propose(source=interval, target=old, kind='contradicts')
        self.assertEqual(result['status'], 'eligible')
        self.assertEqual(result['verification']['status'], 'accepted')
        later = replace(interval, text='At 09:00 the medical route was blocked.',
                        valid_at='2025-02-22T09:00:00+03:00', metadata={**interval.metadata,
                            'evidence_refs':[{'event_id':'later','quote':'At 09:00 the medical route was blocked.',
                                             'start':0,'end':len('At 09:00 the medical route was blocked.'),'role':'user'}]})
        result = self.propose(source=later, target=old, kind='contradicts', verdict='unsupported')
        self.assertEqual(result['status'], 'rejected')

    def test_adjudication_alone_cannot_authorize_modern_relation(self):
        result = _adjudicate(self.new, {'old': self.old}, {
            'target_id': 'old', 'kind': 'supersedes', 'source_quote': self.new.text,
            'target_quote': self.old.text,
        })
        self.assertEqual(result['status'], 'pending')
        self.assertTrue(result['verification_required'])

    def test_legacy_only_keeps_existing_lexical_policy(self):
        old = assertion('old', 'We decided to use A.', modern=False)
        new = assertion('new', 'We decided to replace A with B.', modern=False, day=2)
        proposal = self.propose(source=new, target=old)
        self.assertEqual(proposal['status'], 'eligible')
        self.assertEqual(len(self.requests), 1)

    def test_incomplete_verifier_output_fails_closed(self):
        def provider(request):
            if request.task == 'Verify semantic evidence links':
                return '{"verdicts":[]}'
            return json.dumps({'links': [{'target_id': 'old', 'kind': 'supersedes',
                                          'source_quote': self.new.text, 'target_quote': self.old.text}]})
        with self.assertRaises(ModelError):
            _propose(provider, self.new, {'old': self.old}, {})

    def test_renewal_callback_precedes_each_semantic_provider_call(self):
        proposals = [_adjudicate(self.new, {'old': self.old}, {
            'target_id': 'old', 'kind': 'supersedes', 'source_quote': self.new.text,
            'target_quote': self.old.text,
        }) for _ in range(2)]
        calls = []
        def provider(request):
            self.assertEqual(calls[-1], 'renew')
            calls.append('provider')
            return json.dumps({'verdicts': [{'index': row['index'], 'status': 'accepted', 'reason': 'Verified.'}
                                           for row in request.records if row.get('role') == 'proposed_link']})
        _verify_semantic_links(provider, {'old': self.old, 'new': self.new}, proposals,
                               before_verification=lambda: calls.append('renew'))
        self.assertEqual(calls, ['renew', 'provider', 'renew', 'provider'])

    def test_failed_renewal_prevents_provider_call_and_any_partial_eligibility(self):
        proposals = [_adjudicate(self.new, {'old': self.old}, {
            'target_id': 'old', 'kind': 'supersedes', 'source_quote': self.new.text,
            'target_quote': self.old.text,
        })]
        def renew():
            raise RuntimeError('lease lost')
        with self.assertRaisesRegex(RuntimeError, 'lease lost'):
            _verify_semantic_links(lambda _: self.fail('provider called after lease loss'),
                                   {'old': self.old, 'new': self.new}, proposals, before_verification=renew)
        self.assertEqual(proposals[0]['status'], 'pending')

    def test_worker_resolves_compact_refs_and_persists_verification_on_edges(self):
        with MemoryStore(':memory:') as store:
            queue = CaptureQueue(store)
            events = [queue.capture_event('s', str(i), row.text, source=row.source)
                      for i, row in enumerate((self.old, self.new))]
            # These events are fixtures for immutable source text; only link the captured claims.
            with store.transaction(write=True) as connection:
                connection.execute("UPDATE memory_capture_jobs SET status='completed', phase='done'")
            for row, event in zip((self.old, self.new), events):
                metadata = {**row.metadata, 'evidence_refs': [{'event_id': event.id,
                            'start': 0, 'end': len(event.prompt), 'role': 'user'}]}
                queue.capture(CaptureInput(row.text, row.actor, row.source, row.episode,
                                           assertion_id=row.assertion_id, scope=row.scope,
                                           recorded_at=row.recorded_at, metadata=metadata))
            seen = []
            def provider(request):
                seen.append(request)
                if request.task == 'Verify semantic evidence links':
                    for record in request.records:
                        if record.get('role') == 'claim':
                            self.assertEqual(record['metadata']['evidence_refs'][0]['text'], record['text'])
                    return json.dumps({'verdicts': [{'index': r['index'], 'status': 'accepted', 'reason': 'Evidence supports revision.'}
                                                   for r in request.records if r.get('role') == 'proposed_link']})
                source = request.records[0]
                if source['id'] == 'old':
                    return '{"links":[]}'
                return json.dumps({'links': [{'target_id': 'old', 'kind': 'supersedes',
                                             'source_quote': self.new.text, 'target_quote': self.old.text}]})
            LinkWorker(queue, MemoryEngine(store), provider).drain()
            edges = store.list_edges()
            self.assertEqual(len(edges), 1)
            self.assertEqual(edges[0].metadata['verification']['status'], 'accepted')
            self.assertEqual(queue.get('new').proposals[0]['verification']['status'], 'accepted')


if __name__ == '__main__':
    unittest.main()
