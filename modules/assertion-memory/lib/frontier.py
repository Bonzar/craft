"""Durable operational reports and per-stream delivery receipts, never source order."""
import json
import base64
import time
import uuid


def _dump(value): return json.dumps(value, sort_keys=True, separators=(',', ':'))


def initialize(db):
    db.executescript('''
        CREATE TABLE IF NOT EXISTS transport_identity (name TEXT PRIMARY KEY,value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS transport_streams (session_id TEXT PRIMARY KEY,data TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS delivery_sequences (session_id TEXT,message_id TEXT,seq INTEGER NOT NULL,receipt TEXT,
            PRIMARY KEY(session_id,message_id),UNIQUE(session_id,seq));
    ''')
    with db:
        db.execute('INSERT OR IGNORE INTO transport_identity VALUES (?,?)',('producer_id','jarvis:'+str(uuid.uuid4())))
        # Upgrade existing raw receipts without rewriting immutable arguments. A raw
        # receipt alone does not prove that this operational stream accepted seq.
        migrated=db.execute("SELECT value FROM transport_identity WHERE name='delivery_sequence_schema'").fetchone()
        if migrated is None:
            rows=db.execute('SELECT session_id,message_id FROM messages ORDER BY rowid').fetchall()
            for row in rows: reserve(db,row['session_id'],row['message_id'])
            db.execute('INSERT INTO transport_identity VALUES (?,?)',('delivery_sequence_schema','1'))


def reserve(db,session,message):
    old=db.execute('SELECT seq FROM delivery_sequences WHERE session_id=? AND message_id=?',(session,message)).fetchone()
    if old:return old['seq']
    seq=db.execute('SELECT COALESCE(MAX(seq),0)+1 FROM delivery_sequences WHERE session_id=?',(session,)).fetchone()[0]
    db.execute('INSERT INTO delivery_sequences(session_id,message_id,seq) VALUES (?,?,?)',(session,message,seq))
    return seq


class Coordinator:
    def __init__(self,store,client,*,clock=time.time):
        self.store,self.db,self.client,self.clock=store,store.db,client,clock
        self.reconciled=set()

    def local(self,session):
        row=self.db.execute('SELECT data FROM transport_streams WHERE session_id=?',(session,)).fetchone()
        if row:return json.loads(row['data'])
        producer=self.db.execute("SELECT value FROM transport_identity WHERE name='producer_id'").fetchone()[0]
        value={'session_id':session,'producer_id':producer,'stream_id':None,'epoch':None,'report_seq':0,
            'server_observed_seq':0,'server_acked_through':0,'pending_report':None,'last_report_at':0,
            'scan_state':'unknown','gap_count':0,'lifecycle':'active','last_snapshot':None,'retry_at':0,'delay':1,'error':None}
        with self.db:self._save(value)
        return value

    def _save(self,value):
        self.db.execute('INSERT INTO transport_streams VALUES (?,?) ON CONFLICT(session_id) DO UPDATE SET data=excluded.data',
            (value['session_id'],_dump(value)))

    def observed(self,session):
        return self.db.execute('SELECT COALESCE(MAX(seq),0) FROM delivery_sequences WHERE session_id=?',(session,)).fetchone()[0]

    def observe(self,session,result):
        value=self.local(session);state=self.store.session(session) or {};summary=self.store.summary(session)
        gap_count=len(state.get('gaps',[]))+int(bool(state.get('blocked')))
        if gap_count:scan='gapped'
        elif result.get('status')!='registered':scan='unknown'
        elif result.get('backlog') or summary['pending_source_proof'] or state.get('skip_partial') or (state.get('turn') or {}).get('assistant'):
            scan='catching_up'
        else:scan='current'
        if self.observed(session)>value['server_observed_seq'] or (scan,gap_count)!=(value['scan_state'],value['gap_count']):
            value['lifecycle']='active'
        value.update(scan_state=scan,gap_count=gap_count)
        with self.db:self._save(value)

    def _accept(self,value,response):
        if (not isinstance(response,dict) or response.get('stream_id')!=value['stream_id']
                or response.get('epoch')!=value['epoch']):raise ValueError('Unmatched stream receipt')
        for field in ('report_seq','observed_seq','acked_through'):
            if type(response.get(field)) is not int or response[field]<0:raise ValueError('Invalid stream frontier')
        if response['acked_through']>response['observed_seq']:raise ValueError('Invalid acknowledged prefix')
        if response['observed_seq']<value['server_observed_seq'] or response['acked_through']<value['server_acked_through']:
            raise ValueError('Remote stream frontier regressed; review required')
        value['report_seq']=max(value['report_seq'],response['report_seq'])
        value['server_observed_seq']=response['observed_seq'];value['server_acked_through']=response['acked_through']

    def _register(self,value):
        response=self.client.call('memory_ingest_register',{'producer_id':value['producer_id'],'session_id':value['session_id']})
        if not value['stream_id']:
            if not isinstance(response.get('stream_id'),str) or not isinstance(response.get('epoch'),str):raise ValueError('Invalid stream registration')
            value['stream_id'],value['epoch']=response['stream_id'],response['epoch']
        self._accept(value,response)
        if value['server_observed_seq']>self.observed(value['session_id']):
            raise ValueError('Server observed sequences missing from local outbox; review required')
        with self.db:self._save(value)
        self.reconciled.add(value['session_id'])

    def _report(self,value,*,force=False):
        snapshot={'observed_seq':max(self.observed(value['session_id']),value['server_observed_seq']),
            'scan_state':value['scan_state'],'gap_count':value['gap_count'],'lifecycle':value['lifecycle']}
        pending=value.get('pending_report')
        if pending is None:
            if not force and snapshot==value['last_snapshot'] and self.clock()-value['last_report_at']<20:return
            pending={'stream_id':value['stream_id'],'epoch':value['epoch'],'report_seq':value['report_seq']+1,**snapshot}
            value.update(report_seq=pending['report_seq'],pending_report=pending)
            with self.db:self._save(value)  # durable exact request before sending
        response=self.client.call('memory_ingest_report',pending)
        self._accept(value,response)
        if response['report_seq']<pending['report_seq'] or response['observed_seq']<pending['observed_seq']:
            raise ValueError('Report receipt does not cover requested frontier')
        value.update(pending_report=None,last_snapshot={k:pending[k] for k in snapshot},last_report_at=self.clock())
        with self.db:self._save(value)
        # A replay does not renew the remote lease. Follow it with a fresh monotone
        # report, also publishing local changes made while the old ACK was lost.
        if pending['report_seq']!=value['report_seq'] or snapshot!=value['last_snapshot']:
            self._report(value,force=True)

    def _capture(self,value,row):
        arguments=json.loads(row['arguments'])
        for aid in arguments.get('artifact_ids',[]):
            artifact=self.db.execute('SELECT * FROM artifacts WHERE artifact_id=?',(aid,)).fetchone()
            if artifact is None:raise ValueError('Missing local attachment bytes; message remains pending')
            if artifact['receipt'] is None:
                try:
                    response=self.client.call('memory_store_artifact',{'data_base64':base64.b64encode(artifact['data']).decode('ascii'),
                        'mime_type':artifact['mime_type'],'sha256':artifact['sha256']})
                    expected={'artifact_id':aid,'sha256':artifact['sha256'],'size_bytes':artifact['size_bytes'],'mime_type':artifact['mime_type'],'not_interpreted':True}
                    if response!=expected:raise ValueError('Unmatched artifact upload receipt')
                    with self.db:self.db.execute('UPDATE artifacts SET receipt=?,attempts=attempts+1,error=NULL WHERE artifact_id=?',(_dump(response),aid))
                except Exception as failure:
                    with self.db:self.db.execute('UPDATE artifacts SET attempts=attempts+1,error=? WHERE artifact_id=?',(type(failure).__name__,aid))
                    raise
        delivery={'stream_id':value['stream_id'],'epoch':value['epoch'],'seq':row['seq']}
        receipt=self.client.call('memory_capture_message',{**arguments,'delivery':delivery})
        event=receipt.get('event',{}) if isinstance(receipt,dict) else {}
        mid=event.get('message_id') or event.get('metadata',{}).get('message_id')
        if (not event.get('id') or event.get('session_id')!=arguments['session_id']
                or mid!=arguments['message_id'] or event.get('source')!=arguments['source']):raise ValueError('Unmatched message receipt')
        remote=receipt.get('delivery')
        self._accept(value,remote)
        if remote['observed_seq']<row['seq']:raise ValueError('Delivery receipt lacks observed membership')
        with self.db:
            self.db.execute('UPDATE delivery_sequences SET receipt=? WHERE session_id=? AND message_id=?',
                (_dump(receipt),arguments['session_id'],arguments['message_id']))
            self.db.execute("UPDATE messages SET status='acknowledged',attempts=attempts+1,error=NULL,receipt=? WHERE session_id=? AND message_id=?",
                (_dump(receipt),arguments['session_id'],arguments['message_id']))
            self._save(value)

    def tick(self,limit=100):
        result={'attempted':0,'acknowledged':0,'stream_errors':0}
        for session in self.store.sessions():
            value=self.local(session['session_id'])
            if (not session.get('watching',True) and value['pending_report'] is None
                    and not self.db.execute('SELECT 1 FROM delivery_sequences WHERE session_id=? AND receipt IS NULL LIMIT 1',
                        (session['session_id'],)).fetchone()):continue
            if value['retry_at']>self.clock():continue
            try:
                if value['session_id'] not in self.reconciled:self._register(value)
                if value['lifecycle']=='closed' and value['pending_report'] is None:continue
                replay=bool(value.get('pending_report'))
                self._report(value)
                if replay:self._report(value,force=True)
                rows=self.db.execute('SELECT m.*,d.seq FROM messages m JOIN delivery_sequences d USING(session_id,message_id) '
                    'WHERE m.session_id=? AND d.receipt IS NULL ORDER BY d.seq LIMIT ?',
                    (value['session_id'],limit-result['attempted'])).fetchall()
                for row in rows:
                    result['attempted']+=1
                    try:self._capture(value,row)
                    except Exception:
                        with self.db:self.db.execute('UPDATE messages SET attempts=attempts+1,error=? WHERE session_id=? AND message_id=?',
                            ('DeliveryReceiptError',row['session_id'],row['message_id']))
                        raise
                    result['acknowledged']+=1
                value.update(error=None,delay=1,retry_at=0)
                with self.db:self._save(value)
            except Exception as failure:
                # Preserve pending exact report and every unacknowledged raw body.
                value=self.local(session['session_id'])
                value.update(error=type(failure).__name__,retry_at=self.clock()+value['delay'],delay=min(60,value['delay']*2))
                with self.db:self._save(value)
                self.reconciled.discard(value['session_id']);result['stream_errors']+=1
            if result['attempted']>=limit:break
        return result

    def close(self,session):
        value=self.local(session)
        missing=self.db.execute('SELECT COUNT(*) FROM delivery_sequences WHERE session_id=? AND receipt IS NULL',(session,)).fetchone()[0]
        if (missing or value['scan_state']!='current' or value['gap_count']
                or value['server_acked_through']!=self.observed(session)):raise ValueError('Stream is not cleanly acknowledged/current')
        if session not in self.reconciled:self._register(value)
        value['lifecycle']='closed';self._report(value,force=True)
