"""NAV pins uncertainty without adjudicating any unapproved world relation."""
import copy
import json
import unittest
from dataclasses import replace
from unittest.mock import patch

from prototypes.assertion_memory.engine import MemoryEngine
from prototypes.assertion_memory.model import ModelError
from prototypes.assertion_memory.query_snapshot import capture, receipt, _restore_frozen
from prototypes.assertion_memory.service import MemoryService
from prototypes.assertion_memory.tests import test_background_overviews as fixtures

QUALIFICATION = 'Permission cannot be established while relation candidates remain unassessed.'


class NavigationRelationBoundaryTest(unittest.TestCase):
    def setUp(self):
        fixtures.BackgroundOverviewTest.setUp(self)
        self.groups = False
        self.engine = MemoryEngine(self.store, model=self.provider, verify_overviews=True)
        self.service = MemoryService(self.store, self.engine)

    def provider(self, request):
        if self.groups and request.task == 'Discover reusable memory themes':
            self.requests.append(request)
            return json.dumps({'themes': [
                {'question': 'Voltage?', 'source_ids': ['voltage']},
                {'question': 'Gate?', 'source_ids': ['gate']},
                {'question': 'Interface?', 'source_ids': ['interface']},
                {'question': 'Shared interface?', 'source_ids': ['voltage', 'interface']}],
                'unassigned_source_ids': []})
        if request.task == 'Plan reusable memory theme hierarchy':
            self.requests.append(request)
            ids = [row['id'] for row in request.records if row.get('role') == 'view']
            return json.dumps({'groups': [
                {'question': 'Which voltage themes were reported?', 'depends_on': ids[:3]},
                {'question': 'Which interface themes were reported?', 'depends_on': ids[1:]}]})
        answer = fixtures.BackgroundOverviewTest.model(self, request)
        if request.task == 'Write attributed thematic map':
            value = json.loads(answer)
            value['summary'] += '\n' + QUALIFICATION
            return json.dumps(value)
        if request.task in ('Review grounded memory overview', 'Review grounded memory contribution'):
            value = json.loads(answer)
            inventory = next((row for row in request.records if row.get('role') == 'relation_gap_inventory'), None)
            if inventory:
                value['relation_gap_dispositions'] = [
                    {'proposal_id': row['proposal_id'], 'revision': row['revision'],
                     'disposition': 'unassessed', 'reason': 'Fixture preserves all uncertainty; no world permission follows.',
                     'source_ids': [], 'answer_qualified': True, 'qualification_quote': QUALIFICATION}
                    for row in inventory['gaps']]
                value['unassessed_relation_gap_qualification'] = {
                    'answer_qualified': True, 'quote': QUALIFICATION,
                    'reason': 'Omitted proposals remain unassessed, so no action permission follows.'}
            return json.dumps(value)
        if request.task not in ('Discover reusable memory themes', 'Plan memory retrieval for a question') and not request.task.startswith('Review'):
            return answer + '\n' + QUALIFICATION
        return answer

    def proposal(self, index=0, *, status='completed', error=None, waiting=0, to_id='gate'):
        proposal = {'kind': 'depends_on', 'from_id': 'voltage', 'to_id': to_id,
            'status': 'unresolved', 'condition': 'Both checks remain required: ' + str(index),
            'reason': 'Object equivalence remains uncertain.', 'verification': {'status': 'unresolved'}}
        with self.store.transaction(write=True) as db:
            db.execute('INSERT INTO memory_capture_jobs(id,assertion_id,status,phase,proposals,updated_at,available_at,error,waiting_model) '
                'VALUES(?,?,?,?,?,?,?,?,?)', ('boundary-' + str(index), 'voltage', status, 'done', json.dumps([proposal]),
                    '2026-01-02', '2026-01-02', error, waiting))

    def job_rows(self):
        with self.store.transaction() as db:
            return [tuple(row) for row in db.execute('SELECT * FROM memory_capture_jobs ORDER BY id')]

    def directory(self, snapshot=None):
        from prototypes.assertion_memory.background_overviews import run_background_scope
        snapshot = snapshot or capture(self.engine)
        checkpoint = {}
        def group(engine, spec, request, ids):
            from prototypes.assertion_memory.topic_hierarchy import _level
            children = [{'role': 'view', 'id': key, 'question': engine._spec(key).query} for key in ids]
            dependencies, level = _level(engine, spec, request, children, 24000)
            return dependencies, [level]
        with patch('prototypes.assertion_memory.topic_hierarchy.group_definitions', side_effect=group) if self.groups else patch.dict({}, {}):
            result = run_background_scope(self.engine, 's', snapshot, checkpoint, lambda value: None)
        return snapshot, checkpoint, result, self.engine.get_version(result['version_id'])

    def assert_terminal_directory(self, count=3):
        for index in range(count): self.proposal(index)
        rows = self.job_rows()
        status = self.service.status(scope='s')['unresolved']
        snapshot, checkpoint, result, root = self.directory()
        inventory = snapshot.relation_gaps(('s',))
        audit = root.overview_review
        boundary = audit['relation_boundary']
        for key in ('frontier_sha256', 'inventory_sha256', 'source_frontier_sha256', 'total',
                    'operational_count', 'complete', 'unassessed_count'):
            self.assertEqual(boundary[key], inventory[key])
        self.assertEqual(boundary['semantic_assessment'], 'not_assessed')
        self.assertIs(boundary['terminal_candidates_approved'], False)
        self.assertNotIn('relation_gap_review', audit)
        self.assertNotIn('processing_gaps_apply_to_query', audit)
        self.assertEqual(self.job_rows(), rows)
        self.assertEqual(self.service.status(scope='s')['unresolved'], status)
        self.assertEqual(self.store.list_edges(), [])
        self.assertFalse(any(request.task == self.engine._spec(root.view_id).query for request in self.requests))
        return snapshot, checkpoint, result, root

    def test_three_terminal_candidates_allow_only_navigation_without_clearing_scope_state(self):
        self.assert_terminal_directory()

    def test_shared_hierarchy_navigation_pins_same_unassessed_relation_boundary(self):
        self.groups = True
        _, _, _, root = self.assert_terminal_directory()
        children = [self.engine.get_version(key) for key in root.upstream.values()]
        self.assertEqual(len(children), 2)
        self.assertTrue(set(children[0].upstream) & set(children[1].upstream))
        for child in children:
            self.assertEqual(child.overview_review['relation_boundary'], root.overview_review['relation_boundary'])
            self.assertEqual(child.overview_review['assembly_coverage'], 'navigation_index')

    def test_omitted_terminal_candidates_are_allowed_only_as_unassessed_metadata(self):
        snapshot, _, _, root = self.assert_terminal_directory(18)
        boundary = root.overview_review['relation_boundary']
        self.assertFalse(boundary['complete'])
        self.assertGreater(boundary['unassessed_count'], 0)
        self.assertEqual(boundary['total'], 18)
        self.assertEqual(snapshot.relation_gaps(('s',))['operational_count'], 0)

    def test_operational_and_malformed_relation_jobs_block_even_with_zero_terminal_total(self):
        from prototypes.assertion_memory.navigation_index import _require_source_frontier
        for index, changes in enumerate(({'status': 'pending'}, {'status': 'failed', 'error': 'Fixture failure'},
                                        {'waiting': 1}, {'to_id': None})):
            self.proposal(index, **changes)
            snapshot = capture(self.engine)
            self.assertEqual(snapshot.relation_gaps(('s',))['total'], 0)
            self.assertGreater(snapshot.relation_gaps(('s',))['operational_count'], 0)
            with self.subTest(changes=changes), self.assertRaisesRegex(ModelError, 'relation processing'):
                _require_source_frontier(snapshot, ('s',))

    def test_missing_malformed_or_forged_frontier_metadata_fails_closed(self):
        from prototypes.assertion_memory.navigation_index import _require_source_frontier
        snapshot = capture(self.engine)
        original = snapshot.relation_gaps(('s',))
        changes = [{'operational_count': None}, {'operational_count': True}, {'operational_count': -1},
                   {'total': False}, {'total': -1}, {'unassessed_count': -1}, {'complete': 1},
                   {'complete': False}, {'unassessed_count': 1}, {'frontier_sha256': None},
                   {'frontier_sha256': '0' * 64}, {'inventory_sha256': 'bad'},
                   {'source_frontier_sha256': 'f' * 64}]
        class Altered:
            def __getattr__(self, name): return getattr(snapshot, name)
            def relation_gaps(self, scopes): return {**original, **changed}
        for changed in changes:
            with self.subTest(changed=changed), self.assertRaisesRegex(ModelError, 'relation processing'):
                _require_source_frontier(Altered(), ('s',))
        with self.assertRaisesRegex(ModelError, 'relation processing'):
            _require_source_frontier(replace(snapshot, relation_processing_frontier=None), ('s',))

    def test_restored_nav_requires_both_audits_to_keep_mechanical_nonsemantic_purpose(self):
        from prototypes.assertion_memory.navigation_index import validate_publication
        from prototypes.assertion_memory.background_overviews import navigation_context
        snapshot, _, result, root = self.directory()
        check = receipt(self.engine, result['check_id'])
        context = navigation_context(self.engine, self.engine._spec(root.view_id), snapshot)
        self.assertTrue(validate_publication(self.engine, snapshot, context, root, check))
        for field, value in (('domain_coverage', 'all'), ('verification', 'semantic')):
            with self.subTest(field=field, audit='check'):
                changed = copy.deepcopy(check); changed['overview_review'][field] = value
                self.assertFalse(validate_publication(self.engine, snapshot, context, root, changed))
            with self.subTest(field=field, audit='version'):
                changed = replace(root, overview_review={**root.overview_review, field: value})
                self.assertFalse(validate_publication(self.engine, snapshot, context, changed, check))
        self.assertFalse(validate_publication(self.engine, snapshot, context,
            replace(root, summary=root.summary + '\nPermission granted.'), check))

    def test_mutated_relation_descriptor_and_frozen_frontier_cannot_reuse_nav(self):
        from prototypes.assertion_memory.navigation_index import validate_publication
        from prototypes.assertion_memory.background_overviews import navigation_context
        snapshot, _, result, root = self.assert_terminal_directory()
        check = receipt(self.engine, result['check_id'])
        context = navigation_context(self.engine, self.engine._spec(root.view_id), snapshot)
        changed = copy.deepcopy(check)
        changed['overview_review']['relation_boundary']['frontier_sha256'] = '0' * 64
        self.assertFalse(validate_publication(self.engine, snapshot, context, root, changed))
        with self.store.transaction(write=True) as db:
            proposals = json.loads(db.execute("SELECT proposals FROM memory_capture_jobs WHERE id='boundary-0'").fetchone()[0])
            proposals[0]['condition'] = 'Changed condition requiring independent assessment.'
            db.execute("UPDATE memory_capture_jobs SET proposals=? WHERE id='boundary-0'", (json.dumps(proposals),))
        fresh = capture(self.engine)
        self.assertNotEqual(fresh.relation_gaps(('s',))['frontier_sha256'], snapshot.relation_gaps(('s',))['frontier_sha256'])
        with self.assertRaisesRegex(ValueError, 'frontier changed'):
            _restore_frozen(self.engine, snapshot.descriptor())
        self.assertFalse(validate_publication(self.engine, fresh, context, root, check))

    def test_actual_permission_query_cannot_borrow_nav_or_relation_clearance(self):
        from prototypes.assertion_memory.question_routes import answer_reusable
        from prototypes.assertion_memory.semantic_views import contributions
        snapshot, _, result, root = self.assert_terminal_directory()
        check = receipt(self.engine, result['check_id'])
        self.assertFalse(answer_reusable(self.engine, root.view_id))
        with self.assertRaisesRegex(ModelError, 'navigation'):
            contributions(self.engine, {root.view_id: root}, {root.view_id: check}, snapshot)
        original = self.engine.model
        def malicious(request):
            if request.task == 'Plan memory retrieval for a question':
                return json.dumps({'reuse_view_id': root.view_id, 'related_view_ids': [], 'subquestions': [], 'reason': 'Try NAV as permission.'})
            return original(request)
        self.engine.model = malicious
        with self.assertRaisesRegex(ModelError, 'reused an obsolete'):
            self.engine.prepare_question('Is voltage gate use permitted now?', scopes=('s',), _snapshot=snapshot)
        def domain(request):
            if request.task == 'Plan memory retrieval for a question':
                return json.dumps({'reuse_view_id': None, 'related_view_ids': [root.view_id], 'subquestions': [], 'reason': 'Use source index only.'})
            return original(request)
        self.engine.model = domain
        answer = self.service.query('Is voltage gate use permitted now?', scope='s')
        self.assertIn(QUALIFICATION, answer['view']['summary'])
        self.assertEqual(answer['completeness'], 'incomplete')
        self.assertIsNone(answer['source_status']['processing_gaps_apply_to_query'])
        self.assertNotEqual(answer['view']['view_id'], root.view_id)
