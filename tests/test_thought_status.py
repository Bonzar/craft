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
            db.execute("UPDATE memory_thought_work SET error='Private maintenance error' WHERE thought_id=?",(a.id,))
        with patch.object(self.service.thoughts,'list_thoughts',side_effect=AssertionError('whole corpus loaded')):
            scoped=self.service.status(scope='public')
        self.assertEqual(scoped['pending_count'],1)
        self.assertEqual(scoped['thought_work_counts'],{'pending':1})
        self.assertEqual(scoped['error_count'],0)
        self.assertNotIn('Private maintenance error',json.dumps(scoped))
        self.assertNotIn('thought_count',scoped)
        with self.store.transaction(write=True) as db:
            db.execute("UPDATE memory_thought_work SET status='completed' WHERE thought_id=?",(a.id,))
        self.assertNotIn('completed',self.service.status(scope='public')['thought_work_counts'])
        self.assertEqual(self.service.status()['pending_count'],2)
        self.assertEqual(self.service.status(scope='missing')['pending_count'],0)

    def test_canonical_work_errors_are_bounded_and_recoverable_by_job_id(self):
        identity=self.capture(0,'public')
        thought=self.service.thoughts.publish('Public interpretation.',bases=[{'event_id':identity}])
        self.service.work.enqueue([thought])
        reason='Exact maintenance failure. '*4000
        with self.store.transaction(write=True) as db:
            db.execute("UPDATE memory_capture_jobs SET status='completed'")
            db.execute("UPDATE memory_thought_work SET error=? WHERE thought_id=?",(reason,thought.id))
        page=self.service.status(scope='public')
        self.assertEqual(page['pending_count'],1)
        self.assertEqual(page['raw_pending_count'],0)
        self.assertEqual(page['error_count'],1)
        self.assertEqual(page['pagination']['pending_jobs']['total'],0)
        self.assertEqual(page['pagination']['thought_pending_jobs']['total'],1)
        self.assertFalse(page['response_complete'])
        cursor=next(item for item in page['continuations'] if item['arguments'].get('event_id')==thought.id)
        pieces=[]
        while cursor:
            chunk=self.service.status(**cursor['arguments'])
            pieces.append(chunk['content']);cursor=chunk['continuation']
        self.assertEqual(json.loads(''.join(pieces))['error'],reason)

    def test_unrequested_canonical_error_bodies_are_not_loaded(self):
        import sqlite3
        thoughts=[]
        for index in range(2):
            identity=self.capture(index,'public')
            thoughts.append(self.service.thoughts.publish('Interpretation '+str(index),bases=[{'event_id':identity}]))
        self.service.work.enqueue(thoughts)
        reason='Unrequested oversized maintenance error. '*4000
        with self.store.transaction(write=True) as db:
            db.execute("UPDATE memory_capture_jobs SET status='completed'")
            for index,thought in enumerate(thoughts):
                db.execute('UPDATE memory_thought_work SET available_at=?,error=? WHERE thought_id=?',
                    (str(index),reason if index==0 else None,thought.id))
        loaded=[]
        def observe(cursor,values):
            loaded.extend(True for value in values if isinstance(value,str) and value==reason)
            return sqlite3.Row(cursor,values)
        self.store._connection.row_factory=observe
        for scope in (None,'public'):
            page=self.service.status(scope=scope,offset=1,limit=1)
            self.assertEqual(page['error_count'],1)
            self.assertEqual(page['thought_errors'],[])
            self.assertEqual(len(page['thought_pending_jobs']),1)
        self.assertEqual(loaded,[])
        page=self.service.status(thoughts[0].id,scope='public',section='job')
        pieces=[page['content']]
        while page['continuation']:
            page=self.service.status(**page['continuation']['arguments']);pieces.append(page['content'])
        self.assertEqual(json.loads(''.join(pieces))['error'],reason)
        self.assertTrue(loaded)

    def test_query_diagnostic_fallback_preserves_answer_and_existing_continuations(self):
        from prototypes.assertion_memory.thought_status import query_diagnostics
        answer_cursor={'tool':'memory_source','arguments':{'record_id':'answer','section':'text'}}
        job_cursor={'tool':'memory_status','arguments':{'event_id':'job','section':'job'}}
        health={'error_count':1,'pending_count':1,'unresolved_count':0,'response_complete':False,
                'continuations':[job_cursor]}
        result={'answer':'Retained answer.','answer_continuations':[answer_cursor]}
        with patch('prototypes.assertion_memory.thought_status.snapshot',return_value=health), \
             patch('prototypes.assertion_memory.thought_status._fits',side_effect=[False,True]):
            page=query_diagnostics(self.service,result,'public')
        self.assertEqual(page['answer_continuations'],[answer_cursor])
        self.assertIn(job_cursor,page['continuations'])
        self.assertIn({'tool':'memory_status','arguments':{'scope':'public','offset':0,'limit':100}},page['continuations'])

    def test_intentional_source_exclusion_is_not_a_processing_error(self):
        captured=self.service.capture_message('fixture','tool','fixture-tool','tool','Tool result.',0,
            'codex://fixture/tool','public')
        self.assertEqual(captured['job']['status'],'completed')
        self.assertEqual(captured['job']['phase'],'excluded')
        self.assertFalse(captured['semantic_admission']['allowed'])
        state=self.service.status(captured['event']['id'],scope='public')
        self.assertEqual(state['error_count'],0)
        self.assertEqual(state['errors'],[])
        self.assertIn('automatic_codex_tool',state['job']['error'])
        self.assertEqual(state['pagination']['errors']['total'],0)
        answer=self.service.query('What is public?',scope='public')
        self.assertEqual(answer['processing_state'],'idle')
        self.assertEqual(answer['error_count'],0)
        self.assertEqual(answer['errors'],[])
        ordinary=self.capture(1,'public')
        with self.store.transaction(write=True) as db:
            db.execute("UPDATE memory_capture_jobs SET status='completed',error='Real completed failure',proposals=? WHERE id=?",
                (json.dumps([{'status':'unresolved','reason':'Actual unresolved interpretation.'}]),ordinary))
        state=self.service.status(scope='public')
        self.assertEqual(state['error_count'],1)
        self.assertEqual([row['job_id'] for row in state['errors']],[ordinary])
        self.assertEqual(state['unresolved_count'],1)

    def test_unsupported_sections_and_invalid_arguments_are_rejected(self):
        for section in ('unknown','derived_overview','derived_overviews','view_names','policy_coverage'):
            with self.subTest(section=section),self.assertRaises(ValueError):
                self.service.status(section=section)
        for args in ({'scope':''},{'catalog_id':'unsupported'},{'offset':-1},{'limit':True}):
            with self.subTest(args=args),self.assertRaises(ValueError):self.service.status(**args)


if __name__=='__main__':unittest.main()
