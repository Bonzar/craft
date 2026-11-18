import json
import sqlite3
import unittest
from prototypes.assertion_memory.session_api import SessionAPI

class SessionAPITest(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:'); self.db.row_factory=sqlite3.Row
        self.db.executescript('''CREATE TABLE memory_raw_events(id TEXT,session_id TEXT,turn_id TEXT,prompt TEXT,actor TEXT,source TEXT,scope TEXT,recorded_at TEXT,metadata TEXT); CREATE TABLE memory_event_positions(event_id TEXT,position INTEGER); CREATE TABLE memory_assertions(assertion_id TEXT,metadata TEXT);''')
        for eid,sid,tid,mid,role,pos in [('e1','s1','t1','m1','user',0),('e2','s1','t1','m2','tool',1),('e3','s2','t2',None,'assistant',0)]:
            self.db.execute('INSERT INTO memory_raw_events VALUES(?,?,?,?,?,?,?,?,?)',(eid,sid,tid,'PRIVATE BODY',role,'chat://fixture','misleading-title','2026-01-01',json.dumps({'message_id':mid,'role':role})))
            self.db.execute('INSERT INTO memory_event_positions VALUES(?,?)',(eid,pos))
        self.db.execute('INSERT INTO memory_assertions VALUES(?,?)',('mixed',json.dumps({'session_id':'false','evidence_refs':[{'event_id':'e1','start':0,'end':2},{'event_id':'e3','start':0,'end':2}]})))
        self.db.execute('INSERT INTO memory_assertions VALUES(?,?)',('missing',json.dumps({'session_id':'false','evidence_refs':[{'event_id':'absent','start':0,'end':2}]})))
        self.db.commit(); self.db.execute('PRAGMA query_only=ON'); self.api=SessionAPI(self.db)
    def test_sessions_snapshot_and_no_titles_or_bodies(self):
        before=self.db.total_changes; page=self.api.sessions(limit=1)
        self.assertEqual(page['total'],2); self.assertEqual(page['sessions'][0]['message_count'],2)
        self.assertIsNone(page['sessions'][0]['title']); self.assertNotIn('PRIVATE',json.dumps(page))
        self.assertEqual(self.api.sessions(offset=page['next_offset'],snapshot_id=page['snapshot_id'])['sessions'][0]['session_id'],'s2')
        self.assertEqual(before,self.db.total_changes)
    def test_exact_lookup_no_turn_message_guess(self):
        self.assertEqual(self.api.messages('s1',turn_id='t1',message_id='m2')['messages'][0]['role'],'tool')
        self.assertEqual(self.api.messages('s2',message_id='t2')['total'],0)
        self.assertEqual(self.api.messages('absent')['total'],0)
        self.assertNotIn('PRIVATE',json.dumps(self.api.messages('s1')))
    def test_batch_real_origins_mixed_and_missing(self):
        result=self.api.provenance(['mixed','missing','e2','unknown'])['records']
        self.assertEqual(result[0]['session_ids'],['s1','s2']); self.assertEqual(result[0]['membership'],'mixed')
        self.assertEqual(result[1]['membership'],'unknown'); self.assertFalse(result[1]['complete'])
        self.assertEqual(result[2]['session_ids'],['s1']); self.assertEqual(result[3]['membership'],'unknown')
    def test_snapshot_excludes_new_session(self):
        page=self.api.sessions(limit=1)
        self.db.execute('PRAGMA query_only=OFF')
        self.db.execute("INSERT INTO memory_raw_events VALUES('new','s3','t','new body','user','x','x','2026','{}')")
        self.db.execute('PRAGMA query_only=ON')
        self.assertEqual(self.api.sessions(snapshot_id=page['snapshot_id'])['total'],2)
        self.assertEqual(self.api.sessions()['total'],3)
    def test_large_identifiers_have_bounded_pagination(self):
        self.db.execute('PRAGMA query_only=OFF')
        for i in range(20):
            self.db.execute('INSERT INTO memory_raw_events VALUES(?,?,?,?,?,?,?,?,?)',('long'+str(i),'z'*2000+str(i),'t','body','user','x','x','2026','{}'))
        self.db.execute('PRAGMA query_only=ON')
        page=self.api.sessions(limit=200)
        self.assertLess(len(json.dumps(page)),24000)
        self.assertIsNotNone(page['next_offset'])
    def test_bounds(self):
        for kwargs in [{'limit':True},{'limit':201},{'offset':-1},{'snapshot_id':-1}]:
            with self.assertRaises(ValueError):self.api.sessions(**kwargs)
        with self.assertRaises(ValueError):self.api.provenance(['a']*101)

if __name__=='__main__':unittest.main()
