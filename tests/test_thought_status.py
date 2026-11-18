"""Canonical diagnostics retain raw intake visibility without crossing scopes."""
import json
import unittest
from unittest.mock import patch

from prototypes.assertion_memory.storage import MemoryStore
from prototypes.assertion_memory.thought_service import ThoughtService


class ThoughtStatusTest(unittest.TestCase):
    def setUp(self):
        self.store = MemoryStore(':memory:')
        self.addCleanup(self.store.close)
        self.service = ThoughtService(self.store)

    def capture(self, index, scope='private'):
        return self.service.capture_message('fixture', str(index), 'fixture', 'user',
            'Source '+str(index), index, 'fixture://'+str(index), scope)['event']['id']

    def test_raw_job_counts_and_independent_scoped_pages(self):
        ids = [self.capture(index) for index in range(3)]
        with self.store.transaction(write=True) as db:
            db.execute("UPDATE memory_capture_jobs SET error='Fixture failure',proposals=? WHERE id=?",
                (json.dumps([{'kind':'interpretation','status':'unresolved','reason':'Check condition.'}]),ids[0]))
        page = self.service.status(ids[0], scope='private', offset=1, limit=1)
        self.assertEqual(page['event_count'], 3)
        self.assertEqual(page['pending_count'], 3)
        self.assertEqual(page['error_count'], 1)
        self.assertEqual(page['unresolved_count'], 1)
        self.assertEqual(page['job']['id'], ids[0])
        self.assertEqual(len(page['pending_jobs']), 1)
        self.assertEqual(page['pagination']['pending_jobs']['next_offset'], 2)
        self.assertEqual(page['errors'], [])
        self.assertEqual(page['pagination']['errors']['total'], 1)
        public = self.service.status(scope='public')
        self.assertEqual(public['event_count'], 0)
        self.assertEqual(public['pending_count'], 0)
        self.assertEqual(public['pending_jobs'], [])
        with self.assertRaises(ValueError):
            self.service.status(ids[0], scope='public')

    def test_query_scope_does_not_inherit_private_pending_or_errors(self):
        identity = self.capture(0)
        with self.store.transaction(write=True) as db:
            db.execute("UPDATE memory_capture_jobs SET error='Private failure' WHERE id=?", (identity,))
        public = self.service.query('What is public?', scope='public')
        self.assertEqual(public['event_count'], 0)
        self.assertEqual(public['pending_count'], 0)
        self.assertEqual(public['error_count'], 0)
        self.assertEqual(public['unresolved_count'], 0)
        self.assertEqual(public['processing_state'], 'idle')
        self.assertNotIn('Private failure', json.dumps(public))
        self.assertNotIn('private', json.dumps(public))

    def test_completed_unresolved_job_remains_visible_in_query(self):
        identity = self.capture(0)
        with self.store.transaction(write=True) as db:
            db.execute("UPDATE memory_capture_jobs SET status='completed',proposals=? WHERE id=?",
                (json.dumps([{'kind':'interpretation','status':'unresolved','reason':'Unclear condition.'}]),identity))
        result = self.service.query('What is known?', scope='private')
        self.assertEqual(result['pending_count'], 0)
        self.assertEqual(result['unresolved_count'], 1)
        self.assertEqual(result['processing_state'], 'needs_review')
        self.assertEqual(result['completeness'], 'incomplete')

    def test_oversize_job_uses_lossless_bounded_continuation(self):
        identity = self.capture(0)
        reason = 'Exact qualifier. '*4000
        with self.store.transaction(write=True) as db:
            db.execute('UPDATE memory_capture_jobs SET proposals=? WHERE id=?',
                (json.dumps([{'status':'unresolved','reason':reason}]),identity))
        page = self.service.status(identity, scope='private')
        self.assertFalse(page['response_complete'])
        self.assertLess(len(json.dumps(page)), 28000)
        cursor = next(item for item in page['continuations'] if item['arguments'].get('section')=='job')
        pieces=[]
        while cursor:
            chunk=self.service.status(**cursor['arguments'])
            pieces.append(chunk['content']);cursor=chunk['continuation']
        self.assertEqual(json.loads(''.join(pieces))['proposals'][0]['reason'],reason)
        result=self.service.query('What is known?',scope='public')
        self.assertEqual(result['unresolved_count'],0)

    def test_scoped_canonical_work_uses_only_queued_heads_and_exact_ancestry(self):
        private=self.capture(0)
        public=self.capture(1,'public')
        a=self.service.thoughts.publish('Private interpretation.',bases=[{'event_id':private}])
        b=self.service.thoughts.publish('Public interpretation.',bases=[{'event_id':public}])
        mixed=self.service.thoughts.publish('Mixed interpretation.',bases=[
            {'thought_id':a.id,'version':a.version},{'thought_id':b.id,'version':b.version}])
        self.service.work.enqueue([a,b,mixed])
        for index in range(12):self.service.thoughts.publish('Unqueued '+str(index))
        with self.store.transaction(write=True) as db:
            db.execute("UPDATE memory_capture_jobs SET status='completed'")
        with patch.object(self.service.thoughts,'list_thoughts',side_effect=AssertionError('whole corpus loaded')):
            scoped=self.service.status(scope='public')
        self.assertEqual(scoped['pending_count'],1)
        self.assertEqual(scoped['thought_work_counts'],{'pending':1})
        self.assertEqual(self.service.status()['pending_count'],3)
        self.assertEqual(self.service.status(scope='missing')['pending_count'],0)

    def test_unsupported_sections_and_invalid_arguments_are_rejected(self):
        for section in ('unknown','derived_overview','derived_overviews','view_names','policy_coverage'):
            with self.subTest(section=section),self.assertRaises(ValueError):
                self.service.status(section=section)
        for args in ({'scope':''},{'catalog_id':'unsupported'},{'offset':-1},{'limit':True}):
            with self.subTest(args=args),self.assertRaises(ValueError):self.service.status(**args)


if __name__=='__main__':unittest.main()
