"""Synthetic upper maps consume exact accepted child prose, not replayed sources."""
import json
import unittest

from prototypes.assertion_memory.engine import MemoryEngine, ViewSpec
from prototypes.assertion_memory.model import ModelError
from prototypes.assertion_memory.navigation_overview import build
from prototypes.assertion_memory.query_snapshot import receipt
from prototypes.assertion_memory.storage import MemoryStore


class UpperChildSummariesTest(unittest.TestCase):
    def setUp(self):
        self.store = MemoryStore(':memory:')
        self.addCleanup(self.store.close)
        for key, text in (('permit', 'Release needs a permit during the pilot.'),
                          ('desk', 'Release needs a staffed desk when the lead is absent.')):
            self.store.append_assertion(assertion_id=key, text=text, actor='council',
                source='fixture://' + key, episode='pilot', scope='s', recorded_at='2026-01-01')
        self.store.append_edge(edge_id='interface', from_id='permit', to_id='desk', kind='supports',
            recorded_at='2026-01-01', metadata={'condition': 'Only when both pilot conditions apply.'})
        self.requests = []
        self.reject_parent = False
        self.engine = MemoryEngine(self.store, model=self.provider, verify_overviews=True)
        self.snapshot = self.engine.capture_snapshot()
        self.checks = {}
        self.upstream = {}
        for key in ('permit', 'desk'):
            spec = ViewSpec(key, key, key, scopes=('s',), root_ids=(key,))
            self.engine.define_view(spec)
            self.upstream[key] = build(self.engine, spec, self.snapshot, self.context(spec, [key]),
                {}, self.checks, {})
        self.parent = ViewSpec('parent', 'Pilot operation', 'How do the pilot conditions fit together?',
            scopes=('s',), depends_on=('permit', 'desk'))
        self.engine.define_view(self.parent)

    def context(self, spec, sources):
        return {'registry_id': 'fixture-registry', 'route_revision': 1, 'scope': 's',
            'view_id': spec.id, 'required_source_ids': sources,
            'required_view_ids': list(spec.depends_on)}

    def provider(self, request):
        self.requests.append(request)
        context = request.records[0]
        parent = context['topic'] == 'How do the pilot conditions fit together?'
        if parent:
            self.assertFalse(any(row.get('role') == 'source' for row in request.records),
                'parent must read accepted child summaries instead of replaying canonical source text')
            contributions = [row['summary'] for row in request.records
                             if row.get('role') == 'verified_navigation_contribution']
            self.assertEqual(set(contributions), {version.summary for version in self.upstream.values()})
            relation = next(row for row in request.records if row.get('role') == 'relation')
            self.assertEqual(relation['metadata']['condition'], 'Only when both pilot conditions apply.')
        sources, edges = context['required_source_ids'], context['required_edge_ids']
        if request.task.startswith('Review'):
            rejected = parent and self.reject_parent
            return json.dumps({'status': 'revise' if rejected else 'accepted',
                'reason': 'The combined pilot qualification is missing.' if rejected else 'Exact qualified meanings retained.',
                'issues': ['Missing qualification.'] if rejected else [],
                'faithfulness': not rejected, 'navigation_coverage': 'complete',
                'qualifications_preserved': not rejected})
        prose = [row.get('summary', row.get('text', '')) for row in request.records[1:]
                 if row.get('role') in ('source', 'verified_navigation_contribution')]
        return json.dumps({'summary': '\n'.join(prose) + '\nOnly when both pilot conditions apply.'})

    def build_parent(self, checks=None):
        return build(self.engine, self.parent, self.snapshot, self.context(self.parent, ['permit', 'desk']),
            self.upstream, self.checks if checks is None else checks, {})

    def test_parent_uses_exact_children_and_retains_versions_checks_and_evidence(self):
        version = self.build_parent()
        self.assertEqual(version.upstream, {key: value.id for key, value in self.upstream.items()})
        self.assertEqual({row['id'] for row in version.evidence}, {'permit', 'desk'})
        self.assertEqual({row['id'] for row in version.edges}, {'interface'})
        self.assertEqual({row['version_id'] for row in version.overview_review['children']},
                         {value.id for value in self.upstream.values()})
        self.assertEqual(receipt(self.engine, self.checks['parent']['id'])['query_snapshot'], self.snapshot.descriptor())
        for child in self.upstream.values():
            self.assertEqual(self.engine.get_version(child.id), child)

    def test_parent_semantic_rejection_never_promotes_a_version(self):
        self.reject_parent = True
        with self.assertRaisesRegex(ModelError, 'independent review unresolved'):
            self.build_parent()
        self.assertIsNone(self.engine.inspect_view('parent')['current_id'])

    def test_changed_child_receipt_is_rejected_before_parent_model_call(self):
        calls = len(self.requests)
        checks = {**self.checks, 'permit': {**self.checks['permit'], 'query_snapshot': {}}}
        with self.assertRaisesRegex(ModelError, 'matching-frontier'):
            self.build_parent(checks)
        self.assertEqual(len(self.requests), calls)


if __name__ == '__main__':
    unittest.main()
