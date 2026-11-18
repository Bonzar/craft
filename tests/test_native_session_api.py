"""Explicit native provenance can split a legacy logical capture group."""
import json
import sqlite3
import unittest
from prototypes.assertion_memory.session_api import SessionAPI

class NativeSessionAPITest(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:');self.db.row_factory=sqlite3.Row
        self.db.executescript('CREATE TABLE memory_raw_events(id TEXT,session_id TEXT,turn_id TEXT,prompt TEXT,actor TEXT,source TEXT,scope TEXT,recorded_at TEXT,metadata TEXT);CREATE TABLE memory_event_positions(event_id TEXT,position INTEGER);CREATE TABLE memory_assertions(assertion_id TEXT,metadata TEXT);')
        self.add('one','capture-group','native-one','m1',0)
        self.add('two','capture-group','native-two','m2',1)
        self.add('unknown','capture-group',None,'m3',2)
        self.add('same-native-other-capture','capture-other','native-one','m4',0)
        self.add('unknown-other','capture-unknown',None,'m5',0)
        for key,event_ids in [('mixed-native',['one','two']),('same-native',['one','same-native-other-capture']),('mixed-fallback',['one','unknown'])]:
            self.db.execute('INSERT INTO memory_assertions VALUES(?,?)',(key,json.dumps({'native_session_id':'do-not-trust-assertion','evidence_refs':[{'event_id':i,'start':0,'end':2} for i in event_ids]})))
        self.db.commit();self.db.execute('PRAGMA query_only=ON');self.api=SessionAPI(self.db)
    def add(self,event,capture,native,message,position):
        metadata={'role':'user','message_id':message}
        if native is not None:metadata['native_session_id']=native
        self.db.execute('INSERT INTO memory_raw_events VALUES(?,?,?,?,?,?,?,?,?)',(event,capture,'turn','Synthetic private body','user','codex://native-wrong','scope-not-title','2026-01-01',json.dumps(metadata)))
        self.db.execute('INSERT INTO memory_event_positions VALUES(?,?)',(event,position))
    def test_catalog_splits_two_native_chats_and_retains_unknown_fallback(self):
        before=self.db.total_changes;result=self.api.sessions();rows={r['session_id']:r for r in result['sessions']}
        self.assertEqual(result['total'],4)
        self.assertEqual(rows['native-one']['message_count'],2)
        self.assertEqual(rows['native-one']['capture_session_count'],2)
        self.assertIsNone(rows['native-one']['capture_session_id'])
        self.assertEqual(rows['native-one']['native_session_id'],'native-one')
        self.assertEqual(rows['native-one']['session_id_basis'],'metadata.native_session_id')
        self.assertEqual(rows['capture-group']['message_count'],1)
        self.assertEqual(rows['capture-group']['session_id_basis'],'stored_session_id_fallback')
        self.assertIsNone(rows['capture-group']['native_session_id'])
        self.assertTrue(all(r['title'] is None for r in rows.values()))
        self.assertEqual(before,self.db.total_changes)
    def test_exact_native_and_legacy_capture_lookup_with_separate_provenance(self):
        messages=self.api.messages('native-two',turn_id='turn',message_id='m2')['messages']
        self.assertEqual(len(messages),1);row=messages[0]
        self.assertEqual(row['session_id'],'native-two')
        self.assertEqual(row['source_session_id'],'native-two')
        self.assertEqual(row['capture_session_id'],'capture-group')
        self.assertEqual(row['native_session_id'],'native-two')
        self.assertEqual(self.api.messages('capture-group')['total'],3)
        self.assertEqual(self.api.messages("native-two' OR 1=1 --")['total'],0)
        self.assertEqual(self.api.messages('native-wrong')['total'],0)
    def test_provenance_uses_raw_native_anchors_not_capture_group_or_claim_metadata(self):
        records=self.api.provenance(['mixed-native','same-native','mixed-fallback','unknown'])['records']
        self.assertEqual(records[0]['session_ids'],['native-one','native-two'])
        self.assertEqual(records[0]['source_session_ids'],['native-one','native-two'])
        self.assertEqual(records[0]['capture_session_ids'],['capture-group'])
        self.assertEqual(records[0]['membership'],'mixed')
        self.assertEqual(records[1]['session_ids'],['native-one'])
        self.assertEqual(records[1]['capture_session_ids'],['capture-group','capture-other'])
        self.assertEqual(records[1]['membership'],'single')
        self.assertEqual(records[2]['session_ids'],['capture-group','native-one'])
        self.assertEqual(records[2]['membership'],'mixed')
        self.assertEqual(records[3]['native_session_ids'],[])
        self.assertEqual(records[3]['session_ids'],['capture-group'])
    def test_nonstring_or_empty_native_id_remains_labeled_stored_fallback(self):
        self.db.execute('PRAGMA query_only=OFF')
        self.add('invalid-number','capture-number',123,'m6',0)
        self.add('invalid-empty','capture-empty','   ','m7',0)
        self.db.execute('PRAGMA query_only=ON')
        for key in ('capture-number','capture-empty'):
            row=self.api.messages(key)['messages'][0]
            self.assertIsNone(row['native_session_id'])
            self.assertEqual(row['session_id_basis'],'stored_session_id_fallback')
        self.assertEqual(self.api.messages('123')['total'],0)
    def test_same_literal_native_and_fallback_id_catalog_is_explicitly_mixed(self):
        self.db.execute('PRAGMA query_only=OFF')
        self.add('identity-collision','native-one',None,'m6',0)
        self.db.execute('PRAGMA query_only=ON')
        row=next(r for r in self.api.sessions()['sessions'] if r['session_id']=='native-one')
        self.assertEqual(row['session_id_basis'],'mixed_native_and_stored_fallback')
        self.assertEqual(row['native_message_count'],2)
        self.assertEqual(row['fallback_message_count'],1)
    def test_watermark_pagination_preserves_source_catalog_and_native_message_slice(self):
        first=self.api.sessions(limit=1);snapshot=first['snapshot_id']
        self.db.execute('PRAGMA query_only=OFF');self.add('later','capture-group','native-three','new',3);self.db.execute('PRAGMA query_only=ON')
        ids=[first['sessions'][0]['session_id']];offset=first['next_offset']
        while offset is not None:
            page=self.api.sessions(limit=1,offset=offset,snapshot_id=snapshot);ids.extend(r['session_id'] for r in page['sessions']);offset=page['next_offset']
        self.assertEqual(len(ids),4);self.assertNotIn('native-three',ids)
        self.assertEqual(self.api.messages('native-three',snapshot_id=snapshot)['total'],0)
        self.assertEqual(self.api.messages('native-three')['total'],1)

if __name__=='__main__':unittest.main()
