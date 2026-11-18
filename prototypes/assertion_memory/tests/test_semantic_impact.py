"""Bound background hypotheses without weakening conservative read freshness.

Model-call counts here use an explicitly injected provider/ranking fixture.
"""
import json
import unittest

from prototypes.assertion_memory.engine import MemoryEngine, ViewSpec
from prototypes.assertion_memory.model import Candidate
from prototypes.assertion_memory.storage import MemoryStore


class RankFixture:
    identity = 'provider-fixture'
    def __init__(self, selected=('view-00', 'view-01'), *, include_new=True):
        self.selected = set(selected)
        self.include_new = include_new
        self.evidence_seed = None
        self.view_calls = []
    def rank(self, query, candidates, *, limit):
        if any(key.startswith('view-') for key in candidates):
            self.view_calls.append((query, tuple(candidates), limit))
            return [Candidate(key, 1.0, self.identity) for key in sorted(self.selected.intersection(candidates))][:limit]
        selected = ('fact-update',) if self.include_new and 'fact-update' in candidates else (self.evidence_seed,)
        return [Candidate(key, 1.0, self.identity) for key in selected if key in candidates][:limit]


class SemanticImpactTest(unittest.TestCase):
    def setUp(self):
        self.store = MemoryStore(':memory:')
        self.addCleanup(self.store.close)
        self.calls = []
        self.ranker = RankFixture()
        def provider(request):
            self.calls.append(request)
            return 'Provider fixture summary: ' + ', '.join(row['id'] for row in request.records)
        self.engine = MemoryEngine(self.store, model=provider, candidate_search=self.ranker)

    def add(self, key, text='Neutral evidence.', scope='default'):
        return self.store.append_assertion(assertion_id=key, text=text, actor='user', source='fixture://' + key,
                                           episode='s', scope=scope, recorded_at='2026-01-01')

    def views(self, count):
        for index in range(count):
            key = f'fact-{index:02d}'
            self.add(key)
            self.engine.define_view(ViewSpec(f'view-{index:02d}', f'View {index}', f'Topic {index}',
                                             scopes=('default',), root_ids=(key,)))
            self.engine.build_view(f'view-{index:02d}')
        self.calls.clear()

    def test_many_same_scope_views_only_generate_for_bounded_semantic_hypotheses(self):
        self.views(40)
        self.add('fact-update', 'A newly reported change.')
        refreshed = self.engine.refresh_from('fact-update')
        self.assertEqual({row.view_id for row in refreshed}, {'view-00', 'view-01'})
        self.assertEqual(len(self.calls), 2)
        self.assertTrue(self.ranker.view_calls)
        self.assertLessEqual(self.ranker.view_calls[-1][2], 8)

    def test_omitted_background_view_rechecks_full_scope_on_read(self):
        self.ranker.selected = {'view-00'}
        self.views(3)
        before = self.engine.inspect_view('view-02')['current_id']
        self.add('fact-update', 'A newly reported change.')
        self.engine.refresh_from('fact-update')
        self.assertEqual(self.engine.inspect_view('view-02')['current_id'], before)
        self.assertEqual(self.engine.inspect_view('view-02')['status'], 'stale')
        self.calls.clear()
        refreshed = self.engine.build_view('view-02')
        self.assertNotEqual(refreshed.id, before)
        self.assertIn('fact-update', {row['id'] for row in refreshed.evidence})
        self.assertEqual(len(self.calls), 1)

    def test_known_roots_saved_evidence_and_reviews_ignore_bad_semantic_ranking(self):
        self.add('fact-old')
        self.engine.define_view(ViewSpec('view-root', 'Root', 'Unrelated vocabulary', scopes=('default',), root_ids=('fact-old',)))
        self.engine.define_view(ViewSpec('view-evidence', 'Evidence', 'Other words', scopes=('default',)))
        self.ranker.evidence_seed = 'fact-old'
        self.engine.build_view('view-evidence')
        self.ranker.evidence_seed = None
        self.engine.define_view(ViewSpec('view-reviewed', 'Reviewed', 'Reviewed evidence', scopes=('other',)))
        with self.store.transaction(write=True) as connection:
            connection.execute('INSERT INTO memory_branch_reviews VALUES (?, ?, ?, ?, ?, ?, ?)',
                               ('view-reviewed', 'fact-old', 'accepted', 'reviewer', 'Explicit prior relevance.',
                                '2026-01-02', json.dumps({})))
        self.add('fact-update', 'A new qualification.', scope='third')
        self.store.append_edge(edge_id='exception', from_id='fact-update', kind='exception_to', to_id='fact-old',
                               recorded_at='2026-01-03', metadata={'condition': 'only after review'})
        self.ranker.selected = set()
        self.assertEqual(set(self.engine.impact('fact-update')), {'view-root', 'view-evidence', 'view-reviewed'})

    def test_direct_new_links_reach_known_evidence_without_unbounded_navigation_walk(self):
        for key in ('fact-a', 'fact-b', 'fact-c', 'fact-update'):
            self.add(key)
        for key, target in (('fact-update', 'fact-a'), ('fact-a', 'fact-b'), ('fact-b', 'fact-c')):
            self.store.append_edge(edge_id=key + '/' + target, from_id=key, kind='supports', to_id=target,
                                   recorded_at='2026-01-02')
        self.engine.define_view(ViewSpec('view-direct', 'Direct', 'Direct source', scopes=('default',), root_ids=('fact-a',)))
        self.engine.define_view(ViewSpec('view-remote', 'Remote', 'Remote source', scopes=('default',), root_ids=('fact-c',)))
        self.ranker.selected = set()
        self.assertEqual(self.engine.impact('fact-update'), ['view-direct'])

    def test_unchanged_selected_branch_does_not_eagerly_propagate_to_dependents(self):
        self.ranker.selected = {'view-child'}
        self.ranker.include_new = False
        self.add('fact-old')
        self.engine.define_view(ViewSpec('view-child', 'Child', 'Unmatched child topic', scopes=('default',), root_ids=('fact-old',)))
        self.engine.define_view(ViewSpec('view-parent', 'Parent', 'Unmatched parent topic', scopes=('default',), depends_on=('view-child',)))
        old = self.engine.build_view('view-parent')
        self.calls.clear()
        self.add('fact-update', 'Fresh alien record.')
        self.assertEqual(set(self.engine.impact('fact-update')), {'view-child', 'view-parent'})
        refreshed = self.engine.refresh_from('fact-update')
        self.assertEqual([row.view_id for row in refreshed], ['view-child'])
        self.assertEqual(self.calls, [])
        self.assertEqual(self.engine.inspect_view('view-parent')['current_id'], old.id)

    def test_changed_selected_branch_propagates_even_if_parent_semantic_rank_is_poor(self):
        self.ranker.selected = {'view-child'}
        self.add('fact-old')
        self.engine.define_view(ViewSpec('view-child', 'Child', 'Unmatched child topic', scopes=('default',), root_ids=('fact-old',)))
        self.engine.define_view(ViewSpec('view-parent', 'Parent', 'Unmatched parent topic', scopes=('default',), depends_on=('view-child',)))
        self.engine.build_view('view-parent')
        self.add('fact-update', 'Fresh alien record.')
        refreshed = self.engine.refresh_from('fact-update')
        self.assertEqual({row.view_id for row in refreshed}, {'view-child', 'view-parent'})

    def test_semantic_hypotheses_do_not_add_unseen_sources_across_scopes(self):
        self.add('fact-update', 'A new shared subject.', scope='public')
        self.engine.define_view(ViewSpec('view-public', 'Public', 'Shared subject', scopes=('public',)))
        self.engine.define_view(ViewSpec('view-private', 'Private', 'Shared subject', scopes=('private',)))
        self.ranker.selected = {'view-public', 'view-private'}
        self.assertEqual(self.engine.impact('fact-update'), ['view-public'])
        self.assertNotIn('view-private', self.ranker.view_calls[-1][1])

    def test_all_known_obligations_survive_the_hypothesis_limit(self):
        self.add('fact-old')
        for index in range(20):
            self.engine.define_view(ViewSpec(f'view-{index:02d}', 'Known', 'Known evidence',
                                             scopes=('default',), root_ids=('fact-old',)))
        self.ranker.selected = set()
        self.add('fact-update', 'A mandatory correction.')
        self.store.append_edge(edge_id='revision', from_id='fact-update', kind='supersedes', to_id='fact-old',
                               recorded_at='2026-01-03')
        self.assertEqual(len(self.engine.impact('fact-update')), 20)


if __name__ == '__main__':
    unittest.main()
