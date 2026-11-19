"""Synthetic immutable ledger publications, without a source engine instance."""
import json
import sqlite3
from test_service import SCHEMA, EARLY, LATER

BEFORE='2025-02-01T00:00:00.000000+00:00'
AFTER='2025-04-01T00:00:00.000000+00:00'
SUMMARY='Saved blue claim [blue]; retained round [round]. 😀'
CURRENT={'mode':'current','at':None,'history':False,'undated':'separate'}


def add_node(c, key, valid=EARLY):
    meta={'event_id':'event','source_span':{'start':0,'end':4}}
    c.execute('INSERT INTO memory_assertions VALUES(?,?,?,?,?,?,?,?,?)',
              (key,key,'actor','saved','session',valid,EARLY,'s',json.dumps(meta)))


def publish(c, members, decisions, status='accepted_complete', completed=LATER, bump=0):
    proof={'status':'accepted','verifier':'synthetic-independent','reason':'exact source check',
           'evidence_refs':[{'event_id':'event','start':0,'end':4}]}
    revision={'schema':'source-interpretation/v1','event_id':'event','protocol':'synthetic-v1',
              'input_fingerprint':'fixture','status':status,'assertion_ids':members,
              'reconciliation':[{'assertion_id':key,'decision':decision,'verification':proof}
                                for key,decision in decisions.items()]}
    c.execute('INSERT INTO memory_extraction_runs(event_id,result,completed_at) VALUES(?,?,?)',
              ('event',json.dumps({'source_revision':revision}),completed))
    if bump:
        c.execute('UPDATE memory_state SET version=version+?',(bump,))


def fixture(path, revised=True):
    with sqlite3.connect(path) as c:
        c.executescript(SCHEMA)
        c.execute('CREATE TABLE memory_extraction_runs(id INTEGER PRIMARY KEY,event_id TEXT,result TEXT,completed_at TEXT)')
        for key,valid in [('blue',None),('round','2030-01-01T00:00:00+00:00'),('green',EARLY),('direct',None)]:
            add_node(c,key,valid)
        c.execute('INSERT INTO memory_raw_events VALUES(?,?,?,?,?,?,?,?,?)',
                  ('event','session','turn','blue round green 😀','actor','saved','s',EARLY,'{}'))
        c.execute('INSERT INTO memory_event_positions VALUES(?,?)',('event',0))
        c.execute('INSERT INTO memory_extraction_runs(event_id,result,completed_at) VALUES(?,?,?)',
                  ('event',json.dumps({'assertion_ids':['blue','round']}),EARLY))
        for key in ['blue','green','direct']:
            c.execute('INSERT INTO memory_edges VALUES(?,?,?,?,?,?)',(key+'-edge',key,'round','supports',EARLY,'{}'))
        snapshot={'evidence':[{'id':'blue'},{'id':'round'}],'summary':SUMMARY}
        c.execute('INSERT INTO memory_views VALUES(?,?,?,?,?,?)',('view',json.dumps({'title':'Saved view'}),1,1,None,1))
        c.execute('INSERT INTO memory_view_versions VALUES(?,?,?,?)',(1,'view',json.dumps(snapshot),EARLY))
        c.execute('INSERT INTO memory_view_checks VALUES(?,?,?,?,?,?)',(1,'view',1,1,EARLY,'current'))
        if revised:
            publish(c,['round','green'],{'blue':'withdrawn','round':'retained','green':'introduced'})


def ids(graph):
    with graph.connect() as c:
        return {r[0] for r in c.execute('SELECT id FROM nodes')}


def logical_source(path):
    with sqlite3.connect(path) as c:
        return '\n'.join(c.iterdump())
