import sqlite3
import unittest

from prototypes.assertion_memory.processing_control import Conflict
from prototypes.assertion_memory.queue_item_removal import (
    annotate, remove, source_preview, source_tombstoned, was_removed)


class Removal(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:')
        self.addCleanup(self.db.close)
        self.db.executescript("""
            PRAGMA foreign_keys=ON;
            CREATE TABLE memory_raw_events(id TEXT PRIMARY KEY,prompt TEXT,actor TEXT,metadata TEXT);
            CREATE TABLE memory_capture_jobs(
                id TEXT PRIMARY KEY,status,lease_token,lease_until,event_id REFERENCES memory_raw_events(id),
                phase,attempts,updated_at,available_at);
            CREATE TABLE memory_event_positions(event_id REFERENCES memory_raw_events(id),position INTEGER);
            CREATE TABLE memory_source_context_changes(event_id);
            CREATE TABLE memory_message_receipts(event_id REFERENCES memory_raw_events(id),payload TEXT);
            CREATE TABLE memory_extraction_runs(event_id REFERENCES memory_raw_events(id));
            CREATE TABLE memory_thought_work(thought_id TEXT PRIMARY KEY,status,lease_token,lease_until,
                desired_generation INTEGER,completed_generation INTEGER,desired_version INTEGER,action,attempts,available_at,completed_version);
            INSERT INTO memory_raw_events VALUES ('source-a','body <not html>','owner-record-57','{"role":"user"}'),
                ('active-a','active','automation-record','{"role":"assistant"}'),
                ('source-b','other','user','{}'),('prior-a','processed','user','{"role":["user"]}');
            INSERT INTO memory_capture_jobs VALUES ('source-a','waiting_model',NULL,NULL,'source-a','extract',0,'0','0'),
                ('active-a','running','lease','0','active-a','extract',0,'0','0'),
                ('source-b','pending',NULL,NULL,'source-b','extract',0,'0','0'),
                ('prior-a','pending',NULL,NULL,'prior-a','extract',0,'0','0');
            INSERT INTO memory_extraction_runs VALUES ('prior-a');
            INSERT INTO memory_event_positions VALUES ('source-a',42),('source-b',43);
            INSERT INTO memory_source_context_changes VALUES ('source-a');
            INSERT INTO memory_message_receipts VALUES ('source-a','source body receipt');
            INSERT INTO memory_thought_work VALUES ('thought-a','pending',NULL,NULL,4,2,3,'refresh',0,'0',2);
        """)

    def hints(self,q,id):
        item={'id':id}
        annotate(self.db,{'queues':[{'id':q,'items':[item]}]})
        return item

    def remove(self,q,id,rev,stage='test'):
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            return remove(self.db,stage=stage,queue_id=q,item_id=id,expected_revision=rev,now='now')

    def test_exact_unprocessed_item_deletes_replica_and_scaffolding_only(self):
        hint=self.hints('source','source-a')
        self.assertNotIn('source_preview',hint)
        read=source_preview(self.db,stage='test',queue_id='source',item_id='source-a',expected_revision=hint['preview_revision'])
        self.assertEqual((read['source_role'],read['source_preview']),('user','body <not html>'))
        self.assertEqual(self.remove('source','source-a',hint['remove_revision'])['effect'],'source_and_queue')
        for table in ('memory_raw_events','memory_capture_jobs','memory_event_positions','memory_source_context_changes','memory_message_receipts'):
            key='id' if table in ('memory_raw_events','memory_capture_jobs') else 'event_id'
            self.assertFalse(self.db.execute(f'SELECT 1 FROM {table} WHERE {key}=?',('source-a',)).fetchone())
        self.assertTrue(source_tombstoned(self.db,'source-a'))
        self.assertFalse(self.db.execute('PRAGMA foreign_key_check').fetchall())
        self.assertTrue(self.db.execute("SELECT 1 FROM memory_raw_events WHERE id='source-b'").fetchone())

    def test_preview_role_only_from_canonical_metadata(self):
        for item,expected in [('source-a','user'),('active-a','assistant'),('source-b',None),('prior-a',None)]:
            with self.subTest(item=item):
                self.assertEqual(source_preview(self.db,stage='test',queue_id='source',item_id=item,
                    expected_revision=self.hints('source',item)['preview_revision'])['source_role'],expected)

    def test_in_flight_dependent_stale_and_other_lane_rejected(self):
        self.assertNotIn('remove_revision',self.hints('source','active-a'))
        self.assertIn('preview_revision',self.hints('source','active-a'))
        with self.assertRaises(Conflict):
            self.remove('source','prior-a',self.hints('source','prior-a')['remove_revision'])
        rev=self.hints('source','source-b')['remove_revision']
        with self.assertRaises(ValueError):
            self.remove('source','source-b',rev,'prod')
        self.db.execute("UPDATE memory_capture_jobs SET updated_at='1' WHERE id='source-b'")
        self.db.commit()
        with self.assertRaises(Conflict):
            self.remove('source','source-b',rev)
        self.assertFalse(source_tombstoned(self.db,'source-b'))

    def test_maintenance_only_observed_generation_and_preserves_record(self):
        self.assertEqual(self.remove('maintenance','thought-a',self.hints('maintenance','thought-a')['remove_revision'])['effect'],'queue_only')
        self.assertEqual(self.db.execute('SELECT status,completed_version,completed_generation FROM memory_thought_work').fetchone(),
                         ('cancelled',2,4))
        self.assertTrue(was_removed(self.db,'thought-a',3))
        self.assertTrue(was_removed(self.db,'thought-a',4))
        self.assertFalse(was_removed(self.db,'thought-a',5))
        self.db.execute("UPDATE memory_thought_work SET status='pending',desired_generation=5")
        self.assertIn('remove_revision',self.hints('maintenance','thought-a'))

    def test_real_capture_queue_fk_waiting_model_and_recapture(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from prototypes.assertion_memory.queue import CaptureQueue
        from prototypes.assertion_memory.storage import MemoryStore
        with TemporaryDirectory() as temp, MemoryStore(Path(temp)/'memory.sqlite3') as store:
            q=CaptureQueue(store)
            args=('test-session','first-turn','synthetic test fixture')
            kwargs=dict(source='test',actor='machine-principal',metadata={'role':'user'},position=1)
            e=q.capture_event(*args,**kwargs)
            event=e.id
            self.assertEqual(q.get(event).status,'waiting_model')
            with store.transaction(write=True) as db:
                self.assertEqual(db.execute('PRAGMA foreign_keys').fetchone()[0],1)
                item={'id':event}
                annotate(db,{'queues':[{'id':'source','items':[item]}]})
                self.assertEqual(source_preview(db,stage='test',queue_id='source',item_id=event,
                    expected_revision=item['preview_revision'])['source_role'],'user')
                self.assertEqual(remove(db,stage='test',queue_id='source',item_id=event,
                                        expected_revision=item['remove_revision'],now='now')['effect'],'source_and_queue')
                self.assertFalse(db.execute('PRAGMA foreign_key_check').fetchall())
            self.assertIsNone(q.get(event))
            self.assertIsNone(q.get_event(event))
            with self.assertRaisesRegex(ValueError,'removed before processing'):
                q.capture_event(*args,**kwargs)
