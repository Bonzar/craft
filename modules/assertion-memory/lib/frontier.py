"""Durable operational reports and per-stream delivery receipts, never source order."""
from datetime import datetime, timezone
import json
import re
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
        self.last_probe={}

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

    def _wire_observed(self,value):
        seq=self.observed(value['session_id'])-value.get('reset_seq_floor',0)
        if seq<0:raise ValueError('Invalid local reset sequence floor')
        return seq

    def observe(self,session,result):
        value=self.local(session);state=self.store.session(session) or {};summary=self.store.summary(session)
        gap_count=len(state.get('gaps',[]))+int(bool(state.get('blocked')))
        if gap_count:scan='gapped'
        elif result.get('status')!='registered':scan='unknown'
        elif result.get('backlog') or summary['pending_source_proof'] or state.get('skip_partial') or summary['pending_turn_completion']:
            scan='catching_up'
        else:scan='current'
        if self._wire_observed(value)>value['server_observed_seq'] or (scan,gap_count)!=(value['scan_state'],value['gap_count']):
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

    def _transition_reset(self,value,response):
        from .native_capture import arm_database_reset_boundary
        from .outbox import lifecycle_guard
        previous={'previous_stream_id':value['stream_id'],'previous_epoch':value['epoch'],
                  'producer_id':value['producer_id'],'session_id':value['session_id']}
        marker=response.get('database_clear_reset')
        if (type(marker) is not dict or marker.keys()!=previous.keys()|{'protocol','generation','cleared_at'}
                or marker.get('protocol')!='owner-clear-v1'
                or any(marker.get(k)!=v for k,v in previous.items())
                or any(response.get(k)!=value[k] for k in ('producer_id','session_id'))):
            raise ValueError('Unbound database reset receipt')
        generation=marker['generation']
        cleared=marker['cleared_at']
        if (not isinstance(generation,str) or not re.fullmatch('[0-9a-f]{32}',generation)
                or generation=='0'*32 or generation==value.get('database_clear_generation')
                or not isinstance(cleared,str) or not cleared.endswith(('Z','+00:00'))
                or datetime.fromisoformat(cleared.removesuffix('Z')+'+00:00' if cleared.endswith('Z') else cleared).utcoffset()!=timezone.utc.utcoffset(None)):
            raise ValueError('Invalid database reset receipt')
        for k in ('stream_id','epoch'):
            ident=response.get(k)
            if not isinstance(ident,str) or str(uuid.UUID(ident,version=4))!=ident or ident==value[k]:
                raise ValueError('Invalid replacement stream identity')
        if any(type(response.get(k)) is not int or response[k]!=0
               for k in ('report_seq','observed_seq','acked_through')):
            raise ValueError('Unexpected replacement stream frontier')
        session=value['session_id']
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            persisted=self.local(session)
            if any(value[k]!=persisted[k] for k in ('producer_id','stream_id','epoch')):
                raise ValueError('Concurrent stream reset')
            with lifecycle_guard(self.store.root,session):
                state=self.store.session(session)
                if state is None:raise ValueError('Missing enrolled reset source')
                fenced=arm_database_reset_boundary(state,clock_ns=lambda:int(self.clock()*1e9))
                floor=self.observed(session)
                dropped=self.db.execute("UPDATE messages SET status='discarded_reset',error=NULL "
                    "WHERE session_id=? AND status='pending'",(session,)).rowcount
                # This outbox has one authenticated Base binding. Old artifact
                # receipts are not proof that the cleared Base still has bytes.
                self.db.execute('UPDATE artifacts SET receipt=NULL')
                self.store.save_session(fenced)
                updated=persisted | dict(stream_id=response['stream_id'],epoch=response['epoch'],
                    report_seq=0,server_observed_seq=0,server_acked_through=0,pending_report=None,
                    last_report_at=0,last_snapshot=None,lifecycle='active',scan_state='unknown',gap_count=0,
                    reset_seq_floor=floor,database_clear_generation=generation,reset_discarded_messages=dropped,
                    reset_ack_pending=generation,database_clear_ready_generation=None)
                self._save(updated)
                self.db.commit()
        value.clear()
        value.update(updated)

    def _register(self,value):
        args={'producer_id':value['producer_id'],'session_id':value['session_id']}
        register_bound=getattr(self.client,'register_stream',None)
        bound=bool(value['stream_id'] and register_bound)
        response=register_bound(args,previous=(value['stream_id'],value['epoch'])) if bound else self.client.call('memory_ingest_register',args)
        if not isinstance(response,dict):raise ValueError('Invalid stream registration')
        if not value['stream_id']:
            if not isinstance(response.get('stream_id'),str) or not isinstance(response.get('epoch'),str) or 'database_clear_reset' in response:
                raise ValueError('Invalid stream registration')
            value['stream_id'],value['epoch']=response['stream_id'],response['epoch']
        elif bound and 'database_clear_reset' in response:
            self._transition_reset(value,response)
        self._accept(value,response)
        if value['server_observed_seq']>self._wire_observed(value):
            raise ValueError('Server observed sequences missing from local outbox; review required')
        with self.db:self._save(value)
        self.reconciled.add(value['session_id'])
        self.last_probe[value['session_id']]=self.clock()

    def _report(self,value,*,force=False):
        snapshot={'observed_seq':max(self._wire_observed(value),value['server_observed_seq']),
            'scan_state':value['scan_state'],'gap_count':value['gap_count'],'lifecycle':value['lifecycle']}
        pending=value.get('pending_report')
        if pending is None:
            if not force and snapshot==value['last_snapshot'] and self.clock()-value['last_report_at']<20:return
            pending={'stream_id':value['stream_id'],'epoch':value['epoch'],'report_seq':value['report_seq']+1,**snapshot}
            if value.get('reset_ack_pending'):
                pending['reset_generation']=value['reset_ack_pending']
            value.update(report_seq=pending['report_seq'],pending_report=pending)
            with self.db:self._save(value)  # durable exact request before sending
        response=self.client.call('memory_ingest_report',pending)
        self._accept(value,response)
        if response['report_seq']<pending['report_seq'] or response['observed_seq']<pending['observed_seq']:
            raise ValueError('Report receipt does not cover requested frontier')
        if value.get('reset_ack_pending'):
            generation=value['reset_ack_pending']
            if (pending.get('reset_generation')!=generation
                    or response.get('database_clear_ready_generation')!=generation):
                raise ValueError('Database reset readiness is not acknowledged')
            value.update(reset_ack_pending=None,database_clear_ready_generation=generation)
        value.update(pending_report=None,last_snapshot={k:pending[k] for k in snapshot},last_report_at=self.clock())
        with self.db:self._save(value)
        # A replay does not renew the remote lease. Follow it with a fresh monotone
        # report, also publishing local changes made while the old ACK was lost.
        if pending['report_seq']!=value['report_seq'] or snapshot!=value['last_snapshot']:
            self._report(value,force=True)

    def _prepare_capture(self,value,row):
        arguments=json.loads(row['arguments'])
        arguments.pop('_capture_batch',None)
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
        seq=row['seq']-value.get('reset_seq_floor',0)
        if seq<=0:raise ValueError('Delivery predates database reset')
        delivery={'stream_id':value['stream_id'],'epoch':value['epoch'],'seq':seq}
        return {**arguments,'delivery':delivery}

    def _record_capture(self,value,row,receipt):
        arguments=json.loads(row['arguments'])
        event=receipt.get('event',{}) if isinstance(receipt,dict) else {}
        mid=event.get('message_id') or event.get('metadata',{}).get('message_id')
        if (not event.get('id') or event.get('session_id')!=arguments['session_id']
                or mid!=arguments['message_id'] or event.get('source')!=arguments['source']):raise ValueError('Unmatched message receipt')
        remote=receipt.get('delivery')
        self._accept(value,remote)
        if remote['observed_seq']<row['seq']-value.get('reset_seq_floor',0):raise ValueError('Delivery receipt lacks observed membership')
        self.db.execute('UPDATE delivery_sequences SET receipt=? WHERE session_id=? AND message_id=?',
            (_dump(receipt),arguments['session_id'],arguments['message_id']))
        self.db.execute("UPDATE messages SET status='acknowledged',attempts=attempts+1,error=NULL,receipt=? WHERE session_id=? AND message_id=?",
            (_dump(receipt),arguments['session_id'],arguments['message_id']))
        self._save(value)

    def _capture(self,value,row):
        receipt=self.client.call('memory_capture_message',self._prepare_capture(value,row))
        with self.db:
            self._record_capture(value,row,receipt)

    def _capture_batch(self,value,row,marker):
        members=marker['message_ids']
        stored=self.db.execute('SELECT m.*,d.seq FROM messages m JOIN delivery_sequences d USING(session_id,message_id) '
            'WHERE m.session_id=? AND d.seq>=? ORDER BY d.seq LIMIT ?',
            (value['session_id'],row['seq'],len(members))).fetchall()
        if [m['message_id'] for m in stored]!=members or any(
                json.loads(m['arguments']).get('_capture_batch')!=marker for m in stored):
            raise ValueError('Complete local batch membership could not be proved')
        receipt=self.client.call('memory_capture_batch',{'batch_id':marker['id'],
            'messages':[self._prepare_capture(value,member) for member in stored]})
        if (not isinstance(receipt,dict) or receipt.get('batch_id')!=marker['id']
                or not isinstance(receipt.get('receipts'),list) or len(receipt['receipts'])!=len(stored)):
            raise ValueError('Unmatched atomic batch receipt')
        # Confirm all individual acknowledgements or none; retry exactly this batch on lost response.
        with self.db:
            for member,response in zip(stored,receipt['receipts'],strict=True):
                self._record_capture(value,member,response)
        return len(stored)

    def _failed(self,session,failure):
        value=self.local(session)
        value.update(error=type(failure).__name__,retry_at=self.clock()+value['delay'],delay=min(60,value['delay']*2))
        with self.db:self._save(value)
        self.reconciled.discard(session)

    def prepare_source_scan(self,session):
        # Even with no queued work, an active machine checks already-enrolled
        # streams before polling the transcript. After long downtime, its
        # first check runs before any new source bytes are admitted locally.
        if not getattr(self.client,'register_stream',None):
            return
        value=self.local(session)
        now=self.clock()
        if (not value['stream_id'] or value['retry_at']>now
                or (session in self.reconciled and now-self.last_probe.get(session,float('-inf'))<10)):
            return
        try:
            self._register(value)
        except Exception as failure:
            self._failed(session,failure)

    def tick(self,limit=100):
        result={'attempted':0,'acknowledged':0,'stream_errors':0}
        for session in self.store.sessions():
            value=self.local(session['session_id'])
            if (not session.get('watching',True) and value['pending_report'] is None
                    and not self.db.execute('SELECT 1 FROM delivery_sequences WHERE session_id=? AND seq>? AND receipt IS NULL LIMIT 1',
                        (session['session_id'],value.get('reset_seq_floor',0))).fetchone()):continue
            if value['retry_at']>self.clock():continue
            try:
                if value['session_id'] not in self.reconciled:self._register(value)
                if value['lifecycle']=='closed' and value['pending_report'] is None:continue
                replay=bool(value.get('pending_report'))
                self._report(value)
                if replay:self._report(value,force=True)
                rows=self.db.execute('SELECT m.*,d.seq FROM messages m JOIN delivery_sequences d USING(session_id,message_id) '
                    'WHERE m.session_id=? AND d.seq>? AND d.receipt IS NULL ORDER BY d.seq LIMIT ?',
                    (value['session_id'],value.get('reset_seq_floor',0),limit-result['attempted'])).fetchall()
                through=0
                for row in rows:
                    if row['seq']<=through:continue
                    try:
                        marker=json.loads(row['arguments']).get('_capture_batch')
                        if marker is None:
                            count=1
                            result['attempted']+=count
                            self._capture(value,row)
                        else:
                            count=len(marker['message_ids'])
                            result['attempted']+=count
                            through=row['seq']+self._capture_batch(value,row,marker)-1
                    except Exception:
                        with self.db:self.db.execute('UPDATE messages SET attempts=attempts+1,error=? WHERE session_id=? AND message_id=?',
                            ('DeliveryReceiptError',row['session_id'],row['message_id']))
                        raise
                    result['acknowledged']+=count
                value.update(error=None,delay=1,retry_at=0)
                with self.db:self._save(value)
            except Exception as failure:
                # Preserve pending exact report and every unacknowledged raw body.
                self._failed(session['session_id'],failure)
                result['stream_errors']+=1
            if result['attempted']>=limit:break
        return result

    def close(self,session):
        value=self.local(session)
        missing=self.db.execute('SELECT COUNT(*) FROM delivery_sequences WHERE session_id=? AND seq>? AND receipt IS NULL',(session,value.get('reset_seq_floor',0))).fetchone()[0]
        if (missing or value['scan_state']!='current' or value['gap_count']
                or value['server_acked_through']!=self._wire_observed(value)):raise ValueError('Stream is not cleanly acknowledged/current')
        if session not in self.reconciled:self._register(value)
        value['lifecycle']='closed';self._report(value,force=True)
